"""订单模块 Pydantic 模型（F6）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OrderCreate(BaseModel):
    """货主从撮合候选中选定船创建订单。"""

    cargo_id: int = Field(gt=0, description="货源 ID")
    ship_id: int = Field(gt=0, description="承运船舶 ID")
    # 运费（元）。注意：不会自动继承货源的 offer_price，须由调用方显式传入；
    # 不传即落库为 null（面议单），此时不可发起支付。
    freight_price: float | None = Field(default=None, gt=0, description="成交运费（元）")


class OrderCancel(BaseModel):
    reason: str = Field(default="", max_length=255, description="撤单原因")


class CargoSummary(BaseModel):
    """订单内嵌货源摘要（订单卡直接展示，免去前端二次拉取）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cargo_name: str
    cargo_type: str
    weight_t: float
    origin_port: str
    dest_port: str
    expect_date: date


class ShipSummary(BaseModel):
    """订单内嵌承运船舶摘要（同上）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ship_name: str
    ship_type: str
    deadweight_t: float
    home_port: str


class OrderOut(BaseModel):
    """订单完整视图（参与方可见）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cargo_id: int
    ship_id: int
    shipper_id: int
    owner_id: int
    freight_price: float | None
    status: Literal["matched", "shipped", "completed", "cancelled"]
    cancel_reason: str
    matched_at: datetime
    shipped_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    # 内嵌摘要：参与方（含船东）都有权看到所承运的货与船，
    # 前端无需再按角色拉「我的货源 / 我的船队」做富化。
    cargo: CargoSummary | None = None
    ship: ShipSummary | None = None


class OrderListResponse(BaseModel):
    """订单列表（角色视角分页）。"""

    total: int
    page: int
    size: int
    items: list[OrderOut]
