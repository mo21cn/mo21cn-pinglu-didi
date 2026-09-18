"""**经审批的委托货量变更** —— S6-1 / D1-09 / §10.1 第 8 步（2026-09-18）。

这份文件回答一件事：`Apply the 800→950 change` 这条判据在**产品内**能不能走通。

它此前走不通，而原因是**结构性**的：容量判定的需求量只有一个出处
（`capacity._assignment_demand` → `ent_assignment.quantity`），而把**已受理**委托的
货量改掉，全仓没有命令 ——
① `PATCH /assignments/{id}` 走 `update_draft`，对 `claimed` 单 409；
② 变更请求的 apply 只写**成果载荷**（`TARGET_KINDS = {task, artifact}`），不碰 quantity；
③ `UPDATE ent_assignment SET quantity = …` 只出现在**测试**里。
⇒ 那时只能靠直接改库驱动 D1-09，而手改出来的与用户点出来的**不是同一条路**：
前者证明不了审批、原子性、留痕与复核传播。

本文件覆盖八组：

1. **正例**：批准 → 应用 ⇒ 货量落库 + 版本递增 + 历史留痕 + 传播（复核任务）；
2. **批准时的校验**：形状/正数/单位/依据/「不是变更的变更」—— 坏内容**不进快照**；
3. **值级过期**：批准后有人绕过本命令改了货量 ⇒ 409 且**货量一动不动**；
4. **版本级过期**：批准后委托版本被推进 ⇒ 409 `stale_basis`；
5. **重复应用 / 并发**：状态机 + 条件更新两道闸门；
6. **越权**：非组织成员 404、无管理动作权限 403；
7. **读侧**：升序、未知保持未知（不补 0）、跨方言文本一致；
8. **读端点口径**：经理 200 / **货主 404** / 局外人 404、空列表不是 404。
"""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"

from decimal import Decimal  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import artifacts as art_svc  # noqa: E402
from app.modules.entrust import assignments as assign_svc  # noqa: E402
from app.modules.entrust import capacity as cap_svc  # noqa: E402
from app.modules.entrust import exceptions as exc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

OWNER = 1
MANAGER = 900
OUTSIDER = 901
ALL_PERMS = (
    '["entrust:view","entrust:assignment:claim","entrust:quote:create","entrust:task:dispatch"]'
)
#: 组织成员、能读案件，但**没有**管理动作权限（`entrust:task:dispatch`）。
VIEW_PERMS = '["entrust:view","entrust:assignment:claim"]'

CARGO_CATEGORY = "cargo_quantity_category"


@pytest.fixture()
def db():
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ── 播种助手 ────────────────────────────────────────────────────────────────


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


def _env(session, *, permissions: str = ALL_PERMS, quantity: str | None = "800.000") -> dict:
    """组织 + 经理成员 + 货主授权 + 一张**已受理**的委托（默认 800 吨）+ 一份成果。

    为什么要那份成果：`approved` **必须**给出 `basis_revision_id`（§3.1.1：决定必须指向
    它所依据的**精确**成果版本）。委托货量变更的依据通常正是"现有的那批成果"
    （报价、采购确认、运力确认…），所以夹具里备一份 —— 否则整条批准路径根本走不到，
    而"绕开这条硬规矩"才是真正该避免的事。

    ⚠️ 该成果**只作为依据**、**不进受影响项**：受影响项是委托单本身（`assignment` 目标）。
    把成果也登记进来会要求它同时带"经过批准的修改内容"，那是另一件事。
    """
    org = _org(session)
    _member(session, org, MANAGER, role="manager")
    _member(session, org, OUTSIDER, role="member")
    result = session.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": OWNER, "p": permissions, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()
    entrustment_id = int(result.lastrowid or 0)
    draft = assign_svc.create_assignment(
        session,
        owner_user_id=OWNER,
        title="钢材运输",
        quantity=quantity,
        quantity_unit="吨" if quantity is not None else None,
    )
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
    assignment_id = int(assignment["assignment_id"])
    artifact = art_svc.create_artifact(
        session,
        entrustment_id=entrustment_id,
        artifact_type="quote_parsed",
        payload={"freight": 12000, "currency": "CNY"},
        created_by=MANAGER,
        assignment_id=assignment_id,
    )
    basis_revision_id = int(
        session.execute(
            text("SELECT current_revision_id FROM ent_artifact WHERE id = :aid"),
            {"aid": int(artifact["artifact_id"])},
        ).scalar()
        or 0
    )
    return {
        "org_id": org,
        "entrustment_id": entrustment_id,
        "assignment_id": assignment_id,
        "artifact_id": int(artifact["artifact_id"]),
        "basis_revision_id": basis_revision_id,
    }


