"""智能合同仿真案例（可重复执行 · 幂等）—— 给「订单 → 智能合同」铺可点开的样本。

用法（backend 目录下，需先启动后端 uvicorn）：
    python scripts/seed_demo.py            # 先铺基础演示数据（三角色 / 4 张订单 / 泊位预约）
    python scripts/seed_contract_cases.py  # 再铺智能合同仿真案例

为什么单独一个脚本
------------------
seed_demo 铺的是「交易主链路」锚点（已完成 / 待支付 / 已退款 / 面议），
本脚本铺的是「合同风险引擎」样本（R1–R9），关注点不同、可独立重跑，
避免为了演示合同去动已在多个校验脚本里被引用的基础锚点。

铺出内容
--------
订单归属 seed-shipper、承运船归属 seed-owner —— **两个角色都能在「订单」里看到同一批案例**。

| 案例              | 点开「查看合同」应看到 | 构造手法                            |
|-------------------|------------------------|-------------------------------------|
| 基础 · 已完成     | 无风险（干净合同）     | 复用 seed_demo 的已完成单 #7        |
| 基础 · 待支付     | R1 运费未支付（高）    | 复用 seed_demo 的待支付单 #11       |
| 基础 · 面议       | R1 + R2 运费未锁定（高）| 复用 seed_demo 的面议单 #10        |
| R3 装货日期临近   | R3（中）               | 期望装货日期 = 今日 +2（<3 日）     |
| R4 船舶证书临期   | R4（中）               | 专用船证书有效期 = 今日 +20（<30 日）|
| R5 液货危险品     | R5（中）               | 液货船 + tanker 货类（硬约束配平）  |
| R9 在途不可抗力   | R9（低）               | 出单后由船东启运 → status=shipped   |

商务条款类（R6 滞期费 / R7 保险 / R8 违约金量化）会**叠加**在履约中的订单上，
这是风险引擎的常态（一单可同时命中多条），预期叠加关系：

- **R6 滞期费未约定**：货类为散货 bulk 或液货 tanker，且订单未完结（matched/shipped）；
- **R7 货物保险未约定**：货类为集装箱/液货（货值高），或运费 ≥ 30000 元，且订单未完结；
- **R8 违约金标准未量化**：matched 且装货日在 7 日内（R3 样本即命中）。
- 已签收/已撤销订单**不再提示 R6/R7** —— 保证「已完成 = 干净合同」的演示口径。

R3 / R4 为什么能正常成单
------------------------
撮合的硬约束只要求「船舶证书覆盖装货日」，**证书余量与装货日窗口不属于硬约束**；
「3 日内 / 30 日内」是合同风险引擎（contract.check_risks）的规则。
因此「临期但未过期」的样本可以正常出单，然后由风险引擎命中。

边界：已撤单订单后端拒绝生成合同（`订单已撤销，无法生成合同` → 400），故不铺撤单样本。
"""
from __future__ import annotations

from datetime import date, timedelta

from seed_demo import (
    OWNER_CODE,
    PORT_CODE,
    SHIPPER_CODE,
    best_ship,
    call,
    ensure_cargo,
    login,
    pick,
)


def ensure_ship_cert(tok_owner: str, tok_port: str, spec: dict) -> tuple[dict, bool]:
    """按船名复用；证书有效期与目标不符时刷新并重新送审。

    自愈原因：证书有效期是按「今天 +N 天」算的，隔天重跑若不刷新，
    R4 会随时间漂移成「证书过期（高）」；编辑 verified 船会降级重审，故随后补一次审核。
    """
    ships = call("GET", "/ship/registry", token=tok_owner, params={"size": 100})["items"]
    hit = pick(ships, ship_name=spec["ship_name"])
    created = False
    if hit is None:
        hit = call("POST", "/ship/registry", token=tok_owner, body=spec)
        created = True
    elif hit["cert_expiry"] != spec["cert_expiry"]:
        hit = call("PATCH", f"/ship/registry/{hit['id']}", token=tok_owner,
                   body={"cert_expiry": spec["cert_expiry"]})
        print(f"      船 #{hit['id']} {hit['ship_name']} 证书刷新为 {spec['cert_expiry']}（编辑后已降级，待重审）")
    if hit["status"] != "verified":
        call("POST", f"/ship/registry/{hit['id']}/verify", token=tok_port,
             body={"approved": True, "reason": "仿真案例：证件齐全，准予入池"})
        hit = call("GET", f"/ship/registry/{hit['id']}", token=tok_owner)
    return hit, created


def ensure_case_order(tok_shipper: str, spec: dict, ship_id: int | None = None) -> dict | None:
    """仿真案例出单：默认走撮合首选船；传 ship_id 时强制指定（用于构造特定风险）。"""
    cargo, _ = ensure_cargo(tok_shipper, spec)
    orders = call("GET", "/order/orders", token=tok_shipper, params={"size": 100})["items"]
    live = [o for o in orders if o["cargo_id"] == cargo["id"] and o["status"] != "cancelled"]
    if live:
        return sorted(live, key=lambda o: o["id"])[-1]
    sid = ship_id if ship_id is not None else best_ship(tok_shipper, cargo["id"])
    if sid is None:
        return None
    body: dict = {"cargo_id": cargo["id"], "ship_id": sid}
    # 订单运费不继承货源出价，须显式传入（面议货源除外）
    if cargo.get("offer_price") is not None:
        body["freight_price"] = cargo["offer_price"]
    return call("POST", "/order/orders", token=tok_shipper, body=body)


