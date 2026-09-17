"""合同签署证据（§10.1 第 7 步后半 / D1-08 的 `linked evidence`）。

这一片验的是**"证据"与"签署"能不能分开**，不是"端点存在"。断言分五层：

1. **模式不由调用方决定**：`mode` 恒为 `labeled_sample`，请求体里**没有**这个字段
   —— 能传 `mode=live` 就等于允许界面自称已完成电子签署（合同 §3.2 / D1-08）；
2. **绑的是版本不是成果**：合同被编辑出第 2 版之后，第 1 版那条证据**仍然指着第 1 版**
   —— "客户签的是哪一版"必须在事后可复核，这条是 D1-08 最容易假绿的地方
   （若按 artifact 而非 revision 存，派生当时同样通过，只有事后编辑才露馅）；
3. **形态不收未知值**：取值域外的 `evidence_kind` 一律 400，且说出取值域。
   ⚠️ 这与航段 `mode`（自由文本）是**相反**的口径，别互相照抄；
4. **同版同形态只一条**：重放被唯一约束挡住 ⇒ 409，并带回已存在的那条 id；
5. **只有组织侧能看**：货主本人与局外人都 404（与派生同一条纪律：证据行带内部
   编号与审计措辞，客户看合同走已有发布通路）。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_TS = "2026-09-18 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch"]'
)

QUOTE_PAYLOAD = {
    "amount": 36000,
    "currency": "CNY",
    "includes": ["船舶运输", "装船", "卸船"],
    "valid_until": "2026-12-31",
}


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


def _publish(env, manager, *, eid: int, artifact_id: int, revision_no: int = 1):
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/offer-releases",
        json={"artifact_id": artifact_id, "revision_no": revision_no},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _respond(env, user, *, release_id: int, decision="accept"):
    return env.client.post(
        f"/api/v1/entrust/offer-releases/{release_id}/responses",
        json={"decision": decision},
        headers=_headers(user, uuid.uuid4().hex),
    )


def _derive(env, user, *, release_id: int):
    return env.client.post(
        f"/api/v1/entrust/offer-releases/{release_id}/contract",
        json={},
        headers=_headers(user, uuid.uuid4().hex),
    )


def _contract(env, db, **seed_kw):
    """一条到「合同已派生」为止的完整链路，返回（上下文…, 合同成果 id）。"""
    manager, owner, outsider, org, eid, aid = _seed(env, db, **seed_kw)
    quote = _create_artifact(env, manager, eid=eid, aid=aid)
    release = _publish(env, manager, eid=eid, artifact_id=quote["artifact_id"])
    assert _respond(env, owner, release_id=release["release_id"]).status_code == 200
    resp = _derive(env, manager, release_id=release["release_id"])
    assert resp.status_code == 200, resp.text
    return manager, owner, outsider, org, eid, aid, int(resp.json()["contract_artifact_id"])


def _record(
    env, user, *, contract_artifact_id: int, kind="sample_scan", note=None, rev=None, key=None
):
    body = {"evidence_kind": kind}
    if note is not None:
        body["note"] = note
    if rev is not None:
        body["revision_no"] = rev
    return env.client.post(
        f"/api/v1/entrust/contracts/{contract_artifact_id}/signature-evidence",
        json=body,
        headers=_headers(user, key or uuid.uuid4().hex),
    )


def _list(env, user, *, contract_artifact_id: int):
    return env.client.get(
        f"/api/v1/entrust/contracts/{contract_artifact_id}/signature-evidence",
        headers=_headers(user),
    )


# ───────────────────────────── 1. 正例：模式由服务端写死，绑到当前版本


def test_record_uses_labeled_sample_and_binds_current_revision(env):
    """正例：`mode` 恒为 labeled_sample，绑到合同的**当前版本**（v1）。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, cart = _contract(env, db)

    resp = _record(env, manager, contract_artifact_id=cart, note="客户签回的样件扫描件")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # ① 模式不是入参 ⇒ 恒为样件标注（合同 §3.2 / D1-08 的红线）
    assert body["mode"] == "labeled_sample"
    # 文案是给人看的，原值是给机器比对的 —— 两者**必须不同**（相同就说明只给了一份）
    assert "样件标注" in body["mode_text"]
    assert body["mode_text"] != body["mode"]
    assert "实时电子签署" in body["mode_text"]
    # ② 绑的是**版本**
    assert body["contract_revision_no"] == 1
    assert body["revision_no_text"] == "第 1 版"
    assert body["contract_artifact_id"] == cart
    # ③ 形态给了原值与文案**两份**（界面显示文案、比对用原值）
    assert body["evidence_kind"] == "sample_scan"
    assert body["evidence_kind_text"]
    assert body["evidence_kind_text"] != body["evidence_kind"]
    assert body["note_text"] == "客户签回的样件扫描件"

    listed = _list(env, manager, contract_artifact_id=cart)
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["has_items"] is True
    assert len(payload["items"]) == 1
    # 常驻声明由**服务端**给出（措辞只有一份，不会有的界面说、有的界面不说）
    assert payload["disclaimer"]
    assert "电子签署" in payload["disclaimer"]
    # 形态选项也由服务端给出 ⇒ 界面不可能给出一个服务端不收的形态
    kinds = {o["value"]: o["label"] for o in payload["kind_options"]}
    assert set(kinds) == {"sample_scan", "written_confirmation", "manual_record"}
    assert all(v and v != k for k, v in kinds.items())


