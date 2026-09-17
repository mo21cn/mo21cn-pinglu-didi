"""S3 运力确认与有效期（BP-03 第 3 条；D1-06；合同 §10.1 第 5 步）。

这一片验的**不是"端点存在"**，而是三件在数据上能分开的事：

1. **确认 ≠ 选中**（BP-03 Exit evidence：`A chosen quotation alone does not create
   confirmed capacity.`）。所以有一条用例专门只登记候选、不确认，然后断言
   **一条确认记录都没有、候选 status 还是 candidate、`procurement_confirm` 成果为 0**。
   "两个端点分开"本身不构成证据 —— 证据是"只做前一步时，后一步的痕迹一点都没有"。
2. **判定是确定性的、逐条可核对**：过期 / 装不下 / 证据没有引用 / 需求口径不明，
   各自**独立**失败，且失败的响应里带**每一条**规则（含通过的）的比较值。
   同一套规则在换了输入之后（800 → 950）要给出不同结论 —— 那就是 D1-09 的内核。
3. **确认是冻结的事实**：确认之后**改写候选行**，确认里的值一个都不变；
   而 `recheck` 用当前事实复算时**必须**说"不再成立"，并把差异列出来。
   这两条一正一反，才把"冻结"与"复算"分开 —— 只做前者会把"冻结"写成"永远成立"。

⚠️ 日期一律相对 `svc.today_utc()` 计算，**不写死** "2026-12-31" 这类常量：
写死会让这条正例在某一天之后自动变成"过期"用例，而失败信息看起来像业务缺陷。
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.entrust import capacity as cap

_TS = "2026-09-17 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch"]'
)
READ_ONLY_PERMS = '["entrust:view"]'

#: 相对今天算出的“未来/过去”有效期，理由见模块文档
FUTURE_DATE = (cap.today_utc() + timedelta(days=90)).isoformat()
PAST_DATE = (cap.today_utc() - timedelta(days=90)).isoformat()


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_contract_derivation.env` 同源）。"""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app
    from app.models import Base
    from app.modules.auth.router import get_db
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    settings = get_settings()
    monkeypatch.setattr(settings, "ENTRUST_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield type("Env", (), {"client": tc, "make_session": factory})()
    app.dependency_overrides.clear()
    engine.dispose()


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _seed(env, db, *, entrustment_perms: str = ALL_PERMS, quantity: str | None = "800.000"):
    """组织 + 经理成员 + 货主授权 + 已受理委托单（货量可指定，含 NULL）+ 一个局外人。"""
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    outsider = _login(env.client, "other")
    res = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
        {"n": f"org-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    org = int(res.lastrowid or 0)
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'manager', 'active', :c)"
        ),
        {"o": org, "u": manager["user_id"], "c": _TS},
    )
    res = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": owner["user_id"], "p": entrustment_perms, "c": _TS},
    )
    eid = int(res.lastrowid or 0)
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, '钢材', :q, '吨', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "g": org, "t": "南宁→贵港 钢材运输", "q": quantity, "c": _TS},
    )
    aid = int(res.lastrowid or 0)
    db.commit()
    return manager, owner, outsider, org, eid, aid


def _add_candidate(env, user, *, aid: int, **over):
    body = {
        "carrier": "桂平航 6688",
        "capacity_tonnes": "900.000",
        "vessel_count": 1,
        "allows_partial_load": False,
        "valid_until": FUTURE_DATE,
        "evidence_kind": "document",
        "evidence_ref": "att:12",
        "rate": "45.00",
        "rate_unit": "吨",
        "currency": "CNY",
    }
    body.update(over)
    return env.client.post(
        f"/api/v1/entrust/assignments/{aid}/capacity-candidates",
        json=body,
        headers=_headers(user, uuid.uuid4().hex),
    )


def _confirm(env, user, *, aid: int, candidate_id: int, key: str | None = None, **over):
    body = {"candidate_id": candidate_id, "agreed_scope": "南宁→贵港 水运段 900 吨舱位"}
    body.update(over)
    return env.client.post(
        f"/api/v1/entrust/assignments/{aid}/capacity-confirmations",
        json=body,
        headers=_headers(user, key or uuid.uuid4().hex),
    )


