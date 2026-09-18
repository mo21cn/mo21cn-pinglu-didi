"""委托支线 API 的请求/响应模型。

纪律（PRD 9）：422 字段校验错误由 Pydantic 承担；未知值保持 None；
数量用 Decimal 承载，避免浮点误差（开发规范：金额/数量定点精度）。
"""

from __future__ import annotations

from datetime import date, datetime
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
    #: 承接组织名（`ent_organization.name`，S1 工作项 5「客户侧看到真实状态与承接组织」）。
    #:
    #: ⚠️ 可能为 `None`，且这是**正确**的取值：草稿还没选组织（`org_id` 为空）、
    #:    或组织记录已不存在时就是 None。界面据此显示「尚未委托组织」——
    #:    未知就报未知，不编一个占位组织名（那会让货主以为已经有人接手了）。
    #:    取值来自 `_ASSIGNMENT_FROM` 的 LEFT JOIN，因此**不是**可选输出：
    #:    字段始终存在，只是值可能为空。
    org_name: str | None = None
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


# ── 我的委托授权清单（S1 / DEMO-1 §3.1） ─────────────────────────────────────
# 与 `/my-orgs` 是**两张表**：那边是 `ent_org_member`（我所在的组织），
# 这边是 `ent_entrustment`（我授权出去的组织）。DR-0012：归属 ≠ 权限边界。
# 字段刻意与 `ent_entrustment` 行一一对应，便于"事实有来源"回溯。


class MyEntrustmentOut(BaseModel):
    """一条「我授权出去」的生效委托授权。

    ⚠️ **只投影授权本身**：不含该组织的成员、任务、成果、案件等任何内部数据。
    """

    entrustment_id: int
    org_id: int
    org_name: str
    #: 授权动作代码（白名单投影后的结果，未知代码已在服务层丢弃）
    permissions: list[str]
    status: str
    #: 授权创建时间；无法解析时为空串（**不编造**）
    granted_at: str


class MyEntrustmentListOut(BaseModel):
    total: int
    items: list[MyEntrustmentOut]


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
    #: 当前文本的来源（`extractor` / `manual_transcription`；没有文本时为 None）。
    #: 界面据此区分"机读提取 / 人工转录"，并在重抽前提示会不会覆盖掉人写的内容。
    text_source: str | None = None
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
    #: 覆盖前那份文本的来源（`extractor` / `manual_transcription`；没有文本时为 None）。
    #: 作用：重抽会**覆盖**同一行、不留历史 ⇒ 至少要让"这次覆盖掉的是人工转录还是
    #: 机器提取"在响应与幂等记录里可查（HO 0917-3 裁定四）。
    previous_text_source: str | None = None


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


class SessionContextOut(BaseModel):
    """「为这张委托单开会话」所需的上下文：**这是一次查找，不是一次判定**。

    ⚠️ 这里**没有** `can_create` 之类的字段，是刻意的：能不能建由
    `POST /entrustments/{eid}/sessions` 里的 `assert_can_write_entrustment` 唯一决定。
    本模型再给一个布尔值，就等于在权限上制造第二个真相 ——
    两侧一旦分叉，界面会给出一个"看起来可以、点下去 403"的按钮。

    `entrustment_id` 为 `None` 时**不是**"不许"，而是"定位不到唯一一条"，
    原因写在 `note` 里（未指定组织 / 无生效授权 / 多条匹配需显式指定）。
    """

    assignment_id: int
    org_id: int | None
    entrustment_id: int | None = None
    note: str = ""


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
    #: 最近一次尝试是否是**规则模板（fixture）**输出。
    #: 三态是刻意的 —— `None` 表示"还没有任何尝试记录、无从判断"，
    #: 与 `False`（"跑过，且不是桩"）是两句不同的话（合同 §3.2 要求区分
    #: deterministic fixture 与 live invocation）。界面的 fixture 提示条据此显示。
    mocked: bool | None = None
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


# ── 成果归属（DR-0012）────────────────────────────────────────────────────────
# 两个入口都用这套契约：单委托成果清单（`artifacts_api`）与作业提案采纳
# （`agent_api`）。归属是**服务端派生**的，不由客户端声明 —— 采纳时归属取自作业行
# 自己记录的 `assignment_id`，因此请求体里没有这个字段。


class AssignmentArtifactItem(BaseModel):
    """单委托成果清单里的一项。

    `current_revision_id` 与 `current_revision_no` 成对返回：它们就是
    「对话与工作台引用同一成果 ID 与版本」里的那一对值（PRD 第 187 行 / AC-05），
    两端引用同一对精确值，而不是各自再查一次"最新"。
    """

    model_config = ConfigDict(from_attributes=True)

    artifact_id: int
    entrustment_id: int
    assignment_id: int | None
    artifact_type: str
    status: str
    current_revision_id: int | None
    current_revision_no: int | None
    created_at: str
    updated_at: str
    #: 待复核标记（A2 五之二派生、五之四处投影）。`None` = 没有待复核项。
    #: ⚠️ 必须在此**显式声明**：pydantic 默认丢弃未声明字段，漏了它会让徽标的
    #: 数据在响应层被静默剥掉 —— 界面拿到的永远是"没有待复核"，且不报错。
    needs_revalidation: dict[str, Any] | None = None


class AssignmentArtifactListOut(BaseModel):
    """单委托成果清单。

    `unassigned_total` = 同一 (货主, 组织) 授权范围内**归属为空**的成果数。
    它不是"属于本委托的成果"，所以既不计入 `total`、也不进 `items` ——
    但必须出现在响应里，否则"历史成果去哪了"这个问题在界面上无法回答。
    """

    assignment_id: int
    total: int
    page: int
    size: int
    unassigned_total: int
    items: list[AssignmentArtifactItem]


class ArtifactAdoptIn(BaseModel):
    """采纳一份 Agent 提案为成果。

    `payload` 是**人工确认过的内容**（R1 要求所有 Agent 产出都过人一遍），
    服务端只校验"该作业确实提出过这个类型"（见 `agent_api.adopt_job_proposal`）——
    它不假装能判断内容是否被改过：提案与采纳是两个动作，采纳者有权修正。
    """

    artifact_type: str = Field(min_length=1, max_length=48)
    payload: dict[str, Any]
    note: str | None = Field(default=None, max_length=255)


# ── 委托工作台（UI-05 / ENT-021）─────────────────────────────────────────────
# 七槽位是**投影的呈现单位**：一条只读聚合查询返回每槽四字段 + 计数，详情按需
# 加载（DR-0010 §3.2）。四个派生字段**各自带 `state`**，因为「暂无记录 / 尚未分配 /
# 不适用 / 信息缺失」是四句不同的话（DR-0010 §3.6）—— 用一个 null 表示，
# 界面就只能被迫说"待补充"，用户分不清该不该动。


