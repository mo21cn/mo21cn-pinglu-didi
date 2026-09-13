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
