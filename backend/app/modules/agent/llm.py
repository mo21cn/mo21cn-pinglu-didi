"""LLM 网关客户端（F9 Agent 底座）。

设计要点（对齐架构红线：确定性内核不经 LLM）：
- 统一入口 ``chat_json``：面向"结构化输出"场景，LLM 仅承担理解/解析/辅助，
  绝不进入撮合 / 支付 / 订单状态机等确定性内核。
- 供应商协议：OpenAI 兼容 ``/chat/completions``（DeepSeek 起步，可平移 Qwen 等）。
- ``LLM_MOCK=true``：规则模板模式，供 CI / 无 Key 开发；不发起任何网络请求。
- 错误分类（LLMError.kind）：timeout / network / auth / rate_limit / bad_request /
  bad_response（JSON 解析失败）/ unknown，调用方可据此降级（返回 503 提示稍后重试）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


class LLMError(Exception):
    """LLM 调用异常（kind 分类便于上层降级处理）。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class LLMResult:
    """一次 LLM 调用的结果（content 为解析后的 dict；raw 保留原始文本供审计）。"""

    content: dict[str, Any]
    raw: str
    latency_ms: int
    mocked: bool


async def chat_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.1,
    mock_content: Any = None,
) -> LLMResult:
    """调用 LLM 并强制返回 JSON 对象。

    - 真实模式：``response_format={"type": "json_object"}`` 约束输出；
      响应体必须能 ``json.loads`` 出 dict，否则抛 ``bad_response``。
    - Mock 模式：默认走货源解析规则模板；调用方可传 ``mock_content``
      （``Callable[[str], dict]``）注入自己的领域模板，保证各 Agent
      在 LLM_MOCK 下输出结构与真实模式同构（CI 可全链路验证）。
    """
    settings = get_settings()
    import time

    started = time.monotonic()

    if settings.LLM_MOCK or not settings.LLM_API_KEY:
        content = mock_content(user) if callable(mock_content) else _mock_chat_json(user)
        return LLMResult(
            content=content,
            raw=json.dumps(content, ensure_ascii=False),
            latency_ms=int((time.monotonic() - started) * 1000),
            mocked=True,
        )

    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise LLMError("timeout", f"LLM 调用超时（{settings.LLM_TIMEOUT_SECONDS}s）") from exc
    except httpx.HTTPError as exc:
        raise LLMError("network", f"LLM 网络错误：{exc}") from exc

    if resp.status_code in (401, 403):
        raise LLMError("auth", "LLM API Key 无效或无权限")
    if resp.status_code == 429:
        raise LLMError("rate_limit", "LLM 限流，请稍后重试")
    if resp.status_code >= 500:
        raise LLMError("network", f"LLM 服务端错误 {resp.status_code}")
    if resp.status_code != 200:
        raise LLMError("bad_request", f"LLM 请求被拒绝 {resp.status_code}: {resp.text[:200]}")

    try:
        body = resp.json()
        raw: str = body["choices"][0]["message"]["content"]
        content = json.loads(raw)
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError("bad_response", "LLM 输出无法解析为 JSON") from exc
    if not isinstance(content, dict):
        raise LLMError("bad_response", "LLM 输出 JSON 不是对象")

    return LLMResult(
        content=content,
        raw=raw,
        latency_ms=int((time.monotonic() - started) * 1000),
        mocked=False,
    )


# ---------------------------------------------------------------------------
# Mock 规则模板（LLM_MOCK=true，CI / 无 Key 开发用）
# ---------------------------------------------------------------------------

# 13 港中文名 → 代码（与 cargo.schemas.PORT_CODES 对齐）
_MOCK_PORT_ALIASES: dict[str, str] = {
    "南宁": "NNG",
    "贵港": "GGU",
    "梧州": "WUZ",
    "来宾": "BIN",
    "柳州": "LZH",
    "百色": "BSZ",
    "崇左": "CHZ",
    "桂林": "GXL",
    "贺州": "HEZ",
    "玉林": "YUL",
    "钦州": "QNZ",
    "防城港": "FCG",
    "北海": "BHZ",
}

_MOCK_TYPE_HINTS: list[tuple[str, str]] = [
    ("集装箱", "container"),
    ("液货", "tanker"),
    ("油", "tanker"),
    ("化工", "tanker"),
    ("水泥", "bulk"),
    ("矿", "bulk"),
    ("煤", "bulk"),
    ("砂", "bulk"),
    ("石", "bulk"),
    ("粮", "bulk"),
    ("钢", "general"),
    ("设备", "general"),
    ("件杂", "general"),
]

_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:吨|t|T)")
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|万?块钱|块)")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _mock_chat_json(text: str) -> dict[str, Any]:
    """规则模板：从自然语言里正则抽取货源字段（仅联调/CI 冒烟，不追求召回）。"""
    weight = None
    if m := _WEIGHT_RE.search(text):
        weight = float(m.group(1))

    ports: list[str] = []
    for name, code in _MOCK_PORT_ALIASES.items():
        if name in text and code not in ports:
            ports.append(code)

    cargo_type = "other"
    for hint, t in _MOCK_TYPE_HINTS:
        if hint in text:
            cargo_type = t
            break

    price = None
    if m := _PRICE_RE.search(text):
        price = float(m.group(1))

    expect_date = None
    if m := _DATE_RE.search(text):
        expect_date = m.group(1)

    # 货名：取第一个逗号/句号前的短句兜底
    name = re.split(r"[，。,.\n]", text.strip())[0][:32] or "未识别货物"

    return {
        "cargo_name": name,
        "cargo_type": cargo_type,
        "weight_t": weight,
        "origin_port": ports[0] if len(ports) >= 1 else None,
        "dest_port": ports[1] if len(ports) >= 2 else None,
        "expect_date": expect_date,
        "offer_price": price,
    }
