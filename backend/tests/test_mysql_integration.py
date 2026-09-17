"""MySQL 并发集成测试（ENT-007，DR-0002 后续增量）。

定位与范围
----------
SQLite 结果**不作为 MySQL 锁正确性的证据**（计划 §7.3）。本模块用真实
MySQL 8.0 验证三条并发正确性锚点：

1. **双认领竞争**（AC-03）：`claim_assignment` 的单条条件 UPDATE 在真并发下
   仍然只有一个赢家 —— 多轮重复提高竞争窗口的命中概率；
2. **乐观锁并发编辑**（AC-11）：两个会话基于同一 revision 并发编辑，恰好一个
   成功、另一个 409（`UPDATE ... WHERE revision = :expected` 的裁决点）；
3. **幂等并发**（ENT-002 / AC-15 前置）：同一幂等键并发执行同一业务，
   副作用恰好发生一次；
4. **基线建表**（AC-25）：`create_all` 能在真实 MySQL 上建出全部基线表
   （外键两侧类型必须一致）—— SQLite 类型宽松，只有 MySQL 拦得住。
5. **API 层时间列方言**（BASE-002 / R14）：MySQL 的 DATETIME 由驱动取回 `datetime`，
   而响应模型声明 `str | None` —— 只有真实 MySQL 才能证明 API 层不因类型不符而降级
   （委托接口一条、会话与作业接口一条）；
6. **Agent 作业的三条并发锚点**（H7b，R1 前必须闭合）：双 worker 竞争同一作业、
   租约过期接管、旧 worker 迟到写入作废。胜负由**条件 UPDATE 命中几行**裁决，
   是 InnoDB 行锁语义 —— SQLite 整库一把写锁，怎么跑都只有一个赢家，证明不了。

运行方式
--------
* 需要环境变量 `MYSQL_TEST_URL`（如
  `mysql+pymysql://root:root@127.0.0.1:3306/entrust_test?charset=utf8mb4`）；
* 未设置时整模块**显式跳过**（skip，不是静默消失 —— CI 无 MySQL 的 job 里
  可见 skipped 计数）；
* CI 的 `pytest-mysql` job 起真实 MySQL 8.0 service 运行本模块。
"""

from __future__ import annotations

import os
import threading

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.idempotency import (  # noqa: E402
    IdempotencyError,
    idempotent,
)
from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

MYSQL_TEST_URL = os.environ.get("MYSQL_TEST_URL", "")

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(not MYSQL_TEST_URL, reason="未设置 MYSQL_TEST_URL，跳过 MySQL 并发集成"),
]

_TS = "%Y-%m-%d %H:%M:%S"
_ROUNDS = 4  # 竞争轮数：每轮重新播种一张 submitted 委托单


@pytest.fixture(scope="module")
def mysql():
    """模块级 MySQL 引擎与会话工厂（只应用迁移，不 create_all）。

    **为什么 fixture 不建基线表**：本模块的用例只依赖 `ent_` 表；基线表
    （users/ships/...）的建表由独立用例 `test_baseline_create_all_succeeds_on_mysql`
    单独验证（AC-25）—— 那里曾暴露 `users.id` BIGINT 与引用列 INT 不兼容
    （MySQL errno 3780），修复后由该用例持续守护，与并发用例的关注点分离。
    """
    from migrate import apply_pending

    engine = create_engine(
        MYSQL_TEST_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )
    apply_pending(engine)  # ent_ 表按迁移创建（已应用的跳过）

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    engine.dispose()


def _unique(prefix: str) -> str:
    import uuid as _uuid

    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _seed_submitted(db, owner_id: int = 900) -> int:
    """播种一张 submitted 状态的委托单，返回 assignment_id。"""
    org_result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": _unique("并发认领组织"), "c": utcnow_naive().strftime(_TS)},
    )
    org_id = int(org_result.lastrowid or 0)
    for uid in (10, 11):
        db.execute(
            text(
                "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
                "VALUES (:o, :u, 'manager', 'active', :c)"
            ),
            {"o": org_id, "u": uid, "c": utcnow_naive().strftime(_TS)},
        )
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, '[\"entrust:view\"]', 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    a = svc.create_assignment(db, owner_user_id=owner_id, title=f"并发认领-{utcnow_naive()}")
    submitted = svc.submit_assignment(
        db,
        assignment_id=a["assignment_id"],
        actor_id=owner_id,
        org_id=org_id,
        expected_revision=a["revision"],
    )
    return submitted["assignment_id"]


def test_migrations_settled_on_mysql(mysql):
    """迁移在真实 MySQL 上全部就位且无待执行（幂等的会话级复核）。"""
    from migrate import load_entries, split_entries

    _, pending = split_entries(mysql.kw["bind"], load_entries())
    assert pending == []


def test_baseline_create_all_succeeds_on_mysql(mysql):
    """AC-25：基线模型能在真实 MySQL 上建表（外键两侧类型一致）。

    ENT-007 首跑时此步失败：`users.id` 编译为 BIGINT，而 `ships.owner_id`
    等 8 个引用列是 INT，MySQL 拒绝建外键（errno 3780）。修复后由本用例在
    `pytest-mysql` job 中持续守护 —— SQLite 上类型宽松，拦不住这类问题。
    """
    from app.models import Base

    engine = mysql.kw["bind"]
    Base.metadata.create_all(bind=engine)  # 已存在的表按 IF NOT EXISTS 跳过

    inspector = inspect(engine)
    missing = sorted(t for t in Base.metadata.tables if not inspector.has_table(t))
    assert missing == [], f"create_all 之后仍缺失的基线表：{missing}"

    users_id = inspector.get_columns("users")[0]
    print(f"users.id -> {users_id['type']}")


