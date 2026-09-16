"""会话、Agent 作业与类型化信封测试（ENT-011 / S2 第 5~8 条）。

七组断言：
1. **信封校验（AC-08）**：非对象、字段类型错、未知成果类型 → 作业失败；
   缺必填字段与未知字段被**记录**；编造来源被标记；`requires_review` 被强制改真；
2. **提案 ≠ 动作（AC-09）**：作业成功**不产生任何成果行**，信封只是提案载体；
3. **客户投影**：内部成本类提案在服务端就被剔除（不是让前端折叠）；
4. **专业槽位**：五个专业全部可列出，只有两个开放；未开放专业建会话/提作业被拒；
5. **会话**：消息序号单调、归档后拒绝新消息、绑定不可变、无上下文会话被拒；
6. **作业状态机**：提交与执行分离、重复推进 409、取消、租约过期可回收、
   有界重试、尝试日志逐条留痕；
7. **权限与幂等（AC-10 / ENT-002）**：非参与方 404、只读成员 403、缺幂等键 400、
   同键重放、同键异体 409、开关关闭 404。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.agent.llm import LLMError  # noqa: E402
from app.modules.entrust import agentjobs as jobs  # noqa: E402
from app.modules.entrust import envelope as env_mod  # noqa: E402
from app.modules.entrust import sessions as sess  # noqa: E402

_TS = "2026-09-13 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch"]'
)
READ_ONLY_PERMS = '["entrust:view"]'


# ─────────────────────────────────────────── fixtures / 播种


@pytest.fixture()
def session():
    """独立 SQLite 内存库会话（服务层直测）。"""
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 播种会话；ENTRUST_ENABLED=True，模型走确定性 fixture。"""
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
    # 与 conftest 的 force_wechat_mock 同理：本机 .env.local 里一旦有真实 Key，
    # 用例就会真的发网络请求 —— 不可复现，且把"真实凭据"变成测试前提。
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _org(db, name="测试组织", status="active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": f"{name}-{uuid.uuid4().hex[:6]}", "s": status, "c": _TS},
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


def _assignment(
    db, *, owner_id: int, org_id: int, cargo_summary: str | None = "钢材 500 吨", quantity=None
) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, :cs, :q, :u, 'claimed', 1, :c, :c)"
        ),
        {
            "o": owner_id,
            "g": org_id,
            "t": "南宁→贵港 钢材运输",
            "cs": cargo_summary,
            "q": quantity,
            "u": "吨" if quantity is not None else None,
            "c": _TS,
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


def _task(db, *, assignment_id: int, status="pending", title="收集单证", created_by=1) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_workflow_task "
            "(assignment_id, task_type, title, status, created_by, created_at, updated_at) "
            "VALUES (:a, 'collect_documents', :t, :s, :by, :c, :c)"
        ),
        {"a": assignment_id, "t": title, "s": status, "by": created_by, "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _run(coro):
    """在本进程内跑一个协程（用例里没有事件循环，asyncio.run 最直接）。"""
    return asyncio.run(coro)


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
    """组织 + 经理成员 + 货主授权 + 一张已受理委托单。"""
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=owner["user_id"], permissions=permissions)
    aid = _assignment(db, owner_id=owner["user_id"], org_id=org)
    return manager, owner, org, eid, aid


def _make_session(env, manager, *, eid: int, aid: int | None = None, specialty="agent_01"):
    return env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": specialty,
            "title": "报价解析",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )


# ─────────────────────────────────────────── 1. 信封校验（AC-08）


def test_envelope_rejects_non_object():
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.parse_envelope(["not", "an", "object"])
    assert err.value.kind == "schema"


def test_envelope_rejects_missing_required_structure():
    """缺 `summary` / `base_revision` 这类结构字段 → 结构校验失败。"""
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.parse_envelope({"base_revision": 1})
    assert err.value.kind == "schema"


def test_envelope_rejects_wrong_container_type():
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.validate_envelope(
            {"base_revision": 1, "summary": "x", "artifact_proposals": "not-a-list"}
        )
    assert err.value.kind == "schema"