def _assignment_row(session, assignment_id: int) -> dict:
    return dict(
        session.execute(
            text("SELECT quantity, quantity_unit, revision FROM ent_assignment WHERE id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .one()
    )


def _history(session, assignment_id: int) -> list[dict]:
    return exc.list_quantity_changes(session, assignment_id=assignment_id)


def _quantity(session, assignment_id: int) -> Decimal | None:
    """库里的货量，读成 `Decimal`。

    ⛔ 不要用 `str(raw).rstrip("0")` 之类的"去掉尾零"写法：`"800.000".rstrip("0")`
    得到的是 **`"8"`**（它剥掉的是所有尾随零，包括整数部分的）。
    这类"看起来对"的比较会把 800 判成 8 —— 而它只在恰好整百/整千时出错。
    """
    raw = _assignment_row(session, assignment_id)["quantity"]
    return None if raw is None else Decimal(str(raw))


def _revision(session, assignment_id: int) -> int:
    return int(_assignment_row(session, assignment_id)["revision"])


def _change_body(*, quantity: str = "950.000", unit: str = "吨", basis: str = "客户加货") -> dict:
    return {"quantity": quantity, "quantity_unit": unit, "basis": basis}


def _case_with_assignment(session, env: dict, **overrides) -> dict:
    """一张**货量变更请求**，受影响项就是**本委托**（新目标类型 `assignment`）。"""
    params: dict = {
        "assignment_id": env["assignment_id"],
        "actor_id": MANAGER,
        "kind": exc.KIND_CHANGE_REQUEST,
        "title": "货量由 800 吨调整为 950 吨",
        "severity": "medium",
        "impact_kind": exc.IMPACT_REVIEW_REQUIRED,
        "links": [
            {"target_kind": exc.TARGET_ASSIGNMENT, "target_id": env["assignment_id"]},
        ],
    }
    params.update(overrides)
    return exc.raise_case(session, **params)


def _approve(
    session,
    case_id: int,
    *,
    env: dict,
    changes: dict | None = None,
    category: str | None = CARGO_CATEGORY,
    note: str | None = None,
    basis_revision_id: int | None = None,
) -> dict:
    """走到 `approved`：`change_request` 必须先经 `in_review`（两套状态机各自生效）。

    `basis_revision_id` 默认取**案件所依据的那份成果**的当前版本（§3.1.1 要求 `approved`
    必须指向精确的成果版本）。它与"委托货量的旧值"是**两件事**：
    前者记在案件行上（`case.basis_revision_id`），后者记在批准快照的
    `quantity_before` 里 —— 委托本身不是成果，没有成果版本可指。
    """
    exc.decide(
        session,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_IN_REVIEW,
        expected_revision=1,
        decision_note=note,
    )
    return exc.decide(
        session,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_APPROVED,
        expected_revision=2,
        basis_revision_id=(
            basis_revision_id if basis_revision_id is not None else int(env["basis_revision_id"])
        ),
        approved_changes=changes,
        change_category=category,
    )


def _kinds(events: list[dict]) -> list[str]:
    return [str(e["event_kind"]) for e in events]


def _set_quantity(session, assignment_id: int, *, quantity: str, unit: str = "吨") -> None:
    """**绕过本命令**直接改货量 —— 只用于构造"有人在我们之外动过它"这一情形。

    ⚠️ 刻意**不**推进 `revision`：绕过去的写入不会推进它（那是本命令的责任），
    而这正是值级核对要抓的东西 —— 若这里也 +1，被触发的是**版本级**那条闸门，
    值级那条就永远测不到（两条闸门"总是先响同一个"是典型的分支遮蔽）。
    """
    session.execute(
        text("UPDATE ent_assignment SET quantity = :q, quantity_unit = :u WHERE id = :aid"),
        {"q": quantity, "u": unit, "aid": assignment_id},
    )
    session.commit()


def _bump_revision(session, assignment_id: int) -> None:
    """只推进委托版本、不改值 —— 构造"版本级过期"（批准后委托被别人动过别的字段）。"""
    session.execute(
        text("UPDATE ent_assignment SET revision = revision + 1 WHERE id = :aid"),
        {"aid": assignment_id},
    )
    session.commit()


# ── 1. 正例：批准 → 应用 ────────────────────────────────────────────────────


def test_apply_lands_quantity_history_and_revision_in_one_go(db):
    """判据主线：批准后应用 ⇒ 库里是 950、版本 +1、历史留着一行 **800 → 950**。"""
    env = _env(db)
    aid = env["assignment_id"]
    before = _assignment_row(db, aid)
    base_revision = int(before["revision"])

    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    key = f"assignment#{aid}"
    _approve(
        db,
        case_id,
        env=env,
        changes={key: _change_body()},
    )

    # 批准快照里**记下了旧值** —— 它是应用时的值级依据，也是"从哪改到哪"的出处。
    snapshot = exc._load_approval_snapshot(db, case_id)
    assert snapshot is not None
    target = snapshot["targets"][0]
    assert target["target_kind"] == "assignment"
    assert target["target_id"] == aid
    assert target["basis_revision_id"] == base_revision
    assert target["quantity_before"] == {"quantity": "800.000", "quantity_unit": "吨"}
    assert snapshot["changes"][key]["quantity"] == "950.000"

    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    after = _assignment_row(db, aid)
    assert _quantity(db, aid) == Decimal("950"), f"库里应当已经是 950，实际 {after['quantity']!r}"
    assert after["quantity_unit"] == "吨"
    assert int(after["revision"]) == base_revision + 1, "货量变更必须推进委托版本"

    rows = _history(db, aid)
    assert len(rows) == 1
    row = rows[0]
    assert row["old_quantity"] == "800.000"
    assert row["new_quantity"] == "950.000"
    assert row["old_quantity_text"] == "800.000 吨"
    assert row["new_quantity_text"] == "950.000 吨"
    assert row["basis"] == "客户加货"
    assert row["exception_id"] == case_id
    assert row["base_revision"] == base_revision
    assert row["applied_by"] == MANAGER

    assert str(exc.get_case(db, case_id)["status"]) == exc.STATUS_APPLIED


def test_apply_propagates_revalidation_in_same_transaction(db):
    """「标待复核」不是附注：与货量更新**同一事务**生成复核任务（含运力复算那一条）。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    areas = [
        str(r["area"])
        for r in db.execute(
            text("SELECT area FROM ent_revalidation WHERE exception_id = :cid"),
            {"cid": case_id},
        ).mappings()
    ]
    # DR-0016 的货量那一行有五个复核区域；第一条就是运力/货物适配 ——
    # 即"原运力不再适用之后要复核与重新确认"这件事的落点。
    assert "船型、运力及货物适配" in areas, areas
    assert len(areas) == 5, areas

    planned = [
        e
        for e in exc.list_events(db, case_id)
        if str(e["event_kind"]) == exc.EVENT_REVALIDATION_PLANNED
    ]
    assert len(planned) == 1
    assert str(planned[0]["payload"]["category"]) == CARGO_CATEGORY


def test_applied_event_carries_before_and_after(db):
    """应用事件必须带 `800 → 950`：审计链上"改了什么"不能只留一个 950。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    applied = [e for e in exc.list_events(db, case_id) if str(e["event_kind"]) == exc.EVENT_APPLIED]
    assert len(applied) == 1
    item = applied[0]["payload"]["applied"][0]
    assert item["target"] == f"assignment#{aid}"
    assert item["old_quantity"] == "800.000"
    assert item["new_quantity"] == "950.000"
    assert item["new_quantity_unit"] == "吨"


def test_change_actually_moves_the_capacity_rule_verdict(db):
    """正例的**业务意义**：同一个 900 吨候选，变更前**确认下来了**、变更后**不再适用**。

    这是 D1-09 那句 `the 900-tonne candidate unsuitable` 在产品内的完整形态：
    需求量确实从 `ent_assignment.quantity` 读 ⇒ 改库之后**同一套规则**换了结论。
    ⛔ 这里绝不能"手工把 demand 当参数喂给规则"（`seed_entrust_canonical.py` 只能那样，
    因为它没有可用的命令）—— 参数喂进去证明不了"货量被改过"。
    """
    env = _env(db)
    aid = env["assignment_id"]
    candidate = cap_svc.record_candidate(
        db,
        assignment_id=aid,
        actor_user_id=MANAGER,
        carrier="甲承运",
        capacity_tonnes="900.000",
        vessel_count=1,
        allows_partial_load=False,
        valid_until="2026-12-31",
        evidence_kind="confirmation",
        evidence_ref="doc#900",
    )
    # 变更前：900 ≥ 800 ⇒ 四条全过，确认成立
    confirmation = cap_svc.confirm_capacity(
        db,
        assignment_id=aid,
        candidate_id=int(candidate["candidate_id"]),
        actor_user_id=MANAGER,
        agreed_scope="整船包运",
        entrustment_id=env["entrustment_id"],
    )
    cid = int(confirmation["confirmation_id"])
    assert cap_svc.recheck_confirmation(db, confirmation_id=cid)["still_valid"] is True

    # 经审批把货量改成 950
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    # 变更后：同一套规则、同一个候选 ⇒ **只剩 capacity 不过**
    recheck = cap_svc.recheck_confirmation(db, confirmation_id=cid)
    assert recheck["still_valid"] is False
    failures = [r["rule_code"] for r in recheck["rule_checks"] if r["outcome"] == "fail"]
    assert failures == ["capacity"], (
        f"只有 capacity 该不过；其余三条照旧通过。实际不过的是 {failures} —— "
        "否则「900 吨候选不适用」就成了一句无从定位的话"
    )
    # 「为什么不再成立」必须能指出来：需求量 800 → 950
    assert "demand_tonnes" in recheck["changed_fields"]


# ── 2. 批准时的校验（坏内容**不进快照**）────────────────────────────────────


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"quantity_unit": "吨", "basis": "x"}, "缺字段"),
        ({"quantity": "950.000", "basis": "x"}, "缺字段"),
        ({"quantity": "950.000", "quantity_unit": "吨"}, "缺字段"),
        ({"quantity": "0", "quantity_unit": "吨", "basis": "x"}, "正数"),
        ({"quantity": "-5", "quantity_unit": "吨", "basis": "x"}, "正数"),
        ({"quantity": "abc", "quantity_unit": "吨", "basis": "x"}, "正数"),
        ({"quantity": "950.000", "quantity_unit": "   ", "basis": "x"}, "单位不能为空"),
        ({"quantity": "950.000", "quantity_unit": "吨", "basis": "  "}, "依据不能为空"),
        # 「不是变更的变更」：批了它只会白跑一轮复核（还会推进一个版本号）。
        ({"quantity": "800", "quantity_unit": "吨", "basis": "x"}, "与当前一致"),
        ({"quantity": "800.000", "quantity_unit": "吨", "basis": "x"}, "与当前一致"),
    ],
)
def test_approval_rejects_bad_change_content(db, change: dict, needle: str):
    """坏内容必须在**批准**时拦住 —— `apply` 只认快照，没有第二次机会。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    with pytest.raises(exc.ExceptionCaseError, match=needle):
        _approve(db, case_id, env=env, changes={f"assignment#{aid}": change})

    # 批准失败 ⇒ 案件停在上一步，**快照没写进去**（不是"写了个半成品"）
    assert str(exc.get_case(db, case_id)["status"]) == exc.STATUS_IN_REVIEW
    assert exc._load_approval_snapshot(db, case_id) is None
    # 货量当然也没动
    assert _quantity(db, aid) == Decimal("800")


def test_approval_normalizes_quantity_to_fixed_places(db):
    """`950` 进快照时被归一成 `950.000` —— 记录与写库用同一个字符串。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body(quantity="950")})
    snapshot = exc._load_approval_snapshot(db, case_id)
    assert snapshot is not None
    assert snapshot["changes"][f"assignment#{aid}"]["quantity"] == "950.000"


