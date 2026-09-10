"""货源解析 Agent 请求/响应模型（F9）。

定位（工程底线 2：Agent 无直写）：
- 输入：货主的自然语言描述（口语化，如"我有 800 吨水泥要从南宁运到贵港"）
- 输出：结构化货源**草稿**（对齐 cargo.schemas.CargoCreate 字段）+ 置信度
  + 待人工确认字段列表；**绝不直接创建发货单**——用户在小程序上核对草稿
  后走 POST /cargo/shipments 正常业务 API 落库。
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class CargoParseRequest(BaseModel):
    """货源解析请求。"""

    text: str = Field(..., min_length=4, max_length=512, description="货源自然语言描述")


class ParsedCargoField(BaseModel):
    """单个解析出的字段（供小程序表单回填预览）。"""

    cargo_name: str | None = Field(default=None, description="货物名称")
    cargo_type: Literal["bulk", "general", "container", "tanker", "other"] | None = None
    weight_t: float | None = Field(default=None, gt=0, le=100000, description="重量（吨）")
    origin_port: str | None = Field(default=None, description="起运港代码（13 港之一）")
    dest_port: str | None = Field(default=None, description="目的港代码")
    expect_date: date | None = Field(default=None, description="期望装货日期")
    offer_price: float | None = Field(default=None, ge=0, description="运费出价（元）")


class CargoParseResult(BaseModel):
    """货源解析结果（草稿，不落库）。"""

    draft: ParsedCargoField = Field(description="结构化货源草稿（回填表单用）")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="整体置信度（0-1，低置信字段越多越低）"
    )
    needs_review: list[str] = Field(
        default_factory=list, description="缺失/低置信、需人工补充确认的字段名"
    )
    mocked: bool = Field(default=False, description="是否为规则模板输出（LLM_MOCK）")
    latency_ms: int = Field(default=0, description="解析耗时（毫秒）")


class AssistantRequest(BaseModel):
    """客服导购请求（FAQ / 航线 / 用法咨询，纯读）。"""

    question: str = Field(..., min_length=2, max_length=512, description="用户问题")
    history: list[dict] = Field(
        default_factory=list,
        max_length=10,
        description="多轮上下文（[{role, content}]，最近 10 条）",
    )


class AssistantResult(BaseModel):
    """客服导购回答。"""

    answer: str = Field(..., min_length=1, description="回答文本（Markdown 简易格式）")
    mocked: bool = Field(default=False, description="是否为规则模板输出（LLM_MOCK）")
    latency_ms: int = Field(default=0, description="回答耗时（毫秒）")


class ContractGenerateRequest(BaseModel):
    """合同生成请求。"""

    order_id: int = Field(..., gt=0, description="订单 ID")


class ContractRisk(BaseModel):
    """单个风险点（由确定性规则引擎产生，零 LLM）。"""

    severity: Literal["high", "medium", "low"] = Field(description="风险等级")
    title: str = Field(description="风险标题")
    detail: str = Field(description="风险说明（基于订单事实）")
    suggestion: str = Field(description="处置建议")


class ContractDraftResult(BaseModel):
    """合同草稿生成结果。

    安全设计：核心条款（金额/日期/港口/主体）由订单数据模板渲染；
    LLM 仅产出补充条款文字。草稿不落库、不具法律效力。
    """

    order_id: int = Field(description="订单 ID")
    contract_text: str = Field(description="合同草稿全文（Markdown）")
    risks: list[ContractRisk] = Field(default_factory=list, description="风险点（确定性规则引擎）")
    mocked: bool = Field(default=False, description="是否为规则模板输出（LLM_MOCK）")
    latency_ms: int = Field(default=0, description="生成耗时（毫秒）")
