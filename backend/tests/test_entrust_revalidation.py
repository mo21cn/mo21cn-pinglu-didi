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
from app.modules.entrust import schemas as sch  # noqa: E402
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


def _current_revision_no(session, artifact_id: int) -> int | None:
    """**生效版本的版本号**。

    与 `_current_revision`（那返回的是 `current_revision_id`，即**行 id**）不是同一个数 ——
    项目铁律「`id` 与编号是两个不同的数」在这里同样成立，混用会让"版本是否推进"的断言失真。
    """
    art = art_svc.get_artifact(session, artifact_id)
    cur = art.get("current_revision") or {}
    return None if cur.get("revision_no") is None else int(cur["revision_no"])


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


# ── 第 4 组：AC-12 后半条「阻止失效成果被确认或执行」─────────────────────────


def test_confirm_is_blocked_while_revalidation_is_open(db):
    """有待复核项时**不得静默成为生效版本**（PRD 第 297 行）。

    这是 AC-12 里另一半要求：验证 19 只到"标出来 + 生成任务"，
    而"标了却照旧能被设为生效版本"等于没有约束。
    """
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    assert rv.open_for_artifact(db, art_id), "前置：应用后应留下待复核项"
    current_before = _current_revision(db, art_id)

    # 再编辑一版（这是合法动作：草稿可以有），但**设为生效版本**必须被拒
    draft = art_svc.append_revision(
        db,
        artifact_id=art_id,
        payload={"freight": 14000, "currency": "CNY"},
        actor_id=MANAGER,
        source="manual",
    )
    with pytest.raises(art_svc.ArtifactRevalidationError) as info:
        art_svc.confirm_revision(
            db,
            artifact_id=art_id,
            revision_no=int(draft["revision_no"]),
            actor_id=MANAGER,
        )
    assert "复核" in str(info.value)
    assert _current_revision(db, art_id) == current_before, "被拒时生效版本**不得**被改动"


def test_completing_review_task_unblocks_confirm(db):
    """完成复核任务 ⇒ 标记转 resolved ⇒ 确认恢复可用（解除路径只有这一条）。"""
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    rows = rv.open_for_artifact(db, art_id)
    assert rows, "前置：应有待复核项"
    review_task_id = int(rows[0]["review_task_id"])

    # 未完成复核前：确认被拒
    draft = art_svc.append_revision(
        db,
        artifact_id=art_id,
        payload={"freight": 15000, "currency": "CNY"},
        actor_id=MANAGER,
        source="manual",
    )
    with pytest.raises(art_svc.ArtifactRevalidationError):
        art_svc.confirm_revision(
            db, artifact_id=art_id, revision_no=int(draft["revision_no"]), actor_id=MANAGER
        )

    # 完成复核任务
    task_svc.start_task(db, task_id=review_task_id, actor_id=MANAGER)
    task_svc.complete_task(db, task_id=review_task_id, actor_id=MANAGER)

    resolved = rv.list_for_case(db, int(rows[0]["exception_id"]))
    done = [r for r in resolved if int(r["review_task_id"]) == review_task_id]
    assert done and all(str(r["status"]) == "resolved" for r in done), (
        "完成复核任务必须把对应标记转为 resolved（否则成果被永久锁死）"
    )
    assert done[0]["resolved_by"] == MANAGER, "解除者必须是**完成复核任务的人**（事实有来源）"

    # 解除后可确认
    art_svc.confirm_revision(
        db, artifact_id=art_id, revision_no=int(draft["revision_no"]), actor_id=MANAGER
    )
    assert _current_revision_no(db, art_id) == int(draft["revision_no"])


