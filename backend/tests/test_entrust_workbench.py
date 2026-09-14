"""委托工作台七槽位聚合投影（UI-05）—— DR-0010 的验收测试。

对应 DR-0010 §6 验证表：

* **#1** 槽位顺序与 key 与 §3.1 逐字一致（本文件断言后端半边；
  前端配置表的半边在 `scripts/verify_entrust_ui.js` 做静态交叉核对）；
* **#2** 每槽 4 个派生字段可由投影返回，**未知保持 null 且状态正确**；
* **#4** 范围内槽位的人工落点（后端半边：每个开放槽位都有可执行的下一步依据——
  责任人 / 待办任务 / 成果精确版本）。

另覆盖本切片的业务口径：槽位 ↔ 数据落点映射、同客户多委托隔离、跨单访问拒绝、
「本期未开放」不得退化成「暂无记录」、只读性。
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

_TS = "2026-09-13 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:task:dispatch","entrust:agent:job"]'
)
_WB_URL = "/api/v1/entrust/assignments/{aid}/workbench"
_ARTIFACT_URL = "/api/v1/entrust/entrustments/{eid}/artifacts"
_ARTIFACT_LIST_URL = "/api/v1/entrust/assignments/{aid}/artifacts"


# ─────────────────────────────────────────── fixtures / 播种


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 播种会话；`ENTRUST_ENABLED=True`，模型与微信都走确定性 mock。"""
    from app.core.config import get_settings
    from app.main import app
    from app.models import Base
    from app.modules.auth.router import get_db
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    settings = get_settings()
    monkeypatch.setattr(settings, "ENTRUST_ENABLED", True)
    monkeypatch.setattr(settings, "WECHAT_MOCK", True)
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _org(db, name="测试组织") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": f"{name}-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": _TS},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str = ALL_PERMS) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "p": permissions, "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _assignment(
    db,
    *,
    owner_id: int,
    org_id: int | None,
    title: str = "南宁→贵港 钢材运输",
    cargo: str | None = "钢材",
    quantity: str | None = None,
    unit: str | None = None,
    updated_at: str = _TS,
) -> int:
    """造一张已受理的委托单（`org_id=None` 表示还没选定服务经营主体的草稿）。"""
    result = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status,"
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, :cargo, :q, :u, :s, 1, :c, :upd)"
        ),
        {
            "o": owner_id,
            "g": org_id,
            "t": title,
            "cargo": cargo,
            "q": quantity,
            "u": unit,
            "s": "claimed" if org_id is not None else "draft",
            "c": _TS,
            "upd": updated_at,
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


def _task(
    db,
    *,
    assignment_id: int,
    task_type: str,
    title: str = "任务",
    status: str = "pending",
    assignee: int | None = None,
    precondition: int | None = None,
    created_by: int = 1,
    updated_at: str = _TS,
) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_workflow_task "
            "(assignment_id, task_type, title, status, assignee_user_id, precondition_task_id,"
            " created_by, created_at, updated_at) "
            "VALUES (:a, :tt, :ti, :s, :asg, :pre, :cb, :c, :upd)"
        ),
        {
            "a": assignment_id,
            "tt": task_type,
            "ti": title,
            "s": status,
            "asg": assignee,
            "pre": precondition,
            "cb": created_by,
            "c": _TS,
            "upd": updated_at,
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


def _orphan_artifact(db, *, eid: int, artifact_type: str = "quote_parsed") -> int:
    """造一份**归属为空**的成果（归属机制上线前的存量形态）。"""
    result = db.execute(
        text(
            "INSERT INTO ent_artifact "
            "(entrustment_id, assignment_id, artifact_type, current_revision_id, status,"
            " created_at, updated_at) "
            "VALUES (:e, NULL, :t, NULL, 'active', :c, :c)"
        ),
        {"e": eid, "t": artifact_type, "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _seed(env, db, *, quantity: str | None = None, unit: str | None = None):
    """组织 + 经理 + 货主授权 + 一张已受理委托单 → (manager, owner, org, eid, aid)。"""
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=owner["user_id"])
    aid = _assignment(db, owner_id=owner["user_id"], org_id=org, quantity=quantity, unit=unit)
    return manager, owner, org, eid, aid


def _get(env, user: dict, aid: int):
    return env.client.get(_WB_URL.format(aid=aid), headers=_headers(user))


def _slot(body: dict, key: str) -> dict:
    for slot in body["slots"]:
        if slot["key"] == key:
            return slot
    raise AssertionError(f"槽位 {key} 不存在；实际 {[s['key'] for s in body['slots']]}")


def _create_artifact(
    env, user: dict, eid: int, *, payload: dict | None = None, aid: int | None = None
):
    body: dict = {
        "artifact_type": "quote_parsed",
        "payload": payload if payload is not None else {"carrier": "桂航物流", "rate": "38.50"},
    }
    if aid is not None:
        body["assignment_id"] = aid
    return env.client.post(
        _ARTIFACT_URL.format(eid=eid),
        json=body,
        headers=_headers(user, f"art-{uuid.uuid4().hex[:8]}"),
    )


# ─────────────────────────────────────────── 1. 槽位定义（DR-0010 §3.1 / 验证 #1）


def test_slot_specs_are_self_consistent():
    """槽位声明必须与任务取值域、成果注册表一致（配置漂移要在这里失败）。"""
    from app.modules.entrust import workbench as wb

    wb.assert_slot_specs_consistent()
    assert len(wb.SLOT_SPECS) == 7
    assert len(set(wb.SLOT_ORDER)) == 7


def test_slot_order_and_titles_match_dr0010(env):
    """七槽位的顺序、key、标题与 DR-0010 §3.1 逐字一致，**不得重排**。"""
    from app.modules.entrust import workbench as wb

    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)

    resp = _get(env, manager, aid)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [s["key"] for s in body["slots"]] == [
        "overview",
        "plan_tasks",
        "procurement",
        "customer_contracts",
        "execution",
        "exceptions",
        "settlement",
    ]
    assert [s["title"] for s in body["slots"]] == [
        "委托概况",
        "方案与任务",
        "采购与报价",
        "对客方案与合同",
        "履约与交接",
        "异常与变更",
        "费用与结案",
    ]
    assert [s["key"] for s in body["slots"]] == list(wb.SLOT_ORDER)


def test_every_slot_has_four_derived_fields_and_counts(env):
    """每槽四字段（当前成果 / 未决问题 / 下一责任方 / 最后更新）+ 计数都在。"""
    from app.modules.entrust import workbench as wb

    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    body = _get(env, manager, aid).json()

    current_states = {wb.CURRENT_PRESENT, wb.CURRENT_NO_RECORD}
    issue_states = {wb.ISSUES_NONE, wb.ISSUES_PRESENT, wb.ISSUES_MISSING_INFO}
    owner_states = {wb.OWNER_ASSIGNED, wb.OWNER_UNASSIGNED, wb.OWNER_NOT_APPLICABLE}

    for slot in body["slots"]:
        assert set(slot) >= {
            "key",
            "title",
            "available",
            "unavailable_reason",
            "current",
            "issues",
            "next_owner",
            "updated_at",
            "counts",
        }, slot["key"]
        assert slot["current"]["state"] in current_states, slot["key"]
        assert slot["issues"]["state"] in issue_states, slot["key"]
        assert slot["next_owner"]["state"] in owner_states, slot["key"]
        assert set(slot["counts"]) == {"artifacts", "tasks", "open_tasks", "unassigned_tasks"}
        # 未决问题的 state 必须与 items 自洽，不能"有 0 条却说有问题"
        assert (slot["issues"]["count"] == 0) == (slot["issues"]["state"] == wb.ISSUES_NONE)


# ─────────────────────────────────────────── 2. 空值四态（DR-0010 §3.6 / 验证 #2）


def test_empty_assignment_reports_no_record_not_zero(env):
    """没有任何记录 → 「暂无记录」，而不是 0、也不是空白（DR-0010 §3.6）。"""
    from app.modules.entrust import workbench as wb

    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db, quantity=None)
    body = _get(env, manager, aid).json()

    for key in ("procurement", "customer_contracts", "execution", "settlement", "plan_tasks"):
        slot = _slot(body, key)
        assert slot["current"]["state"] == wb.CURRENT_NO_RECORD, key
        assert slot["current"]["text"] == "", key
        assert slot["counts"]["tasks"] == 0, key
        assert slot["updated_at"] is None, key

    # 委托本体没填货量 → 「信息缺失」（不是"暂无记录"）
    assert _slot(body, "overview")["current"]["state"] == wb.CURRENT_PRESENT
    assert _slot(body, "overview")["issues"]["state"] == wb.ISSUES_MISSING_INFO


