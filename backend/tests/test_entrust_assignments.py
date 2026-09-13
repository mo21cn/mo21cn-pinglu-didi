"""委托受理链路测试（ENT-005）。

三组断言：
1. **服务层状态机**：draft→submitted→claimed / cancel，非法迁移被拒；
2. **受理约束**：提交需要生效授权（服务端校验）、认领原子（AC-03）、
   乐观锁 409（AC-11）、未知数量保持 NULL（PRD 5.1）；
3. **API 层**：特性开关 404（AC-22）、幂等键语义（ENT-002）、
   非参与方 404 不泄漏存在性（PRD 9）。
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust.access import (  # noqa: E402
    AccessDeniedError,
    utcnow_naive,
)

_TS = "%Y-%m-%d %H:%M:%S"


@pytest.fixture()
def session():
    """独立 SQLite 内存库会话，`ent_` 表由迁移创建。"""
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 可直接造数据的会话工厂，且 ENTRUST_ENABLED=True。

    conftest.client 不暴露引擎，本支线 API 测试需要为组织/授权播种，
    所以在这里搭一份等价环境并同时交出 client 与 db 会话。
    """
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
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", True)

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _org(db, name="测试组织", status="active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": name, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager", status: str = "active") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, :s, :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()


def _entrust(
    db,
    org_id: int,
    owner_id: int,
    permissions: str = '["entrust:view"]',
    status: str = "active",
    valid_from=None,
    valid_until=None,
) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " valid_from, valid_until, created_at) VALUES (:o, :u, :p, :s, :f, :t, :c)"
        ),
        {
            "o": org_id,
            "u": owner_id,
            "p": permissions,
            "s": status,
            "f": valid_from.strftime(_TS) if valid_from else None,
            "t": valid_until.strftime(_TS) if valid_until else None,
            "c": utcnow_naive().strftime(_TS),
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


def _submitted(db, owner_id: int, org_id: int) -> dict:
    a = svc.create_assignment(db, owner_user_id=owner_id, title="钢材运输")
    return svc.submit_assignment(
        db,
        assignment_id=a["assignment_id"],
        actor_id=owner_id,
        org_id=org_id,
        expected_revision=a["revision"],
    )


# ─────────────────────────────────────────── 服务层：创建/编辑/未知值


def test_create_draft_minimal(session):
    """草稿允许不完整：只给标题，其余字段为空且 org 未选。"""
    a = svc.create_assignment(session, owner_user_id=1, title="  钢材运输  ")
    assert a["status"] == svc.STATUS_DRAFT
    assert a["revision"] == 1
    assert a["title"] == "钢材运输"  # 去首尾空白
    assert a["org_id"] is None
    assert a["cargo_summary"] is None
    assert a["quantity"] is None
    assert a["quantity_unit"] is None


def test_unknown_quantity_stays_null(session):
    """未知数量保持 NULL，绝不静默补 0（PRD 5.1）。"""
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    got = svc.get_assignment(session, a["assignment_id"])
    assert got is not None
    assert got["quantity"] is None
    assert got["quantity_unit"] is None


def test_create_rejects_blank_title(session):
    with pytest.raises(svc.AssignmentError):
        svc.create_assignment(session, owner_user_id=1, title="   ")


def test_update_draft_bumps_revision(session):
    a = svc.create_assignment(session, owner_user_id=1, title="t", quantity_unit="吨")
    updated = svc.update_draft(
        session,
        assignment_id=a["assignment_id"],
        actor_id=1,
        expected_revision=1,
        cargo_summary="螺纹钢 800 吨",
        quantity="800",
    )
    assert updated["revision"] == 2
    assert updated["cargo_summary"] == "螺纹钢 800 吨"
    assert str(updated["quantity"]).startswith("800")
    assert updated["title"] == "t"  # 未提供字段保持原值


def test_update_stale_revision_conflicts(session):
    """AC-11：expected_revision 过期 → 409 语义异常。"""
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    svc.update_draft(
        session,
        assignment_id=a["assignment_id"],
        actor_id=1,
        expected_revision=1,
        cargo_summary="v2",
    )
    with pytest.raises(svc.RevisionConflictError):
        svc.update_draft(
            session,
            assignment_id=a["assignment_id"],
            actor_id=1,
            expected_revision=1,
            cargo_summary="v3",
        )


def test_update_non_draft_rejected(session):
    org = _org(session)
    _entrust(session, org, owner_id=1)
    a = _submitted(session, 1, org)
    with pytest.raises(svc.AssignmentStateError):
        svc.update_draft(
            session,
            assignment_id=a["assignment_id"],
            actor_id=1,
            expected_revision=a["revision"],
            cargo_summary="late edit",
        )


def test_update_by_non_owner_is_404(session):
    """非创建人编辑 → NotFound（不区分"不存在"与"无权"）。"""
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    with pytest.raises(svc.AssignmentNotFoundError):
        svc.update_draft(
            session,
            assignment_id=a["assignment_id"],
            actor_id=2,
            expected_revision=1,
            cargo_summary="x",
        )


# ─────────────────────────────────────────── 服务层：提交与授权门槛


def test_submit_requires_active_entrustment(session):
    org = _org(session)
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    with pytest.raises(AccessDeniedError):  # 无授权
        svc.submit_assignment(
            session,
            assignment_id=a["assignment_id"],
            actor_id=1,
            org_id=org,
            expected_revision=a["revision"],
        )
    _entrust(session, org, owner_id=1, status="revoked")
    with pytest.raises(AccessDeniedError):  # 已撤销
        svc.submit_assignment(
            session,
            assignment_id=a["assignment_id"],
            actor_id=1,
            org_id=org,
            expected_revision=a["revision"],
        )
    _entrust(session, org, owner_id=1)
    submitted = svc.submit_assignment(
        session,
        assignment_id=a["assignment_id"],
        actor_id=1,
        org_id=org,
        expected_revision=a["revision"],
    )
    assert submitted["status"] == svc.STATUS_SUBMITTED
    assert submitted["org_id"] == org
    assert submitted["submitted_at"] is not None


def test_submit_window_not_started_denied(session):
    """授权存在但窗口未开始 → 提交被拒（窗口校验按条授权逐条生效）。"""
    org = _org(session)
    _entrust(session, org, owner_id=1, valid_from=utcnow_naive() + timedelta(days=1))
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    with pytest.raises(AccessDeniedError):
        svc.submit_assignment(
            session,
            assignment_id=a["assignment_id"],
            actor_id=1,
            org_id=org,
            expected_revision=a["revision"],
        )


def test_submit_suspended_org_denied(session):
    """组织停用 → 授权链路整体失效，提交被拒。"""
    org = _org(session, status="suspended")
    _entrust(session, org, owner_id=1)
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    with pytest.raises(AccessDeniedError):
        svc.submit_assignment(
            session,
            assignment_id=a["assignment_id"],
            actor_id=1,
            org_id=org,
            expected_revision=a["revision"],
        )


def test_submit_by_non_owner_is_404(session):
    org = _org(session)
    _entrust(session, org, owner_id=99)
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    with pytest.raises(svc.AssignmentNotFoundError):
        svc.submit_assignment(
            session,
            assignment_id=a["assignment_id"],
            actor_id=2,
            org_id=org,
            expected_revision=a["revision"],
        )


# ─────────────────────────────────────────── 服务层：原子认领（AC-03）


def test_claim_by_manager_succeeds(session):
    org = _org(session)
    _member(session, org, user_id=10, role="manager")
    _entrust(session, org, owner_id=1)
    a = _submitted(session, 1, org)
    claimed = svc.claim_assignment(session, assignment_id=a["assignment_id"], actor_id=10)
    assert claimed["status"] == svc.STATUS_CLAIMED
    assert claimed["claimed_by"] == 10
    assert claimed["claimed_at"] is not None


def test_double_claim_conflicts(session):
    """双认领：第二个经理得到冲突，第一个的认领不被覆盖。"""
    org = _org(session)
    _member(session, org, user_id=10, role="manager")
    _member(session, org, user_id=11, role="manager")
    _entrust(session, org, owner_id=1)
    a = _submitted(session, 1, org)
    first = svc.claim_assignment(session, assignment_id=a["assignment_id"], actor_id=10)
    with pytest.raises(svc.AssignmentStateError):
        svc.claim_assignment(session, assignment_id=a["assignment_id"], actor_id=11)
    still = svc.get_assignment(session, a["assignment_id"])
    assert still is not None and still["claimed_by"] == first["claimed_by"] == 10


def test_claim_requires_membership_and_permission(session):
    org = _org(session)
    other_org = _org(session, name="另一组织")
    _member(session, other_org, user_id=20, role="manager")  # 别的组织的经理
    _member(session, org, user_id=21, role="member")  # 本组织但只读
    _entrust(session, org, owner_id=1)
    a = _submitted(session, 1, org)
    with pytest.raises(AccessDeniedError):
        svc.claim_assignment(session, assignment_id=a["assignment_id"], actor_id=20)
    with pytest.raises(AccessDeniedError):
        svc.claim_assignment(session, assignment_id=a["assignment_id"], actor_id=21)


def test_claim_requires_submitted_state(session):
    org = _org(session)
    _member(session, org, user_id=10, role="manager")
    _entrust(session, org, owner_id=1)
    a = svc.create_assignment(session, owner_user_id=1, title="t")
    with pytest.raises(svc.AssignmentStateError):  # draft 不能认领
        svc.claim_assignment(session, assignment_id=a["assignment_id"], actor_id=10)


# ─────────────────────────────────────────── 服务层：撤回与列表


def test_cancel_from_draft_and_submitted(session):
    org = _org(session)
    _entrust(session, org, owner_id=1)
    a = _submitted(session, 1, org)
    cancelled = svc.cancel_assignment(session, assignment_id=a["assignment_id"], actor_id=1)
    assert cancelled["status"] == svc.STATUS_CANCELLED
    assert cancelled["cancelled_at"] is not None

    b = svc.create_assignment(session, owner_user_id=1, title="t2")
    assert (
        svc.cancel_assignment(session, assignment_id=b["assignment_id"], actor_id=1)["status"]
        == svc.STATUS_CANCELLED
    )


def test_cancel_claimed_rejected(session):
    org = _org(session)
    _member(session, org, user_id=10, role="manager")
    _entrust(session, org, owner_id=1)
    a = _submitted(session, 1, org)
    svc.claim_assignment(session, assignment_id=a["assignment_id"], actor_id=10)
    with pytest.raises(svc.AssignmentStateError):
        svc.cancel_assignment(session, assignment_id=a["assignment_id"], actor_id=1)


def test_list_requires_visibility_boundary(session):
    """不给任何视角过滤条件 → 空集（列表必须有明确可见性边界）。"""
    assert svc.list_assignments(session) == (0, [])


def test_list_by_owner_and_org(session):
    org = _org(session)
    _org(session, name="别的组织")
    _entrust(session, org, owner_id=1)
    a = _submitted(session, 1, org)
    svc.create_assignment(session, owner_user_id=2, title="他人的委托")

    total, items = svc.list_assignments(session, owner_user_id=1)
    assert total == 1 and items[0]["assignment_id"] == a["assignment_id"]

    total, items = svc.list_assignments(session, org_id=org)
    assert total == 1 and items[0]["status"] == svc.STATUS_SUBMITTED

    total, _ = svc.list_assignments(session, owner_user_id=1, status=svc.STATUS_DRAFT)
    assert total == 0


# ─────────────────────────────────────────── API 层


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['access_token']}"}


