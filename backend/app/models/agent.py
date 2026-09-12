"""智能体域实体（F9）：Agent 调用审计留痕表。

工程底线 3（交易全链路留痕）在 Agent 层的落点：
- 每次领域 Agent 的 LLM 调用落一行审计（谁 / 哪个 Agent / 输入摘要 /
  输出摘要 / 耗时 / 成败），出问题可回溯、可复核。
- Agent 无直写（工程底线 2）：Agent 仅产出结构化建议（如货源草稿），
  写操作一律由用户确认后走业务领域 API，审计表不含任何"已代写"语义。

digest 截断说明：只存摘要不存全文（隐私 + 表膨胀控制），
原始 prompt/response 的全量留档由后续观测平台（如 LangSmith）承接。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class AgentCall(Base):
    """领域 Agent 单次调用审计记录。"""

    __tablename__ = "agent_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="发起调用的用户 ID"
    )
    agent_name: Mapped[str] = mapped_column(
        String(64), index=True, comment="领域 Agent 名（cargo_parse / faq / ops_analytics ...）"
    )
    provider: Mapped[str] = mapped_column(String(32), default="deepseek", comment="LLM 供应商")
    model: Mapped[str] = mapped_column(String(64), default="", comment="模型标识")
    mocked: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否规则模板输出（LLM_MOCK）"
    )

    prompt_digest: Mapped[str] = mapped_column(Text, default="", comment="输入摘要（截断）")
    response_digest: Mapped[str] = mapped_column(Text, default="", comment="输出摘要（截断）")

    latency_ms: Mapped[int] = mapped_column(Integer, default=0, comment="端到端耗时（毫秒）")
    success: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否成功产出结构化结果")
    error_kind: Mapped[str | None] = mapped_column(
        String(32), default=None, comment="失败分类（timeout/network/auth/rate_limit/...）"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="调用时间"
    )
