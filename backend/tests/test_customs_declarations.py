"""关务 D1 骨架测试（人工登记 + 状态时间线）。

四组断言，每条都能**失败**（只写"功能正常"不算验收标准）：

1. 服务层：派生状态、终态不被乱序事件顶回、重复登记冲突、越权写 403 等价物；
2. 留痕：事件只追加（更正不覆盖原记录）+ 每条事件带 `source` / `fetched_at`（AC-09）；
3. 可见性：A 看不到 B 的记录（AC-07 的"不能 200"）；
4. API：路由挂载、幂等键必填、登记 201 / 查询 / 详情 / 追加事件。

另有一组覆盖 `sources`：默认官方源不可用、人工登记可用 —— 这是演示里"数据源状态条"
的数据源（AC-02 的显式呈现），也是 Q-02a 决定在代码里的落点。
以及 Q-02a 的**实现层守卫**：开关 + 真实适配器**同时存在**才允许 `available`。
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


def test_sources_default(monkeypatch):
    """默认档：官方源不可用、人工登记可用（AC-02 的显式呈现）。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CUSTOMS_OFFICIAL_SOURCE_ENABLED", False)
    snap = src.snapshot()
    by_key = {s["key"]: s for s in snap["sources"]}
    assert by_key["single_window"]["state"] == "unavailable"
    assert by_key["manual"]["state"] == "available"
    assert snap["official_available"] is False


# ---------------------------------------------------------------------------
# Q-02a 的实现层守卫：**开关不能单独代表来源可用**
#
# 旧行为（已删除）：置 `CUSTOMS_OFFICIAL_SOURCE_ENABLED=true` 就让
# `single_window.state == "available"` —— 而背后没有任何数据源。
# 这正是"看起来有"，且恰好落在 Q-02a 的兑现机制上。
#
# 新判定式：`official_available == True ⇒ 存在真实适配器`。
# 下面四条是它的真值表，**每一条都能失败**（含一条正控，否则"恒 false"也能全绿）。
# ---------------------------------------------------------------------------


def test_switch_on_without_adapter_is_not_available(monkeypatch):
    """开关开了但一个适配器都没有 ⇒ 仍然是不可用，且**明说**是守卫拦截。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CUSTOMS_OFFICIAL_SOURCE_ENABLED", True)
    monkeypatch.setattr(src, "OFFICIAL_ADAPTERS", {})
    snap = src.snapshot()
    by_key = {s["key"]: s for s in snap["sources"]}
    assert snap["official_available"] is False, "开关单独不能把来源翻成可用（Q-02a 守卫）"
    assert by_key["single_window"]["state"] == "unavailable"
    assert by_key["single_window"]["reason"] == src.GUARD_REASON, by_key["single_window"]["reason"]
    assert "配置异常" in snap["notice"], snap["notice"]
    assert snap["official_missing_adapters"] == ["single_window", "manifest"]
    assert snap["official_enabled"] is True, "开关状态本身要如实报出来，不能被守卫吞掉"


def test_switch_off_with_adapter_still_unavailable(monkeypatch):
    """有适配器但授权未到（开关关）⇒ 仍然不可用。两个条件缺一都不行。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CUSTOMS_OFFICIAL_SOURCE_ENABLED", False)
    monkeypatch.setattr(src, "OFFICIAL_ADAPTERS", {"single_window": object()})
    snap = src.snapshot()
    by_key = {s["key"]: s for s in snap["sources"]}
    assert snap["official_available"] is False
    assert by_key["single_window"]["state"] == "unavailable"
    # 缺的是 manifest 那条 —— 报"缺哪些"，不只报一个总数
    assert snap["official_missing_adapters"] == ["manifest"]


def test_switch_on_with_adapters_is_available_positive_control(monkeypatch):
    """（正控）授权到位 + 适配器齐全 ⇒ 必须翻成可用。没有这条，"恒 false"也能全绿。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CUSTOMS_OFFICIAL_SOURCE_ENABLED", True)
    monkeypatch.setattr(
        src,
        "OFFICIAL_ADAPTERS",
        {"single_window": object(), "manifest": object()},
    )
    snap = src.snapshot()
    by_key = {s["key"]: s for s in snap["sources"]}
    assert snap["official_available"] is True
    assert by_key["single_window"]["state"] == "available"
    assert by_key["single_window"]["reason"] == "已按授权接入（配置开启 · 适配器已登记）"
    assert snap["official_missing_adapters"] == []


def test_no_ad_hoc_official_http_without_adapter():
    """静态守卫：关务模块里出现 HTTP 客户端 ⇒ 必须先在 `OFFICIAL_ADAPTERS` 登记。

    为什么要有这条：开关 + 适配器是**两个**条件，但只有"开关"这一个显式配置项。
    有人想偷偷接一个官方源时，最省事的做法是直接在 `sources.py` 里 `httpx.get(...)` ——
    那样绕过了登记表，`official_ready()` 仍然说"没有适配器"，两边的说法打架。
    本判据把这条路堵上：要么不写 HTTP，要么先登记适配器。
    """
    from pathlib import Path

    pkg = Path(src.__file__).parent
    http_hits = [
        p.name
        for p in sorted(pkg.glob("*.py"))
        if any(
            tok in p.read_text(encoding="utf-8")
            for tok in ("import httpx", "import requests", "aiohttp", "urllib.request")
        )
    ]
    if http_hits:
        assert src.OFFICIAL_ADAPTERS, (
            f"{http_hits} 里出现了 HTTP 客户端，但 OFFICIAL_ADAPTERS 为空 —— "
            "接入官方源必须先登记适配器，否则开关与实现会对不上"
        )


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
