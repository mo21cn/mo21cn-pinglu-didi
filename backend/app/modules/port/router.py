"""港域（泊位与泊位预约）路由（F4）。

泊位管理端点仅港口方（port）角色；
预约申请/撤销端点仅船东（owner）角色；
确认/驳回/核销端点仅港口方（port）角色。

- POST   /api/v1/port/berths                新建泊位（港口方）
- GET    /api/v1/port/berths                泊位列表（?port_code=&status=）
- PATCH  /api/v1/port/berths/{id}           编辑泊位（港口方）
- GET    /api/v1/port/berths/{id}/schedule  泊位档期（confirmed 占用）
- POST   /api/v1/port/appts                 申请泊位预约（船东）
- GET    /api/v1/port/appts                 我的预约（船东，?status=）
- GET    /api/v1/port/appts-review          预约审核列表（港口方，默认 pending）
- POST   /api/v1/port/appts/{id}/confirm     确认预约（港口方，防超卖校验）
- POST   /api/v1/port/appts/{id}/reject      驳回预约（港口方）
- POST   /api/v1/port/appts/{id}/cancel      撤销预约（船东本人）
- POST   /api/v1/port/appts/{id}/complete    核销完成（港口方）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.port import Berth, BerthAppt
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.port import service
from app.modules.port.schemas import (
    BerthApptCreate,
    BerthApptListResponse,
    BerthApptResponse,
    BerthApptReview,
    BerthCreate,
    BerthListResponse,
    BerthResponse,
    BerthScheduleItem,
    BerthScheduleResponse,
    BerthUpdate,
)

router = APIRouter()


def _require_role(user: User, role: str, message: str) -> None:
    if user.current_role != role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _get_berth_or_404(db: Session, berth_id: int) -> Berth:
    berth = service.get_berth(db, berth_id)
    if berth is None:
        raise HTTPException(status_code=404, detail="泊位不存在")
    return berth


def _get_appt_or_404(db: Session, appt_id: int) -> BerthAppt:
    appt = service.get_appt(db, appt_id)
    if appt is None:
        raise HTTPException(status_code=404, detail="泊位预约不存在")
    return appt


# ---------- 泊位管理（港口方） ----------

@router.post("/berths", response_model=BerthResponse, summary="新建泊位")
def create_berth(
    body: BerthCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    try:
        return service.create_berth(db, body)
    except service.PortStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/berths", response_model=BerthListResponse, summary="泊位列表")
def list_berths(
    port_code: str | None = Query(default=None),
    berth_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    total, items = service.list_berths(db, port_code, berth_status, page, size)
    return BerthListResponse(total=total, items=[BerthResponse.model_validate(b) for b in items])


@router.patch("/berths/{berth_id}", response_model=BerthResponse, summary="编辑泊位")
def update_berth(
    berth_id: int,
    body: BerthUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    berth = _get_berth_or_404(db, berth_id)
    return service.update_berth(db, berth, body)


@router.get("/berths/{berth_id}/schedule", response_model=BerthScheduleResponse, summary="泊位档期")
def berth_schedule(
    berth_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    berth = _get_berth_or_404(db, berth_id)
    confirmed = service.get_berth_schedule(db, berth)
    return BerthScheduleResponse(
        berth=BerthResponse.model_validate(berth),
        confirmed=[
            BerthScheduleItem(
                appt_id=a.id, ship_id=a.ship_id, plan_start=a.plan_start, plan_end=a.plan_end
            )
            for a in confirmed
        ],
    )


# ---------- 泊位预约（船东申请） ----------

@router.post("/appts", response_model=BerthApptResponse, summary="申请泊位预约")
def create_appt(
    body: BerthApptCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "owner", "该操作仅船东角色可用，请先切换角色")
    try:
        return service.create_appt(db, user.id, body)
    except service.PortStateError as exc:
        # 硬约束不满足属业务校验失败，409 语义更贴近资源冲突
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/appts", response_model=BerthApptListResponse, summary="我的泊位预约")
def list_my_appts(
    appt_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "owner", "该操作仅船东角色可用，请先切换角色")
    total, items = service.list_my_appts(db, user.id, appt_status, page, size)
    return BerthApptListResponse(
        total=total, items=[BerthApptResponse.model_validate(a) for a in items]
    )


# ---------- 预约审核（港口方） ----------

@router.get("/appts-review", response_model=BerthApptListResponse, summary="预约审核列表（港口方）")
def list_review(
    berth_id: int | None = Query(default=None),
    appt_status: str | None = Query(default="pending", alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    total, items = service.list_berth_appts(db, berth_id, appt_status, page, size)
    return BerthApptListResponse(
        total=total, items=[BerthApptResponse.model_validate(a) for a in items]
    )


@router.post("/appts/{appt_id}/confirm", response_model=BerthApptResponse, summary="确认预约（防超卖）")
def confirm_appt(
    appt_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    appt = _get_appt_or_404(db, appt_id)
    try:
        return service.confirm_appt(db, appt)
    except service.BerthConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.PortStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/appts/{appt_id}/reject", response_model=BerthApptResponse, summary="驳回预约")
def reject_appt(
    appt_id: int,
    body: BerthApptReview,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    appt = _get_appt_or_404(db, appt_id)
    try:
        return service.reject_appt(db, appt, body.reason)
    except service.PortStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/appts/{appt_id}/cancel", response_model=BerthApptResponse, summary="撤销预约（船东本人）")
def cancel_appt(
    appt_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "owner", "该操作仅船东角色可用，请先切换角色")
    appt = _get_appt_or_404(db, appt_id)
    try:
        return service.cancel_appt(db, appt, user.id)
    except service.PortStateError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/appts/{appt_id}/complete", response_model=BerthApptResponse, summary="核销完成（港口方）")
def complete_appt(
    appt_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(user, "port", "该操作仅港口方角色可用")
    appt = _get_appt_or_404(db, appt_id)
    try:
        return service.complete_appt(db, appt)
    except service.PortStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
