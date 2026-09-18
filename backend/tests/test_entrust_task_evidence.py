"""交接与缺证据（S7-2 / §10.1 第 10 步 / 合同 S4 段第 1、2、4、8 条）。

本文件验的是**四件在数据上能分开的事**，不是"端点存在"：

1. **缺件是派生出来的、不是存出来的**：`required_evidence` 减 `evidence_refs` 的类别
   ⇒ 补录之后同一个读数必须**变短**（§8 的"可执行补救"要能看见效果）。
2. ⭐ **业务发生时间与记录时间分开**（§2）：`occurred_at` 是"这事什么时候真的发生了"，
   `recorded_at` 是"我们什么时候知道的"。两者**不能互相顶替** ——
   不给 `occurred_at` 就缺着，绝不用"现在"填上。
3. ⭐ **记录时间与记录人不可伪造**：服务端写，调用方给 ⇒ 400。
4. **交接＝任务**（合同 S4 段 `Do not invent a "handover artifact"`）：
   交接没有独立成果实体，`handover` 汇总只是把 `task_type = handover` 的任务算一遍；
   ⛔ **没有交接任务时 `satisfied` 必须是 `None`** —— 报成 `True` 就是真空通过。

另外两条结构性判据：

* **完成不清空已登记的补录证据**（`_merge_evidence`）：证据是审计事实，只增不删；
* ⛔ **缺件清单不分页**：造 25 个带证据要求的任务，派生必须一次给全 25 条 ——
  被截断的缺件清单会**静默少报缺项**，那正是 O-9 记下的那类缺陷。

负例覆盖：未知 `kind` ⇒ 400；终态任务补录 ⇒ 409；`expected_revision` 过期 ⇒ 409；
只读成员**能读**缺件视图、**不能**补录（403）；局外人 ⇒ 404；`ENTRUST_ENABLED=false` ⇒ 404。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_TS = "2026-09-18 00:00:00"
MGR_PERMS = '["entrust:view","entrust:task:dispatch","entrust:assignment:claim","entrust:settlement:create"]'
VIEWER_PERMS = '["entrust:view"]'


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_charges.env` 同源）。"""
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
    """组织 + 经理 + 只读成员 + 货主授权 + 已受理委托 + 一个局外人。"""
    manager = _login(env.client, "mgr")
    viewer = _login(env.client, "viewer")
    owner = _login(env.client, "shipper")
    outsider = _login(env.client, "other")
    res = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
        {"n": f"org-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    org = int(res.lastrowid or 0)
    for uid, role in (
        (manager["user_id"], "manager"),
        (viewer["user_id"], "member"),
    ):
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
        {"o": owner["user_id"], "g": org, "t": "S7-2 交接与缺证据用例", "c": _TS},
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


def _view_only_assignment(env, seeded, *, perms: str = VIEWER_PERMS) -> tuple[dict, int]:
    """另开一个货主 ＋ 一张已受理委托，其授权**不含**任务派单权限。

    为什么必须换一个货主（而不是给同一货主再插一条只读授权）：权限按
    **(组织, 货主) 对**解析，同一对再插一条只读授权是**并集**，
    不会把已经拿到的权限收回去 —— 那样构造出来的"403"是假的。
    """
    from app.modules.entrust import assignments as assign_svc

    db = env.make_session()
    try:
        owner2 = _login(env.client, "shipper2")
        db.execute(
            text(
                "INSERT INTO ent_entrustment "
                "(org_id, entrust_user_id, permissions, status, created_at) "
                "VALUES (:o, :u, :p, 'active', :c)"
            ),
            {"o": seeded["org_id"], "u": owner2["user_id"], "p": perms, "c": _TS},
        )
        db.commit()
        draft = assign_svc.create_assignment(
            db, owner_user_id=owner2["user_id"], title="另一货主的委托"
        )
        submitted = assign_svc.submit_assignment(
            db,
            assignment_id=draft["assignment_id"],
            actor_id=owner2["user_id"],
            org_id=seeded["org_id"],
            expected_revision=draft["revision"],
        )
        claimed = assign_svc.claim_assignment(
            db, assignment_id=submitted["assignment_id"], actor_id=seeded["manager"]["user_id"]
        )
        return owner2, int(claimed["assignment_id"])
    finally:
        db.close()


def _raw_task(env, *, aid: int, required: list[str], created_by: int, title="卸货交接") -> int:
    """直接插一条任务（**夹具**，不经服务层）。

    用在"另一个货主"的场景上：那位的授权刻意不含派单权限，
    于是连建任务这一步都该被拒 —— 而本用例要测的是**证据端点**的授权，
    不是建任务的授权，所以夹具绕过服务层、不掩盖被测行为。
    """
    db = env.make_session()
    try:
        res = db.execute(
            text(
                "INSERT INTO ent_workflow_task "
                "(assignment_id, task_type, title, status, assignee_user_id, due_at, "
                " precondition_task_id, required_evidence, evidence_refs, wait_reason, "
                " reopen_count, last_reopen_reason, lease_generation, revision, created_by, "
                " started_at, completed_at, cancelled_at, created_at, updated_at) "
                "VALUES (:aid, 'handover', :title, 'in_progress', NULL, NULL, NULL, :req, "
                " NULL, NULL, 0, NULL, 0, 1, :actor, :ts, NULL, NULL, :ts, :ts)"
            ),
            {
                "aid": aid,
                "title": title,
                "req": '["photo"]',
                "actor": created_by,
                "ts": _TS,
            },
        )
        db.commit()
        return int(res.lastrowid or 0)
    finally:
        db.close()


def _task(
    env,
    seeded,
    *,
    aid: int | None = None,
    kind="handover",
    title="卸货交接",
    required=None,
    task_status=None,
) -> int:
    """直接经服务层建任务（比走 HTTP 快，且能精确控制 `required_evidence`）。"""
    from app.modules.entrust import tasks as svc

    db = env.make_session()
    try:
        task = svc.create_task(
            db,
            assignment_id=aid or seeded["aid"],
            actor_id=seeded["manager"]["user_id"],
            task_type=kind,
            title=title,
            required_evidence=required,
        )
        if task_status == "in_progress":
            svc.start_task(db, task_id=task["task_id"], actor_id=seeded["manager"]["user_id"])
        elif task_status == "waiting":
            svc.wait_task(
                db,
                task_id=task["task_id"],
                actor_id=seeded["manager"]["user_id"],
                reason="缺卸货单原件",
            )
        return int(task["task_id"])
    finally:
        db.close()


def _record(
    env,
    user,
    *,
    tid: int,
    kind="photo",
    ref="att-discharge",
    occurred="2026-09-17T08:30:00",
    **over,
):
    body = {"kind": kind, "ref": ref, "occurred_at": occurred, "source": "manual"}
    body.update(over)
    return env.client.post(
        f"/api/v1/entrust/tasks/{tid}/evidence",
        json=body,
        headers=_headers(user, f"ev-{uuid.uuid4().hex}"),
    )


def _gaps(env, user, *, aid: int):
    return env.client.get(
        f"/api/v1/entrust/assignments/{aid}/evidence-gaps", headers=_headers(user)
    )


def _gap_of(payload: dict, tid: int) -> dict:
    return next(item for item in payload["items"] if item["task_id"] == tid)


# ─────────────────────────────────────────── 1. 派生


def test_gap_is_derived_from_requirement_minus_registered(env):
    """缺什么＝要求的类别 − 已登记的类别；补录前 `missing` 必须是**全部**。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["document", "photo"])

    payload = _gaps(env, seeded["manager"], aid=seeded["aid"]).json()
    item = _gap_of(payload, tid)
    assert item["required"] == ["document", "photo"]
    assert item["registered"] == []
    assert item["missing"] == ["document", "photo"], "还没登记，缺的应当是全部"
    assert item["satisfied"] is False
    assert payload["missing_total"] == 2


def test_recording_evidence_returns_the_shrunk_gap(env):
    """补录的**唯一目的**就是让缺件清单变短 ⇒ 响应必须直接给出重算结果。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["document", "photo"])

    resp = _record(env, seeded["manager"], tid=tid, kind="document", ref="att-bl-001")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gap"]["missing"] == ["photo"], body["gap"]
    assert body["gap"]["registered"] == ["document"]
    assert body["task"]["evidence_refs"][0]["ref"] == "att-bl-001"

    again = _record(env, seeded["manager"], tid=tid, kind="photo", ref="att-photo-001")
    assert again.json()["gap"]["missing"] == [], "两条都登记后不该还缺"
    assert again.json()["gap"]["satisfied"] is True


