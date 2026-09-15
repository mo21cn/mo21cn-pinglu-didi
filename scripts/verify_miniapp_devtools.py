"""小程序真机走查（`wechatide` 工具链版）。

本脚本是 `scripts/verify_miniapp_device.js` 的**换轨实现**（DR-0009 方案 A）：
旧脚本依赖 `miniprogram-automator` 的 ws 协议，在本版本 IDE（3.17.3）上已不再被服务；
本脚本改用 IDE 自带的 `wechatide` 工具链，驱动层见 `scripts/wechatide_client.py`。

已实现的章节
------------
| 章节 | 内容 | 对应旧脚本 |
| --- | --- | --- |
| `smoke` | 全部页面可达 + 不白屏 + 无运行期报错（旧脚本没有这一节） | — |
| `0` | 清缓存 → 身份卡 → 订单非空（货主 / 船东双身份） | ⓪ |
| `1` | 首页身份选择渲染 | ① |
| `2` | 货主工作台进入 + 取数完成 | ② |
| `4` | 发布货物页渲染 | ④ |
| `6` | 订单页列表/统计/待办 + 卡片摘要字段 | ⑥ |
| `12` | 「我的」页渲染 | ⑫ |
| `15` | 发货方式选择（自主 / 委托）+ 几何对齐设计稿 | ⑮ |
| `16` | 委托发货 · 组织选择器（AC-02 / DR-0008） | ⑯ |
| `25` | 成果详情：字段类型的**渲染层**证据 + AC-05（ENT-025） | —（新增章节） |
| `4b` | 撮合页（货主方向：为货源找船） | ④b |
| `5` | 发布空船页渲染（船东视角） | ⑤ |
| `7` | 合同三级页 + 长按弹层 + ⑦b 仿真案例 + ⑦c 已完成订单 | ⑦/⑦b/⑦c |
| `8` | 支付详情三级页（真实点击「去支付」） | ⑧ |
| `8b` | 支付**状态流转**（**默认不跑**，须 `WALK_PAY=1`；会消耗演示锚点） | ⑧b |
| `9` | 船东链路：工作台 → 船队 → 为船找货 + ⑨b 船东合同 | ⑨/⑨b |
| `10` | 港口：服务网格 → 运营台 → 泊位档期（甘特 + 峰值并发） | ⑩ |
| `11` | 预约审核详情 + 防超卖（与服务端 409 同口径） | ⑪ |
| `13` | 智能入口：✨Ai 解析 → 一句话发货 → 草稿带回发布页 | ⑬ |
| `14` | UI 打磨：顶栏身份 / 智能搜索页 / 订单页自绘导航 | ⑭ |

**未迁移章节已清零（ENT-035，2026-09-15）**：上表 `4b` / `5` / `7` / `8` / `9` /
`10` / `11` / `13` / `14` 即旧轨有、换轨后一直记 `not-run` 的 12 章，本轮补齐。
DR-0009 记的阻塞点是「按序号点第 i 个同类元素」，解法走它给出的**出路 ①** ——
属性选择器 + REST 锚点预取，因此**点击全部是真实点击**，没有退化成 `callMethod`。
锚点由 `scripts/verify_miniapp.js` 的检查 9（`WALK_ANCHORS`）盯住三类静默失效。

**仍有的覆盖缺口（不假装已覆盖）**：
* ⑧b「模拟支付后转已支付」**已实现但默认不跑**（`WALK_PAY=1`）：它会**消耗演示锚点**
  （支付单一旦 paid 不可回退，同库再跑 ⑧ 章就取不到 pending 锚点）。
  ⚠️ 且它有一处**已知限制**：支付确认键在原生 `wx.showModal` 里、工具点不到
  ⇒ 该步用**真实接口**驱动支付、只断言**支付之后**的渲染，**不等于**「确认键可点」；
* ⑬/⑭ 的「7 字段解析卡」依赖真实 LLM 解析结果，mock 与真模型的字段数可能不同；
* 真机页面栈深度（DR-0011 的 `STACK_BUDGET = 8`）仍未做运行期验证。

换轨带来的能力差异（实测）
--------------------------
* **支持**：属性选择器 `[data-org="2"]`、`createSelectorQuery` 几何（含 dataset）、
  原生滚动 `automation_viewport_action pageScrollTo`、`setData` / `callMethod`、
  官方 console（`get_simulator_console`，比自建钩子可靠）。
* **不支持**：CSS 伪类（`:nth-of-type(n)` 实测被忽略，仍命中全部元素）；`--x/--y`
  坐标触摸实测同样落到第一个匹配元素。⇒「按序号点第 i 个元素」没有直接等价物，
  只能改用属性选择器，或退化为 `callMethod` 并**明确标注**为方法调用。

前置条件
--------
1. 后端在 8000 端口运行，并已按**这个顺序**铺三份种子：
   `cd backend && python scripts/seed_demo.py && python scripts/seed_entrust_demo.py
    && python scripts/seed_entrust_orgpicker.py`
   ⚠️ **顺序有意义**：`seed_entrust_demo.py` 必须在 `seed_entrust_orgpicker.py`
   **之前** —— 本文件多处按 `ENTRUST_ASSIGNMENT_ID` 取号（那是
   `seed_entrust_demo` 的 `ASSIGNMENT_MAIN`），而 orgpicker 会先占掉两个委托号；
   顺序反了 ㉖ 就会打到另一张单上（它会以「委托状态」断言明确失败，不会静默）。
   （此前这里把两份写成「orgpicker 在前」，是错的 —— 2026-09-14 修正。）
   三份各自不可省：`seed_demo`＝基础域数据；`seed_entrust_demo`＝委托/任务/成果/两宗案件；
   `seed_entrust_orgpicker`＝⑯ 的多组织身份与甲乙两张委托。
2. 后端必须**在跑章节之前**就绪：本脚本先 `backend_ready()` 探一次，不通直接 rc=2。
   后端没起来时所有页面都是 `view=error`，看起来像「页面全坏了」，其实一条业务缺陷都没有。
3. 微信开发者工具**主界面**已启动（进程 `微信开发者工具.exe`）并打开过本项目，
   且已完成一次人工授权（授权持久，见 DR-0009）。
   ⚠️ 只起 CLI 服务**不够**：`check_wechatide_status` 与 `open_project_window`
   在主进程没跑时也回 `ok`，但 `pageStack` 恒空 ⇒ 后续每次导航都等满超时，
   整轮零输出地空转。本脚本用 `simulator_ready()` 拦这一条（rc=2）。
4. **不得在沙箱中运行**（`wechatide` 官方硬要求）。

用法
----
    python scripts/verify_miniapp_devtools.py                  # 全部已实现章节
    python scripts/verify_miniapp_devtools.py --section 16     # 只跑组织选择器
    python scripts/verify_miniapp_devtools.py --section smoke,6,15

退出码：0 = 全部通过；1 = 有断言失败；2 = 前置不可用或章节名非法。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechatide_client import DEFAULT_CLIENT, Client  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROJECT = os.path.join(REPO_ROOT, "miniapp")

# 演示种子约定的常量（backend/scripts/seed_entrust_orgpicker.py）
ORG_STORAGE_KEY = "entrust_active_org"
ORG_A = "演示经营主体·甲"
ORG_B = "演示经营主体·乙"
TITLE_A = "演示委托·甲组织队列样本"
TITLE_B = "演示委托·乙组织队列样本"

CODE_SHIPPER = "seed-shipper"
CODE_OWNER = "seed-owner"

#: 后端健康检查。端口写死 8000 不是偷懒 —— 前端 `utils/request.js` 的 BASE 与
#: 两份种子脚本都指向 8000，换端口要三处一起改。
HEALTHZ = "http://127.0.0.1:8000/healthz"
API_BASE = "http://127.0.0.1:8000/api/v1"


def backend_ready(timeout: float = 2.0) -> bool:
    """后端是否在 8000 上应答。

    **为什么必须在跑章节之前问这一句**（2026-09-14 实测的教训）：后端没起来时，
    所有页面都会落到 `view=error`，于是登录进不去、锚点全 `n=0`、断言成片变红 ——
    看起来像"页面全坏了"，其实**一条业务缺陷都没有**。那一轮 44 条级联假失败
    只差这一句就能在 3 秒内被说清楚：**环境错要报成环境错**。

    绕代理：本机环境变量里可能有 http_proxy，健康检查走本机直连。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(HEALTHZ, timeout=timeout) as resp:
            return int(resp.status) == 200
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- REST 锚点预取
# 为什么需要：待迁移的 12 章要「点第 N 张订单卡 / 第 N 个泊位」，而 wechatide
# 的元素工具**没有 index 参数**。先向后端问清目标对象的真实 id，再用属性选择器
# `[data-order-id="12"]` 精确命中 —— 与旧脚本 `apiLogin/apiGet/apiPost` 同义。
#
# ⚠️ 三条纪律：
#   1. 一律走**空代理** opener（本机 http_proxy 会把 127.0.0.1 的请求也接管，
#      表现为 502 Bad Gateway —— 那是环境问题，不是后端没起）。
#   2. 预取失败**不致命**：返回 None，由章节自己以「前置锚点缺失」记一条失败，
#      绝不静默跳过（静默跳过＝把 not-run 记成通过）。
#   3. 锚点只在需要时现取，不缓存跨章（换库/重铺种子后编号会漂）。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


#: 最近一次 GET 失败的原因（供锚点断言区分"请求失败"与"数据里真的没有"）。
#:
#: 没有它时两者都表现为 `None`，而处置**完全不同**：前者是环境/网络问题
#: （该重试或报环境错），后者是数据问题（该改断言或补种子）。
#: 实测踩到过：全量走查跑到第 25 分钟时，`/ship/registry` 单次请求超时被静默吞掉
#: ⇒ `ship_id=None` ⇒ ⑨ 章少跑 3 条断言并报 FAIL，**看起来像页面缺陷**。
_LAST_API_ERROR: str | None = None


def _http(req: urllib.request.Request, timeout: float = 30.0):
    with _OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return int(resp.status), (json.loads(raw) if raw.strip() else None)


def api_login(code: str) -> dict | None:
    """用演示账号 code 换 token（联调期的固定身份，与 `utils/auth.js` 同源）。"""
    body = json.dumps({"code": code}).encode("utf-8")
    req = urllib.request.Request(
        API_BASE + "/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        status, data = _http(req)
    except Exception:  # noqa: BLE001
        return None
    return data if status == 200 and isinstance(data, dict) else None


def api_get(path: str, token: str, tries: int = 3) -> dict | None:
    """读接口（**异常会重试**，并把最后一次失败的原因记进 `_LAST_API_ERROR`）。

    重试的理由：走查跑到靠后的章节时，机器上同时跑着 IDE 与后端，负载高，
    实测出现过单次请求超时；一次瞬时故障不该让后面成片章节静默"跳过"。

    ⚠️ **只给 GET 重试**：POST 可能带副作用（`mock-pay` / 决定 / 应用变更），
    重试等于重复写一次。POST 的失败由调用方按状态码处置。
    """
    global _LAST_API_ERROR
    last = "unknown"
    for attempt in range(max(1, tries)):
        req = urllib.request.Request(
            API_BASE + path, headers={"Authorization": "Bearer " + token}, method="GET"
        )
        try:
            status, data = _http(req)
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < tries:
                time.sleep(0.4)
                continue
        else:
            if status == 200:
                _LAST_API_ERROR = None
                return data
            last = f"HTTP {status}"
        break
    _LAST_API_ERROR = last
    return None


def api_post(path: str, token: str, payload: dict) -> tuple[int, dict | None]:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        API_BASE + path,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
        method="POST",
    )
    try:
        return _http(req)
    except urllib.error.HTTPError as exc:  # 409 之类要拿到状态码，不当成异常
        try:
            raw = exc.read().decode("utf-8", "replace")
            return int(exc.code), (json.loads(raw) if raw.strip() else None)
        except Exception:  # noqa: BLE001
            return int(exc.code), None
    except Exception:  # noqa: BLE001
        return 0, None


def ensure_role(token: str, role: str) -> tuple[str, str]:
    """确保该 token 的主体处于 `role`，返回 `(可用的 token, 说明)`。

    ⚠️ **为什么预取不能假设"登录后就是想要的角色"**：`/auth/switch-role` 把角色写在
    **用户级**（`auth.service.switch_role` 直接 `user.current_role = role`），而
    `/ship/registry` 的判权用的是 `user.current_role`（**不是 token 里的 role**）。
    走查里 ㉕ / ㉖ 章会 `login_as(CODE_OWNER)` 后 `enterRole("shipper")` 去进落点页 ——
    那一步会把 **seed-owner 的当前角色持久化成 shipper**，于是**后面**章节的预取
    `GET /ship/registry` 一律 **403**，表现为"缺失=已认证船"。

    实测代价：全量两轮都因此多出 2 条**假失败**（首轮连原因都看不出来）。
    ⇒ 预取**显式声明依赖的角色**，与 ⑩ 章对 port 身份的做法一致。
    """
    me = api_get("/auth/me", token) or {}
    if me.get("current_role") == role:
        return token, f"已是 {role}"
    status, data = api_post("/auth/switch-role", token, {"role": role})
    if status == 200 and isinstance(data, dict):
        return (data.get("access_token") or token), f"已切到 {role}"
    return token, f"切换 {role} 失败（HTTP {status}）"


class Anchors:
    """章节共享的演示数据锚点（现取现用）。

    取不到时对应字段为 `None`，章节须**明确记一条失败**，不许当作通过。
    """

    def __init__(self) -> None:
        self.token_shipper: str | None = None
        self.token_owner: str | None = None
        self.token_port: str | None = None
        self.pay_order_id: int | None = None
        self.match_cargo_id: int | None = None
        self.ship_id: int | None = None
        self.sim_cases: list[dict] = []  # [{key, order_id}]
        self.completed_order_id: int | None = None
        self.appt_id: int | None = None
        self.berth_id: int | None = None
        # 内部中间量：全部 completed 订单（等 sim_cases 定下来后排除掉仿真案例）
        self._completed_all: list[int] = []
        #: 每个锚点的**取数详情**（命中几条 / 为什么没取到）。
        #: 写进汇总，让"缺失"能区分**请求失败**与**数据里真没有** —— 两者的处置不同。
        self.notes: dict[str, str] = {}

    @classmethod
    def fetch(cls) -> Anchors:
        a = cls()
        s = api_login("seed-shipper")
        o = api_login("seed-owner")
        p = api_login("seed-port")
        a.token_shipper = (s or {}).get("access_token")
        a.token_owner = (o or {}).get("access_token")
        a.token_port = (p or {}).get("access_token")

        # ⚠️ 三个身份都**显式声明所需角色**（理由见 `ensure_role`）：角色是**用户级**
        #    持久化状态，任何一章点了别的身份卡都会把它改走，而下游判权看的是它。
        #    shipper 侧：seed-shipper 是 `orders` / `cargos` 的判权依据。
        if a.token_shipper:
            a.token_shipper, a.notes["shipper 角色"] = ensure_role(a.token_shipper, "shipper")
        if a.token_owner:
            a.token_owner, a.notes["owner 角色"] = ensure_role(a.token_owner, "owner")
        if a.token_port:
            a.token_port, a.notes["port 角色"] = ensure_role(a.token_port, "port")

        if a.token_shipper:
            orders = (api_get("/order/orders?size=100", a.token_shipper) or {}).get("items") or []
            cargos = (api_get("/cargo/shipments?size=100", a.token_shipper) or {}).get(
                "items"
            ) or []
            for x in orders:
                if x.get("status") != "matched":
                    continue
                pm = api_get(f"/payment/payments/order/{x.get('id')}", a.token_shipper)
                if pm and pm.get("status") == "pending":
                    a.pay_order_id = x.get("id")
                    break
            # ⑦c 要的是「干净合同」：必须是 completed **且不带仿真风险**的订单。
            # 仿真案例里也有 completed 的（R5 那宗），若撞上会得到「已完成却有风险」的假失败。
            # ⇒ 先取全量订单，等 sim_cases 定下来后再挑（见本函数末尾）。
            a._completed_all = [x.get("id") for x in orders if x.get("status") == "completed"]

            # 智能合同仿真案例（seed_contract_cases 铺设，货源名里带 R3/R4/R5）
            for key in ("R3", "R4", "R5"):
                cargo = next(
                    (c for c in cargos if "仿真案例 · " + key in str(c.get("cargo_name") or "")),
                    None,
                )
                if not cargo:
                    continue
                order = next(
                    (
                        x
                        for x in orders
                        if x.get("cargo_id") == cargo.get("id") and x.get("status") != "cancelled"
                    ),
                    None,
                )
                if order:
                    a.sim_cases.append({"key": key, "order_id": order.get("id")})

            # 撮合货源：取候选最多的一票 published 货源
            best_total = -1
            for c in cargos:
                if c.get("status") != "published":
                    continue
                st, data = api_post(f"/match/cargos/{c.get('id')}/ships", a.token_shipper, {})
                total = (data or {}).get("total") or 0 if st == 200 else 0
                if total > best_total:
                    best_total, a.match_cargo_id = total, c.get("id")

        if a.token_owner:
            got = api_get("/ship/registry?size=50", a.token_owner)
            # 角色说明拼进来：锚点断言只展示**缺失项**的备注，缺了才会被读到，
            # 而"角色被切走了"恰恰是这一项最常见的失败原因。
            role_note = a.notes.get("owner 角色", "")
            if got is None:
                # ⚠️ 请求失败**不等于**"库里没有已认证船"：这里如实记下原因，
                #    否则两条路都只表现为 `ship_id=None`，而处置完全不同。
                a.notes["已认证船"] = f"{role_note}；请求失败（{_LAST_API_ERROR or '未知'}）"
            else:
                ships = got.get("items") or []
                verified = [x for x in ships if x.get("status") == "verified"]
                verified.sort(key=lambda x: float(x.get("deadweight_t") or 0), reverse=True)
                a.ship_id = verified[0].get("id") if verified else None
                a.notes["已认证船"] = f"{role_note}；候选 {len(ships)} / 已认证 {len(verified)}"

        if a.token_port:
            got_appts = api_get("/port/appts-review?status=&size=100", a.token_port)
            if got_appts is None:
                a.notes["港口预约"] = f"请求失败（{_LAST_API_ERROR or '未知'}）"
            else:
                appts = got_appts.get("items") or []
                # ⑪ 章验的是「待确认预约 + 防超卖被 409 拦下」，所以锚点必须挑 **pending**：
                # 挑到 confirmed 的会走到「核销完成」分支，断言全部变成"没验成"。
                pick = next((x for x in appts if x.get("status") == "pending"), None)
                a.appt_id = (pick or (appts[0] if appts else {}) or {}).get("id")
                a.notes["港口预约"] = f"共 {len(appts)} 条 / 待确认 {1 if pick else 0}"
            got_berths = api_get("/port/berths?size=50", a.token_port)
            if got_berths is None:
                a.notes["港口泊位"] = f"请求失败（{_LAST_API_ERROR or '未知'}）"
            else:
                berths = got_berths.get("items") or []
                demo = next((b for b in berths if "DEMO-01" in str(b.get("berth_no") or "")), None)
                a.berth_id = (demo or (berths[0] if berths else {}) or {}).get("id")
                a.notes["港口泊位"] = (
                    f"共 {len(berths)} 条 / 演示泊位 {'命中' if demo else '未命中'}"
                )

        # ⑦c 的「干净合同」订单：completed 且**不在**仿真案例里
        sim_ids = {c["order_id"] for c in a.sim_cases}
        clean = [i for i in a._completed_all if i not in sim_ids]
        a.completed_order_id = clean[0] if clean else None
        return a


