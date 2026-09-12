"""船域（船舶备案）请求/响应模型（F3）。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SHIP_TYPES = ("bulk", "general", "container", "tanker")

ShipType = Literal["bulk", "general", "container", "tanker"]


class ShipCreate(BaseModel):
    """船舶备案。"""

    ship_name: str = Field(..., min_length=1, max_length=64)
    ship_type: ShipType = "bulk"
    deadweight_t: float = Field(..., gt=0, le=50000, description="载重吨（吨）")
    length_m: float = Field(..., gt=0, le=300, description="船长（米）")
    width_m: float = Field(..., gt=0, le=60, description="船宽（米）")
    draft_m: float = Field(..., gt=0, le=15, description="满载吃水（米）")
    home_port: str = Field(default="", max_length=16, description="船籍港代码")
    cert_no: str = Field(..., min_length=1, max_length=64, description="船舶检验证书号")
    cert_expiry: date = Field(..., description="证书有效期至")

    @model_validator(mode="after")
    def check_cert(self) -> ShipCreate:
        if self.cert_expiry < date.today():
            raise ValueError("证书已过有效期，不能备案")
        return self


class ShipUpdate(BaseModel):
    """编辑船舶备案（verified 状态修改后将回到 pending_verify 重新审核）。"""

    ship_name: str | None = Field(default=None, min_length=1, max_length=64)
    ship_type: ShipType | None = None
    deadweight_t: float | None = Field(default=None, gt=0, le=50000)
    length_m: float | None = Field(default=None, gt=0, le=300)
    width_m: float | None = Field(default=None, gt=0, le=60)
    draft_m: float | None = Field(default=None, gt=0, le=15)
    home_port: str | None = Field(default=None, max_length=16)
    cert_no: str | None = Field(default=None, min_length=1, max_length=64)
    cert_expiry: date | None = None


class ShipVerify(BaseModel):
    """审核结论（港口方/平台侧临时承担，后续由运营角色接管）。"""

    approved: bool = Field(..., description="true=通过，false=驳回")
    reason: str = Field(default="", max_length=255, description="驳回原因")


class ShipResponse(BaseModel):
    """船舶备案视图。"""

    id: int
    owner_id: int
    ship_name: str
    ship_type: str
    deadweight_t: float
    length_m: float
    width_m: float
    draft_m: float
    home_port: str
    cert_no: str
    cert_expiry: date
    status: str
    reject_reason: str

    model_config = {"from_attributes": True}


class ShipListResponse(BaseModel):
    """船队列表（分页）。"""

    total: int
    items: list[ShipResponse]
