"""关务 D1 骨架测试（人工登记 + 状态时间线）。

四组断言，每条都能**失败**（只写"功能正常"不算验收标准）：

1. 服务层：派生状态、终态不被乱序事件顶回、重复登记冲突、越权写 403 等价物；
2. 留痕：事件只追加（更正不覆盖原记录）+ 每条事件带 `source` / `fetched_at`（AC-09）；
3. 可见性：A 看不到 B 的记录（AC-07 的"不能 200"）；
4. API：路由挂载、幂等键必填、登记 201 / 查询 / 详情 / 追加事件。

另有一组覆盖 `sources`：默认官方源不可用、人工登记可用 —— 这是演示里"数据源状态条"
的数据源（AC-02 的显式呈现），也是 Q-02 决定在代码里的落点。
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.customs import declarations as svc  # noqa: E402
from app.modules.customs import sources as src  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"


@pytest.fixture()
def session():
    """独立 SQLite 内存库会话；`cus_` 两张表由迁移创建。"""
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
    """TestClient + 会话工厂（API 层用）。"""
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
    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _org(db, name: str = "测试组织") -> int:
    res = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": name, "c": svc.utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return int(res.lastrowid or 0)


def _join(db, org_id: int, user_id: int, role: str = "manager") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": svc.utcnow_naive().strftime(_TS)},
    )
    db.commit()


# ─────────────────────────────────────────────── 服务层


def test_create_derives_status_and_writes_first_event(session):
    detail = svc.create_declaration(
        session,
        user_id=101,
        org_ids=frozenset(),
        declaration_no="PL202610250001",
        status="declared",
        source="报关行口述",
    )
    assert detail["declaration"]["declaration_no"] == "PL202610250001"
    assert detail["derived_status"] == "declared"
    assert detail["declaration"]["status"] == "declared"
    assert len(detail["timeline"]) == 1
    ev = detail["timeline"][0]
    assert ev["status"] == "declared"
    assert ev["source"] == "报关行口述"
    assert ev["source_kind"] == "manual"
    assert ev["fetched_at"], "获取时间不可空（AC-09）"


def test_released_is_terminal_and_not_overridden_by_late_event(session):
    base = svc.utcnow_naive() - timedelta(days=2)
    detail = svc.create_declaration(
        session,
        user_id=102,
        org_ids=frozenset(),
        declaration_no="PL-REL-1",
        status="released",
        source="放行回执照片",
        occurred_at=base,
    )
    did = detail["declaration"]["id"]
    # 补录一条**更早发生**的查验事件：乱序到达不得把放行顶回去（PRD §4.4 规则 2）
    after = svc.append_event(
        session,
        user_id=102,
        org_ids=frozenset(),
        declaration_id=did,
        status="inspection",
        source="查验通知单（补录）",
        occurred_at=base - timedelta(days=1),
    )
    assert after["derived_status"] == "released"
    assert after["declaration"]["status"] == "released"
    assert len(after["timeline"]) == 2


def test_correction_appends_and_keeps_original(session):
    """AC-06：更正＝新增一条指向原事件，原记录不被更新/删除。"""
    detail = svc.create_declaration(
        session,
        user_id=103,
        org_ids=frozenset(),
        declaration_no="PL-COR-1",
        status="declared",
        source="报关行口述",
    )
    did = detail["declaration"]["id"]
    original = detail["timeline"][0]

    fixed = svc.append_event(
        session,
        user_id=103,
        org_ids=frozenset(),
        declaration_id=did,
        status="inspection",
        source="查验通知（更正：先前口述有误）",
        corrects_event_id=original["id"],
    )
    assert len(fixed["timeline"]) == 2
    kept = [e for e in fixed["timeline"] if e["id"] == original["id"]][0]
    assert kept["status"] == original["status"]
    assert kept["source"] == original["source"]
    assert fixed["timeline"][-1]["corrects_event_id"] == original["id"]


def test_duplicate_declaration_no_conflicts(session):
    kwargs = {
        "user_id": 104,
        "org_ids": frozenset(),
        "declaration_no": "PL-DUP-1",
        "status": "declared",
        "source": "报关行口述",
    }
    svc.create_declaration(session, **kwargs)
    with pytest.raises(svc.DeclarationConflictError):
        svc.create_declaration(session, **kwargs)


def test_org_write_requires_membership(session):
    org = _org(session, "甲方组织")
    with pytest.raises(svc.DeclarationForbiddenError):
        svc.create_declaration(
            session,
            user_id=105,
            org_ids=frozenset(),
            declaration_no="PL-ORG-1",
            status="declared",
            source="报关行口述",
            org_id=org,
        )


def test_other_owner_cannot_see(session):
    """AC-07：B 看不到 A 的记录（返回同一种"不存在"）。"""
    detail = svc.create_declaration(
        session,
        user_id=106,
        org_ids=frozenset(),
        declaration_no="PL-SEC-1",
        status="declared",
        source="报关行口述",
    )
    did = detail["declaration"]["id"]
    with pytest.raises(svc.DeclarationNotFoundError):
        svc.get_detail(session, user_id=107, org_ids=frozenset(), declaration_id=did)
    # 正控：本人拿得到
    assert (
        svc.get_detail(session, user_id=106, org_ids=frozenset(), declaration_id=did)[
            "declaration"
        ]["id"]
        == did
    )


def test_org_member_can_see_org_record(session):
    org = _org(session, "货代组织")
    detail = svc.create_declaration(
        session,
        user_id=108,
        org_ids={org},
        declaration_no="PL-ORG-2",
        status="declared",
        source="报关行回执",
        org_id=org,
    )
    did = detail["declaration"]["id"]
    # 同组织成员（manager）可见
    assert (
        svc.get_detail(session, user_id=109, org_ids={org}, declaration_id=did)["declaration"]["id"]
        == did
    )


def test_freshness_declares_official_source_unavailable(session):
    detail = svc.create_declaration(
        session,
        user_id=110,
        org_ids=frozenset(),
        declaration_no="PL-FRESH-1",
        status="declared",
        source="报关行口述",
    )
    fresh = detail["freshness"]
    assert fresh["official_state"] == "unavailable"
    assert fresh["official_state_label"] == "暂不可用"
    assert "人工登记" in fresh["message"]
    assert fresh["last_fetched_at"], "最后获取时间必须给出（AC-02/AC-09）"


def test_derive_status_without_events_is_unknown():
    assert svc.derive_status([]) == "unknown"
    assert svc.freshness([])["last_fetched_at"] is None


def test_sources_default_and_switch(monkeypatch):
    snap = src.snapshot()
    by_key = {s["key"]: s for s in snap["sources"]}
    assert by_key["single_window"]["state"] == "unavailable"
    assert by_key["manual"]["state"] == "available"
    assert snap["official_available"] is False

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CUSTOMS_OFFICIAL_SOURCE_ENABLED", True)
    snap2 = src.snapshot()
    by_key2 = {s["key"]: s for s in snap2["sources"]}
    assert by_key2["single_window"]["state"] == "available"
    assert snap2["official_available"] is True


# ─────────────────────────────────────────────── API 层


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


def _create(client, headers, **over) -> dict:
    body = {
        "declaration_no": f"PL-{uuid.uuid4().hex[:8].upper()}",
        "status": "declared",
        "source": "报关行口述",
    }
    body.update(over)
    resp = client.post(
        "/api/v1/customs/declarations",
        json=body,
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    return {"resp": resp, "body": body}


def test_api_routes_registered(env):
    """路由挂载断言走 openapi：本版 FastAPI 的 `app.routes` 里 include 进来的是
    惰性 `_IncludedRouter`（path 为 None），直接扫 `app.routes` 会**假绿**。"""
    paths = set(env.client.get("/openapi.json").json()["paths"])
    assert "/api/v1/customs/sources" in paths
    assert "/api/v1/customs/declarations" in paths
    assert "/api/v1/customs/declarations/{declaration_id}" in paths
    assert "/api/v1/customs/declarations/{declaration_id}/events" in paths


def test_api_requires_login(env):
    resp = env.client.get("/api/v1/customs/sources")
    assert resp.status_code == 401


def test_api_create_requires_idempotency_key(env):
    headers = _login(env.client, "cus-no-key")
    resp = env.client.post(
        "/api/v1/customs/declarations",
        json={"declaration_no": "PL-NOKEY", "status": "declared", "source": "报关行口述"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert "Idempotency" in resp.json()["detail"] or "幂等" in resp.json()["detail"]


def test_api_create_then_list_and_detail(env):
    headers = _login(env.client, "cus-a")
    created = _create(env.client, headers, declaration_no="PL-API-1")
    assert created["resp"].status_code == 201, created["resp"].text
    detail = created["resp"].json()
    did = detail["declaration"]["id"]
    assert detail["freshness"]["official_state"] == "unavailable"

    listed = env.client.get("/api/v1/customs/declarations?no=PL-API-1", headers=headers)
    assert listed.status_code == 200
    assert [i["declaration_no"] for i in listed.json()["items"]] == ["PL-API-1"]

    got = env.client.get(f"/api/v1/customs/declarations/{did}", headers=headers)
    assert got.status_code == 200
    assert got.json()["derived_status"] == "declared"

    missing = env.client.get("/api/v1/customs/declarations?no=NOT-EXIST", headers=headers)
    assert missing.status_code == 200
    assert missing.json()["items"] == [], "查不到就是空表，不得返回空壳成功记录"


def test_api_other_user_gets_404(env):
    owner = _login(env.client, "cus-owner")
    other = _login(env.client, "cus-other")
    did = _create(env.client, owner)["resp"].json()["declaration"]["id"]

    assert env.client.get(f"/api/v1/customs/declarations/{did}", headers=other).status_code == 404
    # 正控：本人 200
    assert env.client.get(f"/api/v1/customs/declarations/{did}", headers=owner).status_code == 200


def test_api_append_event_is_idempotent(env):
    headers = _login(env.client, "cus-ev")
    did = _create(env.client, headers)["resp"].json()["declaration"]["id"]
    key = uuid.uuid4().hex
    payload = {"status": "inspection", "source": "查验通知单照片"}

    first = env.client.post(
        f"/api/v1/customs/declarations/{did}/events",
        json=payload,
        headers={**headers, "Idempotency-Key": key},
    )
    second = env.client.post(
        f"/api/v1/customs/declarations/{did}/events",
        json=payload,
        headers={**headers, "Idempotency-Key": key},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert len(first.json()["timeline"]) == 2
    # 同键同体重放：时间线不会变成 3 条
    assert len(second.json()["timeline"]) == 2
