"""A2 五之二「变更复核传播」—— 验证 19 / AC-12 的服务层用例（ENT-033）。

验证 19 原文（DR-0013 §6.2）：*变更传播 —— 受影响成果标 `needs_revalidation` +
生成复核任务；**已接受/已签/已执行历史不被改写***。

本文件分三组：

1. **映射与决策记录逐格比对**（`test_impact_map_matches_decision_record`）：
   代码里的 `IMPACT_MAP` 是 DR-0016 §3 的**契约副本**。两边不一致就红 ——
   防止"代码改了、决策记录还是旧的"（DR-0016 §5 明确实现必须照抄，不得现推）。
2. **验证 19 本体**：标记、复核任务、结构化关联、历史不被改写。
3. **四条容易做反的边界**（DR-0016 §4）：
   * 无对象也要有区域任务（§4.3），否则"没有对应成果类型"会被读成"可以跳过"；
   * 范围不足只登记、**不自动扩大**（§4.1），否则等于整单自动改写；
   * **不改写历史**（不 UPDATE revision、不动 current_revision_id）；
   * 幂等：重放不产生第二批复核任务（P4-A7）。
"""

from __future__ import annotations

import os
import re

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import artifacts as art_svc  # noqa: E402
from app.modules.entrust import assignments as assign_svc  # noqa: E402
from app.modules.entrust import exceptions as exc  # noqa: E402
from app.modules.entrust import revalidation as rv  # noqa: E402
from app.modules.entrust import tasks as task_svc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"


