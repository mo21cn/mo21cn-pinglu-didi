"""F9 货源解析 Agent 测试。

全部走 LLM_MOCK 规则模板（显式 monkeypatch，避免本机 .env.local 的真实 Key
让测试产生真实网络调用）；审计落库用独立内存库直连 session 断言。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.agent import AgentCall
from app.modules.agent import service
from app.modules.agent.llm import LLMError


@pytest.fixture(autouse=True)
def force_llm_mock(monkeypatch):
    """本文件所有用例强制规则模板模式（不发起任何网络请求）。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "LLM_MOCK", True)


def test_cargo_parse_mock_success(shipper, client):
    """标准口语描述：全字段命中（吨位/两港/货类关键词）。"""
    resp = client.post(
        "/api/v1/agent/cargo-parse",
        json={"text": "我有800吨散装水泥，下周从南宁运到贵港，运费2万5"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    draft = body["draft"]
    assert draft["cargo_name"]
    assert draft["cargo_type"] == "bulk"
    assert draft["weight_t"] == 800.0
    assert draft["origin_port"] == "NNG"
    assert draft["dest_port"] == "GGU"
    assert body["mocked"] is True
    assert body["confidence"] >= 0.5


def test_cargo_parse_missing_fields_in_review(shipper, client):
    """信息不足：缺失字段进 needs_review，draft 对应位置为 null。"""
    resp = client.post(
        "/api/v1/agent/cargo-parse",
        json={"text": "帮我发一批钢材过去"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "weight_t" in body["needs_review"]
    assert "origin_port" in body["needs_review"]
    assert "dest_port" in body["needs_review"]
    assert body["draft"]["weight_t"] is None
    assert body["draft"]["origin_port"] is None


def test_cargo_parse_requires_shipper_role(owner, client):
    """Agent 端点同业务端点一致：非 shipper 角色 403。"""
    resp = client.post(
        "/api/v1/agent/cargo-parse",
        json={"text": "800吨水泥南宁到贵港"},
        headers=owner["_headers"],
    )
    assert resp.status_code == 403


def test_cargo_parse_invalid_port_filtered(shipper, client):
    """LLM 输出含非法港口时代码 → 合法性过滤置 null 并标 needs_review。"""
    resp = client.post(
        "/api/v1/agent/cargo-parse",
        json={"text": "有批货想从广州运到深圳，大概300吨"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"]["origin_port"] is None
    assert "origin_port" in body["needs_review"]


def test_cargo_parse_llm_failure_degrades(shipper, client, monkeypatch):
    """LLM 故障：HTTP 504 + 审计行 success=False error_kind=timeout。"""

    async def _boom(*, system, user, temperature=0.1):
        raise LLMError("timeout", "LLM 调用超时（60s）")

    monkeypatch.setattr(service, "llm_gateway", type("M", (), {"chat_json": staticmethod(_boom), "LLMError": LLMError}))

    resp = client.post(
        "/api/v1/agent/cargo-parse",
        json={"text": "800吨水泥南宁到贵港"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 504
    assert "稍后重试" in resp.json()["detail"]


# ---------- F10 客服导购 ----------

def test_assistant_mock_faq(shipper, client):
    """关键词命中 FAQ 模板（mock 模式）。"""
    resp = client.post(
        "/api/v1/agent/assistant",
        json={"question": "怎么发布货源？"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "发布货源" in body["answer"]
    assert body["mocked"] is True


def test_assistant_available_to_all_roles(owner, client):
    """客服问答不限角色（对比 cargo-parse 的 shipper 限定）。"""
    resp = client.post(
        "/api/v1/agent/assistant",
        json={"question": "平台支持哪些港口？"},
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text
    assert "13" in resp.json()["answer"] or "港口" in resp.json()["answer"]


def test_assistant_requires_auth(client):
    """未登录 401。"""
    resp = client.post("/api/v1/agent/assistant", json={"question": "怎么发货"})
    assert resp.status_code == 401


def test_audit_row_written_on_success_and_failure():
    """审计留痕（工程底线 3）：成功与失败各落一行 AgentCall。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    # 成功路径
    result = asyncio.run(
        service.parse_cargo(db, user_id=1, text="我有800吨水泥从南宁运到贵港")
    )
    assert result.draft.weight_t == 800.0

    # 失败路径
    async def _boom(*, system, user, temperature=0.1):
        raise LLMError("network", "connection reset")

    original = service.llm_gateway.chat_json
    service.llm_gateway.chat_json = staticmethod(_boom)
    try:
        with pytest.raises(service.AgentServiceError):
            asyncio.run(service.parse_cargo(db, user_id=1, text="发货"))
    finally:
        service.llm_gateway.chat_json = original

    rows = db.execute(select(AgentCall).order_by(AgentCall.id)).scalars().all()
    assert len(rows) == 2
    ok_row, fail_row = rows
    assert ok_row.success is True
    assert ok_row.agent_name == "cargo_parse"
    assert ok_row.mocked is True
    assert ok_row.latency_ms >= 0
    assert fail_row.success is False
    assert fail_row.error_kind == "network"
    db.close()


# ---------- F11 智能合同 ----------

def _make_order(shipper, owner, client, **kw):
    """货主建货源→船东备案船→撮合→下单，返回 order_id（走业务 API 全链路）。"""
    from datetime import date, timedelta

    tomorrow = (date.today() + timedelta(days=kw.get("days", 10))).isoformat()
    cargo = client.post(
        "/api/v1/cargo/shipments",
        json={
            "cargo_name": kw.get("cargo_name", "散装水泥"),
            "cargo_type": kw.get("cargo_type", "bulk"),
            "weight_t": 800,
            "origin_port": "NNG",
            "dest_port": "GGU",
            "expect_date": tomorrow,
            "publish_now": True,
        },
        headers=shipper["_headers"],
    ).json()

    ship = client.post(
        "/api/v1/ship/registry",
        json={
            "ship_name": "平陆 001",
            "ship_type": kw.get("ship_type", "bulk"),
            "deadweight_t": 2000,
            "length_m": 60,
            "width_m": 12,
            "draft_m": 3.5,
            "home_port": "NNG",
            "cert_no": "CERT-F11-001",
            "cert_expiry": (date.today() + timedelta(days=kw.get("cert_days", 300))).isoformat(),
        },
        headers=owner["_headers"],
    ).json()
    client.post(
        f"/api/v1/ship/registry/{ship['id']}/verify",
        json={"approved": True},
        headers=kw["port_headers"],
    )

    order = client.post(
        f"/api/v1/match/cargos/{cargo['id']}/ships",
        headers=shipper["_headers"],
    )  # 探测（非必须）
    order = client.post(
        "/api/v1/order/orders",
        json={"cargo_id": cargo["id"], "ship_id": ship["id"], "freight_price": kw.get("price", 25000)},
        headers=shipper["_headers"],
    )
    assert order.status_code == 200, order.text
    return order.json()["id"]


def test_contract_generate_shipper_view(shipper, owner, port_user, client):
    """货主视角：合同主体含订单确定性字段 + 四条补充条款 + 审计。"""
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"])
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["order_id"] == oid
    assert body["mocked"] is True
    text = body["contract_text"]
    # 确定性核心条款（金额/日期/港口）来自订单数据而非 LLM
    assert "25,000.00 元" in text
    assert "南宁" in text and "贵港" in text
    assert "平陆 001" in text
    # 补充条款（mock 四条标准条款）
    assert "不可抗力" in text and "争议解决" in text
    # 未支付 → R1 命中
    titles = [r["title"] for r in body["risks"]]
    assert "运费未支付" in titles


def test_contract_generate_owner_participant_ok(shipper, owner, port_user, client):
    """船东（订单参与方）同样可生成。"""
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"])
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text


def test_contract_generate_non_participant_rejected(
    shipper, owner, port_user, client
):
    """非参与方（第三方货主）禁止生成。"""
    from tests.conftest import _login

    other = _login(client, f"other-{__import__('uuid').uuid4().hex[:8]}")
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"])
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert resp.status_code == 400
    assert "参与方" in resp.json()["detail"]


def test_contract_risk_rules_hit(shipper, owner, port_user, client):
    """风险规则引擎：面议未锁价 + 日期临近 + 证书临期 + 液货。"""
    oid = _make_order(
        shipper, owner, client,
        port_headers=port_user["_headers"],
        days=1,            # 装货日期临近（<3 天）
        cert_days=10,      # 证书临期（<30 天）
        cargo_type="tanker", ship_type="tanker",  # 液货
        price=None,        # 面议未锁价
    )
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    titles = [r["title"] for r in resp.json()["risks"]]
    assert "运费未锁定" in titles
    assert "装货日期临近" in titles
    assert "船舶证书临期" in titles
    assert "液货/危险品运输" in titles
    assert "面议" in resp.json()["contract_text"]


def test_contract_cancelled_order_rejected(shipper, owner, port_user, client):
    """已撤销订单禁止生成。"""
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"])
    resp = client.post(
        f"/api/v1/order/orders/{oid}/cancel",
        json={"reason": "测试"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 400
    assert "撤销" in resp.json()["detail"]


def test_contract_order_not_found(shipper, client):
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": 99999},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 400
