"""成果归属（`ent_artifact.assignment_id`）—— DR-0012 的验收测试。

覆盖六个面（与 DR-0012 §验证方式一一对应）：

1. **存量数据库升级**：老库（无 `assignment_id`）升级后列与索引到位，
   且**历史行保持 NULL** —— 不按 (货主, 组织) 猜测回填；重复执行幂等；
2. **同客户多委托隔离**：同一货主同一组织下的两张委托，成果互不串台
   （这是原缺陷的核心场景）；
3. **跨单访问拒绝**：非参与方读某张委托的成果清单一律 404，不给 403；
4. **归属有效性**：跨货主/跨组织/未选定主体的委托单一律拒绝，不存在则 404；
5. **Agent 采纳**：归属由作业行派生（不由请求体声明）、`source=agent`、
   作业状态与提案类型校验、幂等重放、**作业重试不产生第二份成果**；
6. **版本引用**：清单与详情给出同一对 `current_revision_id` / `current_revision_no`。
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

_TS = "2026-09-13 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch"]'
)
_CREATE_URL = "/api/v1/entrust/entrustments/{eid}/artifacts"
_LIST_URL = "/api/v1/entrust/assignments/{aid}/artifacts"
_ADOPT_URL = "/api/v1/entrust/agent/jobs/{jid}/adopt"

# AG-02 的确定性 fixture 输入（与 test_entrust_agent 的端到端用例一致）：
# 该输入会产出 quote_parsed 与 supplier_compare 两份提案。
_QUOTE_INPUT = {
    "quote_text": "承运人：桂航物流 38.5 元/吨",
    "candidates": [
        {"carrier": "桂航物流", "rate": "38.50"},
        {"carrier": "西江航运", "rate": "36.00"},
    ],
}


# ─────────────────────────────────────────── fixtures / 播种


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 播种会话；`ENTRUST_ENABLED=True`，模型与微信都走确定性 mock。"""
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
    # 与 conftest.force_wechat_mock 同理：不能把"本机填了真实 Key"变成测试前提。
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _org(db, name="测试组织") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": f"{name}-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": _TS},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str = ALL_PERMS) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "p": permissions, "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _assignment(db, *, owner_id: int, org_id: int | None) -> int:
    """造一张已受理的委托单（`org_id=None` 表示尚未选定服务经营主体的草稿）。"""
    result = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, '钢材 500 吨', NULL, NULL, :s, 1, :c, :c)"
        ),
        {
            "o": owner_id,
            "g": org_id,
            "t": "南宁→贵港 钢材运输",
            "s": "claimed" if org_id is not None else "draft",
            "c": _TS,
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _seed(env, db, *, permissions: str = ALL_PERMS):
    """组织 + 经理 + 货主授权 + 一张已受理委托单。返回 (manager, owner, org, eid, aid)。"""
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=owner["user_id"], permissions=permissions)
    aid = _assignment(db, owner_id=owner["user_id"], org_id=org)
    return manager, owner, org, eid, aid


def _create(
    env, manager: dict, eid: int, *, assignment_id: int | None = None, key: str | None = None
):
    body: dict = {
        "artifact_type": "quote_parsed",
        "payload": {"carrier": "船东A", "rate": "38 元/吨"},
    }
    if assignment_id is not None:
        body["assignment_id"] = assignment_id
    return env.client.post(
        _CREATE_URL.format(eid=eid), json=body, headers=_headers(manager, key or uuid.uuid4().hex)
    )


def _make_session(env, manager, *, eid: int, aid: int | None, specialty: str = "agent_02"):
    return env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": specialty,
            "title": "方案与采购",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )


def _run_job_to_success(env, manager, *, eid: int, aid: int | None) -> int:
    """建会话 → 提作业 → 推进一次；返回成功作业的 job_id。"""
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={"base_revision": 1, "input": _QUOTE_INPUT},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 200, job.text
    jid = int(job.json()["job_id"])
    run = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert run.status_code == 200, run.text
    assert run.json()["job"]["status"] == "succeeded"
    return jid


def _artifact_rows(db) -> list[tuple]:
    return list(db.execute(text("SELECT id, assignment_id FROM ent_artifact ORDER BY id")))


# ─────────────────────────────────────────── 1. 存量数据库升级