def _repo_root() -> str:
    """从本文件向上找到含 `docs/entrust/decisions` 的目录（不靠数层数）。

    数层数在测试里很容易写成"少一层"，而那样的失败信息是 `FileNotFoundError`，
    看起来像"文档不见了"，实际是自己算错了路径。向上找**锚点目录**更稳。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, "docs", "entrust", "decisions")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            raise RuntimeError("向上找不到含 docs/entrust/decisions 的仓库根")
        here = parent


_DECISION_DOC = os.path.join(_repo_root(), "docs", "entrust", "decisions", "0016-变更影响映射.md")

OWNER = 1
MANAGER = 900
ALL_PERMS = (
    '["entrust:view","entrust:assignment:claim","entrust:quote:create","entrust:task:dispatch"]'
)


@pytest.fixture()
def db():
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ── 播种助手（与 test_entrust_exceptions_apply.py 同款，独立内存库）───────────


def _org(session, name: str = "测试组织") -> int:
    result = session.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": name, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()
    return int(result.lastrowid or 0)


def _member(session, org_id: int, user_id: int, role: str = "manager") -> None:
    session.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()


def _env(session) -> dict:
    org = _org(session)
    _member(session, org, MANAGER, role="manager")
    result = session.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": OWNER, "p": ALL_PERMS, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()
    entrustment_id = int(result.lastrowid or 0)
    draft = assign_svc.create_assignment(session, owner_user_id=OWNER, title="钢材运输")
    submitted = assign_svc.submit_assignment(
        session,
        assignment_id=draft["assignment_id"],
        actor_id=OWNER,
        org_id=org,
        expected_revision=draft["revision"],
    )
    assignment = assign_svc.claim_assignment(
        session, assignment_id=submitted["assignment_id"], actor_id=MANAGER
    )
    return {
        "org_id": org,
        "entrustment_id": entrustment_id,
        "assignment_id": int(assignment["assignment_id"]),
    }


def _artifact(session, env: dict, *, atype: str = "quote_parsed", freight: int = 12000) -> int:
    return int(
        art_svc.create_artifact(
            session,
            entrustment_id=env["entrustment_id"],
            artifact_type=atype,
            payload={"freight": freight, "currency": "CNY"},
            created_by=MANAGER,
            assignment_id=env["assignment_id"],
        )["artifact_id"]
    )


def _current_revision(session, artifact_id: int) -> int | None:
    row = session.execute(
        text("SELECT current_revision_id FROM ent_artifact WHERE id = :aid"),
        {"aid": artifact_id},
    ).first()
    return None if row is None or row[0] is None else int(row[0])


def _revisions(session, artifact_id: int) -> list[tuple[int, str]]:
    rows = session.execute(
        text(
            "SELECT revision_no, payload_json FROM ent_artifact_revision "
            "WHERE artifact_id = :aid ORDER BY revision_no"
        ),
        {"aid": artifact_id},
    ).all()
    return [(int(r[0]), str(r[1])) for r in rows]


def _tasks_for(session, assignment_id: int) -> list[dict]:
    _total, items = task_svc.list_tasks(session, assignment_id=assignment_id, size=100)
    return items


def _apply_change(session, env: dict, *, category: str, targets: list[dict], changes: dict) -> dict:
    """登记变更请求 → 批准 → 应用（一条龙，供各用例复用）。"""
    case = exc.raise_case(
        session,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        kind=exc.KIND_CHANGE_REQUEST,
        title="运价调整",
        severity="medium",
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        links=targets,
        change_category=category,
    )
    case_id = int(case["id"])
    basis = None
    if changes:
        first = next(iter(changes))
        basis = _current_revision(session, int(first.split("#")[1]))
    exc.decide(
        session,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_IN_REVIEW,
        expected_revision=1,
    )
    approved = exc.decide(
        session,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_APPROVED,
        expected_revision=2,
        basis_revision_id=basis,
        approved_changes=changes or None,
    )
    applied = exc.apply_case(
        session,
        exception_id=case_id,
        actor_id=MANAGER,
        expected_revision=int(approved["revision_no"]),
    )
    return {"case_id": case_id, "applied": applied}


# ── 第 1 组：映射与决策记录逐格比对（DR-0016 §5.1「不得现推」）─────────────


def _doc_rows() -> list[tuple[str, str, str]]:
    """解析 DR-0016 §3 的表格，返回 [(类别, Artifact 落点, Task 落点), ...]。"""
    with open(_DECISION_DOC, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 3."))
    rows: list[tuple[str, str, str]] = []
    for line in lines[start:]:
        if line.startswith("## 4."):
            break
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("变更类别", "---") or set(cells[0]) <= set("- "):
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


#: DR-0016 §3 的表行**顺序**（逐行对应；表本身按 PRD §6.2 的五类排列）
_DOC_CATEGORY_ORDER = (
    rv.CHANGE_CARGO,
    rv.CHANGE_ROUTE,
    rv.CHANGE_WINDOW,
    rv.CHANGE_SUPPLIER,
    rv.CHANGE_CHARGE,
)


def _doc_artifact_types(rows: list[tuple[str, str, str]]) -> list[set[str]]:
    """逐行取 DR-0016 §3 的 Artifact 落点类型集合。

    ⚠️ DR-0016 第二行写的是「**同上**（`quote_parsed` 为相关适用性）」—— 用回指而不是
    重抄一遍。解析必须**沿用它**，否则会得出"文档只列了 1 个类型"的假结论，
    进而把一条完全正确的实现判成不一致（反向也一样：真不一致时会被"同上"掩盖）。
    """
    out: list[set[str]] = []
    for _label, cell, _tasks in rows:
        codes = set(re.findall(r"`([a-z_]+)`", cell))
        if cell.startswith("同上"):
            assert out, "第一行不能用「同上」回指"
            codes |= out[-1]
        out.append(codes)
    return out


def test_impact_map_matches_decision_record():
    """代码的 `IMPACT_MAP` 必须与 DR-0016 §3 的表格**逐行**一致。

    只比对**两列的类型集合**，不比对中文措辞：措辞可以在文档里润色，
    但"哪类成果/哪个任务类型进入复核范围"是实施契约，改一处就必须改另一处。
    """
    rows = _doc_rows()
    assert len(rows) == 5, f"DR-0016 §3 应有 5 行，解析到 {len(rows)} 行：{rows}"
    assert len(rows) == len(_DOC_CATEGORY_ORDER)
    doc_arts = _doc_artifact_types(rows)

    for idx, (category, row) in enumerate(zip(_DOC_CATEGORY_ORDER, rows, strict=True)):
        _doc_label, _cell, doc_tasks = row
        impact = rv.IMPACT_MAP[category]
        want_arts = doc_arts[idx]
        want_tasks = set(re.findall(r"`([a-z_]+)`", doc_tasks))
        assert set(impact.artifact_types) == want_arts, (
            f"{category} 的 Artifact 落点与 DR-0016 §3 不一致："
            f"代码 {sorted(set(impact.artifact_types))} vs 文档 {sorted(want_arts)}"
        )
        assert {a.task_type for a in impact.areas} == want_tasks, (
            f"{category} 的 Task 落点与 DR-0016 §3 不一致："
            f"代码 {sorted(a.task_type for a in impact.areas)} vs 文档 {sorted(want_tasks)}"
        )


def test_artifact_review_task_covers_every_registry_type():
    """成果类型 → 复核任务类型必须**满射**：漏一类，那类成果就永远不会进复核范围。"""
    from app.modules.entrust.registry import list_specs

    known = {str(spec["code"]) for spec in list_specs()}
    assert set(rv.ARTIFACT_REVIEW_TASK) == known
    assert set(rv.ARTIFACT_REVIEW_TASK.values()) <= task_svc.TASK_TYPES


# ── 第 2 组：验证 19 本体 ───────────────────────────────────────────────────


def test_apply_marks_affected_artifact_and_creates_review_tasks(db):
    """变更应用后：受影响成果标 `needs_revalidation`，并按映射生成复核任务。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    before_tasks = len(_tasks_for(db, env["assignment_id"]))

    result = _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    case_id = result["case_id"]

    # ① 成果侧标记（派生自 ent_revalidation 的 open 行）
    marks = rv.open_targets(db, artifact_ids=[art_id])
    assert art_id in marks, "受影响成果必须被标为待复核"
    assert marks[art_id]["case_ids"] == [case_id]
    assert marks[art_id]["count"] >= 1

    # ② 任务侧：映射列出的每个复核区域都有一条任务，标题明确用途为**复核**
    rows = rv.list_for_case(db, case_id)
    assert rows, "必须生成复核项"
    areas = {str(r["task_type"]) for r in rows}
    assert areas == {a.task_type for a in rv.IMPACT_MAP[rv.CHANGE_CARGO].areas}
    tasks = _tasks_for(db, env["assignment_id"])
    assert len(tasks) == before_tasks + len(rows)
    new_ids = {int(r["review_task_id"]) for r in rows}
    by_id = {int(t["task_id"]): t for t in tasks}
    for task_id in new_ids:
        assert str(by_id[task_id]["title"]).startswith("复核："), (
            "复核任务标题必须显式说明用途，否则会被当成新增运输作业去执行（DR-0016 §4.4）"
        )
        assert str(by_id[task_id]["task_type"]) in task_svc.TASK_TYPES

    # ③ 结构化关联（DR-0016 §4.4）：来源变更 / 目标对象 / 目标版本 / 复核区域，四组齐备
    artifact_row = next(r for r in rows if r["target_kind"] == "artifact")
    assert int(artifact_row["exception_id"]) == case_id
    assert int(artifact_row["target_id"]) == art_id
    assert int(artifact_row["target_revision_id"]) is not None, "目标版本不得为空"
    assert str(artifact_row["area"]).strip(), "复核区域不得为空"
    # 无具体对象的区域项：三者为空，但区域与任务类型仍必须有
    area_row = next(r for r in rows if r["target_kind"] is None)
    assert area_row["target_id"] is None and area_row["target_revision_id"] is None
    assert str(area_row["area"]).strip() and str(area_row["task_type"]).strip()

    # ④ 传播计划落事件（审计：下游收到了什么）
    kinds = [str(e["event_kind"]) for e in exc.list_events(db, case_id)]
    assert exc.EVENT_REVALIDATION_PLANNED in kinds


