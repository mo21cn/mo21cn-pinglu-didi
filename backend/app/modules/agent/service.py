"""货源解析 Agent 编排（F9）。

流程：自然语言 → LLM（结构化 JSON）→ pydantic 校验 + 港口/日期合法性过滤
→ AgentCall 审计落库 → 返回草稿（前端回填表单，用户确认后走 cargo API 创建）。

Agent 无直写（工程底线 2）：本模块不 import cargo.service，不写任何业务表
（除审计表 agent_calls）；确定性内核零接触。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.agent import AgentCall
from app.modules.agent import llm as llm_gateway
from app.modules.agent.llm import LLMError
from app.modules.agent.schemas import CargoParseResult, ParsedCargoField
from app.modules.cargo.schemas import PORT_CODES

_DIGEST_LEN = 512

SYSTEM_PROMPT = """你是平陆运河"滴滴打船"平台的货源解析助手。任务：把货主的口语化描述解析为结构化货源草稿。

输出严格 JSON 对象，字段如下（信息缺失时置 null，不要编造）：
{
  "cargo_name": string|null,      // 货物名称，如"散装水泥"
  "cargo_type": string|null,      // 枚举: bulk(散货)/general(件杂货)/container(集装箱)/tanker(液货)/other
  "weight_t": number|null,        // 重量（吨）
  "origin_port": string|null,    // 起运港代码，只能是: NNG/GGU/WUZ/BIN/LZH/BSZ/CHZ/GXL/HEZ/YUL/QNZ/FCG/BHZ
  "dest_port": string|null,      // 目的港代码，同上
  "expect_date": string|null,    // 期望装货日期 "YYYY-MM-DD"（口语"下周三"等按今天推算）
  "offer_price": number|null,    // 运费出价（元；"2万5"→25000）
  "field_confidence": {           // 每个字段的置信度 0-1
    "cargo_name": number, "cargo_type": number, "weight_t": number,
    "origin_port": number, "dest_port": number, "expect_date": number, "offer_price": number
  }
}

规则：
1. 港口用"城市名→代码"映射：南宁→NNG、贵港→GGU、梧州→WUZ、来宾→BIN、柳州→LZH、百色→BSZ、
   崇左→CHZ、桂林→GXL、贺州→HEZ、玉林→YUL、钦州→QNZ、防城港→FCG、北海→BHZ。
2. 只输出 JSON，不要任何其他文字。
"""


class AgentServiceError(Exception):
    """Agent 服务错误（携带 kind 便于路由层映射 HTTP 状态）。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _sanitize(field: str, value, today: date) -> tuple[object, bool]:
    """单字段合法化过滤；返回 (清洗后值, 是否需人工复核)。"""
    if value is None:
        return None, True
    if field in ("origin_port", "dest_port"):
        ok = isinstance(value, str) and value in PORT_CODES
        return (value if ok else None), (not ok)
    if field == "expect_date":
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError:
            return None, True
        return (parsed if parsed >= today else None), (parsed < today)
    return value, False


async def parse_cargo(db: Session, *, user_id: int, text: str) -> CargoParseResult:
    """解析货源自然语言 → 草稿（含审计落库）。"""
    settings = get_settings()
    today = date.today()

    record = AgentCall(
        user_id=user_id,
        agent_name="cargo_parse",
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        prompt_digest=text[:_DIGEST_LEN],
    )

    try:
        result = await llm_gateway.chat_json(system=SYSTEM_PROMPT, user=text)
    except LLMError as exc:
        record.success = False
        record.error_kind = exc.kind
        record.response_digest = str(exc)[:_DIGEST_LEN]
        db.add(record)
        db.commit()
        raise AgentServiceError(exc.kind, str(exc)) from exc

    raw: dict = result.content
    field_conf: dict = raw.get("field_confidence") or {}

    cleaned: dict = {}
    needs_review: list[str] = []
    confidences: list[float] = []

    for field in (
        "cargo_name", "cargo_type", "weight_t",
        "origin_port", "dest_port", "expect_date", "offer_price",
    ):
        value, review = _sanitize(field, raw.get(field), today)
        if review:
            needs_review.append(field)
        confidences.append(float(field_conf.get(field) or (0.9 if value is not None and not review else 0.0)))
        cleaned[field] = value

    draft = ParsedCargoField(**cleaned)
    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

    record.mocked = result.mocked
    record.latency_ms = result.latency_ms
    record.response_digest = result.raw[:_DIGEST_LEN]
    db.add(record)
    db.commit()

    return CargoParseResult(
        draft=draft,
        confidence=confidence,
        needs_review=needs_review,
        mocked=result.mocked,
        latency_ms=result.latency_ms,
    )