# ─────────────────────────────────────────── 2. 业务发生时间 vs 记录时间


def test_occurred_at_is_kept_separate_from_recorded_at(env):
    """⭐ §2：业务发生时间（卸货那一刻）与记录时间（我们登记的时刻）**分开存**。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["photo"])

    resp = _record(env, seeded["manager"], tid=tid, occurred="2026-09-17T08:30:00", source="upload")
    assert resp.status_code == 200, resp.text
    entry = resp.json()["task"]["evidence_refs"][0]
    assert entry["occurred_at"] == "2026-09-17 08:30:00"
    assert entry["source"] == "upload"
    assert "recorded_at" in entry and "recorded_by" in entry
    assert entry["recorded_at"] != entry["occurred_at"], (
        "记录时间与业务发生时间被填成了同一个值 —— 那正是 §2 要防的"
    )
    assert entry["recorded_by"] == seeded["manager"]["user_id"]


def test_missing_occurred_at_is_not_defaulted_to_now(env):
    """不给业务发生时间就**缺着** —— 未知保持未知，绝不用"现在"顶上。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["photo"])

    # 服务层直调：`occurred_at` 缺省
    from app.modules.entrust import tasks as svc

    db = env.make_session()
    try:
        updated = svc.record_task_evidence(
            db, task_id=tid, actor_id=seeded["manager"]["user_id"], kind="photo", ref="att-x"
        )
    finally:
        db.close()
    entry = updated["task"]["evidence_refs"][0]
    assert "occurred_at" not in entry, "没登记业务发生时间，就不该凭空多出一个"
    assert entry["recorded_at"]


