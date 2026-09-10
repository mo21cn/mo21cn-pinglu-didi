"""港域（泊位与泊位预约）请求/响应模型（F4）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.modules.cargo.schemas import PORT_CODES

SHIP_TYPE_VALUES = ("bulk", "general", "container", "tanker")


class BerthCreate(BaseModel):
    """新建泊位（港口方）。"""

    port_code: str = Field(..., description="港口代码（13 港体系）")
    berth_no: str = Field(..., min_length=1, max_length=16, description="泊位号")
    berth_name: str = Field(default="", max_length=64, description="泊位名称")
    max_dwt: float = Field(..., gt=0, le=50000, description="允许靠泊最大载重吨")
    max_draft: float = Field(..., gt=0, le=15, description="泊位允许吃水（米）")
    allowed_ship_types: list[Literal["bulk", "general", "container", "tanker"]] = Field(
        default_factory=lambda: list[Literal["bulk", "general", "container", "tanker"]](
            ["bulk"]
        ),
        min_length=1,
        description="适靠船型",
    )
    concurrent_capacity: int = Field(default=1, ge=1, le=4, description="并发靠泊容量")

    @model_validator(mode="after")
    def check_port(self) -> BerthCreate:
        if self.port_code not in PORT_CODES:
            raise ValueError(f"未知港口代码 {self.port_code}，须为 13 港体系内代码")
        return self


class BerthUpdate(BaseModel):
    """编辑泊位（改尺度约束不影响已有 confirmed 预约，新预约按新参数校验）。"""

    berth_name: str | None = Field(default=None, max_length=64)
    max_dwt: float | None = Field(default=None, gt=0, le=50000)
    max_draft: float | None = Field(default=None, gt=0, le=15)
    allowed_ship_types: list[Literal["bulk", "general", "container", "tanker"]] | None = None
    concurrent_capacity: int | None = Field(default=None, ge=1, le=4)
    status: Literal["active", "inactive"] | None = None


class BerthResponse(BaseModel):
    """泊位视图。"""

    id: int
    port_code: str
    berth_no: str
    berth_name: str
    max_dwt: float
    max_draft: float
    allowed_ship_types: list[str]
    concurrent_capacity: int
    status: str

    model_config = {"from_attributes": True}


class BerthListResponse(BaseModel):
    total: int
    items: list[BerthResponse]


class BerthApptCreate(BaseModel):
    """船东申请泊位预约。

    硬校验（撮合 Stage1 约束在港态的体现）：
    船须 verified；ship.deadweight_t ≤ berth.max_dwt；
    ship.draft_m ≤ berth.max_draft；ship.ship_type ∈ berth.allowed_ship_types。
    """

    berth_id: int = Field(..., description="目标泊位 ID")
    ship_id: int = Field(..., description="预约船舶 ID（须为本人 verified 船）")
    plan_start: datetime = Field(..., description="计划靠泊时间")
    plan_end: datetime = Field(..., description="计划离泊时间")
    remark: str = Field(default="", max_length=255, description="申请备注")

    @model_validator(mode="after")
    def check_window(self) -> BerthApptCreate:
        if self.plan_start >= self.plan_end:
            raise ValueError("离泊时间必须晚于靠泊时间")
        if self.plan_start < datetime.now().replace(tzinfo=None):
            raise ValueError("靠泊时间不能早于当前时间")
        return self


class BerthApptReview(BaseModel):
    """港口方确认/驳回。"""

    reason: str = Field(default="", max_length=255, description="驳回原因")


class BerthApptResponse(BaseModel):
    """预约视图。"""

    id: int
    berth_id: int
    ship_id: int
    applier_id: int
    plan_start: datetime
    plan_end: datetime
    remark: str
    status: str
    reject_reason: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class BerthApptListResponse(BaseModel):
    total: int
    items: list[BerthApptResponse]


class BerthScheduleItem(BaseModel):
    """泊位档期视图（某泊位的 confirmed 预约占用情况）。"""

    appt_id: int
    ship_id: int
    plan_start: datetime
    plan_end: datetime


class BerthScheduleResponse(BaseModel):
    """泊位档期查询结果。"""

    berth: BerthResponse
    confirmed: list[BerthScheduleItem]


# 日期占位（未来档期按日查询扩展用）
_DateHint = date
