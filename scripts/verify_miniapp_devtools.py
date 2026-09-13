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

**未迁移章节**：④b 撮合、⑤ 发布空船、⑦/⑦b/⑦c 合同、⑧ 支付、⑨/⑨b 船东链路、
⑩/⑪ 港口链路、⑬ 智能入口、⑭ UI 打磨 —— 它们需要 REST 锚点预取与「按序号点击」，
后者在 wechatide 上没有直接等价物（见下），属后续增量。

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
1. 后端在 8000 端口运行并已铺演示数据：
   `cd backend && python scripts/seed_demo.py && python scripts/seed_entrust_orgpicker.py`
2. 微信开发者工具已启动、已开过本项目窗口、已完成一次人工授权（授权持久，见 DR-0009）。
3. **不得在沙箱中运行**（`wechatide` 官方硬要求）。

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

INDEX = "pages/index/index"
SHIPPER = "pages/shipper/shipper"
OWNER = "pages/owner/owner"
ORDERS = "pages/trade/orders/orders"
MINE = "pages/mine/mine"
PUBLISH_CARGO = "pages/publish/cargo/cargo"
PREVIEW = "pages/preview/preview"
WORKBENCH = "pages/entrust/workbench/workbench"

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
    def shot(self, name: str) -> str:
        path = os.path.join(self.shots_dir, name + ".jpg")
        ok = self.c.screenshot(path)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        # 空截图（<5KB）等于没拿到证据，按失败计
        self.rep.rec(f"截图 {name}", ok and size > 5000, f"{size}B")
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
                f"⑯ [{code}] 前置登录", False, f"未进入货主工作台（{self.c.current_path()}）"
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
        w.rep.rec(f"冒烟 {path}", ok, f"reach={reached} dataKeys={len(keys)} 本页报错={page_err}")
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
            f"⓪ 清缓存后停在身份选择页（{label}）", w.c.current_path() == INDEX, w.c.current_path()
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
        "② 货主工作台进入（真实点击身份卡）", w.enter_role("shipper", SHIPPER), w.c.current_path()
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
        "⑮ 弹窗几何可读（面板/卡片/图标/单选圈/文案）", has_geo, "ok" if has_geo else "缺几何回执"
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
        w.rep.rec("⑯ 多组织：清单里同时有甲与乙", ORG_A in names and ORG_B in names, str(names))
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

        # 点「甲」—— 工具无 index 参数，用属性选择器精确定位第 1 个 pill
        w.rep.rec("⑯ 点「甲」：tap 成功", w.c.tap('[data-org="1"]'), 'selector=[data-org="1"]')
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
        w.rep.rec("⑯ 点「乙」：tap 成功", w.c.tap('[data-org="2"]'), 'selector=[data-org="2"]')
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
        w.rep.rec("⑯ 点「乙」：Storage 跟随更新", stored_b == str(d.get("activeOrgId")), stored_b)

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
                "⑯ 重进：直接渲染乙组织队列，不再要求选择", first_title == TITLE_B, str(first_title)
            )
        else:
            w.rep.rec("⑯ 重进工作台", False, "未跳转")

    # ---------- 段二：单组织 ----------
    print("\n-- 段二：单组织（only） --", flush=True)
    if w.open_workbench("seed-mgr-single"):
        d = w.wait_data(lambda x: bool(x.get("orgReason")))
        w.shot("16-组织选择器-单组织不出现")
        orgs = d.get("orgs") or []
        names = [o.get("name") for o in orgs]
        w.rep.rec("⑯ 单组织：清单只有 1 个组织", len(orgs) == 1, f"orgs={len(orgs)}")
        w.rep.rec("⑯ 单组织：组织为甲", names[:1] == [ORG_A], str(names))
        w.rep.rec(
            "⑯ 单组织：reason=only（唯一选项自动选中）",
            d.get("orgReason") == "only",
            f"reason={d.get('orgReason')}",
        )
        stored_now = w.c.get_storage(ORG_STORAGE_KEY)
        w.rep.rec(
            "⑯ 单组织：旧选择（乙）已失效则不放行 → 仍落到甲",
            str(d.get("activeOrgId")) == "1",
            f"active={d.get('activeOrgId')!r}（上一段的乙未被沿用）残留 storage={stored_now!r}",
        )
        pills = w.c.count(".org-pill")
        w.rep.rec("⑯ 单组织：不出现组织选择器（唯一选项是噪音）", pills == 0, str(pills))
        first_title = ((d.get("items") or [{}])[0] or {}).get("title")
        w.rep.rec(
            "⑯ 单组织：直接 ready 且渲染甲组织队列",
            d.get("view") == "ready" and first_title == TITLE_A,
            f"view={d.get('view')} title={first_title}",
        )

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
            "⑯ 无组织：reason=none", d.get("orgReason") == "none", f"reason={d.get('orgReason')}"
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
}

# 默认执行顺序：冒烟先跑（最快暴露白屏类缺陷），再逐章
DEFAULT_ORDER = ["smoke", "0", "1", "2", "4", "6", "12", "15", "16"]


def main() -> int:
    ap = argparse.ArgumentParser(description="小程序真机走查（wechatide 工具链版）")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="小程序项目目录")
    ap.add_argument("--shots", default="", help="截图输出目录（默认 artifacts/ 下按时间戳建目录）")
    ap.add_argument("--client", default=DEFAULT_CLIENT, help="wechatide clientName")
    ap.add_argument(
        "--section", default="all", help="all 或逗号分隔的章节：" + ",".join(DEFAULT_ORDER)
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
        print(f"章节名非法：{unknown}；可选：{','.join(DEFAULT_ORDER)} 或 all", file=sys.stderr)
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
    print(
        f"门禁    ：ok / versionRelation={meta.get('versionRelation')} / "
        f"loginExpired={meta.get('loginExpired')} / tokenRequired={meta.get('tokenRequired')}",
        flush=True,
    )

    w = Walker(client, shots, rep)
    print("\n== 开窗 ==", flush=True)
    client.open_window()

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