def test_recorded_fields_cannot_be_forged(env):
    """记录时间与记录人由服务端写 ⇒ 调用方自带要**报错**，不能静默忽略。

    注入点选 `complete_task`：HTTP 层经 Pydantic 只会带出声明过的字段，
    但服务层调用方（内部代码 / 未来的批量导入）传的是自由字典 ——
    守卫要落在**真正能被绕过的那一层**上才有意义。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["photo"], task_status="in_progress")

    from app.modules.entrust import tasks as svc

    db = env.make_session()
    try:
        with pytest.raises(svc.TaskValidationError) as excinfo:
            svc.complete_task(
                db,
                task_id=tid,
                actor_id=seeded["manager"]["user_id"],
                evidence_refs=[
                    {
                        "kind": "photo",
                        "ref": "att-forged",
                        # 自称的记录时间 —— 一旦被采纳，`recorded_at` 就不再是
                        # "我们什么时候知道的"，审计就断了
                        "recorded_at": "2020-01-01 00:00:00",
                    }
                ],
            )
    finally:
        db.close()
    assert "recorded_at" in str(excinfo.value)


# ─────────────────────────────────────────── 3. 只增不删 / 幂等


def test_completing_does_not_wipe_recorded_evidence(env):
    """⭐ 先补录两条，完成时只提交第三条 ⇒ **三条都在**（证据是审计事实）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(
        env, seeded, required=["document", "photo", "confirmation"], task_status="in_progress"
    )
    assert _record(env, seeded["manager"], tid=tid, kind="document", ref="att-1").status_code == 200
    assert _record(env, seeded["manager"], tid=tid, kind="photo", ref="att-2").status_code == 200

    resp = env.client.post(
        f"/api/v1/entrust/tasks/{tid}/complete",
        json={"evidence_refs": [{"kind": "confirmation", "ref": "att-3"}]},
        headers=_headers(seeded["manager"], f"cp-{uuid.uuid4().hex}"),
    )
    assert resp.status_code == 200, resp.text
    refs = {(e["kind"], e["ref"]) for e in resp.json()["evidence_refs"]}
    assert refs == {("document", "att-1"), ("photo", "att-2"), ("confirmation", "att-3")}, (
        "完成把先前补录的证据吃掉了 —— 替换语义在有补录入口之后是错的"
    )