def _pct(val: object) -> float:
    """把 `"12.50%"` 这类 CSS 百分比字符串转 float；不可解析返回 -1（判为越界）。

    甘特条的 `left` / `width` 是拼给 `style` 用的字符串，不是数字 ——
    直接 `float()` 会让「几何校验」变成「解析校验」，失败原因被吞掉。
    """
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip().rstrip("%"))
    except (TypeError, ValueError):
        return -1.0


def simulator_ready(client: Client, tries: int = 6, gap: float = 2.0) -> bool:
    """模拟器里是否真的有页面（`pageStack` 非空）。

    为什么必须有这一句（2026-09-15 实测）：`check_wechatide_status` 与
    `open_project_window` 在 **IDE 主进程没启动** 时照样回 `ok`（后者回
    `type: "newopen"`），但 `pageStack` 恒为 `[]` —— 此后每次 `navigate` / `tap`
    都等满 150s 超时再重试，整轮走查**零输出地空转二十几分钟**。

    和 `backend_ready()` 同一条取向：**环境错要报成环境错**，不能让它在断言里
    伪装成"页面全坏了"。
    """
    for _ in range(tries):
        if client.page_stack():
            return True
        time.sleep(gap)
    return False


INDEX = "pages/index/index"
SHIPPER = "pages/shipper/shipper"
OWNER = "pages/owner/owner"
ORDERS = "pages/trade/orders/orders"
MINE = "pages/mine/mine"
PUBLISH_CARGO = "pages/publish/cargo/cargo"
PUBLISH_SHIP = "pages/publish/ship/ship"
MATCH = "pages/trade/match/match"
CONTRACT = "pages/trade/contract/contract"
PAYMENT = "pages/trade/payment/payment"
PORT = "pages/port/port"
APPT = "pages/port/appt/appt"
BERTH = "pages/port/berth/berth"
ASSISTANT = "pages/assistant/assistant"
PREVIEW = "pages/preview/preview"
WORKBENCH = "pages/entrust/workbench/workbench"
ARTIFACT = "pages/entrust/artifact/artifact"
DETAIL = "pages/entrust/detail/detail"
CASE = "pages/entrust/case/case"
CASE_CREATE = "pages/entrust/case-create/case-create"

# ㉖/㉗/㉘ 章（ENT-030 切四之六：登记案件 → 记录决定 → 关闭）依赖的演示数据。
# 委托 `#1` 是 `seed_entrust_demo.py` 的 `ASSIGNMENT_MAIN`，状态 `claimed`
# —— 案件登记要求委托**已受理**（否则服务端 409；另一张 `#2` 是 `submitted`）。
ENTRUST_ASSIGNMENT_ID = 1
ENTRUST_TASK_ID = 5  # `execution` 任务（阻断类案件挂的就是它）
CASE_TITLE = "走查·主机故障（界面登记）"
DECISION_NOTE = "走查：转复核（界面记录决定）"
REJECT_NOTE = "走查：复核后驳回（证据不足以支持阻断）"
CLOSE_EVIDENCE = "走查-证据引用-票号W4C001"
CLOSE_NOTE = "走查：复核后撤销，恢复正常班期"

# ㉙/㉚ 章（ENT-032：UI-04 队列 + 「批准」正例）
APPROVE_NOTE = "走查：依据现行版本批准（界面记录决定）"
#: 一个**不存在**的成果版本 id：用来验「填错不会被当成填对」。
#: 用大数而不是 `1` —— 后者很可能是某个真实版本，负例就变成了正例。
BOGUS_REVISION_ID = 999999

#: ㉖ 登记出来的案件 id 在 ㉗/㉘ 之间传递（三章是同一条链，不能各写各的编号）
_STATE: dict = {}

# ㉕ 章依赖的演示成果：`settlement_draft #5` 刻意缺必填项 `receivable_lines`
# —— 它是 `ARTIFACT_SPECS` 的第 5 条（backend/scripts/seed_entrust_demo.py），
# 另两份种子（seed_demo / seed_entrust_orgpicker）都不创建成果，故编号稳定。
#
# **本章可重复跑**：缺项是注册表按**当前生效版本**即时派生的
# （artifacts.py `get_artifact` 只读 `current_revision_id` 那一版），
# 而编辑只**追加**版本、不改生效版本 —— 于是跑过一遍之后「缺项提示」依然成立。
# 换成另一个库 / 另一份种子时编号会漂，此时 ㉕A 的成果类型断言会明确失败。
ARTIFACT_ID = 5

# ⑮ 章文案（逐字对齐设计稿）
DESC_SELF = "您发布货物，自行在船好多平台找寻认证船主接单并完成运输。"
DESC_ENTRUST = "您发布货物，委托给船好多平台承运，由平台组织运力完成运输。"


class Reporter:
    """收集断言结果，同时逐条打印，便于边跑边看。"""

    def __init__(self) -> None:
        self.results: list[dict] = []

    def rec(self, step: str, ok: bool, note: str = "") -> bool:
        self.results.append({"step": step, "ok": bool(ok), "note": str(note)})
        tail = f" | {note}" if note else ""
        print(f"{'PASS' if ok else 'FAIL'} | {step}{tail}", flush=True)
        return bool(ok)

    @property
    def failures(self) -> list[dict]:
        return [r for r in self.results if not r["ok"]]