def test_envelope_rejects_unknown_artifact_type():
    """未知成果类型是**取值域**问题 → 直接失败（与"多写一个字段"不同）。"""
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.validate_envelope(
            {
                "base_revision": 1,
                "summary": "x",
                "artifact_proposals": [{"artifact_type": "settlement_final", "payload": {}}],
            }
        )
    assert err.value.kind == "unknown_artifact_type"


def test_envelope_records_missing_and_unknown_fields_without_failing():
    result = env_mod.validate_envelope(
        {
            "base_revision": 3,
            "summary": "解析完成",
            "artifact_proposals": [
                {"artifact_type": "quote_parsed", "payload": {"carrier": "某物流", "extra": 1}}
            ],
        }
    )
    assert result.proposal_missing_fields["quote_parsed"] == ["rate"]
    assert result.proposal_unknown_fields["quote_parsed"] == ["extra"]
    assert result.envelope.base_revision == 3


def test_envelope_marks_fabricated_sources():
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [
                {"kind": "attachment", "ref": "999"},
                {"kind": "assignment", "ref": "1"},
            ],
        },
        known_source_refs=frozenset({("assignment", "1")}),
    )
    assert result.unverified_sources == [{"kind": "attachment", "ref": "999"}]


def test_envelope_forces_human_review():
    """模型不能自己宣布"不用复核"。"""
    result = env_mod.validate_envelope(
        {"base_revision": 1, "summary": "x", "requires_review": False}
    )
    assert result.envelope.requires_review is True
    assert result.forced_review is True


def test_customer_projection_drops_internal_fields():
    """客户投影在**服务端**剔除内部字段，且非客户可见类型整体剔除。"""
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "artifact_proposals": [
                {
                    "artifact_type": "supplier_compare",
                    "payload": {"candidates": [{"carrier": "A", "rate": "10.00"}]},
                },
                {
                    "artifact_type": "customer_quote",
                    "payload": {"amount": "100.00", "currency": "CNY", "includes": ["运费"]},
                },
            ],
        }
    )
    projected = env_mod.project_envelope_for_operator(result, customer_visible_only=True)
    assert [p["artifact_type"] for p in projected["artifact_proposals"]] == ["customer_quote"]
    assert projected["artifact_proposals"][0]["payload"] == {
        "amount": "100.00",
        "currency": "CNY",
        "includes": ["运费"],
    }


# ─────────────────────────────────────────── 2. 专业槽位


def test_specialties_list_marks_unopened():
    items = {item["code"]: item for item in env_mod.list_specialties()}
    assert items["agent_01"]["open"] is True
    assert items["agent_02"]["open"] is True
    assert items["agent_03"]["open"] is False
    assert items["agent_05"]["open"] is False


@pytest.mark.parametrize("code", ["agent_03", "agent_04", "agent_05", "agent_99"])
def test_unopened_specialty_rejected(code):
    with pytest.raises(env_mod.SpecialtyNotOpenError):
        env_mod.assert_specialty_open(code)


# ─────────────────────────────────────────── 3. 会话（服务层）


def test_session_messages_seq_is_monotonic(session):
    org = _org(session)
    _member(session, org, user_id=1)
    eid = _entrust(session, org, owner_id=2)
    row = sess.create_session(
        session,
        entrustment_id=eid,
        assignment_id=None,
        owner_user_id=2,
        org_id=org,
        created_by=1,
        specialty="agent_01",
        title="受理问答",
    )
    seqs = [
        sess.append_message(
            session,
            session_id=row["session_id"],
            role=sess.ROLE_USER,
            content=f"第{i}条",
            source=sess.SOURCE_MANUAL,
            created_by=1,
        )["seq"]
        for i in range(1, 4)
    ]
    assert seqs == [1, 2, 3]
    assert len(sess.list_messages(session, row["session_id"])) == 3