def test_legacy_db_upgrade_adds_column_index_and_keeps_history_null():
    """老库升级：列与索引到位；**历史行保持 NULL**（不猜测回填）；重复执行幂等。"""
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        # 1) 只应用最初的 ent_artifact 模块 → 复刻"归属机制上线之前"的库结构
        first = apply_pending(engine, module_filter="ent_artifact")
        assert [e.module for e in first] == ["ent_artifact"] * len(first) and first
        assert "assignment_id" not in {
            col["name"] for col in inspect(engine).get_columns("ent_artifact")
        }

        # 2) 造一行**历史数据**（归属机制之前产生的成果）
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO ent_artifact "
                "(entrustment_id, artifact_type, current_revision_id, status, created_at,"
                " updated_at) VALUES (1, 'quote_parsed', NULL, 'active', :ts, :ts)",
                {"ts": _TS},
            )

        # 3) 升级
        executed = apply_pending(engine)
        modules = {e.module for e in executed}
        assert "ent_artifact_assignment" in modules, f"升级未执行归属迁移：{modules}"

        columns = {col["name"] for col in inspect(engine).get_columns("ent_artifact")}
        assert "assignment_id" in columns
        indexes = {idx["name"] for idx in inspect(engine).get_indexes("ent_artifact")}
        assert "idx_ent_artifact_assignment" in indexes

        # 4) 历史行保持 NULL —— 绝不按 (货主, 组织) 猜测回填
        with engine.begin() as conn:
            rows = list(conn.exec_driver_sql("SELECT id, assignment_id FROM ent_artifact"))
        assert rows and all(row[1] is None for row in rows), f"历史行被改写了：{rows}"

        # 5) 幂等：再跑一次不应有任何待执行条目
        assert apply_pending(engine) == []
    finally:
        engine.dispose()


# ─────────────────────────────────────────── 2. 同客户多委托隔离


def test_same_owner_two_assignments_do_not_share_artifacts(env):
    """同一货主、同一组织的两张委托：各自的成果清单只含自己的成果。"""
    db = env.make_session()
    manager, owner, org, eid, aid1 = _seed(env, db)
    aid2 = _assignment(db, owner_id=owner["user_id"], org_id=org)

    first = _create(env, manager, eid, assignment_id=aid1).json()
    second = _create(env, manager, eid, assignment_id=aid2, key=uuid.uuid4().hex).json()
    assert first["assignment_id"] == aid1
    assert second["assignment_id"] == aid2

    listing1 = env.client.get(_LIST_URL.format(aid=aid1), headers=_headers(manager))
    listing2 = env.client.get(_LIST_URL.format(aid=aid2), headers=_headers(manager))
    assert listing1.status_code == 200, listing1.text
    items1 = listing1.json()["items"]
    assert [i["artifact_id"] for i in items1] == [first["artifact_id"]]
    assert listing2.json()["items"] and [i["artifact_id"] for i in listing2.json()["items"]] == [
        second["artifact_id"]
    ]

    # 版本引用：清单里的 current_revision_id / no 与详情完全一致（同成果同版本）
    item = items1[0]
    detail = env.client.get(
        f"/api/v1/entrust/artifacts/{first['artifact_id']}", headers=_headers(manager)
    ).json()
    assert item["current_revision_id"] == detail["current_revision_id"]
    assert item["current_revision_no"] == detail["current_revision"]["revision_no"]
    assert item["assignment_id"] == aid1