def test_note_empty_shows_placeholder_not_blank(env):
    """没写说明 ⇒ 显示「未写说明」，不留空白格、也不说成"说明未知"。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, cart = _contract(env, db)

    resp = _record(env, manager, contract_artifact_id=cart)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["note"] == ""
    assert body["note_text"] == "未写说明"


# ───────────────────────────── 2. 绑的是版本：合同出第 2 版后仍指着第 1 版


def test_evidence_stays_bound_to_the_revision_it_was_recorded_on(env):
    """合同编辑出第 2 版之后，第 1 版那条证据**仍然指着第 1 版**。

    这是本切片最容易假绿的地方：若证据只绑 `artifact_id`，派生当时也"通过"，
    只有事后编辑才露馅 —— 而"客户签的是哪一版"恰恰要在事后才被问到。
    """
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, cart = _contract(env, db)
    first = _record(env, manager, contract_artifact_id=cart, note="签的是第一版")
    assert first.status_code == 200, first.text
    first_rev_id = first.json()["contract_revision_id"]

    # 编辑合同 ⇒ 出第 2 版（走服务层，不新开端点）。
    # ⚠️ `append_revision` 只写新版本，**不**推进"当前版本" —— 后者要经
    # `confirm_revision`。少这一步会得到 v1：那不是 bug，是"新版本尚未生效"的语义，
    # 而本测试要的是"已经生效的第 2 版"，所以两步都要有。
    from app.modules.entrust import artifacts as art

    row = (
        db.execute(
            text("SELECT payload_json FROM ent_artifact_revision WHERE id = :r"),
            {"r": first_rev_id},
        )
        .mappings()
        .first()
    )
    payload = json.loads(row["payload_json"])
    art.append_revision(
        db,
        artifact_id=cart,
        payload=payload,
        actor_id=int(manager["user_id"]),
        source=art.SOURCE_MANUAL,
        note="双方协商后改了有效期",
    )
    art.confirm_revision(
        db,
        artifact_id=cart,
        revision_no=2,
        actor_id=int(manager["user_id"]),
        as_source=art.SOURCE_MANUAL,
    )

    # 第 1 版那条证据：版本**没跟着漂**
    listed = _list(env, manager, contract_artifact_id=cart).json()
    by_kind = {it["evidence_kind"]: it for it in listed["items"]}
    assert by_kind["sample_scan"]["contract_revision_no"] == 1
    assert by_kind["sample_scan"]["contract_revision_id"] == first_rev_id

    # 给第 2 版再记一条**别的形态** ⇒ 两条并存，各自指着各的版本
    second = _record(env, manager, contract_artifact_id=cart, kind="written_confirmation")
    assert second.status_code == 200, second.text
    assert second.json()["contract_revision_no"] == 2
    listed = _list(env, manager, contract_artifact_id=cart).json()
    assert len(listed["items"]) == 2
    assert sorted(it["contract_revision_no"] for it in listed["items"]) == [1, 2]

    # 显式指定版本：给第 1 版补一条"人工记录" ⇒ 落在第 1 版
    third = _record(env, manager, contract_artifact_id=cart, kind="manual_record", rev=1)
    assert third.status_code == 200, third.text
    assert third.json()["contract_revision_no"] == 1


# ───────────────────────────── 3. 形态不收未知值 / 版本不存在


def test_unknown_evidence_kind_is_rejected_with_domain(env):
    """取值域外的形态 ⇒ 400，且**说出**取值域（不是一句"参数错误"）。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, cart = _contract(env, db)

    resp = _record(env, manager, contract_artifact_id=cart, kind="live_esign")
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    for kind in ("sample_scan", "written_confirmation", "manual_record"):
        assert kind in detail, f"拒绝理由里没有列出取值域：{detail}"