def test_no_object_area_still_gets_a_review_task(db):
    """§4.3：没有对应对象**不代表可以跳过** —— 区域任务照样生成（目标为空）。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    # 只登记 settlement_draft 之外的一个目标，映射里其余区域都没有对象
    result = _apply_change(
        db,
        env,
        category=rv.CHANGE_CHARGE,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    rows = rv.list_for_case(db, result["case_id"])
    # customer_quote 在「已批准附加费」里对应 quote；settlement 区域无对象 ⇒ 仍须有任务
    settlement = [r for r in rows if str(r["task_type"]) == task_svc.TASK_TYPE_SETTLEMENT]
    assert len(settlement) == 1, "无具体对象的复核区域也必须生成任务"
    assert settlement[0]["target_id"] is None


def test_history_is_not_rewritten(db):
    """**已接受/已签/已执行的历史不被改写**：传播只追加，不改 revision 与生效指针。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    before = _revisions(db, art_id)
    before_current = _current_revision(db, art_id)

    _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )

    after = _revisions(db, art_id)
    # 应用本身会**追加**一个版本（那是五之一的应用动作），但历史行必须逐字未变
    assert after[: len(before)] == before, "既有 revision 行不得被改写"
    assert len(after) == len(before) + 1, "传播本身不产生版本；这一条来自应用动作"
    current_after = _current_revision(db, art_id)
    assert current_after is not None and current_after != before_current, (
        "应用会推进生效版本；若相等说明应用没生效（另一件事失败了）"
    )

    # 传播**不写任何 revision**：把传播单独跑一次，版本数不变
    n = len(_revisions(db, art_id))
    rv.apply_revalidation(
        db,
        exception_id=1,
        assignment_id=env["assignment_id"],
        category=rv.CHANGE_CARGO,
        actor_id=MANAGER,
        artifact_targets=[art_id],
        commit=True,
    )
    assert len(_revisions(db, art_id)) == n, "传播本身不得产生/改写任何成果版本"


