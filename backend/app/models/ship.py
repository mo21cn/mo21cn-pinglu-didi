"""船域实体：船舶备案。

状态机：
    pending_verify（待审核）→ verified（已通过，进入可用船池）→ rejected（驳回）
    verified 后修改备案信息 → 自动回到 pending_verify 重新审核

尺度字段（载重吨/吃水）是撮合引擎 Stage1 硬约束的核心输入。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Ship(Base):
    """船舶备案（船东拥有的船）。"""

    __tablename__ = "ships"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, comment="船东用户 ID"
    )
    ship_name: Mapped[str] = mapped_column(String(64), comment="船名")
    # 船型：bulk 散货船 / general 件杂货船 / container 集装箱船 / tanker 液货船
    ship_type: Mapped[str] = mapped_column(String(16), default="bulk", comment="船型")
    deadweight_t: Mapped[float] = mapped_column(Numeric(10, 2), comment="载重吨（吨）")
    length_m: Mapped[float] = mapped_column(Numeric(8, 2), comment="船长（米）")
    width_m: Mapped[float] = mapped_column(Numeric(8, 2), comment="船宽（米）")
    # 满载吃水（米）——撮合硬约束核心（航道/泊位水深校验）
    draft_m: Mapped[float] = mapped_column(Numeric(6, 2), comment="满载吃水（米）")
    home_port: Mapped[str] = mapped_column(String(16), default="", comment="船籍港代码")
    cert_no: Mapped[str] = mapped_column(String(64), comment="船舶检验证书号")
    cert_expiry: Mapped[date] = mapped_column(Date, comment="证书有效期至")
    # pending_verify | verified | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending_verify", index=True)
    reject_reason: Mapped[str] = mapped_column(String(255), default="", comment="驳回原因")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[str] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
