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
from collections.abc import Mapping
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
# 发起 Agent 作业（会话内提交/推进/重试/取消）。与"看得到会话"分开：
# 只读成员能看见会话与历史消息，但**不能**让 Agent 干活。
PERM_AGENT_JOB = "entrust:agent:job"
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
        PERM_AGENT_JOB,
        PERM_MEMBER_MANAGE,
        PERM_ENTRUSTMENT_MANAGE,
    }
)

ORG_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # 组织所有者：含成员与授权管理
    "owner": ALL_PERMISSIONS,
    # 管理员：可管理成员与授权，但不能发布报价/派单/结算（业务动作需经理人角色）
    "admin": frozenset({PERM_VIEW, PERM_MEMBER_MANAGE, PERM_ENTRUSTMENT_MANAGE}),
    # 经理人：一线执行，可认领委托、制作并发布报价、派单、生成结算、发起 Agent 作业
    "manager": frozenset(
        {
            PERM_VIEW,
            PERM_ASSIGN_CLAIM,
            PERM_QUOTE_CREATE,
            PERM_QUOTE_PUBLISH,
            PERM_TASK_DISPATCH,
            PERM_SETTLEMENT_CREATE,
            PERM_AGENT_JOB,
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
    #: 授权归属的组织（`ent_entrustment.org_id`）。按组织判定权限时必须用到 ——
    #: 缺了它就无法回答"这条授权是给**哪个组织**的"。
    org_id: int
    permissions: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class AccessContext:
    """某用户的委托支线访问上下文。

    ## 权限的两个维度（**这是本模块最容易搞错的地方**）

    权限必须同时回答"**在哪个组织**"与"**对哪个货主**"：

    * `org_permissions[org_id]` —— 按组织细分的权限（该组织内的成员角色权限，
      加上**授予该组织**的生效授权权限）；
    * `delegations` —— 按货主细分的授权（**某货主**把权限授予了某组织）。

    因此 `can()` **必须**指定其中一个维度：`org_id=` 或 `owner_user_id=`。

    ## 为什么不允许"不带作用域"的判定

    早期版本把 `permissions` 做成**跨组织并集**，且 `can(perm)` 允许不传作用域。
    后果是真实的越权（2026-09-13 复现）：

        用户 U 在组织 A 是 manager（有 `entrust:assignment:claim`），
        在组织 B 只是 member（只有 `entrust:view`）。
        判定"U 能否认领组织 B 的委托单"时用的是**并集** → 通过 → U 认领成功。

    只要还有人能写出"不指定组织"的判定，这类漏洞就会随每个新端点复发。
    所以这里把**不带作用域的判定变成错误**（`ValueError`），把约束交给运行时，
    而不是交给"记得传参数"。

    ## `permissions` 字段

    跨组织并集，**仅供诊断与"是否有任何组织身份"判断**，
    **不得**用于任何单个对象的授权判定（`can()` 已不接受无作用域调用）。
    """

    user_id: int
    org_ids: frozenset[int] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    #: 按组织细分的权限：`{org_id: frozenset[权限代码]}`。
    org_permissions: Mapping[int, frozenset[str]] = field(default_factory=dict)
    delegations: tuple[Delegation, ...] = ()
    evaluated_at: datetime = field(default_factory=utcnow_naive)

    def can(
        self,
        permission: str,
        *,
        org_id: int | None = None,
        owner_user_id: int | None = None,
    ) -> bool:
        """是否具备某项权限。**至少**指定 `org_id` / `owner_user_id` 之一。

        两个都给出时按 **AND** 判定（更严）：既要在该组织内有该权限，
        又要有该货主的生效授权提供它。调用方能在手上拿到两个维度时就该给两个 ——
        `assert_can_write_entrustment` 场景下委托授权的 `org_id` 与 `entrust_user_id`
        都是现成的。

        Args:
            permission: 权限代码。
            org_id: 在**该组织内**是否具备该权限（成员角色权限 ∪ 授予该组织的授权）。
            owner_user_id: 是否有**该货主**的生效授权提供该权限。

        Raises:
            ValueError: 两个作用域都未给出。不指定作用域会退化成跨组织并集判定，
                已导致过真实越权（见类文档），所以这里**报错而不是放行**。
        """
        if org_id is None and owner_user_id is None:
            raise ValueError(
                "ctx.can() 必须指定作用域：org_id（组织内权限）或 owner_user_id（该货主的授权）。"
                "不指定作用域会退化成跨组织并集判定，已导致过真实越权（见类文档）。"
            )
        if org_id is not None and permission not in self.org_permissions.get(
            int(org_id), frozenset()
        ):
            return False
        return not (owner_user_id is not None and not self._delegated_by(owner_user_id, permission))

    def _delegated_by(self, owner_user_id: int, permission: str) -> bool:
        """是否有该货主的生效授权提供该权限（与组织无关，授权归货主所有）。"""
        return any(
            d.owner_user_id == owner_user_id and permission in d.permissions
            for d in self.delegations
        )

    def can_in_org(self, org_id: int, permission: str) -> bool:
        """`can(permission, org_id=...)` 的可读别名（调用点意图更直白）。"""
        return self.can(permission, org_id=org_id)

    def permissions_in_org(self, org_id: int) -> frozenset[str]:
        """某组织内的全部权限（供组织选择器展示"我在这个组织能做什么"）。"""
        return self.org_permissions.get(int(org_id), frozenset())


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
    # 按组织细分：这是授权判定的**主维度**；`permissions`（并集）仅供诊断。
    org_permissions: dict[int, set[str]] = {}
    for row in rows:
        org_id = int(row["org_id"])
        org_ids.add(org_id)
        role_perms = ORG_ROLE_PERMISSIONS.get(str(row["member_role"]), frozenset())
        permissions |= role_perms
        org_permissions.setdefault(org_id, set()).update(role_perms)

    delegations: tuple[Delegation, ...] = ()
    if org_ids:
        delegations = _active_delegations(session, org_ids=org_ids, now=current)

    for delegation in delegations:
        permissions |= delegation.permissions
        # 授权是授予**某个组织**的，只加进那个组织的权限集合 —— 这正是修复越权的关键：
        # 组织 A 的经理角色不能让他在组织 B 获得任何权限。
        org_permissions.setdefault(delegation.org_id, set()).update(delegation.permissions)

    return AccessContext(
        user_id=user_id,
        org_ids=frozenset(org_ids),
        permissions=frozenset(permissions),
        org_permissions={oid: frozenset(p) for oid, p in org_permissions.items()},
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
            # org_id 必须取出来：授权是给**某个组织**的，按组织判定权限时要靠它归位。
            "SELECT id, org_id, entrust_user_id, permissions, valid_from, valid_until "
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
                org_id=int(row["org_id"]),
                permissions=_load_permissions(row["permissions"]),
            )
        )
    return tuple(result)


def assert_can(
    session: Session,
    *,
    user_id: int,
    permission: str,
    org_id: int | None = None,
    owner_user_id: int | None = None,
    now: datetime | None = None,
) -> AccessContext:
    """校验权限，不足则抛 `AccessDeniedError`。**必须**指定 `org_id` 或 `owner_user_id`。

    Args:
        org_id: 校验"在该组织内是否具备该权限"（成员角色权限 ∪ 授予该组织的授权）。
        owner_user_id: 校验"是否由**该货主**的生效授权提供该权限" —— 这是防止
            "拿到 A 的授权去操作 B 的数据"的关键检查。

    Raises:
        ValueError: 未指定作用域（见 `AccessContext.can` 的说明：无作用域的判定
            会退化成跨组织并集，已导致过真实越权）。
    """
    context = resolve_context(session, user_id=user_id, now=now)
    if not context.can(permission, org_id=org_id, owner_user_id=owner_user_id):
        scope = ""
        if org_id is not None:
            scope += f"（作用于组织 {org_id}）"
        if owner_user_id is not None:
            scope += f"（作用于货主 {owner_user_id}）"
        raise AccessDeniedError(f"用户 {user_id} 缺少权限 {permission}{scope}")
    return context