def test_archived_session_rejects_new_message(session):
    org = _org(session)
    eid = _entrust(session, org, owner_id=2)
    row = sess.create_session(
        session,
        entrustment_id=eid,
        assignment_id=None,
        owner_user_id=2,
        org_id=org,
        created_by=1,
        specialty=None,
        title="通用会话",
    )
    sess.archive_session(session, session_id=row["session_id"], actor_id=1)
    with pytest.raises(sess.SessionArchivedError):
        sess.append_message(
            session,
            session_id=row["session_id"],
            role=sess.ROLE_USER,
            content="还能说吗",
            source=sess.SOURCE_MANUAL,
            created_by=1,
        )


def test_session_requires_binding_and_rejects_bad_role(session):
    with pytest.raises(sess.SessionBindingError):
        sess.create_session(
            session,
            entrustment_id=None,
            assignment_id=None,
            owner_user_id=1,
            org_id=None,
            created_by=1,
            specialty=None,
            title="无上下文",
        )
    org = _org(session)
    eid = _entrust(session, org, owner_id=2)
    row = sess.create_session(
        session,
        entrustment_id=eid,
        assignment_id=None,
        owner_user_id=2,
        org_id=org,
        created_by=1,
        specialty=None,
        title="通用会话",
    )
    with pytest.raises(sess.SessionError):
        sess.append_message(
            session,
            session_id=row["session_id"],
            role="root",
            content="未知角色",
            source=sess.SOURCE_MANUAL,
            created_by=1,
        )
    with pytest.raises(sess.SessionError):
        sess.append_message(
            session,
            session_id=row["session_id"],
            role=sess.ROLE_USER,
            content="未知来源",
            source="telepathy",
            created_by=1,
        )


def test_session_specialty_must_be_open(session):
    org = _org(session)
    eid = _entrust(session, org, owner_id=2)
    with pytest.raises(env_mod.SpecialtyNotOpenError):
        sess.create_session(
            session,
            entrustment_id=eid,
            assignment_id=None,
            owner_user_id=2,
            org_id=org,
            created_by=1,
            specialty="agent_03",
            title="合同助手",
        )


# ─────────────────────────────────────────── 4. 作业状态机（服务层）


def _seed_service(session):
    org = _org(session)
    _member(session, org, user_id=1)
    eid = _entrust(session, org, owner_id=2)
    aid = _assignment(session, owner_id=2, org_id=org, cargo_summary="钢材 500 吨")
    return org, eid, aid


def test_claim_next_consumes_attempt_and_execute_succeeds(session):
    _org_, eid, aid = _seed_service(session)
    _task(session, assignment_id=aid, status="waiting")
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    assert job["status"] == jobs.STATUS_QUEUED
    assert job["attempt_count"] == 0

    claimed = jobs.claim_next(session, worker_id="w1")
    assert claimed is not None
    # 尝试次数在**领取时**消耗：崩溃循环也会被 max_attempts 兜住
    assert claimed["attempt_count"] == 1
    assert claimed["status"] == jobs.STATUS_RUNNING

    scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
    result = _run(jobs.execute_claimed_job(session, job_id=claimed["job_id"], scope=scope))
    assert result["status"] == jobs.STATUS_SUCCEEDED
    assert result["envelope"] is not None
    assert result["envelope"]["requires_review"] is True
    # 受理摘要里的"500 吨"被保守抽取 → 必填字段齐备（缺项派生，不落库）
    assert result["envelope"]["missing_fields"] == []
    # 有 waiting 任务 → 风险提示（缺件会影响后续排期）
    assert any(f["code"] == "tasks_waiting" for f in result["envelope"]["findings"])
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["mocked"] is True


def test_successful_job_creates_no_artifact(session):
    """AC-09：作业成功只产出提案，**不产生任何成果行**。"""
    _org_, eid, aid = _seed_service(session)
    jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    _run(jobs.tick(session, worker_id="w1"))
    count = session.execute(text("SELECT COUNT(*) FROM ent_artifact")).scalar_one()
    assert int(count) == 0