def test_rerecording_same_evidence_is_idempotent(env):
    """同一份材料重复补录不新增，也**不刷新**它的记录时间。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["document"])

    first = _record(env, seeded["manager"], tid=tid, kind="document", ref="att-1")
    stamp = first.json()["task"]["evidence_refs"][0]["recorded_at"]
    second = _record(env, seeded["manager"], tid=tid, kind="document", ref="att-1")
    refs = second.json()["task"]["evidence_refs"]
    assert len(refs) == 1, f"同一条证据被登记了两次：{refs}"
    assert refs[0]["recorded_at"] == stamp, '重复提交不该刷新"我们什么时候知道的"'


def test_rerecording_can_fill_in_the_business_time(env):
    """`occurred_at` 是事实，允许在重复提交时补齐 —— 但记录时间仍归服务端。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["document"])

    from app.modules.entrust import tasks as svc

    db = env.make_session()
    try:
        svc.record_task_evidence(
            db, task_id=tid, actor_id=seeded["manager"]["user_id"], kind="document", ref="att-1"
        )
        stamp = svc.get_task(db, tid)["evidence_refs"][0]["recorded_at"]
    finally:
        db.close()

    resp = _record(
        env,
        seeded["manager"],
        tid=tid,
        kind="document",
        ref="att-1",
        occurred="2026-09-16T10:00:00",
    )
    entry = resp.json()["task"]["evidence_refs"][0]
    assert entry["occurred_at"] == "2026-09-16 10:00:00", "补齐业务发生时间应当生效"
    assert entry["recorded_at"] == stamp, "但记录时间不该被刷新"


# ─────────────────────────────────────────── 4. 交接汇总（不发明交接成果）


def test_handover_rollup_is_none_when_no_handover_task(env):
    """⛔ 没有交接任务时 `satisfied` 必须是 `None`，不是 `True`（真空通过防护）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _task(env, seeded, kind="collect_documents", title="收集单证", required=["document"])

    handover = _gaps(env, seeded["manager"], aid=seeded["aid"]).json()["handover"]
    assert handover["present"] is False
    assert handover["task_ids"] == []
    assert handover["satisfied"] is None, (
        '这张委托上没有交接任务，无从判断 —— 报 True 就是"没有对象也算通过"'
    )


def test_handover_rollup_tracks_the_handover_task(env):
    """交接＝任务：卸货证据挂在 handover 任务上，缺件在汇总里必须看得见。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, kind="handover", title="卸货交接", required=["photo", "receipt"])

    handover = _gaps(env, seeded["manager"], aid=seeded["aid"]).json()["handover"]
    assert handover["present"] is True
    assert handover["task_ids"] == [tid]
    assert handover["missing"] == ["photo", "receipt"]
    assert handover["satisfied"] is False

    _record(env, seeded["manager"], tid=tid, kind="photo", ref="att-p")
    _record(env, seeded["manager"], tid=tid, kind="receipt", ref="att-r")
    after = _gaps(env, seeded["manager"], aid=seeded["aid"]).json()["handover"]
    assert after["missing"] == []
    assert after["satisfied"] is True


# ─────────────────────────────────────────── 5. 等待条件是任务自己的字段


def test_waiting_on_evidence_reflects_state_and_derivation(env):
    """缺件条件是任务自己的 `waiting` ＋ `wait_reason` —— 本切片**不新建**等待实体。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["photo"], task_status="waiting")

    payload = _gaps(env, seeded["manager"], aid=seeded["aid"]).json()
    item = _gap_of(payload, tid)
    assert item["status"] == "waiting"
    assert item["wait_reason"] == "缺卸货单原件"
    assert item["waiting_on_evidence"] is True
    assert payload["tasks_waiting_on_evidence"] == 1

    # 补录**只登记证据**，不推进状态（人工接管优先：不自动把任务拉回 in_progress）
    resp = _record(env, seeded["manager"], tid=tid, kind="photo", ref="att-p")
    assert resp.status_code == 200, resp.text
    assert resp.json()["task"]["status"] == "waiting", "补录不该顺手改状态"
    assert resp.json()["gap"]["missing"] == []
    assert resp.json()["gap"]["waiting_on_evidence"] is False, (
        '证据已齐，就不该再说"在等证据"（等待的原因已经消失）'
    )
    assert resp.json()["task"]["wait_reason"] == "缺卸货单原件", (
        "原因文本属于人工写下的记录，不由本命令改写"
    )


def test_task_without_requirement_is_satisfied(env):
    """不强制证据的任务：`required` 为空 ⇒ 无缺项。这不是真空通过，是"没要求"。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=None)

    item = _gap_of(_gaps(env, seeded["manager"], aid=seeded["aid"]).json(), tid)
    assert item["required"] == []
    assert item["missing"] == []
    assert item["satisfied"] is True
    assert item["waiting_on_evidence"] is False