class WorkbenchArtifactRef(BaseModel):
    """槽位里引用的一个成果：**精确版本**（PRD 第 187 行要求两端同 ID 同版本）。"""

    artifact_id: int
    artifact_type: str
    label: str
    revision_no: int | None


class CaseRef(BaseModel):
    """UI-05 的 `exceptions` 槽里引用的一宗案件（DR-0014 §3.3）。

    **与 `WorkbenchArtifactRef` 平行但独立** —— 这不是「风格统一」问题：

    | | `WorkbenchArtifactRef` | `CaseRef` |
    | --- | --- | --- |
    | 键 | artifact_id / artifact_type / label / revision_no | case_id / kind / title / status / blocking |
    | 「版本」 | **精确版本**，两端引用同一对 (artifact_id, revision_no) | **无** —— 案件的 revision_no 是乐观锁版本号，不是「看哪一版」 |
    | 消费者 | 成果页（`artifact_id` 进深链） | 案件页（`case_id` 进深链） |

    字段名不同是**刻意的防线**：一旦共用键名，前端就会用一个渲染分支吃两种数据，
    而两边「有版本 / 无版本」的语义差异会在某次改动里静默错位。
    前端按**槽位 `key`** 选渲染分支，不靠猜字段。
    """

    case_id: int
    kind: str
    title: str
    status: str
    blocking: bool


class WorkbenchCurrentOut(BaseModel):
    """「当前成果」：有效业务版本；多个成果时给摘要与数量。

    `refs` 按**槽位**承载不同形状，且**不混装**：普通槽位是 `WorkbenchArtifactRef`，
    `exceptions` 槽是 `CaseRef`。用联合而不是另开一个字段，是为了让「这一槽位里
    可点的引用」对前端保持**一个**入口 —— 渲染分支按槽位 `key` 选。
    """

    state: str
    text: str
    refs: list[WorkbenchArtifactRef | CaseRef] = Field(default_factory=list)


class WorkbenchIssueOut(BaseModel):
    """一条未决问题。`kind` 是**类别**，界面据此选文案，不用字符串前缀猜。"""

    kind: str
    text: str


class WorkbenchIssuesOut(BaseModel):
    """「未决问题」：缺项、待确认、失效成果、阻断条件、未关闭异常。"""

    state: str
    count: int
    items: list[WorkbenchIssueOut] = Field(default_factory=list)


class WorkbenchOwnerOut(BaseModel):
    """「下一责任方」。

    `state=unassigned` 是"有活但还没派人"，`state=not_applicable` 是"该槽没有
    待推进的任务" —— 两者的下一步动作完全不同，不能合成一句话。
    """

    state: str
    user_id: int | None
    text: str


class WorkbenchCountsOut(BaseModel):
    artifacts: int
    tasks: int
    open_tasks: int
    unassigned_tasks: int


class WorkbenchSlotOut(BaseModel):
    """一个槽位。

    `available=False` 表示该槽位的能力**本期未开放**（当前只有 `exceptions`），
    此时 `unavailable_reason` 非空，界面显示"本期未开放"而**不是**"暂无记录"——
    后者会把"能力还没做"说成"这单没有异常"（DR-0010 §3.8）。

    `exceptions` 的真实投影已实现（DR-0013 A1 切片三之三），但标记按
    DR-0013 §7.3 仍需保持 —— 撤下条件第 4 条（人工落点在真载荷走查中走通）未满足。
    见 `workbench.EXCEPTIONS_SLOT_OPEN`。
    """

    key: str
    title: str
    available: bool
    unavailable_reason: str
    current: WorkbenchCurrentOut
    issues: WorkbenchIssuesOut
    next_owner: WorkbenchOwnerOut
    updated_at: str | None
    counts: WorkbenchCountsOut


class WorkbenchOut(BaseModel):
    """单委托工作台聚合投影（UI-05 的取数入口）。

    `unassigned_artifact_total` 与 `AssignmentArtifactListOut.unassigned_total`
    同一口径：归属机制上线前的历史成果不在任何槽位里，但必须如实报数，
    否则界面会让人以为"本单只有这些成果"。
    """

    assignment_id: int
    org_id: int | None
    status: str
    slots: list[WorkbenchSlotOut]
    unassigned_artifact_total: int


# ── 异常与变更案件（ENT-030 / DR-0013）────────────────────────────────────────
# 投影形状直接对应服务层的 `project_case_internal`：**不在这里再挑一遍字段**。
# 若响应模型自己挑字段，就会出现「服务层投影漏了一个字段、但没人发现」——
# 因为响应模型把它丢了，前端只会觉得"这个字段还没做"。
# 对客投影（`project_case_for_customer`）刻意**没有**对应的响应模型：
# 它属 UI-07（客户页，未开工），现在造一个模型就等于宣称它可用。


class ExceptionCaseLinkIn(BaseModel):
    """一条受影响项（任务或成果）。归属校验在服务层（跨委托 → 403）。

    用于**随案件一起登记**的场景（`ExceptionCaseCreate.links`），因此**不带**
    `expected_revision` —— 那时案件还没有版本号可言。
    """

    target_kind: str = Field(min_length=1, max_length=16)
    target_id: int = Field(ge=1)


class ExceptionCaseLinkAddIn(BaseModel):
    """给**已有**案件补登记一条受影响项。

    `expected_revision` 是**案件**的乐观锁：登记受影响项同样推进案件版本，
    否则两个客户端可以各自往同一案件里塞 link 而互不察觉。
    """

    expected_revision: int = Field(ge=1)
    target_kind: str = Field(min_length=1, max_length=16)
    target_id: int = Field(ge=1)


class ExceptionCaseCreate(BaseModel):
    """登记案件。

    `links` 允许**随案件一次登记**：`impact_kind='execution-blocking'` 时 C2 要求
    至少一条受影响项，若必须先建案件再补 link，那个中间态本身就违反 C2。

    `org_id` 是**可选的冗余声明**：带上就必须与委托所属组织一致（否则 403），
    不带上则完全由服务端派生 —— 二者都不会让客户端**决定**作用域。
    """

    kind: str = Field(min_length=1, max_length=16)
    title: str = Field(min_length=1, max_length=200)
    severity: str = Field(min_length=1, max_length=16)
    impact_kind: str = Field(min_length=1, max_length=24)
    cause: str | None = None
    owner_user_id: int | None = Field(default=None, ge=1)
    source: str = Field(default="manual", min_length=1, max_length=24)
    due_at: datetime | None = None
    proposed_action: str | None = None
    links: list[ExceptionCaseLinkIn] | None = None
    #: 变更类别（A2 五之二 / DR-0016 五行）。**可空**：登记时可能还没定，
    #: 但**应用变更时必须已登记** —— 没有类别就无从确定复核范围，
    #: 而"先应用、后补范围"会让下游照着失效事实干活（见 `apply_case`）。
    #: 异常案件（`kind='exception'`）不允许带此字段。
    change_category: str | None = Field(default=None, min_length=1, max_length=32)
    org_id: int | None = Field(default=None, ge=1)


