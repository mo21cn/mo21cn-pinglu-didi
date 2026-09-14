"""A2 五之一「应用已批准的变更」—— 验证 17 / 18 的服务层用例（ENT-033）。

覆盖 HO 2026-09-15 裁决 P4 的实施前提，四条不变量各有用例：

* **只认批准快照**：应用内容来自 `decided` 事件的批准快照，请求方不能临时替换；
* **逐目标核对版本**：过期 ⇒ 整批拒绝（409）+ `applied_rejected`，**不产生部分生效**；
* **统一事务**：失败整笔回滚；成功后再次应用不产生重复副作用；
* **纯任务目标不虚构成果版本**：任务 link 的 `applied_revision_id` 保持 NULL。

本文件与 `test_entrust_exceptions_service.py` 同款播种助手（独立 SQLite 内存库）。
"""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import artifacts as art_svc  # noqa: E402
from app.modules.entrust import assignments as assign_svc  # noqa: E402
from app.modules.entrust import exceptions as exc  # noqa: E402
from app.modules.entrust import tasks as task_svc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

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


# ── 播种助手（与 test_entrust_exceptions_service.py 同款）────────────────────


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


def _artifact(session, env: dict, *, freight: int = 12000) -> dict:
    return art_svc.create_artifact(
        session,
        entrustment_id=env["entrustment_id"],
        artifact_type="quote_parsed",
        payload={"freight": freight, "currency": "CNY"},
        created_by=MANAGER,
        assignment_id=env["assignment_id"],
    )


def _current_revision(session, artifact_id: int) -> int | None:
    row = session.execute(
        text("SELECT current_revision_id FROM ent_artifact WHERE id = :aid"),
        {"aid": artifact_id},
    ).first()
    return None if row is None or row[0] is None else int(row[0])


def _revision_count(session, artifact_id: int) -> int:
    row = session.execute(
        text("SELECT COUNT(*) FROM ent_artifact_revision WHERE artifact_id = :aid"),
        {"aid": artifact_id},
    ).first()
    return int(row[0]) if row else 0


def _case_with_artifact(session, env: dict, artifact_id: int, **overrides) -> dict:
    params: dict = {
        "assignment_id": env["assignment_id"],
        "actor_id": MANAGER,
        "kind": exc.KIND_CHANGE_REQUEST,
        "title": "运价调整",
        "severity": "medium",
        "impact_kind": exc.IMPACT_REVIEW_REQUIRED,
        "links": [{"target_kind": exc.TARGET_ARTIFACT, "target_id": artifact_id}],
    }
    params.update(overrides)
    return exc.raise_case(session, **params)


def _approve(session, case_id: int, *, basis_revision_id: int, changes: dict | None = None) -> dict:
    """走到 `approved` —— `change_request` 必须先经 `in_review`（两套状态机各自生效）。

    返回批准后的案件（带**新的** `revision_no`），调用方据此传 `expected_revision`。
    """
    exc.decide(
        session,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_IN_REVIEW,
        expected_revision=1,
    )
    return exc.decide(
        session,
        exception_id=case_id,
        actor_id=MANAGER,
        to_status=exc.STATUS_APPROVED,
        expected_revision=2,
        basis_revision_id=basis_revision_id,
        approved_changes=changes,
    )


def _events(session, case_id: int) -> list[dict]:
    return exc.list_events(session, case_id)


def _kinds(events: list[dict]) -> list[str]:
    return [str(e["event_kind"]) for e in events]


# ── 批准快照（P4-A1/A2）─────────────────────────────────────────────────────


def test_approval_writes_machine_checkable_snapshot(db):
    """`approved` 必须留下**逐目标**基础版本 —— 案件级单个 basis 覆盖不了多目标。"""
    env = _env(db)
    art = _artifact(db, env)
    art_id = int(art["artifact_id"])
    case = _case_with_artifact(db, env, art_id)
    case_id = int(case["id"])
    basis = _current_revision(db, art_id)

    _approve(
        db,
        case_id,
        basis_revision_id=basis,
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )

    snapshot = exc._load_approval_snapshot(db, case_id)
    assert snapshot is not None, "批准必须写入可核对的快照"
    assert snapshot["version"] == exc.APPROVAL_SNAPSHOT_VERSION
    assert snapshot["case_basis_revision_id"] == basis
    assert snapshot["targets"] == [
        {
            "target_kind": "artifact",
            "target_id": art_id,
            "basis_revision_id": basis,
            "has_changes": True,
        }
    ]
    assert snapshot["changes"][f"artifact#{art_id}"]["freight"] == 13000