def test_double_claim_race_exactly_one_winner(mysql):
    """AC-03 在真实 MySQL 上：多轮双经理并发认领，每轮恰好一个成功。"""
    winners: list[int] = []
    loser_reasons: list[str] = []

    for round_no in range(_ROUNDS):
        db = mysql()
        assignment_id = _seed_submitted(db)
        db.close()

        start = threading.Barrier(2)
        outcomes: list[tuple[str, int]] = []

        def claim(
            uid: int,
            *,
            start=start,
            assignment_id=assignment_id,
            outcomes=outcomes,
        ) -> None:
            # 循环内的闭包显式绑定当前轮次变量（B023），避免读到下一轮的值
            session = mysql()
            try:
                start.wait(timeout=10)
                try:
                    claimed = svc.claim_assignment(
                        session, assignment_id=assignment_id, actor_id=uid
                    )
                    outcomes.append(("won", int(claimed["claimed_by"])))
                except svc.AssignmentStateError as exc:
                    outcomes.append(("lost", str(exc)))
            finally:
                session.close()

        t1 = threading.Thread(target=claim, args=(10,))
        t2 = threading.Thread(target=claim, args=(11,))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert len(outcomes) == 2, f"第 {round_no} 轮有线程未完成: {outcomes}"
        won = [o for o in outcomes if o[0] == "won"]
        assert len(won) == 1, f"第 {round_no} 轮赢家数错误: {outcomes}"
        assert won[0][1] in (10, 11)
        for kind, detail in outcomes:
            if kind == "won":
                winners.append(detail)
            else:
                loser_reasons.append(str(detail))

        verify = mysql()
        row = svc.get_assignment(verify, assignment_id)
        verify.close()
        assert row is not None and row["claimed_by"] == won[0][1]

    # 4 轮全部有且仅有一个赢家；输家得到可读的状态冲突（不是异常崩塌）。
    # 输家有两种合法路径：进入时已读到 claimed（"只有待受理可认领"），
    # 或通过检查后输给条件 UPDATE（"认领失败"）—— 两者都是 AssignmentStateError。
    assert len(winners) == _ROUNDS
    assert all(
        ("认领失败" in reason or "状态已变化" in reason or "只有待受理可认领" in reason)
        for reason in loser_reasons
    )


def test_concurrent_edit_same_revision_one_winner(mysql):
    """AC-11 在真实 MySQL 上：同一 revision 并发编辑，恰好一个成功、另一个 409。"""
    db = mysql()
    a = svc.create_assignment(db, owner_user_id=1, title="并发编辑")
    assignment_id = a["assignment_id"]
    base_revision = a["revision"]
    db.close()

    start = threading.Barrier(2)
    outcomes: list[tuple[str, str]] = []

    def edit(cargo: str) -> None:
        session = mysql()
        try:
            start.wait(timeout=10)
            try:
                svc.update_draft(
                    session,
                    assignment_id=assignment_id,
                    actor_id=1,
                    expected_revision=base_revision,
                    cargo_summary=cargo,
                )
                outcomes.append(("ok", cargo))
            except svc.RevisionConflictError as exc:
                outcomes.append(("conflict", str(exc)))
        finally:
            session.close()

    t1 = threading.Thread(target=edit, args=("会话A的修改",))
    t2 = threading.Thread(target=edit, args=("会话B的修改",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert len(outcomes) == 2, outcomes
    kinds = sorted(kind for kind, _ in outcomes)
    assert kinds == ["conflict", "ok"], outcomes

    verify = mysql()
    row = svc.get_assignment(verify, assignment_id)
    verify.close()
    assert row is not None and row["revision"] == base_revision + 1  # 只累加了一次
    assert row["cargo_summary"] in ("会话A的修改", "会话B的修改")


def test_idempotency_concurrent_duplicate_side_effect_once(mysql):
    """同一幂等键并发执行：业务副作用（插入成果）恰好一次（ENT-002 语义）。"""
    from app.modules.entrust import artifacts as art

    db = mysql()
    org_result = db.execute(
        text(
            "INSERT INTO ent_organization (name, status, created_at) VALUES ('幂等组织', 'active', :c)"
        ),
        {"c": utcnow_naive().strftime(_TS)},
    )
    org_id = int(org_result.lastrowid or 0)
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, 900, '[\"entrust:quote:create\"]', 'active', :c)"
        ),
        {"o": org_id, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    entrustment_id = int(
        db.execute(text("SELECT id FROM ent_entrustment WHERE org_id = :o"), {"o": org_id}).scalar()
    )
    db.close()

    n_threads = 6
    start = threading.Barrier(n_threads)
    outcomes: list[str] = []
    lock = threading.Lock()
    scope = f"mysql-it:{utcnow_naive()}"  # 每次运行用新 scope，避免重放历史成功快照

    def worker() -> None:
        session = mysql()
        try:
            start.wait(timeout=15)
            with idempotent(
                session,
                scope=scope,
                key="same-key",
                actor_user_id=10,
                payload={"artifact_type": "quote_parsed"},
            ) as guard:
                if guard.replay is not None:
                    with lock:
                        outcomes.append("replayed")
                    return
                art.create_artifact(
                    session,
                    entrustment_id=entrustment_id,
                    artifact_type="quote_parsed",
                    payload={"seeded_by": threading.get_ident()},
                    created_by=10,
                )
                guard.succeed(200, {"artifact_id": 0})
                with lock:
                    outcomes.append("created")
        except IdempotencyError:
            with lock:
                outcomes.append("in-progress/conflict")
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(outcomes) == n_threads, outcomes

    verify = mysql()
    count = verify.execute(
        text(
            "SELECT COUNT(*) FROM ent_artifact WHERE entrustment_id = :eid "
            "AND artifact_type = 'quote_parsed'"
        ),
        {"eid": entrustment_id},
    ).scalar()
    verify.close()
    assert int(count) == 1, f"副作用发生了 {count} 次（应为 1）；outcomes={outcomes}"
    assert outcomes.count("created") == 1, outcomes


def test_assignment_api_timestamps_render_on_mysql(mysql):
    """BASE-002 / R14：真实 MySQL 上，委托详情接口必须返回字符串时间。

    修复前 `_row_to_assignment` 原样透传 `datetime`，而 `AssignmentOut` 声明
    时间为 `str | None` → 响应校验失败（500）。SQLite 上时间列是 TEXT，永远
    测不出来，所以这条断言必须跑在真实 MySQL 上才有意义。
    """
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app
    from app.modules.auth.dependencies import get_current_user
    from app.modules.auth.router import get_db

    owner_id = 4242
    db = mysql()
    created = svc.create_assignment(db, owner_user_id=owner_id, title="R14 时间列方言")
    submitted = svc.submit_assignment(
        db,
        assignment_id=created["assignment_id"],
        actor_id=owner_id,
        org_id=_seed_org_with_entrustment(db, owner_id),
        expected_revision=created["revision"],
    )
    db.close()

    def override_get_db():
        session = mysql()
        try:
            yield session
        finally:
            session.close()

    settings = get_settings()
    previous = settings.ENTRUST_ENABLED
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_id)
    settings.ENTRUST_ENABLED = True
    try:
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/entrust/assignments/{submitted['assignment_id']}")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            for key in (
                "created_at",
                "updated_at",
                "submitted_at",
                "claimed_at",
                "cancelled_at",
            ):
                assert body[key] is None or isinstance(body[key], str), (
                    f"{key} 未归一为文本：{type(body[key])} = {body[key]!r}"
                )
            assert isinstance(body["created_at"], str)
            assert isinstance(body["submitted_at"], str)
    finally:
        app.dependency_overrides.clear()
        settings.ENTRUST_ENABLED = previous