class ExceptionCaseDecisionIn(BaseModel):
    """记录决定。

    **没有** `severity` / `impact_kind` 字段 —— 这是 C3 在接口层的形态：
    决定路径不存在「顺手改一个展示用字段」的入口。
    """

    expected_revision: int = Field(ge=1)
    to_status: str = Field(min_length=1, max_length=16)
    decision_note: str | None = None
    basis_revision_id: int | None = Field(default=None, ge=1)
    #: **经过批准的结构化修改内容**，按 `"{target_kind}#{target_id}"` 索引。
    #: 只在 `to_status=approved` 时进入批准快照；apply 阶段**只认这份内容**，
    #: 不接受临时替换（A2 五之一前提 P4-A2/A4）。
    approved_changes: dict[str, dict[str, Any]] | None = None
    #: 补登变更类别（A2 五之二）。批准是**最后一个合理时机**：应用要按它定复核范围。
    #: 只有变更请求可带；批准后改类别会被 `apply_case` 拒绝（会生成无人批准过的复核清单）。
    change_category: str | None = Field(default=None, min_length=1, max_length=32)


class ExceptionCaseApplyIn(BaseModel):
    """应用已批准的变更（A2 五之一）。

    **没有**「改成什么」这类字段 —— 修改内容只来自批准快照，请求方不能临时替换。
    带 `expected_revision` 是乐观锁：应用会推进案件版本，过期快照不会被静默接受。
    """

    expected_revision: int = Field(ge=1)


class ExceptionCaseCloseIn(BaseModel):
    """关闭案件。`closure_disposition` 与 `evidence_ref` 都是必填 —— 没有一键关闭。"""

    expected_revision: int = Field(ge=1)
    closure_disposition: str = Field(min_length=1, max_length=32)
    evidence_ref: str = Field(min_length=1, max_length=512)
    decision_note: str | None = None
    resolution_note: str | None = None


class ExceptionCaseReopenIn(BaseModel):
    """重开已关闭案件：原因必填，落审计事件的 `note`。"""

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=255)


class ExceptionCaseLinkOut(BaseModel):
    link_id: int
    target_kind: str
    target_id: int
    applied_revision_id: int | None


class ExceptionCaseDecisionOut(BaseModel):
    note: str | None
    by: int | None
    at: str | None
    basis_revision_id: int | None


class ExceptionCaseResolutionOut(BaseModel):
    note: str | None


class ExceptionCaseClosureOut(BaseModel):
    disposition: str | None
    by: int | None
    at: str | None


class ExceptionCaseEventOut(BaseModel):
    """一条案件事件（append-only）。`payload` 是附加结构化信息。"""

    seq: int
    event_kind: str
    from_status: str | None
    to_status: str | None
    actor_user_id: int
    note: str | None
    evidence_ref: str | None
    basis_revision_id: int | None
    payload: dict[str, Any] | None
    created_at: str


class ExceptionCaseOut(BaseModel):
    """案件投影（**内部**；`project_case_internal` 的响应形态）。"""

    case_id: int
    assignment_id: int
    org_id: int
    kind: str
    title: str
    cause: str | None
    severity: str
    impact_kind: str
    status: str
    #: 变更类别（A2 五之二 / DR-0016）。`None` 表示未登记 ——
    #: 未登记时**不能应用**变更（无从确定复核范围）。异常案件恒为 `None`。
    change_category: str | None = None
    owner_user_id: int | None
    raised_by_user_id: int
    source: str
    raised_at: str
    due_at: str | None
    proposed_action: str | None
    decision: ExceptionCaseDecisionOut
    resolution: ExceptionCaseResolutionOut
    closure: ExceptionCaseClosureOut
    blocking: bool
    affected: list[ExceptionCaseLinkOut] = Field(default_factory=list)
    revision_no: int
    created_at: str
    updated_at: str


class ExceptionCaseListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[ExceptionCaseOut]


class ExceptionCaseListItem(BaseModel):
    """组织级清单的一行（UI-04 的「异常/变更」队列，DR-0014 §3.2）。

    UI-04 是**跨委托**的组合工作台，因此这里要带 `assignment_id`（界面得说清
    「是哪张单的」）；但**不带** `org_id` —— 视图本身已限定单个组织，回传没有信息量。

    **刻意不含 `severity`**：C3 的用意是堵住「按严重度决定流程」的联想，清单里给了它，
    界面迟早会拿它排序或加重；而清单已经有表达轻重的正确字段 —— `blocking`（流程约束）
    与 `impact_kind`（影响类型）。严重度属详情页的业务判断，留在 UI-08。
    """

    case_id: int
    assignment_id: int
    kind: str
    title: str
    status: str
    impact_kind: str
    blocking: bool
    due_at: str | None = None
    updated_at: str
    affected_count: int


class ExceptionCaseOrgListOut(BaseModel):
    """`GET /exceptions?view=org` 的响应（DR-0014 §3.1）。

    与单委托视图**共用分页口径**（`ORDER BY id DESC`、`size` 上限 100），
    但行形状不同：单委托视图给完整投影（含 `cause` / 决定 / 处置 / 受影响项明细），
    组织视图只给清单所需的最小集合 —— 组合工作台没有"同时展开 N 宗案件详情"的用途，
    而每宗案件详情都带事件链。
    """

    total: int
    page: int
    size: int
    org_id: int
    items: list[ExceptionCaseListItem]


class ExceptionCaseCapabilities(BaseModel):
    """UI-08 的按钮可用性（DR-0014 §3.4）。

    **挂在详情而不挂共享模型上**：它描述的是**调用者**，不是案件数据。
    挂进 `ExceptionCaseOut` 会污染白名单投影，并让清单的每一行都带一份
    「我能做什么」；写端点也仍返回纯数据的 `ExceptionCaseOut` ——
    界面写完重新拉详情刷新能力。

    字段的判定一律来自服务层 `case_capabilities`（与写命令同源），
    这里**不重算、不给默认值**：任何本地推算都是第二份实现。
    """

    can_add_link: bool
    can_remove_link: bool
    can_decide: bool
    can_close: bool
    can_reopen: bool
    can_apply_change: bool


class ExceptionCaseRevalidationOut(BaseModel):
    """一条复核项（A2 五之二生成、五之四展示）。

    带 `task_title` / `task_status` 是为了让"这批复核做完了没有"能一眼回答；
    ⚠️ 两者**可空**（任务行缺席时必须如实为空，不能让这一项从列表里消失）。
    """

    review_key: str
    area: str
    task_type: str
    target_kind: str | None = None
    target_id: int | None = None
    target_revision_id: int | None = None
    review_task_id: int
    #: open=待复核 / resolved=已复核 / cancelled=复核要求被撤销（取消任务时联动）
    status: str
    note: str | None = None
    created_at: str
    resolved_at: str | None = None
    resolved_by: int | None = None
    task_title: str | None = None
    task_status: str | None = None