def test_change_from_unknown_quantity_is_legal(db):
    """**未知保持未知**：从 NULL 改成一个确定的数是合法变更，历史里旧值也是 NULL。

    把它当 0 处理会让历史显示"从 0 涨到 950"，而那是编出来的一个事实。
    """
    env = _env(db, quantity=None)
    aid = env["assignment_id"]
    assert _assignment_row(db, aid)["quantity"] is None
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    row = _history(db, aid)[0]
    assert row["old_quantity"] is None
    assert row["old_quantity_unit"] is None
    assert row["old_quantity_text"] == "未知"
    assert row["new_quantity"] == "950.000"


def test_change_without_approved_content_is_rejected(db):
    """受影响项登记了、但批准时**没给**修改内容 ⇒ 400（P4-A4：不许临时替换）。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes=None)
    with pytest.raises(exc.ExceptionCaseError, match="没有经过批准的修改内容"):
        exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)
    assert _quantity(db, aid) == Decimal("800")


# ── 3. 值级过期：有人绕过本命令改过货量 ─────────────────────────────────────


def test_value_level_staleness_is_rejected_and_rolls_back(db):
    """批准后有人直接改库 ⇒ 409，**货量一动不动**，且拒绝原因指名道姓。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})

    _set_quantity(db, aid, quantity="901.000")

    with pytest.raises(exc.AssignmentQuantityStaleError):
        exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    # 货量保持被绕过后的那个值（不是被"改回 800"，也不是"改成 950"）
    assert _quantity(db, aid) == Decimal("901")
    assert _history(db, aid) == [], "整笔回滚：不能留下历史行"
    assert str(exc.get_case(db, case_id)["status"]) == exc.STATUS_APPROVED, "案件不得转 applied"

    rejected = [
        e
        for e in exc.list_events(db, case_id)
        if str(e["event_kind"]) == exc.EVENT_APPLIED_REJECTED
    ]
    assert len(rejected) == 1
    assert str(rejected[0]["payload"]["reason"]) == "stale_quantity", (
        "审计原因必须是具体的那一个 —— 「应用失败」四个字把定位工作留给了下一个人"
    )


