"""exceptions 服务层规则的契约测试（DR-0013 §6.1 验证 1～6）。

这批用例**不起库、不建委托单** —— 它们钉的是语义：
「驳回处置方案不会解除真实异常」「降级严重度不放行」「关闭要回答凭什么关」。
这些结论必须能在纯函数层被证伪，否则它们会被埋在 IO 里、只在端到端偶尔暴露一次。

对应关系（DR-0013 §6.1）：
    1 → test_*transition* / test_self_transition*       非法转移 409
    2 → test_c1_*                                       C1 一致性
    3 → test_c3_*                                       C3 降级不放行
    4 → test_c2_*                                       C2 阻断须有对应动作
    5 → test_closure_*                                  关闭校验处置与证据
    6 → test_exception_rejected_*                       驳回后仍阻断
"""

from __future__ import annotations

import pytest

from app.modules.entrust import exceptions as ex

# ── 验证 1：非法转移（两套状态机分别覆盖）──────────────────────────────────


@pytest.mark.parametrize("kind", [ex.KIND_EXCEPTION, ex.KIND_CHANGE_REQUEST])
@pytest.mark.parametrize("status", sorted(ex.STATUSES))
def test_self_transition_is_rejected(kind: str, status: str) -> None:
    """自环 `x → x` 一律非法（PRD 第 279 行）。"""
    with pytest.raises(ex.ExceptionCaseConflictError):
        ex.assert_transition(kind=kind, from_status=status, to_status=status)


def test_closed_cannot_go_to_applied() -> None:
    """`closed → applied` 不是合法回退方向（重开只能回到 `open`）。"""
    with pytest.raises(ex.ExceptionCaseConflictError):
        ex.assert_transition(
            kind=ex.KIND_EXCEPTION, from_status=ex.STATUS_CLOSED, to_status=ex.STATUS_APPLIED
        )


def test_change_request_open_cannot_go_straight_to_applied() -> None:
    """变更请求 `open → applied` 跳过了批准，非法。"""
    with pytest.raises(ex.ExceptionCaseConflictError):
        ex.assert_transition(
            kind=ex.KIND_CHANGE_REQUEST, from_status=ex.STATUS_OPEN, to_status=ex.STATUS_APPLIED
        )


@pytest.mark.parametrize("kind", [ex.KIND_EXCEPTION, ex.KIND_CHANGE_REQUEST])
def test_closed_can_reopen(kind: str) -> None:
    """重开是允许的（AC-20 要求保留 legitimate reopen 的历史）。"""
    ex.assert_transition(kind=kind, from_status=ex.STATUS_CLOSED, to_status=ex.STATUS_OPEN)


def test_exception_open_can_be_rejected_but_change_request_open_can_too() -> None:
    """两者都能从 `open` 直接驳回 —— 但语义不同，见下面的阻断用例。"""
    for kind in (ex.KIND_EXCEPTION, ex.KIND_CHANGE_REQUEST):
        ex.assert_transition(kind=kind, from_status=ex.STATUS_OPEN, to_status=ex.STATUS_REJECTED)


# ── 验证 1'：未知取值必须抛错（fail-closed）────────────────────────────────


def test_unknown_values_raise_instead_of_passing() -> None:
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_transition(kind="banana", from_status=ex.STATUS_OPEN, to_status=ex.STATUS_CLOSED)
    with pytest.raises(ex.ExceptionCaseError):
        ex.allowed_transitions(ex.KIND_EXCEPTION, "banana")
    with pytest.raises(ex.ExceptionCaseError):
        ex.is_blocking(kind=ex.KIND_EXCEPTION, impact_kind="banana", status=ex.STATUS_OPEN)


# ── 验证 6：驳回处置方案**不**解除真实异常（本片最重要的一条）──────────────


def test_exception_rejected_still_blocks() -> None:
    """`exception` 的 `rejected` = 提交的处置方案被驳回 ⇒ 异常仍在 ⇒ 阻断保持。"""
    assert (
        ex.is_blocking(
            kind=ex.KIND_EXCEPTION,
            impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
            status=ex.STATUS_REJECTED,
        )
        is True
    )


def test_change_request_rejected_stops_blocking() -> None:
    """`change_request` 的 `rejected` = 变更被否 ⇒ 变更不会发生 ⇒ 不再阻断。

    同一个 `rejected`，两种 kind 结论相反 —— 这正是 "as applicable to type" 的落地。
    """
    assert (
        ex.is_blocking(
            kind=ex.KIND_CHANGE_REQUEST,
            impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
            status=ex.STATUS_REJECTED,
        )
        is False
    )