def test_api_disabled_returns_404(client, shipper):
    """AC-22：开关关闭 → 整组端点 404（隐藏入口，不是权限拒绝）。"""
    resp = client.get("/api/v1/entrust/assignments", headers=shipper["_headers"])
    assert resp.status_code == 404


def test_api_create_requires_idempotency_key(env):
    resp = env.client.post(
        "/api/v1/entrust/assignments",
        json={"title": "t"},
        headers=_headers(_login(env.client, "shipper")),
    )
    assert resp.status_code == 400


def test_api_create_and_replay(env):
    owner = _login(env.client, "shipper")
    body = {
        "title": "钢材运输",
        "cargo_summary": "螺纹钢",
        "quantity": "800",
        "quantity_unit": "吨",
    }
    r1 = env.client.post(
        "/api/v1/entrust/assignments",
        json=body,
        headers={**_headers(owner), "Idempotency-Key": "k-create-1"},
    )
    assert r1.status_code == 200, r1.text
    first = r1.json()
    assert first["status"] == "draft" and first["quantity"] is not None

    r2 = env.client.post(
        "/api/v1/entrust/assignments",
        json=body,
        headers={**_headers(owner), "Idempotency-Key": "k-create-1"},
    )
    assert r2.status_code == 200
    assert r2.json()["assignment_id"] == first["assignment_id"]  # 重放，不新建


