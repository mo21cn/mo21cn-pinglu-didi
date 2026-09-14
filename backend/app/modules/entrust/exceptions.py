"""异常与变更案件 —— 服务层**规则**（DR-0013 A1 切片二）。

本模块分两区：

* **上半区 = 纯规则**（切片二）：取值域、两套状态机、阻断判据、一致性约束、关闭前置条件。
  不依赖数据库，因此「驳回处置方案不会解除真实异常」这类**语义**能在纯函数层被用例钉住，
  不必先起一个库、一张委托单、一次认领。
* **下半区 = 持久化与服务层**（切片三）：案件与受影响项的读写、事件追加（**与状态变更同事务**）、
  两套**独立**白名单投影、阻断查询。IO 需要规则时就是普通函数调用 —— 规则仍是单一实现。

三处最容易被写错的地方（都在这里固定成单一实现）
------------------------------------------------
1. **阻断只认 `impact_kind`**（§3.3）。`severity=casual` 到 `critical` 全部**不参与**
   allow/deny —— 否则「改一个展示用字段」就成了权限级开关：把 critical 调成 low
   就能放行结案与执行。严重度是**业务判断**，阻断是**流程约束**，两者权限与审计要求不同。
2. **两套状态机**（§3.4）。PRD 第 277 行的六个值带一句 "as applicable to type"：
   * `exception` 的 `rejected` = **提交的处置方案被驳回** ⇒ 真实异常仍在 ⇒ **不是终态**，阻断**保持**；
   * `change_request` 的 `rejected` = **变更请求被否** ⇒ 该变更不会发生 ⇒ **不再阻断**。
   所以「同一个 `rejected`」在两种 kind 下语义相反，**不能共用一句判据**。
3. **未知值一律抛错，不返回「不阻断」**。`is_blocking` 若对未知 kind/status 返回 `False`，
   就等于 fail-open —— 一条脏数据能让执行门禁静默放行。门禁路径必须 fail-closed。

与 HO 裁决的对应：第 2 条（C1/C2/C3、降级不自动解除阻断）、第 3 条（分别定义转移、
驳回不解除真实异常、关闭校验处置规则与证据）、第 4 条（重开留审计 → 下半区**同事务**实现）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Final, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust.access import (
    PERM_TASK_DISPATCH,
    AccessContext,
    assert_can,
    resolve_context,
    utcnow_naive,
)
from app.modules.entrust.authz import assert_can_view_assignment, load_assignment
from app.modules.entrust.registry import CUSTOMER_VISIBLE_TYPES

# ── 取值域（逐项对应 DR-0013 §3.1；PRD §5.5 / §6.1 / §5.4）────────────────────

KIND_EXCEPTION: Final = "exception"
KIND_CHANGE_REQUEST: Final = "change_request"
KINDS: Final = frozenset({KIND_EXCEPTION, KIND_CHANGE_REQUEST})

SEVERITIES: Final = frozenset({"low", "medium", "high", "critical"})
SEVERITY_CRITICAL: Final = "critical"

IMPACT_INFORMATIONAL: Final = "informational"
IMPACT_REVIEW_REQUIRED: Final = "review-required"
IMPACT_EXECUTION_BLOCKING: Final = "execution-blocking"
IMPACT_KINDS: Final = frozenset(
    {IMPACT_INFORMATIONAL, IMPACT_REVIEW_REQUIRED, IMPACT_EXECUTION_BLOCKING}
)

STATUS_OPEN: Final = "open"
STATUS_IN_REVIEW: Final = "in_review"
STATUS_APPROVED: Final = "approved"
STATUS_REJECTED: Final = "rejected"
STATUS_APPLIED: Final = "applied"
STATUS_CLOSED: Final = "closed"
STATUSES: Final = frozenset(
    {STATUS_OPEN, STATUS_IN_REVIEW, STATUS_APPROVED, STATUS_REJECTED, STATUS_APPLIED, STATUS_CLOSED}
)

SOURCES: Final = frozenset({"manual", "chat", "customer", "agent_proposal"})

DISPOSITION_RESOLVED: Final = "resolved"
DISPOSITION_ACCEPTED_RESIDUAL: Final = "accepted_residual"
DISPOSITION_CANCELLED: Final = "cancelled"
DISPOSITION_DUPLICATE: Final = "duplicate"
DISPOSITION_SUPERSEDED: Final = "superseded"
DISPOSITIONS: Final = frozenset(
    {
        DISPOSITION_RESOLVED,
        DISPOSITION_ACCEPTED_RESIDUAL,
        DISPOSITION_CANCELLED,
        DISPOSITION_DUPLICATE,
        DISPOSITION_SUPERSEDED,
    }
)

#: 无需「实际应用」即可直接终结的处置 —— 它们说的是「这单本身不该存在」，
#: 而不是「问题已解决」。`resolved` / `accepted_residual` **不在**其中。
_DISPOSITIONS_WITHOUT_APPLICATION: Final = frozenset(
    {DISPOSITION_CANCELLED, DISPOSITION_DUPLICATE, DISPOSITION_SUPERSEDED}
)


# ── 规则层异常（由 API 层集中映射：*Error→400 / *ConflictError→409 / *NotFoundError→404）──


class ExceptionCaseError(Exception):
    """规则违反 → HTTP 400。"""


class ExceptionCaseConflictError(Exception):
    """状态冲突（非法转移、乐观锁过期）→ HTTP 409。"""


class ExceptionCaseNotFoundError(Exception):
    """对象不存在 → HTTP 404。"""


# ── 两套状态机（DR-0013 §3.4）────────────────────────────────────────────────

#: `kind` → 当前状态 → 允许到达的状态集合。
#: **自环不在此表中** ⇒ `x → x` 天然非法（PRD 第 279 行）。
_STATUS_TRANSITIONS: Final[dict[str, dict[str, frozenset[str]]]] = {
    KIND_EXCEPTION: {
        STATUS_OPEN: frozenset({STATUS_IN_REVIEW, STATUS_APPROVED, STATUS_REJECTED, STATUS_CLOSED}),
        STATUS_IN_REVIEW: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_OPEN}),
        STATUS_APPROVED: frozenset({STATUS_APPLIED, STATUS_REJECTED, STATUS_CLOSED}),
        STATUS_REJECTED: frozenset({STATUS_IN_REVIEW, STATUS_OPEN, STATUS_CLOSED}),
        STATUS_APPLIED: frozenset({STATUS_CLOSED}),
        STATUS_CLOSED: frozenset({STATUS_OPEN}),
    },
    KIND_CHANGE_REQUEST: {
        STATUS_OPEN: frozenset({STATUS_IN_REVIEW, STATUS_REJECTED, STATUS_CLOSED}),
        STATUS_IN_REVIEW: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_OPEN}),
        STATUS_APPROVED: frozenset({STATUS_APPLIED, STATUS_REJECTED, STATUS_CLOSED}),
        STATUS_REJECTED: frozenset({STATUS_CLOSED}),
        STATUS_APPLIED: frozenset({STATUS_CLOSED}),
        STATUS_CLOSED: frozenset({STATUS_OPEN}),
    },
}

#: 关闭时允许的处置，按 **(kind, 关闭前状态)** 限定（DR-0013 §3.5）。
#:
#: 关键两行：`exception` 的 `rejected` / `approved` 关闭**不含** `resolved` 与
#: `accepted_residual` —— 这正是 HO「**不允许通过驳回处置方案解除真实异常**」的落地。
#: 真实异常要真正终结，必须走到 `applied` 再以 `resolved` 关闭（或按 §3.7 应用变更）。
_CLOSURE_DISPOSITIONS: Final[dict[tuple[str, str], frozenset[str]]] = {
    (KIND_EXCEPTION, STATUS_OPEN): _DISPOSITIONS_WITHOUT_APPLICATION,
    (KIND_EXCEPTION, STATUS_APPROVED): _DISPOSITIONS_WITHOUT_APPLICATION,
    (KIND_EXCEPTION, STATUS_REJECTED): _DISPOSITIONS_WITHOUT_APPLICATION,
    (KIND_EXCEPTION, STATUS_APPLIED): frozenset(
        {DISPOSITION_RESOLVED, DISPOSITION_ACCEPTED_RESIDUAL}
    ),
    (KIND_CHANGE_REQUEST, STATUS_OPEN): _DISPOSITIONS_WITHOUT_APPLICATION,
    # 变更请求在 approved 阶段没有 `duplicate`：到这一步已确认「这是个真请求」。
    (KIND_CHANGE_REQUEST, STATUS_APPROVED): frozenset(
        {DISPOSITION_CANCELLED, DISPOSITION_SUPERSEDED}
    ),
    (KIND_CHANGE_REQUEST, STATUS_REJECTED): _DISPOSITIONS_WITHOUT_APPLICATION,
    (KIND_CHANGE_REQUEST, STATUS_APPLIED): frozenset(
        {DISPOSITION_RESOLVED, DISPOSITION_ACCEPTED_RESIDUAL, DISPOSITION_SUPERSEDED}
    ),
}


def _require_known_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ExceptionCaseError(f"未知案件类型：{kind!r}（取值域：{sorted(KINDS)}）")
    return kind


def _require_known_status(status: str) -> str:
    if status not in STATUSES:
        raise ExceptionCaseError(f"未知案件状态：{status!r}（取值域：{sorted(STATUSES)}）")
    return status


def allowed_transitions(kind: str, from_status: str) -> frozenset[str]:
    """返回 `kind` 下从 `from_status` 可到达的状态集合（只读查询，供 UI 与用例使用）。"""
    _require_known_kind(kind)
    _require_known_status(from_status)
    return _STATUS_TRANSITIONS[kind][from_status]


def allowed_closure_dispositions(kind: str, from_status: str) -> frozenset[str]:
    """返回该 (kind, from_status) 下允许的关闭处置。"""
    _require_known_kind(kind)
    _require_known_status(from_status)
    return _CLOSURE_DISPOSITIONS.get((kind, from_status), frozenset())


def assert_transition(
    *,
    kind: str,
    from_status: str,
    to_status: str,
    closure_disposition: str | None = None,
) -> None:
    """校验一次状态转移；非法 → 409（响应须带当前状态，由 API 层补）。

    关闭（`to_status == 'closed'`）额外校验处置规则 —— 这是 §3.5 的「关闭必须回答
    **凭什么关**」，也正是 PRD 第 255 行那句 `not a generic skip`：
    没有「一键关闭」，`closure_disposition` 缺失一律 400，**不做默认值填充**。
    """
    _require_known_kind(kind)
    _require_known_status(from_status)
    _require_known_status(to_status)

    allowed = _STATUS_TRANSITIONS[kind][from_status]
    if to_status not in allowed:
        raise ExceptionCaseConflictError(
            f"非法状态转移：{kind} 不能从 {from_status} 转到 {to_status}"
            f"（当前允许：{sorted(allowed)}）"
        )

    if to_status != STATUS_CLOSED:
        return

    if not closure_disposition:
        raise ExceptionCaseError(
            "关闭案件必须给出 closure_disposition（不提供默认值 —— 关闭要说明凭什么关）"
        )
    if closure_disposition not in DISPOSITIONS:
        raise ExceptionCaseError(
            f"未知处置：{closure_disposition!r}（取值域：{sorted(DISPOSITIONS)}）"
        )
    permitted = allowed_closure_dispositions(kind, from_status)
    if closure_disposition not in permitted:
        raise ExceptionCaseError(
            f"{kind} 在 {from_status} 状态不能以 {closure_disposition} 关闭"
            f"（允许：{sorted(permitted)}）"
        )


# ── 阻断判据（DR-0013 §3.3；**唯一实现**，执行门禁与结案检查共用）──────────────


def is_blocking(*, kind: str, impact_kind: str, status: str) -> bool:
    """该案件当前是否**阻断对应动作**。

    单一判据，§6.1 验证 9（`complete_task` 门禁）与验证 20（结案检查）共用。
    **不复制第二份** —— 两处各写一遍，迟早出现「执行命令认阻断、结案不认」。

    未知取值**抛错而非返回 `False`**：门禁路径返回 `False` 就是 fail-open，
    一条脏数据足以让执行动作被静默放行。

    Note:
        `severity` **有意不在签名里**。把它加进来（哪怕只是默认参数）就等于给了
        「用严重度决定放行」的入口，而 HO 第 2 条与 C3 正是要堵住它。
    """
    _require_known_kind(kind)
    _require_known_status(status)
    if impact_kind not in IMPACT_KINDS:
        raise ExceptionCaseError(f"未知影响类型：{impact_kind!r}（取值域：{sorted(IMPACT_KINDS)}）")

    if impact_kind != IMPACT_EXECUTION_BLOCKING:
        return False
    if kind == KIND_EXCEPTION:
        # 真实异常：只要没关闭就一直算阻断 —— `rejected` 也不解除（驳回的是方案，不是异常）。
        return status != STATUS_CLOSED
    # 变更请求：被否 ⇒ 该变更不会发生 ⇒ 不再阻断；其余未关闭状态仍阻断。
    return status not in (STATUS_CLOSED, STATUS_REJECTED)


# ── C1 / C2 / C3 一致性约束（DR-0013 §3.3）──────────────────────────────────


def assert_severity_impact_consistent(*, severity: str, impact_kind: str) -> None:
    """**C1**：`severity='critical'` ⇒ `impact_kind='execution-blocking'`。

    依据 PRD §5.5「Critical unresolved cases block the relevant execution/closure actions」。
    **单向**：`execution-blocking` 不要求 `critical` —— 一个 `high` 的异常同样可以阻断
    执行流程（阻断是流程约束，不是严重度标签）。反向要求会把两个正交维度绑死。
    """
    if severity not in SEVERITIES:
        raise ExceptionCaseError(f"未知严重度：{severity!r}（取值域：{sorted(SEVERITIES)}）")
    if impact_kind not in IMPACT_KINDS:
        raise ExceptionCaseError(f"未知影响类型：{impact_kind!r}（取值域：{sorted(IMPACT_KINDS)}）")
    if severity == SEVERITY_CRITICAL and impact_kind != IMPACT_EXECUTION_BLOCKING:
        raise ExceptionCaseError(
            "severity=critical 必须搭配 impact_kind=execution-blocking"
            "（PRD §5.5：critical 且未解决会阻断对应动作）"
        )


def assert_blocking_requires_link(*, impact_kind: str, link_count: int) -> None:
    """**C2**：`impact_kind='execution-blocking'` ⇒ 至少一条受影响项。

    依据 PRD §5.4「prevent the **corresponding action**」—— 没有对应动作，
    就没有「the corresponding action」可阻断，这条阻断是空转的。
    """
    if impact_kind not in IMPACT_KINDS:
        raise ExceptionCaseError(f"未知影响类型：{impact_kind!r}（取值域：{sorted(IMPACT_KINDS)}）")
    if impact_kind == IMPACT_EXECUTION_BLOCKING and link_count <= 0:
        raise ExceptionCaseError(
            "impact_kind=execution-blocking 必须至少登记一条受影响项"
            "（没有对应动作就没有可阻断的对象）"
        )


def assert_impact_change_is_explicit(
    *, current_impact_kind: str, requested_impact_kind: str, explicit_command: bool
) -> None:
    """**C3**：`impact_kind` 只能由**显式命令**变更。

    `severity` 的变更**不得**隐式改写阻断判据 —— 否则「把 critical 调成 low」就顺带
    解除了阻断。解除阻断只有两条合法路径：显式改 `impact_kind`，或把案件合法流转到终结态。

    Note:
        实现方式上，普通更新路径**根本不接受** `impact_kind` 字段；本函数是那道约束的
        可测形式（切片三的更新函数调用它），也是给未来「顺手加个字段」的人留的警示牌。
    """
    if current_impact_kind not in IMPACT_KINDS:
        raise ExceptionCaseError(f"未知影响类型：{current_impact_kind!r}")
    if requested_impact_kind not in IMPACT_KINDS:
        raise ExceptionCaseError(f"未知影响类型：{requested_impact_kind!r}")
    if requested_impact_kind != current_impact_kind and not explicit_command:
        raise ExceptionCaseError(
            "impact_kind 只能由显式命令变更；severity 的变更不得隐式改写阻断判据"
        )


# ── 关闭的其余前置（DR-0013 §3.5）────────────────────────────────────────────


def assert_closure_requirements(
    *,
    closure_disposition: str,
    severity: str,
    decision_note: str | None,
    resolution_note: str | None,
) -> None:
    """关闭的**字段级**前置校验（与 link 相关的部分需查库，见下一片）。

    | 处置 | 要求 |
    | --- | --- |
    | 任意 | `closure_disposition` 已在 `assert_transition` 校验非空且匹配状态 |
    | `resolved` | `resolution_note`（证据）非空 |
    | `accepted_residual` | `severity != 'critical'`（critical 按 C1 必然阻断，不能以残留关闭） |
    | | 且 `decision_note` 与 `resolution_note` 均非空 |
    | `cancelled` / `duplicate` / `superseded` | `decision_note` 非空（说清为什么这单本身不该存在） |
    """
    if closure_disposition not in DISPOSITIONS:
        raise ExceptionCaseError(
            f"未知处置：{closure_disposition!r}（取值域：{sorted(DISPOSITIONS)}）"
        )

    if closure_disposition == DISPOSITION_RESOLVED:
        if not (resolution_note or "").strip():
            raise ExceptionCaseError("以 resolved 关闭必须给出 resolution_note（处置证据）")
        return

    if closure_disposition == DISPOSITION_ACCEPTED_RESIDUAL:
        if severity == SEVERITY_CRITICAL:
            raise ExceptionCaseError(
                "critical 案件不能以 accepted_residual 关闭"
                "（critical 按 C1 必然阻断，必须先解决或显式解除阻断）"
            )
        if not (decision_note or "").strip() or not (resolution_note or "").strip():
            raise ExceptionCaseError(
                "以 accepted_residual 关闭必须同时给出 decision_note 与 resolution_note"
            )
        return

    # cancelled / duplicate / superseded：要能说清「为什么这单本身不该存在」。
    if not (decision_note or "").strip():
        raise ExceptionCaseError(
            f"以 {closure_disposition} 关闭必须给出 decision_note（需说明为什么这单本身不应存在）"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 下半区：持久化与服务层（DR-0013 A1 切片三）
#
# 四条贯穿全区的纪律：
#   1. **状态与事件同事务**（§3.6）—— 每个写函数只在最末尾 `commit()` 一次；
#      中途任何异常都 `rollback()` 后原样抛出。既不会「状态改了但没留痕」，
#      也不会「留痕了但状态没改」。
#   2. **乐观锁用条件 UPDATE，不是「先读后写」**——`WHERE id = :cid AND revision_no = :rev`，
#      rowcount 为 0 即 409。先读后写之间总有窗口。
#   3. **作用域不由客户端决定**（§3.1.4）—— `org_id` 一律取自 `assignment.org_id`；
#      请求带上不一致的 `org_id` → 403；受影响项跨委托 → 403。
#   4. **权限只有一条入口**——可见性复用 `authz.assert_can_view_assignment`，
#      动作权限复用 `access.assert_can`；本模块不自己拼一遍「货主或组织成员」。
# ═══════════════════════════════════════════════════════════════════════════════


class ExceptionCaseScopeError(Exception):
    """作用域违反（客户端指定 org / 受影响项跨委托 / 决定依据跨委托）→ HTTP 403。

    与上半区三条并列，但**只在下半区产生** —— 只有真正读了库，
    才可能发现「请求声明的组织与委托实际所属组织不一致」。
    """


#: 案件只能登记在**已受理**的委托上（受理前没有责任主体，案件无归属对象）。
#: 与 `tasks.ASSIGNMENT_ACTIVE_STATUS` **同值**，但此处**不 import `tasks`**：
#: `tasks.py` 要 import 本模块做执行门禁，反向 import 会成环。
ASSIGNMENT_ACTIVE_STATUS: Final = "claimed"

TARGET_TASK: Final = "task"
TARGET_ARTIFACT: Final = "artifact"
TARGET_KINDS: Final = frozenset({TARGET_TASK, TARGET_ARTIFACT})

#: 事件类型（§3.1.3）。`applied` / `applied_rejected` 属 A2 期，本片不产生。
EVENT_CREATED: Final = "created"
EVENT_STATUS_CHANGED: Final = "status_changed"
EVENT_DECIDED: Final = "decided"
EVENT_LINK_ADDED: Final = "link_added"
EVENT_LINK_REMOVED: Final = "link_removed"
EVENT_CLOSED: Final = "closed"
EVENT_REOPENED: Final = "reopened"

#: 对客投影**绝不允许**出现的字段（§3.2）。
#: 写成常量是为了让用例能直接断言 —— 比人读一遍函数体确认「好像删干净了」可靠。
CUSTOMER_EXCLUDED_FIELDS: Final = frozenset(
    {
        "assignment_id",
        "org_id",
        "cause",
        "severity",
        "impact_kind",
        "owner_user_id",
        "raised_by_user_id",
        "source",
        "due_at",
        "proposed_action",
        "decision_note",
        "decided_by",
        "decided_at",
        "basis_revision_id",
        "resolution_note",
        "closure_disposition",
        "closed_by",
        "closed_at",
        "revision_no",
        "blocking",
        "affected",
        "events",
    }
)

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

_CASE_FIELDS: Final = (
    "org_id",
    "assignment_id",
    "kind",
    "title",
    "cause",
    "severity",
    "impact_kind",
    "status",
    "owner_user_id",
    "raised_by_user_id",
    "source",
    "due_at",
    "proposed_action",
    "decision_note",
    "decided_by",
    "decided_at",
    "basis_revision_id",
    "resolution_note",
    "closure_disposition",
    "closed_by",
    "closed_at",
    "revision_no",
    "raised_at",
    "created_at",
    "updated_at",
)
_CASE_COLS = "id, " + ", ".join(_CASE_FIELDS)
_CASE_COLS_PREFIXED = ", ".join(f"c.{name}" for name in ("id", *_CASE_FIELDS))
_CASE_TIME_COLS: Final = (
    "due_at",
    "decided_at",
    "closed_at",
    "raised_at",
    "created_at",
    "updated_at",
)

_LINK_COLS = "id, exception_id, target_kind, target_id, applied_revision_id, created_at"
_EVENT_COLS = (
    "id, exception_id, seq, event_kind, from_status, to_status, actor_user_id, note, "
    "evidence_ref, basis_revision_id, payload_json, created_at"
)

#: 受影响项的取值域 → 表名。只有这两张表，**不放任任意 target**。
_TARGET_TABLE: Final = {TARGET_TASK: "ent_workflow_task", TARGET_ARTIFACT: "ent_artifact"}


# ── 时间与行转换（方言归一） ─────────────────────────────────────────────────


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FORMAT)


def _text_ts(raw: Any) -> str | None:
    """时间列 → 统一文本。

    SQLite 的 DATETIME 存 TEXT（取出来是 `str`），MySQL 由驱动转成 `datetime`；
    不归一，同一段代码会在开发库通过、在生产库报类型错 —— 这类「只在生产暴露」
    的缺陷必须在本层吸收。

    **有意再写一份**而不是 import：`tasks.py` 要 import 本模块做执行门禁，
    反向 import 会成环；`workbench.py` 的私有助手也不适合被跨模块引用。
    三处口径一致这件事由用例锁住（同一批数据在两种方言下渲染必须相同）。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _parse_ts(raw: Any) -> str | None:
    """时间入参规整为存储格式；`datetime` 直接格式化，字符串原样交给数据库校验。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _case_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    for col in _CASE_TIME_COLS:
        if col in data:
            data[col] = _text_ts(data[col])
    return data


def _link_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["created_at"] = _text_ts(data["created_at"])
    return data


def _event_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["created_at"] = _text_ts(data["created_at"])
    raw_payload = data.pop("payload_json", None)
    data["payload"] = None
    if isinstance(raw_payload, str) and raw_payload:
        try:
            parsed = json.loads(raw_payload)
        except ValueError:
            parsed = None
        data["payload"] = parsed if isinstance(parsed, dict) else None
    return data


# ── 读取 ─────────────────────────────────────────────────────────────────────


def get_case(session: Session, exception_id: int) -> dict[str, Any] | None:
    """按 id 取案件本体；不存在返回 `None`（**不判权限** —— 调用方负责）。"""
    row = (
        session.execute(
            text(f"SELECT {_CASE_COLS} FROM ent_exception WHERE id = :cid"),
            {"cid": exception_id},
        )
        .mappings()
        .first()
    )
    return _case_from_row(row) if row is not None else None


def list_links(session: Session, exception_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                f"SELECT {_LINK_COLS} FROM ent_exception_link "
                "WHERE exception_id = :cid ORDER BY id ASC"
            ),
            {"cid": exception_id},
        )
        .mappings()
        .all()
    )
    return [_link_from_row(row) for row in rows]


def list_events(session: Session, exception_id: int) -> list[dict[str, Any]]:
    """按 `seq` 升序返回事件（append-only，历史依次保留）。"""
    rows = (
        session.execute(
            text(
                f"SELECT {_EVENT_COLS} FROM ent_exception_event "
                "WHERE exception_id = :cid ORDER BY seq ASC"
            ),
            {"cid": exception_id},
        )
        .mappings()
        .all()
    )
    return [_event_from_row(row) for row in rows]


def list_links_for_cases(session: Session, case_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """一次取回多张案件的受影响项（列表端点用，避免逐条查询造成 N+1）。

    列表接口一条一条 load_links 也能跑对，但会让「翻一页 20 张案件」变成 21 次查询 ——
    这类 N+1 在数据少时看不出问题，等它变慢时已经很难归因。
    """
    if not case_ids:
        return {}
    placeholders = ", ".join(f":cid_{i}" for i in range(len(case_ids)))
    params = {f"cid_{i}": case_id for i, case_id in enumerate(case_ids)}
    rows = (
        session.execute(
            text(
                f"SELECT {_LINK_COLS} FROM ent_exception_link "
                f"WHERE exception_id IN ({placeholders}) ORDER BY id ASC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        link = _link_from_row(row)
        grouped.setdefault(int(link["exception_id"]), []).append(link)
    return grouped


def list_cases(
    session: Session,
    *,
    assignment_id: int,
    status: str | None = None,
    kind: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """按委托列案件。

    归属用**精确等值** `assignment_id = :aid`，不做任何「回退到货主或组织」的放宽：
    同一货主在同一组织下可能有多张委托，放宽一步就会把别的委托的案件
    显示在这张委托的工作台上（DR-0012 / 验证 11）。
    """
    if status is not None:
        _require_known_status(status)
    if kind is not None:
        _require_known_kind(kind)

    where = ["assignment_id = :aid"]
    params: dict[str, Any] = {"aid": assignment_id}
    if status is not None:
        where.append("status = :status")
        params["status"] = status
    if kind is not None:
        where.append("kind = :kind")
        params["kind"] = kind
    clause = " AND ".join(where)

    total = int(
        session.execute(text(f"SELECT COUNT(*) FROM ent_exception WHERE {clause}"), params).scalar()
        or 0
    )
    rows = (
        session.execute(
            text(
                f"SELECT {_CASE_COLS} FROM ent_exception WHERE {clause} "
                "ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
        .mappings()
        .all()
    )
    return total, [_case_from_row(row) for row in rows]


def count_open_cases(session: Session, *, assignment_id: int) -> int:
    """未关闭案件数（工作台计数用 —— `closed` 不算未决）。"""
    return int(
        session.execute(
            text(
                "SELECT COUNT(*) FROM ent_exception "
                "WHERE assignment_id = :aid AND status != :closed"
            ),
            {"aid": assignment_id, "closed": STATUS_CLOSED},
        ).scalar()
        or 0
    )


def _load_case_rows(
    session: Session, *, clause: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(f"SELECT {_CASE_COLS} FROM ent_exception WHERE {clause} ORDER BY id ASC"), params
        )
        .mappings()
        .all()
    )
    return [_case_from_row(row) for row in rows]


def _blocking_only(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用**同一个** `is_blocking` 过滤候选集。

    不把判据翻译成 SQL：`rejected` 在两种 kind 下语义相反，翻译就等于**第二份实现**，
    而两份实现迟早出现「执行命令认阻断、结案不认」。候选集与案件数同量级，
    Python 侧过滤不构成性能问题。
    """
    return [
        case
        for case in cases
        if is_blocking(
            kind=str(case["kind"]),
            impact_kind=str(case["impact_kind"]),
            status=str(case["status"]),
        )
    ]


