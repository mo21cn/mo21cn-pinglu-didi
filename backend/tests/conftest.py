"""测试公共 fixture：SQLite 内存库 + TestClient + 三角色用户。

各角色用户通过 Mock 登录创建，再 bind-role + switch-role 组合出多角色账号。
"""
from __future__ import annotations

import os
import uuid

# 必须先设 APP_ENV 再导入 app（config 按 APP_ENV 加载 .env.test）
os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def force_wechat_mock(monkeypatch):
    """所有用例强制微信 Mock 链路。

    config 的 env_file 叠加了 `.env.local`（本机敏感值，不入库）。本机一旦填入真实
    WX_APP_ID / WX_APP_SECRET，单元测试就会真的去调 code2session —— 网络依赖、超时、
    不可复现，且会把「真实凭据」当成测试前提。与 test_agent.py 的 force_llm_mock 同理。
    需要验证真实链路的用例，自行在函数内再 monkeypatch 覆盖。
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "WECHAT_MOCK", True)


@pytest.fixture()
def client(monkeypatch):
    """每个测试用独立的 SQLite 内存库 + TestClient。"""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # 内存库需共享同一连接
    )
    test_session_factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    from app.main import app
    from app.modules.auth.router import get_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


def _login(client: TestClient, code: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": code})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def shipper(client):
    """纯货主账号（默认注册即 shipper）。"""
    data = _login(client, f"shipper-{uuid.uuid4().hex[:8]}")
    data["_headers"] = _auth(data["access_token"])
    return data


@pytest.fixture()
def owner(client):
    """纯船东账号（注册默认 shipper → bind owner → switch owner）。"""
    data = _login(client, f"owner-{uuid.uuid4().hex[:8]}")
    h = _auth(data["access_token"])
    client.post("/api/v1/auth/bind-role", json={"role": "owner"}, headers=h)
    resp = client.post("/api/v1/auth/switch-role", json={"role": "owner"}, headers=h)
    assert resp.status_code == 200
    data = resp.json()
    data["_headers"] = _auth(data["access_token"])
    return data


@pytest.fixture()
def port_user(client):
    """港口方账号（承担平台侧审核职能，见 F3 说明）。"""
    data = _login(client, f"port-{uuid.uuid4().hex[:8]}")
    h = _auth(data["access_token"])
    client.post("/api/v1/auth/bind-role", json={"role": "port"}, headers=h)
    resp = client.post("/api/v1/auth/switch-role", json={"role": "port"}, headers=h)
    assert resp.status_code == 200
    data = resp.json()
    data["_headers"] = _auth(data["access_token"])
    return data
