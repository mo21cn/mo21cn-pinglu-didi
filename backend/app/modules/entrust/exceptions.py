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
from decimal import Decimal, InvalidOperation
from typing import Any, Final, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust.access import (
    PERM_TASK_DISPATCH,
    AccessContext,
    AccessDeniedError,
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


class AssignmentQuantityStaleError(ExceptionCaseConflictError):
    """**值级**过期：批准时记的货量与库里现在的不一致（→ 409）。

    与版本级过期（`stale_basis`）分开成一类，是因为它们要人做的事不同：
    版本变了通常意味着"下游有别的变更"，值变了则意味着"有人绕过了本命令直接改了数"。
    合成一句话会让人去查错方向。

    `audit_reason` 会落进 `applied_rejected` 事件的 `reason` —— 审计链上
    "为什么没应用成功"必须能一眼看出来，而不是只有一句"应用失败"。
    """

    audit_reason = "stale_quantity"


class AssignmentQuantityConflictError(ExceptionCaseConflictError):
    """**并发**冲突：条件更新的受影响行数为 0，说明该委托的版本在本次事务内被别人推进了（→ 409）。

    条件更新（`WHERE id=… AND revision=:base`）是这条命令的**唯一**并发闸门：
    先读后判再写在这里不安全 —— 两个应用同时读到同一个 `revision`，
    两边都会"检查通过"，而只有一个能改成功。
    """

    audit_reason = "concurrent_quantity_change"


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


def _require_known_change_category(*, kind: str, change_category: str | None) -> None:
    """变更类别校验（A2 五之二 / DR-0016）。

    **不在这里要求必填**：类别是"应用变更"时才必须有的东西（应用要按它定复核范围），
    登记时可能还没想清 —— 那时强迫填会逼出一个**猜的值**，而猜错的类别会让复核范围
    整表选错，且失败是静默的（任务照样生成，只是复查了不相干的对象）。
    必填检查放在 `apply_case`，那里缺了就直接拒绝应用。

    **异常案件不允许有类别**：DR-0016 管的是"业务变更"，给真实异常套一个变更类别
    会让复核范围凭空出现一批没有依据的任务。
    """
    if change_category is None:
        return
    from app.modules.entrust import revalidation as rv  # 局部导入：避免与 tasks 成环

    if kind != KIND_CHANGE_REQUEST:
        raise ExceptionCaseError(
            f"只有变更请求（{KIND_CHANGE_REQUEST}）有变更类别，{kind} 案件请勿填写 change_category"
        )
    if change_category not in rv.CHANGE_CATEGORIES:
        raise ExceptionCaseError(
            f"未知变更类别：{change_category!r}（取值域：{sorted(rv.CHANGE_CATEGORIES)}）"
        )


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
#: **委托单本身**也可以是被影响项（2026-09-18 新增）。
#:
#: 为什么必须有这一种：货量（`ent_assignment.quantity`）是容量判定的**需求量唯一出处**
#: （`capacity._assignment_demand` 就指着这一列），而它此前**没有任何命令可以改** ——
#: 已受理（`claimed`）的委托走 `PATCH /assignments/{id}` 会被 `update_draft` 的
#: draft-only 检查挡成 409，变更应用的 `TARGET_KINDS` 又只认成果与任务。
#: ⇒ D1-09 的「800 → 950」只能靠**直接改库**驱动，而手改出来的与用户点出来的
#: **不是同一条路**：前者证明不了审批、原子性、留痕与复核传播。
TARGET_ASSIGNMENT: Final = "assignment"
TARGET_KINDS: Final = frozenset({TARGET_TASK, TARGET_ARTIFACT, TARGET_ASSIGNMENT})

#: 事件类型（§3.1.3）。`applied` / `applied_rejected` 由 **A2 五之一**产生（ENT-033）。
EVENT_CREATED: Final = "created"
EVENT_STATUS_CHANGED: Final = "status_changed"
EVENT_DECIDED: Final = "decided"
EVENT_LINK_ADDED: Final = "link_added"
EVENT_LINK_REMOVED: Final = "link_removed"
EVENT_CLOSED: Final = "closed"
EVENT_REOPENED: Final = "reopened"
EVENT_APPLIED: Final = "applied"
EVENT_APPLIED_REJECTED: Final = "applied_rejected"
#: 变更传播计划（A2 五之二 / 验证 19）：把「哪些成果进入待复核、生成哪些复核任务」
#: 记为审计事实。**与事件表其余条目同事务** —— 传播是变更的一部分，不是附注。
EVENT_REVALIDATION_PLANNED: Final = "revalidation_planned"

#: 「应用变更」是否对**正式业务**开放（HO 2026-09-15 裁决）。
#:
#: 五之一可以先合并，但业务开放必须等五之二（复核传播）接通：否则变更已经生效，
#: 下游报价与合同却收不到失效提示 —— 那不是"少一个提示"，是**静默交付错误结果**。
#: 写成一个具名常量（而不是散在代码里的 `False`），是为了让"何时开放"这件事
#: 有**唯一一个**可翻的点，也能被用例直接钉住。
#:
#: **2026-09-15 已翻 `True`（ENT-041 / 五之四）** —— 三个前置逐条核对过，缺一不可：
#: ① 五之二传播已接通（`apply_case` 同事务生成标记与复核任务，验证 19 通过）；
#: ② AC-12 后半条已落地（`confirm_revision` 有未完成复核项时 409，ENT-040）；
#: ③ 界面与入口已具备（案件页「应用变更」区 + 成果页「待复核」徽标，五之四）。
#: ⚠️ 翻它**不等于**"应用变更这件事已经验收"：S3 的结案校验（验证 20/10）仍未上线。
APPLY_OPEN: Final = True

#: **批准快照**（A2 五之一前提 P4，HO 2026-09-15 裁决）。
#:
#: 为什么必须有它：`case.basis_revision_id` 是**案件级单个**值，既覆盖不了
#: 「同时修改采购确认和对客报价」这类**多目标**批准范围，也回答不了
#: 「这个版本**现在**还是不是批准时的那个」。`_assert_basis_revision` 只校验
#: 「版本存在且属于本委托」，**不做过期版本检查**（DR-0013 §3.1.4 的归属校验
#: 与 A2 的版本新鲜度是两件事，不能互相替代）。
#:
#: 快照存在 `decided` 事件的 `payload_json` 里（不新增表）：事件**已经**与状态同事务、
#: 已经 append-only、已经带 actor 与时间，是「批准那一瞬间」最天然的载体。
APPROVAL_SNAPSHOT_KIND: Final = "approval_snapshot"
APPROVAL_SNAPSHOT_VERSION: Final = 1


#: 快照里 `changes` 的键格式：`"{target_kind}#{target_id}"`。
def _change_key(target_kind: str, target_id: int) -> str:
    return f"{target_kind}#{target_id}"


def _artifact_current_revision(session: Session, artifact_id: int) -> int | None:
    row = session.execute(
        text("SELECT current_revision_id FROM ent_artifact WHERE id = :aid"),
        {"aid": artifact_id},
    ).first()
    return None if row is None or row[0] is None else int(row[0])


def _task_current_revision(session: Session, task_id: int) -> int | None:
    """任务自己的**版本依据**（P4-A5）。

    任务没有成果版本，**不得**为满足字段要求而虚构一份成果 —— 它的依据就是
    任务行上的 `revision`（与 `complete_task` 的乐观锁同一个值）。
    """
    row = session.execute(
        text("SELECT revision FROM ent_workflow_task WHERE id = :tid"), {"tid": task_id}
    ).first()
    return None if row is None or row[0] is None else int(row[0])


def _assignment_current_revision(session: Session, assignment_id: int) -> int | None:
    """委托单的**当前**乐观锁值（`ent_assignment.revision`）。

    它与「货量变更」是同一个版本号：应用走
    `UPDATE ... SET quantity=…, revision=revision+1 WHERE id=… AND revision=:expected`，
    因此"版本变了"与"货量变了"在委托上是**同一件事**，不需要第三个字段来同步。
    """
    row = session.execute(
        text("SELECT revision FROM ent_assignment WHERE id = :aid"), {"aid": assignment_id}
    ).first()
    return None if row is None or row[0] is None else int(row[0])


def _target_basis_revision(session: Session, *, target_kind: str, target_id: int) -> int | None:
    """某个受影响项的**当前**版本依据 —— 唯一出处，构造快照与应用共用同一份。

    三者"版本"的语义不同，但都必须指向**能证明它没变**的那个值：
    成果取 `current_revision_id`；任务取任务自己的 `revision`（P4-A5：不为字段要求虚构成果版本）；
    委托单取 `ent_assignment.revision`。

    ⚠️ 共用同一个函数是硬要求：批准时认的版本与应用时认的版本若各写一份，
    「依据版本已变化」这条判定在两侧就可能给出**不同结论** —— 那是裁决分歧，
    症状是"能批准的用不了 / 用得了的没批准"，而不是一个会报错的 bug。
    """
    if target_kind == TARGET_ARTIFACT:
        return _artifact_current_revision(session, target_id)
    if target_kind == TARGET_TASK:
        return _task_current_revision(session, target_id)
    if target_kind == TARGET_ASSIGNMENT:
        return _assignment_current_revision(session, target_id)
    raise ExceptionCaseError(f"未知受影响项类型：{target_kind!r}（取值域：{sorted(TARGET_KINDS)}）")


# ── 委托货量变更：解析与校验（批准时就要拦住，不能等到应用）──────────────────

#: 变更内容里**必须**出现的三个键（`changes["assignment#<id>"]`）。
#: 旧值**不在其中** —— 旧值由 `build_approval_snapshot` 自己从库里读。
#: 让调用方给旧值，它就能声称"我是从 700 改到 950"，而库里其实是 800；
#: 而那条记录看起来完全正常（三个键齐、格式对），审计时才炸。
ASSIGNMENT_CHANGE_FIELDS: Final = ("quantity", "quantity_unit", "basis")

QUANTITY_PLACES: Final = 3


def _quantity_decimal(value: Any) -> Decimal | None:
    """把入参读成定点数；读不出来返回 `None`。

    口径与 `capacity._decimal` **刻意保持一致**（金额/吨位一律定点，绝不用浮点走一遍）：
    `bool` 是 `int` 的子类（`True` 会变成吨位 1）、`NaN`/`inf` 写进 DECIMAL 列会在
    MySQL 上炸而在 Python 里只会得到"比较恒假"。两侧口径漂移由
    `tests/test_entrust_cargo_quantity_change.py` 用同一张输入表钉住。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            got = Decimal(raw)
        except InvalidOperation:
            return None
        return got if got.is_finite() else None
    return None


def _quantity_text(value: Any) -> str | None:
    """定点数 → **固定小数位**文本（与 `capacity._dec_text` 同口径）。

    固定位数是为了让 SQLite 与 MySQL 读回来是**同一个字符串**；
    否则逐字段核对要先解释格式差异，而那种差异会掩盖真实差异。
    """
    got = _quantity_decimal(value)
    return None if got is None else f"{got:.{QUANTITY_PLACES}f}"


def _assignment_quantity(session: Session, assignment_id: int) -> tuple[Any, Any]:
    """委托当前的 `(quantity, quantity_unit)` —— 未知就是 `None`，不补 0。"""
    row = (
        session.execute(
            text("SELECT quantity, quantity_unit FROM ent_assignment WHERE id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None, None
    return row["quantity"], row["quantity_unit"]


def _validate_assignment_change(
    change: Any, *, target_id: int, before_quantity: Any, before_unit: Any
) -> dict[str, Any]:
    """校验并**归一**一条货量变更内容（批准时调用）。

    五条，每条都对应一种"批准了一份没人能执行的东西"：

    1. 形状必须是对象、三个键齐 —— 缺 `basis` 的变更事后无法回答"凭什么改"；
    2. 新值必须是**正数**（定点）；0 或负数不是货量；
    3. 新单位非空 —— `"950"` 没有单位就没有口径，容量规则连"是不是吨"都判不了；
    4. 新值与旧值**必须不同**：一样的数不是变更，批了它只会白跑一轮复核；
    5. `basis` 非空且 ≤255（列宽）—— 依据是这条记录里唯一解释"为什么"的字段。

    ⚠️ 校验放在**批准**而不是只放在应用：`apply` 只认快照、不接受入参（P4-A4），
    所以坏内容一旦被批，就没有"应用时再拦"的机会 —— 只能在应用时 409 整批拒绝，
    而那时用户已经把整条链路走完一遍了。
    """
    if not isinstance(change, dict):
        raise ExceptionCaseError(
            f"受影响项 assignment#{target_id} 的变更内容必须是对象（当前 {type(change).__name__}）"
        )
    missing = [k for k in ASSIGNMENT_CHANGE_FIELDS if change.get(k) in (None, "")]
    if missing:
        raise ExceptionCaseError(
            f"受影响项 assignment#{target_id} 的变更内容缺字段：{missing}"
            f"（必填：{list(ASSIGNMENT_CHANGE_FIELDS)}）"
        )
    new_qty = _quantity_decimal(change.get("quantity"))
    if new_qty is None or new_qty <= 0:
        raise ExceptionCaseError(
            f"受影响项 assignment#{target_id} 的变更后货量必须是正数（当前 {change.get('quantity')!r}）"
        )
    new_unit = str(change.get("quantity_unit") or "").strip()
    if not new_unit:
        raise ExceptionCaseError(f"受影响项 assignment#{target_id} 的变更后单位不能为空")
    if len(new_unit) > 24:
        raise ExceptionCaseError(
            f"受影响项 assignment#{target_id} 的单位过长（≤24 字符，当前 {len(new_unit)}）"
        )
    basis = str(change.get("basis") or "").strip()
    if not basis:
        raise ExceptionCaseError(f"受影响项 assignment#{target_id} 的变更依据不能为空")
    if len(basis) > 255:
        raise ExceptionCaseError(
            f"受影响项 assignment#{target_id} 的变更依据过长（≤255 字符，当前 {len(basis)}）"
        )
    if _quantity_text(new_qty) == _quantity_text(before_quantity) and new_unit == str(
        before_unit or ""
    ):
        raise ExceptionCaseError(
            f"受影响项 assignment#{target_id} 的变更后货量与当前一致"
            f"（{_quantity_text(before_quantity)} {before_unit or ''}）——"
            "这不是一次变更；批准它只会白跑一轮复核"
        )
    return {
        "quantity": _quantity_text(new_qty),
        "quantity_unit": new_unit,
        "basis": basis,
    }


def build_approval_snapshot(
    session: Session,
    *,
    exception_id: int,
    basis_revision_id: int | None,
    approved_changes: dict[str, dict[str, Any]] | None = None,
    change_category: str | None = None,
) -> dict[str, Any]:
    """构造批准瞬间的应用目标清单（P4-A1/A2/A5）。

    每个目标记录三件事：`target_kind`、`target_id`、以及**它自己的基础版本**
    （成果取 `current_revision_id`，任务取 `revision`，委托取 `revision`）。
    `approved_changes` 是**经过批准的结构化修改内容**，按 `_change_key` 索引；
    应用时**只认它** —— 请求方不得在 apply 时临时替换（P4-A4）。

    `change_category`（五之二）也进快照：复核范围由它决定，若批准后被改动、
    应用却按新类别算范围，就会得到一份**没人批准过的复核清单**。

    **委托货量变更（2026-09-18 新增）**：`assignment` 目标的快照里额外记
    `quantity_before`（批准那一刻库里的货量与单位）—— 它是应用时的**值级**新鲜度依据。
    只比 `revision` 是不够的：`revision` 相同而值不同虽不该发生，但一旦发生，
    记录会声称"从 800 改到 950"而库里其实是别的数 —— 那种记录看起来完全正常。
    变更内容在这里就**校验并归一**（`_validate_assignment_change`），
    因为 `apply` 只认快照、没有第二次机会。
    """
    targets: list[dict[str, Any]] = []
    normalized: dict[str, dict[str, Any]] = {}
    for link in list_links(session, exception_id):
        target_kind = str(link["target_kind"])
        target_id = int(link["target_id"])
        key = _change_key(target_kind, target_id)
        raw_change = (approved_changes or {}).get(key)
        entry: dict[str, Any] = {
            "target_kind": target_kind,
            "target_id": target_id,
            "basis_revision_id": _target_basis_revision(
                session, target_kind=target_kind, target_id=target_id
            ),
            "has_changes": raw_change is not None,
        }
        if target_kind == TARGET_ASSIGNMENT:
            before_qty, before_unit = _assignment_quantity(session, target_id)
            # 旧值**总是**记：即使这次没批变更内容，它也是"批准时是什么样"的一部分。
            entry["quantity_before"] = {
                "quantity": _quantity_text(before_qty),
                "quantity_unit": None if before_unit is None else str(before_unit),
            }
            if raw_change is not None:
                normalized[key] = _validate_assignment_change(
                    raw_change,
                    target_id=target_id,
                    before_quantity=before_qty,
                    before_unit=before_unit,
                )
        elif raw_change is not None:
            normalized[key] = raw_change
        targets.append(entry)
    return {
        "kind": APPROVAL_SNAPSHOT_KIND,
        "version": APPROVAL_SNAPSHOT_VERSION,
        "case_basis_revision_id": basis_revision_id,
        "change_category": change_category,
        "targets": targets,
        # ⚠️ 必须是**归一后**的 `normalized` 而不是入参：货量变更的 `quantity` 在
        # `_validate_assignment_change` 里被格式化成固定小数位（`950` → `"950.000"`）。
        # 直接存原文的话，应用时写进 DECIMAL 列的是调用方给的那个字面量，
        # 而快照里记的是另一个 —— 事后核对"批准了什么"就要先解释格式差异。
        "changes": normalized,
    }


def _load_approval_snapshot(session: Session, exception_id: int) -> dict[str, Any] | None:
    """取**最近一次** approved 决定的快照（事件倒序第一条携带快照的 `decided` 事件）。"""
    rows = session.execute(
        text(
            "SELECT payload_json FROM ent_exception_event "
            "WHERE exception_id = :cid AND event_kind = :k AND to_status = :st "
            "ORDER BY seq DESC"
        ),
        {"cid": exception_id, "k": EVENT_DECIDED, "st": STATUS_APPROVED},
    ).fetchall()
    for row in rows:
        raw = row[0]
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("kind") == APPROVAL_SNAPSHOT_KIND:
            return cast("dict[str, Any]", data)
    return None


def _quantity_side(quantity: Any, unit: Any) -> dict[str, Any]:
    """货量的一侧（`before` 或 `after`）→ 原值 + 文案。

    **原值与文案一起给**，且都在服务端算：界面显示文案、程序比对用原值。
    让界面自己拼 `数值 + 单位`，同一句"800.000 吨"就会有两处实现，
    而漂移的表现是"历史里写的是 800 吨、批准摘要里写的是 800.000吨" ——
    看的人会以为是两次不同的变更。

    未知保持 `"未知"`（**不是 0**）：从 NULL 改成确定值是合法变更，
    把旧值显示成 0 会让它看起来像"从 0 涨到 950"。
    """
    text = _quantity_text(quantity)
    unit_text = "" if unit is None else str(unit)
    return {
        "quantity": text,
        "quantity_unit": None if unit is None else unit_text,
        "quantity_text": "未知" if text is None else f"{text} {unit_text}".strip(),
    }


def approval_summary(session: Session, exception_id: int) -> dict[str, Any] | None:
    """批准快照的**界面摘要**（`None` = 没有快照，即"没批准过 / 批准时没给内容"）。

    为什么界面必须拿到它：`apply` 只认批准快照，**不接受**"改成什么"的入参
    （P4-A4）。如果界面不展示将应用什么，点「应用变更」就是一次**盲操作** ——
    应用之后才发现改错了，而历史已经写进去了（revision 是 append-only，
    改错只能再追加一版，前一次仍在审计链上）。

    只投影"将动哪些目标、各自改哪些字段"，**不投影字段值**：
    值的形状随成果类型而变（金额、日期、枚举），塞进界面要么排版崩、要么误导；
    要看精确值应当去成果页的版本历史（那里有完整的 `payload` 与来源）。

    **唯一的例外是委托货量变更**：它给 `quantity_change`（`before` / `after` / `basis`）。
    理由不是"这类重要一些"，而是**值的形状差异**：成果载荷是不定形的，
    而货量变更恰好是两个标量 + 一个依据 —— 不把它显示出来，「应用变更」就退化成
    盲操作（点下去才知道是从 800 改到 950）。列字段名在这里毫无信息量。

    ⚠️ 这不是权限边界：能读案件详情的人本来就能读事件链里的原始快照。
    """
    snapshot = _load_approval_snapshot(session, exception_id)
    if snapshot is None:
        return None
    changes = snapshot.get("changes") or {}
    targets: list[dict[str, Any]] = []
    for item in snapshot.get("targets", []):
        target_kind = str(item["target_kind"])
        target_id = int(item["target_id"])
        key = _change_key(target_kind, target_id)
        change = changes.get(key)
        # `changes[key]` **就是**该目标的新 payload 本身（apply 直接把它交给
        # `append_revision(payload=...)`），不是 `{"payload": {...}}` 的包裹 ——
        # 按包裹读会拿到空字段列表，界面上表现为"这次应用什么都不改"。
        fields = sorted(str(k) for k in change) if isinstance(change, dict) else []
        entry: dict[str, Any] = {
            "target_kind": target_kind,
            "target_id": target_id,
            "basis_revision_id": item.get("basis_revision_id"),
            "change_fields": fields,
        }
        if target_kind == TARGET_ASSIGNMENT and isinstance(change, dict):
            before = item.get("quantity_before") or {}
            entry["quantity_change"] = {
                "before": _quantity_side(before.get("quantity"), before.get("quantity_unit")),
                "after": _quantity_side(change.get("quantity"), change.get("quantity_unit")),
                "basis": change.get("basis"),
            }
        targets.append(entry)
    return {
        "snapshot_version": int(snapshot.get("version", 0)),
        "change_category": snapshot.get("change_category"),
        "case_basis_revision_id": snapshot.get("case_basis_revision_id"),
        "targets": targets,
    }


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
    "change_category",
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

#: 受影响项的取值域 → 表名。只有这三张表，**不放任任意 target**。
_TARGET_TABLE: Final = {
    TARGET_TASK: "ent_workflow_task",
    TARGET_ARTIFACT: "ent_artifact",
    TARGET_ASSIGNMENT: "ent_assignment",
}

#: 受影响项的**归属列**：任务与成果挂在委托上（该列存的必须是本委托的 id）；
#: 委托单自己就是归属对象（列就是它自己的主键）。
#:
#: 写成映射而不是 `if` 分支，是为了让「新增一种受影响项」**必须同时回答"它属于谁"** ——
#: 落进一个默认分支的话，新类型会静默地按错误的列校验归属，而那种错看起来完全正常。
_TARGET_ASSIGNMENT_COL: Final = {
    TARGET_TASK: "assignment_id",
    TARGET_ARTIFACT: "assignment_id",
    TARGET_ASSIGNMENT: "id",
}


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


# ── 案件的两个形状：主键键名**故意不同**，别当成疏漏 ──────────────────────────
#
#   * **行形状**（镜像数据库列，键名 == 列名）—— 主键键名 `id`：
#     `get_case` / `list_cases` / `load_visible_case` / `blocking_cases_for_task` /
#     `list_blocking_cases`。跟着 `_CASE_COLS` 走，加一列只改一处 SQL。
#   * **投影形状**（要出服务层的载荷）—— 主键键名 `case_id`：
#     `project_case_internal` / `project_case_for_customer`。不用裸 `id`，是因为
#     投影里同时有 `affected[].link_id`，`case_id` / `link_id` 并列才自解释；
#     API 响应沿用投影的 `case_id`，前端与用例都按它取值。
#
# 两个形状的对应关系已被用例钉住：
# `test_internal_projection_exposes_decision_and_affected` 里的
# `assert view["case_id"] == int(case["id"])` 就是这条约定。要统一键名的话，改的是
# 这层约定**与投影的消费方**（含 API 契约与前端取值），不是随手改一个函数。
# ────────────────────────────────────────────────────────────────────────────


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


def _page_cases(
    session: Session, *, where: list[str], params: dict[str, Any], page: int, size: int
) -> tuple[int, list[dict[str, Any]]]:
    """按 `where` 分页取案件 —— `ORDER BY id DESC` 是**唯一**排序口径。

    计数与取页共用同一个 `WHERE` 与同一份 `params`。两处各拼一次 SQL 是
    「`total` 与内容对不上」最常见的来源（筛选条件只加在取页那一边，
    数据少时看不出来，等翻到第二页才发现总数虚高）。
    """
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
    return _page_cases(session, where=where, params=params, page=page, size=size)


#: 组织级视图的开闭范围取值域（DR-0014 §3.1）。
#:
#: 用 `scope` 而**不**复用 `status`：`status != 'closed'` 表达的是一个**范围**，
#: 塞进只接受单值的 `status` 就得自造 `status=!closed` 这类语法，
#: 而每一个消费方（前端、用例、将来的导出）都得先学会解析它。
ORG_SCOPES: Final = frozenset({"unclosed", "all"})
ORG_SCOPE_UNCLOSED = "unclosed"
ORG_SCOPE_ALL = "all"


def list_cases_for_org(
    session: Session,
    *,
    org_id: int,
    scope: str = ORG_SCOPE_UNCLOSED,
    kind: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """按**单个组织**列案件（`GET /exceptions?view=org`，DR-0014 §3.1）。

    三条硬约束，与 DR-0014 §3.1 一一对应：

    1. **作用域只接受一个 `org_id`**：`WHERE org_id = :oid` 是精确等值，**不是** `IN`。
       没有「不传 `org_id` 就返回我所属全部组织」的用法 —— 那正是跨组织拼接；
       要列多个组织就分多次请求，每次独立过一遍授权。
    2. **授权先于分页与计数**：本函数**不判权限**，由 API 层在**调用它之前**
       经 `authz.assert_can_view_org` 判定。把判定塞进函数里看似更安全，实则不然 ——
       那样计数会发生在可能未被授权的目标上，而 `total` 本身就是一次信息泄漏。
    3. **开闭范围以 `scope` 表达**，不用 `status`（理由见 `ORG_SCOPES`）。

    排序与 `size` 上限沿用单委托视图（`_page_cases`），**不引入第二套规则**。
    """
    if scope not in ORG_SCOPES:
        raise ExceptionCaseError(f"未知范围：{scope!r}（取值域：{sorted(ORG_SCOPES)}）")
    if kind is not None:
        _require_known_kind(kind)

    where = ["org_id = :oid"]
    params: dict[str, Any] = {"oid": org_id}
    if scope != ORG_SCOPE_ALL:
        where.append("status != :closed")
        params["closed"] = STATUS_CLOSED
    if kind is not None:
        where.append("kind = :kind")
        params["kind"] = kind
    return _page_cases(session, where=where, params=params, page=page, size=size)


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
    """该委托下**当前阻断**的全部案件（未关闭候选集 → `is_blocking` 过滤）。

    与工作台共用的是**判据** `is_blocking`，**不是这个函数**：工作台的 `exceptions`
    槽要连**已关闭**案件一起看（"最后更新"取自全部案件），候选集不同，它走自己的
    `_load_cases` + `_is_blocking_case`（后者同样只调 `is_blocking`）。所以别把这里
    写成"工作台通过本函数取阻断项" —— 那是另一条路径，改了这边不会影响工作台。

    ⚠️ 当前**没有生产调用方**（只有 `test_list_blocking_cases_uses_the_same_judgement`）。
    保留它是因为「某委托下哪些案件在阻断」是一个独立、可复用的查询口径；但它此刻
    **不是**任何门禁的执行路径，不要拿它当"阻断已生效"的证据。
    """
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


def can_write_case(session: Session, *, actor_id: int, assignment: dict[str, Any]) -> bool:
    """能否**改动**这张委托下的案件 —— 与 `_assert_can_write` **同一份判据**。

    折叠成布尔只是为了给 `case_capabilities` 一个不抛异常的入口。
    方向是「这里包它」而不是「它包这里」：`_assert_can_write` 还要保留
    404（非组织成员）与 403（无权限）的**原因区分**，而布尔会把原因丢掉 ——
    若由它反向调用本函数，就得再判一次成员资格才能决定抛哪一种，那才是第二份实现。
    """
    try:
        _assert_can_write(session, actor_id=actor_id, assignment=assignment)
    except (ExceptionCaseNotFoundError, AccessDeniedError):
        return False
    return True


def case_capabilities(
    session: Session,
    *,
    actor_id: int,
    case: dict[str, Any],
    affected_count: int,
) -> dict[str, bool]:
    """该用户对**这一宗**案件的可执行动作（UI-08 的按钮可用性，DR-0014 §3.4）。

    三条纪律：

    1. **不自建第二份权限判据**：能力位的「写权限」这一维统一由 `can_write_case`
       给出，而它包的就是 `_assert_can_write`；
    2. **不硬编码状态名**：「状态维」一律取 `allowed_transitions` 与
       `allowed_closure_dispositions` 的结果 —— DR-0013 §3.4 的状态机改了就跟着变；
    3. **服务端只是如实投影**：界面据 `can_*` 隐藏按钮**不是**权限控制。写端仍独立
       复核权限、证据、幂等与版本；两者不一致时**以写端为准**，界面收到 403/409
       必须提示，不能静默。

    `affected_count` 由调用方传入：详情端点已经取过受影响项，在服务层再查一次是浪费。

    Note:
        `can_decide` 对**已关闭**案件恒 `false`，即便状态机允许 `closed → open`。
        那条转移归 `reopen` 专用命令所有，它写的是 `reopened` 事件；
        走 `decide` 会写成 `status_changed`，审计形态不同。界面同时亮出
        「记录决定」与「重开」只会把人引到审计不一致的那条路上。
    """
    assignment = load_assignment(session, int(case["assignment_id"]))
    can_write = assignment is not None and can_write_case(
        session, actor_id=actor_id, assignment=assignment
    )
    kind = str(case["kind"])
    status = str(case["status"])
    open_case = status != STATUS_CLOSED
    return {
        "can_add_link": can_write and open_case,
        "can_remove_link": can_write and open_case and affected_count > 0,
        "can_decide": can_write and open_case and bool(allowed_transitions(kind, status)),
        "can_close": can_write and bool(allowed_closure_dispositions(kind, status)),
        "can_reopen": can_write and status == STATUS_CLOSED,
        # 「应用变更」（A2 五之一）。
        # 状态维照旧取状态机（不硬编码状态名）：`approved → applied` 两个 kind 都有，
        # 也就是"批准之后要执行/应用"这件事对异常与变更都存在。
        #
        # ⚠️ 翻 `APPLY_OPEN`（五之四，2026-09-15）的前提是**传播闭环已接通**，不是
        # "界面做完了"。界面只是让这个能力**能被用到**；能不能开，取决于开了之后
        # 下游能不能同时收到失效与复核提示 —— 那是五之二的事，与前端无关。
        "can_apply_change": (
            can_write and APPLY_OPEN and STATUS_APPLIED in allowed_transitions(kind, status)
        ),
    }


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
    owner_col = _TARGET_ASSIGNMENT_COL[target_kind]
    row = session.execute(
        text(f"SELECT {owner_col} FROM {table} WHERE id = :tid"), {"tid": target_id}
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
    change_category: str | None = None,
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
    _require_known_change_category(kind=kind, change_category=change_category)

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
                    " created_at, updated_at, change_category) "
                    "VALUES (:org, :aid, :kind, :title, :cause, :severity, :impact, :status, "
                    " :owner, :raiser, :source, :due, :proposed, NULL, NULL, NULL, NULL, NULL, "
                    " NULL, NULL, NULL, 1, :raised, :ts, :ts, :category)"
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
                    "category": change_category,
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
    approved_changes: dict[str, dict[str, Any]] | None = None,
    change_category: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """记录决定（`approved` / `rejected`，以及退回补充类的状态变更）。

    **A2 五之一新增**：`approved` 会顺带写入**批准快照**（P4）—— 它记下批准那一瞬间
    每个受影响项的**基础版本**与**结构化修改内容**，供 `apply_case` 逐目标核对。
    没有它，「旧批准不得直接应用到新内容」（验证 17）就无从判定：
    `case.basis_revision_id` 是案件级单值，管不了多目标，也不做过期检查。


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
    _require_known_change_category(kind=kind, change_category=change_category)

    current = now or utcnow_naive()
    sets = ["status = :to", "decided_by = :actor", "decided_at = :ts"]
    params: dict[str, Any] = {"to": to_status, "actor": actor_id}
    if decision_note is not None:
        sets.append("decision_note = :note")
        params["note"] = decision_note
    if basis_revision_id is not None:
        sets.append("basis_revision_id = :basis")
        params["basis"] = basis_revision_id
    if change_category is not None:
        # 批准时刻是补登记变更类别的**最后合理时机**：应用要按它定复核范围，
        # 而决定人此时正看着这份变更的内容（`kind` 已在上方校验过）。
        sets.append("change_category = :category")
        params["category"] = change_category

    decided = to_status in (STATUS_APPROVED, STATUS_REJECTED)
    snapshot_payload: dict[str, Any] | None = None
    if to_status == STATUS_APPROVED:
        snapshot_payload = build_approval_snapshot(
            session,
            exception_id=exception_id,
            basis_revision_id=basis_revision_id,
            approved_changes=approved_changes,
            change_category=(
                change_category if change_category is not None else case.get("change_category")
            ),
        )
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
            payload=snapshot_payload,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _case_with_links(session, exception_id)


def list_quantity_changes(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """某张委托的**货量变更历史**（append-only，按应用时间升序）。

    为什么读侧必须存在：`ent_assignment_quantity_change` 只写不读的话，
    「原来是 800」就只能靠案件反查 —— 而 D1-09 要的是**在这张委托上**看到
    `800 → 950` 这条对照。一张没有读路径的历史表，等于把审计留痕藏在审计链深处。

    `basis` 是**经理写的变更依据**（可能含内部口径），因此读端点的可见性
    与运力那一组同口径（组织成员 + `entrust:view`），**不给货主本人放行** ——
    货量本身客户当然看得见（在委托详情里），但"为什么改"是谁写的话是关键。
    """
    rows = (
        session.execute(
            text(
                "SELECT id, assignment_id, exception_id, base_revision, old_quantity, "
                "old_quantity_unit, new_quantity, new_quantity_unit, basis, applied_by, "
                "applied_at FROM ent_assignment_quantity_change "
                "WHERE assignment_id = :aid ORDER BY applied_at ASC, id ASC"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [project_quantity_change(r) for r in rows]


def project_quantity_change(row: Any) -> dict[str, Any]:
    """一行货量变更 → 响应形状。

    同时给**原值**与**文案**（`quantity_text`）：界面显示文案、程序比对用原值 ——
    两者都从同一处产出，界面就不会自己拼字符串（各拼一份必然漂移）。
    旧值**未知保持 `None`**，不补 0：从"未知"改成确定值是合法变更，
    把它显示成 0 会让历史看起来像"从 0 涨到 950"。
    """
    old_qty = _quantity_text(row["old_quantity"])
    old_unit = None if row["old_quantity_unit"] is None else str(row["old_quantity_unit"])
    new_qty = _quantity_text(row["new_quantity"])
    new_unit = str(row["new_quantity_unit"])
    return {
        "change_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "exception_id": int(row["exception_id"]),
        "base_revision": int(row["base_revision"]),
        "old_quantity": old_qty,
        "old_quantity_unit": old_unit,
        "new_quantity": new_qty,
        "new_quantity_unit": new_unit,
        # 文案与 `approval_summary` 的 `quantity_change` 走**同一个** `_quantity_side`：
        # 同一句"800.000 吨"若有两处实现，漂移的表现是"历史里一次写法、
        # 批准摘要里另一次写法"，看的人会以为是两次不同的变更。
        "old_quantity_text": _quantity_side(row["old_quantity"], row["old_quantity_unit"])[
            "quantity_text"
        ],
        "new_quantity_text": _quantity_side(row["new_quantity"], row["new_quantity_unit"])[
            "quantity_text"
        ],
        "basis": str(row["basis"]),
        "applied_by": None if row["applied_by"] is None else int(row["applied_by"]),
        "applied_at": _text_ts(row["applied_at"]),
    }


def _apply_assignment_quantity_change(
    session: Session,
    *,
    exception_id: int,
    target_id: int,
    snapshot_item: dict[str, Any],
    change: dict[str, Any],
    actor_id: int,
    now: datetime,
) -> dict[str, Any]:
    """把一条**已批准**的货量变更应用到委托单（2026-09-18 / D1-09）。

    四条顺序不能颠倒：

    1. **先做值级核对**（库里现在是不是还是批准时那个数）；
    2. **再条件更新**（`WHERE id=… AND revision=:base`）—— 这是唯一的并发闸门；
    3. **写历史行**（append-only，旧值在这里才真正"留下来"）；
    4. **交给调用方统一提交** —— 本函数**不 commit**：货量更新必须与"案件转 applied、
       写事件、生成复核任务"在**同一个事务**里。分开提交就会留下
       "货量改了但复核没生成"的中间态，而那正是 HO 明确否掉的那种状态
       （`capacity` 会照新货量判，下游却没人知道要复核）。

    ⚠️ 第 2 步的 `revision = revision + 1` 不是可选项：不推进版本，客户端手里那份
    委托快照就永远不会失效，下一次变更会拿着同一个 `base_revision` 再来一遍 ——
    而那时的"批准依据"已经指错版本了。

    ⚠️ 第 3 步的旧值取自**快照**（`snapshot_item["quantity_before"]`）而不是重新读库：
    重新读会在并发下把"批准时的 800"记成"现在的 950"，历史行就此错位。

    Returns:
        应用记录（进 `applied` 事件 payload 与 `applied_rejected` 的对照）。
    """
    before = snapshot_item.get("quantity_before") or {}
    before_qty_text = before.get("quantity")
    before_unit_text = before.get("quantity_unit")

    cur_qty, cur_unit = _assignment_quantity(session, target_id)
    cur_unit_text = None if cur_unit is None else str(cur_unit)
    if _quantity_text(cur_qty) != before_qty_text or cur_unit_text != before_unit_text:
        raise AssignmentQuantityStaleError(
            f"委托 {target_id} 的当前货量（{_quantity_text(cur_qty) or '未知'}"
            f" {cur_unit_text or ''}）与批准时记下的（{before_qty_text or '未知'}"
            f" {before_unit_text or ''}）不一致 —— 有人在本变更之外改过它，"
            "旧批准不能直接应用；请重新读取后再次批准"
        )

    result = cast(
        CursorResult[Any],
        session.execute(
            text(
                "UPDATE ent_assignment SET quantity = :new_qty, quantity_unit = :new_unit, "
                "revision = revision + 1, updated_at = :ts "
                "WHERE id = :aid AND revision = :base"
            ),
            {
                "new_qty": change["quantity"],
                "new_unit": change["quantity_unit"],
                "ts": _fmt(now),
                "aid": target_id,
                "base": snapshot_item.get("basis_revision_id"),
            },
        ),
    )
    if result.rowcount == 0:
        raise AssignmentQuantityConflictError(
            f"委托 {target_id} 的版本在本次应用期间已被他人推进"
            f"（本次以 revision={snapshot_item.get('basis_revision_id')} 为条件）—— "
            "货量未被改动，请重新读取后再试"
        )

    session.execute(
        text(
            "INSERT INTO ent_assignment_quantity_change "
            "(assignment_id, exception_id, base_revision, old_quantity, old_quantity_unit, "
            " new_quantity, new_quantity_unit, basis, applied_by, applied_at) "
            "VALUES (:aid, :cid, :base, :old_qty, :old_unit, :new_qty, :new_unit, "
            " :basis, :actor, :ts)"
        ),
        {
            "aid": target_id,
            "cid": exception_id,
            "base": int(snapshot_item.get("basis_revision_id") or 0),
            "old_qty": before_qty_text,
            "old_unit": before_unit_text,
            "new_qty": change["quantity"],
            "new_unit": change["quantity_unit"],
            "basis": change["basis"],
            "actor": actor_id,
            "ts": _fmt(now),
        },
    )
    return {
        "target": _change_key(TARGET_ASSIGNMENT, target_id),
        "old_quantity": before_qty_text,
        "old_quantity_unit": before_unit_text,
        "new_quantity": change["quantity"],
        "new_quantity_unit": change["quantity_unit"],
        "basis": change["basis"],
    }


def apply_case(
    session: Session,
    *,
    exception_id: int,
    actor_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """应用**已批准**的变更（A2 五之一；验证 17 / 18）。

    五条不能让的边界（HO 2026-09-15 裁决 P4 + DR-0013 §3.7）：

    1. **只认批准快照**：修改内容来自 `decided` 事件的批准快照，请求方**不得**
       在 apply 时临时替换（P4-A4）—— 本函数因此**不接受**任何"改成什么"的入参；
    2. **逐目标核对版本**：每个目标各自比对基础版本，任一过期 ⇒ **整批拒绝**
       （409）+ 事件 `applied_rejected`，**不产生部分生效**；
    3. **纯任务目标不虚构成果版本**：任务用任务自己的 `revision` 作依据（P4-A5），
       任务目标的"已处理"由任务状态机保证，不往 `applied_revision_id` 里塞语义不符的值；
    4. **统一事务**：成果的新版本、确认、link 回写、案件状态与事件在**一个事务**里；
       任一处失败整笔回滚 —— 不会留下"改了一半的委托"（验证 18）；
    5. **拒绝事件不因回滚丢失**（P4-A8）：无论"版本过期"还是"应用失败"，
       `applied_rejected` 事件都**独立提交**，它是审计事实，不是业务的一部分。

    **A2 五之二新增（验证 19）**：应用成功后**在同一事务内**做变更传播 ——
    受影响成果标 `needs_revalidation`、生成复核任务（照抄 DR-0016 五行映射）。
    为什么不另开一个传播命令：HO 明确否掉了「变更已生效、下游却收不到失效或复核提示」
    这个中间态（04-A2期切片计划 §6）。**变更类别未登记时拒绝应用** —— 没有类别就
    无从确定复核范围，而"先应用、后补范围"会让下游短时间照着失效事实干活。

    Raises:
        ExceptionCaseError: 缺少快照 / 快照版本不认识 / 某目标没有批准的修改内容 /
            变更请求未登记变更类别。
        ExceptionCaseConflictError: 状态不允许、乐观锁过期、或依据版本已变化。
    """
    from app.modules.entrust import artifacts as artifacts_svc  # 局部导入：避免与 artifacts 成环
    from app.modules.entrust import revalidation as reval_svc
    from app.modules.entrust.artifacts import SOURCE_MANUAL

    case = _case_or_404(session, exception_id)
    assignment = _assignment_or_404(session, int(case["assignment_id"]))
    _assert_can_write(session, actor_id=actor_id, assignment=assignment)
    kind = str(case["kind"])
    from_status = str(case["status"])
    assert_transition(kind=kind, from_status=from_status, to_status=STATUS_APPLIED)

    current = now or utcnow_naive()

    # ── 变更传播的前置：类别必须已登记（五之二）──────────────────────────────
    category = case.get("change_category")
    artifact_targets: list[int] = []
    if kind == KIND_CHANGE_REQUEST:
        if category is None or str(category) not in reval_svc.CHANGE_CATEGORIES:
            # **在任何写入之前拒绝**：这类拒绝不需要（也不该）留下 applied_rejected 事件 ——
            # 它说的是"请求本身不完整"，不是"这次应用被业务规则挡下"。
            raise ExceptionCaseError(
                "变更请求未登记变更类别（change_category），无法确定复核范围，"
                "不能应用 —— 请先在决定时补登类别（取值域见 DR-0016）"
            )
        artifact_targets = [
            int(link["target_id"])
            for link in list_links(session, exception_id)
            if str(link["target_kind"]) == TARGET_ARTIFACT
        ]

    def _reject(reason: str, note: str, payload: dict[str, Any] | None = None) -> None:
        """写拒绝事件并**独立提交**（业务已回滚或尚未开始，审计不能跟着没）。"""
        try:
            _append_event(
                session,
                exception_id=exception_id,
                event_kind=EVENT_APPLIED_REJECTED,
                actor_user_id=actor_id,
                now=current,
                from_status=from_status,
                to_status=from_status,
                note=note,
                payload={"reason": reason, **(payload or {})},
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

    snapshot = _load_approval_snapshot(session, exception_id)
    if snapshot is None:
        raise ExceptionCaseError("缺少批准快照：不能以『曾经批准过』为凭据应用变更，请重新记录批准")
    if int(snapshot.get("version", 0)) != APPROVAL_SNAPSHOT_VERSION:
        raise ExceptionCaseError(
            f"批准快照版本 {snapshot.get('version')!r} 无法识别（当前 {APPROVAL_SNAPSHOT_VERSION}）"
        )

    # ── 变更类别在批准后不得被改（五之二）────────────────────────────────────
    # 复核范围由类别决定。批准后改类别、应用却按新类别算范围 ⇒ 生成一份**没人批准过的
    # 复核清单**，而且它看起来完全正常（任务有、标记有）。
    snap_category = snapshot.get("change_category")
    if (
        kind == KIND_CHANGE_REQUEST
        and snap_category is not None
        and str(snap_category) != str(category)
    ):
        _reject(
            "category_changed",
            "批准时的变更类别与当前不一致，复核范围会与批准内容不符",
            {"approved_category": snap_category, "current_category": category},
        )
        raise ExceptionCaseConflictError(
            f"批准时的变更类别是 {snap_category!r}，现在是 {category!r}；"
            "复核范围会与批准内容不符，请重新批准"
        )

    # ── 逐目标核对基础版本（P4-A4）───────────────────────────────────────────
    stale: list[dict[str, Any]] = []
    for item in snapshot.get("targets", []):
        target_kind = str(item["target_kind"])
        target_id = int(item["target_id"])
        expected_basis = item["basis_revision_id"]
        current_basis = _target_basis_revision(
            session, target_kind=target_kind, target_id=target_id
        )
        if current_basis != expected_basis:
            stale.append(
                {
                    "target": _change_key(target_kind, target_id),
                    "approved_basis": expected_basis,
                    "current": current_basis,
                }
            )
    if stale:
        _reject(
            "stale_basis",
            "批准所依据的版本已变化，旧批准不能直接应用",
            {"stale": stale},
        )
        detail = ", ".join(
            f"{s['target']}（批准时 {s['approved_basis']} → 现在 {s['current']}）" for s in stale
        )
        raise ExceptionCaseConflictError(f"依据版本已变化：{detail}；请重新读取并再次批准")

    # ── 应用（统一事务）─────────────────────────────────────────────────────
    changes: dict[str, Any] = snapshot.get("changes") or {}
    applied: list[dict[str, Any]] = []
    try:
        for item in snapshot.get("targets", []):
            target_kind = str(item["target_kind"])
            target_id = int(item["target_id"])
            if target_kind == TARGET_TASK:
                # 任务没有成果版本；它的"已处置"由任务状态机承担（P4-A5）。
                continue
            key = _change_key(target_kind, target_id)
            if key not in changes:
                raise ExceptionCaseError(
                    f"受影响项 {key} 没有经过批准的修改内容，不能应用"
                    "（应用只认批准快照，不接受临时替换）"
                )
            if target_kind == TARGET_ASSIGNMENT:
                # 同一事务内改 `ent_assignment.quantity` 并留一行历史（不 commit：
                # 由本函数末尾统一提交，与案件转 applied、写事件、传播绑在一起）。
                applied.append(
                    _apply_assignment_quantity_change(
                        session,
                        exception_id=exception_id,
                        target_id=target_id,
                        snapshot_item=item,
                        change=changes[key],
                        actor_id=actor_id,
                        now=current,
                    )
                )
                continue
            appended = artifacts_svc.append_revision(
                session,
                artifact_id=target_id,
                payload=changes[key],
                actor_id=actor_id,
                source=SOURCE_MANUAL,
                note=f"由案件 {exception_id} 的批准变更应用",
                now=current,
                commit=False,  # 由本函数统一提交（P4-A7）
            )
            artifacts_svc.confirm_revision(
                session,
                artifact_id=target_id,
                revision_no=int(appended["revision_no"]),
                actor_id=actor_id,
                as_source=SOURCE_MANUAL,
                now=current,
                commit=False,
            )
            session.execute(
                text(
                    "UPDATE ent_exception_link SET applied_revision_id = :rid "
                    "WHERE exception_id = :cid AND target_kind = :tk AND target_id = :tid"
                ),
                {
                    "rid": int(appended["revision_id"]),
                    "cid": exception_id,
                    "tk": target_kind,
                    "tid": target_id,
                },
            )
            applied.append(
                {
                    "target": key,
                    "revision_id": int(appended["revision_id"]),
                    "revision_no": int(appended["revision_no"]),
                }
            )

        # ── 变更传播（五之二 / 验证 19）：与「应用 + 事件」同一事务 ──────────────
        # 放在这里而不是另开命令，是为了让"变更生效"与"下游知道要复核"不可能分离。
        # 只有 change_request 传播：DR-0016 管的是业务变更（见 _require_known_change_category）。
        revalidation: dict[str, Any] = {"planned": 0, "skipped": 0, "items": [], "unconfirmed": []}
        if kind == KIND_CHANGE_REQUEST:
            revalidation = reval_svc.apply_revalidation(
                session,
                exception_id=exception_id,
                assignment_id=int(case["assignment_id"]),
                category=str(category),
                actor_id=actor_id,
                artifact_targets=artifact_targets,
                now=current,
                commit=False,  # 与外层同一个事务（P4-A7）
            )

        _apply_case_update(
            session,
            exception_id=exception_id,
            expected_revision=expected_revision,
            sets=["status = :to"],
            params={"to": STATUS_APPLIED},
            now=current,
        )
        _append_event(
            session,
            exception_id=exception_id,
            event_kind=EVENT_APPLIED,
            actor_user_id=actor_id,
            now=current,
            from_status=from_status,
            to_status=STATUS_APPLIED,
            note="已按批准快照逐目标应用",
            payload={"applied": applied, "snapshot_version": APPROVAL_SNAPSHOT_VERSION},
        )
        if kind == KIND_CHANGE_REQUEST:
            # 传播计划单独成事件：它是"下游收到了什么"，与"改了什么"是两件事。
            # `unconfirmed` 必须落进事件 —— 那是**范围不足、需人工确认**的信号，
            # 只写在返回值里等于没人看得到（DR-0016 §4.1 要求交经理人确认）。
            _append_event(
                session,
                exception_id=exception_id,
                event_kind=EVENT_REVALIDATION_PLANNED,
                actor_user_id=actor_id,
                now=current,
                from_status=STATUS_APPLIED,
                to_status=STATUS_APPLIED,
                note=(
                    f"已按变更类别 {category} 生成 {revalidation['planned']} 项复核"
                    + (
                        f"；{len(revalidation['unconfirmed'])} 类候选成果未登记受影响项，需确认范围"
                        if revalidation["unconfirmed"]
                        else ""
                    )
                ),
                basis_revision_id=None,
                payload={"category": category, **revalidation},
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        # 原因码取自异常自己（`audit_reason`），而不是一律写 `apply_failed`：
        # 「值级过期」与「并发冲突」要人做的事完全不同（前者要重新批准、后者只要重试），
        # 审计链上把它们压成一句话，等于把定位工作留给了下一个人。
        # ⚠️ 用 `getattr` 兜底：绝大多数失败（`IntegrityError`、写库异常）没有这个属性，
        # 它们仍然记 `apply_failed` —— 不因为新加了两类就改变原有语义。
        _reject(
            str(getattr(exc, "audit_reason", "apply_failed")),
            "应用失败，已整笔回滚（未产生任何业务更新）",
        )
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
        # 变更类别（五之二）：决定**复核范围**的输入。放在投影里是为了让 UI 能回答
        # "这批复核任务凭什么生成的" —— 只给任务列表、不给类别，复核范围就没法追溯。
        "change_category": case.get("change_category"),
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


def project_case_list_item(case: dict[str, Any], *, affected_count: int) -> dict[str, Any]:
    """组织级清单的一行（DR-0014 §3.2）。

    `blocking` 由 `is_blocking` **当场算出**，不读任何缓存列 —— 清单与执行门禁
    必须是同一次判定的结果，否则会出现「门禁拦住但清单没标阻断」（或反向）。

    `affected_count` 由调用方传入：列表端点用 `list_links_for_cases` 一次取回整页的
    受影响项，在这里再查一遍就成了 N+1。传长度而不是传列表，是为了让
    「这一行只需要数量」这件事写在签名里。
    """
    return {
        "case_id": int(case["id"]),
        "assignment_id": int(case["assignment_id"]),
        "kind": str(case["kind"]),
        "title": str(case["title"]),
        "status": str(case["status"]),
        "impact_kind": str(case["impact_kind"]),
        "blocking": is_blocking(
            kind=str(case["kind"]),
            impact_kind=str(case["impact_kind"]),
            status=str(case["status"]),
        ),
        "due_at": case["due_at"],
        "updated_at": case["updated_at"],
        "affected_count": int(affected_count),
    }


def project_case_ref(
    *, case_id: int, kind: str, title: str, status: str, impact_kind: str
) -> dict[str, Any]:
    """UI-05 `exceptions` 槽里的一条案件引用（DR-0014 §3.3 的 `CaseRef`）。

    与 `project_case_list_item` 的差别是**刻意的**，不是漏投影：

    | | 清单行（UI-04） | 槽引用（UI-05） |
    | --- | --- | --- |
    | 作用域 | 跨委托，所以要 `assignment_id` | 已在某一委托内，带了是冗余 |
    | 数量 | 要 `affected_count` 供列表展示 | 点进详情就有，列表不必先算 |
    | 版本位 | —— | **无**：`revision_no` 是乐观锁号，不是「看哪一版」 |

    `blocking` 由 `is_blocking` 当场算出，**不读缓存列** ——
    槽位里的"阻断"标记与执行门禁必须是同一次判定的结果。

    Note:
        本函数收**显式字段**而不是 dict，与另两个投影不同。原因是它有两个调用面：
        案件**行形状**（主键 `id`）与工作台 `_load_cases` 的本地形状（主键 `case_id`）。
        让函数按 `case.get("id") or case.get("case_id")` 去猜键名，正是
        `_case_from_row` 上方那段形状注释要防的事 —— 猜错时不会报错，
        只会把别的案件的 id 当成本案件的 id 写进引用里。
    """
    return {
        "case_id": int(case_id),
        "kind": str(kind),
        "title": str(title),
        "status": str(status),
        "blocking": is_blocking(kind=str(kind), impact_kind=str(impact_kind), status=str(status)),
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
    "ASSIGNMENT_CHANGE_FIELDS",
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
    "ORG_SCOPE_ALL",
    "ORG_SCOPES",
    "ORG_SCOPE_UNCLOSED",
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
    "TARGET_ASSIGNMENT",
    "TARGET_KINDS",
    "TARGET_TASK",
    "AssignmentQuantityConflictError",
    "AssignmentQuantityStaleError",
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
    "can_write_case",
    "case_capabilities",
    "close_case",
    "count_open_cases",
    "decide",
    "get_case",
    "is_blocking",
    "list_blocking_cases",
    "list_cases",
    "list_cases_for_org",
    "list_quantity_changes",
    "project_quantity_change",
    "list_events",
    "list_links",
    "list_links_for_cases",
    "load_visible_case",
    "project_case_for_customer",
    "project_case_internal",
    "project_case_list_item",
    "project_case_ref",
    "raise_case",
    "remove_link",
    "reopen_case",
]
