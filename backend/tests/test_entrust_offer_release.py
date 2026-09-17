"""S3 对客发布与客户响应（BP-03 第 4/5/6/7/10 条；D1-07 / D1-11）。

这一片验的是**业务结果**，不是"端点存在"：按 HO 0917-3 的执行顺序，下一条完整交付是
「经理发布指定版本 → 货主查看冻结内容 → 接受/拒绝 → 经理看到响应」，且**权限、失效版本、
重复请求与并发处理随这条业务一起完成**。所以本文件按那四件事组织：

1. **发布指定版本**：不取最新；不可对客的类型不能发；跨授权不能发；发布时冻结客户投影；
2. **客户看冻结内容**：内容来自**发布那一刻的快照**（不是按现在的规则重算）；
   内部看不到的东西（未知/内部口径字段、发布人与来源台账）不进客户通道；
3. **接受／拒绝**：只有货主本人（经理 ⇒ 403）；未发布/已撤回/已被取代 ⇒ 409；
   同一次发布只能响应一次；
4. **来源门槛**：声明过的来源**逐条**要有核验记录（对象 + 记录），否则拒发；
   模型产出却完全没有声明记录 ⇒ 也拒发（那条缝不能静默放行）。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.entrust import artifacts as art
from app.modules.entrust import offers as svc

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
    # ⚠️ 未知字段：注册表会把它记为 unknown，客户投影必须**剔除**它
    #    （它就是"内部成本口径"这类不该到客户手上的东西）。
    "internal_cost": 30000,
}


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_agent.env` 同源）。"""
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


def _seed(env, db, *, manager_perms: str = ALL_PERMS):
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
        {"o": org, "u": owner["user_id"], "p": manager_perms, "c": _TS},
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


def _publish(env, manager, *, eid: int, artifact_id: int, revision_no: int, atts=None, key=None):
    return env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/offer-releases",
        json={
            "artifact_id": artifact_id,
            "revision_no": revision_no,
            "authorized_attachment_ids": atts,
        },
        headers=_headers(manager, key or uuid.uuid4().hex),
    )


def _respond(env, user, *, release_id: int, decision="accept", note=None, key=None):
    return env.client.post(
        f"/api/v1/entrust/offer-releases/{release_id}/responses",
        json={"decision": decision, "note": note},
        headers=_headers(user, key or uuid.uuid4().hex),
    )


# ───────────────────────────────────────── 1. 发布指定版本


def test_publish_freezes_customer_projection_at_that_revision(env):
    """发布冻结的是**那一刻的客户白名单投影**，且内部口径字段不进快照。

    关键点（裁定三）：客户是照着**当时看到的那份内容**做决定的。若发布后按"现在的投影
    规则"重算，改一次内部字段归属就可能悄悄改变客户已看到的内容 —— 而记录上还写着
    "同一个版本"。
    """
    db = env.make_session()
    manager, _owner, _out, _org, eid, aid = _seed(env, db)
    created = _create_artifact(env, manager, eid=eid, aid=aid)
    art_id = created["artifact_id"]

    resp = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1, atts=[7, 9])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revision_no"] == 1 and body["status"] == "released"
    assert body["authorized_attachment_ids"] == [7, 9], "授权附件清单必须随发布冻结"

    # ⚠️ 这两条断言不是"顺手多写"：**响应模型与服务层用不同的键名**
    #    （`snapshot` vs `customer_snapshot`），对不上时 pydantic 不报错、静默用默认值 ——
    #    表现就是"发布成功、客户快照却是空的"。必须断言**响应里真的有快照**。
    snap = body["customer_snapshot"]
    assert snap, "响应里必须带客户快照（空字典＝键名对不上，属于静默降级）"
    assert body["source_gate"]["revision_no"] == 1, "响应里必须带来源门槛状态"
    assert snap["payload"]["amount"] == 36000
    assert snap["payload"]["includes"] == ["船舶运输", "装船", "卸船"]
    assert "internal_cost" not in snap["payload"], (
        "内部成本口径字段必须被**服务端投影剔除**，而不是靠前端不显示"
    )
    assert snap["revision_no"] == 1, "快照必须自证它固定的是哪一个版本"


def test_publish_rejects_unknown_revision_and_non_customer_type(env):
    """⛔ 不取最新版本；⛔ 不可对客的类型不能发布。"""
    db = env.make_session()
    manager, _o, _x, _org, eid, aid = _seed(env, db)
    created = _create_artifact(env, manager, eid=eid, aid=aid)
    art_id = created["artifact_id"]

    resp = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=99)
    assert resp.status_code == 400, resp.text
    assert "没有版本" in str(resp.json()["detail"])

    raw = _create_artifact(
        env,
        manager,
        eid=eid,
        aid=aid,
        atype="quote_parsed",
        payload={"carrier": "西江航运有限公司", "rate": "45.00"},
    )
    resp = _publish(env, manager, eid=eid, artifact_id=raw["artifact_id"], revision_no=1)
    assert resp.status_code == 400, resp.text
    assert "白名单投影" in str(resp.json()["detail"])


