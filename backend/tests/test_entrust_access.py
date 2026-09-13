"""组织成员与委托访问权叠加层测试。

两组断言：
1. **叠加层语义**：组织角色 / 委托授权 / 时间窗口 / 作用域 / 组织停用。
2. **AC-01 旧流程回归**：权限叠加不得改动既有 `current_role` 链路 —— 既有接口
   行为保持原样（含港口方角色门禁仍对非 port 角色返回 403）。
"""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust.access import (  # noqa: E402
    PERM_ASSIGN_CLAIM,
    PERM_MEMBER_MANAGE,
    PERM_QUOTE_PUBLISH,
    PERM_TASK_DISPATCH,
    PERM_VIEW,
    AccessDeniedError,
    assert_can,
    resolve_context,
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


def _org(db, name="测试组织", status="active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": name, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    # text() 插入在 SQLAlchemy 2.0 拿不到 inserted_primary_key，用 DBAPI 的 lastrowid
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager", status: str = "active") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, :s, :c)"
        ),
        {
            "o": org_id,
            "u": user_id,
            "r": role,
            "s": status,
            "c": utcnow_naive().strftime(_TS),
        },
    )
    db.commit()


def _entrust(
    db,
    org_id: int,
    owner_id: int,
    permissions: str,
    status: str = "active",
    valid_from=None,
    valid_until=None,
) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " valid_from, valid_until, created_at) "
            "VALUES (:o, :u, :p, :s, :f, :t, :c)"
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
    # text() 插入在 SQLAlchemy 2.0 拿不到 inserted_primary_key，用 DBAPI 的 lastrowid
    return int(result.lastrowid or 0)


# ─────────────────────────────────────────── 叠加层语义


def test_user_without_org_has_no_permissions(session):
    """无任何组织身份的用户：叠加层权限为空（不改动既有角色的前提）。"""
    ctx = resolve_context(session, user_id=1)
    assert ctx.permissions == frozenset()
    assert ctx.org_ids == frozenset()
    assert ctx.org_permissions == {}
    assert ctx.can(PERM_VIEW, org_id=1) is False


def test_member_role_grants_permissions(session):
    """组织成员按角色获得权限。"""
    org = _org(session)
    _member(session, org, user_id=10, role="manager")
    _member(session, org, user_id=11, role="member")

    ctx10 = resolve_context(session, user_id=10)
    ctx11 = resolve_context(session, user_id=11)

    assert ctx10.can(PERM_QUOTE_PUBLISH, org_id=org) is True
    assert ctx11.can(PERM_VIEW, org_id=org) is True
    assert ctx11.can(PERM_QUOTE_PUBLISH, org_id=org) is False


def test_admin_does_not_get_business_actions(session):
    """管理员能管成员，但不能发布报价（最小权限）。"""
    org = _org(session)
    _member(session, org, user_id=10, role="admin")

    ctx = resolve_context(session, user_id=10)
    assert ctx.can(PERM_MEMBER_MANAGE, org_id=org) is True
    assert ctx.can(PERM_QUOTE_PUBLISH, org_id=org) is False


def test_removed_member_loses_permissions(session):
    """成员被移除后权限立即失效。"""
    org = _org(session)
    _member(session, org, user_id=10, role="manager", status="removed")

    assert resolve_context(session, user_id=10).permissions == frozenset()


def test_suspended_organization_disables_members(session):
    """组织被停用 → 其成员的组织身份与授权全部不生效。"""
    org = _org(session, status="suspended")
    _member(session, org, user_id=10, role="owner")
    _entrust(session, org, owner_id=99, permissions='["entrust:quote:publish"]')

    ctx = resolve_context(session, user_id=10)
    assert ctx.permissions == frozenset()
    assert ctx.delegations == ()


def test_entrustment_grants_permission_with_scope(session):
    """委托授权按货主作用域生效：A 的授权不能用于 B。"""
    org = _org(session)
    _member(session, org, user_id=10, role="member")  # 只读角色
    _entrust(
        session, org, owner_id=100, permissions='["entrust:quote:publish", "entrust:task:dispatch"]'
    )

    ctx = resolve_context(session, user_id=10)
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=100) is True
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=200) is False


