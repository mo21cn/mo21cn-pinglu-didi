"""成果版本 API 测试（ENT-006）。

四组断言：
1. **写权限**：组织成员 + 按货主作用域授权；非组织成员 404 不泄漏存在性；
   授权失效（revoked）409；
2. **版本语义**：追加不改生效版本、确认绑定精确版本、作废后拒绝确认/追加
   （AC-12 前置）；人工产出不可被 Agent 替换已在服务层覆盖；
3. **读可见性**：货主本人可读不可写；陌生人 404；
4. **横切**：开关 404（AC-22）、幂等重放/缺键 400（ENT-002）。
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

_CREATE_URL = "/api/v1/entrust/entrustments/{eid}/artifacts"


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 播种会话，且 ENTRUST_ENABLED=True。"""
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
        {"n": name, "s": status, "c": "2026-09-13 00:00:00"},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": "2026-09-13 00:00:00"},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str, status: str = "active") -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, :s, :c)"
        ),
        {"o": org_id, "u": owner_id, "p": permissions, "s": status, "c": "2026-09-13 00:00:00"},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _seed_manager(env, db, permissions: str = '["entrust:quote:create", "entrust:quote:publish"]'):
    """播种组织 + 经理成员 + 货主授权，返回 (manager, org_id, entrustment_id)。"""
    manager = _login(env.client, "mgr")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=900, permissions=permissions)
    return manager, org, eid


def _create_artifact(env, manager: dict, eid: int, key: str | None = None, **overrides):
    body = {
        "artifact_type": "quote_parsed",
        "payload": {"carrier": "船东A", "rate": "38 元/吨", "missing": ["装卸费承担方"]},
        **overrides,
    }
    return env.client.post(
        _CREATE_URL.format(eid=eid),
        json=body,
        headers=_headers(manager, key or uuid.uuid4().hex),
    )


# ─────────────────────────────────────────── 创建与写权限


def test_create_artifact_by_delegated_manager(env):
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)

    resp = _create_artifact(env, manager, eid)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["artifact_type"] == "quote_parsed"
    assert data["status"] == "active"
    assert data["current_revision"]["revision_no"] == 1
    assert data["current_revision"]["source"] == "manual"
    assert data["current_revision"]["payload"]["carrier"] == "船东A"


def test_create_without_scoped_permission_403(env):
    """是组织成员，但授权里没有 quote:create → 403。"""
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db, permissions='["entrust:view"]')

    resp = _create_artifact(env, manager, eid)
    assert resp.status_code == 403


def test_create_by_non_member_404(env):
    """非该组织成员 → 404，不泄漏授权存在性。"""
    db = env.make_session()
    outsider = _login(env.client, "mgr")
    org = _org(db)
    _member(db, org, user_id=99999)  # 别人在这组织里，outsider 不在
    eid = _entrust(db, org, owner_id=900, permissions='["entrust:quote:create"]')

    resp = _create_artifact(env, outsider, eid)
    assert resp.status_code == 404


def test_create_on_revoked_entrustment_409(env):
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)
    db.execute(text("UPDATE ent_entrustment SET status = 'revoked' WHERE id = :eid"), {"eid": eid})
    db.commit()

    resp = _create_artifact(env, manager, eid)
    assert resp.status_code == 409