def test_publish_requires_publish_permission_and_same_entrustment(env):
    """权限门禁 + 作用域自洽（A 的授权不能发布 B 的成果）。

    ⚠️ 一条**实测纠正过**的口径：权限是按 **(组织, 货主) 作用域**解析的（DR-0008），
    不是按"某一条授权记录"解析的 ⇒ 给同一个 (org, owner) 再插一条只读授权**不会**
    收回权限。所以"无发布权限"要用**另一对 (org, owner)** 来构造，否则用例会绿得
    毫无意义（第一版就是这么写错的：它期望 403，实际拿到 200）。
    """
    db = env.make_session()
    # ── 情形 A：该货主的授权里**没有** quote:publish ⇒ 403 ──
    manager_ro, _owner_ro, _x, org_ro, eid_ro, aid_ro = _seed(
        env, db, manager_perms=READ_ONLY_PERMS
    )
    art_ro = art.create_artifact(
        db,
        entrustment_id=eid_ro,
        artifact_type="customer_quote",
        payload=dict(QUOTE_PAYLOAD),
        created_by=1,
        source=art.SOURCE_MANUAL,
        assignment_id=aid_ro,
    )
    db.commit()
    resp = _publish(
        env, manager_ro, eid=eid_ro, artifact_id=int(art_ro["artifact_id"]), revision_no=1
    )
    assert resp.status_code == 403, resp.text
    assert "quote:publish" in str(resp.json()["detail"])

    # ── 情形 B：有发布权限，但成果属于**另一条**授权 ⇒ 404（不泄漏存在性）──
    manager, _owner, _y, _org, eid, aid = _seed(env, db)
    art_main = _create_artifact(env, manager, eid=eid, aid=aid)
    crossed = _publish(env, manager, eid=eid_ro, artifact_id=art_main["artifact_id"], revision_no=1)
    assert crossed.status_code == 404, (
        "这条 manager 对**自己的** (org, owner) 有发布权限 ⇒ 权限门通过，"
        "于是命中**作用域自洽**那道门：成果不属于路径上的授权 ⇒ 404（不泄漏存在性）"
        f"：{crossed.text}"
    )


# ───────────────────────────────────────── 2. 客户看冻结内容


def test_two_channels_get_different_projections(env):
    """同一个 `release_id`，经理与客户拿到的**不是同一份投影**（服务端决定）。"""
    db = env.make_session()
    manager, owner, outsider, _org, eid, aid = _seed(env, db)
    art_id = _create_artifact(env, manager, eid=eid, aid=aid)["artifact_id"]
    rid = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1).json()["release_id"]

    cust = env.client.get(f"/api/v1/entrust/offer-releases/{rid}", headers=_headers(owner))
    assert cust.status_code == 200, cust.text
    cb = cust.json()
    assert cb["content"]["amount"] == 36000
    assert "released_by" not in cb and "artifact_id" not in cb, (
        "客户通道不得出现发布人 / 内部成果 id 这类内部字段"
    )
    assert "customer_snapshot" not in cb
    assert cb["can_respond"] is True

    mgr = env.client.get(f"/api/v1/entrust/offer-releases/{rid}", headers=_headers(manager))
    assert mgr.status_code == 200, mgr.text
    mb = mgr.json()
    assert mb["customer_snapshot"]["payload"]["amount"] == 36000
    assert mb["released_by"] == manager["user_id"]
    assert mb["artifact_id"] == art_id

    out = env.client.get(f"/api/v1/entrust/offer-releases/{rid}", headers=_headers(outsider))
    assert out.status_code == 404, "局外人读一条发布 ⇒ 404，不泄漏存在性"


def test_my_offer_releases_lists_only_my_own(env):
    db = env.make_session()
    manager, owner, outsider, _org, eid, aid = _seed(env, db)
    art_id = _create_artifact(env, manager, eid=eid, aid=aid)["artifact_id"]
    _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1)

    mine = env.client.get("/api/v1/entrust/my-offer-releases", headers=_headers(owner))
    assert mine.status_code == 200 and mine.json()["total"] == 1, mine.text
    assert mine.json()["items"][0]["content"]["amount"] == 36000

    other = env.client.get("/api/v1/entrust/my-offer-releases", headers=_headers(outsider))
    assert other.status_code == 200 and other.json()["total"] == 0, (
        "客户清单的归属由服务端从登录身份推导 ⇒ 别人的发布不该出现"
    )