def test_entrustment_outside_window_is_ignored(session):
    """授权只在时间窗口内生效：未开始 / 已过期都不算数。"""
    now = utcnow_naive()
    org = _org(session)
    _member(session, org, user_id=10, role="member")

    _entrust(
        session,
        org,
        owner_id=101,
        permissions='["entrust:quote:publish"]',
        valid_from=now + timedelta(days=1),
    )
    _entrust(
        session,
        org,
        owner_id=102,
        permissions='["entrust:quote:publish"]',
        valid_until=now - timedelta(days=1),
    )
    _entrust(
        session,
        org,
        owner_id=103,
        permissions='["entrust:quote:publish"]',
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=1),
    )

    ctx = resolve_context(session, user_id=10, now=now)
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=101) is False
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=102) is False
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=103) is True


def test_revoked_entrustment_is_ignored(session):
    """被撤销/仍在草稿的授权不生效。"""
    org = _org(session)
    _member(session, org, user_id=10, role="member")
    _entrust(session, org, owner_id=104, permissions='["entrust:quote:publish"]', status="revoked")
    _entrust(session, org, owner_id=105, permissions='["entrust:quote:publish"]', status="draft")

    ctx = resolve_context(session, user_id=10)
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=104) is False
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=105) is False


def test_unknown_permission_code_is_dropped(session):
    """授权里出现未知权限代码 → 丢弃（解析失败返回空集，不放行）。"""
    org = _org(session)
    _member(session, org, user_id=10, role="member")
    _entrust(session, org, owner_id=106, permissions='["entrust:god:mode", 42]')

    ctx = resolve_context(session, user_id=10)
    assert ctx.delegations[0].permissions == frozenset()


def test_assert_can_raises_without_permission(session):
    """`assert_can` 在权限不足时抛 AccessDenied，且带作用域信息。"""
    org = _org(session)
    _member(session, org, user_id=10, role="member")

    with pytest.raises(AccessDeniedError):
        assert_can(session, user_id=10, permission=PERM_QUOTE_PUBLISH, owner_user_id=100)

    # 按组织判定时，组织角色自带的权限可以通过
    assert_can(session, user_id=10, permission=PERM_VIEW, org_id=org)


# ─────────────────────────────────────────── 权限按组织细分（越权修复）

# 背景：早期 `permissions` 是**跨组织并集**，且 `can()` 允许不带作用域。
# 于是"在 A 组织是 manager、在 B 组织只是 member"的用户，判定"能否认领 B 的委托单"
# 时用并集 → 通过。2026-09-13 用真实数据复现了这个越权（见
# test_cross_org_claim_is_denied）。下面几条把"按组织细分"钉死。


def test_can_requires_an_explicit_scope(session):
    """`can()` 不带作用域必须报错 —— 无作用域判定会退化成跨组织并集。"""
    org = _org(session)
    _member(session, org, user_id=10, role="manager")
    ctx = resolve_context(session, user_id=10)

    with pytest.raises(ValueError, match="必须指定作用域"):
        ctx.can(PERM_VIEW)


def test_can_with_both_scopes_is_an_and(session):
    """同时给 org_id 与 owner_user_id 时按 AND 判定（更严，不会因多给而放行）。"""
    org = _org(session)
    _member(session, org, user_id=10, role="member")
    _entrust(session, org, owner_id=100, permissions='["entrust:quote:publish"]')

    ctx = resolve_context(session, user_id=10)
    # 组织内有该权限 + 该货主授权提供该权限 → 通过
    assert ctx.can(PERM_QUOTE_PUBLISH, org_id=org, owner_user_id=100) is True
    # 换一个没授权过的货主 → 不通过
    assert ctx.can(PERM_QUOTE_PUBLISH, org_id=org, owner_user_id=999) is False