def test_revision_level_staleness_still_works(db):
    """版本级过期走**原有**那条路（409 + `stale_basis`），不被新分支吞掉。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    _bump_revision(db, aid)  # 只推进版本，货量不变

    with pytest.raises(exc.ExceptionCaseConflictError, match="依据版本已变化"):
        exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    rejected = [
        e
        for e in exc.list_events(db, case_id)
        if str(e["event_kind"]) == exc.EVENT_APPLIED_REJECTED
    ]
    assert str(rejected[0]["payload"]["reason"]) == "stale_basis"


# ── 4. 重复应用 / 并发 ──────────────────────────────────────────────────────


def test_second_apply_is_rejected_and_writes_no_second_history_row(db):
    """重复应用 ⇒ 409（状态机 `applied → applied` 是自环，天然非法）。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)
    quantity_after_first = _assignment_row(db, aid)["quantity"]

    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=4)

    assert len(_history(db, aid)) == 1, "第二次应用不得再留一行"
    assert _assignment_row(db, aid)["quantity"] == quantity_after_first


def test_same_case_cannot_be_written_twice_through_the_history_unique_key(db):
    """`UNIQUE (exception_id)` 是**数据库层**的第二道闸 —— 绕过服务层也写不进第二行。

    并发/重放的最终防线：条件更新挡"版本没变时的重复写"，唯一键挡"同一案件写两次"。
    没有它，一个绕过状态机的写入路径就能把同一笔变更记两遍。
    """
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    # 直接插一行同 exception_id 的历史 —— 必须被唯一键挡住
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ent_assignment_quantity_change "
                "(assignment_id, exception_id, base_revision, old_quantity, old_quantity_unit,"
                " new_quantity, new_quantity_unit, basis, applied_by, applied_at) "
                "VALUES (:aid, :cid, 1, '1.000', '吨', '2.000', '吨', 'x', 1, :ts)"
            ),
            {"aid": aid, "cid": case_id, "ts": utcnow_naive().strftime(_TS)},
        )
        db.commit()
    db.rollback()


