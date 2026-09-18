"""委托结案命令（S4-b / 合同 §6.4）—— 五维度前置、逐条报缺、幂等、并发、不可绕过。

本文件验的是**合同 §6.4 表格那五件事各自的判断力**，不是"端点存在"：

1. **维度可分辨**：每个维度都有自己的 `code`，且逐条给 detail（⛔ 不是一句
   「不可结案」）。只回一句会让调用方无法自助（DR-0013 §3.3 作用面 1）。
2. ⭐ **「没有 blocker」≠「已结清」**：`derive_financial_status` 在毫无财务事实时
   **提前返回** `not_started` 且 `blockers=[]`（那是刻意的）——若结案只按 blocker
   归类，一张什么都没做的委托就能过第 4/5 条前置。`test_no_finance_facts_is_not_settled`
   钉住这一格。
3. **三个"不算缺"的边界**（都容易写反）：取消掉的任务不再算它缺证据；没有交接任务
   不算缺（无从判断 ⇏ 不满足）；案件维度一旦报了，财务派生的 `open_cases` 不重复报。
4. **不可绕过**：没有 `force` / `skip_checks` 之类的入参，给了也不生效。
5. **并发与幂等**：同键重放不产生第二条结案事件；异键再结 ⇒ 409；
   `expected_revision` 过期 ⇒ 409。⚠️ SQLite 上跑的是**降级路径**
   （没有 `FOR UPDATE`、单写者），MySQL 上的真并发证据在
   `test_mysql_integration.py` 的 `mysql` 标记用例里（合同：*An exception query
   existing in the code is not this proof*）。
6. **结案后是冻结的**：案件登记与任务变更都会被拒（它们的既有前置就要求 `claimed`）——
   这一条保证"结案时的五维度"不会在结案之后被静默破坏。
"""

from __future__ import annotations