# ── 应用正例 ────────────────────────────────────────────────────────────────


def test_apply_confirms_exact_revision_and_records_link(db):
    env = _env(db)
    art = _artifact(db, env)
    art_id = int(art["artifact_id"])
    case = _case_with_artifact(db, env, art_id)
    case_id = int(case["id"])
    basis = _current_revision(db, art_id)
    before = _revision_count(db, art_id)
    approved = _approve(
        db,
        case_id,
        basis_revision_id=basis,
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )

    applied = exc.apply_case(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        expected_revision=int(approved["revision_no"]),
    )

    assert applied["status"] == exc.STATUS_APPLIED
    assert _revision_count(db, art_id) == before + 1, "应用产生**一个**新版本"
    link = applied["links"][0]
    assert link["applied_revision_id"] is not None
    assert _current_revision(db, art_id) == int(link["applied_revision_id"])
    assert exc.EVENT_APPLIED in _kinds(_events(db, case_id))


def test_task_target_does_not_fake_an_artifact_revision(db):
    """纯任务目标：任务用自己的 `revision` 作依据，**不**虚构成果版本（P4-A5）。"""
    env = _env(db)
    task_id = int(
        task_svc.create_task(
            db,
            assignment_id=env["assignment_id"],
            actor_id=MANAGER,
            task_type=task_svc.TASK_TYPE_EXECUTION,
            title="复核船型适配",
        )["task_id"]
    )
    case = exc.raise_case(
        db,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        kind=exc.KIND_CHANGE_REQUEST,
        title="货物数量调整",
        severity="medium",
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        links=[{"target_kind": exc.TARGET_TASK, "target_id": task_id}],
    )
    case_id = int(case["id"])
    art = _artifact(db, env)  # 仅用于提供 basis（approved 强制要求）
    approved = _approve(
        db, case_id, basis_revision_id=_current_revision(db, int(art["artifact_id"]))
    )

    applied = exc.apply_case(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        expected_revision=int(approved["revision_no"]),
    )

    assert applied["status"] == exc.STATUS_APPLIED
    assert applied["links"][0]["target_kind"] == exc.TARGET_TASK
    assert applied["links"][0]["applied_revision_id"] is None, (
        "任务没有成果版本；塞一个语义不符的值会让 resolved 关闭判据失真"
    )


# ── 验证 17：依据版本变化后，旧批准不能直接应用 ─────────────────────────────


def test_apply_rejected_when_basis_moved(db):
    """批准后改动受影响成果 ⇒ 409 + `applied_rejected`，且**不产生新版本**。"""
    env = _env(db)
    art = _artifact(db, env)
    art_id = int(art["artifact_id"])
    case = _case_with_artifact(db, env, art_id)
    case_id = int(case["id"])
    basis = _current_revision(db, art_id)
    approved = _approve(
        db,
        case_id,
        basis_revision_id=basis,
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )

    # 批准后、应用前：成果被改了一版（依据版本不再新鲜）
    moved = art_svc.append_revision(
        db,
        artifact_id=art_id,
        payload={"freight": 12500, "currency": "CNY"},
        actor_id=MANAGER,
        source="manual",
    )
    art_svc.confirm_revision(
        db,
        artifact_id=art_id,
        revision_no=int(moved["revision_no"]),
        actor_id=MANAGER,
    )
    before = _revision_count(db, art_id)

    with pytest.raises(exc.ExceptionCaseConflictError) as info:
        exc.apply_case(
            db,
            exception_id=case_id,
            actor_id=MANAGER,
            expected_revision=int(approved["revision_no"]),
        )
    assert "依据版本已变化" in str(info.value)

    fresh = exc.get_case(db, case_id)
    assert fresh["status"] == exc.STATUS_APPROVED, "被拒后案件保持 approved，不是半应用"
    assert _revision_count(db, art_id) == before, "拒绝路径**不得**产生新版本"
    assert exc.EVENT_APPLIED_REJECTED in _kinds(_events(db, case_id)), (
        "拒绝事件必须落库 —— 它是审计事实，不能随业务回滚一起消失（P4-A8）"
    )