def test_exception_rejected_cannot_close_as_resolved() -> None:
    """驳回之后**不能**以 `resolved` 关闭 —— 否则就是「用驳回解除真实异常」。"""
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_transition(
            kind=ex.KIND_EXCEPTION,
            from_status=ex.STATUS_REJECTED,
            to_status=ex.STATUS_CLOSED,
            closure_disposition=ex.DISPOSITION_RESOLVED,
        )
    # 也不允许以 accepted_residual「豁免」掉。
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_transition(
            kind=ex.KIND_EXCEPTION,
            from_status=ex.STATUS_REJECTED,
            to_status=ex.STATUS_CLOSED,
            closure_disposition=ex.DISPOSITION_ACCEPTED_RESIDUAL,
        )
    # 但它可以因为「本单不该存在」而正常终结。
    ex.assert_transition(
        kind=ex.KIND_EXCEPTION,
        from_status=ex.STATUS_REJECTED,
        to_status=ex.STATUS_CLOSED,
        closure_disposition=ex.DISPOSITION_CANCELLED,
    )


# ── 阻断判据真值表 ─────────────────────────────────────────────────────────


def test_blocking_signature_has_no_severity() -> None:
    """`severity` 是展示用业务判断，**不进** allow/deny —— 它甚至不在函数签名里。

    把它加回来（哪怕只是默认参数）就等于开了「用严重度决定放行」的口子。
    这条用例钉的是**接口形状**：比「跑一个高严重度看结论不变」更难被绕过。
    """
    import inspect

    params = set(inspect.signature(ex.is_blocking).parameters)
    assert params == {"kind", "impact_kind", "status"}
    assert "severity" not in params


@pytest.mark.parametrize(
    "impact_kind",
    [ex.IMPACT_INFORMATIONAL, ex.IMPACT_REVIEW_REQUIRED],
)
@pytest.mark.parametrize("status", sorted(ex.STATUSES))
def test_non_blocking_impact_never_blocks(impact_kind: str, status: str) -> None:
    for kind in (ex.KIND_EXCEPTION, ex.KIND_CHANGE_REQUEST):
        assert ex.is_blocking(kind=kind, impact_kind=impact_kind, status=status) is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ex.STATUS_OPEN, True),
        (ex.STATUS_IN_REVIEW, True),
        (ex.STATUS_APPROVED, True),
        (ex.STATUS_REJECTED, True),  # ← 真实异常：驳回方案不解除
        (ex.STATUS_APPLIED, True),  # 还没关闭，阻断仍在
        (ex.STATUS_CLOSED, False),
    ],
)
def test_exception_blocking_by_status(status: str, expected: bool) -> None:
    assert (
        ex.is_blocking(
            kind=ex.KIND_EXCEPTION, impact_kind=ex.IMPACT_EXECUTION_BLOCKING, status=status
        )
        is expected
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ex.STATUS_OPEN, True),
        (ex.STATUS_IN_REVIEW, True),
        (ex.STATUS_APPROVED, True),
        (ex.STATUS_REJECTED, False),  # ← 变更被否：不会再发生，不阻断
        (ex.STATUS_APPLIED, True),
        (ex.STATUS_CLOSED, False),
    ],
)
def test_change_request_blocking_by_status(status: str, expected: bool) -> None:
    assert (
        ex.is_blocking(
            kind=ex.KIND_CHANGE_REQUEST,
            impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
            status=status,
        )
        is expected
    )


# ── 验证 2：C1（critical 必须阻断；单向）──────────────────────────────────


def test_c1_critical_requires_execution_blocking() -> None:
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_severity_impact_consistent(
            severity=ex.SEVERITY_CRITICAL, impact_kind=ex.IMPACT_REVIEW_REQUIRED
        )
    ex.assert_severity_impact_consistent(
        severity=ex.SEVERITY_CRITICAL, impact_kind=ex.IMPACT_EXECUTION_BLOCKING
    )


def test_c1_is_one_way_only() -> None:
    """`execution-blocking` 不要求 `critical` —— high 的异常一样可以阻断。"""
    for severity in ("low", "medium", "high"):
        ex.assert_severity_impact_consistent(
            severity=severity, impact_kind=ex.IMPACT_EXECUTION_BLOCKING
        )


# ── 验证 3：C3（降级严重度不自动放行）─────────────────────────────────────


def test_c3_severity_change_cannot_implicitly_flip_impact() -> None:
    """`critical → high` 同时把 impact 从 blocking 改成 review-required（隐式）→ 拒。"""
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_impact_change_is_explicit(
            current_impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
            requested_impact_kind=ex.IMPACT_REVIEW_REQUIRED,
            explicit_command=False,
        )


def test_c3_downgrade_keeps_blocking() -> None:
    """降级严重度后**不动** impact_kind ⇒ 阻断结论不变（这才是 HO 第 2 条的要求）。"""
    ex.assert_impact_change_is_explicit(
        current_impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
        requested_impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
        explicit_command=False,  # 没变，不需要显式命令
    )
    assert (
        ex.is_blocking(
            kind=ex.KIND_EXCEPTION,
            impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
            status=ex.STATUS_OPEN,
        )
        is True
    )


def test_c3_explicit_command_may_change_impact() -> None:
    """解除阻断有一条合法路径：显式命令改 `impact_kind`。"""
    ex.assert_impact_change_is_explicit(
        current_impact_kind=ex.IMPACT_EXECUTION_BLOCKING,
        requested_impact_kind=ex.IMPACT_INFORMATIONAL,
        explicit_command=True,
    )