def test_concurrent_revision_bump_loses_the_conditional_update(db, monkeypatch):
    """**并发**：另一笔事务在"检查通过之后、条件更新之前"推进了版本 ⇒ 条件更新落空 ⇒ 409。

    这个时间窗在单线程用例里无法自然出现，所以**只**把"读当前版本"这一步换掉，
    模拟"读到的是旧值" —— 条件更新（`WHERE revision = :base`）仍然是真实执行的那一条。
    ⛔ 不改成"先查后写"：那会让两个并发请求都通过检查，而只有一个能改成功，
    另一个静默地什么都没做却报告成功。
    """
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})

    snapshot = exc._load_approval_snapshot(db, case_id)
    assert snapshot is not None
    approved_basis = int(snapshot["targets"][0]["basis_revision_id"])
    _bump_revision(db, aid)  # 别人先提交了一步：版本 R+1，货量不变

    monkeypatch.setattr(exc, "_assignment_current_revision", lambda _session, _aid: approved_basis)

    with pytest.raises(exc.AssignmentQuantityConflictError) as caught:
        exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)
    assert caught.value.audit_reason == "concurrent_quantity_change"

    assert _quantity(db, aid) == Decimal("800")
    assert _history(db, aid) == []
    rejected = [
        e
        for e in exc.list_events(db, case_id)
        if str(e["event_kind"]) == exc.EVENT_APPLIED_REJECTED
    ]
    assert str(rejected[0]["payload"]["reason"]) == "concurrent_quantity_change"


