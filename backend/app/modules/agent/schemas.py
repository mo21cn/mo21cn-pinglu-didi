"""智能体域请求/响应模型（F9–F20）。

定位（工程底线 2：Agent 无直写）：
- 输入：货主的自然语言描述（口语化，如"我有 800 吨水泥要从南宁运到贵港"）
- 输出：结构化货源**草稿**（对齐 cargo.schemas.CargoCreate 字段）+ 置信度
  + 待人工确认字段列表；**绝不直接创建发货单**——用户在小程序上核对草稿
  后走 POST /cargo/shipments 正常业务 API 落库。

本文件同时承载 F17 合规初筛与 F20 统一入口（Router）的模型。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

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
    history: list[dict[str, str]] = Field(
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
    degraded: bool = Field(
        default=False, description="LLM 故障已降级：补充条款回退平台内置标准条款"
    )
    degraded_reason: str | None = Field(
        default=None, description="降级原因（LLM 错误分类：timeout/network/auth/rate_limit/...）"
    )


# ===========================================================================
# F17 合规初筛（发布货源 / 船舶备案前的即时预检，确定性规则、零 LLM）
# ===========================================================================


class ComplianceFinding(BaseModel):
    """单条合规结论（确定性规则引擎产物）。"""

    code: str = Field(description="规则编号（C1–C4 货源 / S1–S5 船舶）")
    severity: Literal["block", "warn"] = Field(description="block=阻断，warn=提示")
    title: str = Field(description="结论标题")
    detail: str = Field(description="结论说明（基于提交内容的事实）")
    suggestion: str = Field(description="处置建议")


class ComplianceResult(BaseModel):
    """合规初筛结果。

    语义：``level`` 为总体结论，前端据此决定提示强度；
    **本结果不阻断写入**（Agent 无直写），放行由用户与业务域校验决定。
    """

    target: Literal["cargo", "ship", "text"] = Field(description="预检对象")
    level: Literal["pass", "warn", "block"] = Field(description="总体结论")
    summary: str = Field(description="一句话结论（可直接展示给用户）")
    findings: list[ComplianceFinding] = Field(default_factory=list, description="逐条结论")
    checked_rules: int = Field(default=0, description="本次实际检查的规则条数")


class CargoComplianceRequest(BaseModel):
    """货源合规预检请求（对齐发布货源表单字段）。"""

    cargo_name: str = Field(..., min_length=1, max_length=64)
    cargo_type: Literal["bulk", "general", "container", "tanker", "other"] = "bulk"
    weight_t: float = Field(..., gt=0, le=100000)
    origin_port: str = Field(..., description="起运港代码")
    dest_port: str = Field(..., description="目的港代码")
    expect_date: date = Field(..., description="期望装货日期")
    remark: str = Field(default="", max_length=255)


class ShipComplianceRequest(BaseModel):
    """船舶备案合规预检请求（对齐船舶备案表单字段）。"""

    ship_name: str = Field(..., min_length=1, max_length=64)
    ship_type: Literal["bulk", "general", "container", "tanker"] = "bulk"
    deadweight_t: float = Field(..., gt=0, le=50000)
    length_m: float = Field(..., gt=0, le=300)
    width_m: float = Field(..., gt=0, le=60)
    draft_m: float = Field(..., gt=0, le=15)
    home_port: str = Field(default="", max_length=16)
    cert_no: str = Field(..., min_length=1, max_length=64)
    cert_expiry: date = Field(..., description="证书有效期至")


# ===========================================================================
# F20 统一入口（Router 意图路由）
# ===========================================================================

Intent = Literal["cargo_parse", "compliance", "contract", "assistant"]


class RouteRequest(BaseModel):
    """统一入口请求：一句自然语言 + 可选订单上下文。"""

    text: str = Field(..., min_length=2, max_length=512, description="用户输入")
    order_id: int | None = Field(default=None, gt=0, description="订单 ID（合同意图需要）")


class RouteResult(BaseModel):
    """意图路由结果：命中意图 + 派发结论 + 被派发 Agent 的原始返回。

    ``dispatched=False`` 表示只做了意图判定、未实际调用下游（例如缺订单上下文、
    角色不匹配），此时 ``message`` 给出引导语，前端据此提示或跳转。
    """

    intent: Intent = Field(description="命中的意图")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="分类置信度")
    matched: str = Field(default="", description="命中的分类依据（可解释性）")
    dispatched: bool = Field(default=False, description="是否已派发到下游 Agent")
    target: str = Field(default="", description="下游目标（端点或动作名）")
    message: str = Field(default="", description="给用户的引导语（未派发时必填）")
    result: dict[str, Any] | None = Field(
        default=None, description="下游 Agent 的结构化返回（形状随 intent 变化）"
    )