def test_open_task_without_assignee_is_unassigned(env):
    """有未完成任务但没人负责 → 「尚未分配」（不是「不适用」）。"""
    from app.modules.entrust import workbench as wb

    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    _task(db, assignment_id=aid, task_type="quote", title="询价", status="pending", assignee=None)

    slot = _slot(_get(env, manager, aid).json(), "procurement")
    assert slot["next_owner"]["state"] == wb.OWNER_UNASSIGNED
    assert slot["next_owner"]["user_id"] is None
    assert slot["counts"]["open_tasks"] == 1
    assert slot["counts"]["unassigned_tasks"] == 1
    # 未指派同时要出现在未决问题里，而不是只在责任方一栏体现
    assert any(i["kind"] == wb.ISSUE_UNASSIGNED_TASK for i in slot["issues"]["items"])


def test_all_tasks_closed_is_not_applicable(env):
    """该槽任务全部结束 → 「不适用」（没有下一步动作，不是"还没分配"）。"""
    from app.modules.entrust import workbench as wb

    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    _task(db, assignment_id=aid, task_type="settlement", title="出结算", status="done", assignee=7)

    slot = _slot(_get(env, manager, aid).json(), "settlement")
    assert slot["next_owner"]["state"] == wb.OWNER_NOT_APPLICABLE
    assert slot["counts"]["open_tasks"] == 0
    assert slot["current"]["state"] == wb.CURRENT_PRESENT


