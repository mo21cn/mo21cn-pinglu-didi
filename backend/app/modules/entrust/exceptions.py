"""异常与变更案件 —— 服务层**规则**（DR-0013 A1 切片二）。

本模块只放**规则**：取值域、两套状态机、阻断判据、一致性约束、关闭前置条件。
数据库读写与端点在下一片（`exceptions_api.py`）—— 规则与 IO 分开，
是为了让「驳回处置方案不会解除真实异常」这类**语义**能在纯函数层被用例钉住，
而不必先起一个库、一张委托单、一次认领。

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
驳回不解除真实异常、关闭校验处置规则与证据）、第 4 条（重开留审计 → 见下一片同事务实现）。
"""

from __future__ import annotations

from typing import Final

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


__all__ = [
    "DISPOSITIONS",
    "DISPOSITION_ACCEPTED_RESIDUAL",
    "DISPOSITION_CANCELLED",
    "DISPOSITION_DUPLICATE",
    "DISPOSITION_RESOLVED",
    "DISPOSITION_SUPERSEDED",
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
    "ExceptionCaseConflictError",
    "ExceptionCaseError",
    "ExceptionCaseNotFoundError",
    "allowed_closure_dispositions",
    "allowed_transitions",
    "assert_blocking_requires_link",
    "assert_closure_requirements",
    "assert_impact_change_is_explicit",
    "assert_severity_impact_consistent",
    "assert_transition",
    "is_blocking",
]