# ───────────────────────────────────────── 3. 接受／拒绝与失效版本


def test_manager_cannot_respond_for_customer(env):
    """**组织经理权限不能代替客户确认权限** —— 403，不是"也行"，也不是 404。"""
    db = env.make_session()
    manager, _owner, _x, _org, eid, aid = _seed(env, db)
    art_id = _create_artifact(env, manager, eid=eid, aid=aid)["artifact_id"]
    rid = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1).json()["release_id"]

    resp = _respond(env, manager, release_id=rid)
    assert resp.status_code == 403, resp.text
    assert "不能代替客户确认" in str(resp.json()["detail"])


def test_response_is_once_only_and_replays_on_same_key(
    env,
):
    """同一次发布**只能被响应一次**：同键重放返回首次结果，不同键被挡住。"""
    db = env.make_session()
    manager, owner, _x, _org, eid, aid = _seed(env, db)
    art_id = _create_artifact(env, manager, eid=eid, aid=aid)["artifact_id"]
    rid = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1).json()["release_id"]

    key = uuid.uuid4().hex
    first = _respond(env, owner, release_id=rid, decision="accept", key=key)
    assert first.status_code == 200, first.text
    assert first.json()["decision"] == "accept"
    assert first.json()["responded_revision_id"], "响应行必须冗余记录精确版本（D1-07 自证）"

    replay = _respond(env, owner, release_id=rid, decision="accept", key=key)
    assert replay.status_code == 200 and replay.json() == first.json(), "同键必须重放首次响应"

    again = _respond(env, owner, release_id=rid, decision="accept")
    assert again.status_code == 409, again.text
    assert "已经被响应过" in str(again.json()["detail"])

    # 经理侧能看到响应
    mgr = env.client.get(f"/api/v1/entrust/offer-releases/{rid}", headers=_headers(manager))
    assert mgr.json()["response"]["decision"] == "accept"
    assert mgr.json()["response"]["customer_user_id"] == owner["user_id"]


