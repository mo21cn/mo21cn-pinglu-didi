"""支付模块 Pydantic 模型（F7）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaymentCreate(BaseModel):
    """货主发起支付。"""

    order_id: int = Field(description="订单 ID（须为 matched 状态）")
    channel: str = Field(default="mock", description="支付渠道：mock / wechat_mp")


class PaymentMockPay(BaseModel):
    """模拟支付成功回调（联调期替代真实微信支付回调）。"""

    transaction_no: str = Field(default="", description="支付流水号，缺省自动生成")


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    payer_id: int
    payee_id: int
    amount: float
    status: str
    channel: str
    transaction_no: str
    refund_no: str
    created_at: datetime
    paid_at: datetime | None = None
    refunded_at: datetime | None = None
    closed_at: datetime | None = None
