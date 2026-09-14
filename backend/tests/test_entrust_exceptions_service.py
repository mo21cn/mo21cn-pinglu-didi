"""异常与变更案件 —— **服务层**用例（DR-0013 A1 切片三）。

覆盖 DR-0013 §6.1 的验证 1～9、11～13 与 15 的服务层部分；
纯规则部分（取值域、两套转移表、C1/C2/C3 的函数形态）在 `test_entrust_exceptions.py`。

本文件刻意不依赖 HTTP：这些是**业务不变量**，用例直接打服务层，
失败时能立刻指到是规则错了还是映射错了。端点级口径在 `exceptions_api` 的用例里。

`applied` 状态说明：A1 **没有**「应用变更」的实际执行（属 A2），因此状态机上的
`applied` 在 A1 不可达；涉及该状态的用例用一条裸 SQL 置位后再验证关闭规则 ——
断言的是 §3.5 的**规则**，不是可达性。
"""

from __future__ import annotations

import inspect
import os

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import artifacts as art_svc  # noqa: E402
from app.modules.entrust import assignments as assign_svc  # noqa: E402
from app.modules.entrust import exceptions as exc  # noqa: E402
from app.modules.entrust import tasks as task_svc  # noqa: E402
from app.modules.entrust.access import AccessDeniedError, utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

OWNER = 1  # 货主
MANAGER = 900  # 经理人（持派单权限）
MANAGER2 = 901  # 另一位经理人（乐观锁/并发场景）
OUTSIDER = 980  # 完全不在任何组织里的人

ALL_PERMS = (
    '["entrust:view","entrust:assignment:claim","entrust:quote:create","entrust:task:dispatch"]'
)
VIEW_ONLY = '["entrust:view"]'


@pytest.fixture()
def db():
    """独立 SQLite 内存库会话；`ent_` 表由迁移创建（与其他 entrust 用例同款）。"""
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ─────────────────────────────────────────── 播种助手


def _org(session, name: str = "测试组织") -> int:
    result = session.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": name, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()
    return int(result.lastrowid or 0)


def _member(session, org_id: int, user_id: int, role: str = "manager") -> None:
    session.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()


def _entrust(session, org_id: int, owner_id: int, permissions: str = ALL_PERMS) -> int:
    result = session.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "p": permissions, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()
    return int(result.lastrowid or 0)


def _claim_assignment(session, *, org_id: int, owner_id: int, title: str = "钢材运输") -> dict:
    draft = assign_svc.create_assignment(session, owner_user_id=owner_id, title=title)
    submitted = assign_svc.submit_assignment(
        session,
        assignment_id=draft["assignment_id"],
        actor_id=owner_id,
        org_id=org_id,
        expected_revision=draft["revision"],
    )
    return assign_svc.claim_assignment(
        session, assignment_id=submitted["assignment_id"], actor_id=MANAGER
    )


def _env(session, *, permissions: str = ALL_PERMS, members: bool = True) -> dict:
    """组织 + 经理人 + 货主授权 + 一张已受理委托单。"""
    org = _org(session)
    if members:
        _member(session, org, MANAGER, role="manager")
        _member(session, org, MANAGER2, role="manager")
    entrustment = _entrust(session, org, OWNER, permissions=permissions)
    assignment = _claim_assignment(session, org_id=org, owner_id=OWNER)
    return {
        "org_id": org,
        "entrustment_id": entrustment,
        "assignment": assignment,
        "assignment_id": int(assignment["assignment_id"]),
    }


def _task(session, assignment_id: int, title: str = "收集单证") -> int:
    created = task_svc.create_task(
        session,
        assignment_id=assignment_id,
        actor_id=MANAGER,
        task_type=task_svc.TASK_TYPE_COLLECT,
        title=title,
    )
    return int(created["task_id"])


def _artifact(session, *, assignment_id: int, entrustment_id: int) -> dict:
    return art_svc.create_artifact(
        session,
        entrustment_id=entrustment_id,
        artifact_type="quote_parsed",
        payload={"freight": 12000, "currency": "CNY"},
        created_by=MANAGER,
        assignment_id=assignment_id,
    )


def _case(session, env: dict, **overrides) -> dict:
    """登记一个案件（默认：不要受影响项、informational）。"""
    params: dict = {
        "assignment_id": env["assignment_id"],
        "actor_id": MANAGER,
        "kind": exc.KIND_EXCEPTION,
        "title": "船期延误",
        "severity": "medium",
        "impact_kind": exc.IMPACT_INFORMATIONAL,
    }
    params.update(overrides)
    return exc.raise_case(session, **params)


