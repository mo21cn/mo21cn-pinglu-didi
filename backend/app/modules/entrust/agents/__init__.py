"""R1 开放的 Agent 专业包（AG-01 委托助理 / AG-02 方案与采购）。

结构约定：每个专业一个模块，导出四样东西 ——

* `SYSTEM_PROMPT`：系统提示词（**禁止输出具体金额/日期由业务层渲染** 的约束写在里面）；
* `build_user_prompt(context, job_input)`：把只读事实渲染成提示词；
* `mock_content(context, job_input)`：**确定性 fixture**，返回与真实模式**同构**的
  信封 dict —— CI 在无 Key、无网络时跑的就是它（计划 §3.4「确定性 fixture 模式是
  CI 的硬要求」）；
* `source_kinds`：该专业允许引用的来源类别（用于构造来源目录）。

三个已登记但未开放的专业（AG-03 合同与单证 / AG-04 履约与交接 / AG-05 结算）
在 `envelope.KNOWN_SPECIALTIES` 里可见但 `open=False`，服务层拒绝建会话与提作业 ——
对应的业务在 R1 走**完整人工路径**。
"""

from app.modules.entrust.agents import ag01, ag02
from app.modules.entrust.agents.runner import (
    AgentRunOutcome,
    build_source_catalog,
    run_agent,
)

SPECIALTY_MODULES = {
    ag01.CODE: ag01,
    ag02.CODE: ag02,
}

__all__ = [
    "SPECIALTY_MODULES",
    "AgentRunOutcome",
    "ag01",
    "ag02",
    "build_source_catalog",
    "run_agent",
]
