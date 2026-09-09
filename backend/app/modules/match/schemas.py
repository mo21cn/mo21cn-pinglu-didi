"""撮合模块（match）请求/响应模型（F5）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateBreakdown(BaseModel):
    """单项得分拆解。"""

    load_utilization: float = Field(..., description="载重利用率得分（满分 40）")
    type_fit: float = Field(..., description="船型适配度得分（满分 20）")
    home_port: float = Field(..., description="船籍港就近得分（满分 20）")
    cert_margin: float = Field(..., description="证书余量得分（满分 20）")


class ShipCandidate(BaseModel):
    """候选船（货主视角）。"""

    ship_id: int
    ship_name: str
    ship_type: str
    deadweight_t: float
    home_port: str
    score: float = Field(..., description="综合得分（0-100，越高越优）")
    breakdown: CandidateBreakdown


class CargoShipsMatchResponse(BaseModel):
    """为货源找候选船的结果。"""

    cargo_id: int
    total: int
    filter_stats: dict[str, int] = Field(
        default_factory=dict, description="未入局原因统计（观测用）"
    )
    items: list[ShipCandidate]


class CargoCandidate(BaseModel):
    """候选货源（船东视角）。"""

    cargo_id: int
    cargo_name: str
    cargo_type: str
    weight_t: float
    origin_port: str
    dest_port: str
    expect_date: str
    score: float = Field(..., description="综合得分（0-100，越高越优）")
    breakdown: CandidateBreakdown


class ShipCargosMatchResponse(BaseModel):
    """为船找候选货源的结果。"""

    ship_id: int
    total: int
    filter_stats: dict[str, int] = Field(
        default_factory=dict, description="未入局原因统计（观测用）"
    )
    items: list[CargoCandidate]