def _read_confirmation(env, user, *, confirmation_id: int):
    return env.client.get(
        f"/api/v1/entrust/capacity-confirmations/{confirmation_id}", headers=_headers(user)
    )


def _recheck(env, user, *, confirmation_id: int):
    return env.client.get(
        f"/api/v1/entrust/capacity-confirmations/{confirmation_id}/recheck",
        headers=_headers(user),
    )


def _count(db, table: str) -> int:
    return int(db.execute(text(f"SELECT COUNT(*) AS c FROM {table}")).mappings().first()["c"])


# ───────────────────────── 1. 正例：确认 + 逐规则判定 + 冻结输入


def test_confirm_passes_every_rule_and_freezes_inputs(env):
    """正例：四条规则全部评估、逐条有比较值，判定的输入整体冻结在确认行上。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)

    cand = _add_candidate(env, manager, aid=aid)
    assert cand.status_code == 200, cand.text
    candidate = cand.json()
    assert candidate["status"] == "candidate"
    assert candidate["capacity_tonnes"] == "900.000"

    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # ① 逐规则判定：**四条都在**，顺序固定，且每条的 detail 里有实际比较的数
    codes = [r["rule_code"] for r in body["rule_checks"]]
    assert codes == list(cap.ALL_RULE_CODES), codes
    assert body["rule_check_count"] == len(cap.ALL_RULE_CODES)
    assert all(r["outcome"] == "pass" for r in body["rule_checks"])
    assert all(r["detail"].strip() for r in body["rule_checks"]), "判定说明不能为空"
    capacity_detail = next(
        r["detail"] for r in body["rule_checks"] if r["rule_code"] == cap.RULE_CAPACITY
    )
    assert "900.000" in capacity_detail and "800.000" in capacity_detail

    # ② 判定的输入整体冻结（不是"候选行现在的值"）
    assert body["capacity_tonnes"] == "900.000"
    assert body["demand_tonnes"] == "800.000"
    assert body["demand_unit"] == "吨"
    assert body["demand_ref"] == f"assignment:{aid}.quantity"
    assert body["evidence_kind"] == "document" and body["evidence_ref"] == "att:12"
    assert body["rule_set_version"] == cap.RULESET_VERSION
    assert body["as_of_date"] == cap.today_utc().isoformat()
    assert body["rate"] == "45.0000" and body["rate_unit"] == "吨"

    # ③ 产出的成果是采购确认（不是对客报价），且版本从 1 起
    assert body["artifact_revision_no"] == 1
    artifact = (
        db.execute(
            text("SELECT artifact_type, assignment_id FROM ent_artifact WHERE id = :a"),
            {"a": body["artifact_id"]},
        )
        .mappings()
        .first()
    )
    assert artifact["artifact_type"] == cap.CONFIRM_TYPE
    assert int(artifact["assignment_id"]) == aid

    # ④ 候选行 status 由确认命令推进为 confirmed
    cand_row = (
        db.execute(
            text("SELECT status FROM ent_capacity_candidate WHERE id = :c"),
            {"c": candidate["candidate_id"]},
        )
        .mappings()
        .first()
    )
    assert cand_row["status"] == "confirmed"

    # ⑤ 库里落的判定行数 = 规则数（一条不多一条不少）
    assert _count(db, "ent_capacity_rule_check") == len(cap.ALL_RULE_CODES)


def test_confirmation_appears_in_assignment_list(env):
    """确认后能在该委托的确认清单里读到（含逐规则判定），候选清单里 status 也变了。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    created = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"]).json()

    listed = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/capacity-confirmations", headers=_headers(manager)
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [r["confirmation_id"] for r in rows] == [created["confirmation_id"]]
    assert len(rows[0]["rule_checks"]) == len(cap.ALL_RULE_CODES)

    cands = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/capacity-candidates", headers=_headers(manager)
    )
    assert cands.status_code == 200, cands.text
    assert cands.json()[0]["status"] == "confirmed"