def test_session_and_job_api_timestamps_render_on_mysql(mysql):
    """BASE-002 教训的延伸：会话与作业接口的时间列也必须归一为文本。

    ENT-011 新增的 `ent_session` / `ent_session_message` / `ent_agent_job` /
    `ent_agent_job_attempt` 与既有表一样，在 SQLite 上是 TEXT、在 MySQL 上由驱动
    取回 `datetime`，而响应模型声明 `str`。这类缺陷只有真实 MySQL 才拦得住，
    所以幂等/并发之外，**时间列方言**也属于"必须在 MySQL 上验"的面。
    """
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app
    from app.modules.auth.dependencies import get_current_user
    from app.modules.auth.router import get_db
    from app.modules.entrust import sessions as sess_svc

    manager_id, owner_id = 5151, 5152
    db = mysql()
    org_id = _seed_org_with_entrustment(db, owner_id)
    now_ts = utcnow_naive().strftime(_TS)
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'manager', 'active', :c)"
        ),
        {"o": org_id, "u": manager_id, "c": now_ts},
    )
    # 授出 Agent 作业权限（作用域按该货主）
    db.execute(
        text(
            "UPDATE ent_entrustment SET permissions = :p WHERE entrust_user_id = :u AND org_id = :o"
        ),
        {"p": '["entrust:view","entrust:agent:job"]', "u": owner_id, "o": org_id},
    )
    db.commit()
    entrustment_id = int(
        db.execute(
            text(
                "SELECT id FROM ent_entrustment WHERE entrust_user_id = :u AND org_id = :o "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"u": owner_id, "o": org_id},
        ).scalar_one()
    )
    created = svc.create_assignment(db, owner_user_id=owner_id, title="方言-会话作业")
    submitted = svc.submit_assignment(
        db,
        assignment_id=created["assignment_id"],
        actor_id=owner_id,
        org_id=org_id,
        expected_revision=created["revision"],
    )
    session_row = sess_svc.create_session(
        db,
        entrustment_id=entrustment_id,
        assignment_id=submitted["assignment_id"],
        owner_user_id=owner_id,
        org_id=org_id,
        created_by=manager_id,
        specialty="agent_01",
        title="方言用例",
    )
    session_id = session_row["session_id"]
    db.close()

    def override_get_db():
        session = mysql()
        try:
            yield session
        finally:
            session.close()

    settings = get_settings()
    previous_enabled = settings.ENTRUST_ENABLED
    previous_mock = settings.LLM_MOCK
    previous_key = settings.LLM_API_KEY
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=manager_id)
    settings.ENTRUST_ENABLED = True
    settings.LLM_MOCK = True
    settings.LLM_API_KEY = ""
    headers = {"Authorization": "Bearer dialect-test"}

    def assert_times(body: dict[str, object], keys: tuple[str, ...]) -> None:
        for key in keys:
            value = body.get(key)
            assert value is None or isinstance(value, str), (
                f"{key} 未归一为文本：{type(value)} = {value!r}"
            )

    try:
        with TestClient(app) as client:
            detail = client.get(f"/api/v1/entrust/sessions/{session_id}", headers=headers)
            assert detail.status_code == 200, detail.text
            assert_times(detail.json()["session"], ("created_at", "updated_at"))

            msg = client.post(
                f"/api/v1/entrust/sessions/{session_id}/messages",
                json={"content": "方言检查"},
                headers={**headers, "Idempotency-Key": _unique("msg")},
            )
            assert msg.status_code == 200, msg.text
            assert_times(msg.json(), ("created_at",))

            job = client.post(
                f"/api/v1/entrust/sessions/{session_id}/jobs",
                json={"base_revision": 1},
                headers={**headers, "Idempotency-Key": _unique("job")},
            )
            assert job.status_code == 200, job.text
            job_id = job.json()["job_id"]
            assert_times(job.json(), ("created_at", "updated_at", "started_at", "cancelled_at"))

            run = client.post(f"/api/v1/entrust/agent/jobs/{job_id}/run", headers=headers)
            assert run.status_code == 200, run.text
            assert run.json()["job"]["status"] == "succeeded"
            assert_times(
                run.json()["job"],
                ("created_at", "updated_at", "started_at", "finished_at", "lease_expires_at"),
            )
            assert isinstance(run.json()["job"]["finished_at"], str)
            assert_times(run.json()["attempts"][0], ("started_at", "finished_at"))
    finally:
        app.dependency_overrides.clear()
        settings.ENTRUST_ENABLED = previous_enabled
        settings.LLM_MOCK = previous_mock
        settings.LLM_API_KEY = previous_key