def test_void_requires_publish_permission(env):
    """作废用 quote:publish；授权只有 quote:create → 403。"""
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db, permissions='["entrust:quote:create"]')
    created = _create_artifact(env, manager, eid).json()

    resp = env.client.post(
        f"/api/v1/entrust/artifacts/{created['artifact_id']}/void",
        json={"reason": "不再使用"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 403


# ─────────────────────────────────────────── 版本语义


def test_append_then_confirm_binds_exact_revision(env):
    """追加 v2 不改生效版本；确认后生效版本精确绑定 v2；历史完整。"""
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)
    created = _create_artifact(env, manager, eid).json()
    aid = created["artifact_id"]

    appended = env.client.post(
        f"/api/v1/entrust/artifacts/{aid}/revisions",
        json={"payload": {"carrier": "船东A", "rate": "40 元/吨"}, "note": "修正费率"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert appended.status_code == 200, appended.text
    assert appended.json()["revision_no"] == 2
    assert appended.json()["superseding_current"] is False

    still_v1 = env.client.get(f"/api/v1/entrust/artifacts/{aid}", headers=_headers(manager)).json()
    assert still_v1["current_revision"]["revision_no"] == 1

    confirmed = env.client.post(
        f"/api/v1/entrust/artifacts/{aid}/confirm",
        json={"revision_no": 2},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["current_revision"]["revision_no"] == 2

    history = env.client.get(
        f"/api/v1/entrust/artifacts/{aid}/revisions", headers=_headers(manager)
    ).json()
    assert [r["revision_no"] for r in history["items"]] == [1, 2]


def test_confirm_unknown_revision_404(env):
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)
    created = _create_artifact(env, manager, eid).json()

    resp = env.client.post(
        f"/api/v1/entrust/artifacts/{created['artifact_id']}/confirm",
        json={"revision_no": 7},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


def test_void_blocks_confirm_and_append(env):
    """AC-12 前置：作废后服务端拒绝确认与追加；历史版本仍可读。"""
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)
    created = _create_artifact(env, manager, eid).json()
    aid = created["artifact_id"]

    voided = env.client.post(
        f"/api/v1/entrust/artifacts/{aid}/void",
        json={"reason": "报价已失效"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["status"] == "void"

    confirm_again = env.client.post(
        f"/api/v1/entrust/artifacts/{aid}/confirm",
        json={"revision_no": 1},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert confirm_again.status_code == 409

    append_again = env.client.post(
        f"/api/v1/entrust/artifacts/{aid}/revisions",
        json={"payload": {}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert append_again.status_code == 409

    history = env.client.get(
        f"/api/v1/entrust/artifacts/{aid}/revisions", headers=_headers(manager)
    )
    assert history.status_code == 200  # 审计视图仍可读


# ─────────────────────────────────────────── 读可见性


def test_owner_can_read_but_not_write(env):
    """货主本人：详情/版本历史可读（客户投影入口）；写操作 404（非组织成员）。"""
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db, permissions='["entrust:quote:create"]')
    owner = _login(env.client, "shipper")
    db.execute(
        text("UPDATE ent_entrustment SET entrust_user_id = :oid WHERE id = :eid"),
        {"oid": owner["user_id"], "eid": eid},
    )
    db.commit()
    created = _create_artifact(env, manager, eid).json()
    aid = created["artifact_id"]

    read = env.client.get(f"/api/v1/entrust/artifacts/{aid}", headers=_headers(owner))
    assert read.status_code == 200
    assert read.json()["current_revision"]["payload"]["carrier"] == "船东A"

    history = env.client.get(f"/api/v1/entrust/artifacts/{aid}/revisions", headers=_headers(owner))
    assert history.status_code == 200

    write_attempt = env.client.post(
        f"/api/v1/entrust/artifacts/{aid}/revisions",
        json={"payload": {"hacked": True}},
        headers=_headers(owner, uuid.uuid4().hex),
    )
    assert write_attempt.status_code == 404


def test_stranger_read_404(env):
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)
    stranger = _login(env.client, "shipper")
    created = _create_artifact(env, manager, eid).json()

    resp = env.client.get(
        f"/api/v1/entrust/artifacts/{created['artifact_id']}", headers=_headers(stranger)
    )
    assert resp.status_code == 404


# ─────────────────────────────────────────── 横切


def test_disabled_returns_404(client, shipper):
    """AC-22：开关关闭 → 404。"""
    resp = client.get("/api/v1/entrust/artifacts/1", headers=shipper["_headers"])
    assert resp.status_code == 404


def test_missing_idempotency_key_400(env):
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)

    resp = env.client.post(
        _CREATE_URL.format(eid=eid),
        json={"artifact_type": "x", "payload": {}},
        headers=_headers(manager),
    )
    assert resp.status_code == 400


def test_create_idempotent_replay(env):
    db = env.make_session()
    manager, org, eid = _seed_manager(env, db)

    r1 = _create_artifact(env, manager, eid, key="k-art-1")
    assert r1.status_code == 200
    r2 = _create_artifact(env, manager, eid, key="k-art-1")
    assert r2.status_code == 200
    assert r2.json()["artifact_id"] == r1.json()["artifact_id"]
