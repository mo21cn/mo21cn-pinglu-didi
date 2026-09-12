"""认证路由（F1 三角色登录）。

端点：
- POST /api/v1/auth/login          微信 code 登录（新用户自动注册并绑定首个角色）
- POST /api/v1/auth/switch-role    切换当前角色（须已绑定）
- POST /api/v1/auth/bind-role      绑定新角色
- GET  /api/v1/auth/me             获取当前用户信息
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token
from app.models.user import User
from app.modules.auth import service
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import (
    LoginRequest,
    SwitchRoleRequest,
    TokenResponse,
    UserInfoResponse,
)

router = APIRouter()


@router.post("/login", response_model=TokenResponse, summary="微信登录（三角色合一）")
async def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """wx.login 的 code 换 openid；新用户自动注册，默认角色为货主（shipper）。"""
    try:
        wx = await service.code2session(body.code, body.dev_code)
    except service.WechatAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    user = service.upsert_user(db, wx["openid"], wx["unionid"], body.nickname, role="shipper")
    token = create_access_token(user.id, user.openid, user.current_role)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        openid=user.openid,
        nickname=user.nickname,
        roles=user.roles or [],
        current_role=user.current_role,
    )


@router.post("/switch-role", response_model=TokenResponse, summary="切换当前角色")
def switch_role(
    body: SwitchRoleRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """切换活跃角色后重签 token（角色写进 JWT payload，随请求携带可审计）。"""
    try:
        user = service.switch_role(db, user, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    token = create_access_token(user.id, user.openid, user.current_role)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        openid=user.openid,
        nickname=user.nickname,
        roles=user.roles or [],
        current_role=user.current_role,
    )


@router.post("/bind-role", response_model=UserInfoResponse, summary="绑定新角色")
def bind_role(
    body: SwitchRoleRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserInfoResponse:
    """为当前用户绑定新角色（如货主升级为'货主+船东'双角色）。"""
    user.bind_role(body.role)
    db.commit()
    db.refresh(user)
    return UserInfoResponse(
        user_id=user.id, openid=user.openid, nickname=user.nickname, avatar=user.avatar,
        phone=user.phone, roles=user.roles or [], current_role=user.current_role,
        status=user.status,
    )


@router.get("/me", response_model=UserInfoResponse, summary="当前用户信息")
def me(user: User = Depends(get_current_user)) -> UserInfoResponse:
    return UserInfoResponse(
        user_id=user.id, openid=user.openid, nickname=user.nickname, avatar=user.avatar,
        phone=user.phone, roles=user.roles or [], current_role=user.current_role,
        status=user.status,
    )
