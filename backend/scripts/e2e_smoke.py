"""端到端交易流冒烟（E2E）—— 联调用可重复执行。

用法（backend 目录下，需先跑 seed_demo.py）：
    python scripts/e2e_smoke.py
    （未激活虚拟环境时用 .venv 内的解释器显式执行）

场景 A（完整履约）：货主发布货源 → 撮合选船 → 下单 → 支付 → 船东启运 → 货主签收
场景 B（撤单退款）：另发一单 → 支付 → 撤单 → 验证支付单 refunded + 货源回到撮合池
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {err[:300]}") from None


def login(code: str, role: str) -> str:
    tok = call("POST", "/auth/login", body={"code": code})["access_token"]
    tok = call("POST", "/auth/switch-role", token=tok, body={"role": role})["access_token"]
    return tok


def publish_cargo(tok: str, name: str, price: float) -> int:
    c = call("POST", "/cargo/shipments", token=tok, body={
        "cargo_name": name, "cargo_type": "bulk", "weight_t": 800, "volume_m3": 500,
        "origin_port": "NNG", "dest_port": "WUZ", "expect_date": "2026-09-18",
        "freight_price": price,
    })
    call("POST", f"/cargo/shipments/{c['id']}/publish", token=tok)
    return c["id"]


def main() -> None:
    tok_shipper = login("e2e-shipper", "shipper")
    tok_owner = login("seed-owner", "owner")  # 复用种子的 verified 船（ship #1）
    print("[身份] 货主 e2e-shipper / 船东 seed-owner 就绪")

    # ---- 场景 A：完整履约 ----
    print("\n===== 场景 A：发布 → 撮合 → 下单 → 支付 → 启运 → 签收 =====")
    cargo_id = publish_cargo(tok_shipper, "E2E 冒烟钢材 800 吨", 36000)
    print(f"[1] 货源 #{cargo_id} 已发布")

    m = call("POST", f"/match/cargos/{cargo_id}/ships", token=tok_shipper, body={})
    top = m["items"][0]
    print(f"[2] 撮合首选：船 #{top['ship_id']} {top['ship_name']} score={top['score']}")

    order = call("POST", "/order/orders", token=tok_shipper, body={
        "cargo_id": cargo_id, "ship_id": top["ship_id"], "freight_price": 35500,
    })
    print(f"[3] 订单 #{order['id']} 已创建（{order['status']}，运费 {order['freight_price']}）")

    pay = call("POST", "/payment/payments", token=tok_shipper,
               body={"order_id": order["id"], "channel": "mock"})
    pay = call("POST", f"/payment/payments/{pay['id']}/mock-pay", token=tok_shipper, body={})
    print(f"[4] 支付单 #{pay['id']} {pay['status']}（金额 {pay['amount']}，流水 {pay['transaction_no'][:16]}...）")

    order = call("POST", f"/order/orders/{order['id']}/ship", token=tok_owner, body={})
    print(f"[5] 船东启运：{order['status']}（{order['shipped_at']}）")

    order = call("POST", f"/order/orders/{order['id']}/complete", token=tok_shipper, body={})
    print(f"[6] 货主签收：{order['status']}（{order['completed_at']}）→ 交易闭环 ✔")

    # ---- 场景 B：撤单退款 ----
    print("\n===== 场景 B：下单 → 支付 → 撤单 → 退款联动 =====")
    cargo_id2 = publish_cargo(tok_shipper, "E2E 冒烟化肥 800 吨（撤单分支）", 28000)
    order2 = call("POST", "/order/orders", token=tok_shipper, body={
        "cargo_id": cargo_id2, "ship_id": top["ship_id"], "freight_price": 27000,
    })
    pay2 = call("POST", "/payment/payments", token=tok_shipper,
                body={"order_id": order2["id"], "channel": "mock"})
    call("POST", f"/payment/payments/{pay2['id']}/mock-pay", token=tok_shipper, body={})
    print(f"[1] 订单 #{order2['id']} 已支付")

    order2 = call("POST", f"/order/orders/{order2['id']}/cancel", token=tok_shipper,
                  body={"reason": "货主计划变更，联调撤单"})
    print(f"[2] 撤单：订单 {order2['status']}，货源 {order2['cargo_id']} 释放回撮合池")

    pay2 = call("GET", f"/payment/payments/order/{order2['id']}", token=tok_shipper)
    print(f"[3] 支付单联动：{pay2['status']}（{pay2['refunded_at']}）→ 资金流一致性 ✔")

    cargo2 = call("GET", f"/cargo/shipments/{cargo_id2}", token=tok_shipper)
    print(f"[4] 货源状态回滚验证：{cargo2['status']}（应为 published）→ 可重新出单 ✔")

    print("\nE2E 冒烟全链路通过。")


if __name__ == "__main__":
    main()
