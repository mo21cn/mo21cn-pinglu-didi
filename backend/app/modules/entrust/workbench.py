"""委托工作台（UI-05）—— 七槽位**只读**聚合投影。

权威依据
--------
* [`docs/entrust/decisions/0010-工作台七槽位定义.md`](../../../../docs/entrust/decisions/0010-工作台七槽位定义.md)
  §3.1（七槽位的顺序与稳定 key）、§3.3（槽位 ↔ 数据落点）、§3.5（四个派生字段的推导）、
  §3.6（空值四态）、§3.7（Agent 入口判据）、§3.8（首版人工落点要求）；
* [`0012-成果归属.md`](../../../../docs/entrust/decisions/0012-成果归属.md) ——
  成果能归属到**单张委托**（`ent_artifact.assignment_id`），本模块的成果读数按它精确匹配。

为什么是「一条聚合查询」而不是每槽一个接口
------------------------------------------
DR-0010 §3.2：**一条单委托工作台聚合查询**返回七槽位摘要（每槽四字段 + 计数），
详情按需加载；**不**为每槽各建一套 CRUD、表或成果类型。
槽位是**投影的呈现单位**，不是数据边界 —— 七个接口会把"呈现单位"固化成"数据边界"，
此后每调整一次槽位划分都要动一批接口与作用域矩阵。

本模块**只读**：不写表、不动状态机、不新增迁移、不加表。
`SLOT_SPECS` 是槽位定义的**唯一真相**；前端配置表与它逐字对齐，由
`scripts/verify_entrust_ui.js` 静态核对（DR-0010 验证 #1）。

空值四态不是「一个 null 走天下」
--------------------------------
DR-0010 §3.6 要求区分「暂无记录 / 尚未分配 / 不适用 / 信息缺失」，禁止一律显示
"待补充"。四态在本模块有**各自的落点**，界面上是四句不同的话：

============  ==========================  ==========================================
态            字段                        判据
============  ==========================  ==========================================
暂无记录       `current.state`             该槽业务上确实还没产生记录
尚未分配       `next_owner.state`          有未完成任务，但还没有负责人
不适用         `next_owner.state`          该槽没有待推进的任务（已全完成/取消）
信息缺失       `issues.state`              应有但缺失：成果必填字段为空、委托未填货量…
============  ==========================  ==========================================

「不适用」与「尚未分配」必须分开：前者的下一步动作不存在，后者是"去找个人来负责"。
把它们合成一句"待补充"，用户就分不清该不该动。

「本期未开放」≠「暂无记录」
---------------------------
槽位可以**声明为「本期未开放」**（`SlotSpec.open=False` + `not_open_reason`）：那时走
独立的 `available=False` + `unavailable_reason` 通道，**不套用**空值四态 —— 按四态
呈现，用户会把"这个能力还没做"读成"这单没有异常"。声明为未开放却**没给理由**
是配置漂移，`assert_slot_specs_consistent` 会拦下（否则界面只能猜）。

`exceptions` 槽曾用它挡了整条支线：投影**先写好**、由 `EXCEPTIONS_SLOT_OPEN` **一个常量**
决定用不用。不先撤标记再补投影（中间态会对外撒谎），也不等前端做完再写投影
（那样"真实投影"无法先被用例固定住，撤下时就只剩"看起来差不多"）。

**该标记已于 2026-09-14 撤下**：DR-0013 §7.3 的五条**同一提交内**同时满足，其中条件 4
（人工落点〔登记案件 + 记录决定 + 关闭〕在**真载荷走查**中走通）的证据见
`docs/ENT-030-案件页真机走查交付说明.md`（真机点击走通，PASS 67 / FAIL 0）。
常量与机制都保留：DR-0013 §5 的回退口径依赖那个常量，而"把某块能力整体收回去"
这条路**下次还会用**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.entrust import exceptions as case_svc
from app.modules.entrust import settlement as settlement_svc
from app.modules.entrust.artifacts import (
    STATUS_ACTIVE as ARTIFACT_STATUS_ACTIVE,
)
from app.modules.entrust.artifacts import (
    count_unassigned,
)
from app.modules.entrust.registry import ARTIFACT_TYPES, validate_payload
from app.modules.entrust.tasks import (
    OPEN_STATUSES as TASK_OPEN_STATUSES,
)
from app.modules.entrust.tasks import (
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    STATUS_WAITING,
    TASK_TYPE_CONTRACT,
    TASK_TYPE_EXECUTION,
    TASK_TYPE_HANDOVER,
    TASK_TYPE_PURCHASE,
    TASK_TYPE_QUOTE,
    TASK_TYPE_SETTLEMENT,
    TASK_TYPES,
)

# ── 空值四态（DR-0010 §3.6）───────────────────────────────────────────────────

#: 「当前成果」两态
CURRENT_PRESENT = "present"
CURRENT_NO_RECORD = "no_record"

#: 「未决问题」三态
ISSUES_NONE = "none"
ISSUES_PRESENT = "present"
ISSUES_MISSING_INFO = "missing_info"

#: 「下一责任方」三态
OWNER_ASSIGNED = "assigned"
OWNER_UNASSIGNED = "unassigned"
OWNER_NOT_APPLICABLE = "not_applicable"

#: 未决问题的类别 —— 界面按类别选文案，**不用字符串前缀猜**
ISSUE_MISSING_FIELD = "missing_field"
ISSUE_BLOCKED = "blocked"
ISSUE_WAITING = "waiting"
ISSUE_UNASSIGNED_TASK = "unassigned_task"
ISSUE_INACTIVE_ARTIFACT = "inactive_artifact"

#: `exceptions` 槽是否对用户开放（= 曾用来挡「本期未开放」标记的那个开关）。
#:
#: **`True`**：DR-0013 §7.3 的五条已于 2026-09-14 **同一提交内**同时满足 ——
#: 三张表由版本化迁移建、两套状态机与阻断判据有服务端实现与用例、四字段走真实投影、
#: **人工落点（登记案件 + 记录决定 + 关闭）在真载荷走查中走通**
#: （`docs/ENT-030-案件页真机走查交付说明.md`，真机点击 PASS 67 / FAIL 0）、
#: 客户投影白名单有用例。
#:
#: 收回去同样是**独立的一次提交**（本常量 + 用例 + 走查证据），不是顺手改一个布尔值：
#: 提前撤下（或再次收回却不给理由）等于把「能力还没做」说成「这单没有异常」。
#:
#: 前端半边在 `scripts/verify_entrust_ui.js`：`available=false` 时前端必须按
#: 「本期未开放」渲染，**不得**退化成空值四态 —— 机制仍在（见 `SlotSpec.open`），
#: 只是当前七个槽位没有一个走这条路。
EXCEPTIONS_SLOT_OPEN: Final = True


class WorkbenchConfigError(RuntimeError):
    """槽位声明与任务/成果取值域不一致（配置漂移，必须在测试期就失败）。"""


@dataclass(frozen=True)
class SlotSpec:
    """一个槽位的声明。顺序即 `SLOT_SPECS` 的书写顺序（DR-0010 §3.1 不得重排）。"""

    key: str
    title: str
    #: 该槽位读取的任务类型；`all_tasks=True` 时忽略本字段
    task_types: tuple[str, ...] = ()
    #: 该槽位读取的成果类型
    artifact_types: tuple[str, ...] = ()
    #: 读**全单任务**（"方案与任务"是任务总览，不是某个 task_type 的子集）
    all_tasks: bool = False
    #: `False` 表示该槽位的能力**本期未开放** —— 必须显式标注（DR-0010 §3.8），
    #: 不得退化成"暂无记录"
    open: bool = True
    not_open_reason: str = ""


# ── 七槽位定案（顺序与 key 由 DR-0010 §3.1 固定，不得重排）──────────────────────

SLOT_SPECS: tuple[SlotSpec, ...] = (
    SlotSpec(key="overview", title="委托概况"),
    SlotSpec(key="plan_tasks", title="方案与任务", all_tasks=True),
    SlotSpec(
        key="procurement",
        title="采购与报价",
        task_types=(TASK_TYPE_QUOTE, TASK_TYPE_PURCHASE),
        artifact_types=("quote_parsed", "supplier_compare", "procurement_confirm"),
    ),
    SlotSpec(
        key="customer_contracts",
        title="对客方案与合同",
        task_types=(TASK_TYPE_CONTRACT,),
        artifact_types=("customer_quote", "contract_review"),
    ),
    SlotSpec(
        key="execution",
        title="履约与交接",
        task_types=(TASK_TYPE_EXECUTION, TASK_TYPE_HANDOVER),
    ),
    SlotSpec(
        key="exceptions",
        title="异常与变更",
        # 2026-09-14 起开放（DR-0013 §7.3 五条满足，证据见模块 docstring）。
        # 开关仍是常量而不是字面量：再次收回要有据可依、有据可查。
        open=EXCEPTIONS_SLOT_OPEN,
    ),
    SlotSpec(
        key="settlement",
        title="费用与结案",
        task_types=(TASK_TYPE_SETTLEMENT,),
        artifact_types=("settlement_draft",),
    ),
)

SLOT_ORDER: tuple[str, ...] = tuple(spec.key for spec in SLOT_SPECS)
SLOT_TITLES: dict[str, str] = {spec.key: spec.title for spec in SLOT_SPECS}

#: 未完成任务的"下一责任方"优先级：进行中最紧，其次待开始，再次等待外部
_OWNER_RANK: dict[str, int] = {STATUS_IN_PROGRESS: 0, STATUS_PENDING: 1, STATUS_WAITING: 2}

_TASK_COLS = "id, task_type, title, status, assignee_user_id, precondition_task_id, updated_at"
_ARTIFACT_COLS = (
    "a.id, a.artifact_type, a.status, a.current_revision_id, "
    "r.revision_no, r.payload_json, a.updated_at"
)
#: 案件投影只要这 8 列。**有意不含 `severity`**：它不参与阻断判定，
#: 本槽位也不拿它排序或分组 —— 取进来就会有人顺手用它，那就等于悄悄给它流程含义。
_CASE_COLS = (
    "id, assignment_id, kind, title, status, impact_kind, owner_user_id, raised_at, updated_at"
)


def assert_slot_specs_consistent() -> None:
    """槽位声明必须与**成果类型注册表 / 任务类型取值域**一致。

    三条，任一条不成立都是配置漂移，而且**不会自然报错**：

    1. `artifact_types` / `task_types` 里的每个取值都在取值范围里 ——
       否则该槽位永远读不到记录，查不到就是"暂无记录"，把**配置错误伪装成正常业务态**；
    2. 同一个 `task_type` / `artifact_type` **只归属一个槽位** ——
       否则同一份记录在两个槽位各显示一次，用户会以为是两份；
    3. `open=False` 的槽位**必须**给 `not_open_reason` —— 没理由时界面只能猜，
       猜出来就是"暂无记录"。`exceptions` 开放后本支线已无未开放槽位，这条是
       **机制自身的守卫**：下次谁再收回某块能力，忘了写理由就会在这里红。

    由测试调用（`tests/test_entrust_workbench.py`），不在导入期执行 ——
    导入期副作用会让"能不能 import"依赖配置正确性，反而更难定位。
    """
    seen_tasks: set[str] = set()
    seen_artifacts: set[str] = set()
    for spec in SLOT_SPECS:
        if not spec.open and not spec.not_open_reason:
            raise WorkbenchConfigError(
                f"槽位 {spec.key} 声明为「本期未开放」却没给理由 —— 界面只能猜，"
                "猜出来的就是「暂无记录」，等于把「能力还没做」说成「这单没有」"
                "（DR-0010 §3.8）"
            )
        for task_type in spec.task_types:
            if task_type not in TASK_TYPES:
                raise WorkbenchConfigError(
                    f"槽位 {spec.key} 声明了未知任务类型 {task_type!r}（取值域 {sorted(TASK_TYPES)}）"
                )
            if task_type in seen_tasks:
                raise WorkbenchConfigError(f"任务类型 {task_type!r} 被多个槽位声明")
            seen_tasks.add(task_type)
        for artifact_type in spec.artifact_types:
            if artifact_type not in ARTIFACT_TYPES:
                raise WorkbenchConfigError(
                    f"槽位 {spec.key} 声明了未知成果类型 {artifact_type!r}"
                    f"（注册表 {sorted(ARTIFACT_TYPES)}）"
                )
            if artifact_type in seen_artifacts:
                raise WorkbenchConfigError(f"成果类型 {artifact_type!r} 被多个槽位声明")
            seen_artifacts.add(artifact_type)


# ── 方言归一（与 tasks / artifacts 的 `_text_ts` 同一口径）──────────────────────


_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(_FMT)


def _text_ts(raw: Any) -> str | None:
    """时间列 → 统一文本。

    SQLite 的 DATETIME 以 TEXT 存放（取出来是 `str`），MySQL 由驱动转成
    `datetime` —— 不归一，同一段代码在开发库通过、在生产库报类型错，
    这类"只在生产暴露"的缺陷必须在本层吸收。

    判据用 `isinstance(raw, datetime)` 而不是 `hasattr(raw, "strftime")`：本模块
    声明的契约就是"时间列只有这两种形状"，鸭子类型会把任何带 `strftime` 的对象
    都放进来并让返回值退化成 `Any`（mypy `no-any-return` 正是在这条上报的），
    而且与 `tasks / artifacts / assignments / sessions` 的既有口径保持一致。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _payload_of(raw: Any) -> dict[str, Any]:
    """版本内容 JSON 文本 → dict；坏数据返回空 dict（缺项会照实进入未决问题）。"""
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ── 取数（只读；不做权限判断 —— 调用方已完成）──────────────────────────────────