def test_confirmation_read_model_carries_agreed_scope_from_artifact(env):
    """范围（`agreed_scope`）按**冻结的成果版本**投影，不是在确认表上加一列。

    ⚠️ 本条钉的是**设计决定**，不只是"字段存在"：
    ① 四种读法（确认响应 / 单条读端点 / 清单读端点 / 成果那一版的载荷）**逐字一致** ——
       同一个事实只有一个来源；
    ② 确认表上**没有** `agreed_scope` 这一列。加一列就等于同一事实在库里有两份，
       改一处就静默不一致 —— 与 `contracts._assert_every_field_has_source`
       防的是同一类病（那边防的是"字段没有来源"，这边防的是"一个事实两个落点"）；
    ③ 读模型有的业务字段，**投影必须都有**：`project_confirmation` 是显式白名单，
       POST 响应 / 单条 / 清单三个读端点全从它出去 —— 忘了登记会让三个端点**齐声**
       少掉那一项，而库里数据完全正确（"写进去了却读不出来"）。第一次就是这么错的。
    """
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    scope = "南宁→贵港 水运段 900 吨舱位（含过驳）"

    created = _confirm(
        env, manager, aid=aid, candidate_id=candidate["candidate_id"], agreed_scope=scope
    ).json()

    # ⓪ 先钉**事实来源**：成果那一版的载荷里到底有没有范围。
    #    顺序有意如此 —— 失败时能一眼分出「没写进去」与「没读出来」，
    #    而不是对着一个 None 猜是哪一侧的问题。
    payload_raw = db.execute(
        text("SELECT payload_json FROM ent_artifact_revision WHERE id = :r"),
        {"r": created["artifact_revision_id"]},
    ).scalar_one()
    assert json.loads(payload_raw)["agreed_scope"] == scope, payload_raw

    # ① 确认响应
    assert created["agreed_scope"] == scope, sorted(created.keys())

    # ② 单条读端点（与 ③ 共用同一个 `_row_to_confirmation`，但还是各核一次：
    #    哪天有人给某一条换成自己的组装代码，这里就是唯一会响的地方）
    one = _read_confirmation(env, manager, confirmation_id=created["confirmation_id"])
    assert one.status_code == 200, one.text
    assert one.json()["agreed_scope"] == scope

    # ③ 清单读端点
    listed = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/capacity-confirmations", headers=_headers(manager)
    )
    assert listed.status_code == 200, listed.text
    assert [r["agreed_scope"] for r in listed.json()] == [scope]

    # ④ 与成果**那一版**的载荷逐字一致（事实只有一个来源）
    payload_raw = db.execute(
        text("SELECT payload_json FROM ent_artifact_revision WHERE id = :r"),
        {"r": created["artifact_revision_id"]},
    ).scalar_one()
    assert json.loads(payload_raw)["agreed_scope"] == scope

    # ⑤ 确认表上没有这一列（设计决定本身）
    from sqlalchemy import inspect as sa_inspect

    cols = {c["name"] for c in sa_inspect(db.get_bind()).get_columns("ent_capacity_confirmation")}
    assert "agreed_scope" not in cols, (
        "范围不该被复制到确认行上 —— 它的来源是成果版本（见 capacity._scope_from_payload）"
    )

    # ⑥ 读模型与投影的**键对齐** —— 本切片真正的根因就在这道缝上。
    #
    #    `project_confirmation` 是一层**显式白名单**，三个读端点（POST 响应 /
    #    单条 / 清单）全都从它出去。只给 `_row_to_confirmation` 加字段、忘了在投影里
    #    登记 ⇒ **三个端点齐声少掉那一项**，而库里数据完全正确 —— 表现成
    #    "写进去了却读不出来"，是这一片最难查的一类（第一次就是这么错的）。
    #    所以这里不钉某个字段，钉的是"读模型有的业务字段，投影必须都有"。
    #    要故意不投影某个字段，就得在本条里显式豁免 —— 让"少一项"是个决定，不是疏忽。
    row_keys = set(cap.get_confirmation(db, confirmation_id=created["confirmation_id"]) or {})
    proj_keys = set(cap.project_confirmation(db, created))
    assert row_keys - proj_keys == set(), (
        "这些字段读模型里有、投影里没有 ⇒ 三个读端点都会静默少掉它们："
        f"{sorted(row_keys - proj_keys)}"
    )