def test_missing_required_field_is_missing_info(env):
    """成果缺必填字段 → 「信息缺失」，且缺项**点名到字段**。"""
    from app.modules.entrust import workbench as wb

    db = env.make_session()
    manager, _, _, eid, aid = _seed(env, db)
    # quote_parsed 必填 (carrier, rate)：只给 carrier → 缺 rate
    assert (
        _create_artifact(env, manager, eid, payload={"carrier": "桂航物流"}, aid=aid).status_code
        == 200
    )

    slot = _slot(_get(env, manager, aid).json(), "procurement")
    assert slot["issues"]["state"] == wb.ISSUES_MISSING_INFO
    missing = [i for i in slot["issues"]["items"] if i["kind"] == wb.ISSUE_MISSING_FIELD]
    assert missing, slot["issues"]
    assert "rate" in missing[0]["text"]


def test_blocked_task_reports_precondition_as_issue(env):
    """等待且带前置条件的任务 → 阻断问题（DR-0010 §3.5「阻断条件」）。"""
    from app.modules.entrust import workbench as wb

    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    first = _task(db, assignment_id=aid, task_type="purchase", title="先确认供应商")
    _task(
        db,
        assignment_id=aid,
        task_type="purchase",
        title="再下单",
        status="waiting",
        precondition=first,
    )

    slot = _slot(_get(env, manager, aid).json(), "procurement")
    kinds = [i["kind"] for i in slot["issues"]["items"]]
    assert wb.ISSUE_BLOCKED in kinds


# ─────────────────────────────────────────── 3. 「本期未开放」≠「暂无记录」


def test_exceptions_slot_is_marked_not_open(env):
    """`exceptions` 必须显式标注「本期未开放」（DR-0010 §3.8），不得套用空值四态。"""
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    slot = _slot(_get(env, manager, aid).json(), "exceptions")

    assert slot["available"] is False
    assert slot["unavailable_reason"], "未开放槽位必须给理由，否则界面只能猜"
    assert "未开放" in slot["unavailable_reason"]
    assert slot["counts"]["tasks"] == 0
    assert slot["current"]["state"] == "no_record"


