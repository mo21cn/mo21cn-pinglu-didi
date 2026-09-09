"""认证模块的请求/响应模型（F1）。"""
from __future__ import annotations

from pydantic import BaseModel, Field

# 平台支持的角色（三合一 MVP：货主/船东/港口方）
SUPPORTED_ROLES = ("shipper", "owner", "port")

ROLE_LABELS = {
    "shipper": "货主",
    "owner": "船东",
    "port": "港口方",
}


class LoginRequest(BaseModel):
    """微信登录请求：code 由 wx.login() 获取。"""

    code: str = Field(..., min_length=1, max_length=128, description="wx.login 返回的临时凭证")
    nickname: str = Field(default="", max_length=64, description="昵称（可选，首次注册时使用")


class SwitchRoleRequest(BaseModel):
    """切换当前角色请求。"""

    role: str = Field(..., description="目标角色：shipper|owner|port")


class TokenResponse(BaseModel):
    """登录成功响应。"""

    access_token: str
    token_type: str = "bearer"
    user_id: int
    openid: str
    nickname: str
    roles: list[str]
    current_role: str


class UserInfoResponse(BaseModel):
    """当前用户信息。"""

    user_id: int
    openid: str
    nickname: str
    avatar: str
    phone: str
    roles: list[str]
    current_role: str
    status: str