# ───────────────────────── 2. 「选中 ≠ 确认」：只登记候选，什么确认痕迹都没有


def test_recording_a_candidate_does_not_create_confirmed_capacity(env):
    """**BP-03 Exit evidence 的正面证据**：`A chosen quotation alone does not create
    confirmed capacity.`

    只登记候选（第 2 条"两家可比"要的数据全给齐，含单价与证据），然后断言确认侧
    **一点痕迹都没有** —— 清单为空、候选 status 仍是 candidate、
    `procurement_confirm` 成果为 0、判定表为 0。
    断言"两组端点分开"没有意义；有意义的是**前一步不产生后一步的任何数据**。
    """
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)

    # 两条候选 = canonical 夹具的「两家可比」形态
    a = _add_candidate(env, manager, aid=aid)
    b = _add_candidate(
        env, manager, aid=aid, carrier="横州集运 101", capacity_tonnes="1200.000", rate="47.50"
    )
    assert a.status_code == 200 and b.status_code == 200

    listed = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/capacity-confirmations", headers=_headers(manager)
    )
    assert listed.status_code == 200
    assert listed.json() == [], "只登记候选不该产生任何确认"

    assert _count(db, "ent_capacity_confirmation") == 0
    assert _count(db, "ent_capacity_rule_check") == 0
    assert (
        db.execute(
            text("SELECT COUNT(*) AS c FROM ent_artifact WHERE artifact_type = :t"),
            {"t": cap.CONFIRM_TYPE},
        )
        .mappings()
        .first()["c"]
        == 0
    )
    statuses = (
        db.execute(text("SELECT status FROM ent_capacity_candidate ORDER BY id")).mappings().all()
    )
    assert [r["status"] for r in statuses] == ["candidate", "candidate"]


# ───────────────────────── 3. 负例：过期 / 装不下 / 证据 / 需求口径


def test_expired_candidate_cannot_be_confirmed(env):
    """`expired ... cannot be confirmed`：过期 ⇒ 409，且**只有** validity 没过。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid, valid_until=PAST_DATE).json()

    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 409, resp.text
    checks = resp.json()["detail"]["rule_checks"]
    assert [c["rule_code"] for c in checks] == list(cap.ALL_RULE_CODES), (
        "失败的响应要带**全部**判定"
    )
    failed = {c["rule_code"] for c in checks if c["outcome"] == "fail"}
    assert failed == {cap.RULE_VALIDITY}, failed
    assert PAST_DATE in next(c for c in checks if c["rule_code"] == cap.RULE_VALIDITY)["detail"]

    # 拒绝**不留痕**：没有"半条确认"躺在库里
    assert _count(db, "ent_capacity_confirmation") == 0
    assert _count(db, "ent_capacity_rule_check") == 0
    assert _count(db, "ent_artifact") == 0


def test_unsuitable_capacity_cannot_be_confirmed_and_reports_shortfall(env):
    """`unsuitable ... cannot be confirmed`：900 吨单船不拆批装不下 950 吨。

    这条就是 D1-09 的确定性内核（`makes the 900-tonne candidate unsuitable`）——
    结论由算术给出，不由任何人（或模型）表态。
    """
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db, quantity="950.000")
    candidate = _add_candidate(env, manager, aid=aid).json()

    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 409, resp.text
    checks = resp.json()["detail"]["rule_checks"]
    failed = {c["rule_code"]: c for c in checks if c["outcome"] == "fail"}
    assert set(failed) == {cap.RULE_CAPACITY}, failed
    detail = failed[cap.RULE_CAPACITY]["detail"]
    assert "950.000" in detail and "900.000" in detail and "50.000" in detail, detail


def test_multi_vessel_capacity_makes_950_fit(env):
    """canonical 夹具的注：**多船承运 ⇒ 950 未必装不下**（判据是吨位 × 船数）。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db, quantity="950.000")
    candidate = _add_candidate(env, manager, aid=aid, vessel_count=2).json()
    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 200, resp.text
    detail = next(
        r["detail"] for r in resp.json()["rule_checks"] if r["rule_code"] == cap.RULE_CAPACITY
    )
    assert "1800.000" in detail, detail