class ExceptionCaseApprovalOut(BaseModel):
    """批准快照的界面摘要（A2 五之一前提 P4）。

    只给"将动哪些目标、各自改哪些字段"，**不给字段值** —— 值的形状随成果类型而变，
    要看精确值应当去成果页的版本历史。界面据此在点「应用变更」**之前**把要发生的事
    写清楚（apply 只认快照，不给人改写的机会，所以更不能盲点）。
    """

    snapshot_version: int
    change_category: str | None = None
    case_basis_revision_id: int | None = None
    targets: list[dict[str, Any]] = Field(default_factory=list)


class ExceptionCaseDetailOut(BaseModel):
    """案件详情：投影 + 完整事件链（重开再关闭的两轮历史在此可判定）。

    `capabilities` **必填**（不给默认值）：它缺席时界面只能靠猜按钮可用性，
    而「猜」正是 DR-0014 §3.4 要消除的东西。

    `revalidation` / `unconfirmed` / `approval` 都是 **A2 五之四**新增的界面输入：
    前两个回答"这批复核任务凭什么生成的、还差什么要人工确认"，后一个回答
    "点『应用变更』会发生什么"。三者缺席都不会让详情不可用，故给默认值。
    """

    case: ExceptionCaseOut
    capabilities: ExceptionCaseCapabilities
    events: list[ExceptionCaseEventOut] = Field(default_factory=list)
    revalidation: list[ExceptionCaseRevalidationOut] = Field(default_factory=list)
    #: DR-0016 §4.1：映射点名、委托里**确实存在**、却没被登记为受影响项的成果类型
    #: ⇒ 范围可能不足，**交经理人确认**（不是让代码自动扩大范围）。
    unconfirmed_types: list[str] = Field(default_factory=list)
    approval: ExceptionCaseApprovalOut | None = None


def exception_case_out(data: dict[str, Any]) -> ExceptionCaseOut:
    """服务层投影 dict → 响应模型。"""
    return ExceptionCaseOut.model_validate(data)


def exception_case_list_item(data: dict[str, Any]) -> ExceptionCaseListItem:
    """服务层「清单行」投影 dict → 响应模型。"""
    return ExceptionCaseListItem.model_validate(data)


def exception_case_capabilities(data: dict[str, Any]) -> ExceptionCaseCapabilities:
    """服务层能力投影 dict → 响应模型。"""
    return ExceptionCaseCapabilities.model_validate(data)


def exception_event_out(data: dict[str, Any]) -> ExceptionCaseEventOut:
    return ExceptionCaseEventOut.model_validate(data)


# ── S3 对客发布与客户响应（BP-03 第 4/5/6/7/10 条；D1-07 / D1-11）────────────


class OfferReleaseCreate(BaseModel):
    """发布**指定的那一个**成果版本。

    刻意**没有**"不传版本就发最新"的默认分支：D1-07 要证明客户接受的是"那一个版本"，
    而"取最新"在并发编辑下会让证据退化成"反正是某一个版本"。
    """

    artifact_id: int = Field(ge=1)
    revision_no: int = Field(ge=1)
    #: 本发布**明确授权**客户下载的附件（冻结在发布记录上）。
    #: 客户下载只限这些 —— 不能因为同属一条委托授权就把内部附件全开。
    authorized_attachment_ids: list[int] | None = Field(default=None)
    note: str | None = Field(default=None, max_length=255)


class OfferWithdrawIn(BaseModel):
    """撤回：理由必填（留痕，不靠猜）。"""

    reason: str = Field(min_length=1, max_length=255)


class OfferResponseCreate(BaseModel):
    """客户响应（只有该委托的货主本人能发）。"""

    decision: str = Field(min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=500)


class SourceCheckCreate(BaseModel):
    """人工核验留痕：**必须**给出核验依据。

    `state` 只接受 `verified` / `rejected`：`declared`（待核验项）由服务端在采纳提案时
    按作业信封写入，不允许人手工造"声明"（那等于自己给自己发一张待核验清单）。
    """

    revision_no: int = Field(ge=1)
    source_kind: str = Field(min_length=1, max_length=32)
    source_ref: str = Field(min_length=1, max_length=160)
    state: str = Field(min_length=1, max_length=16)
    method: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=255)


class SourceCheckOut(BaseModel):
    check_id: int
    artifact_id: int
    revision_no: int
    source_kind: str
    source_ref: str
    state: str
    method: str | None = None
    checked_by: int | None = None
    checked_at: str | None = None
    note: str | None = None


class SourceGateOut(BaseModel):
    """发布前来源门槛状态（`ok=False` 时 `pending` / `rejected` / `missing_declaration` 至少一个成立）。"""

    artifact_id: int
    revision_no: int
    declared: list[dict[str, str]] = Field(default_factory=list)
    verified: list[dict[str, str]] = Field(default_factory=list)
    pending: list[dict[str, str]] = Field(default_factory=list)
    rejected: list[dict[str, str]] = Field(default_factory=list)
    #: 模型产出却**没有任何来源声明记录** ⇒ 无从核对、不得发布（见 `offers.source_gate`）。
    missing_declaration: bool = False
    ok: bool


class OfferResponseOut(BaseModel):
    response_id: int
    release_id: int
    assignment_id: int
    artifact_id: int
    responded_revision_id: int
    decision: str
    customer_user_id: int
    note: str | None = None
    responded_at: str


class OfferReleaseOut(BaseModel):
    """**经理视角**：含发布控制信息、客户当初看到的快照、来源门槛状态与响应。"""

    release_id: int
    assignment_id: int
    entrustment_id: int | None = None
    artifact_id: int
    #: 被发布的成果类型（`customer_quote` 等）。**顶层显式声明**，不让客户端去
    #: `customer_snapshot` 里挖 —— 快照是"客户当初看到的内容"，不是元数据。
    #: ⚠️ 它与客户视角的同名字段（`OfferReleaseCustomerOut.artifact_type`）语义相同：
    #: "这条发布的是什么"。两处都声明，是为了让"派生前置要求接受的是对客报价"
    #: 这条判据能在**有 schema 保证**的字段上成立。
    artifact_type: str | None = None
    revision_id: int
    revision_no: int
    customer_user_id: int
    status: str
    released_by: int
    released_at: str
    closed_by: int | None = None
    closed_at: str | None = None
    close_reason: str | None = None
    customer_snapshot: dict[str, Any] = Field(default_factory=dict)
    authorized_attachment_ids: list[int] = Field(default_factory=list)
    #: 数据来源标注的**完整依据**（mode + basis + 作业行 id）。经理有权看内部数据，
    #: 而"凭什么说这是 live"必须有据可查 —— 只给一个词就不是证据。
    data_origin: dict[str, Any] | None = None
    source_gate: SourceGateOut | None = None
    response: OfferResponseOut | None = None