def _blocking_case(session, env: dict, **overrides) -> tuple[dict, int]:
    """登记一个 `execution-blocking` 案件（C2 要求带一条受影响任务）。"""
    task_id = _task(session, env["assignment_id"])
    params: dict = {
        "severity": "high",
        "impact_kind": exc.IMPACT_EXECUTION_BLOCKING,
        "links": [{"target_kind": exc.TARGET_TASK, "target_id": task_id}],
    }
    params.update(overrides)
    return _case(session, env, **params), task_id


def _events(session, case_id: int) -> list[dict]:
    return exc.list_events(session, case_id)


# ─────────────────────────────────────────── 登记（验证 13 / C1 / C2）


def test_raise_minimal_keeps_unknowns_null(db):
    """未给原因/责任人/到期/处置 → 全部保持 NULL（不静默补默认值）。"""
    env = _env(db)
    case = _case(db, env)
    assert case["status"] == exc.STATUS_OPEN
    assert case["revision_no"] == 1
    assert case["cause"] is None
    assert case["owner_user_id"] is None
    assert case["due_at"] is None
    assert case["proposed_action"] is None
    assert case["decision_note"] is None
    assert case["decided_by"] is None
    assert case["decided_at"] is None
    assert case["basis_revision_id"] is None
    assert case["resolution_note"] is None
    assert case["closure_disposition"] is None
    assert case["closed_by"] is None
    assert case["closed_at"] is None
    assert case["raised_by_user_id"] == MANAGER
    assert case["source"] == "manual"
    assert case["links"] == []


def test_raise_writes_created_event_in_same_transaction(db):
    env = _env(db)
    case = _case(db, env)
    events = _events(db, int(case["id"]))
    assert [e["event_kind"] for e in events] == [exc.EVENT_CREATED]
    assert events[0]["seq"] == 1
    assert events[0]["actor_user_id"] == MANAGER
    assert events[0]["to_status"] == exc.STATUS_OPEN
    assert events[0]["payload"]["impact_kind"] == exc.IMPACT_INFORMATIONAL


def test_raise_requires_claimed_assignment(db):
    """未受理的委托不能登记案件（受理前没有责任主体）。"""
    org = _org(db)
    _member(db, org, MANAGER)
    _entrust(db, org, OWNER)
    draft = assign_svc.create_assignment(db, owner_user_id=OWNER, title="钢材运输")
    submitted = assign_svc.submit_assignment(
        db,
        assignment_id=draft["assignment_id"],
        actor_id=OWNER,
        org_id=org,
        expected_revision=draft["revision"],
    )
    assert submitted["status"] == assign_svc.STATUS_SUBMITTED
    with pytest.raises(exc.ExceptionCaseConflictError):
        _case(db, {"assignment_id": int(submitted["assignment_id"])})


def test_raise_on_draft_assignment_is_not_found(db):
    """尚无服务经营主体（org_id 为空）的草稿：连作用域都没有，按不存在处理。"""
    org = _org(db)
    _member(db, org, MANAGER)
    _entrust(db, org, OWNER)
    draft = assign_svc.create_assignment(db, owner_user_id=OWNER, title="钢材运输")
    with pytest.raises(exc.ExceptionCaseNotFoundError):
        _case(db, {"assignment_id": int(draft["assignment_id"])})


def test_raise_outsider_gets_not_found(db):
    """不在该组织里的人拿到的是 404 语义（不泄漏委托是否存在）。"""
    env = _env(db)
    with pytest.raises(exc.ExceptionCaseNotFoundError):
        _case(db, env, actor_id=OUTSIDER)


def test_raise_requires_dispatch_permission(db):
    """组织成员但授权作用域内没有派单权限 → 403 语义异常。"""
    env = _env(db, permissions=VIEW_ONLY)
    with pytest.raises(AccessDeniedError):
        _case(db, env)


def test_raise_rejects_request_org_mismatch(db):
    """请求带不一致的 org_id → 403（作用域由服务端派生，不接受客户端指定）。"""
    env = _env(db)
    with pytest.raises(exc.ExceptionCaseScopeError):
        _case(db, env, request_org_id=int(env["org_id"]) + 1)


def test_raise_accepts_matching_request_org(db):
    """带**一致**的 org_id 是允许的（只是冗余，不是错误）。"""
    env = _env(db)
    case = _case(db, env, request_org_id=int(env["org_id"]))
    assert case["org_id"] == env["org_id"]


def test_raise_rejects_critical_without_execution_blocking(db):
    """C1：`critical` ⇒ `execution-blocking`（单向）。"""
    env = _env(db)
    with pytest.raises(exc.ExceptionCaseError):
        _case(db, env, severity="critical", impact_kind=exc.IMPACT_REVIEW_REQUIRED)


def test_raise_rejects_execution_blocking_without_link(db):
    """C2：`execution-blocking` 必须至少一条受影响项。"""
    env = _env(db)
    with pytest.raises(exc.ExceptionCaseError):
        _case(db, env, severity="high", impact_kind=exc.IMPACT_EXECUTION_BLOCKING)


