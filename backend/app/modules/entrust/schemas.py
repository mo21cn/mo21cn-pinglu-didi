"""委托支线 API 的请求/响应模型。

纪律（PRD 9）：422 字段校验错误由 Pydantic 承担；未知值保持 None；
数量用 Decimal 承载，避免浮点误差（开发规范：金额/数量定点精度）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssignmentCreate(BaseModel):
    """创建委托草稿。草稿允许不完整（§2.1 第 1 步）。"""

    title: str = Field(min_length=1, max_length=128)
    cargo_summary: str | None = Field(default=None, max_length=512)
    quantity: Decimal | None = Field(default=None, ge=0)
    quantity_unit: str | None = Field(default=None, max_length=24)


class AssignmentUpdate(BaseModel):
    """编辑草稿。`expected_revision` 必填（AC-11 乐观锁）。"""

    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=128)
    cargo_summary: str | None = Field(default=None, max_length=512)
    quantity: Decimal | None = Field(default=None, ge=0)
    quantity_unit: str | None = Field(default=None, max_length=24)


class AssignmentSubmit(BaseModel):
    """提交委托：必须选定服务经营主体。"""

    expected_revision: int = Field(ge=1)
    org_id: int = Field(ge=1)


class AssignmentOut(BaseModel):
    """委托单投影。

    R1 说明：货主与经理当前共用这一份字段集 —— 委托单受理层没有内部成本类
    字段，客户数据白名单投影的严格分叉在成果/财务层（artifacts / settlement）。
    """

    model_config = ConfigDict(from_attributes=True)

    assignment_id: int
    owner_user_id: int
    org_id: int | None
    title: str
    cargo_summary: str | None
    quantity: str | None
    quantity_unit: str | None
    status: str
    revision: int
    claimed_by: int | None
    claimed_at: str | None
    submitted_at: str | None
    cancelled_at: str | None
    created_at: str
    updated_at: str


class AssignmentListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[AssignmentOut]


def assignment_out(data: dict[str, Any]) -> AssignmentOut:
    """服务层 dict → 响应模型。"""
    return AssignmentOut.model_validate(data)


# ── 我的组织清单（ENT-012 组织选择器） ───────────────────────────────────────
# 只投影「调用者自己的组织身份」。`permissions` 用**字符串列表**而不是布尔开关：
# 界面要显示"我在这个组织能做什么"，直接把权限码交给前端，
# 由前端按权限码映射中文（`utils/entrust.js` 的 ORG_PERMISSION_LABELS），
# 这样新增权限时不必改接口 —— 但**前端的展示永远不是权限判定**。


class MyOrgOut(BaseModel):
    """一个组织身份：我在它是谁，以及我在它能做什么。"""

    org_id: int
    name: str
    member_role: str
    permissions: list[str]


class MyOrgListOut(BaseModel):
    total: int
    items: list[MyOrgOut]


# ── 任务（ENT-008） ──────────────────────────────────────────────────────────
# 固定前置条件与循环检查在服务层（数据库约束表达不了传递闭包），
# 因此这里只做字段级校验：类型/证据取值域的领域校验由服务层给出可读的 400。


class EvidenceRef(BaseModel):
    """一条证据：类别 + 来源（`ref` 是附件键 / 文件说明 / 外部凭据编号）。"""

    kind: str = Field(min_length=1, max_length=32)
    ref: str = Field(min_length=1, max_length=512)


class TaskCreate(BaseModel):
    """创建任务（已受理的委托下，需派单权限）。"""

    task_type: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=128)
    assignee_user_id: int | None = Field(default=None, ge=1)
    due_at: datetime | None = None
    required_evidence: list[str] | None = None
    precondition_task_id: int | None = Field(default=None, ge=1)


class TaskUpdate(BaseModel):
    """编辑任务；`expected_revision` 必填（AC-11 乐观锁）。"""

    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=128)
    assignee_user_id: int | None = Field(default=None, ge=1)
    due_at: datetime | None = None
    required_evidence: list[str] | None = None


class TaskPreconditionIn(BaseModel):
    """设置/清除固定前置条件（`null` 表示清除）。"""

    expected_revision: int = Field(ge=1)
    precondition_task_id: int | None = Field(default=None, ge=1)


class TaskStartIn(BaseModel):
    """开始任务：可选带上执行代次（旧代次会被 fence 拒绝）。"""

    expected_generation: int | None = Field(default=None, ge=0)


class TaskWaitIn(BaseModel):
    """标记缺件等待：缺件是业务状态，不是错误。"""

    reason: str = Field(min_length=1, max_length=255)
    expected_generation: int | None = Field(default=None, ge=0)


class TaskCompleteIn(BaseModel):
    """完成任务：所需证据未齐会被服务层拒绝。"""

    evidence_refs: list[EvidenceRef] | None = None
    expected_generation: int | None = Field(default=None, ge=0)


class TaskReopenIn(BaseModel):
    """重开已完成任务：必须给出原因（历史不删除）。"""

    reason: str = Field(min_length=1, max_length=255)


class TaskReassignIn(BaseModel):
    """改派任务：推进执行代次，旧执行者代次失效。"""

    assignee_user_id: int = Field(ge=1)


class TaskOut(BaseModel):
    """任务投影。"""

    model_config = ConfigDict(from_attributes=True)

    task_id: int
    assignment_id: int
    task_type: str
    title: str
    status: str
    assignee_user_id: int | None
    due_at: str | None
    precondition_task_id: int | None
    required_evidence: list[str] | None
    evidence_refs: list[dict[str, Any]] | None
    wait_reason: str | None
    reopen_count: int
    last_reopen_reason: str | None
    lease_generation: int
    revision: int
    created_by: int
    started_at: str | None
    completed_at: str | None
    cancelled_at: str | None
    created_at: str
    updated_at: str


class TaskListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[TaskOut]


def task_out(data: dict[str, Any]) -> TaskOut:
    """服务层 dict → 响应模型。"""
    return TaskOut.model_validate(data)


# ── 附件（ENT-009） ──────────────────────────────────────────────────────────
# 上传走 multipart（小程序 `wx.uploadFile` 原生就是 multipart），
# 其余字段用 Form 传；大小与类型校验在服务层（服务端实测，不信客户端声明）。


class AttachmentOut(BaseModel):
    """附件投影。

    `storage_key` 与 `sha256` **都不出现在这里**：前者是服务端内部键，
    后者是内容指纹 —— 对外只需要"多大、什么类型、什么时候传的、提取到哪一步"。
    """

    model_config = ConfigDict(from_attributes=True)

    attachment_id: int
    entrustment_id: int | None
    assignment_id: int | None
    owner_user_id: int
    org_id: int | None
    uploader_user_id: int
    filename: str
    content_type: str
    size_bytes: int
    extract_status: str
    extract_error: str | None
    extracted_chars: int | None
    source_event_at: str | None
    created_at: str
    updated_at: str


class AttachmentListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[AttachmentOut]


class AttachmentBindOut(BaseModel):
    """绑定结果：`created=False` 表示这次是幂等重放（此前已绑定）。"""

    artifact_id: int
    attachment_id: int
    created: bool


def attachment_out(data: dict[str, Any]) -> AttachmentOut:
    """服务层 dict → 响应模型。

    `_row_to_attachment` 会带上 `storage_key` / `sha256`；这里通过响应模型
    做**白名单投影**（只取声明过的字段），避免内部存储键顺带外泄。
    """
    return AttachmentOut.model_validate(data)


# ── 文档提取（S1 第 6 条 / ENT-013） ────────────────────────────────────────


class ExtractionResultOut(BaseModel):
    """一次提取的结论。

    `detail` 与 `notes` 都是**给人看的**：结论必须可解释 ——
    "为什么这文件没有被提取" 与 "提取出来的文字能不能直接引用" 都要能回答。
    `text_preview` 只返回开头一段，便于页面确认提取成功；完整文本走 `GET /text`。
    """

    attachment_id: int
    extract_status: str
    extract_error: str | None
    extracted_chars: int | None
    media_kind: str
    truncated: bool
    detail: str
    notes: list[str]
    text_preview: str | None


class AttachmentTextOut(BaseModel):
    """提取文本读取结果。

    没有文本时**不返回 404**：`extract_status` 本身就是有用信息
    （`needs_transcription` / `unsupported` / `failed` 各对应不同的下一步动作），
    返回 404 会把这个区别抹掉，前端只能显示"没有"。
    """

    attachment_id: int
    extract_status: str
    extract_error: str | None
    has_text: bool
    text: str | None
    char_count: int | None
    truncated: bool
    source: str | None
    transcribed_by: int | None
    updated_at: str | None


class TranscriptionIn(BaseModel):
    """人工转录提交（图片 / 扫描件 PDF / 老式 xls 的降级补录）。"""

    text: str = Field(min_length=1, max_length=200_000)


def extraction_result_out(data: dict[str, Any]) -> ExtractionResultOut:
    return ExtractionResultOut.model_validate(data)


def attachment_text_out(
    attachment: dict[str, Any], text_row: dict[str, Any] | None
) -> AttachmentTextOut:
    """附件状态 + 文本行合成一处（两者必须一起看才不矛盾）。"""
    return AttachmentTextOut(
        attachment_id=int(attachment["attachment_id"]),
        extract_status=str(attachment["extract_status"]),
        extract_error=attachment["extract_error"],
        has_text=text_row is not None,
        text=text_row["content"] if text_row is not None else None,
        char_count=text_row["char_count"] if text_row is not None else None,
        truncated=bool(text_row["truncated"]) if text_row is not None else False,
        source=text_row["source"] if text_row is not None else None,
        transcribed_by=text_row["created_by"] if text_row is not None else None,
        updated_at=text_row["updated_at"] if text_row is not None else None,
    )


# ── 会话与 Agent 作业（ENT-011） ─────────────────────────────────────────────
# 会话与作业是**经理侧**能力：货主看不到内部比价、也看不到 Agent 的建议动作，
# 所以这两组模型只在管理端点使用，不参与客户投影。


class SessionCreate(BaseModel):
    """创建会话：必须绑定委托授权或委托单之一（无上下文的会话无处取数）。"""

    entrustment_id: int | None = Field(default=None, ge=1)
    assignment_id: int | None = Field(default=None, ge=1)
    agent_specialty: str | None = Field(default=None, max_length=16)
    title: str = Field(min_length=1, max_length=128)


class SessionMessageIn(BaseModel):
    """追加一条操作者消息（角色固定 user，避免调用方伪造 agent 消息）。"""

    content: str = Field(min_length=1, max_length=8000)


class SessionOut(BaseModel):
    """会话投影。`agent_specialty` 为 None 表示通用会话壳（无专业能力）。"""

    model_config = ConfigDict(from_attributes=True)

    session_id: int
    entrustment_id: int | None
    assignment_id: int | None
    owner_user_id: int
    org_id: int | None
    created_by: int
    agent_specialty: str | None
    agent_specialty_label: str | None
    title: str
    status: str
    revision: int
    created_at: str
    updated_at: str


class SessionMessageOut(BaseModel):
    """消息投影。`source` 区分 manual / agent / deterministic —— UI 必须按它标注来源。"""

    model_config = ConfigDict(from_attributes=True)

    message_id: int
    session_id: int
    seq: int
    role: str
    content: str
    source: str
    job_id: int | None
    created_by: int
    created_at: str


class SessionDetailOut(BaseModel):
    session: SessionOut
    messages: list[SessionMessageOut]


class SessionListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[SessionOut]


def session_out(data: dict[str, Any]) -> SessionOut:
    return SessionOut.model_validate(data)


def message_out(data: dict[str, Any]) -> SessionMessageOut:
    return SessionMessageOut.model_validate(data)


class JobCreate(BaseModel):
    """提交 Agent 作业。

    `input` 是操作者当场提供的作业输入（如粘贴的报价文本、候选清单）。
    它与**附件**不同：附件是不可信数据，按 `source_refs` 核对；
    操作者输入是当面提交的可信输入。
    """

    task_id: int | None = Field(default=None, ge=1)
    artifact_id: int | None = Field(default=None, ge=1)
    base_revision: int | None = Field(default=None, ge=0)
    input: dict[str, Any] | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)


class JobAttemptOut(BaseModel):
    attempt_no: int
    status: str
    error_kind: str | None
    error_message: str | None
    latency_ms: int | None
    mocked: bool
    started_at: str
    finished_at: str


class AgentJobOut(BaseModel):
    """作业投影。

    `raw_output` **不出现在这里**（只在服务端尝试日志里，供审计）：
    原始模型输出未经过任何校验，把它返回给前端会诱导调用方直接使用它。
    """

    model_config = ConfigDict(from_attributes=True)

    job_id: int
    session_id: int | None
    entrustment_id: int | None
    assignment_id: int | None
    task_id: int | None
    artifact_id: int | None
    specialty: str
    status: str
    attempt_count: int
    max_attempts: int
    lease_expires_at: str | None
    base_revision: int | None
    input: dict[str, Any] | None
    envelope: dict[str, Any] | None
    error_kind: str | None
    error_message: str | None
    requires_review: bool
    created_by: int
    started_at: str | None
    finished_at: str | None
    cancelled_at: str | None
    created_at: str
    updated_at: str


class AgentJobDetailOut(BaseModel):
    job: AgentJobOut
    attempts: list[JobAttemptOut]


class AgentJobListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[AgentJobOut]


def job_out(data: dict[str, Any]) -> AgentJobOut:
    """服务层 dict → 响应模型（白名单投影：未声明字段不会外泄）。"""
    return AgentJobOut.model_validate(data)