class OfferReleaseCreatedOut(OfferReleaseOut):
    """发布成功时额外回"被取代的旧发布"，让调用方不必自己再查一遍。"""

    superseded_release_ids: list[int] = Field(default_factory=list)


class OfferReleaseCustomerOut(BaseModel):
    """**客户视角**：只回发布时冻结的那份投影。

    刻意不含 `released_by` / `closed_by` / `artifact_id` 与来源台账 —— 客户不该看到
    内部是谁发布的、有哪些内部来源待核验。

    来源标注**给客户**（BP-03 第 9 条要客户看到"这份内容来自哪类产出"），
    但只给 `mode`，不给 `basis`（依据里有作业 id 这类内部编号）。
    """

    release_id: int
    assignment_id: int
    revision_no: int
    status: str
    released_at: str
    content: dict[str, Any] = Field(default_factory=dict)
    artifact_type: str | None = None
    content_source: str | None = None
    data_origin_mode: str | None = None
    #: 签署证据模式（`labeled_sample`）：声明这是演示/样张语境下的确认，
    #: 不是已生效的法律签署 —— 不标注就会被下游当成真签署。
    signature_mode: str | None = None
    authorized_attachment_ids: list[int] = Field(default_factory=list)
    can_respond: bool
    response: OfferResponseOut | None = None


class OfferReleaseListOut(BaseModel):
    total: int
    items: list[OfferReleaseOut]


class OfferReleaseCustomerListOut(BaseModel):
    total: int
    items: list[OfferReleaseCustomerOut]


class SourceCheckListOut(BaseModel):
    artifact_id: int
    total: int
    items: list[SourceCheckOut]
    gate: SourceGateOut


def offer_release_out(data: dict[str, Any]) -> OfferReleaseOut:
    return OfferReleaseOut.model_validate(data)


def offer_release_created_out(data: dict[str, Any]) -> OfferReleaseCreatedOut:
    return OfferReleaseCreatedOut.model_validate(data)


def offer_release_customer_out(data: dict[str, Any]) -> OfferReleaseCustomerOut:
    return OfferReleaseCustomerOut.model_validate(data)


def offer_response_out(data: dict[str, Any]) -> OfferResponseOut:
    return OfferResponseOut.model_validate(data)


def source_check_out(data: dict[str, Any]) -> SourceCheckOut:
    return SourceCheckOut.model_validate(data)


# ── 合同派生（contracts_api.py / S3 / BP-03 第 8 条 / D1-08）────────────────
#
# ⚠️ 这里**故意没有**"客户视角"的合同响应模型。客户要看合同走的是**已有的发布通路**
# （把这份 `contract_review` 发布出去，客户在 `/my-offer-releases` 读冻结快照）——
# 字段来源表里带 `release:12@v3` / `leg:4` 这类内部编号，那是审计信息。
# 为"客户看合同"新开一条通道会让"客户能看到什么"变成两个判据
# （与 §6.3 的白名单下载同一条纪律：能下什么由**发布那一刻**决定）。


class ContractDeriveIn(BaseModel):
    """派生请求体。**整个请求体可选** —— 派生不需要调用方提供任何业务参数。

    为什么不强制一个字段：合同内容**不能**由调用方给。它必须完全来自
    "已接受的那一条发布"（金额、费用范围、有效期）与委托单（当事方）。
    留一个可选 `note` 只是给经理写一句备注的位置；正文一律由服务端派生。
    ⛔ 若将来有人想往这里加 `amount` / `parties` 之类的入参，那等于允许手工
    编造合同条款 —— 与 BP-03 第 8 条"从已接受事实派生"直接冲突，**不要加**。
    """

    note: str | None = Field(default=None, max_length=500)


class ContractFieldSourceOut(BaseModel):
    """**一行**字段来源：合同里某个字段的值是从哪来的（逐字段可核对的最小单位）。"""

    #: 合同里的字段路径，如 `parties[1].name` / `clauses[0].text` / `effective_date`
    field_path: str
    #: 实际写进合同的**字面值**。与合同内容对不上 ⇒ 派生不可信（核对从这里开始）
    value_text: str
    #: 来源类别：`accepted_release` / `customer_response` / `assignment` / `leg` /
    #: `organization` / `template`
    source_kind: str
    #: 来源类别的**显示文案**（服务端给出，标签唯一实现在 `contracts.SOURCE_KIND_LABELS`）
    source_kind_text: str = ""
    #: 来源标识（内部编号）。**只在本表出现，不进合同正文**
    source_ref: str


class ContractDerivationOut(BaseModel):
    """一条派生记录（经理视角）：派生关系 + 逐字段来源 + 如实列出的缺失项。

    D1-08 的判据原文是 `Contract version and linked evidence inspection` ——
    要能"检查它怎么来的"。所以这里把报价与合同**各自**的精确版本都摆出来，
    再逐字段给出出处，而不是只回一句"已派生"。
    """

    derivation_id: int
    assignment_id: int
    entrustment_id: int | None = None
    #: **已接受**的那一条发布；连同下面的精确版本构成"派生自哪份已接受事实"
    release_id: int
    response_id: int
    quote_artifact_id: int
    quote_revision_id: int
    quote_revision_no: int
    #: 派生出的合同成果与其版本
    contract_artifact_id: int
    contract_revision_id: int
    contract_revision_no: int
    template_code: str
    template_version: str
    effective_date: str | None = None
    derived_by: int
    derived_at: str
    note: str | None = None
    #: 冻结快照里**没有**提供、因而合同未写的报价字段。列出来而不是编默认值 ——
    #: "这份合同少写了什么"必须能一眼看到。
    absent_quote_fields: list[str] = Field(default_factory=list)
    field_sources: list[ContractFieldSourceOut] = Field(default_factory=list)


class ContractDerivationCreatedOut(ContractDerivationOut):
    """派生成功时额外回来源行数，让调用方一眼看到覆盖规模（少一行就说明漏登记）。"""

    field_source_count: int = 0


def contract_derivation_out(data: dict[str, Any]) -> ContractDerivationOut:
    return ContractDerivationOut.model_validate(data)


def contract_derivation_created_out(data: dict[str, Any]) -> ContractDerivationCreatedOut:
    return ContractDerivationCreatedOut.model_validate(data)


# ── 签署证据（§10.1 第 7 步后半 / D1-08 的 linked evidence）─────────────────
#
# ⚠️ `SignatureEvidenceIn` **没有 `mode` 字段**，这是刻意的：
# 模式由服务端写死为 `labeled_sample`（`contracts.SIGNATURE_MODE`）。
# 让调用方能传 `mode` 就等于让界面能自称"已完成电子签署"，
# 而合同 §3.2 与 D1-08 都要求证据与状态**不得**等同于实时电子签。