def test_only_exceptions_is_not_open(env):
    """本期未开放的槽位**有且只有** `exceptions`（多一个都说明有人悄悄降级了能力）。"""
    from app.modules.entrust import workbench as wb

    closed = [s.key for s in wb.SLOT_SPECS if not s.open]
    assert closed == ["exceptions"]


# ─────────────────────────────────────────── 4. 槽位 ↔ 数据落点（DR-0010 §3.3）


def test_task_types_map_to_expected_slots(env):
    """任务类型按 §3.3 落到各自槽位；`plan_tasks` 是**全单任务总览**。"""
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    for task_type in (
        "collect_documents",
        "quote",
        "purchase",
        "contract",
        "execution",
        "handover",
        "settlement",
    ):
        _task(db, assignment_id=aid, task_type=task_type, title=f"T-{task_type}")

    body = _get(env, manager, aid).json()
    assert _slot(body, "plan_tasks")["counts"]["tasks"] == 7  # 总览
    assert _slot(body, "overview")["counts"]["tasks"] == 0  # 概况只读本体
    assert _slot(body, "procurement")["counts"]["tasks"] == 2  # quote + purchase
    assert _slot(body, "customer_contracts")["counts"]["tasks"] == 1  # contract
    assert _slot(body, "execution")["counts"]["tasks"] == 2  # execution + handover
    assert _slot(body, "settlement")["counts"]["tasks"] == 1


def test_artifact_types_map_to_expected_slots_and_keep_exact_version(env):
    """成果按注册表类型落槽，且给出**精确版本**（PRD 第 187 行的同一对值）。"""
    db = env.make_session()
    manager, _, _, eid, aid = _seed(env, db)
    assert _create_artifact(env, manager, eid, aid=aid).status_code == 200

    body = _get(env, manager, aid).json()
    slot = _slot(body, "procurement")
    assert slot["counts"]["artifacts"] == 1
    ref = slot["current"]["refs"][0]
    assert ref["artifact_type"] == "quote_parsed"
    assert ref["artifact_id"] > 0
    assert ref["revision_no"] == 1

    # 与单委托成果清单的取值一致（两端引用同一对，不各自再查一次"最新"）
    listing = env.client.get(_ARTIFACT_LIST_URL.format(aid=aid), headers=_headers(manager)).json()
    item = listing["items"][0]
    assert item["artifact_id"] == ref["artifact_id"]
    assert item["current_revision_no"] == ref["revision_no"]


def test_last_updated_uses_business_record_time(env):
    """「最后更新」取相关业务记录的最近时间，**不是**页面/委托的更新时间。"""
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    _task(
        db,
        assignment_id=aid,
        task_type="settlement",
        title="出结算",
        updated_at="2026-09-20 08:00:00",
    )

    body = _get(env, manager, aid).json()
    assert _slot(body, "settlement")["updated_at"] == "2026-09-20 08:00:00"
    # 没有相关记录的槽位保持 None —— 不能拿委托的更新时间顶替
    assert _slot(body, "customer_contracts")["updated_at"] is None


# ─────────────────────────────────────────── 5. 隔离与拒绝（DR-0012 / AC-10）


def test_same_owner_same_org_slots_do_not_leak(env):
    """同货主同组织的两张委托：成果与任务**互不串台**。"""
    db = env.make_session()
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=owner["user_id"])
    aid_a = _assignment(db, owner_id=owner["user_id"], org_id=org, title="单A")
    aid_b = _assignment(db, owner_id=owner["user_id"], org_id=org, title="单B")

    assert _create_artifact(env, manager, eid, aid=aid_a).status_code == 200
    _task(db, assignment_id=aid_a, task_type="contract", title="A 的合同")

    body_b = _get(env, manager, aid_b).json()
    assert _slot(body_b, "procurement")["counts"]["artifacts"] == 0
    assert _slot(body_b, "customer_contracts")["counts"]["tasks"] == 0
    assert _slot(body_b, "plan_tasks")["counts"]["tasks"] == 0

    body_a = _get(env, manager, aid_a).json()
    assert _slot(body_a, "procurement")["counts"]["artifacts"] == 1
    assert _slot(body_a, "customer_contracts")["counts"]["tasks"] == 1


