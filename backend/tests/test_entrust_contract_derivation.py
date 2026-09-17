"""S3 合同派生（BP-03 第 8 条；D1-08；合同 §10.1 第 7 步）。

这一片验的是**"派生"与"编造"能不能分开**，不是"端点存在"。所以断言分四层：

1. **派生自哪份已接受事实**：合同的报价版本必须**就是**客户接受的那一版；
   前置不满足时各自失败（未响应 409 / 被拒绝 409 / 被接受的不是对客报价 400）；
2. **逐字段有出处**：合同 payload 里每个字段路径都在字段来源表里有行，
   且**每个值都能指着它核实**；反过来来源表不能指向合同里不存在的字段；
3. **冻结不受后续编辑影响**：派生之后**编辑报价产生新版本**，合同内容与来源
   仍然指向被接受的那一版 —— 这是 D1-08 最容易假绿的地方（读"当前版本"也会
   在派生当时通过，只有在事后编辑才露馅）；
4. **只有组织侧能看**：字段来源表里是内部编号，货主本人与局外人都必须 404
   （上一轮修过一类投影泄漏，这条是**结构性预防**，不是同一个 bug 的补丁）。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.entrust import contracts as ctr

_TS = "2026-09-17 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch"]'
)
READ_ONLY_PERMS = '["entrust:view"]'

QUOTE_PAYLOAD = {
    "amount": 36000,
    "currency": "CNY",
    "includes": ["船舶运输", "装船", "卸船"],
    "valid_until": "2026-12-31",
    # ⚠️ 未知字段：注册表记为 unknown，客户投影剔除它。合同派生读的是**发布快照**
    #    （已剔除），所以它**不可能**出现在合同里 —— 下面有一条断言论这件事。
    "internal_cost": 30000,
}


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_offer_release.env` 同源）。"""
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


def _seed(env, db, *, entrustment_perms: str = ALL_PERMS):
    """组织 + 经理成员 + 货主授权 + 已受理委托单 + 一个局外人。"""
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
            "VALUES (:o, :g, :t, '钢材 800 吨', 800, '吨', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "g": org, "t": "南宁→贵港 钢材运输", "c": _TS},
    )
    aid = int(res.lastrowid or 0)
    db.commit()
    return manager, owner, outsider, org, eid, aid


