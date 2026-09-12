"""意图路由（F20）：把用户的一句自然语言分派到对应领域 Agent。

设计要点
--------
- **确定性优先**：分类采用有序关键词规则，纯标准库、可复现、CI 可断言
  （与撮合/风险引擎同一取向）。LLM 分类是后续替换点——只需替换
  ``classify`` 的实现，上层 ``service.route_request`` 与前端契约不变。
- **可解释**：返回 ``matched`` 说明命中依据，便于排障与前端展示。
- **按角色裁剪**：货源解析仅货主可用（与 /agent/cargo-parse 的鉴权一致），
  角色不匹配时仍返回意图，由路由层决定是否派发并给出引导语。

优先级：合同 > 合规 > 货源解析 > 客服（长尾兜底）。
"""

from __future__ import annotations

from typing import Literal

Intent = Literal["cargo_parse", "compliance", "contract", "assistant"]

# 意图 → 下游目标（与 agent/router.py 的端点对齐）
TARGETS: dict[str, str] = {
    "cargo_parse": "POST /api/v1/agent/cargo-parse",
    "compliance": "POST /api/v1/agent/compliance/cargo",
    "contract": "POST /api/v1/agent/contract/generate",
    "assistant": "POST /api/v1/agent/assistant",
}

_CONTRACT_KW = ("合同", "条款", "风险点", "违约责任", "滞期")
_COMPLIANCE_KW = (
    "合规",
    "禁运",
    "违禁",
    "管制",
    "能不能运",
    "可不可以运",
    "能不能拉",
    "能否承运",
    "适装",
    "资质",
    "预检",
)
_CARGO_PARSE_KW = ("发货", "发布货源", "发一批", "运一批", "我有", "帮我发", "要发货")
_PORT_WORDS = (
    "南宁",
    "贵港",
    "梧州",
    "来宾",
    "柳州",
    "百色",
    "崇左",
    "桂林",
    "贺州",
    "玉林",
    "钦州",
    "防城港",
    "北海",
    "平塘",
)
_WEIGHT_HINTS = ("吨", "t", "T")


def _hit(text: str, keywords: tuple[str, ...]) -> str | None:
    """返回首个命中的关键词（保持词表顺序，保证结果确定）。"""
    for k in keywords:
        if k in text:
            return k
    return None


def classify(text: str, *, role: str, order_id: int | None = None) -> tuple[Intent, float, str]:
    """把一句话分类到某个领域 Agent。

    返回 ``(intent, confidence, matched)``；``matched`` 为可读的命中依据。
    """
    t = text.strip()

    # 1) 合同：显式提合同/条款/风险，或带订单上下文且提"合同"（强信号）
    kw = _hit(t, _CONTRACT_KW)
    if kw:
        return "contract", 0.9, f"关键词「{kw}」"
    if order_id is not None and ("风险" in t or "审核" in t):
        return "contract", 0.75, "指定订单 + 风险相关表述"

    # 2) 合规：合规/禁运/适装资质类提问
    kw = _hit(t, _COMPLIANCE_KW)
    if kw:
        return "compliance", 0.85, f"关键词「{kw}」"

    # 3) 货源解析：发货口吻（强信号，跨角色识别，便于给出「请切换货主」引导）
    kw = _hit(t, _CARGO_PARSE_KW)
    if kw:
        return "cargo_parse", 0.8, f"关键词「{kw}」"

    # 3b) 仅含吨位 + 港口地名（弱信号，仅在货主角色下判为解析意图）
    port = _hit(t, _PORT_WORDS)
    weight = _hit(t, _WEIGHT_HINTS)
    if port and weight:
        if role == "shipper":
            return "cargo_parse", 0.7, f"含「{port}」与吨位表述（货主角色）"
        return "assistant", 0.45, f"含「{port}」与吨位表述（非货主，转客服）"

    # 4) 兜底：客服导购（纯读问答，全角色可用）
    return "assistant", 0.5, "未命中专项意图，兜底客服问答"
