"""Agent 输出的**类型化信封**与校验 —— AC-08 的落点（ENT-011）。

问题
----
模型输出是不可信的：字段值类型可能错、可能缺必填、可能编造一个不存在的来源
（"据船东邮件第 3 页"），甚至可能自称"已确认"。如果把这些直接写成业务事实，
一次幻觉就变成一条无法追溯的账。

本模块给出三层防线，**顺序固定**：

1. **结构**（`AgentEnvelope`，pydantic）：不是对象、字段类型错、缺必填 →
   `EnvelopeInvalidError`，作业失败，**不产生任何业务变更**（AC-08 前半句）；
2. **内容**（`registry` 的字段契约）：未知成果类型 → 失败；缺必填字段 → 记进
   `missing_fields`；未声明字段 → 记进 `unknown_fields`。**缺项不等于失败** ——
   草稿本来就允许不完整，但必须如实记着；
3. **来源**（`evidence.SourceCatalog`）：信封引用的每个来源都必须能在
   "本次作业允许的证据范围"里找到。找不到的不是报错，而是被标进
   `unverified_sources` —— 因为**标记**已经足够阻止它成为事实：
   `envelope_json` 只是提案的载体，任何确认动作都要人工显式发起。

提案 ≠ 动作
-----------
`artifact_proposals` **绝不自动落库**。作业成功只意味着"有了一份通过校验的提案"，
把提案变成成果必须再走一次显式的写接口（`POST /entrustments/{id}/artifacts`）。
这是 AC-09 的实现方式 —— 不是靠约定，而是靠"作业层没有任何写成果的代码路径"。

`requires_review`
-----------------
模型返回 `false` 一律被**强制改回 true**并记录 `forced_review`。R1 的策略是
所有 Agent 产出都要人工复核；让模型自己声明"不需要复核"等于把闸门交给闸门的
被检查方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.entrust import registry as reg

# ── 专业槽位（R1 只开放 2 个，其余"后续开放"但不可用） ────────────────────────

SPECIALTY_AG01 = "agent_01"
SPECIALTY_AG02 = "agent_02"

#: 专业代码 → (展示名, 是否开放)。未开放的专业**不能建会话、不能提作业** ——
#: "能建但不可用"会让 UI 出现永远转圈的入口，比不给入口更糟。
KNOWN_SPECIALTIES: dict[str, tuple[str, bool]] = {
    SPECIALTY_AG01: ("委托助理", True),
    SPECIALTY_AG02: ("方案与采购", True),
    "agent_03": ("合同与单证", False),
    "agent_04": ("履约与交接", False),
    "agent_05": ("结算", False),
}

OPEN_SPECIALTIES: frozenset[str] = frozenset(
    code for code, (_, is_open) in KNOWN_SPECIALTIES.items() if is_open
)


class SpecialtyNotOpenError(ValueError):
    """专业未开放（或不存在）。HTTP 层应转 400/404。"""


def specialty_label(code: str) -> str:
    return KNOWN_SPECIALTIES.get(code, (code, False))[0]


def assert_specialty_open(code: str) -> str:
    """校验专业代码开放可用；未知或未开放一律拒绝。"""
    if code not in KNOWN_SPECIALTIES:
        raise SpecialtyNotOpenError(f"未知专业 {code!r}；已知专业：{sorted(KNOWN_SPECIALTIES)}")
    if code not in OPEN_SPECIALTIES:
        raise SpecialtyNotOpenError(
            f"专业 {code}（{specialty_label(code)}）本期未开放，R1 仅开放 "
            f"{sorted(OPEN_SPECIALTIES)}"
        )
    return code


def list_specialties() -> list[dict[str, Any]]:
    """列出全部专业槽位及开放状态（UI-02 据此把未开放项标注"后续开放"）。"""
    return [
        {"code": code, "label": label, "open": is_open}
        for code, (label, is_open) in KNOWN_SPECIALTIES.items()
    ]


# ── 信封结构 ────────────────────────────────────────────────────────────────


class SourceRef(BaseModel):
    """一条来源引用。`kind` 决定到哪个目录里去核对。"""

    model_config = ConfigDict(extra="ignore")

    kind: str = Field(min_length=1, max_length=32)
    ref: str = Field(min_length=1, max_length=512)


class Finding(BaseModel):
    """一条发现（缺项提示、风险点、下一步建议）。"""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=48)
    severity: str = Field(default="info", max_length=16)
    message: str = Field(min_length=1, max_length=2000)
    source_refs: list[SourceRef] = Field(default_factory=list)


class ArtifactProposal(BaseModel):
    """一份**提案**（不是成果）。`artifact_type` 必须在注册表取值域内。"""

    model_config = ConfigDict(extra="ignore")

    artifact_type: str = Field(min_length=1, max_length=48)
    payload: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=255)


class ProposedAction(BaseModel):
    """建议动作。R1 只做展示与转人工，**不自动执行**任何动作。"""

    model_config = ConfigDict(extra="ignore")

    action: str = Field(min_length=1, max_length=64)
    target_type: str | None = Field(default=None, max_length=32)
    target_id: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=500)


class AgentEnvelope(BaseModel):
    """最小信封（计划 §3.4）。字段与计划给出的 JSON 结构一一对应。"""

    model_config = ConfigDict(extra="ignore")

    assignment_id: int | None = Field(default=None, ge=1)
    task_id: int | None = Field(default=None, ge=1)
    base_revision: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=4000)
    artifact_proposals: list[ArtifactProposal] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    requires_review: bool = True


class EnvelopeValidationError(RuntimeError):
    """信封校验失败。作业应失败，**不得产生任何业务变更**。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class ValidationResult:
    """校验结果：信封 + 派生出的"哪里不干净"。"""

    envelope: AgentEnvelope
    #: 提案里缺的必填字段（`{artifact_type: [字段...]}`），派生不落库
    proposal_missing_fields: dict[str, list[str]] = field(default_factory=dict)
    #: 提案里未在注册表声明的字段
    proposal_unknown_fields: dict[str, list[str]] = field(default_factory=dict)
    #: 声明的来源里查不到的那些（编造来源）—— 标记，不阻断
    unverified_sources: list[dict[str, str]] = field(default_factory=list)
    #: 模型把 requires_review 返回成 false 的次数（已强制改回 true）
    forced_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_missing_fields": self.proposal_missing_fields,
            "proposal_unknown_fields": self.proposal_unknown_fields,
            "unverified_sources": self.unverified_sources,
            "forced_review": self.forced_review,
        }


