"""任务 API —— ENT-008 服务层的 HTTP 面。

R1 最小接口集（计划 §3.3「任务」组）：创建/编辑/列表/状态流转/接管/reopen/改派，
以及**固定前置条件的合法性校验**。

- POST   /api/v1/entrust/tasks                              创建任务（幂等）
- GET    /api/v1/entrust/tasks                              按委托列任务
- GET    /api/v1/entrust/tasks/{tid}                        任务详情
- PATCH  /api/v1/entrust/tasks/{tid}                        编辑（expected_revision）
- POST   /api/v1/entrust/tasks/{tid}/precondition           设置/清除固定前置条件
- POST   /api/v1/entrust/tasks/{tid}/start                  开始（前置未完成则 409）
- POST   /api/v1/entrust/tasks/{tid}/wait                   缺件等待（业务状态，非报错）
- POST   /api/v1/entrust/tasks/{tid}/evidence               补录证据（缺件的补救入口，幂等）
- POST   /api/v1/entrust/tasks/{tid}/complete               完成（所需证据未齐则 409）
- POST   /api/v1/entrust/tasks/{tid}/reopen                 重开（原因必填，历史保留）
- POST   /api/v1/entrust/tasks/{tid}/assign                 改派（推进执行代次）
- POST   /api/v1/entrust/tasks/{tid}/takeover               人工接管（推进执行代次）
- POST   /api/v1/entrust/tasks/{tid}/cancel                 撤回任务
- GET    /api/v1/entrust/assignments/{aid}/evidence-gaps    这张委托还缺什么证据（派生）

横切约束（与受理/成果两组一致，经 `_http.py` 统一口径）：
* `ENTRUST_ENABLED=false` → 整组 404（开关 ≠ 访问控制）；
* POST 写操作必须带 `Idempotency-Key`；PATCH 与前置条件设置靠 `expected_revision`
  天然防重（同 ENT-005 的编辑口径）；
* 非参与方 404 不泄漏存在性；权限不足 403；
* 本 router 不触碰 `current_role`，权限全部走叠加层（ENT-003）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import tasks as svc
from app.modules.entrust._http import (
    guard_or_400,
    map_access_denied,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.schemas import (
    EvidenceGapsOut,
    EvidenceRecordOut,
    TaskCompleteIn,
    TaskCreate,
    TaskEvidenceIn,
    TaskListOut,
    TaskOut,
    TaskPreconditionIn,
    TaskReassignIn,
    TaskReopenIn,
    TaskStartIn,
    TaskUpdate,
    TaskWaitIn,
    evidence_record_out,
    task_out,
)

router = APIRouter()

_SCOPE_CREATE = "entrust:task:create"
_SCOPE_START = "entrust:task:start"
_SCOPE_WAIT = "entrust:task:wait"
_SCOPE_EVIDENCE = "entrust:task:evidence"
_SCOPE_COMPLETE = "entrust:task:complete"
_SCOPE_REOPEN = "entrust:task:reopen"
_SCOPE_ASSIGN = "entrust:task:assign"
_SCOPE_TAKEOVER = "entrust:task:takeover"
_SCOPE_CANCEL = "entrust:task:cancel"


def _map_task_error(exc: Exception) -> HTTPException | None:
    """任务服务异常 → HTTP 语义；无法识别返回 None（不吞真实 bug）。"""
    mapped = map_access_denied(exc)
    if mapped is not None:
        return mapped
    if isinstance(exc, svc.TaskNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            svc.TaskPreconditionError,
            svc.TaskStateError,
            svc.TaskRevisionConflictError,
            svc.LeaseConflictError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, svc.TaskValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, svc.TaskError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


def _call(business: Callable[[], dict[str, Any]]) -> Any:
    """非幂等端点（读 / PATCH / 前置条件）的统一异常映射。"""
    try:
        return task_out(business())
    except Exception as exc:
        mapped = _map_task_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _post(
    db: Session,
    *,
    scope: str,
    key: str | None,
    actor_user_id: int,
    payload: Any,
    business: Callable[[], dict[str, Any]],
) -> Any:
    """带幂等的 POST：缺键 400、同键同体重放、同键异体 409、业务异常映射。"""
    execute = business
    return run_write(
        db,
        scope=scope,
        key=guard_or_400(key),
        actor_user_id=actor_user_id,
        payload=payload,
        business=lambda: task_out(execute()).model_dump(mode="json"),
        map_domain_error=_map_task_error,
    )


@router.post(
    "/assignments/{assignment_id}/tasks",
    response_model=TaskOut,
    summary="在委托下创建任务（需派单权限，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_task_under_assignment(
    assignment_id: int,
    data: TaskCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"assignment_id": assignment_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_CREATE,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.create_task(
            db,
            assignment_id=assignment_id,
            actor_id=int(user.id),
            task_type=data.task_type,
            title=data.title,
            assignee_user_id=data.assignee_user_id,
            due_at=data.due_at,
            required_evidence=data.required_evidence,
            precondition_task_id=data.precondition_task_id,
        ),
    )


@router.get(
    "/tasks",
    response_model=TaskListOut,
    summary="按委托列任务（货主本人或该组织成员）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_tasks(
    assignment_id: int = Query(ge=1),
    task_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    try:
        total, items = svc.list_visible_tasks(
            db,
            user_id=int(user.id),
            assignment_id=assignment_id,
            task_status=task_status,
            page=page,
            size=size,
        )
    except Exception as exc:
        mapped = _map_task_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    # `has_more` 按**已消费条数**算，不写 `page * size < total`：
    # 页号越过末页时（`page` 很大、`items` 为空）前者仍给出"没有更多"，后者会算出
    # 一个永远为真的值。两种写法在正常翻页下等价，差别只在异常页号上 ——
    # 而"异常页号"恰恰是调用方最需要被告知"你没有取到东西"的时候。
    return TaskListOut(
        total=total,
        page=page,
        size=size,
        has_more=(page - 1) * size + len(items) < total,
        items=[task_out(i) for i in items],
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskOut,
    summary="任务详情（货主本人或该组织成员）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    try:
        task, _ = svc.authorize(db, task_id=task_id, user_id=int(user.id))
    except Exception as exc:
        mapped = _map_task_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return task_out(task)


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskOut,
    summary="编辑任务（需派单权限，expected_revision 乐观锁）",
    dependencies=[Depends(require_entrust_enabled)],
)
def update_task(
    task_id: int,
    data: TaskUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    provided = data.model_fields_set - {"expected_revision"}
    return _call(
        lambda: svc.update_task(
            db,
            task_id=task_id,
            actor_id=int(user.id),
            expected_revision=data.expected_revision,
            title=data.title if "title" in provided else None,
            assignee_user_id=data.assignee_user_id if "assignee_user_id" in provided else None,
            clear_assignee="assignee_user_id" in provided and data.assignee_user_id is None,
            due_at=data.due_at if "due_at" in provided else None,
            clear_due_at="due_at" in provided and data.due_at is None,
            required_evidence=(data.required_evidence if "required_evidence" in provided else None),
            clear_required_evidence=(
                "required_evidence" in provided and data.required_evidence is None
            ),
        )
    )


@router.post(
    "/tasks/{task_id}/precondition",
    response_model=TaskOut,
    summary="设置/清除固定前置条件（自依赖与循环一律拒绝）",
    dependencies=[Depends(require_entrust_enabled)],
)
def set_precondition(
    task_id: int,
    data: TaskPreconditionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    return _call(
        lambda: svc.set_precondition(
            db,
            task_id=task_id,
            actor_id=int(user.id),
            precondition_task_id=data.precondition_task_id,
            expected_revision=data.expected_revision,
        )
    )


@router.post(
    "/tasks/{task_id}/start",
    response_model=TaskOut,
    summary="开始任务（被指派人本人或管理者；前置未完成则拒绝，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def start_task(
    task_id: int,
    data: TaskStartIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    expected_generation = data.expected_generation if data is not None else None
    payload = {"task_id": task_id, "expected_generation": expected_generation}
    return _post(
        db,
        scope=_SCOPE_START,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.start_task(
            db,
            task_id=task_id,
            actor_id=int(user.id),
            expected_generation=expected_generation,
        ),
    )


@router.post(
    "/tasks/{task_id}/wait",
    response_model=TaskOut,
    summary="标记缺件等待（缺件是业务状态，不是错误，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def wait_task(
    task_id: int,
    data: TaskWaitIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"task_id": task_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_WAIT,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.wait_task(
            db,
            task_id=task_id,
            actor_id=int(user.id),
            reason=data.reason,
            expected_generation=data.expected_generation,
        ),
    )


@router.post(
    "/tasks/{task_id}/evidence",
    response_model=EvidenceRecordOut,
    summary="在原任务上补录一条证据（缺件的补救入口，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def record_task_evidence(
    task_id: int,
    data: TaskEvidenceIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """在**原任务**上补录一条证据（S4 段 §8 的"可执行补救"）。

    ⛔ **不另造工作流**：缺件条件就是任务自己的 `waiting` ＋ `wait_reason`，
    补救出口就是在本任务上把证据补齐；不新建任务、不新建等待实体。
    返回**重算后**的齐备度 —— 补录的唯一目的就是让缺件清单变短。
    """
    payload = {"task_id": task_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_EVIDENCE,
        key=guard_or_400(idempotency_key),
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: evidence_record_out(
            svc.record_task_evidence(
                db,
                task_id=task_id,
                actor_id=int(user.id),
                kind=data.kind,
                ref=data.ref,
                occurred_at=data.occurred_at,
                source=data.source,
                expected_revision=data.expected_revision,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_task_error,
    )


@router.post(
    "/tasks/{task_id}/complete",
    response_model=TaskOut,
    summary="完成任务（证据未齐、或存在未终结的阻断案件则拒绝，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def complete_task(
    task_id: int,
    data: TaskCompleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"task_id": task_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_COMPLETE,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.complete_task(
            db,
            task_id=task_id,
            actor_id=int(user.id),
            evidence_refs=(
                [item.model_dump() for item in data.evidence_refs] if data.evidence_refs else None
            ),
            expected_generation=data.expected_generation,
        ),
    )


@router.post(
    "/tasks/{task_id}/reopen",
    response_model=TaskOut,
    summary="重开已完成任务（原因必填，历史不删除，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def reopen_task(
    task_id: int,
    data: TaskReopenIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"task_id": task_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_REOPEN,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.reopen_task(
            db, task_id=task_id, actor_id=int(user.id), reason=data.reason
        ),
    )


@router.post(
    "/tasks/{task_id}/assign",
    response_model=TaskOut,
    summary="改派任务（推进执行代次，旧执行者代次失效，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def reassign_task(
    task_id: int,
    data: TaskReassignIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"task_id": task_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_ASSIGN,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.reassign_task(
            db, task_id=task_id, actor_id=int(user.id), assignee_user_id=data.assignee_user_id
        ),
    )


@router.post(
    "/tasks/{task_id}/takeover",
    response_model=TaskOut,
    summary="人工接管（接管人成为负责人并推进代次，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def takeover_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"task_id": task_id}
    return _post(
        db,
        scope=_SCOPE_TAKEOVER,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.takeover_task(db, task_id=task_id, actor_id=int(user.id)),
    )


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskOut,
    summary="撤回任务（未完成任务可撤，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def cancel_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"task_id": task_id}
    return _post(
        db,
        scope=_SCOPE_CANCEL,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.cancel_task(db, task_id=task_id, actor_id=int(user.id)),
    )


@router.get(
    "/assignments/{assignment_id}/evidence-gaps",
    response_model=EvidenceGapsOut,
    summary="这张委托还缺什么证据（派生；交接＝任务，不另造实体）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_evidence_gaps(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """S4 段 §8 的读模型：缺什么、谁在等、交接类任务齐了没有。

    **派生**读数 —— 不落库、不新建实体；缺件条件就是任务自己的
    `waiting` ＋ `wait_reason`，本端点只把"缺什么"算出来。
    交接没有独立成果实体（合同 S4 段明写不得发明），
    它就是 `task_type = handover` 的那些任务。
    """
    try:
        return EvidenceGapsOut.model_validate(
            svc.list_evidence_gaps(db, user_id=int(user.id), assignment_id=assignment_id)
        )
    except Exception as exc:
        mapped = _map_task_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