# ── 验证 18：失败/重试不产生部分生效、重复副作用 ───────────────────────────


def test_apply_is_atomic_when_second_target_fails(db, monkeypatch):
    """第 2 条 link 失败 ⇒ 第 1 条的新版本**不落库**（整笔回滚）。"""
    env = _env(db)
    art1 = _artifact(db, env, freight=12000)
    art2 = _artifact(db, env, freight=21000)
    id1, id2 = int(art1["artifact_id"]), int(art2["artifact_id"])
    case = exc.raise_case(
        db,
        assignment_id=env["assignment_id"],
        actor_id=MANAGER,
        kind=exc.KIND_CHANGE_REQUEST,
        title="同时调整两份报价",
        severity="medium",
        impact_kind=exc.IMPACT_REVIEW_REQUIRED,
        links=[
            {"target_kind": exc.TARGET_ARTIFACT, "target_id": id1},
            {"target_kind": exc.TARGET_ARTIFACT, "target_id": id2},
        ],
    )
    case_id = int(case["id"])
    basis1 = _current_revision(db, id1)
    approved = _approve(
        db,
        case_id,
        basis_revision_id=basis1,
        changes={
            f"artifact#{id1}": {"freight": 13000, "currency": "CNY"},
            f"artifact#{id2}": {"freight": 22000, "currency": "CNY"},
        },
    )
    before1, before2 = _revision_count(db, id1), _revision_count(db, id2)

    original = art_svc.append_revision
    calls = {"n": 0}

    def _fail_on_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("注入失败：第 2 条 link 应用失败")
        return original(*args, **kwargs)

    monkeypatch.setattr(art_svc, "append_revision", _fail_on_second)

    with pytest.raises(RuntimeError):
        exc.apply_case(
            db,
            exception_id=case_id,
            actor_id=MANAGER,
            expected_revision=int(approved["revision_no"]),
        )

    monkeypatch.undo()
    assert _revision_count(db, id1) == before1, "第 1 条的新版本必须随整笔回滚消失"
    assert _revision_count(db, id2) == before2
    assert exc.get_case(db, case_id)["status"] == exc.STATUS_APPROVED
    assert exc.EVENT_APPLIED_REJECTED in _kinds(_events(db, case_id))


def test_replay_after_success_does_not_create_revision(db):
    """应用成功后再次应用 ⇒ 拒绝，且**不**再产生新版本（重试无重复副作用）。"""
    env = _env(db)
    art = _artifact(db, env)
    art_id = int(art["artifact_id"])
    case = _case_with_artifact(db, env, art_id)
    case_id = int(case["id"])
    approved = _approve(
        db,
        case_id,
        basis_revision_id=_current_revision(db, art_id),
        changes={f"artifact#{art_id}": {"freight": 13000, "currency": "CNY"}},
    )
    exc.apply_case(
        db,
        exception_id=case_id,
        actor_id=MANAGER,
        expected_revision=int(approved["revision_no"]),
    )
    after_first = _revision_count(db, art_id)

    with pytest.raises(exc.ExceptionCaseConflictError):
        exc.apply_case(
            db,
            exception_id=case_id,
            actor_id=MANAGER,
            expected_revision=int(approved["revision_no"]) + 1,
        )

    assert _revision_count(db, art_id) == after_first


# ── 拒绝临时替换修改内容（P4-A4）────────────────────────────────────────────


def test_apply_refuses_target_without_approved_changes(db):
    """快照里没有该目标的批准内容 ⇒ 拒绝，不「边应用边编」。"""
    env = _env(db)
    art = _artifact(db, env)
    art_id = int(art["artifact_id"])
    case = _case_with_artifact(db, env, art_id)
    case_id = int(case["id"])
    approved = _approve(db, case_id, basis_revision_id=_current_revision(db, art_id), changes=None)
    before = _revision_count(db, art_id)

    with pytest.raises(exc.ExceptionCaseError) as info:
        exc.apply_case(
            db,
            exception_id=case_id,
            actor_id=MANAGER,
            expected_revision=int(approved["revision_no"]),
        )
    assert "没有经过批准的修改内容" in str(info.value)
    assert _revision_count(db, art_id) == before