def main() -> None:
    print("=" * 68)
    print("滴滴打船 · 智能合同仿真案例（幂等，可反复执行）")
    print("=" * 68)

    print("\n[1/3] 登录货主 / 船东 / 港口方")
    shipper, tok_shipper = login(SHIPPER_CODE, "shipper")
    owner, tok_owner = login(OWNER_CODE, "owner")
    _, tok_port = login(PORT_CODE, "port")
    print(f"      货主 #{shipper['user_id']} / 船东 #{owner['user_id']}")

    print("\n[2/3] 专项船舶（合同风险样本专用）")
    ship_cert, c1 = ensure_ship_cert(tok_owner, tok_port, {
        "ship_name": "仿真船 · 证书临期 20 天", "ship_type": "bulk", "deadweight_t": 1000,
        "length_m": 70, "width_m": 12, "draft_m": 3.0, "home_port": "GGU",
        "cert_no": "CERT-SIM-EXPIRY",
        "cert_expiry": (date.today() + timedelta(days=20)).isoformat(),
    })
    print(f"      船 #{ship_cert['id']} {ship_cert['ship_name']} 证书 {ship_cert['cert_expiry']} "
          f"→ {ship_cert['status']} {'（新建）' if c1 else '（复用）'}")
    ship_tank, c2 = ensure_ship_cert(tok_owner, tok_port, {
        "ship_name": "仿真船 · 液货 3001", "ship_type": "tanker", "deadweight_t": 1200,
        "length_m": 76, "width_m": 12.5, "draft_m": 3.2, "home_port": "QNZ",
        "cert_no": "CERT-SIM-TANK",
        "cert_expiry": (date.today() + timedelta(days=400)).isoformat(),
    })
    print(f"      船 #{ship_tank['id']} {ship_tank['ship_name']} → {ship_tank['status']} "
          f"{'（新建）' if c2 else '（复用）'}")

    print("\n[3/3] 仿真订单（覆盖 R3 / R4 / R5 / R9，商务条款类 R6–R8 叠加命中）")
    expect = (date.today() + timedelta(days=5)).isoformat()
    soon = (date.today() + timedelta(days=2)).isoformat()
    # (标签, 货源规格, 指定船 id, 出单后是否启运)
    cases = [
        ("R3 装货日期临近", {
            "cargo_name": "仿真案例 · R3 装货日期临近（2 天后）", "cargo_type": "general",
            "weight_t": 600, "volume_m3": 400, "origin_port": "NNG", "dest_port": "GGU",
            "expect_date": soon, "offer_price": 26000,
            "remark": "仿真：期望装货日期在 3 日内 → 合同风险 R3",
        }, None, False),
        ("R4 船舶证书临期", {
            "cargo_name": "仿真案例 · R4 承运船证书临期（20 天）", "cargo_type": "bulk",
            "weight_t": 700, "volume_m3": 450, "origin_port": "GGU", "dest_port": "WUZ",
            "expect_date": expect, "offer_price": 31000,
            "remark": "仿真：指定证书临期船承运 → 合同风险 R4",
        }, ship_cert["id"], False),
        ("R5 液货危险品", {
            "cargo_name": "仿真案例 · R5 液货危险品（甲醇 500 吨）", "cargo_type": "tanker",
            "weight_t": 500, "volume_m3": 600, "origin_port": "QNZ", "dest_port": "GGU",
            "expect_date": expect, "offer_price": 45000,
            "remark": "仿真：液货运输 → 合同风险 R5",
        }, ship_tank["id"], False),
        ("R9 在途不可抗力", {
            "cargo_name": "仿真案例 · R9 在途不可抗力（在途货物）", "cargo_type": "general",
            "weight_t": 550, "volume_m3": 380, "origin_port": "LZH", "dest_port": "WUZ",
            "expect_date": expect, "offer_price": 24000,
            "remark": "仿真：出单后启运 → 合同风险 R9（在途）",
        }, None, True),
    ]
    anchors: list[tuple[str, int]] = []
    for tag, spec, sid, ship_after in cases:
        order = ensure_case_order(tok_shipper, spec, sid)
        if order is None:
            print(f"      {tag} → 未成单（撮合无候选，请检查船是否 verified）")
            continue
        # 幂等启运：仅当仍是 matched 时启运（重复执行不会二次启运/报错）
        if ship_after and order["status"] == "matched":
            order = call("POST", f"/order/orders/{order['id']}/ship", token=tok_owner, body={})
        anchors.append((tag, order["id"]))
        print(f"      {tag} → 订单 #{order['id']} {order['status']} 承运船 #{order['ship_id']}")

    print("\n" + "=" * 68)
    print("仿真案例就绪。小程序「订单」页两个角色都能看到，点卡片「查看合同」即可检查智能合同：")
    for tag, oid in anchors:
        print(f"  订单 #{oid}  {tag}")
    print("另：seed_demo 的基础锚点同样可点开合同")
    print("  订单 #7  已完成 → 无风险（干净合同）")
    print("  订单 #11 待支付 → R1 运费未支付（高）")
    print("  订单 #10 面议   → R1 + R2 运费未锁定（高）")
    print("已撤单订单不支持生成合同（后端 400），页面上也不显示「查看合同」")
    print("=" * 68)


if __name__ == "__main__":
    main()