def test_permissions_are_scoped_to_each_org(session):
    """同一用户在不同组织的权限互不串台 —— 这是越权修复的核心断言。"""
    org_a = _org(session, "组织A")
    org_b = _org(session, "组织B")
    _member(session, org_a, user_id=10, role="manager")  # A：经理
    _member(session, org_b, user_id=10, role="member")  # B：普通成员（只读）

    ctx = resolve_context(session, user_id=10)
    assert ctx.org_ids == frozenset({org_a, org_b})

    # A 组织：经理应有尽有
    assert ctx.can(PERM_ASSIGN_CLAIM, org_id=org_a) is True
    assert ctx.can(PERM_TASK_DISPATCH, org_id=org_a) is True
    # B 组织：只有只读 —— 修复前这里会被 A 的经理权限"顶"成 True
    assert ctx.can(PERM_VIEW, org_id=org_b) is True
    assert ctx.can(PERM_ASSIGN_CLAIM, org_id=org_b) is False
    assert ctx.can(PERM_TASK_DISPATCH, org_id=org_b) is False
    assert ctx.permissions_in_org(org_b) == frozenset({PERM_VIEW})

    # 并集字段保留（诊断/兼容），但已**不是**任何授权判定的依据
    assert PERM_ASSIGN_CLAIM in ctx.permissions


def test_delegation_only_counts_in_its_own_org(session):
    """授权是"授予某个组织"的：对 A 的授权不能在 B 生效。"""
    org_a = _org(session, "组织A")
    org_b = _org(session, "组织B")
    _member(session, org_a, user_id=10, role="member")
    _member(session, org_b, user_id=10, role="member")
    _entrust(session, org_a, owner_id=100, permissions='["entrust:quote:publish"]')

    ctx = resolve_context(session, user_id=10)
    assert ctx.can(PERM_QUOTE_PUBLISH, org_id=org_a) is True
    assert ctx.can(PERM_QUOTE_PUBLISH, org_id=org_b) is False
    # 按货主维度仍是全局的：授权归货主所有，与它落在哪个组织无关
    assert ctx.can(PERM_QUOTE_PUBLISH, owner_user_id=100) is True
    # Delegation 现在带 org_id，可回答"这条授权是给哪个组织的"
    assert [d.org_id for d in ctx.delegations] == [org_a]


def test_org_permissions_are_exposed_for_each_org(session):
    """`permissions_in_org` 按组织给出权限（组织选择器就靠它显示"我能做什么"）。"""
    org_a = _org(session, "组织A")
    org_b = _org(session, "组织B")
    _member(session, org_a, user_id=10, role="owner")
    _member(session, org_b, user_id=10, role="admin")

    ctx = resolve_context(session, user_id=10)
    assert PERM_QUOTE_PUBLISH in ctx.permissions_in_org(org_a)
    assert PERM_QUOTE_PUBLISH not in ctx.permissions_in_org(org_b)
    assert PERM_MEMBER_MANAGE in ctx.permissions_in_org(org_b)
    # 查一个与己无关的组织 → 空集（不报错、不泄漏）
    assert ctx.permissions_in_org(99999) == frozenset()


# ─────────────────────────────────────────── AC-01 旧流程回归


def test_legacy_auth_me_unchanged(client, shipper):
    """既有 `/api/v1/auth/me` 行为不变：角色仍是注册时的 shipper。"""
    resp = client.get("/api/v1/auth/me", headers=shipper["_headers"])
    assert resp.status_code == 200
    assert resp.json()["current_role"] == "shipper"


def test_legacy_cargo_list_unchanged(client, shipper):
    """既有货主列表接口不受新增 `ent_` 表影响。"""
    resp = client.get("/api/v1/cargo/shipments", headers=shipper["_headers"])
    assert resp.status_code == 200


def test_legacy_role_gate_still_rejects(client, shipper):
    """既有角色门禁未被削弱：非港口方访问泊位管理仍 403。

    入参要合法（否则会先卡在 422 校验，测不到角色门禁）；港口代码取自 schemas 的
    PORT_CODES，避免把具体港口硬编码进测试。
    """
    from app.modules.port.schemas import PORT_CODES

    resp = client.post(
        "/api/v1/port/berths",
        json={
            "port_code": sorted(PORT_CODES)[0],
            "berth_no": "A1",
            "max_dwt": 5000,
            "max_draft": 8.5,
        },
        headers=shipper["_headers"],
    )
    assert resp.status_code == 403
