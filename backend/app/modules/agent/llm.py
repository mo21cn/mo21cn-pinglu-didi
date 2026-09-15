"""LLM 网关客户端（F9 Agent 底座）。

设计要点（对齐架构红线：确定性内核不经 LLM）：
- 统一入口 ``chat_json``：面向"结构化输出"场景，LLM 仅承担理解/解析/辅助，
  绝不进入撮合 / 支付 / 订单状态机等确定性内核。
- 供应商协议：OpenAI 兼容 ``/chat/completions``（DeepSeek 起步，可平移 Qwen 等）。
- ``LLM_MOCK=true``：规则模板模式，供 CI / 无 Key 开发；不发起任何网络请求。
- 错误分类（LLMError.kind）：timeout / network / auth / rate_limit / bad_request /
  **quota**（HTTP 402 余额或配额耗尽）/ bad_response（JSON 解析失败）/ unknown，
  调用方可据此降级（返回 503 提示稍后重试）。
  ⚠️ `quota` **不可重试**（重试不会让账户有钱）：它单独成一类，就是为了不被
  笼统归进 `bad_request` —— 那样运维看到的是"请求被拒绝"，而真正要做的动作是充值。
- **推理模型**：`content` 里可能夹着思维链（`<think>…</think>`），答案在其后，
  且 `response_format` 常被忽略、答案还可能包在 markdown 围栏里 ⇒ 解析前先剥推理块、
  再从文本里取第一个完整的顶层 JSON 对象，见 `_split_reasoning` / `_first_json_object`。
  ⚠️ 别用"整段 `json.loads`"去兼容它 —— H7a 首跑 24 次全因此记 `bad_response`。
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
    """一次 LLM 调用的结果。

    - ``content``：解析后的 dict（业务只认它）。
    - ``raw``：**参与解析的那段文本**（推理模型的内联思维链已被剥掉）。
      审计与 ``response_digest`` 用它 —— 否则摘要会退化成几千字的思维链，
      而"模型到底答了什么"反而读不到。
    - ``reasoning``：推理模型的思维链原文（非推理模型为空串）。**不参与任何判分**，
      留着只为满足"事实有来源"：否则我们主动要求模型思考，却把它的思考直接丢掉。
    - ``latency_ms`` / ``mocked``：语义与既有实现一致。
    """

    content: dict[str, Any]
    raw: str
    latency_ms: int
    mocked: bool
    reasoning: str = ""


# ---------------------------------------------------------------------------
# 推理模型的输出清洗（2026-09-16 实测）
# ---------------------------------------------------------------------------
# 背景：H7a 首跑 24 次调用**全部** `bad_response`，一条可评分结果都没产生。
# 另写探针把真实响应体照录后才看清：MiniMax-M2.7 是**推理模型**，它把思维链
# 直接内联进 `message.content`（`<think>…</think>`），答案跟在后面；
# 并且**无视** `response_format={"type":"json_object"}`。
# 于是 `json.loads(content)` 在 char 0 就抛 JSONDecodeError。
# 实测（同一请求，带/不带 response_format、带 max_tokens）四次全是这个形态，
# 剥掉推理块后每一次都能解析出合法对象。
#
# 供应商官方另给了一个对策：请求里带 `reasoning_split=true`，把推理挪进
# `reasoning_content`（实测有效）。本网关**不采用**：那是供应商专有参数，
# 而"content 里可能夹推理块"在 OpenAI 兼容生态里是普遍现象（DeepSeek-R1 系同形），
# 在客户端剥一次就能覆盖全部供应商，也让网关保持中立。
_THINK_CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)


def _split_reasoning(content: str) -> tuple[str, str]:
    """把 `content` 拆成 ``(答案文本, 推理文本)``。

    以**最后一个** ``</think>`` 切分：推理过程自身可能引用该字面量。
    没有 ``</think>`` 时视作"没有推理块"，答案即全文 —— 对非推理模型
    （content 本来就是纯 JSON）这是个**无副作用的空操作**。
    """
    closes = list(_THINK_CLOSE_RE.finditer(content))
    if not closes:
        return content, ""
    last = closes[-1]
    answer = content[last.end() :]
    head = content[: last.start()]
    # 去掉推理块的开标签（推不出开标签就原样保留）
    head = re.sub(r"^\s*<think\b[^>]*>", "", head, count=1, flags=re.IGNORECASE)
    return answer, head.strip()


def _first_json_object(text: str) -> str | None:
    """取第一个**完整**的顶层 ``{…}`` 片段（按括号深度配对，字符串内的括号不计）。

    为什么需要它：模型常把 JSON 包在 markdown 代码围栏里（`` ```json … ``` ``）。
    2026-09-16 实测：同一个 `SYSTEM_PROMPT` 下，S10 样本**有时**返回裸 JSON、
    **有时**返回 `</think>` + 围栏 —— 也就是说这不是"理论上的边界情况"。
    （当时先按"没观测到就不做"跳过它，重跑 H7a 立刻被 S10 打了回来。）
    取"第一个顶层对象"而不是"整段"：整段可能含多个对象而解析失败。
    """
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_json_answer(text: str) -> tuple[dict[str, Any] | None, str, str]:
    """把答案文本解析成 JSON 对象。

    Returns:
        ``(对象, 实际用于解析的文本, 失败原因)`` —— 解析成功时失败原因为空串。
        第二个返回值是"真正被 ``json.loads`` 吃下去的那段"，交给 ``LLMResult.raw``，
        这样审计看到的和判分用的是同一段文本。
    """
    candidates = [text]
    if (span := _first_json_object(text)) is not None and span != text:
        candidates.append(span)

    reason = "未找到 JSON 对象"
    for cand in candidates:
        try:
            value = json.loads(cand)
        except ValueError as exc:
            reason = str(exc)
            continue
        if isinstance(value, dict):
            return value, cand, ""
        reason = f"解析结果是 {type(value).__name__}，不是对象"
    return None, text, reason


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
    if resp.status_code == 402:
        # 2026-09-16 H7a 实测撞到：MiniMax 余额不足返回 402
        # `{"type":"insufficient_balance_error"}`。此前落进 bad_request ⇒
        # 报出来是"请求被拒绝"，掩盖了"该充值"这个**真正要做的动作**。
        # ⚠️ 但 402 **不等于**「没买订阅」：平台的**订阅额度**与**账户余额（wallet）**
        # 挂在**两套互不通用的凭证**上（MiniMax 官方："订阅 Key 与普通按量计费 API Key
        # 相互独立，不能混用"）。拿按量计费的 Key 去花订阅额度 ⇒ 报 402，
        # 而套餐里额度可能还剩满 —— 那时"充值"是错的动作，"换 Key"才是对的。
        # 故提示须同时指向两种可能；现场定位用 backend/scripts/llm_key_doctor.py。
        raise LLMError(
            "quota",
            "LLM 账户余额或可用资源不足（HTTP 402）：请核对所用 Key 与计费方式是否匹配"
            "（订阅额度须用订阅 Key），或为当前凭证充值",
        )
    if resp.status_code >= 500:
        raise LLMError("network", f"LLM 服务端错误 {resp.status_code}")
    if resp.status_code != 200:
        raise LLMError("bad_request", f"LLM 请求被拒绝 {resp.status_code}: {resp.text[:200]}")

    try:
        body = resp.json()
    except ValueError as exc:
        raise LLMError(
            "bad_response", f"LLM 响应不是 JSON（HTTP {resp.status_code}）：{resp.text[:200]}"
        ) from exc
    try:
        content_raw = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        head = json.dumps(body, ensure_ascii=False)[:200]
        raise LLMError("bad_response", f"LLM 响应缺少 choices[0].message.content：{head}") from exc
    if not isinstance(content_raw, str):
        raise LLMError("bad_response", f"LLM content 不是字符串：{type(content_raw).__name__}")

    answer, reasoning = _split_reasoning(content_raw)
    answer = answer.strip()
    content, raw, reason = _parse_json_answer(answer)
    if content is None:
        # ⚠️ 必须把 content 开头带出来：干巴巴一句"无法解析为 JSON"，让人只能
        #    另写探针去猜根因 —— H7a 首跑就是为此白花了几十分钟。
        raise LLMError(
            "bad_response",
            f"LLM 输出无法解析为 JSON 对象：{reason}；"
            f"剥离推理块后 {len(answer)} 字符，开头 {answer[:120]!r}",
        )

    return LLMResult(
        content=content,
        raw=raw,
        latency_ms=int((time.monotonic() - started) * 1000),
        mocked=False,
        reasoning=reasoning,
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