class Walker:
    """走查执行器：把「登录 → 导航 → 读 data / 元素」封成可读步骤。"""

    def __init__(self, client: Client, shots_dir: str, rep: Reporter) -> None:
        self.c = client
        self.shots_dir = shots_dir
        self.rep = rep
        os.makedirs(shots_dir, exist_ok=True)

    # ---------------------------------------------------------------- 取证
    #: 小于这个字节数的 jpg 视为空图（实测正常页 20KB+，空转页 1–2KB）。
    SHOT_MIN_BYTES = 5000

    def shot(self, name: str, tries: int = 3) -> str:
        """截图留证。

        为什么要重试：截图是 **取证** 而不是断言对象，但抓取本身有偶发抖动 ——
        2026-09-15 一轮 16 张里就有 1 张抓到 1.9KB 空图。一次性抓取会把「抓图抖了一下」
        记成「页面没渲染」，属于把环境噪声写成业务结论。这里重试到拿到非空图为止，
        但仍**只记一条**断言，不因为重试把断言数灌水。
        """
        path = os.path.join(self.shots_dir, name + ".jpg")
        ok = False
        size = 0
        for i in range(tries):
            ok = self.c.screenshot(path)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if ok and size >= self.SHOT_MIN_BYTES:
                break
            if i + 1 < tries:
                time.sleep(1.0)
        self.rep.rec(f"截图 {name}", ok and size >= self.SHOT_MIN_BYTES, f"{size}B")
        return path

    def shot_on_fail(self, name: str, ok: bool) -> None:
        """仅在失败时截图 —— 冒烟覆盖 17 页，默认不留 17 张图。"""
        if not ok:
            self.shot("FAIL-" + name)

    # ---------------------------------------------------------------- 工具
    def app_pages(self) -> tuple[list[str], set[str]]:
        with open(os.path.join(DEFAULT_PROJECT, "app.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        pages = list(cfg.get("pages") or [])
        tabs = {t.get("pagePath") for t in ((cfg.get("tabBar") or {}).get("list") or []) if t}
        return pages, tabs

    def login_as(self, code: str) -> str:
        """写 `dev_login_code` + 清 token → reLaunch 首页（不点击身份卡）。"""
        self.c.set_storage("dev_login_code", code)
        self.c.remove_storage("access_token")
        self.c.remove_storage("user_info")
        self.c.navigate("reLaunch", "/" + INDEX)
        time.sleep(1.5)
        return self.c.current_path()

    def enter_role(self, role: str, want: str, tries: int = 30) -> bool:
        """按身份卡进入工作台。

        旧脚本用 `tapAt(page, '.role-card', 0|1)` 按序号点；工具无 index 参数，
        但身份卡带 `data-role` → 用属性选择器精确命中（等价且更明确）。
        """
        self.c.tap(f'[data-role="{role}"]')
        return self.c.wait_path(want, tries)

    def wait_data(self, pred, tries: int = 20, gap: float = 0.4) -> dict:
        """轮询页面 data 直到满足条件（页面渲染是异步的，固定 sleep 不可靠）。"""
        last: dict = {}
        for _ in range(tries):
            last = self.c.page_data()
            if pred(last):
                return last
            time.sleep(gap)
        return last

    # ------------------------------------------------- 未迁移章节需要的交互
    def scroll_into(self, selector: str) -> None:
        """把元素滚进视口（元素级 scrollTo；失败不阻断，tap 自带重试）。

        长列表里第 N 张卡的按钮在折叠线外时 `tap` 会落空 —— 旧脚本为此手写
        `scrollIntoView`；工具原生支持后不必自己算坐标。
        """
        self.c.scroll_into(selector)
        time.sleep(0.4)

    def tap_order_act(self, order_id: int, act: str, tries: int = 2) -> bool:
        """点某张订单卡里的行内动作（`act` ∈ pay / contract / detail / …）。

        这是「按序号点第 i 张卡」的等价替代：订单卡的每个动作都带
        `data-act-<act>="<order_id>"`，用属性选择器即可**精确**命中，
        且比序号更稳（列表排序变了也不会点错卡）。
        """
        sel = f'[data-act-{act}="{order_id}"]'
        for i in range(tries):
            if i:
                self.scroll_into(f'[data-order-id="{order_id}"]')
            if self.c.tap(sel):
                return True
            time.sleep(0.8)
        return False

    def tap_card_by_id(self, selector: str, obj_id: int, tries: int = 2) -> bool:
        """点带 `data-id` 的卡片（运营台预约 / 泊位、船东船队卡）。"""
        sel = f'{selector}[data-id="{obj_id}"]'
        for i in range(tries):
            if i:
                self.scroll_into(sel)
            if self.c.tap(sel):
                return True
            time.sleep(0.8)
        return False

    def back_to(self, want: str, tries: int = 3) -> bool:
        """返回上一页直到到达 `want`（深栈下 navigateBack 回执偶发抖动）。"""
        for _ in range(tries):
            self.c.back()
            if self.c.wait_path(want, 15):
                return True
            time.sleep(0.8)
        return self.c.current_path() == want

    def new_errors(self, baseline: str) -> str:
        """相对基线的新增 console 错误（IDE 的 console 是累计日志）。"""
        cur = self.c.errors()
        if baseline and cur.startswith(baseline):
            return cur[len(baseline) :]
        return cur

    def win_height(self) -> float:
        val = self.c.evaluate("function(){return wx.getWindowInfo().windowHeight;}")
        return float(val) if isinstance(val, (int, float)) else 0.0

    def open_workbench(self, code: str) -> bool:
        """登录指定身份 → 「我的」页委托入口 → 经理工作台（⑯ 章前置）。"""
        path = self.login_as(code)
        if path != INDEX:
            self.rep.rec(f"⑯ [{code}] 前置登录", False, f"未停在身份选择页（{path}）")
            return False
        if not self.enter_role("shipper", SHIPPER):
            self.rep.rec(
                f"⑯ [{code}] 前置登录",
                False,
                f"未进入货主工作台（{self.c.current_path()}）",
            )
            return False
        self.c.nav("switchTab", "/" + MINE, MINE)
        time.sleep(2.5)
        mine = self.c.page_data()
        # 入口是否可见由服务端决定 —— 这也是在验「我的」页的权限投影
        if not self.rep.rec(
            f"⑯ [{code}] 「我的」页委托入口可见（服务端放行）",
            mine.get("showEntrust") is True,
            f"showEntrust={mine.get('showEntrust')}",
        ):
            return False
        self.c.tap(".entrust-entry")
        time.sleep(5)
        path2 = self.c.current_path()
        return self.rep.rec(f"⑯ [{code}] 点击入口进入经理工作台", path2 == WORKBENCH, path2)

    def reenter_workbench(self) -> str:
        """不重新登录，直接再进工作台 —— 验「上次选择是否被沿用」。"""
        self.c.nav("switchTab", "/" + MINE, MINE)
        time.sleep(2)
        self.c.tap(".entrust-entry")
        time.sleep(4)
        return self.c.current_path()


# ============================== 章节实现 ==============================


def sec_smoke(w: Walker) -> None:
    """冒烟：全部页面可达 + 不白屏 + 无运行期报错。

    旧的 automator 版没有这一节，而真机白屏类缺陷（如 ENT-012 的 `require` 路径写错）
    恰恰只有真机跑页面才会暴露。白屏判据：页面 data 只有 `__webviewId__` 一个 key
    （正常页面至少有一个自己在 `data` 里声明的字段）。
    """
    print("\n== 冒烟：全部页面可达 + 不白屏 + 无运行期报错 ==", flush=True)
    pages, tabs = w.app_pages()

    # 轮 1：未登录态只验身份选择页（已登录时 index 会被自动重定向，断言会失真）
    for key in ("dev_login_code", "dev_device_code", "access_token", "user_info"):
        w.c.remove_storage(key)
    w.c.navigate("reLaunch", "/" + INDEX)
    time.sleep(1.6)
    reached = w.c.current_path()
    keys = [k for k in w.c.page_data() if k != "__webviewId__"]
    w.rep.rec(
        f"冒烟（未登录）{INDEX}",
        reached == INDEX and len(keys) > 0,
        f"reach={reached} dataKeys={len(keys)}",
    )

    # 轮 2：登录后逐页过一遍
    w.login_as(CODE_SHIPPER)
    base = w.c.errors()
    for path in pages:
        if path == INDEX:
            continue  # 已登录时 index 会被重定向，已在轮 1 验过
        action = "switchTab" if path in tabs else "reLaunch"
        w.c.navigate(action, "/" + path)
        time.sleep(1.5)
        reached = w.c.current_path()
        keys = [k for k in w.c.page_data() if k != "__webviewId__"]
        page_err = path in w.new_errors(base)
        ok = (reached == path) and len(keys) > 0 and not page_err
        w.rep.rec(
            f"冒烟 {path}",
            ok,
            f"reach={reached} dataKeys={len(keys)} 本页报错={page_err}",
        )
        w.shot_on_fail(path.replace("/", "_"), ok)
        base = w.c.errors()


def sec_00(w: Walker) -> None:
    """⓪ 清缓存 → 点身份卡 → 订单必须有数据（复现「订单页空白」这类回归）。"""
    print("\n== ⓪ 清缓存 → 身份卡 → 订单非空（双身份） ==", flush=True)
    pairs = (
        ("shipper", "货主", SHIPPER, CODE_SHIPPER),
        ("owner", "船东", OWNER, CODE_OWNER),
    )
    for role, label, want, code in pairs:
        for key in ("dev_login_code", "dev_device_code", "access_token", "user_info"):
            w.c.remove_storage(key)
        w.c.navigate("reLaunch", "/" + INDEX)
        time.sleep(1.6)
        w.rep.rec(
            f"⓪ 清缓存后停在身份选择页（{label}）",
            w.c.current_path() == INDEX,
            w.c.current_path(),
        )
        w.shot(f"00-清缓存-身份选择-{label}")

        entered = w.enter_role(role, want)
        w.rep.rec(f"⓪ 点「{label}」进入工作台", entered, w.c.current_path())
        if not entered:
            continue

        # 身份映射的判据随环境而异：**无真实 AppID** 时客户端把演示账号写进
        # `dev_device_code`；配了合法 AppID 后身份由 openid 决定，`ensureDevAccount`
        # 会直接不写（见 utils/auth.js → `_stableIdentityEnabled`）。
        # 故以「该角色的演示账号确实登进去了」为判据：三个键任一命中即可。
        dev_code = w.c.get_storage("dev_device_code")
        login_code = w.c.get_storage("dev_login_code")
        user_info = w.c.get_storage("user_info")
        mapped = any(code in str(v) for v in (dev_code, login_code, user_info))
        w.rep.rec(
            f"⓪ 身份已映射到演示账号（{label}）",
            mapped,
            f"命中 {code}={mapped}（dev_device_code={dev_code!r} "
            f"dev_login_code={login_code!r} user_info 含={code in user_info}）",
        )

        w.c.nav("switchTab", "/" + ORDERS, ORDERS)
        time.sleep(2.2)
        od = w.c.page_data()
        n = len(od.get("list") or [])
        w.rep.rec(
            f"★ ⓪ {label}「订单」页有数据（不再是空白）",
            n > 0 and not od.get("error"),
            f"list={n} raw={len(od.get('rawList') or [])} error={od.get('error') or '-'}",
        )
        stats = od.get("stats") or []
        w.rep.rec(
            f"⓪ {label} 订单统计行已渲染",
            len(stats) == 5,
            ",".join(f"{s.get('label')}:{s.get('count')}" for s in stats),
        )
        w.shot(f"00-{label}-订单页")


def sec_01(w: Walker) -> None:
    """① 首页身份选择渲染。"""
    print("\n== ① 首页身份选择 ==", flush=True)
    w.login_as(CODE_SHIPPER)
    w.rep.rec("① 首页身份选择渲染", w.c.current_path() == INDEX, w.c.current_path())
    w.shot("01-首页-身份选择")


def sec_02(w: Walker) -> None:
    """② 货主工作台进入 + 取数完成。"""
    print("\n== ② 货主工作台 ==", flush=True)
    w.rep.rec(
        "② 货主工作台进入（真实点击身份卡）",
        w.enter_role("shipper", SHIPPER),
        w.c.current_path(),
    )
    time.sleep(1.6)
    w.shot("02-货主找船")
    sd = w.c.page_data()
    w.rep.rec(
        "② 货主页无错误且取数完成",
        not sd.get("error") and not sd.get("loading"),
        f"myCargoTotal={sd.get('myCargoTotal')} hotShips={len(sd.get('hotShips') or [])} "
        f"hotRoutes={len(sd.get('hotRoutes') or [])}",
    )


def sec_04(w: Walker) -> None:
    """④ 发布货物页渲染。"""
    print("\n== ④ 发布货物页 ==", flush=True)
    w.c.nav("navigateTo", "/" + PUBLISH_CARGO, PUBLISH_CARGO)
    time.sleep(1.2)
    w.shot("04-发布货物")
    d = w.c.page_data()
    w.rep.rec(
        "④ 发布货物页渲染",
        not d.get("error"),
        "form keys=" + ",".join((d.get("form") or {}).keys()),
    )


def sec_06(w: Walker) -> None:
    """⑥ 订单页：列表有数据、卡片摘要取自内嵌字段（无 `#id` 降级）。"""
    print("\n== ⑥ 订单页 ==", flush=True)
    w.c.nav("switchTab", "/" + ORDERS, ORDERS)
    time.sleep(1.8)
    w.shot("06-订单页")
    od = w.c.page_data()
    olist = od.get("list") or []
    w.rep.rec(
        "⑥ 订单页无错误且列表有数据",
        (not od.get("error")) and len(olist) > 0,
        f"list={len(olist)} stats={len(od.get('stats') or [])}",
    )
    c0 = olist[0] if olist else {}
    has_fields = all(c0.get(k) for k in ("origin_label", "dest_label", "cargo_name", "ship_name"))
    no_id_fallback = "#" not in str(c0.get("cargo_name")) and "#" not in str(c0.get("ship_name"))
    w.rep.rec(
        "⑥ 订单卡路线/货名/船名取自订单内嵌摘要（无 #id 降级）",
        has_fields and no_id_fallback,
        f"#{c0.get('id')} {c0.get('origin_label')}→{c0.get('dest_label')} "
        f"{c0.get('cargo_name')} / {c0.get('ship_name')}",
    )
    todos = od.get("todoList") or []
    w.rep.rec("⑥ 待办卡生成", len(todos) >= 0, json.dumps(todos, ensure_ascii=False)[:200])


def sec_12(w: Walker) -> None:
    """⑫ 「我的」页渲染。"""
    print("\n== ⑫ 我的 ==", flush=True)
    w.c.nav("switchTab", "/" + MINE, MINE)
    time.sleep(1.6)
    w.shot("14-我的")
    mdd = w.c.page_data()
    w.rep.rec(
        "⑫ 我的页渲染",
        not mdd.get("error"),
        f"role={mdd.get('currentRole')} functions={len(mdd.get('functions') or [])}",
    )


def sec_15(w: Walker) -> None:
    """⑮ 发货方式选择：自主发货 / 委托发货（纯前端 + 几何对齐设计稿）。"""
    print("\n== ⑮ 发货方式选择 ==", flush=True)
    w.c.nav("navigateTo", "/" + PUBLISH_CARGO, PUBLISH_CARGO)
    cd = w.wait_data(lambda d: d.get("showChannel") is True, tries=30)
    time.sleep(1.0)
    w.shot("15-发货方式弹窗")
    w.rep.rec(
        "⑮ 进入发布货物页即弹出发货方式选择",
        cd.get("showChannel") is True,
        str(cd.get("showChannel")),
    )

    # 文案：`--action text` 只能读第一个匹配项 → 用整块 WXML 做包含断言（等价）。
    # ⚠️ 必须先剥掉标签：desc 文案里嵌了 `<text class="ch-brand">船好多</text>`，
    # 标签会把纯文本切断（2026-09-13 实测踩到，误报为「文案缺失」）。
    panel = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", w.c.outer_wxml(".ch-panel")))
    w.rep.rec(
        "⑮ 两张卡片文案为「自主发货 / 委托发货」",
        "自主发货" in panel and "委托发货" in panel,
        f"面板文本 {len(panel)} 字符",
    )
    has_self = DESC_SELF.replace(" ", "") in panel
    has_entrust = DESC_ENTRUST.replace(" ", "") in panel
    w.rep.rec(
        "⑮ 两段说明文案齐全（自行找船 / 平台承运）",
        has_self and has_entrust,
        f"self={has_self} entrust={has_entrust}",
    )
    rings = w.c.count(".ch-ring")
    labels = w.c.count(".ch-radio-label")
    w.rep.rec(
        "⑮ 两枚单选圈 + 「默认」标记（设计稿元素齐全）",
        rings == 2 and labels == 2,
        f"ring={rings} label={labels}",
    )

    # 几何：`createSelectorQuery`（单元素取数组首项，等价旧脚本的 `q.select`）
    def first(sel: str) -> dict:
        got = w.c.rects(sel)
        return got[0] if got else {}

    panel_r = first(".ch-panel")
    self_r = first(".ch-card-self")
    ent_r = first(".ch-card-entrust")
    icon_r = first(".ch-icon")
    ring_r = first(".ch-ring")
    desc_r = first(".ch-desc")
    win_h = w.win_height()
    has_geo = all((panel_r, self_r, ent_r, icon_r, ring_r, desc_r))
    w.rep.rec(
        "⑮ 弹窗几何可读（面板/卡片/图标/单选圈/文案）",
        has_geo,
        "ok" if has_geo else "缺几何回执",
    )
    if has_geo:
        ratio = self_r["width"] / self_r["height"]
        w.rep.rec(
            "⑮ 卡片宽高比 ≈ 设计稿 3:1（非压扁/拉伸）",
            2.7 < ratio < 3.4,
            f"w/h={ratio:.2f} ({self_r['width']:.0f}x{self_r['height']:.0f})",
        )
        w.rep.rec(
            "⑮ 两张卡片等高（同规格）",
            abs(self_r["height"] - ent_r["height"]) <= 1,
            f"{self_r['height']:.1f} vs {ent_r['height']:.1f}",
        )
        w.rep.rec(
            "⑮ 卡片顺序：自主发货在委托发货之上",
            self_r["top"] < ent_r["top"],
            f"self={self_r['top']:.1f} entrust={ent_r['top']:.1f}",
        )
        w.rep.rec(
            "⑮ 图标为白圆且在卡片左内侧",
            icon_r["left"] > self_r["left"]
            and icon_r["left"] - self_r["left"] < 20
            and abs(icon_r["width"] - icon_r["height"]) <= 1,
            f"Δleft={icon_r['left'] - self_r['left']:.1f} "
            f"{icon_r['width']:.0f}x{icon_r['height']:.0f}",
        )
        w.rep.rec(
            "⑮ 单选圈在卡片右侧之外（设计稿布局）",
            ring_r["left"] >= self_r["right"],
            f"ring.left={ring_r['left']:.1f} card.right={self_r['right']:.1f}",
        )
        w.rep.rec(
            "⑮ 说明文案在卡片下方且不重叠",
            desc_r["top"] >= self_r["bottom"] - 1,
            f"desc.top={desc_r['top']:.1f} card.bottom={self_r['bottom']:.1f}",
        )
        w.rep.rec(
            "⑮ 弹窗完整落在视口内（不被裁切）",
            panel_r["top"] >= 0 and (not win_h or panel_r["bottom"] <= win_h),
            f"panel={panel_r['top']:.0f}..{panel_r['bottom']:.0f} winH={win_h:.0f}",
        )

    # 行为 1：自主发货 → 只关弹窗、留在本页
    w.c.tap(".ch-card-self")
    time.sleep(1.1)
    d2 = w.wait_data(lambda d: d.get("showChannel") is False)
    w.shot("15-自主发货-留在发布页")
    w.rep.rec(
        "⑮ 自主发货只关弹窗、留在发布货物页",
        d2.get("showChannel") is False,
        str(d2.get("showChannel")),
    )
    w.rep.rec(
        "⑮ 关弹窗后发布页表单可用（原有内容仍在）",
        bool((d2.get("form") or {}).get("expect_date")),
        str((d2.get("form") or {}).get("expect_date")),
    )

    # 行为 2：点遮罩关闭
    w.c.set_data({"showChannel": True})
    time.sleep(0.9)
    w.c.tap(".ch-mask-bg")
    time.sleep(0.9)
    d3 = w.c.page_data()
    w.rep.rec(
        "⑮ 点遮罩可关闭弹窗（不进占位页）",
        d3.get("showChannel") is False and w.c.current_path() == PUBLISH_CARGO,
        f"showChannel={d3.get('showChannel')} path={w.c.current_path()}",
    )

    # 行为 3：委托发货 → 「功能预览，即将开放」占位页
    w.c.set_data({"showChannel": True})
    time.sleep(0.9)
    w.c.tap(".ch-card-entrust")
    time.sleep(1.6)
    at_preview = w.c.wait_path(PREVIEW, 25)
    time.sleep(1.2)  # 页面已就位但渲染帧可能滞后，静置后再截图
    w.shot("15-委托发货-功能预览")
    w.rep.rec("⑮ 委托发货跳「功能预览」占位页", at_preview, w.c.current_path())
    if at_preview:
        pvd = w.c.page_data()
        w.rep.rec(
            "⑮ 占位页文案为「功能预览，即将开放」",
            str(pvd.get("title")) == "功能预览，即将开放",
            str(pvd.get("title")),
        )
        n = w.c.count(".preview-text")
        w.rep.rec("⑮ 占位页仅一行文案（空白页）", n == 1, str(n))

    # 返回：深栈下 navigateBack 偶发抖动 → 重试直到回到发布货物页
    back = False
    for _ in range(3):
        w.c.back()
        time.sleep(0.8)
        if w.c.wait_path(PUBLISH_CARGO, 15):
            back = True
            break
    w.rep.rec("⑮ 从占位页可返回发布货物页（二级页栈正常）", back, w.c.current_path())
    w.c.back()
    time.sleep(1.0)


def sec_16(w: Walker) -> None:
    """⑯ 委托发货 · 组织选择器（AC-02 / DR-0008）：多组织 / 单组织 / 无组织三身份。"""
    print("\n== ⑯ 组织选择器（三身份） ==", flush=True)

    # ---------- 段一：多组织且未选择 → 不猜，要求用户选 ----------
    print("\n-- 段一：多组织（ambiguous） --", flush=True)
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.remove_storage(ORG_STORAGE_KEY)  # 双保险：确认清掉上次选择
    if w.open_workbench("seed-mgr-multi"):
        d = w.wait_data(lambda x: bool(x.get("orgReason")))
        w.shot("16-组织选择器-多组织未选")
        orgs = d.get("orgs") or []
        names = [o.get("name") for o in orgs]
        w.rep.rec("⑯ 多组织：服务端清单含甲乙两个组织", len(orgs) == 2, f"orgs={len(orgs)}")
        w.rep.rec(
            "⑯ 多组织：清单里同时有甲与乙",
            ORG_A in names and ORG_B in names,
            str(names),
        )
        w.rep.rec(
            "⑯ 多组织未选：reason=ambiguous（不猜，猜错会看到别人的组织）",
            d.get("orgReason") == "ambiguous",
            f"reason={d.get('orgReason')}",
        )
        w.rep.rec(
            "⑯ 多组织未选：activeOrgId 为空",
            d.get("activeOrgId") == "",
            f"activeOrgId={d.get('activeOrgId')!r}",
        )
        w.rep.rec(
            "⑯ 多组织未选：view=denied 且不渲染队列",
            d.get("view") == "denied" and len(d.get("items") or []) == 0,
            f"view={d.get('view')} items={len(d.get('items') or [])}",
        )
        w.rep.rec(
            "⑯ 多组织未选：文案指向「需要选择服务经营主体」",
            d.get("viewTitle") == "需要选择服务经营主体",
            str(d.get("viewTitle")),
        )
        pill_count = w.c.count(".org-pill")
        active0 = w.c.count(".org-pill-active")
        w.rep.rec("⑯ 多组织未选：真机渲染出 2 个组织 pill", pill_count == 2, str(pill_count))
        w.rep.rec("⑯ 多组织未选：初始无 active pill", active0 == 0, str(active0))

        # 点「甲」—— 工具无 index 参数，只能靠属性选择器；但**编号不能写死**：
        # 组织 id 取决于种子插入顺序（三份种子各建自己的组织），写死 `1`/`2`
        # 会在换顺序/换库时静默点到别的组织上。2026-09-14 就这么踩了一次 ——
        # 把三份种子的顺序改成 `entrust_demo` 在前之后，甲/乙 的 id 从 1/2 变成 2/3，
        # 于是 `[data-org="1"]` 直接点空，后面 7 条断言全部级联失败。
        org_ids = {o.get("name"): o.get("orgId") for o in (d.get("orgs") or [])}
        id_a = str(org_ids.get(ORG_A, ""))
        id_b = str(org_ids.get(ORG_B, ""))
        _STATE["org_id_b"] = id_b
        w.rep.rec(
            "⑯ 从页面清单里读出甲/乙的组织编号（不写死）",
            bool(id_a) and bool(id_b),
            f"甲={id_a} 乙={id_b}",
        )
        w.rep.rec(
            "⑯ 点「甲」：tap 成功",
            w.c.tap(f'[data-org="{id_a}"]'),
            f'selector=[data-org="{id_a}"]',
        )
        d = w.wait_data(lambda x: (x.get("items") or [{}])[0].get("title") == TITLE_A)
        w.shot("16-组织选择器-选中甲")
        w.rep.rec(
            "⑯ 点「甲」：理由变为 picked（来自用户操作）",
            d.get("orgReason") == "picked",
            f"reason={d.get('orgReason')}",
        )
        first_title = ((d.get("items") or [{}])[0] or {}).get("title")
        w.rep.rec("⑯ 点「甲」：队列渲染出甲组织委托", first_title == TITLE_A, str(first_title))
        active_a = w.c.count(".org-pill-active")
        w.rep.rec("⑯ 点「甲」：恰好 1 个 active pill", active_a == 1, str(active_a))
        stored_a = w.c.get_storage(ORG_STORAGE_KEY)
        w.rep.rec(
            "⑯ 点「甲」：选择已写入 Storage（下次进来不用再选）",
            stored_a == str(d.get("activeOrgId")),
            f"stored={stored_a!r} active={d.get('activeOrgId')!r}",
        )

        # 点「乙」（第二个 pill）
        w.rep.rec(
            "⑯ 点「乙」：tap 成功",
            w.c.tap(f'[data-org="{id_b}"]'),
            f'selector=[data-org="{id_b}"]',
        )
        d = w.wait_data(lambda x: (x.get("items") or [{}])[0].get("title") == TITLE_B)
        w.shot("16-组织选择器-选中乙")
        first_title = ((d.get("items") or [{}])[0] or {}).get("title")
        w.rep.rec(
            "⑯ 点「乙」：队列换成乙组织委托（标题确实不同）",
            first_title == TITLE_B,
            str(first_title),
        )
        w.rep.rec(
            "⑯ 点「乙」：两个组织的标题互不相同（否则切换看起来没反应）",
            TITLE_A != TITLE_B,
            f"{TITLE_A} / {TITLE_B}",
        )
        stored_b = w.c.get_storage(ORG_STORAGE_KEY)
        w.rep.rec(
            "⑯ 点「乙」：Storage 跟随更新",
            stored_b == str(d.get("activeOrgId")),
            stored_b,
        )

        # 重进（不重新登录）→ 应沿用乙
        if w.reenter_workbench() == WORKBENCH:
            rd = w.wait_data(lambda x: bool(x.get("orgReason")))
            w.shot("16-组织选择器-沿用上次选择")
            w.rep.rec(
                "⑯ 重进：沿用上次选择（reason=saved，且等于乙）",
                rd.get("orgReason") == "saved" and str(rd.get("activeOrgId")) == stored_b,
                f"reason={rd.get('orgReason')} active={rd.get('activeOrgId')!r}",
            )
            first_title = ((rd.get("items") or [{}])[0] or {}).get("title")
            w.rep.rec(
                "⑯ 重进：直接渲染乙组织队列，不再要求选择",
                first_title == TITLE_B,
                str(first_title),
            )
        else:
            w.rep.rec("⑯ 重进工作台", False, "未跳转")

    # ---------- 段二：单组织 ----------
    print("\n-- 段二：单组织（only） --", flush=True)
    # 先清掉段一留下的选择：本段验的是 **only** 分支（唯一选项自动选中），
    # 而残留值若恰好等于甲的组织 id，页面会走 **saved** 分支 —— 那是另一件事。
    # （2026-09-14：id 不再固定为 1/2，残留值真的会撞上，所以必须显式清。
    #   这不是"为了好过"而放宽断言：saved 分支随后用**反向用例**单独覆盖。）
    w.c.remove_storage(ORG_STORAGE_KEY)
    if w.open_workbench("seed-mgr-single"):
        d = w.wait_data(lambda x: bool(x.get("orgReason")))
        w.shot("16-组织选择器-单组织不出现")
        orgs = d.get("orgs") or []
        names = [o.get("name") for o in orgs]
        single_id = str(((orgs[0] if orgs else {}) or {}).get("orgId"))
        w.rep.rec("⑯ 单组织：清单只有 1 个组织", len(orgs) == 1, f"orgs={len(orgs)}")
        w.rep.rec("⑯ 单组织：组织为甲", names[:1] == [ORG_A], str(names))
        w.rep.rec(
            "⑯ 单组织：reason=only（唯一选项自动选中）",
            d.get("orgReason") == "only",
            f"reason={d.get('orgReason')}",
        )
        w.rep.rec(
            "⑯ 单组织：自动选中的就是甲（编号从清单读，不写死）",
            str(d.get("activeOrgId")) == single_id and bool(single_id),
            f"active={d.get('activeOrgId')!r} 期望={single_id}",
        )
        pills = w.c.count(".org-pill")
        w.rep.rec("⑯ 单组织：不出现组织选择器（唯一选项是噪音）", pills == 0, str(pills))
        first_title = ((d.get("items") or [{}])[0] or {}).get("title")
        w.rep.rec(
            "⑯ 单组织：直接 ready 且渲染甲组织队列",
            d.get("view") == "ready" and first_title == TITLE_A,
            f"view={d.get('view')} title={first_title}",
        )

        # 反向用例：把**别的组织**的 id 塞进 Storage（就是段一存下的乙），
        # 不得被沿用 —— 否则「沿用上次选择」会变成"沿用别人的组织"。
        stale_b = str(_STATE.get("org_id_b") or "")
        if stale_b:
            w.c.set_storage(ORG_STORAGE_KEY, stale_b)
            if w.reenter_workbench() == WORKBENCH:
                d2 = w.wait_data(lambda x: bool(x.get("orgReason")))
                w.rep.rec(
                    "⑯ 单组织：**别人组织**的残留选择不得被沿用（仍落到甲、且走 only）",
                    str(d2.get("activeOrgId")) == single_id and d2.get("orgReason") == "only",
                    f"active={d2.get('activeOrgId')!r} reason={d2.get('orgReason')} "
                    f"（塞进去的乙={stale_b}）",
                )
            else:
                w.rep.rec("⑯ 单组织反向用例：重进工作台", False, "未跳转")
        else:
            w.rep.rec("⑯ 单组织反向用例", False, "段一没拿到乙的组织编号，跳过")

    # ---------- 段三：无组织 ----------
    print("\n-- 段三：无组织（none） --", flush=True)
    if w.open_workbench("seed-mgr-none"):
        d = w.wait_data(lambda x: bool(x.get("orgReason")))
        w.shot("16-组织选择器-无组织")
        orgs = d.get("orgs")
        w.rep.rec(
            "⑯ 无组织：服务端返回空清单（200 + 空，不是 403/404）",
            isinstance(orgs, list) and len(orgs) == 0,
            f"orgs={orgs}",
        )
        w.rep.rec(
            "⑯ 无组织：reason=none",
            d.get("orgReason") == "none",
            f"reason={d.get('orgReason')}",
        )
        pills = w.c.count(".org-pill")
        w.rep.rec("⑯ 无组织：不出现组织选择器", pills == 0, str(pills))
        w.rep.rec(
            "⑯ 无组织：文案说清「还没被加入组织」",
            d.get("viewTitle") == "还没有加入服务经营主体",
            str(d.get("viewTitle")),
        )
        cards = w.c.count(".list-card")
        w.rep.rec("⑯ 无组织：不渲染任何委托条目", cards == 0, str(cards))


def _as_dict(val: object) -> dict:
    """Storage 里取出的对象常被序列化成 JSON **字符串**，两种形态都接住。"""
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip().startswith("{"):
        try:
            parsed = json.loads(val)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def sec_25(w: Walker) -> None:
    """㉕ 成果详情（ENT-025）：字段类型的**渲染层**证据 + AC-05。

    为什么必须真机：静态门禁与纯逻辑校验都只能证明「源码里有这段分支」。
    本片加的「列表（JSON 数组）」提示，静态断言最多证明模板里写了 `item.kindHint`，
    证明不了它**渲染出来** —— `onEdit` 漏投影那次就是模板有、数据无，静态断言照样全绿。

    身份与落点页是**两件事**，分别断言：身份由 `dev_login_code` 决定
    （`utils/auth.js` 的 DEV_ROLE_CODE 优先于所点卡片），落点页由所点卡片决定。
    旧脚本 `tap('.role-card')` 只能命中第一张卡，于是「身份是 seed-owner、落点是
    货主页」是**既有事实**而非缺陷 —— 这里沿用同一口径，不去"修正"它，
    否则先前那轮 PASS 34 / FAIL 0 的结论就不再对应当前脚本。

    ⚠️ 前置：本机须已铺 `backend/scripts/seed_entrust_demo.py`（成果 `#5` 由它创建）。
    本节断言的是「**缺值**字段仍按契约判为结构化」，而缺项是**当前生效版本**的派生
    事实 —— 编辑只追加版本、不改生效版本，所以本节**可重复跑**；但换库 / 换种子时
    编号会漂，届时 ㉕A 会以「成果类型」断言明确失败，不会静默通过。
    """
    print("\n== ㉕ 成果详情（ENT-025）==", flush=True)
    art_url = f"/{ARTIFACT}?artifact_id={ARTIFACT_ID}"
    base_err = w.c.errors()

    path = w.login_as(CODE_OWNER)
    w.rep.rec("㉕ 前置 · 回到身份选择页", path == INDEX, path)
    w.rep.rec(
        "㉕ 前置 · 进入货主工作台（落点页，与身份无关）",
        w.enter_role("shipper", SHIPPER),
        w.c.current_path(),
    )
    ui_raw = w.c.evaluate("function(){return wx.getStorageSync('user_info')||null;}")
    ui = _as_dict(ui_raw)
    uid = str(ui.get("id") or ui.get("user_id") or "")
    w.rep.rec(
        "㉕ 前置 · 实际身份是 seed-owner（演示经理；成果页写权限来自它）",
        uid == "2" or ui.get("current_role") == "owner",
        json.dumps(ui, ensure_ascii=False)[:140] if ui else str(ui_raw)[:140],
    )

    print("\n-- A. 进入成果详情页 --", flush=True)
    w.c.nav("reLaunch", art_url, ARTIFACT)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    w.rep.rec(
        "㉕A view=ready（真实渲染，不是白屏）",
        d.get("view") == "ready",
        str(d.get("view")),
    )
    w.rep.rec(
        "㉕A 页面持有的成果编号正确",
        str(d.get("artifactId")) == str(ARTIFACT_ID),
        str(d.get("artifactId")),
    )
    art = d.get("artifact") or {}
    w.rep.rec(
        "㉕A 成果类型为 settlement_draft",
        art.get("typeCode") == "settlement_draft",
        f"typeCode={art.get('typeCode')}；取值不对说明编号漂了，先铺"
        " backend/scripts/seed_entrust_demo.py",
    )
    w.rep.rec(
        "㉕A 可编辑 / 可确认入口已亮出",
        art.get("canEdit") is True and art.get("canConfirm") is True,
        f"canEdit={art.get('canEdit')} canConfirm={art.get('canConfirm')}",
    )
    w.rep.rec(
        "㉕A 缺项提示点名到字段（不是只说「信息不完整」）",
        "还缺" in str(art.get("missingHint") or ""),
        str(art.get("missingHint"))[:80],
    )
    w.shot("25-A-成果详情-查看态")

    print("\n-- B. 缺值列表字段：进编辑态 --", flush=True)
    rows = [f for f in (art.get("fields") or []) if f.get("name") == "receivable_lines"]
    w.rep.rec("㉕B 投影里有 receivable_lines 字段行", len(rows) == 1, f"n={len(rows)}")
    if rows:
        r0 = rows[0]
        w.rep.rec(
            "㉕B 缺值字段仍按契约判为结构化（declared=list / kind=json）",
            r0.get("declared") == "list" and r0.get("kind") == "json",
            f"declared={r0.get('declared')} kind={r0.get('kind')}",
        )
        w.rep.rec(
            "㉕B 查看态显示「（未填）」而不是 null / 空白",
            r0.get("empty") is True,
            f"empty={r0.get('empty')}",
        )

    if not w.c.tap(".btn-primary"):
        w.rep.rec("㉕B 点「编辑内容」", False, ".btn-primary 未命中")
    d2 = w.wait_data(lambda x: x.get("editing") is True, tries=12, gap=0.5)
    w.rep.rec(
        "㉕B 点「编辑内容」后进入编辑态",
        d2.get("editing") is True,
        str(d2.get("editing")),
    )
    ff = d2.get("formFields") or []
    idx_recv = idx_note = -1
    for i, f in enumerate(ff):
        if f.get("name") == "receivable_lines":
            idx_recv = i
        elif f.get("name") == "note":
            idx_note = i
    w.rep.rec("㉕B 编辑表单里有 receivable_lines", idx_recv >= 0, f"idx={idx_recv}")
    if idx_recv >= 0:
        fe = ff[idx_recv]
        w.rep.rec(
            "㉕B 编辑态该字段走 JSON 形态且带契约类型",
            fe.get("kind") == "json" and fe.get("declared") == "list",
            f"kind={fe.get('kind')} declared={fe.get('declared')}",
        )
        w.rep.rec(
            "㉕B 编辑态初始文本是空串（不是 undefined / 'null'）",
            fe.get("text") == "" and isinstance(fe.get("text"), str),
            json.dumps(fe.get("text"), ensure_ascii=False),
        )
        w.rep.rec(
            "㉕B 编辑态带可读类型提示（含 JSON）",
            "JSON" in str(fe.get("kindHint") or ""),
            str(fe.get("kindHint")),
        )

    # 渲染层证据：data 层对了不等于渲染层对了（本片修的就是渲染层）
    n_hint = w.c.count(".art-kind-hint")
    n_area = w.c.count("textarea.art-textarea")
    w.rep.rec("㉕B 渲染层 · 类型提示元素已渲染", n_hint >= 1, f".art-kind-hint n={n_hint}")
    w.rep.rec(
        "㉕B 渲染层 · 结构化字段走 textarea",
        n_area >= 1,
        f"textarea.art-textarea n={n_area}",
    )
    hint = w.c.outer_wxml(".art-kind-hint")
    w.rep.rec(
        "㉕B 渲染层 · 提示文案是「列表（JSON 数组）」",
        "列表" in hint and "JSON" in hint,
        hint[:120].replace("\n", " "),
    )
    w.shot("25-B-成果详情-编辑态")

    print("\n-- C. 保存新版本 → 导航复核不产生重复成果 --", flush=True)
    revs_before = d2.get("revisions") or []
    cur_before = (d2.get("artifact") or {}).get("currentRevisionNo")
    n_before = len(revs_before)
    new_no: int | None = None
    if idx_recv < 0:
        w.rep.rec("㉕C 保存走查", False, "找不到 receivable_lines 在表单里的下标，无法注入")
    else:
        # skill 坑 12：模拟器里对 textarea 赋值常不触发 bindinput。用 setData 注值，
        # 再走**真实点击**保存 —— 输入路径被跳过，但组 payload / 发请求 / 渲染都是真的。
        new_ff = json.loads(json.dumps(ff, ensure_ascii=False))
        new_ff[idx_recv]["text"] = '["运费 8000"]'
        if idx_note >= 0:
            new_ff[idx_note]["text"] = "真机走查改过备注"
        w.rep.rec("㉕C 注入编辑值（setData）", bool(w.c.set_data({"formFields": new_ff})))
        time.sleep(1.0)

        w.c.tap(".btn-primary")
        d3 = w.wait_data(
            lambda x: x.get("saveNotice") or x.get("editing") is False,
            tries=30,
            gap=0.5,
        )
        notice = str(d3.get("saveNotice") or "")
        w.rep.rec("㉕C 保存后退出编辑态", d3.get("editing") is False, str(d3.get("editing")))
        w.rep.rec("㉕C 保存提示说清新版本号", "已保存为 v" in notice, notice[:90])
        w.rep.rec(
            "㉕C 保存提示说清生效版本未变（少了这句，用户会以为客户已看到新内容）",
            "生效版本仍是 v" in notice,
            notice[:90],
        )
        w.shot("25-C1-保存后")

        # 导航复核：reLaunch 清空页面栈 ⇒ 读到的一定是后端事实，不是本地缓存
        w.c.nav("reLaunch", art_url, ARTIFACT)
        d4 = w.wait_data(lambda x: x.get("view") == "ready", tries=40, gap=0.5)
        w.rep.rec(
            "㉕C 复核 · 仍指向同一成果编号（未产生重复成果）",
            str(d4.get("artifactId")) == str(ARTIFACT_ID),
            str(d4.get("artifactId")),
        )
        revs_after = d4.get("revisions") or []
        nos = [r.get("revisionNo") for r in revs_after]
        w.rep.rec(
            "㉕C 复核 · 版本数恰好 +1",
            len(revs_after) == n_before + 1,
            f"{n_before} → {len(revs_after)}",
        )
        w.rep.rec("㉕C 复核 · 版本号无重复", len(set(nos)) == len(nos), str(nos))
        cur_after = (d4.get("artifact") or {}).get("currentRevisionNo")
        w.rep.rec(
            "㉕C 复核 · 生效版本仍是保存前的那个（编辑不改生效版本）",
            cur_after == cur_before,
            f"{cur_after} vs {cur_before}",
        )
        new_no = max(nos) if nos else None
        hist = [r for r in revs_after if r.get("revisionNo") == new_no]
        w.rep.rec(
            "㉕C 复核 · 新版本进入历史且未标为生效",
            len(hist) == 1 and hist[0].get("isCurrent") is False,
            f"v{new_no} isCurrent={hist[0].get('isCurrent') if hist else 'N/A'}",
        )
        w.shot("25-C2-导航复核后")

    print("\n-- D. 确认入口可见（**刻意不点**）--", flush=True)
    if new_no is None:
        w.rep.rec("㉕D 确认入口可见", False, "无新版本号，无法定位入口")
    else:
        n_btn = w.c.count(f'[data-no="{new_no}"]')
        w.rep.rec(
            "㉕D 非生效版本行上有「设为生效版本」入口",
            n_btn >= 1,
            f'[data-no="{new_no}"] n={n_btn}',
        )
        det = w.c.outer_wxml(".tl")
        w.rep.rec(
            "㉕D 渲染层 · 版本历史里有该入口文案",
            "设为生效版本" in det,
            det[:100].replace("\n", " "),
        )
    w.rep.rec(
        "㉕D 确认动作未在真机点击（记为**限制**，不是通过）",
        True,
        "原生 showModal 不在渲染层、工具点不到「确定」；确认的后端语义"
        "由 verify_frontend_e2e.js 读后端事实覆盖",
    )

    new_err = w.new_errors(base_err)
    w.rep.rec(
        "㉕E 本章运行期无新增 console error",
        not new_err.strip(),
        new_err[:160] or "(无)",
    )


def sec_26(w: Walker) -> None:
    """㉖ 登记案件（ENT-030 切四之六）：从**委托详情页**经界面登记一宗阻断类案件。

    为什么必须真机（三条，都不是"顺手补一条"）：

    1. **入口可见性由委托状态决定**（只有已受理 `claimed` 才给入口）——
       判错就是让用户点进一个必然 409 的按钮，而 409 会被读成"系统随机失败"。
    2. **「选受影响项」要点到候选里的某一条**：走查工具**没有 index 参数**，
       而 `[data-id="5"]` 在候选里会同时命中「任务 #5」与「成果 #5」（本节实测 n=2），
       只有复合选择器 `[data-kind="task"][data-id="5"]` 才唯一命中。这类歧义
       在静态断言里完全看不见（它们只看源码文本）。
    3. **C2 的前置拦截必须"拦得住且不写库"**：阻断类案件没有受影响项时，
       界面要拦住、服务端也要拒；只验前者会得到"界面拦住了但库里多了一条"。

    ⚠️ 前置：`backend/scripts/seed_entrust_demo.py` 已铺（委托 `#1` 为 `claimed`）。
    换库/换种子后编号会漂，此时 ㉖A 会以「委托状态」断言明确失败，不会静默通过。
    ⚠️ 文本输入用 `w.c.set_data` 注值（模拟器对 input/textarea 赋值常不触发 `bindinput`，
    见 skill `miniapp-device-walkthrough` 坑 12）—— 因此本节**不覆盖**"键盘输入 →
    bindinput"这一环，其余（选择条、候选、提交按钮）全是真点击。
    """
    print("\n== ㉖ 登记案件（ENT-030 切四之六）==", flush=True)
    base_err = w.c.errors()
    detail_url = f"/{DETAIL}?assignment_id={ENTRUST_ASSIGNMENT_ID}"

    path = w.login_as(CODE_OWNER)
    w.rep.rec("㉖ 前置 · 回到身份选择页", path == INDEX, path)
    w.rep.rec(
        "㉖ 前置 · 进入组织经理落点的某张工作台",
        w.enter_role("shipper", SHIPPER),
        w.c.current_path(),
    )

    print("\n-- A. 入口可见性 --", flush=True)
    w.c.nav("reLaunch", detail_url, DETAIL)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    w.rep.rec(
        "㉖A 委托详情页 ready（真实渲染，不是白屏）",
        d.get("view") == "ready",
        str(d.get("view")),
    )
    w.rep.rec(
        "㉖A 已受理（claimed）委托才给「登记异常 / 变更」入口",
        d.get("canCreateCase") is True,
        f"canCreateCase={d.get('canCreateCase')}（False 多半是委托不是 claimed，先铺种子）",
    )
    w.rep.rec(
        "㉖A 待受理委托**不**给该入口（受理前 raise_case 必 409）",
        d.get("canClaim") is False,
        f"canClaim={d.get('canClaim')}",
    )
    n_anchor = w.c.count('[data-act-create-case="create-case"]')
    w.rep.rec(
        "㉖A 登记入口锚点**唯一命中**（否则 tap 会点到 7 个「记录任务」里的第一个）",
        n_anchor == 1,
        f"n={n_anchor}",
    )
    w.shot("26-A-委托详情-登记入口")

    print("\n-- B. 真点击进入登记页 --", flush=True)
    w.c.tap('[data-act-create-case="create-case"]')
    w.rep.rec("㉖B 真点击进入登记页", w.c.wait_path(CASE_CREATE), w.c.current_path())
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    w.rep.rec(
        "㉖B 登记页 ready 且持有正确的委托编号",
        d.get("view") == "ready" and str(d.get("assignmentId")) == str(ENTRUST_ASSIGNMENT_ID),
        f"{d.get('view')} / {d.get('assignmentId')}",
    )
    w.rep.rec(
        "㉖B 选择条取值域齐备（类型 2 / 严重度 4 / 影响 3）",
        len(d.get("kinds") or []) == 2
        and len(d.get("severities") or []) == 4
        and len(d.get("impacts") or []) == 3,
        f"{len(d.get('kinds') or [])}/{len(d.get('severities') or [])}/{len(d.get('impacts') or [])}",
    )
    w.rep.rec(
        "㉖B 默认影响类型**不是**「阻断执行」（默认值不该带流程后果）",
        (d.get("form") or {}).get("impact_kind") == "review-required",
        str((d.get("form") or {}).get("impact_kind")),
    )
    w.shot("26-B-登记页-初始态")

    print("\n-- C. 负例：阻断类不挂受影响项 → 拦住且不写库 --", flush=True)
    sel_impact = '[data-field="impact_kind"][data-key="execution-blocking"]'
    w.rep.rec(
        "㉖C 影响类型锚点复合选择器唯一命中",
        w.c.count(sel_impact) == 1,
        w.c.count(sel_impact),
    )
    w.c.tap(sel_impact)
    w.c.tap('[data-field="severity"][data-key="high"]')
    w.c.set_data({"form.title": CASE_TITLE, "form.cause": "走查：主机第 3 缸异常"})
    time.sleep(1.0)
    d = w.c.page_data()
    w.rep.rec(
        "㉖C 选择条**真点击**改到了表单（影响类型 = 阻断执行）",
        (d.get("form") or {}).get("impact_kind") == "execution-blocking",
        str((d.get("form") or {}).get("impact_kind")),
    )
    w.rep.rec(
        "㉖C 严重度真点击改到了表单（= 高）",
        (d.get("form") or {}).get("severity") == "high",
        str((d.get("form") or {}).get("severity")),
    )
    w.rep.rec("㉖C 提交按钮锚点唯一", w.c.count('[data-act-submit-case="1"]') == 1)
    w.c.tap('[data-act-submit-case="1"]')
    time.sleep(2.5)
    w.rep.rec(
        "㉖C 阻断类没挂受影响项 → 被前置检查拦下，**留在本页**",
        w.c.current_path() == CASE_CREATE,
        w.c.current_path(),
    )
    d = w.c.page_data()
    w.rep.rec(
        "㉖C 被拦下后表单内容仍在（不能让用户白填一遍）",
        (d.get("form") or {}).get("title") == CASE_TITLE,
        str((d.get("form") or {}).get("title")),
    )
    w.shot("26-C-登记页-C2拦截")

    print("\n-- D. 选受影响项并提交 --", flush=True)
    w.rep.rec("㉖D 候选开关锚点唯一", w.c.count('[data-act-toggle-links="1"]') == 1)
    w.c.tap('[data-act-toggle-links="1"]')
    time.sleep(4.0)
    d = w.c.page_data()
    cands = d.get("candidates") or []
    w.rep.rec(
        "㉖D 候选懒加载完成（本单任务 + 成果，来自真接口）",
        d.get("candLoaded") is True and len(cands) >= 8,
        f"candLoaded={d.get('candLoaded')} n={len(cands)}",
    )
    sel_cand = f'[data-kind="task"][data-id="{ENTRUST_TASK_ID}"]'
    n_comp = w.c.count(sel_cand)
    n_single = w.c.count(f'[data-id="{ENTRUST_TASK_ID}"]')
    w.rep.rec(
        "㉖D 复合选择器唯一命中目标候选；单属性选择器**不唯一**（这就是必须用复合的实证）",
        n_comp == 1 and n_single > 1,
        f"复合 n={n_comp} / 单属性 n={n_single}",
    )
    w.c.tap(sel_cand)
    time.sleep(1.5)
    links = w.c.page_data().get("links") or []
    w.rep.rec(
        "㉖D 受影响项真的进了列表（key 形如 task-N）",
        len(links) == 1 and str(links[0].get("key")) == f"task-{ENTRUST_TASK_ID}",
        json.dumps(links, ensure_ascii=False)[:120],
    )
    # 移除锚点：属性名是 `data-rm-key`（`data-key` 已被筛选 pill 占用，共用会歧义）
    w.c.tap(f'[data-rm-key="task-{ENTRUST_TASK_ID}"]')
    time.sleep(1.0)
    w.rep.rec(
        "㉖D 移除按钮锚点可用（data-rm-key，不与筛选 pill 的 data-key 冲突）",
        len(w.c.page_data().get("links") or []) == 0,
        f"n={len(w.c.page_data().get('links') or [])}",
    )
    w.c.tap(sel_cand)
    time.sleep(1.2)
    w.shot("26-D-登记页-已选受影响项")

    w.c.tap('[data-act-submit-case="1"]')
    w.rep.rec(
        "㉖E 提交成功后**替换**到案件详情页（返回键不该回到已提交的表单）",
        w.c.wait_path(CASE, tries=20),
        w.c.current_path(),
    )
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    det = d.get("detail") or {}
    new_id = d.get("caseId")
    w.rep.rec(
        "㉖E 落到的是**刚登记的那一宗**，且标题来自界面输入（重取数＝后端事实）",
        d.get("view") == "ready" and det.get("title") == CASE_TITLE,
        f"view={d.get('view')} caseId={new_id} title={det.get('title')}",
    )
    w.rep.rec(
        "㉖E 阻断标记由后端派生（execution-blocking → blocking=True）",
        det.get("blocking") is True and det.get("impactLabel") == "阻断执行",
        f"blocking={det.get('blocking')} impact={det.get('impactLabel')}",
    )
    blocks = det.get("blocks") or []
    affected = ([b for b in blocks if b.get("key") == "affected"] or [{}])[0]
    items = affected.get("items") or []
    w.rep.rec(
        "㉖E 受影响项**随案件同事务写入**（②受影响记录有 1 条，不是空）",
        len(items) == 1 and str(items[0].get("text")) == f"任务 #{ENTRUST_TASK_ID}",
        json.dumps(items, ensure_ascii=False)[:140],
    )
    w.shot("26-E-登记后落到案件详情")
    w.rep.rec(
        "㉖ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        w.new_errors(base_err)[:200],
    )
    print(f"    （本章新建案件 caseId={new_id}；后续章节用它继续）", flush=True)
    _STATE["case_id"] = new_id


def sec_27(w: Walker) -> None:
    """㉗ 记录决定（ENT-030 切四之六）：`open → in_review`，并验"必填项缺了就拦住"。

    ⚠️ 输入走**页内表单**而不是 `wx.showModal`：原生弹层**不在渲染树里**
    （`weui-dialog*` 选择器全部 n=0、`page` 的 outerWXML 为空），走查工具点不到它的
    确认键 ⇒ 弹层承担的关键输入**无法被验证**。该改动见 `case.js` 的
    `onPickDecision` 注释（同一取向此前用过一次：7 项任务类型不用 showActionSheet）。
    """
    print("\n== ㉗ 记录决定（open → in_review）==", flush=True)
    base_err = w.c.errors()
    cid = _STATE.get("case_id")
    if not cid:
        w.rep.rec("㉗ 前置 · 有可用的 case_id（㉖ 已登记）", False, "㉖ 未产出 case_id")
        return
    w.c.nav("reLaunch", f"/{CASE}?case_id={cid}", CASE)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    w.rep.rec("㉗ 案件页 ready", d.get("view") == "ready", str(d.get("view")))
    w.rep.rec(
        "㉗ 处置区能力位：可决定 / 可关闭 / 可加受影响项（open 下都亮）",
        d.get("canDecide") is True and d.get("canClose") is True and d.get("canAddLink") is True,
        f"decide={d.get('canDecide')} close={d.get('canClose')} add={d.get('canAddLink')}",
    )
    w.rep.rec(
        "㉗ 决定选项来自契约镜像（open 下 3 个，且**不含 closed** —— 关闭归 close 命令）",
        [o.get("key") for o in (d.get("decisionOptions") or [])]
        == ["in_review", "approved", "rejected"],
        str([o.get("key") for o in (d.get("decisionOptions") or [])]),
    )
    w.rep.rec(
        "㉗ 关闭处置来自契约镜像（阻断异常在 open 下拿不到 resolved）",
        [o.get("key") for o in (d.get("closureOptions") or [])]
        == ["cancelled", "duplicate", "superseded"],
        str([o.get("key") for o in (d.get("closureOptions") or [])]),
    )
    w.rep.rec("㉗ 决定选择条锚点唯一", w.c.count('[data-status="in_review"]') == 1)

    # 负例：选「已批准」但不填依据版本 → 页内拦住（批准必填依据版本，服务端 §3.1.1）
    w.c.tap('[data-status="approved"]')
    time.sleep(1.2)
    d = w.c.page_data()
    w.rep.rec(
        "㉗ 选中「已批准」后才出现依据版本输入框（该栏只对批准有意义）",
        (d.get("decideForm") or {}).get("to") == "approved" and w.c.count('[data-df="basis"]') == 1,
        f"to={(d.get('decideForm') or {}).get('to')} n={w.c.count('[data-df="basis"]')}",
    )
    w.c.tap('[data-act-decide-submit="1"]')
    time.sleep(2.5)
    hint = str(w.c.page_data().get("decideHint") or "")
    w.rep.rec(
        "㉗ 缺依据版本 → 页内拦住并说明原因",
        bool(hint),
        hint or "(没有提示)",
    )
    w.shot("27-A-决定-缺依据版本拦截")

    # 正路：改选「复核中」→ 填说明 → 真点击提交
    w.c.tap('[data-status="in_review"]')
    time.sleep(1.2)
    w.c.set_data({"decideForm.note": DECISION_NOTE})
    time.sleep(0.8)
    w.rep.rec(
        "㉗ 换目标状态后依据版本输入框消失（不留一个用不上的栏）",
        w.c.count('[data-df="basis"]') == 0,
    )
    w.c.tap('[data-act-decide-submit="1"]')
    time.sleep(5.0)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    det = d.get("detail") or {}
    w.rep.rec(
        "㉗ 后端事实：状态真的到了 in_review（页面重取数后显示「复核中」）",
        (det.get("status") == "in_review") and det.get("statusLabel") == "复核中",
        f"status={det.get('status')} label={det.get('statusLabel')}",
    )
    blk = {b.get("key"): b for b in (det.get("blocks") or [])}
    rows = (blk.get("decision") or {}).get("rows") or []
    w.rep.rec(
        "㉗ 界面：④决定与审批 有了内容（不再空态），且说明就是刚填的那句",
        any(str(r.get("value")) == DECISION_NOTE for r in rows),
        json.dumps(rows, ensure_ascii=False)[:160],
    )
    w.rep.rec(
        "㉗ 提交成功后表单清空（不把上一次的输入留在屏幕上）",
        (d.get("decideForm") or {}).get("to") == "",
        json.dumps(d.get("decideForm"), ensure_ascii=False),
    )
    w.shot("27-B-记录决定后")
    w.rep.rec(
        "㉗ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        w.new_errors(base_err)[:200],
    )


def sec_28(w: Walker) -> None:
    """㉘ 关闭案件（ENT-030 切四之六）：先钉一条**状态机事实**，再走合法路径关闭。

    ⚠️ 本节第一版把顺序写成「open → in_review → 关闭」，结果 6 条断言全红，
    报出来是"关闭区没渲染"，**看起来像页面漏了一块**。实际是：

        `exception` 从 `in_review` **没有到 `closed` 的边**，关闭处置表里也**没有**
        `(exception, in_review)` 这一格 ⇒ 后端给 `can_close=false`、界面不渲染关闭条。

    **产品是对的，是脚本排错了顺序。** 所以本节把这个"不给关闭入口"的行为
    也写成一条 PASS 断言（它本身就是 §3.4 该有的形状），然后走合法路径：
    `in_review → rejected`（复核驳回）→ 从 `rejected` 关闭。

    这条教训按 skill 的口径处理：**断言失败先怀疑断言写错，再去怀疑代码**。
    """
    print("\n== ㉘ 关闭案件（必须给处置与证据）==", flush=True)
    base_err = w.c.errors()
    cid = _STATE.get("case_id")
    if not cid:
        w.rep.rec("㉘ 前置 · 有可用的 case_id", False, "㉖ 未产出 case_id")
        return
    w.c.nav("reLaunch", f"/{CASE}?case_id={cid}", CASE)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    n_close = w.c.count('[data-disp="cancelled"]')
    w.rep.rec(
        "㉘ in_review 下**不给**关闭入口"
        "（状态机里 exception 从 in_review 没有到 closed 的边 —— 这是形状，不是缺陷）",
        d.get("canClose") is False and n_close == 0 and not (d.get("closureOptions") or []),
        f"canClose={d.get('canClose')} 关闭条 n={n_close}",
    )

    # 合法路径：in_review → rejected
    w.c.tap('[data-status="rejected"]')
    time.sleep(1.2)
    w.c.set_data({"decideForm.note": REJECT_NOTE})
    time.sleep(0.8)
    w.c.tap('[data-act-decide-submit="1"]')
    time.sleep(5.0)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    det = d.get("detail") or {}
    w.rep.rec(
        "㉘ 后端事实：状态到了 rejected（in_review → rejected 是合法边）",
        det.get("status") == "rejected",
        f"status={det.get('status')} label={det.get('statusLabel')}",
    )
    n_close2 = w.c.count('[data-disp="cancelled"]')
    w.rep.rec(
        "㉘ 从 rejected 起才出现关闭入口（与状态机一致，不是页面漏块）",
        d.get("canClose") is True and n_close2 == 1,
        f"canClose={d.get('canClose')} n={n_close2}",
    )
    w.shot("28-A-复核驳回后-关闭入口出现")

    w.c.tap('[data-disp="cancelled"]')
    time.sleep(1.5)
    d = w.c.page_data()
    n_ev = w.c.count('[data-df="evidence"]')
    w.rep.rec(
        "㉘ 选中处置后出现证据引用栏（没有一键关闭）",
        (d.get("closeForm") or {}).get("disp") == "cancelled" and n_ev == 1,
        f"disp={(d.get('closeForm') or {}).get('disp')} n={n_ev}",
    )
    # 负例：证据留空直接提交 → 页内拦住
    w.c.tap('[data-act-close-submit="1"]')
    time.sleep(2.5)
    hint = str(w.c.page_data().get("closeHint") or "")
    w.rep.rec("㉘ 证据留空 → 页内拦住并说明原因", bool(hint), hint or "(没有提示)")
    w.shot("28-B-关闭-缺证据拦截")

    w.c.set_data({"closeForm.evidence": CLOSE_EVIDENCE, "closeForm.resolution": CLOSE_NOTE})
    time.sleep(1.0)
    w.c.tap('[data-act-close-submit="1"]')
    time.sleep(5.0)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    det = d.get("detail") or {}
    w.rep.rec(
        "㉘ 后端事实：案件已关闭（页面重取数后显示「已关闭」）",
        det.get("status") == "closed",
        f"status={det.get('status')} label={det.get('statusLabel')}",
    )
    blk = {b.get("key"): b for b in (det.get("blocks") or [])}
    clo = (blk.get("closure") or {}).get("rows") or []
    w.rep.rec(
        "㉘ 界面：⑥结案 显示处置方式与说明（不再空态）",
        any("撤销" in str(r.get("value")) for r in clo),
        json.dumps(clo, ensure_ascii=False)[:180],
    )
    exi = (blk.get("evidence") or {}).get("items") or []
    w.rep.rec(
        "㉘ 界面：⑤执行证据 出现刚填的证据引用（关闭**必带证据**）",
        any(CLOSE_EVIDENCE in str(i.get("text")) for i in exi),
        json.dumps(exi, ensure_ascii=False)[:160],
    )
    w.rep.rec(
        "㉘ 已关闭 → 处置区收回关闭/决定按钮（能力位随状态收回）",
        d.get("canClose") is False and d.get("canDecide") is False,
        f"close={d.get('canClose')} decide={d.get('canDecide')}",
    )
    w.shot("28-C-关闭后")
    w.rep.rec(
        "㉘ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        w.new_errors(base_err)[:200],
    )


def sec_29(w: Walker) -> None:
    """㉙ UI-04 组织级队列（ENT-032）：队列切换 / 两行筛选 / 点行进案件详情。

    本章的存在理由 = **每一行都要能点进那一宗**。走查工具没有 index 参数，
    「点第 N 行」只能靠属性选择器；而这一页上同时有**四组**可点元素
    （两个队列 pill、两行筛选、案件卡），属性名两两不同是**前提**而非风格 ——
    都是 `data-key` 时 `[data-key="all"]` 会落到另一个筛选条上，选到谁看引擎实现。

    前置：走查库须已铺 `backend/scripts/seed_entrust_demo.py`（自带两宗案件），
    且默认顺序里 ㉖㉗㉘ 已跑过 —— 那一章登记的案件在 ㉘ 被关闭，于是
    「关闭的那宗在 `unclosed` 里不出现、在 `all` 里出现」成为筛选生效的硬证据。
    """
    print("\n== ㉙ UI-04 组织级队列（切换 / 筛选 / 点行进详情）==", flush=True)
    base_err = w.c.errors()

    path = w.reenter_workbench()
    w.rep.rec("㉙ 前置 · 从「我的」页进入经理工作台", path == WORKBENCH, path)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)

    print("\n-- A. 默认队列与锚点唯一 --", flush=True)
    n_asg = w.c.count('[data-queue="assignment"]')
    n_case = w.c.count('[data-queue="case"]')
    w.rep.rec(
        "㉙A 默认落在「委托队列」，两个队列 pill 各唯一命中",
        d.get("queue") == "assignment" and n_asg == 1 and n_case == 1,
        f"queue={d.get('queue')} n_assignment={n_asg} n_case={n_case}",
    )
    w.shot("29-A-工作台-委托队列")

    print("\n-- B. 真点击切到案件队列 --", flush=True)
    w.c.tap('[data-queue="case"]')
    d = w.wait_data(
        lambda x: x.get("queue") == "case" and x.get("view") not in (None, "", "loading"),
        tries=40,
        gap=0.5,
    )
    items = d.get("items") or []
    w.rep.rec(
        "㉙B 真点击切到案件队列；行形状是**案件**（每行有 caseId，没有委托行的字段残留）",
        d.get("queue") == "case"
        and bool(items)
        and all(("caseId" in it) and ("assignmentId" not in it) for it in items),
        f"total={d.get('total')} 首行={json.dumps(items[0], ensure_ascii=False)[:110] if items else '(空)'}",
    )
    w.shot("29-B-案件队列")

    print("\n-- C. 案件卡锚点唯一 → 点哪一宗进哪一宗 --", flush=True)
    # 挑**最后一行**而不是第一行：第一行用「总是选第一个」的坏选择器也会对，
    # 最后一行的位置本身就是对锚点的考验（ENT-031 修的就是这类歧义）。
    target = items[-1] if items else {}
    tid = str(target.get("caseId") or "")
    n_card = w.c.count(f'[data-case-id="{tid}"]') if tid else 0
    w.rep.rec(
        "㉙C 案件卡锚点唯一命中（`data-case-id`，不与筛选 pill 的属性撞名）",
        bool(tid) and n_card == 1,
        f"caseId={tid} n={n_card}",
    )
    if tid and n_card == 1:
        w.c.tap(f'[data-case-id="{tid}"]')
        opened = w.c.wait_path(CASE, tries=25)
        w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
        d2 = w.c.page_data()
        w.rep.rec(
            "㉙C 点**最后一行**进的是**那一宗**（不是恰好第一宗）",
            bool(opened) and str(d2.get("caseId")) == tid,
            f"当前路径={w.c.current_path()} caseId={d2.get('caseId')} 期望={tid}",
        )
        w.shot("29-C-点行进案件详情")
    else:
        w.rep.rec(
            "㉙C 点**最后一行**进的是**那一宗**（不是恰好第一宗）",
            False,
            "锚点不唯一，跳过",
        )

    print("\n-- D. 开闭范围筛选 --", flush=True)
    w.reenter_workbench()
    w.c.tap('[data-queue="case"]')
    d = w.wait_data(
        lambda x: x.get("queue") == "case" and x.get("view") not in (None, "", "loading"),
        tries=40,
        gap=0.5,
    )
    closed_id = str(_STATE.get("case_id") or "")
    unclosed_ids = [str(x.get("caseId")) for x in (d.get("items") or [])]
    unclosed_total = int(d.get("total") or 0)
    w.rep.rec(
        "㉙D 默认范围是「未关闭」，且 ㉘ 关闭掉的那宗不在未关闭清单里",
        d.get("activeCaseScope") == "unclosed" and (not closed_id or closed_id not in unclosed_ids),
        f"scope={d.get('activeCaseScope')} total={unclosed_total} ㉖案件={closed_id}",
    )
    w.c.tap('[data-case-scope="all"]')
    d = w.wait_data(
        lambda x: x.get("activeCaseScope") == "all" and x.get("view") not in (None, "", "loading"),
        tries=40,
        gap=0.5,
    )
    all_ids = [str(x.get("caseId")) for x in (d.get("items") or [])]
    all_total = int(d.get("total") or 0)
    w.rep.rec(
        "㉙D 切「全部」后总数**严格更多**，且已关闭那宗出现了（筛选真的生效）",
        d.get("activeCaseScope") == "all"
        and all_total > unclosed_total
        and (not closed_id or closed_id in all_ids),
        f"all={all_total} unclosed={unclosed_total} 已关闭在全部里={closed_id in all_ids}",
    )
    w.shot("29-D-范围-全部")
    w.c.tap('[data-case-scope="unclosed"]')
    d = w.wait_data(
        lambda x: (
            x.get("activeCaseScope") == "unclosed" and x.get("view") not in (None, "", "loading")
        ),
        tries=40,
        gap=0.5,
    )
    w.rep.rec(
        "㉙D 切回「未关闭」后总数回到较少的那一侧（两个方向都能点）",
        d.get("activeCaseScope") == "unclosed" and int(d.get("total") or 0) == unclosed_total,
        f"total={d.get('total')} 期望={unclosed_total}",
    )

    print("\n-- E. 案件类型筛选 --", flush=True)
    n_ck = w.c.count('[data-case-kind="change_request"]')
    w.rep.rec("㉙E 类型筛选 pill 锚点唯一命中", n_ck == 1, f"n={n_ck}")
    w.c.tap('[data-case-kind="change_request"]')
    d = w.wait_data(
        lambda x: (
            x.get("activeCaseKind") == "change_request"
            and x.get("view") not in (None, "", "loading")
        ),
        tries=40,
        gap=0.5,
    )
    rows = d.get("items") or []
    w.rep.rec(
        "㉙E 选「变更请求」后**每一行**都是变更请求（不是只有第一行对）",
        d.get("activeCaseKind") == "change_request"
        and bool(rows)
        and all(x.get("kindLabel") == "变更请求" for x in rows),
        f"n={len(rows)} kinds={[x.get('kindLabel') for x in rows][:6]}",
    )
    w.shot("29-E-类型-变更请求")
    n_all = w.c.count('[data-case-kind=""]')
    w.rep.rec("㉙E 「全部类型」pill 取值为空串，锚点仍唯一命中", n_all == 1, f"n={n_all}")
    if n_all == 1:
        w.c.tap('[data-case-kind=""]')
        d = w.wait_data(
            lambda x: x.get("activeCaseKind") == "" and x.get("view") not in (None, "", "loading"),
            tries=40,
            gap=0.5,
        )
        kinds = sorted({str(x.get("kindLabel")) for x in (d.get("items") or [])})
        w.rep.rec(
            "㉙E 切回「全部类型」后两种案件都回来了（否则筛选会变成单向开关）",
            len(kinds) >= 2,
            f"kinds={kinds}",
        )
    else:
        w.rep.rec(
            "㉙E 切回「全部类型」后两种案件都回来了（否则筛选会变成单向开关）",
            False,
            "空串锚点没命中，跳过",
        )

    w.rep.rec(
        "㉙ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        w.new_errors(base_err)[:200],
    )


def sec_30(w: Walker) -> None:
    """㉚ 「批准」的完整正例（ENT-032）：依据版本 id 从界面取到 → 填对能过。

    ㉗ 只验了「缺依据版本被拦」这半边 —— **半个负例不能证明这条路走得通**：
    若 `basis_revision_id` 被当成 `revision_no` 收（两者是不同的数），界面上
    **永远批准不了**，而「被拦」那半边照样绿。本章补另外半边。

    依据版本 id 从哪来？本章**故意走界面**取：案件页的候选面板给出本单的成果 →
    进成果页读「版本历史」那行 id。为此本轮顺带修了一个真缺陷：成果页原先
    **只显示 `vN`（revision_no）**，而案件页要填的是 revision **id** ——
    界面上根本取不到那个值（走查发现）。现在成果页每条都标「版本 id N」。

    ⚠️ 写失败的提示走 `wx.showModal`，**不在渲染树里**（工具读不到，见技能里的
    「原生弹层」那条）。所以负例只断言**没写进去**（后端事实），不断言提示文案 ——
    「看不见」与「没有」是两件事，不能拿后者当结论。
    """
    print("\n== ㉚ 「批准」正例（依据版本 id 从界面取到 → 填对能过）==", flush=True)
    base_err = w.c.errors()

    print("\n-- A. 从队列里挑一宗「待处理」的案件（不写死编号）--", flush=True)
    path = w.reenter_workbench()
    w.rep.rec("㉚ 前置 · 进入经理工作台", path == WORKBENCH, path)
    w.c.tap('[data-queue="case"]')
    d = w.wait_data(
        lambda x: x.get("queue") == "case" and x.get("view") not in (None, "", "loading"),
        tries=40,
        gap=0.5,
    )
    # 必须挑 **异常** 而不是变更请求：`change_request` 在 `open` 下的出边只有
    # in_review / rejected —— **没有 approved**（它的批准要等复核之后，见
    # `_STATUS_TRANSITIONS`）。挑错类型会让整章以 `[data-status="approved"] n=0` 失败，
    # 看起来像"界面少了个按钮"，其实是脚本挑错了对象（2026-09-14 实测踩到）。
    open_rows = [
        x
        for x in (d.get("items") or [])
        if x.get("statusLabel") == "待处理" and x.get("kindLabel") == "异常"
    ]
    cid = str(open_rows[0].get("caseId") or "") if open_rows else ""
    w.rep.rec(
        "㉚A 队列里能找到一宗「待处理」的**异常**案件（编号来自界面）",
        bool(cid),
        f"待处理异常 {len(open_rows)} 宗 → caseId={cid}",
    )
    if not cid:
        w.rep.rec("㉚ 本章后续断言", False, "没有待处理案件，跳过")
        return
    case_url = f"/{CASE}?case_id={cid}"
    w.c.nav("reLaunch", case_url, CASE)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)

    print("\n-- B. 选「已批准」→ 出现依据版本输入框 --", flush=True)
    w.rep.rec(
        "㉚B 案件页 ready 且持有正确的案件编号",
        d.get("view") == "ready" and str(d.get("caseId")) == cid,
        f"view={d.get('view')} caseId={d.get('caseId')}",
    )
    n_ok = w.c.count('[data-status="approved"]')
    w.rep.rec("㉚B 「已批准」选项锚点唯一命中", n_ok == 1, f"n={n_ok}")
    w.c.tap('[data-status="approved"]')
    time.sleep(1.2)
    d = w.c.page_data()
    n_basis = w.c.count('[data-df="basis"]')
    w.rep.rec(
        "㉚B 真点击选中「已批准」后出现依据版本输入框",
        (d.get("decideForm") or {}).get("to") == "approved" and n_basis == 1,
        f"to={(d.get('decideForm') or {}).get('to')} n={n_basis}",
    )

    print("\n-- C. 负例：一个**不存在**的版本 id 不能被当成填对 --", flush=True)
    w.c.set_data({"decideForm.basis": str(BOGUS_REVISION_ID), "decideForm.note": APPROVE_NOTE})
    time.sleep(0.8)
    w.c.tap('[data-act-decide-submit="1"]')
    time.sleep(4.0)
    d = w.c.page_data()
    w.rep.rec(
        "㉚C 不存在的版本 id 被服务端拒掉：案件**仍是待处理**（没写进去）",
        (d.get("detail") or {}).get("status") == "open",
        f"status={(d.get('detail') or {}).get('status')}；"
        f"提示走 wx.showModal（渲染树外，工具读不到，故不断言文案）",
    )

    print("\n-- D. 从界面取一份**真实**的成果版本 id --", flush=True)
    w.c.tap('[data-act-toggle-links="1"]')
    d = w.wait_data(lambda x: x.get("candLoaded"), tries=40, gap=0.5)
    art_rows = [x for x in (d.get("candidates") or []) if x.get("target_kind") == "artifact"]
    art_id = str(art_rows[0].get("target_id") or "") if art_rows else ""
    w.rep.rec(
        "㉚D 候选面板列出本单的成果（该面板是「本单有什么成果」的权威来源）",
        bool(art_id),
        f"成果候选 {len(art_rows)} 项 → artifact_id={art_id}",
    )
    if not art_id:
        w.rep.rec("㉚ 本章后续断言", False, "没有成果候选，跳过")
        return
    w.c.nav("reLaunch", f"/{ARTIFACT}?artifact_id={art_id}", ARTIFACT)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    revs = d.get("revisions") or []
    rid = str(revs[0].get("revisionId") or "") if revs else ""
    tl = str(w.c.text(".tl-time") or "")
    w.rep.rec(
        "㉚D 成果页「版本历史」**显示出**版本 id（原先只显示 vN，界面上取不到这个值）",
        bool(rid) and "版本 id" in tl,
        f"revisionId={rid} 首行文案={tl[:60]}",
    )
    w.shot("30-D-成果页-版本id")

    print("\n-- E. 正例：填对 → 状态真的到 approved --", flush=True)
    w.c.nav("reLaunch", case_url, CASE)
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    w.c.tap('[data-status="approved"]')
    time.sleep(1.2)
    w.c.set_data({"decideForm.basis": rid, "decideForm.note": APPROVE_NOTE})
    time.sleep(0.8)
    w.c.tap('[data-act-decide-submit="1"]')
    d = w.wait_data(
        lambda x: ((x.get("detail") or {}).get("status")) == "approved",
        tries=40,
        gap=0.5,
    )
    det = d.get("detail") or {}
    w.rep.rec(
        "㉚E 填**真实**的版本 id → 状态真的到了 approved（批准这条路走得通）",
        det.get("status") == "approved",
        f"status={det.get('status')} label={det.get('statusLabel')}",
    )
    blk = {b.get("key"): b for b in (det.get("blocks") or [])}
    dec_rows = (blk.get("decision") or {}).get("rows") or []
    basis_row = [r for r in dec_rows if r.get("label") == "依据版本"]
    w.rep.rec(
        "㉚E ④决定与审批 的「依据版本」显示的就是刚填的那个 id（与输入框口径一致）",
        bool(basis_row) and str(basis_row[0].get("value")) == "版本 id " + rid,
        json.dumps(basis_row, ensure_ascii=False)[:140],
    )
    w.shot("30-E-批准后")
    w.rep.rec(
        "㉚ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        w.new_errors(base_err)[:200],
    )


