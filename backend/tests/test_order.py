"""F6 订单域测试：状态机推进 + 防重复出单 + 硬约束前置 + 角色权限。"""
from __future__ import annotations

API_SHIP = "/api/v1/ship/registry"
API_CARGO = "/api/v1/cargo/shipments"
API_ORDER = "/api/v1/order/orders"

CARGO_PAYLOAD = {
    "cargo_name": "水泥熟料",
    "cargo_type": "bulk",
    "weight_t": 1000,
    "origin_port": "NNG",
    "dest_port": "QNZ",
    "expect_date": "2026-10-01",
    "publish_now": True,
}


def _ship_payload(**overrides) -> dict:
    payload = {
        "ship_name": "桂平顺发 88",
        "ship_type": "bulk",
        "deadweight_t": 1500,
        "length_m": 90,
        "width_m": 14,
        "draft_m": 3.5,
        "home_port": "GGU",
        "cert_no": "CERT-F6-000",
        "cert_expiry": "2027-12-31",
    }
    payload.update(overrides)
    return payload


def _make_verified_ship(client, owner, port_user, **overrides) -> int:
    sid = client.post(API_SHIP, json=_ship_payload(**overrides), headers=owner["_headers"]).json()["id"]
    resp = client.post(f"{API_SHIP}/{sid}/verify", json={"approved": True}, headers=port_user["_headers"])
    assert resp.status_code == 200, resp.text
    return sid


