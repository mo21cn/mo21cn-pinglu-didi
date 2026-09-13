"""委托支线授权判定的**唯一入口**（AC-10 / 计划 §5.2 S1 第 8 条）。

问题
----
"权限落到每一个 API / 作业 / 附件下载 / 成果查询 / 会话上下文构造器"这句话，
最怕的是**每个接口各写一遍**：谁先写、谁后补，语义就漂了 —— 例如有人先判权限
再判可见性，于是"非参与方"得到 403（暴露了对象存在），而规范要求 404。

本模块把三件事固定成一条链，顺序不可调换：

1. **可见性**（谁是参与方）：货主本人 / 委托所属组织的 active 成员 → 否则 **404**；
2. **成员资格**（是不是"这个"组织的成员）：不是 → **404**（连对象都不该知道存在）；
3. **权限**（叠加层的动作权限，且必须由**该货主**的生效授权提供）→ 否则 **403**。

授权带作用域：对 A 货主的授权不能用来操作 B 货主的数据（`owner_user_id` 参数）。
`ENTRUST_ENABLED` 只管隐藏入口，**不构成访问控制** —— 开关打开后这里的每一层都必须过。

调用方约定
----------
本模块只做判定与读取，不写库、不碰 `current_role`。所有委托支线的读/写端点都应
经它取对象（`load_*` + `assert_*`），而不是自己拼 SQL 判权限。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.entrust.access import (
    PERM_VIEW,
    AccessContext,
    AccessDeniedError,
    resolve_context,
)

#: 委托授权（ent_entrustment）判定所需的列
ENTRUSTMENT_COLS = "id, org_id, entrust_user_id, status"
#: 委托单（ent_assignment）判定所需的列
ASSIGNMENT_COLS = "id, owner_user_id, org_id, status"

ENTRUSTMENT_ACTIVE = "active"


def load_entrustment(session: Session, entrustment_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {ENTRUSTMENT_COLS} FROM ent_entrustment WHERE id = :eid"),
            {"eid": entrustment_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def load_assignment(session: Session, assignment_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {ASSIGNMENT_COLS} FROM ent_assignment WHERE id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def not_found(detail: str = "对象不存在") -> HTTPException:
    """统一的 404 —— **不区分"不存在"与"无权知晓"**（不泄漏存在性）。"""
    return HTTPException(status_code=404, detail=detail)


def _ctx(session: Session, user_id: int) -> AccessContext:
    return resolve_context(session, user_id=user_id)


def assert_org_member(context: AccessContext, *, org_id: int | None, detail: str) -> None:
    """操作者必须是该对象所属组织的 active 成员；否则 404（不是 403）。"""
    if org_id is None or int(org_id) not in context.org_ids:
        raise not_found(detail)


def assert_can_view_entrustment(
    session: Session, *, user_id: int, entrustment: dict[str, Any], detail: str = "对象不存在"
) -> AccessContext:
    """读可见性：货主本人，或该授权所属组织的成员（且具备查看权限）。"""
    if user_id == int(entrustment["entrust_user_id"]):
        return _ctx(session, user_id)
    context = _ctx(session, user_id)
    assert_org_member(context, org_id=entrustment["org_id"], detail=detail)
    if not context.can(PERM_VIEW):
        raise not_found(detail)
    return context


def assert_can_write_entrustment(
    session: Session,
    *,
    user_id: int,
    permission: str,
    entrustment: dict[str, Any],
    detail: str = "对象不存在",
) -> AccessContext:
    """写权限：组织成员（否则 404）→ 授权 active（否则 409）→ 按货主作用域有权限（否则 403）。"""
    context = _ctx(session, user_id)
    assert_org_member(context, org_id=entrustment["org_id"], detail=detail)
    if str(entrustment["status"]) != ENTRUSTMENT_ACTIVE:
        raise HTTPException(status_code=409, detail="委托授权未生效或已失效，不能执行该操作")
    owner_user_id = int(entrustment["entrust_user_id"])
    if not context.can(permission, owner_user_id=owner_user_id):
        raise HTTPException(
            status_code=403,
            detail=f"用户 {user_id} 缺少权限 {permission}（作用于货主 {owner_user_id}）",
        )
    return context


def assert_can_view_assignment(
    session: Session, *, user_id: int, assignment: dict[str, Any], detail: str = "委托单不存在"
) -> AccessContext:
    """委托单读可见性：货主本人，或所属组织成员。"""
    if user_id == int(assignment["owner_user_id"]):
        return _ctx(session, user_id)
    context = _ctx(session, user_id)
    assert_org_member(context, org_id=assignment["org_id"], detail=detail)
    if not context.can(PERM_VIEW):
        raise not_found(detail)
    return context


def assert_can_view_scoped_object(
    session: Session,
    *,
    user_id: int,
    owner_user_id: int,
    org_id: int | None,
    detail: str = "对象不存在",
) -> AccessContext:
    """**未挂在委托授权链上的对象**（如私有未绑定草稿附件）的读可见性。

    规则：归属人本人，或该对象所属组织的成员（且具备查看权限）；其余一律 404。
    这类对象没有 `ent_entrustment` 行可以引用，所以只能按"归属人 + 组织"判定 ——
    它是 `assert_can_view_entrustment` 的补充，而不是替代：**能引用授权链的
    对象必须走授权链那条**，否则作用域约束（A 的授权不能操作 B 的数据）会丢。
    """
    if user_id == owner_user_id:
        return _ctx(session, user_id)
    context = _ctx(session, user_id)
    assert_org_member(context, org_id=org_id, detail=detail)
    if not context.can(PERM_VIEW):
        raise not_found(detail)
    return context


def map_access_denied(exc: Exception) -> HTTPException | None:
    """叠加层权限异常 → 403（供服务层的 `assert_can` 复用同一口径）。"""
    if isinstance(exc, AccessDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    return None


__all__ = [
    "ASSIGNMENT_COLS",
    "ENTRUSTMENT_ACTIVE",
    "ENTRUSTMENT_COLS",
    "assert_can_view_assignment",
    "assert_can_view_entrustment",
    "assert_can_view_scoped_object",
    "assert_can_write_entrustment",
    "assert_org_member",
    "load_assignment",
    "load_entrustment",
    "map_access_denied",
    "not_found",
]