def list_blocking_cases(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托下**当前阻断**的全部案件（结案检查与工作台共用同一判据）。"""
    candidates = _load_case_rows(
        session,
        clause="assignment_id = :aid AND status != :closed",
        params={"aid": assignment_id, "closed": STATUS_CLOSED},
    )
    return _blocking_only(candidates)


def blocking_cases_for_task(session: Session, *, task_id: int) -> list[dict[str, Any]]:
    """**阻断 `task_id` 这条执行命令**的案件（§6.1 验证 9 的唯一判据）。

    先按 link 命中取出候选（`target_kind='task'` 且 `target_id` 匹配，且案件未关闭），
    再用同一个 `is_blocking` 过滤。返回体带案件 id 与 kind/status，
    供端点「指出是哪几条」——**不能只说「有阻断项」**（§3.3 作用面 1）。
    """
    rows = (
        session.execute(
            text(
                f"SELECT {_CASE_COLS_PREFIXED} FROM ent_exception c "
                "JOIN ent_exception_link l ON l.exception_id = c.id "
                "WHERE l.target_kind = :tk AND l.target_id = :tid AND c.status != :closed "
                "ORDER BY c.id ASC"
            ),
            {"tk": TARGET_TASK, "tid": task_id, "closed": STATUS_CLOSED},
        )
        .mappings()
        .all()
    )
    return _blocking_only([_case_from_row(row) for row in rows])


# ── 授权（复用唯一入口，不重写第二份） ───────────────────────────────────────


def _assignment_or_404(session: Session, assignment_id: int) -> dict[str, Any]:
    """取委托；不存在**或尚未选定服务经营主体**都按不存在处理。

    `org_id IS NULL` 的草稿同样拒绝：没有作用域就没有可校验的边界，
    放行等于让案件挂在一个还不存在的委托关系上（与 `authz.load_assignment_for_entrustment`
    对草稿的处理一致）。
    """
    assignment = load_assignment(session, assignment_id)
    if assignment is None or assignment["org_id"] is None:
        raise ExceptionCaseNotFoundError(f"委托 {assignment_id} 不存在")
    return assignment


def _case_or_404(session: Session, exception_id: int) -> dict[str, Any]:
    case = get_case(session, exception_id)
    if case is None:
        raise ExceptionCaseNotFoundError(f"案件 {exception_id} 不存在")
    return case


def assert_can_view_case(session: Session, *, user_id: int, case: dict[str, Any]) -> None:
    """读可见性 —— **复用 `authz.assert_can_view_assignment`（同一份判据）**。

    非参与方拿到的就是 `authz` 抛的 404，**不区分「不存在」与「无权查看」**。
    刻意不在这里再写一遍「货主本人 or 组织成员」：本支线只有一条权限入口，
    多写一遍就多一处可以漂移的地方。
    """
    assignment = load_assignment(session, int(case["assignment_id"]))
    if assignment is None:
        raise ExceptionCaseNotFoundError(f"案件 {case['id']} 不存在")
    assert_can_view_assignment(session, user_id=user_id, assignment=assignment)


def load_visible_case(session: Session, *, exception_id: int, user_id: int) -> dict[str, Any]:
    case = _case_or_404(session, exception_id)
    assert_can_view_case(session, user_id=user_id, case=case)
    return case


def _assert_can_write(
    session: Session, *, actor_id: int, assignment: dict[str, Any]
) -> AccessContext:
    """写权限：组织成员（否则 **404**，不泄漏存在性）→ 动作权限（否则 403）。

    与 `tasks.create_task` 同一口径。权限取 `entrust:task:dispatch`（管理动作）：
    DR-0013 **没有**为案件新增权限码，而新增权限码要动
    `access.ORG_ROLE_PERMISSIONS`，等于顺带改变既有角色语义 ——
    超出本裁定范围（§3.9「不另建一套」）。`member` 角色（只有 `entrust:view`）
    因此可以读案件、不能改案件。
    """
    owner_user_id = int(assignment["owner_user_id"])
    org_id = int(assignment["org_id"])
    context = resolve_context(session, user_id=actor_id)
    if org_id not in context.org_ids:
        raise ExceptionCaseNotFoundError(f"委托 {assignment['id']} 不存在")
    return assert_can(
        session, user_id=actor_id, permission=PERM_TASK_DISPATCH, owner_user_id=owner_user_id
    )


# ── 归属与证据校验（§3.1.4） ─────────────────────────────────────────────────


def _assert_link_target(
    session: Session, *, assignment: dict[str, Any], target_kind: str, target_id: int
) -> None:
    """受影响项必须与案件**同属一张委托单**，否则 403。

    放宽一步就会让「甲单的异常」把「乙单的任务」卡住，而两单可能分属不同客户（验证 11）。
    归属为 NULL 的成果同样拒绝 —— 要先按 DR-0012 的流程把成果归属到本单，
    而不是靠案件去「认领」一份不属于任何委托的成果。
    """
    if target_kind not in TARGET_KINDS:
        raise ExceptionCaseError(
            f"未知受影响项类型：{target_kind!r}（取值域：{sorted(TARGET_KINDS)}）"
        )
    table = _TARGET_TABLE[target_kind]
    row = session.execute(
        text(f"SELECT assignment_id FROM {table} WHERE id = :tid"), {"tid": target_id}
    ).first()
    if row is None:
        raise ExceptionCaseNotFoundError(f"{target_kind} {target_id} 不存在")
    target_assignment = row[0]
    if target_assignment is None or int(target_assignment) != int(assignment["id"]):
        raise ExceptionCaseScopeError(
            f"{target_kind} {target_id} 不属于委托 {assignment['id']}，不能作为本案件的受影响项"
        )


def _assert_basis_revision(
    session: Session, *, assignment: dict[str, Any], revision_id: int
) -> None:
    """决定依据版本必须属于**本委托**的成果（§3.1.4「证据同样校验」）。"""
    row = session.execute(
        text(
            "SELECT a.assignment_id FROM ent_artifact_revision r "
            "JOIN ent_artifact a ON a.id = r.artifact_id WHERE r.id = :rid"
        ),
        {"rid": revision_id},
    ).first()
    if row is None:
        raise ExceptionCaseNotFoundError(f"成果版本 {revision_id} 不存在")
    if row[0] is None or int(row[0]) != int(assignment["id"]):
        raise ExceptionCaseScopeError(
            f"成果版本 {revision_id} 不属于委托 {assignment['id']}，不能作为决定依据"
        )


# ── 写入原语 ─────────────────────────────────────────────────────────────────


def _apply_case_update(
    session: Session,
    *,
    exception_id: int,
    expected_revision: int,
    sets: list[str],
    params: dict[str, Any],
    now: datetime,
) -> None:
    """条件 UPDATE（乐观锁）。rowcount 为 0 ⇒ `expected_revision` 过期 → 409。

    `sets` 可以为空 —— 表达式里始终追加 `revision_no = revision_no + 1`，
    因此「只追加事件、不改字段」的命令（如登记受影响项）同样推进版本，
    客户端手里的 `expected_revision` 会随之失效，不会拿着过期快照继续提交。
    """
    assignments = [*sets, "revision_no = revision_no + 1", "updated_at = :ts"]
    result = cast(
        CursorResult[Any],
        session.execute(
            text(
                f"UPDATE ent_exception SET {', '.join(assignments)} "
                "WHERE id = :cid AND revision_no = :rev"
            ),
            {"cid": exception_id, "rev": expected_revision, "ts": _fmt(now), **params},
        ),
    )
    if result.rowcount == 0:
        raise ExceptionCaseConflictError(
            f"案件 {exception_id} 的 expected_revision={expected_revision} 已过期"
            "（他人已修改，请重新读取后重试）"
        )


def _append_event(
    session: Session,
    *,
    exception_id: int,
    event_kind: str,
    actor_user_id: int,
    now: datetime,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    evidence_ref: str | None = None,
    basis_revision_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """追加一条案件事件（**append-only**：从不 UPDATE、从不 DELETE）。

    `seq` 取 `COALESCE(MAX(seq), 0) + 1`，在 MySQL 上受案件行锁保护 ——
    调用方总是先做过 `_apply_case_update`（拿到该行的排他锁）；
    `(exception_id, seq)` 唯一约束是并发下的最后一道防线，冲突即整笔回滚，
    不产生「半个状态」。
    """
    seq = session.execute(
        text("SELECT COALESCE(MAX(seq), 0) + 1 FROM ent_exception_event WHERE exception_id = :cid"),
        {"cid": exception_id},
    ).scalar()
    session.execute(
        text(
            "INSERT INTO ent_exception_event "
            "(exception_id, seq, event_kind, from_status, to_status, actor_user_id, note, "
            " evidence_ref, basis_revision_id, payload_json, created_at) "
            "VALUES (:cid, :seq, :kind, :frm, :to, :actor, :note, :ev, :basis, :payload, :ts)"
        ),
        {
            "cid": exception_id,
            "seq": int(seq or 1),
            "kind": event_kind,
            "frm": from_status,
            "to": to_status,
            "actor": actor_user_id,
            "note": note,
            "ev": evidence_ref,
            "basis": basis_revision_id,
            "payload": json.dumps(payload, ensure_ascii=False) if payload else None,
            "ts": _fmt(now),
        },
    )


def _insert_link(
    session: Session, *, exception_id: int, target_kind: str, target_id: int, now: datetime
) -> int:
    result = cast(
        CursorResult[Any],
        session.execute(
            text(
                "INSERT INTO ent_exception_link "
                "(exception_id, target_kind, target_id, applied_revision_id, created_at) "
                "VALUES (:cid, :tk, :tid, NULL, :ts)"
            ),
            {"cid": exception_id, "tk": target_kind, "tid": target_id, "ts": _fmt(now)},
        ),
    )
    return int(result.lastrowid or 0)


def _case_with_links(session: Session, exception_id: int) -> dict[str, Any]:
    """写命令的统一返回体：案件本体 + 当前受影响项（`links` 键）。"""
    case = _case_or_404(session, exception_id)
    case["links"] = list_links(session, exception_id)
    return case


# ── 写命令 ───────────────────────────────────────────────────────────────────


def raise_case(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    kind: str,
    title: str,
    severity: str,
    impact_kind: str,
    cause: str | None = None,
    owner_user_id: int | None = None,
    source: str = "manual",
    due_at: Any = None,
    proposed_action: str | None = None,
    links: list[dict[str, Any]] | None = None,
    request_org_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """登记一个案件（异常或变更请求）。

    五道校验，缺一不可：

    * 委托必须**已受理**（`claimed`）—— 受理前没有责任主体，案件没有归属对象；
    * `org_id` **由服务端派生**；请求带上不一致的值 → **403**（§3.1.4）；
    * 权限（组织成员 → 404；动作权限 → 403）；
    * **C1**（`critical` ⇒ `execution-blocking`）—— 创建与更新都校验；
    * **C2**（`execution-blocking` ⇒ 至少一条受影响项）—— 因此 `links` 允许**随案件一起登记**，
      否则「先建阻断案件、再补受影响项」的中间态本身违反 C2，只能靠绕过校验实现。

    受影响项与案件同事务写入，任一条非法则整笔回滚 —— 不会留下「案件建了、link 少一条」
    这种让 C2 名存实亡的状态。
    """
    assignment = _assignment_or_404(session, assignment_id)
    if str(assignment["status"]) != ASSIGNMENT_ACTIVE_STATUS:
        raise ExceptionCaseConflictError(
            f"委托状态为 {assignment['status']}，只有已受理（{ASSIGNMENT_ACTIVE_STATUS}）"
            "的委托才能登记异常或变更案件"
        )
    org_id = int(assignment["org_id"])
    if request_org_id is not None and int(request_org_id) != org_id:
        raise ExceptionCaseScopeError(
            f"请求的 org_id={request_org_id} 与委托所属组织 {org_id} 不一致"
            "（作用域由服务端派生，不接受客户端指定）"
        )
    _assert_can_write(session, actor_id=actor_id, assignment=assignment)

    _require_known_kind(kind)
    cleaned_title = (title or "").strip()
    if not cleaned_title:
        raise ExceptionCaseError("案件必须有一句话摘要（title）")
    if source not in SOURCES:
        raise ExceptionCaseError(f"未知来源：{source!r}（取值域：{sorted(SOURCES)}）")
    assert_severity_impact_consistent(severity=severity, impact_kind=impact_kind)

    prepared: list[tuple[str, int]] = []
    for item in links or []:
        target_kind = str(item.get("target_kind", ""))
        target_id = int(item.get("target_id", 0))
        _assert_link_target(
            session, assignment=assignment, target_kind=target_kind, target_id=target_id
        )
        prepared.append((target_kind, target_id))
    assert_blocking_requires_link(impact_kind=impact_kind, link_count=len(prepared))

    current = now or utcnow_naive()
    try:
        insert = cast(
            CursorResult[Any],
            session.execute(
                text(
                    "INSERT INTO ent_exception "
                    "(org_id, assignment_id, kind, title, cause, severity, impact_kind, status, "
                    " owner_user_id, raised_by_user_id, source, due_at, proposed_action, "
                    " decision_note, decided_by, decided_at, basis_revision_id, resolution_note, "
                    " closure_disposition, closed_by, closed_at, revision_no, raised_at, "
                    " created_at, updated_at) "
                    "VALUES (:org, :aid, :kind, :title, :cause, :severity, :impact, :status, "
                    " :owner, :raiser, :source, :due, :proposed, NULL, NULL, NULL, NULL, NULL, "
                    " NULL, NULL, NULL, 1, :raised, :ts, :ts)"
                ),
                {
                    "org": org_id,
                    "aid": assignment_id,
                    "kind": kind,
                    "title": cleaned_title,
                    "cause": cause,
                    "severity": severity,
                    "impact": impact_kind,
                    "status": STATUS_OPEN,
                    "owner": owner_user_id,
                    "raiser": actor_id,
                    "source": source,
                    "due": _parse_ts(due_at),
                    "proposed": proposed_action,
                    "raised": _parse_ts(current),
                    "ts": _fmt(current),
                },
            ),
        )
        case_id = int(insert.lastrowid or 0)
        _append_event(
            session,
            exception_id=case_id,
            event_kind=EVENT_CREATED,
            actor_user_id=actor_id,
            now=current,
            to_status=STATUS_OPEN,
            payload={
                "kind": kind,
                "source": source,
                "severity": severity,
                "impact_kind": impact_kind,
            },
        )
        for target_kind, target_id in prepared:
            link_id = _insert_link(
                session,
                exception_id=case_id,
                target_kind=target_kind,
                target_id=target_id,
                now=current,
            )
            _append_event(
                session,
                exception_id=case_id,
                event_kind=EVENT_LINK_ADDED,
                actor_user_id=actor_id,
                now=current,
                payload={
                    "link_id": link_id,
                    "target_kind": target_kind,
                    "target_id": target_id,
                },
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _case_with_links(session, case_id)


def add_link(
    session: Session,
    *,
    exception_id: int,
    actor_id: int,
    target_kind: str,
    target_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """登记一条受影响项（§5.5 affected tasks/artifacts、§7 affected records）。

    已关闭的案件不能再登记 —— `closed` 只允许回到 `open`，而那是 `reopen` 的职责
    （要带 reason 并留审计）。重复登记由唯一约束与显式预检共同挡住，
    预检是为了一句能看懂的 409，约束是并发下的兜底。
    """
    case = _case_or_404(session, exception_id)
    assignment = _assignment_or_404(session, int(case["assignment_id"]))
    _assert_can_write(session, actor_id=actor_id, assignment=assignment)
    if str(case["status"]) == STATUS_CLOSED:
        raise ExceptionCaseConflictError(
            f"案件 {exception_id} 已关闭，不能再登记受影响项（需继续处理请先 reopen）"
        )
    _assert_link_target(
        session, assignment=assignment, target_kind=target_kind, target_id=target_id
    )
    duplicate = session.execute(
        text(
            "SELECT id FROM ent_exception_link "
            "WHERE exception_id = :cid AND target_kind = :tk AND target_id = :tid"
        ),
        {"cid": exception_id, "tk": target_kind, "tid": target_id},
    ).first()
    if duplicate is not None:
        raise ExceptionCaseConflictError(
            f"{target_kind} {target_id} 已登记为该案件的受影响项（同一项不重复登记）"
        )

    current = now or utcnow_naive()
    try:
        link_id = _insert_link(
            session,
            exception_id=exception_id,
            target_kind=target_kind,
            target_id=target_id,
            now=current,
        )
        _apply_case_update(
            session,
            exception_id=exception_id,
            expected_revision=expected_revision,
            sets=[],
            params={},
            now=current,
        )
        _append_event(
            session,
            exception_id=exception_id,
            event_kind=EVENT_LINK_ADDED,
            actor_user_id=actor_id,
            now=current,
            payload={"link_id": link_id, "target_kind": target_kind, "target_id": target_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _case_with_links(session, exception_id)


def remove_link(
    session: Session,
    *,
    exception_id: int,
    link_id: int,
    actor_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """移除一条受影响项。

    为什么 A1 就要有「移除」：登记错了却无法更正，会让 `resolved` **永久无法成立**
    —— `resolved` 要求每条受影响项都已写入 `applied_revision_id`，
    而一条根本不该存在的项永远不会有。那会逼着人去直接改库，反而绕过全部校验。

    移除后**重新校验 C2**：移掉最后一条会让一道 `execution-blocking` 变成空转阻断
    （没有「corresponding action」可阻断）。要这么做必须先显式改 `impact_kind`。
    """
    case = _case_or_404(session, exception_id)
    assignment = _assignment_or_404(session, int(case["assignment_id"]))
    _assert_can_write(session, actor_id=actor_id, assignment=assignment)
    if str(case["status"]) == STATUS_CLOSED:
        raise ExceptionCaseConflictError(
            f"案件 {exception_id} 已关闭，不能改动受影响项（需继续处理请先 reopen）"
        )
    row = (
        session.execute(
            text(
                f"SELECT {_LINK_COLS} FROM ent_exception_link "
                "WHERE id = :lid AND exception_id = :cid"
            ),
            {"lid": link_id, "cid": exception_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ExceptionCaseNotFoundError(f"受影响项 {link_id} 不存在")
    link = _link_from_row(row)
    remaining = len(list_links(session, exception_id)) - 1
    assert_blocking_requires_link(impact_kind=str(case["impact_kind"]), link_count=remaining)

    current = now or utcnow_naive()
    try:
        session.execute(text("DELETE FROM ent_exception_link WHERE id = :lid"), {"lid": link_id})
        _apply_case_update(
            session,
            exception_id=exception_id,
            expected_revision=expected_revision,
            sets=[],
            params={},
            now=current,
        )
        _append_event(
            session,
            exception_id=exception_id,
            event_kind=EVENT_LINK_REMOVED,
            actor_user_id=actor_id,
            now=current,
            payload={
                "link_id": link_id,
                "target_kind": str(link["target_kind"]),
                "target_id": int(link["target_id"]),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _case_with_links(session, exception_id)


def decide(
    session: Session,
    *,
    exception_id: int,
    actor_id: int,
    to_status: str,
    expected_revision: int,
    decision_note: str | None = None,
    basis_revision_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """记录决定（`approved` / `rejected`，以及退回补充类的状态变更）。

    * 转移合法性由**两套**状态机判定（`assert_transition`）—— 同一个 `rejected`
      在两种 kind 下语义相反，而**转移表本身不同**这件事在这里自动生效；
    * `approved` **必须**给出 `basis_revision_id`（§3.1.1「approved 后必填」）：
      决定必须指向它所依据的**精确**成果版本，否则 §3.7 的「旧批准不得直接应用到新内容」
      无从校验；
    * `rejected` **不强制** `basis_revision_id`。这是对 §3.1.3「`decided` 事件必填 basis」
      的**有意收窄**，依据是 §3.1.1 更具体的那句：一条还没有任何成果引用的案件
      （例如「缺一份提单」这类异常）也必须能被驳回，强制 basis 会让驳回在无成果场景下不可用；
    * **C3**：本函数**不接受** `impact_kind`。状态与严重度的变化都不得隐式改写阻断判据 ——
      解除阻断只能显式改 `impact_kind`，或把案件合法流转到终结态。
    """
    case = _case_or_404(session, exception_id)
    assignment = _assignment_or_404(session, int(case["assignment_id"]))
    _assert_can_write(session, actor_id=actor_id, assignment=assignment)
    if to_status == STATUS_CLOSED:
        raise ExceptionCaseConflictError(
            "关闭案件请走 close 命令（必须给出处置与证据），不能借 decide 关闭"
        )
    kind = str(case["kind"])
    from_status = str(case["status"])
    assert_transition(kind=kind, from_status=from_status, to_status=to_status)
    if to_status == STATUS_APPROVED and basis_revision_id is None:
        raise ExceptionCaseError("approved 必须给出 basis_revision_id（决定所依据的成果版本）")
    if basis_revision_id is not None:
        _assert_basis_revision(session, assignment=assignment, revision_id=basis_revision_id)

    current = now or utcnow_naive()
    sets = ["status = :to", "decided_by = :actor", "decided_at = :ts"]
    params: dict[str, Any] = {"to": to_status, "actor": actor_id}
    if decision_note is not None:
        sets.append("decision_note = :note")
        params["note"] = decision_note
    if basis_revision_id is not None:
        sets.append("basis_revision_id = :basis")
        params["basis"] = basis_revision_id

    decided = to_status in (STATUS_APPROVED, STATUS_REJECTED)
    try:
        _apply_case_update(
            session,
            exception_id=exception_id,
            expected_revision=expected_revision,
            sets=sets,
            params=params,
            now=current,
        )
        _append_event(
            session,
            exception_id=exception_id,
            event_kind=EVENT_DECIDED if decided else EVENT_STATUS_CHANGED,
            actor_user_id=actor_id,
            now=current,
            from_status=from_status,
            to_status=to_status,
            note=decision_note,
            basis_revision_id=basis_revision_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _case_with_links(session, exception_id)


def close_case(
    session: Session,
    *,
    exception_id: int,
    actor_id: int,
    closure_disposition: str,
    expected_revision: int,
    evidence_ref: str,
    decision_note: str | None = None,
    resolution_note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """关闭案件 —— **必须回答「凭什么关」**，没有一键关闭。

    三层校验，各管一件事：

    1. **转移与处置组合**（`assert_transition`）：`exception` 从 `rejected` / `approved`
       关闭时**不含** `resolved` / `accepted_residual` —— 这就是 HO「不允许通过驳回
       处置方案解除真实异常」的落地；
    2. **字段级要求**（`assert_closure_requirements`）：用**本次提供的或既有的**值判定，
       因为「关闭」与「补写说明」常在同一次调用里完成；
    3. **受影响项级要求**：`resolved` 要求**每条** link 都已写入 `applied_revision_id`
       （确实应用过），否则 409。

    `evidence_ref` 必填：§3.1.3 要求 `closed` 事件携带证据引用。
    """
    case = _case_or_404(session, exception_id)
    assignment = _assignment_or_404(session, int(case["assignment_id"]))
    _assert_can_write(session, actor_id=actor_id, assignment=assignment)
    kind = str(case["kind"])
    from_status = str(case["status"])
    assert_transition(
        kind=kind,
        from_status=from_status,
        to_status=STATUS_CLOSED,
        closure_disposition=closure_disposition,
    )
    effective_decision_note = decision_note if decision_note is not None else case["decision_note"]
    effective_resolution_note = (
        resolution_note if resolution_note is not None else case["resolution_note"]
    )
    assert_closure_requirements(
        closure_disposition=closure_disposition,
        severity=str(case["severity"]),
        decision_note=effective_decision_note,
        resolution_note=effective_resolution_note,
    )
    cleaned_evidence = (evidence_ref or "").strip()
    if not cleaned_evidence:
        raise ExceptionCaseError("关闭案件必须给出 evidence_ref（关闭证据引用）")
    if closure_disposition == DISPOSITION_RESOLVED:
        # 只对**成果**受影响项要求 applied_revision_id。
        #
        # §3.5 的字面是「每条 link 的 applied_revision_id 已写入」，但该列在 §3.1.2 里
        # 的定义是「该受影响**成果**被应用到的精确版本」—— 任务根本没有版本号。
        # 对任务 link 也要求它，等于把 resolved 关成**死局**（永远无人能写入，
        # `applied` 之后就永远关不掉），那不是控制变严，而是把一条合法路径堵死。
        # 任务侧的「确实做过」由任务自身状态机保证（`complete_task` + 执行门禁），
        # 不在这里重复一遍、也不用一个语义不匹配的列去表示。
        pending = [
            item
            for item in list_links(session, exception_id)
            if str(item["target_kind"]) == TARGET_ARTIFACT and item["applied_revision_id"] is None
        ]
        if pending:
            targets = [f"{p['target_kind']}#{p['target_id']}" for p in pending]
            raise ExceptionCaseConflictError(
                f"以 resolved 关闭要求每条受影响成果都已写入 applied_revision_id（尚未应用：{targets}）"
            )

    current = now or utcnow_naive()
    sets = [
        "status = :to",
        "closure_disposition = :disp",
        "closed_by = :actor",
        "closed_at = :ts",
    ]
    params: dict[str, Any] = {
        "to": STATUS_CLOSED,
        "disp": closure_disposition,
        "actor": actor_id,
    }
    if decision_note is not None:
        sets.append("decision_note = :note")
        params["note"] = decision_note
    if resolution_note is not None:
        sets.append("resolution_note = :resnote")
        params["resnote"] = resolution_note
    try:
        _apply_case_update(
            session,
            exception_id=exception_id,
            expected_revision=expected_revision,
            sets=sets,
            params=params,
            now=current,
        )
        _append_event(
            session,
            exception_id=exception_id,
            event_kind=EVENT_CLOSED,
            actor_user_id=actor_id,
            now=current,
            from_status=from_status,
            to_status=STATUS_CLOSED,
            note=effective_resolution_note,
            evidence_ref=cleaned_evidence,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _case_with_links(session, exception_id)


def reopen_case(
    session: Session,
    *,
    exception_id: int,
    actor_id: int,
    reason: str,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """重开已关闭案件（`closed → open`）。

    **硬约束（§3.6 / HO 第 4 条）**：状态更新与审计追加在**同一事务**内完成。
    本函数只有一个 `session.commit()`，且它在 `_append_event` 之后 ——
    事件写失败则状态一并回滚。`reason` 落事件的 `note`（必填），
    `closed_by` / `closed_at` / `closure_disposition` 一并清空
    （否则「重开后再关闭」会带着上一轮的处置，历史看起来像被改写）。
    事件行只追加，从不 UPDATE ⇒ 两轮关闭的历史完整可判定（验证 7）。
    """
    case = _case_or_404(session, exception_id)
    assignment = _assignment_or_404(session, int(case["assignment_id"]))
    _assert_can_write(session, actor_id=actor_id, assignment=assignment)
    reason_clean = (reason or "").strip()
    if not reason_clean:
        raise ExceptionCaseError("重开必须给出原因（落审计事件的 note）")
    assert_transition(
        kind=str(case["kind"]), from_status=str(case["status"]), to_status=STATUS_OPEN
    )

    current = now or utcnow_naive()
    try:
        _apply_case_update(
            session,
            exception_id=exception_id,
            expected_revision=expected_revision,
            sets=[
                "status = :to",
                "closure_disposition = NULL",
                "closed_by = NULL",
                "closed_at = NULL",
            ],
            params={"to": STATUS_OPEN},
            now=current,
        )
        _append_event(
            session,
            exception_id=exception_id,
            event_kind=EVENT_REOPENED,
            actor_user_id=actor_id,
            now=current,
            from_status=STATUS_CLOSED,
            to_status=STATUS_OPEN,
            note=reason_clean,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _case_with_links(session, exception_id)


# ── 两套**独立**白名单投影（§3.2） ───────────────────────────────────────────


def project_case_internal(
    case: dict[str, Any], *, links: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """**内部**投影（工作台 `exceptions` 槽 / UI-08）。

    产出 §4.3 的四类内容（open cases / blocking impacts / proposed changes /
    applied revisions）与 §7 的 owner / decision / implementation & resolution evidence。

    与 `project_case_for_customer` 是**两套独立白名单**：它们服务的可见性集合本来就不同
    （案件有「客户自提变更请求」这一窄口，成果有 `CUSTOMER_VISIBLE_TYPES`）。
    硬塞进同一函数，会让「异常一律不得对客」退化成函数内的一个特殊分支，
    此后任何一次「顺手放宽」都会**同时**放宽两边。
    """
    link_list = links if links is not None else list(case.get("links") or [])
    return {
        "case_id": int(case["id"]),
        "assignment_id": int(case["assignment_id"]),
        "org_id": int(case["org_id"]),
        "kind": str(case["kind"]),
        "title": str(case["title"]),
        "cause": case["cause"],
        "severity": str(case["severity"]),
        "impact_kind": str(case["impact_kind"]),
        "status": str(case["status"]),
        "owner_user_id": case["owner_user_id"],
        "raised_by_user_id": int(case["raised_by_user_id"]),
        "source": str(case["source"]),
        "raised_at": case["raised_at"],
        "due_at": case["due_at"],
        "proposed_action": case["proposed_action"],
        "decision": {
            "note": case["decision_note"],
            "by": case["decided_by"],
            "at": case["decided_at"],
            "basis_revision_id": case["basis_revision_id"],
        },
        "resolution": {"note": case["resolution_note"]},
        "closure": {
            "disposition": case["closure_disposition"],
            "by": case["closed_by"],
            "at": case["closed_at"],
        },
        "blocking": is_blocking(
            kind=str(case["kind"]),
            impact_kind=str(case["impact_kind"]),
            status=str(case["status"]),
        ),
        "affected": [
            {
                "link_id": int(item["id"]),
                "target_kind": str(item["target_kind"]),
                "target_id": int(item["target_id"]),
                "applied_revision_id": item["applied_revision_id"],
            }
            for item in link_list
        ],
        "revision_no": int(case["revision_no"]),
        "created_at": case["created_at"],
        "updated_at": case["updated_at"],
    }


def project_case_for_customer(
    case: dict[str, Any], *, links: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """**对客**投影：只暴露**客户自己提出**的变更请求单，其余一律返回 `None`。

    三个「不」：

    * 不是 `change_request` → `None`。**异常一律不得对客** —— 不看严重度、不看状态；
    * `source != 'customer'` → `None`。内部来源的单据对客不可见；
    * 成果引用只保留 `CUSTOMER_VISIBLE_TYPES` 内的类型 —— 与 `ent_artifact` 侧的白名单
      **双层冗余**，是刻意的。

    返回体**只含** `case_id` / `title` / `status` / `raised_at` / `artifacts`。
    绝不允许出现的字段列在 `CUSTOMER_EXCLUDED_FIELDS`，由用例直接断言 ——
    比人读一遍函数体确认「好像删干净了」可靠。

    `links` 的每一项可带 `artifact_type`（由调用方从成果表带出）；
    缺失即视为不可对客，不会因为「没查到类型」而放行。
    """
    if str(case["kind"]) != KIND_CHANGE_REQUEST or str(case["source"]) != "customer":
        return None
    artifacts = [
        {"artifact_id": int(item["target_id"])}
        for item in links
        if str(item["target_kind"]) == TARGET_ARTIFACT
        and str(item.get("artifact_type") or "") in CUSTOMER_VISIBLE_TYPES
    ]
    return {
        "case_id": int(case["id"]),
        "title": str(case["title"]),
        "status": str(case["status"]),
        "raised_at": case["raised_at"],
        "artifacts": artifacts,
    }


__all__ = [
    "ASSIGNMENT_ACTIVE_STATUS",
    "CUSTOMER_EXCLUDED_FIELDS",
    "DISPOSITIONS",
    "DISPOSITION_ACCEPTED_RESIDUAL",
    "DISPOSITION_CANCELLED",
    "DISPOSITION_DUPLICATE",
    "DISPOSITION_RESOLVED",
    "DISPOSITION_SUPERSEDED",
    "EVENT_CLOSED",
    "EVENT_CREATED",
    "EVENT_DECIDED",
    "EVENT_LINK_ADDED",
    "EVENT_LINK_REMOVED",
    "EVENT_REOPENED",
    "EVENT_STATUS_CHANGED",
    "IMPACT_EXECUTION_BLOCKING",
    "IMPACT_INFORMATIONAL",
    "IMPACT_KINDS",
    "IMPACT_REVIEW_REQUIRED",
    "KINDS",
    "KIND_CHANGE_REQUEST",
    "KIND_EXCEPTION",
    "SEVERITIES",
    "SEVERITY_CRITICAL",
    "SOURCES",
    "STATUSES",
    "STATUS_APPLIED",
    "STATUS_APPROVED",
    "STATUS_CLOSED",
    "STATUS_IN_REVIEW",
    "STATUS_OPEN",
    "STATUS_REJECTED",
    "TARGET_ARTIFACT",
    "TARGET_KINDS",
    "TARGET_TASK",
    "ExceptionCaseConflictError",
    "ExceptionCaseError",
    "ExceptionCaseNotFoundError",
    "ExceptionCaseScopeError",
    "add_link",
    "allowed_closure_dispositions",
    "allowed_transitions",
    "assert_blocking_requires_link",
    "assert_can_view_case",
    "assert_closure_requirements",
    "assert_impact_change_is_explicit",
    "assert_severity_impact_consistent",
    "assert_transition",
    "blocking_cases_for_task",
    "close_case",
    "count_open_cases",
    "decide",
    "get_case",
    "is_blocking",
    "list_blocking_cases",
    "list_cases",
    "list_events",
    "list_links",
    "list_links_for_cases",
    "load_visible_case",
    "project_case_for_customer",
    "project_case_internal",
    "raise_case",
    "remove_link",
    "reopen_case",
]