def test_unassigned_total_reports_legacy_rows_without_guessing(env):
    """未归属（NULL）的成果不进清单，但必须被**如实报数**出来。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)

    legacy = _create(env, manager, eid)  # 不带 assignment_id（历史形态）
    bound = _create(env, manager, eid, assignment_id=aid, key=uuid.uuid4().hex)
    assert legacy.json()["assignment_id"] is None
    assert bound.json()["assignment_id"] == aid

    listing = env.client.get(_LIST_URL.format(aid=aid), headers=_headers(manager)).json()
    assert [i["artifact_id"] for i in listing["items"]] == [bound.json()["artifact_id"]]
    assert listing["total"] == 1
    assert listing["unassigned_total"] == 1, "存量未归属成果被静默隐藏了"


# ─────────────────────────────────────────── 3. 跨单访问拒绝


def test_cross_owner_and_stranger_get_404(env):
    """非参与方读委托成果清单一律 404（不区分"不存在"与"无权"）。

    参与方口径 = `authz.assert_can_view_assignment`：**货主本人，或该委托所属组织的成员**。
    因此负例必须避开「目标委托所属组织的成员」这一身份 —— 组织内经理读**本组织**的
    委托单是**合法可见**的（正常业务），拿它当"越权"会把正确实现判成失败。
    真正的跨单负例是：**甲组织的经理去读乙组织的委托单**。

    断言消息只打角色标签，**不把登录返回体打进输出**（其中含 `access_token`）。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)

    other_owner = _login(env.client, "shipper2")
    other_org = _org(db, "别的组织")
    other_mgr = _login(env.client, "mgr2")
    _member(db, other_org, user_id=other_mgr["user_id"])
    other_aid = _assignment(db, owner_id=other_owner["user_id"], org_id=other_org)

    stranger = _login(env.client, "nobody")

    # ① 甲组织的经理读乙组织的委托单 → 404（真正的跨组织/跨单）
    # ② 与两张委托都无关的陌生用户 → 404
    for label, token in (("甲组织经理", manager), ("无关用户", stranger)):
        resp = env.client.get(_LIST_URL.format(aid=other_aid), headers=_headers(token))
        assert resp.status_code == 404, (
            f"{label}越权读到了他人委托的成果清单（HTTP {resp.status_code}）"
        )

    # 反向确认：不是把所有人都拒了 —— 目标委托的组织内经理与货主本人都应 200
    assert (
        env.client.get(_LIST_URL.format(aid=other_aid), headers=_headers(other_mgr)).status_code
        == 200
    ), "该委托所属组织的经理对本组织委托应当可见（这是正常业务，不是越权）"
    assert (
        env.client.get(_LIST_URL.format(aid=other_aid), headers=_headers(other_owner)).status_code
        == 200
    )
    assert env.client.get(_LIST_URL.format(aid=aid), headers=_headers(manager)).status_code == 200


# ─────────────────────────────────────────── 4. 归属有效性


def test_reject_attribution_to_other_owner_assignment(env):
    """跨货主归属 → 400（否则 A 的成果会挂到 B 的委托上）。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    other_owner = _login(env.client, "shipper2")
    stray = _assignment(db, owner_id=other_owner["user_id"], org_id=org)

    resp = _create(env, manager, eid, assignment_id=stray)
    assert resp.status_code == 400, resp.text
    assert _artifact_rows(db) == [], "归属校验失败却留下了成果行"


def test_reject_attribution_to_assignment_without_org(env):
    """委托单尚未选定服务经营主体（草稿）→ 400。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    draft = _assignment(db, owner_id=owner["user_id"], org_id=None)

    resp = _create(env, manager, eid, assignment_id=draft)
    assert resp.status_code == 400, resp.text
    assert _artifact_rows(db) == []


def test_reject_attribution_to_missing_assignment(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)

    resp = _create(env, manager, eid, assignment_id=999_999)
    assert resp.status_code == 404, resp.text
    assert _artifact_rows(db) == []


# ─────────────────────────────────────────── 5. Agent 采纳


