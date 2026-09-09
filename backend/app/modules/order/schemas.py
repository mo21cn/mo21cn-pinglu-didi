"""订单模块 Pydantic 模型（F6）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OrderCreate(BaseModel):
    """货主从撮合候选中选定船创建订单。"""

    cargo_id: int = Field(gt=0, description="货源 ID")
    ship_id: int = Field(gt=0, description="承运船舶 ID")
    # 运费（元）；空则沿用货源出价（offer_price，仍空为面议）
    freight_price: float | None = Field(default=None, gt=0, description="成交运费（元）")


class OrderCancel(BaseModel):
    reason: str = Field(default="", max_length=255, description="撤单原因")


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


class OrderListResponse(BaseModel):
    """订单列表（角色视角分页）。"""

    total: int
    page: int
    size: int
    items: list[OrderOut]
