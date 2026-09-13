"""ENT-012 第二切片：`GET /my-orgs` —— 组织选择器的数据源。

为什么这组用例值得单独存在
--------------------------
这个端点存在的唯一理由是**打破多组织身份的死局**：
`GET /assignments?view=org` 在「属于多个组织且未指定 org_id」时返回 400，
而前端**无法自行枚举候选**（`current_role` 存本地、可篡改，且不表达组织成员关系）。

所以它必须同时满足两件事：

1. **列得全** —— 用户在任一 active 组织里的 active 成员身份都要出现。
   漏一个，界面就少一个可选项，用户会被"没有这个选项"卡死；
2. **不越界** —— 只列**调用者自己**的，且成员与组织都必须是 active。

第 2 点尤其需要独立用例盯住：为了不制造新的死局，这个端点的守卫只是
`authenticated`（**不要求业务权限**），因此"只列自己"这件事**完全靠 SQL 的
`WHERE m.user_id = :user_id` 兜住**，没有任何权限层帮忙兜底。
测试只测"正常路径返回了什么"是不够的，必须有"别人的组织不会出现"的用例。
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

from app.modules.entrust.access import (  # noqa: E402
    PERM_ASSIGN_CLAIM,
    PERM_VIEW,
    list_my_orgs,
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
    """TestClient + 可直接造数据的会话工厂，且 ENTRUST_ENABLED=True。"""
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


def _org(db, name: str = "测试组织", status: str = "active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": name, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager", status: str = "active") -> None:
    """注意 `ent_org_member` 有 (org_id, user_id) 唯一约束：同一组织只能有一条。"""
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, :s, :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str = '["entrust:view"]') -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " valid_from, valid_until, created_at) VALUES (:o, :u, :p, :s, :f, :t, :c)"
        ),
        {
            "o": org_id,
            "u": owner_id,
            "p": permissions,
            "s": "active",
            "f": None,
            "t": None,
            "c": utcnow_naive().strftime(_TS),
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


# ─────────────────────────────────────── 服务层


def test_lists_only_active_memberships(session):
    """组织非 active、或成员非 active，都不该出现在清单里。"""
    a = _org(session, name="组织A")
    b = _org(session, name="组织B")
    suspended_org = _org(session, name="已停用组织", status="suspended")
    c = _org(session, name="组织C")

    _member(session, a, 30, role="manager")
    _member(session, b, 30, role="member")
    _member(session, suspended_org, 30, role="manager")  # 组织 suspended → 排除
    _member(session, c, 30, role="manager", status="removed")  # 成员 removed → 排除

    orgs = list_my_orgs(session, user_id=30)

    assert [o["org_id"] for o in orgs] == [a, b]
    assert [o["name"] for o in orgs] == ["组织A", "组织B"]


def test_permissions_are_scoped_per_org(session):
    """A 组织是经理、B 组织只是成员 —— 权限不得跨组织并集。"""
    a = _org(session, name="组织A")
    b = _org(session, name="组织B")
    _member(session, a, 30, role="manager")
    _member(session, b, 30, role="member")

    by_id = {o["org_id"]: o for o in list_my_orgs(session, user_id=30)}

    assert PERM_ASSIGN_CLAIM in by_id[a]["permissions"]
    assert PERM_ASSIGN_CLAIM not in by_id[b]["permissions"]
    assert by_id[b]["permissions"] == [PERM_VIEW]


def test_member_role_is_reported(session):
    a = _org(session, name="组织A")
    _member(session, a, 30, role="admin")
    assert list_my_orgs(session, user_id=30)[0]["member_role"] == "admin"


def test_empty_for_user_without_membership(session):
    """没有任何组织身份 → 空清单（不是报错）。界面据此显示"还没有加入经营主体"。"""
    _org(session, name="组织A")
    assert list_my_orgs(session, user_id=99) == []


def test_does_not_leak_other_users_orgs(session):
    """守卫只是 authenticated，越界风险全靠 WHERE 兜住 —— 这条盯住它。"""
    a = _org(session, name="组织A")
    secret = _org(session, name="仅他人可见组织")
    _member(session, a, 30, role="manager")
    _member(session, secret, 31, role="owner")

    orgs = list_my_orgs(session, user_id=30)

    assert [o["org_id"] for o in orgs] == [a]
    assert all(o["name"] != "仅他人可见组织" for o in orgs)


def test_delegation_lands_in_its_own_org_only(session):
    """货主给组织 B 的授权只体现在 B 的权限里 —— 不得串到 A（DR-0008 的口径）。"""
    a = _org(session, name="组织A")
    b = _org(session, name="组织B")
    _member(session, a, 30, role="member")
    _member(session, b, 30, role="member")
    _entrust(
        session,
        b,
        owner_id=1,
        permissions='["entrust:view", "entrust:assignment:claim"]',
    )

    by_id = {o["org_id"]: o for o in list_my_orgs(session, user_id=30)}

    assert PERM_ASSIGN_CLAIM in by_id[b]["permissions"]
    assert PERM_ASSIGN_CLAIM not in by_id[a]["permissions"]


# ─────────────────────────────────────── API 层


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['access_token']}"}


def test_api_my_orgs_requires_auth(env):
    """未登录 → 401（不能靠"没登录就没有组织"来隐藏）。"""
    resp = env.client.get("/api/v1/entrust/my-orgs")
    assert resp.status_code == 401


def test_api_my_orgs_returns_own_orgs(env):
    db = env.make_session()
    manager = _login(env.client, "mgr")
    other = _login(env.client, "mgr")

    mine = _org(db, name="我的组织")
    theirs = _org(db, name="别人的组织")
    _member(db, mine, user_id=manager["user_id"], role="manager")
    _member(db, theirs, user_id=other["user_id"], role="owner")

    resp = env.client.get("/api/v1/entrust/my-orgs", headers=_headers(manager))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["org_id"] == mine
    assert row["name"] == "我的组织"
    assert row["member_role"] == "manager"
    assert PERM_ASSIGN_CLAIM in row["permissions"]


def test_api_my_orgs_is_empty_for_new_user(env):
    """新用户没有任何组织身份 → 200 + 空清单（不是 403/404）。"""
    fresh = _login(env.client, "newbie")
    resp = env.client.get("/api/v1/entrust/my-orgs", headers=_headers(fresh))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"total": 0, "items": []}


def test_api_my_orgs_404_when_disabled(env, monkeypatch):
    """AC-22：开关关闭时这个端点也不可达（与其他端点同口径）。"""
    from app.core.config import get_settings

    manager = _login(env.client, "mgr")
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    resp = env.client.get("/api/v1/entrust/my-orgs", headers=_headers(manager))
    assert resp.status_code == 404