def _load_tasks(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托的**全部**任务（不分页）。

    不复用 `tasks.list_tasks`：那是分页列表接口（含全列投影与 JOIN），而工作台要的是
    **全量聚合** —— 用分页接口取全量必须给一个"取多少条才够"的隐含上限，
    一旦真实任务数超过它就静默少算，且没有任何信号。这里的查询走
    `idx_ent_task_assignment`，列也只有本模块用到的 7 列。
    """
    rows = (
        session.execute(
            text(
                f"SELECT {_TASK_COLS} FROM ent_workflow_task "
                "WHERE assignment_id = :aid ORDER BY id ASC"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "task_id": int(r["id"]),
            "task_type": str(r["task_type"]),
            "title": str(r["title"]),
            "status": str(r["status"]),
            "assignee_user_id": (
                int(r["assignee_user_id"]) if r["assignee_user_id"] is not None else None
            ),
            "precondition_task_id": (
                int(r["precondition_task_id"]) if r["precondition_task_id"] is not None else None
            ),
            "updated_at": _text_ts(r["updated_at"]),
        }
        for r in rows
    ]


def _load_artifacts(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托**归属恰好等于本单**的全部成果（含当前生效版本的精确版本号）。

    `assignment_id` 是精确等值条件，不做任何"回退到货主或组织"的放宽：
    同一货主在同一组织下可能有多张委托，放宽一步就是把别的委托的成果
    显示在这张委托的工作台上（DR-0012）。
    """
    rows = (
        session.execute(
            text(
                f"SELECT {_ARTIFACT_COLS} FROM ent_artifact a "
                "LEFT JOIN ent_artifact_revision r ON r.id = a.current_revision_id "
                "WHERE a.assignment_id = :aid ORDER BY a.id ASC"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "artifact_id": int(r["id"]),
            "artifact_type": str(r["artifact_type"]),
            "status": str(r["status"]),
            "revision_no": int(r["revision_no"]) if r["revision_no"] is not None else None,
            "payload": _payload_of(r["payload_json"]),
            "updated_at": _text_ts(r["updated_at"]),
        }
        for r in rows
    ]


def _load_cases(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托的**全部**案件，含已关闭（＝ `_load_tasks` 的同一理由，不是分页接口）。

    已关闭的也要取：`updated_at` 的定义是"**相关业务记录的**最近更新时间"，
    昨天关掉的那张单也是本槽位的一次真实更新，把它排除掉会让"最后更新"停在
    上一张还活着的单上 —— 看起来像没人处理过。

    只看 `assignment_id` **精确等值**：同一货主在同一组织下可能有多张委托，
    放宽一步就是把别的委托的异常显示在这张委托的工作台上（DR-0012 / 验证 11）。
    """
    rows = (
        session.execute(
            text(
                f"SELECT {_CASE_COLS} FROM ent_exception WHERE assignment_id = :aid ORDER BY id ASC"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "case_id": int(r["id"]),
            "assignment_id": int(r["assignment_id"]),
            "kind": str(r["kind"]),
            "title": str(r["title"]),
            "status": str(r["status"]),
            "impact_kind": str(r["impact_kind"]),
            "owner_user_id": (int(r["owner_user_id"]) if r["owner_user_id"] is not None else None),
            "raised_at": _text_ts(r["raised_at"]),
            "updated_at": _text_ts(r["updated_at"]),
        }
        for r in rows
    ]


# ── 四个派生字段（DR-0010 §3.5）──────────────────────────────────────────────


def _customer_settlement_hint(session: Session, *, assignment_id: int) -> dict[str, Any] | None:
    """**对客**的结算版本提示（货主可见）—— 只给 id / 版本号 / 客户决定状态。

    ⛔ **白名单投影**：这一格会出现在**货主**的工作台上，所以绝不能带
    `internal_total`（我们付给供应商的成本）或任何费用行 —— 客户要看明细走
    `GET /settlements/{id}/customer-view`（那条通道才是对客的）。

    为什么需要它（2026-09-18 补）：`customer-view` 与 `customer-confirm` 都按**版本 id** 取，
    而版本清单 `GET /assignments/{id}/settlements` **没有货主面**（版本带内部成本）
    ⇒ 没有这一格，客户就**不知道"我该确认哪一版"**，裁定 Q5 的「客户确认」在界面上
    根本没有可走的路（写 §52 走查章时发现的）。这一格补的就是那个**发现路径**，
    而且只补发现路径 —— 明细与确认仍各自走自己对客端点，不在这里复用内部投影。
    """
    row = settlement_svc.applicable_settlement(session, assignment_id=assignment_id)
    if row is None:
        return None
    return {
        "settlement_id": int(row["settlement_id"]),
        "version_no": int(row["version_no"]),
        "status": str(row["status"]),
        "customer_decision": row["customer_decision"],
        "customer_confirmed_at": row["customer_confirmed_at"],
        # 客户此刻要不要动作：内部已确认、且自己还没表态。
        # 这是**推导**而不是存下来的状态 —— 存一个就会出现"字段说要、链上不要"的分叉。
        "awaiting_customer": bool(
            row["status"] == settlement_svc.STATUS_APPROVED and row["customer_confirmed_at"] is None
        ),
    }


def _overview_text(assignment: dict[str, Any]) -> str:
    """委托概况的「当前成果」= **本体摘要**（这一槽位没有成果记录）。"""
    cargo = assignment.get("cargo_summary")
    quantity = assignment.get("quantity")
    unit = assignment.get("quantity_unit") or ""
    if not cargo and quantity is None:
        return ""
    if not cargo:
        return f"货量 {quantity} {unit}".strip()
    if quantity is None:
        return str(cargo)
    return f"{cargo} · {quantity} {unit}".strip()


def _build_current(
    spec: SlotSpec,
    *,
    assignment: dict[str, Any],
    tasks: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """「当前成果」= 有效业务版本；多个成果时给摘要与数量。"""
    if spec.key == "overview":
        summary = _overview_text(assignment)
        return (
            {"state": CURRENT_PRESENT, "text": summary, "refs": []}
            if summary
            else {
                "state": CURRENT_NO_RECORD,
                "text": "",
                "refs": [],
            }
        )
    if artifacts:
        labels = [ARTIFACT_TYPES[a["artifact_type"]].label for a in artifacts]
        summary = (
            f"「{labels[0]}」" if len(labels) == 1 else f"「{labels[0]}」等 {len(labels)} 项成果"
        )
        refs = [
            {
                "artifact_id": a["artifact_id"],
                "artifact_type": a["artifact_type"],
                "label": ARTIFACT_TYPES[a["artifact_type"]].label,
                # 精确版本：工作台与对话引用**同一对** (artifact_id, revision_no)，见 PRD 第 187 行
                "revision_no": a["revision_no"],
            }
            for a in artifacts
        ]
        return {"state": CURRENT_PRESENT, "text": summary, "refs": refs}
    if tasks:
        done = sum(1 for t in tasks if t["status"] == STATUS_DONE)
        return {
            "state": CURRENT_PRESENT,
            "text": f"任务 {len(tasks)} 项 · 已完成 {done} 项",
            "refs": [],
        }
    return {"state": CURRENT_NO_RECORD, "text": "", "refs": []}


def _build_issues(
    spec: SlotSpec,
    *,
    assignment: dict[str, Any],
    tasks: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """「未决问题」= 缺项、待确认、失效成果、阻断条件、未关闭异常（DR-0010 §3.5）。"""
    issues: list[dict[str, str]] = []

    # ① 「应有但缺失」→ 信息缺失态
    if spec.key == "overview":
        for name, present in (
            ("货物概述", bool(assignment.get("cargo_summary"))),
            ("货量", assignment.get("quantity") is not None),
        ):
            if not present:
                issues.append({"kind": ISSUE_MISSING_FIELD, "text": f"委托未填写{name}"})

    for artifact in artifacts:
        label = ARTIFACT_TYPES[artifact["artifact_type"]].label
        if artifact["status"] != ARTIFACT_STATUS_ACTIVE:
            issues.append({"kind": ISSUE_INACTIVE_ARTIFACT, "text": f"「{label}」已作废"})
            continue
        missing = validate_payload(artifact["artifact_type"], artifact["payload"])
        if missing:
            issues.append(
                {"kind": ISSUE_MISSING_FIELD, "text": f"「{label}」缺 {'、'.join(missing)}"}
            )

    # ② 阻断与等待
    for task in tasks:
        if task["status"] != STATUS_WAITING:
            continue
        if task["precondition_task_id"] is not None:
            issues.append({"kind": ISSUE_BLOCKED, "text": f"「{task['title']}」被前置任务阻断"})
        else:
            issues.append({"kind": ISSUE_WAITING, "text": f"「{task['title']}」等待外部条件"})

    # ③ 未指派 —— 汇总成一条，避免任务多时列表被同一种问题刷满
    unassigned = [
        t for t in tasks if t["status"] in TASK_OPEN_STATUSES and t["assignee_user_id"] is None
    ]
    if unassigned:
        issues.append(
            {
                "kind": ISSUE_UNASSIGNED_TASK,
                "text": f"{len(unassigned)} 项任务尚未指派负责人",
            }
        )

    if any(i["kind"] == ISSUE_MISSING_FIELD for i in issues):
        state = ISSUES_MISSING_INFO
    elif issues:
        state = ISSUES_PRESENT
    else:
        state = ISSUES_NONE
    return {"state": state, "count": len(issues), "items": issues}


def _build_next_owner(open_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """「下一责任方」= 下一项可执行／待响应任务的责任人（DR-0010 §3.5）。

    该槽位**没有待推进任务**时是「不适用」而不是「尚未分配」：前者没有下一步动作，
    后者是"去找个人来负责"。合成一句话，用户就分不清要不要动。
    """
    if not open_tasks:
        return {"state": OWNER_NOT_APPLICABLE, "user_id": None, "text": ""}
    head = min(open_tasks, key=lambda t: (_OWNER_RANK.get(str(t["status"]), 9), int(t["task_id"])))
    assignee = head["assignee_user_id"]
    if assignee is None:
        return {"state": OWNER_UNASSIGNED, "user_id": None, "text": ""}
    # 只给 id：本模块**不查用户表**取姓名（那会把工作台查询扩到 auth 域），
    # 展示口径与既有页面一致（`组织 #N` 同款）。
    return {"state": OWNER_ASSIGNED, "user_id": int(assignee), "text": f"成员 #{assignee}"}


def _latest_updated_at(
    tasks: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    fallback: str | None = None,
) -> str | None:
    """「最后更新」= **相关业务记录的最近更新时间**，不是页面刷新时间。

    该槽位没有任何相关记录时返回 `None`（界面按四态显示），**不拿委托的更新时间顶替** ——
    那会把"这一块完全没动过"说成"刚刚更新过"。`fallback` 只供 `overview` 使用：
    它的"记录"就是委托本体本身。
    """
    stamps = [t["updated_at"] for t in tasks if t["updated_at"]]
    stamps += [a["updated_at"] for a in artifacts if a["updated_at"]]
    if not stamps:
        return fallback
    # 统一格式 `%Y-%m-%d %H:%M:%S`，字典序即时间序（无需再解析一次）
    return max(str(s) for s in stamps)


def _is_blocking_case(case: dict[str, Any]) -> bool:
    """经**服务层唯一判据**判定 —— 本模块不复制那份规则。

    `is_blocking` 对未知取值**抛错**而不是返回 `False`（fail-closed）。这里刻意不
    包一层 `try/except` 兜成"不阻断"：库里出现取值域外的 `impact_kind` 只可能是
    写路径被绕过，属真实缺陷；在读取侧把它压成"看起来正常"，门禁就会静默放行。
    """
    return case_svc.is_blocking(
        kind=str(case["kind"]),
        impact_kind=str(case["impact_kind"]),
        status=str(case["status"]),
    )


def _build_exceptions_slot(spec: SlotSpec, *, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """`exceptions` 槽的真实投影（DR-0010 §3.3 / §3.5，DR-0013 §4.3）。

    数据落点 = "异常案件、阻断影响、变更提案、决策与已应用版本"。四字段落到这里：

    | 字段 | 取值 |
    | --- | --- |
    | 当前成果 | 未关闭案件的摘要与数量（`no_record` 表示本单确实还没登记过案件） |
    | 未决问题 | **阻断中**的案件逐条列出（非阻断案件是记录，不是问题） |
    | 下一责任方 | 最该动的那张未关闭案件的责任人 —— 阻断的优先 |
    | 最后更新 | 本单**全部**案件（含已关闭）的最近更新时间 |

    两处刻意的取舍：

    * **没有「信息缺失」态**。`cause` / `owner_user_id` / `due_at` 都是**可选**字段
      （未知值保持未知是硬要求），把"可选字段为空"报成信息缺失，会变成永不消失的
      骚扰，用户很快学会忽略整个未决问题列表。责任人未定由 `next_owner=unassigned`
      单独表达 —— 它是"去找个人来负责"，不是"数据不全"。
    * **排序不用 `severity`**：先阻断、再按登记顺序。严重度是业务判断，阻断是流程
      约束；用它排序会让人以为它影响流程（C3 要堵的正是这个联想）。

    `current.refs` 现在**有内容了**（DR-0014 §3.3）：未关闭案件各产出一条 `CaseRef`。
    形状是在消费者确定之后才写的 —— 在没人读的时候先定形状等于猜，猜错的代价是
    前端按错的键取值后静默显示成"没有案件"。

    引用顺序沿用本槽位的既定口径（`next_owner` 也用它）：**阻断优先，再按案件号**。
    与 UI-04 清单的 `id DESC` 不同是有意的 —— 槽位要的是"最该动的排前面"，
    清单要的是"最新的排前面"。
    """
    open_cases = [c for c in cases if str(c["status"]) != case_svc.STATUS_CLOSED]
    blocking = [c for c in open_cases if _is_blocking_case(c)]
    changes = [c for c in open_cases if str(c["kind"]) == case_svc.KIND_CHANGE_REQUEST]

    if open_cases:
        parts = [f"未关闭案件 {len(open_cases)} 项"]
        if blocking:
            parts.append(f"阻断 {len(blocking)} 项")
        if changes:
            parts.append(f"变更请求 {len(changes)} 项")
        refs = [
            case_svc.project_case_ref(
                case_id=c["case_id"],
                kind=c["kind"],
                title=c["title"],
                status=c["status"],
                impact_kind=c["impact_kind"],
            )
            for c in sorted(
                open_cases, key=lambda c: (0 if _is_blocking_case(c) else 1, c["case_id"])
            )
        ]
        current: dict[str, Any] = {
            "state": CURRENT_PRESENT,
            "text": " · ".join(parts),
            "refs": refs,
        }
    else:
        current = {"state": CURRENT_NO_RECORD, "text": "", "refs": []}

    items: list[dict[str, str]] = [
        {
            "kind": ISSUE_BLOCKED,
            # 案件号写进描述，是为了让人能凭这句话找到那张单；**不要反过来解析它** ——
            # 结构化引用随 UI-04 的 refs 走，这里的文本是给人读的。
            "text": f"「{c['title']}」阻断执行（案件 #{c['case_id']}）",
        }
        for c in blocking
    ]
    issues: dict[str, Any] = {
        "state": ISSUES_PRESENT if items else ISSUES_NONE,
        "count": len(items),
        "items": items,
    }

    next_owner: dict[str, Any]
    if not open_cases:
        next_owner = {"state": OWNER_NOT_APPLICABLE, "user_id": None, "text": ""}
    else:
        head = min(open_cases, key=lambda c: (0 if _is_blocking_case(c) else 1, c["case_id"]))
        owner = head["owner_user_id"]
        if owner is None:
            next_owner = {"state": OWNER_UNASSIGNED, "user_id": None, "text": ""}
        else:
            # 与 `_build_next_owner` 同一展示口径：只给 id，**不查用户表**取姓名
            next_owner = {
                "state": OWNER_ASSIGNED,
                "user_id": int(owner),
                "text": f"成员 #{owner}",
            }

    stamps = [c["updated_at"] for c in cases if c["updated_at"]]
    return {
        "key": spec.key,
        "title": spec.title,
        "available": True,
        "unavailable_reason": "",
        "current": current,
        "issues": issues,
        "next_owner": next_owner,
        "updated_at": max(str(s) for s in stamps) if stamps else None,
        # 本槽位没有任务与成果：计数键保持既有形状（前端按它拼文案），全为 0。
        "counts": {"artifacts": 0, "tasks": 0, "open_tasks": 0, "unassigned_tasks": 0},
    }


def _build_slot(
    spec: SlotSpec,
    *,
    assignment: dict[str, Any],
    tasks: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    if not spec.open:
        # 「本期未开放」走独立通道：它不是"暂无记录"，也不是"不适用"
        return {
            "key": spec.key,
            "title": spec.title,
            "available": False,
            "unavailable_reason": spec.not_open_reason,
            "current": {"state": CURRENT_NO_RECORD, "text": "", "refs": []},
            "issues": {"state": ISSUES_NONE, "count": 0, "items": []},
            "next_owner": {"state": OWNER_NOT_APPLICABLE, "user_id": None, "text": ""},
            "updated_at": None,
            "counts": {"artifacts": 0, "tasks": 0, "open_tasks": 0, "unassigned_tasks": 0},
        }

    if spec.key == "exceptions":
        return _build_exceptions_slot(spec, cases=cases)

    slot_tasks = (
        list(tasks) if spec.all_tasks else [t for t in tasks if t["task_type"] in spec.task_types]
    )
    slot_artifacts = [a for a in artifacts if a["artifact_type"] in spec.artifact_types]
    active = [a for a in slot_artifacts if a["status"] == ARTIFACT_STATUS_ACTIVE]
    open_tasks = [t for t in slot_tasks if t["status"] in TASK_OPEN_STATUSES]

    return {
        "key": spec.key,
        "title": spec.title,
        "available": True,
        "unavailable_reason": "",
        "current": _build_current(spec, assignment=assignment, tasks=slot_tasks, artifacts=active),
        "issues": _build_issues(
            spec, assignment=assignment, tasks=slot_tasks, artifacts=slot_artifacts
        ),
        "next_owner": _build_next_owner(open_tasks),
        "updated_at": _latest_updated_at(
            slot_tasks,
            slot_artifacts,
            fallback=assignment.get("updated_at") if spec.key == "overview" else None,
        ),
        "counts": {
            "artifacts": len(active),
            "tasks": len(slot_tasks),
            "open_tasks": len(open_tasks),
            "unassigned_tasks": sum(1 for t in open_tasks if t["assignee_user_id"] is None),
        },
    }


def build_workbench(session: Session, *, assignment: dict[str, Any]) -> dict[str, Any]:
    """组装单委托工作台的七槽位摘要（调用方已完成可见性与权限校验）。

    四条只读查询：本单任务、本单归属成果、"同 (货主, 组织) 下归属为空"的存量计数，
    以及**对客结算版本提示**（`customer_settlement`，只给 id / 版本号 / 客户决定 ——
    客户按版本 id 取对客投影，而版本清单没有货主面，见 `_customer_settlement_hint`）；
    `exceptions` 槽开放时再加一条（本单案件）。不留任何写路径 ——
    工作台是**投影**，写动作各自走自己的端点。
    """
    owner_user_id = int(assignment["owner_user_id"])
    org_id = int(assignment["org_id"]) if assignment["org_id"] is not None else None
    assignment_id = int(assignment["assignment_id"])

    tasks = _load_tasks(session, assignment_id=assignment_id)
    artifacts = _load_artifacts(session, assignment_id=assignment_id)
    # 槽位关着就不查案件：不为了"投影已写好"而在每次工作台请求里白跑一条 SQL。
    # 这条判断读的是**声明**而不是常量，因此切片四翻转常量时无需再改这里。
    cases = (
        _load_cases(session, assignment_id=assignment_id)
        if any(s.key == "exceptions" and s.open for s in SLOT_SPECS)
        else []
    )
    slots = [
        _build_slot(spec, assignment=assignment, tasks=tasks, artifacts=artifacts, cases=cases)
        for spec in SLOT_SPECS
    ]
    return {
        "assignment_id": assignment_id,
        "org_id": org_id,
        "status": str(assignment["status"]),
        "slots": slots,
        # 归属机制上线前的历史成果：不在任何槽位里，但**必须如实报数**，
        # 否则界面会让人以为"本单只有这些成果"
        "unassigned_artifact_total": count_unassigned(
            session, owner_user_id=owner_user_id, org_id=org_id
        ),
        # 对客的结算版本提示（货主可见；白名单，见函数文档）
        "customer_settlement": _customer_settlement_hint(session, assignment_id=assignment_id),
    }


__all__ = [
    "CURRENT_NO_RECORD",
    "CURRENT_PRESENT",
    "EXCEPTIONS_SLOT_OPEN",
    "ISSUES_MISSING_INFO",
    "ISSUES_NONE",
    "ISSUES_PRESENT",
    "ISSUE_BLOCKED",
    "ISSUE_INACTIVE_ARTIFACT",
    "ISSUE_MISSING_FIELD",
    "ISSUE_UNASSIGNED_TASK",
    "ISSUE_WAITING",
    "OWNER_ASSIGNED",
    "OWNER_NOT_APPLICABLE",
    "OWNER_UNASSIGNED",
    "SLOT_ORDER",
    "SLOT_SPECS",
    "SLOT_TITLES",
    "SlotSpec",
    "WorkbenchConfigError",
    "assert_slot_specs_consistent",
    "build_workbench",
]
