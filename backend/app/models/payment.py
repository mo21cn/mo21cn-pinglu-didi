"""支付域实体（F7）：订单运费支付单。

状态机（确定性内核，不经 LLM；工程底线 1/3）：
    pending（待支付）→ paid（已支付，货期保障）
    pending → closed（撤单联动/超时未付关闭）
    paid   → refunded（撤单联动全额退款）

全链路留痕：每个状态迁移落时间戳（created/paid/refunded/closed_at），
支付流水号 transaction_no、退款流水号 refund_no 双向留痕。

防重复支付：每订单至多一笔支付单（order_id 唯一约束 + 创建时
FOR UPDATE 锁订单行串行化，MySQL 生效，SQLite 测试天然串行）。

渠道抽象：channel 字段区分 mock（联调模拟）/ wechat_mp（微信小程序
支付，后续接入），MVP 默认 mock。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Payment(Base):
    """运费支付单（货主→平台→船东的资金流凭证）。"""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 一订单一支付单（MVP：撤单后订单为终态，不存在二次支付场景）
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id"), unique=True, index=True, comment="订单 ID"
    )
    payer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="付款方（货主）用户 ID"
    )
    payee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="收款方（船东）用户 ID"
    )
    amount: Mapped[float] = mapped_column(
        Numeric(12, 2), comment="支付金额（元），下单时锁定订单运费"
    )
    # pending | paid | refunded | closed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, comment="状态")
    # mock | wechat_mp
    channel: Mapped[str] = mapped_column(String(16), default="mock", comment="支付渠道")
    transaction_no: Mapped[str] = mapped_column(String(64), default="", comment="支付成功流水号")
    refund_no: Mapped[str] = mapped_column(String(64), default="", comment="退款流水号")

    # ---- 全链路留痕 ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="支付成功时间"
    )
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="退款时间"
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="关闭时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
