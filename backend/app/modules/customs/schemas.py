"""智慧关务（关域）请求体契约 —— `/api/v1/customs`。

字段纪律与 PRD §7 一致：

* 单号是**人工登记的原文**，不做格式猜测、不自动纠错（猜错比不猜更糟）；
* `source` 必填且非空 —— 一条"不知道从哪来的状态"不允许入库；
* `occurred_at` 可空（按登记时刻），`fetched_at` 由服务端写入，**不接受客户端传入**
  （否则"获取时间"可以随便伪造，AC-09 就失去意义）；
* `kind` 首版只开 `export`（Q-03）：把 `import` 传进来直接 422，比"静默接受但页面没有"
  诚实。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: 状态机全集（PRD §4.4）。`unknown` 是默认态，不是错误态。
DeclarationStatus = Literal[
    "unknown",
    "manifest_filed",
    "declared",
    "inspection",
    "released",
    "held",
    "rejected",
]


class DeclarationCreate(BaseModel):
    """人工登记一条报关记录（同时写入它的第一条状态事件）。"""

    declaration_no: str = Field(min_length=1, max_length=64, description="报关单号/提单号/舱单号")
    kind: Literal["export"] = Field(default="export", description="首版只开出口（Q-03）")
    status: DeclarationStatus = Field(description="首条事件的关务状态")
    occurred_at: datetime | None = Field(
        default=None, description="事件发生时间（UTC）；缺省＝登记时刻"
    )
    source: str = Field(min_length=1, max_length=128, description="来源原文，如「报关行口述」")
    note: str | None = Field(default=None, max_length=512)
    org_id: int | None = Field(default=None, description="所属组织；必须是你所在的组织")
    assignment_id: int | None = Field(default=None, description="弱关联的委托 id（可空）")


class DeclarationEventCreate(BaseModel):
    """给已有记录追加一条状态事件（只追加；更正请填 `corrects_event_id`）。"""

    status: DeclarationStatus
    occurred_at: datetime | None = None
    source: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=512)
    corrects_event_id: int | None = Field(
        default=None, description="更正某条事件时指向原事件；原记录保留（AC-06）"
    )