def test_ag02_produces_proposals_but_scope_blocks_cross_specialty(session):
    """AG-02 能产出报价提案；AG-01 产出同类提案会被范围挡下（fail-closed）。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_02",
        base_revision=1,
        job_input={"quote_text": "承运人：桂航物流 38.5 元/吨 有效期至 2026-10-01"},
        created_by=1,
    )
    scope = jobs.scope_for_job(session, job, operator_user_id=1)
    assert scope.allows_artifact_type("quote_parsed") is True

    ag01_scope = sess.build_agent_scope(
        {
            "session_id": None,
            "agent_specialty": "agent_01",
            "owner_user_id": 2,
            "entrustment_id": eid,
            "assignment_id": aid,
            "org_id": scope.org_id,
        },
        operator_user_id=1,
    )
    validation = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "artifact_proposals": [
                {"artifact_type": "quote_parsed", "payload": {"carrier": "A", "rate": "1.00"}}
            ],
        }
    )
    from app.modules.entrust.agents.runner import enforce_scope

    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        enforce_scope(validation, ag01_scope)
    assert err.value.kind == "out_of_scope"


def test_bounded_retry_then_failed(session, monkeypatch):
    """外部抖动（超时）按额度重试，用尽即 failed —— 不无限重试。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=3,
        created_by=1,
    )

    async def boom(**kwargs):
        raise LLMError("timeout", "LLM 调用超时（60s）")

    monkeypatch.setattr(jobs, "run_agent", boom)

    for _ in range(3):
        claimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1")
        assert claimed is not None
        scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
        result = _run(jobs.execute_claimed_job(session, job_id=job["job_id"], scope=scope))
        assert result["error_kind"] == "llm_timeout"

    assert result["status"] == jobs.STATUS_FAILED
    assert result["attempt_count"] == 3
    assert len(result["attempts"]) == 3
    assert result["envelope"] is None
    # 额度用尽后再领也领不到
    assert jobs.claim_job(session, job_id=job["job_id"], worker_id="w2") is None


def test_non_retryable_error_fails_immediately(session, monkeypatch):
    """确定性错误（鉴权失败）不重试：重试只会烧预算。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=3,
        created_by=1,
    )

    async def boom(**kwargs):
        raise LLMError("auth", "LLM API Key 无效或无权限")

    monkeypatch.setattr(jobs, "run_agent", boom)

    claimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1")
    scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
    result = _run(jobs.execute_claimed_job(session, job_id=job["job_id"], scope=scope))
    assert result["status"] == jobs.STATUS_FAILED
    assert result["error_kind"] == "llm_auth"
    assert result["attempt_count"] == 1


def test_invalid_output_does_not_become_fact(session, monkeypatch):
    """AC-08：信封校验失败 → 作业失败且**没有信封**（谈不上成为事实）。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_02",
        base_revision=1,
        created_by=1,
    )

    async def bad(**kwargs):
        return SimpleNamespace(
            raw={"base_revision": 1, "summary": "x", "artifact_proposals": "oops"},
            raw_text="{}",
            latency_ms=5,
            mocked=True,
        )

    monkeypatch.setattr(jobs, "run_agent", bad)

    claimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1")
    scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
    result = _run(jobs.execute_claimed_job(session, job_id=job["job_id"], scope=scope))
    assert result["status"] == jobs.STATUS_FAILED
    assert result["error_kind"] == "schema"
    assert result["envelope"] is None
    assert session.execute(text("SELECT COUNT(*) FROM ent_artifact")).scalar_one() == 0