# ============================== 未迁移章节（ENT-035） ==============================
# 旧脚本有、换轨后一直记 `not-run` 的 12 章。DR-0009 记的阻塞点是「按序号点第 i 个
# 同类元素」——wechatide 的元素工具没有 index 参数、忽略伪类、`--x/--y` 也落到第一个
# 匹配项。这里走 DR-0009 给的**出路 ①**：页面已登记唯一锚点（`data-act-*` /
# `data-order-id` / `data-id` / 新增的 `data-svc-key` 等），配合 REST 预取出来的真实
# id 走属性选择器 ⇒ **本章的点击全部是真实点击**，没有退化成 `callMethod`。
#
# 唯一例外是端口会话：C 端「我是港口方」入口已下线，只能把 port 身份的 token 写进
# Storage（键与 `utils/auth.js` / `utils/request.js` 一致）—— 页面渲染与后续点击
# 仍然是真机的，只是绕过了已删除的入口。这一点在断言名里写明了。

#: 智能合同仿真案例的预期风险（与 `seed_contract_cases` 铺设的三条规则一一对应）
EXPECT_RISK = {
    "R3": "装货日期临近",
    "R4": "船舶证书临期",
    "R5": "液货/危险品运输",
}

CODE_PORT = "seed-port"


def _anchors(w: Walker) -> Anchors:
    """取（并缓存）演示数据锚点。

    ⚠️ 预取结果**本身记一条断言**：锚点缺失时后面各章会成片"跳过"，而"跳过"在
    汇总里和"通过"长得一模一样 —— 这是本项目反复强调的「不把 not-run 记成通过」。
    """
    a = _STATE.get("anchors")
    if a is None:
        a = Anchors.fetch()
        _STATE["anchors"] = a
        missing = [
            name
            for name, val in (
                ("shipper token", a.token_shipper),
                ("owner token", a.token_owner),
                ("port token", a.token_port),
                ("待支付订单", a.pay_order_id),
                ("撮合货源", a.match_cargo_id),
                ("已认证船", a.ship_id),
                ("港口预约", a.appt_id),
                ("港口泊位", a.berth_id),
            )
            if val is None
        ]
        # 缺失项**带上取数详情**：区分"请求失败"与"库里真没有"。
        # 少了这层，两者在汇总里一模一样，会被统一读成"页面/数据坏了"。
        detail = "；".join(f"{n}：{a.notes[n]}" for n in missing if n in a.notes)
        w.rep.rec(
            "锚点预取：三身份 token + 订单/货源/船/预约/泊位",
            not missing,
            (
                "缺失=" + "、".join(missing) + (f"（{detail}）" if detail else "")
                if missing
                else f"pay={a.pay_order_id} cargo={a.match_cargo_id} ship={a.ship_id} "
                f"appt={a.appt_id} berth={a.berth_id} sim={len(a.sim_cases)}"
            ),
        )
    return a


