"""订单域实体（F6）：货-船撮合结果落地为交易单。

状态机（确定性内核，不经 LLM；工程底线 1/3）：
    matched（已撮合/待承运）→ shipped（已启运）→ completed（已签收）
    matched → cancelled（撤单，货源释放回撮合池）

全链路留痕：每个状态迁移落对应时间戳字段（matched_at/shipped_at/
completed_at/cancelled_at），撤单记录原因。

防重复出单：同一货源至多一个 active（matched/shipped）订单——
创建时 FOR UPDATE 锁货源行串行化并发出单（MySQL 生效，
SQLite 测试环境单连接天然串行）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Order(Base):
    """运输订单（货主基于撮合候选选定船东后创建）。"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cargo_id: Mapped[int] = mapped_column(
        ForeignKey("cargo.id"), index=True, comment="货源 ID"
    )
    ship_id: Mapped[int] = mapped_column(
        ForeignKey("ships.id"), index=True, comment="承运船舶 ID"
    )
    shipper_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="货主用户 ID"
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="船东用户 ID"
    )
    # 成交运费（元）；创建时取入参或货源出价
    freight_price: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="成交运费（元），空为面议"
    )
    # matched | shipped | completed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="matched", index=True, comment="状态")
    cancel_reason: Mapped[str] = mapped_column(String(255), default="", comment="撤单原因")

    # ---- 全链路留痕 ----
    matched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="撮合成交时间"
    )
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="启运时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="签收时间"
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="撤单时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