def test_cross_org_manager_gets_404(env):
    """甲组织的经理读乙组织的委托工作台 → **404**（不区分"不存在"与"无权"）。"""
    db = env.make_session()
    manager, owner, _, _, aid = _seed(env, db)

    other_manager = _login(env.client, "mgr2")
    other_owner = _login(env.client, "shipper2")
    other_org = _org(db, "别的组织")
    _member(db, other_org, user_id=other_manager["user_id"])
    other_aid = _assignment(db, owner_id=other_owner["user_id"], org_id=other_org)

    stranger = _login(env.client, "nobody")
    for user in (other_manager, stranger):
        assert _get(env, user, aid).status_code == 404
    # 反向确认：不是把所有人都拒了
    assert _get(env, manager, aid).status_code == 200
    assert _get(env, owner, aid).status_code == 200
    # 他自己组织的委托单是 200（那是正常业务，不是越权）
    assert _get(env, other_manager, other_aid).status_code == 200


def test_missing_assignment_is_404(env):
    db = env.make_session()
    manager, _, _, _, _ = _seed(env, db)
    assert _get(env, manager, 999999).status_code == 404


# ─────────────────────────────────────────── 6. 存量成果与只读性


def test_unassigned_artifact_is_counted_but_not_in_slots(env):
    """归属为空的历史成果**不进任何槽位**，但必须如实报数。"""
    db = env.make_session()
    manager, _, _, eid, aid = _seed(env, db)
    _orphan_artifact(db, eid=eid)

    body = _get(env, manager, aid).json()
    assert body["unassigned_artifact_total"] == 1
    total = sum(slot["counts"]["artifacts"] for slot in body["slots"])
    assert total == 0


def test_workbench_is_read_only(env):
    """工作台是投影：调用前后业务记录**零变化**。"""
    db = env.make_session()
    manager, _, _, eid, aid = _seed(env, db)
    assert _create_artifact(env, manager, eid, aid=aid).status_code == 200
    _task(db, assignment_id=aid, task_type="quote", title="询价")

    def snapshot() -> tuple[int, int, int]:
        art = db.execute(text("SELECT COUNT(*) FROM ent_artifact")).scalar()
        rev = db.execute(text("SELECT COUNT(*) FROM ent_artifact_revision")).scalar()
        task = db.execute(text("SELECT COUNT(*) FROM ent_workflow_task")).scalar()
        return int(art), int(rev), int(task)

    before = snapshot()
    assert _get(env, manager, aid).status_code == 200
    assert snapshot() == before


# ─────────────────────────────────────────── 7. `exceptions` 槽的真实投影
#                                                （DR-0013 A1 切片三之三）


def _open_exceptions_slot(monkeypatch) -> None:
    """**临时**把 `exceptions` 槽打开，只用来验证投影本身。

    线上默认仍是关闭：DR-0013 §7.3 撤下条件的第 4 条（人工落点须在**真载荷走查**中
    走通）依赖 UI-08 与 UI-04，尚未满足。这不是绕过门禁，而是把两件事分开断言 ——
    「投影已就绪」靠本函数与下面的用例；「标记可以撤下」靠
    `test_exceptions_slot_marker_still_held`。
    """
    import dataclasses

    from app.modules.entrust import workbench as wb

    monkeypatch.setattr(
        wb,
        "SLOT_SPECS",
        tuple(
            dataclasses.replace(spec, open=True, not_open_reason="")
            if spec.key == "exceptions"
            else spec
            for spec in wb.SLOT_SPECS
        ),
    )


