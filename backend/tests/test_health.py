"""健康检查与基础冒烟测试（不依赖数据库，供 CI 冒烟）。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["env"] in {"development", "test", "production"}
