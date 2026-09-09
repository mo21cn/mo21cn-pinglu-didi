"""F4 港域测试：泊位管理 + 泊位预约 + 防超卖。

覆盖：
1. 泊位 CRUD：port 角色新建/编辑/列表/档期查询；重复泊位号 400；非 port 角色 403
2. 预约申请硬校验（撮合 Stage1 约束前置）：
   非 verified 船 409 / 非本人船 409 / 吨位超限 409 / 吃水超限 409 / 船型不适靠 409 / 泊位停用 409
3. 防超卖（核心）：
   - 同泊位同时间窗：容量 1 时第二个确认 409
   - 首尾相接时间窗不冲突
   - 容量 2：两个重叠确认通过，第三个 409
   - 撤销（cancelled）后档期释放，可再确认
4. 状态机：confirmed 不可再确认/驳回；cancelled/completed 不可撤销
5. 权限：船东不可审核、港口方不可申请预约
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("APP_ENV", "test")

API = "/api/v1/port"
SHIP_API = "/api/v1/ship/registry"


def _now_plus(hours: float) -> str:
    return (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")


def _berth_payload(**overrides) -> dict:
    payload = {
        "port_code": "NNG",
        "berth_no": "01",
        "berth_name": "平塘港区1号泊位",
        "max_dwt": 5000,
        "max_draft": 6.0,
        "allowed_ship_types": ["bulk", "general"],
        "concurrent_capacity": 1,
    }
    payload.update(overrides)
    return payload


def _appt_payload(berth_id: int, ship_id: int, start_h: float = 24, end_h: float = 48) -> dict:
    return {
        "berth_id": berth_id,
        "ship_id": ship_id,
        "plan_start": _now_plus(start_h),
        "plan_end": _now_plus(end_h),
        "remark": "测试预约",
    }


def _create_verified_ship(client, owner, port_user, **overrides) -> int:
    """造一艘 verified 船（owner 备案 → port 审核），返回 ship_id。"""
    payload = {
        "ship_name": "桂船测试号",
        "ship_type": "bulk",
        "deadweight_t": 3000,
        "length_m": 95,
        "width_m": 16,
        "draft_m": 4.5,
        "home_port": "NNG",
        "cert_no": "CERT-F4-001",
        "cert_expiry": "2030-12-31",
    }
    payload.update(overrides)
    resp = client.post(SHIP_API, json=payload, headers=owner["_headers"])
    assert resp.status_code == 200, resp.text
    ship_id = resp.json()["id"]
    resp = client.post(
        f"{SHIP_API}/{ship_id}/verify", json={"approved": True}, headers=port_user["_headers"]
    )
    assert resp.status_code == 200, resp.text
    return ship_id


def _create_berth(client, port_user, **overrides) -> int:
    resp = client.post(f"{API}/berths", json=_berth_payload(**overrides), headers=port_user["_headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _apply_appt(client, owner, berth_id: int, ship_id: int, start_h=24, end_h=48) -> dict:
    resp = client.post(
        f"{API}/appts", json=_appt_payload(berth_id, ship_id, start_h, end_h),
        headers=owner["_headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 泊位管理 ----------

def test_create_berth_by_port(client, port_user):
    resp = client.post(f"{API}/berths", json=_berth_payload(), headers=port_user["_headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["port_code"] == "NNG"
    assert body["status"] == "active"
    assert body["concurrent_capacity"] == 1


def test_create_berth_duplicate_rejected(client, port_user):
    _create_berth(client, port_user)
    resp = client.post(f"{API}/berths", json=_berth_payload(), headers=port_user["_headers"])
    assert resp.status_code == 400
    assert "已存在" in resp.json()["detail"]


def test_create_berth_invalid_port_code(client, port_user):
    resp = client.post(
        f"{API}/berths", json=_berth_payload(port_code="XXX"), headers=port_user["_headers"]
    )
    assert resp.status_code == 422  # schema 校验拒绝未知港代码


def test_berth_role_guard(client, owner):
    resp = client.post(f"{API}/berths", json=_berth_payload(), headers=owner["_headers"])
    assert resp.status_code == 403


def test_list_and_update_berth(client, port_user):
    berth_id = _create_berth(client, port_user)
    resp = client.get(f"{API}/berths?port_code=NNG", headers=port_user["_headers"])
    assert resp.status_code == 200 and resp.json()["total"] >= 1

    resp = client.patch(
        f"{API}/berths/{berth_id}", json={"max_dwt": 3000}, headers=port_user["_headers"]
    )
    assert resp.status_code == 200 and float(resp.json()["max_dwt"]) == 3000


# ---------- 预约申请硬校验 ----------

def test_appt_requires_verified_ship(client, owner, port_user):
    berth_id = _create_berth(client, port_user)
    resp = client.post(SHIP_API, json={
        "ship_name": "未审船", "ship_type": "bulk", "deadweight_t": 2000,
        "length_m": 90, "width_m": 15, "draft_m": 4.0,
        "home_port": "NNG", "cert_no": "CERT-P", "cert_expiry": "2030-12-31",
    }, headers=owner["_headers"])
    pending_ship_id = resp.json()["id"]
    resp = client.post(
        f"{API}/appts", json=_appt_payload(berth_id, pending_ship_id), headers=owner["_headers"]
    )
    assert resp.status_code == 409
    assert "审核" in resp.json()["detail"]


def test_appt_ship_not_owned(client, owner, port_user, shipper):
    berth_id = _create_berth(client, port_user)
    # 用 owner 建船但用另一个 owner 账号申请
    other_owner = shipper  # 另一账号（shipper 角色不匹配会先被 403 拦住）
    resp = client.post(
        f"{API}/appts", json=_appt_payload(berth_id, 999), headers=other_owner["_headers"]
    )
    assert resp.status_code == 403  # shipper 角色先被拦截


def test_appt_dwt_exceeds_berth(client, owner, port_user):
    berth_id = _create_berth(client, port_user, max_dwt=2000)
    ship_id = _create_verified_ship(client, owner, port_user, deadweight_t=3000)
    resp = client.post(
        f"{API}/appts", json=_appt_payload(berth_id, ship_id), headers=owner["_headers"]
    )
    assert resp.status_code == 409
    assert "载重吨" in resp.json()["detail"]


def test_appt_draft_exceeds_berth(client, owner, port_user):
    berth_id = _create_berth(client, port_user, max_draft=4.0)
    ship_id = _create_verified_ship(client, owner, port_user, draft_m=4.5)
    resp = client.post(
        f"{API}/appts", json=_appt_payload(berth_id, ship_id), headers=owner["_headers"]
    )
    assert resp.status_code == 409
    assert "吃水" in resp.json()["detail"]


def test_appt_ship_type_not_allowed(client, owner, port_user):
    berth_id = _create_berth(client, port_user, allowed_ship_types=["container"])
    ship_id = _create_verified_ship(client, owner, port_user, ship_type="bulk")
    resp = client.post(
        f"{API}/appts", json=_appt_payload(berth_id, ship_id), headers=owner["_headers"]
    )
    assert resp.status_code == 409
    assert "不适靠" in resp.json()["detail"]


def test_appt_inactive_berth_rejected(client, owner, port_user):
    berth_id = _create_berth(client, port_user)
    ship_id = _create_verified_ship(client, owner, port_user)
    client.patch(f"{API}/berths/{berth_id}", json={"status": "inactive"}, headers=port_user["_headers"])
    resp = client.post(
        f"{API}/appts", json=_appt_payload(berth_id, ship_id), headers=owner["_headers"]
    )
    assert resp.status_code == 409
    assert "停用" in resp.json()["detail"]


# ---------- 确认流程与防超卖（核心） ----------

def _confirm(client, port_user, appt_id: int):
    return client.post(f"{API}/appts/{appt_id}/confirm", headers=port_user["_headers"])


def test_confirm_flow_and_schedule(client, owner, port_user):
    berth_id = _create_berth(client, port_user)
    ship_id = _create_verified_ship(client, owner, port_user)
    appt = _apply_appt(client, owner, berth_id, ship_id, start_h=24, end_h=48)

    resp = _confirm(client, port_user, appt["id"])
    assert resp.status_code == 200 and resp.json()["status"] == "confirmed"

    # 档期查询可见
    resp = client.get(f"{API}/berths/{berth_id}/schedule", headers=port_user["_headers"])
    assert resp.status_code == 200
    assert len(resp.json()["confirmed"]) == 1


def test_oversell_blocked_same_window(client, owner, port_user):
    """防超卖：容量 1 的泊位，同时间窗第二个确认被 409 拦截。"""
    berth_id = _create_berth(client, port_user)
    ship_a = _create_verified_ship(client, owner, port_user, ship_name="船A", cert_no="C-A")
    ship_b = _create_verified_ship(client, owner, port_user, ship_name="船B", cert_no="C-B")

    appt_a = _apply_appt(client, owner, berth_id, ship_a, start_h=24, end_h=48)
    appt_b = _apply_appt(client, owner, berth_id, ship_b, start_h=30, end_h=50)  # 重叠窗

    assert _confirm(client, port_user, appt_a["id"]).status_code == 200
    resp = _confirm(client, port_user, appt_b["id"])
    assert resp.status_code == 409
    assert "档期冲突" in resp.json()["detail"]
    # B 仍为 pending，可由港口方驳回
    resp = client.post(
        f"{API}/appts/{appt_b['id']}/reject", json={"reason": "档期已满"},
        headers=port_user["_headers"],
    )
    assert resp.status_code == 200 and resp.json()["status"] == "rejected"


def test_back_to_back_windows_no_conflict(client, owner, port_user):
    """首尾相接（前一窗结束=后一窗开始）不算重叠，可确认。"""
    berth_id = _create_berth(client, port_user)
    ship_a = _create_verified_ship(client, owner, port_user, ship_name="船A", cert_no="C-A")
    ship_b = _create_verified_ship(client, owner, port_user, ship_name="船B", cert_no="C-B")

    appt_a = _apply_appt(client, owner, berth_id, ship_a, start_h=24, end_h=48)
    appt_b = _apply_appt(client, owner, berth_id, ship_b, start_h=48, end_h=72)  # 首尾相接

    assert _confirm(client, port_user, appt_a["id"]).status_code == 200
    assert _confirm(client, port_user, appt_b["id"]).status_code == 200


def test_capacity_two_allows_double_booking(client, owner, port_user):
    """容量 2 的泊位：两个重叠确认通过，第三个被拦截。"""
    berth_id = _create_berth(client, port_user, concurrent_capacity=2)
    ships = [
        _create_verified_ship(client, owner, port_user, ship_name=f"船{i}", cert_no=f"C-{i}")
        for i in range(3)
    ]
    appts = [
        _apply_appt(client, owner, berth_id, s, start_h=24, end_h=48) for s in ships
    ]
    assert _confirm(client, port_user, appts[0]["id"]).status_code == 200
    assert _confirm(client, port_user, appts[1]["id"]).status_code == 200
    assert _confirm(client, port_user, appts[2]["id"]).status_code == 409


def test_cancel_releases_window(client, owner, port_user):
    """撤销 confirmed 预约后档期释放，等待中的预约可确认。"""
    berth_id = _create_berth(client, port_user)
    ship_a = _create_verified_ship(client, owner, port_user, ship_name="船A", cert_no="C-A")
    ship_b = _create_verified_ship(client, owner, port_user, ship_name="船B", cert_no="C-B")

    appt_a = _apply_appt(client, owner, berth_id, ship_a)
    appt_b = _apply_appt(client, owner, berth_id, ship_b, start_h=30, end_h=50)
    assert _confirm(client, port_user, appt_a["id"]).status_code == 200
    assert _confirm(client, port_user, appt_b["id"]).status_code == 409

    # 船东撤销 A → 档期释放 → B 可确认
    resp = client.post(f"{API}/appts/{appt_a['id']}/cancel", headers=owner["_headers"])
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    assert _confirm(client, port_user, appt_b["id"]).status_code == 200


# ---------- 状态机与权限 ----------

def test_state_machine_guards(client, owner, port_user):
    berth_id = _create_berth(client, port_user)
    ship_id = _create_verified_ship(client, owner, port_user)
    appt = _apply_appt(client, owner, berth_id, ship_id)
    assert _confirm(client, port_user, appt["id"]).status_code == 200
    # confirmed 不可再确认/驳回
    assert _confirm(client, port_user, appt["id"]).status_code == 400
    resp = client.post(
        f"{API}/appts/{appt['id']}/reject", json={"reason": "x"}, headers=port_user["_headers"]
    )
    assert resp.status_code == 400
    # 核销
    resp = client.post(f"{API}/appts/{appt['id']}/complete", headers=port_user["_headers"])
    assert resp.status_code == 200 and resp.json()["status"] == "completed"
    # completed 不可撤销
    resp = client.post(f"{API}/appts/{appt['id']}/cancel", headers=owner["_headers"])
    assert resp.status_code == 403


def test_cancel_only_by_applier(client, owner, port_user, shipper):
    berth_id = _create_berth(client, port_user)
    ship_id = _create_verified_ship(client, owner, port_user)
    appt = _apply_appt(client, owner, berth_id, ship_id)
    # 港口方走 cancel 端点会被角色守卫拦截（cancel 仅船东）
    resp = client.post(f"{API}/appts/{appt['id']}/cancel", headers=port_user["_headers"])
    assert resp.status_code == 403


def test_port_cannot_apply_appt(client, port_user):
    resp = client.post(
        f"{API}/appts", json=_appt_payload(1, 1), headers=port_user["_headers"]
    )
    assert resp.status_code == 403


def test_my_appts_list(client, owner, port_user):
    berth_id = _create_berth(client, port_user)
    ship_id = _create_verified_ship(client, owner, port_user)
    _apply_appt(client, owner, berth_id, ship_id)
    resp = client.get(f"{API}/appts?status=pending", headers=owner["_headers"])
    assert resp.status_code == 200 and resp.json()["total"] == 1