def test_partial_load_passes_but_records_trip_count(env):
    """允许拆批 ⇒ 通过，但**必须记下需几趟** —— "通过"两个字不是判定说明。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db, quantity="950.000")
    candidate = _add_candidate(env, manager, aid=aid, allows_partial_load=True).json()
    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 200, resp.text
    detail = next(
        r["detail"] for r in resp.json()["rule_checks"] if r["rule_code"] == cap.RULE_CAPACITY
    )
    assert "2 趟" in detail, detail


def test_evidence_without_reference_is_not_identified(env):
    """`identified evidence` = 类别 + **引用**。只写"是单据"不算 identified。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid, evidence_ref=None).json()

    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 409, resp.text
    failed = {
        c["rule_code"] for c in resp.json()["detail"]["rule_checks"] if c["outcome"] == "fail"
    }
    assert failed == {cap.RULE_EVIDENCE}, failed


def test_missing_validity_does_not_mean_not_expired(env):
    """没登记有效期 ⇒ 不通过。**未知有效期 ≠ 未过期**（不把两者折成一个结论）。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid, valid_until=None).json()

    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 409, resp.text
    failed = {
        c["rule_code"] for c in resp.json()["detail"]["rule_checks"] if c["outcome"] == "fail"
    }
    assert failed == {cap.RULE_VALIDITY}, failed


def test_unknown_demand_blocks_capacity_judgement(env):
    """委托货量未知 ⇒ 两条规则同时不通过，且**不**归并成一条（归并会让人以为只差一项）。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db, quantity=None)
    candidate = _add_candidate(env, manager, aid=aid).json()

    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 409, resp.text
    failed = {
        c["rule_code"] for c in resp.json()["detail"]["rule_checks"] if c["outcome"] == "fail"
    }
    assert failed == {cap.RULE_DEMAND_KNOWN, cap.RULE_CAPACITY}, failed


def test_different_quantity_unit_is_not_comparable(env):
    """单位为「件」⇒ 不通过。不同口径之间没有可判定的换算关系，不许硬比大小。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    db.execute(text("UPDATE ent_assignment SET quantity_unit = '件' WHERE id = :a"), {"a": aid})
    db.commit()
    candidate = _add_candidate(env, manager, aid=aid).json()

    resp = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 409, resp.text
    detail = next(
        c["detail"]
        for c in resp.json()["detail"]["rule_checks"]
        if c["rule_code"] == cap.RULE_DEMAND_KNOWN
    )
    assert "件" in detail and "同一口径" in detail, detail


# ───────────────────────── 4. 幂等、唯一性与冻结


def test_confirm_is_idempotent_and_one_candidate_confirms_once(env):
    """同键重放回同一成果；换键 ⇒ 409 并带回已存在的那条记录 id（判据是唯一约束）。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    cid = candidate["candidate_id"]

    first_key = uuid.uuid4().hex
    first = _confirm(env, manager, aid=aid, candidate_id=cid, key=first_key)
    assert first.status_code == 200, first.text

    # 同键同体 ⇒ 重放历史响应（含同一个 confirmation_id / artifact_id）
    again = _confirm(env, manager, aid=aid, candidate_id=cid, key=first_key)
    assert again.status_code == 200, again.text
    assert again.json()["confirmation_id"] == first.json()["confirmation_id"]
    assert again.json()["artifact_id"] == first.json()["artifact_id"]
    assert _count(db, "ent_capacity_confirmation") == 1

    # 换键 ⇒ 409，且告诉调用方去读哪一条
    other = _confirm(env, manager, aid=aid, candidate_id=cid)
    assert other.status_code == 409, other.text
    detail = other.json()["detail"]
    assert detail["existing_confirmation_id"] == first.json()["confirmation_id"]
    assert detail["existing_artifact_id"] == first.json()["artifact_id"]
    assert _count(db, "ent_capacity_rule_check") == len(cap.ALL_RULE_CODES)


