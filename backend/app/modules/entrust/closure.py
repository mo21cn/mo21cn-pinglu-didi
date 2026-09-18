"""委托结案命令（`complete`）—— 合同 §6.4 五个维度 ＋ S4-b 切片。

为什么单开一个模块
------------------
结案**不是**"把 `status` 改成 `completed`"：它要同时读四个模块的事实
（任务与证据 `tasks` ／ 案件与重评 `exceptions` ＋ `ent_revalidation` ／
结算与收付 `settlement` ／ 费用 `charges`）。写进 `assignments.py` 会让受理链路的
最底层反向依赖上面四个（`access` / `authz` 都对着 `assignments`）—— 依赖方向会成环。

口径（**逐条给依据，不自己发明**）
----------------------------------
* 合同 §6.4 的五个维度（Task disposition / Evidence / Exceptions / Settlement /
  Balance-disputes）逐条落成 `missing[]` 的一项。⛔ **不返回"不可结案"一句了事**：
  只说"不行"会让调用方无法自助（DR-0013 §3.3 作用面 1）。
* 设计文档 `S4-委托结案状态机口径设计.md` §5.3.1：DEMO-1 期间**不存在**
  「`completed` ＋ `open`」这条可达路径 ⇒ 第 5 条前置（余额已结清）是**硬门槛**。
* §5.4 / §5.5：⛔ **不提供"管理员可跳过"的后门**（没有 `force` 参数、
  没有"高级模式"），PRD 明写 `Required hard checks cannot be bypassed`。
* §5.6 末条：`financial_status` 的**唯一**派生仍是
  `settlement.derive_financial_status`。本模块只把它的 blocker **归类**到维度上，
  **不复制第二份判据**；`FINANCE_BLOCKER_DIMENSIONS` 的完整性由用例钉住
  （漏一个 code，那条 blocker 就永远不会阻止结案）。

原子性与并发
------------
合同 §6.4 要求「evaluate all applicable predicates **atomically** with its state
transition」以及「Close-versus-new-blocking-case concurrency must be safe on MySQL」。
落法是**两把锁 ＋ 一条条件 UPDATE**（见 `complete_assignment` 的注释）：
委托行锁让两个结案者串行、让"结案 vs 登记新阻断案件"也串行；
条件 UPDATE 用 `status` ＋ `revision` 裁决，rowcount 为 0 即 409。
⚠️ SQLite 没有 `FOR UPDATE`（写了是语法错）且本身单写者 —— 它跑的是**降级路径**，
并发正确性证据只认 CI 的 `pytest -m mysql`（合同：`An exception query existing in the
code is not this proof`）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, cast

from fastapi import HTTPException
from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust import assignments as assignment_svc
from app.modules.entrust import exceptions as case_svc
from app.modules.entrust import locks
from app.modules.entrust import settlement as settlement_svc
from app.modules.entrust import tasks as task_svc
from app.modules.entrust.access import (
    PERM_ASSIGN_COMPLETE,
    find_active_entrustments,
    resolve_context,
)
from app.modules.entrust.authz import assert_org_member, load_entrustment, not_found

# ── 五个维度（顺序即报缺的书写顺序，与合同 §6.4 表格同序）─────────────────────

DIM_TASKS: Final = "tasks"
DIM_EVIDENCE: Final = "evidence"
DIM_EXCEPTIONS: Final = "exceptions"
DIM_SETTLEMENT: Final = "settlement"
DIM_BALANCE: Final = "balance"

DIMENSIONS: Final = (DIM_TASKS, DIM_EVIDENCE, DIM_EXCEPTIONS, DIM_SETTLEMENT, DIM_BALANCE)

DIMENSION_LABELS: Final[dict[str, str]] = {
    DIM_TASKS: "任务处置",
    DIM_EVIDENCE: "交付证据",
    DIM_EXCEPTIONS: "异常与重评",
    DIM_SETTLEMENT: "结算（内部确认＋客户确认）",
    DIM_BALANCE: "余额与争议",
}

#: `settlement.derive_financial_status` 的 blocker code → 本模块的维度。
#:
#: ⛔ 这张映射必须**全量**：新加一个 blocker code 而忘了归类，那条 blocker 就
#: 永远不会阻止结案（"派生说未结清、结案却放行"）。用例断言
#: `set(FINANCE_BLOCKER_DIMENSIONS) == 派生实际会产出的全部 code`。
FINANCE_BLOCKER_DIMENSIONS: Final[dict[str, str]] = {
    # 结算维度：缺版本 / 未内部确认 / 客户未确认该精确版本 / 快照已过期
    "settlement_missing": DIM_SETTLEMENT,
    "settlement_not_approved": DIM_SETTLEMENT,
    "customer_not_confirmed": DIM_SETTLEMENT,
    "settlement_stale": DIM_SETTLEMENT,
    # 余额与争议：未结费用行（草稿/争议）＋ 未结余额非零
    "unsettled_charges": DIM_BALANCE,
    "balance_unsettled": DIM_BALANCE,
    # 案件：与下面的案件检查同一件事（**同一个结论只报一次**的来源在代码里用
    # `_dedupe` 收口，不在映射里漏掉它 —— 漏掉就是"派生说未结、结案放行"）
    "open_cases": DIM_EXCEPTIONS,
}

#: `ent_revalidation.status` 的"待复核"取值（与 `revalidation.open_targets` /
#: `open_for_artifact` 的 SQL 同值；那边是字面量，没有常量可引用）。
REVALIDATION_OPEN: Final = "open"


# ── 异常 ─────────────────────────────────────────────────────────────────────


class ClosureError(assignment_svc.AssignmentError):
    """结案服务的基础异常。

    继承 `AssignmentError` 是**刻意的**：受理链路的写端点用
    `except (svc.AssignmentError, AccessDeniedError)` 统一兜底（`_http.run_write`），
    结案异常若自成一族，就会绕过那层映射一路抛成 500 —— 本仓已有一次同类事故
    （2026-09-16，越权认领抛 `AccessDeniedError` ⇒ HTTP 500，见 `router._http_error`
    的 docstring）。这里让类型进既有族，避免重犯。
    """


class ClosureBlockedError(ClosureError):
    """不满足结案前置（HTTP 409）—— **带 `missing[]`**。

    为什么不是 `AssignmentStateError`：那条异常的语义是"当前状态不允许这个动作"
    （如认领非 submitted）。结案被拒的原因不是状态错，而是**五种前置里若干条没满足**，
    需要把"缺什么"交给调用方。压成一句话会让前端只能显示"不可结案"。
    """

    def __init__(self, assignment_id: int, missing: list[dict[str, Any]]) -> None:
        self.assignment_id = assignment_id
        self.missing = missing
        dims: list[str] = []
        for item in missing:
            label = DIMENSION_LABELS.get(str(item.get("dimension")), str(item.get("dimension")))
            if label not in dims:
                dims.append(label)
        super().__init__(
            f"委托 {assignment_id} 不满足结案前置：{len(missing)} 条未通过"
            f"（{'、'.join(dims)}）。逐条见 missing[] —— 补齐后可用同一幂等键重试。"
        )


# ── 方言：行锁子句 ───────────────────────────────────────────────────────────


def _for_update(session: Session) -> str:
    """`FOR UPDATE` 子句（实现在 `locks.for_update_clause`，与登记案件共用一处）。

    ⛔ 不复刻第二份方言判断：两处不一致时，症状是"一边锁了、一边没锁"，
    而那只在真实并发下才看得见。本函数只是本地别名，读起来更短。
    """
    return locks.for_update_clause(session)


# ── 各维度的判据（每一条都复用既有模块的"唯一判据"）────────────────────────────


def _task_dimension_missing(
    gaps: dict[str, Any],
) -> list[dict[str, Any]]:
    """维度 1/2：任务处置 ＋ 交付证据（**同一份派生读模型**的两种视角）。

    依据 §5.4 第 1、2 条：任务全部 `completed` 或按 disposition `cancelled`；
    要求的交付证据齐备。

    两个刻意的取舍：

    * **`cancelled` 的任务不再算它缺证据**：任务已被合法处置掉，它的证据要求
      随任务一起消失。否则"取消掉一个拿不到证据的任务"反而更结不了案 ——
      那会把人逼回"改库造证据"。
    * **没有交接任务不算缺**（`handover.present=False`）：无从判断 ⇏ 不满足。
      与 S7-2 的口径一致（`satisfied` 在无交接任务时是 `None`，不是 `True`）。
    """
    out: list[dict[str, Any]] = []
    unfinished = [
        item for item in gaps["items"] if item["status"] not in task_svc.TERMINAL_STATUSES
    ]
    if unfinished:
        out.append(
            {
                "dimension": DIM_TASKS,
                "code": "tasks_not_disposed",
                "message": (
                    f"有 {len(unfinished)} 个任务尚未处置完成（结案要求全部完成，或按处置规则取消）"
                ),
                "detail": {
                    "count": len(unfinished),
                    "tasks": [
                        {
                            "task_id": item["task_id"],
                            "task_type": item["task_type"],
                            "title": item["title"],
                            "status": item["status"],
                        }
                        for item in unfinished[:20]
                    ],
                },
            }
        )

    missing_evidence = [
        item
        for item in gaps["items"]
        if item["missing"] and item["status"] != task_svc.STATUS_CANCELLED
    ]
    if missing_evidence:
        out.append(
            {
                "dimension": DIM_EVIDENCE,
                "code": "required_evidence_missing",
                "message": (
                    f"有 {len(missing_evidence)} 个任务的要求证据未登记"
                    "（缺就是缺 —— 可以先在**原任务**上补录，不需要新建工作流）"
                ),
                "detail": {
                    "count": len(missing_evidence),
                    "total_missing": sum(len(item["missing"]) for item in missing_evidence),
                    "tasks": [
                        {
                            "task_id": item["task_id"],
                            "title": item["title"],
                            "missing": item["missing"],
                        }
                        for item in missing_evidence[:20]
                    ],
                },
            }
        )
    return out


def _unclosed_cases(
    session: Session, *, assignment_id: int, lock: bool = False
) -> list[dict[str, Any]]:
    """该委托**未关闭**的全部案件（不看严重度/影响类型 —— 由 `is_blocking` 判）。

    `lock=True` 时走**当前读**（`FOR UPDATE`，MySQL）：REPEATABLE READ 下普通
    `SELECT` 返回的是事务开始时的快照，而"结案 vs 并发登记新案件"这条竞态恰恰
    要求看见**刚刚提交**的那一行。顺带在 `(assignment_id, status)` 索引上取到的
    next-key 锁会把并发插入**挡在提交之后** —— 这就是那条竞态的收口处。
    """
    sql = (
        "SELECT id, kind, severity, impact_kind, status, title FROM ent_exception "
        "WHERE assignment_id = :aid AND status != :closed ORDER BY id ASC"
    )
    if lock:
        sql += _for_update(session)
    rows = (
        session.execute(text(sql), {"aid": assignment_id, "closed": case_svc.STATUS_CLOSED})
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _open_revalidation_count(session: Session, *, assignment_id: int, lock: bool = False) -> int:
    """该委托下**未完成的复核项**数（合同 §6.4：`unresolved blocking revalidation`）。

    判据与 AC-12 那格同源：`confirm_revision` 遇到**任一**未完成复核项即 409
    （`artifacts.confirm_revision` → `ArtifactRevalidationError`），不按严重度过滤。
    这里沿用同一条（不为结案另立一个更宽的判据 —— 否则会出现"成果确认被挡、
    结案却放行"）。
    """
    sql = (
        "SELECT COUNT(*) FROM ent_revalidation r "
        "JOIN ent_exception c ON c.id = r.exception_id "
        "WHERE c.assignment_id = :aid AND r.status = :open"
    )
    if lock:
        sql += _for_update(session)
    return int(
        session.execute(text(sql), {"aid": assignment_id, "open": REVALIDATION_OPEN}).scalar() or 0
    )


def _case_dimension_missing(
    session: Session, *, assignment_id: int, lock: bool = False
) -> list[dict[str, Any]]:
    """维度 3：异常与重评。

    三条事实合起来回答合同 §6.4 的 `No unresolved blocking case or unresolved
    blocking revalidation remains` ＋ §5.4 第 3 条的「非关键残留须有显式 disposition」：

    * **有阻断案件** ⇒ 缺（判据是 `exceptions.is_blocking`，与 `complete_task` 共用
      同一条 —— 两处各写一遍迟早出现"执行命令认阻断、结案不认"）；
    * **有未完成复核项** ⇒ 缺；
    * **有未关闭的案件** ⇒ 缺。这一条的读法说明在 `closure_readiness` 的 docstring 里
      （它是"残留须有显式 disposition"在全仓唯一可核对形态下的落法），
      并且与 `settlement.derive_financial_status` 的 `open_cases` **同一结论**——
      ⛔ 两处给出相反答案比两处都严格糟得多。
    """
    out: list[dict[str, Any]] = []
    unclosed = _unclosed_cases(session, assignment_id=assignment_id, lock=lock)
    blocking = [
        row
        for row in unclosed
        if case_svc.is_blocking(
            kind=str(row["kind"]),
            impact_kind=str(row["impact_kind"]),
            status=str(row["status"]),
        )
    ]
    if blocking:
        out.append(
            {
                "dimension": DIM_EXCEPTIONS,
                "code": "blocking_cases_open",
                "message": (
                    f"有 {len(blocking)} 条**阻断类**案件尚未解决"
                    "（阻断不因严重度解除，也不接受管理员跳过）"
                ),
                "detail": {
                    "count": len(blocking),
                    "cases": [
                        {
                            "case_id": int(row["id"]),
                            "kind": str(row["kind"]),
                            "severity": str(row["severity"]),
                            "impact_kind": str(row["impact_kind"]),
                            "status": str(row["status"]),
                            "title": str(row["title"]),
                        }
                        for row in blocking[:20]
                    ],
                },
            }
        )

    open_reval = _open_revalidation_count(session, assignment_id=assignment_id, lock=lock)
    if open_reval:
        out.append(
            {
                "dimension": DIM_EXCEPTIONS,
                "code": "revalidation_open",
                "message": (
                    f"有 {open_reval} 项受影响的复核**未完成**"
                    "（解除路径只有一条：完成对应的复核任务）"
                ),
                "detail": {"count": open_reval},
            }
        )

    residual = [row for row in unclosed if row not in blocking]
    if residual:
        out.append(
            {
                "dimension": DIM_EXCEPTIONS,
                "code": "cases_not_closed",
                "message": (
                    f"有 {len(residual)} 条案件未关闭 —— 残留必须**显式处置**"
                    "（关闭时必填 `closure_disposition`，由案件关闭命令强制；"
                    "非关键残留可用 accepted_residual，但要给决定与处置说明）"
                ),
                "detail": {
                    "count": len(residual),
                    "cases": [
                        {
                            "case_id": int(row["id"]),
                            "kind": str(row["kind"]),
                            "severity": str(row["severity"]),
                            "status": str(row["status"]),
                            "title": str(row["title"]),
                        }
                        for row in residual[:20]
                    ],
                },
            }
        )
    return out


def _finance_dimension_missing(state: dict[str, Any]) -> list[dict[str, Any]]:
    """维度 4/5：结算（内部＋客户确认）与余额/争议 —— 由**唯一**派生归类而来。

    ⛔ 不在这里重算总额、不在这里重判"客户确认了没有"：那些判据只在
    `settlement.derive_financial_status` 里有一份（`state` 由调用方取一次、传进来，
    ⛔ 不在本函数里再取一次 —— 同一事务里对同一事实取两次，就可能拿到两个答案）。
    本函数只做**归类**：`open_cases` 那条归到案件维度，与
    `_case_dimension_missing` 的结论重合 ⇒ 由调用方去重（同一条事实只报一次，
    且报在案件维度下 —— 那条带逐条 id，调用方能直接去关闭）。
    """
    out: list[dict[str, Any]] = []
    status = str(state["financial_status"])
    if status != settlement_svc.FINANCIAL_SETTLED and not state["blockers"]:
        # ⚠️ 这一格是**必须**的：`derive_financial_status` 在"一件事都还没开始"时
        # **提前返回** `not_started` 且 `blockers` 为空（那是刻意的 —— 还没开始结算时
        # "还没有结算版本"是同义反复，不是待办）。若本模块只按 `blockers` 归类，
        # 一张**毫无财务事实**的委托就会"第 4/5 条前置全过"而结案。
        # §5.3.2 写得很清楚：`settled` 的正向判据是四条全不成立**且余额已显式处置**，
        # ⛔ 不是"没有 blocker"。没有事实 ⇏ 已结清。
        out.append(
            {
                "dimension": DIM_SETTLEMENT,
                "code": "financial_not_started",
                "message": (
                    "还没有任何财务事实（无费用行 / 无结算版本 / 无收付依据）"
                    "—— 结案要求结算已批准且余额按已记录的收付依据结清，"
                    "「没有 blocker」不等于「已结清」"
                ),
                "detail": {"financial_status": status},
            }
        )
        return out

    for blocker in state["blockers"]:
        code = str(blocker["code"])
        dimension = FINANCE_BLOCKER_DIMENSIONS.get(code)
        if dimension is None:
            # 派生加了新 code 而这张映射没跟 —— **不许静默跳过**（跳过的后果是
            # "派生说未结清、结案却放行"）。这里显式补一条兜底缺项。
            out.append(
                {
                    "dimension": DIM_BALANCE,
                    "code": f"unmapped_finance_blocker:{code}",
                    "message": (
                        f"财务派生给出了一条本模块尚未归类的未结成因 {code!r}"
                        "—— 在 FINANCE_BLOCKER_DIMENSIONS 里归类后才能结案"
                    ),
                    "detail": dict(blocker.get("detail") or {}),
                }
            )
            continue
        out.append(
            {
                "dimension": dimension,
                "code": code,
                "message": str(blocker["message"]),
                "detail": dict(blocker.get("detail") or {}),
            }
        )
    return out


def _dedupe(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一件事只报一次（见 `_finance_dimension_missing` 的说明）。

    财务派生的 `open_cases` 与案件维度的两条（`blocking_cases_open` /
    `cases_not_closed`）读的是**同一批行**。只要案件维度已经报了，就把派生那条去掉 ——
    ⛔ 否则调用方会看到"有阻断案件"和"有 N 条案件没关"两条讲同一件事的缺项，
    而它们对**处置动作**的指向还不一样。

    ⚠️ `revalidation_open` 不参与去重：复核项与案件是两种不同的记录
    （一个案件可以有 0..n 项复核），关掉案件不等于完成复核。
    """
    covered = {"blocking_cases_open", "cases_not_closed"}
    if any(item["code"] in covered for item in missing):
        return [item for item in missing if item["code"] != "open_cases"]
    return missing


