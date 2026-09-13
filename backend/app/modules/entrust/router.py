"""委托支线路由 —— `/api/v1/entrust`（ENT-005：受理链路）。

R1 最小接口集（计划 §3.3「委托」组）中的受理部分：

- POST   /api/v1/entrust/assignments                 货主创建草稿（幂等）
- GET    /api/v1/entrust/assignments                 列表（货主视角 / 组织视角）
- GET    /api/v1/entrust/assignments/{id}            详情（创建人或该组织成员）
- PATCH  /api/v1/entrust/assignments/{id}            货主编辑草稿（expected_revision）
- POST   /api/v1/entrust/assignments/{id}/submit     货主提交（需生效授权，幂等）
- POST   /api/v1/entrust/assignments/{id}/claim      经理认领（原子，幂等）
- POST   /api/v1/entrust/assignments/{id}/cancel     货主撤回（幂等）

横切约束：
* **特性开关**（AC-22）：`ENTRUST_ENABLED=false` 时整组端点 404 —— 开关只是隐藏
  入口，**不是访问控制**；开启后每个端点各自做叠加层权限校验；
* **幂等**（ENT-002）：所有写操作必须带 `Idempotency-Key` 请求头；
  同键同体重放快照，同键异体 409，失败释放键可重试；
* **不泄漏存在性**（PRD 9）：非参与方访问详情/写操作一律 404，不区分
  "不存在"与"无权查看"；
* **不改旧接口**：本 router 不触碰 `current_role`，权限全部走叠加层（ENT-003）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.idempotency import IdempotencyError, idempotent, require_idempotency_key
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import assignments as svc
from app.modules.entrust.access import PERM_VIEW, AccessDeniedError, resolve_context
from app.modules.entrust.schemas import (
    AssignmentCreate,
    AssignmentListOut,
    AssignmentOut,
    AssignmentSubmit,
    AssignmentUpdate,
    assignment_out,
)

router = APIRouter()

_SCOPE_CREATE = "entrust:assignment:create"
_SCOPE_SUBMIT = "entrust:assignment:submit"
_SCOPE_CLAIM = "entrust:assignment:claim"
_SCOPE_CANCEL = "entrust:assignment:cancel"


def require_entrust_enabled() -> None:
    """特性开关依赖：关闭时整组端点 404（隐藏入口；开关 ≠ 访问控制）。"""
    if not get_settings().ENTRUST_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


def _http_error(exc: Exception) -> HTTPException:
    """服务层异常 → HTTP 语义（集中映射，避免各端点口径漂移）。"""
    if isinstance(exc, svc.AssignmentNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (svc.RevisionConflictError, svc.AssignmentStateError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AccessDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _replay(replay_status: int, replay_body: Any) -> JSONResponse:
    """幂等命中：原样重放历史成功响应（含状态码）。"""
    return JSONResponse(status_code=replay_status, content=replay_body)


def _guard_or_400(key: str | None) -> str:
    """幂等键缺失 → 400（写操作必须提供 Idempotency-Key）。"""
    try:
        return require_idempotency_key({"Idempotency-Key": key or ""})
    except Exception as exc:  # MissingIdempotencyKeyError
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_write(
    db: Session,
    *,
    scope: str,
    key: str,
    actor_user_id: int,
    payload: Any,
    business: Callable[[], dict[str, Any]],
) -> Any:
    """幂等包裹 + 统一异常映射（所有写端点共用，口径不漂移）。

    * 同键同体重放历史成功响应（含状态码）；
    * 同键异体 / 上一次仍在处理中 → 409（`IdempotencyError` 从 `__enter__` 抛出，
      必须在本层接住，否则会变成 500）；
    * 业务异常 → `_http_error` 映射；幂等键由上下文管理器释放，可同键重试。
    """
    try:
        with idempotent(
            db, scope=scope, key=key, actor_user_id=actor_user_id, payload=payload
        ) as guard:
            if guard.replay is not None:
                return _replay(guard.replay.status_code, guard.replay.body)
            body = business()
            guard.succeed(200, body)
            return body
    except IdempotencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except svc.AssignmentError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/assignments",
    response_model=AssignmentOut,
    summary="创建委托草稿（货主本人，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_assignment(
    data: AssignmentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = _guard_or_400(idempotency_key)
    payload = data.model_dump(mode="json")
    return _run_write(
        db,
        scope=_SCOPE_CREATE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: assignment_out(
            svc.create_assignment(
                db,
                owner_user_id=int(user.id),
                title=data.title,
                cargo_summary=data.cargo_summary,
                quantity=data.quantity,
                quantity_unit=data.quantity_unit,
            )
        ).model_dump(mode="json"),
    )


@router.get(
    "/assignments",
    response_model=AssignmentListOut,
    summary="委托列表（货主视角 / 组织视角）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_assignments(
    view: str = Query(default="owner", description="owner=我的委托；org=授权组织的队列"),
    org_id: int | None = Query(default=None, ge=1),
    assignment_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    user_id = int(user.id)
    owner_user_id: int | None = None
    scope_org_id: int | None = None

    if view == "owner":
        owner_user_id = user_id
        if org_id is not None:
            scope_org_id = org_id
    elif view == "org":
        ctx = resolve_context(db, user_id=user_id)
        if org_id is not None:
            if org_id not in ctx.org_ids:
                # 不是该组织成员：空列表即可，不暴露组织是否存在（404 反而泄漏）
                return AssignmentListOut(total=0, page=page, size=size, items=[])
            scope_org_id = org_id
        elif len(ctx.org_ids) == 1:
            scope_org_id = next(iter(ctx.org_ids))
        elif len(ctx.org_ids) == 0:
            return AssignmentListOut(total=0, page=page, size=size, items=[])
        else:
            raise HTTPException(
                status_code=400, detail="用户属于多个组织，请用 org_id 指定要查看的组织"
            )
    else:
        raise HTTPException(status_code=422, detail="view 必须是 owner 或 org")

    if view == "org" and scope_org_id is not None:
        ctx = resolve_context(db, user_id=user_id)
        if not ctx.can(PERM_VIEW):
            raise HTTPException(status_code=403, detail="缺少委托查看权限")

    total, items = svc.list_assignments(
        db,
        owner_user_id=owner_user_id,
        org_id=scope_org_id,
        status=assignment_status,
        page=page,
        size=size,
    )
    return AssignmentListOut(
        total=total,
        page=page,
        size=size,
        items=[assignment_out(i) for i in items],
    )


@router.get(
    "/assignments/{assignment_id}",
    response_model=AssignmentOut,
    summary="委托详情（创建人或所属组织成员）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_assignment(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    try:
        assignment = svc.get_assignment(db, assignment_id)
    except svc.AssignmentError as exc:
        raise _http_error(exc) from exc
    if assignment is None:
        raise HTTPException(status_code=404, detail="委托单不存在")

    visible = assignment["owner_user_id"] == int(user.id)
    if not visible and assignment["org_id"] is not None:
        ctx = resolve_context(db, user_id=int(user.id))
        visible = assignment["org_id"] in ctx.org_ids and ctx.can(PERM_VIEW)
    if not visible:
        raise HTTPException(status_code=404, detail="委托单不存在")
    return assignment_out(assignment)


@router.patch(
    "/assignments/{assignment_id}",
    response_model=AssignmentOut,
    summary="编辑草稿（货主本人，expected_revision 乐观锁）",
    dependencies=[Depends(require_entrust_enabled)],
)
def update_assignment(
    assignment_id: int,
    data: AssignmentUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    provided = data.model_fields_set - {"expected_revision"}
    try:
        result = svc.update_draft(
            db,
            assignment_id=assignment_id,
            actor_id=int(user.id),
            expected_revision=data.expected_revision,
            title=data.title if "title" in provided else None,
            cargo_summary=data.cargo_summary if "cargo_summary" in provided else None,
            quantity=data.quantity if "quantity" in provided else None,
            quantity_unit=data.quantity_unit if "quantity_unit" in provided else None,
        )
    except svc.AssignmentError as exc:
        raise _http_error(exc) from exc
    return assignment_out(result)


@router.post(
    "/assignments/{assignment_id}/submit",
    response_model=AssignmentOut,
    summary="提交委托到服务主体（货主本人，需生效授权，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def submit_assignment(
    assignment_id: int,
    data: AssignmentSubmit,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = _guard_or_400(idempotency_key)
    payload = data.model_dump(mode="json")
    return _run_write(
        db,
        scope=_SCOPE_SUBMIT,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: assignment_out(
            svc.submit_assignment(
                db,
                assignment_id=assignment_id,
                actor_id=int(user.id),
                org_id=data.org_id,
                expected_revision=data.expected_revision,
            )
        ).model_dump(mode="json"),
    )


@router.post(
    "/assignments/{assignment_id}/claim",
    response_model=AssignmentOut,
    summary="认领委托（组织成员 + 认领权限，原子，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def claim_assignment(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = _guard_or_400(idempotency_key)
    payload = {"assignment_id": assignment_id}
    return _run_write(
        db,
        scope=_SCOPE_CLAIM,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: assignment_out(
            svc.claim_assignment(db, assignment_id=assignment_id, actor_id=int(user.id))
        ).model_dump(mode="json"),
    )


@router.post(
    "/assignments/{assignment_id}/cancel",
    response_model=AssignmentOut,
    summary="撤回委托（货主本人，仅草稿/待受理可撤，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def cancel_assignment(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = _guard_or_400(idempotency_key)
    payload = {"assignment_id": assignment_id}
    return _run_write(
        db,
        scope=_SCOPE_CANCEL,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: assignment_out(
            svc.cancel_assignment(db, assignment_id=assignment_id, actor_id=int(user.id))
        ).model_dump(mode="json"),
    )
