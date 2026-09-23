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


# 本地种子身份码前缀：seed-* / mock-*，只在开发与联调期出现，
# 真实 wx.login 返回的 code 不会长这样。
_PREVIEW_DEV_CODE_PREFIXES = ("seed-", "mock-")


def _is_preview_dev_code(code: str) -> bool:
    """是否为本地种子身份码（后端 Mock 模式下「code 即身份」）。"""
    return code.startswith(_PREVIEW_DEV_CODE_PREFIXES)


def _preview_openid(dev_code: str) -> str:
    """预览期回退 openid：与 seed_demo.py 的演示账号 openid 保持同一命名。"""
    return f"mock-openid-{dev_code}"


async def code2session(code: str, dev_code: str = "") -> dict[str, str]:
    """用临时凭证换取 openid/session_key。

    Args:
        code: wx.login 返回的临时凭证；开发期也可能是种子身份码（如 seed-shipper）。
        dev_code: 预览期回退身份（演示账号 code）。仅当后端拿不到真实微信身份时启用，
            保证真机预览也能命中演示数据，而不是每次登录新建一个空账号。

    Returns:
        {"openid": ..., "unionid": ...}（unionid 可能为空）

    Raises:
        WechatAuthError: 调用失败或微信返回错误码。
    """
    if settings.WECHAT_MOCK:
        # Mock 模式：openid 由 code 确定性生成，便于开发/测试复现同一用户。
        # 但真机 wx.login 的 code 每次都不同 —— 直接拿它当身份，等于每次登录都新建空账号，
        # 表现与「功能故障」完全一致。故 code 非种子身份时改用客户端附带的预览回退身份。
        if not _is_preview_dev_code(code) and dev_code and not settings.is_production:
            logger.warning(
                "WECHAT_MOCK 下 code 非种子身份，回退预览身份 openid=%s", _preview_openid(dev_code)
            )
            return {"openid": _preview_openid(dev_code), "unionid": ""}
        return {"openid": f"mock-openid-{code}", "unionid": ""}

    if not settings.is_production and _is_preview_dev_code(code):
        # 真实凭据就绪（WECHAT_MOCK=false）后，本地校验脚本仍以 seed-* 直连后端；
        # 真实登录的 code 不可能长这样，故开发期保留此直通，避免本地校验被微信链路卡住。
        return {"openid": f"mock-openid-{code}", "unionid": ""}

    if not settings.WX_APP_ID or not settings.WX_APP_SECRET:
        if not settings.is_production and dev_code:
            logger.warning("未配置微信凭据，回退预览身份 openid=%s", _preview_openid(dev_code))
            return {"openid": _preview_openid(dev_code), "unionid": ""}
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


async def resolve_identity(
    code: str = "",
    dev_code: str = "",
    cloud_openid: str = "",
    cloud_unionid: str = "",
) -> dict[str, str]:
    """解析登录身份：**云托管注入的可信 openid 优先**，否则回落到 code2session。

    为什么需要云托管通道：小程序走 `wx.cloud.callContainer` 时，平台按**微信私有协议**
    注入当前用户身份（`x-wx-openid`），后端**无需**再调 code2session —— 既省一次外部往返，
    也绕开了容器出网可能遇到的 TLS 证书问题（云托管容器访问微信接口时可能撞上平台
    自签证书，表现为 `CERTIFICATE_VERIFY_FAILED`，详见 `docs/` 的联动诊断报告）。

    ⚠️ 采信该头**必须**满足 `CLOUD_OPENID_TRUSTED=true`：公网直连时平台不注入，
    若无条件采信，任何人都能自带该头冒充任意用户。Mock 模式下也不采信（保证测试可复现）。

    Args:
        code: `wx.login` 的临时凭证（云托管通道下可为空）。
        dev_code: 预览期回退身份（演示账号 code）。
        cloud_openid: 请求头 `x-wx-openid`。
        cloud_unionid: 请求头 `x-wx-unionid`。

    Returns:
        {"openid": ..., "unionid": ...}

    Raises:
        WechatAuthError: 两条通道都拿不到身份。
    """
    if cloud_openid and settings.CLOUD_OPENID_TRUSTED and not settings.WECHAT_MOCK:
        logger.info("云托管通道采信平台注入身份: openid=%s", cloud_openid)
        return {"openid": cloud_openid, "unionid": cloud_unionid or ""}

    if not code:
        raise WechatAuthError(
            "缺少登录凭证：未收到云托管注入的 x-wx-openid，也未提供 wx.login 的 code"
        )
    return await code2session(code, dev_code)


def upsert_user(db: Session, openid: str, unionid: str, nickname: str, role: str) -> User:
    """按 openid 查找用户；不存在则注册（新用户绑定首个角色）。"""
    user = db.query(User).filter(User.openid == openid).first()
    if user is None:
        user = User(
            openid=openid,
            unionid=unionid or None,
            nickname=nickname,
            roles=[role],
            current_role=role,
        )
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
    """切换当前角色（须已绑定该角色）。

    ⚠️ **这是账号级动作**：`current_role` 写在**用户行**上，而各模块判权读的也是用户行
    ⇒ **会影响该用户在所有设备与会话上的身份**（另一台设备、另一个窗口的权限随之变化）。
    DR-0017 方案 A 认定这一语义是**产品本意**，因此**不改判权路径**；
    签发出去的 token 里那份 `role_snapshot` 只是留痕，**不参与判权**
    （见 `core/security.py` 的模块 docstring）。
    """
    if role not in (user.roles or []):
        raise ValueError(f"用户未绑定角色 {role}，请先在角色管理中绑定")
    user.current_role = role
    db.commit()
    db.refresh(user)
    return user
