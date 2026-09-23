"""F1 三角色登录测试。

覆盖：
1. 新用户微信登录（Mock code2session）→ 自动注册 + 默认 shipper 角色
2. 老用户重复登录 → 同一用户（openid 幂等）
3. 绑定新角色 → roles 累加且幂等
4. 切换角色 → 重签 token、current_role 生效
5. 未绑定角色切换 → 400
6. 无 token 访问 /me → 401；伪造 token → 401；合法 token → 200
7. 预览回退身份（dev_code）：真机已填真实 AppID、后端尚无微信凭据时不产生空账号

client fixture 见 tests/conftest.py。
"""

from __future__ import annotations

import uuid

import jwt
from fastapi.testclient import TestClient

from app.core.config import get_settings

settings = get_settings()


def _login(client: TestClient, code: str = None, nickname: str = "测试用户") -> dict:
    """登录辅助：返回响应 JSON。"""
    payload = {"code": code or f"wx-code-{uuid.uuid4().hex[:8]}", "nickname": nickname}
    resp = client.post("/api/v1/auth/login", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- 登录 ----------


def test_login_new_user_defaults_shipper(client):
    data = _login(client, code="code-1001")
    assert data["current_role"] == "shipper"
    assert data["roles"] == ["shipper"]
    assert data["access_token"]
    assert data["openid"] == "mock-openid-code-1001"


def test_login_existing_user_same_identity(client):
    first = _login(client, code="code-1002")
    second = _login(client, code="code-1002")
    assert first["user_id"] == second["user_id"]
    assert first["openid"] == second["openid"]


# ---------- 角色绑定与切换 ----------


def test_bind_role_then_switch(client):
    data = _login(client, code="code-1003")
    headers = _auth_headers(data["access_token"])

    # 绑定船东角色
    resp = client.post("/api/v1/auth/bind-role", json={"role": "owner"}, headers=headers)
    assert resp.status_code == 200
    assert set(resp.json()["roles"]) == {"shipper", "owner"}

    # 切换到船东
    resp = client.post("/api/v1/auth/switch-role", json={"role": "owner"}, headers=headers)
    assert resp.status_code == 200
    switched = resp.json()
    assert switched["current_role"] == "owner"
    assert switched["access_token"] != data["access_token"]  # 重签了 token


def test_switch_role_is_account_scope_not_session_scope(client):
    """`switch-role` 是**账号级**：切一次，**旧 token 的判权结果也跟着变**。

    ⚠️ 这条断言的是**产品本意**、不是缺陷（DR-0017 方案 A，2026-09-15 HO 裁决）：
    判权读的是 **DB 的 `user.current_role`**（`auth/dependencies.py` 只从 token 取 `sub`），
    而 `switch-role` 改的正是用户行 ⇒「另一台设备/另一个窗口」的权限会随之变化。

    写这条用例的目的：**把这个语义钉住**，避免有人日后按"每会话独立"的直觉"顺手修好"它 ——
    真要改成每会话独立（DR-0017 的 C 方案），必须走独立切片并按 AC-01 逐域回归，
    而不是让这个行为在一次无人注意的重构里悄悄反转。
    """
    data = _login(client, code="code-1007")
    old_headers = _auth_headers(data["access_token"])  # 留着不换，模拟"另一台设备"
    assert (
        client.post(
            "/api/v1/auth/bind-role", json={"role": "owner"}, headers=old_headers
        ).status_code
        == 200
    )

    assert client.get("/api/v1/auth/me", headers=old_headers).json()["current_role"] == "shipper"

    switched = client.post(
        "/api/v1/auth/switch-role", json={"role": "owner"}, headers=old_headers
    ).json()
    assert switched["current_role"] == "owner"

    # ⭐ 关键：**用那张旧 token** 再问一次，角色已经变了 —— 它读的是用户行，不是 token 里的快照
    assert client.get("/api/v1/auth/me", headers=old_headers).json()["current_role"] == "owner"
    # 而旧 token 里的留痕仍是签发那一刻的值（这正是「快照」二字的含义）
    old_payload = jwt.decode(
        data["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert old_payload["role_snapshot"] == "shipper"


def test_switch_unbound_role_rejected(client):
    data = _login(client, code="code-1004")
    resp = client.post(
        "/api/v1/auth/switch-role",
        json={"role": "port"},
        headers=_auth_headers(data["access_token"]),
    )
    assert resp.status_code == 400


# ---------- 鉴权 ----------


def test_me_without_token_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_with_forged_token_401(client):
    headers = {"Authorization": "Bearer forged.token.value"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_me_with_valid_token(client):
    data = _login(client, code="code-1005")
    resp = client.get("/api/v1/auth/me", headers=_auth_headers(data["access_token"]))
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-code-1005"


def test_token_payload_carries_audit_fields(client):
    """JWT payload 须携带 user_id/openid/role_snapshot（留痕底线）。

    ⚠️ 这条是**留痕**断言，不是判权断言：`role_snapshot` 只记录签发那一刻的角色，
    判权读的是 **DB 的 `user.current_role`**（DR-0017 方案 A）。
    字段名 2026-09-15 由 `role` 改为 `role_snapshot` —— 因为旧名会被读成
    "每会话角色"，而实际语义是**账号级单值**（切一次影响所有设备）。
    下面额外断言"**没有** `role` 这个键"，把这次裁决钉住：谁要是把旧名加回来就会红。
    """
    data = _login(client, code="code-1006")
    payload = jwt.decode(
        data["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert payload["sub"] == str(data["user_id"])
    assert payload["openid"] == data["openid"]
    assert payload["role_snapshot"] == data["current_role"]
    assert "role" not in payload, (
        "旧字段名 `role` 不得回来（DR-0017 方案 A：它是留痕、不是判权依据）"
    )
    assert payload["jti"]


# ---------- 非微信侧校验 ----------


def test_login_without_code_and_without_cloud_header_401(client, monkeypatch):
    """既无云托管注入身份、又无 code ⇒ 401（凭证缺失）。

    注：`code` 已改为可选（云托管通道不需要它），因此这里不再是 422 参数校验失败，
    而是登录凭证缺失的 401 —— 语义更准确。
    """
    monkeypatch.setattr(settings, "WECHAT_MOCK", False)
    monkeypatch.setattr(settings, "CLOUD_OPENID_TRUSTED", True)
    resp = client.post("/api/v1/auth/login", json={"code": ""})
    assert resp.status_code == 401
    assert "缺少登录凭证" in resp.json()["detail"]


# ---------- 微信云托管通道（x-wx-openid）----------
# 小程序走 wx.cloud.callContainer 时，平台按**微信私有协议**注入当前用户身份，
# 后端无需再调 code2session —— 既省一次外部往返，也绕开了容器出网可能撞上的
# TLS 自签证书问题。⚠️ 但该头在公网直连时平台**不会**注入，故必须显式开启
# CLOUD_OPENID_TRUSTED 才采信，否则任何人都能自带该头冒充任意用户。


def test_cloud_openid_trusted_takes_precedence(client, monkeypatch):
    """开关开启：平台注入的 openid 直接采信，且**不需要** code。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", False)
    monkeypatch.setattr(settings, "CLOUD_OPENID_TRUSTED", True)
    resp = client.post(
        "/api/v1/auth/login",
        json={"nickname": "云通道用户"},
        headers={"x-wx-openid": "o-cloud-openid-trusted"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["openid"] == "o-cloud-openid-trusted"
    assert body["user_id"] > 0
    assert body["current_role"] == "shipper"


def test_cloud_openid_rejected_when_not_trusted(client, monkeypatch):
    """开关关闭（默认）：注入头被忽略 ⇒ 无 code 时 401，伪造身份不可行。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", False)
    monkeypatch.setattr(settings, "CLOUD_OPENID_TRUSTED", False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"code": ""},
        headers={"x-wx-openid": "o-forged-openid"},
    )
    assert resp.status_code == 401
    assert "缺少登录凭证" in resp.json()["detail"]


def test_cloud_openid_ignored_in_mock_mode(client, monkeypatch):
    """Mock 模式下即使开关开启也不采信注入头，保证本地/CI 身份可复现。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", True)
    monkeypatch.setattr(settings, "CLOUD_OPENID_TRUSTED", True)
    resp = client.post(
        "/api/v1/auth/login",
        json={"code": "seed-shipper"},
        headers={"x-wx-openid": "o-should-be-ignored"},
    )
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-seed-shipper"


def test_cloud_header_does_not_break_direct_channel(client, monkeypatch):
    """开关关闭时，带 code 的直连链路不受注入头影响（既有行为零改动）。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", True)
    monkeypatch.setattr(settings, "CLOUD_OPENID_TRUSTED", False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"code": "seed-owner"},
        headers={"x-wx-openid": "o-ignored"},
    )
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-seed-owner"


# ---------- 预览回退身份（dev_code）----------
# 场景：真机预览必须填真实 AppID（否则生成不了预览码），但该 AppID 是**新的** ——
# 开发者工具/真机上 wx.login 返回的真实 code 每次都不同，而后端此时往往还没配 AppSecret。
# 若直接拿真实 code 当身份，每次登录都注册一个全新空账号，「我的货源 / 我的订单」永远为空，
# 表现与功能故障一模一样。dev_code 就是这层兜底：有真实凭据时用真实 openid，没有才回退。


def test_mock_real_wx_code_falls_back_to_dev_code(client, monkeypatch):
    """WECHAT_MOCK + 真实 code（每次不同）→ 回退演示身份，账号稳定。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", True)
    resp = client.post(
        "/api/v1/auth/login",
        json={"code": "081AbCdEfGhIjKlMnOpQrStUvWxYz", "dev_code": "seed-shipper"},
    )
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-seed-shipper"


def test_mock_seed_code_wins_over_dev_code(client, monkeypatch):
    """种子身份码优先：模拟器/走查脚本注入的 seed-* 不被 dev_code 顶掉。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", True)
    resp = client.post(
        "/api/v1/auth/login", json={"code": "seed-owner", "dev_code": "seed-shipper"}
    )
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-seed-owner"


def test_mock_without_dev_code_keeps_legacy_behavior(client, monkeypatch):
    """不带 dev_code（老客户端 / curl）行为不变，避免影响既有联调方式。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", True)
    resp = client.post("/api/v1/auth/login", json={"code": "code-legacy"})
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-code-legacy"


def test_real_mode_seed_code_still_passthrough_in_dev(client, monkeypatch):
    """WECHAT_MOCK=false 后，本地校验脚本的 seed-* 仍需可用（真实 code 不会长这样）。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", False)
    resp = client.post("/api/v1/auth/login", json={"code": "seed-port"})
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-seed-port"


def test_real_mode_without_credentials_falls_back(client, monkeypatch):
    """有真实 AppID 但缺 AppSecret（真机预览最常见的断层）→ 回退演示身份，而不是 401。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", False)
    monkeypatch.setattr(settings, "WX_APP_ID", "wx4a57f29bc38ca11d")
    monkeypatch.setattr(settings, "WX_APP_SECRET", "")
    resp = client.post(
        "/api/v1/auth/login", json={"code": "081RealCodeXxx", "dev_code": "seed-owner"}
    )
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock-openid-seed-owner"


def test_real_mode_without_credentials_and_without_dev_code_401(client, monkeypatch):
    """既无凭据又无回退身份 → 明确报错，不允许静默降级。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", False)
    monkeypatch.setattr(settings, "WX_APP_ID", "")
    monkeypatch.setattr(settings, "WX_APP_SECRET", "")
    resp = client.post("/api/v1/auth/login", json={"code": "081RealCodeXxx"})
    assert resp.status_code == 401


def test_production_disables_preview_fallback(client, monkeypatch):
    """生产环境不得回退预览身份 —— 否则任何人可用 seed-* 冒充演示账号。"""
    monkeypatch.setattr(settings, "WECHAT_MOCK", False)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WX_APP_ID", "wx4a57f29bc38ca11d")
    monkeypatch.setattr(settings, "WX_APP_SECRET", "")
    resp = client.post(
        "/api/v1/auth/login", json={"code": "seed-shipper", "dev_code": "seed-shipper"}
    )
    assert resp.status_code == 401
