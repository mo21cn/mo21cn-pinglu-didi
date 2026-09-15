"""变更复核传播 —— A2 五之二：把「哪五类变更影响哪些对象」落成**复核范围**（DR-0016）。

范围与边界（先读这一段，再看代码）
---------------------------------
* 本模块实现的是 PRD 第 285 行：相关事实变化时，把受影响的**当前**成果标
  `needs_revalidation`，并给出**原因与复核任务**；**不改写已接受／已签／已执行历史**。
* DR-0016 冻结的五行映射**照抄在本文件**（`IMPACT_MAP`）。它在本模块里**不是**数据源，
  而是**契约副本** —— 所以 `tests/test_entrust_revalidation.py` 会逐格比对
  「本文件写死的行」与「DR-0016 §3 的表格文字」，两边不一致就红。
  这样改契约必须同时改文档，不会出现"代码改了、决策记录还是旧的"。

两条最容易做错的地方
--------------------
1. **复核范围 ≠ 自动改写清单**（DR-0016 §4）。本模块**不**改任何成果内容、
   不 `UPDATE` 任何 revision、不动 `current_revision_id`；它只**追加**标记与复核任务。
   ⇒「标记为待复核」永远不等于「已经改好了」。
2. **范围不足时交给经理人，不由模型/代码猜**（DR-0016 §4.1）。映射里列出了候选成果
   类型，但案件**只登记了自己声明的受影响项**；若某候选类型在本委托里**确实存在**
   却没被登记，本模块**不自动扩大范围**，而是把它记进 `unconfirmed` 交给经理人确认。

标记是**派生**的，不是第二个真相
------------------------------
「某成果需要复核」⇔ 存在一条 `status='open'` 的 `ent_revalidation` 行指向它。
刻意不在 `ent_artifact` 上加布尔列 —— 那会造出可以与本表不一致的第二份事实。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.modules.entrust.access import utcnow_naive
from app.modules.entrust.tasks import (
    TASK_TYPE_CONTRACT,
    TASK_TYPE_EXECUTION,
    TASK_TYPE_HANDOVER,
    TASK_TYPE_PURCHASE,
    TASK_TYPE_QUOTE,
    TASK_TYPE_SETTLEMENT,
    TASK_TYPES,
)

# ── 变更类别：逐项对应 PRD §6.2 第 289–295 行的五行 ──────────────────────────────

CHANGE_CARGO: Final = "cargo_quantity_category"
CHANGE_ROUTE: Final = "origin_destination_mode"
CHANGE_WINDOW: Final = "loading_delivery_window"
CHANGE_SUPPLIER: Final = "selected_supplier_quote"
CHANGE_CHARGE: Final = "approved_extra_charge"

CHANGE_CATEGORIES: Final = frozenset(
    {CHANGE_CARGO, CHANGE_ROUTE, CHANGE_WINDOW, CHANGE_SUPPLIER, CHANGE_CHARGE}
)


@dataclass(frozen=True)
class ReviewArea:
    """一个复核区域：用**哪个任务类型**、按**什么复核内容**去复查。"""

    task_type: str
    contents: str


@dataclass(frozen=True)
class ChangeImpact:
    """某一类变更的复核范围（DR-0016 §3 的一行）。"""

    artifact_types: tuple[str, ...]
    areas: tuple[ReviewArea, ...]


#: **DR-0016 §3 的五行映射，逐行照抄**。改这张表 = 改决策记录，必须同时改两者。
#: 读法：`artifact_types` 是**候选**（按类型选候选，再按真实关联确定对象，§4.1）；
#: `areas` 是复核落点（每个 task_type 一项，含 DR-0016 的「复核内容」原文要点）。
IMPACT_MAP: Final[dict[str, ChangeImpact]] = {
    CHANGE_CARGO: ChangeImpact(
        artifact_types=(
            "supplier_compare",
            "procurement_confirm",
            "customer_quote",
            "contract_review",
            "quote_parsed",
        ),
        areas=(
            ReviewArea(TASK_TYPE_EXECUTION, "船型、运力及货物适配"),
            ReviewArea(TASK_TYPE_PURCHASE, "采购范围与成本"),
            ReviewArea(TASK_TYPE_HANDOVER, "交接数量、单位及作业安排"),
            ReviewArea(TASK_TYPE_QUOTE, "报价复核"),
            ReviewArea(TASK_TYPE_CONTRACT, "合同复核"),
        ),
    ),
    CHANGE_ROUTE: ChangeImpact(
        artifact_types=(
            "supplier_compare",
            "procurement_confirm",
            "customer_quote",
            "contract_review",
            "quote_parsed",
        ),
        areas=(
            ReviewArea(TASK_TYPE_EXECUTION, "路线、航段、方式及时间"),
            ReviewArea(TASK_TYPE_PURCHASE, "供应商与服务范围"),
            ReviewArea(TASK_TYPE_QUOTE, "报价范围"),
            ReviewArea(TASK_TYPE_CONTRACT, "合同条款"),
        ),
    ),
    CHANGE_WINDOW: ChangeImpact(
        artifact_types=("procurement_confirm", "customer_quote", "contract_review", "quote_parsed"),
        areas=(
            ReviewArea(TASK_TYPE_PURCHASE, "资源可用性"),
            ReviewArea(TASK_TYPE_EXECUTION, "下游计划时间"),
            ReviewArea(TASK_TYPE_HANDOVER, "下游计划时间"),
            ReviewArea(TASK_TYPE_QUOTE, "有效期"),
            ReviewArea(TASK_TYPE_CONTRACT, "合同日期"),
        ),
    ),
    CHANGE_SUPPLIER: ChangeImpact(
        artifact_types=("supplier_compare", "procurement_confirm", "customer_quote"),
        areas=(
            ReviewArea(TASK_TYPE_PURCHASE, "采购及成本"),
            ReviewArea(TASK_TYPE_EXECUTION, "执行资源确认"),
            ReviewArea(TASK_TYPE_QUOTE, "对客方案复核"),
        ),
    ),
    CHANGE_CHARGE: ChangeImpact(
        artifact_types=("settlement_draft", "customer_quote"),
        areas=(
            ReviewArea(TASK_TYPE_SETTLEMENT, "费用依据和结算"),
            ReviewArea(TASK_TYPE_QUOTE, "客户变更确认（必须绑定明确版本）"),
        ),
    ),
}

#: 成果类型 → 它自己的复核任务类型。
#: 来源：DR-0016 §3 各行里点名的对应关系（"报价、合同复核分别落 `quote`、`contract`"）。
#: 与 `registry.py` 的 6 个类型**必须满射** —— 少一个就意味着某类成果永远进不了复核范围，
#: 而那在界面上看不出来（任务照样生成，只是少了那一条）。由下方断言守住。
ARTIFACT_REVIEW_TASK: Final[dict[str, str]] = {
    "quote_parsed": TASK_TYPE_QUOTE,
    "supplier_compare": TASK_TYPE_PURCHASE,
    "customer_quote": TASK_TYPE_QUOTE,
    "contract_review": TASK_TYPE_CONTRACT,
    "procurement_confirm": TASK_TYPE_PURCHASE,
    "settlement_draft": TASK_TYPE_SETTLEMENT,
}


@dataclass(frozen=True)
class ReviewItem:
    """一个待复核项（= 一行 `ent_revalidation` = 一条复核任务）。"""

    review_key: str
    area: str
    task_type: str
    title: str
    target_kind: str | None = None
    target_id: int | None = None
    target_revision_id: int | None = None


@dataclass(frozen=True)
class ScopePlan:
    items: tuple[ReviewItem, ...]
    #: 映射列出、但本委托里**存在却未被登记为受影响项**的成果类型（DR-0016 §4.1）
    unconfirmed: tuple[str, ...]


def _assert_mapping_complete() -> None:
    """注册表的成果类型必须**全部**能落到某个复核任务类型上。

    这是 import 期断言：漏映射时**立刻**报错，而不是等到某类成果静默不进复核范围。
    """
    from app.modules.entrust.registry import list_specs

    known = {str(spec["code"]) for spec in list_specs()}
    missing = sorted(known - set(ARTIFACT_REVIEW_TASK))
    if missing:
        raise RuntimeError(
            f"成果类型 {missing} 没有对应的复核任务类型 —— "
            "意味着该类成果永远不会进入复核范围，且界面上看不出来。"
            "请在本模块 ARTIFACT_REVIEW_TASK 补上，并同步 DR-0016。"
        )
    extra = sorted(set(ARTIFACT_REVIEW_TASK) - known)
    if extra:
        raise RuntimeError(
            f"ARTIFACT_REVIEW_TASK 里的 {extra} 不是注册表中的成果类型（拼写或已移除？）"
        )
    for task_type in sorted(
        set(ARTIFACT_REVIEW_TASK.values())
        | {a.task_type for i in IMPACT_MAP.values() for a in i.areas}
    ):
        if task_type not in TASK_TYPES:
            raise RuntimeError(
                f"复核任务类型 {task_type!r} 不在现有取值域 {sorted(TASK_TYPES)} 内 —— "
                "DR-0016 §6 明确本期不新增任务类型。"
            )


_assert_mapping_complete()


def impact_for(category: str) -> ChangeImpact | None:
    """类别 → 复核范围。**未知类别返回 None**（不猜、不退回默认行）。"""
    return IMPACT_MAP.get(category)


def _artifact_rows(session: Session, assignment_id: int) -> dict[int, dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT id, artifact_type, current_revision_id FROM ent_artifact "
                "WHERE assignment_id = :aid AND status = 'active'"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return {int(r["id"]): dict(r) for r in rows}


def plan_scope(
    session: Session,
    *,
    category: str,
    assignment_id: int,
    artifact_targets: list[int],
) -> ScopePlan:
    """按 DR-0016 定复核范围。

    三个输入都必须是**已存在的事实**：类别来自案件登记，`artifact_targets` 来自案件的
    受影响项（AC-12 的权威来源），委托号来自案件归属。**本函数不写库**。

    Raises:
        KeyError: 类别不在取值域内。调用方应先判 `impact_for()` 是否为 None。
    """
    impact = IMPACT_MAP[category]
    arts = _artifact_rows(session, assignment_id)
    linked = [aid for aid in artifact_targets if aid in arts]
    linked_types = {str(arts[aid]["artifact_type"]) for aid in linked}

    items: list[ReviewItem] = []
    covered: set[str] = set()

    # ① 案件已登记的**成果目标**：各自一条（DR-0016 §4.4 要求"目标对象/目标版本"可追溯，
    #    把多个目标塞进一条任务会把这两个字段变成"多值"，等于没有）。
    for aid in linked:
        row = arts[aid]
        atype = str(row["artifact_type"])
        # **按类型选候选**（DR-0016 §4.1）：只有该类别点名影响的成果类型才进复核范围。
        # 若改成"按任务类型撞名"（例如本类别有 quote 区域、成果又恰好映射到 quote），
        # 会把毫不相干的成果一起标上 —— 那正是 §4.1 禁止的"整单自动改写"。
        if atype not in impact.artifact_types:
            continue
        task_type = ARTIFACT_REVIEW_TASK[atype]
        area = next((a.contents for a in impact.areas if a.task_type == task_type), None)
        if area is None:
            # 类型进了候选、但本类别没有对应的复核区域 ⇒ 无区域可复核，不标记。
            continue
        covered.add(task_type)
        items.append(
            ReviewItem(
                review_key=f"artifact:{aid}",
                area=area,
                task_type=task_type,
                title=f"复核：{area}（变更 #{0}）",
                target_kind="artifact",
                target_id=aid,
                # 目标版本 = 标记时该成果**当前生效**的版本（PRD 第 285 行"当前结果"）。
                # 刻意不用"最新版本"：最新可能是草稿，而复核针对的是对外生效的那一版。
                target_revision_id=(
                    int(row["current_revision_id"])
                    if row["current_revision_id"] is not None
                    else None
                ),
            )
        )

    # ② 映射里列出、但**没有具体对象**的复核区域：仍要生成任务（DR-0016 §4.3
    #    "没有对应成果类型，不代表可以跳过"）—— 只是它的目标对象为空。
    for area_spec in impact.areas:
        if area_spec.task_type in covered:
            continue
        items.append(
            ReviewItem(
                review_key=f"area:{area_spec.task_type}",
                area=area_spec.contents,
                task_type=area_spec.task_type,
                title=f"复核：{area_spec.contents}（变更 #{0}）",
            )
        )

    # ③ 范围不足：候选类型在本委托里**存在**却未被登记 ⇒ 记下来交经理人确认（§4.1）。
    #    只有"确实存在却没登记"才值得确认；委托里根本没有这类成果时没有可确认的对象。
    unconfirmed = tuple(
        sorted(
            ty
            for ty in impact.artifact_types
            if ty not in linked_types and any(str(r["artifact_type"]) == ty for r in arts.values())
        )
    )
    return ScopePlan(items=tuple(items), unconfirmed=unconfirmed)


def apply_revalidation(
    session: Session,
    *,
    exception_id: int,
    assignment_id: int,
    category: str,
    actor_id: int,
    artifact_targets: list[int],
    now: datetime | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """把复核范围落库：**追加**标记与复核任务（验证 19 / AC-12）。

    三条纪律：

    1. **只追加**：不 UPDATE 任何 `ent_artifact_revision`、不动 `current_revision_id`
       ⇒ 「已接受／已签／已执行的历史不被改写」；
    2. **幂等**：`(exception_id, review_key)` 唯一。重放（A7 成功重试不重复副作用）
       时已存在的项**连同它的复核任务**一起跳过，不会多出一批待办；
    3. **默认不提交**（`commit=False`）：调用方是 `apply_case`，它要把"应用变更 +
       写事件 + 传播"放在**一个事务**里（P4-A7）。单独调用时显式传 `commit=True`。

    Returns:
        `{"items": [...], "unconfirmed": [...], "planned": n, "skipped": n}`
    """
    from app.modules.entrust import tasks as tasks_svc

    current = now or utcnow_naive()
    plan = plan_scope(
        session, category=category, assignment_id=assignment_id, artifact_targets=artifact_targets
    )
    existing = {
        str(r["review_key"])
        for r in session.execute(
            text("SELECT review_key FROM ent_revalidation WHERE exception_id = :cid"),
            {"cid": exception_id},
        )
        .mappings()
        .all()
    }

    written: list[dict[str, Any]] = []
    skipped = 0
    for item in plan.items:
        if item.review_key in existing:
            skipped += 1
            continue
        # 标题里的编号是给**人**读的；结构化关联落 ent_revalidation 行（DR-0016 §4.4）。
        # 前缀「复核：」是硬要求 —— 否则这批任务会被当成新增的运输作业去执行。
        title = item.title.replace("#0", f"#{exception_id}")
        task = tasks_svc.create_task(
            session,
            assignment_id=assignment_id,
            actor_id=actor_id,
            task_type=item.task_type,
            title=title,
            now=current,
            commit=False,
        )
        task_id = int(task["task_id"])
        session.execute(
            text(
                "INSERT INTO ent_revalidation "
                "(exception_id, review_key, target_kind, target_id, target_revision_id, "
                " area, task_type, review_task_id, status, note, created_at, resolved_at, "
                " resolved_by) "
                "VALUES (:cid, :key, :tk, :tid, :trid, :area, :ttype, :rtid, 'open', NULL, "
                " :ts, NULL, NULL)"
            ),
            {
                "cid": exception_id,
                "key": item.review_key,
                "tk": item.target_kind,
                "tid": item.target_id,
                "trid": item.target_revision_id,
                "area": item.area,
                "ttype": item.task_type,
                "rtid": task_id,
                "ts": current.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        written.append(
            {
                "review_key": item.review_key,
                "area": item.area,
                "task_type": item.task_type,
                "review_task_id": task_id,
                "target_kind": item.target_kind,
                "target_id": item.target_id,
                "target_revision_id": item.target_revision_id,
            }
        )

    if commit:
        session.commit()
    return {
        "items": written,
        "unconfirmed": list(plan.unconfirmed),
        "planned": len(written),
        "skipped": skipped,
    }


# ── 读路径（投影用）───────────────────────────────────────────────────────────


def list_for_case(session: Session, exception_id: int) -> list[dict[str, Any]]:
    """该案件生成的复核项（含**复核任务的当前状态**）。

    带任务状态与标题，是因为界面上「这批复核做完了没有」必须一眼可见：只给
    `review_task_id`，读的人得再去任务页逐个查；而复核任务**可以**被取消或重开
    （见 `cancel_for_task` / `restore_for_task`），状态是会变的。

    LEFT JOIN 而不是 INNER JOIN：任务行理论上不该缺席（同一事务内创建），但
    缺席时宁可**如实返回空标题**，也不要让这条复核项从列表里凭空消失 ——
    少一条会读成"范围里本来就没这一项"。
    """
    rows = (
        session.execute(
            text(
                "SELECT r.id, r.exception_id, r.review_key, r.target_kind, r.target_id, "
                "r.target_revision_id, r.area, r.task_type, r.review_task_id, r.status, "
                "r.note, r.created_at, r.resolved_at, r.resolved_by, "
                "t.title AS task_title, t.status AS task_status "
                "FROM ent_revalidation r "
                "LEFT JOIN ent_workflow_task t ON t.id = r.review_task_id "
                "WHERE r.exception_id = :cid ORDER BY r.id"
            ),
            {"cid": exception_id},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def scope_hint(
    session: Session, *, category: str, assignment_id: int, artifact_targets: list[int]
) -> list[str]:
    """范围不足的候选成果类型（DR-0016 §4.1：交经理人确认）。

    **复用 `plan_scope` 而不是另写一遍筛选**：`unconfirmed` 的判据（"映射点名了这类成果、
    它在委托里确实存在、却没被登记为受影响项"）与生成复核范围用的是同一段逻辑。
    另写一遍就会出现"列表说没问题、生成时却少一条"这类只在某些数据形态下出现的分叉。

    只取 `unconfirmed`，不落任何库、不生成任务 —— 它是**提问**，不是结论。
    """
    if category not in IMPACT_MAP:
        return []
    return list(
        plan_scope(
            session,
            category=category,
            assignment_id=assignment_id,
            artifact_targets=artifact_targets,
        ).unconfirmed
    )


def open_targets(session: Session, *, artifact_ids: list[int]) -> dict[int, dict[str, Any]]:
    """成果 id → 待复核摘要（**成果侧 `needs_revalidation` 的唯一来源**）。

    一次查完，避免投影层对每个成果各查一次（列表页 N+1）。
    """
    if not artifact_ids:
        return {}
    rows = (
        session.execute(
            text(
                "SELECT target_id, area, task_type, review_task_id, exception_id "
                "FROM ent_revalidation "
                "WHERE status = 'open' AND target_kind = 'artifact' "
                " AND target_id IN :ids "
                "ORDER BY id"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": list(artifact_ids)},
        )
        .mappings()
        .all()
    )
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        tid = int(r["target_id"])
        one = out.setdefault(tid, {"count": 0, "areas": [], "review_task_ids": [], "case_ids": []})
        one["count"] += 1
        if r["area"] not in one["areas"]:
            one["areas"].append(str(r["area"]))
        one["review_task_ids"].append(int(r["review_task_id"]))
        if int(r["exception_id"]) not in one["case_ids"]:
            one["case_ids"].append(int(r["exception_id"]))
    return out


def revalidation_payload(case_id: int, result: dict[str, Any]) -> str:
    """传播结果 → 事件 `payload_json` 文本。"""
    return json.dumps({"case_id": case_id, **result}, ensure_ascii=False)


def open_for_artifact(session: Session, artifact_id: int) -> list[dict[str, Any]]:
    """该成果的**未完成**复核项（AC-12 后半条的判据）。

    返回空列表表示"没有待复核项" ⇒ 可以设为生效版本。
    """
    rows = (
        session.execute(
            text(
                "SELECT id, exception_id, area, task_type, review_task_id "
                "FROM ent_revalidation "
                "WHERE status = 'open' AND target_kind = 'artifact' AND target_id = :aid "
                "ORDER BY id"
            ),
            {"aid": artifact_id},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def resolve_for_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    now: datetime | None = None,
    commit: bool = False,
) -> int:
    """复核任务完成 ⇒ 对应的待复核项转为 `resolved`（返回改了几行）。

    **为什么把解除路径挂在任务完成上**（而不是另开一个"标记已复核"命令）：

    * 复核任务就是那个待办本身 —— 它被完成，意味着"已经按当前版本核对过"；
    * 「事实有来源」：`resolved_by` 记的是**完成该任务的人**，不是另一个可以随手点的按钮；
    * 不新增端点 = 不扩大权限面（谁有权完成该任务，谁就能解除这条标记）。

    ⚠️ **必须幂等**：任务可以被 reopen 再完成，重复调用只应影响仍为 `open` 的行。
    """
    current = now or utcnow_naive()
    result = session.execute(
        text(
            "UPDATE ent_revalidation SET status = 'resolved', resolved_at = :ts, "
            "resolved_by = :actor WHERE review_task_id = :tid AND status = 'open'"
        ),
        {"ts": current.strftime("%Y-%m-%d %H:%M:%S"), "actor": actor_id, "tid": task_id},
    )
    if commit:
        session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def cancel_for_task(
    session: Session,
    *,
    task_id: int,
    actor_id: int,
    now: datetime | None = None,
    commit: bool = False,
) -> int:
    """复核任务被**取消** ⇒ 对应的待复核项转为 `cancelled`（返回改了几行）。

    为什么必须联动（2026-09-15，ENT-041）
    ------------------------------------
    解除路径若只有「完成任务」，那么复核任务一旦被 `cancel`（撤回/作废），标记就
    **永远停在 `open`** ⇒ 那个成果**再也无法被设为生效版本**，而界面上没有任何解释
    —— 收到 409 的人只能看到「有未完成的复核项」，可那个复核任务已经不存在了。
    那不是"保守"，那是一条**看起来像 bug 的业务结论**。

    两条语义上的边界（不能含糊）：

    * `cancelled` **不是** `resolved`。前者说的是"这个复核要求被撤销了"，后者是
      "已经按当前版本核对过"。混成一个值，`resolved_by` 就会同时表示"谁核对过"
      与"谁撤销了要求"，事后无法区分（事实必须有来源，且来源要能分辨动作）。
    * 取消复核任务**是管理动作**（`PERM_TASK_DISPATCH`），有 actor、有任务事件、
      本行也记 `resolved_by`/`resolved_at` ⇒ 它不是"悄悄绕过"，而是留有痕迹的撤销。
      **留意**：AC-12 的阻止意图是"不让人在没核对的情况下把它设为生效"，取消复核
      等于显式声明"不需要核对" —— 这**必须**由有权限的人做出并留名。

    Note:
        与其它解除路径一样**默认不提交**，由调用方决定事务边界。
    """
    current = now or utcnow_naive()
    # ⚠️ 不写成 `note || :why` 的 SQL 拼接：`||` 在 MySQL 里默认是**逻辑或**（不是拼接），
    # 在 SQLite 里才是拼接 —— 同一条语句会在两个方言下拿到完全不同的结果，且 SQLite 上
    # 看起来是对的（本地测试全绿、CI 的 MySQL job 才红）。拼字符串一律在 Python 里做。
    rows = (
        session.execute(
            text(
                "SELECT id, note FROM ent_revalidation "
                "WHERE review_task_id = :tid AND status = 'open' ORDER BY id"
            ),
            {"tid": task_id},
        )
        .mappings()
        .all()
    )
    why = "复核任务被取消 ⇒ 本复核要求随之撤销（不等同于已核对）"
    for row in rows:
        old = str(row["note"] or "").strip()
        session.execute(
            text(
                "UPDATE ent_revalidation SET status = 'cancelled', resolved_at = :ts, "
                "resolved_by = :actor, note = :note WHERE id = :rid"
            ),
            {
                "ts": current.strftime("%Y-%m-%d %H:%M:%S"),
                "actor": actor_id,
                "note": f"{old}；{why}" if old else why,
                "rid": int(row["id"]),
            },
        )
    if commit:
        session.commit()
    return len(rows)


def restore_for_task(
    session: Session,
    *,
    task_id: int,
    now: datetime | None = None,
    commit: bool = False,
) -> int:
    """复核任务被**重开** ⇒ 已解除的待复核项**恢复**为 `open`（返回改了几行）。

    为什么重开必须恢复
    ------------------
    「某成果需要复核」⇔ 存在一条 `status='open'` 的行（本模块的核心不变式）。
    任务完成会把行置 `resolved`；若任务之后被 `reopen`（"那次完成不算数"），
    行却停在 `resolved`，不变式就**静默失效**了 —— 成果看起来已复核完，而实际上
    那次复核已经作废。⇒ 重开必须把它退回 `open`。

    只恢复 `resolved` 的行：`cancelled` 是"复核要求被撤销"（不是"完成被撤销"），
    重开任务并不重新提出这个要求，撤销应当仍然有效。

    `resolved_at` / `resolved_by` 清回 NULL：那两个字段说的是"谁在何时解除了本项"，
    解除已被撤销，留着就是一条**错误的事实**（未知保持未知）。
    """
    _ = now or utcnow_naive()  # 保留参数位：本函数不写时间戳，理由见 docstring
    result = session.execute(
        text(
            "UPDATE ent_revalidation SET status = 'open', resolved_at = NULL, "
            "resolved_by = NULL WHERE review_task_id = :tid AND status = 'resolved'"
        ),
        {"tid": task_id},
    )
    if commit:
        session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