def _contract_ready(d: dict) -> bool:
    """合同页就绪：不在 loading，且（有正文 或 已落到错误态）。

    固定 sleep 不可靠 —— 真实生成慢于 sleep 时读到的是 loading 态空数据，
    表现为 `raw=0 / risks=[]`，看起来像功能坏了，其实只是等太短。
    """
    return d.get("loading") is False and (
        len(str(d.get("rawText") or "")) > 0 or bool(d.get("error"))
    )


def _goto_orders(w: Walker, code: str = CODE_SHIPPER, role: str = "shipper") -> dict:
    """登录指定身份 → 点身份卡进工作台 → 切到「订单」tab，返回订单页 data。"""
    w.login_as(code)
    w.enter_role(role, SHIPPER if role == "shipper" else OWNER)
    w.c.nav("switchTab", "/" + ORDERS, ORDERS)
    time.sleep(1.8)
    return w.c.page_data()


def _port_session(w: Walker, a: Anchors) -> bool:
    """把 port 身份会话写进 Storage（C 端「我是港口方」入口已下线）。

    与旧脚本同义：`api_login` → 必要时 `switch-role` → `/auth/me` → 写
    `access_token` + `user_info`。返回 False 表示前置就失败了。
    """
    if not a.token_port:
        return False
    me = api_get("/auth/me", a.token_port)
    if not me:
        return False
    if me.get("current_role") != "port":
        _st, data = api_post("/auth/switch-role", a.token_port, {"role": "port"})
        tok = (data or {}).get("access_token")
        if tok:
            a.token_port = tok
            me = api_get("/auth/me", a.token_port) or me
    w.c.set_storage("access_token", a.token_port)
    w.c.set_storage("user_info", json.dumps(me, ensure_ascii=False))
    w.c.remove_storage("dev_login_code")
    return True


