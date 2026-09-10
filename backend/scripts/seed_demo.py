"""联调种子数据脚本 —— 本地端到端联调用（E2E）。

用法（backend 目录下）：
    C:/Users/Administrator/.venvs/pinglu-didi/Scripts/python.exe scripts/seed_demo.py

前置：后端已启动（uvicorn，WECHAT_MOCK=true）。

生成内容（登录幂等：openid=mock-openid-{code}，用户唯一；数据按次追加）：
  - 货主 seed-shipper：发布 2 个货源（1 个散货已发布 + 1 个集装箱草稿）
  - 船东 seed-owner：备案 2 条船（1 条港口方已审核通过 + 1 条待审核）
  - 港口 seed-port：创建 1 个泊位（多用途，容量 2）

小程序侧固定身份登录：开发者工具 Storage 面板设置 dev_login_code=seed-shipper /
seed-owner / seed-port，再点登录即以对应种子账号进入。
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"

SHIPPER_CODE = "seed-shipper"
OWNER_CODE = "seed-owner"
PORT_CODE = "seed-port"


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {err_body[:300]}") from None


def login(code: str, role: str) -> tuple[dict, str]:
    """登录并切换到目标角色，返回 (user, token)。"""
    resp = call("POST", "/auth/login", body={"code": code})
    token = resp["access_token"]
    roles = resp.get("roles", [])
    if role not in roles:
        call("POST", "/auth/bind-role", token=token, body={"role": role})
        token = call("POST", "/auth/switch-role", token=token, body={"role": role})["access_token"]
    elif resp.get("current_role") != role:
        token = call("POST", "/auth/switch-role", token=token, body={"role": role})["access_token"]
    user = call("GET", "/auth/me", token=token)
    return user, token


def main() -> None:
    print("== 登录三角色（Mock） ==")
    shipper, tok_shipper = login(SHIPPER_CODE, "shipper")
    owner, tok_owner = login(OWNER_CODE, "owner")
    port_user, tok_port = login(PORT_CODE, "port")
    print(f"货主 user_id={shipper['user_id']} / 船东 user_id={owner['user_id']} / 港口 user_id={port_user['user_id']}")

    print("== 港口方：创建泊位（南宁平塘港区 1 号多用途泊位） ==")
    berth = call(
        "POST", "/port/berths", token=tok_port,
        body={
            "port_code": "NNG",
            "berth_no": "PT-01",
            "berth_name": "平塘港区1号多用途泊位",
            "max_dwt": 3000,
            "max_draft": 6.0,
            "allowed_ship_types": ["bulk", "container", "general"],
            "concurrent_capacity": 2,
        },
    )
    print(f"泊位 #{berth['id']} {berth['berth_name']} 已创建")

    print("== 货主：发布货源 ==")
    cargo1 = call(
        "POST", "/cargo/shipments", token=tok_shipper,
        body={
            "cargo_name": "水泥熟料 1200 吨",
            "cargo_type": "bulk",
            "weight_t": 1200,
            "volume_m3": 800,
            "origin_port": "NNG",
            "dest_port": "GGU",
            "expect_date": "2026-09-15",
            "freight_price": 48000,
        },
    )
    call("POST", f"/cargo/shipments/{cargo1['id']}/publish", token=tok_shipper)
    print(f"货源 #{cargo1['id']} {cargo1['cargo_name']} 已发布进撮合池")

    cargo2 = call(
        "POST", "/cargo/shipments", token=tok_shipper,
        body={
            "cargo_name": "集装箱设备 8 标箱",
            "cargo_type": "container",
            "weight_t": 200,
            "volume_m3": 160,
            "origin_port": "GGU",
            "dest_port": "WUZ",
            "expect_date": "2026-09-20",
        },
    )
    print(f"货源 #{cargo2['id']} {cargo2['cargo_name']} 保留草稿（freight_price 面议）")

    print("== 船东：备案船舶 ==")
    ship1 = call(
        "POST", "/ship/registry", token=tok_owner,
        body={
            "ship_name": "桂平航 6688",
            "ship_type": "bulk",
            "deadweight_t": 1500,
            "length_m": 88,
            "width_m": 13.5,
            "draft_m": 3.4,
            "home_port": "NNG",
            "cert_no": "CERT-2026-06688",
            "cert_expiry": "2027-08-31",
        },
    )
    call("POST", f"/ship/registry/{ship1['id']}/verify", token=tok_port,
         body={"approved": True, "reason": "证件齐全，准予入池"})
    print(f"船舶 #{ship1['id']} {ship1['ship_name']} 已审核通过（verified）")

    ship2 = call(
        "POST", "/ship/registry", token=tok_owner,
        body={
            "ship_name": "横州集运 101",
            "ship_type": "container",
            "deadweight_t": 900,
            "length_m": 75,
            "width_m": 12,
            "draft_m": 2.9,
            "home_port": "GGU",
            "cert_no": "CERT-2026-01101",
            "cert_expiry": "2027-05-31",
        },
    )
    print(f"船舶 #{ship2['id']} {ship2['ship_name']} 待港口方审核（registry-pending 可见）")

    print("== 撮合冒烟：货源 #1 找候选船 ==")
    match = call("POST", f"/match/cargos/{cargo1['id']}/ships", token=tok_shipper, body={})
    print(f"候选 {match['total']} 艘：")
    for c in match["items"]:
        print(f"  船 #{c['ship_id']} {c['ship_name']} score={c['score']:.1f} breakdown={c['breakdown']}")
    print(f"未入局统计：{match.get('filter_stats', {})}")

    print("\n种子数据就绪。小程序联调身份：")
    print("  开发者工具 Storage 面板添加 dev_login_code = seed-shipper（货主）/ seed-owner（船东）/ seed-port（港口方），点登录即可。")


if __name__ == "__main__":
    main()