def test_confirmation_is_frozen_against_candidate_edits(env):
    """确认之后**改写候选行**，确认里的值一个都不变。

    这是"冻结"与"重算"的分界：不冻的话，改一次候选的吨位就悄悄改掉了
    "当初凭什么确认"，而记录上仍写着同一条确认。
    """
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    created = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"]).json()

    db.execute(
        text(
            "UPDATE ent_capacity_candidate SET capacity_tonnes = '1200.000', "
            "valid_until = :v, evidence_ref = 'att:999' WHERE id = :c"
        ),
        {"v": FUTURE_DATE, "c": candidate["candidate_id"]},
    )
    db.commit()

    fresh = _read_confirmation(env, manager, confirmation_id=created["confirmation_id"])
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["capacity_tonnes"] == "900.000", "确认里的吨位被候选行的改写带走了"
    assert body["evidence_ref"] == "att:12"
    # 判定行也一个字都不变
    assert body["rule_checks"] == created["rule_checks"]


# ───────────────────────── 5. 只读复算（D1-09 的可见面）


def test_recheck_detects_that_the_confirmation_no_longer_holds(env):
    """确认时 800 吨通过；之后货量变成 950 ⇒ `recheck` 必须说"不再成立"并列出差异。

    ⚠️ 同时断言**复算不改任何行**（确认与判定行与复算前逐字相同）——
    没有这条，"复算"这个动作到底是读还是写，光看响应是分不出来的。
    """
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    created = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"]).json()

    before = _read_confirmation(env, manager, confirmation_id=created["confirmation_id"]).json()

    ok = _recheck(env, manager, confirmation_id=created["confirmation_id"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["still_valid"] is True
    assert ok.json()["changed_fields"] == []

    # 变更：800 → 950（canonical 夹具的数量变更）
    db.execute(text("UPDATE ent_assignment SET quantity = '950.000' WHERE id = :a"), {"a": aid})
    db.commit()

    resp = _recheck(env, manager, confirmation_id=created["confirmation_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["still_valid"] is False
    assert body["changed_fields"] == ["demand_tonnes"], body["changed_fields"]
    failed = {c["rule_code"] for c in body["rule_checks"] if c["outcome"] == "fail"}
    assert failed == {cap.RULE_CAPACITY}, failed
    assert len(body["frozen_rule_checks"]) == len(cap.ALL_RULE_CODES)
    assert all(c["outcome"] == "pass" for c in body["frozen_rule_checks"])

    after = _read_confirmation(env, manager, confirmation_id=created["confirmation_id"]).json()
    assert after == before, "只读复算改动了确认记录"


def test_recheck_without_candidate_does_not_claim_valid(env):
    """候选行消失 ⇒ `still_valid=False`（判据没了，结论就没了；**不**默认仍有效）。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    created = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"]).json()
    db.execute(
        text("DELETE FROM ent_capacity_candidate WHERE id = :c"), {"c": candidate["candidate_id"]}
    )
    db.commit()

    body = _recheck(env, manager, confirmation_id=created["confirmation_id"]).json()
    assert body["candidate_missing"] is True
    assert body["still_valid"] is False
    assert body["rule_checks"] == []


# ───────────────────────── 6. 权限：整组没有货主通道


def test_owner_and_outsider_cannot_read_capacity(env):
    """候选/确认两侧都**不给货主本人放行**（供应商单价与采购口径是内部事实）。"""
    db = env.make_session()
    manager, owner, outsider, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    created = _confirm(env, manager, aid=aid, candidate_id=candidate["candidate_id"]).json()

    for path in (
        f"/api/v1/entrust/assignments/{aid}/capacity-candidates",
        f"/api/v1/entrust/assignments/{aid}/capacity-confirmations",
        f"/api/v1/entrust/capacity-confirmations/{created['confirmation_id']}",
        f"/api/v1/entrust/capacity-confirmations/{created['confirmation_id']}/recheck",
    ):
        owner_resp = env.client.get(path, headers=_headers(owner))
        assert owner_resp.status_code == 404, f"货主本人不该读到 {path}：{owner_resp.text}"
        out_resp = env.client.get(path, headers=_headers(outsider))
        assert out_resp.status_code == 404, f"局外人不该读到 {path}：{out_resp.text}"


def test_owner_cannot_confirm_capacity(env):
    """货主本人也不能确认运力 —— 采购确认是**组织侧**的动作。"""
    db = env.make_session()
    manager, owner, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()
    resp = _confirm(env, owner, aid=aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 404, resp.text
    assert _count(db, "ent_capacity_confirmation") == 0


def test_read_only_member_cannot_write_capacity(env):
    """只读成员：读得到，写不了（403，不是 404 —— 他看得见，只是无权）。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db, entrustment_perms=READ_ONLY_PERMS)

    assert (
        env.client.get(
            f"/api/v1/entrust/assignments/{aid}/capacity-candidates", headers=_headers(manager)
        ).status_code
        == 200
    )
    resp = _add_candidate(env, manager, aid=aid)
    assert resp.status_code == 403, resp.text
    assert _count(db, "ent_capacity_candidate") == 0


def test_candidate_of_another_assignment_cannot_be_confirmed(env):
    """跨委托确认必须失败（按"不存在"处理，不说破那个 id 存在）。"""
    db = env.make_session()
    manager, owner, _x, _org, _eid, aid = _seed(env, db)
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, status, revision, created_at, updated_at) "
            "VALUES (:o, NULL, '另一单', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "c": _TS},
    )
    other_aid = int(res.lastrowid or 0)
    db.commit()
    candidate = _add_candidate(env, manager, aid=aid).json()

    resp = _confirm(env, manager, aid=other_aid, candidate_id=candidate["candidate_id"])
    assert resp.status_code == 404, resp.text


# ───────────────────────── 7. 登记侧的输入校验（录错了 vs 还没齐）


def test_candidate_registration_rejects_structurally_meaningless_input(env):
    """登记只拒"录错了"，不拒"还没齐"（后者是确认时的判定，见下一条）。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)

    assert _add_candidate(env, manager, aid=aid, carrier="   ").status_code == 400
    assert _add_candidate(env, manager, aid=aid, capacity_tonnes="0").status_code == 400
    assert _add_candidate(env, manager, aid=aid, capacity_tonnes="-5").status_code == 400
    assert _add_candidate(env, manager, aid=aid, vessel_count=0).status_code == 400
    # 只有金额没有计价单位 ⇒ 无法确定费用
    assert _add_candidate(env, manager, aid=aid, rate="45.00", rate_unit=None).status_code == 400
    assert _add_candidate(env, manager, aid=aid, rate=None, rate_unit="吨").status_code == 400
    # 证据类别不在取值域
    assert _add_candidate(env, manager, aid=aid, evidence_kind="口头的").status_code == 400
    assert _count(db, "ent_capacity_candidate") == 0


def test_candidate_registration_allows_pending_evidence_and_validity(env):
    """**还没有证据、还没有有效期也可以登记** —— 否则「过期不得确认」那条规则永远触发不了。"""
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    resp = _add_candidate(
        env, manager, aid=aid, valid_until=None, evidence_kind=None, evidence_ref=None
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["valid_until"] is None
    assert resp.json()["evidence_kind"] is None


def test_leg_must_belong_to_the_same_assignment(env):
    """航段必须属于本委托：跨单引用会让"这条运力属于哪一单"变得可争。"""
    db = env.make_session()
    manager, owner, _x, _org, _eid, aid = _seed(env, db)
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, status, revision, created_at, updated_at) "
            "VALUES (:o, NULL, '另一单', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "c": _TS},
    )
    other_aid = int(res.lastrowid or 0)
    res = db.execute(
        text(
            "INSERT INTO ent_leg (assignment_id, seq, mode, from_name, to_name, created_at, "
            " updated_at) VALUES (:a, 1, 'water', 'A', 'B', :c, :c)"
        ),
        {"a": other_aid, "c": _TS},
    )
    foreign_leg = int(res.lastrowid or 0)
    db.commit()

    resp = _add_candidate(env, manager, aid=aid, leg_id=foreign_leg)
    assert resp.status_code == 400, resp.text
    assert _count(db, "ent_capacity_candidate") == 0


# ───────────────────────── 8. 闸门与规则集本身


def test_gate_rejects_incomplete_or_failed_rule_reports():
    """闸门红绿对照：缺一条判定、或带着 fail 走到写库，都必须当场炸（不是 4xx）。

    没有这道闸门，"某条规则这次没跑"在数据上与"跑了且通过"完全一样 ——
    确认记录照样存在，逐规则判定表少一行而已，没有任何下游症状。
    """
    good = cap.evaluate(
        demand_tonnes="800.000",
        demand_unit="吨",
        demand_ref="assignment:1.quantity",
        capacity_tonnes="900.000",
        vessel_count=1,
        allows_partial_load=0,
        valid_until=FUTURE_DATE,
        evidence_kind="document",
        evidence_ref="att:12",
        as_of=cap.today_utc(),
    )
    cap._assert_every_rule_reported(good)  # 绿：四条齐全且全通过

    with pytest.raises(cap.CapacityInvariantError):
        cap._assert_every_rule_reported(good[:-1])  # 少一条
    with pytest.raises(cap.CapacityInvariantError):
        cap._assert_every_rule_reported((good[1], good[0], good[2], good[3]))  # 顺序变了
    with pytest.raises(cap.CapacityInvariantError):
        cap._assert_every_rule_reported(good + (good[0],))  # 多一条

    expired = cap.evaluate(
        demand_tonnes="800.000",
        demand_unit="吨",
        demand_ref="assignment:1.quantity",
        capacity_tonnes="900.000",
        vessel_count=1,
        allows_partial_load=0,
        valid_until=PAST_DATE,
        evidence_kind="document",
        evidence_ref="att:12",
        as_of=cap.today_utc(),
    )
    with pytest.raises(cap.CapacityInvariantError):
        cap._assert_every_rule_reported(expired)

    # 闸门异常**不**继承 CapacityError ⇒ 不会被 `_map_errors` 映射成 4xx，必须 500
    assert not issubclass(cap.CapacityInvariantError, cap.CapacityError)


def test_unrelated_integrity_error_is_not_reported_as_already_confirmed(env):
    """**回归**：约束错误不得被一律说成"已被确认过"。

    初版 `except IntegrityError` **无条件**抛 `CapacityStateError("...已被确认过...")`，
    于是任何 IntegrityError 都被说成业务冲突。实测触发它的是并发预跑里漏传
    `entrustment_id`（`ent_artifact.entrustment_id` 是 NOT NULL）——
    **两个线程都拿到"已被确认过"，而库里一条确认行都没有**。

    把约束错误一律改写成语义错误，等于把"代码写错了"伪装成"业务不允许"，
    而那类伪装下次会以同样面目出现（"并发时偶发 409，重试即可"）。
    现在的判据是：**确实查到确认行**才翻译成 409，否则原样抛出。
    """
    db = env.make_session()
    manager, _o, _x, _org, _eid, aid = _seed(env, db)
    candidate = _add_candidate(env, manager, aid=aid).json()

    with pytest.raises(IntegrityError):
        cap.confirm_capacity(
            db,
            assignment_id=aid,
            candidate_id=candidate["candidate_id"],
            actor_user_id=1,
            agreed_scope="南宁→贵港 水运段",
            entrustment_id=None,  # type: ignore[arg-type] —— 有意制造 NOT NULL 违例
        )
    db.rollback()
    # 而且不留半条确认、不留成果
    assert _count(db, "ent_capacity_confirmation") == 0
    assert _count(db, "ent_artifact") == 0


def test_rule_set_and_version_are_locked():
    """规则集与版本号**锁定**：新增/删除规则必须同时改这里与 `RULESET_VERSION`。

    规则集会演进（S4 要把同一套容量规则用到变更影响上）。锁住之后，
    "规则变了但旧确认没跟着说明"这件事不可能悄悄发生 —— 旧确认行上冻着
    `rule_set_version`，而这道断言逼人显式升级版本号。
    """
    assert cap.ALL_RULE_CODES == ("demand_known", "evidence", "validity", "capacity")
    assert cap.RULESET_VERSION == "v1"