def test_superseded_and_withdrawn_versions_cannot_be_responded(env):
    """失效版本必须失败：新一次发布取代旧的；显式撤回同样让它不可响应。"""
    db = env.make_session()
    manager, owner, _x, _org, eid, aid = _seed(env, db)
    art_id = _create_artifact(env, manager, eid=eid, aid=aid)["artifact_id"]
    old = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1).json()

    # v2：追加版本后重新发布 ⇒ 旧的待响应版本被**显式取代**
    appended = env.client.post(
        f"/api/v1/entrust/artifacts/{art_id}/revisions",
        json={"payload": {**QUOTE_PAYLOAD, "amount": 34200}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert appended.status_code == 200, appended.text
    new = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=2).json()
    assert new["superseded_release_ids"] == [old["release_id"]]

    detail = env.client.get(
        f"/api/v1/entrust/offer-releases/{old['release_id']}", headers=_headers(manager)
    ).json()
    assert detail["status"] == "superseded"
    assert detail["close_reason"], "取代要留下理由，不能靠推断"

    stale = _respond(env, owner, release_id=old["release_id"])
    assert stale.status_code == 409, stale.text
    assert "不能响应" in str(stale.json()["detail"])

    # 撤回：显式动作，理由必填
    bad = env.client.post(
        f"/api/v1/entrust/offer-releases/{new['release_id']}/withdraw",
        json={"reason": ""},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert bad.status_code == 422, "理由为空必须被请求体约束挡住"

    ok = env.client.post(
        f"/api/v1/entrust/offer-releases/{new['release_id']}/withdraw",
        json={"reason": "客户要求改口径"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert ok.status_code == 200 and ok.json()["status"] == "withdrawn", ok.text
    assert _respond(env, owner, release_id=new["release_id"]).status_code == 409


def test_accepted_release_survives_and_cannot_be_withdrawn(env):
    """**已接受的旧版本永久保留**：不能被取代，也不能被撤回。"""
    db = env.make_session()
    manager, owner, _x, _org, eid, aid = _seed(env, db)
    art_id = _create_artifact(env, manager, eid=eid, aid=aid)["artifact_id"]
    rid = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1).json()["release_id"]
    assert _respond(env, owner, release_id=rid).status_code == 200

    wd = env.client.post(
        f"/api/v1/entrust/offer-releases/{rid}/withdraw",
        json={"reason": "想撤回"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert wd.status_code == 409, wd.text
    assert "已被客户响应" in str(wd.json()["detail"])

    env.client.post(
        f"/api/v1/entrust/artifacts/{art_id}/revisions",
        json={"payload": {**QUOTE_PAYLOAD, "amount": 33000}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    second = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=2)
    assert second.status_code == 409, (
        "已被接受的发布不能被取代：后续变更要**新的客户确认**，不能覆盖此前的接受事实"
    )


# ───────────────────────────────────────── 4. 发布前来源门槛


def _agent_artifact_with_declared_sources(env, db, *, eid: int, aid: int, sources) -> int:
    """造一个**模型产出**的成果版本，并按采纳的做法登记来源声明。"""
    created = art.create_artifact(
        db,
        entrustment_id=eid,
        artifact_type="customer_quote",
        payload=dict(QUOTE_PAYLOAD),
        created_by=1,
        source=art.SOURCE_AGENT,
        assignment_id=aid,
    )
    svc.record_declared_sources(
        db, artifact_id=int(created["artifact_id"]), revision_no=1, sources=sources
    )
    db.commit()
    return int(created["artifact_id"])


def test_source_gate_requires_a_record_for_every_declared_source(env):
    """ "核验要留**对象 + 记录**，不是一个勾选" —— 声明了几条就要核几条。"""
    db = env.make_session()
    manager, _owner, _x, _org, eid, aid = _seed(env, db)
    art_id = _agent_artifact_with_declared_sources(
        env,
        db,
        eid=eid,
        aid=aid,
        sources=[
            {"kind": "attachment_text", "ref": "1"},
            {"kind": "assignment", "ref": str(aid)},
        ],
    )

    blocked = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1)
    assert blocked.status_code == 400, blocked.text
    detail = blocked.json()["detail"]
    assert isinstance(detail, dict) and len(detail["pending_sources"]) == 2, (
        "拒发必须**带待核验清单**，否则调用方只知道'不行'而不知道该核什么"
    )

    # 只核一条 ⇒ 仍然拦住（这条最容易写错成"核过一条就放行"）
    one = env.client.post(
        f"/api/v1/entrust/artifacts/{art_id}/source-checks",
        json={
            "revision_no": 1,
            "source_kind": "attachment_text",
            "source_ref": "1",
            "state": "verified",
            "method": "对照上传原件逐项核对",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert one.status_code == 200, one.text
    assert one.json()["gate"]["ok"] is False and len(one.json()["gate"]["pending"]) == 1

    two = env.client.post(
        f"/api/v1/entrust/artifacts/{art_id}/source-checks",
        json={
            "revision_no": 1,
            "source_kind": "assignment",
            "source_ref": str(aid),
            "state": "verified",
            "method": "与受理单原字段比对",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert two.status_code == 200 and two.json()["gate"]["ok"] is True, two.text

    ok = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1)
    assert ok.status_code == 200, ok.text


def test_source_gate_needs_a_reason_and_distinguishes_rejected(env):
    """无依据的核验不算核验；"判定不可用"与"还没核"必须分开说。"""
    db = env.make_session()
    manager, _owner, _x, _org, eid, aid = _seed(env, db)
    art_id = _agent_artifact_with_declared_sources(
        env, db, eid=eid, aid=aid, sources=[{"kind": "attachment_text", "ref": "1"}]
    )

    resp = env.client.post(
        f"/api/v1/entrust/artifacts/{art_id}/source-checks",
        json={
            "revision_no": 1,
            "source_kind": "attachment_text",
            "source_ref": "1",
            "state": "verified",
            "method": "   ",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 400, "空白依据必须被服务端挡下（schema 只保证非空串）"

    rej = env.client.post(
        f"/api/v1/entrust/artifacts/{art_id}/source-checks",
        json={
            "revision_no": 1,
            "source_kind": "attachment_text",
            "source_ref": "1",
            "state": "rejected",
            "method": "原件缺失，无法核对",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert rej.status_code == 200, rej.text
    blocked = _publish(env, manager, eid=eid, artifact_id=art_id, revision_no=1)
    assert blocked.status_code == 400
    assert blocked.json()["detail"]["rejected_sources"], "被判定不可用的来源要单独列出"
    assert blocked.json()["detail"]["pending_sources"] == []


def test_agent_revision_without_any_declaration_cannot_be_published(env):
    """模型产出却**一条来源声明都没有** ⇒ 不得发布（那道缝不能静默放行）。"""
    db = env.make_session()
    manager, _owner, _x, _org, eid, aid = _seed(env, db)
    created = art.create_artifact(
        db,
        entrustment_id=eid,
        artifact_type="customer_quote",
        payload=dict(QUOTE_PAYLOAD),
        created_by=1,
        source=art.SOURCE_AGENT,
        assignment_id=aid,
    )
    db.commit()

    resp = _publish(env, manager, eid=eid, artifact_id=int(created["artifact_id"]), revision_no=1)
    assert resp.status_code == 400, resp.text
    assert "没有任何来源声明记录" in str(resp.json()["detail"])