def test_raise_rejects_unknown_source_and_kind_and_blank_title(db):
    env = _env(db)
    with pytest.raises(exc.ExceptionCaseError):
        _case(db, env, source="telepathy")
    with pytest.raises(exc.ExceptionCaseError):
        _case(db, env, kind="incident")
    with pytest.raises(exc.ExceptionCaseError):
        _case(db, env, title="   ")


def test_raise_is_atomic_when_a_link_is_cross_assignment(db):
    """随案件登记的一条受影响项跨委托 → 403，且**整笔回滚**（案件不落库）。"""
    env = _env(db)
    other = _claim_assignment(db, org_id=int(env["org_id"]), owner_id=OWNER, title="另一张单")
    foreign_task = _task(db, int(other["assignment_id"]))
    with pytest.raises(exc.ExceptionCaseScopeError):
        _case(
            db,
            env,
            severity="high",
            impact_kind=exc.IMPACT_EXECUTION_BLOCKING,
            links=[{"target_kind": exc.TARGET_TASK, "target_id": foreign_task}],
        )
    total, _ = exc.list_cases(db, assignment_id=env["assignment_id"])
    assert total == 0


def test_raise_rejects_unknown_target_kind(db):
    env = _env(db)
    with pytest.raises(exc.ExceptionCaseError):
        _case(db, env, links=[{"target_kind": "invoice", "target_id": 1}])


# ─────────────────────────────────────────── 受影响项


def test_add_link_records_event_and_bumps_revision(db):
    env = _env(db)
    case = _case(db, env)
    task_id = _task(db, env["assignment_id"])
    after = exc.add_link(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        target_kind=exc.TARGET_TASK,
        target_id=task_id,
        expected_revision=int(case["revision_no"]),
    )
    assert after["revision_no"] == 2
    assert [(link["target_kind"], link["target_id"]) for link in after["links"]] == [
        (exc.TARGET_TASK, task_id)
    ]
    kinds = [e["event_kind"] for e in _events(db, int(case["id"]))]
    assert kinds == [exc.EVENT_CREATED, exc.EVENT_LINK_ADDED]


def test_add_link_rejects_duplicate(db):
    env = _env(db)
    case, task_id = _blocking_case(db, env)
    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.add_link(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            target_kind=exc.TARGET_TASK,
            target_id=task_id,
            expected_revision=int(case["revision_no"]),
        )


def test_add_link_rejects_closed_case(db):
    env = _env(db)
    case = _case(db, env)
    closed = exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_CANCELLED,
        expected_revision=int(case["revision_no"]),
        evidence_ref="chat#42",
        decision_note="重复登记，作废",
    )
    task_id = _task(db, env["assignment_id"])
    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.add_link(
            db,
            exception_id=int(closed["id"]),
            actor_id=MANAGER,
            target_kind=exc.TARGET_TASK,
            target_id=task_id,
            expected_revision=int(closed["revision_no"]),
        )


def test_remove_last_link_of_blocking_case_is_rejected(db):
    """移掉最后一条受影响项会让阻断空转 → 先显式改 impact_kind 才行。"""
    env = _env(db)
    case, _ = _blocking_case(db, env)
    link_id = int(case["links"][0]["id"])
    with pytest.raises(exc.ExceptionCaseError):
        exc.remove_link(
            db,
            exception_id=int(case["id"]),
            link_id=link_id,
            actor_id=MANAGER,
            expected_revision=int(case["revision_no"]),
        )
    assert len(exc.list_links(db, int(case["id"]))) == 1


def test_remove_link_allowed_when_impact_is_not_blocking(db):
    env = _env(db)
    task_id = _task(db, env["assignment_id"])
    case = _case(db, env, links=[{"target_kind": exc.TARGET_TASK, "target_id": task_id}])
    after = exc.remove_link(
        db,
        exception_id=int(case["id"]),
        link_id=int(case["links"][0]["id"]),
        actor_id=MANAGER,
        expected_revision=int(case["revision_no"]),
    )
    assert after["links"] == []
    assert [e["event_kind"] for e in _events(db, int(case["id"]))] == [
        exc.EVENT_CREATED,
        exc.EVENT_LINK_ADDED,
        exc.EVENT_LINK_REMOVED,
    ]


def test_add_link_rejects_cross_assignment_task(db):
    env = _env(db)
    other = _claim_assignment(db, org_id=int(env["org_id"]), owner_id=OWNER, title="另一张单")
    foreign_task = _task(db, int(other["assignment_id"]))
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseScopeError):
        exc.add_link(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            target_kind=exc.TARGET_TASK,
            target_id=foreign_task,
            expected_revision=int(case["revision_no"]),
        )