def test_lease_expiry_allows_reclaim(session):
    """worker 崩溃后租约过期，作业可被重新领取（重启不丢任务）。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=5,
        created_by=1,
    )
    now = jobs.utcnow_naive()
    first = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1", now=now)
    assert first is not None
    # 租约期内别人抢不到
    assert jobs.claim_job(session, job_id=job["job_id"], worker_id="w2", now=now) is None
    later = now + timedelta(seconds=jobs.DEFAULT_LEASE_SECONDS + 5)
    reclaimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w2", now=later)
    assert reclaimed is not None
    assert reclaimed["attempt_count"] == 2
    assert reclaimed["lease_owner"] == "w2"


def test_expired_lease_with_attempts_exhausted_is_failed(session):
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=1,
        created_by=1,
    )
    now = jobs.utcnow_naive()
    jobs.claim_job(session, job_id=job["job_id"], worker_id="w1", now=now)
    reaped = jobs.reap_expired(session, now=now + timedelta(seconds=jobs.DEFAULT_LEASE_SECONDS + 5))
    assert reaped == 1
    after = jobs.get_job(session, job["job_id"])
    assert after["status"] == jobs.STATUS_FAILED
    assert after["error_kind"] == "lease_expired"


def test_cancel_and_retry_transitions(session):
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    cancelled = jobs.cancel_job(session, job_id=job["job_id"], actor_id=1)
    assert cancelled["status"] == jobs.STATUS_CANCELLED
    # 幂等：再取消一次不报错
    assert jobs.cancel_job(session, job_id=job["job_id"], actor_id=1)["status"] == (
        jobs.STATUS_CANCELLED
    )
    with pytest.raises(jobs.AgentJobStateError):
        jobs.retry_job(session, job_id=job["job_id"], actor_id=1)

    other = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    jobs.cancel_job(session, job_id=other["job_id"], actor_id=1)
    assert jobs.claim_job(session, job_id=other["job_id"], worker_id="w") is None


def test_retry_resets_attempts(session):
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=1,
        created_by=1,
    )
    now = jobs.utcnow_naive()
    jobs.claim_job(session, job_id=job["job_id"], worker_id="w1", now=now)
    jobs.reap_expired(session, now=now + timedelta(seconds=jobs.DEFAULT_LEASE_SECONDS + 5))
    assert jobs.get_job(session, job["job_id"])["status"] == jobs.STATUS_FAILED

    retried = jobs.retry_job(session, job_id=job["job_id"], actor_id=1)
    assert retried["status"] == jobs.STATUS_QUEUED
    assert retried["attempt_count"] == 0
    assert retried["error_kind"] is None


# ─────────────────────────────────────────── 5. API 层：权限 / 幂等 / 开关


def test_create_session_and_job_end_to_end(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    _task(db, assignment_id=aid, status="waiting")

    resp = _make_session(env, manager, eid=eid, aid=aid)
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]
    assert resp.json()["agent_specialty_label"] == "委托助理"

    msg = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/messages",
        json={"content": "帮我看看还缺什么"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert msg.status_code == 200, msg.text
    assert msg.json()["role"] == "user"
    assert msg.json()["source"] == "manual"
    assert msg.json()["seq"] == 1

    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={"base_revision": 1},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 200, job.text
    jid = job.json()["job_id"]
    assert job.json()["status"] == "queued"
    assert job.json()["requires_review"] is True

    run = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["job"]["status"] == "succeeded"
    assert body["job"]["envelope"]["summary"]
    assert body["attempts"][0]["mocked"] is True
    assert "raw_output" not in body["attempts"][0]

    # 重复推进：状态机结构性拦下（不消耗尝试次数）
    again = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert again.status_code == 409

    detail = env.client.get(f"/api/v1/entrust/agent/jobs/{jid}", headers=_headers(manager))
    assert detail.status_code == 200
    assert detail.json()["job"]["attempt_count"] == 1

    # ── S2 首片：页面实际调用的两个查询 ──────────────────────────────
    # 会话页从工作台进来时只有委托单号，`load()` 靠这两个端点恢复现场。
    # 服务层那条测试证明的是过滤逻辑；这里证明的是**参数真的透传到了 HTTP 层**
    # ——只测服务层会漏掉"查询参数忘了声明"这种错。
    found = env.client.get(
        f"/api/v1/entrust/sessions?view=mine&assignment_id={aid}", headers=_headers(manager)
    )
    assert found.status_code == 200, found.text
    assert found.json()["total"] == 1
    assert found.json()["items"][0]["session_id"] == sid

    # 反事实：换一个没有会话的委托单号必须为空，而不是"忽略参数返回全部"
    none_ = env.client.get(
        "/api/v1/entrust/sessions?view=mine&assignment_id=999999", headers=_headers(manager)
    )
    assert none_.status_code == 200
    assert none_.json()["total"] == 0

    # "离开后重新进入仍可恢复"：按委托单号能取回本单作业
    jl = env.client.get(
        f"/api/v1/entrust/agent/jobs?assignment_id={aid}", headers=_headers(manager)
    )
    assert jl.status_code == 200, jl.text
    assert jl.json()["total"] == 1
    assert jl.json()["items"][0]["job_id"] == jid


def test_outsider_cannot_see_or_touch_session(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]
    outsider = _login(env.client, "stranger")

    # 非参与方：404（不区分"不存在"与"无权"）
    assert (
        env.client.get(f"/api/v1/entrust/sessions/{sid}", headers=_headers(outsider)).status_code
        == 404
    )
    assert (
        env.client.post(
            f"/api/v1/entrust/sessions/{sid}/jobs",
            json={},
            headers=_headers(outsider, uuid.uuid4().hex),
        ).status_code
        == 404
    )


def test_readable_but_write_requires_agent_job_permission(env):
    """可见性由授权链给（组织成员即可读）；**写**还要求授权里含 `entrust:agent:job`。

    这是 ENT-003 叠加层的既有语义：按货主作用域的权限判定只看**该货主的生效授权**，
    所以把授权收窄成只读后，连 manager 角色也做不了 Agent 作业 —— 想要能干活，
    货主必须显式授予这个动作。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db, permissions=READ_ONLY_PERMS)
    # 会话由服务层直接建（本用例验证的是读写权限差异，不是建会话本身）
    row = sess.create_session(
        db,
        entrustment_id=eid,
        assignment_id=aid,
        owner_user_id=owner["user_id"],
        org_id=org,
        created_by=manager["user_id"],
        specialty="agent_01",
        title="只读场景",
    )
    sid = row["session_id"]

    assert (
        env.client.get(f"/api/v1/entrust/sessions/{sid}", headers=_headers(manager)).status_code
        == 200
    )
    assert (
        env.client.post(
            f"/api/v1/entrust/sessions/{sid}/jobs",
            json={},
            headers=_headers(manager, uuid.uuid4().hex),
        ).status_code
        == 403
    )
    assert (
        env.client.post(
            f"/api/v1/entrust/sessions/{sid}/archive",
            headers=_headers(manager, uuid.uuid4().hex),
        ).status_code
        == 403
    )


