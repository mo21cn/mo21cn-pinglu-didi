"""船域（船舶备案）路由（F3）。

备案/编辑端点要求船东（owner）角色；
审核端点临时由港口方（port）角色承担（三合一 MVP 港口方兼平台协同），
后续由运营角色接管（见《开发需求文档 V2.0》2.3 智能体与角色表）。

- POST   /api/v1/ship/registry               船舶备案
- GET    /api/v1/ship/registry               我的船队（?status=）
- GET    /api/v1/ship/registry/{id}           备案详情
- PATCH  /api/v1/ship/registry/{id}           编辑备案（verified 改关键信息自动降级重审）
- GET    /api/v1/ship/registry-pending        待审核列表（港口方）
- POST   /api/v1/ship/registry/{id}/verify    审核（港口方）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.ship import Ship
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.ship import service
from app.modules.ship.schemas import (
    ShipCreate,
    ShipListResponse,
    ShipResponse,
    ShipUpdate,
    ShipVerify,
)

router = APIRouter()


def _require_role(user: User, role: str, message: str) -> None:
    if user.current_role != role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _get_ship_or_404(db: Session, ship_id: int) -> Ship:
    ship = service.get_ship(db, ship_id)
    if ship is None:
        raise HTTPException(status_code=404, detail="船舶备案不存在")
    return ship


@router.post("/registry", response_model=ShipResponse, summary="船舶备案")
def create_ship(
    body: ShipCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_role(user, "owner", "该操作仅船东角色可用，请先切换角色")
    try:
        return service.create_ship(db, user.id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/registry", response_model=ShipListResponse, summary="我的船队")
def list_my_ships(
    ship_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_role(user, "owner", "该操作仅船东角色可用，请先切换角色")
    total, items = service.list_my_ships(db, user.id, ship_status, page, size)
    return ShipListResponse(total=total, items=[ShipResponse.model_validate(s) for s in items])


@router.get(
    "/registry-pending", response_model=ShipListResponse, summary="待审核船舶列表（港口方）"
)
def list_pending(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_role(user, "port", "该操作仅港口方角色可用")
    total, items = service.list_pending_ships(db, page, size)
    return ShipListResponse(total=total, items=[ShipResponse.model_validate(s) for s in items])


@router.get("/registry/{ship_id}", response_model=ShipResponse, summary="备案详情")
def get_ship(
    ship_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_role(user, "owner", "该操作仅船东角色可用，请先切换角色")
    ship = _get_ship_or_404(db, ship_id)
    if ship.owner_id != user.id:
        raise HTTPException(status_code=404, detail="船舶备案不存在")
    return ship


@router.patch("/registry/{ship_id}", response_model=ShipResponse, summary="编辑备案")
def update_ship(
    ship_id: int,
    body: ShipUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_role(user, "owner", "该操作仅船东角色可用，请先切换角色")
    ship = _get_ship_or_404(db, ship_id)
    if ship.owner_id != user.id:
        raise HTTPException(status_code=404, detail="船舶备案不存在")
    try:
        return service.update_ship(db, ship, body)
    except service.ShipStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/registry/{ship_id}/verify", response_model=ShipResponse, summary="审核船舶备案")
def verify_ship(
    ship_id: int,
    body: ShipVerify,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_role(user, "port", "该操作仅港口方角色可用")
    ship = _get_ship_or_404(db, ship_id)
    try:
        return service.verify_ship(db, ship, body.approved, body.reason)
    except service.ShipStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
