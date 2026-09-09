"""订单模块路由（F6）。

- POST   /api/v1/order/orders              货主创建订单（撮合候选选船）
- GET    /api/v1/order/orders              订单列表（角色视角分页）
- GET    /api/v1/order/orders/{id}         订单详情（仅参与方）
- POST   /api/v1/order/orders/{id}/ship    船东启运（matched→shipped）
- POST   /api/v1/order/orders/{id}/complete 货主签收（shipped→completed）
- POST   /api/v1/order/orders/{id}/cancel  撤单（matched→cancelled，任一方）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.order import service
from app.modules.order.schemas import OrderCancel, OrderCreate, OrderListResponse, OrderOut

router = APIRouter()


def _get_order_or_404(db: Session, order_id: int, user: User):
    order = service.get_order(db, order_id)
    if order is None or not service.is_participant(order, user.id):
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


@router.post(
    "/orders",
    response_model=OrderOut,
    status_code=status.HTTP_200_OK,
    summary="创建订单（货主，基于撮合候选）",
)
def create_order(
    data: OrderCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.current_role != "shipper":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅货主角色可用，请先切换角色",
        )
    try:
        return service.create_order(db, user.id, data)
    except service.OrderConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.OrderStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/orders", response_model=OrderListResponse, summary="订单列表（角色视角）")
def list_orders(
    order_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.current_role not in ("shipper", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅货主/船东角色可查看订单",
        )
    try:
        total, items = service.list_orders(
            db, user.id, user.current_role, order_status=order_status, page=page, size=size
        )
    except service.OrderStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OrderListResponse(
        total=total, page=page, size=size, items=[OrderOut.model_validate(o) for o in items]
    )


@router.get("/orders/{order_id}", response_model=OrderOut, summary="订单详情（仅参与方）")
def get_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_order_or_404(db, order_id, user)


@router.post(
    "/orders/{order_id}/ship",
    response_model=OrderOut,
    summary="启运（船东，matched→shipped）",
)
def ship_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.current_role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅船东角色可用，请先切换角色",
        )
    order = _get_order_or_404(db, order_id, user)
    if order.owner_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    try:
        return service.ship_order(db, order)
    except service.OrderStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/orders/{order_id}/complete",
    response_model=OrderOut,
    summary="签收（货主，shipped→completed）",
)
def complete_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.current_role != "shipper":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅货主角色可用，请先切换角色",
        )
    order = _get_order_or_404(db, order_id, user)
    if order.shipper_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    try:
        return service.complete_order(db, order)
    except service.OrderStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderOut,
    summary="撤单（货主或船东，matched→cancelled，货源释放回撮合池）",
)
def cancel_order(
    order_id: int,
    data: OrderCancel | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.current_role not in ("shipper", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅货主/船东角色可撤单",
        )
    order = _get_order_or_404(db, order_id, user)
    try:
        return service.cancel_order(db, order, (data.reason if data else "") or "")
    except service.OrderStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