def test_history_write_failure_rolls_back_the_quantity_update(db):
    """**统一事务**：历史行写不进去 ⇒ 货量更新一并回滚（不留"改了一半的委托"）。

    构造方式：先手插一条同 `(assignment_id, exception_id)` 的历史行，
    让应用时的 INSERT 撞唯一键 —— 这一步在条件更新**之后**，
    所以它检验的正是"前面已经改过了，后面失败会不会回滚"。
    """
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})

    db.execute(
        text(
            "INSERT INTO ent_assignment_quantity_change "
            "(assignment_id, exception_id, base_revision, old_quantity, old_quantity_unit,"
            " new_quantity, new_quantity_unit, basis, applied_by, applied_at) "
            "VALUES (:aid, :cid, 0, '1.000', '吨', '2.000', '吨', '占位', 1, :ts)"
        ),
        {"aid": aid, "cid": case_id, "ts": utcnow_naive().strftime(_TS)},
    )
    db.commit()

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    assert _quantity(db, aid) == Decimal("800"), "条件更新已执行但历史写失败 ⇒ 必须整笔回滚"
    assert str(exc.get_case(db, case_id)["status"]) == exc.STATUS_APPROVED


# ── 5. 越权 ─────────────────────────────────────────────────────────────────


def test_non_member_cannot_apply(db):
    """非组织成员 ⇒ 404（不泄漏"这张单存在"），货量不动。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})
    with pytest.raises(exc.ExceptionCaseNotFoundError):
        exc.apply_case(db, exception_id=case_id, actor_id=OUTSIDER + 500, expected_revision=3)
    assert _quantity(db, aid) == Decimal("800")


def test_member_without_dispatch_permission_cannot_apply(db):
    """组织成员但**无管理动作权限** ⇒ 403（能读不能改），货量不动。

    构造：先把整条链走到 `approved`（那几步同样要 `entrust:task:dispatch`），
    再把货主授权收紧成只读，最后应用 —— 这一步检验的是**应用本身**的权限，
    而不是"前面几步进不来"。
    """
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})

    db.execute(
        text(
            "UPDATE ent_entrustment SET permissions = :p WHERE org_id = :o AND entrust_user_id = :u"
        ),
        {"p": VIEW_PERMS, "o": env["org_id"], "u": OWNER},
    )
    db.commit()

    from app.modules.entrust.access import AccessDeniedError

    with pytest.raises(AccessDeniedError):
        exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)
    assert _quantity(db, aid) == Decimal("800")
    assert _history(db, aid) == []


def test_assignment_link_of_another_assignment_is_403(db):
    """受影响项必须与案件**同属一张委托** —— 拿乙单来改甲单的货量必须在登记时就被挡。"""
    env = _env(db)
    other = _env(db)
    case = _case_with_assignment(db, env)
    with pytest.raises(exc.ExceptionCaseScopeError):
        exc.add_link(
            db,
            exception_id=int(case["id"]),
            actor_id=MANAGER,
            expected_revision=1,
            target_kind=exc.TARGET_ASSIGNMENT,
            target_id=other["assignment_id"],
        )


# ── 6. 读侧 ─────────────────────────────────────────────────────────────────


def test_history_is_ordered_and_carries_both_values(db):
    """两次变更按时间升序，且每次的"从哪到哪"都能独立读出（不是只留最后一步）。"""
    env = _env(db)
    aid = env["assignment_id"]

    first = _case_with_assignment(db, env)
    _approve(db, int(first["id"]), env=env, changes={f"assignment#{aid}": _change_body()})
    exc.apply_case(db, exception_id=int(first["id"]), actor_id=MANAGER, expected_revision=3)

    second = _case_with_assignment(db, env, title="再加 50 吨")
    _approve(
        db,
        int(second["id"]),
        env=env,
        changes={f"assignment#{aid}": _change_body(quantity="1000.000", basis="再补一船")},
    )
    exc.apply_case(db, exception_id=int(second["id"]), actor_id=MANAGER, expected_revision=3)

    rows = _history(db, aid)
    assert [(r["old_quantity"], r["new_quantity"]) for r in rows] == [
        ("800.000", "950.000"),
        ("950.000", "1000.000"),
    ]
    assert [r["basis"] for r in rows] == ["客户加货", "再补一船"]


def test_empty_history_is_empty_list_not_error(db):
    """没改过货量 ⇒ 空列表（不是异常、不是 404）—— 集合可以为空，单条记录不行。"""
    env = _env(db)
    assert _history(db, env["assignment_id"]) == []


def test_quantity_parsing_agrees_with_capacity_module(db):
    """两侧定点解析口径必须**同一份结论** —— 漂移会让同一份数据判出两种结果。

    `exceptions._quantity_decimal` 与 `capacity._decimal` 是两份实现
    （前者要判"新值必须为正"，后者是通用读值），这里用同一张输入表钉死它们一致。
    这也是本仓"适用性有两个定义是必然出错的"那条教训的一道防线。
    """
    cases: list[object] = [
        None,
        True,  # bool 是 int 的子类：不挡住会把 True 当成吨位 1
        False,
        0,
        1,
        -5,
        950,
        Decimal("800.000"),
        Decimal("NaN"),
        Decimal("Infinity"),
        "950.000",
        " 950 ",
        "",
        "   ",
        "abc",
        "NaN",
        950.5,
        float("nan"),
        float("inf"),
    ]
    for raw in cases:
        assert exc._quantity_decimal(raw) == cap_svc._decimal(raw), f"口径漂移：{raw!r}"

    assert exc._quantity_decimal(True) is None
    assert exc._quantity_decimal(Decimal("NaN")) is None
    assert exc._quantity_text(Decimal("950")) == "950.000"


def test_quantity_change_projection_keeps_raw_and_text_in_step(db):
    """投影同时给原值与文案：界面显示文案，程序比对用原值，两者同一处产出。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body(quantity="950.5")})
    exc.apply_case(db, exception_id=case_id, actor_id=MANAGER, expected_revision=3)

    row = _history(db, aid)[0]
    assert row["new_quantity"] == "950.500"
    assert row["new_quantity_text"] == "950.500 吨"
    # 3 位小数是**跨方言**的：SQLite 侧是 NUMERIC、MySQL 侧是 DECIMAL(14,3)，
    # 固定位数让两边读回来是同一个字符串（与 `capacity._dec_text` 同口径）。
    assert Decimal(row["new_quantity"]) == Decimal("950.5")