# ── 只读：五维度齐备度 ───────────────────────────────────────────────────────


def _evaluate(
    session: Session, *, assignment_id: int, actor_id: int, lock: bool
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """五维度的**唯一**一处评估 —— 读（`closure_readiness`）与写（`complete`）共用。

    ⛔ 读写各写一遍是"页面说可以、命令说不行"这种缺陷的标准成因；共用一份之后，
    两处**结构上**不可能给出不同的缺项。`lock` 只影响案件侧那两条查询是否走当前读
    （写路径要，读路径不要 —— 只读端点不该去锁 `ent_exception` 的索引区间）。

    Returns:
        `(missing, 证据缺件派生, 财务派生)` —— 后两项一并返回，因为两个调用方都要用，
        而同一事务里对同一事实**取两次**就可能拿到两个答案。
    """
    gaps = task_svc.list_evidence_gaps(session, user_id=actor_id, assignment_id=assignment_id)
    state = settlement_svc.derive_financial_status(session, assignment_id=assignment_id)
    missing = [
        *_task_dimension_missing(gaps),
        *_case_dimension_missing(session, assignment_id=assignment_id, lock=lock),
        *_finance_dimension_missing(state),
    ]
    return _dedupe(missing), gaps, state


# ── 只读：五维度齐备度 ───────────────────────────────────────────────────────


def closure_readiness(session: Session, *, assignment_id: int, user_id: int) -> dict[str, Any]:
    """**结案齐备度**：五个维度逐条报缺（纯派生，不落库）。

    调用方的两条约定：

    * `user_id` 用于**任务派生的可见性校验**（`tasks.list_evidence_gaps` 自带那条），
      与端点层的授权是同一条链，不是第二把锁；
    * `missing` 与 `complete` 的 409 用的是**同一份评估**（`_evaluate`）。
      ⚠️ 但两者**不等价**：`ready=True` 只说"五条前置成立"，
      `complete` 还要求委托处于 `claimed`（已结案/未受理都会 409）。
      把状态也塞进 `ready` 会让这个字段同时回答两个问题。

    `cases_not_closed` 的读法（**可争议**，实现说明与依据见
    `docs/entrust/S4-b-结案命令切片.md` §2）：
    §5.4 第 3 条要求"非关键残留须有显式 disposition"，而仓库里 disposition 的载体
    `ent_exception.closure_disposition` **只在关闭时**落（列注释：`关闭时必填`）
    ⇒ "有显式 disposition"在当前结构下唯一可核对的形态就是"这条案件已经关闭"。
    因此这里把"未关闭的非阻断案件"也报成缺项，并要求调用方**关闭它**（关闭命令本身
    会强制 disposition 与证据）而不是把它留在开着。同一件事在
    `derive_financial_status` 里也是 `open_cases`（两处必须同结论）。
    """
    missing, gaps, state = _evaluate(
        session, assignment_id=assignment_id, actor_id=user_id, lock=False
    )

    by_dimension = dict.fromkeys(DIMENSIONS, 0)
    for item in missing:
        by_dimension[str(item["dimension"])] = by_dimension.get(str(item["dimension"]), 0) + 1

    return {
        "assignment_id": assignment_id,
        "ready": not missing,
        "missing": missing,
        "missing_by_dimension": by_dimension,
        "tasks_total": gaps["tasks_total"],
        "handover": gaps["handover"],
        # 如实转述派生（不改写、不解释成结论）—— 界面要"区分运营与财务"就靠它
        "financial_status": state["financial_status"],
        "applicable_settlement": state["applicable_settlement"],
        "balances": state["balances"],
    }


def read_closure_readiness(session: Session, *, assignment_id: int, user_id: int) -> dict[str, Any]:
    """读端点用的包装：先 404（不存在／非参与方**同一口径**），再过权限。

    ⚠️ 判据取的是**结案那一把锁**（`entrust:assignment:complete`），**不是**
    "能看财务"（`assert_can_view_org`）—— 这份清单里带着结算版本、余额与案件处置，
    是**运营侧口径**；而"能看到缺项的人就该是能执行的人"这条等式一旦打破，界面就会长出
    "看得见缺什么、按钮点下去 403"的形态（`charges_api` 那一族刻意相反，理由见彼处）。
    """
    assignment = assignment_svc.get_assignment(session, assignment_id)
    if assignment is None:
        raise assignment_svc.AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
    _assert_can_complete(session, assignment=assignment, user_id=user_id)
    return closure_readiness(session, assignment_id=assignment_id, user_id=user_id)


# ── 写：结案命令 ─────────────────────────────────────────────────────────────


def complete_assignment(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """结案：`claimed → completed`（**原子**评估五维度前置 ＋ 状态迁移）。

    与 `claim` / `cancel` 的差别，以及为什么这里**要求** `expected_revision`：

    * `claim` / `cancel` 的裁决点是状态本身（`WHERE status = 'submitted'`），
      谁先把状态推走谁赢，不需要额外版本号；
    * 结案的裁决点是**五个维度的一堆事实**，而"两个经理同时结案"必须有一个拿到
      409（HO 0917 第 3 条）。条件 UPDATE 里带上 `revision` 才是那条裁决 ——
      只判状态的话，第二个到达者会看到已经是 `completed` 并得到一个"状态不对"的
      409，看起来对、实际是**碰巧**对（中途任何一次编辑都会让 revision 先行变化）。

    顺序（每一步都有理由，别重排）：

    1. 取对象 → **404**（不存在/非参与方同一口径）；
    2. 权限 → 组织成员 ＋ 该货主授权上有 `entrust:assignment:complete`（否则 403）；
    3. 状态守卫：已结案 ⇒ 409（幂等重放走 `Idempotency-Key`，⛔ 不靠静默成功，
       否则"重开之后又结一次"与"早就结过"会得到同一个响应）；
    4. `expected_revision` 核对 ⇒ 409；
    5. **拿委托行锁**（MySQL）⇒ 与另一个结案者、以及"并发登记新阻断案件"串行；
       拿到锁后**重读**一次状态/版本（锁前读的可能是旧快照）；
    6. 评估五维度（案件的两次查询走**当前读**）⇒ 有缺即 409 ＋ `missing[]`；
    7. 条件 UPDATE（`status` ＋ `revision`）⇒ rowcount 0 即 409；
    8. 提交后重读返回。
    """
    assignment = assignment_svc.get_assignment(session, assignment_id)
    if assignment is None:
        raise assignment_svc.AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
    _assert_can_complete(session, assignment=assignment, user_id=actor_id)

    status = str(assignment["status"])
    if status == assignment_svc.STATUS_COMPLETED:
        raise assignment_svc.AssignmentStateError(
            f"委托单 {assignment_id} 已经结案（completed_at={assignment.get('completed_at')}）"
            "—— 重复提交不产生第二条结案事件；同键重放由 Idempotency-Key 负责"
        )
    if status != assignment_svc.STATUS_CLAIMED:
        raise assignment_svc.AssignmentStateError(
            f"委托单 {assignment_id} 状态为 {status}，只有已受理（claimed）的委托可以结案"
        )
    if int(expected_revision) != int(assignment["revision"]):
        raise assignment_svc.RevisionConflictError(
            f"版本冲突：当前 revision={assignment['revision']}，"
            f"请求基于 revision={expected_revision}（数据可能已被他人修改）"
        )

    # ⑤ 行锁 ＋ 锁后重读
    _lock_assignment_row(session, assignment_id=assignment_id)
    locked = assignment_svc.get_assignment(session, assignment_id)
    if locked is None:
        session.rollback()
        raise assignment_svc.AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
    if str(locked["status"]) != assignment_svc.STATUS_CLAIMED or int(locked["revision"]) != int(
        expected_revision
    ):
        session.rollback()
        raise assignment_svc.RevisionConflictError(
            f"委托单 {assignment_id} 在结案评估期间被其他写者改动"
            f"（status={locked['status']} revision={locked['revision']}）"
        )

    # ⑥ 五维度评估（案件侧走当前读）
    missing = _collect_missing(session, assignment_id=assignment_id, actor_id=actor_id)
    if missing:
        session.rollback()
        raise ClosureBlockedError(assignment_id, missing)

    # ⑦ 条件 UPDATE：`status` ＋ `revision` 双条件 —— 两者的裁决点都必须在 SQL 里
    current = _fmt(now or assignment_svc.utcnow_naive())
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "UPDATE ent_assignment SET status = :to, completed_at = :ts, "
                "revision = revision + 1, updated_at = :ts "
                "WHERE id = :aid AND status = :from AND revision = :rev"
            ),
            {
                "to": assignment_svc.STATUS_COMPLETED,
                "from": assignment_svc.STATUS_CLAIMED,
                "rev": int(expected_revision),
                "ts": current,
                "aid": assignment_id,
            },
        ),
    )
    if int(result.rowcount or 0) == 0:
        session.rollback()
        raise assignment_svc.RevisionConflictError(
            f"委托单 {assignment_id} 已被其他写者改动，结案未生效（revision 或状态已变）"
        )
    session.commit()

    final = assignment_svc.get_assignment(session, assignment_id)
    assert final is not None
    return final


