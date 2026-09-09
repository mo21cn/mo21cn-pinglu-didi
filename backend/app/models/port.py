"""港域实体：泊位与泊位预约（F4）。

Berth（泊位）：
    active（可用）| inactive（停用）
    尺度约束（max_dwt/max_draft/allowed_ship_types）是撮合引擎 Stage1
    硬约束在"港"态的直接输入——船舶预约时逐项校验。

BerthAppt（泊位预约）——稀缺资源防超卖核心：
    pending（待港口方确认）→ confirmed（锁定泊位档期，计入容量）
                        → rejected（驳回）/ cancelled（撤销）/ completed（完成）
    防超卖规则：同一泊位、时间窗重叠的 confirmed 预约数
    不得超过泊位并发容量（concurrent_capacity）。
    预约"确认即锁定"，是订单状态机防超卖体系的一环
    （见《开发需求文档 V2.0》6. 数据需求——稀缺资源型实体）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.user import Base


class Berth(Base):
    """泊位（港口方维护的靠泊资源）。"""

    __tablename__ = "berths"
    __table_args__ = (
        UniqueConstraint("port_code", "berth_no", name="uq_berth_port_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 港口代码（13 港体系：NNG/GGU/WUZ/QNZ/FCG/BHZ...）
    port_code: Mapped[str] = mapped_column(String(16), index=True, comment="港口代码")
    berth_no: Mapped[str] = mapped_column(String(16), comment="泊位号（如 01/02）")
    berth_name: Mapped[str] = mapped_column(String(64), default="", comment="泊位名称")
    # 靠泊能力约束——撮合 Stage1 硬约束输入
    max_dwt: Mapped[float] = mapped_column(Numeric(10, 2), comment="允许靠泊最大载重吨")
    max_draft: Mapped[float] = mapped_column(Numeric(6, 2), comment="泊位水深允许吃水（米）")
    # 适靠船型（JSON 数组，取值同 Ship.ship_type：bulk/general/container/tanker）
    allowed_ship_types: Mapped[list] = mapped_column(JSON, default=list, comment="适靠船型")
    # 并发容量：同一时间窗内最多允许的 confirmed 预约数（物理泊位多为 1）
    concurrent_capacity: Mapped[int] = mapped_column(Integer, default=1, comment="并发靠泊容量")
    # active | inactive
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class BerthAppt(Base):
    """泊位预约（船 × 泊位 × 时间窗）。

    状态机：
        pending（船东申请，待港口方确认）
          → confirmed（港口方确认，档期锁定，计入泊位容量）
          → rejected（港口方驳回）
          → cancelled（船东/港口方撤销，释放档期）
          → completed（靠泊完成，港口方核销）
    只有 confirmed 计入容量；pending 不占用档期（避免恶意占位），
    但港口方确认时做重叠校验，超容量即拒绝——防超卖。
    """

    __tablename__ = "berth_appts"
    __table_args__ = (
        # 常用查询：按泊位看档期占用
        Index("ix_berth_appts_berth_status", "berth_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    berth_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("berths.id"), index=True, comment="泊位 ID"
    )
    ship_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ships.id"), index=True, comment="预约船舶 ID"
    )
    applier_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, comment="申请人（船东）用户 ID"
    )
    plan_start: Mapped[datetime] = mapped_column(DateTime, comment="计划靠泊时间")
    plan_end: Mapped[datetime] = mapped_column(DateTime, comment="计划离泊时间")
    remark: Mapped[str] = mapped_column(String(255), default="", comment="申请备注")
    # pending | confirmed | rejected | cancelled | completed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reject_reason: Mapped[str] = mapped_column(String(255), default="", comment="驳回原因")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