# ── H7b：Agent 作业的三条并发正确性锚点 ──────────────────────────────────────
#
# 为什么这三条必须落在真实 MySQL 上（不能只用 SQLite 用例顶替）：
#   * 「双 worker 竞争」的胜负由**条件 UPDATE 命中几行**裁决 —— 这是 InnoDB 行锁
#     的语义，SQLite 整个库一把写锁，怎么跑都只有一个赢家，证明不了任何事；
#   * 「租约过期接管」比较的是 DATETIME 列与文本参数（`lease_expires_at < :now`），
#     MySQL 与 SQLite 对「DATETIME 存成什么、怎么比」的实现不同；
#   * 「旧 worker 迟到写入」要的是"两个连接各自持租约"的并发形状。
# 这三条同时也是 H7b 的**闭合证据**：守卫写错时它们在 CI 里会红。
#
# ⚠️ **跨会话读取的坑（首跑就踩了）**：MySQL 默认 REPEATABLE READ，而
# SQLAlchemy 的 Session **不会自动提交纯读** —— 一个会话做过 SELECT 之后事务
# 就一直开着，后续读都落在**同一个快照**上。于是「A 会话写并提交、再用 B 会话
# 读」会读到旧数据，表现为"刚才那行没写进去"。SQLite 没有这个现象（读也能看见
# 最新提交），**本地复现不出来**。⇒ 断言终局一律用**新开的会话**去读。


def _seed_job(db, *, max_attempts: int = 3) -> int:
    """造一个无会话的 queued 作业，返回 job_id。"""
    from app.modules.entrust import agentjobs as jobs

    job = jobs.submit_job(
        db,
        session_id=None,
        entrustment_id=None,
        assignment_id=None,
        specialty="agent_01",
        created_by=1,
        max_attempts=max_attempts,
    )
    return int(job["job_id"])


def _run(coro):
    """在同步用例里跑协程（线程里没有事件循环，各自 `asyncio.run`）。"""
    import asyncio

    return asyncio.run(coro)


def test_agent_job_two_workers_race_exactly_one_claim(mysql):
    """H7b①：双 worker 竞争**同一**作业，每轮恰好一个领到，尝试计数只 +1。

    领取的裁决点是 `UPDATE ... WHERE id=:jid AND status=:expected_status
    AND attempt_count=:expected_attempt`（乐观锁）。守卫缺失时两位 worker 都会
    领到 —— 同一份作业被跑两遍，attempt 日志与终局都会自相矛盾。
    """
    from app.modules.entrust import agentjobs as jobs

    for round_no in range(_ROUNDS):
        db = mysql()
        job_id = _seed_job(db)
        db.close()

        start = threading.Barrier(2)
        outcomes: list[str | None] = []
        lock = threading.Lock()

        def claim(
            worker: str,
            *,
            start=start,
            job_id=job_id,
            lock=lock,
            outcomes=outcomes,
        ) -> None:
            # 循环内闭包显式绑定本轮变量（B023），避免读到下一轮的值
            session = mysql()
            try:
                start.wait(timeout=10)
                got = jobs.claim_job(session, job_id=job_id, worker_id=worker)
                with lock:
                    outcomes.append(None if got is None else str(got["lease_owner"]))
            finally:
                session.close()

        t1 = threading.Thread(target=claim, args=("w1",))
        t2 = threading.Thread(target=claim, args=("w2",))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert len(outcomes) == 2, f"第 {round_no} 轮有线程未完成: {outcomes}"
        winners = [o for o in outcomes if o is not None]
        assert len(winners) == 1, f"第 {round_no} 轮赢家数错误（应为 1）: {outcomes}"
        assert winners[0] in ("w1", "w2")

        verify = mysql()
        row = jobs.get_job(verify, job_id)
        verify.close()
        assert row["status"] == "running"
        assert row["attempt_count"] == 1, f"尝试计数被重复消耗: {row['attempt_count']}"
        assert row["lease_owner"] == winners[0]


def test_agent_job_expired_lease_is_taken_over(mysql):
    """H7b②：租约过期后作业可被接管，且未过期时**抢不到**。

    两条断言缺一不可：只测"过期能接管"会放过「租约形同虚设、随时可抢」，
    只测"未过期抢不到"会放过「过期后没人能接、作业永远卡在 running」。
    """
    from datetime import timedelta

    from app.modules.entrust import agentjobs as jobs

    db = mysql()
    job_id = _seed_job(db, max_attempts=3)
    now = jobs.utcnow_naive()

    w1 = jobs.claim_job(db, job_id=job_id, worker_id="w1", lease_seconds=30, now=now)
    assert w1 is not None and w1["lease_owner"] == "w1"

    # 未过期：w2 抢不到（租约必须在有效期内被尊重）
    assert (
        jobs.claim_job(db, job_id=job_id, worker_id="w2", now=now + timedelta(seconds=10)) is None
    )

    # 过期：w2 接手，尝试计数继续累加（崩溃 worker 的任务不会丢）
    w2 = jobs.claim_job(
        db, job_id=job_id, worker_id="w2", lease_seconds=30, now=now + timedelta(seconds=31)
    )
    assert w2 is not None and w2["lease_owner"] == "w2"
    assert w2["attempt_count"] == 2
    db.close()


