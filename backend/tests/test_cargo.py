"""F2 货主发货单测试。

覆盖：创建/直发布、列表与状态筛选、draft 编辑、发布/取消状态机、
非法转移拒绝、非货主 403、同港校验、过期日期校验、越权 404。
"""
from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import _auth, _login

API = "/api/v1/cargo/shipments"

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _payload(**overrides) -> dict:
    base = {
        "cargo_name": "散装水泥",
        "cargo_type": "bulk",
        "weight_t": 800,
        "origin_port": "NNG",
        "dest_port": "WUZ",
        "expect_date": TOMORROW,
        "offer_price": 25000,
        "remark": "需防潮",
    }
    base.update(overrides)
    return base


# ---------- 创建 ----------

def test_create_draft_shipment(shipper, client):
    resp = client.post(API, json=_payload(), headers=shipper["_headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "draft"
    assert data["shipper_id"] == shipper["user_id"]


def test_create_and_publish_now(shipper, client):
    resp = client.post(API, json=_payload(publish_now=True), headers=shipper["_headers"])
    assert resp.json()["status"] == "published"


def test_create_same_port_rejected(shipper, client):
    resp = client.post(API, json=_payload(dest_port="NNG"), headers=shipper["_headers"])
    assert resp.status_code == 422


def test_create_past_date_rejected(shipper, client):
    resp = client.post(
        API, json=_payload(expect_date="2020-01-01"), headers=shipper["_headers"]
    )
    assert resp.status_code == 422


def test_create_unknown_port_rejected(shipper, client):
    resp = client.post(API, json=_payload(dest_port="XXX"), headers=shipper["_headers"])
    assert resp.status_code == 422


def test_create_non_shipper_403(owner, client):
    resp = client.post(API, json=_payload(), headers=owner["_headers"])
    assert resp.status_code == 403


# ---------- 列表 ----------

def test_list_my_shipments_with_status_filter(shipper, client):
    client.post(API, json=_payload(), headers=shipper["_headers"])                       # draft
    client.post(API, json=_payload(publish_now=True), headers=shipper["_headers"])       # published
    resp = client.get(API, params={"status": "published"}, headers=shipper["_headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "published"


# ---------- 编辑 ----------

def test_update_draft_shipment(shipper, client):
    sid = client.post(API, json=_payload(), headers=shipper["_headers"]).json()["id"]
    resp = client.patch(f"{API}/{sid}", json={"weight_t": 950}, headers=shipper["_headers"])
    assert resp.status_code == 200
    assert float(resp.json()["weight_t"]) == 950


def test_update_published_rejected(shipper, client):
    sid = client.post(API, json=_payload(publish_now=True), headers=shipper["_headers"]).json()["id"]
    resp = client.patch(f"{API}/{sid}", json={"weight_t": 950}, headers=shipper["_headers"])
    assert resp.status_code == 400


# ---------- 状态机 ----------

def test_publish_then_cancel(shipper, client):
    sid = client.post(API, json=_payload(), headers=shipper["_headers"]).json()["id"]
    assert client.post(f"{API}/{sid}/publish", headers=shipper["_headers"]).json()["status"] == "published"
    assert client.post(f"{API}/{sid}/cancel", headers=shipper["_headers"]).json()["status"] == "cancelled"


def test_double_publish_rejected(shipper, client):
    sid = client.post(API, json=_payload(publish_now=True), headers=shipper["_headers"]).json()["id"]
    resp = client.post(f"{API}/{sid}/publish", headers=shipper["_headers"])
    assert resp.status_code == 400


def test_cancel_twice_rejected(shipper, client):
    sid = client.post(API, json=_payload(publish_now=True), headers=shipper["_headers"]).json()["id"]
    client.post(f"{API}/{sid}/cancel", headers=shipper["_headers"])
    resp = client.post(f"{API}/{sid}/cancel", headers=shipper["_headers"])
    assert resp.status_code == 400


# ---------- 越权 ----------

def test_other_shipper_cannot_access(shipper, client):
    sid = client.post(API, json=_payload(), headers=shipper["_headers"]).json()["id"]
    other = _login(client, f"other-{__name__}")
    resp = client.get(f"{API}/{sid}", headers=_auth(other["access_token"]))
    assert resp.status_code in (403, 404)  # 非货主 403 或不可见