def _case(db, *, aid: int, actor: int, **overrides) -> dict:
    """经**服务层**登记一个案件（端点侧口径由 `test_entrust_exceptions_api.py` 覆盖）。"""
    from app.modules.entrust import exceptions as case_svc

    params: dict = {
        "assignment_id": aid,
        "actor_id": actor,
        "kind": case_svc.KIND_EXCEPTION,
        "title": "船期延误",
        "severity": "medium",
        "impact_kind": case_svc.IMPACT_INFORMATIONAL,
    }
    params.update(overrides)
    return case_svc.raise_case(db, **params)


def test_exceptions_slot_marker_still_held(env):
    """标记与常量都仍是「未开放」，且理由指向**尚未满足的那一条**。

    这是撤下动作的**闸门**：谁翻转 `EXCEPTIONS_SLOT_OPEN`，这条就会红。撤下必须是
    独立提交，并在**同一提交内**带上真载荷走查证据（DR-0013 §7.3）——
    「能力还没做」与「这单没有异常」是两句不同的话。
    """
    from app.modules.entrust import workbench as wb

    assert wb.EXCEPTIONS_SLOT_OPEN is False, "撤下标记要先补走查证据，不能顺手翻常量"

    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    _case(db, aid=aid, actor=int(manager["user_id"]), title="确有异常")

    slot = _slot(_get(env, manager, aid).json(), "exceptions")
    # 案件确实存在，槽位仍报「未开放」——这正是「未开放 ≠ 空」的可测形式
    assert slot["available"] is False
    assert "§7.3" in slot["unavailable_reason"]


def test_exceptions_projection_without_cases_is_no_record(env, monkeypatch):
    """无案件 → 四态里的「暂无记录」，四个字段各自给出对应的态，不是空数组糊过去。"""
    _open_exceptions_slot(monkeypatch)
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)

    slot = _slot(_get(env, manager, aid).json(), "exceptions")
    assert slot["available"] is True
    assert slot["unavailable_reason"] == ""
    assert slot["current"]["state"] == "no_record"
    assert slot["current"]["refs"] == [], "没有可点的引用就不该造空壳引用"
    assert slot["issues"]["state"] == "none"
    assert slot["next_owner"]["state"] == "not_applicable"
    assert slot["updated_at"] is None


def test_exceptions_projection_reports_open_cases_and_blocking(env, monkeypatch):
    """未关闭案件 → 摘要与数量；**只有阻断的**进未决问题；阻断的那张优先成为责任方。"""
    from app.modules.entrust import exceptions as case_svc

    _open_exceptions_slot(monkeypatch)
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    actor = int(manager["user_id"])
    task_id = _task(db, assignment_id=aid, task_type="execution", title="执行任务")

    _case(db, aid=aid, actor=actor, title="信息补充")
    _case(
        db,
        aid=aid,
        actor=actor,
        title="船期延误",
        severity="high",
        impact_kind=case_svc.IMPACT_EXECUTION_BLOCKING,
        links=[{"target_kind": case_svc.TARGET_TASK, "target_id": task_id}],
        owner_user_id=actor,
    )
    _case(
        db,
        aid=aid,
        actor=actor,
        kind=case_svc.KIND_CHANGE_REQUEST,
        title="改配载",
        severity="low",
    )

    slot = _slot(_get(env, manager, aid).json(), "exceptions")
    assert slot["current"]["state"] == "present"
    assert slot["current"]["text"] == "未关闭案件 3 项 · 阻断 1 项 · 变更请求 1 项"
    # 非阻断案件是**记录**，不是问题：不进未决问题列表
    assert [i["kind"] for i in slot["issues"]["items"]] == ["blocked"]
    assert "船期延误" in slot["issues"]["items"][0]["text"]
    # 「下一责任方」= 最该动的那张 —— 阻断优先，而不是按登记顺序（#1 没有责任人）
    assert slot["next_owner"] == {"state": "assigned", "user_id": actor, "text": f"成员 #{actor}"}