import inspect
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_TS = "2026-09-18 00:00:00"
#: 经理权限里必须含 `entrust:assignment:complete` —— 结案**另开一个权限码**，
#: 不挂到 `claim` 上：认领是接单，结案是对客户宣告"这件事做完了"，两件事不同。
MGR_PERMS = (
    '["entrust:view","entrust:task:dispatch","entrust:settlement:create",'
    '"entrust:quote:create","entrust:assignment:complete"]'
)
VIEWER_PERMS = '["entrust:view"]'


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_settlement.env` 同源）。"""
    from fastapi.testclient import TestClient

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
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield type("Env", (), {"client": tc, "make_session": factory})()
    app.dependency_overrides.clear()
    engine.dispose()


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _seed(env, db):
    """组织 + 经理 + 只读成员 + 货主授权 + 已受理委托 + 局外人。"""
    manager = _login(env.client, "mgr")
    viewer = _login(env.client, "viewer")
    owner = _login(env.client, "shipper")
    outsider = _login(env.client, "other")
    res = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
        {"n": f"org-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    org = int(res.lastrowid or 0)
    for uid, role in ((manager["user_id"], "manager"), (viewer["user_id"], "member")):
        db.execute(
            text(
                "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
                "VALUES (:o, :u, :r, 'active', :c)"
            ),
            {"o": org, "u": uid, "r": role, "c": _TS},
        )
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": owner["user_id"], "p": MGR_PERMS, "c": _TS},
    )
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, '钢材', '800.000', '吨', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "g": org, "t": "S4-b 结案用例", "c": _TS},
    )
    aid = int(res.lastrowid or 0)
    db.commit()
    return {
        "manager": manager,
        "viewer": viewer,
        "owner": owner,
        "outsider": outsider,
        "org_id": org,
        "aid": aid,
    }


# ── 造数助手（尽量走服务层；夹具本身不是被测对象）────────────────────────────


def _task(env, seeded, *, required=None, title="卸货交接", finish=True) -> dict:
    """建一个任务；`finish=True` 时顺手**开始并完成**（要求的证据一并登记）。

    ⚠️ 必须 `start` 再 `complete`：任务是 `pending → in_progress → done` 的状态机，
    直接完成会 409（`TaskStateError`）—— 夹具也要走产品的合法迁移，否则测的是
    "状态机不生效"而不是结案。
    """
    from app.modules.entrust import tasks as task_svc

    db = env.make_session()
    try:
        task = task_svc.create_task(
            db,
            assignment_id=seeded["aid"],
            actor_id=seeded["manager"]["user_id"],
            task_type=task_svc.TASK_TYPE_HANDOVER,
            title=title,
            required_evidence=required,
        )
        if finish:
            task = task_svc.start_task(
                db, task_id=int(task["task_id"]), actor_id=seeded["manager"]["user_id"]
            )
            task = task_svc.complete_task(
                db,
                task_id=int(task["task_id"]),
                actor_id=seeded["manager"]["user_id"],
                evidence_refs=[{"kind": k, "ref": f"att-{k}"} for k in (required or [])],
            )
        return task
    finally:
        db.close()


def _charge(env, seeded, *, direction="receivable", amount="1000", confirm=True):
    from app.modules.entrust import charges as charge_svc

    db = env.make_session()
    try:
        row = charge_svc.record_charge(
            db,
            assignment_id=seeded["aid"],
            direction=direction,
            charge_kind="freight",
            amount=amount,
            currency="CNY",
            basis="合同附件三·费率表",
            actor_id=seeded["manager"]["user_id"],
        )
        if confirm:
            row = charge_svc.confirm_charge(db, charge_id=row["charge_id"])
        return row
    finally:
        db.close()


def _raise_case(
    env, seeded, *, severity="high", impact_kind="informational", with_link=False
) -> int:
    """经**服务层**登记案件（C1/C2 一并生效，夹具与产品同一套校验）。"""
    from app.modules.entrust import exceptions as case_svc

    db = env.make_session()
    try:
        links = None
        if with_link:
            task = _task(env, seeded, finish=False)
            links = [{"target_kind": "task", "target_id": int(task["task_id"])}]
        case = case_svc.raise_case(
            db,
            assignment_id=seeded["aid"],
            actor_id=seeded["manager"]["user_id"],
            kind="exception",
            title="卸货短量待核",
            severity=severity,
            impact_kind=impact_kind,
            links=links,
        )
        # 服务层返回的是**行投影**（键＝列名 `id`）；API 层才把它改名成 `case_id`。
        return int(case.get("case_id") or case["id"])
    finally:
        db.close()


def _close_case(env, seeded, case_id: int) -> None:
    """按**合法处置**关闭案件。

    ⚠️ 处置与状态**成对**：`open` 状态只允许 `cancelled / duplicate / superseded`
    （`accepted_residual` 要求先有决定 ⇒ 得先进 `in_review`）。夹具走最短的合法路径
    —— 状态机不接受"随便挑一个 disposition"，夹具也不该绕过它。
    """
    from app.modules.entrust import exceptions as case_svc

    db = env.make_session()
    try:
        case_svc.close_case(
            db,
            exception_id=case_id,
            actor_id=seeded["manager"]["user_id"],
            closure_disposition="superseded",
            expected_revision=1,
            evidence_ref="现场记录-001",
            decision_note="本单被后续记录取代",
            resolution_note="已记录并留观",
        )
    finally:
        db.close()


def _open_revalidation(env, seeded) -> None:
    """插一条**未完成**的复核项（挂在一条已关闭的案件上，指向一个真实复核任务）。

    复核项是 `ent_revalidation` 的行（`status='open'`），它**不属于**案件状态机
    —— 这正是"案件关了 ≠ 复核做完了"的来源。
    """
    task = _task(env, seeded, finish=False)
    case_id = _raise_case(env, seeded, severity="low")
    _close_case(env, seeded, case_id)
    db = env.make_session()
    try:
        db.execute(
            text(
                "INSERT INTO ent_revalidation "
                "(exception_id, review_key, target_kind, target_id, target_revision_id, area, "
                " task_type, review_task_id, status, created_at) "
                "VALUES (:e, 'artifact:1', NULL, NULL, NULL, '报价复核', 'quote', :t, 'open', :c)"
            ),
            {"e": case_id, "t": int(task["task_id"]), "c": _TS},
        )
        db.commit()
    finally:
        db.close()


def _open_case_raw(
    env, seeded, *, status="open", impact_kind="informational", severity="low"
) -> int:
    """直接插一条案件（不带 link）—— 用于构造"非阻断的残留"这类夹具。"""
    db = env.make_session()
    try:
        res = db.execute(
            text(
                "INSERT INTO ent_exception "
                "(org_id, assignment_id, kind, title, severity, impact_kind, status, "
                " raised_by_user_id, source, raised_at, created_at, updated_at) "
                "VALUES (:o, :a, 'exception', '卸货短量待核', :sev, :imp, :st, "
                " :actor, 'manual', :c, :c, :c)"
            ),
            {
                "o": seeded["org_id"],
                "a": seeded["aid"],
                "sev": severity,
                "imp": impact_kind,
                "st": status,
                "actor": seeded["manager"]["user_id"],
                "c": _TS,
            },
        )
        db.commit()
        return int(res.lastrowid or 0)
    finally:
        db.close()


def _post(env, user, path: str, body: dict | None = None, key: str | None = None):
    return env.client.post(
        path, json=body or {}, headers=_headers(user, key or f"k-{uuid.uuid4().hex}")
    )


def _complete(env, user, aid: int, revision: int, *, key=None, extra: dict | None = None):
    body = {"expected_revision": revision}
    if extra:
        body.update(extra)
    return _post(env, user, f"/api/v1/entrust/assignments/{aid}/complete", body, key=key)


def _rev(env, user, aid: int) -> int:
    resp = env.client.get(f"/api/v1/entrust/assignments/{aid}", headers=_headers(user))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["revision"])


def _missing(resp) -> list[dict]:
    """409 的 `detail` 必须是**对象**（不是字符串），且带 `missing[]`。"""
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), f"detail 不是对象而是 {type(detail).__name__}：{detail!r}"
    return detail["missing"]


def _codes(resp) -> list[str]:
    return [str(item["code"]) for item in _missing(resp)]


def _version(env, seeded, *, approve=True, confirm=True, pay=True) -> dict:
    """一条**已确认的费用 → 结算版本（已内部确认／已客户确认）→ 收付覆盖全额**的链路。"""
    _charge(env, seeded)
    created = _post(
        env, seeded["manager"], f"/api/v1/entrust/assignments/{seeded['aid']}/settlements"
    )
    assert created.status_code == 200, created.text
    sid = int(created.json()["settlement_id"])
    if approve:
        assert (
            _post(env, seeded["manager"], f"/api/v1/entrust/settlements/{sid}/approve").status_code
            == 200
        )
    if confirm:
        assert (
            _post(
                env,
                seeded["owner"],
                f"/api/v1/entrust/settlements/{sid}/customer-confirm",
                {"decision": "accepted"},
            ).status_code
            == 200
        )
    if pay:
        paid = _post(
            env,
            seeded["manager"],
            f"/api/v1/entrust/settlements/{sid}/payments",
            {"direction": "receivable", "amount": "1000", "ref": "水单-SAMPLE-001"},
        )
        assert paid.status_code == 200, paid.text
    return {"settlement_id": sid}


# ───────────────────────────────── 1. 每个维度各自报缺


def test_no_finance_facts_is_not_settled(env):
    """⭐ 毫无财务事实**不等于**已结清 —— `not_started` 必须被拦下。

    这是"只按 blocker 归类"会漏掉的那一格：`derive_financial_status` 在
    "还没有任何费用/结算/收付"时**提前返回**且 `blockers=[]`（刻意的 —— 那时
    "还没有结算版本"是同义反复）。若结案直接按 blocker 归类，这张**什么都没做**的
    委托就会"第 4/5 条前置全过"。§5.3.2 的正向判据是 `settled`，不是"没有 blocker"。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()

    resp = _complete(env, seeded["manager"], seeded["aid"], 1)
    assert resp.status_code == 409, resp.text
    assert "financial_not_started" in _codes(resp)
    # 逐条报缺 ⇒ 其它维度也必须各自说话（任务一个都没有 ⇒ 任务维度不报缺）
    assert "tasks_not_disposed" not in _codes(resp), "没有任务时不该报「任务未处置」"

    after = env.client.get(
        f"/api/v1/entrust/assignments/{seeded['aid']}", headers=_headers(seeded["manager"])
    ).json()
    assert after["status"] == "claimed", "被拒的结案不得改动状态"
    assert after["completed_at"] is None


def test_tasks_and_evidence_have_their_own_entries(env):
    """任务处置与交付证据是**两条**缺项（不同维度、不同处置动作）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _task(env, seeded, required=["photo"], finish=False)

    resp = _complete(env, seeded["manager"], seeded["aid"], 1)
    codes = _codes(resp)
    assert "tasks_not_disposed" in codes
    assert "required_evidence_missing" in codes
    ev = next(item for item in _missing(resp) if item["code"] == "required_evidence_missing")
    assert ev["dimension"] == "evidence"
    assert ev["detail"]["tasks"][0]["missing"] == ["photo"], "缺件必须指名到「缺哪一类」"


def test_evidence_gap_alone_does_not_block_when_task_is_done(env):
    """任务完成且证据齐备 ⇒ 这两个维度都不报缺（正例，避免"永远报缺"的假绿）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _task(env, seeded, required=["photo"], finish=True)

    resp = _complete(env, seeded["manager"], seeded["aid"], 1)
    codes = _codes(resp)
    assert "tasks_not_disposed" not in codes
    assert "required_evidence_missing" not in codes


def test_cancelled_task_evidence_is_exempt(env):
    """⭐ 取消掉的任务**不再算它缺证据**（否则"拿不到证据就取消"反而更结不了案）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    task = _task(env, seeded, required=["photo"], finish=False)

    from app.modules.entrust import tasks as task_svc

    db = env.make_session()
    try:
        task_svc.cancel_task(
            db, task_id=int(task["task_id"]), actor_id=seeded["manager"]["user_id"]
        )
    finally:
        db.close()

    codes = _codes(_complete(env, seeded["manager"], seeded["aid"], 1))
    assert "tasks_not_disposed" not in codes
    assert "required_evidence_missing" not in codes


def test_open_cases_and_blocking_cases_are_reported_separately(env):
    """阻断案件与"未处置的残留"各报一条，且都**指名到具体案件**。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    blocking = _raise_case(
        env, seeded, severity="critical", impact_kind="execution-blocking", with_link=True
    )
    residual = _open_case_raw(env, seeded, impact_kind="informational", severity="low")

    resp = _complete(env, seeded["manager"], seeded["aid"], 1)
    codes = _codes(resp)
    assert "blocking_cases_open" in codes
    assert "cases_not_closed" in codes
    # 同一条事实只报一次：财务派生的 `open_cases` 不再重复出现
    assert "open_cases" not in codes, "同一批案件被两个维度各报了一次"

    ids = {
        int(c["case_id"]) for item in _missing(resp) for c in (item["detail"].get("cases") or [])
    }
    assert {blocking, residual} <= ids, f"缺项没有指名到案件 id：{ids}"


def test_blocking_does_not_depend_on_severity_being_critical(env):
    """阻断的判据是 `impact_kind`，**不是** `severity`（`is_blocking` 的签名里没有它）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _raise_case(env, seeded, severity="high", impact_kind="execution-blocking", with_link=True)

    assert "blocking_cases_open" in _codes(_complete(env, seeded["manager"], seeded["aid"], 1))


def test_open_revalidation_blocks_even_when_its_case_is_closed(env):
    """⭐ 案件关了**不等于**复核做完了 —— 两种记录，两条判据。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _open_revalidation(env, seeded)

    resp = _complete(env, seeded["manager"], seeded["aid"], 1)
    codes = _codes(resp)
    assert "revalidation_open" in codes
    assert "cases_not_closed" not in codes, "该案件已关闭，不该再报残留"


def test_settlement_needs_customer_confirmation_of_the_exact_version(env):
    """结算维度：内部确认过了、**客户没确认** ⇒ 缺；且说明里点明原报价接受不能替代。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _version(env, seeded, approve=True, confirm=False, pay=False)

    resp = _complete(env, seeded["manager"], seeded["aid"], 1)
    codes = _codes(resp)
    assert "customer_not_confirmed" in codes
    assert "settlement_not_approved" not in codes, "内部确认已过，不该报未批准"
    msg = next(m["message"] for m in _missing(resp) if m["code"] == "customer_not_confirmed")
    assert "不能替代" in msg, "必须说清「原报价接受替不了客户确认」"


def test_unsettled_balance_blocks_and_names_the_direction(env):
    """余额维度：客户确认过了、**余额未结清** ⇒ 缺，并指出方向与差额。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _version(env, seeded, approve=True, confirm=True, pay=False)

    resp = _complete(env, seeded["manager"], seeded["aid"], 1)
    codes = _codes(resp)
    assert "balance_unsettled" in codes
    assert "customer_not_confirmed" not in codes
    msg = next(m["message"] for m in _missing(resp) if m["code"] == "balance_unsettled")
    assert "receivable" in msg and "1000" in msg, f"未结余额必须说清方向与差额：{msg!r}"


# ───────────────────────────────── 2. 成功路径与"结案后冻结"


def _fully_ready(env, seeded) -> int:
    """五维度全部满足，返回当前 revision。"""
    _task(env, seeded, required=["photo"], finish=True)
    _version(env, seeded, approve=True, confirm=True, pay=True)
    return _rev(env, seeded["manager"], seeded["aid"])


def test_complete_succeeds_when_all_five_dimensions_hold(env):
    """五维度全过 ⇒ 200 且**同时**满足：`status=completed`、`completed_at` 非空、revision +1。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    rev = _fully_ready(env, seeded)

    state = env.client.get(
        f"/api/v1/entrust/assignments/{seeded['aid']}/financial-status",
        headers=_headers(seeded["manager"]),
    ).json()
    assert state["financial_status"] == "settled", f"前提：财务已结清，实际 {state}"

    resp = _complete(env, seeded["manager"], seeded["aid"], rev)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["completed_at"], "完成时间必须落下来（它是「运营完成」的可核对痕迹）"
    assert int(body["revision"]) == rev + 1


def test_completed_assignment_is_frozen(env):
    """结案之后不能再往上登记案件、也不能再动任务（既有前置就要求 `claimed`）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    rev = _fully_ready(env, seeded)
    assert _complete(env, seeded["manager"], seeded["aid"], rev).status_code == 200

    from app.modules.entrust import exceptions as case_svc
    from app.modules.entrust import tasks as task_svc

    db = env.make_session()
    try:
        # ⚠️ 案件层的四个异常类**没有共同基类**（`ExceptionCaseError` /
        #    `ExceptionCaseConflictError` / `...NotFound` / `...Scope`），
        #    所以这里两个都要列 —— 只捕获一个会让另一个穿出去变成"用例报错"。
        with pytest.raises(
            (case_svc.ExceptionCaseError, case_svc.ExceptionCaseConflictError)
        ) as excinfo:
            case_svc.raise_case(
                db,
                assignment_id=seeded["aid"],
                actor_id=seeded["manager"]["user_id"],
                kind="exception",
                title="结案后还想登记",
                severity="low",
                impact_kind="informational",
            )
    finally:
        db.close()
    assert "completed" in str(excinfo.value)

    db = env.make_session()
    try:
        with pytest.raises(task_svc.TaskStateError):
            task_svc.create_task(
                db,
                assignment_id=seeded["aid"],
                actor_id=seeded["manager"]["user_id"],
                task_type=task_svc.TASK_TYPE_HANDOVER,
                title="结案后还想加任务",
            )
    finally:
        db.close()


# ───────────────────────────────── 3. 幂等 / 并发 / 不可绕过


def test_same_key_replays_without_a_second_event(env):
    """同键重放：第二次拿到的还是第一次那份响应，且**不产生第二条结案事件**。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    rev = _fully_ready(env, seeded)
    key = f"k-{uuid.uuid4().hex}"

    first = _complete(env, seeded["manager"], seeded["aid"], rev, key=key)
    assert first.status_code == 200, first.text
    again = _complete(env, seeded["manager"], seeded["aid"], rev, key=key)
    assert again.status_code == 200, again.text
    assert again.json()["completed_at"] == first.json()["completed_at"]
    assert again.json()["revision"] == first.json()["revision"], "重放不该再涨 revision"


def test_completing_twice_with_a_new_key_is_409(env):
    """异键再结 ⇒ 409（明确告知已结案）—— ⛔ 不静默成功，否则「重开后重结」与
    「早就结过」会得到同一个响应。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    rev = _fully_ready(env, seeded)
    assert _complete(env, seeded["manager"], seeded["aid"], rev).status_code == 200

    second = _complete(env, seeded["manager"], seeded["aid"], rev)
    assert second.status_code == 409, second.text
    assert "已经结案" in str(second.json()["detail"])


def test_stale_expected_revision_is_409_and_changes_nothing(env):
    """`expected_revision` 过期 ⇒ 409，且状态/完成时间都不动。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _fully_ready(env, seeded)

    resp = _complete(env, seeded["manager"], seeded["aid"], 999)
    assert resp.status_code == 409, resp.text
    sysmsg = str(resp.json()["detail"])
    assert "revision" in sysmsg

    after = env.client.get(
        f"/api/v1/entrust/assignments/{seeded['aid']}", headers=_headers(seeded["manager"])
    ).json()
    assert after["status"] == "claimed"
    assert after["completed_at"] is None


def test_no_bypass_parameter_is_honored(env):
    """⛔ 没有「跳过前置」的后门：传 `force` / `skip_checks` 也**不生效**。

    PRD：`Required hard checks cannot be bypassed by chat, generic "complete",
    or administrator UI`。这里连"看起来留了口子"的入参一起试掉。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()

    resp = _complete(
        env,
        seeded["manager"],
        seeded["aid"],
        1,
        extra={"force": True, "skip_checks": True, "acknowledge_missing": True},
    )
    assert resp.status_code == 409, resp.text
    assert "financial_not_started" in _codes(resp)
    after = env.client.get(
        f"/api/v1/entrust/assignments/{seeded['aid']}", headers=_headers(seeded["manager"])
    ).json()
    assert after["status"] == "claimed"


def test_rejected_complete_releases_the_idempotency_key(env):
    """被拒之后**同一个键**必须能重试（失败释放）—— 与全支线的写入口径一致。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    key = f"k-{uuid.uuid4().hex}"

    rejected = _complete(env, seeded["manager"], seeded["aid"], 1, key=key)
    assert rejected.status_code == 409, rejected.text

    rev = _fully_ready(env, seeded)
    retry = _complete(env, seeded["manager"], seeded["aid"], rev, key=key)
    assert retry.status_code == 200, f"同键重试被幂等记录挡住：{retry.text}"


def test_only_the_org_manager_can_complete(env):
    """权限三档：**该货主的授权里没有 complete** ⇒ 403／局外人 404／**货主本人 404**
    （结案是运营方对客户的宣告，货主不能自己宣布做完）。

    ⚠️ 构造 403 必须**换一个货主**：权限按 **(组织, 货主) 对**解析，
    同一对再插一条只读授权是**并集**，收不回已经拿到的权限 —— 那样构造出来的
    "403"是假的（S7-3 那一轮踩过这个坑，用例里的注释也留着）。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
        other = _login(env.client, "shipper2")
        db.execute(
            text(
                "INSERT INTO ent_entrustment "
                "(org_id, entrust_user_id, permissions, status, created_at) "
                "VALUES (:o, :u, :p, 'active', :c)"
            ),
            {"o": seeded["org_id"], "u": other["user_id"], "p": VIEWER_PERMS, "c": _TS},
        )
        res = db.execute(
            text(
                "INSERT INTO ent_assignment "
                "(owner_user_id, org_id, title, status, revision, created_at, updated_at) "
                "VALUES (:o, :g, 'S4-b 只读授权', 'claimed', 1, :c, :c)"
            ),
            {"o": other["user_id"], "g": seeded["org_id"], "c": _TS},
        )
        aid2 = int(res.lastrowid or 0)
        db.commit()
    finally:
        db.close()

    assert _complete(env, seeded["viewer"], aid2, 1).status_code == 403
    assert _complete(env, seeded["outsider"], seeded["aid"], 1).status_code == 404
    assert _complete(env, seeded["owner"], seeded["aid"], 1).status_code == 404


# ───────────────────────────────── 4. 读写两处口径一致 + 映射全量


def test_readiness_and_command_agree(env):
    """⭐ 读（`closure_readiness`）与写（`complete`）报出的缺项**必须逐条相同**。

    两处各有一套前置判断时，迟早出现"页面说可以、命令说不行"。这里钉的是
    **缺项集合相等**（不只是"都为真/都为假"）—— 否则容易出现"都能过/都不过，
    但原因说得不一样"，那同样是调用方无法自助。
    ⚠️ 另有一条**不**等价的：`ready=True` 只说五条前置成立；`complete` 还要求
    委托处于 `claimed`（已结案会 409）。状态前置不塞进 `ready`（那会让一个字段
    同时回答两个问题）。
    """
    from app.modules.entrust import closure as cl

    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    rev = _fully_ready(env, seeded)

    def readiness() -> dict:
        session = env.make_session()
        try:
            return cl.closure_readiness(
                session, assignment_id=seeded["aid"], user_id=seeded["manager"]["user_id"]
            )
        finally:
            session.close()

    # 五维度全过：读说齐了，写也放行
    assert readiness()["missing"] == []
    assert _complete(env, seeded["manager"], seeded["aid"], rev).status_code == 200

    # 结案之后：五条前置仍然成立（事实没变）⇒ `ready` 仍为 True，
    # 而命令因为**状态**拒绝 —— 两者说的是两件事，读数上必须分得开
    assert readiness()["ready"] is True
    second = _complete(env, seeded["manager"], seeded["aid"], rev)
    assert second.status_code == 409
    assert "已经结案" in str(second.json()["detail"])


def test_readiness_and_command_report_the_same_missing_codes(env):
    """缺项集合相等（含多维缺失的情形）—— 与上一条互补：那条测"齐"、这条测"缺"。"""
    from app.modules.entrust import closure as cl

    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _task(env, seeded, required=["photo"], finish=False)
    _open_case_raw(env, seeded, impact_kind="informational", severity="low")

    session = env.make_session()
    try:
        read_codes = sorted(
            item["code"]
            for item in cl.closure_readiness(
                session, assignment_id=seeded["aid"], user_id=seeded["manager"]["user_id"]
            )["missing"]
        )
    finally:
        session.close()
    write_codes = sorted(_codes(_complete(env, seeded["manager"], seeded["aid"], 1)))
    assert read_codes == write_codes, f"读写两处报的缺项不一致：{read_codes} vs {write_codes}"
    assert read_codes, "前提：这一组夹具应当确实有缺项"


def test_finance_blocker_mapping_is_exhaustive():
    """⭐ 财务派生产出的**每一个** blocker code 都必须被归类到某个维度。

    漏一个的后果不是报错而是**静默**：那条 blocker 永远不会阻止结案
    （"派生说未结清、结案却放行"）。所以这里从 `derive_financial_status` 的
    **源码字面量**里把 code 全抓出来逐个核对 —— 派生新增 code 而映射没跟 ⇒ 红。
    """
    from app.modules.entrust import closure as cl
    from app.modules.entrust import settlement as st

    src = inspect.getsource(st.derive_financial_status)
    produced = set(re.findall(r'"code":\s*"([a-z_]+)"', src))
    assert produced, "没抓到任何 code ⇒ 本用例的前提失效（派生改了写法）"
    assert produced == set(cl.FINANCE_BLOCKER_DIMENSIONS), (
        "财务 blocker 的归类不是全量的："
        f"派生产出 {sorted(produced)}，映射覆盖 {sorted(cl.FINANCE_BLOCKER_DIMENSIONS)}"
    )
    # 每个 code 都要落在**五个维度之一**里（写错一个维度名不会被上面的集合比较发现）
    assert set(cl.FINANCE_BLOCKER_DIMENSIONS.values()) <= set(cl.DIMENSIONS)