def test_replay_is_idempotent(db):
    """重放传播：不新增复核项、不新增任务（P4-A7 成功重试不重复副作用）。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    result = _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    case_id = result["case_id"]
    rows_before = rv.list_for_case(db, case_id)
    tasks_before = len(_tasks_for(db, env["assignment_id"]))

    again = rv.apply_revalidation(
        db,
        exception_id=case_id,
        assignment_id=env["assignment_id"],
        category=rv.CHANGE_CARGO,
        actor_id=MANAGER,
        artifact_targets=[art_id],
        commit=True,
    )

    assert again["planned"] == 0
    assert again["skipped"] == len(rows_before)
    assert len(rv.list_for_case(db, case_id)) == len(rows_before)
    assert len(_tasks_for(db, env["assignment_id"])) == tasks_before


# ── 第 3 组：四条边界（做反了会静默产生错误范围）───────────────────────────


def test_scope_is_not_widened_when_linked_set_is_short(db):
    """§4.1：关联不足 ⇒ 只**登记**待确认，**不自动扩大范围**。"""
    env = _env(db)
    linked = _artifact(db, env, atype="customer_quote")
    # 同委托里**存在**但未登记为受影响项的候选类型（pc 是「货物数量」的候选之一）
    unlinked = _artifact(db, env, atype="procurement_confirm")

    result = _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": linked}],
        changes={f"artifact#{linked}": {"freight": 13000, "currency": "CNY"}},
    )

    marks = rv.open_targets(db, artifact_ids=[linked, unlinked])
    assert linked in marks
    assert unlinked not in marks, "未登记为受影响项的成果**不得**被自动标记（那是整单自动改写）"

    planned = [
        e
        for e in exc.list_events(db, result["case_id"])
        if str(e["event_kind"]) == exc.EVENT_REVALIDATION_PLANNED
    ]
    assert planned, "必须有传播计划事件"
    unconfirmed = (planned[0]["payload"] or {}).get("unconfirmed") or []
    assert "procurement_confirm" in unconfirmed, (
        "候选类型确实存在却没登记 ⇒ 必须记进 unconfirmed 交经理人确认，而不是静默跳过"
    )


def test_artifact_type_outside_the_mapping_is_not_marked(db):
    """类型不在该类别的复核范围里 ⇒ 不标记（范围由映射决定，不由「挂了 link」决定）。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="quote_parsed")  # 「已批准附加费」的候选里没有它
    result = _apply_change(
        db,
        env,
        category=rv.CHANGE_CHARGE,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    assert art_id not in rv.open_targets(db, artifact_ids=[art_id])
    rows = rv.list_for_case(db, result["case_id"])
    assert all(str(r["target_kind"]) != "artifact" for r in rows), (
        "不该被标记的成果不得出现在复核项里"
    )


def test_change_request_without_category_cannot_be_applied(db):
    """没有变更类别 ⇒ **拒绝应用**，且不留下任何写入。

    为什么不在应用时"默认一类"：类别决定复核范围，猜错的类别会让下游
    复查不相干的对象，而任务照样生成、看起来完全正常。
    """
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    case = exc.raise_case(
        db,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        kind=exc.KIND_CHANGE_REQUEST,
        title="未登记类别",
        severity="medium",
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        links=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
    )
    case_id = int(case["id"])
    exc.decide(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_IN_REVIEW,
        expected_revision=1,
    )
    approved = exc.decide(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_APPROVED,
        expected_revision=2,
        basis_revision_id=_current_revision(db, art_id),
        approved_changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    revs_before = _revisions(db, art_id)
    tasks_before = len(_tasks_for(db, env["assignment_id"]))

    with pytest.raises(exc.ExceptionCaseError) as info:
        exc.apply_case(
            db,
            exception_id=case_id,
            actor_id=MANAGER,
            expected_revision=int(approved["revision_no"]),
        )
    assert "变更类别" in str(info.value)

    assert _revisions(db, art_id) == revs_before, "拒绝必须在任何写入之前发生"
    assert len(_tasks_for(db, env["assignment_id"])) == tasks_before
    assert exc.get_case(db, case_id)["status"] == exc.STATUS_APPROVED


def test_category_change_after_approval_is_rejected(db):
    """批准后改类别 ⇒ 409 + `applied_rejected`：否则会生成一份没人批准过的复核清单。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    case = exc.raise_case(
        db,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        kind=exc.KIND_CHANGE_REQUEST,
        title="类别会被改",
        severity="medium",
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        links=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        change_category=rv.CHANGE_CARGO,
    )
    case_id = int(case["id"])
    exc.decide(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_IN_REVIEW,
        expected_revision=1,
    )
    approved = exc.decide(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_APPROVED,
        expected_revision=2,
        basis_revision_id=_current_revision(db, art_id),
        approved_changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    # 批准之后有人改了类别（直接改库，模拟"另一条路径改了它"）
    db.execute(
        text("UPDATE ent_exception SET change_category = :c WHERE id = :cid"),
        {"c": rv.CHANGE_CHARGE, "cid": case_id},
    )
    db.commit()

    with pytest.raises(exc.ExceptionCaseConflictError) as info:
        exc.apply_case(
            db,
            exception_id=case_id,
            actor_id=MANAGER,
            expected_revision=int(approved["revision_no"]),
        )
    assert "变更类别" in str(info.value)
    kinds = [str(e["event_kind"]) for e in exc.list_events(db, case_id)]
    assert exc.EVENT_APPLIED_REJECTED in kinds


def test_exception_kind_may_not_carry_a_category(db):
    """真实异常没有"变更类别"：给它套一个会让复核范围凭空出现一批无依据任务。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    with pytest.raises(exc.ExceptionCaseError) as info:
        exc.raise_case(
            db,
            assignment_id=env["assignment_id"],
            actor_id=MANAGER,
            kind=exc.KIND_EXCEPTION,
            title="缺一份提单",
            severity="medium",
            impact_kind=exc.IMPACT_REVIEW_REQUIRED,
            change_category=rv.CHANGE_CARGO,
        )
    assert "change_category" in str(info.value)
    assert art_id  # 仅为保持播种路径一致
