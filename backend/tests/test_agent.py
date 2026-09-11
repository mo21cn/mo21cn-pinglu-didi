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


# ---------- F12 RAG 知识检索 ----------

def test_rag_search_single_port_hit():
    """问单港 → 精确命中该港文档。"""
    from app.modules.agent.knowledge import search_knowledge

    hits = search_knowledge("钦州港是干什么的，能走什么货")
    assert hits, "应有召回"
    assert hits[0][0].id == "port-qnz"
    assert "QNZ" in hits[0][0].text


def test_rag_search_tanker_and_refund():
    """液货/退款问题 → 各自命中正确领域文档。"""
    from app.modules.agent.knowledge import search_knowledge

    assert search_knowledge("液货用什么船拉")[0][0].id == "cargo-tanker"
    assert search_knowledge("撤单了钱退吗")[0][0].id == "flow-payment"


def test_rag_fallback_core_docs():
    """无召回（无关问题）→ 回退注入角色+主流程核心文档。"""
    from app.modules.agent.service import _build_system_prompt

    prompt = _build_system_prompt("zzzz qqqq 完全无关")
    assert "三角色" in prompt or "货主" in prompt
    assert "主流程" in prompt or "签收" in prompt


def test_rag_prompt_contains_retrieved_doc():
    """正常问题 → prompt 含检索到的知识文本（RAG 注入生效）。"""
    from app.modules.agent.service import _build_system_prompt

    prompt = _build_system_prompt("贵港港是什么地位")
    assert "GGU" in prompt
    assert "参考资料" in prompt


