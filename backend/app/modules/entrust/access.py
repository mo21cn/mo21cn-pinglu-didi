"""组织成员与委托访问的权限解析（叠加层）。

设计前提（AGENTS.md 3.1，对应 AC-01 基线回归）
---------------------------------------------
既有鉴权是 **JWT + `User.current_role`**，被 8 个 router 与多个页面直接使用，
形如 `_require_role(user, "port")`。本模块**不替换、不改写**这条链路：

* 不修改 `User.current_role` 的取值与语义；
* 不参与既有 router 的角色判断；
* 只提供**叠加**能力：`resolve_permissions()` 返回"组织成员角色 + 委托授权"带来的
  权限集合，供**新增的委托接口**使用。

因此本模块对旧流程的影响是**零**：没有组织成员身份的用户，解析结果为空集，
既有接口的一切行为保持原样。

权限来源
--------
1. **组织成员角色**（`ent_org_member.member_role` → `ORG_ROLE_PERMISSIONS`）；
2. **委托授权**（`ent_entrustment.permissions`），且必须同时满足：
   * 授权状态为 `active`；
   * 当前时间在 `[valid_from, valid_until]` 窗口内（未设置端点视为不限）；
   * 操作者所属组织处于 `active`。

作用域
------
委托授权天然带作用域：授权是"某个货主把自己在委托上的操作权授予某组织"。
所以判断权限时要能带上 `owner_user_id` —— **对 A 货主的授权不能用来操作 B 货主的数据**。
`assert_can(..., owner_user_id=...)` 就是这个约束的落点。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# ── 权限代码 ────────────────────────────────────────────────────────────────
# 命名规则 `域:对象:动作`，与幂等 scope 保持一致，便于日志与审计对齐。

PERM_VIEW = "entrust:view"
PERM_ASSIGN_CLAIM = "entrust:assignment:claim"
PERM_QUOTE_CREATE = "entrust:quote:create"
PERM_QUOTE_PUBLISH = "entrust:quote:publish"
PERM_TASK_DISPATCH = "entrust:task:dispatch"
PERM_SETTLEMENT_CREATE = "entrust:settlement:create"
PERM_MEMBER_MANAGE = "org:member:manage"
PERM_ENTRUSTMENT_MANAGE = "org:entrustment:manage"

ALL_PERMISSIONS = frozenset(
    {
        PERM_VIEW,
        PERM_ASSIGN_CLAIM,
        PERM_QUOTE_CREATE,
        PERM_QUOTE_PUBLISH,
        PERM_TASK_DISPATCH,
        PERM_SETTLEMENT_CREATE,
        PERM_MEMBER_MANAGE,
        PERM_ENTRUSTMENT_MANAGE,
    }
)

ORG_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # 组织所有者：含成员与授权管理
    "owner": ALL_PERMISSIONS,
    # 管理员：可管理成员与授权，但不能发布报价/派单/结算（业务动作需经理人角色）
    "admin": frozenset({PERM_VIEW, PERM_MEMBER_MANAGE, PERM_ENTRUSTMENT_MANAGE}),
    # 经理人：一线执行，可认领委托、制作并发布报价、派单、生成结算
    "manager": frozenset(
        {
            PERM_VIEW,
            PERM_ASSIGN_CLAIM,
            PERM_QUOTE_CREATE,
            PERM_QUOTE_PUBLISH,
            PERM_TASK_DISPATCH,
            PERM_SETTLEMENT_CREATE,
        }
    ),
    # 普通成员：只读
    "member": frozenset({PERM_VIEW}),
}

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
STATUS_REMOVED = "removed"
STATUS_REVOKED = "revoked"
STATUS_DRAFT = "draft"

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


class AccessDeniedError(RuntimeError):
    """权限不足。HTTP 层应转成 403。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与表中时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FORMAT)


def _parse(raw: Any) -> datetime | None:
    """解析时间字段；空值或不可解析返回 None。"""
    if raw is None:
        return None
    value = str(raw).replace("T", " ").rstrip("Z")
    try:
        return datetime.strptime(value[:19], _TS_FORMAT)
    except ValueError:
        return None


def _load_permissions(raw: Any) -> frozenset[str]:
    """解析 `permissions` 列（JSON 数组文本）。

    未知代码一律丢弃，且**不静默报错为"全部权限"** —— 解析失败返回空集，
    让调用方走"无权限"分支，这比放行更安全。
    """
    if not raw:
        return frozenset()
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return frozenset()
    if not isinstance(data, list):
        return frozenset()
    return frozenset(p for p in data if isinstance(p, str)) & ALL_PERMISSIONS


