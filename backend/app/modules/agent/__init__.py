"""agent 模块（F9）：LLM 网关 + Agent 审计 + 领域 Agent。

架构红线落点：
1. 确定性内核不经 LLM —— 本模块只做理解/解析/辅助，不触碰撮合/支付/订单状态机。
2. Agent 无直写 —— 解析产物是"草稿"，写操作由用户确认后走业务领域 API。
3. 交易全链路留痕 —— 每次调用落 AgentCall 审计行。
"""
from app.modules.agent.router import router

__all__ = ["router"]
