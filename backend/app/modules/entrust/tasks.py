"""工作任务服务层 —— 任务模型、状态流转、固定前置条件（ENT-008）。

对应计划 §5.2 S1 第 4 条：**任务模型 + 状态流转 + 证据要求字段 + 固定前置条件与
自依赖/循环检查**；以及 §3.3「任务」组的接口能力（创建/编辑/列表/状态流转/接管/
reopen/改派 + 固定前置条件的合法性校验）。

状态机::

    pending ──start──> in_progress ──complete──> done ──reopen──> pending
       │                    │
       ├────── wait ────────┤                （缺件 → waiting，**不是硬报错**）
       │          ↓         │
       │      waiting ──start┘
       └───────── cancel ───┴──────────────────────────────────> cancelled

**完成只允许从 in_progress / waiting 进入** —— pending 必须先 start，否则前置条件
检查会被绕过（规则要落在唯一入口上才有意义）。

固定前置条件（R1 的**全部**依赖语义，AC-13 本期子用例）
------------------------------------------------------
* 一个任务至多一个前置任务（`precondition_task_id`）—— 本模块**不声明**支持通用
  Dependency / DAG，也不提供图形化依赖编辑（R2）；
* **自依赖**（`precondition == self`）与**循环**（沿前置链走回自身）一律拒绝；
* 前置未完成时**不能开始**：`start` 检查前置任务状态为 `done`，否则 409；
* 跨委托的前置条件一律拒绝 —— 委托之间不互相阻塞。

权限（叠加层，ENT-003）
----------------------
* 读：货主本人或委托所属组织成员（`PERM_VIEW`）；非参与方 **404**（不泄漏存在性）；
* 管理动作（创建/编辑/改派/接管/前置条件/撤回/reopen）：`PERM_TASK_DISPATCH`，
  且必须是对**该货主**的生效授权（作用域）；
* 执行动作（开始/缺件/完成）：**被指派人本人**即可推进自己的任务，或者持有
  `PERM_TASK_DISPATCH` 的管理者代操作 —— 执行是本职，不需要派单权限。

执行代次（fencing，R9）
----------------------
`lease_generation` 在**人工接管或改派**时 +1。执行方提交结果时可带
`expected_generation`：代次不匹配即 409 —— 迟到的 Agent / 旧执行者结果不得覆盖
接管后的人工状态。本模块只做任务层的裁决点，Agent 侧接入在 S3/S4。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust.access import (
    PERM_TASK_DISPATCH,
    PERM_VIEW,
    AccessContext,
    AccessDeniedError,
    assert_can,
    resolve_context,
)

# ── 状态 ────────────────────────────────────────────────────────────────────

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_WAITING = "waiting"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

OPEN_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_WAITING)
TERMINAL_STATUSES = (STATUS_DONE, STATUS_CANCELLED)
COMPLETABLE_STATUSES = (STATUS_IN_PROGRESS, STATUS_WAITING)

# ── 固定取值域 ──────────────────────────────────────────────────────────────
# R1 按工作台的业务槽位固定任务类型；未知类型拒绝（不静默接受，也不假装通用）。

TASK_TYPE_COLLECT = "collect_documents"
TASK_TYPE_QUOTE = "quote"
TASK_TYPE_PURCHASE = "purchase"
TASK_TYPE_CONTRACT = "contract"
TASK_TYPE_EXECUTION = "execution"
TASK_TYPE_HANDOVER = "handover"
TASK_TYPE_SETTLEMENT = "settlement"

TASK_TYPES = frozenset(
    {
        TASK_TYPE_COLLECT,
        TASK_TYPE_QUOTE,
        TASK_TYPE_PURCHASE,
        TASK_TYPE_CONTRACT,
        TASK_TYPE_EXECUTION,
        TASK_TYPE_HANDOVER,
        TASK_TYPE_SETTLEMENT,
    }
)

#: 证据类型（`required_evidence` 的取值域）：这些是"要求什么证据"的类别，
#: 具体证据内容放 `evidence_refs[].ref`，本层不解释其业务含义。
EVIDENCE_KINDS = frozenset(
    {"document", "photo", "email", "receipt", "contract", "payment", "confirmation"}
)

#: 前置链遍历上限：正常数据不会这么深；超出只可能是脏数据，宁可报错也不死循环。
_MAX_PRECONDITION_DEPTH = 1000

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 任务只能挂在**已受理**的委托上（受理前没有责任主体，派单无意义）。
ASSIGNMENT_ACTIVE_STATUS = "claimed"

_TASK_COLS = (
    "t.id, t.assignment_id, t.task_type, t.title, t.status, t.assignee_user_id, "
    "t.due_at, t.precondition_task_id, t.required_evidence, t.evidence_refs, "
    "t.wait_reason, t.reopen_count, t.last_reopen_reason, t.lease_generation, "
    "t.revision, t.created_by, t.started_at, t.completed_at, t.cancelled_at, "
    "t.created_at, t.updated_at, a.owner_user_id AS owner_user_id, "
    "a.org_id AS org_id, a.status AS assignment_status"
)


# ── 异常（HTTP 层按语义映射） ────────────────────────────────────────────────


class TaskError(RuntimeError):
    """任务服务的基础异常。"""


class TaskNotFoundError(TaskError):
    """任务不存在，或调用方无权知晓其存在。HTTP 层应转 404。"""


class TaskValidationError(TaskError):
    """入参或业务规则不合法（未知任务类型/证据类型、前置任务不存在或跨委托等）。→400。"""


class TaskPreconditionError(TaskError):
    """前置条件非法：自依赖或循环。→409。"""


class TaskStateError(TaskError):
    """当前状态不允许该操作（含"前置任务未完成"）。→409。"""


class TaskRevisionConflictError(TaskError):
    """`expected_revision` 过期。→409。"""


class LeaseConflictError(TaskError):
    """执行代次过期（人工已接管/改派）。→409。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 ent_ 表时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FORMAT)