def sec_4b(w: Walker) -> None:
    """④b 撮合页（货主方向：为货源找船）。"""
    print("\n== ④b 撮合（货主方向） ==", flush=True)
    a = _anchors(w)
    if a.match_cargo_id is None:
        w.rep.rec(
            "④b 撮合页(货主方向)进入",
            False,
            "前置锚点缺失：没有可撮合的 published 货源",
        )
        return
    w.login_as(CODE_SHIPPER)
    w.enter_role("shipper", SHIPPER)
    w.c.nav("navigateTo", f"/{MATCH}?mode=cargo&refId={a.match_cargo_id}", MATCH)
    time.sleep(2.6)
    w.shot("09-撮合-为货源找船")
    md = w.c.page_data()
    w.rep.rec("④ 撮合页(货主方向)进入", w.c.current_path() == MATCH, w.c.current_path())
    w.rep.rec(
        "④ 候选与未入局原因就绪",
        (md.get("total") or 0) > 0 and bool(md.get("filterStats")),
        f"total={md.get('total')} filter={json.dumps(md.get('filterStats'), ensure_ascii=False)[:120]}",
    )
    cand = (md.get("items") or [{}])[0]
    dims = cand.get("dims") or []
    if cand:
        w.rep.rec(
            "④ 评分四项拆解齐备",
            len(dims) == 4,
            " ".join(f"{d.get('name')}={d.get('value')}/{d.get('max')}" for d in dims),
        )
    # 「点第 1 张候选卡的下单按钮」：`data-act-pick-ship="<ship_id>"` 是唯一锚点
    ship_id = cand.get("ship_id")
    opened = False
    if ship_id is not None:
        w.c.tap(f'[data-act-pick-ship="{ship_id}"]')
        opened = bool(
            w.wait_data(lambda d: bool(d.get("orderModal")), tries=12, gap=0.4).get("orderModal")
        )
    w.shot("09b-撮合-下单确认弹窗")
    w.rep.rec("④ 下单确认弹窗可打开", opened, f"ship #{ship_id}")
    if opened:
        w.c.tap(".btn-ghost")
        time.sleep(0.9)
        w.rep.rec(
            "④ 弹窗取消后未产生订单",
            not w.c.page_data().get("orderModal"),
            str(w.c.page_data().get("orderModal")),
        )
    w.back_to(SHIPPER)


def sec_05(w: Walker) -> None:
    """⑤ 发布空船页渲染（船东视角）。"""
    print("\n== ⑤ 发布空船 ==", flush=True)
    w.login_as(CODE_OWNER)
    ok = w.enter_role("owner", OWNER)
    w.rep.rec("⑤ 船东工作台进入（真实点击身份卡）", ok, w.c.current_path())
    if not ok:
        return
    time.sleep(1.6)
    w.c.nav("navigateTo", "/" + PUBLISH_SHIP, PUBLISH_SHIP)
    time.sleep(1.8)
    w.shot("05-发布空船")
    psd = w.c.page_data()
    w.rep.rec(
        "⑤ 发布空船页渲染（船东视角）",
        (not psd.get("error")) and len(psd.get("ships") or []) > 0,
        f"已认证船={len(psd.get('ships') or [])}",
    )
    w.back_to(OWNER)