def parse_envelope(raw: Any) -> AgentEnvelope:
    """第一层：结构校验。任何不符合契约的输入都在这里被拦下。"""
    if not isinstance(raw, dict):
        raise EnvelopeValidationError("schema", "Agent 输出必须是 JSON 对象")
    try:
        return AgentEnvelope.model_validate(raw)
    except ValidationError as exc:
        # 只保留前 3 条错误，错误信息可能很长；细节在尝试日志里有原始输出
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()[:3]
        )
        raise EnvelopeValidationError("schema", f"信封结构校验失败：{problems}") from exc


def validate_envelope(
    raw: Any,
    *,
    known_source_refs: frozenset[tuple[str, str]] | None = None,
) -> ValidationResult:
    """完整校验：结构 → 内容（注册表）→ 来源。

    Args:
        raw: 模型返回的 JSON 对象。
        known_source_refs: 本次作业**允许引用**的来源集合 `(kind, ref)`。
            给定 `None` 表示不做来源核对（调用方明确放弃该层时才行）。

    Raises:
        EnvelopeValidationError: 结构有问题，或提案使用了注册表外的成果类型。
    """
    envelope = parse_envelope(raw)

    result = ValidationResult(envelope=envelope)

    for proposal in envelope.artifact_proposals:
        try:
            reg.get_spec(proposal.artifact_type)
        except reg.UnknownArtifactTypeError as exc:
            # 未知成果类型属于**取值域问题**，与"多写一个字段"不同：类型决定
            # 字段契约、投影规则与证据类别，含糊的类型等于没有契约 → 直接失败。
            raise EnvelopeValidationError("unknown_artifact_type", str(exc)) from exc
        missing = reg.validate_payload(proposal.artifact_type, proposal.payload)
        if missing:
            result.proposal_missing_fields[proposal.artifact_type] = missing
        unknown = reg.unknown_fields(proposal.artifact_type, proposal.payload)
        if unknown:
            result.proposal_unknown_fields[proposal.artifact_type] = unknown

    if known_source_refs is not None:
        for ref in envelope.source_refs:
            if (ref.kind, ref.ref) not in known_source_refs:
                result.unverified_sources.append({"kind": ref.kind, "ref": ref.ref})

    if not envelope.requires_review:
        # 模型不能自己决定"这个不用复核"：R1 全部产出都要人工过一遍
        envelope.requires_review = True
        result.forced_review = True

    return result


def project_envelope_for_operator(
    validation: ValidationResult, *, customer_visible_only: bool = False
) -> dict[str, Any]:
    """把校验结果投影给**操作者**（经理）。

    `customer_visible_only=True` 时，只有客户可见类型的提案被保留，且 payload
    经注册表的白名单投影 —— 这是"客户数据白名单投影不能先返回再隐藏"在 Agent
    侧的落点：内部成本类提案在**服务端**就被剔除，不依赖前端折叠。
    """
    envelope = validation.envelope
    proposals: list[dict[str, Any]] = []
    for proposal in envelope.artifact_proposals:
        if customer_visible_only and proposal.artifact_type not in reg.CUSTOMER_VISIBLE_TYPES:
            continue
        payload = (
            reg.project_for_customer(proposal.artifact_type, proposal.payload)
            if customer_visible_only
            else proposal.payload
        )
        proposals.append(
            {
                "artifact_type": proposal.artifact_type,
                "artifact_label": reg.get_spec(proposal.artifact_type).label,
                "payload": payload,
                "note": proposal.note,
            }
        )
    return {
        "assignment_id": envelope.assignment_id,
        "task_id": envelope.task_id,
        "base_revision": envelope.base_revision,
        "summary": envelope.summary,
        "artifact_proposals": proposals,
        "missing_fields": envelope.missing_fields,
        "findings": [f.model_dump() for f in envelope.findings],
        "source_refs": [s.model_dump() for s in envelope.source_refs],
        "proposed_actions": [a.model_dump() for a in envelope.proposed_actions],
        "requires_review": envelope.requires_review,
        **validation.to_dict(),
    }


__all__ = [
    "KNOWN_SPECIALTIES",
    "OPEN_SPECIALTIES",
    "SPECIALTY_AG01",
    "SPECIALTY_AG02",
    "AgentEnvelope",
    "ArtifactProposal",
    "EnvelopeValidationError",
    "Finding",
    "ProposedAction",
    "SourceRef",
    "SpecialtyNotOpenError",
    "ValidationResult",
    "assert_specialty_open",
    "list_specialties",
    "parse_envelope",
    "project_envelope_for_operator",
    "specialty_label",
    "validate_envelope",
]