def test_add_link_rejects_unattributed_artifact(db):
    """归属为 NULL 的成果不能挂到案件上 —— 先按 DR-0012 流程归属，而不是让案件去认领。"""
    env = _env(db)
    orphan = art_svc.create_artifact(
        db,
        entrustment_id=int(env["entrustment_id"]),
        artifact_type="quote_parsed",
        payload={"freight": 12000, "currency": "CNY"},
        created_by=MANAGER,
        assignment_id=None,
    )
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseScopeError):
        exc.add_link(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            target_kind=exc.TARGET_ARTIFACT,
            target_id=int(orphan["artifact_id"]),
            expected_revision=int(case["revision_no"]),
        )


# ─────────────────────────────────────────── 记录决定（C3 / 两套状态机）


def test_decide_signature_has_no_severity_or_impact_kind(db):
    """C3 的可测形式：决定路径**根本不接受** `severity` / `impact_kind`。

    没有这个字段，就不存在「改一个展示用字段顺带解除阻断」的入口 ——
    这比事后比较值更彻底。
    """
    for func in (exc.decide, exc.close_case, exc.reopen_case):
        params = set(inspect.signature(func).parameters)
        assert "severity" not in params, func.__name__
        assert "impact_kind" not in params, func.__name__


def test_decide_rejects_close_via_decide(db):
    env = _env(db)
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.decide(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            to_status=exc.STATUS_CLOSED,
            expected_revision=int(case["revision_no"]),
        )


def test_decide_open_to_applied_is_illegal_for_both_kinds(db):
    """未列出的组合一律 409（两套状态机分别生效）。"""
    env = _env(db)
    for kind in (exc.KIND_EXCEPTION, exc.KIND_CHANGE_REQUEST):
        case = _case(db, env, kind=kind, title=f"{kind}-非法转移")
        with pytest.raises(exc.ExceptionCaseConflictError):
            exc.decide(
                db,
                exception_id=int(case["id"]),
                actor_id=MANAGER,
                to_status=exc.STATUS_APPLIED,
                expected_revision=int(case["revision_no"]),
            )


def test_decide_self_loop_is_illegal(db):
    env = _env(db)
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.decide(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            to_status=exc.STATUS_OPEN,
            expected_revision=int(case["revision_no"]),
        )


def test_decide_approved_requires_basis_revision(db):
    env = _env(db)
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseError):
        exc.decide(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            to_status=exc.STATUS_APPROVED,
            expected_revision=int(case["revision_no"]),
        )


def test_decide_approved_with_basis_records_decision_audit(db):
    env = _env(db)
    artifact = _artifact(
        db, assignment_id=env["assignment_id"], entrustment_id=int(env["entrustment_id"])
    )
    case = _case(db, env)
    approved = exc.decide(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        to_status=exc.STATUS_APPROVED,
        expected_revision=int(case["revision_no"]),
        decision_note="同意换船",
        basis_revision_id=int(artifact["current_revision_id"]),
    )
    assert approved["status"] == exc.STATUS_APPROVED
    assert approved["decided_by"] == MANAGER
    assert approved["decided_at"] is not None
    assert approved["basis_revision_id"] == artifact["current_revision_id"]
    last = _events(db, int(case["id"]))[-1]
    assert last["event_kind"] == exc.EVENT_DECIDED
    assert last["basis_revision_id"] == artifact["current_revision_id"]


def test_decide_rejects_basis_revision_from_another_assignment(db):
    env = _env(db)
    other = _claim_assignment(db, org_id=int(env["org_id"]), owner_id=OWNER, title="另一张单")
    foreign = _artifact(
        db, assignment_id=int(other["assignment_id"]), entrustment_id=int(env["entrustment_id"])
    )
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseScopeError):
        exc.decide(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            to_status=exc.STATUS_APPROVED,
            expected_revision=int(case["revision_no"]),
            basis_revision_id=int(foreign["current_revision_id"]),
        )


def test_change_request_rejects_actions_not_in_its_own_table(db):
    """`change_request` 从 `rejected` 只能 `closed` —— 而 `exception` 可以 `in_review`。

    同一句「rejected 之后能做什么」在两种 kind 下不同，正是 "as applicable to type"。
    """
    env = _env(db)
    change = _case(db, env, kind=exc.KIND_CHANGE_REQUEST, title="改数量")
    rejected = exc.decide(
        db,
        exception_id=int(change["id"]),
        actor_id=MANAGER,
        to_status=exc.STATUS_REJECTED,
        expected_revision=int(change["revision_no"]),
        decision_note="不采纳",
    )
    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.decide(
            db,
            exception_id=int(rejected["id"]),
            actor_id=MANAGER,
            to_status=exc.STATUS_IN_REVIEW,
            expected_revision=int(rejected["revision_no"]),
        )
    assert exc.allowed_transitions(exc.KIND_CHANGE_REQUEST, exc.STATUS_REJECTED) == frozenset(
        {exc.STATUS_CLOSED}
    )
    assert exc.STATUS_IN_REVIEW in exc.allowed_transitions(exc.KIND_EXCEPTION, exc.STATUS_REJECTED)