def test_assistant_rag_mock_pipeline(shipper, client):
    """mock 模式全链路：assistant 经 RAG 拼装后正常回答（回归）。"""
    resp = client.post(
        "/api/v1/agent/assistant",
        json={"question": "怎么发货？"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    assert "发货" in resp.json()["answer"]


def test_contract_llm_failure_degrades(shipper, owner, port_user, client, monkeypatch):
    """LLM 故障时降级返回（TODO-10）：主体条款与风险仍可用，补充条款回退内置模板。"""
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"])

    async def _boom(**_: object) -> None:
        raise LLMError("network", "LLM 网络错误：模拟故障")

    monkeypatch.setattr(service.llm_gateway, "chat_json", _boom)
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is True
    assert body["degraded_reason"] == "network"
    assert body["mocked"] is False
    text = body["contract_text"]
    # 确定性核心条款不受 LLM 故障影响
    assert "25,000.00 元" in text
    assert "南宁" in text and "贵港" in text
    # 补充条款回退内置标准模板
    assert "不可抗力" in text and "争议解决" in text
    # 风险点为规则引擎产物，照常返回
    assert "运费未支付" in [r["title"] for r in body["risks"]]
    # 降级对使用者可见
    assert "已回退平台内置标准条款" in text


def test_contract_llm_failure_audit_marks_failure(monkeypatch):
    """降级路径的审计照实记录：success=False + error_kind（不掩盖 LLM 故障）。"""
    import datetime as _dt

    from app.models.cargo import Cargo
    from app.models.order import Order
    from app.models.ship import Ship
    from app.models.user import User

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    shipper = User(openid="audit-shipper", roles=["shipper"])
    owner = User(openid="audit-owner", roles=["owner"])
    db.add_all([shipper, owner])
    db.flush()
    cargo = Cargo(
        shipper_id=shipper.id,
        cargo_name="散装水泥",
        cargo_type="bulk",
        weight_t=800,
        origin_port="NNG",
        dest_port="GGU",
        expect_date=_dt.date(2026, 10, 1),
        offer_price=25000,
        status="published",
    )
    ship = Ship(
        owner_id=owner.id,
        ship_name="平陆 001",
        ship_type="bulk",
        deadweight_t=1500,
        length_m=45,
        width_m=8,
        draft_m=2.5,
        home_port="GGU",
        cert_no="C-AUDIT-1",
        cert_expiry=_dt.date(2027, 1, 1),
        status="verified",
    )
    db.add_all([cargo, ship])
    db.flush()
    order = Order(
        cargo_id=cargo.id,
        ship_id=ship.id,
        shipper_id=shipper.id,
        owner_id=owner.id,
        freight_price=25000,
        status="matched",
    )
    db.add(order)
    db.commit()

    async def _boom(**_: object) -> None:
        raise LLMError("timeout", "LLM 调用超时（模拟）")

    monkeypatch.setattr(service.llm_gateway, "chat_json", _boom)
    result = asyncio.run(service.generate_contract(db, user_id=shipper.id, order_id=order.id))
    assert result.degraded is True
    assert result.degraded_reason == "timeout"

    rows = (
        db.execute(select(AgentCall).where(AgentCall.agent_name == "contract")).scalars().all()
    )
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error_kind == "timeout"


# ---------- F18 合同商务条款类风险（对齐验收口径：滞期费/违约金/保险/不可抗力） ----------


def test_contract_business_clause_rules_hit(shipper, owner, port_user, client):
    """滞期费（散货/液货）+ 保险（大额）+ 违约金量化（临近装货）三条同时命中。"""
    oid = _make_order(
        shipper, owner, client,
        port_headers=port_user["_headers"],
        days=5,        # 装货日在 7 日内 → R8 违约金标准未量化
        price=31000,   # ≥ 20000 → R7 货物保险未约定
    )
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    risks = resp.json()["risks"]
    titles = [r["title"] for r in risks]
    assert "滞期费未约定" in titles          # R6（散货装卸耗时长）
    assert "货物保险未约定" in titles        # R7（大额运输）
    assert "违约金标准未量化" in titles      # R8（matched + 装货日 ≤7 天）
    # 商务条款类均为中/低风险，不与事实类高风险混同等级
    by_title = {r["title"]: r["severity"] for r in risks}
    assert by_title["滞期费未约定"] == "medium"
    assert by_title["货物保险未约定"] == "medium"
    assert by_title["违约金标准未量化"] == "low"


def test_contract_shipped_order_flags_force_majeure(shipper, owner, port_user, client):
    """在途（shipped）订单命中 R9 在途不可抗力风险。"""
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"], days=5)
    started = client.post(
        f"/api/v1/order/orders/{oid}/ship", json={}, headers=owner["_headers"]
    )
    assert started.status_code == 200, started.text
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    assert "在途不可抗力风险" in [r["title"] for r in resp.json()["risks"]]


def test_contract_completed_order_has_no_clause_completeness_noise(
    shipper, owner, port_user, client
):
    """已签收订单不再提示条款完备性类（R6/R7），保证「已完成 = 干净合同」口径。"""
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"], days=5)
    client.post(f"/api/v1/order/orders/{oid}/ship", json={}, headers=owner["_headers"])
    client.post(f"/api/v1/order/orders/{oid}/complete", json={}, headers=shipper["_headers"])
    resp = client.post(
        "/api/v1/agent/contract/generate",
        json={"order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    titles = [r["title"] for r in resp.json()["risks"]]
    assert "滞期费未约定" not in titles
    assert "货物保险未约定" not in titles
    assert "违约金标准未量化" not in titles


# ---------- F17 合规初筛（发布 / 备案前即时预检） ----------


def _cargo_body(**kw):
    from datetime import date, timedelta

    body = {
        "cargo_name": "散装水泥",
        "cargo_type": "bulk",
        "weight_t": 800,
        "origin_port": "NNG",
        "dest_port": "GGU",
        "expect_date": (date.today() + timedelta(days=kw.get("days", 10))).isoformat(),
        "remark": kw.get("remark", ""),
    }
    body.update({k: v for k, v in kw.items() if k not in ("days",)})
    return body


def test_compliance_cargo_pass(shipper, client):
    """常规货源：无结论、level=pass、返回检查规则条数。"""
    resp = client.post(
        "/api/v1/agent/compliance/cargo",
        json=_cargo_body(),
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["level"] == "pass"
    assert body["findings"] == []
    assert body["target"] == "cargo"
    assert body["checked_rules"] == 4
    assert "未发现合规问题" in body["summary"]


def test_compliance_cargo_forbidden_blocks(shipper, client):
    """禁运/管制货品 → block（C1），summary 提示勿提交。"""
    resp = client.post(
        "/api/v1/agent/compliance/cargo",
        json=_cargo_body(cargo_name="烟花爆竹 一批"),
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["level"] == "block"
    assert [f["code"] for f in body["findings"]] == ["C1"]
    assert body["findings"][0]["severity"] == "block"


def test_compliance_cargo_dangerous_goods_warns(shipper, client):
    """危险货物 → warn（C2，不阻断但提示申报与适装资质）。"""
    resp = client.post(
        "/api/v1/agent/compliance/cargo",
        json=_cargo_body(cargo_name="甲醇", cargo_type="tanker"),
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["level"] == "warn"
    assert "C2" in [f["code"] for f in body["findings"]]


def test_compliance_cargo_container_route_and_date_warn(shipper, client):
    """集装箱非干线港 + 装货日过近 → 两条 warn（C3 / C4）。"""
    resp = client.post(
        "/api/v1/agent/compliance/cargo",
        json=_cargo_body(cargo_type="container", origin_port="NNG", dest_port="LZH", days=1),
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    codes = [f["code"] for f in resp.json()["findings"]]
    assert "C3" in codes and "C4" in codes
    assert resp.json()["level"] == "warn"


def test_compliance_cargo_past_date_blocks(shipper, client):
    """装货日期已过 → block（C4）。"""
    resp = client.post(
        "/api/v1/agent/compliance/cargo",
        json=_cargo_body(days=-2),
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["level"] == "block"


def test_compliance_cargo_requires_shipper(owner, client):
    """货源预检仅货主可用（与货源写入权限一致）。"""
    resp = client.post(
        "/api/v1/agent/compliance/cargo", json=_cargo_body(), headers=owner["_headers"]
    )
    assert resp.status_code == 403


def _ship_body(**kw):
    from datetime import date, timedelta

    body = {
        "ship_name": "平陆 001",
        "ship_type": "bulk",
        "deadweight_t": 1500,
        "length_m": 60,
        "width_m": 12,
        "draft_m": 3.5,
        "home_port": "GGU",
        "cert_no": "CERT-F17-001",
        "cert_expiry": (date.today() + timedelta(days=kw.get("cert_days", 300))).isoformat(),
    }
    body.update({k: v for k, v in kw.items() if k != "cert_days"})
    return body


def test_compliance_ship_pass(owner, client):
    """常规船舶：无结论。"""
    resp = client.post(
        "/api/v1/agent/compliance/ship", json=_ship_body(), headers=owner["_headers"]
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["level"] == "pass"
    assert body["target"] == "ship"
    assert body["checked_rules"] == 5


def test_compliance_ship_expired_cert_blocks(owner, client):
    """证书过期 → block（S1）。"""
    resp = client.post(
        "/api/v1/agent/compliance/ship",
        json=_ship_body(cert_days=-5),
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["level"] == "block"
    assert body["findings"][0]["code"] == "S1"


def test_compliance_ship_warns_on_dimension_draft_and_home_port(owner, client):
    """主尺度比例异常 + 吃水偏深 + 缺船籍港 → 三条 warn（S3/S4/S5）。"""
    resp = client.post(
        "/api/v1/agent/compliance/ship",
        json=_ship_body(length_m=50, width_m=25, draft_m=5.2, home_port=""),
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    codes = sorted(f["code"] for f in body["findings"])
    assert codes == ["S3", "S4", "S5"]
    assert body["level"] == "warn"


def test_compliance_ship_requires_owner(shipper, client):
    """船舶预检仅船东可用。"""
    resp = client.post(
        "/api/v1/agent/compliance/ship", json=_ship_body(), headers=shipper["_headers"]
    )
    assert resp.status_code == 403


def test_compliance_and_router_audit_rows_written():
    """合规预检与统一入口同样留痕（provider=rule-engine，与 LLM 类 Agent 可区分）。"""
    import datetime as _dt

    from app.modules.agent.schemas import CargoComplianceRequest, ShipComplianceRequest

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    cargo_req = CargoComplianceRequest(
        cargo_name="散装水泥", cargo_type="bulk", weight_t=800,
        origin_port="NNG", dest_port="GGU",
        expect_date=_dt.date.today() + _dt.timedelta(days=10),
    )
    ship_req = ShipComplianceRequest(
        ship_name="平陆 001", ship_type="bulk", deadweight_t=1500,
        length_m=60, width_m=12, draft_m=3.5, home_port="GGU",
        cert_no="C-AUDIT-F17", cert_expiry=_dt.date.today() + _dt.timedelta(days=300),
    )
    assert service.screen_cargo_compliance(db, user_id=1, req=cargo_req).target == "cargo"
    assert service.screen_ship_compliance(db, user_id=1, req=ship_req).target == "ship"
    routed = asyncio.run(
        service.route_request(db, user_id=1, role="owner", text="平台支持哪些港口")
    )
    assert routed.intent == "assistant" and routed.dispatched is True

    rows = db.execute(select(AgentCall).order_by(AgentCall.id)).scalars().all()
    names = [r.agent_name for r in rows]
    assert names[:2] == ["compliance_cargo", "compliance_ship"]
    assert "router" in names
    rule_rows = [r for r in rows if r.agent_name != "assistant"]
    assert all(r.provider == "rule-engine" and r.mocked is False for r in rule_rows)
    assert all(r.latency_ms >= 0 for r in rows)
    db.close()


# ---------- F20 统一入口（意图路由） ----------


def test_intent_rules_are_deterministic():
    """分类器可解释且确定：同一输入稳定返回同一意图。"""
    from app.modules.agent.intent import classify

    assert classify("帮我起草合同", role="shipper")[0] == "contract"
    assert classify("烟花爆竹能不能运", role="owner")[0] == "compliance"
    assert classify("我有800吨水泥要发货", role="owner")[0] == "cargo_parse"
    assert classify("平台支持哪些港口", role="owner")[0] == "assistant"
    # 弱信号（仅吨位+港口）按角色区分
    assert classify("南宁到贵港 800吨", role="shipper")[0] == "cargo_parse"
    assert classify("南宁到贵港 800吨", role="owner")[0] == "assistant"


def test_route_to_cargo_parse(shipper, client):
    """发货口吻 → 派发货源解析，返回结构化草稿。"""
    resp = client.post(
        "/api/v1/agent/route",
        json={"text": "我有800吨散装水泥，下周从南宁运到贵港，运费2万5"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "cargo_parse"
    assert body["dispatched"] is True
    assert body["result"]["draft"]["weight_t"] == 800.0
    assert body["result"]["draft"]["origin_port"] == "NNG"


def test_route_to_assistant_fallback(owner, client):
    """长尾问题兜底到客服问答。"""
    resp = client.post(
        "/api/v1/agent/route",
        json={"text": "平台支持哪些港口"},
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "assistant"
    assert body["dispatched"] is True
    assert "answer" in body["result"]


def test_route_compliance_text_screening(owner, client):
    """合规提问 → 文本级词表初筛（block）。"""
    resp = client.post(
        "/api/v1/agent/route",
        json={"text": "烟花爆竹能不能运"},
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "compliance"
    assert body["dispatched"] is True
    assert body["result"]["level"] == "block"


def test_route_cargo_parse_wrong_role_not_dispatched(shipper, owner, client):
    """船东发货口吻 → 识别出意图但不派发，给出切换角色的引导语（不抛错）。"""
    resp = client.post(
        "/api/v1/agent/route",
        json={"text": "我有800吨水泥要发货"},
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "cargo_parse"
    assert body["dispatched"] is False
    assert "货主" in body["message"]


def test_route_contract_needs_order_context(shipper, client):
    """合同意图缺订单上下文 → 不派发，引导去订单页。"""
    resp = client.post(
        "/api/v1/agent/route",
        json={"text": "帮我生成一份合同"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "contract"
    assert body["dispatched"] is False
    assert "订单" in body["message"]


def test_route_contract_with_order_context(shipper, owner, port_user, client):
    """带订单上下文的合同意图 → 直接派发并返回合同草稿。"""
    oid = _make_order(shipper, owner, client, port_headers=port_user["_headers"])
    resp = client.post(
        "/api/v1/agent/route",
        json={"text": "帮我生成这份订单的合同", "order_id": oid},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "contract"
    assert body["dispatched"] is True
    assert body["result"]["order_id"] == oid


def test_route_downstream_failure_is_not_500(shipper, client, monkeypatch):
    """下游 LLM 故障 → 路由层不报 5xx，返回引导语（dispatched=False）。"""
    async def _boom(*, system, user, temperature=0.1):
        raise LLMError("timeout", "LLM 调用超时（模拟）")

    monkeypatch.setattr(service, "llm_gateway", type("M", (), {"chat_json": staticmethod(_boom)}))

    resp = client.post(
        "/api/v1/agent/route",
        json={"text": "我有800吨水泥从南宁运到贵港"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "cargo_parse"
    assert body["dispatched"] is False
    assert "timeout" in body["message"]


def test_route_requires_auth(client):
    """统一入口同样要求登录。"""
    resp = client.post("/api/v1/agent/route", json={"text": "怎么发货"})
    assert resp.status_code == 401