def test_api_same_key_different_body_conflicts(env):
    owner = _login(env.client, "shipper")
    h = {**_headers(owner), "Idempotency-Key": "k-dup"}
    r1 = env.client.post("/api/v1/entrust/assignments", json={"title": "a"}, headers=h)
    assert r1.status_code == 200
    r2 = env.client.post("/api/v1/entrust/assignments", json={"title": "b"}, headers=h)
    assert r2.status_code == 409


def test_api_detail_hidden_from_strangers(env):
    """非参与方看详情 → 404，不区分"不存在"与"无权"。"""
    owner = _login(env.client, "shipper")
    stranger = _login(env.client, "shipper")
    created = env.client.post(
        "/api/v1/entrust/assignments",
        json={"title": "t"},
        headers={**_headers(owner), "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    resp = env.client.get(
        f"/api/v1/entrust/assignments/{created['assignment_id']}", headers=_headers(stranger)
    )
    assert resp.status_code == 404
    mine = env.client.get(
        f"/api/v1/entrust/assignments/{created['assignment_id']}", headers=_headers(owner)
    )
    assert mine.status_code == 200


def test_api_full_intake_flow_and_double_claim(env):
    """API 全链路：创建 → 提交 → 经理认领 → 第二个经理 409。"""
    db = env.make_session()
    owner = _login(env.client, "shipper")
    m1 = _login(env.client, "mgr")
    m2 = _login(env.client, "mgr")

    org = _org(db)
    _member(db, org, user_id=m1["user_id"], role="manager")
    _member(db, org, user_id=m2["user_id"], role="manager")
    _entrust(db, org, owner_id=owner["user_id"])

    created = env.client.post(
        "/api/v1/entrust/assignments",
        json={"title": "钢材运输"},
        headers={**_headers(owner), "Idempotency-Key": uuid.uuid4().hex},
    ).json()

    submitted = env.client.post(
        f"/api/v1/entrust/assignments/{created['assignment_id']}/submit",
        json={"expected_revision": created["revision"], "org_id": org},
        headers={**_headers(owner), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    claim1 = env.client.post(
        f"/api/v1/entrust/assignments/{created['assignment_id']}/claim",
        headers={**_headers(m1), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert claim1.status_code == 200, claim1.text
    assert claim1.json()["claimed_by"] == m1["user_id"]

    claim2 = env.client.post(
        f"/api/v1/entrust/assignments/{created['assignment_id']}/claim",
        headers={**_headers(m2), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert claim2.status_code == 409
    assert claim2.json()["detail"], "冲突必须带可读原因"


def test_api_patch_stale_revision_conflicts(env):
    owner = _login(env.client, "shipper")
    created = env.client.post(
        "/api/v1/entrust/assignments",
        json={"title": "t"},
        headers={**_headers(owner), "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    ok = env.client.patch(
        f"/api/v1/entrust/assignments/{created['assignment_id']}",
        json={"expected_revision": created["revision"], "cargo_summary": "v2"},
        headers=_headers(owner),
    )
    assert ok.status_code == 200
    stale = env.client.patch(
        f"/api/v1/entrust/assignments/{created['assignment_id']}",
        json={"expected_revision": created["revision"], "cargo_summary": "v3"},
        headers=_headers(owner),
    )
    assert stale.status_code == 409


def test_api_org_view_requires_membership(env):
    """组织视角列表：非成员看到空集，成员看到委托。"""
    db = env.make_session()
    owner = _login(env.client, "shipper")
    insider = _login(env.client, "mgr")
    outsider = _login(env.client, "mgr")

    org = _org(db)
    _member(db, org, user_id=insider["user_id"], role="manager")
    _entrust(db, org, owner_id=owner["user_id"])
    a = _submitted(db, owner["user_id"], org)

    empty = env.client.get(
        "/api/v1/entrust/assignments",
        params={"view": "org", "org_id": org},
        headers=_headers(outsider),
    )
    assert empty.status_code == 200 and empty.json()["total"] == 0

    visible = env.client.get(
        "/api/v1/entrust/assignments",
        params={"view": "org", "org_id": org},
        headers=_headers(insider),
    )
    assert visible.status_code == 200
    assert visible.json()["total"] == 1
    assert visible.json()["items"][0]["assignment_id"] == a["assignment_id"]