def test_agent_job_stale_worker_write_is_discarded(mysql):
    """H7b③：旧 worker 的迟到写入**不得**覆盖接管者的终局。

    形状：w1 领到 → 租约过期 → w2 接手并成功 → w1 带着自己的结果迟到写入。
    期望：w1 的写入被拒（`lease_lost`），终局仍是 w2 的成功结果，
    且"跑过但没生效"这件事在尝试日志里留痕（`abandoned` / `lease_lost`）——
    静默丢弃会让事后查「为什么没结果」无从下手。
    """
    from datetime import timedelta

    from app.modules.entrust import agentjobs as jobs

    db = mysql()
    job_id = _seed_job(db, max_attempts=3)
    now = jobs.utcnow_naive()

    c1 = jobs.claim_job(db, job_id=job_id, worker_id="w1", lease_seconds=30, now=now)
    assert c1 is not None

    # 租约过期 → w2 接手并跑完
    c2 = jobs.claim_job(
        db, job_id=job_id, worker_id="w2", lease_seconds=30, now=now + timedelta(seconds=31)
    )
    assert c2 is not None
    scope2 = jobs.scope_for_job(db, c2, operator_user_id=1)
    done = _run(
        jobs.execute_claimed_job(
            db,
            job_id=job_id,
            scope=scope2,
            worker_id="w2",
            attempt_no=int(c2["attempt_count"]),
        )
    )
    assert done["status"] == "succeeded", done

    # w1 迟到写入（它以为自己还持有租约）
    stale_db = mysql()
    scope1 = jobs.scope_for_job(stale_db, c1, operator_user_id=1)
    stale = _run(
        jobs.execute_claimed_job(
            stale_db,
            job_id=job_id,
            scope=scope1,
            worker_id="w1",
            attempt_no=int(c1["attempt_count"]),
        )
    )
    # ⚠️ 终局读取必须用**新会话**，不能用 `db`：
    # MySQL 默认隔离级别是 REPEATABLE READ，而 `db` 在上一次 `get_job` 之后
    # **事务一直开着**（SQLAlchemy 不会自动提交纯读）。它的快照早于 `stale_db`
    # 的提交 ⇒ 用 `db` 读会看不见那行 `abandoned`，表现为"作废没留痕"
    # （首次跑本用例就是这么红的：留痕其实在库里，是读的那只眼睛是旧的）。
    # SQLite 没有这个问题（整个库一把写锁、读也能看见最新提交），所以本地
    # 用 SQLite 复现不出来 —— 这正是这条必须在真实 MySQL 上跑的理由之一。
    stale_db.close()
    db.close()
    verify = mysql()
    final = jobs.get_job(verify, job_id)
    verify.close()
    assert stale.get("lease_lost") is True, stale
    assert final["status"] == "succeeded", "迟到写入改掉了接管者的终局"
    assert final["lease_owner"] is None
    kinds = [(a["status"], a["error_kind"]) for a in final["attempts"]]
    assert ("abandoned", "lease_lost") in kinds, (
        f"作废未留痕: {kinds}｜写入方会话读到的 attempts="
        f"{[(a['status'], a['error_kind']) for a in (stale.get('attempts') or [])]}"
    )


def _seed_org_with_entrustment(db, owner_id: int) -> int:
    """造一个 active 组织 + 该货主的 active 委托授权，返回 org_id。"""
    org_result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": _unique("方言组织"), "c": utcnow_naive().strftime(_TS)},
    )
    org_id = int(org_result.lastrowid or 0)
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, '[\"entrust:view\"]', 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return org_id


def test_extraction_api_timestamps_render_on_mysql(mysql):
    """ENT-013 的 `ent_attachment_text` 与提取接口同样受时间列方言影响。

    提取文本行的 `created_at` / `updated_at` 与附件元数据一样：SQLite 上是 TEXT、
    MySQL 上由驱动取回 `datetime`，而响应模型声明 `str | None`。**只有真实 MySQL
    才能证明归一函数确实生效** —— 漏归一时 Pydantic 不会把 datetime 强转成字符串，
    而是直接抛校验错误（接口 500），SQLite 上永远看不到。
    """
    import tempfile
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app
    from app.modules.auth.dependencies import get_current_user
    from app.modules.auth.router import get_db
    from app.modules.entrust import attachments as att_svc

    # 存储目录先指向临时目录：落盘与读取必须用同一处，否则下载/读取会因找不到文件而 500
    settings = get_settings()
    previous_enabled = settings.ENTRUST_ENABLED
    previous_dir = settings.ATTACHMENT_STORAGE_DIR
    temp_dir = tempfile.mkdtemp(prefix="att-dialect-")
    settings.ATTACHMENT_STORAGE_DIR = temp_dir
    settings.ENTRUST_ENABLED = True

    manager_id, owner_id = 5251, 5252
    db = mysql()
    try:
        org_id = _seed_org_with_entrustment(db, owner_id)
        now_ts = utcnow_naive().strftime(_TS)
        db.execute(
            text(
                "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
                "VALUES (:o, :u, 'manager', 'active', :c)"
            ),
            {"o": org_id, "u": manager_id, "c": now_ts},
        )
        db.execute(
            text(
                "UPDATE ent_entrustment SET permissions = :p "
                "WHERE entrust_user_id = :u AND org_id = :o"
            ),
            {"p": '["entrust:view","entrust:quote:create"]', "u": owner_id, "o": org_id},
        )
        db.commit()
        entrustment_id = int(
            db.execute(
                text(
                    "SELECT id FROM ent_entrustment WHERE entrust_user_id = :u AND org_id = :o "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"u": owner_id, "o": org_id},
            ).scalar_one()
        )
        attachment = att_svc.store_bytes(
            db,
            uploader_user_id=manager_id,
            owner_user_id=owner_id,
            org_id=org_id,
            filename="报价.txt",
            content_type="text/plain",
            data="承运人：长江物流有限公司\n单价：38.00 元/吨".encode(),
            entrustment_id=entrustment_id,
        )
    finally:
        db.close()

    attachment_id = int(attachment["attachment_id"])

    def override_get_db():
        session = mysql()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=manager_id)
    headers = {"Authorization": "Bearer dialect-test"}
    try:
        with TestClient(app) as client:
            extracted = client.post(
                f"/api/v1/entrust/attachments/{attachment_id}/extract",
                headers={**headers, "Idempotency-Key": _unique("ext")},
            )
            assert extracted.status_code == 200, extracted.text
            assert extracted.json()["extract_status"] == "done"
            assert isinstance(extracted.json()["extracted_chars"], int)

            fetched = client.get(
                f"/api/v1/entrust/attachments/{attachment_id}/text", headers=headers
            )
            assert fetched.status_code == 200, fetched.text
            payload = fetched.json()
            assert payload["has_text"] is True
            assert payload["source"] == "extractor"
            assert "长江物流" in payload["text"]
            # 这两列在 MySQL 上是 DATETIME，未归一即会 500 或被强转 —— 必须仍是文本
            assert isinstance(payload["updated_at"], str), type(payload["updated_at"])
            assert payload["updated_at"]

            meta = client.get(f"/api/v1/entrust/attachments/{attachment_id}", headers=headers)
            assert meta.status_code == 200, meta.text
            for key in ("created_at", "updated_at", "source_event_at"):
                value = meta.json().get(key)
                assert value is None or isinstance(value, str), (key, type(value))
    finally:
        app.dependency_overrides.clear()
        settings.ENTRUST_ENABLED = previous_enabled
        settings.ATTACHMENT_STORAGE_DIR = previous_dir


