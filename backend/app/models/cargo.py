"""货域实体：发货单（货源）。

状态机（F2 范围实现前半段，matched 及之后由撮合/订单模块推进）：
    draft（草稿）→ published（已发布，进入撮合池）→ matched（已撮合，后续模块）
                                        ↘ cancelled（已取消）

硬校验：吨位>0、起讫港不同、期望日期不早于今天。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Cargo(Base):
    """货主发货单（货源）。"""

    __tablename__ = "cargo"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shipper_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="货主用户 ID"
    )
    cargo_name: Mapped[str] = mapped_column(String(64), comment="货物名称")
    # 货类：bulk 散货 / general 件杂货 / container 集装箱 / tanker 液货危险品 / other
    cargo_type: Mapped[str] = mapped_column(String(16), default="bulk", comment="货类")
    weight_t: Mapped[float] = mapped_column(Numeric(10, 2), comment="重量（吨）")
    volume_m3: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True, comment="体积（立方米）"
    )
    origin_port: Mapped[str] = mapped_column(String(16), comment="起运港代码")
    dest_port: Mapped[str] = mapped_column(String(16), comment="目的港代码")
    expect_date: Mapped[date] = mapped_column(Date, comment="期望装货日期")
    # 运费出价（元）；None=面议
    offer_price: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="运费出价（元），空为面议"
    )
    remark: Mapped[str] = mapped_column(String(255), default="", comment="备注")
    # draft | published | matched | shipped | completed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True, comment="状态")
    created_at: Mapped[str] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