# ── 验证 4：C2（阻断须明确对应动作）───────────────────────────────────────


def test_c2_blocking_requires_at_least_one_link() -> None:
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_blocking_requires_link(impact_kind=ex.IMPACT_EXECUTION_BLOCKING, link_count=0)
    ex.assert_blocking_requires_link(impact_kind=ex.IMPACT_EXECUTION_BLOCKING, link_count=1)


def test_c2_non_blocking_needs_no_link() -> None:
    ex.assert_blocking_requires_link(impact_kind=ex.IMPACT_INFORMATIONAL, link_count=0)


# ── 验证 5：关闭校验处置规则与证据 ────────────────────────────────────────


@pytest.mark.parametrize("kind", [ex.KIND_EXCEPTION, ex.KIND_CHANGE_REQUEST])
def test_closure_requires_disposition(kind: str) -> None:
    """没有 `closure_disposition` 一律 400 —— 不做默认值填充。"""
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_transition(kind=kind, from_status=ex.STATUS_OPEN, to_status=ex.STATUS_CLOSED)


def test_closure_resolved_requires_resolution_note() -> None:
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_closure_requirements(
            closure_disposition=ex.DISPOSITION_RESOLVED,
            severity="high",
            decision_note="决定",
            resolution_note="   ",
        )
    ex.assert_closure_requirements(
        closure_disposition=ex.DISPOSITION_RESOLVED,
        severity="high",
        decision_note=None,
        resolution_note="已完成变更并复核",
    )


def test_closure_accepted_residual_rejects_critical() -> None:
    """critical 按 C1 必然阻断，不能以「残留接受」关闭。"""
    with pytest.raises(ex.ExceptionCaseError):
        ex.assert_closure_requirements(
            closure_disposition=ex.DISPOSITION_ACCEPTED_RESIDUAL,
            severity=ex.SEVERITY_CRITICAL,
            decision_note="决定",
            resolution_note="证据",
        )
    ex.assert_closure_requirements(
        closure_disposition=ex.DISPOSITION_ACCEPTED_RESIDUAL,
        severity="high",
        decision_note="决定",
        resolution_note="证据",
    )


def test_closure_terminating_dispositions_require_decision_note() -> None:
    for disposition in (
        ex.DISPOSITION_CANCELLED,
        ex.DISPOSITION_DUPLICATE,
        ex.DISPOSITION_SUPERSEDED,
    ):
        with pytest.raises(ex.ExceptionCaseError):
            ex.assert_closure_requirements(
                closure_disposition=disposition,
                severity="low",
                decision_note=None,
                resolution_note="证据",
            )
        ex.assert_closure_requirements(
            closure_disposition=disposition,
            severity="low",
            decision_note="重复登记，已并入 #42",
            resolution_note=None,
        )


# ── 关闭处置的取值域按 (kind, 前状态) 分别限定 ────────────────────────────


def test_closure_dispositions_differ_by_kind_at_same_status() -> None:
    """同样是 `approved → closed`，两种 kind 允许的处置**不同**。"""
    exception_ok = ex.allowed_closure_dispositions(ex.KIND_EXCEPTION, ex.STATUS_APPROVED)
    change_ok = ex.allowed_closure_dispositions(ex.KIND_CHANGE_REQUEST, ex.STATUS_APPROVED)
    assert ex.DISPOSITION_DUPLICATE in exception_ok
    assert ex.DISPOSITION_DUPLICATE not in change_ok
    assert ex.DISPOSITION_CANCELLED in exception_ok and ex.DISPOSITION_CANCELLED in change_ok


def test_applied_exception_closes_only_as_resolved_or_residual() -> None:
    ok = ex.allowed_closure_dispositions(ex.KIND_EXCEPTION, ex.STATUS_APPLIED)
    assert ok == {ex.DISPOSITION_RESOLVED, ex.DISPOSITION_ACCEPTED_RESIDUAL}


def test_open_cannot_be_closed_as_resolved() -> None:
    """没经过 `applied` 就以 `resolved` 关闭 ⇒ 声称解决了却没有实际应用记录。"""
    for kind in (ex.KIND_EXCEPTION, ex.KIND_CHANGE_REQUEST):
        assert ex.DISPOSITION_RESOLVED not in ex.allowed_closure_dispositions(kind, ex.STATUS_OPEN)
        with pytest.raises(ex.ExceptionCaseError):
            ex.assert_transition(
                kind=kind,
                from_status=ex.STATUS_OPEN,
                to_status=ex.STATUS_CLOSED,
                closure_disposition=ex.DISPOSITION_RESOLVED,
            )


# ── 重开：闭合回 open（审计同事务的实现见下一片）─────────────────────────


@pytest.mark.parametrize("kind", [ex.KIND_EXCEPTION, ex.KIND_CHANGE_REQUEST])
def test_reopen_from_closed_to_open(kind: str) -> None:
    ex.assert_transition(kind=kind, from_status=ex.STATUS_CLOSED, to_status=ex.STATUS_OPEN)
    assert ex.allowed_transitions(kind, ex.STATUS_CLOSED) == {ex.STATUS_OPEN}
