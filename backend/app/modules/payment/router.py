"""支付模块路由（F7）。

- POST /api/v1/payment/payments            货主发起支付（matched 订单）
- GET  /api/v1/payment/payments/{id}        支付单详情（仅参与方）
- GET  /api/v1/payment/payments/order/{oid} 按订单查支付单（仅参与方）
- POST /api/v1/payment/payments/{id}/mock-pay 模拟支付成功（联调期，货主）

退款/关闭由撤单联动（order.cancel_order → payment.settle_on_cancel），
不开放独立端点，保证资金流与订单流的状态一致性由确定性内核驱动。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.order.service import get_order
from app.modules.payment import service
from app.modules.payment.schemas import PaymentCreate, PaymentMockPay, PaymentOut

router = APIRouter()


def _get_payment_or_404(db: Session, payment_id: int, user: User) -> Any:
    payment = service.get_payment(db, payment_id)
    if payment is None or not service.is_participant(payment, user.id):
        raise HTTPException(status_code=404, detail="支付单不存在")
    return payment


@router.post(
    "/payments",
    response_model=PaymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="发起支付（货主，matched 订单）",
)
def create_payment(
    data: PaymentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if user.current_role != "shipper":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅货主角色可用，请先切换角色",
        )
    try:
        return service.create_payment(db, user.id, data)
    except service.PaymentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.PaymentStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/payments/{payment_id}", response_model=PaymentOut, summary="支付单详情（仅参与方）")
def get_payment(
    payment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    return _get_payment_or_404(db, payment_id, user)


@router.get(
    "/payments/order/{order_id}",
    response_model=PaymentOut,
    summary="按订单查支付单（仅参与方）",
)
def get_payment_by_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    order = get_order(db, order_id)
    if order is None or user.id not in (order.shipper_id, order.owner_id):
        raise HTTPException(status_code=404, detail="订单不存在")
    payment = service.get_by_order(db, order_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="该订单尚无支付单")
    return payment


@router.post(
    "/payments/{payment_id}/mock-pay",
    response_model=PaymentOut,
    summary="模拟支付成功（联调期回调，货主）",
)
def mock_pay(
    payment_id: int,
    data: PaymentMockPay | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    payment = _get_payment_or_404(db, payment_id, user)
    if payment.payer_id != user.id:
        raise HTTPException(status_code=404, detail="支付单不存在")
    try:
        return service.mock_pay(db, payment, (data.transaction_no if data else "") or "")
    except service.PaymentStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