# ─────────────────────────────────────────── 6. 清单不被截断（O-9 同型）


def test_gap_list_is_not_truncated(env):
    """⭐ 造 25 个带证据要求的任务（> 列表默认页长 20）⇒ 派生必须一次给全。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    for i in range(25):
        _task(env, seeded, kind="collect_documents", title=f"单证 {i}", required=["document"])

    payload = _gaps(env, seeded["manager"], aid=seeded["aid"]).json()
    assert payload["tasks_total"] == 25
    assert len(payload["items"]) == 25, (
        "缺件清单被分页截断了 —— 被截的清单会静默少报缺项（O-9 同型缺陷）"
    )
    assert payload["missing_total"] == 25


# ─────────────────────────────────────────── 7. 负例


def test_unknown_evidence_kind_is_rejected(env):
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["photo"])
    resp = _record(env, seeded["manager"], tid=tid, kind="video", ref="att-v")
    assert resp.status_code == 400, resp.text


def test_terminal_task_rejects_recording(env):
    """已完成的任务要补证据，先 reopen —— 否则"完成时证据齐备"会被事后改写。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["photo"], task_status="in_progress")
    done = env.client.post(
        f"/api/v1/entrust/tasks/{tid}/complete",
        json={"evidence_refs": [{"kind": "photo", "ref": "att-p"}]},
        headers=_headers(seeded["manager"], f"cp-{uuid.uuid4().hex}"),
    )
    assert done.status_code == 200, done.text
    resp = _record(env, seeded["manager"], tid=tid, kind="receipt", ref="att-r")
    assert resp.status_code == 409, resp.text


def test_stale_revision_is_rejected(env):
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    tid = _task(env, seeded, required=["photo"])
    resp = _record(
        env, seeded["manager"], tid=tid, kind="photo", ref="att-p", expected_revision=999
    )
    assert resp.status_code == 409, resp.text


def test_viewer_can_read_gaps_but_not_record(env):
    """只读成员**看得见**还缺什么（参与方本来就该看见），但**补不了**（403）。

    ⚠️ 这条负例必须落在**另一个货主**的委托上：权限按 (组织, 货主) 对解析，
    同一货主再插一条只读授权是并集、收不回权限 —— 那种写法构造出的 403 是假的。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _task(env, seeded, required=["photo"])  # 本组织自己的委托，只为让 org 有数据
    _owner2, aid2 = _view_only_assignment(env, seeded)
    tid2 = _raw_task(env, aid=aid2, required=["photo"], created_by=seeded["manager"]["user_id"])

    read = _gaps(env, seeded["viewer"], aid=aid2)
    assert read.status_code == 200, read.text
    assert _gap_of(read.json(), tid2)["missing"] == ["photo"]

    denied = _record(env, seeded["viewer"], tid=tid2)
    assert denied.status_code == 403, denied.text


def test_outsider_gets_404_on_gaps(env):
    """非参与方 404 —— 不泄漏存在性（读不加严不等于"任何登录用户"）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _task(env, seeded, required=["photo"])
    resp = _gaps(env, seeded["outsider"], aid=seeded["aid"])
    assert resp.status_code == 404, resp.text


def test_gaps_are_404_when_entrust_disabled(env, monkeypatch):
    from app.core.config import get_settings

    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    assert _gaps(env, seeded["manager"], aid=seeded["aid"]).status_code == 404
    assert _record(env, seeded["manager"], tid=1).status_code == 404