def _collect_missing(
    session: Session, *, assignment_id: int, actor_id: int
) -> list[dict[str, Any]]:
    """写路径的缺项（走**当前读**，见 `_case_dimension_missing`）。

    实现只有一份（`_evaluate`）—— 与 `closure_readiness` 的差别**仅**在于是否加锁。
    """
    missing, _gaps, _state = _evaluate(
        session, assignment_id=assignment_id, actor_id=actor_id, lock=True
    )
    return missing


def _lock_assignment_row(session: Session, *, assignment_id: int) -> None:
    """取这张委托的**行锁**（实现在 `locks.lock_assignment_row`，与登记案件共用一处）。

    为什么必须有它（合同 §6.4 第二条）：没有行锁时，"结案评估"与"并发登记一条
    阻断案件"是两个互不知情的读/写 ⇒ 可能出现"评估时没有阻断案件、提交后却有了"，
    而结案已经把状态推成 `completed`。拿到行锁之后：

    * 两个结案者串行（后到者重读时看到 `completed` ⇒ 409）；
    * `exceptions.raise_case` 也先取**同一把锁**再复核状态 ⇒ 要么它先提交
      （本次评估看得见那条阻断案件 ⇒ 409），要么结案先提交（它随后拒绝在已结案的
      委托上登记案件）。**两个方向都有明确的拒绝者**，不存在"两件都成立"。
    """
    locks.lock_assignment_row(session, assignment_id=assignment_id)


