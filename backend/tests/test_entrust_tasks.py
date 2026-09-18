"""任务模型测试（ENT-008）。

四组断言：
1. **创建与授权**：只有已受理委托能挂任务；需要派单权限（叠加层作用域）；
   非参与方 404 不泄漏存在性；
2. **状态机**：pending→in_progress→done / waiting / cancelled / reopen，
   非法迁移被拒；完成要求证据齐备；缺件走 waiting 而**不是**报错；
3. **固定前置条件（AC-13 本期子用例）**：自依赖与循环被拒、跨委托被拒、
   前置未完成不能开始；
4. **执行代次 fencing（R9）**：接管/改派推进代次，旧代次提交被拒。
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import assignments as assign_svc  # noqa: E402
from app.modules.entrust import tasks as svc  # noqa: E402
from app.modules.entrust.access import (  # noqa: E402
    AccessDeniedError,
    utcnow_naive,
)

_TS = "%Y-%m-%d %H:%M:%S"

OWNER = 1  # 货主
MANAGER = 900  # 经理人（持派单权限）
MANAGER2 = 901  # 另一经理人（接管/改派场景）
WORKER = 950  # 普通成员（组织角色 member，仅 entrust:view）

ALL_PERMS = '["entrust:view","entrust:assignment:claim","entrust:task:dispatch"]'


@pytest.fixture()
def session():
    """独立 SQLite 内存库会话，`ent_` 表由迁移创建。"""
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    """TestClient + 可直接造数据的会话工厂，且 ENTRUST_ENABLED=True。"""
    from app.core.config import get_settings
    from app.main import app
    from app.models import Base
    from app.modules.auth.router import get_db
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", True)

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


# ─────────────────────────────────────────── 播种助手


def _org(db, name="测试组织", status="active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": name, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager", status: str = "active") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, :s, :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str = ALL_PERMS) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "p": permissions, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _claimed(db, *, owner_id: int = OWNER, permissions: str = ALL_PERMS) -> dict:
    """组织 + 经理/普通成员 + 货主授权 + 一张**已受理**的委托单。"""
    org = _org(db)
    _member(db, org, MANAGER, role="manager")
    _member(db, org, MANAGER2, role="manager")
    _member(db, org, WORKER, role="member")
    _entrust(db, org, owner_id, permissions=permissions)
    draft = assign_svc.create_assignment(db, owner_user_id=owner_id, title="钢材运输")
    submitted = assign_svc.submit_assignment(
        db,
        assignment_id=draft["assignment_id"],
        actor_id=owner_id,
        org_id=org,
        expected_revision=draft["revision"],
    )
    claimed = assign_svc.claim_assignment(
        db, assignment_id=submitted["assignment_id"], actor_id=MANAGER
    )
    return {"org_id": org, "assignment": claimed}


def _task(db, assignment_id: int, **kwargs):
    params = {"task_type": svc.TASK_TYPE_COLLECT, "title": "收集单证"}
    params.update(kwargs)
    return svc.create_task(db, assignment_id=assignment_id, actor_id=MANAGER, **params)


# ─────────────────────────────────────────── 创建与授权


def test_create_requires_claimed_assignment(session):
    """草稿/待受理的委托不能挂任务（受理前没有责任主体）。"""
    org = _org(session)
    _member(session, org, MANAGER)
    _entrust(session, org, OWNER)
    draft = assign_svc.create_assignment(session, owner_user_id=OWNER, title="钢材运输")
    with pytest.raises(svc.TaskStateError):
        svc.create_task(
            session,
            assignment_id=draft["assignment_id"],
            actor_id=MANAGER,
            task_type=svc.TASK_TYPE_COLLECT,
            title="收集单证",
        )


def test_create_minimal_keeps_unknowns_null(session):
    """未给到期/证据/负责人 → 全部保持 NULL（不静默补默认值）。"""
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    assert task["status"] == svc.STATUS_PENDING
    assert task["revision"] == 1
    assert task["assignee_user_id"] is None
    assert task["due_at"] is None
    assert task["required_evidence"] is None
    assert task["precondition_task_id"] is None
    assert task["lease_generation"] == 0
    assert task["evidence_refs"] is None


def test_create_rejects_unknown_task_type(session):
    env_data = _claimed(session)
    with pytest.raises(svc.TaskValidationError):
        _task(session, env_data["assignment"]["assignment_id"], task_type="free_form_dag")


def test_create_rejects_unknown_evidence_kind(session):
    env_data = _claimed(session)
    with pytest.raises(svc.TaskValidationError):
        _task(session, env_data["assignment"]["assignment_id"], required_evidence=["whatever"])


def test_create_requires_dispatch_permission(session):
    """组织成员但授权作用域内没有派单权限 → 403 语义异常。"""
    env_data = _claimed(session, permissions='["entrust:view"]')
    with pytest.raises(AccessDeniedError):
        _task(session, env_data["assignment"]["assignment_id"])


def test_create_by_non_member_hidden_as_not_found(session):
    """非组织成员 → 404（不泄漏委托是否存在）。"""
    env_data = _claimed(session)
    with pytest.raises(svc.TaskNotFoundError):
        svc.create_task(
            session,
            assignment_id=env_data["assignment"]["assignment_id"],
            actor_id=123456,
            task_type=svc.TASK_TYPE_COLLECT,
            title="收集单证",
        )


# ─────────────────────────────────────────── 状态机


def test_happy_path_pending_to_done(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"], assignee_user_id=WORKER)

    started = svc.start_task(session, task_id=task["task_id"], actor_id=WORKER)
    assert started["status"] == svc.STATUS_IN_PROGRESS
    assert started["started_at"] is not None
    assert started["revision"] == 2

    done = svc.complete_task(session, task_id=task["task_id"], actor_id=WORKER)
    assert done["status"] == svc.STATUS_DONE
    assert done["completed_at"] is not None


def test_complete_without_start_rejected(session):
    """未 start 不能直接完成 —— 否则前置条件检查会被绕过。"""
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    with pytest.raises(svc.TaskStateError):
        svc.complete_task(session, task_id=task["task_id"], actor_id=MANAGER)


def test_complete_requires_required_evidence(session):
    """所需证据未齐 → 拒绝完成；补齐证据后通过且证据落库。"""
    env_data = _claimed(session)
    task = _task(
        session,
        env_data["assignment"]["assignment_id"],
        required_evidence=["document", "photo"],
    )
    svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER)
    svc.wait_task(session, task_id=task["task_id"], actor_id=MANAGER, reason="等待卸货单据")

    with pytest.raises(svc.TaskStateError) as excinfo:
        svc.complete_task(
            session,
            task_id=task["task_id"],
            actor_id=MANAGER,
            evidence_refs=[{"kind": "document", "ref": "att-001"}],
        )
    assert "photo" in str(excinfo.value), "缺件提示必须指明缺哪类证据"

    done = svc.complete_task(
        session,
        task_id=task["task_id"],
        actor_id=MANAGER,
        evidence_refs=[
            {"kind": "document", "ref": "att-001"},
            {"kind": "photo", "ref": "att-002"},
        ],
    )
    assert done["status"] == svc.STATUS_DONE
    assert {item["kind"] for item in done["evidence_refs"]} == {"document", "photo"}
    assert done["wait_reason"] is None, "完成后等待原因应清空"


def test_evidence_ref_without_source_rejected(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"], required_evidence=["document"])
    svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER)
    with pytest.raises(svc.TaskValidationError):
        svc.complete_task(
            session,
            task_id=task["task_id"],
            actor_id=MANAGER,
            evidence_refs=[{"kind": "document", "ref": "   "}],
        )


def test_wait_is_a_state_not_an_error(session):
    """缺件 → waiting（业务状态），不是硬报错；resume 后原因清空。"""
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    waiting = svc.wait_task(
        session, task_id=task["task_id"], actor_id=MANAGER, reason="缺一份卸货单据"
    )
    assert waiting["status"] == svc.STATUS_WAITING
    assert waiting["wait_reason"] == "缺一份卸货单据"

    resumed = svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER)
    assert resumed["status"] == svc.STATUS_IN_PROGRESS
    assert resumed["wait_reason"] is None


def test_wait_requires_reason(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    with pytest.raises(svc.TaskValidationError):
        svc.wait_task(session, task_id=task["task_id"], actor_id=MANAGER, reason="  ")


def test_reopen_preserves_history(session):
    """reopen：回到 pending、原因留痕、次数累加，完成证据不删除。"""
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"], required_evidence=["document"])
    svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER)
    svc.complete_task(
        session,
        task_id=task["task_id"],
        actor_id=MANAGER,
        evidence_refs=[{"kind": "document", "ref": "att-001"}],
    )

    reopened = svc.reopen_task(
        session, task_id=task["task_id"], actor_id=MANAGER, reason="单据金额与合同不一致"
    )
    assert reopened["status"] == svc.STATUS_PENDING
    assert reopened["reopen_count"] == 1
    assert reopened["last_reopen_reason"] == "单据金额与合同不一致"
    assert reopened["completed_at"] is None
    assert reopened["evidence_refs"] == [{"kind": "document", "ref": "att-001"}], "历史证据不删除"


def test_reopen_requires_reason(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER)
    svc.complete_task(session, task_id=task["task_id"], actor_id=MANAGER)
    with pytest.raises(svc.TaskValidationError):
        svc.reopen_task(session, task_id=task["task_id"], actor_id=MANAGER, reason="")


def test_cancel_open_task_but_not_done(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    cancelled = svc.cancel_task(session, task_id=task["task_id"], actor_id=MANAGER)
    assert cancelled["status"] == svc.STATUS_CANCELLED
    assert cancelled["cancelled_at"] is not None

    done_task = _task(session, env_data["assignment"]["assignment_id"])
    svc.start_task(session, task_id=done_task["task_id"], actor_id=MANAGER)
    svc.complete_task(session, task_id=done_task["task_id"], actor_id=MANAGER)
    with pytest.raises(svc.TaskStateError):
        svc.cancel_task(session, task_id=done_task["task_id"], actor_id=MANAGER)


def test_terminal_task_cannot_be_edited(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER)
    done = svc.complete_task(session, task_id=task["task_id"], actor_id=MANAGER)
    with pytest.raises(svc.TaskStateError):
        svc.update_task(
            session,
            task_id=task["task_id"],
            actor_id=MANAGER,
            expected_revision=done["revision"],
            title="改个名",
        )


def test_update_stale_revision_conflicts(session):
    """AC-11：编辑带过期 revision → 冲突异常。"""
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    svc.update_task(
        session, task_id=task["task_id"], actor_id=MANAGER, expected_revision=1, title="v2"
    )
    with pytest.raises(svc.TaskRevisionConflictError):
        svc.update_task(
            session, task_id=task["task_id"], actor_id=MANAGER, expected_revision=1, title="v3"
        )


def test_assignee_advances_task_without_dispatch_permission(session):
    """被指派人本人可推进自己的任务（执行是本职），且不依赖派单权限。

    做法：先用含派单权限的授权创建并指派任务，随后把授权**收窄为只读** ——
    此时组织内谁都拿不到派单权限，但被指派人仍应能推进自己的任务。
    """
    env_data = _claimed(session)
    aid = env_data["assignment"]["assignment_id"]
    task = _task(session, aid, assignee_user_id=WORKER)

    session.execute(
        text("UPDATE ent_entrustment SET permissions = '[\"entrust:view\"]' WHERE org_id = :o"),
        {"o": env_data["org_id"]},
    )
    session.commit()

    started = svc.start_task(session, task_id=task["task_id"], actor_id=WORKER)
    assert started["status"] == svc.STATUS_IN_PROGRESS
    done = svc.complete_task(session, task_id=task["task_id"], actor_id=WORKER)
    assert done["status"] == svc.STATUS_DONE

    # 同组织、同为组织成员的管理者此时没有派单权限 → 不能创建任务
    with pytest.raises(AccessDeniedError):
        _task(session, aid, title="越权创建")


def test_cancelled_assignment_blocks_task_changes(session):
    """委托侧状态变化后任务不可再变更（任务只在已受理状态下可操作）。"""
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    session.execute(
        text("UPDATE ent_assignment SET status = 'cancelled' WHERE id = :aid"),
        {"aid": env_data["assignment"]["assignment_id"]},
    )
    session.commit()
    with pytest.raises(svc.TaskStateError):
        svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER)


# ─────────────────────────────────────────── 固定前置条件（AC-13 本期子用例）


def test_self_dependency_rejected(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    with pytest.raises(svc.TaskPreconditionError):
        svc.set_precondition(
            session,
            task_id=task["task_id"],
            actor_id=MANAGER,
            precondition_task_id=task["task_id"],
            expected_revision=task["revision"],
        )


def test_cyclic_dependency_rejected(session):
    """三节点环 A→B→C→A 必须被拒绝（数据库约束表达不了传递闭包）。"""
    env_data = _claimed(session)
    aid = env_data["assignment"]["assignment_id"]
    a = _task(session, aid, title="A")
    b = _task(session, aid, title="B")
    c = _task(session, aid, title="C")

    svc.set_precondition(
        session,
        task_id=a["task_id"],
        actor_id=MANAGER,
        precondition_task_id=b["task_id"],
        expected_revision=a["revision"],
    )
    b = svc.get_task(session, b["task_id"])
    assert b is not None
    svc.set_precondition(
        session,
        task_id=b["task_id"],
        actor_id=MANAGER,
        precondition_task_id=c["task_id"],
        expected_revision=b["revision"],
    )
    c = svc.get_task(session, c["task_id"])
    assert c is not None
    with pytest.raises(svc.TaskPreconditionError):
        svc.set_precondition(
            session,
            task_id=c["task_id"],
            actor_id=MANAGER,
            precondition_task_id=a["task_id"],
            expected_revision=c["revision"],
        )


def test_cross_assignment_precondition_rejected(session):
    first = _claimed(session)
    second = _claimed(session, owner_id=2)
    task = _task(session, first["assignment"]["assignment_id"])
    other = _task(session, second["assignment"]["assignment_id"], title="别的委托的任务")
    with pytest.raises(svc.TaskValidationError):
        svc.set_precondition(
            session,
            task_id=task["task_id"],
            actor_id=MANAGER,
            precondition_task_id=other["task_id"],
            expected_revision=task["revision"],
        )


def test_missing_precondition_task_rejected(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    with pytest.raises(svc.TaskValidationError):
        svc.set_precondition(
            session,
            task_id=task["task_id"],
            actor_id=MANAGER,
            precondition_task_id=999999,
            expected_revision=task["revision"],
        )


def test_start_blocked_until_precondition_done(session):
    """固定前置条件的实际语义：前置未完成不能开始；完成后可开始。"""
    env_data = _claimed(session)
    aid = env_data["assignment"]["assignment_id"]
    gate = _task(session, aid, title="先收齐单证")
    downstream = _task(session, aid, title="再报价", precondition_task_id=gate["task_id"])
    assert downstream["precondition_task_id"] == gate["task_id"]

    with pytest.raises(svc.TaskStateError):
        svc.start_task(session, task_id=downstream["task_id"], actor_id=MANAGER)

    svc.start_task(session, task_id=gate["task_id"], actor_id=MANAGER)
    svc.complete_task(session, task_id=gate["task_id"], actor_id=MANAGER)

    started = svc.start_task(session, task_id=downstream["task_id"], actor_id=MANAGER)
    assert started["status"] == svc.STATUS_IN_PROGRESS


def test_precondition_can_be_cleared(session):
    env_data = _claimed(session)
    aid = env_data["assignment"]["assignment_id"]
    gate = _task(session, aid, title="gate")
    downstream = _task(session, aid, title="down", precondition_task_id=gate["task_id"])
    cleared = svc.set_precondition(
        session,
        task_id=downstream["task_id"],
        actor_id=MANAGER,
        precondition_task_id=None,
        expected_revision=downstream["revision"],
    )
    assert cleared["precondition_task_id"] is None
    resumed = svc.start_task(session, task_id=downstream["task_id"], actor_id=MANAGER)
    assert resumed["status"] == svc.STATUS_IN_PROGRESS


# ─────────────────────────────────────────── 执行代次 fencing（R9）


def test_takeover_bumps_generation_and_fences_stale_executor(session):
    """接管后旧代次提交被拒 —— 迟到结果不得覆盖人工接管后的状态。"""
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"], assignee_user_id=MANAGER)
    svc.start_task(session, task_id=task["task_id"], actor_id=MANAGER, expected_generation=0)

    taken = svc.takeover_task(session, task_id=task["task_id"], actor_id=MANAGER2)
    assert taken["assignee_user_id"] == MANAGER2
    assert taken["lease_generation"] == 1

    with pytest.raises(svc.LeaseConflictError):
        svc.complete_task(
            session,
            task_id=task["task_id"],
            actor_id=MANAGER,
            expected_generation=0,
        )

    done = svc.complete_task(
        session, task_id=task["task_id"], actor_id=MANAGER2, expected_generation=1
    )
    assert done["status"] == svc.STATUS_DONE


def test_reassign_bumps_generation_and_rejects_noop(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"], assignee_user_id=MANAGER)
    with pytest.raises(svc.TaskValidationError):
        svc.reassign_task(
            session, task_id=task["task_id"], actor_id=MANAGER, assignee_user_id=MANAGER
        )
    moved = svc.reassign_task(
        session, task_id=task["task_id"], actor_id=MANAGER, assignee_user_id=WORKER
    )
    assert moved["assignee_user_id"] == WORKER
    assert moved["lease_generation"] == 1


# ─────────────────────────────────────────── 列表


def test_list_filters_by_status_and_assignee(session):
    env_data = _claimed(session)
    aid = env_data["assignment"]["assignment_id"]
    first = _task(session, aid, title="A", assignee_user_id=MANAGER)
    _task(session, aid, title="B", assignee_user_id=WORKER)
    svc.start_task(session, task_id=first["task_id"], actor_id=MANAGER)

    total, all_items = svc.list_tasks(session, assignment_id=aid)
    assert total == 2 and len(all_items) == 2

    total, running = svc.list_tasks(session, assignment_id=aid, task_status=svc.STATUS_IN_PROGRESS)
    assert total == 1 and running[0]["task_id"] == first["task_id"]

    total, worker_tasks = svc.list_tasks(session, assignment_id=aid, assignee_user_id=WORKER)
    assert total == 1 and worker_tasks[0]["assignee_user_id"] == WORKER


def test_list_hidden_from_non_participant(session):
    env_data = _claimed(session)
    _task(session, env_data["assignment"]["assignment_id"])
    with pytest.raises(svc.TaskNotFoundError):
        svc.list_visible_tasks(
            session, user_id=123456, assignment_id=env_data["assignment"]["assignment_id"]
        )
    total, items = svc.list_visible_tasks(
        session, user_id=OWNER, assignment_id=env_data["assignment"]["assignment_id"]
    )
    assert total == 1 and len(items) == 1


def test_get_task_visible_to_owner_and_hidden_from_stranger(session):
    env_data = _claimed(session)
    task = _task(session, env_data["assignment"]["assignment_id"])
    visible, _ = svc.authorize(session, task_id=task["task_id"], user_id=OWNER)
    assert visible["task_id"] == task["task_id"]
    with pytest.raises(svc.TaskNotFoundError):
        svc.authorize(session, task_id=task["task_id"], user_id=123456)


# ─────────────────────────────────────────── API 层


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['access_token']}"}


def _api_setup(env) -> SimpleNamespace:
    """API 场景播种：货主 / 经理 / 普通成员 + 已受理委托。"""
    db = env.make_session()
    owner = _login(env.client, "shipper")
    manager = _login(env.client, "mgr")
    worker = _login(env.client, "worker")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"], role="manager")
    _member(db, org, user_id=worker["user_id"], role="member")
    _entrust(db, org, owner_id=owner["user_id"], permissions=ALL_PERMS)
    draft = assign_svc.create_assignment(db, owner_user_id=owner["user_id"], title="钢材运输")
    submitted = assign_svc.submit_assignment(
        db,
        assignment_id=draft["assignment_id"],
        actor_id=owner["user_id"],
        org_id=org,
        expected_revision=draft["revision"],
    )
    claimed = assign_svc.claim_assignment(
        db, assignment_id=submitted["assignment_id"], actor_id=manager["user_id"]
    )
    db.close()
    return SimpleNamespace(
        owner=owner, manager=manager, worker=worker, org_id=org, assignment=claimed
    )


def test_api_disabled_returns_404(client, shipper):
    """AC-22：开关关闭 → 整组端点 404（隐藏入口，不是权限拒绝）。"""
    resp = client.get(
        "/api/v1/entrust/tasks", params={"assignment_id": 1}, headers=shipper["_headers"]
    )
    assert resp.status_code == 404


def test_api_create_requires_idempotency_key(env):
    ctx = _api_setup(env)
    resp = env.client.post(
        f"/api/v1/entrust/assignments/{ctx.assignment['assignment_id']}/tasks",
        json={"task_type": "collect_documents", "title": "收集单证"},
        headers=_headers(ctx.manager),
    )
    assert resp.status_code == 400


def test_api_create_replay_same_key(env):
    """同键同体重放历史响应（不重复建任务）。"""
    ctx = _api_setup(env)
    key = uuid.uuid4().hex
    body = {"task_type": "collect_documents", "title": "收集单证"}
    url = f"/api/v1/entrust/assignments/{ctx.assignment['assignment_id']}/tasks"
    first = env.client.post(
        url, json=body, headers={**_headers(ctx.manager), "Idempotency-Key": key}
    )
    assert first.status_code == 200, first.text
    second = env.client.post(
        url, json=body, headers={**_headers(ctx.manager), "Idempotency-Key": key}
    )
    assert second.status_code == 200
    assert second.json()["task_id"] == first.json()["task_id"]

    total = env.client.get(
        "/api/v1/entrust/tasks",
        params={"assignment_id": ctx.assignment["assignment_id"]},
        headers=_headers(ctx.manager),
    )
    assert total.json()["total"] == 1


def test_api_same_key_different_body_conflicts(env):
    ctx = _api_setup(env)
    key = uuid.uuid4().hex
    url = f"/api/v1/entrust/assignments/{ctx.assignment['assignment_id']}/tasks"
    env.client.post(
        url,
        json={"task_type": "collect_documents", "title": "收集单证"},
        headers={**_headers(ctx.manager), "Idempotency-Key": key},
    )
    conflict = env.client.post(
        url,
        json={"task_type": "collect_documents", "title": "另一个标题"},
        headers={**_headers(ctx.manager), "Idempotency-Key": key},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]


def test_api_full_task_flow(env):
    """API 全链路：创建 → 详情 → start → complete → reopen。"""
    ctx = _api_setup(env)
    aid = ctx.assignment["assignment_id"]
    created = env.client.post(
        f"/api/v1/entrust/assignments/{aid}/tasks",
        json={
            "task_type": "handover",
            "title": "卸货交接",
            "assignee_user_id": ctx.worker["user_id"],
            "required_evidence": ["photo"],
        },
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["status"] == "pending" and task["required_evidence"] == ["photo"]

    detail = env.client.get(
        f"/api/v1/entrust/tasks/{task['task_id']}", headers=_headers(ctx.worker)
    )
    assert detail.status_code == 200

    started = env.client.post(
        f"/api/v1/entrust/tasks/{task['task_id']}/start",
        headers={**_headers(ctx.worker), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert started.status_code == 200 and started.json()["status"] == "in_progress"

    missing = env.client.post(
        f"/api/v1/entrust/tasks/{task['task_id']}/complete",
        json={"evidence_refs": []},
        headers={**_headers(ctx.worker), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert missing.status_code == 409, "缺必需证据必须被拒"

    done = env.client.post(
        f"/api/v1/entrust/tasks/{task['task_id']}/complete",
        json={"evidence_refs": [{"kind": "photo", "ref": "att-9"}]},
        headers={**_headers(ctx.worker), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert done.status_code == 200 and done.json()["status"] == "done"

    reopened = env.client.post(
        f"/api/v1/entrust/tasks/{task['task_id']}/reopen",
        json={"reason": "照片看不清数量"},
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert reopened.status_code == 200
    assert reopened.json()["reopen_count"] == 1


def test_api_cycle_returns_409(env):
    """自依赖经 HTTP 得 409（不是 500）。"""
    ctx = _api_setup(env)
    aid = ctx.assignment["assignment_id"]
    created = env.client.post(
        f"/api/v1/entrust/assignments/{aid}/tasks",
        json={"task_type": "quote", "title": "报价"},
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    resp = env.client.post(
        f"/api/v1/entrust/tasks/{created['task_id']}/precondition",
        json={"expected_revision": created["revision"], "precondition_task_id": created["task_id"]},
        headers=_headers(ctx.manager),
    )
    assert resp.status_code == 409
    assert "自依赖" in resp.json()["detail"]


def test_api_non_participant_gets_404(env):
    ctx = _api_setup(env)
    stranger = _login(env.client, "stranger")
    created = env.client.post(
        f"/api/v1/entrust/assignments/{ctx.assignment['assignment_id']}/tasks",
        json={"task_type": "quote", "title": "报价"},
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    hidden = env.client.get(
        f"/api/v1/entrust/tasks/{created['task_id']}", headers=_headers(stranger)
    )
    assert hidden.status_code == 404
    hidden_list = env.client.get(
        "/api/v1/entrust/tasks",
        params={"assignment_id": ctx.assignment["assignment_id"]},
        headers=_headers(stranger),
    )
    assert hidden_list.status_code == 404
    owner_view = env.client.get(
        "/api/v1/entrust/tasks",
        params={"assignment_id": ctx.assignment["assignment_id"]},
        headers=_headers(ctx.owner),
    )
    assert owner_view.status_code == 200 and owner_view.json()["total"] == 1


def test_api_patch_stale_revision_conflicts(env):
    ctx = _api_setup(env)
    created = env.client.post(
        f"/api/v1/entrust/assignments/{ctx.assignment['assignment_id']}/tasks",
        json={"task_type": "quote", "title": "报价"},
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    ok = env.client.patch(
        f"/api/v1/entrust/tasks/{created['task_id']}",
        json={"expected_revision": created["revision"], "title": "报价 v2"},
        headers=_headers(ctx.manager),
    )
    assert ok.status_code == 200
    stale = env.client.patch(
        f"/api/v1/entrust/tasks/{created['task_id']}",
        json={"expected_revision": created["revision"], "title": "报价 v3"},
        headers=_headers(ctx.manager),
    )
    assert stale.status_code == 409


def test_api_takeover_fences_old_generation(env):
    """接管后旧代次提交经 HTTP 得 409。"""
    ctx = _api_setup(env)
    created = env.client.post(
        f"/api/v1/entrust/assignments/{ctx.assignment['assignment_id']}/tasks",
        json={
            "task_type": "purchase",
            "title": "订舱确认",
            "assignee_user_id": ctx.manager["user_id"],
        },
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    ).json()

    env.client.post(
        f"/api/v1/entrust/tasks/{created['task_id']}/start",
        json={"expected_generation": 0},
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    )
    taken = env.client.post(
        f"/api/v1/entrust/tasks/{created['task_id']}/takeover",
        headers={**_headers(ctx.owner), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert taken.status_code == 403, "货主本人没有派单权限，接管应被拒"

    taken = env.client.post(
        f"/api/v1/entrust/tasks/{created['task_id']}/takeover",
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert taken.status_code == 200
    assert taken.json()["lease_generation"] == 1

    stale = env.client.post(
        f"/api/v1/entrust/tasks/{created['task_id']}/complete",
        json={"expected_generation": 0},
        headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert stale.status_code == 409
    assert "代次" in stale.json()["detail"]


# ───────────────────────── 列表：截断可观测（开放项 O-9，2026-09-18 裁定）


def test_api_list_reports_has_more_at_page_boundaries(env):
    """`has_more` 在四种页号下都给出正确结论（O-9 的判据）。

    只回 `total` 时，"被截了没有"要调用方自己算（`page * size < total`）——
    漏算一次就静默。四种情况各钉一次：

    - 整页且还有下一页 ⇒ `True`；
    - 末页不满 ⇒ `False`；
    - 恰好整页取完 ⇒ `False`（边界：没有它，"只要有下一页就 True"这类
      恒真写法也能过前两条）；
    - **越过末页**（`items` 为空）⇒ `False`。这条最关键：按 `page * size < total`
      算会得到 `True`，于是"你什么都没取到"被报成"还有更多"，
      正是"被截"信号本身在撒谎。
    """
    ctx = _api_setup(env)
    aid = ctx.assignment["assignment_id"]
    url = f"/api/v1/entrust/assignments/{aid}/tasks"
    for i in range(3):
        created = env.client.post(
            url,
            json={"task_type": "collect_documents", "title": f"单证 {i}"},
            headers={**_headers(ctx.manager), "Idempotency-Key": uuid.uuid4().hex},
        )
        assert created.status_code == 200, created.text

    def listed(**params):
        return env.client.get(
            "/api/v1/entrust/tasks",
            params={"assignment_id": aid, **params},
            headers=_headers(ctx.manager),
        )

    page1 = listed(page=1, size=2)
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["total"] == 3
    assert len(body1["items"]) == 2
    assert body1["has_more"] is True

    page2 = listed(page=2, size=2)
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["has_more"] is False

    exact = listed(page=1, size=3)
    assert exact.status_code == 200, exact.text
    body3 = exact.json()
    assert len(body3["items"]) == 3
    assert body3["has_more"] is False

    beyond = listed(page=9, size=2)
    assert beyond.status_code == 200, beyond.text
    body4 = beyond.json()
    assert body4["items"] == []
    assert body4["total"] == 3
    assert body4["has_more"] is False