# ─────────────────────────────────────────── 驳回不解除真实异常（验证 6）


def test_rejected_exception_still_blocks(db):
    """HO 验收重点 1：驳回处置方案后真实异常仍在 ⇒ 阻断保持。"""
    env = _env(db)
    case, task_id = _blocking_case(db, env)
    assert exc.blocking_cases_for_task(db, task_id=task_id)

    rejected = exc.decide(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        to_status=exc.STATUS_REJECTED,
        expected_revision=int(case["revision_no"]),
        decision_note="方案不可行，重新提",
    )
    assert rejected["status"] == exc.STATUS_REJECTED
    still = exc.blocking_cases_for_task(db, task_id=task_id)
    assert [int(item["id"]) for item in still] == [int(case["id"])]


def test_rejected_exception_cannot_close_resolved_or_accepted_residual(db):
    """`exception` 从 `rejected` 关闭只允许 {cancelled, duplicate, superseded}。"""
    env = _env(db)
    for disposition in (exc.DISPOSITION_RESOLVED, exc.DISPOSITION_ACCEPTED_RESIDUAL):
        case = _case(db, env, title=f"驳回-{disposition}")
        rejected = exc.decide(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            to_status=exc.STATUS_REJECTED,
            expected_revision=int(case["revision_no"]),
            decision_note="方案不可行",
        )
        with pytest.raises(exc.ExceptionCaseError):
            exc.close_case(
                db,
                exception_id=int(rejected["id"]),
                actor_id=MANAGER,
                closure_disposition=disposition,
                expected_revision=int(rejected["revision_no"]),
                evidence_ref="chat#7",
                decision_note="方案不可行",
                resolution_note="已处理",
            )


# ─────────────────────────────────────────── 关闭校验（验证 5）


def test_close_requires_disposition(db):
    """不做默认值填充：不给 disposition 一律 400。"""
    env = _env(db)
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseError):
        exc.close_case(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            closure_disposition="",
            expected_revision=int(case["revision_no"]),
            evidence_ref="chat#1",
            decision_note="作废",
        )


def test_close_requires_evidence_ref(db):
    env = _env(db)
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseError):
        exc.close_case(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            closure_disposition=exc.DISPOSITION_CANCELLED,
            expected_revision=int(case["revision_no"]),
            evidence_ref="   ",
            decision_note="重复登记",
        )


def test_close_cancelled_requires_decision_note(db):
    env = _env(db)
    case = _case(db, env)
    with pytest.raises(exc.ExceptionCaseError):
        exc.close_case(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            closure_disposition=exc.DISPOSITION_CANCELLED,
            expected_revision=int(case["revision_no"]),
            evidence_ref="chat#1",
        )


def test_close_cancelled_accepts_note_given_in_the_same_call(db):
    """说明可以在关闭的那一次调用里补写（用「提供的或既有的」值判定）。"""
    env = _env(db)
    case = _case(db, env)
    closed = exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_DUPLICATE,
        expected_revision=int(case["revision_no"]),
        evidence_ref="chat#9",
        decision_note="与 #12 重复",
    )
    assert closed["status"] == exc.STATUS_CLOSED
    assert closed["closure_disposition"] == exc.DISPOSITION_DUPLICATE
    assert closed["closed_by"] == MANAGER
    assert closed["closed_at"] is not None
    last = _events(db, int(case["id"]))[-1]
    assert last["event_kind"] == exc.EVENT_CLOSED
    assert last["evidence_ref"] == "chat#9"
    assert last["from_status"] == exc.STATUS_OPEN
    assert last["to_status"] == exc.STATUS_CLOSED


def _force_applied(session, case_id: int) -> dict:
    """把案件直接置为 `applied`（A1 无实际应用命令，见模块 docstring）。"""
    session.execute(
        text("UPDATE ent_exception SET status = :st WHERE id = :cid"),
        {"st": exc.STATUS_APPLIED, "cid": case_id},
    )
    session.commit()
    forced = exc.get_case(session, case_id)
    assert forced is not None
    return forced


def test_close_resolved_rejects_artifact_link_without_applied_revision(db):
    env = _env(db)
    artifact = _artifact(
        db, assignment_id=env["assignment_id"], entrustment_id=int(env["entrustment_id"])
    )
    case = _case(
        db,
        env,
        links=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": int(artifact["artifact_id"])}],
    )
    forced = _force_applied(db, int(case["id"]))
    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.close_case(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            closure_disposition=exc.DISPOSITION_RESOLVED,
            expected_revision=int(forced["revision_no"]),
            evidence_ref="chat#5",
            resolution_note="已换船",
        )

    db.execute(
        text("UPDATE ent_exception_link SET applied_revision_id = :rid WHERE exception_id = :cid"),
        {"rid": int(artifact["current_revision_id"]), "cid": int(case["id"])},
    )
    db.commit()
    closed = exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_RESOLVED,
        expected_revision=int(forced["revision_no"]),
        evidence_ref="chat#5",
        resolution_note="已换船",
    )
    assert closed["closure_disposition"] == exc.DISPOSITION_RESOLVED