# ─────────────────────────────────────────── S3 客户响应并发（BP-03 / D1-07）


def _seed_release_for_race(db, *, owner_id: int = 950, manager_id: int = 951) -> int:
    """播种一条**可响应**的发布记录，返回 release_id。

    走的是**生产同一条命令**（`offers.release_offer`），不是手写 INSERT ——
    手写的话，一旦发布命令的写入逻辑变了，这条并发用例会继续绿着而实际已经失效。
    """
    from app.modules.entrust import artifacts as art
    from app.modules.entrust import offers as offers_svc

    ts = utcnow_naive().strftime(_TS)
    org_id = int(
        db.execute(
            text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
            {"n": _unique("发布响应并发组织"), "c": ts},
        ).lastrowid
        or 0
    )
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'manager', 'active', :c)"
        ),
        {"o": org_id, "u": manager_id, "c": ts},
    )
    eid = int(
        db.execute(
            text(
                "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
                "VALUES (:o, :u, :p, 'active', :c)"
            ),
            {
                "o": org_id,
                "u": owner_id,
                "p": '["entrust:view","entrust:quote:create","entrust:quote:publish"]',
                "c": ts,
            },
        ).lastrowid
        or 0
    )
    aid = int(
        db.execute(
            text(
                "INSERT INTO ent_assignment (owner_user_id, org_id, title, status, revision, "
                " created_at, updated_at) VALUES (:o, :g, :t, 'claimed', 1, :c, :c)"
            ),
            {"o": owner_id, "g": org_id, "t": _unique("并发响应委托"), "c": ts},
        ).lastrowid
        or 0
    )
    db.commit()
    created = art.create_artifact(
        db,
        entrustment_id=eid,
        artifact_type="customer_quote",
        payload={"amount": 36000, "currency": "CNY", "includes": ["装船", "卸船"]},
        created_by=manager_id,
        source=art.SOURCE_MANUAL,
        assignment_id=aid,
    )
    release = offers_svc.release_offer(
        db,
        artifact_id=int(created["artifact_id"]),
        revision_no=1,
        actor_user_id=manager_id,
    )
    return int(release["release_id"])


def test_offer_response_race_exactly_one_accept(mysql):
    """D1-07 的**并发**面：两个响应同时到达，恰好一个成功、库里恰好一行。

    判据是 `ent_offer_response` 上的 `UNIQUE (release_id)` —— 应用层"先查后写"
    在真并发下两个请求可以同时通过检查，于是同一次发布被接受两次，而两次都
    "看起来"合法。SQLite 证明不了这条（整库一把写锁），所以放在本模块。
    """
    from app.modules.entrust import offers as offers_svc

    owner_id = 950
    winners: list[int] = []
    for round_no in range(_ROUNDS):
        db = mysql()
        release_id = _seed_release_for_race(db, owner_id=owner_id)
        db.close()

        start = threading.Barrier(2)
        outcomes: list[tuple[str, str]] = []

        def respond(
            *,
            start=start,
            release_id=release_id,
            outcomes=outcomes,
        ) -> None:
            session = mysql()
            try:
                start.wait(timeout=10)
                try:
                    got = offers_svc.respond_to_offer(
                        session,
                        release_id=release_id,
                        decision="accept",
                        note=None,
                        actor_user_id=owner_id,
                    )
                    outcomes.append(("won", str(got["response_id"])))
                except Exception as exc:  # noqa: BLE001 —— 输家的**具体形态**要记录，不掩盖
                    outcomes.append(("lost", f"{type(exc).__name__}: {exc}"))
            finally:
                session.close()

        t1 = threading.Thread(target=respond)
        t2 = threading.Thread(target=respond)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert len(outcomes) == 2, f"第 {round_no} 轮有线程未完成: {outcomes}"
        won = [o for o in outcomes if o[0] == "won"]
        assert len(won) == 1, f"第 {round_no} 轮赢家数错误（同一次发布被响应了多次）: {outcomes}"
        winners.append(int(won[0][1]))

        verify = mysql()
        try:
            rows = (
                verify.execute(
                    text("SELECT COUNT(*) AS n FROM ent_offer_response WHERE release_id = :r"),
                    {"r": release_id},
                )
                .mappings()
                .first()
            )
            assert int(rows["n"]) == 1, (
                f"第 {round_no} 轮库里出现了 {rows['n']} 行客户响应 —— "
                "唯一约束没兜住，同一次发布被响应了多次"
            )
        finally:
            verify.close()

    assert len(winners) == _ROUNDS


# ─────────────────────────────────────── S3 合同派生并发（BP-03 第 8 条 / D1-08）


