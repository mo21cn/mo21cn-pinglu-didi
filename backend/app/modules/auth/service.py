"""认证服务层（F1）。

职责：
1. 调用微信 code2session 换取 openid（WECHAT_MOCK=true 时本地模拟，供开发/CI 使用）。
2. 用户 upsert：首次登录自动注册并绑定所选角色。
3. 角色绑定与切换的业务校验。
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User

logger = logging.getLogger(__name__)
settings = get_settings()

WX_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


class WechatAuthError(Exception):
    """微信登录失败（code 无效/网络异常等）。"""


async def code2session(code: str) -> dict[str, str]:
    """用临时凭证换取 openid/session_key。

    Returns:
        {"openid": ..., "unionid": ...}（unionid 可能为空）

    Raises:
        WechatAuthError: 调用失败或微信返回错误码。
    """
    if settings.WECHAT_MOCK:
        # Mock 模式：openid 由 code 确定性生成，便于开发/测试复现同一用户
        return {"openid": f"mock-openid-{code}", "unionid": ""}

    if not settings.WX_APP_ID or not settings.WX_APP_SECRET:
        raise WechatAuthError("未配置 WX_APP_ID/WX_APP_SECRET（开发环境可设 WECHAT_MOCK=true）")

    params = {
        "appid": settings.WX_APP_ID,
        "secret": settings.WX_APP_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(WX_CODE2SESSION_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.error("code2session 网络异常: %s", exc)
        raise WechatAuthError("微信登录服务暂不可用，请稍后重试") from exc

    if data.get("errcode"):
        logger.warning("code2session 业务失败: %s", data)
        raise WechatAuthError(f"微信登录失败: {data.get('errmsg', 'unknown')}")

    openid = data.get("openid", "")
    if not openid:
        raise WechatAuthError("微信登录返回缺少 openid")
    return {"openid": openid, "unionid": data.get("unionid", "") or ""}


def upsert_user(db: Session, openid: str, unionid: str, nickname: str, role: str) -> User:
    """按 openid 查找用户；不存在则注册（新用户绑定首个角色）。"""
    user = db.query(User).filter(User.openid == openid).first()
    if user is None:
        user = User(openid=openid, unionid=unionid or None, nickname=nickname, roles=[role],
                    current_role=role)
        db.add(user)
    else:
        # 老用户：绑定新角色（幂等），昵称仅在传入非空时更新
        user.bind_role(role)
        if nickname:
            user.nickname = nickname
    db.commit()
    db.refresh(user)
    return user


def switch_role(db: Session, user: User, role: str) -> User:
    """切换当前角色（须已绑定该角色）。"""
    if role not in (user.roles or []):
        raise ValueError(f"用户未绑定角色 {role}，请先在角色管理中绑定")
    user.current_role = role
    db.commit()
    db.refresh(user)
    return user