def test_close_resolved_ignores_task_links(db):
    """任务 link 没有版本号，不参与 `resolved` 的「已应用」要求（否则 resolved 成死局）。"""
    env = _env(db)
    case, _ = _blocking_case(db, env)
    forced = _force_applied(db, int(case["id"]))
    closed = exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_RESOLVED,
        expected_revision=int(forced["revision_no"]),
        evidence_ref="chat#6",
        resolution_note="任务已重排",
    )
    assert closed["status"] == exc.STATUS_CLOSED


def test_close_accepted_residual_rejects_critical(db):
    """critical 按 C1 必然阻断，不能以残留关闭。"""
    env = _env(db)
    case, _ = _blocking_case(db, env, severity="critical")
    forced = _force_applied(db, int(case["id"]))
    with pytest.raises(exc.ExceptionCaseError):
        exc.close_case(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            closure_disposition=exc.DISPOSITION_ACCEPTED_RESIDUAL,
            expected_revision=int(forced["revision_no"]),
            evidence_ref="chat#8",
            decision_note="接受残留",
            resolution_note="部分缓解",
        )


def test_close_accepted_residual_accepts_non_critical_with_both_notes(db):
    env = _env(db)
    case, _ = _blocking_case(db, env, severity="high")
    forced = _force_applied(db, int(case["id"]))
    closed = exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_ACCEPTED_RESIDUAL,
        expected_revision=int(forced["revision_no"]),
        evidence_ref="chat#10",
        decision_note="接受残留",
        resolution_note="已加保赔条款",
    )
    assert closed["closure_disposition"] == exc.DISPOSITION_ACCEPTED_RESIDUAL


# ─────────────────────────────────────────── 重开与审计同事务（验证 7 / 8）


def test_reopen_requires_reason(db):
    env = _env(db)
    case = _case(db, env)
    closed = exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_CANCELLED,
        expected_revision=int(case["revision_no"]),
        evidence_ref="chat#1",
        decision_note="误登记",
    )
    with pytest.raises(exc.ExceptionCaseError):
        exc.reopen_case(
            db,
            exception_id=int(closed["id"]),
            actor_id=MANAGER,
            reason="  ",
            expected_revision=int(closed["revision_no"]),
        )


def test_reopen_then_close_twice_keeps_full_history(db):
    """HO 验收重点 3：两轮 closed + 一次 reopened 事件齐备，`seq` 严格递增。"""
    env = _env(db)
    case = _case(db, env)
    case_id = int(case["id"])
    first = exc.close_case(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_CANCELLED,
        expected_revision=int(case["revision_no"]),
        evidence_ref="chat#1",
        decision_note="误登记",
    )
    reopened = exc.reopen_case(
        db,
        exception_id=case_id,
        actor_id=MANAGER2,
        reason="客户又说要处理",
        expected_revision=int(first["revision_no"]),
    )
    assert reopened["status"] == exc.STATUS_OPEN
    assert reopened["closure_disposition"] is None
    assert reopened["closed_by"] is None
    assert reopened["closed_at"] is None

    second = exc.close_case(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_SUPERSEDED,
        expected_revision=int(reopened["revision_no"]),
        evidence_ref="chat#2",
        decision_note="被 #20 取代",
    )

    events = _events(db, case_id)
    assert [e["event_kind"] for e in events] == [
        exc.EVENT_CREATED,
        exc.EVENT_CLOSED,
        exc.EVENT_REOPENED,
        exc.EVENT_CLOSED,
    ]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert events[2]["note"] == "客户又说要处理"
    assert events[2]["from_status"] == exc.STATUS_CLOSED
    assert events[2]["to_status"] == exc.STATUS_OPEN
    assert int(second["revision_no"]) == int(reopened["revision_no"]) + 1


def test_reopen_audit_failure_rolls_back_status(db, monkeypatch):
    """HO 第 4 条：审计写失败 ⇒ 状态一并回滚，不出现「一侧生效」。"""
    env = _env(db)
    case = _case(db, env)
    case_id = int(case["id"])
    closed = exc.close_case(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_CANCELLED,
        expected_revision=int(case["revision_no"]),
        evidence_ref="chat#1",
        decision_note="误登记",
    )
    before = _events(db, case_id)

    def _boom(*args, **kwargs):
        raise RuntimeError("审计写入失败（用例注入）")

    monkeypatch.setattr(exc, "_append_event", _boom)
    with pytest.raises(RuntimeError):
        exc.reopen_case(
            db,
            exception_id=case_id,
            actor_id=MANAGER,
            reason="重开",
            expected_revision=int(closed["revision_no"]),
        )
    monkeypatch.undo()

    fresh = exc.get_case(db, case_id)
    assert fresh is not None
    assert fresh["status"] == exc.STATUS_CLOSED
    assert [e["event_kind"] for e in _events(db, case_id)] == [e["event_kind"] for e in before]