def _seed_accepted_release(db, *, owner_id: int = 960, manager_id: int = 961) -> int:
    """播种一条**已被客户接受**的发布，返回 release_id（并发派生的前置）。

    与上面那条一样走**生产命令**（`release_offer` / `respond_to_offer`），不手写
    INSERT —— 手写的话，一旦发布或响应命令的写入逻辑变了，这条并发用例会继续绿着
    而实际已经失效。
    """
    from app.modules.entrust import offers as offers_svc

    release_id = _seed_release_for_race(db, owner_id=owner_id, manager_id=manager_id)
    offers_svc.respond_to_offer(
        db, release_id=release_id, decision="accept", note=None, actor_user_id=owner_id
    )
    return release_id


def test_contract_derivation_race_exactly_one_contract(mysql):
    """两个派生请求同时到达 ⇒ 恰好一个成功，库里恰好**一份**合同与**一条**派生记录。

    判据是 `ent_contract_derivation` 上的 `UNIQUE (release_id)`。这条尤其需要真 MySQL：

    * 派生**一次写四张表**（合同成果 / 合同版本 / 派生记录 / 字段来源表）。靠"先查后写"
      判重，并发下会派生出一式两份合同，而两份都"看起来"合法；
    * 更糟的是两份合同各有独立的版本链 —— 事后审计无法判断哪一份才是"客户接受事实"
      的那一份，而 D1-08 的全部价值就是这条可核对性。

    SQLite 整库一把写锁，两个线程必然串行，怎么跑都只有一个赢家，**证明不了任何事**，
    所以这条只能放在本模块（与上面客户响应并发同一条理由）。
    """
    from app.modules.entrust import contracts as ctr

    owner_id = 960
    manager_id = 961
    for round_no in range(_ROUNDS):
        db = mysql()
        release_id = _seed_accepted_release(db, owner_id=owner_id, manager_id=manager_id)
        db.close()

        start = threading.Barrier(2)
        outcomes: list[tuple[str, str]] = []

        def derive(*, start=start, release_id=release_id, outcomes=outcomes) -> None:
            session = mysql()
            try:
                start.wait(timeout=10)
                try:
                    got = ctr.derive_contract(
                        session, release_id=release_id, actor_user_id=manager_id
                    )
                    outcomes.append(("won", str(got["contract_artifact_id"])))
                except Exception as exc:  # noqa: BLE001 —— 输家的**具体形态**要记录，不掩盖
                    outcomes.append(("lost", f"{type(exc).__name__}: {exc}"))
            finally:
                session.close()

        t1 = threading.Thread(target=derive)
        t2 = threading.Thread(target=derive)
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)

        assert len(outcomes) == 2, f"第 {round_no} 轮有线程未完成: {outcomes}"
        won = [o for o in outcomes if o[0] == "won"]
        assert len(won) == 1, (
            f"第 {round_no} 轮赢家数错误（同一份已接受事实被派生了多次）: {outcomes}"
        )

        # ⚠️ 跨会话读取：MySQL 默认 REPEATABLE READ，同一个会话读过一次之后事务就一直开着，
        #    后续读落在同一个快照上。终局一律用**新开的会话**去读（见本模块 H7b 段的同一纪律）。
        verify = mysql()
        try:
            row = (
                verify.execute(
                    text(
                        "SELECT COUNT(*) AS n, MIN(assignment_id) AS a FROM ent_contract_derivation "
                        "WHERE release_id = :r"
                    ),
                    {"r": release_id},
                )
                .mappings()
                .first()
            )
            assert int(row["n"]) == 1, (
                f"第 {round_no} 轮库里出现了 {row['n']} 条派生记录 —— "
                "唯一约束没兜住，同一份已接受事实被派生了多次"
            )
            # 只数"这条发布所在的委托单"下的合同成果：同一轮里没有别的来源会造它
            contracts = verify.execute(
                text(
                    "SELECT COUNT(*) AS n FROM ent_artifact WHERE assignment_id = :a "
                    "AND artifact_type = 'contract_review'"
                ),
                {"a": int(row["a"])},
            ).scalar()
            assert int(contracts) == 1, (
                f"第 {round_no} 轮库里出现了 {contracts} 份合同成果 —— "
                "并发下派生出了一式两份（各自还有独立版本链，事后无法判断哪份有效）"
            )
        finally:
            verify.close()


# ────────────────────────────── S3 运力确认并发（BP-03 第 3 条 / D1-06）


def _seed_candidate_for_race(
    db, *, owner_id: int = 970, manager_id: int = 971
) -> tuple[int, int, int]:
    """播种一条**可确认**的候选运力，返回 `(assignment_id, candidate_id, entrustment_id)`。

    候选走**生产同一条命令**（`capacity.record_candidate`），不是手写 INSERT ——
    手写的话，一旦登记命令的写入逻辑变了（比如吨位规范化改了），这条并发用例
    会继续绿着而实际已经失效（与上面两条同一纪律）。

    有效期取"今天 +90 天"而不是写死日期：写死会让这条用例在某一天之后自动变成
    "过期 ⇒ 判定不通过"的场景，而那时失败信息看起来像并发缺陷。
    """
    from datetime import timedelta

    from app.modules.entrust import capacity as cap

    ts = utcnow_naive().strftime(_TS)
    org_id = int(
        db.execute(
            text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
            {"n": _unique("运力确认并发组织"), "c": ts},
        ).lastrowid
        or 0
    )
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'manager', 'active', :c)"
        ),
        {"o": org_id, "u": manager_id, "c": ts},
    )
    eid = int(
        db.execute(
            text(
                "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
                "VALUES (:o, :u, :p, 'active', :c)"
            ),
            {
                "o": org_id,
                "u": owner_id,
                "p": '["entrust:view","entrust:quote:create","entrust:quote:publish"]',
                "c": ts,
            },
        ).lastrowid
        or 0
    )
    aid = int(
        db.execute(
            text(
                "INSERT INTO ent_assignment (owner_user_id, org_id, title, cargo_summary, quantity, "
                " quantity_unit, status, revision, created_at, updated_at) "
                "VALUES (:o, :g, :t, '钢材', 800.000, '吨', 'claimed', 1, :c, :c)"
            ),
            {"o": owner_id, "g": org_id, "t": _unique("运力确认并发委托"), "c": ts},
        ).lastrowid
        or 0
    )
    db.commit()
    candidate = cap.record_candidate(
        db,
        assignment_id=aid,
        actor_user_id=manager_id,
        carrier="桂平航 6688",
        capacity_tonnes="900.000",
        vessel_count=1,
        allows_partial_load=False,
        rate="45.00",
        rate_unit="吨",
        currency="CNY",
        valid_until=(cap.today_utc() + timedelta(days=90)).isoformat(),
        evidence_kind="document",
        evidence_ref="att:race",
    )
    return aid, int(candidate["candidate_id"]), eid