class SignatureEvidenceIn(BaseModel):
    """记一条签署证据的请求体。

    `revision_no` 省略 ⇒ 记到合同的**当前版本**（界面主路径）。
    """

    #: 证据形态：`sample_scan` / `written_confirmation` / `manual_record`。
    #: 未知取值由服务层拒（400），这里**不**做宽松解析 ——
    #: 一个未登记的形态进库后，"这份证据到底存不存在实物"就再无答案。
    evidence_kind: str = Field(min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=255)
    revision_no: int | None = Field(default=None, ge=1)


class SignatureEvidenceOut(BaseModel):
    """一条签署证据（经理投影）：原值与显示文案**同时**给出。"""

    evidence_id: int
    assignment_id: int
    entrustment_id: int | None = None
    contract_artifact_id: int
    contract_revision_id: int
    contract_revision_no: int
    revision_no_text: str
    mode: str
    mode_text: str
    evidence_kind: str
    evidence_kind_text: str
    note: str
    note_text: str
    recorded_by: int | None = None
    recorded_at: str


class EvidenceKindOptionOut(BaseModel):
    """可选的一种证据形态。

    与 `disclaimer` 同一条理由：**由服务端给出**。形态的取值域在
    `contracts.ALL_EVIDENCE_KINDS`、标签在 `contracts.EVIDENCE_KIND_LABELS` ——
    前端自己存一份就等于允许界面给出一个服务端根本不收的形态。
    """

    value: str
    label: str


class SignatureEvidenceListOut(BaseModel):
    """某份合同的签署证据清单。

    `disclaimer` 是**合同要求常驻**的声明（§3.2 / D1-08），由服务端给出而不是前端写死：
    措辞只有一份，改了不会有的界面说、有的界面不说。
    """

    contract_artifact_id: int
    items: list[SignatureEvidenceOut] = Field(default_factory=list)
    has_items: bool = False
    disclaimer: str = ""
    kind_options: list[EvidenceKindOptionOut] = Field(default_factory=list)


def signature_evidence_out(data: dict[str, Any]) -> SignatureEvidenceOut:
    return SignatureEvidenceOut.model_validate(data)


def signature_evidence_list_out(data: dict[str, Any]) -> SignatureEvidenceListOut:
    return SignatureEvidenceListOut.model_validate(data)


# ── 运力确认与有效期（capacity_api.py / S3 / BP-03 第 3 条 / D1-06）──────────
#
# ⚠️ 这一组**没有**客户视角的响应模型，而且是**成组的**：候选运力含承运人与供应商
# 单价，采购确认含 `agreed_amount`/`currency`/`supplier` —— 注册表已把这三个字段
# 标为 `internal_fields`。所以运力这一整条支线（登记、清单、确认、复算）
# **一律不给货主本人放行**（`assert_can_view_org`，没有货主旁路）。
# 客户要了解商业承诺，看到的是**对客报价的冻结快照**，不是这里的任何一条记录。


class CapacityCandidateIn(BaseModel):
    """登记一条候选运力（**不是确认**）。

    请求体里给的是**关于这份资源的事实**：吨位、船数、是否拆批、单价、有效期、证据。
    ⛔ 不要往这里加"是否已确认"这类字段 —— 确认是**另一个动作**，由
    `POST /assignments/{id}/capacity-confirmations` 作出，且要有 actor 与逐规则判定。
    把"已确认"做成登记时的一个入参，等于让"选中"一步到位变成"确认"，
    而那正是 BP-03 第 3 条要分开的两件事。

    数值用 `Decimal`：`capacity_tonnes` 参与容量判定，用 `float` 会在
    "950 vs 900"这类边界上引入二进制误差，而这条判定的全部价值就是它算得准。
    """

    carrier: str = Field(min_length=1, max_length=64)
    capacity_tonnes: Decimal
    vessel_count: int = 1
    allows_partial_load: bool = False
    #: 对应航段（`ent_leg.id`）；给了就必须属于同一委托
    leg_id: int | None = None
    vessel_name: str | None = Field(default=None, max_length=64)
    rate: Decimal | None = None
    #: 与 `rate` **成对**：只有金额无法确定费用（与注册表里 `rate`/`rate_unit` 同一条理由）
    rate_unit: str | None = Field(default=None, max_length=16)
    currency: str | None = Field(default=None, max_length=8)
    #: 报价有效期。**可以不给**（登记时还不知道），但确认时会因此判不通过 ——
    #: "未知有效期"不等于"未过期"。
    valid_until: date | None = None
    #: 证据类别（取值域见 `registry.ALL_EVIDENCE_KINDS`）与**证据引用**。
    #: 两者都可后补；确认时两者都必须齐（`identified evidence`）。
    evidence_kind: str | None = Field(default=None, max_length=32)
    evidence_ref: str | None = Field(default=None, max_length=255)


class CapacityCandidateOut(BaseModel):
    """一条候选运力。数值列是**定长文本**（3 位吨位 / 4 位单价），

    用文本而不是 `Decimal` 输出，是为了让 SQLite 与 MySQL 两个后端给出**同一个字符串** ——
    否则"同一份数据在两边看起来不同"，逐字段核对还要先解释格式差异。
    """

    candidate_id: int
    assignment_id: int
    leg_id: int | None = None
    carrier: str
    vessel_name: str | None = None
    capacity_tonnes: str
    vessel_count: int
    allows_partial_load: bool
    rate: str | None = None
    rate_unit: str | None = None
    currency: str | None = None
    valid_until: str | None = None
    evidence_kind: str | None = None
    evidence_ref: str | None = None
    #: `candidate` / `confirmed`（由确认命令写）/ `withdrawn`
    status: str
    created_at: str
    updated_at: str


class CapacityConfirmIn(BaseModel):
    """确认请求体。**只有三样东西**：确认哪一条候选、这一确认覆盖什么范围、一句备注。

    ⛔ 不要往这里加 `supplier` / `capacity_tonnes` / `agreed_amount` 这类字段。
    确认的一切**事实**都必须从候选行读（服务端按 `candidate_id` 取）。
    让请求体带这些值会造出"确认的内容"与"候选运力"可以不一致的状态，
    而那条不一致在界面上看不出来 —— 于是"确认"退化成一次自述，
    容量判定也就成了对调用方输入的判定，不是对数据的判定。

    为什么 `agreed_scope` 是**必填**（与 `ContractDeriveIn` 全可选形成对照）：
    合同正文可以完全派生（金额、费用范围、当事方都有出处），所以派生请求体不需要
    任何业务参数；但"这次确认覆盖什么范围"是一条**采购范围判断**，数据里没有它的出处，
    只能由人给出。把它变成可选并补一个默认值，等于宣称「确认了」三个字本身
    就构成可复核的商务事实。
    """

    candidate_id: int
    agreed_scope: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class CapacityRuleCheckOut(BaseModel):
    """**一条规则的判定**。

    同一个模型服务两种场景，这是**有意**的：

    * 落库的行（读确认详情时）**只有通过**的判定 —— 所以 `outcome` 恒为 `pass`；
      库里**没有** `outcome` 列：失败不落库（拒绝的证据是 409 响应体，不是一条历史行），
      写一个恒为 `pass` 的列是造一个假字段。
    * 409 响应体里的判定**可能**是 `fail` —— 在这里带 `outcome` 是为了让
      "被拒绝"和"已确认"在客户端**形状一致**，一套渲染就够（否则失败要多写一套）。
    """

    rule_code: str
    seq: int
    #: `pass` / `fail`
    outcome: str
    #: 判定说明，含**实际比较的数**（如「900.000 × 1 = 900.000 吨 < 需求 950.000 吨，缺口 50.000 吨」）
    detail: str
    created_at: str | None = None


