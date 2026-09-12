"""委托支线的 FastAPI 权限依赖（叠加层入口）。

用法（仅用于**新增的委托接口**，既有 router 不用）::

    @router.post("/api/v1/entrust/quotes/publish")
    def publish(..., ctx: AccessContext = Depends(require_permission(PERM_QUOTE_PUBLISH))):
        ...

带作用域时（操作某个货主的数据）::

    def publish(owner_id: int,
                ctx: AccessContext = Depends(require_permission(PERM_QUOTE_PUBLISH, owner_user_id=...))):
        ...

注意：作用域参数在装饰期通常是拿不到的（要等请求体），这种场景请在函数体内调用
`assert_can(db, user_id=..., permission=..., owner_user_id=body.owner_id)`，
而不是把路由参数塞进 `Depends`。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust.access import (
    AccessContext,
    AccessDeniedError,
    assert_can,
    resolve_context,
)


def get_access_context(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccessContext:
    """解析当前用户的委托支线访问上下文（不拦截，供需要细分的接口自行判断）。"""
    return resolve_context(db, user_id=int(user.id))


def require_permission(
    permission: str, *, owner_user_id: int | None = None
) -> Callable[..., AccessContext]:
    """生成一个依赖：要求当前用户具备指定权限，否则 403。

    Args:
        permission: 权限代码常量（见 `app.modules.entrust.access`）。
        owner_user_id: 可选。给出时要求该货主存在生效授权。
    """

    def _dependency(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AccessContext:
        try:
            return assert_can(
                db,
                user_id=int(user.id),
                permission=permission,
                owner_user_id=owner_user_id,
            )
        except AccessDeniedError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    return _dependency