def test_apply_is_not_blocked_by_its_own_marker(db):
    """**顺序保证**：变更应用先 confirm、后写传播标记，所以不会被自己刚生成的标记挡住。

    这一条是"阻止确认"能安全上线的**前提** —— 若顺序反了，五之二的传播会
    把五之一的应用直接打死，而且失败点看起来像"应用坏了"。
    """
    env = _env(db)
    art_id = _artifact(db, env, atype="customer_quote")
    result = _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 16000, "currency": "CNY"}},
    )
    assert result["applied"]["status"] == exc.STATUS_APPLIED
    assert rv.open_for_artifact(db, art_id), "应用本身必须成功，且同时留下待复核项"
    # 生效版本是**应用写入的那一版**（不是被复核挡住的原版本）
    revs = _revisions(db, art_id)
    assert _current_revision_no(db, art_id) == max(r[0] for r in revs)


# ── 第 5 组：五之四（界面输入）与「标记三态一致」（ENT-041）───────────────────


def _open_mark(db, env: dict, *, atype: str = "customer_quote") -> tuple[int, int, int]:
    """应用一次变更，返回 (成果 id, 案件 id, 复核任务 id)。"""
    art_id = _artifact(db, env, atype=atype)
    result = _apply_change(
        db,
        env,
        category=rv.CHANGE_CARGO,
        targets=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    rows = rv.open_for_artifact(db, art_id)
    assert rows, "前置：应用后应留下待复核项"
    return art_id, int(result["case_id"]), int(rows[0]["review_task_id"])


def test_artifact_projection_exposes_needs_revalidation(db):
    """成果**详情**与**清单**都要带 `needs_revalidation`（徽标的数据来源）。

    两处都测，是因为它们走的是两个不同的取数函数（`get_artifact` /
    `list_by_assignment`）；只测一处的话，另一处漏加字段会**静默**表现为
    "列表页没有徽标、详情页有"。
    """
    env = _env(db)
    art_id, case_id, task_id = _open_mark(db, env)
    untouched = _artifact(db, env, atype="contract_review")

    detail = art_svc.get_artifact(db, art_id)
    mark = detail["needs_revalidation"]
    assert mark is not None, "有待复核项时详情必须带标记"
    assert mark["count"] >= 1
    assert mark["case_ids"] == [case_id], "标记要能指回来源变更（否则无法回答「为什么」）"
    assert task_id in mark["review_task_ids"], "标记要能指到复核任务（否则界面点不进去）"
    assert mark["areas"], "标记要带复核区域（AC-12 要求显示原因）"

    clean = art_svc.get_artifact(db, untouched)
    assert clean["needs_revalidation"] is None, (
        "没有待复核项时必须是 None（不是 {}）—— 「没有标记」与「有个空标记」是两件事"
    )

    _total, items = art_svc.list_by_assignment(db, assignment_id=env["assignment_id"], size=50)
    by_id = {int(x["artifact_id"]): x for x in items}
    assert by_id[art_id]["needs_revalidation"] is not None
    assert by_id[untouched]["needs_revalidation"] is None
    assert by_id[art_id].get("current_revision_no") is not None, "原有字段不得被这次改动挤掉"


def test_case_surface_fields_survive_the_response_model(db):
    """五之四新增的三个字段必须在**响应模型**里显式声明。

    ⚠️ 这条用例防的是一类静默缺陷：pydantic 默认**丢弃未声明字段**。漏声明时
    接口照样 200、也不报错，只是界面永远拿不到 —— 表现成"徽标/复核清单/批准摘要
    都没做"，而代码里明明全都在。⇒ 服务层返回字典对不对**不足以**证明接口给得出来。
    """
    assert "needs_revalidation" in sch.AssignmentArtifactItem.model_fields
    for field in ("revalidation", "unconfirmed_types", "approval"):
        assert field in sch.ExceptionCaseDetailOut.model_fields, field
    assert "targets" in sch.ExceptionCaseApprovalOut.model_fields
    assert "task_status" in sch.ExceptionCaseRevalidationOut.model_fields


def test_approval_summary_lists_targets_and_changed_fields(db):
    """批准快照摘要：说清"将动哪些目标、各自改哪些字段"。

    apply **不接受**"改成什么"，所以界面只能在点之前把将发生的事说清楚；
    摘要给错（例如把 `changes[key]` 当成 `{"payload": {...}}` 的包裹去读）
    会让界面显示"这次应用什么都不改"，而实际上会改 —— 一次盲操作的点击。
    """
    env = _env(db)
    art_id, case_id, _task_id = _open_mark(db, env)

    summary = exc.approval_summary(db, case_id)
    assert summary is not None, "批准过就必须有摘要（否则界面无法提示将发生什么）"
    assert summary["snapshot_version"] == exc.APPROVAL_SNAPSHOT_VERSION
    assert summary["change_category"] == rv.CHANGE_CARGO
    targets = {f"{t['target_kind']}#{t['target_id']}": t for t in summary["targets"]}
    assert f"artifact#{art_id}" in targets
    fields = targets[f"artifact#{art_id}"]["change_fields"]
    assert fields == ["currency", "freight"], f"要列出将改的字段，实际 {fields}"
    assert targets[f"artifact#{art_id}"]["basis_revision_id"] is not None

    # 没有批准过的案件：`None`，不是空摘要（两者在界面上要说不同的话）
    fresh = exc.raise_case(
        db,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        kind=exc.KIND_EXCEPTION,
        title="尚未批准",
        severity="medium",
        impact_kind=exc.IMPACT_INFORMATIONAL,
    )
    assert exc.approval_summary(db, int(fresh["id"])) is None


def test_scope_hint_reports_the_same_unconfirmed_as_the_plan(db):
    """`scope_hint` 与真实计划必须给出**同一份** unconfirmed（范围逻辑只有一份实现）。

    若各写一遍筛选，就会出现"清单说没问题、生成时却少一条"这类只在特定数据形态下
    出现的分叉 —— 而那种分叉在界面上看起来完全正常。
    """
    env = _env(db)
    linked = _artifact(db, env, atype="customer_quote")
    unlinked = _artifact(db, env, atype="procurement_confirm")  # 候选之一，未登记

    hint = rv.scope_hint(
        db,
        category=rv.CHANGE_CARGO,
        assignment_id=env["assignment_id"],
        artifact_targets=[linked],
    )
    plan = rv.plan_scope(
        db,
        category=rv.CHANGE_CARGO,
        assignment_id=env["assignment_id"],
        artifact_targets=[linked],
    )
    assert hint == list(plan.unconfirmed)
    assert "procurement_confirm" in hint, "确实存在却没登记的候选类型要提示确认"
    assert unlinked  # 播种路径一致

    # 类别未知（不该发生的输入）⇒ 空列表，**不猜一个默认行**
    assert (
        rv.scope_hint(db, category="nope", assignment_id=env["assignment_id"], artifact_targets=[])
        == []
    )


def test_cancelling_review_task_releases_the_marker(db):
    """取消**复核任务** ⇒ 标记转 `cancelled` ⇒ 成果恢复可设生效版本。

    为什么必须这样：若标记永远停在 `open`，而那个复核任务已经不存在了，成果会被
    **永久**锁死，且界面上无法解释（409 说"有未完成的复核项"，可它已经被取消）。
    `cancelled` 与 `resolved` 分开，是为了让"谁核对过"与"谁撤销了要求"事后能分辨。
    """
    env = _env(db)
    art_id, case_id, review_task_id = _open_mark(db, env)
    current_before = _current_revision(db, art_id)

    draft = art_svc.append_revision(
        db,
        artifact_id=art_id,
        payload={"freight": 21000, "currency": "CNY"},
        actor_id=MANAGER,
        source="manual",
    )
    with pytest.raises(art_svc.ArtifactRevalidationError):
        art_svc.confirm_revision(
            db, artifact_id=art_id, revision_no=int(draft["revision_no"]), actor_id=MANAGER
        )

    task_svc.cancel_task(db, task_id=review_task_id, actor_id=MANAGER)

    rows = [r for r in rv.list_for_case(db, case_id) if int(r["review_task_id"]) == review_task_id]
    assert rows and all(str(r["status"]) == "cancelled" for r in rows), (
        "取消复核任务必须把标记转 cancelled（否则成果被永久锁死且无法解释）"
    )
    assert rows[0]["resolved_by"] == MANAGER, "撤销者必须留名（管理动作要可追溯）"
    assert "取消" in str(rows[0]["note"] or ""), "要写清为什么解除"
    assert not rv.open_for_artifact(db, art_id)

    # 解除后可确认；**取消不是"已核对"** —— 它只是撤销了这个复核要求
    art_svc.confirm_revision(
        db, artifact_id=art_id, revision_no=int(draft["revision_no"]), actor_id=MANAGER
    )
    assert _current_revision(db, art_id) != current_before

    # 幂等：再取消一次不改变已解除的行（也不报错）
    assert rv.cancel_for_task(db, task_id=review_task_id, actor_id=MANAGER) == 0


def test_reopening_review_task_restores_the_marker(db):
    """重开复核任务 ⇒ 标记**退回 `open`** ⇒ 成果再次不可设生效版本。

    重开意味着"那次完成不算数"。若标记停在 `resolved`，核心不变式
    「需要复核 ⇔ 存在 open 行」就被**静默**破坏了：成果看起来已复核完，
    而实际上那次复核已经作废。
    """
    env = _env(db)
    art_id, case_id, review_task_id = _open_mark(db, env)

    task_svc.start_task(db, task_id=review_task_id, actor_id=MANAGER)
    task_svc.complete_task(db, task_id=review_task_id, actor_id=MANAGER)
    assert not rv.open_for_artifact(db, art_id), "完成复核后标记应已解除"

    task_svc.reopen_task(
        db, task_id=review_task_id, actor_id=MANAGER, reason="复核结论有误，重新核对"
    )
    back = rv.open_for_artifact(db, art_id)
    assert back, "重开复核任务必须把标记退回 open（否则不变式静默失效）"
    row = [r for r in rv.list_for_case(db, case_id) if int(r["review_task_id"]) == review_task_id][
        0
    ]
    assert row["resolved_at"] is None and row["resolved_by"] is None, (
        "解除已被撤销 ⇒ 解除时间与人必须清回未知，留着就是一条错误的事实"
    )

    draft = art_svc.append_revision(
        db,
        artifact_id=art_id,
        payload={"freight": 22000, "currency": "CNY"},
        actor_id=MANAGER,
        source="manual",
    )
    with pytest.raises(art_svc.ArtifactRevalidationError):
        art_svc.confirm_revision(
            db, artifact_id=art_id, revision_no=int(draft["revision_no"]), actor_id=MANAGER
        )


def test_cancelling_a_plain_task_does_not_touch_revalidation(db):
    """取消**普通任务**不得动任何标记（联动只针对挂着复核项的那个任务）。

    这是负例：把联动写成"取消任何任务都清标记"，会让复核约束可以被一个
    毫不相干的操作绕过，而且看起来完全正常。
    """
    env = _env(db)
    art_id, case_id, _review_task_id = _open_mark(db, env)

    plain = task_svc.create_task(
        db,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        task_type=task_svc.TASK_TYPE_EXECUTION,
        title="普通执行任务",
    )
    task_svc.cancel_task(db, task_id=int(plain["task_id"]), actor_id=MANAGER)

    assert rv.open_for_artifact(db, art_id), "普通任务被取消不得解除复核标记"
    rows = rv.list_for_case(db, case_id)
    assert all(str(r["status"]) == "open" for r in rows), "标记应全部仍为 open"


def test_review_task_status_is_visible_on_the_case_surface(db):
    """复核任务的状态与标题要能在案件面读到（界面不能只拿到一个 id）。"""
    env = _env(db)
    _art_id, case_id, review_task_id = _open_mark(db, env)
    row = [r for r in rv.list_for_case(db, case_id) if int(r["review_task_id"]) == review_task_id][
        0
    ]
    assert row["task_title"], "复核任务标题要带出来（否则列表只能显示 #id）"
    assert str(row["task_status"]) == task_svc.STATUS_PENDING
    assert str(row["target_kind"]) == exc.TARGET_ARTIFACT
    assert row["target_revision_id"] is not None, "摘要要带目标版本（复核针对的是哪一版）"
