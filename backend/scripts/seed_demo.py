"""演示种子数据（可重复执行 · 幂等）—— 第一次汇报演示用。

用法（backend 目录下，需先启动后端 uvicorn）：
    C:/Users/Administrator/.venvs/pinglu-didi/Scripts/python.exe scripts/seed_demo.py

幂等设计
--------
所有实体按「自然键」查找复用（泊位 port_code+berth_no / 船舶 ship_name /
货源 cargo_name / 预约 remark），存在即跳过创建；仅对状态未推进的实体补齐状态。
因此本脚本可反复执行，不会产生重复数据 —— 汇报当天重跑一遍即可拿到干净演示态。

铺出内容（覆盖小程序全演示路径）
--------------------------------
- 三角色账号：seed-shipper 货主 / seed-owner 船东 / seed-port 港口方
- 港口：2 个**演示独占泊位**（NNG-DEMO-01 容量 2 满档样本、GGU-DEMO-02 空档样本）；
  用 DEMO- 前缀与本机其它联调数据隔离，保证档期演示在任何库状态下都可复现
- 船东：3 条船（2 条已审核通过参与撮合 + 1 条待审核）
- 货主：4 个货源 → 4 张订单，覆盖 已完成 / 待支付 / 已退款 / 面议 四种态
- 港域：4 条预约 → 2 条已锁定 + 1 条**因档期占满被 409 拦截**（防超卖演示）+ 1 条错峰已锁定
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

BASE = "http://127.0.0.1:8000/api/v1"

SHIPPER_CODE = "seed-shipper"
OWNER_CODE = "seed-owner"
PORT_CODE = "seed-port"


# ---------------------------------------------------------------- HTTP


def call(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    params: dict | None = None,
    soft: bool = False,
) -> dict:
    """soft=True 时不抛异常，返回 {"__err__": code, "detail": ...} 便于探测已有资源。"""
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")
        if soft:
            return {"__err__": e.code, "detail": err_body}
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
    return call("GET", "/auth/me", token=token), token


def ok(res: dict) -> bool:
    return isinstance(res, dict) and "__err__" not in res


def pick(items: list[dict], **kw) -> dict | None:
    for it in items:
        if all(it.get(k) == v for k, v in kw.items()):
            return it
    return None


# ---------------------------------------------------------------- 域操作


def ensure_berth(tok_port: str, spec: dict) -> tuple[dict, bool]:
    berths = call("GET", "/port/berths", token=tok_port, params={"size": 100})["items"]
    hit = pick(berths, port_code=spec["port_code"], berth_no=spec["berth_no"])
    if hit:
        return hit, False
    return call("POST", "/port/berths", token=tok_port, body=spec), True


def ensure_ship(tok_owner: str, tok_port: str, spec: dict, verify: bool) -> tuple[dict, bool]:
    ships = call("GET", "/ship/registry", token=tok_owner, params={"size": 100})["items"]
    hit = pick(ships, ship_name=spec["ship_name"])
    created = False
    if hit is None:
        hit = call("POST", "/ship/registry", token=tok_owner, body=spec)
        created = True
    if verify and hit["status"] != "verified":
        call(
            "POST",
            f"/ship/registry/{hit['id']}/verify",
            token=tok_port,
            body={"approved": True, "reason": "证件齐全，准予入池"},
        )
        hit = call("GET", f"/ship/registry/{hit['id']}", token=tok_owner)
    return hit, created


def ensure_cargo(tok_shipper: str, spec: dict) -> tuple[dict, bool]:
    cargos = call("GET", "/cargo/shipments", token=tok_shipper, params={"size": 100})["items"]
    hit = pick(cargos, cargo_name=spec["cargo_name"])
    if hit is None:
        hit = call("POST", "/cargo/shipments", token=tok_shipper, body={**spec, "publish_now": True})
        return hit, True
    if hit["status"] == "draft":
        call("POST", f"/cargo/shipments/{hit['id']}/publish", token=tok_shipper)
        hit = call("GET", f"/cargo/shipments/{hit['id']}", token=tok_shipper)
    return hit, False


def best_ship(tok_shipper: str, cargo_id: int) -> int | None:
    m = call("POST", f"/match/cargos/{cargo_id}/ships", token=tok_shipper, body={})
    items = m.get("items") or []
    return items[0]["ship_id"] if items else None


def ensure_order(
    tok_shipper: str, cargo: dict, want_cancelled: bool = False, require_unpaid_payment: bool = False
) -> tuple[dict | None, bool]:
    """按货源复用已有订单；必要时重新出单。

    自愈规则（保证脚本在非干净库上也能跑到目标演示态）：
    - 最新订单为「已撤单」：撤单流程视为达标；其它流程说明货源已回撮合池 → 重新出单
    - 最新订单在途但运费为空、而货源本身有出价：属无法支付的历史脏单 → 撤销后重新出单
    - require_unpaid_payment 且最新订单的支付单已非 pending：待支付锚点已被现场点掉
      （点了「模拟支付」/被退款）→ 撤销该单释放货源后重新出单，使锚点可反复排练
    """
    orders = call("GET", "/order/orders", token=tok_shipper, params={"size": 100})["items"]
    cands = sorted((o for o in orders if o["cargo_id"] == cargo["id"]), key=lambda o: o["id"])
    if cands:
        latest = cands[-1]
        if latest["status"] == "cancelled":
            if want_cancelled:
                return latest, False
        elif require_unpaid_payment and latest["status"] in ("matched", "shipped"):
            pm = call("GET", f"/payment/payments/order/{latest['id']}", token=tok_shipper, soft=True)
            if ok(pm) and pm.get("status") != "pending":
                res = call("POST", f"/order/orders/{latest['id']}/cancel", token=tok_shipper,
                           body={"reason": "演示脚本：待支付锚点已被消耗，撤销后重新出单"})
                if ok(res):
                    print(f"    订单 #{latest['id']} 支付单为 {pm.get('status')}（锚点已被消耗）→ 已撤销并重新出单")
                else:
                    print(f"    !! 订单 #{latest['id']} 无法撤销（{res.get('__err__')}），待支付锚点可能不可复现")
                    return latest, False
            else:
                return latest, False
        elif (
            latest["status"] in ("matched", "shipped")
            and latest["freight_price"] is None
            and cargo.get("offer_price") is not None
        ):
            call("POST", f"/order/orders/{latest['id']}/cancel", token=tok_shipper,
                 body={"reason": "演示脚本：清理运费为空的历史脏单"})
            print(f"    订单 #{latest['id']} 运费为空但货源有出价，已撤销并重新出单")
        else:
            return latest, False

    ship_id = best_ship(tok_shipper, cargo["id"])
    if ship_id is None:
        print(f"    货源 #{cargo['id']} 撮合无候选船，跳过下单")
        return None, False
    # 注意：订单运费不会自动继承货源出价，须显式传入（面议货源除外）
    body: dict = {"cargo_id": cargo["id"], "ship_id": ship_id}
    if cargo.get("offer_price") is not None:
        body["freight_price"] = cargo["offer_price"]
    order = call("POST", "/order/orders", token=tok_shipper, body=body)
    return order, True


def ensure_paid(tok_shipper: str, order_id: int) -> dict:
    """确保支付单存在并已支付（幂等：已 paid 直接返回）。"""
    p = call("GET", f"/payment/payments/order/{order_id}", token=tok_shipper, soft=True)
    if not ok(p) or not p.get("id"):
        p = call(
            "POST", "/payment/payments", token=tok_shipper,
            body={"order_id": order_id, "channel": "mock"},
        )
    if p["status"] == "pending":
        p = call("POST", f"/payment/payments/{p['id']}/mock-pay", token=tok_shipper, body={})
    return p


def advance_order(tok_shipper: str, tok_owner: str, order: dict, flow: str) -> str:
    """把订单推进到目标演示态，返回可读结果描述。"""
    oid = order["id"]
    if flow == "negotiable":
        return "面议单：保持待承运（运费未议定，发起支付应被 400 拒绝）"

    if flow == "matched":
        # 待支付锚点：必须真的落一张待支付支付单，否则现场「模拟支付」按钮根本不存在
        # （页面在无支付单时只显示「发起支付」，多一步操作，演示脚本口径会对不上）
        if order.get("freight_price") is None:
            return "订单待承运但运费未议定（面议单），按规则跳过支付单创建"
        p = call("GET", f"/payment/payments/order/{oid}", token=tok_shipper, soft=True)
        if not ok(p) or not p.get("id"):
            p = call(
                "POST", "/payment/payments", token=tok_shipper,
                body={"order_id": oid, "channel": "mock"},
            )
        return f"支付 #{p['id']} {p['status']}（现场点「模拟支付」）→ 订单待承运"

    if flow in ("complete", "refund"):
        p = ensure_paid(tok_shipper, oid)
        if flow == "refund":
            if order["status"] == "matched":
                order = call("POST", f"/order/orders/{oid}/cancel", token=tok_shipper,
                             body={"reason": "演示：撤单联动退款"})
            p = call("GET", f"/payment/payments/order/{oid}", token=tok_shipper, soft=True)
            st = p.get("status") if ok(p) else "?"
            return f"支付 #{p.get('id')} → 撤单 → 订单 {order['status']} / 支付单 {st}"
        # complete
        order = call("GET", f"/order/orders/{oid}", token=tok_shipper)
        if order["status"] == "matched":
            order = call("POST", f"/order/orders/{oid}/ship", token=tok_owner)
        if order["status"] == "shipped":
            order = call("POST", f"/order/orders/{oid}/complete", token=tok_shipper)
        return f"支付 #{p['id']} → 启运 → 签收，订单 {order['status']}"

    return f"订单 {order['status']}（待支付，可进支付详情三级页）"


def ensure_appt(tok_owner: str, all_appts: list[dict], spec: dict) -> tuple[dict | None, str]:
    """按 remark 找预约；无则创建。返回 (预约, 动作说明)。"""
    hit = pick(all_appts, remark=spec["remark"])
    if hit:
        return hit, "已存在"
    res = call("POST", "/port/appts", token=tok_owner, body=spec, soft=True)
    if not ok(res):
        return None, f"申请被拒 {res['__err__']}: {json.loads(res['detail'])['detail']}"
    return res, "已创建"


# ---------------------------------------------------------------- 主流程


def main() -> None:
    print("=" * 68)
    print("滴滴打船 · 演示种子数据（幂等，可反复执行）")
    print("=" * 68)

    print("\n[1/6] 登录三角色（Mock）")
    shipper, tok_shipper = login(SHIPPER_CODE, "shipper")
    owner, tok_owner = login(OWNER_CODE, "owner")
    port_user, tok_port = login(PORT_CODE, "port")
    print(f"      货主 #{shipper['user_id']} / 船东 #{owner['user_id']} / 港口 #{port_user['user_id']}")

    print("\n[2/6] 港口方：泊位")
    # 演示独占泊位（DEMO- 前缀）：与本机其它联调数据隔离，保证档期演示可复现
    berth_main, c1 = ensure_berth(tok_port, {
        "port_code": "NNG", "berth_no": "DEMO-01", "berth_name": "南宁演示泊位1号（满档样本）",
        "max_dwt": 3000, "max_draft": 6.0,
        "allowed_ship_types": ["bulk", "container", "general"], "concurrent_capacity": 2,
    })
    print(f"      泊位 #{berth_main['id']} NNG-DEMO-01 容量 {berth_main['concurrent_capacity']} {'（新建）' if c1 else '（复用）'}")
    berth_empty, c2 = ensure_berth(tok_port, {
        "port_code": "GGU", "berth_no": "DEMO-02", "berth_name": "贵港演示泊位2号（空档样本）",
        "max_dwt": 8000, "max_draft": 7.5,
        "allowed_ship_types": ["bulk", "container", "general"], "concurrent_capacity": 1,
    })
    print(f"      泊位 #{berth_empty['id']} GGU-DEMO-02 容量 {berth_empty['concurrent_capacity']} {'（新建）' if c2 else '（复用）'}")

    print("\n[3/6] 船东：船舶备案（2 通过 + 1 待审）")
    ship1, s1 = ensure_ship(tok_owner, tok_port, {
        "ship_name": "桂平航 6688", "ship_type": "bulk", "deadweight_t": 1500,
        "length_m": 88, "width_m": 13.5, "draft_m": 3.4, "home_port": "NNG",
        "cert_no": "CERT-2026-06688", "cert_expiry": "2027-08-31",
    }, verify=True)
    print(f"      船 #{ship1['id']} {ship1['ship_name']} → {ship1['status']} {'（新建）' if s1 else '（复用）'}")
    ship2, s2 = ensure_ship(tok_owner, tok_port, {
        "ship_name": "横州集运 101", "ship_type": "container", "deadweight_t": 900,
        "length_m": 75, "width_m": 12, "draft_m": 2.9, "home_port": "GGU",
        "cert_no": "CERT-2026-01101", "cert_expiry": "2027-05-31",
    }, verify=True)
    print(f"      船 #{ship2['id']} {ship2['ship_name']} → {ship2['status']} {'（新建）' if s2 else '（复用）'}")
    ship3, s3 = ensure_ship(tok_owner, tok_port, {
        "ship_name": "邕江 3008", "ship_type": "general", "deadweight_t": 2000,
        "length_m": 92, "width_m": 14, "draft_m": 3.8, "home_port": "NNG",
        "cert_no": "CERT-2026-03008", "cert_expiry": "2027-11-30",
    }, verify=False)
    print(f"      船 #{ship3['id']} {ship3['ship_name']} → {ship3['status']} {'（新建，保留待审态）' if s3 else '（复用）'}")

    print("\n[4/6] 货主：货源 → 订单四态")
    expect = (date.today() + timedelta(days=5)).isoformat()
    specs = [
        ("complete", {
            "cargo_name": "演示货源 01 · 水泥熟料 1200 吨", "cargo_type": "bulk",
            "weight_t": 1200, "volume_m3": 800, "origin_port": "NNG", "dest_port": "GGU",
            "expect_date": expect, "offer_price": 48000, "remark": "演示：已完成订单",
        }),
        ("matched", {
            "cargo_name": "演示货源 02 · 钢材 800 吨", "cargo_type": "bulk",
            "weight_t": 800, "volume_m3": 500, "origin_port": "NNG", "dest_port": "WUZ",
            "expect_date": expect, "offer_price": 36000, "remark": "演示：待支付订单",
        }),
        ("refund", {
            "cargo_name": "演示货源 03 · 砂石 1500 吨", "cargo_type": "bulk",
            "weight_t": 1500, "volume_m3": 900, "origin_port": "NNG", "dest_port": "QNZ",
            "expect_date": expect, "offer_price": 42000, "remark": "演示：撤单退款订单",
        }),
        ("negotiable", {
            "cargo_name": "演示货源 04 · 集装箱设备 8 标箱", "cargo_type": "container",
            "weight_t": 200, "volume_m3": 160, "origin_port": "GGU", "dest_port": "WUZ",
            "expect_date": expect, "remark": "演示：面议运费订单",
        }),
    ]
    anchors = {}
    for flow, spec in specs:
        cargo, cnew = ensure_cargo(tok_shipper, spec)
        order, onew = ensure_order(
            tok_shipper, cargo,
            want_cancelled=(flow == "refund"),
            require_unpaid_payment=(flow == "matched"),
        )
        tag = f"货源 #{cargo['id']}[{'新建' if cnew else '复用'}]"
        if order is None:
            print(f"      {tag} {cargo['cargo_name']} → 未成单")
            continue
        detail = advance_order(tok_shipper, tok_owner, order, flow)
        anchors[flow] = {"cargo": cargo["id"], "order": order["id"]}
        print(f"      {tag} → 订单 #{order['id']}[{'新建' if onew else '复用'}] {detail}")

    print("\n[5/6] 港域：泊位预约（防超卖演示）")
    base = (datetime.now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    all_appts = call("GET", "/port/appts-review", token=tok_port,
                     params={"status": "", "size": 100})["items"]
    appt_specs = [
        {"ship": ship1, "start": base.replace(hour=8), "end": base.replace(hour=18),
         "plan": "主窗口 A（08:00–18:00）", "remark": "演示窗口 A1", "confirm": True},
        {"ship": ship2, "start": base.replace(hour=12), "end": base.replace(hour=12) + timedelta(hours=16),
         "plan": "主窗口 B（12:00–次日04:00，与 A 重叠）", "remark": "演示窗口 A2", "confirm": True},
        {"ship": ship2, "start": base.replace(hour=14), "end": base.replace(hour=14) + timedelta(hours=8),
         "plan": "冲突窗口 C（14:00–22:00，与 A+B 重叠 → 预期 409）", "remark": "演示窗口 A3", "confirm": False},
        {"ship": ship1, "start": (base + timedelta(days=3)).replace(hour=7),
         "end": (base + timedelta(days=3)).replace(hour=15),
         "plan": "错峰窗口 D（+3 日 07:00–15:00，不重叠）", "remark": "演示窗口 A4", "confirm": True},
    ]
    appt_anchors = []
    for s in appt_specs:
        body = {
            "berth_id": berth_main["id"], "ship_id": s["ship"]["id"],
            "plan_start": s["start"].isoformat(), "plan_end": s["end"].isoformat(),
            "remark": s["remark"],
        }
        appt, note = ensure_appt(tok_owner, all_appts, body)
        if appt is None:
            print(f"      {s['plan']} → {note}")
            continue
        status = appt["status"]
        if s["confirm"] and status == "pending":
            res = call("POST", f"/port/appts/{appt['id']}/confirm", token=tok_port, soft=True)
            if ok(res):
                status = res["status"]
                note = "确认成功，档期锁定"
            else:
                err = json.loads(res["detail"])["detail"]
                status, note = "pending(被拦截)", f"确认被拒 {res['__err__']}：{err}"
        elif not s["confirm"] and status == "pending":
            res = call("POST", f"/port/appts/{appt['id']}/confirm", token=tok_port, soft=True)
            if ok(res):
                status, note = res["status"], "意外确认成功（请检查容量）"
            else:
                err = json.loads(res["detail"])["detail"]
                status, note = "pending", f"★ 防超卖拦截 {res['__err__']}：{err}"
        appt_anchors.append((appt["id"], status))
        print(f"      预约 #{appt['id']} {s['plan']} → {status} · {note}")

    print("\n[6/6] 档期快照")
    sch = call("GET", f"/port/berths/{berth_main['id']}/schedule", token=tok_port)
    print(f"      泊位 #{berth_main['id']} 容量 {sch['berth']['concurrent_capacity']}，已确认 {len(sch['confirmed'])} 条")
    for c in sch["confirmed"]:
        print(f"        预约 #{c['appt_id']} 船 #{c['ship_id']} {c['plan_start']} ~ {c['plan_end']}")

    print("\n" + "=" * 68)
    print("演示就绪。小程序开发者工具 Storage 面板设置 dev_login_code：")
    print("  seed-shipper → 货主端 · seed-owner → 船东端 · seed-port → 港口方")
    print("演示锚点：")
    print(f"  订单/支付：已完成 #{anchors.get('complete', {}).get('order', '-')} · "
          f"待支付 #{anchors.get('matched', {}).get('order', '-')} · "
          f"已退款 #{anchors.get('refund', {}).get('order', '-')} · "
          f"面议 #{anchors.get('negotiable', {}).get('order', '-')}")
    print(f"  泊位档期：# {berth_main['id']} NNG-DEMO-01（满档演示）· "
          f"#{berth_empty['id']} GGU-DEMO-02（空档演示）")
    print(f"  预约审核：{', '.join(f'#{i}({s})' for i, s in appt_anchors)}")
    print("=" * 68)


if __name__ == "__main__":
    main()
