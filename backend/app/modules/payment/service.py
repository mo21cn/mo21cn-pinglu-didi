"""支付模块业务逻辑（F7）。

核心纪律：
1. 状态机（确定性内核，不经 LLM）：
   pending → paid（支付成功回调）
   pending → closed（撤单联动/超时未付）
   paid → refunded（撤单联动全额退款）
2. 防重复支付——一订单一支付单（order_id 唯一约束），创建时
   FOR UPDATE 锁订单行串行化（MySQL 生效；SQLite 测试单连接
   天然串行，镜像 F4/F6 模式）。
3. 支付成功回调幂等——重复回调返回当前单，不重复记账。
4. 全链路留痕——每次迁移落时间戳 + 双流水号。
5. 金额锁定——创建支付单时锁定订单运费，后续订单字段变化不影响。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.payment import Payment
from app.modules.payment.schemas import PaymentCreate


class PaymentStateError(Exception):
    """支付状态机/前置条件错误。"""


class PaymentConflictError(Exception):
    """订单已有支付单（防重复支付拦截）。"""


def create_payment(db: Session, payer_id: int, data: PaymentCreate) -> Payment:
    """货主对 matched 订单发起支付（创建 pending 支付单）。

    前置校验：
    - 订单存在、付款方为订单货主本人；
    - 订单状态为 matched（启运后/已完成/已撤单均不可发起）；
    - 运费已议定（freight_price 非空，面议单须先议价）；
    - 订单无支付单（FOR UPDATE 锁订单行串行化校验）。
    """
    order = db.get(Order, data.order_id)
    if order is None or order.shipper_id != payer_id:
        raise PaymentStateError("订单不存在或不属于当前货主")
    if order.status != "matched":
        raise PaymentStateError(f"当前订单状态 {order.status} 不可发起支付，须为已撮合待承运")
    if order.freight_price is None:
        raise PaymentStateError("订单运费为面议，须先与船东议定运价再支付")

    # 防重复支付：锁订单行，串行化同一订单的并发发起
    db.execute(
        select(Order).where(Order.id == order.id).with_for_update()
    ).scalar_one()
    existing = db.execute(
        select(Payment).where(Payment.order_id == order.id)
    ).scalar_one_or_none()
    if existing is not None:
        raise PaymentConflictError("该订单已存在支付单，不可重复发起")

    payment = Payment(
        order_id=order.id,
        payer_id=order.shipper_id,
        payee_id=order.owner_id,
        amount=order.freight_price,
        status="pending",
        channel=data.channel,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def mock_pay(db: Session, payment: Payment, transaction_no: str = "") -> Payment:
    """模拟支付成功回调：pending → paid。

    幂等：已 paid 的单直接返回，不重复记账（paid_at 不变）。
    """
    if payment.status == "paid":
        return payment
    if payment.status != "pending":
        raise PaymentStateError(f"当前支付单状态 {payment.status} 不可支付")
    payment.status = "paid"
    payment.transaction_no = transaction_no or f"MOCK-{uuid.uuid4().hex[:24]}"
    payment.paid_at = datetime.now()
    db.commit()
    db.refresh(payment)
    return payment


def settle_on_cancel(db: Session, order: Order) -> None:
    """撤单联动结算（由订单模块 cancel_order 调用）。

    - paid → refunded（全额退款，生成退款流水号）
    - pending → closed（未付关闭）
    - 无支付单/已终态支付单 → 无操作
    """
    payment = db.execute(
        select(Payment).where(Payment.order_id == order.id)
    ).scalar_one_or_none()
    if payment is None:
        return
    if payment.status == "paid":
        payment.status = "refunded"
        payment.refund_no = f"RF-{uuid.uuid4().hex[:24]}"
        payment.refunded_at = datetime.now()
    elif payment.status == "pending":
        payment.status = "closed"
        payment.closed_at = datetime.now()


def get_payment(db: Session, payment_id: int) -> Payment | None:
    return db.get(Payment, payment_id)


def get_by_order(db: Session, order_id: int) -> Payment | None:
    return db.execute(
        select(Payment).where(Payment.order_id == order_id)
    ).scalar_one_or_none()


def is_participant(payment: Payment, user_id: int) -> bool:
    """支付单参与方判定（付款方或收款方）。"""
    return user_id in (payment.payer_id, payment.payee_id)
