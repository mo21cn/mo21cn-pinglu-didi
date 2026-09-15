"""S1 / DEMO-1 §3.1：`GET /my-entrustments` —— UI-07 的**提交目标**数据源。

为什么这组用例值得单独存在
--------------------------
这个端点存在的唯一理由是补上一个**接口面缺口**：提交委托必须带 `org_id` 且服务端
校验"货主对该组织存在生效委托授权"（`assignments.submit_assignment` →
`owner_has_active_entrustment`），但 `ent_entrustment` 此前**没有任何 HTTP 面**，
货主无从知道"可以委托给谁"。

它和 `GET /my-orgs` **不是一回事**（DR-0012「归属 ≠ 权限边界」）：

* `/my-orgs` 读 `ent_org_member` —— 「我**所在**的组织」；
* 本端点读 `ent_entrustment` —— 「我**授权出去**的组织」。

因此本文件里最重要的不是"正常路径返回了什么"，而是下面两类断言：

1. **交叉断言**（`test_selection_is_exactly_the_submittable_set`）——
   列表给出的选项集合必须**恰好等于**提交门禁会放行的集合。
   多一个 ⇒ 界面出现"点了就 403"的选项；少一个 ⇒ 界面少一个合法选项，
   用户被"没有这个选项"卡死。这条断言是防两侧口径漂移的**唯一**机制。
2. **白名单投影**（`test_projection_is_whitelisted`）——
   只出契约里那 6 个字段，且 `permissions` 里的未知代码被丢弃；
   `ent_entrustment` 不得成为"把库里任意字符串透给前端"的通道。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
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
    list_my_entrustments,
    utcnow_naive,
)
from app.modules.entrust.assignments import owner_has_active_entrustment  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

#: 契约字段全集（`DEMO-1-interface-delta.md` §3.1 的"响应字段"行）。
#: 多一个字段就是越权投影 —— 见 `test_projection_is_whitelisted`。
_CONTRACT_KEYS = frozenset(
    {"entrustment_id", "org_id", "org_name", "permissions", "status", "granted_at"}
)


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


# ─────────────────────────────────────── 造数


def _now_second() -> datetime:
    """**秒对齐**的当前时间。

    窗口字段是秒精度（`access._parse` 把值截到 `[:19]`），若 `now` 带微秒，
    一个"`valid_until` 正好等于现在"的用例会因微秒被截掉而变成"已过期" ——
    边界就测不到了。所以凡是构造边界数据的用例都要用这个。
    """
    return utcnow_naive().replace(microsecond=0)


def _org(db, name: str, status: str = "active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": name, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _entrust(
    db,
    org_id: int,
    owner_id: int,
    *,
    permissions: str = '["entrust:view"]',
    status: str = "active",
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> int:
    """插一条委托授权。

    ⚠️ `valid_from` / `valid_until` 传的是**已格式化字符串**（或 None）：
    窗口边界要能精确控制到秒，才测得出边界行为。
    """
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
            "f": valid_from,
            "t": valid_until,
            "c": utcnow_naive().strftime(_TS),
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


# ─────────────────────────── 1. 交叉断言：选项集合 ≡ 门禁放行集合


def test_selection_is_exactly_the_submittable_set(session):
    """**本文件最重要的一条。**

    列表给出的组织集合必须**恰好等于**提交门禁会放行的集合：
    多一个 → 界面出现"点了就 403"的选项（DR-0012 要防的错误）；
    少一个 → 界面少一个合法选项，用户被"没有这个选项"卡死。

    这里一次性摆出五类边界：正常在窗内 / 授权已撤回 / 组织已停用 /
    `valid_from` 在未来 / `valid_until` 已过 / `valid_until` **正好等于 now**
    （闭边界，两侧都必须算"有效"）。
    """
    now = _now_second()
    owner = 7

    def ts(delta: timedelta) -> str:
        return (now + delta).strftime(_TS)

    ok = _org(session, "在窗内")
    revoked = _org(session, "授权已撤")
    suspended_org = _org(session, "组织已停用", status="suspended")
    not_yet = _org(session, "尚未生效")
    expired = _org(session, "已过期")
    boundary = _org(session, "刚好到点")

    _entrust(session, ok, owner)
    _entrust(session, revoked, owner, status="revoked")
    _entrust(session, suspended_org, owner)
    _entrust(session, not_yet, owner, valid_from=ts(timedelta(hours=1)))
    _entrust(session, expired, owner, valid_until=ts(timedelta(hours=-1)))
    _entrust(
        session,
        boundary,
        owner,
        valid_from=ts(timedelta(hours=-1)),
        valid_until=ts(timedelta(0)),
    )

    listed = {item["org_id"] for item in list_my_entrustments(session, user_id=owner, now=now)}
    gated = {
        org
        for org in (ok, revoked, suspended_org, not_yet, expired, boundary)
        if owner_has_active_entrustment(session, owner_user_id=owner, org_id=org, now=now)
    }

    assert listed == gated, (
        f"选项集合与门禁放行集合不一致：仅列表有 {listed - gated}，仅门禁有 {gated - listed}。"
        "多出的一侧会让界面给出必然 403 的选项，少的一侧会把合法路径藏起来。"
    )
    assert listed == {ok, boundary}, f"预期只有「在窗内」与「刚好到点」可选，实际 {listed}"


def test_boundary_valid_until_equal_to_now_is_selectable(session):
    """闭边界：`valid_until == now` 仍可选（与门禁同口径，1 秒都不能差）。"""
    now = _now_second()
    org = _org(session, "刚好到点")
    _entrust(
        session,
        org,
        7,
        valid_from=(now - timedelta(hours=1)).strftime(_TS),
        valid_until=now.strftime(_TS),
    )

    assert [i["org_id"] for i in list_my_entrustments(session, user_id=7, now=now)] == [org]
    assert owner_has_active_entrustment(session, owner_user_id=7, org_id=org, now=now) is True


# ─────────────────────────────────────── 2. 只列自己的


def test_lists_only_my_own_entrustments(session):
    """守卫只是 `authenticated`，越界风险全靠 `WHERE entrust_user_id` 兜住 —— 盯住它。"""
    mine = _org(session, "我授出去的")
    theirs = _org(session, "别人授出去的")
    _entrust(session, mine, 7)
    _entrust(session, theirs, 8)

    items = list_my_entrustments(session, user_id=7)

    assert [i["org_id"] for i in items] == [mine]
    assert all(i["org_name"] != "别人授出去的" for i in items)


def test_empty_for_user_without_entrustment(session):
    """没有任何授权出去 → 空清单（不是报错）。UI 据此禁用提交并给出去处。"""
    _org(session, "和我无关的组织")
    assert list_my_entrustments(session, user_id=99) == []


def test_membership_is_not_enough(session):
    """**归属 ≠ 权限边界**（DR-0012）：我是这个组织的成员，但没把委托授权给它。

    这条正是"不能拿 `/my-orgs` 顶上"的可执行证据：成员关系不产生可选项。
    """
    org = _org(session, "我是成员但没授权")
    session.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'manager', 'active', :c)"
        ),
        {"o": org, "u": 7, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()

    assert list_my_entrustments(session, user_id=7) == []


# ─────────────────────────────────────── 3. 投影与排序


def test_projection_is_whitelisted(session):
    """字段白名单 + 权限代码白名单：不得多投影、不得透传未知代码。"""
    org = _org(session, "有未知权限代码的组织")
    _entrust(
        session,
        org,
        7,
        permissions='["entrust:view", "entrust:assignment:claim", "evil:do:anything"]',
    )

    item = list_my_entrustments(session, user_id=7)[0]

    assert set(item) == _CONTRACT_KEYS, (
        f"投影字段与契约不符：多出 {set(item) - _CONTRACT_KEYS}，"
        f"缺少 {_CONTRACT_KEYS - set(item)}（契约见 DEMO-1-interface-delta.md §3.1）"
    )
    assert item["permissions"] == sorted([PERM_VIEW, PERM_ASSIGN_CLAIM])
    assert "evil:do:anything" not in item["permissions"], "未知权限代码不得透给前端"


def test_projection_carries_no_org_internals(session):
    """只投影授权本身：组织内部数据（成员/任务/成果）一个字段都不许带上。"""
    org = _org(session, "有内部数据的组织")
    session.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'owner', 'active', :c)"
        ),
        {"o": org, "u": 12345, "c": utcnow_naive().strftime(_TS)},
    )
    session.commit()
    _entrust(session, org, 7)

    item = list_my_entrustments(session, user_id=7)[0]

    assert item["org_name"] == "有内部数据的组织"
    assert set(item) == _CONTRACT_KEYS
    # 组织成员既不在顶层，也不在嵌套结构里
    assert "members" not in item
    assert 12345 not in [v for v in item.values() if isinstance(v, int)]


def test_sorted_by_org_name_ascending(session):
    """按 `org_name` 升序 —— 前端**不做二次排序**，顺序即契约。"""
    for name in ("丙组织", "甲组织", "乙组织"):
        _entrust(session, _org(session, name), 7)

    names = [i["org_name"] for i in list_my_entrustments(session, user_id=7)]

    assert names == sorted(names)


def test_sort_is_stable_for_same_name(session):
    """同名组织按 `entrustment_id` 兜底排序 —— 否则顺序不稳定，前端无法依赖。"""
    a = _org(session, "同名组织")
    b = _org(session, "同名组织")
    _entrust(session, a, 7)
    _entrust(session, b, 7)

    ids = [i["entrustment_id"] for i in list_my_entrustments(session, user_id=7)]

    assert ids == sorted(ids)


def test_reports_entrustment_id_and_status(session):
    """`entrustment_id` 必须在场 —— 「事实有来源」要求每条能回溯到 `ent_entrustment` 行。"""
    org = _org(session, "可回溯的组织")
    eid = _entrust(session, org, 7)

    item = list_my_entrustments(session, user_id=7)[0]

    assert item["entrustment_id"] == eid
    assert item["status"] == "active"
    assert item["granted_at"] != ""


# ─────────────────────────────────────── 4. API 层


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['access_token']}"}


def test_api_requires_auth(env):
    """未登录 → 401（不能靠"没登录就没有授权"来隐藏）。"""
    resp = env.client.get("/api/v1/entrust/my-entrustments")
    assert resp.status_code == 401


def test_api_returns_own_entrustments(env):
    db = env.make_session()
    owner = _login(env.client, "owner")
    other = _login(env.client, "owner")

    mine = _org(db, "我授给的组织")
    theirs = _org(db, "别人授给的组织")
    _entrust(db, mine, owner["user_id"], permissions='["entrust:view"]')
    _entrust(db, theirs, other["user_id"])

    resp = env.client.get("/api/v1/entrust/my-entrustments", headers=_headers(owner))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["org_id"] == mine
    assert body["items"][0]["org_name"] == "我授给的组织"
    assert body["items"][0]["permissions"] == [PERM_VIEW]


def test_api_is_empty_for_user_without_entrustment(env):
    """新用户没有任何授权出去 → 200 + 空清单（不是 403/404）。"""
    fresh = _login(env.client, "newbie")
    resp = env.client.get("/api/v1/entrust/my-entrustments", headers=_headers(fresh))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"total": 0, "items": []}


def test_api_404_when_disabled(env, monkeypatch):
    """AC-22：开关关闭时这个端点也不可达（与其他端点同口径）。"""
    from app.core.config import get_settings

    owner = _login(env.client, "owner")
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    resp = env.client.get("/api/v1/entrust/my-entrustments", headers=_headers(owner))
    assert resp.status_code == 404