def _parse_ts(raw: Any) -> str | None:
    """时间入参规整为存储格式；datetime 直接格式化，字符串原样交给 DB 校验。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _json_list_or_none(raw: Any) -> list[Any] | None:
    """解析 JSON 数组列；空/非法返回 None（**空与未知不同**，不静默补空数组）。"""
    if raw is None or raw == "":
        return None
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _text_ts(raw: Any) -> str | None:
    """时间列 → 统一文本表示。

    SQLite 的 DATETIME 存 TEXT，取出来是 `str`；MySQL 的 DATETIME 取出来是
    `datetime` —— 若不归一，响应模型（`str | None`）会在 MySQL 上校验失败。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _row_to_task(row: Any) -> dict[str, Any]:
    return {
        "task_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "task_type": str(row["task_type"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "assignee_user_id": (
            int(row["assignee_user_id"]) if row["assignee_user_id"] is not None else None
        ),
        "due_at": _text_ts(row["due_at"]),
        "precondition_task_id": (
            int(row["precondition_task_id"]) if row["precondition_task_id"] is not None else None
        ),
        "required_evidence": _json_list_or_none(row["required_evidence"]),
        "evidence_refs": _json_list_or_none(row["evidence_refs"]),
        "wait_reason": row["wait_reason"],
        "reopen_count": int(row["reopen_count"]),
        "last_reopen_reason": row["last_reopen_reason"],
        "lease_generation": int(row["lease_generation"]),
        "revision": int(row["revision"]),
        "created_by": int(row["created_by"]),
        "started_at": _text_ts(row["started_at"]),
        "completed_at": _text_ts(row["completed_at"]),
        "cancelled_at": _text_ts(row["cancelled_at"]),
        "created_at": _text_ts(row["created_at"]),
        "updated_at": _text_ts(row["updated_at"]),
        # 授权上下文（不投影给前端，仅服务层使用）
        "owner_user_id": int(row["owner_user_id"]),
        "org_id": int(row["org_id"]) if row["org_id"] is not None else None,
        "assignment_status": str(row["assignment_status"]),
    }