def _create_artifact(env, manager, *, eid: int, aid: int, payload=None, atype="customer_quote"):
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/artifacts",
        json={
            "artifact_type": atype,
            "payload": payload if payload is not None else dict(QUOTE_PAYLOAD),
            "assignment_id": aid,
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _publish(env, manager, *, eid: int, artifact_id: int, revision_no: int = 1, key=None):
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/offer-releases",
        json={"artifact_id": artifact_id, "revision_no": revision_no},
        headers=_headers(manager, key or uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _respond(env, user, *, release_id: int, decision="accept"):
    return env.client.post(
        f"/api/v1/entrust/offer-releases/{release_id}/responses",
        json={"decision": decision},
        headers=_headers(user, uuid.uuid4().hex),
    )


def _derive(env, user, *, release_id: int, key: str | None = None):
    return env.client.post(
        f"/api/v1/entrust/offer-releases/{release_id}/contract",
        json={},
        headers=_headers(user, key or uuid.uuid4().hex),
    )


def _read(env, user, *, release_id: int):
    return env.client.get(
        f"/api/v1/entrust/offer-releases/{release_id}/contract", headers=_headers(user)
    )


def _accepted_release(env, db, **seed_kw):
    """一条到「客户已接受」为止的完整链路，返回上下文。"""
    manager, owner, outsider, org, eid, aid = _seed(env, db, **seed_kw)
    quote = _create_artifact(env, manager, eid=eid, aid=aid)
    release = _publish(env, manager, eid=eid, artifact_id=quote["artifact_id"])
    assert _respond(env, owner, release_id=release["release_id"]).status_code == 200
    return manager, owner, outsider, org, eid, aid, quote, release


# ───────────────────────────────── 1. 正例：派生 + 逐字段有出处


def test_derive_produces_contract_with_every_field_sourced(env):
    """正例：合同内容来自**已接受的那一版报价**，且每个字段都有来源行。"""
    db = env.make_session()
    manager, owner, _outsider, _org, eid, aid, quote, release = _accepted_release(env, db)

    resp = _derive(env, manager, release_id=release["release_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # D1-08：派生自哪份已接受事实 —— 报价的**精确版本**必须写下来
    assert body["release_id"] == release["release_id"]
    assert body["quote_artifact_id"] == quote["artifact_id"]
    assert body["quote_revision_no"] == 1
    assert body["template_code"] == "domestic-transport"
    assert body["contract_revision_no"] == 1

    # 逐字段有出处
    paths = {s["field_path"] for s in body["field_sources"]}
    for expected in (
        "parties[0].role",
        "parties[0].name",
        "parties[1].role",
        "parties[1].name",
        "effective_date",
        "note",
    ):
        assert expected in paths, f"字段 {expected} 没有来源行：{sorted(paths)}"
    assert body["field_source_count"] == len(body["field_sources"]) > 0

    # 金额来自**已接受发布**（不是委托单、不是内部成本）
    amount_rows = [
        s
        for s in body["field_sources"]
        if s["field_path"].endswith(".text") and "36000" in s["value_text"]
    ]
    assert amount_rows, body["field_sources"]
    assert all(s["source_kind"] == "accepted_release" for s in amount_rows)
    assert all(s["source_ref"] == f"release:{release['release_id']}@v1" for s in amount_rows)
    # 来源类别给了原值与文案**两份**：界面显示文案、比对用原值（标签唯一实现在服务端）
    for src in body["field_sources"]:
        assert src["source_kind_text"], f"来源行缺文案：{src}"
        assert src["source_kind_text"] != src["source_kind"], f"文案与原值同形：{src}"

    # 内部成本口径绝不出现在合同里（它连发布快照都进不去）
    assert not any("30000" in str(s["value_text"]) for s in body["field_sources"])

    # 合同**正文**里不能夹带内部编号：来源表是审计面，正文是业务面
    row = (
        db.execute(
            text("SELECT payload_json FROM ent_artifact_revision WHERE artifact_id = :a"),
            {"a": body["contract_artifact_id"]},
        )
        .mappings()
        .first()
    )
    payload_text = json.loads(row["payload_json"])
    dumped = json.dumps(payload_text, ensure_ascii=False)
    for marker in ("release:", "leg:", "assignment:", "org:"):
        assert marker not in dumped, f"合同正文里夹带了内部编号 {marker}：{dumped}"
    assert payload_text["parties"][1]["name"], "承运方名称不能为空"
    assert payload_text["effective_date"], "生效日应有值"


def test_legs_make_one_clause_with_one_source_row_per_leg(env):
    """有航段时，一条运输范围条款由**多个航段**构成 ⇒ 同一字段多行来源。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, eid, aid, _quote, release = _accepted_release(env, db)
    for seq, (mode, frm, to) in enumerate(
        [
            ("road", "南宁仓", "贵港码头"),
            ("water", "贵港码头", "梧州码头"),
            ("road", "梧州码头", "广州仓"),
        ],
        start=1,
    ):
        db.execute(
            text(
                "INSERT INTO ent_leg (assignment_id, seq, mode, from_name, to_name, created_at, "
                " updated_at) VALUES (:a, :s, :m, :f, :t, :c, :c)"
            ),
            {"a": aid, "s": seq, "m": mode, "f": frm, "t": to, "c": _TS},
        )
    db.commit()

    body = _derive(env, manager, release_id=release["release_id"]).json()
    leg_rows = [s for s in body["field_sources"] if s["source_kind"] == "leg"]
    assert len(leg_rows) == 3, leg_rows
    # ⚠️ 三行来源指向**同一个字段路径** —— 这正是唯一键必须包含 (kind, ref) 的原因：
    #    按 field_path 去重会丢掉"另外两段航段也在里面"这条事实。
    assert len({r["field_path"] for r in leg_rows}) == 1
    assert len({r["source_ref"] for r in leg_rows}) == 3
    assert leg_rows[0]["field_path"] == "clauses[0].text"
    assert "内河" in leg_rows[0]["value_text"] and "公路" in leg_rows[0]["value_text"]


def test_non_numeric_amount_is_reported_absent_not_coerced(env):
    """金额不是数（如「面议」）⇒ 计入缺失清单，**不**写进价款条款。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, eid, aid = _seed(env, db)
    quote = _create_artifact(
        env,
        manager,
        eid=eid,
        aid=aid,
        payload={"amount": "面议", "currency": "CNY", "includes": ["船舶运输"]},
    )
    release = _publish(env, manager, eid=eid, artifact_id=quote["artifact_id"])
    assert _respond(env, _owner, release_id=release["release_id"]).status_code == 200

    body = _derive(env, manager, release_id=release["release_id"]).json()
    assert "amount" in body["absent_quote_fields"]
    assert not any("面议" in str(s["value_text"]) for s in body["field_sources"])
    # 正文里也不能出现 —— "面议"被写进价款条款会让下游拿到一个像金额的字符串
    payload_row = (
        db.execute(
            text("SELECT payload_json FROM ent_artifact_revision WHERE artifact_id = :a"),
            {"a": body["contract_artifact_id"]},
        )
        .mappings()
        .first()
    )
    assert "面议" not in str(payload_row["payload_json"])
    assert "amount" in str(body["note"]), "缺失项要写进派生说明，不能只在字段里"


# ───────────────────────────────── 2. 前置：不是"已接受事实"就不能派生


def test_derive_refuses_release_without_customer_response(env):
    """未响应的报价**不是**已接受事实 ⇒ 409。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, eid, aid = _seed(env, db)
    quote = _create_artifact(env, manager, eid=eid, aid=aid)
    release = _publish(env, manager, eid=eid, artifact_id=quote["artifact_id"])

    resp = _derive(env, manager, release_id=release["release_id"])
    assert resp.status_code == 409, resp.text
    assert "还没有客户响应" in resp.json()["detail"]["message"]


def test_derive_refuses_rejected_release(env):
    """客户**拒绝**过的报价不能派生合同 ⇒ 409（与"还没响应"分开说）。"""
    db = env.make_session()
    manager, owner, _outsider, _org, eid, aid = _seed(env, db)
    quote = _create_artifact(env, manager, eid=eid, aid=aid)
    release = _publish(env, manager, eid=eid, artifact_id=quote["artifact_id"])
    assert (
        _respond(env, owner, release_id=release["release_id"], decision="reject").status_code == 200
    )

    resp = _derive(env, manager, release_id=release["release_id"])
    assert resp.status_code == 409, resp.text
    assert "reject" in resp.json()["detail"]["message"]


def test_derive_refuses_acceptance_of_a_non_quote(env):
    """`contract_review` 也在客户可见白名单里、也会被接受 —— 但它的接受不是
    "客户接受了商业承诺" ⇒ 据此派生第二份合同是语义错位，必须 400。"""
    db = env.make_session()
    manager, owner, _outsider, _org, eid, aid = _seed(env, db)
    review = _create_artifact(
        env,
        manager,
        eid=eid,
        aid=aid,
        atype="contract_review",
        payload={"parties": ["南宁仓", "贵港航运"], "clauses": ["按模板"]},
    )
    release = _publish(env, manager, eid=eid, artifact_id=review["artifact_id"])
    assert _respond(env, owner, release_id=release["release_id"]).status_code == 200

    resp = _derive(env, manager, release_id=release["release_id"])
    assert resp.status_code == 400, resp.text
    assert "不是对客报价" in resp.json()["detail"]


# ───────────────────────────────── 3. 一份已接受事实只派生一份合同


def test_derive_is_single_shot_and_replay_returns_the_same_contract(env):
    """同键重放返回**同一份**合同；换键再派生 ⇒ 409 并告知已有那份的 id。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid, _quote, release = _accepted_release(env, db)

    key = uuid.uuid4().hex
    first = _derive(env, manager, release_id=release["release_id"], key=key)
    assert first.status_code == 200, first.text
    replay = _derive(env, manager, release_id=release["release_id"], key=key)
    assert replay.status_code == 200
    assert replay.json()["contract_artifact_id"] == first.json()["contract_artifact_id"]

    again = _derive(env, manager, release_id=release["release_id"])
    assert again.status_code == 409, again.text
    detail = again.json()["detail"]
    assert detail["existing_contract_artifact_id"] == first.json()["contract_artifact_id"]

    # 库里确实只有一份合同成果与一条派生记录（唯一约束兜住的那件事）
    contracts = db.execute(
        text(
            "SELECT COUNT(*) AS c FROM ent_artifact WHERE assignment_id = :a "
            "AND artifact_type = 'contract_review'"
        ),
        {"a": aid},
    ).scalar()
    derivations = db.execute(
        text("SELECT COUNT(*) AS c FROM ent_contract_derivation WHERE release_id = :r"),
        {"r": release["release_id"]},
    ).scalar()
    assert (contracts, derivations) == (1, 1)


# ───────────────────────────────── 4. 冻结：事后编辑不改写已派生的合同


def test_contract_keeps_the_accepted_revision_after_the_quote_is_edited(env):
    """派生之后编辑报价 → 合同内容与来源**仍然**指向被接受的那一版。

    这一条是 D1-08 最容易假绿的地方：读"成果当前版本"在派生当时也会通过，
    只有在**事后编辑**之后才露馅。所以必须造出"派生后编辑"这个顺序。
    """
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, quote, release = _accepted_release(env, db)
    derived = _derive(env, manager, release_id=release["release_id"]).json()
    assert any("36000" in s["value_text"] for s in derived["field_sources"])

    # 编辑报价：追加 v2，金额改成 99000
    edited = dict(QUOTE_PAYLOAD)
    edited["amount"] = 99000
    resp = env.client.post(
        f"/api/v1/entrust/artifacts/{quote['artifact_id']}/revisions",
        json={"payload": edited, "note": "内部复核后上调"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text

    again = _read(env, manager, release_id=release["release_id"])
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["quote_revision_no"] == 1, "合同必须钉在被接受的那一版上"
    assert any("36000" in s["value_text"] for s in body["field_sources"])
    assert not any("99000" in str(s["value_text"]) for s in body["field_sources"])


# ───────────────────────────────── 5. 权限与投影（内部编号不给客户）


def test_derive_requires_create_permission_and_org_membership(env):
    """只读成员 ⇒ 403；局外人 ⇒ 404；**货主本人也 ⇒ 404**（派生是组织侧动作）。"""
    db = env.make_session()
    manager, owner, outsider, _org, _eid, _aid, _quote, release = _accepted_release(env, db)

    # 把该授权的权限降成只读 ⇒ 缺 entrust:quote:create ⇒ 403
    db.execute(
        text(
            "UPDATE ent_entrustment SET permissions = :p WHERE id = (SELECT id FROM ent_entrustment LIMIT 1)"
        ),
        {"p": READ_ONLY_PERMS},
    )
    db.commit()
    denied = _derive(env, manager, release_id=release["release_id"])
    assert denied.status_code == 403, denied.text
    assert "entrust:quote:create" in denied.json()["detail"]

    # 恢复权限后：局外人与货主本人都不是该组织成员 ⇒ 404（不泄漏存在性）
    db.execute(text("UPDATE ent_entrustment SET permissions = :p"), {"p": ALL_PERMS})
    db.commit()
    assert _derive(env, outsider, release_id=release["release_id"]).status_code == 404
    assert _derive(env, owner, release_id=release["release_id"]).status_code == 404


def test_derivation_read_is_org_side_only_and_404_before_derivation(env):
    """读派生的端点**没有货主旁路**：来源表里是内部编号。

    上一轮修掉的那类投影泄漏是"两个入口判据不一致"；这一次是"这条通道本就不该有
    客户面"，所以用 `assert_can_view_org`（无货主旁路）—— 断言它，等于把这条设计
    决定钉在用例里：谁哪天顺手换成 `assert_can_view_entrustment`，这条会红。
    """
    db = env.make_session()
    manager, owner, outsider, _org, _eid, _aid, _quote, release = _accepted_release(env, db)

    # 还没派生 ⇒ 经理也是 404（回空壳会让"还没派生"与"派生了空合同"长得一样）
    assert _read(env, manager, release_id=release["release_id"]).status_code == 404

    assert _derive(env, manager, release_id=release["release_id"]).status_code == 200
    assert _read(env, manager, release_id=release["release_id"]).status_code == 200
    assert _read(env, owner, release_id=release["release_id"]).status_code == 404
    assert _read(env, outsider, release_id=release["release_id"]).status_code == 404


# ───────────────────────────────── 6. 闸门本身不能是空的


def test_field_source_gate_rejects_unregistered_and_ghost_paths():
    """闸门必须真的会拦 —— 否则"逐字段可核对"只是文档里的一句话。"""
    ok = {"field_path": "note", "value_text": "n", "source_kind": "template", "source_ref": "t"}
    # 合同里有一个字段没登记来源 ⇒ 拦
    with pytest.raises(ctr.ContractError, match="没有登记来源"):
        ctr._assert_every_field_has_source(
            {"parties": [{"role": "shipper", "name": "甲"}], "note": "n"}, [ok]
        )
    # 来源指向合同里不存在的字段 ⇒ 也拦（来源与内容脱节同样是坏数据）
    with pytest.raises(ctr.ContractError, match="不存在的字段路径"):
        ctr._assert_every_field_has_source({"note": "n"}, [ok, {**ok, "field_path": "ghost"}])
    # 来源类别不在取值域内 ⇒ 拦
    with pytest.raises(ctr.ContractError, match="来源类别"):
        ctr._assert_every_field_has_source({"note": "n"}, [{**ok, "source_kind": "guess"}])
    # 齐全时放行
    ctr._assert_every_field_has_source({"note": "n"}, [ok])