def test_adopt_derives_assignment_from_job_and_marks_source_agent(env):
    """采纳：归属取自作业行（不由请求体声明），来源记为 agent，进该委托的成果清单。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    jid = _run_job_to_success(env, manager, eid=eid, aid=aid)

    adopted = env.client.post(
        _ADOPT_URL.format(jid=jid),
        json={
            "artifact_type": "quote_parsed",
            "payload": {"carrier": "桂航物流", "rate": "38.50"},
            "note": "人工确认后采纳",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert adopted.status_code == 200, adopted.text
    body = adopted.json()
    assert body["assignment_id"] == aid, "归属必须由作业行派生"
    assert body["current_revision"]["source"] == "agent"
    assert body["current_revision"]["revision_no"] == 1

    listing = env.client.get(_LIST_URL.format(aid=aid), headers=_headers(manager)).json()
    assert [i["artifact_id"] for i in listing["items"]] == [body["artifact_id"]]


def test_adopt_rejects_type_the_job_did_not_propose(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    jid = _run_job_to_success(env, manager, eid=eid, aid=aid)

    resp = env.client.post(
        _ADOPT_URL.format(jid=jid),
        json={"artifact_type": "settlement_draft", "payload": {}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 400, resp.text
    assert _artifact_rows(db) == []


def test_adopt_requires_succeeded_job(env):
    """未成功的作业没有可采纳的提案（信封只在 succeeded 落库）。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={"base_revision": 1, "input": _QUOTE_INPUT},
        headers=_headers(manager, uuid.uuid4().hex),
    ).json()

    resp = env.client.post(
        _ADOPT_URL.format(jid=job["job_id"]),
        json={"artifact_type": "quote_parsed", "payload": {}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 409, resp.text


def test_adopt_requires_idempotency_key_and_replays_same_artifact(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    jid = _run_job_to_success(env, manager, eid=eid, aid=aid)
    body = {"artifact_type": "quote_parsed", "payload": {"carrier": "桂航物流", "rate": "38.50"}}

    missing_key = env.client.post(_ADOPT_URL.format(jid=jid), json=body, headers=_headers(manager))
    assert missing_key.status_code == 400

    first = env.client.post(
        _ADOPT_URL.format(jid=jid), json=body, headers=_headers(manager, "adopt-1")
    )
    second = env.client.post(
        _ADOPT_URL.format(jid=jid), json=body, headers=_headers(manager, "adopt-1")
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["artifact_id"] == second.json()["artifact_id"]
    assert len(_artifact_rows(db)) == 1


def test_retrying_succeeded_job_creates_no_second_artifact(env):
    """作业重试：已成功作业不可重试，且**不会**因此多出一份成果。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    jid = _run_job_to_success(env, manager, eid=eid, aid=aid)
    adopted = env.client.post(
        _ADOPT_URL.format(jid=jid),
        json={"artifact_type": "quote_parsed", "payload": {"carrier": "桂航物流", "rate": "38.50"}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert adopted.status_code == 200
    assert len(_artifact_rows(db)) == 1

    retry = env.client.post(
        f"/api/v1/entrust/agent/jobs/{jid}/retry", headers=_headers(manager, uuid.uuid4().hex)
    )
    assert retry.status_code == 409, retry.text
    assert len(_artifact_rows(db)) == 1, "重试不应产生副作用（第二份成果）"

    # 再次推进同样被状态机拦下
    assert (
        env.client.post(
            f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager)
        ).status_code
        == 409
    )
    assert len(_artifact_rows(db)) == 1


def test_adopt_requires_quote_create_permission(env):
    """采纳 = 创建成果，权限同创建：授权里去掉 `quote:create` 后 403。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    jid = _run_job_to_success(env, manager, eid=eid, aid=aid)

    db.execute(
        text("UPDATE ent_entrustment SET permissions = :p WHERE id = :eid"),
        {"p": '["entrust:view","entrust:agent:job"]', "eid": eid},
    )
    db.commit()

    resp = env.client.post(
        _ADOPT_URL.format(jid=jid),
        json={"artifact_type": "quote_parsed", "payload": {"carrier": "桂航物流", "rate": "38.50"}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 403, resp.text
    assert _artifact_rows(db) == []


def test_adopt_by_outsider_is_404(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    jid = _run_job_to_success(env, manager, eid=eid, aid=aid)
    outsider = _login(env.client, "nobody")

    resp = env.client.post(
        _ADOPT_URL.format(jid=jid),
        json={"artifact_type": "quote_parsed", "payload": {}},
        headers=_headers(outsider, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


def test_adopt_without_entrustment_is_rejected(env):
    """只挂委托单（无授权）的作业不能采纳：成果必须挂在生效授权下，且不猜授权。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    # 直接造一个只有 assignment_id 的作业行（内部流程可能这样建）
    result = db.execute(
        text(
            "INSERT INTO ent_agent_job "
            "(session_id, entrustment_id, assignment_id, task_id, artifact_id, specialty, status,"
            " attempt_count, max_attempts, base_revision, requires_review, created_by,"
            " created_at, updated_at) "
            "VALUES (NULL, NULL, :aid, NULL, NULL, 'agent_02', 'succeeded', 0, 3, 1, 1, :by,"
            " :ts, :ts)"
        ),
        {"aid": aid, "by": manager["user_id"], "ts": _TS},
    )
    db.commit()
    jid = int(result.lastrowid or 0)

    resp = env.client.post(
        _ADOPT_URL.format(jid=jid),
        json={"artifact_type": "quote_parsed", "payload": {}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 400, resp.text
