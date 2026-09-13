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
   副作用恰好发生一次。

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
from sqlalchemy import create_engine, text  # noqa: E402
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
    """模块级 MySQL 引擎与会话工厂：全新结构。

    为可重复执行，先尽力清掉历史表（CI 每次都是新库；本地重跑也能干净开始）。
    """
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        MYSQL_TEST_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )
    Base.metadata.create_all(bind=engine)  # user 等基线表（IF NOT EXISTS 语义）
    apply_pending(engine)  # ent_ 表按迁移创建（已应用的跳过）

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory, engine
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

    # 4 轮全部由条件更新裁决；输家的原因必须可读（不是异常崩塌）
    assert len(winners) == _ROUNDS
    assert all("认领失败" in reason or "状态已变化" in reason for reason in loser_reasons)


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