def test_owner_cannot_open_manager_session(env):
    """货主不是组织成员：建会话 404（授权链判定先于权限）。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "agent_specialty": "agent_01", "title": "x"},
        headers=_headers(owner, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


def test_session_assignment_must_match_entrustment(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    other_owner = _login(env.client, "shipper2")
    stray = _assignment(db, owner_id=other_owner["user_id"], org_id=org)
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "assignment_id": stray, "title": "x"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 400


def test_unopened_specialty_endpoint_400(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    resp = _make_session(env, manager, eid=eid, aid=aid, specialty="agent_03")
    assert resp.status_code == 400
    assert "未开放" in resp.json()["detail"]


def test_specialties_endpoint_lists_five(env):
    db = env.make_session()
    manager, _, _, _, _ = _seed(env, db)
    resp = env.client.get("/api/v1/entrust/agent/specialties", headers=_headers(manager))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 5
    assert sum(1 for i in items if i["open"]) == 2


def test_idempotency_required_and_replay(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)

    missing = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "agent_specialty": "agent_01", "title": "x"},
        headers=_headers(manager),
    )
    assert missing.status_code == 400

    key = uuid.uuid4().hex
    first = _make_session(env, manager, eid=eid, aid=aid)
    assert first.status_code == 200
    replay = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": "agent_01",
            "title": "报价解析",
        },
        headers=_headers(manager, key),
    )
    assert replay.status_code == 200
    again = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": "agent_01",
            "title": "报价解析",
        },
        headers=_headers(manager, key),
    )
    assert again.status_code == 200
    assert again.json()["session_id"] == replay.json()["session_id"]

    conflict = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": "agent_01",
            "title": "换个标题",
        },
        headers=_headers(manager, key),
    )
    assert conflict.status_code == 409


def test_disabled_feature_hides_endpoints(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]

    from app.core.config import get_settings

    original = get_settings().ENTRUST_ENABLED
    get_settings().ENTRUST_ENABLED = False
    try:
        assert (
            env.client.get(f"/api/v1/entrust/sessions/{sid}", headers=_headers(manager)).status_code
            == 404
        )
        assert (
            env.client.get(
                "/api/v1/entrust/agent/specialties", headers=_headers(manager)
            ).status_code
            == 404
        )
    finally:
        get_settings().ENTRUST_ENABLED = original


def test_archived_session_blocks_new_job(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]
    arch = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/archive",
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert arch.status_code == 200, arch.text
    assert arch.json()["status"] == "archived"
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 409


def test_agent02_end_to_end_reports_cost_constraints(env):
    """AG-02：解析报价 → 提案含 quote_parsed，且缺有效期被提示为风险。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid, specialty="agent_02").json()["session_id"]
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={
            "base_revision": 1,
            "input": {
                "quote_text": "承运人：桂航物流 38.5 元/吨",
                "candidates": [
                    {"carrier": "桂航物流", "rate": "38.50"},
                    {"carrier": "西江航运", "rate": "36.00"},
                ],
            },
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 200, job.text
    jid = job.json()["job_id"]
    run = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert run.status_code == 200, run.text
    envelope = run.json()["job"]["envelope"]
    types = [p["artifact_type"] for p in envelope["artifact_proposals"]]
    assert "quote_parsed" in types
    assert "supplier_compare" in types
    assert any(f["code"] == "validity_missing" for f in envelope["findings"])
    compare = next(
        p for p in envelope["artifact_proposals"] if p["artifact_type"] == "supplier_compare"
    )
    assert compare["payload"]["selected_candidate"] == "西江航运"


def test_job_requires_specialty(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "assignment_id": aid, "title": "通用壳"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]
    assert resp.json()["agent_specialty"] is None
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 400


def test_quota_error_is_not_retryable():
    """`llm_quota`（余额/配额耗尽）**不可重试** —— 重试不会让账户有钱。

    与 `llm_timeout` / `llm_rate_limit` 对照：那些是外部抖动，重试有意义。
    """
    assert jobs.is_retryable("llm_quota") is False
    assert jobs.is_retryable("llm_bad_request") is False
    assert jobs.is_retryable("llm_timeout") is True
    assert jobs.is_retryable("llm_rate_limit") is True
    # 未分类的一律不重试（宁可停下来人工看，也不要盲目重试掩盖问题）
    assert jobs.is_retryable(None) is False
    assert jobs.is_retryable("llm_unknown") is False


def test_list_sessions_filters_by_assignment(session):
    """S2 首片：`GET /sessions` 必须能按委托单号过滤。

    会话页从工作台进来时手里**只有委托单号**，没有委托授权号。没有这个过滤，
    页面就得把整页会话拉下来在前端筛 —— 既多传数据，又会把"该组织别的单子的会话"
    一并暴露给前端。

    反事实那一条是必要的：只断言"过滤后是 1 条"证明不了过滤生效 ——
    万一建会话那一步本身只建了一条，这个断言照样过。
    """
    sess.create_session(
        session,
        entrustment_id=1,
        assignment_id=101,
        owner_user_id=7,
        org_id=3,
        created_by=7,
        specialty=None,
        title="委托 101 的会话",
    )
    sess.create_session(
        session,
        entrustment_id=1,
        assignment_id=102,
        owner_user_id=7,
        org_id=3,
        created_by=7,
        specialty=None,
        title="委托 102 的会话",
    )

    total_all, _ = sess.list_sessions(session, created_by=7)
    assert total_all == 2, "反事实：不过滤时应有 2 条（否则下面那条断言没有意义）"

    total, items = sess.list_sessions(session, created_by=7, assignment_id=102)
    assert total == 1
    assert items[0]["title"] == "委托 102 的会话"
    assert int(items[0]["assignment_id"]) == 102

    # 查一个没有会话的委托单 ⇒ 空，而不是"忽略过滤条件返回全部"
    total_none, items_none = sess.list_sessions(session, created_by=7, assignment_id=999)
    assert total_none == 0
    assert items_none == []


# ─────────────────────────── S2 首片：会话上下文（该用哪条委托授权）


def test_session_context_resolves_the_only_matching_entrustment(env):
    """经理要建会话，就必须有一个**合法**拿到授权 id 的地方。

    经理不在 `/my-entrustments` 里（那是"货主自己授权出去"的视角），
    `/my-orgs` 又只回成员身份 —— 缺了这个端点，界面只能猜一个 id 试到不报错为止。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)

    resp = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assignment_id"] == aid
    assert body["org_id"] == org
    assert body["entrustment_id"] == eid
    assert body["note"] == ""


def test_session_context_id_is_actually_accepted_by_create(env):
    """交叉断言：上下文给出的 id 必须**真的能用**。

    只断言"字段非空"不够 —— 一个查错的 id 同样非空，而界面会在建会话时撞
    400/403，表现为"能打开页面但建不了会话"。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    ctx = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert ctx["entrustment_id"] == eid, "上下文给的必须就是那条授权"
    made = _make_session(env, manager, eid=ctx["entrustment_id"], aid=aid)
    assert made.status_code == 200, made.text
    assert int(made.json()["assignment_id"]) == aid
    assert int(made.json()["entrustment_id"]) == eid


def test_session_context_agrees_with_assignment_detail_on_org(env):
    """`org_id` 必须与委托详情同一事实 —— 两处不一致时界面无从判断该信谁。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    detail = env.client.get(f"/api/v1/entrust/assignments/{aid}", headers=_headers(manager))
    assert detail.status_code == 200, detail.text
    ctx = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert ctx["org_id"] == detail.json().get("org_id")
    assert ctx["org_id"] == org


def test_session_context_does_not_guess_when_assignment_has_no_org(env):
    """反事实 ①：委托没选服务经营主体 ⇒ 不许猜一条授权回来。

    ⚠️ 这条必须用**货主本人**调用：`org_id` 一旦为 NULL，组织侧的可见性就没了
    （经理是靠组织成员身份才看得到这单），用经理会得到 404 而不是本用例要验的分支。
    反过来这也顺带证明了查找与调用者身份无关 —— 货主不是任何组织的成员，
    却仍能问出"这单所属的(货主,组织)"这件事。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    db.execute(text("UPDATE ent_assignment SET org_id = NULL WHERE id = :i"), {"i": aid})
    db.commit()

    resp = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(owner)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["org_id"] is None
    assert body["entrustment_id"] is None
    assert "未指定服务经营主体" in body["note"]


def test_session_context_reports_missing_entrustment_without_guessing(env):
    """反事实 ①b：组织与货主之间没有生效授权 ⇒ 说清楚，而不是随便给一条。

    取值用 `suspended`：`ent_entrustment.status` 的列注释是 `active/suspended`
    （不是 `revoked` —— 那个常量属别的表，别按"常量存在"就假定它属于本列）。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    db.execute(text("UPDATE ent_entrustment SET status = 'suspended' WHERE id = :i"), {"i": eid})
    db.commit()

    body = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert body["entrustment_id"] is None
    assert "没有生效中的委托授权" in body["note"]


def test_session_context_does_not_guess_between_multiple_entrustments(env):
    """反事实 ②：同一(货主, 组织)下有两条生效授权 ⇒ 一条都不自动选中。

    猜错的后果是把会话挂到**另一条**授权上（数据边界随之改变），而界面上看不出来。
    ⚠️ 前提是库里真允许两条：`ent_entrustment` **没有** (org_id, entrust_user_id)
    唯一约束（只有 `ent_org_member` 有），所以这条分支是可达的、不是纯粹的防御代码。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    second = _entrust(db, org, owner_id=owner["user_id"])
    assert second != eid, "夹具必须真的造出第二条，否则这条用例没有意义"

    body = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert body["entrustment_id"] is None
    assert "多条" in body["note"]


def test_session_context_is_invisible_to_outsiders(env):
    """反事实 ③：非参与方 404 —— 与委托详情同一条可见性判据，不是新判据。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    outsider = _login(env.client, "stranger")
    resp = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(outsider)
    )
    assert resp.status_code == 404
    assert "拒绝" in resp.text or "不存在" in resp.text
