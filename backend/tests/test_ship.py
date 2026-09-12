"""F3 船东船舶备案测试。

覆盖：备案默认待审、编辑、verified 改关键信息降级重审、
港口方审核通过/驳回、重复审核拒绝、非船东 403、非港口方审核 403、
过期证书校验、越权 404、待审核列表。
"""

from __future__ import annotations

from datetime import date, timedelta

API = "/api/v1/ship/registry"

NEXT_YEAR = (date.today() + timedelta(days=365)).isoformat()


def _payload(**overrides) -> dict:
    base = {
        "ship_name": "平陆001",
        "ship_type": "bulk",
        "deadweight_t": 2000,
        "length_m": 88.0,
        "width_m": 13.6,
        "draft_m": 3.8,
        "home_port": "NNG",
        "cert_no": "CERT-2026-0001",
        "cert_expiry": NEXT_YEAR,
    }
    base.update(overrides)
    return base


# ---------- 备案 ----------


def test_register_ship_defaults_pending(owner, client):
    resp = client.post(API, json=_payload(), headers=owner["_headers"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending_verify"
    assert resp.json()["owner_id"] == owner["user_id"]


def test_register_expired_cert_rejected(owner, client):
    resp = client.post(API, json=_payload(cert_expiry="2020-01-01"), headers=owner["_headers"])
    assert resp.status_code == 422


def test_register_non_owner_403(shipper, client):
    resp = client.post(API, json=_payload(), headers=shipper["_headers"])
    assert resp.status_code == 403


def test_register_draft_validation(owner, client):
    resp = client.post(API, json=_payload(draft_m=20), headers=owner["_headers"])
    assert resp.status_code == 422


# ---------- 列表与详情 ----------


def test_list_my_fleet_with_filter(owner, client):
    client.post(API, json=_payload(ship_name="A船"), headers=owner["_headers"])
    resp = client.get(API, params={"status": "pending_verify"}, headers=owner["_headers"])
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_other_owner_cannot_see(owner, client):
    from tests.conftest import _auth, _login

    sid = client.post(API, json=_payload(), headers=owner["_headers"]).json()["id"]
    other = _login(client, f"other-owner-{__name__}")
    # other 默认 shipper，先切 owner
    h = _auth(other["access_token"])
    client.post("/api/v1/auth/bind-role", json={"role": "owner"}, headers=h)
    h = _auth(
        client.post("/api/v1/auth/switch-role", json={"role": "owner"}, headers=h).json()[
            "access_token"
        ]
    )
    resp = client.get(f"{API}/{sid}", headers=h)
    assert resp.status_code == 404


# ---------- 编辑与降级重审 ----------


def test_update_pending_ship(owner, client):
    sid = client.post(API, json=_payload(), headers=owner["_headers"]).json()["id"]
    resp = client.patch(f"{API}/{sid}", json={"home_port": "GGU"}, headers=owner["_headers"])
    assert resp.status_code == 200


def test_verified_update_critical_back_to_pending(owner, port_user, client):
    sid = client.post(API, json=_payload(), headers=owner["_headers"]).json()["id"]
    # 审核通过
    resp = client.post(
        f"{API}/{sid}/verify", json={"approved": True}, headers=port_user["_headers"]
    )
    assert resp.json()["status"] == "verified"
    # 改载重吨（关键信息）→ 自动降级待审
    resp = client.patch(f"{API}/{sid}", json={"deadweight_t": 2500}, headers=owner["_headers"])
    assert resp.json()["status"] == "pending_verify"


# ---------- 审核 ----------


def test_verify_approve_and_reject(owner, port_user, client):
    sid1 = client.post(API, json=_payload(cert_no="C-1"), headers=owner["_headers"]).json()["id"]
    sid2 = client.post(API, json=_payload(cert_no="C-2"), headers=owner["_headers"]).json()["id"]

    r1 = client.post(f"{API}/{sid1}/verify", json={"approved": True}, headers=port_user["_headers"])
    assert r1.json()["status"] == "verified"
    assert r1.json()["reject_reason"] == ""

    r2 = client.post(
        f"{API}/{sid2}/verify",
        json={"approved": False, "reason": "证书号与登记不符"},
        headers=port_user["_headers"],
    )
    assert r2.json()["status"] == "rejected"
    assert r2.json()["reject_reason"] == "证书号与登记不符"


def test_double_verify_rejected(owner, port_user, client):
    sid = client.post(API, json=_payload(), headers=owner["_headers"]).json()["id"]
    client.post(f"{API}/{sid}/verify", json={"approved": True}, headers=port_user["_headers"])
    resp = client.post(
        f"{API}/{sid}/verify", json={"approved": True}, headers=port_user["_headers"]
    )
    assert resp.status_code == 400


def test_verify_by_non_port_403(owner, shipper, client):
    sid = client.post(API, json=_payload(), headers=owner["_headers"]).json()["id"]
    resp = client.post(f"{API}/{sid}/verify", json={"approved": True}, headers=shipper["_headers"])
    assert resp.status_code == 403


def test_pending_list(port_user, owner, client):
    client.post(API, json=_payload(ship_name="待审船"), headers=owner["_headers"])
    resp = client.get("/api/v1/ship/registry-pending", headers=port_user["_headers"])
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