@dataclass(frozen=True)
class Delegation:
    """一条生效中的委托授权。"""

    entrustment_id: int
    owner_user_id: int
    permissions: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class AccessContext:
    """某用户的委托支线访问上下文。

    `permissions` 是**叠加层**带来的权限；它不替代、也不修改 `current_role`。
    """

    user_id: int
    org_ids: frozenset[int] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    delegations: tuple[Delegation, ...] = ()
    evaluated_at: datetime = field(default_factory=utcnow_naive)

    def can(self, permission: str, *, owner_user_id: int | None = None) -> bool:
        """是否具备某项权限。

        Args:
            permission: 权限代码。
            owner_user_id: 数据归属的货主。给出时，权限必须由**该货主**的
                生效授权提供；不给则只检查组织成员角色带来的权限。
        """
        if owner_user_id is None:
            return permission in self.permissions
        return any(
            d.owner_user_id == owner_user_id and permission in d.permissions
            for d in self.delegations
        )


def resolve_context(
    session: Session, *, user_id: int, now: datetime | None = None
) -> AccessContext:
    """解析用户的组织成员身份与生效委托授权。"""
    current = now or utcnow_naive()

    rows = session.execute(
        text(
            "SELECT m.org_id AS org_id, m.member_role AS member_role "
            "FROM ent_org_member m "
            "JOIN ent_organization o ON o.id = m.org_id "
            "WHERE m.user_id = :user_id AND m.status = :m_status AND o.status = :o_status"
        ),
        {
            "user_id": user_id,
            "m_status": STATUS_ACTIVE,
            "o_status": STATUS_ACTIVE,
        },
    ).mappings()

    org_ids: set[int] = set()
    permissions: set[str] = set()
    for row in rows:
        org_id = int(row["org_id"])
        org_ids.add(org_id)
        permissions |= ORG_ROLE_PERMISSIONS.get(str(row["member_role"]), frozenset())

    delegations: tuple[Delegation, ...] = ()
    if org_ids:
        delegations = _active_delegations(session, org_ids=org_ids, now=current)

    for delegation in delegations:
        permissions |= delegation.permissions

    return AccessContext(
        user_id=user_id,
        org_ids=frozenset(org_ids),
        permissions=frozenset(permissions),
        delegations=delegations,
        evaluated_at=current,
    )


def _active_delegations(
    session: Session, *, org_ids: set[int], now: datetime
) -> tuple[Delegation, ...]:
    """查询生效中的委托授权：状态 active 且在当前时间窗口内。"""
    if not org_ids:
        return ()

    placeholders = ", ".join(f":org_{i}" for i in range(len(org_ids)))
    params: dict[str, Any] = {"status": STATUS_ACTIVE}
    params.update({f"org_{i}": org_id for i, org_id in enumerate(sorted(org_ids))})

    rows = session.execute(
        text(
            "SELECT id, entrust_user_id, permissions, valid_from, valid_until "
            "FROM ent_entrustment "
            f"WHERE status = :status AND org_id IN ({placeholders})"
        ),
        params,
    ).mappings()

    result: list[Delegation] = []
    for row in rows:
        valid_from = _parse(row["valid_from"])
        valid_until = _parse(row["valid_until"])
        if valid_from is not None and now < valid_from:
            continue
        # valid_until 按闭区间处理：到点当天仍视为有效，避免"最后一天失效"的歧义
        if valid_until is not None and now > valid_until + timedelta(seconds=1):
            continue
        result.append(
            Delegation(
                entrustment_id=int(row["id"]),
                owner_user_id=int(row["entrust_user_id"]),
                permissions=_load_permissions(row["permissions"]),
            )
        )
    return tuple(result)


def assert_can(
    session: Session,
    *,
    user_id: int,
    permission: str,
    owner_user_id: int | None = None,
    now: datetime | None = None,
) -> AccessContext:
    """校验权限，不足则抛 `AccessDenied`。

    Args:
        owner_user_id: 数据归属的货主。给出时要求**该货主**存在生效授权 —— 这是防止
            "拿到 A 的授权去操作 B 的数据"的关键检查。
    """
    context = resolve_context(session, user_id=user_id, now=now)
    if not context.can(permission, owner_user_id=owner_user_id):
        scope = f"（作用于货主 {owner_user_id}）" if owner_user_id is not None else ""
        raise AccessDeniedError(f"用户 {user_id} 缺少权限 {permission}{scope}")
    return context