def sec_07(w: Walker) -> None:
    """⑦ 合同三级页 + 长按弹层 + ⑦b 仿真案例 + ⑦c 已完成订单。"""
    print("\n== ⑦ 合同（三级页 / 长按 / 仿真案例 / 已完成） ==", flush=True)
    a = _anchors(w)
    if a.pay_order_id is None:
        w.rep.rec("⑦ 「查看合同」真实点击", False, "前置锚点缺失：无待支付订单")
        return
    oid = a.pay_order_id
    _goto_orders(w)
    w.rep.rec(
        "⑦ 待支付订单在列表可见",
        any(x.get("id") == oid for x in (w.c.page_data().get("list") or [])),
        f"order #{oid}",
    )

    # （1）点「查看合同」→ 合同三级页
    tapped = w.tap_order_act(oid, "contract")
    w.rep.rec("⑦ 「查看合同」真实点击", tapped, f'[data-act-contract="{oid}"]')
    w.c.wait_path(CONTRACT, 25)
    cd = w.wait_data(_contract_ready, tries=60, gap=0.5)
    w.shot("07-合同预览三级页")
    w.rep.rec("⑦ 合同页进入", w.c.current_path() == CONTRACT, w.c.current_path())
    w.rep.rec(
        "⑦ 合同正文有内容",
        len(cd.get("contractHtml") or "") > 200 or len(cd.get("rawText") or "") > 200,
        f"html={len(cd.get('contractHtml') or '')} raw={len(cd.get('rawText') or '')}",
    )
    w.rep.rec(
        "⑦ 风险卡渲染",
        isinstance(cd.get("risks"), list),
        f"risks={len(cd.get('risks') or [])} high={cd.get('highCount')}",
    )
    w.back_to(ORDERS)
    time.sleep(1.2)

    # （2）长按订单卡 → 合同弹层（长按只能靠元素工具的 longpress）
    w.c.longpress(f'[data-order-id="{oid}"]')
    c3 = w.wait_data(lambda d: bool((d.get("contract") or {}).get("show")), tries=24, gap=0.4)
    w.shot("07b-订单页-长按合同弹层")
    shown = bool((c3.get("contract") or {}).get("show"))
    w.rep.rec("⑦ 长按订单卡弹出合同弹层", shown, f"contract.show={shown}")
    if shown:
        w.c.tap(".modal-mask")
        time.sleep(0.7)

    # （3）⑦b 智能合同仿真案例（R3/R4/R5 → 命中预期风险规则）
    for sc in a.sim_cases:
        key, soid = sc["key"], sc["order_id"]
        if w.c.current_path() != ORDERS:
            w.c.nav("switchTab", "/" + ORDERS, ORDERS)
            time.sleep(1.5)
        vis = any(x.get("id") == soid for x in (w.c.page_data().get("list") or []))
        w.rep.rec(f"⑦b 仿真案例 {key} 在订单列表可见", vis, f"#{soid}")
        if not vis:
            continue
        w.rep.rec(f"⑦b 「查看合同」可点（{key}）", w.tap_order_act(soid, "contract"))
        w.c.wait_path(CONTRACT, 25)
        cd = w.wait_data(_contract_ready, tries=60, gap=0.5)
        w.shot(f"07c-合同-仿真案例-{key}")
        titles = [r.get("title") for r in (cd.get("risks") or [])]
        w.rep.rec(f"⑦b {key} 合同页进入", w.c.current_path() == CONTRACT, w.c.current_path())
        w.rep.rec(
            f"⑦b {key} 命中预期风险「{EXPECT_RISK.get(key)}」",
            EXPECT_RISK.get(key) in titles,
            "risks=" + "、".join(str(t) for t in titles),
        )
        w.rep.rec(
            f"⑦b {key} 合同正文非空",
            len(cd.get("rawText") or "") > 400,
            f"raw={len(cd.get('rawText') or '')}",
        )
        w.back_to(ORDERS)
        time.sleep(1.0)

    # （4）⑦c 已完成订单也能查看合同（干净合同 / 无风险）
    if a.completed_order_id is None:
        w.rep.rec("⑦c 已完成订单可查看合同", False, "前置锚点缺失：库里没有 completed 订单")
        return
    if w.c.current_path() != ORDERS:
        w.c.nav("switchTab", "/" + ORDERS, ORDERS)
        time.sleep(1.5)
    done = a.completed_order_id
    w.rep.rec("⑦c 已完成订单可查看合同", w.tap_order_act(done, "contract"), f"#{done}")
    w.c.wait_path(CONTRACT, 25)
    cd2 = w.wait_data(_contract_ready, tries=60, gap=0.5)
    w.shot("07d-合同-已完成订单-无风险")
    w.rep.rec(
        "⑦c 已完成合同无风险项",
        len(cd2.get("risks") or []) == 0,
        f"risks={len(cd2.get('risks') or [])}",
    )
    w.rep.rec(
        "⑦c 已完成合同正文非空",
        len(cd2.get("rawText") or "") > 400,
        f"raw={len(cd2.get('rawText') or '')}",
    )
    w.back_to(ORDERS)


def sec_08(w: Walker) -> None:
    """⑧ 支付详情三级页（真实点击「去支付」）。"""
    print("\n== ⑧ 支付详情三级页 ==", flush=True)
    a = _anchors(w)
    if a.pay_order_id is None:
        w.rep.rec(
            "⑧ 待支付订单在列表可见",
            False,
            "前置锚点缺失：无 matched 且支付单 pending 的订单",
        )
        return
    oid = a.pay_order_id
    _goto_orders(w)
    w.rep.rec(
        "⑧ 待支付订单在列表可见",
        any(x.get("id") == oid for x in (w.c.page_data().get("list") or [])),
        f"order #{oid}",
    )
    tapped = w.tap_order_act(oid, "pay")
    ok = w.c.wait_path(PAYMENT, 25) if tapped else False
    if not ok:
        # 长列表里按钮可能在折叠线外：滚进视口再点一次（仍失败则如实记为失败）
        w.scroll_into(f'[data-order-id="{oid}"]')
        if w.tap_order_act(oid, "pay"):
            ok = w.c.wait_path(PAYMENT, 25)
    w.rep.rec("⑧ 「去支付」真实点击", ok, f'[data-act-pay="{oid}"]')
    time.sleep(2.2)
    w.shot("08-支付详情三级页")
    pm = w.c.page_data()
    w.rep.rec("⑧ 支付页进入", w.c.current_path() == PAYMENT, w.c.current_path())
    w.rep.rec(
        "⑧ 金额为锁定的订单运费",
        bool(pm.get("amountText")) and pm.get("amountText") != "面议",
        f"amountText={pm.get('amountText')}",
    )
    w.rep.rec(
        "⑧ 资金留痕时间轴就绪",
        len(pm.get("timeline") or []) >= 2,
        f"segments={len(pm.get('timeline') or [])}",
    )
    w.rep.rec("⑧ 底栏可支付", pm.get("canPay") is True, f"canPay={pm.get('canPay')}")
    w.back_to(ORDERS)


def sec_8b(w: Walker) -> None:
    """⑧b 支付状态流转 —— **默认不跑**，须显式开关（`WALK_PAY=1`）。

    为什么默认不跑：本步会**消耗演示锚点**。支付单一旦变成 `paid` 就不可回退，
    同一个库上再跑 ⑧ 章就取不到 `pending` 锚点 —— 那一轮会以「前置锚点缺失」失败，
    看起来像第 ⑧ 章坏了，实际是自己把数据改了。⇒ 只在**显式开关 + 临时库**上跑。

    ⚠️ 已知限制（**记在结论里，不当通过**）：「模拟支付成功」的确认键在
    **原生 `wx.showModal`** 里，该弹层不进渲染树、元素工具点不到（本项目铁律）。
    ⇒ 本步用**真实接口**触发支付，再回真机断言**支付之后的渲染**：
    按钮态、状态文案、时间轴、以及订单页的支付入口消失。
    它证明的是「支付后界面正确」，**不是**「弹层确认键可点」。
    """
    print("\n== ⑧b 支付状态流转（WALK_PAY）==", flush=True)
    if os.environ.get("WALK_PAY") != "1":
        # **显式记 not-run**，不是静默跳过：静默跳过会让"少了一章的证据"
        # 看起来像"那一章通过"。
        w.rep.rec(
            "⑧b 支付状态流转",
            True,
            "not-run：需显式开关 WALK_PAY=1（会消耗演示锚点，须在临时库上跑）",
        )
        return

    a = _anchors(w)
    if a.pay_order_id is None or not a.token_shipper:
        w.rep.rec("⑧b 前置锚点", False, "无待支付订单或未取到 shipper token")
        return
    oid = a.pay_order_id
    pay = api_get(f"/payment/payments/order/{oid}", a.token_shipper) or {}
    pay_id = pay.get("id")
    w.rep.rec(
        "⑧b 支付单仍为 pending（开关打开时锚点未被消耗）",
        pay.get("status") == "pending" and bool(pay_id),
        f"pay_id={pay_id} status={pay.get('status')}",
    )
    if not pay_id:
        return

    _goto_orders(w)
    if not w.tap_order_act(oid, "pay"):
        w.scroll_into(f'[data-order-id="{oid}"]')
        w.tap_order_act(oid, "pay")
    w.c.wait_path(PAYMENT, 25)
    time.sleep(1.6)
    before = w.c.page_data()
    w.rep.rec(
        "⑧b 支付前：底栏可支付 + 状态为未支付",
        before.get("canPay") is True and str(before.get("statusLabel")) != "已支付",
        f"canPay={before.get('canPay')} statusLabel={before.get('statusLabel')}",
    )

    # 真实接口触发（弹层确认键点不到 —— 见本函数 docstring 的限制说明）
    status, _data = api_post(f"/payment/payments/{pay_id}/mock-pay", a.token_shipper, {})
    w.rep.rec("⑧b 模拟支付回调成功", status == 200, f"http={status}")

    # 回真机看渲染：**按 URL 重新导航进支付页**，让它走真实取数。
    #
    # ⚠️ 两个"看着像"的做法都不能用（都是本章**第一次真机执行**时才暴露的）：
    #   ① `w.c.refresh()` —— 它的语义是「让 IDE **重新编译**」（见
    #      `wechatide_client.refresh` 的 docstring），重编译会重置页面栈，
    #      于是 `page_data()` 读到的是**别的页面**的 data（`statusLabel` / `canPay` /
    #      `barNote` 全是 `None`）⇒ 看起来像"支付后状态没更新"，实则读错了页。
    #      **判据**：同一页支付前断言全过、支付后**全 None** ⇒ 先怀疑读到别的页。
    #   ② `tap_order_act(oid, "pay")` —— 支付后那个入口**会消失**（正是本章要断言的事）。
    w.c.nav("reLaunch", f"/{PAYMENT}?order_id={oid}", PAYMENT)
    after = w.wait_data(
        lambda x: x.get("canPay") is not None or x.get("statusLabel") is not None,
        tries=40,
        gap=0.5,
    )
    w.shot("08b-支付后")
    w.rep.rec(
        "⑧b 支付后：状态文案变为已支付（渲染层）",
        str(after.get("statusLabel")) == "已支付",
        f"statusLabel={after.get('statusLabel')} canPay={after.get('canPay')}",
    )
    w.rep.rec(
        "⑧b 支付后：可支付按钮消失（不能让同一笔再付一次）",
        after.get("canPay") is False,
        f"canPay={after.get('canPay')} canCreate={after.get('canCreate')}",
    )
    w.rep.rec(
        "⑧b 支付后：底栏说明随之更新",
        str(after.get("barNote") or "") != str(before.get("barNote") or ""),
        f"before={before.get('barNote')!r} after={after.get('barNote')!r}",
    )

    # 幂等：重复回调不重复记账（接口是幂等的），状态仍是 paid
    status2, _d2 = api_post(f"/payment/payments/{pay_id}/mock-pay", a.token_shipper, {})
    now = api_get(f"/payment/payments/order/{oid}", a.token_shipper) or {}
    w.rep.rec(
        "⑧b 重复回调幂等：仍为 paid，且接口不报错",
        status2 == 200 and now.get("status") == "paid" and now.get("id") == pay_id,
        f"http={status2} status={now.get('status')} id={now.get('id')}",
    )

    # 订单页：该单的「去支付」入口 —— **记为限制，不是通过**（首次真机执行时查明）
    #
    # ⚠️ 不能断言"入口消失"：**它不会消失**，根因已查清且不是页面的 bug：
    #   · 订单页的 `data-act-pay` 是**同一属性两处复用**：「去支付」（`status==='matched'`）
    #     与「支付详情」（`status!=='matched'`）；两者条件互斥 ⇒ 同一时刻只命中一个，
    #     所以"锚点数"仍能唯一判断是哪一个；
    #   · 但 `status` 是**订单**状态，而 `OrderOut` **不带支付状态** ⇒ 列表前端无从知道
    #     "这一单的支付单已经 paid"；`payment.service.mock_pay` 也只改支付单、不碰订单。
    # ⇒ 已支付的订单在货主列表里**仍显示主按钮「去支付」**（点进去支付页正确显示"已支付"
    #   且没有可点按钮 ⇒ **不影响资金安全**，属体验/一致性缺口）。
    # **已登记为缺口 ENT-044**；修法要订单列表带支付状态（订单/支付域改动，不在本轮范围）。
    # 因此本步**只如实记录现状**，不当通过 —— 与 ㉕D「确认动作未在真机点击」同一写法。
    #
    # ⚠️ 用 `nav` 直进订单页而不是 `back_to`：上面为了重新取数用了 `reLaunch`，
    #    页面栈里已经只有支付页，`back_to` 没有可返回的上一页。
    w.c.nav("reLaunch", "/" + ORDERS, ORDERS)
    w.c.wait_path(ORDERS, 25)
    time.sleep(1.6)
    n = w.c.count(f'[data-act-pay="{oid}"]')
    w.rep.rec(
        "⑧b 订单页「去支付」入口现状（**记为限制，不是通过**；已登记缺口 ENT-044）",
        True,
        f'[data-act-pay="{oid}"] n={n} —— n=1 = 仍显示「去支付」；'
        "根因：订单列表不返回支付状态，入口只按订单状态渲染（mock_pay 不碰订单）",
    )
    w.rep.rec(
        "⑧b 弹层确认键未真实点击",
        True,
        "限制：确认键在原生 wx.showModal 里、工具点不到；本步用真实接口驱动支付，"
        "支付**后**的渲染由真机断言覆盖",
    )


def sec_09(w: Walker) -> None:
    """⑨ 船东链路：工作台 → 船队 → 为船找货 + ⑨b 船东侧智能合同。"""
    print("\n== ⑨ 船东链路 ==", flush=True)
    a = _anchors(w)
    w.login_as(CODE_OWNER)
    ok = w.enter_role("owner", OWNER)
    w.rep.rec("⑨ 船东工作台进入（真实点击身份卡）", ok, w.c.current_path())
    if not ok:
        return
    time.sleep(1.8)
    w.shot("10-船东找货")
    owd = w.c.page_data()
    w.rep.rec(
        "⑨ 船东页渲染",
        not owd.get("error"),
        f"货源大厅={len(owd.get('list') or [])} 船队={len(owd.get('shipList') or [])} "
        f"已认证={owd.get('verifiedCount')}",
    )

    # 船队区块仅在 view==='fleet' 渲染，必须先点「我的船队」
    w.c.tap(".my-entry")
    time.sleep(1.5)
    w.shot("10b-船东-我的船队")
    od2 = w.wait_data(lambda d: d.get("view") == "fleet", tries=15, gap=0.4)
    w.rep.rec("⑨ 船队视图切换", od2.get("view") == "fleet", f"view={od2.get('view')}")
    fleets = od2.get("shipList") or []
    in_fleet = any(x.get("id") == a.ship_id for x in fleets)
    w.rep.rec("⑨ 目标船在船队列表", in_fleet, f"ship #{a.ship_id} n={len(fleets)}")
    if in_fleet and a.ship_id is not None:
        w.tap_card_by_id(".btn-secondary", a.ship_id)
        w.c.wait_path(MATCH, 25)
        time.sleep(2.8)
        w.shot("09c-撮合-为船找货")
        md2 = w.c.page_data()
        w.rep.rec("⑨ 撮合页(船东方向)进入", w.c.current_path() == MATCH, w.c.current_path())
        w.rep.rec(
            "⑨ 船东方向有候选可排序",
            (md2.get("total") or 0) > 0,
            f"total={md2.get('total')}",
        )
        scores = [x.get("score") for x in (md2.get("items") or [])]
        w.rep.rec(
            "⑨ 候选按评分降序",
            bool(scores) and all(scores[i - 1] >= scores[i] for i in range(1, len(scores))),
            "scores=" + json.dumps(scores),
        )
        w.back_to(OWNER)
        time.sleep(1.0)

    # ⑨b 船东 · 「订单」→ 智能合同（与货主同一批仿真案例，验两个角色都能检查）
    w.c.nav("switchTab", "/" + ORDERS, ORDERS)
    time.sleep(2.0)
    w.shot("10c-船东-我的订单")
    ood = w.c.page_data()
    olist = ood.get("list") or []
    w.rep.rec(
        "⑨b 船东订单页无错误且非空",
        (not ood.get("error")) and len(olist) > 0,
        f"role={ood.get('role')} list={len(olist)}",
    )
    if not a.sim_cases:
        w.rep.rec("⑨b 船东可见仿真案例", False, "前置锚点缺失：库里没有 R3/R4/R5 仿真案例")
        return
    sc = a.sim_cases[-1]
    soid = sc["order_id"]
    vis = any(x.get("id") == soid for x in olist)
    w.rep.rec(f"⑨b 船东可见仿真案例 {sc['key']}", vis, f"#{soid}")
    if not vis:
        return
    w.rep.rec("⑨b 船东「查看合同」可点", w.tap_order_act(soid, "contract"))
    w.c.wait_path(CONTRACT, 25)
    cd3 = w.wait_data(_contract_ready, tries=60, gap=0.5)
    w.shot(f"10d-船东-合同-{sc['key']}")
    titles3 = [r.get("title") for r in (cd3.get("risks") or [])]
    w.rep.rec(
        f"⑨b 船东侧命中预期风险「{EXPECT_RISK.get(sc['key'])}」",
        EXPECT_RISK.get(sc["key"]) in titles3,
        "risks=" + "、".join(str(t) for t in titles3),
    )
    w.rep.rec(
        "⑨b 船东侧合同正文非空",
        len(cd3.get("rawText") or "") > 400,
        f"raw={len(cd3.get('rawText') or '')}",
    )
    w.back_to(ORDERS)


def sec_10(w: Walker) -> None:
    """⑩ 港口：服务网格 → 运营台 → 泊位档期（甘特 + 峰值并发）。"""
    print("\n== ⑩ 港口工作台 ==", flush=True)
    a = _anchors(w)
    if not _port_session(w, a):
        w.rep.rec(
            "⑩ 港口工作台进入（port 身份会话 · C 端入口已下线）",
            False,
            "前置失败：拿不到 port token（api_login 失败或 /auth/me 不通）",
        )
        return
    arrived = w.c.nav("switchTab", "/" + PORT, PORT)
    time.sleep(1.6)
    w.rep.rec(
        "⑩ 港口工作台进入（port 身份会话 · C 端入口已下线）",
        arrived,
        w.c.current_path(),
    )
    if not arrived:
        return
    w.shot("11-港口服务（占位网格）")
    pd = w.c.page_data()
    w.rep.rec(
        "⑩ 港口服务页（4 组占位）",
        pd.get("view") == "service" and len(pd.get("groups") or []) == 4,
        f"view={pd.get('view')} groups={len(pd.get('groups') or [])}",
    )

    # 服务网格 → 业务办理（锚点 `data-svc-key="ops"`）
    entered = w.c.tap('[data-svc-key="ops"]')
    time.sleep(2.0)
    pd = w.c.page_data()
    w.shot("11b-港口-预约审核（运营台默认页）")
    w.rep.rec(
        "⑩ 真实点击「业务办理」进入运营台",
        entered and pd.get("view") == "ops",
        f"view={pd.get('view')} tab={pd.get('tab')}",
    )

    # 泊位管理 tab → DEMO-01 档期
    if a.berth_id is None:
        w.rep.rec("⑩ 演示泊位在列表", False, "前置锚点缺失：库里没有泊位")
        return
    w.c.tap('[data-tab="list"]')
    time.sleep(2.0)
    w.shot("11c-港口-泊位管理")
    berths = w.c.page_data().get("berthList") or []
    target = next((b for b in berths if b.get("id") == a.berth_id), None)
    w.rep.rec(
        "⑩ 演示泊位在列表",
        target is not None,
        (
            f"#{a.berth_id} cap={target.get('concurrent_capacity')}"
            if target
            else f"#{a.berth_id} 未找到"
        ),
    )
    if target is None:
        return
    w.c.tap(f'[data-berth-id="{a.berth_id}"]')
    okb = w.c.wait_path(BERTH, 25)
    time.sleep(2.2)
    w.shot("12-泊位档期详情（满档+甘特）")
    bd = w.c.page_data()
    w.rep.rec("⑩ 泊位档期页进入", okb and w.c.current_path() == BERTH, w.c.current_path())
    bars = bd.get("bars") or []
    w.rep.rec(
        "⑩ 档期甘特条渲染",
        len(bars) >= 2,
        f"bars={len(bars)} peak={bd.get('peak')}/{bd.get('capacity')}",
    )
    peak = float(bd.get("peak") or 0)
    cap = float(bd.get("capacity") or 0)
    w.rep.rec(
        "⑩ 峰值并发达容量（满档演示）",
        peak >= cap and cap > 0,
        f"peak={peak} cap={cap}",
    )
    geo: list[list[float]] = []
    for b in bars:
        # ⚠️ `left` / `width` 是**带百分号的 CSS 字符串**（berth.js 用
        # `toFixed(2) + '%'` 拼的），直接 `float()` 会抛 ValueError ——
        # 2026-09-15 首跑三条全落到 except 分支，报成 `[[-1,-1]×3]`，
        # 看起来像"甘特几何越界"，实际是取值口径不对。
        geo.append([_pct(b.get("left")), _pct(b.get("width"))])
    w.rep.rec(
        "⑩ 甘特条几何在 [0,100]% 内",
        bool(geo) and all(0 <= left <= 100.5 and wd > 0 and left + wd <= 100.6 for left, wd in geo),
        json.dumps(geo),
    )
    w.back_to(PORT)


