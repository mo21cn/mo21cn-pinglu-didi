"""Agent 作业运行器：构造来源目录 → 调用模型 → 校验信封 → 范围核对。

三道处理顺序刻意固定：

1. **来源目录**（`build_source_catalog`）先于模型调用 —— 因为"允许引用的来源"
   必须由**服务端**从数据库事实里枚举出来，而不是让模型自己声明它引用了什么；
2. **信封校验**（`envelope.validate_envelope`）在拿到输出之后立刻执行 ——
   校验失败就不该有任何后续步骤（AC-08）；
3. **范围核对**（`enforce_scope`）最后执行 —— 校验的是"这个专业有没有资格产出
   这类东西"，与"信封结构对不对"是两件事，分开报错才能给出可操作的信息。

真实模式与 fixture 模式
----------------------
`chat_json` 在 `LLM_MOCK=true` 或无 API Key 时走本地规则模板（fixture），
输出结构与真实模式**同构** —— 所以 CI 可以在没有 Key、没有网络的情况下验证
整条作业链路，而"真实模型质量未验证"这件事必须如实报告，不能靠 fixture 冒充。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.agent.llm import LLMError, chat_json
from app.modules.entrust.agents import ag01, ag02
from app.modules.entrust.envelope import (
    EnvelopeValidationError,
    ValidationResult,
    validate_envelope,
)
from app.modules.entrust.sessions import AgentScope

KIND_ASSIGNMENT = "assignment"
KIND_TASK = "task"
KIND_ARTIFACT = "artifact"
KIND_ATTACHMENT = "attachment"
KIND_ENTRUSTMENT = "entrustment"
KIND_ATTACHMENT_TEXT = "attachment_text"
#: 操作者当面提交的文本 —— 它是**输入**，不是模型声称的来源，所以永远可信
KIND_OPERATOR_INPUT = "operator_input"

SPECIALTY_MODULES: dict[str, Any] = {ag01.CODE: ag01, ag02.CODE: ag02}


@dataclass(slots=True)
class AgentRunOutcome:
    """一次模型调用的原始结果（尚未校验）。"""

    raw: dict[str, Any]
    raw_text: str
    latency_ms: int
    mocked: bool


def build_source_catalog(
    context: dict[str, Any], job_input: dict[str, Any]
) -> frozenset[tuple[str, str]]:
    """枚举本次作业**允许引用**的来源 `(kind, ref)`。

    全部来自服务端已读出的数据库事实：受理单、任务、成果版本、附件、委托授权。
    成果额外提供 `artifact#r<版本号>` 形式，便于引用"精确版本"而不是"最新"。
    """
    refs: set[tuple[str, str]] = set()

    assignment = context.get("assignment") or {}
    if assignment.get("assignment_id") is not None:
        refs.add((KIND_ASSIGNMENT, str(assignment["assignment_id"])))

    entrustment_id = context.get("entrustment_id")
    if entrustment_id is not None:
        refs.add((KIND_ENTRUSTMENT, str(entrustment_id)))

    for task in context.get("tasks") or []:
        refs.add((KIND_TASK, str(task["task_id"])))

    for artifact in context.get("artifacts") or []:
        refs.add((KIND_ARTIFACT, str(artifact["artifact_id"])))
        if artifact.get("revision_no") is not None:
            refs.add((KIND_ARTIFACT, f"{artifact['artifact_id']}#r{artifact['revision_no']}"))

    for attachment in context.get("attachments") or []:
        refs.add((KIND_ATTACHMENT, str(attachment["attachment_id"])))
        # 已提取出文本的附件才可被当作"文本来源"引用
        if attachment.get("extract_status") == "done":
            refs.add((KIND_ATTACHMENT_TEXT, str(attachment["attachment_id"])))

    if job_input.get("quote_text") or job_input.get("note"):
        refs.add((KIND_OPERATOR_INPUT, "job_input"))

    return frozenset(refs)


def enforce_scope(validation: ValidationResult, scope: AgentScope) -> dict[str, Any]:
    """核对执行范围。

    * 提案类型超出专业允许范围 → **硬失败**（`out_of_scope`）。理由与注册表的
      取值域一致：类型的"能不能产出"是能力边界，不是内容问题，含糊处理会让
      "委托助理悄悄生成了一份对客报价"这种事留下记录的余地；
    * 建议动作超出允许范围 → 只记录并丢弃（`dropped_actions`）。建议本身没有
      执行能力，丢弃即可，没必要让整个作业失败。

    Returns:
        含 `dropped_actions` 的报告字典。
    """
    for proposal in validation.envelope.artifact_proposals:
        if not scope.allows_artifact_type(proposal.artifact_type):
            raise EnvelopeValidationError(
                "out_of_scope",
                f"专业 {scope.specialty} 不允许产出成果类型 {proposal.artifact_type!r}",
            )

    dropped: list[str] = []
    kept = []
    for action in validation.envelope.proposed_actions:
        if scope.allows_action(action.action):
            kept.append(action)
        else:
            dropped.append(action.action)
    validation.envelope.proposed_actions = kept
    return {"dropped_actions": sorted(set(dropped))}


async def run_agent(
    *,
    scope: AgentScope,
    context: dict[str, Any],
    job_input: dict[str, Any],
) -> AgentRunOutcome:
    """调用模型（或 fixture）拿到原始输出。

    Raises:
        EnvelopeValidationError: 专业未注册（编程错误，不应发生）。
        LLMError: 模型侧失败（超时/鉴权/限流/无法解析），由作业层分类处理。
    """
    module = SPECIALTY_MODULES.get(scope.specialty)
    if module is None:
        raise EnvelopeValidationError("unknown_specialty", f"未注册的专业 {scope.specialty!r}")

    user_prompt = module.build_user_prompt(context, job_input)
    try:
        result = await chat_json(
            system=module.SYSTEM_PROMPT,
            user=user_prompt,
            mock_content=lambda _text: module.mock_content(context, job_input),
        )
    except LLMError:
        raise
    return AgentRunOutcome(
        raw=result.content,
        raw_text=result.raw,
        latency_ms=result.latency_ms,
        mocked=result.mocked,
    )


def validate_outcome(
    outcome: AgentRunOutcome, *, known_source_refs: frozenset[tuple[str, str]]
) -> ValidationResult:
    """校验原始输出（结构与内容 + 来源目录核对）。"""
    return validate_envelope(outcome.raw, known_source_refs=known_source_refs)


__all__ = [
    "KIND_ASSIGNMENT",
    "KIND_ARTIFACT",
    "KIND_ATTACHMENT",
    "KIND_ATTACHMENT_TEXT",
    "KIND_ENTRUSTMENT",
    "KIND_OPERATOR_INPUT",
    "KIND_TASK",
    "SPECIALTY_MODULES",
    "AgentRunOutcome",
    "build_source_catalog",
    "enforce_scope",
    "run_agent",
    "validate_outcome",
]