def _fmt(dt: datetime) -> str:
    """时间列统一写成 `YYYY-MM-DD HH:MM:SS`（与 `assignments._fmt` 同格式）。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _assert_can_complete(session: Session, *, assignment: dict[str, Any], user_id: int) -> None:
    """结案的权限：组织成员 ＋（组织, 货主）作用域上有 `entrust:assignment:complete`。

    货主本人**不能**结案：结案是运营方对客户的宣告（合同 §6.4 要客户确认**结算**，
    但"宣布完成"是运营侧动作）。所以这里**不用** `assert_can_view_assignment`
    （它带货主旁路），而是先按参与方判 404、再按组织权限判 403：

    * 非参与方（既不是货主、也不是该组织成员）⇒ 404，不泄漏存在性；
    * 该组织成员但无 `entrust:assignment:complete` ⇒ 403（如 `member` 只读角色）；
      货主在本条上同样落到 404 —— 与内部通道的既有口径一致。
    """
    org_id = assignment["org_id"]
    if org_id is None:
        raise not_found("委托单不存在")
    context = resolve_context(session, user_id=user_id)
    assert_org_member(context, org_id=int(org_id), detail="委托单不存在")

    owner_id = int(assignment["owner_user_id"])
    ids = find_active_entrustments(session, org_id=int(org_id), owner_user_id=owner_id)
    entrustment = load_entrustment(session, int(ids[0])) if ids else None
    if entrustment is None:
        raise not_found("委托单不存在")
    if not context.can(PERM_ASSIGN_COMPLETE, owner_user_id=owner_id):
        raise HTTPException(
            status_code=403,
            detail=(f"用户 {user_id} 缺少权限 {PERM_ASSIGN_COMPLETE}（作用于货主 {owner_id}）"),
        )


__all__ = [
    "DIMENSION_LABELS",
    "DIMENSIONS",
    "DIM_BALANCE",
    "DIM_EVIDENCE",
    "DIM_EXCEPTIONS",
    "DIM_SETTLEMENT",
    "DIM_TASKS",
    "FINANCE_BLOCKER_DIMENSIONS",
    "ClosureBlockedError",
    "ClosureError",
    "closure_readiness",
    "complete_assignment",
]