class CapacityConfirmationOut(BaseModel):
    """一条运力确认记录。

    ⚠️ 本模型里的 `capacity_tonnes` / `valid_until` / `evidence_*` 等**全部来自确认行**
    （确认那一刻的冻结副本），**不是**候选行现在的值 —— 候选行可以被改写，
    而确认是一条已经作出的商业事实。想知道"现在还是不是这样"，
    用 `GET /capacity-confirmations/{id}/recheck`（只读复算，它会把差异列出来）。

    `rule_checks` = 这次确认**评估过**的规则（逐条带比较值）。它的存在使
    "规则跑没跑"可被核对 —— 否则"跑了且通过"与"这次没跑"在数据上完全一样。
    """

    confirmation_id: int
    assignment_id: int
    entrustment_id: int | None = None
    candidate_id: int
    leg_id: int | None = None
    carrier: str
    vessel_name: str | None = None
    capacity_tonnes: str
    vessel_count: int
    allows_partial_load: bool
    rate: str | None = None
    rate_unit: str | None = None
    currency: str | None = None
    valid_until: str | None = None
    evidence_kind: str | None = None
    evidence_ref: str | None = None
    #: 判定输入的另一半：需求（确认时的快照）与它的出处
    demand_tonnes: str | None = None
    demand_unit: str | None = None
    demand_ref: str
    #: 判定基准日（有效期与它比较）。与 `confirmed_at` 分开：业务时间可复算
    as_of_date: str
    #: 规则集版本 —— 规则会演进，旧确认要能说清它按哪一版判的
    rule_set_version: str
    #: 这次确认**覆盖的范围**（业务判断，确认时由调用方给出）。
    #: ⚠️ 它**不在确认行的列上** —— 事实来源是这次确认产出的**成果版本**
    #: （`artifact_revision_id` 冻结在确认行上 ⇒ 取到的永远是"当时那一版"），
    #: 见 `capacity._scope_from_payload`。`None` = 那一版载荷里没有这个字段。
    agreed_scope: str | None = None
    #: 确认产出的采购确认成果与版本（可发布、可复核、可作变更影响目标）
    artifact_id: int
    artifact_revision_id: int
    artifact_revision_no: int
    confirmed_by: int
    confirmed_at: str
    note: str | None = None
    rule_checks: list[CapacityRuleCheckOut] = Field(default_factory=list)


class CapacityConfirmationCreatedOut(CapacityConfirmationOut):
    """确认成功时额外回规则条数（少一条就说明有规则没跑）。"""

    rule_check_count: int = 0


class CapacityRecheckOut(BaseModel):
    """**只读复算**：这条确认现在还成立吗。

    `rule_checks` 是**用当前事实**重跑同一套规则的结果；`frozen_rule_checks` 是确认
    当时落库的判定。两者并排，才能回答"为什么不成立了" —— 只给一个
    `still_valid=false` 等于让人自己去猜是哪一项变了。

    ⚠️ 本端点**不写任何行**：正式的重做属 S4 的变更流程。这里只是让
    "900 吨候选在变更后不再适用"这句话**可以被看到**，而不是只在模型意见里。
    """

    confirmation_id: int
    still_valid: bool
    as_of_date: str
    #: 候选行不在了（不该发生；真发生时 `still_valid` 恒为 false —— 判据没了，结论就没了）
    candidate_missing: bool = False
    #: 当前值与确认冻结值**不一致**的判定输入（只列判定真正读到的那几列）
    changed_fields: list[str] = Field(default_factory=list)
    rule_checks: list[CapacityRuleCheckOut] = Field(default_factory=list)
    frozen_rule_checks: list[CapacityRuleCheckOut] = Field(default_factory=list)


def capacity_candidate_out(data: dict[str, Any]) -> CapacityCandidateOut:
    return CapacityCandidateOut.model_validate(data)


def capacity_confirmation_out(data: dict[str, Any]) -> CapacityConfirmationOut:
    return CapacityConfirmationOut.model_validate(data)


def capacity_confirmation_created_out(data: dict[str, Any]) -> CapacityConfirmationCreatedOut:
    return CapacityConfirmationCreatedOut.model_validate(data)


def capacity_recheck_out(data: dict[str, Any]) -> CapacityRecheckOut:
    return CapacityRecheckOut.model_validate(data)


# ── 运输计划与必需任务前置（BP-03 第 1 条 / 合同 §10.1 第 4 步）──────────────
# 读模型，无请求体：所有字段都来自 `ent_leg` 与 `ent_workflow_task`。


class PlanLegOut(BaseModel):
    """一个航段（`ent_leg`）。"""

    leg_id: int
    seq: int
    #: 原始取值（`road` / `water` / …）—— **保留**：标签表会演进，原始值不会
    mode: str
    #: 展示标签；未登记的取值**等于** `mode` 本身（未知保持未知，不兜底成"公路"）
    mode_label: str
    from_name: str
    to_name: str


class PlanTaskOut(BaseModel):
    """一个**必需任务**及其固定前置（`ent_workflow_task`）。

    字段面照抄 `workbench._load_tasks` 的先例，**不夹带** `required_evidence` ——
    本读模型只回答 §10.1 第 4 步问的那件事（计划**与前置**），
    多带的字段会在界面之外多一个会各自演化的落点。
    """

    task_id: int
    task_type: str
    title: str
    status: str
    #: 固定前置任务 id；`None` = **没有前置**（不是"前置未知"）
    precondition_task_id: int | None = None


class AssignmentPlanOut(BaseModel):
    """§10.1 第 4 步的读模型：`Show the road–water–road plan and required task prerequisites`。

    ⚠️ 本模型**不含**"三段"/"公路—内河—公路"这类**结论性文案**：段数是 `legs` 的
    属性，由界面按行渲染。在服务端把它拼成一句话，就多了一个会与数据脱节的落点。
    """

    assignment_id: int
    legs: list[PlanLegOut] = Field(default_factory=list)
    task_prerequisites: list[PlanTaskOut] = Field(default_factory=list)


