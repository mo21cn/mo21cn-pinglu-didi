"""异常与变更案件 API —— DR-0013 A1 的服务面（ENT-030 切片三之二）。

R1 端点集合（DR-0013 §7.1「人工处置端点」）：

- POST   /api/v1/entrust/assignments/{assignment_id}/exceptions   登记案件（幂等）
- GET    /api/v1/entrust/exceptions                                按委托列案件
- GET    /api/v1/entrust/exceptions/{exception_id}                 案件详情（含事件链）
- POST   /api/v1/entrust/exceptions/{exception_id}/links           登记受影响项（幂等）
- DELETE /api/v1/entrust/exceptions/{exception_id}/links/{link_id} 移除受影响项（幂等）
- POST   /api/v1/entrust/exceptions/{exception_id}/decision        记录决定（幂等）
- POST   /api/v1/entrust/exceptions/{exception_id}/close           关闭案件（幂等）
- POST   /api/v1/entrust/exceptions/{exception_id}/reopen          重开案件（幂等）

本层只做四件事：**取对象 → 判权限（复用单一入口）→ 调服务 → 投影**。
业务规则一律在 `exceptions.py`，本层不复制任何一条 —— 两处各写一遍，
迟早出现「同一个规则在端点层比服务层松」的漏洞。

横切口径与受理/成果/任务三组一致（经 `_http.py` 统一）：

* `ENTRUST_ENABLED=false` → 整组端点 404（开关 ≠ 访问控制）；
* 写操作必须带 `Idempotency-Key`（缺键 400 / 同键同体重放 / 同键异体 409）；
* 非参与方 **404** 不泄漏存在性（`authz` 抛的就是 404）；作用域不一致 **403**；
* 本 router 不触碰 `current_role`，权限全部走叠加层（ENT-003 / `authz.py`）。

**本片不含**的工作台 `exceptions` 槽真实投影与 `complete_task` 阻断门禁，
分别在切片三之三与三之四；在它们落地之前，「案件能登记」**不等于**「阻断已生效」。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import exceptions as svc
from app.modules.entrust._http import (
    guard_or_400,
    map_access_denied,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.authz import assert_can_view_assignment, load_assignment
from app.modules.entrust.schemas import (
    ExceptionCaseCloseIn,
    ExceptionCaseCreate,
    ExceptionCaseDecisionIn,
    ExceptionCaseDetailOut,
    ExceptionCaseLinkAddIn,
    ExceptionCaseListOut,
    ExceptionCaseOut,
    ExceptionCaseReopenIn,
    exception_case_out,
    exception_event_out,
)

router = APIRouter()

_SCOPE_CREATE = "entrust:exception:create"
_SCOPE_LINK_ADD = "entrust:exception:link:add"
_SCOPE_LINK_REMOVE = "entrust:exception:link:remove"
_SCOPE_DECIDE = "entrust:exception:decide"
_SCOPE_CLOSE = "entrust:exception:close"
_SCOPE_REOPEN = "entrust:exception:reopen"


def _map_case_error(exc: Exception) -> HTTPException | None:
    """案件服务异常 → HTTP 语义；无法识别的返回 `None`（不吞真实 bug）。

    四类映射与 DR-0013 的裁定一一对应：
    * 不存在（含**非参与方**）→ **404**，不区分「不存在」与「无权知晓」；
    * 作用域违反（客户端指定 org / 受影响项或决定依据跨委托）→ **403**；
    * 状态冲突（非法转移、乐观锁过期、重复登记）→ **409**；
    * 其余规则违反（C1/C2、关闭缺处置或证据…）→ **400**。
    """
    mapped = map_access_denied(exc)
    if mapped is not None:
        return mapped
    if isinstance(exc, svc.ExceptionCaseNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.ExceptionCaseScopeError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, svc.ExceptionCaseConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, svc.ExceptionCaseError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


def _internal(case: dict[str, Any]) -> dict[str, Any]:
    """服务层案件 dict → **内部**投影的 JSON（写端点与读端点共用同一形状）。"""
    return exception_case_out(svc.project_case_internal(case)).model_dump(mode="json")


def _visible_assignment(db: Session, *, assignment_id: int, user_id: int) -> dict[str, Any]:
    """列表端点的可见性前置：先确认能看见这张委托，再列它的案件。

    复用 `authz.assert_can_view_assignment` —— 与工作台、成果清单**同一份判据**。
    案件本身没有「跨委托列表」的入口，因此这一步是列表的唯一作用域来源。
    """
    assignment = load_assignment(db, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="委托单不存在")
    assert_can_view_assignment(db, user_id=user_id, assignment=assignment)
    return assignment


def _post(
    db: Session,
    *,
    scope: str,
    key: str | None,
    actor_user_id: int,
    payload: Any,
    business: Callable[[], dict[str, Any]],
) -> Any:
    """带幂等的写端点统一包裹（与 `tasks_api._post` 同一口径）。"""
    return run_write(
        db,
        scope=scope,
        key=guard_or_400(key),
        actor_user_id=actor_user_id,
        payload=payload,
        business=business,
        map_domain_error=_map_case_error,
    )


# ── 登记 ─────────────────────────────────────────────────────────────────────


@router.post(
    "/assignments/{assignment_id}/exceptions",
    response_model=ExceptionCaseOut,
    summary="登记异常 / 变更案件（需管理动作权限，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def raise_case(
    assignment_id: int,
    data: ExceptionCaseCreate,
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
        business=lambda: _internal(
            svc.raise_case(
                db,
                assignment_id=assignment_id,
                actor_id=int(user.id),
                kind=data.kind,
                title=data.title,
                severity=data.severity,
                impact_kind=data.impact_kind,
                cause=data.cause,
                owner_user_id=data.owner_user_id,
                source=data.source,
                due_at=data.due_at,
                proposed_action=data.proposed_action,
                links=([item.model_dump() for item in data.links] if data.links else None),
                request_org_id=data.org_id,
            )
        ),
    )


# ── 读 ───────────────────────────────────────────────────────────────────────


@router.get(
    "/exceptions",
    response_model=ExceptionCaseListOut,
    summary="按委托列案件（货主本人或该组织成员）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_exceptions(
    assignment_id: int = Query(ge=1),
    case_status: str | None = Query(default=None, alias="status"),
    case_kind: str | None = Query(default=None, alias="kind"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _visible_assignment(db, assignment_id=assignment_id, user_id=int(user.id))
    try:
        total, cases = svc.list_cases(
            db,
            assignment_id=assignment_id,
            status=case_status,
            kind=case_kind,
            page=page,
            size=size,
        )
    except Exception as exc:
        mapped = _map_case_error(exc)
        if mapped is None:
            raise
        raise mapped from exc

    grouped = svc.list_links_for_cases(db, [int(case["id"]) for case in cases])
    items = [
        exception_case_out(svc.project_case_internal(case, links=grouped.get(int(case["id"]), [])))
        for case in cases
    ]
    return ExceptionCaseListOut(total=total, page=page, size=size, items=items)


@router.get(
    "/exceptions/{exception_id}",
    response_model=ExceptionCaseDetailOut,
    summary="案件详情（内部投影 + 完整事件链；非参与方 404）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_exception(
    exception_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    try:
        case = svc.load_visible_case(db, exception_id=exception_id, user_id=int(user.id))
    except Exception as exc:
        mapped = _map_case_error(exc)
        if mapped is None:
            raise
        raise mapped from exc

    events = svc.list_events(db, exception_id)
    return ExceptionCaseDetailOut(
        case=exception_case_out(
            svc.project_case_internal(case, links=svc.list_links(db, exception_id))
        ),
        events=[exception_event_out(item) for item in events],
    )


# ── 受影响项 ─────────────────────────────────────────────────────────────────


@router.post(
    "/exceptions/{exception_id}/links",
    response_model=ExceptionCaseOut,
    summary="登记一条受影响项（任务或成果，须同属本委托；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def add_exception_link(
    exception_id: int,
    data: ExceptionCaseLinkAddIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"exception_id": exception_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_LINK_ADD,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: _internal(
            svc.add_link(
                db,
                exception_id=exception_id,
                actor_id=int(user.id),
                target_kind=data.target_kind,
                target_id=data.target_id,
                expected_revision=data.expected_revision,
            )
        ),
    )


@router.delete(
    "/exceptions/{exception_id}/links/{link_id}",
    response_model=ExceptionCaseOut,
    summary="移除一条受影响项（登记错了要能更正，否则 resolved 永不可达；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def remove_exception_link(
    exception_id: int,
    link_id: int,
    expected_revision: int = Query(ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {
        "exception_id": exception_id,
        "link_id": link_id,
        "expected_revision": expected_revision,
    }
    return _post(
        db,
        scope=_SCOPE_LINK_REMOVE,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: _internal(
            svc.remove_link(
                db,
                exception_id=exception_id,
                link_id=link_id,
                actor_id=int(user.id),
                expected_revision=expected_revision,
            )
        ),
    )


# ── 决定 / 关闭 / 重开 ───────────────────────────────────────────────────────


@router.post(
    "/exceptions/{exception_id}/decision",
    response_model=ExceptionCaseOut,
    summary="记录决定（两套状态机分别生效；approved 须给 basis_revision_id；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def decide_exception(
    exception_id: int,
    data: ExceptionCaseDecisionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"exception_id": exception_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_DECIDE,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: _internal(
            svc.decide(
                db,
                exception_id=exception_id,
                actor_id=int(user.id),
                to_status=data.to_status,
                expected_revision=data.expected_revision,
                decision_note=data.decision_note,
                basis_revision_id=data.basis_revision_id,
            )
        ),
    )


@router.post(
    "/exceptions/{exception_id}/close",
    response_model=ExceptionCaseOut,
    summary="关闭案件（必须给出处置与证据，没有一键关闭；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def close_exception(
    exception_id: int,
    data: ExceptionCaseCloseIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"exception_id": exception_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_CLOSE,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: _internal(
            svc.close_case(
                db,
                exception_id=exception_id,
                actor_id=int(user.id),
                closure_disposition=data.closure_disposition,
                expected_revision=data.expected_revision,
                evidence_ref=data.evidence_ref,
                decision_note=data.decision_note,
                resolution_note=data.resolution_note,
            )
        ),
    )


@router.post(
    "/exceptions/{exception_id}/reopen",
    response_model=ExceptionCaseOut,
    summary="重开已关闭案件（原因必填；状态与审计同事务；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def reopen_exception(
    exception_id: int,
    data: ExceptionCaseReopenIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    payload = {"exception_id": exception_id, **data.model_dump(mode="json")}
    return _post(
        db,
        scope=_SCOPE_REOPEN,
        key=idempotency_key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: _internal(
            svc.reopen_case(
                db,
                exception_id=exception_id,
                actor_id=int(user.id),
                reason=data.reason,
                expected_revision=data.expected_revision,
            )
        ),
    )


__all__ = ["router"]