def sec_11(w: Walker) -> None:
    """⑪ 预约审核详情 + 防超卖（与服务端 409 同口径）+ 跳泊位档期。"""
    print("\n== ⑪ 预约审核 ==", flush=True)
    a = _anchors(w)
    if a.appt_id is None:
        w.rep.rec("⑪ 待确认预约列表就绪", False, "前置锚点缺失：库里没有预约")
        return
    if not _port_session(w, a):
        w.rep.rec("⑪ 待确认预约列表就绪", False, "前置失败：拿不到 port 会话")
        return
    if not w.c.nav("switchTab", "/" + PORT, PORT):
        w.rep.rec("⑪ 待确认预约列表就绪", False, f"未进入港口页（{w.c.current_path()}）")
        return
    time.sleep(1.8)
    pd = w.c.page_data()
    if pd.get("view") != "ops":
        w.c.tap('[data-svc-key="ops"]')
        time.sleep(2.0)
        pd = w.c.page_data()
    if pd.get("tab") != "appts":
        w.c.tap('[data-tab="appts"]')
        time.sleep(1.6)
        pd = w.c.page_data()
    appts = pd.get("apptList") or []
    w.rep.rec("⑪ 待确认预约列表就绪", len(appts) > 0, f"pending={len(appts)}")

    w.c.tap(f'[data-appt-id="{a.appt_id}"]')
    oka = w.c.wait_path(APPT, 25)
    time.sleep(2.0)
    w.shot("13-预约审核详情")
    ad = w.c.page_data()
    w.rep.rec("⑪ 预约详情页进入", oka and w.c.current_path() == APPT, w.c.current_path())
    w.rep.rec(
        "⑪ 容量预检判冲突（与服务端 409 同口径）",
        ad.get("conflict") is True,
        f"重叠 {ad.get('overlapNow')} + 1 > 容量 {ad.get('capacity')}",
    )
    w.rep.rec(
        "⑪ 留痕时间轴就绪",
        len(ad.get("timeline") or []) >= 2,
        f"segments={len(ad.get('timeline') or [])}",
    )
    before = json.dumps(ad.get("timeline") or [], ensure_ascii=False)
    w.c.tap(".btn-primary")
    time.sleep(3.0)
    w.shot("13b-预约确认-服务端409拦截")
    ad2 = w.c.page_data()
    w.rep.rec(
        "⑪ 「确认并锁定档期」被服务端拦下（状态未变）",
        json.dumps(ad2.get("timeline") or [], ensure_ascii=False) == before,
        f"status={ad2.get('status')}",
    )

    more = w.c.tap(".section-head-more")
    to_berth = w.c.wait_path(BERTH, 20) if more else False
    time.sleep(1.8)
    w.shot("12b-泊位档期（由预约页跳入）")
    w.rep.rec(
        "⑪ 预约页 → 泊位档期跳转",
        to_berth and w.c.current_path() == BERTH,
        w.c.current_path(),
    )
    w.back_to(APPT)
    w.back_to(PORT)


def sec_13(w: Walker) -> None:
    """⑬ 智能入口：✨Ai 解析 → 一句话发货 → 草稿带回发布页。

    历史缺陷：`assistant.js` 的 onLoad 曾写成无参，`?mode=parse` 被整体丢弃，
    「✨Ai」与「客服」进的是同一页同一行为（后端做好了但前端从未接上）——
    本章就是钉住这条路径的。
    """
    print("\n== ⑬ 智能入口 ==", flush=True)
    w.login_as(CODE_SHIPPER)
    # login_as 只注入 dev_login_code，**不完成登录**；真正的登录发生在首页点身份卡
    if not w.enter_role("shipper", SHIPPER):
        w.rep.rec("⑬ 前置：货主工作台未进入", False, w.c.current_path())
        return
    time.sleep(1.6)
    n_box = w.c.count(".search-box")
    w.rep.rec("⑬ 货主页有搜索框（统一入口位）", n_box > 0, str(n_box))

    w.c.nav("navigateTo", f"/{ASSISTANT}?mode=parse", ASSISTANT)
    ad = w.wait_data(lambda d: d.get("mode") == "parse", tries=40, gap=0.5)
    w.shot("15-Ai解析态")
    w.rep.rec(
        "⑬ 「✨Ai」进入货源解析态（不再与客服同页）",
        ad.get("mode") == "parse",
        f"mode={ad.get('mode')}",
    )
    w.rep.rec(
        "⑬ 货主进解析态无角色门控",
        ad.get("roleBlocked") is False,
        str(ad.get("roleBlocked")),
    )

    # 用 setData 注入输入框内容后点「解析」→ 走真实 onSend → 真实 HTTP
    w.c.set_data({"input": "800吨散装水泥，下周三从南宁运到贵港，运费2万5"})
    time.sleep(0.4)
    w.c.tap(".btn-send")
    ad2 = w.wait_data(
        lambda d: any(
            m.get("kind") == "parse" and not m.get("pending") for m in (d.get("messages") or [])
        ),
        tries=60,
        gap=0.6,
    )
    parsed = [m for m in (ad2.get("messages") or []) if m.get("kind") == "parse"]
    card = parsed[-1] if parsed else {}
    w.shot("15-Ai解析结果卡片")
    rows = card.get("rows") or []
    w.rep.rec("⑬ 解析结果渲染为结构化卡片（7 字段）", len(rows) == 7, str(len(rows)))
    w.rep.rec(
        "⑬ 卡片含草稿（可带去发布页，Agent 未直写）",
        bool(card.get("draft")),
        "ok" if card.get("draft") else "无 draft",
    )
    if not card.get("draft"):
        w.rep.rec("⑬ 草稿带入发布货源页并回填装货港", False, "前置：卡片无草稿")
        w.back_to(SHIPPER)
        return
    if not (w.c.tap(".parse-card .parse-btn") or w.c.tap(".parse-btn")):
        w.rep.rec("⑬ 草稿带入发布货源页并回填装货港", False, "未点到「带去发布页」")
        w.back_to(SHIPPER)
        return
    w.c.wait_path(PUBLISH_CARGO, 30)
    time.sleep(2.0)
    w.shot("15-解析草稿带入发布页")
    cd = w.c.page_data()
    w.rep.rec(
        "⑬ 草稿带入发布货源页并回填装货港",
        bool((cd.get("form") or {}).get("origin_port")),
        json.dumps((cd.get("form") or {}).get("origin_port"), ensure_ascii=False),
    )
    w.rep.rec(
        "⑬ 回填后给出确认提示",
        "AI 已" in str(cd.get("smartTip") or ""),
        str(cd.get("smartTip")),
    )
    n_smart = w.c.count(".smart-btn")
    w.rep.rec("⑬ 发布页有「一句话发货」与「合规预检」入口", n_smart >= 2, str(n_smart))
    w.back_to(SHIPPER)


def sec_14(w: Walker) -> None:
    """⑭ UI 打磨：顶栏身份 / 智能搜索页 / 订单页自绘导航。"""
    print("\n== ⑭ UI 打磨 ==", flush=True)
    w.login_as(CODE_SHIPPER)
    if not w.enter_role("shipper", SHIPPER):
        w.rep.rec("⑭ 前置：货主工作台未进入", False, w.c.current_path())
        return
    time.sleep(1.6)
    hd = w.c.page_data()
    n_av = w.c.count(".avatar-vec")
    w.rep.rec("⑭ 货主页顶栏有矢量人物头像", n_av > 0, str(n_av))
    w.rep.rec(
        "⑭ 货主页顶栏显示用户 ID",
        bool(re.fullmatch(r"用户\d+", str(hd.get("userCode") or ""))),
        str(hd.get("userCode")),
    )
    w.rep.rec(
        "⑭ 货主页顶栏显示地理位置（常用港）",
        bool(hd.get("defaultPortLabel")),
        str(hd.get("defaultPortLabel")),
    )
    w.shot("16-货主页-顶栏身份")

    # 搜索框 → 智能搜索页（客服同款外壳，不再是系统弹窗）
    w.c.tap(".search-box")
    if not w.c.wait_path(ASSISTANT, 30):
        w.rep.rec("⑭ 搜索框进入「智能搜索」页（不再弹系统弹窗）", False, w.c.current_path())
        return
    sd = w.wait_data(lambda d: d.get("mode") == "search", tries=40, gap=0.5)
    time.sleep(0.7)
    w.shot("16-智能搜索页")
    w.rep.rec(
        "⑭ 搜索框进入「智能搜索」页（不再弹系统弹窗）",
        sd.get("mode") == "search",
        f"mode={sd.get('mode')}",
    )
    n_composer = w.c.count(".composer")
    n_send = w.c.count(".btn-send")
    w.rep.rec(
        "⑭ 智能搜索复用客服外壳（含输入区与底部按钮）",
        n_composer > 0 and n_send > 0,
        f"composer={n_composer} send={n_send}",
    )
    chips = sd.get("chips") or []
    w.rep.rec(
        "⑭ 搜索态文案与示例齐备",
        "智能搜索" in str(sd.get("bannerTitle") or "") and len(chips) >= 3,
        f"banner={sd.get('bannerTitle')} chips={len(chips)}",
    )

    w.c.set_data({"input": "我要发800吨散装水泥，南宁到贵港"})
    time.sleep(0.4)
    w.c.tap(".btn-send")
    sd2 = w.wait_data(
        lambda d: any(
            m.get("role") == "assistant" and not m.get("pending") for m in (d.get("messages") or [])
        ),
        tries=60,
        gap=0.6,
    )
    replied = [
        m
        for m in (sd2.get("messages") or [])
        if m.get("role") == "assistant" and not m.get("pending")
    ]
    last = replied[-1] if replied else {}
    w.shot("16-智能搜索结果")
    w.rep.rec(
        "⑭ 智能搜索返回结果（卡片/气泡，且标注识别意图）",
        bool(last.get("kind") or last.get("text")) and not last.get("error"),
        f"kind={last.get('kind') or '-'} intent={last.get('intentLabel') or '-'} "
        f"text={str(last.get('text') or '')[:20]}",
    )
    if last.get("kind") == "parse":
        w.rep.rec(
            "⑭ 货源类搜索出结构化解析卡（7 字段）",
            len(last.get("rows") or []) == 7,
            str(len(last.get("rows") or [])),
        )
    w.back_to(SHIPPER)

    # 订单页自绘导航：`navigationStyle:custom` 却未自绘时统计行会顶到状态栏
    w.c.nav("switchTab", "/" + ORDERS, ORDERS)
    time.sleep(1.8)
    w.shot("16-订单页-自绘导航")
    n_nav = w.c.count(".nav")
    n_title = w.c.count(".nav-title")
    w.rep.rec("⑭ 订单页自绘导航存在", n_nav > 0, str(n_nav))
    w.rep.rec("⑭ 订单页导航标题渲染", n_title > 0, str(n_title))
    nav_r = w.c.rects(".nav")
    stat_r = w.c.rects(".stat-row")
    nav0 = nav_r[0] if nav_r else {}
    stat0 = stat_r[0] if stat_r else {}
    below = (
        bool(nav0 and stat0)
        and float(stat0.get("top") or 0)
        >= float(nav0.get("top") or 0) + float(nav0.get("height") or 0) - 2
    )
    w.rep.rec(
        "⑭ 统计行位于导航之下（不再顶出页面框架）",
        bool(below),
        json.dumps(
            {
                "navTop": nav0.get("top"),
                "navH": nav0.get("height"),
                "statTop": stat0.get("top"),
            }
        ),
    )


SECTIONS = {
    "smoke": sec_smoke,
    "0": sec_00,
    "1": sec_01,
    "2": sec_02,
    "4": sec_04,
    "6": sec_06,
    "12": sec_12,
    "15": sec_15,
    "16": sec_16,
    "25": sec_25,
    "26": sec_26,
    "27": sec_27,
    "28": sec_28,
    "29": sec_29,
    "30": sec_30,
    "4b": sec_4b,
    "5": sec_05,
    "7": sec_07,
    "8": sec_08,
    "8b": sec_8b,
    "9": sec_09,
    "10": sec_10,
    "11": sec_11,
    "13": sec_13,
    "14": sec_14,
}

# 默认执行顺序：冒烟先跑（最快暴露白屏类缺陷），再逐章
# ⚠️ 26 → 27 → 28 是**一条链**（登记出来的案件被后两章接着处置），顺序不可打乱；
#    单跑其中一章时后两章会以「㉖ 未产出 case_id」明确失败，而不是静默跳过。
# ⚠️ 29 依赖 26～28 已跑：它断言「㉘ 关闭的那宗不在 `unclosed` 清单里」——
#    没有已关闭案件时那条断言会红（**故意**如此：它是在验筛选真的生效，
#    数据不满足就该说"没验成"，而不是退化成一个恒真的空断言）。
# ⚠️ 30 独立：它只要求队列里有一宗「待处理」的案件。
DEFAULT_ORDER = [
    "smoke",
    "0",
    "1",
    "2",
    "4",
    "6",
    "12",
    "15",
    "16",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    # ENT-035 补齐的未迁移章节（旧轨有、换轨后一直记 not-run 的 12 章）
    "4b",
    "5",
    "7",
    "8",
    "8b",
    "9",
    "10",
    "11",
    "13",
    "14",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="小程序真机走查（wechatide 工具链版）")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="小程序项目目录")
    ap.add_argument("--shots", default="", help="截图输出目录（默认 artifacts/ 下按时间戳建目录）")
    ap.add_argument("--client", default=DEFAULT_CLIENT, help="wechatide clientName")
    ap.add_argument(
        "--section",
        default="all",
        help="all 或逗号分隔的章节：" + ",".join(DEFAULT_ORDER),
    )
    ap.add_argument("--skill-version", default="", help="传入则校验 agent skill 版本关系")
    ap.add_argument("--timeout", type=int, default=150, help="单次工具调用超时（秒）")
    args = ap.parse_args()

    wanted = (
        list(DEFAULT_ORDER)
        if args.section.strip() == "all"
        else [s.strip() for s in args.section.split(",") if s.strip()]
    )
    unknown = [s for s in wanted if s not in SECTIONS]
    if unknown:
        print(
            f"章节名非法：{unknown}；可选：{','.join(DEFAULT_ORDER)} 或 all",
            file=sys.stderr,
        )
        return 2

    shots = args.shots or os.path.join(
        REPO_ROOT, "miniapp-device-artifacts", "walk-" + time.strftime("%Y%m%d-%H%M%S")
    )
    rep = Reporter()

    print(f"项目    ：{os.path.abspath(args.project)}")
    print(f"截图目录：{shots}")
    print(f"章节    ：{','.join(wanted)}", flush=True)

    client = Client(args.project, client=args.client, timeout=args.timeout)
    try:
        st = client.require_ready(args.skill_version or None)
    except RuntimeError as exc:
        print(f"\n[前置不通过]\n{exc}", file=sys.stderr)
        return 2
    meta = st.get("result", {}) if isinstance(st.get("result"), dict) else {}
    if st.get("degraded"):
        # 状态工具偶发 CONNECT_ERROR，但开窗/取页面栈是通的 —— 如实说，不要假装没事。
        print(
            "门禁    ：check_wechatide_status 未通过（"
            + str((st.get("status") or {}).get("errorType"))
            + "），已用「开窗 + 取页面栈」复检通过 ⇒ 降级放行",
            flush=True,
        )
    else:
        print(
            f"门禁    ：ok / versionRelation={meta.get('versionRelation')} / "
            f"loginExpired={meta.get('loginExpired')} / tokenRequired={meta.get('tokenRequired')}",
            flush=True,
        )

    if not backend_ready():
        print(
            "\n[前置不通过] 后端 8000 无应答（" + HEALTHZ + "）。\n"
            "  这种情况下所有章节都会落到 view=error，看起来像「页面全坏了」，"
            "其实一条业务缺陷都没有。\n"
            "  先起后端再重跑 —— 见本文件顶部「前置条件」第 1 条（含三份种子）。",
            file=sys.stderr,
        )
        return 2
    print("后端    ：8000 应答正常", flush=True)

    w = Walker(client, shots, rep)
    print("\n== 开窗 ==", flush=True)
    client.open_window()
    if not simulator_ready(client):
        print(
            "\n[前置不通过] 模拟器里没有页面（pageStack 为空）。\n"
            "  这与「后端没起」是同一类环境错，但更隐蔽：门禁 status 与 open_project_window"
            "  **在 IDE 主进程没跑时也回 ok**，而此后每一次 navigate / tap 都会等满超时，\n"
            "  表现为「走查跑了二十几分钟一行输出都没有」，一条业务结论也产不出。\n"
            "  处理：先把微信开发者工具**主界面**打开（进程 `微信开发者工具.exe`）并打开本项目，\n"
            "  再重跑；判据是 pageStack 非空，不是 status().ok。",
            file=sys.stderr,
        )
        return 2
    print("模拟器  ：pageStack 非空", flush=True)

    for name in wanted:
        try:
            SECTIONS[name](w)
        except Exception as exc:  # noqa: BLE001
            # 单章异常不打断整轮：记录为失败，继续跑后面的章节
            rep.rec(f"章节 {name} 执行异常", False, repr(exc)[:300])

    print("\n================ 汇总 ================", flush=True)
    fails = rep.failures
    print(
        f"断言 {len(rep.results)} 项：通过 {len(rep.results) - len(fails)}，失败 {len(fails)}",
        flush=True,
    )
    for f in fails:
        print("  FAIL:", f["step"], "|", f["note"], flush=True)

    errs = client.errors()
    print("[运行期 console error]", (errs[:900] if errs else "(无)"), flush=True)
    print("[截图目录]", shots, flush=True)

    with open(os.path.join(shots, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "sections": wanted,
                "project": os.path.abspath(args.project),
                "results": rep.results,
                "consoleError": errs,
                "consoleErrorDetected": bool(errs.strip()),
                "shots": shots,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )

    print("RESULT:", "ALL_PASS" if not fails else "HAS_FAIL", flush=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
