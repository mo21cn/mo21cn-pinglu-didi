"""JWT 令牌签发与校验（认证安全核心）。

设计要点（对齐 PRD 三条工程底线之"交易全链路留痕"）：
- payload 中固定携带 user_id / openid / role_snapshot / jti，服务端可据此审计。
- ⚠️ `role_snapshot` 是**签发那一刻的留痕**，**不参与任何判权**（DR-0017 方案 A）：
  判权读的是 **DB 的 `user.current_role`**（见 `auth/dependencies.py`），
  而 `switch-role` 改的也是**用户行** ⇒ **一处切角色会影响该用户的所有会话与设备**。
  这个字段**曾叫 `role`**，很容易被读成"每会话角色" —— 改名就是为了消除这个误导：
  全仓没有任何一处读它，**唯一读它的是测试**（`test_token_payload_carries_audit_fields`）。
- 过期时间由配置统一管理（JWT_EXPIRE_MINUTES）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings

settings = get_settings()

# 令牌类型
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def create_access_token(user_id: int, openid: str, role: str) -> str:
    """为指定用户与当前角色签发 access token。

    参数 `role` 会以 **`role_snapshot`** 之名写进 payload —— 它是**留痕**，
    **不是判权依据**（见模块 docstring 与 DR-0017 方案 A）。
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "openid": openid,
        "role_snapshot": role,
        "type": TOKEN_TYPE_ACCESS,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """校验并解码 token。签名错误/过期统一抛 jwt.InvalidTokenError 子类。"""
    payload: dict[str, Any] = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise jwt.InvalidTokenError("非 access token")
    return payload
