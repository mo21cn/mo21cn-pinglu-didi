"""货域（发货单）请求/响应模型（F2）。"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 广西主要港口代码（对齐官方 5 主港 + 7 地区港 + 北部湾三港区）
PORT_CODES = (
    "NNG",  # 南宁（含平塘港区，运河江海联运枢纽）
    "GGU",  # 贵港（内河第一大港）
    "WUZ",  # 梧州（东向大湾区门户）
    "BIN",  # 来宾
    "LZH",  # 柳州
    "BSZ",  # 百色
    "CHZ",  # 崇左
    "GXL",  # 桂林
    "HEZ",  # 贺州
    "YUL",  # 玉林
    "QNZ",  # 钦州（北部湾，海港）
    "FCG",  # 防城港（北部湾，海港）
    "BHZ",  # 北海（北部湾，海港）
    "HZZ",  # 钦州长基/沿线其他
)

CARGO_TYPES = ("bulk", "general", "container", "tanker", "other")

CargoType = Literal["bulk", "general", "container", "tanker", "other"]


class CargoCreate(BaseModel):
    """创建发货单。"""

    cargo_name: str = Field(..., min_length=1, max_length=64)
    cargo_type: CargoType = "bulk"
    weight_t: float = Field(..., gt=0, le=100000, description="重量（吨）")
    volume_m3: float | None = Field(default=None, gt=0, le=100000)
    origin_port: str = Field(..., description="起运港代码")
    dest_port: str = Field(..., description="目的港代码")
    expect_date: date = Field(..., description="期望装货日期")
    offer_price: float | None = Field(default=None, ge=0, description="运费出价（元），空为面议")
    remark: str = Field(default="", max_length=255)
    publish_now: bool = Field(default=False, description="true 时直接发布进入撮合池")

    @model_validator(mode="after")
    def check_ports(self) -> CargoCreate:
        if self.origin_port == self.dest_port:
            raise ValueError("起运港与目的港不能相同")
        if self.origin_port not in PORT_CODES:
            raise ValueError(f"未知起运港代码: {self.origin_port}")
        if self.dest_port not in PORT_CODES:
            raise ValueError(f"未知目的港代码: {self.dest_port}")
        return self


class CargoUpdate(BaseModel):
    """编辑发货单（仅 draft 可编辑）。"""

    cargo_name: str | None = Field(default=None, min_length=1, max_length=64)
    cargo_type: CargoType | None = None
    weight_t: float | None = Field(default=None, gt=0, le=100000)
    volume_m3: float | None = Field(default=None, gt=0, le=100000)
    origin_port: str | None = None
    dest_port: str | None = None
    expect_date: date | None = None
    offer_price: float | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def check_ports(self) -> CargoUpdate:
        if self.origin_port and self.dest_port and self.origin_port == self.dest_port:
            raise ValueError("起运港与目的港不能相同")
        return self


class CargoResponse(BaseModel):
    """发货单视图。"""

    id: int
    shipper_id: int
    cargo_name: str
    cargo_type: str
    weight_t: float
    volume_m3: float | None
    origin_port: str
    dest_port: str
    expect_date: date
    offer_price: float | None
    remark: str
    status: str

    model_config = {"from_attributes": True}


class CargoListResponse(BaseModel):
    """发货单列表（分页）。"""

    total: int
    items: list[CargoResponse]