# ─────────────────────────────────────────── 乐观锁


def test_stale_expected_revision_conflicts(db):
    env = _env(db)
    case = _case(db, env)
    task_id = _task(db, env["assignment_id"])
    exc.add_link(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        target_kind=exc.TARGET_TASK,
        target_id=task_id,
        expected_revision=int(case["revision_no"]),
    )
    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.decide(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            to_status=exc.STATUS_IN_REVIEW,
            expected_revision=int(case["revision_no"]),
        )


# ─────────────────────────────────────────── 隔离与投影（验证 11 / 12）


def test_cases_do_not_leak_across_assignments_of_the_same_owner(db):
    """HO 验收重点 7：同一货主的两张委托，案件互不可见（列表、阻断、工作台同源）。"""
    env = _env(db)
    other = _claim_assignment(db, org_id=int(env["org_id"]), owner_id=OWNER, title="另一张单")
    case_a = _case(db, env, title="甲单异常")
    _case(db, {"assignment_id": int(other["assignment_id"])}, title="乙单异常")

    total_a, items_a = exc.list_cases(db, assignment_id=env["assignment_id"])
    assert total_a == 1
    assert [int(i["id"]) for i in items_a] == [int(case_a["id"])]

    total_b, items_b = exc.list_cases(db, assignment_id=int(other["assignment_id"]))
    assert total_b == 1
    assert int(items_b[0]["id"]) != int(case_a["id"])

    # 写侧同样按精确等值判定：把乙单的任务挂到甲单的案件上一律 403
    foreign_task = _task(db, int(other["assignment_id"]), title="乙单任务")
    with pytest.raises(exc.ExceptionCaseScopeError):
        exc.add_link(
            db,
            exception_id=int(case_a["id"]),
            actor_id=MANAGER,
            target_kind=exc.TARGET_TASK,
            target_id=foreign_task,
            expected_revision=int(case_a["revision_no"]),
        )
    assert len(exc.list_links(db, int(case_a["id"]))) == 0


def test_list_cases_filters_by_status_and_kind(db):
    env = _env(db)
    _case(db, env, title="异常1")
    _case(db, env, kind=exc.KIND_CHANGE_REQUEST, title="变更1")
    total_all, _ = exc.list_cases(db, assignment_id=env["assignment_id"])
    total_change, items = exc.list_cases(
        db, assignment_id=env["assignment_id"], kind=exc.KIND_CHANGE_REQUEST
    )
    total_open, _ = exc.list_cases(db, assignment_id=env["assignment_id"], status=exc.STATUS_OPEN)
    total_closed, _ = exc.list_cases(
        db, assignment_id=env["assignment_id"], status=exc.STATUS_CLOSED
    )
    assert (total_all, total_change, total_open, total_closed) == (2, 1, 2, 0)
    assert items[0]["kind"] == exc.KIND_CHANGE_REQUEST


def test_count_open_cases_excludes_closed(db):
    env = _env(db)
    case = _case(db, env)
    assert exc.count_open_cases(db, assignment_id=env["assignment_id"]) == 1
    exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_CANCELLED,
        expected_revision=int(case["revision_no"]),
        evidence_ref="chat#1",
        decision_note="误登记",
    )
    assert exc.count_open_cases(db, assignment_id=env["assignment_id"]) == 0


def test_internal_projection_exposes_decision_and_affected(db):
    env = _env(db)
    case, task_id = _blocking_case(db, env)
    approved = exc.decide(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        to_status=exc.STATUS_APPROVED,
        expected_revision=int(case["revision_no"]),
        decision_note="同意推迟装船",
        basis_revision_id=int(
            _artifact(
                db,
                assignment_id=env["assignment_id"],
                entrustment_id=int(env["entrustment_id"]),
            )["current_revision_id"]
        ),
    )
    view = exc.project_case_internal(approved, links=exc.list_links(db, int(case["id"])))
    assert view["case_id"] == int(case["id"])
    assert view["blocking"] is True
    assert view["decision"]["note"] == "同意推迟装船"
    assert view["decision"]["by"] == MANAGER
    assert view["affected"] == [
        {
            "link_id": int(approved["links"][0]["id"]),
            "target_kind": exc.TARGET_TASK,
            "target_id": task_id,
            "applied_revision_id": None,
        }
    ]


def test_customer_projection_hides_exception_entirely(db):
    """**异常一律不得对客** —— 不看严重度、不看状态。"""
    env = _env(db)
    case = _case(db, env, severity="low", impact_kind=exc.IMPACT_INFORMATIONAL)
    assert exc.project_case_for_customer(case, links=[]) is None


