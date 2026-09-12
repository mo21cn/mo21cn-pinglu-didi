"""F7 支付域测试：状态机 + 防重复支付 + 幂等回调 + 撤单联动 + 角色权限。"""

from __future__ import annotations

API_SHIP = "/api/v1/ship/registry"
API_CARGO = "/api/v1/cargo/shipments"
API_ORDER = "/api/v1/order/orders"
API_PAY = "/api/v1/payment/payments"

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
        "cert_no": "CERT-F7-000",
        "cert_expiry": "2027-12-31",
    }
    payload.update(overrides)
    return payload


def _make_verified_ship(client, owner, port_user, **overrides) -> int:
    sid = client.post(API_SHIP, json=_ship_payload(**overrides), headers=owner["_headers"]).json()[
        "id"
    ]
    resp = client.post(
        f"{API_SHIP}/{sid}/verify", json={"approved": True}, headers=port_user["_headers"]
    )
    assert resp.status_code == 200, resp.text
    return sid


def _make_cargo(client, shipper, **overrides) -> dict:
    payload = dict(CARGO_PAYLOAD)
    payload.update(overrides)
    resp = client.post(API_CARGO, json=payload, headers=shipper["_headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_matched_order(client, shipper, owner, port_user, freight_price=12000) -> dict:
    """造一条 matched 订单（含已审船 + 已发布货 + 出单）。"""
    sid = _make_verified_ship(client, owner, port_user)
    cargo = _make_cargo(client, shipper)
    resp = client.post(
        API_ORDER,
        json={"cargo_id": cargo["id"], "ship_id": sid, "freight_price": freight_price},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- 发起支付 ----


def test_payment_create_pending(owner, port_user, shipper, client):
    """货主对 matched 订单发起支付 → pending，金额锁定订单运费。"""
    order = _make_matched_order(client, shipper, owner, port_user, freight_price=12000)
    resp = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"])
    assert resp.status_code == 201, resp.text
    pay = resp.json()
    assert pay["status"] == "pending"
    assert pay["amount"] == 12000
    assert pay["payer_id"] == order["shipper_id"]
    assert pay["payee_id"] == order["owner_id"]
    assert pay["channel"] == "mock"
    assert pay["created_at"] is not None
    assert pay["paid_at"] is None


def test_payment_duplicate_409(owner, port_user, shipper, client):
    """同一订单重复发起支付 → 409（防重复支付）。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    assert (
        client.post(
            API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"]
        ).status_code
        == 201
    )
    resp = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"])
    assert resp.status_code == 409
    assert "重复" in resp.json()["detail"]


def test_payment_requires_freight_price(owner, port_user, shipper, client):
    """面议订单（freight_price 空）不可发起支付。"""
    order = _make_matched_order(client, shipper, owner, port_user, freight_price=None)
    resp = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"])
    assert resp.status_code == 400
    assert "面议" in resp.json()["detail"]


def test_payment_requires_matched_order(owner, port_user, shipper, client):
    """启运后订单不可发起支付。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    assert (
        client.post(f"{API_ORDER}/{order['id']}/ship", headers=owner["_headers"]).status_code == 200
    )
    resp = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"])
    assert resp.status_code == 400
    assert "不可发起支付" in resp.json()["detail"]


# ---- 支付成功回调（幂等）----


def test_payment_mock_pay_transitions(owner, port_user, shipper, client):
    """模拟支付成功：pending → paid，流水号与时间戳落库。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    pay = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"]).json()

    resp = client.post(
        f"{API_PAY}/{pay['id']}/mock-pay",
        json={"transaction_no": "TX-20260910-001"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text
    paid = resp.json()
    assert paid["status"] == "paid"
    assert paid["transaction_no"] == "TX-20260910-001"
    assert paid["paid_at"] is not None

    # 幂等：重复回调不重复记账，paid_at 不变
    again = client.post(f"{API_PAY}/{pay['id']}/mock-pay", json={}, headers=shipper["_headers"])
    assert again.status_code == 200
    assert again.json()["status"] == "paid"
    assert again.json()["paid_at"] == paid["paid_at"]
    assert again.json()["transaction_no"] == "TX-20260910-001"


def test_payment_mock_pay_after_refunded_400(owner, port_user, shipper, client):
    """已退款的支付单不可再支付。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    pay = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"]).json()
    client.post(f"{API_PAY}/{pay['id']}/mock-pay", json={}, headers=shipper["_headers"])
    client.post(
        f"{API_ORDER}/{order['id']}/cancel",
        json={"reason": "计划变更"},
        headers=shipper["_headers"],
    )

    resp = client.post(f"{API_PAY}/{pay['id']}/mock-pay", json={}, headers=shipper["_headers"])
    assert resp.status_code == 400


# ---- 撤单联动结算 ----


def test_payment_cancel_after_paid_refunds(owner, port_user, shipper, client):
    """已支付订单撤单 → 全额退款（refunded + refund_no + refunded_at）。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    pay = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"]).json()
    client.post(f"{API_PAY}/{pay['id']}/mock-pay", json={}, headers=shipper["_headers"])

    resp = client.post(
        f"{API_ORDER}/{order['id']}/cancel",
        json={"reason": "计划变更"},
        headers=shipper["_headers"],
    )
    assert resp.status_code == 200, resp.text

    settled = client.get(f"{API_PAY}/order/{order['id']}", headers=shipper["_headers"]).json()
    assert settled["status"] == "refunded"
    assert settled["refund_no"].startswith("RF-")
    assert settled["refunded_at"] is not None
    assert settled["transaction_no"]  # 原支付流水仍留痕


def test_payment_cancel_while_pending_closes(owner, port_user, shipper, client):
    """待支付订单撤单 → 支付单关闭（closed）。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"])

    resp = client.post(
        f"{API_ORDER}/{order['id']}/cancel",
        json={"reason": "议价未成"},
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text

    settled = client.get(f"{API_PAY}/order/{order['id']}", headers=shipper["_headers"]).json()
    assert settled["status"] == "closed"
    assert settled["closed_at"] is not None
    assert settled["refund_no"] == ""


def test_payment_cancel_without_payment_noop(owner, port_user, shipper, client):
    """未发起支付的订单撤单 → 无支付单，按订单查 → 404。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    client.post(
        f"{API_ORDER}/{order['id']}/cancel", json={"reason": "x"}, headers=shipper["_headers"]
    )
    resp = client.get(f"{API_PAY}/order/{order['id']}", headers=shipper["_headers"])
    assert resp.status_code == 404


# ---- 角色与可见性 ----


def test_payment_owner_cannot_create(owner, port_user, shipper, client):
    """船东角色不可发起支付 → 403。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    resp = client.post(API_PAY, json={"order_id": order["id"]}, headers=owner["_headers"])
    assert resp.status_code == 403


def test_payment_detail_participant_only(owner, port_user, shipper, client):
    """非参与方（港口角色）查看支付单 → 404。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    pay = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"]).json()
    resp = client.get(f"{API_PAY}/{pay['id']}", headers=port_user["_headers"])
    assert resp.status_code == 404


def test_payment_payee_cannot_mock_pay(owner, port_user, shipper, client):
    """收款方（船东）不可替货主支付 → 404（非付款人）。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    pay = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"]).json()
    resp = client.post(f"{API_PAY}/{pay['id']}/mock-pay", json={}, headers=owner["_headers"])
    assert resp.status_code == 404


def test_payment_get_by_order_requires_participant(owner, port_user, shipper, client):
    """按订单查支付单：船东参与方可看，无关用户 404。"""
    order = _make_matched_order(client, shipper, owner, port_user)
    client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"])
    as_owner = client.get(f"{API_PAY}/order/{order['id']}", headers=owner["_headers"])
    assert as_owner.status_code == 200
    as_outsider = client.get(f"{API_PAY}/order/{order['id']}", headers=port_user["_headers"])
    assert as_outsider.status_code == 404


# ---- 全链路闭环（货-船-港-单-款）----


def test_payment_full_trade_loop(owner, port_user, shipper, client):
    """端到端：出单 → 支付 → 启运 → 签收，支付单保持 paid 终态。"""
    order = _make_matched_order(client, shipper, owner, port_user, freight_price=25600)
    pay = client.post(API_PAY, json={"order_id": order["id"]}, headers=shipper["_headers"]).json()
    assert (
        client.post(
            f"{API_PAY}/{pay['id']}/mock-pay", json={}, headers=shipper["_headers"]
        ).status_code
        == 200
    )
    assert (
        client.post(f"{API_ORDER}/{order['id']}/ship", headers=owner["_headers"]).status_code == 200
    )
    resp = client.post(f"{API_ORDER}/{order['id']}/complete", headers=shipper["_headers"])
    assert resp.status_code == 200

    final = client.get(f"{API_PAY}/order/{order['id']}", headers=shipper["_headers"]).json()
    assert final["status"] == "paid"
    assert final["amount"] == 25600
    assert final["transaction_no"]
