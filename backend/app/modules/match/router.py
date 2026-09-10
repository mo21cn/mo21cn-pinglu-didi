"""撮合模块路由（F5 Stage1）。撮合为只读推荐——不落库、不出单，
出单动作由 F6 订单模块基于候选结果发起。

- POST /api/v1/match/cargos/{cargo_id}/ships   为货源找候选船（货主）
- POST /api/v1/match/ships/{ship_id}/cargos    为船找候选货源（船东）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.cargo import service as cargo_service
from app.modules.match import service
from app.modules.match.schemas import CargoShipsMatchResponse, ShipCargosMatchResponse
from app.modules.ship import service as ship_service

router = APIRouter()


@router.post(
    "/cargos/{cargo_id}/ships",
    response_model=CargoShipsMatchResponse,
    summary="为货源找候选船（货主视角）",
)
def match_cargo(
    cargo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if user.current_role != "shipper":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅货主角色可用，请先切换角色",
        )
    cargo = cargo_service.get_owned(db, cargo_id, user.id)
    if cargo is None:
        raise HTTPException(status_code=404, detail="发货单不存在")
    try:
        return service.match_for_cargo(db, cargo)
    except service.MatchStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/ships/{ship_id}/cargos",
    response_model=ShipCargosMatchResponse,
    summary="为船找候选货源（船东视角）",
)
def match_ship(
    ship_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if user.current_role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅船东角色可用，请先切换角色",
        )
    ship = ship_service.get_ship(db, ship_id)
    if ship is None or ship.owner_id != user.id:
        raise HTTPException(status_code=404, detail="船舶备案不存在")
    try:
        return service.match_for_ship(db, ship)
    except service.MatchStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