def test_customer_projection_hides_internal_sources(db):
    env = _env(db)
    for source in ("manual", "chat", "agent_proposal"):
        case = _case(db, env, kind=exc.KIND_CHANGE_REQUEST, source=source, title=f"来源-{source}")
        assert exc.project_case_for_customer(case, links=[]) is None


def test_customer_projection_of_own_request_has_no_excluded_fields(db):
    """HO 验收重点 7：对客投影不得出现任何内部字段。"""
    env = _env(db)
    case = _case(
        db,
        env,
        kind=exc.KIND_CHANGE_REQUEST,
        source="customer",
        title="想改装期",
        severity="high",
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        cause="客户排产推迟",
        proposed_action="顺延一周",
    )
    links = [
        {
            "id": 1,
            "target_kind": exc.TARGET_ARTIFACT,
            "target_id": 71,
            "applied_revision_id": None,
            "artifact_type": "customer_quote",
        },
        {
            "id": 2,
            "target_kind": exc.TARGET_ARTIFACT,
            "target_id": 72,
            "applied_revision_id": None,
            "artifact_type": "supplier_compare",
        },
    ]
    view = exc.project_case_for_customer(case, links=links)
    assert view is not None
    assert set(view) == {"case_id", "title", "status", "raised_at", "artifacts"}
    assert set(view) & exc.CUSTOMER_EXCLUDED_FIELDS == set()
    # 双层白名单：只有客户可见类型的成果引用才出现
    assert view["artifacts"] == [{"artifact_id": 71}]


def test_customer_projection_refuses_link_without_type(db):
    """没带类型 = 未知，不得因为「查不到」而放行。"""
    env = _env(db)
    case = _case(db, env, kind=exc.KIND_CHANGE_REQUEST, source="customer", title="想改装期")
    links = [
        {"id": 1, "target_kind": exc.TARGET_ARTIFACT, "target_id": 71, "applied_revision_id": None}
    ]
    view = exc.project_case_for_customer(case, links=links)
    assert view is not None
    assert view["artifacts"] == []


# ─────────────────────────────────────────── 读取可见性（复用 authz 单一入口）


def test_load_visible_case_allows_owner_and_org_member(db):
    """货主本人与该组织的成员都能读；用的是 `authz` 那条链，不另写一份判据。"""
    env = _env(db)
    case = _case(db, env)
    case_id = int(case["id"])
    assert int(exc.load_visible_case(db, exception_id=case_id, user_id=OWNER)["id"]) == case_id
    assert int(exc.load_visible_case(db, exception_id=case_id, user_id=MANAGER2)["id"]) == case_id
    with pytest.raises(Exception) as err:
        exc.load_visible_case(db, exception_id=case_id, user_id=OUTSIDER)
    assert getattr(err.value, "status_code", None) == 404


def test_load_visible_case_missing_id_is_not_found(db):
    _env(db)
    with pytest.raises(exc.ExceptionCaseNotFoundError):
        exc.load_visible_case(db, exception_id=99999, user_id=OWNER)


# ─────────────────────────────────────────── 阻断查询（验证 9 的判据侧）


def test_blocking_cases_for_task_matches_only_linked_task(db):
    env = _env(db)
    case, task_id = _blocking_case(db, env)
    other_task = _task(db, env["assignment_id"], title="无关任务")
    assert [int(c["id"]) for c in exc.blocking_cases_for_task(db, task_id=task_id)] == [
        int(case["id"])
    ]
    assert exc.blocking_cases_for_task(db, task_id=other_task) == []


def test_blocking_cases_for_task_ignores_non_blocking_impact(db):
    env = _env(db)
    task_id = _task(db, env["assignment_id"])
    _case(
        db,
        env,
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        links=[{"target_kind": exc.TARGET_TASK, "target_id": task_id}],
    )
    assert exc.blocking_cases_for_task(db, task_id=task_id) == []


def test_blocking_cases_for_task_drops_closed_cases(db):
    env = _env(db)
    case, task_id = _blocking_case(db, env)
    exc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=MANAGER,
        closure_disposition=exc.DISPOSITION_CANCELLED,
        expected_revision=int(case["revision_no"]),
        evidence_ref="chat#3",
        decision_note="误登记",
    )
    assert exc.blocking_cases_for_task(db, task_id=task_id) == []


def test_list_blocking_cases_uses_the_same_judgement(db):
    env = _env(db)
    case, _ = _blocking_case(db, env)
    _case(db, env, title="不阻断的异常", impact_kind=exc.IMPACT_INFORMATIONAL)
    blocking = exc.list_blocking_cases(db, assignment_id=env["assignment_id"])
    assert [int(c["id"]) for c in blocking] == [int(case["id"])]