def test_exceptions_projection_without_owner_is_unassigned(env, monkeypatch):
    """有未关闭案件但没定责任人 → 「尚未分配」，不是「不适用」（后者没有下一步动作）。"""
    _open_exceptions_slot(monkeypatch)
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    _case(db, aid=aid, actor=int(manager["user_id"]))

    slot = _slot(_get(env, manager, aid).json(), "exceptions")
    assert slot["current"]["state"] == "present"
    assert slot["next_owner"]["state"] == "unassigned"


def test_exceptions_projection_closed_case_only_moves_updated_at(env, monkeypatch):
    """已关闭的案件不再是未决问题、也不再选责任方 —— 但它确实是一次业务更新。"""
    from app.modules.entrust import exceptions as case_svc

    _open_exceptions_slot(monkeypatch)
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    actor = int(manager["user_id"])
    case = _case(db, aid=aid, actor=actor, title="重复登记")
    closed = case_svc.close_case(
        db,
        exception_id=int(case["id"]),
        actor_id=actor,
        closure_disposition=case_svc.DISPOSITION_DUPLICATE,
        expected_revision=int(case["revision_no"]),
        evidence_ref="evidence://dup",
        decision_note="同一问题已另案登记",
    )

    slot = _slot(_get(env, manager, aid).json(), "exceptions")
    assert slot["current"]["state"] == "no_record"
    assert slot["current"]["refs"] == [], "已关闭的案件不该留在可点引用里"
    assert slot["issues"]["state"] == "none"
    assert slot["next_owner"]["state"] == "not_applicable"
    assert slot["updated_at"] == closed["updated_at"]


def test_exceptions_projection_is_isolated_per_assignment(env, monkeypatch):
    """A 单的案件不出现在 B 单的工作台（DR-0012 / 验证 11）。"""
    _open_exceptions_slot(monkeypatch)
    db = env.make_session()
    manager, owner, org, _, aid_a = _seed(env, db)
    aid_b = _assignment(db, owner_id=owner["user_id"], org_id=org)
    _case(db, aid=aid_a, actor=int(manager["user_id"]), title="甲单的异常")

    assert _slot(_get(env, manager, aid_a).json(), "exceptions")["current"]["state"] == "present"
    assert _slot(_get(env, manager, aid_b).json(), "exceptions")["current"]["state"] == "no_record"


def test_exceptions_slot_refs_are_case_refs_blocking_first(env, monkeypatch):
    """`current.refs` 逐条给出 `CaseRef`：**阻断优先**、形状与成果引用**不同**。

    形状独立是 DR-0014 §3.3 的硬要求，不是风格问题：若把案件伪造成
    `artifact_refs`（如 `{artifact_id: case_id}`），前端就会用一个渲染分支吃两种数据，
    而两边「有版本 / 无版本」的差异会在某次改动里静默错位 ——
    所以这里直接断言**键集合**，多一个 `artifact_id` 就会红。
    """
    from app.modules.entrust import exceptions as case_svc

    _open_exceptions_slot(monkeypatch)
    db = env.make_session()
    manager, _, _, _, aid = _seed(env, db)
    actor = int(manager["user_id"])
    task_id = _task(db, assignment_id=aid, task_type="execution", title="执行任务")

    plain = _case(db, aid=aid, actor=actor, title="信息补充")
    blocking = _case(
        db,
        aid=aid,
        actor=actor,
        title="船期延误",
        severity="high",
        impact_kind=case_svc.IMPACT_EXECUTION_BLOCKING,
        links=[{"target_kind": case_svc.TARGET_TASK, "target_id": task_id}],
    )

    refs = _slot(_get(env, manager, aid).json(), "exceptions")["current"]["refs"]
    # 阻断优先，其余按案件号 —— 与 `next_owner` 的排序口径一致
    assert [r["title"] for r in refs] == ["船期延误", "信息补充"]
    assert [r["case_id"] for r in refs] == [int(blocking["id"]), int(plain["id"])]
    assert [r["blocking"] for r in refs] == [True, False]
    assert [r["status"] for r in refs] == [case_svc.STATUS_OPEN, case_svc.STATUS_OPEN]
    assert set(refs[0]) == {"case_id", "kind", "title", "status", "blocking"}