def test_approval_summary_shows_quantity_change_for_the_page(db):
    """界面摘要必须给出 `before → after` —— 否则「应用变更」就是一次盲操作。"""
    env = _env(db)
    aid = env["assignment_id"]
    case = _case_with_assignment(db, env)
    case_id = int(case["id"])
    _approve(db, case_id, env=env, changes={f"assignment#{aid}": _change_body()})

    summary = exc.approval_summary(db, case_id)
    assert summary is not None
    target = summary["targets"][0]
    assert target["target_kind"] == "assignment"
    assert target["quantity_change"] == {
        # ⚠️ `quantity_text` 是**服务端算好的显示文案**（`_quantity_side` 与历史行共用
        #    同一份实现）。界面直接用它，不自己拼 `数值 + 单位` ——
        #    各拼一份必然漂移，而漂移的表现是"历史里写 800 吨、批准摘要里写 800.000吨"。
        "before": {"quantity": "800.000", "quantity_unit": "吨", "quantity_text": "800.000 吨"},
        "after": {"quantity": "950.000", "quantity_unit": "吨", "quantity_text": "950.000 吨"},
        "basis": "客户加货",
    }


def test_artifact_snapshot_shape_is_unchanged(db):
    """成果目标的快照形状**一个字没变** —— 新分支不得顺带改老口径。

    （`test_entrust_exceptions_apply.py` 里对 targets 做了全等断言；
    这里再钉一次，是为了让"给 assignment 加了键"这件事不会悄悄漏到 artifact 上。）
    """
    env = _env(db)
    art = art_svc.create_artifact(
        db,
        entrustment_id=env["entrustment_id"],
        artifact_type="quote_parsed",
        payload={"freight": 12000, "currency": "CNY"},
        created_by=MANAGER,
        assignment_id=env["assignment_id"],
    )
    art_id = int(art["artifact_id"])
    basis = int(
        db.execute(
            text("SELECT current_revision_id FROM ent_artifact WHERE id = :aid"),
            {"aid": art_id},
        ).scalar()
    )
    case = exc.raise_case(
        db,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        kind=exc.KIND_CHANGE_REQUEST,
        title="运价调整",
        severity="medium",
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        links=[{"target_kind": exc.TARGET_ARTIFACT, "target_id": art_id}],
    )
    case_id = int(case["id"])
    _approve(
        db,
        case_id,
        env=env,
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    snapshot = exc._load_approval_snapshot(db, case_id)
    assert snapshot is not None
    assert snapshot["targets"] == [
        {
            "target_kind": "artifact",
            "target_id": art_id,
            "basis_revision_id": basis,
            "has_changes": True,
        }
    ]


# ── 7. 读端点的可见性口径（`quantity_api.py`）────────────────────────────────
#
# 与运力那一组同口径：**不给货主本人放行**。理由是 `basis` 是经理写的变更依据，
# 可能带内部口径；货量本身客户当然在委托详情里看得见。
# 这一条如果写反（顺手给货主放行），症状是**客户看到了内部判断**，而界面上一切正常。


@pytest.fixture()
def client_env(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app
    from app.models import Base
    from app.modules.auth.router import get_db
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", True)
    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _login(client, prefix: str) -> dict:
    import uuid

    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['access_token']}"}


def _api_env(env) -> SimpleNamespace:
    """TestClient + 真登录的经理/货主/局外人 + 一张已受理的委托（800 吨）。"""
    db = env.make_session()
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "own")
    outsider = _login(env.client, "out")

    org = _org(db)
    _member(db, org, user_id=int(manager["user_id"]), role="manager")
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {
            "o": org,
            "u": int(owner["user_id"]),
            "p": ALL_PERMS,
            "c": utcnow_naive().strftime(_TS),
        },
    )
    db.commit()
    draft = assign_svc.create_assignment(
        db,
        owner_user_id=int(owner["user_id"]),
        title="钢材运输",
        quantity="800.000",
        quantity_unit="吨",
    )
    submitted = assign_svc.submit_assignment(
        db,
        assignment_id=draft["assignment_id"],
        actor_id=int(owner["user_id"]),
        org_id=org,
        expected_revision=draft["revision"],
    )
    assignment = assign_svc.claim_assignment(
        db, assignment_id=submitted["assignment_id"], actor_id=int(manager["user_id"])
    )
    aid = int(assignment["assignment_id"])
    artifact = art_svc.create_artifact(
        db,
        entrustment_id=int(result.lastrowid or 0),
        artifact_type="quote_parsed",
        payload={"freight": 12000, "currency": "CNY"},
        created_by=int(manager["user_id"]),
        assignment_id=aid,
    )
    basis = int(
        db.execute(
            text("SELECT current_revision_id FROM ent_artifact WHERE id = :a"),
            {"a": int(artifact["artifact_id"])},
        ).scalar()
        or 0
    )
    db.close()
    return SimpleNamespace(
        client=env.client,
        make_session=env.make_session,
        aid=aid,
        basis=basis,
        manager_user_id=int(manager["user_id"]),
        manager=_headers(manager),
        owner=_headers(owner),
        outsider=_headers(outsider),
    )