def assignment_plan_out(data: dict[str, Any]) -> AssignmentPlanOut:
    return AssignmentPlanOut.model_validate(data)


# ── 航段命令（建段 / 改段 / 版本历史）──────────────────────────────────────────
# HO 2026-09-17 裁定的三条口径见 `backend/migrations/ent_leg_revision.py` 的模块文档。
# 字段面的取舍：`LegOut` 与 `PlanLegOut` **保持同一份前六个键**（航段读模型与写响应
# 说的必须是同一件事），只多出「当前版本号」与「更新时间」两个写侧才有的键。


class LegCreateIn(BaseModel):
    """建段入参。

    ⚠️ `mode` **不是枚举**：`road/water/rail` 是既有数据用到的取值，不是写入口的
    取值域 —— 把它做成枚举就等于用一行代码固化"只能有一种方案"，而裁定明确说
    **不强制 公–水–公**。长度上限与迁移的列宽一致（超长在这里就被拦成 422/400，
    不会变成 DB 的 1406）。
    """

    #: 航段顺序，自 1 起、委托内唯一（读侧按它排序 = 方案顺序）
    seq: int = Field(ge=1)
    mode: str = Field(min_length=1, max_length=16)
    from_name: str = Field(min_length=1, max_length=64)
    to_name: str = Field(min_length=1, max_length=64)
    #: 改动说明；可空（留版本是硬要求，写理由不是）
    change_note: str | None = Field(default=None, max_length=255)


class LegUpdateIn(BaseModel):
    """改段入参 —— 全部可空，**至少要带一个**（服务层会以 400 拒绝空改动）。

    ⚠️ 与 `LegCreateIn` 用两个模型而不是"一个模型 + 全可空"，是因为两者的
    约束**相反**：建段必须给齐 seq/mode/起终点，改段只给要改的那些。
    合成一个模型就只能把必填性放松，于是"建段少给一个字段"会从 422 退化成
    服务层的一句 400 —— 校验点变远了，错的提示也变模糊了。
    """

    seq: int | None = Field(default=None, ge=1)
    mode: str | None = Field(default=None, min_length=1, max_length=16)
    from_name: str | None = Field(default=None, min_length=1, max_length=64)
    to_name: str | None = Field(default=None, min_length=1, max_length=64)
    change_note: str | None = Field(default=None, max_length=255)


class LegOut(BaseModel):
    """一段航段（写响应）。语义＝**这次操作完成之后**该段的当前状态。

    `revision_no` 是"这一改是第几版"：建段恒为 `1`，第 N 次改段为 `N`
    （建段算第 1 版）。完整历史走 `GET /assignments/{id}/legs/{leg_id}/revisions`。
    """

    leg_id: int
    seq: int
    mode: str
    mode_label: str
    from_name: str
    to_name: str
    revision_no: int
    updated_at: str


class LegRevisionOut(BaseModel):
    """航段的一个**历史版本**（append-only 快照）。

    ⚠️ 每一行是**当时**的取值快照，不是"指向当前行" —— 所以改完之后回看，
    能读出"它当时是什么"，这正是"改段留版本"要回答的问题。
    """

    revision_id: int
    leg_id: int
    #: 该版本当时的顺序号（改过 `seq` 的段，历史里会看到不同的值）
    seq: int
    mode: str
    mode_label: str
    from_name: str
    to_name: str
    revision_no: int
    #: `created`（建段）/ `updated`（改段）
    change_kind: str
    #: 改动说明；`None` = 当时没写（**不是**"说明未知"）
    change_note: str | None = None
    #: 发起人；`0` = 系统/种子（留痕用，不参与判权，故不叫 actor 的"权限"含义）
    actor_user_id: int | None = None
    changed_at: str


def leg_out(data: dict[str, Any]) -> LegOut:
    return LegOut.model_validate(data)


def leg_revision_out(data: dict[str, Any]) -> LegRevisionOut:
    """把 `ent_leg_revision` 行投影成对外模型。

    两个改名是**有意**的（读侧不该看到 DB 的列名）：
    `id → revision_id`（它是一条**版本**的 id，不是航段的 id）、
    `created_at → changed_at`（它记的是"这一版是什么时候发生的"）。

    ⚠️ `mode_label` 由**服务层**（`legs.list_leg_revisions`）算好放进行里，
    本函数**不**自己去查标签表 —— 那样 schemas 就要 import `plan`，
    而 `plan` 是业务模块，让校验层依赖业务层会把这个文件的定位弄糊
    （它现在的定位是：**只做形状校验与改名**）。
    """
    return LegRevisionOut.model_validate(
        {
            "revision_id": int(data["id"]),
            "leg_id": int(data["leg_id"]),
            "seq": int(data["seq"]),
            "mode": str(data["mode"]),
            "mode_label": str(data["mode_label"]),
            "from_name": str(data["from_name"]),
            "to_name": str(data["to_name"]),
            "revision_no": int(data["revision_no"]),
            "change_kind": str(data["change_kind"]),
            "change_note": data["change_note"],
            "actor_user_id": data["actor_user_id"],
            "changed_at": str(data["created_at"]),
        }
    )


class AssignmentQuantityChangeOut(BaseModel):
    """一条**已应用的**委托货量变更（`ent_assignment_quantity_change` 一行）。

    同时给原值与文案：`old_quantity` / `new_quantity` 是机器比对的定点数值
    （固定 3 位小数，SQLite 与 MySQL 读回来是同一个字符串），
    `*_text` 是给界面直接显示的那一句（`"800.000 吨"` / `"未知"`）。

    为什么两个都给：让界面自己拼 `字符串 + 单位`，就必然出现"这里拼了、那里没拼"
    的漂移；而让程序去解析显示文案更是本末倒置。**值只有一份来源**是这道纪律的核心。

    `old_quantity` 为 `None` = **变更前是未知**（不是 0）：从"未知"改成确定值是
    一次合法变更，把它显示成 0 会让历史看起来像"从 0 涨到 950"。
    """

    change_id: int
    assignment_id: int
    #: 来源变更案件（`ent_exception.id`）。有了它才能回答"这一改是谁批的"，
    #: 顺着案件号能回到批准快照与决定人。
    exception_id: int
    #: 变更前委托的乐观锁值 —— "这一改从第几版出发"
    base_revision: int
    old_quantity: str | None = None
    old_quantity_unit: str | None = None
    new_quantity: str
    new_quantity_unit: str
    #: 显示用文案（`"800.000 吨"` / `"未知"`）
    old_quantity_text: str
    new_quantity_text: str
    #: 变更依据（经理写的话；**可能含内部口径**，因此本组端点不给货主放行）
    basis: str
    applied_by: int | None = None
    applied_at: str


def assignment_quantity_change_out(data: dict[str, Any]) -> AssignmentQuantityChangeOut:
    return AssignmentQuantityChangeOut.model_validate(data)