def _make_cargo(client, shipper, **overrides) -> dict:
    payload = dict(CARGO_PAYLOAD)
    payload.update(overrides)
    resp = client.post(API_CARGO, json=payload, headers=shipper["_headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_order(client, shipper, cargo_id, ship_id, **extra) -> dict:
    payload = {"cargo_id": cargo_id, "ship_id": ship_id}
    payload.update(extra)
    resp = client.post(API_ORDER, json=payload, headers=shipper["_headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- 全生命周期 ----


def test_order_full_lifecycle(owner, port_user, shipper, client):
    """matched → shipped → completed；每步落时间戳；货源退池。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid, freight_price=12000)

    assert order["status"] == "matched"
    assert order["shipper_id"] != order["owner_id"]
    assert order["freight_price"] == 12000
    assert order["matched_at"] is not None
    assert order["shipped_at"] is None

    # 货源退出撮合池
    my_cargos = client.get(f"{API_CARGO}?status=matched", headers=shipper["_headers"]).json()
    assert my_cargos["items"] and my_cargos["items"][0]["id"] == cargo["id"]

    resp = client.post(f"{API_ORDER}/{order['id']}/ship", headers=owner["_headers"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "shipped"
    assert resp.json()["shipped_at"] is not None

    resp = client.post(f"{API_ORDER}/{order['id']}/complete", headers=shipper["_headers"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
    assert resp.json()["completed_at"] is not None


# ---- 防重复出单 ----


def test_order_duplicate_active_409(owner, port_user, shipper, client):
    """同一货源存在生效订单时再次出单 → 409。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    _make_order(client, shipper, cargo["id"], sid)

    resp = client.post(
        API_ORDER, json={"cargo_id": cargo["id"], "ship_id": sid}, headers=shipper["_headers"]
    )
    assert resp.status_code == 409
    assert "重复" in resp.json()["detail"]


def test_order_cancel_releases_cargo_for_reorder(owner, port_user, shipper, client):
    """撤单后货源释放回撮合池，可再次出单。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)

    resp = client.post(
        f"{API_ORDER}/{order['id']}/cancel",
        json={"reason": "计划变更"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["cancel_reason"] == "计划变更"

    # 货源回到已发布，可再次出单（防重复出单解除）
    reorder = _make_order(client, shipper, cargo["id"], sid)
    assert reorder["status"] == "matched"


# ---- 出单前置校验（复用撮合硬约束）----


def test_order_rejects_unverified_ship(owner, port_user, shipper, client):
    """未审核船舶不可承接订单。"""
    sid = client.post(API_SHIP, json=_ship_payload(), headers=owner["_headers"]).json()["id"]
    cargo = _make_cargo(client, shipper)
    resp = client.post(
        API_ORDER, json={"cargo_id": cargo["id"], "ship_id": sid}, headers=shipper["_headers"]
    )
    assert resp.status_code == 400
    assert "审核" in resp.json()["detail"]


def test_order_rejects_incompatible_pair(owner, port_user, shipper, client):
    """载重不足 / 船型不兼容的组合不可出单。"""
    small_sid = _make_verified_ship(client, owner, port_user, ship_name="小船", cert_no="C-SM", deadweight_t=500)
    tank_sid = _make_verified_ship(client, owner, port_user, ship_name="油轮", cert_no="C-TK", ship_type="tanker")
    cargo = _make_cargo(client, shipper)

    for sid in (small_sid, tank_sid):
        resp = client.post(
            API_ORDER, json={"cargo_id": cargo["id"], "ship_id": sid}, headers=shipper["_headers"]
        )
        assert resp.status_code == 400, resp.text


def test_order_requires_published_cargo(owner, port_user, shipper, client):
    """draft 货源不可出单。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper, publish_now=False)
    resp = client.post(
        API_ORDER, json={"cargo_id": cargo["id"], "ship_id": sid}, headers=shipper["_headers"]
    )
    assert resp.status_code == 400
    assert "发布" in resp.json()["detail"]


def test_order_other_shippers_cargo_400(owner, port_user, shipper, client):
    """不能为他人货源出单。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    resp = client.post(
        API_ORDER, json={"cargo_id": cargo["id"], "ship_id": sid}, headers=owner["_headers"]
    )
    assert resp.status_code in (400, 403)  # 角色先行拦截 403 / 归属校验 400


# ---- 状态机纪律 ----


def test_order_complete_before_ship_400(owner, port_user, shipper, client):
    """未启运不可签收。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)
    resp = client.post(f"{API_ORDER}/{order['id']}/complete", headers=shipper["_headers"])
    assert resp.status_code == 400


def test_order_double_ship_400(owner, port_user, shipper, client):
    """重复启运被拒。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)
    assert client.post(f"{API_ORDER}/{order['id']}/ship", headers=owner["_headers"]).status_code == 200
    resp = client.post(f"{API_ORDER}/{order['id']}/ship", headers=owner["_headers"])
    assert resp.status_code == 400


def test_order_cancel_after_shipped_400(owner, port_user, shipper, client):
    """启运后进入履约期，不可撤单。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)
    client.post(f"{API_ORDER}/{order['id']}/ship", headers=owner["_headers"])
    resp = client.post(f"{API_ORDER}/{order['id']}/cancel", json={"reason": "x"}, headers=shipper["_headers"])
    assert resp.status_code == 400


# ---- 角色与可见性 ----


def test_order_ship_role_guard(owner, port_user, shipper, client):
    """货主不能启运；船东不能签收。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)

    resp = client.post(f"{API_ORDER}/{order['id']}/ship", headers=shipper["_headers"])
    assert resp.status_code == 403
    resp = client.post(f"{API_ORDER}/{order['id']}/complete", headers=owner["_headers"])
    assert resp.status_code == 403


def test_order_owner_can_cancel_matched(owner, port_user, shipper, client):
    """船东亦可撤 matched 订单（货源释放）。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)
    resp = client.post(
        f"{API_ORDER}/{order['id']}/cancel", json={"reason": "船期冲突"}, headers=owner["_headers"]
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_order_list_role_view(owner, port_user, shipper, client):
    """货主/船东各自视角看到同一订单；状态过滤生效。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)

    as_shipper = client.get(API_ORDER, headers=shipper["_headers"]).json()
    assert as_shipper["total"] == 1
    assert as_shipper["items"][0]["id"] == order["id"]

    as_owner = client.get(API_ORDER, headers=owner["_headers"]).json()
    assert as_owner["total"] == 1 and as_owner["items"][0]["id"] == order["id"]

    filtered = client.get(f"{API_ORDER}?status=shipped", headers=shipper["_headers"]).json()
    assert filtered["total"] == 0

    matched_only = client.get(f"{API_ORDER}?status=matched", headers=shipper["_headers"]).json()
    assert matched_only["total"] == 1


def test_order_detail_participant_only(owner, port_user, shipper, client):
    """非参与方查看订单 → 404。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    order = _make_order(client, shipper, cargo["id"], sid)
    resp = client.get(f"{API_ORDER}/{order['id']}", headers=port_user["_headers"])
    assert resp.status_code == 404


def test_order_role_guard_port(port_user, shipper, client):
    """港口角色不可创建订单。"""
    resp = client.post(API_ORDER, json={"cargo_id": 1, "ship_id": 1}, headers=port_user["_headers"])
    assert resp.status_code == 403


# ---- 纯引擎：pair_violation 单一事实来源 ----


def test_engine_pair_violation():
    from datetime import date

    from app.modules.match.engine import CargoInput, ShipInput, pair_violation

    cargo = CargoInput(
        id=1, cargo_type="bulk", weight_t=1000, origin_port="NNG",
        dest_port="QNZ", expect_date=date(2026, 10, 1), status="published",
    )
    ok_ship = ShipInput(
        id=1, ship_type="bulk", deadweight_t=1500, draft_m=3.5,
        home_port="NNG", cert_expiry=date(2027, 12, 31), status="verified",
    )
    assert pair_violation(cargo, ok_ship) is None

    # 四类硬约束逐一违反
    assert "审核" in pair_violation(
        cargo, ShipInput(
            id=2, ship_type="bulk", deadweight_t=1500, draft_m=3.5,
            home_port="NNG", cert_expiry=date(2027, 12, 31), status="pending_verify",
        )
    )
    assert "证书" in pair_violation(
        cargo, ShipInput(
            id=3, ship_type="bulk", deadweight_t=1500, draft_m=3.5,
            home_port="NNG", cert_expiry=date(2026, 9, 20), status="verified",
        )
    )
    assert "载重" in pair_violation(
        cargo, ShipInput(
            id=4, ship_type="bulk", deadweight_t=500, draft_m=3.5,
            home_port="NNG", cert_expiry=date(2027, 12, 31), status="verified",
        )
    )
    assert "兼容" in pair_violation(
        cargo, ShipInput(
            id=5, ship_type="tanker", deadweight_t=1500, draft_m=3.5,
            home_port="NNG", cert_expiry=date(2027, 12, 31), status="verified",
        )
    )