def test_quantity_changes_endpoint_is_empty_for_a_fresh_assignment(client_env):
    env = _api_env(client_env)
    resp = client_env.client.get(
        f"/api/v1/entrust/assignments/{env.aid}/quantity-changes", headers=env.manager
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [], "没改过货量 ⇒ **空列表**；这不是 404（集合可以为空）"


def test_quantity_changes_endpoint_hides_the_internal_basis_from_the_owner(client_env):
    """货主 **404**（不是 403、不是空列表）：本支线不给货主旁路，也不泄漏存在性。"""
    env = _api_env(client_env)
    owner = client_env.client.get(
        f"/api/v1/entrust/assignments/{env.aid}/quantity-changes", headers=env.owner
    )
    assert owner.status_code == 404, owner.text
    outsider = client_env.client.get(
        f"/api/v1/entrust/assignments/{env.aid}/quantity-changes", headers=env.outsider
    )
    assert outsider.status_code == 404, outsider.text


def test_quantity_changes_endpoint_returns_applied_change(client_env):
    """经理侧能读到那条变更（值 + 文案 + 依据 + 来源案件）。"""
    env = _api_env(client_env)
    db = client_env.make_session()
    actor = env.manager_user_id
    case = _case_with_assignment(
        db, {"assignment_id": env.aid, "entrustment_id": 0}, actor_id=actor
    )
    case_id = int(case["id"])
    # 直接走服务层到 applied（写端点不在本切片的新增范围里）
    from app.modules.entrust import exceptions as exc2

    exc2.decide(
        db,
        exception_id=case_id,
        actor_id=actor,
        to_status=exc2.STATUS_IN_REVIEW,
        expected_revision=1,
    )
    exc2.decide(
        db,
        exception_id=case_id,
        actor_id=actor,
        to_status=exc2.STATUS_APPROVED,
        expected_revision=2,
        basis_revision_id=env.basis,
        approved_changes={f"assignment#{env.aid}": _change_body()},
        change_category=CARGO_CATEGORY,
    )
    exc2.apply_case(db, exception_id=case_id, actor_id=actor, expected_revision=3)
    db.close()

    resp = client_env.client.get(
        f"/api/v1/entrust/assignments/{env.aid}/quantity-changes", headers=env.manager
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["old_quantity"] == "800.000"
    assert rows[0]["new_quantity"] == "950.000"
    assert rows[0]["old_quantity_text"] == "800.000 吨"
    assert rows[0]["new_quantity_text"] == "950.000 吨"
    assert rows[0]["basis"] == "客户加货"
    assert rows[0]["exception_id"] == case_id