def test_missing_revision_is_404_not_400(env):
    """合同没有那一版 ⇒ 404（"没有可绑的版本"），不是 400 —— 客户端不该重试改参数。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, cart = _contract(env, db)

    resp = _record(env, manager, contract_artifact_id=cart, rev=9)
    assert resp.status_code == 404, resp.text


def test_non_contract_artifact_is_404(env):
    """对非合同成果记证据 ⇒ 404。类型不对**也当不存在**（不泄露 id 空间）。"""
    db = env.make_session()
    manager, owner, _outsider, _org, eid, aid = _seed(env, db)
    quote = _create_artifact(env, manager, eid=eid, aid=aid)

    resp = _record(env, manager, contract_artifact_id=int(quote["artifact_id"]))
    assert resp.status_code == 404, resp.text
    assert _list(env, manager, contract_artifact_id=int(quote["artifact_id"])).status_code == 404


# ───────────────────────────── 4. 同版同形态只一条（重放 ⇒ 409）


def test_same_revision_same_kind_twice_is_409_with_existing_id(env):
    """同一版同一形态记两次 ⇒ 409，并带回已存在的那条 id（不是一句"已经记过了"）。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, cart = _contract(env, db)

    first = _record(env, manager, contract_artifact_id=cart)
    assert first.status_code == 200, first.text
    again = _record(env, manager, contract_artifact_id=cart)
    assert again.status_code == 409, again.text
    detail = again.json()["detail"]
    assert detail["existing_evidence_id"] == first.json()["evidence_id"]


def test_same_idempotency_key_replays_without_duplicate(env):
    """同一 Idempotency-Key 重放 ⇒ 不产生第二条。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, _aid, cart = _contract(env, db)

    key = uuid.uuid4().hex
    first = _record(env, manager, contract_artifact_id=cart, key=key)
    assert first.status_code == 200, first.text
    replay = _record(env, manager, contract_artifact_id=cart, key=key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["evidence_id"] == first.json()["evidence_id"]
    assert len(_list(env, manager, contract_artifact_id=cart).json()["items"]) == 1


# ───────────────────────────── 5. 只有组织侧能看


def test_owner_and_outsider_get_404(env):
    """货主本人与局外人都 404：证据行带内部编号与审计措辞，不给客户面。"""
    db = env.make_session()
    manager, owner, outsider, _org, _eid, _aid, cart = _contract(env, db)
    assert _record(env, manager, contract_artifact_id=cart).status_code == 200

    assert _list(env, owner, contract_artifact_id=cart).status_code == 404
    assert _list(env, outsider, contract_artifact_id=cart).status_code == 404
    assert _record(env, outsider, contract_artifact_id=cart).status_code == 404
