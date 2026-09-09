"""F1 三角色登录测试。

覆盖：
1. 新用户微信登录（Mock code2session）→ 自动注册 + 默认 shipper 角色
2. 老用户重复登录 → 同一用户（openid 幂等）
3. 绑定新角色 → roles 累加且幂等
4. 切换角色 → 重签 token、current_role 生效
5. 未绑定角色切换 → 400
6. 无 token 访问 /me → 401；伪造 token → 401；合法 token → 200

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
    """JWT payload 须携带 user_id/openid/role（留痕底线）。"""
    data = _login(client, code="code-1006")
    payload = jwt.decode(
        data["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert payload["sub"] == str(data["user_id"])
    assert payload["openid"] == data["openid"]
    assert payload["role"] == data["current_role"]
    assert payload["jti"]


# ---------- 非微信侧校验 ----------

def test_login_empty_code_422(client):
    resp = client.post("/api/v1/auth/login", json={"code": ""})
    assert resp.status_code == 422