def test_capacity_confirmation_race_exactly_one_confirmation(mysql):
    """两个确认请求同时到达同一个候选 ⇒ 恰好一个成功，库里恰好**一条**确认与**一套**判定。

    判据是 `ent_capacity_confirmation` 上的 `UNIQUE (candidate_id)`。这条尤其需要真 MySQL：

    * 确认**一次写四张表**（采购确认成果 / 成果版本 / 确认记录 / 逐规则判定）。靠"先查后写"
      判重，并发下会确认两次，而两条都"看起来"合法 —— 于是**同一条运力被确认了两遍**，
      各自的判定行也各写一套；
    * 更硬的理由：确认除确认行外还写了**一份成果**（`procurement_confirm`）。两份成果
      意味着"同一条候选运力"有两条独立的采购确认版本链，`recheck` 与后续的复核、
      变更影响都会面对"哪一条才是那次确认"这个问题，而它无从回答。

    SQLite 整库一把写锁，两线程必然串行，怎么跑都只有一个赢家，**证明不了任何事**，
    所以这条只能放在本模块（与客户响应并发、合同派生并发同一条理由）。
    """
    from app.modules.entrust import capacity as cap

    owner_id = 970
    manager_id = 971
    for round_no in range(_ROUNDS):
        db = mysql()
        aid, candidate_id, eid = _seed_candidate_for_race(
            db, owner_id=owner_id, manager_id=manager_id
        )
        db.close()

        start = threading.Barrier(2)
        outcomes: list[tuple[str, str]] = []

        def confirm(
            *, start=start, aid=aid, candidate_id=candidate_id, eid=eid, outcomes=outcomes
        ) -> None:
            session = mysql()
            try:
                start.wait(timeout=10)
                try:
                    got = cap.confirm_capacity(
                        session,
                        assignment_id=aid,
                        candidate_id=candidate_id,
                        actor_user_id=manager_id,
                        agreed_scope="南宁→贵港 水运段 900 吨舱位",
                        entrustment_id=eid,
                    )
                    outcomes.append(("won", str(got["confirmation_id"])))
                except Exception as exc:  # noqa: BLE001 —— 输家的**具体形态**要记录，不掩盖
                    outcomes.append(("lost", f"{type(exc).__name__}: {exc}"))
            finally:
                session.close()

        t1 = threading.Thread(target=confirm)
        t2 = threading.Thread(target=confirm)
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)

        assert len(outcomes) == 2, f"第 {round_no} 轮有线程未完成: {outcomes}"
        won = [o for o in outcomes if o[0] == "won"]
        lost = [o for o in outcomes if o[0] == "lost"]
        assert len(won) == 1, f"第 {round_no} 轮赢家数错误（同一候选被确认了多次）: {outcomes}"
        # 输家必须是**业务态**（CapacityStateError ⇒ HTTP 409），不能是裸的
        # IntegrityError / OperationalError —— 后者会变成 500，而"并发时偶发 500"
        # 在演示里会被读成系统不稳，实际是唯一约束没被翻译。不掩盖具体形态。
        assert lost[0][1].startswith("CapacityStateError"), (
            f"第 {round_no} 轮输家的异常形态不对（应为 CapacityStateError）：{lost[0][1]}"
        )

        # ⚠️ 跨会话读取：MySQL 默认 REPEATABLE READ，同一个会话读过一次之后事务就一直开着，
        #    后续读落在同一个快照上。终局一律用**新开的会话**去读（与上面两条同一纪律）。
        verify = mysql()
        try:
            row = (
                verify.execute(
                    text(
                        "SELECT COUNT(*) AS n FROM ent_capacity_confirmation "
                        "WHERE candidate_id = :c"
                    ),
                    {"c": candidate_id},
                )
                .mappings()
                .first()
            )
            assert int(row["n"]) == 1, (
                f"第 {round_no} 轮库里出现了 {row['n']} 条确认记录 —— "
                "唯一约束没兜住，同一候选被确认了多次"
            )
            checks = verify.execute(
                text(
                    "SELECT COUNT(*) AS n FROM ent_capacity_rule_check k "
                    "JOIN ent_capacity_confirmation c ON c.id = k.confirmation_id "
                    "WHERE c.candidate_id = :c"
                ),
                {"c": candidate_id},
            ).scalar()
            assert int(checks) == len(cap.ALL_RULE_CODES), (
                f"第 {round_no} 轮判定行数为 {checks}（应为 {len(cap.ALL_RULE_CODES)}）—— "
                "要么规则没跑全，要么确认被写了两遍"
            )
            artifacts = verify.execute(
                text(
                    "SELECT COUNT(*) AS n FROM ent_artifact WHERE assignment_id = :a "
                    "AND artifact_type = 'procurement_confirm'"
                ),
                {"a": aid},
            ).scalar()
            assert int(artifacts) == 1, (
                f"第 {round_no} 轮库里出现了 {artifacts} 份采购确认成果 —— "
                "并发下同一候选产出了两条独立版本链，事后无法判断哪条是那次确认"
            )
        finally:
            verify.close()
