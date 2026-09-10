"""货域（发货单）路由（F2）。端点均要求货主（shipper）角色。

- POST   /api/v1/cargo/shipments            创建发货单
- GET    /api/v1/cargo/shipments            我的发货单列表（?status=&page=&size=）
- GET    /api/v1/cargo/shipments/{id}      发货单详情
- PATCH  /api/v1/cargo/shipments/{id}      编辑（仅 draft）
- POST   /api/v1/cargo/shipments/{id}/publish   发布进入撮合池
- POST   /api/v1/cargo/shipments/{id}/cancel    取消
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.cargo import Cargo
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.cargo import service
from app.modules.cargo.schemas import (
    CargoCreate,
    CargoListResponse,
    CargoResponse,
    CargoUpdate,
)

router = APIRouter()


def _require_shipper(user: User) -> None:
    """F2 端点仅货主可用（三合一：以当前活跃角色判定）。"""
    if user.current_role != "shipper":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅货主角色可用，请先切换角色",
        )


def _get_owned_or_404(db: Session, cargo_id: int, shipper_id: int) -> Cargo:
    cargo = service.get_owned(db, cargo_id, shipper_id)
    if cargo is None:
        raise HTTPException(status_code=404, detail="发货单不存在")
    return cargo


@router.post("/shipments", response_model=CargoResponse, summary="创建发货单")
def create_shipment(
    body: CargoCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_shipper(user)
    try:
        return service.create_cargo(db, user.id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/shipments", response_model=CargoListResponse, summary="我的发货单列表")
def list_shipments(
    cargo_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_shipper(user)
    total, items = service.list_my_cargo(db, user.id, cargo_status, page, size)
    return CargoListResponse(total=total, items=[CargoResponse.model_validate(c) for c in items])


@router.get("/shipments/{cargo_id}", response_model=CargoResponse, summary="发货单详情")
def get_shipment(
    cargo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_shipper(user)
    return _get_owned_or_404(db, cargo_id, user.id)


@router.patch("/shipments/{cargo_id}", response_model=CargoResponse, summary="编辑发货单")
def update_shipment(
    cargo_id: int,
    body: CargoUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_shipper(user)
    cargo = _get_owned_or_404(db, cargo_id, user.id)
    try:
        return service.update_cargo(db, cargo, body)
    except service.CargoStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/shipments/{cargo_id}/publish", response_model=CargoResponse, summary="发布进入撮合池")
def publish_shipment(
    cargo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_shipper(user)
    cargo = _get_owned_or_404(db, cargo_id, user.id)
    try:
        return service.publish_cargo(db, cargo)
    except service.CargoStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/shipments/{cargo_id}/cancel", response_model=CargoResponse, summary="取消发货单")
def cancel_shipment(
    cargo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_shipper(user)
    cargo = _get_owned_or_404(db, cargo_id, user.id)
    try:
        return service.cancel_cargo(db, cargo)
    except service.CargoStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