def get_task(session: Session, task_id: int) -> dict[str, Any] | None:
    """按 ID 读取任务（含所属委托的货主/组织，供授权判断）。"""
    row = (
        session.execute(
            text(
                f"SELECT {_TASK_COLS} FROM ent_workflow_task t "
                "JOIN ent_assignment a ON a.id = t.assignment_id WHERE t.id = :tid"
            ),
            {"tid": task_id},
        )
        .mappings()
        .first()
    )
    return _row_to_task(row) if row is not None else None


# ── 授权 ────────────────────────────────────────────────────────────────────


def _visible_task(session: Session, *, task_id: int, user_id: int) -> dict[str, Any]:
    """取"对该用户可见"的任务；不可见与不存在**都**抛 NotFound（不泄漏存在性）。"""
    task = get_task(session, task_id)
    if task is None:
        raise TaskNotFoundError(f"任务 {task_id} 不存在")
    if task["owner_user_id"] == user_id:
        return task
    context = resolve_context(session, user_id=user_id)
    if (
        task["org_id"] is not None
        and task["org_id"] in context.org_ids
        and context.can(PERM_VIEW, org_id=task["org_id"])
    ):
        return task
    raise TaskNotFoundError(f"任务 {task_id} 不存在")


def authorize(
    session: Session,
    *,
    task_id: int,
    user_id: int,
    permission: str | None = None,
    allow_assignee: bool = False,
) -> tuple[dict[str, Any], AccessContext | None]:
    """可见性（404）→ 权限（403）的统一入口。

    Args:
        permission: 需要的权限代码；None 表示只读。
        allow_assignee: 被指派人本人可跳过权限检查（执行是本职，不需要派单权限）。
    """
    task = _visible_task(session, task_id=task_id, user_id=user_id)
    if allow_assignee and task["assignee_user_id"] == user_id:
        return task, None
    if permission is not None:
        context = assert_can(
            session, user_id=user_id, permission=permission, owner_user_id=task["owner_user_id"]
        )
        return task, context
    return task, None


def _assert_assignment_active(task: dict[str, Any]) -> None:
    """任务所属委托必须处于已受理状态，否则任务不可变更。"""
    if task["assignment_status"] != ASSIGNMENT_ACTIVE_STATUS:
        raise TaskStateError(
            f"委托状态为 {task['assignment_status']}，任务不可变更"
            f"（任务只在委托为 {ASSIGNMENT_ACTIVE_STATUS} 时可操作）"
        )


# ── 入参校验 ────────────────────────────────────────────────────────────────


def _assert_task_type(task_type: str) -> str:
    if task_type not in TASK_TYPES:
        raise TaskValidationError(
            f"未知任务类型 {task_type!r}；R1 固定取值域：{sorted(TASK_TYPES)}"
        )
    return task_type


def _normalize_required_evidence(raw: list[str] | None) -> list[str] | None:
    """规整所需证据类型：取值域校验 + 去重保序；空列表视为未要求（存 NULL）。"""
    if not raw:
        return None
    bad = [kind for kind in raw if kind not in EVIDENCE_KINDS]
    if bad:
        raise TaskValidationError(f"未知证据类型 {bad}；R1 取值域：{sorted(EVIDENCE_KINDS)}")
    unique: list[str] = []
    for kind in raw:
        if kind not in unique:
            unique.append(kind)
    return unique


def _normalize_evidence_refs(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """规整证据条目：每条必须带 kind 与 ref，且 kind 在取值域内。"""
    if not raw:
        return None
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TaskValidationError(f"证据条目必须是对象，实际为 {type(item).__name__}")
        kind = str(item.get("kind", ""))
        ref = str(item.get("ref", "") or "").strip()
        if kind not in EVIDENCE_KINDS:
            raise TaskValidationError(f"未知证据类型 {kind!r}；R1 取值域：{sorted(EVIDENCE_KINDS)}")
        if not ref:
            raise TaskValidationError("证据条目的 ref 不能为空（证据必须有来源）")
        normalized.append({"kind": kind, "ref": ref})
    return normalized


def _assert_title(title: str) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        raise TaskValidationError("任务标题不能为空")
    return cleaned


# ── 固定前置条件：自依赖与循环检查（AC-13 本期子用例） ───────────────────────


def _assert_precondition_legal(
    session: Session, *, task_id: int, assignment_id: int, precondition_task_id: int | None
) -> None:
    """校验固定前置条件：自依赖、跨委托、循环。"""
    if precondition_task_id is None:
        return
    if precondition_task_id == task_id:
        raise TaskPreconditionError("任务不能以自己为前置条件（自依赖）")

    precondition = get_task(session, precondition_task_id)
    if precondition is None:
        raise TaskValidationError(f"前置任务 {precondition_task_id} 不存在")
    if precondition["assignment_id"] != assignment_id:
        raise TaskValidationError("前置任务必须属于同一委托（跨委托依赖 R1 不支持）")

    # 沿前置链上行：若能回到本任务，说明本任务是前置的祖先 → 形成环
    cursor: int | None = precondition_task_id
    depth = 0
    while cursor is not None:
        if cursor == task_id:
            raise TaskPreconditionError("前置条件形成循环依赖")
        depth += 1
        if depth > _MAX_PRECONDITION_DEPTH:
            raise TaskPreconditionError(
                f"前置链条深度超过 {_MAX_PRECONDITION_DEPTH}，拒绝写入（疑似脏数据）"
            )
        row = (
            session.execute(
                text("SELECT precondition_task_id FROM ent_workflow_task WHERE id = :tid"),
                {"tid": cursor},
            )
            .mappings()
            .first()
        )
        cursor = (
            int(row["precondition_task_id"])
            if row is not None and row["precondition_task_id"] is not None
            else None
        )


def _assert_precondition_satisfied(session: Session, task: dict[str, Any]) -> None:
    """开始任务前检查固定前置条件：前置任务必须已完成。"""
    precondition_id = task["precondition_task_id"]
    if precondition_id is None:
        return
    precondition = get_task(session, precondition_id)
    if precondition is None:
        raise TaskStateError(f"前置任务 {precondition_id} 不存在，无法开始（数据异常）")
    if precondition["status"] != STATUS_DONE:
        raise TaskStateError(
            f"前置任务 {precondition_id} 状态为 {precondition['status']}，未完成前不能开始本任务"
        )


# ── 创建 / 编辑 ─────────────────────────────────────────────────────────────


def create_task(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    task_type: str,
    title: str,
    assignee_user_id: int | None = None,
    due_at: Any = None,
    required_evidence: list[str] | None = None,
    precondition_task_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """在**已受理**的委托下创建任务（管理动作，需派单权限）。"""
    assignment = (
        session.execute(
            text("SELECT id, owner_user_id, org_id, status FROM ent_assignment WHERE id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .first()
    )
    if assignment is None:
        raise TaskNotFoundError(f"委托 {assignment_id} 不存在")
    if str(assignment["status"]) != ASSIGNMENT_ACTIVE_STATUS:
        raise TaskStateError(
            f"委托状态为 {assignment['status']}，只有已受理（{ASSIGNMENT_ACTIVE_STATUS}）"
            "的委托才能创建任务"
        )

    owner_user_id = int(assignment["owner_user_id"])
    org_id = int(assignment["org_id"]) if assignment["org_id"] is not None else None
    context = resolve_context(session, user_id=actor_id)
    if org_id is None or org_id not in context.org_ids:
        # 不是该组织成员：连委托都不该看见 → 404（不泄漏存在性）
        raise TaskNotFoundError(f"委托 {assignment_id} 不存在")
    assert_can(
        session, user_id=actor_id, permission=PERM_TASK_DISPATCH, owner_user_id=owner_user_id
    )

    _assert_task_type(task_type)
    title_clean = _assert_title(title)
    evidence = _normalize_required_evidence(required_evidence)

    current = now or utcnow_naive()
    insert = cast(
        CursorResult[Any],
        session.execute(
            text(
                "INSERT INTO ent_workflow_task "
                "(assignment_id, task_type, title, status, assignee_user_id, due_at, "
                " precondition_task_id, required_evidence, evidence_refs, wait_reason, "
                " reopen_count, last_reopen_reason, lease_generation, revision, created_by, "
                " started_at, completed_at, cancelled_at, created_at, updated_at) "
                "VALUES (:aid, :ttype, :title, :status, :assignee, :due, :pre, :req_ev, NULL, "
                " NULL, 0, NULL, 0, 1, :actor, NULL, NULL, NULL, :ts, :ts)"
            ),
            {
                "aid": assignment_id,
                "ttype": task_type,
                "title": title_clean,
                "status": STATUS_PENDING,
                "assignee": assignee_user_id,
                "due": _parse_ts(due_at),
                "pre": precondition_task_id,
                "req_ev": json.dumps(evidence, ensure_ascii=False) if evidence else None,
                "actor": actor_id,
                "ts": _fmt(current),
            },
        ),
    )
    new_id = int(insert.lastrowid or 0)
    # 前置条件检查放在写库前不可行（需要新行 id 参与循环判定），故此处失败即回滚整笔
    try:
        _assert_precondition_legal(
            session,
            task_id=new_id,
            assignment_id=assignment_id,
            precondition_task_id=precondition_task_id,
        )
    except TaskError:
        session.rollback()
        raise

    session.commit()
    created = get_task(session, new_id)
    assert created is not None
    return created


def update_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    expected_revision: int,
    title: str | None = None,
    assignee_user_id: int | None = None,
    clear_assignee: bool = False,
    due_at: Any = None,
    clear_due_at: bool = False,
    required_evidence: list[str] | None = None,
    clear_required_evidence: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """编辑任务（管理动作，需派单权限）；终态任务须先 reopen 才能改。"""
    task, _ = authorize(session, task_id=task_id, user_id=actor_id, permission=PERM_TASK_DISPATCH)
    _assert_assignment_active(task)
    if task["status"] in TERMINAL_STATUSES:
        raise TaskStateError(
            f"任务状态为 {task['status']}，终态任务不可编辑（如需继续请先 reopen）"
        )

    current = now or utcnow_naive()
    sets: list[str] = []
    params: dict[str, Any] = {"tid": task_id, "ts": _fmt(current), "rev": expected_revision}

    if title is not None:
        sets.append("title = :title")
        params["title"] = _assert_title(title)
    if clear_assignee:
        sets.append("assignee_user_id = NULL")
    elif assignee_user_id is not None:
        sets.append("assignee_user_id = :assignee")
        params["assignee"] = assignee_user_id
    if clear_due_at:
        sets.append("due_at = NULL")
    elif due_at is not None:
        sets.append("due_at = :due")
        params["due"] = _parse_ts(due_at)
    if clear_required_evidence:
        sets.append("required_evidence = NULL")
    elif required_evidence is not None:
        evidence = _normalize_required_evidence(required_evidence)
        sets.append("required_evidence = :req_ev")
        params["req_ev"] = json.dumps(evidence, ensure_ascii=False) if evidence else None

    if not sets:
        raise TaskValidationError("没有需要修改的字段")

    return _apply_update(
        session, task_id=task_id, expected_revision=expected_revision, sets=sets, params=params
    )


def set_precondition(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    precondition_task_id: int | None,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """设置/清除固定前置条件（管理动作）；自依赖与循环一律拒绝。"""
    task, _ = authorize(session, task_id=task_id, user_id=actor_id, permission=PERM_TASK_DISPATCH)
    _assert_assignment_active(task)
    if task["status"] in TERMINAL_STATUSES:
        raise TaskStateError(f"任务状态为 {task['status']}，终态任务不能修改前置条件")
    _assert_precondition_legal(
        session,
        task_id=task_id,
        assignment_id=task["assignment_id"],
        precondition_task_id=precondition_task_id,
    )

    current = now or utcnow_naive()
    return _apply_update(
        session,
        task_id=task_id,
        expected_revision=expected_revision,
        sets=["precondition_task_id = :pre"],
        params={
            "tid": task_id,
            "ts": _fmt(current),
            "rev": expected_revision,
            "pre": precondition_task_id,
        },
    )


def _apply_update(
    session: Session,
    *,
    task_id: int,
    expected_revision: int,
    sets: list[str],
    params: dict[str, Any],
) -> dict[str, Any]:
    """条件 UPDATE（`WHERE revision = :expected`），以受影响行数裁决版本冲突。

    与 ENT-005 同一口径：先读后写在真并发下有竞态窗口，乐观锁必须落在 UPDATE 上。
    """
    result = cast(
        CursorResult[Any],
        session.execute(
            text(
                f"UPDATE ent_workflow_task SET {', '.join(sets)}, "
                "revision = revision + 1, updated_at = :ts "
                "WHERE id = :tid AND revision = :rev"
            ),
            params,
        ),
    )
    if result.rowcount == 0:
        session.rollback()
        fresh = get_task(session, task_id)
        if fresh is None:
            raise TaskNotFoundError(f"任务 {task_id} 不存在")
        raise TaskRevisionConflictError(
            f"任务 {task_id} 当前版本为 {fresh['revision']}，期望 {expected_revision}"
        )
    session.commit()
    updated = get_task(session, task_id)
    assert updated is not None
    return updated


# ── 状态流转 ────────────────────────────────────────────────────────────────


def _transition(
    session: Session,
    *,
    task_id: int,
    from_statuses: tuple[str, ...],
    sets: list[str],
    params: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """条件 UPDATE 驱动状态流转：`WHERE status IN (...)`，行数为 0 即状态冲突。"""
    current = now or utcnow_naive()
    placeholders = ", ".join(f":st_{i}" for i in range(len(from_statuses)))
    full_params: dict[str, Any] = {"tid": task_id, "ts": _fmt(current), **params}
    full_params.update({f"st_{i}": value for i, value in enumerate(from_statuses)})

    result = cast(
        CursorResult[Any],
        session.execute(
            text(
                f"UPDATE ent_workflow_task SET {', '.join(sets)}, "
                "revision = revision + 1, updated_at = :ts "
                f"WHERE id = :tid AND status IN ({placeholders})"
            ),
            full_params,
        ),
    )
    if result.rowcount == 0:
        session.rollback()
        fresh = get_task(session, task_id)
        if fresh is None:
            raise TaskNotFoundError(f"任务 {task_id} 不存在")
        raise TaskStateError(f"任务 {task_id} 当前状态为 {fresh['status']}，不允许该操作")
    session.commit()
    updated = get_task(session, task_id)
    assert updated is not None
    return updated


def _check_generation(task: dict[str, Any], expected_generation: int | None) -> None:
    """执行代次 fence：带旧代次的提交一律拒绝（R9：迟到结果不覆盖人工状态）。"""
    if expected_generation is None:
        return
    if int(task["lease_generation"]) != expected_generation:
        raise LeaseConflictError(
            f"执行代次已过期：任务当前代次 {task['lease_generation']}，提交代次 "
            f"{expected_generation}（人工已接管或任务被改派）"
        )


def start_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    expected_generation: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """开始任务（被指派人本人或管理者）；**前置任务未完成则拒绝**。"""
    task, _ = authorize(
        session,
        task_id=task_id,
        user_id=actor_id,
        permission=PERM_TASK_DISPATCH,
        allow_assignee=True,
    )
    _assert_assignment_active(task)
    _check_generation(task, expected_generation)
    _assert_precondition_satisfied(session, task)
    current = now or utcnow_naive()
    return _transition(
        session,
        task_id=task_id,
        from_statuses=(STATUS_PENDING, STATUS_WAITING),
        sets=["status = :to", "started_at = COALESCE(started_at, :ts)", "wait_reason = NULL"],
        params={"to": STATUS_IN_PROGRESS},
        now=current,
    )


def wait_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    reason: str,
    expected_generation: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """标记缺件等待（**不是硬报错** —— 缺件是业务状态，不是异常）。"""
    task, _ = authorize(
        session,
        task_id=task_id,
        user_id=actor_id,
        permission=PERM_TASK_DISPATCH,
        allow_assignee=True,
    )
    _assert_assignment_active(task)
    cleaned = (reason or "").strip()
    if not cleaned:
        raise TaskValidationError("进入等待必须给出原因（缺什么件）")
    _check_generation(task, expected_generation)
    current = now or utcnow_naive()
    return _transition(
        session,
        task_id=task_id,
        from_statuses=(STATUS_PENDING, STATUS_IN_PROGRESS),
        sets=["status = :to", "wait_reason = :reason"],
        params={"to": STATUS_WAITING, "reason": cleaned},
        now=current,
    )


def complete_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    evidence_refs: list[dict[str, Any]] | None = None,
    expected_generation: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """完成任务；**所需证据未齐则拒绝**（证据要求字段真正生效的地方）。"""
    task, _ = authorize(
        session,
        task_id=task_id,
        user_id=actor_id,
        permission=PERM_TASK_DISPATCH,
        allow_assignee=True,
    )
    _assert_assignment_active(task)
    _check_generation(task, expected_generation)

    refs = _normalize_evidence_refs(evidence_refs)
    required = task["required_evidence"] or []
    provided_kinds = {item["kind"] for item in (refs or [])}
    missing = [kind for kind in required if kind not in provided_kinds]
    if missing:
        raise TaskStateError(f"缺少必需证据 {missing}，不能标记完成（可先用 wait 标记缺件等待）")

    current = now or utcnow_naive()
    return _transition(
        session,
        task_id=task_id,
        from_statuses=COMPLETABLE_STATUSES,
        sets=[
            "status = :to",
            "completed_at = :ts",
            "evidence_refs = :refs",
            "wait_reason = NULL",
        ],
        params={
            "to": STATUS_DONE,
            "refs": json.dumps(refs, ensure_ascii=False) if refs else None,
        },
        now=current,
    )


def reopen_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """重开已完成任务（管理动作）；**历史不删除** —— 完成证据与代次都保留。"""
    task, _ = authorize(session, task_id=task_id, user_id=actor_id, permission=PERM_TASK_DISPATCH)
    _assert_assignment_active(task)
    cleaned = (reason or "").strip()
    if not cleaned:
        raise TaskValidationError("reopen 必须给出原因")
    current = now or utcnow_naive()
    return _transition(
        session,
        task_id=task_id,
        from_statuses=(STATUS_DONE,),
        sets=[
            "status = :to",
            "completed_at = NULL",
            "reopen_count = reopen_count + 1",
            "last_reopen_reason = :reason",
        ],
        params={"to": STATUS_PENDING, "reason": cleaned},
        now=current,
    )


def cancel_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """撤回/作废任务（管理动作）；已完成任务不可撤回（要走 reopen）。"""
    task, _ = authorize(session, task_id=task_id, user_id=actor_id, permission=PERM_TASK_DISPATCH)
    _assert_assignment_active(task)
    current = now or utcnow_naive()
    return _transition(
        session,
        task_id=task_id,
        from_statuses=OPEN_STATUSES,
        sets=["status = :to", "cancelled_at = :ts"],
        params={"to": STATUS_CANCELLED},
        now=current,
    )


def _bump_lease(
    session: Session,
    *,
    task_id: int,
    new_assignee: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """接管/改派的公共实现：换负责人并 **推进执行代次**。"""
    return _transition(
        session,
        task_id=task_id,
        from_statuses=OPEN_STATUSES,
        sets=["assignee_user_id = :assignee", "lease_generation = lease_generation + 1"],
        params={"assignee": new_assignee},
        now=now,
    )


def reassign_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    assignee_user_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """改派任务（管理动作）；推进执行代次，旧执行者持有的代次即失效。"""
    task, _ = authorize(session, task_id=task_id, user_id=actor_id, permission=PERM_TASK_DISPATCH)
    _assert_assignment_active(task)
    if task["status"] in TERMINAL_STATUSES:
        raise TaskStateError(f"任务状态为 {task['status']}，终态任务不能改派")
    if task["assignee_user_id"] == assignee_user_id:
        raise TaskValidationError("新负责人与原负责人相同，无需改派")
    return _bump_lease(session, task_id=task_id, new_assignee=assignee_user_id, now=now)


def takeover_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """人工接管（R9 / AC-16 的任务层落点）：接管人成为负责人并推进代次。

    接管后，持旧代次的执行者（Agent 或原指派人）提交结果会被 `LeaseConflictError`
    拒绝 —— **旧结果不得覆盖接管后的人工状态**。
    """
    task, _ = authorize(session, task_id=task_id, user_id=actor_id, permission=PERM_TASK_DISPATCH)
    _assert_assignment_active(task)
    if task["status"] in TERMINAL_STATUSES:
        raise TaskStateError(f"任务状态为 {task['status']}，终态任务无需接管")
    return _bump_lease(session, task_id=task_id, new_assignee=actor_id, now=now)


# ── 列表 ────────────────────────────────────────────────────────────────────


def list_tasks(
    session: Session,
    *,
    assignment_id: int,
    task_status: str | None = None,
    assignee_user_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[int, list[dict[str, Any]]]:
    """按委托列任务（调用方已完成可见性与权限校验）。"""
    where = ["t.assignment_id = :aid"]
    params: dict[str, Any] = {"aid": assignment_id}
    if task_status is not None:
        where.append("t.status = :task_status")
        params["task_status"] = task_status
    if assignee_user_id is not None:
        where.append("t.assignee_user_id = :assignee")
        params["assignee"] = assignee_user_id

    clause = " AND ".join(where)
    total = int(
        session.execute(
            text(f"SELECT COUNT(*) FROM ent_workflow_task t WHERE {clause}"), params
        ).scalar()
        or 0
    )
    rows = (
        session.execute(
            text(
                f"SELECT {_TASK_COLS} FROM ent_workflow_task t "
                "JOIN ent_assignment a ON a.id = t.assignment_id "
                f"WHERE {clause} ORDER BY t.id ASC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": size, "offset": (page - 1) * size},
        )
        .mappings()
        .all()
    )
    return total, [_row_to_task(row) for row in rows]


def list_visible_tasks(
    session: Session,
    *,
    user_id: int,
    assignment_id: int,
    task_status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[int, list[dict[str, Any]]]:
    """带可见性校验的列表：委托不可见即 404（不泄漏存在性）。"""
    assignment = (
        session.execute(
            text("SELECT id, owner_user_id, org_id FROM ent_assignment WHERE id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .first()
    )
    if assignment is None:
        raise TaskNotFoundError(f"委托 {assignment_id} 不存在")

    visible = int(assignment["owner_user_id"]) == user_id
    if not visible and assignment["org_id"] is not None:
        context = resolve_context(session, user_id=user_id)
        visible = int(assignment["org_id"]) in context.org_ids and context.can(
            PERM_VIEW, org_id=int(assignment["org_id"])
        )
    if not visible:
        raise TaskNotFoundError(f"委托 {assignment_id} 不存在")

    return list_tasks(
        session, assignment_id=assignment_id, task_status=task_status, page=page, size=size
    )


__all__ = [
    "ASSIGNMENT_ACTIVE_STATUS",
    "COMPLETABLE_STATUSES",
    "EVIDENCE_KINDS",
    "OPEN_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_IN_PROGRESS",
    "STATUS_PENDING",
    "STATUS_WAITING",
    "TASK_TYPES",
    "TERMINAL_STATUSES",
    "AccessDeniedError",
    "LeaseConflictError",
    "TaskError",
    "TaskNotFoundError",
    "TaskPreconditionError",
    "TaskRevisionConflictError",
    "TaskStateError",
    "TaskValidationError",
    "cancel_task",
    "complete_task",
    "create_task",
    "get_task",
    "list_tasks",
    "list_visible_tasks",
    "reassign_task",
    "reopen_task",
    "set_precondition",
    "start_task",
    "takeover_task",
    "update_task",
    "utcnow_naive",
    "wait_task",
]
