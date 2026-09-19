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
| `26` | 登记异常 / 变更案件（界面登记 → 案件详情入口） | —（新增章节） |
| `27` | 记录决定（`open → in_review`，含必填项拦截负例） | —（新增章节） |
| `28` | 关闭案件（钉一条状态机事实 + 合法路径 `in_review → rejected → closed`） | —（新增章节） |
| `29` | UI-04 组织级队列（队列切换 / 两行筛选 / 点行进详情） | —（新增章节） |
| `30` | 「批准」完整正例（依据版本 id 从界面取到 → 填对能过） | —（新增章节） |
| `31` | 案件页「应用变更」正例（**真写**造 `approved` 形态 → 应用 → 复核传播） | —（新增章节） |
| `32` | 成果页「待复核」徽标（依赖 ㉛ 应用后留下的复核项） | —（新增章节） |
| `33` | **页面栈深度运行期实测**（DR-0011：实测最深链 + 平台硬限对账） | —（新增章节） |
| `34` | UI-07 客户委托草稿 / 提交屏（S1 出口判据前置：全链真实点击 + 在途载荷续接） | —（新增章节） |
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
* ~~⑬/⑭ 的「7 字段解析卡」依赖真实 LLM 解析结果，mock 与真模型的字段数可能不同~~
  —— **该表述的前提不成立，已核实并改写**（2026-09-15）：
  ① `assistant.js` 的 `buildRows()` 是对前端 `FIELD_LABELS` 的**全量投影**
  （7 个 key 一律出行、未识别的行标「未识别」）⇒ **行数与 provider 无关**，
  `len(rows) == 7` 钉的是**界面契约**而不是模型输出；
  ② `agent/llm.py` 在 `LLM_MOCK` 或**没有 API Key** 时**显式降级**到 mock 规则模板，
  而本机**既无 `.env` 也无 `LLM_API_KEY`** ⇒ 本机**不存在**「跑一次真解析」这条通道
  （不是"没跑"，是环境里没有）。
  真正随模型变化的是**有几行落到「未识别」**（`needs_review` 命中数），
  已据此把 ⑬ 的断言补成「7 行 + 每行带 label/value/missing + 至少一行被识别」。
* ~~真机页面栈深度（DR-0011 的 `STACK_BUDGET = 8`）仍未做运行期验证。~~
  ⇒ ✅ **已由本脚本第 ㉝ 章（`--section 33`）实测闭环（2026-09-15/16）**：
  实测最深链 **4 层**（与声明链深一致）、**平台硬限实测 = 第 10 层被拒**
  （与源码 `MAX_STACK = 10` 对账一致）、预算 8 < 硬限 10。
  仍走不到的那一支（「栈到预算时改走 fallback」）在㉝ 章如实记 `LIMITATION`，不因本行而消失。

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
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation

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
#: 工作台那张样本单（委托 #1）所在的组织 —— `seed_entrust_demo` 的演示组织。
#: 它与甲/乙的**关键差别在授权行**：它是**完整 6 项**（含 `entrust:task:dispatch`），
#: 而甲只拿到 `["entrust:view", "entrust:assignment:claim"]`。㊳⑧ 的「提交成功」路径
#: 必须落在它上面（理由见该节注释）。
ORG_WORKBENCH = "演示经营主体·工作台"
#: ㊴ 章**自己造**的载体单标题。甲组织在种子里只有**一张** `submitted` 样本单
#: （`TITLE_A`），而它已被 ㊳ 章受理掉 ⇒「被抢认领」要另找一张。不新造**种子**
#: （不必动 `seed_entrust_orgpicker.py`），只在本章运行时经 API 建一张。
TITLE_RACE_DETAIL = "走查·详情页被抢认领（本章 API 建单）"
#: ㊶ 章**自己造**的载体单标题（S1 工作项 5：客户侧「我的委托」列表）。
#: 用 API 建单的理由见 `sec_41` 的 docstring：本章断言的对象是**列表页**与
#: **重载之后**，不是建单表单；而经界面建单会与 ㉞ 章的"在途草稿"状态互相消耗
#: （㉞ 会在 `seed-shipper` 名下留草稿/已提交单），两章就不再各自独立成立。
#: 标题取唯一值：列表里同时有种子单与本轮新单，靠标题才能把它们分开。
TITLE_MINE_LIST = "走查·我的委托列表可见性（㊶ 章 API 建单）"
#: ㊶ 章的第二张载体单：**草稿**（`org_id` 为空）。它承载「承接组织未知」这一格 ——
#: 只有真的有这么一张单，才能证界面上的「尚未委托组织」不是一句编出来的文案。
TITLE_MINE_DRAFT = "走查·我的委托·草稿（㊶ 章 API 建单，无承接组织）"
TITLE_PROBE_PAGE = "走查·触底分页探针（㊷ 章 API 建单）"
#: ㊷ 章分页探针：把该身份的单据数抬到**超过一页**，第 2 页才有观测对象。
PROBE_PAGE_TARGET = 25

#: ㊸ 主演示第 1–3 步（合同 §10.1）的输入。
#: 口径来自 canonical 夹具（`backend/scripts/fixtures/demo1_canonical.json`：
#: 钢材、南宁 → 贵港、800 吨）。⚠️ 这里的货名/货量是**演示者会在受理屏填什么**，
#: 走查由真实点击把标题填进去，不靠 API 造单 —— 因为合同第 1 步要的就是
#: "客户提交一张新委托"这个**界面动作**。
A1_MAIN_TITLE = "走查·主演示第1-3步（钢材800吨）"
CANON_CARGO = "钢材"
CANON_QTY = "800"
#: 页面 `assignments.js` 里 `PAGE_SIZE` 的探针侧副本。
#: ⚠️ 两处不一致时本章会在「条目数」那一格红 —— 这是故意的：页长改了而探针没跟，
#:    走查必须暴露出来，而不是继续按旧页长断言、把真实偏差留给用户发现。
MINE_PAGE_SIZE = 20
#: `pageScrollTo` 的落点：取一个必然溢出的值，交给原生 clamp 到最底。
#: （算真实页高要在设备侧量，而这里要的只是「到底」。）
MINE_SCROLL_BOTTOM = 99999

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


#: console 错误的**发生阶段**。白名单豁免只对 `OPEN` 生效（HO D1：白名单绑定阶段）。
CONSOLE_PHASE_OPEN = "open"
CONSOLE_PHASE_BIZ = "biz"

#: 运行期 console 里**已定位并给出证据的「开窗 / 连接阶段」噪声**白名单。
#:
#: HO 裁决（2026-09-15，D1 及其说明）对这张表提了三条硬要求：
#:   ① **白名单必须绑定发生阶段** —— 「只因错误文字包含某串就永远当开窗噪声」范围过宽；
#:      同样的文字若在**业务导航**中出现，必须重新判断；
#:   ② **新增噪声豁免必须有证据**，不得因为影响通过率而扩大白名单；
#:   ③ **未豁免的 error 阻止整轮判为通过**。
#: 因此每一项都带 `phase`（只在哪个阶段豁免）与 `evidence`（可复核的出处），
#: 而不是一个裸字符串集合 —— 没有证据就只能新增不进来。
ENV_NOISE_WHITELIST: tuple[dict[str, str], ...] = (
    {
        "pattern": "appLaunch with non-empty page stack",
        "phase": CONSOLE_PHASE_OPEN,
        "evidence": (
            "实验脚本 _probe_applaunch.py（2026-09-15）：① 闸门通过后、**发出任何导航之前**"
            "console 里就已有该条；② 其后连做两次导航，console 内容逐字不变（无新增条目）；"
            "③ 页面启动路径无 reLaunch（app.js.onLaunch 只探活，index.js 仅用户动作后 switchTab）。"
            "归为工具/框架侧竞态。见 DR-0009 §8.5⑦"
        ),
    },
    {
        "pattern": "routeDone with a webviewId",
        "phase": CONSOLE_PHASE_OPEN,
        "evidence": (
            "同上实验 ①②：WebView 建立期 routeDone 找不到 webview。"
            "⚠️ 业务阶段出现**同样文字也不豁免** —— 整族 `[Page route 错误(system error)]` 里"
            "包含真缺陷（例如 navigateTo 到不存在的页），按文字永久豁免会把真缺陷一起藏掉"
            "（HO D1 明确点名这个范围过宽）"
        ),
    },
)


def split_console_entries(text: str) -> list[str]:
    """把 console 原文切成**条目**列表。

    条目以 `["[error]"` 开头且**自身含换行**（带堆栈），因此按「下一个条目的行首」切；
    按行切会把一条错误拆成十几条，两个计数都失真。
    """
    if not text or text.strip() in ("", "(无)"):
        return []
    return [x.strip() for x in re.split(r'\n(?=\["\[)', text) if x.strip()]


def classify_console(text: str, phase: str) -> tuple[list[str], list[str]]:
    """把 console 文本按**发生阶段**切成 `(已豁免噪声, 未豁免)`。

    phase 取 `CONSOLE_PHASE_OPEN`（开窗阶段）/ `CONSOLE_PHASE_BIZ`（业务阶段）。
    **业务阶段一律不豁免**，哪怕文字与白名单逐字相同 —— 这是 HO D1 的明确要求
    （见 `ENV_NOISE_WHITELIST` 的说明）。
    """
    waived: list[str] = []
    unwaived: list[str] = []
    for chunk in split_console_entries(text):
        hit = any(w["pattern"] in chunk and w["phase"] == phase for w in ENV_NOISE_WHITELIST)
        (waived if hit else unwaived).append(chunk)
    return waived, unwaived


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


def api_status(path: str, token: str) -> int:
    """只取 **HTTP 状态码**的读探测（专供「期望被拒绝」的负例）。

    为什么不复用 `api_get`：它把一切非 200 折成 `None`，调用方就再也分不清
    「404 不可见」「403 无权限」「500 炸了」—— 而对负例来说**状态码本身就是
    被测事实**，折成 `None` 等于把结论丢掉，只剩一句「没通过」。

    也**刻意不重试**：重试会让一次真实的 404 与一次瞬时网络故障在日志里长得
    一模一样，而这两件事的处置完全不同（前者是设计，后者要排查）。

    返回 `0` 表示请求根本没发出去（网络层异常），与「发出去了但被拒」不同 ——
    别把 `0` 当成拒绝。

    ⚠️ **必须单独捕 `HTTPError`**（2026-09-16 实测踩到）：`_http` 走的是
    `_OPENER.open()`，而 `urlopen` 对 **4xx / 5xx 是抛异常、不是返回响应** ——
    状态码挂在 `exc.code` 上。只写 `except Exception: return 0` 的话，一个真实的
    404 会被记成「请求未发出」，负例于是**永远失败**（首跑就撞上：`HTTP=0`）。
    这也顺带解释了 `api_get` 为什么分不清 404 与 500 —— 它把这个异常吞成了 `None`。
    """
    req = urllib.request.Request(
        API_BASE + path, headers={"Authorization": "Bearer " + token}, method="GET"
    )
    try:
        status, _ = _http(req)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:  # noqa: BLE001
        return 0
    return int(status)


def api_post(
    path: str,
    token: str,
    payload: dict,
    idem_key: str | None = None,
) -> tuple[int, dict | None]:
    """写接口。

    ⚠️ **委托的写端点强制要求 `Idempotency-Key` 请求头**（ENT-002 幂等服务）——
    不带会被后端以 **400** 拒掉，回执是
    `{"detail":"写操作必须提供 Idempotency-Key 请求头"}`。
    实测（2026-09-15）：`/entrust/...` 的写命令都要；而 `/auth/switch-role`
    （`ensure_role` 用）不要，所以这个参数**可选**。
    键要**每次唯一**：同一个键重放会命中幂等记录、返回**首次的响应**，而不是再写一次。
    """
    body = json.dumps(payload or {}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    req = urllib.request.Request(
        API_BASE + path,
        data=body,
        headers=headers,
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


# ── 路径② 锚定模式：`--anchor <aid>`（S4-b 切片 §7.1）────────────────────────
#: runner 把 `--anchor <aid>` 翻成 `WALK_ANCHOR`：本轮**各章优先用同一张委托**，
#: 用来把第 2–9 步的界面动作串在一张单上跑（此前各章各挑一张，链就走不起来）。
#: 默认空 ⇒ `prefer_anchor()` 原样放行，行为与从前**逐字一致**。
WALK_ANCHOR = (os.environ.get("WALK_ANCHOR") or "").strip()


def prefer_anchor(picked: str) -> str:
    """给了 `--anchor` 就用它；否则原样返回各章自己挑到的那张。"""
    return WALK_ANCHOR or picked


def find_submitted(org_id: str, title: str, token: str) -> str:
    """在该组织队列里找「标题匹配**且状态为 submitted**」的那张单（找不到给空串）。

    ⚠️ 必须**同时**校验状态：种子里可能已躺着同标题的 `claimed` 单（上一次走查
    受理过的），只按标题命中就会拿一张不可认领的单去断言"有没有受理入口" ——
    那时"没有按钮"虽然是对的，结论却是错的（它因为**状态**不显示，不是因为权限）。
    """
    data = api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", token) or {}
    for row in data.get("items") or []:
        item = row or {}
        if str(item.get("title") or "") == title and item.get("status") == "submitted":
            return str(item.get("assignment_id") or "")
    return ""


def flip_org_role(code: str, org: str, role: str) -> tuple[int, str]:
    """把演示身份在某组织的成员角色改掉（走查专用的**撤权 / 复权**通道）。

    `ent_org_member` **没有 HTTP 接口** ⇒ D-4 §5 的「权限被撤销」只能在**运行中**改库，
    用同族的本机数据工具 `backend/scripts/flip_org_role.py`（见其模块 docstring）。

    ⚠️ 解释器要挑对：那个脚本依赖 sqlalchemy / `app.core.database`，只有仓库 `.venv` 里装了。
    优先用 `.venv/Scripts/python.exe`，没有才退回当前解释器 —— 并且**把失败如实带回来**
    （rc / stderr），不在这里吞掉：改角色失败而后面继续断言，会得到"看起来是权限生效了"的
    假绿（其实是原角色没动、按钮本来就在）。
    """
    venv_py = os.path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    py = venv_py if os.path.isfile(venv_py) else sys.executable
    try:
        proc = subprocess.run(
            [
                py,
                os.path.join("scripts", "flip_org_role.py"),
                "--code",
                code,
                "--org",
                org,
                "--role",
                role,
            ],
            cwd=os.path.join(REPO_ROOT, "backend"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # 解释器/脚本缺失也算失败
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


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
                demo = next(
                    (b for b in berths if "DEMO-01" in str(b.get("berth_no") or "")),
                    None,
                )
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


def simulator_ready(client: Client, tries: int = 6, gap: float = 2.0, probe_s: int = 45) -> bool:
    """模拟器里是否真的有页面（`pageStack` 非空）。

    为什么必须有这一句（2026-09-15 实测）：`check_wechatide_status` 与
    `open_project_window` 在 **IDE 主进程没启动** 时照样回 `ok`（后者回
    `type: "newopen"`），但 `pageStack` 恒为 `[]` —— 此后每次 `navigate` / `tap`
    都等满 150s 超时再重试，整轮走查**零输出地空转二十几分钟**。

    和 `backend_ready()` 同一条取向：**环境错要报成环境错**，不能让它在断言里
    伪装成"页面全坏了"。

    ⚠️ `probe_s` 必须**收窄**（默认 45s，别用客户端的 150s）：本函数按**次数**轮询，
    一次探测吃满默认超时 ⇒ 最坏 `6 × 150s = 15 分钟` 才给结论；而且**耗时没进日志**
    ⇒ 看起来像"卡死"。2026-09-15 在运行器闸门上实测到同一症状：18 次探测烧掉 56 分钟、
    页面栈全程为空，日志里只有一行行 `pageStack=[]`，**完全看不出慢在哪**。
    所以这里把每次探测的**真实耗时**也打出来。
    """
    t0 = time.time()
    for _ in range(tries):
        t1 = time.time()
        stack = client.page_stack(timeout=probe_s)
        t2 = time.time()
        print(
            f"    [{t2 - t0:6.1f}s] pageStack={str(stack)[:110]}  (探测 {t2 - t1:5.1f}s)",
            flush=True,
        )
        if stack:
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
INTAKE = "pages/entrust/intake/intake"
ARTIFACT = "pages/entrust/artifact/artifact"
DETAIL = "pages/entrust/detail/detail"
CASE = "pages/entrust/case/case"
CASE_CREATE = "pages/entrust/case-create/case-create"
SESSION = "pages/entrust/session/session"
#: S1 工作项 5：货主侧「我的委托」列表（真实状态 + 承接组织）。
MINE_LIST = "pages/entrust/assignments/assignments"

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
    """收集断言结果，同时逐条打印，便于边跑边看。

    ## 结果分档（HO 2026-09-15 裁决 D1 及其「立即修正验收报告」）

    | 档 | 含义 | 计入通过 |
    |---|---|---|
    | `PASS` | 所选必需检查完成，无未豁免错误 | ✅ |
    | `FAIL` | 已发现断言失败或产品错误 | ❌ |
    | `ENV_BLOCKED` | 环境故障导致验证无法完成 | ❌ |
    | `REVIEW_REQUIRED` | 出现尚未归因的 console error | ❌ |
    | `NOT_RUN` | 检查未执行 | ❌ |
    | `LIMITATION` | **`NOT_RUN` 的显式子类**：能跑，但工具不可验证（如原生弹层确认键） | ❌ |

    ⚠️ 后四档**一律不计入通过数** —— 这是 HO 明确点名的修正：
    此前 `rec(step, True, "not-run：…")` / `rec(step, True, "限制：…")` 把
    「未执行」和「工具不可验证」写成了通过，最终汇总里与真通过**长得一模一样**。
    ⇒ 这类记录必须走 `not_run()` / `limitation()`，不能再用 `rec(..., True, ...)`。
    """

    PASS = "PASS"
    FAIL = "FAIL"
    ENV_BLOCKED = "ENV_BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_RUN = "NOT_RUN"
    LIMITATION = "LIMITATION"

    #: 不计入通过的档位（HO：not-run、限制说明、环境阻塞都不算通过）。
    NON_PASS = (FAIL, ENV_BLOCKED, REVIEW_REQUIRED, NOT_RUN, LIMITATION)
    #: 汇报给 HO 的五档归并 —— `LIMITATION` 归入 `NOT_RUN`（都是「该项未取得通过证据」）。
    ROLLUP = {
        PASS: PASS,
        FAIL: FAIL,
        ENV_BLOCKED: ENV_BLOCKED,
        REVIEW_REQUIRED: REVIEW_REQUIRED,
        NOT_RUN: NOT_RUN,
        LIMITATION: NOT_RUN,
    }
    #: 整体判定的优先级（越靠前越严重）。
    VERDICT_ORDER = (FAIL, ENV_BLOCKED, REVIEW_REQUIRED, NOT_RUN)

    def __init__(self) -> None:
        self.results: list[dict] = []

    def outcome(self, kind: str, step: str, note: str = "") -> bool:
        """记一条结果，返回 `kind == PASS`。

        新代码请优先用 `not_run()` / `limitation()` / `env_blocked()` /
        `review_required()` 这几个语义化入口，而不是裸传 `kind`。
        """
        if kind not in (self.PASS, *self.NON_PASS):
            raise ValueError(f"未知结果档位：{kind}")
        self.results.append(
            {"step": step, "ok": kind == self.PASS, "kind": kind, "note": str(note)}
        )
        tail = f" | {note}" if note else ""
        print(f"{kind} | {step}{tail}", flush=True)
        return kind == self.PASS

    def rec(self, step: str, ok: bool, note: str = "") -> bool:
        """断言记录（保留原语义）：`ok=True` → `PASS`，`ok=False` → `FAIL`。

        ⚠️ **不要**再用它记「未执行」或「限制」——那是把非通过写成通过的经典错法。
        """
        return self.outcome(self.PASS if ok else self.FAIL, step, note)

    def not_run(self, step: str, note: str = "") -> bool:
        """检查未执行（含需开关而未开、前置缺失）。**不计入通过。**"""
        return self.outcome(self.NOT_RUN, step, note)

    def limitation(self, step: str, note: str = "") -> bool:
        """能跑但**工具不可验证**（原生弹层之类）。**不计入通过。**"""
        return self.outcome(self.LIMITATION, step, note)

    def env_blocked(self, step: str, note: str = "") -> bool:
        """环境故障导致验证无法完成。**不计入通过。**"""
        return self.outcome(self.ENV_BLOCKED, step, note)

    def review_required(self, step: str, note: str = "") -> bool:
        """出现尚未归因的 console error。**不计入通过。**"""
        return self.outcome(self.REVIEW_REQUIRED, step, note)

    @property
    def failures(self) -> list[dict]:
        return [r for r in self.results if r["kind"] == self.FAIL]

    @property
    def non_pass(self) -> list[dict]:
        """所有未取得通过证据的条目（含 FAIL / 环境阻塞 / 待归因 / 未执行 / 限制）。"""
        return [r for r in self.results if r["kind"] != self.PASS]

    def tally(self) -> dict[str, int]:
        counts = dict.fromkeys((self.PASS, *self.NON_PASS), 0)
        for r in self.results:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        return counts

    def rollup(self) -> dict[str, int]:
        """按 HO 的五档归并计数（`LIMITATION` 并入 `NOT_RUN`）。"""
        out: dict[str, int] = {}
        for kind, n in self.tally().items():
            key = self.ROLLUP[kind]
            out[key] = out.get(key, 0) + n
        return out

    def verdict(self) -> str:
        """整体判定：有 FAIL 就是 FAIL，否则按严重度取第一个出现的非通过档。"""
        roll = self.rollup()
        for kind in self.VERDICT_ORDER:
            if roll.get(kind):
                return kind
        return self.PASS

    def print_summary(self) -> None:
        t = self.tally()
        roll = self.rollup()
        total = len(self.results)
        print(
            f"断言 {total} 项：**通过 {t[self.PASS]}** / 未取得通过证据 {total - t[self.PASS]}",
            flush=True,
        )
        print(
            "  分档："
            + " | ".join(
                f"{k}={t[k]}"
                for k in (
                    self.PASS,
                    self.FAIL,
                    self.ENV_BLOCKED,
                    self.REVIEW_REQUIRED,
                    self.NOT_RUN,
                    self.LIMITATION,
                )
            ),
            flush=True,
        )
        print(
            "  归并（HO 五档）："
            + " | ".join(
                f"{k}={roll.get(k, 0)}"
                for k in (
                    self.PASS,
                    self.FAIL,
                    self.ENV_BLOCKED,
                    self.REVIEW_REQUIRED,
                    self.NOT_RUN,
                )
            ),
            flush=True,
        )
        for r in self.non_pass:
            print(f"  {r['kind']}: {r['step']} | {r['note']}", flush=True)


def _errors_safe(client: Client) -> str | None:
    """采集运行期 console 错误；**采不到就返回 `None`，不返回空串**。

    HO D1 明确要求：**日志无法采集也不能当作「console 无错误」**。
    返回空串会让「采不到」与「确实没有」在后续判断里同形 —— 这正是本项目
    反复踩的「不把 not-run 记成通过」的镜像。
    """
    try:
        return client.errors()
    except Exception as exc:  # noqa: BLE001
        print(f"    [console 采集失败] {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        return None


def _console_delta(cur: str | None, prev: str | None) -> str:
    """取 `cur` 相对 `prev` 的增量（IDE 的 console 是**累计**日志）。"""
    if cur is None:
        return ""
    if prev and cur.startswith(prev):
        return cur[len(prev) :]
    return cur


def _previous_runs(shots: str, wanted: list[str]) -> list[dict]:
    """同章节组合的历史运行摘要（HO D1：允许恢复后重跑，但保留首次结果与重跑关联）。

    只读同目录下兄弟 `summary.json`；**不修改任何历史产物**。
    """
    root = os.path.dirname(os.path.abspath(shots))
    out: list[dict] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        f = os.path.join(d, "summary.json")
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                j = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        if list(j.get("sections") or []) != list(wanted):
            continue
        out.append(
            {
                "shots": d,
                "finishedAt": j.get("finishedAt"),
                "argv": j.get("argv"),
                "verdict": j.get("verdict")
                or ("ALL_PASS" if not j.get("failures") else "HAS_FAIL"),
                "passed": j.get("passed"),
                "notPassed": j.get("notPassed"),
            }
        )
    return out


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

    def login_as(self, code: str, tries: int = 20) -> str:
        """写 `dev_login_code` + 清 token → reLaunch 首页（不点击身份卡）。

        ⚠️ **必须轮询，不能固定 sleep**：`reLaunch` 是异步的，页面栈要等小程序
        把目标页创建出来才会变。模拟器**冷启动**时会输 —— 2026-09-18 实测：
        开窗阶段探测了 38.5s，紧随其后的 `reLaunch` 在 1.5s 后读到的仍是
        `pages/shipper/shipper`，于是 ㊸ 的前置被记成 `FAIL`（"未停在身份页"），
        而实情只是**还没跳过去**。这类假红最贵：它看起来像"登录坏了"。

        未命中时**再补发一次** `reLaunch`：冷启动下第一条导航指令可能落在
        页面创建之前而被丢掉。返回最终读到的路径，调用方照旧比对 `INDEX`。
        """
        self.c.set_storage("dev_login_code", code)
        self.c.remove_storage("access_token")
        self.c.remove_storage("user_info")
        path = ""
        for _attempt in range(2):
            self.c.navigate("reLaunch", "/" + INDEX)
            for _ in range(max(1, tries // 2)):
                path = self.c.current_path()
                if path == INDEX:
                    return path
                time.sleep(0.5)
        return path

    def enter_role(self, role: str, want: str, tries: int = 30) -> bool:
        """按身份卡进入工作台。

        旧脚本用 `tapAt(page, '.role-card', 0|1)` 按序号点；工具无 index 参数，
        但身份卡带 `data-role` → 用属性选择器精确命中（等价且更明确）。

        ⚠️ **先等身份卡真的在渲染树上，再点**（2026-09-18 O-8 定向验证的结论）。
        `reLaunch` 是异步的：`current_path()` 已经报 `pages/index/index`，
        而**渲染树里那张卡可能还没建出来**，此时 `tap` 是**静默落空**的 ——
        接着就是等 `want` 等到超时，读数写成
        `未进入货主工作台（pages/index/index）`，看起来像"权限/入口坏了"。
        同一次运行里换个配方又全绿，说明这是**就绪读数**问题，不是业务缺陷。
        ⇒ 与 `login_as` 里"未命中就补发一次 `reLaunch`"是同一手法：
        把"没就绪"变成**可重试的条件**，而不是加固定等待（加 sleep 只会掩盖它）。
        """
        for _attempt in range(2):
            for _ in range(20):
                if self.c.count(f'[data-role="{role}"]') > 0:
                    break
                time.sleep(0.5)
            self.c.tap(f'[data-role="{role}"]')
            if self.c.wait_path(want, tries):
                return True
        return self.c.current_path() == want

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

    def wait_mine_ready(
        self, tries: int = 40, gap: float = 0.5
    ) -> tuple[str, dict, list[str], bool]:
        """等「我的」页**就绪**：返回 `(path, page_data, 键名集合, 是否读到过 showEntrust)`。

        为什么返回值这么啰嗦：这条前置在 O-8 那一族里**反复以 `showEntrust=None`
        出现在读数里**，而 `None` 同时对应两种完全不同的情形 ——

        1. 我们**根本还没停在「我的」页**（`switchTab` 没落地、页面没建出来）；
        2. 页面在了、数据也取回来了，但它**没有产出 `showEntrust` 这个键**
           （那是产品问题，不是"没等到"）。

        只报一个 `None` 会把二者糊成一句，于是每次都要重新猜一遍。所以这里把
        `path` 与**页面数据的键名集合**一起带出来 —— 读到哪个集合，一眼能分辨。
        ⛔ 判据不放松：调用方仍按 `is True` 判定"入口可见"，`None` 与 `False` 都算不通过
        （差别只在于读数说清了是哪一种）。
        """
        path = ""
        data: dict = {}
        saw_key = False
        for _ in range(tries):
            path = self.c.current_path()
            data = self.c.page_data() or {}
            if "showEntrust" in data:
                saw_key = True
                if data.get("showEntrust") is not None:
                    break
            time.sleep(gap)
        return path, data, sorted(data.keys()), saw_key

    def open_workbench(self, code: str, tag: str = "⑯") -> bool:
        """登录指定身份 → 「我的」页委托入口 → 经理工作台（⑯ 章前置）。

        `tag` 只改断言记录的**前缀**：㉟ 章复用这段前置时传 `tag="㉟"`，
        免得 ㉟ 的结论被记成 ⑯ 的行（章级统计按前缀分）。
        """
        path = self.login_as(code)
        if path != INDEX:
            self.rep.rec(f"{tag} [{code}] 前置登录", False, f"未停在身份选择页（{path}）")
            return False
        if not self.enter_role("shipper", SHIPPER):
            self.rep.rec(
                f"{tag} [{code}] 前置登录",
                False,
                f"未进入货主工作台（{self.c.current_path()}）",
            )
            return False
        self.c.nav("switchTab", "/" + MINE, MINE)
        time.sleep(2.5)
        # ⚠️ 轮询到 `showEntrust` **有值**为止，不靠固定 sleep。
        #    2026-09-16 实测踩到：合并轮（`--section 36,37,38,39`）里 ㊱ 章**首跑**拿到
        #    `showEntrust=None` ⇒ 记 1 条 `FAIL` 并把该节后面的对照全推成 `NOT_RUN`；
        #    而**同一次运行**里 ㊳ 章对**同一个身份** `seed-mgr-multi` 的同一条前置是 PASS ⇒
        #    这是「页面还没就绪」的**读数**问题，不是权限功能缺陷。
        #    ⚠️ 轮询只让读数可靠，**不放松判据**：下面照样断言 `is True`，
        #    `None` 与 `False` 都会失败（差别只是"没读到"与"读到了但没有"）。
        mine: dict = {}
        path_mine, mine, keys_mine, saw_key = self.wait_mine_ready()
        # 入口是否可见由服务端决定 —— 这也是在验「我的」页的权限投影
        if not self.rep.rec(
            f"{tag} [{code}] 「我的」页委托入口可见（服务端放行）",
            mine.get("showEntrust") is True,
            f"showEntrust={mine.get('showEntrust')!r} path={path_mine} "
            f"页面数据键 {len(keys_mine)} 个{keys_mine[:12]}"
            + (
                ""
                if saw_key
                else "（**超时前一次都没读到 showEntrust 这个键** ⇒ 先查是不是没停在「我的」页、"
                "页面有没有取到数，别把它读成「这个身份没有入口」）"
            ),
        ):
            return False
        self.c.tap(".entrust-entry")
        # ⚠️ 不 `sleep(N)` 之后读数一次：实测这条路会在**单次读数**里拿到**空串**
        #    （自动化层还没把新页面栈报回来），于是"点了但还没跳完"被记成
        #    "没进工作台"，并把整节推成 `NOT_RUN` —— O-8 那一族里最贵的一类假红。
        #    改成轮询到 path 有值再判，并把两种情形分开写在读数里：
        #    `path` 为空 ⇒ 读不到页面栈（自动化层的读数问题）；
        #    `path` 是别的页 ⇒ 真的没跳过去（那才是产品/入口问题）。
        paths: list[str] = []
        path2 = ""
        for _ in range(30):
            path2 = self.c.current_path()
            if path2:
                if not paths or paths[-1] != path2:
                    paths.append(path2)
                if path2 == WORKBENCH:
                    break
            time.sleep(0.5)
        return self.rep.rec(
            f"{tag} [{code}] 点击入口进入经理工作台",
            path2 == WORKBENCH,
            f"path={path2!r} 读到过={paths[-4:]}"
            + (
                ""
                if path2
                else "（**整整一轮都没读到页面栈** ⇒ 先按自动化层读数问题查，"
                "别读成「入口点了没反应」）"
            ),
        )

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

    def intake_query() -> dict:
        """读受理屏（`pages/entrust/intake/intake`）实例自己记着的 `onLoad` 入参。

        为什么要解一次码：query 是框架**原样（未解码）**交给 `onLoad` 的，中文会以
        百分号编码出现（`sec_34.entry_carry_probe` 的注释记过那次"入口参数不合法"的根因）。
        """

        def read() -> dict:
            val = w.c.evaluate(
                "function(){var s=getCurrentPages()||[];"
                "for(var i=s.length-1;i>=0;i--){var p=s[i];"
                "if(p&&p.route==='pages/entrust/intake/intake'){var o=p.options||{};"
                "var out={};for(var k in o){var r=String(o[k]);"
                "try{out[k]=decodeURIComponent(r)}catch(e){out[k]=r}}"
                "return out;}}"
                "return {};}"
            )
            return val if isinstance(val, dict) else {}

        return read()

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

    # 行为 3：委托发货 → **受理屏（UI-07）**，并把货名 / 货量 / 量纲带过去。
    # ⚠️ 这一格**曾经**断的是「跳『功能预览，即将开放』占位页」。S1 增量 1 把
    #    `pickEntrustDelivery()` 由占位跳转改成经 `R.go()` 进 UI-07
    #    （`DEMO-1-readiness.md` §8.1 第 1 行；静态断言 `verify_ui_interactions.js`
    #    第 ⑨ 章同步改写），而**本章的真机断言当时没跟着改** ⇒ 它此后只能恒红。
    #    现在改断**新契约**：落点页 ＋ 源页表单值是否真的带过去了。
    w.c.set_data({"showChannel": True})
    time.sleep(0.9)
    w.c.tap(".ch-card-entrust")
    time.sleep(1.6)
    at_intake = w.c.wait_path(INTAKE, 25)
    time.sleep(1.2)  # 页面已就位但渲染帧可能滞后，静置后再截图
    w.shot("15-委托发货-受理屏")
    w.rep.rec(
        "⑮ 委托发货跳受理屏（UI-07）—— ⛔ 不再是「功能预览」占位页",
        at_intake,
        f"path={w.c.current_path()}",
    )
    if at_intake:
        q = intake_query()
        w.rep.rec(
            "⑮ 受理屏拿到了**货名**（源页的表单值经 query 带过去，不是空 url）",
            bool(str(q.get("cargo_name") or "").strip()),
            f"cargo_name={q.get('cargo_name')!r}",
        )
        w.rep.rec(
            "⑮ 受理屏拿到了**货量与量纲**（带量不带量纲 ⇒ 受理屏上会出现没有单位的数字）",
            bool(str(q.get("quantity") or "").strip()) and q.get("quantity_unit") == "吨",
            f"quantity={q.get('quantity')!r} unit={q.get('quantity_unit')!r}",
        )

    # 返回：深栈下 navigateBack 偶发抖动 → 重试直到回到发布货物页
    back = False
    for _ in range(3):
        w.c.back()
        time.sleep(0.8)
        if w.c.wait_path(PUBLISH_CARGO, 15):
            back = True
            break
    w.rep.rec("⑮ 从受理屏可返回发布货物页（二级页栈正常）", back, w.c.current_path())
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

    # ⚠️ 必须用**锚点**，不能用 `.btn-primary`：成果详情页现在有**五**处 `.btn-primary`，
    #    其中「发布 / 撤回 / 确认版本」三张卡的提交键排在「编辑内容」**之前**，而工具
    #    **没有 index 参数**（`nth-child` 被忽略、坐标触摸也落到第一个）⇒ 点的是"发布"
    #    的提交键，于是 `editing` 恒不为真、后面整段编辑态断言连锁塌掉（读到的是
    #    "编辑表单里没有 receivable_lines"，看着像产品坏了）。`data-act-edit` 才是
    #    这一格要点的那个键自己的锚点（`verify_miniapp.js` 第 446 行已登记）。
    if not w.c.tap('[data-act-edit="1"]'):
        w.rep.rec("㉕B 点「编辑内容」", False, '[data-act-edit="1"] 未命中')
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
    # 限制档：能跑，但工具不可验证 ⇒ **不计入通过**。
    w.rep.limitation(
        "㉕D 确认动作未在真机点击（能跑，但工具不可验证 ⇒ 不计入通过）",
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
        # ⚠️ 且必须走 `not_run()` —— 曾写成 `rec(..., True, ...)`，
        #    汇总里与真通过**长得一模一样**（HO 2026-09-15 点名要求修正）。
        w.rep.not_run(
            "⑧b 支付状态流转",
            "需显式开关 WALK_PAY=1（会消耗演示锚点，须在临时库上跑）",
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

    # 订单页：该单的「去支付」入口**必须消失**（ENT-044 已修）
    #
    # 首次真机执行时它**不会**消失，当时查明根因并登记为缺口 ENT-044：
    #   · `data-act-pay` 曾在同一文件里**两处复用**（「去支付」`status==='matched'` 与
    #     「支付详情」`status!=='matched'`）⇒ 锚点数无法区分命中的是哪一个；
    #   · `status` 是**订单**状态，而 `OrderOut` **不带支付状态** ⇒ 列表前端无从知道
    #     "这一单的支付单已经 paid"（`mock_pay` 只改支付单、不碰订单）。
    # 现在订单端点带上了 `pay_status`（`payment.service.pay_status_map`），页面按
    # `canGoPay` 渲染，两个入口各用**自己的锚点**（`data-act-pay` / `data-act-payinfo`）
    # ⇒ 本条从"限制记录"升级为**真断言**。
    #
    # ⚠️ 用 `nav` 直进订单页而不是 `back_to`：上面为了重新取数用了 `reLaunch`，
    #    页面栈里已经只有支付页，`back_to` 没有可返回的上一页。
    w.c.nav("reLaunch", "/" + ORDERS, ORDERS)
    w.c.wait_path(ORDERS, 25)
    time.sleep(1.6)
    n_pay = w.c.count(f'[data-act-pay="{oid}"]')
    w.rep.rec(
        "⑧b 订单页：已支付订单不再显示「去支付」（ENT-044）",
        n_pay == 0,
        f'[data-act-pay="{oid}"] n={n_pay}',
    )
    n_info = w.c.count(f'[data-act-payinfo="{oid}"]')
    w.rep.rec(
        "⑧b 订单页：改为显示「支付详情」（入口不是被藏起来，仍可查这笔支付）",
        n_info == 1,
        f'[data-act-payinfo="{oid}"] n={n_info}',
    )
    # ── H3 证据①：确认回调的**自动化**证据 ──────────────────────────────────
    # 原生弹层的确认键点不到，但"确认之后要跑的那段代码"必须另有可触发入口，
    # 否则它是一条永远拿不到证据的路径。页面为此把业务动作抽成 `onPayConfirm(source)`：
    #   · `modal` = 人点了弹层确认键（人工路径，见 H3 记录模板）；
    #   · `auto`  = 本步用 `callMethod` 触发（自动化路径）。
    # 两条**分别记录**，本条只证明「回调这段代码的逻辑正确 + 留痕」，
    # **不证明**「键可点」—— 后者仍由下面那条 LIMITATION 如实挂着。
    w.c.nav("reLaunch", f"/{PAYMENT}?order_id={oid}", PAYMENT)
    w.c.wait_path(PAYMENT, 25)
    w.wait_data(lambda x: x.get("canPay") is not None, tries=40, gap=0.5)
    w.c.call_method("onPayConfirm", ["auto"])
    traced = w.wait_data(
        lambda x: (x.get("payConfirmTrace") or {}).get("source") == "auto",
        tries=30,
        gap=0.5,
    )
    trace = traced.get("payConfirmTrace") or {}
    w.rep.rec(
        "H3 自动化证据：确认回调可触发且留痕（source=auto）",
        trace.get("source") == "auto" and int(trace.get("payId") or 0) == int(pay_id),
        f"trace={trace} 期望 payId={pay_id}",
    )
    # 回调触发后仍是同一笔、仍是 paid —— 证明「确认」这一段是**幂等**的，
    # 不会因为多触发一次就多记一笔账。
    after_auto = api_get(f"/payment/payments/order/{oid}", a.token_shipper) or {}
    w.rep.rec(
        "H3 自动化证据：回调重复触发不重复记账（同一笔、仍 paid）",
        after_auto.get("status") == "paid" and after_auto.get("id") == pay_id,
        f"status={after_auto.get('status')} id={after_auto.get('id')} 期望 id={pay_id}",
    )
    # globalData 那条痕迹也要在：它是**跨页面**可观测点，人工点击的证据靠它留存
    # （页面 data 会随页面销毁消失，globalData 不会）。
    traces = w.c.evaluate(
        "function(){try{var g=getApp().globalData;return g&&g.payConfirmTraces||[];}catch(e){return null;}}"
    )
    sources = [t.get("source") for t in traces] if isinstance(traces, list) else None
    w.rep.rec(
        "H3 可观测点：globalData 里能读到确认痕迹（人工点击的证据落点）",
        isinstance(sources, list) and "auto" in sources,
        f"sources={sources}（null = evaluate 取不到，不是'没有痕迹'）",
    )

    # HO H8（2026-09-15）明确要求这里的措辞与统计口径：
    #   ⑧b 只能写「**支付后界面已验证，原生确认点击未验证**」，不得写成全部闭环。
    #   ⇒ 本条**不计入通过**（`LIMITATION` 档），且单独陈述"未验证"的那一半。
    w.rep.limitation(
        "⑧b 原生弹层确认键未真实点击（支付后界面已验证，**原生确认点击未验证**）",
        "确认键在原生 wx.showModal 里、工具点不到；本步用真实接口驱动支付，"
        "支付**后**的渲染由真机断言覆盖 ⇒ 只证明「支付后界面正确」，"
        "**不证明**「弹层确认键可点」。H3 的两类证据分开记："
        "自动化（source=auto）已拿到，人工点击（source=modal）需按 "
        "docs/entrust/H3-原生弹层确认键人工验证记录.md 人工执行并留截图。",
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
    w.rep.rec("⑬ 解析结果渲染为结构化卡片（7 行）", len(rows) == 7, str(len(rows)))
    # ⚠️ 这里**故意不把「7」与「模型输出字段数」绑在一起**：`assistant.js` 的
    #    `buildRows()` 是对前端 `FIELD_LABELS` 的**全量投影**（7 个 key 一律出行，
    #    未识别的行标「未识别」），所以**行数与 provider 无关**；
    #    真正随模型变化的只是**有几行落到「未识别」**。
    #    ⇒ 断言写成「形状 + 至少一行识别到」，而不是去钉模型行为。
    #    （旧表述「mock 与真模型的字段数可能不同」经核代码不成立，见 DR-0009 §8.2。）
    w.rep.rec(
        "⑬ 卡片每行都有 label/value/missing（模板不会读到空字段）",
        bool(rows)
        and all(isinstance(r, dict) and {"label", "value", "missing"} <= set(r) for r in rows),
        str(rows[:1])[:120],
    )
    n_id = sum(1 for r in rows if not r.get("missing"))
    w.rep.rec(
        "⑬ 至少一行被识别（草稿确实解析出了内容）",
        n_id >= 1,
        f"识别 {n_id}/{len(rows)}",
    )
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


def sec_31(w: Walker) -> None:
    """㉛ 案件页「应用变更」正例（A2 五之四 / ENT-041）—— **真写**造出 `approved` 形态。

    为什么必须真写：`approval.targets[*]` 与 `revalidation[*]` 这两类字段**只**
    在「已批准」与「已应用」两种形态下产出，而演示种子**没有**这类案件
    （`seed_entrust_demo` 造的两宗都是 open）。不造数据，模板核对只会得到
    「数据没覆盖」—— 那是**数据问题、不是页面缺陷**（正确修法是补数据，
    往豁免名单里加字段是错的，加完就再也抓不到真·没产出的字段了）。

    四个写命令的顺序本身就是契约（与 `verify_frontend_e2e.js` 的 ⑯-d 同口径）：
      登记（**必须带 `change_category`**，否则应用会被拒）→ 进入复核 →
      批准（带**依据版本的精确 id** 与**结构化修改内容**）→ 应用。
    ⚠️ 改动**真落库**（写新版本 + 生成复核任务），所以只在运行器建的临时库上跑。
    """
    print("\n== ㉛ 案件页「应用变更」（真写造 approved 形态）==", flush=True)
    a = _anchors(w)  # ⚠️ 别写 `w.a` —— 锚点挂在模块级 `_STATE`，取法是这个 helper
    if not a.token_owner:
        w.rep.rec("㉛ 前置 · owner token 可用", False, "未取到")
        return

    items = (
        (
            api_get(f"/entrust/assignments/{ENTRUST_ASSIGNMENT_ID}/artifacts", a.token_owner) or {}
        ).get("items")
    ) or []
    # 必须挑**在该变更类别的复核候选里**的成果类型，否则复核范围为空、revalidation 仍没数据
    target = next(
        (
            x
            for x in items
            if x.get("artifact_type") == "customer_quote" and x.get("current_revision_id")
        ),
        None,
    )
    w.rep.rec(
        "㉛ 前置 · 有带生效版本的 customer_quote 成果（复核候选里的类型）",
        target is not None,
        f"artifacts={len(items)}",
    )
    if not target:
        return
    art_id = int(target["artifact_id"])
    basis_rev_id = int(target["current_revision_id"])
    # ⚠️ 三个写命令各带**唯一**幂等键：委托写端点不带 `Idempotency-Key` 会直接 400
    #    （实测回执 `{"detail":"写操作必须提供 Idempotency-Key 请求头"}`）。
    tag = str(time.time_ns())

    st1, r1 = api_post(
        f"/entrust/assignments/{ENTRUST_ASSIGNMENT_ID}/exceptions",
        a.token_owner,
        {
            "kind": "change_request",
            "title": "走查㉛·变更：货量调整",
            "severity": "medium",
            "impact_kind": "review-required",
            "change_category": "cargo_quantity_category",
            "links": [{"target_kind": "artifact", "target_id": art_id}],
        },
        idem_key=f"walk31-case-{tag}",
    )
    cid = int((r1 or {}).get("case_id") or 0)
    rev = int((r1 or {}).get("revision_no") or 0)
    w.rep.rec(
        "㉛ 前置 · 登记变更案件（带 change_category）",
        st1 == 200 and cid > 0,
        f"HTTP {st1} case_id={cid} {str(r1)[:140]}",
    )
    if not cid:
        return

    st2, r2 = api_post(
        f"/entrust/exceptions/{cid}/decision",
        a.token_owner,
        {"expected_revision": rev, "to_status": "in_review"},
        idem_key=f"walk31-review-{tag}",
    )
    rev = int((r2 or {}).get("revision_no") or rev)
    w.rep.rec("㉛ 前置 · 进入复核（open → in_review）", st2 == 200, f"HTTP {st2}")

    base = (
        (
            (api_get(f"/entrust/artifacts/{art_id}", a.token_owner) or {}).get("current_revision")
            or {}
        ).get("payload")
    ) or {}
    st3, r3 = api_post(
        f"/entrust/exceptions/{cid}/decision",
        a.token_owner,
        {
            "expected_revision": rev,
            "to_status": "approved",
            "decision_note": "走查㉛ 批准",
            "basis_revision_id": basis_rev_id,
            "approved_changes": {f"artifact#{art_id}": {**base, "note": "走查㉛：变更应用"}},
        },
        idem_key=f"walk31-approve-{tag}",
    )
    rev = int((r3 or {}).get("revision_no") or rev)
    w.rep.rec(
        "㉛ 前置 · 批准（依据版本 id + 结构化修改内容）",
        st3 == 200,
        f"HTTP {st3} {str(r3)[:100]}",
    )

    _STATE["apply_case_id"] = cid
    _STATE["apply_rev"] = rev
    _STATE["apply_artifact_id"] = art_id

    # ---- 页面：approved 形态 ----
    w.login_as(CODE_OWNER)
    if not w.enter_role("owner", OWNER):
        w.rep.rec("㉛ 前置 · 组织经理工作台进入", False, w.c.current_path())
        return
    w.c.nav("reLaunch", f"/{CASE}?case_id={cid}", CASE)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    w.rep.rec("㉛ approved 态案件页 ready", d.get("view") == "ready", str(d.get("view")))
    w.shot("31-案件页-已批准")

    appr = d.get("approval") or {}
    n_tg = len(appr.get("targets") or [])
    w.rep.rec(
        "㉛ 批准快照摘要到位（先看清将发生什么，再点确认）",
        bool(appr.get("categoryLabel")) and n_tg == 1,
        f"categoryLabel={appr.get('categoryLabel')} targets={n_tg}",
    )
    n_apply = w.c.count('[data-act-apply="1"]')
    w.rep.rec("㉛ 「查看并应用已批准的变更」锚点唯一命中", n_apply == 1, f"n={n_apply}")
    w.rep.rec(
        "㉛ 未应用时「复核传播」为空（顺序契约：先应用、再传播）",
        w.c.count(".rv-item") == 0,
        f"n={w.c.count('.rv-item')}",
    )

    w.c.tap('[data-act-apply="1"]')
    time.sleep(1.0)
    w.shot("31-案件页-应用确认条")
    n_box = w.c.count(".apply-box")
    n_sub = w.c.count('[data-act-apply-submit="1"]')
    n_can = w.c.count('[data-act-apply-cancel="1"]')
    w.rep.rec(
        "㉛ 真点击展开**页内确认条**（不是 wx.showModal —— 弹层不在渲染树里、工具点不到）",
        n_box == 1 and n_sub == 1 and n_can == 1,
        f"box={n_box} submit={n_sub} cancel={n_can}",
    )
    n_key = w.c.count(".apply-target .apply-key")
    w.rep.rec("㉛ 确认条列出受影响项（不是只给一个空按钮）", n_key >= 1, f"n={n_key}")

    w.c.tap('[data-act-apply-cancel="1"]')
    time.sleep(0.8)
    n_box2 = w.c.count(".apply-box")
    w.rep.rec(
        "㉛ 「取消」能收回确认条（点错了回得去）",
        n_box2 == 0 and w.c.count('[data-act-apply="1"]') == 1,
        f"box={n_box2}",
    )

    # ---- 真正应用：apply 只带 expected_revision，改什么只来自批准快照 ----
    w.c.tap('[data-act-apply="1"]')
    time.sleep(0.8)
    w.c.tap('[data-act-apply-submit="1"]')
    d2 = w.wait_data(
        lambda x: (x.get("revalidation") or []) or x.get("canApply") is False,
        tries=50,
        gap=0.6,
    )
    w.shot("31-案件页-应用后-复核传播")
    rv = d2.get("revalidation") or []
    w.rep.rec("㉛ 真点击「确认应用」后出现「复核传播」清单", len(rv) >= 1, f"n={len(rv)}")
    w.rep.rec(
        "㉛ 应用后不再给「应用变更」入口（已应用不可重复应用）",
        d2.get("canApply") is False,
        str(d2.get("canApply")),
    )
    w.rep.rec(
        "㉛ 复核清单每项带复核任务号与区域（不是空壳）",
        bool(rv) and all(x.get("reviewTaskId") and x.get("area") for x in rv),
        str(rv[:1])[:140],
    )
    n_item = w.c.count(".rv-item")
    w.rep.rec(
        "㉛ 「复核传播」卡片真的渲染出来（DOM 条目数与数据一致）",
        n_item == len(rv) and n_item >= 1,
        f"dom={n_item} data={len(rv)}",
    )


def sec_32(w: Walker) -> None:
    """㉜ 成果页「待复核」徽标（A2 五之四 / ENT-041）—— 依赖 ㉛ 应用后留下的复核项。

    ⚠️ 这是**只读**视图：复核任务在任务页完成、标记随之解除（AC-12）
    ⇒ 本节的正确断言是「徽标出现且带区域与任务号」，
    **不是**「能在这里把它消掉」（那会去验一个不存在的入口）。
    """
    print("\n== ㉜ 成果页「待复核」徽标 ==", flush=True)
    a = _anchors(w)
    cid = _STATE.get("apply_case_id")
    if not (cid and a.token_owner):
        w.rep.rec("㉜ 前置 · ㉛ 已应用并留下复核项", False, "㉛ 未产出 case_id")
        return

    detail = api_get(f"/entrust/exceptions/{cid}", a.token_owner) or {}
    rv = detail.get("revalidation") or []
    art_id = next(
        (
            int(x["target_id"])
            for x in rv
            if x.get("target_kind") == "artifact" and x.get("target_id")
        ),
        None,
    )
    w.rep.rec(
        "㉜ 前置 · 从 REST 复核项拿到它指向的成果 id",
        art_id is not None,
        f"art_id={art_id} rv={len(rv)}",
    )
    if not art_id:
        return

    w.login_as(CODE_OWNER)
    w.enter_role("owner", OWNER)
    w.c.nav("reLaunch", f"/{ARTIFACT}?artifact_id={art_id}", ARTIFACT)
    d = w.wait_data(
        lambda x: (x.get("artifact") or {}).get("id") or x.get("view") not in (None, "", "loading"),
        tries=40,
        gap=0.5,
    )
    w.shot("32-成果页-待复核徽标")
    nr = ((d.get("artifact") or {}).get("needsRevalidation")) or {}
    w.rep.rec("㉜ 成果页带上 needsRevalidation（派生自复核项）", bool(nr), str(nr)[:120])
    n_title = w.c.count(".art-rv-title")
    n_line = w.c.count(".art-rv-line")
    w.rep.rec(
        "㉜ 徽标标题真的渲染出「待复核」（有元素、不是空白）",
        n_title == 1,
        f"n={n_title}",
    )
    w.rep.rec(
        "㉜ 徽标给出复核区域与复核任务号（不是空壳）",
        bool(nr.get("areasText")) and bool(nr.get("reviewTaskIdsText")) and n_line >= 2,
        f"areas={nr.get('areasText')} tasks={nr.get('reviewTaskIdsText')} lines={n_line}",
    )


def sec_33(w: Walker) -> None:
    """㉝ 页面栈深度**运行期实测**（DR-0011）。

    DR-0011 只在 CI 里证明了「**声明**链深 ≤ `STACK_BUDGET`」，并自己写明
    「**不承诺**『10 层上限在真机上就是这样』」（§5）。本章补的正是运行期那一半：

    一、**按 URL 压声明链**：`shipper → assistant → publish/cargo → preview`
        （`verify_routes.js` 打印的声明最深链 = **4 层**）—— 逐层压，每层断言
        **深度恰好 +1**（不是 0、不是 +2），且不超过从源码读到的 `STACK_BUDGET`。
        链深"恰好 +1"很重要：若中途被 replace/reLaunch 悄悄清栈，深度会**不增或回退**，
        那样"回到上一级"的行为就跟声明的不一样了。
    二、**按 URL 直进压到被拒**：把**观测到的层数**与源码里的 `MAX_STACK` 对账
        —— 这正是 DR-0011 §5 明确没承诺过的那一条；并断言 **预算 < 硬限**（留有余量）。
    三、**真实入口导航（HO H2）**：真点击页面自己的入口 / 系统返回键，覆盖
        **冷启动 / push / replace / 返回 / 复用**，逐步记录实际栈深。
    四、清栈回起点（`reLaunch` 后栈深必须回到 1）。

    ⚠️ 诚实边界（本节内部分清手段，措辞与手段一致）：
    * **第一节是「按 URL 压声明链」**（`automation_navigate`），**不是真实点击** ——
      它证明的是「声明链每层恰好 +1」；
    * **第二节是「按 URL 直进压栈到被拒」** —— 它测的是**平台**的行为，
      **不能**用来证明 `go()` 的预算策略生效；
    * **第三节才是真实入口导航**（HO H2）：每一步都 `tap()` 到页面自己的入口
      （或系统返回键），导航由页面代码的 `go()` 决策，覆盖
      **冷启动 / push / replace / 返回 / 复用** 五类路径，并记录实际栈深。
    * **预算耗尽路径用真实入口走不到**（真实可达最深 4 层 < 预算 8）——
      这一条记为**限制档，不计入通过**，不合并声称已覆盖。
    """
    print("\n== ㉝ 页面栈深度运行期实测（DR-0011）==", flush=True)

    src = ""
    routes_js = os.path.join(REPO_ROOT, "miniapp", "utils", "routes.js")
    try:
        with open(routes_js, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:  # noqa: BLE001
        w.rep.rec("㉝ 读得到 miniapp/utils/routes.js（预算常量出处）", False, str(exc)[:80])

    def _const(name: str) -> int | None:
        m = re.search(rf"const {name} = (\d+)", src)
        return int(m.group(1)) if m else None

    budget = _const("STACK_BUDGET")
    hard = _const("MAX_STACK")
    w.rep.rec(
        "㉝ 从源码读到预算常量（不写死在脚本里）",
        budget is not None and hard is not None,
        f"STACK_BUDGET={budget} MAX_STACK={hard}",
    )
    if budget is None or hard is None:
        return

    # 身份用 `seed-owner`：它的工作台队列才有委托卡（见第三节开头的实测对照）。
    w.login_as(CODE_OWNER)
    if not w.enter_role("shipper", SHIPPER):
        w.rep.rec("㉝ 前置 · 货主工作台进入", False, w.c.current_path())
        return
    time.sleep(1.2)

    # ---- 一、按 URL 压声明链（**不是真实入口**，措辞必须与手段一致）----
    # ⚠️ 这里用 `navigate("navigateTo")` 直压 URL，**不是**「真实点击」——
    # 早期版本把标签写成「真实点击」，属于**手段与措辞不符**（HO H2 正是要求分清两者）。
    # 真实入口的覆盖在第三节。
    chain = [
        ("pages/assistant/assistant", "③ 智能搜索页（声明链第 2 层）"),
        ("pages/publish/cargo/cargo", "③ 发布货源页（声明链第 3 层）"),
        ("pages/preview/preview", "③ 预览页（声明链第 4 层）"),
    ]
    d0 = len(w.c.page_stack())
    w.rep.rec(
        "㉝ switchTab 进工作台后栈深 = 1（tabBar 页归 1，这是链深的起点）",
        d0 == 1,
        f"depth={d0}",
    )
    depths = [d0]
    for path, label in chain:
        before = len(w.c.page_stack())
        w.c.navigate("navigateTo", "/" + path)
        ok_path = w.c.wait_path(path, 25)
        after = len(w.c.page_stack())
        depths.append(after)
        w.rep.rec(
            f"㉝ 链深逐层 +1：{path} | {label}",
            ok_path and after == before + 1,
            f"{before} → {after}",
        )
        w.shot("33-栈深-" + path.rsplit("/", 1)[-1])
    observed = max(depths)
    w.rep.rec(
        f"㉝ 实测最深链深 ≤ STACK_BUDGET（{budget}）",
        observed <= budget,
        f"实测 {observed} 层（声明最深链为 4 层，见 verify_routes.js）",
    )

    # ---- 二、实测平台硬限（**按 URL 直进**，不是真实点击）----
    last_ok = len(w.c.page_stack())
    rejected_at = None
    for _ in range(hard + 3):
        cur = len(w.c.page_stack())
        j = w.c.tool(
            "automation_navigate",
            "--action",
            "navigateTo",
            "--url",
            "/pages/preview/preview",
        )
        time.sleep(0.6)
        nxt = len(w.c.page_stack())
        if not j.get("ok") or nxt <= cur:
            rejected_at = cur
            break
        last_ok = nxt
    w.rep.rec(
        f"㉝ 平台在实测的第 {rejected_at} 层拒绝了再压栈，与源码 MAX_STACK（{hard}）对账",
        rejected_at is not None and rejected_at >= hard,
        f"观测被拒于 {rejected_at} 层 / 源码 MAX_STACK={hard} / 最大成功 {last_ok}",
    )
    w.rep.rec(
        "㉝ 项目预算 < 平台实测硬限（预算留有余量，不是贴着平台上限定的）",
        rejected_at is not None and budget < rejected_at,
        f"预算 {budget} < 实测硬限 {rejected_at}",
    )
    # 限制档（不计入通过）：本节第二节按 URL 直进压栈，测的是**平台行为**；
    # 它**不能**证明 go() 的预算策略在真实入口下生效。
    # ⚠️ HO H2 要求「通过页面真实入口覆盖 push / replace / 返回 / 复用 / 冷启动 / 预算耗尽」
    #    ⇒ 真实入口那部分由本函数后面的第三节承担（见 `sec_33` 第三节）。
    w.rep.limitation(
        "㉝ 按 URL 直进压栈只能测平台行为，**不能**据此证明 go() 预算策略生效",
        "真实可达的链只有 4 层；`replace` 策略由**第三节的真实入口**覆盖（页级策略、与层数无关），"
        "但「栈到预算时改走 fallback」这一支仍走不到（见本节末尾的预算耗尽限制）",
    )

    # ---- 三、真实入口导航（HO H2）----
    #
    # HO H2 原文：「通过页面真实入口覆盖 push、replace、返回、复用、冷启动及预算耗尽路径，
    # 记录实际栈深。**不能只靠注册表或逐页 reLaunch 证明**」。
    # ⇒ 本节每一步都是 `tap()` 打到页面自己的入口（或系统返回键），
    #   导航由页面代码里的 `go()` 决策 —— 而**不是**脚本直接 `navigate()`。
    print("\n-- 三、真实入口导航（HO H2）--", flush=True)
    w.c.navigate("reLaunch", "/" + INDEX)
    time.sleep(1.4)

    # ⚠️ 身份必须是 `seed-owner`（外层 `login_as(CODE_OWNER)` 已设），**不是** `seed-shipper`：
    # 2026-09-16 用 REST 探针（不依赖 IDE）实测各演示身份的工作台队列：
    #   seed-shipper   → `GET /entrust/assignments?view=org` n=0  ⇒ **队列为空**
    #   seed-owner     → n=2 ids=[2, 1]                          ⇒ 有委托卡
    #   seed-mgr-single→ n=1 ids=[3]；seed-mgr-multi → 400（多组织须带 org_id）
    # 上一轮误用 seed-shipper，直接导致「委托卡锚点 n=0」，白烧一轮真机。
    if not w.enter_role("shipper", SHIPPER):
        w.rep.rec("㉝ 第三节前置 · 真点击身份卡进入货主工作台", False, w.c.current_path())
        return
    d = len(w.c.page_stack())
    w.rep.rec(
        "㉝ 冷启动后真点击身份卡 ⇒ switchTab 归 1（冷启动路径的栈深起点）",
        d == 1,
        f"depth={d}",
    )
    w.shot("33-真实入口-冷启动-货主")

    # switchTab 到「我的」只是**前置**（tabBar 页无深链入口），真正的入口是被点击的 `.entrust-entry`
    w.c.nav("switchTab", "/" + MINE, MINE)
    path_mine, mine, keys_mine, saw_key = w.wait_mine_ready()
    n_entry = w.c.count(".entrust-entry")
    if mine.get("showEntrust") is not True or n_entry != 1:
        w.rep.not_run(
            "㉝ 第三节前置 · 「我的」页委托入口不可见 ⇒ 真实入口链走不下去",
            f"showEntrust={mine.get('showEntrust')!r} n_entry={n_entry} path={path_mine} "
            f"页面数据键 {len(keys_mine)} 个{keys_mine[:12]}"
            + (
                ""
                if saw_key
                else "（**超时前一次都没读到 showEntrust 这个键** ⇒ 先查是否停在「我的」页、"
                "页面有没有取到数，再谈「这个身份没有委托入口」）"
            ),
        )
        return
    before = len(w.c.page_stack())
    w.c.tap(".entrust-entry")
    ok = w.c.wait_path(WORKBENCH, 25)
    after = len(w.c.page_stack())
    w.rep.rec(
        "㉝ **push（真实入口）**：「我的」→ 经理工作台，栈深 +1",
        ok and after == before + 1,
        f"{before} → {after}（{w.c.current_path()}）",
    )
    if not ok:
        w.rep.not_run("㉝ 第三节后续步骤", "未进入工作台，真实入口链断在这里")
        return

    # 工作台可能先要求选组织（ambiguous）—— 那也是**真实入口**，用真点击解决
    wb = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    if wb.get("orgReason") == "ambiguous":
        n_pill = w.c.count(".org-pill")
        w.rep.rec("㉝ 工作台要求先选组织：真机渲染出组织 pill", n_pill >= 1, f"n={n_pill}")
        if n_pill >= 1:
            w.c.tap(".org-pill")
            time.sleep(2.5)
            wb = w.c.page_data()
    items = wb.get("items") or []
    ids = [it.get("assignmentId") for it in items]
    if not items:
        w.rep.not_run(
            "㉝ 第三节前置 · 工作台委托队列为空 ⇒ 真实入口链走不下去",
            f"view={wb.get('view')} orgReason={wb.get('orgReason')} items=0"
            "（需要一个 claimed 的委托才有「登记案件」入口）",
        )
        return
    # 优先种子里的 ASSIGNMENT_MAIN（claimed，才有「登记案件」入口），否则退到第一条
    aid = prefer_anchor(ENTRUST_ASSIGNMENT_ID if ENTRUST_ASSIGNMENT_ID in ids else ids[0])
    w.rep.rec("㉝ 工作台委托队列非空（真实入口链起点）", True, f"ids={ids} ⇒ 选中 #{aid}")

    card = f'[data-id="{aid}"]'
    w.rep.rec(
        "㉝ 真实入口锚点唯一（工作台委托卡 data-id）",
        w.c.count(card) == 1,
        f"n={w.c.count(card)}",
    )
    before = len(w.c.page_stack())
    w.c.tap(card)
    ok = w.c.wait_path(DETAIL, 25)
    after = len(w.c.page_stack())
    w.rep.rec(
        "㉝ **push（真实入口）**：委托卡 → 委托详情，栈深 +1",
        ok and after == before + 1,
        f"{before} → {after}（{w.c.current_path()}）",
    )

    detail = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    if detail.get("canCreateCase") is not True:
        w.rep.not_run(
            "㉝ 第三节后续步骤（登记表单 / replace / 返回 / 复用）",
            f"委托 #{aid} 的 canCreateCase={detail.get('canCreateCase')}"
            "（只有 claimed 的委托才给「登记案件」入口）",
        )
        return

    entry = '[data-act-create-case="create-case"]'
    w.rep.rec(
        "㉝ 真实入口锚点唯一（登记案件入口）",
        w.c.count(entry) == 1,
        f"n={w.c.count(entry)}",
    )
    before = len(w.c.page_stack())
    w.c.tap(entry)
    ok = w.c.wait_path(CASE_CREATE, 25)
    after = len(w.c.page_stack())
    w.rep.rec(
        "㉝ **push（真实入口）**：详情页 → 登记表单，栈深 +1",
        ok and after == before + 1,
        f"{before} → {after}（{w.c.current_path()}）",
    )

    # replace 真实入口：表单真提交 → 案件详情。
    # 这里只注入**表单取值**（模拟器对 input 赋值不触发 bindinput，全项目统一用 setData 注值，
    # 见 ㉖ 章），**导航本身 100% 走页面自己的 `go()`** —— 断言的是「replace 不 +1」。
    if w.c.wait_path(CASE_CREATE, 5):
        w.c.set_data({"form.title": CASE_TITLE + "（栈深）", "form.cause": "走查㉝：验 replace"})
        time.sleep(1.2)
        w.rep.rec(
            "㉝ 真实入口锚点唯一（提交按钮）",
            w.c.count('[data-act-submit-case="1"]') == 1,
        )
        before = len(w.c.page_stack())
        w.c.tap('[data-act-submit-case="1"]')
        ok = w.c.wait_path(CASE, 30)
        after = len(w.c.page_stack())
        w.rep.rec(
            "㉝ **replace（真实入口）**：真点击提交 ⇒ 落到案件页而栈深**不变**（replace 不 +1）",
            ok and after == before,
            f"{before} → {after}（{w.c.current_path()}）",
        )
        w.shot("33-真实入口-replace后-案件页")

        # 返回键：replace 掉的表单**不在**返回路径上 ⇒ 应回到详情页
        w.c.back()
        time.sleep(1.6)
        back1 = len(w.c.page_stack())
        w.rep.rec(
            "㉝ **返回**：案件页返回 ⇒ 回到委托详情（被 replace 掉的表单不在返回路径上）",
            w.c.current_path() == DETAIL and back1 == 3,
            f"depth={back1} path={w.c.current_path()}",
        )
        w.c.back()
        time.sleep(1.6)
        back2 = len(w.c.page_stack())
        w.rep.rec(
            "㉝ **返回**：再返回 ⇒ 回到经理工作台，栈深逐层 -1",
            w.c.current_path() == WORKBENCH and back2 == 2,
            f"depth={back2} path={w.c.current_path()}",
        )

        # 复用真实入口：工作台「会话」入口 push 进会话，再从会话返回 ⇒ 应**复用**栈里的工作台
        sess_act = f'[data-act-session="{ENTRUST_ASSIGNMENT_ID}"]'
        w.rep.rec(
            "㉝ 真实入口锚点唯一（委托卡「会话」按钮，catchtap 与卡片 bindtap 分开）",
            w.c.count(sess_act) == 1,
            f"n={w.c.count(sess_act)}",
        )
        before = len(w.c.page_stack())
        w.c.tap(sess_act)
        ok = w.c.wait_path(SESSION, 25)
        after = len(w.c.page_stack())
        w.rep.rec(
            "㉝ **push（真实入口）**：工作台「会话」→ 会话屏，栈深 +1",
            ok and after == before + 1,
            f"{before} → {after}（{w.c.current_path()}）",
        )
        w.c.tap(".nav-back")
        time.sleep(2.0)
        after = len(w.c.page_stack())
        w.rep.rec(
            "㉝ **复用（真实入口）**：会话「返回」→ 复用栈中已有的工作台，栈深**回落而不是再 +1**",
            w.c.current_path() == WORKBENCH and after == 2,
            f"depth={after} path={w.c.current_path()}（若为 push 会是 4）",
        )
        w.shot("33-真实入口-复用回工作台")

    # 预算耗尽：**真实入口到不了预算线**，如实记为限制而不是"已覆盖"。
    w.rep.limitation(
        "㉝ **预算耗尽路径**：真实入口可达的最深链实测只有 4 层，到不了预算 8 层",
        f"真实入口最深 {max(depths)} 层 / STACK_BUDGET={budget} ⇒ "
        "「栈到预算时 go() 改走 fallback」这一支**无法**用真实入口走到；"
        "若要用真实入口覆盖，必须先有深度 ≥ 预算的真实业务链（当前产品形态不存在）",
    )

    # ---- 四、清栈回起点 ----
    w.c.navigate("reLaunch", "/" + INDEX)
    time.sleep(1.0)
    back = len(w.c.page_stack())
    w.rep.rec("㉝ reLaunch 后栈回 1（清栈可控，不是越压越深）", back == 1, f"depth={back}")


# ㉞ UI-07 客户委托草稿 / 提交屏（S1 出口判据的**前置**）
# ---------------------------------------------------------------------------
# 三个演示身份，分别对应受理屏的三种数据形态（种子见 backend/scripts/
# `seed_entrust_demo.py` 与 `seed_entrust_orgpicker.py`）：
#   seed-shipper           → 对「演示经营主体·工作台」有**一条**生效授权 ⇒ 唯一目标
#   seed-shipper-orgpicker → 对甲 / 乙**两条**授权                ⇒ 多目标必须显式选
#   seed-mgr-multi         → **没有任何**委托授权                 ⇒ 空态
# ⚠️ 这几个常量放在**模块级**：函数体内的全大写局部变量会被 ruff 判成 N806。
CODE_SHIPPER_ORGPICKER = "seed-shipper-orgpicker"
CODE_MGR_MULTI = "seed-mgr-multi"
INTAKE_TITLE_OK = "走查·大连→上海 5 万吨煤炭（受理屏真实点击）"
INTAKE_TITLE_LOCK = "走查·锁定与续接（受理屏）"
INTAKE_CARRY_NAME = "走查货物·铁矿石"
INTAKE_CARRY_QTY = "2400"
INTAKE_EMPTY_TITLE = "还没有可委托的服务主体"

# ㉟ 章（S1 出口判据 ②③④ 的**设备侧**取证）用的身份与数据。
#
# `CODE_MGR_ONLY_B` 是**仅乙组织**的经理：出口判据 ④（unrelated organization B
# 不可见）必须用"不属于甲"的身份才验得出来 —— `seed-mgr-multi` 同时属于甲、乙，
# 而详情可见性判据是「该委托的组织 ∈ 调用者的**任一**组织」，拿它验会得到假绿。
# 这个身份是随本章一起补进 `seed_entrust_orgpicker.py` 的。
CODE_MGR_ONLY_B = "seed-mgr-only-b"
# `CODE_MGR_SINGLE` 与 `seed-mgr-multi` 同是甲组织经理 —— 并发负例里的"另一个写者"。
CODE_MGR_SINGLE = "seed-mgr-single"
# 本章会**真写**两张委托（都走界面真实点击提交到甲组织）：
#   · QUEUE —— 用来验「出现在正确的队列」+「队列上受理成功」；
#   · RACE  —— 用来验「别人抢先受理后，界面那一下拿 409 并刷新成真实状态」。
# 两张都要，因为一张单只能被受理一次：受理成功之后就没有"待受理"可点了。
INTAKE_TITLE_QUEUE = "走查·队列可见与受理（界面提交）"
INTAKE_TITLE_RACE = "走查·并发受理（外部先受理）"


def sec_34(w: Walker) -> None:
    """㉞ UI-07 客户委托草稿 / 提交屏（S1 出口判据的**前置**）。

    为什么单独一章
    --------------
    UI-07 是 S1 的出口屏：货主在「发布货源」页选「委托发货」→ 受理屏 → 建草稿 →
    提交 → 落到该委托详情。此前 S1 只到「写码完成」——**没有一次真实点击**验过这条
    链路，也没有人看过五态各自长什么样。CI 能证的只是「五个分支都在模板里」
    （`verify_entrust_ui.js`）与「这一页没有裸导航」（`verify_routes.js`）；
    「点提交之后到底落在哪一页」「续接到底有没有生效」只能真机跑。

    本节覆盖（措辞与手段一一对应）
    ----------------------------
    一、**全链真实点击**：身份卡 → 自定义 tabBar 凸起「发布货物」→「委托发货」
        → 真实输入标题 → 「提交委托」。每一步都是 `tap()` 打到页面 / 组件自己的入口，
        导航由页面代码的 `go()` 决策；`push` 与 `replace` 分别按「栈深 +1 / 不变」对账。
    二、**在途载荷（幂等键持久化）**：建草稿后两把键 + 编号 + 版本是否落盘、
        换页面实例重进是否续接同一张草稿、续接后提交是否落到**同一张**委托。
        「响应丢失后重进会重复建草稿」这条已知余量修没修，看的就是第二节。
    三、多目标不许替用户猜｜四、空态｜五、五态分支渲染。

    ⚠️ 诚实边界（四条，均在末尾按档登记、**不计入通过**）：
    * 「草稿已建、提交未成」这个中间态**无法用真实点击造出**（真实点击要么两步都成，
      要么在 `ensureDraft` 之前就被前置检查拦下）⇒ 第二节那一步走
      `call_method('ensureDraft', [...])`，**证据等级低于点击**；
    * 「重新填写 / 放弃」的清理写在原生 `wx.showModal` 的确认回调里，确认键不在
      渲染树、工具点不到 ⇒ 只证「入口存在 + 未确认前不改数据」；
    * 并发双写、真·跨进程续接、五态的**到达条件**（真实 401 / 403 / 404 / 断网）
      在单模拟器里做不出来。
    """
    print("\n== ㉞ UI-07 客户委托草稿/提交屏（S1 出口判据前置）==", flush=True)

    def draft_key() -> str:
        """按**页面同一条口径**算出当前用户的在途载荷键。

        ⚠️ 这里刻意**复刻** `intake.js` 的推导（`前缀 + user_id`），而不是
        「扫 storage 里所有以 `entrust_intake_draft_` 开头的键」：后者会让
        **按用户隔离**这条性质永远为真 —— 换个账号登录后，扫到的还是上一个人的键，
        而断言照样报「找到了」。键必须由当前 `user_id` 算出来，隔离才算被测到。
        """
        val = w.c.evaluate(
            "function(){var r=wx.getStorageSync('user_info');if(!r)return '';"
            "var u=null;try{u=JSON.parse(r)}catch(e){return ''}"
            "var id=(u&&u.user_id!=null)?String(u.user_id):'';"
            "return id?('entrust_intake_draft_'+id):'';}"
        )
        return str(val) if val else ""

    def draft_raw(key: str) -> str:
        """在途载荷的原始文本（空串 = 没有这份载荷）。"""
        if not key:
            return ""
        val = w.c.evaluate(
            "function(k){var v=wx.getStorageSync(k);return v?JSON.stringify(v):'';}",
            [key],
        )
        return str(val) if val else ""

    def text_of(selector: str) -> str:
        """元素的可见文本（剥标签 + 压空白）。

        `--action text` 只读第一个匹配项，故一律取 `outerWXML` 做包含断言（与 ⑮ 章同口径）。
        """
        return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", w.c.outer_wxml(selector)))

    def clear_draft() -> None:
        """清掉当前身份留下的在途载荷（进场景前用，免得上一次的续接干扰本场景）。"""
        key = draft_key()
        if key:
            w.c.remove_storage(key)

    def entry_carry_probe() -> tuple[str, str]:
        """读受理屏实例自己记着的 `onLoad` 入参：**框架给什么就是什么**。

        返回 `(原样值, 解码一次后的值)`。

        ⚠️ 这个探针是本轮新增的，起因是一次**看不懂的失败**：首跑时受理屏直接进
        `view='error' title='入口参数不合法'`，而当时打印的 note 里既没有 `hint`
        （守卫给的原因）也没有框架实际交给了什么 —— 只能靠 `dir()`/Node 复现去猜。
        结论是：小程序把 query **原样（未解码）**交给 `onLoad`，页面若拿它重建 url
        就会**再编码一次**，中文初值长度膨胀 3 倍。把这件事写成探针，下一次失败
        第一眼就能看到"框架给的是哪一形态"。
        """
        val = w.c.evaluate(
            "function(){var s=getCurrentPages()||[];"
            "for(var i=s.length-1;i>=0;i--){var p=s[i];"
            "if(p&&p.route==='pages/entrust/intake/intake'){var o=p.options||{};"
            "var r=(o.cargo_name==null?'':String(o.cargo_name));var d=r;"
            "try{d=decodeURIComponent(r)}catch(e){d=r}"
            "return {raw:r,norm:d};}}"
            "return {raw:'',norm:''};}"
        )
        if not isinstance(val, dict):
            return "", ""
        return str(val.get("raw") or ""), str(val.get("norm") or "")

    def open_cargo_entry() -> tuple[bool, str]:
        """进入「发布货源」页：先试真实入口（自定义 tabBar 中间的凸起），探不到就 URL 直进。

        **第一节与第二节共用这一个函数** —— 各写一份的后果本轮已经演过一遍：
        第二节硬点了 `[data-key='publish']`，而第一节实测该锚点命中 0（自定义 tabBar
        是组件，不在页面级查询范围内），于是第二节的前置断言必然失败，看上去像
        "续接没生效"，实际是**链路根本没进去**。共用之后，"锚点不可达"这件事只有
        一个地方知道，两节要么都退化成 URL 直进、要么都不退化。

        返回 `(是否到达发布货源页, 进入方式)`；进入方式写进断言 note —— 它是证据等级，
        不能省。
        """
        n_center = w.c.count('[data-key="publish"]')
        if n_center == 1:
            w.c.tap('[data-key="publish"]')
            via = "真实点击 tabBar 凸起"
        else:
            w.c.nav("navigateTo", "/" + PUBLISH_CARGO, PUBLISH_CARGO)
            via = f"URL 直进（凸起锚点命中 {n_center} 个，组件树不在页面级查询范围内）"
        ok = w.c.wait_path(PUBLISH_CARGO, 30)
        time.sleep(1.4)
        return ok, via

    def to_intake(carry: bool, qty: str = INTAKE_CARRY_QTY) -> tuple[bool, bool, int]:
        """在「发布货源」页填草稿初值 → 真实点击「委托发货」→ 到达受理屏。

        返回 `(是否点到入口, 是否到达受理屏, 锚点命中数)`。
        """
        if carry:
            # `pickEntrustDelivery()` 读的就是 `this.data.form.cargo_name / weight_t`，
            # setData 足以驱动它；「输入框接上了 bindinput」是另一件事（受理屏那边
            # 用真实输入验）。⚠️ 用 `set_data` 而不是逐字段真实输入。
            w.c.set_data({"form.cargo_name": INTAKE_CARRY_NAME, "form.weight_t": qty})
            time.sleep(0.6)
        n_entrust = w.c.count('[data-act-entrust="1"]')
        if n_entrust != 1:
            return False, False, n_entrust
        t = w.c.tap('[data-act-entrust="1"]')
        ok = w.c.wait_path(INTAKE, 30)
        time.sleep(1.6)
        return bool(t), bool(ok), n_entrust

    # ===================== 一、全链真实点击 =====================
    print("\n-- 一、全链真实点击（身份卡 → 发布货物 → 委托发货 → 提交）--", flush=True)
    path = w.login_as(CODE_SHIPPER)
    if path != INDEX:
        w.rep.rec("㉞ 前置 · seed-shipper 登录", False, f"未停在身份选择页（{path}）")
        return
    if not w.enter_role("shipper", SHIPPER):
        w.rep.rec("㉞ 前置 · 真点击身份卡进入货主工作台", False, w.c.current_path())
        return
    time.sleep(1.2)
    dep_start = len(w.c.page_stack())
    w.rep.rec(
        "㉞ 冷启动后真点击身份卡 ⇒ switchTab 归 1（链路起点，不是越压越深）",
        dep_start == 1,
        f"depth={dep_start}",
    )
    w.rep.rec(
        "㉞ 在途载荷键按 `user_id` 推导（拿不到 user_id 的账号**不持久化**）",
        draft_key() != "",
        f"key={draft_key()!r}",
    )
    clear_draft()

    # 真实入口＝自定义 tabBar 中间的凸起「发布货物」（`custom-tab-bar/index.js`
    # 的 `key === 'publish'` 分支）。它是**组件**，可能不在页面级 `querySelectorAll`
    # 的查询范围内 ⇒ 先探再用、探不到就**如实降级**（不假装点到了）。
    # 降级路径与第二节共用 `open_cargo_entry()`：两节各写一份，下一次锚点变化时
    # 就会有一节**悄悄退化成假失败**（本轮第二节正是这么挂的）。
    dep_a = len(w.c.page_stack())
    n_center = w.c.count('[data-key="publish"]')
    ok_cargo, via_cargo = open_cargo_entry()
    if n_center == 1:
        w.rep.rec(
            "㉞ **push（真实入口）**：tabBar 凸起「发布货物」→ 发布货源页，栈深 +1",
            ok_cargo and len(w.c.page_stack()) == dep_a + 1,
            f"{dep_a} → {len(w.c.page_stack())}（{via_cargo} path={w.c.current_path()}）",
        )
    else:
        w.rep.not_run(
            "㉞ tabBar 凸起「发布货物」的真实点击",
            f"`[data-key='publish']` 命中 {n_center} 个（自定义 tabBar 是**组件**，不在"
            "页面级查询范围内；客户端也没有坐标点击 / 组件作用域查询通道）⇒ 本跳改用 "
            "URL 直进发布货源页，本节只对「委托发货 → 提交」两跳主张真实点击。"
            "要补上这一跳得另开 OS 级鼠标通道（与 ⑧b 原生弹层同一类手段），本切片未做。",
        )

    pg_cargo = w.wait_data(lambda x: x.get("showChannel") is True, tries=30)
    w.rep.rec(
        "㉞ 发布货源页的默认态就是发货方式选择（进入即出现，不需要额外一步）",
        pg_cargo.get("showChannel") is True,
        f"showChannel={pg_cargo.get('showChannel')}",
    )
    # 给源页面填上货名 / 货量（`weight_t` 口径是吨）——本节要验的是「被带过去当草稿初值」，
    # 所以值必须与默认空值**不同**。⚠️ 用 `set_data` 而不是逐字段真实输入：
    # `pickEntrustDelivery()` 读的就是 `this.data.form.cargo_name / weight_t`，setData 足以
    # 驱动它；「输入框接上了 bindinput」是另一件事（受理屏那边用真实输入验）。
    # 这两步现在收在 `to_intake()` 里，第二节走同一份代码。
    n_entrust = w.c.count('[data-act-entrust="1"]')
    w.shot("34-1-发货方式-锚点")
    w.rep.rec(
        "㉞ 「委托发货」入口可被唯一命中"
        "（同类 `.ch-opt` 有两个，而工具没有 index 参数 ⇒ 必须有可区分的锚点）",
        n_entrust == 1,
        f"命中 {n_entrust} 个",
    )
    if n_entrust != 1:
        w.rep.not_run("㉞ 受理屏相关全部断言", "「委托发货」锚点缺失，链路断在这里")
        return

    dep_b = len(w.c.page_stack())
    t_intake, ok_intake, _ = to_intake(carry=True)
    dep_c = len(w.c.page_stack())
    w.shot("34-2-受理屏-初始")
    w.rep.rec(
        "㉞ **push（真实入口）**：「委托发货」→ 受理屏，栈深 +1",
        bool(t_intake and ok_intake) and dep_c == dep_b + 1,
        f"{dep_b} → {dep_c}（tap={t_intake} path={w.c.current_path()}）",
    )
    if not ok_intake:
        w.rep.not_run("㉞ 受理屏相关全部断言", "未进入受理屏，链路断在这里")
        return

    # 入口入参的**框架原样形态**必须写进 note：受理屏是第一个带**中文**参数的入口，
    # 首跑就栽在「框架交给 `onLoad` 的是未解码串 ⇒ 页面重建 url 时二次编码」上。
    # 断言只要求「解码一次后就是用户填的那串字」—— 对两种框架行为都成立，
    # 而 note 把**本机实测到底是哪一种**记下来（事实有来源，不靠推测）。
    raw_name, norm_name = entry_carry_probe()
    w.rep.rec(
        "㉞ 入口入参归一化：框架原样给的形态不影响判定"
        "（框架给的是「已解码值」还是「百分号原样串」，见 note）",
        norm_name == INTAKE_CARRY_NAME,
        f"onLoad.cargo_name={raw_name!r} → 解码一次={norm_name!r}",
    )

    pg = w.wait_data(lambda x: x.get("view") == "ready", tries=30)
    w.rep.rec(
        "㉞ 五态之 ready：授权清单到达且非空",
        pg.get("view") == "ready",
        f"view={pg.get('view')!r} title={pg.get('viewTitle')!r} hint={pg.get('viewHint')!r}",
    )
    targets = pg.get("targets") or []
    org_names = [str((t or {}).get("orgName") or "") for t in targets]
    w.rep.rec(
        "㉞ 可选目标来自「我授权出去的组织」（不是「我所在的组织」——"
        "后者会给出能选但必然 403 的选项）",
        len(targets) >= 1,
        f"targets={len(targets)} {org_names}",
    )
    form = pg.get("form") or {}
    w.rep.rec(
        "㉞ 货名 / 货量 / 单位被当作草稿初值带过来（不做二次加工）",
        form.get("cargo_summary") == INTAKE_CARRY_NAME
        and form.get("quantity") == INTAKE_CARRY_QTY
        and form.get("quantity_unit") == "吨",
        f"cargo_summary={form.get('cargo_summary')!r} quantity={form.get('quantity')!r} "
        f"unit={form.get('quantity_unit')!r}",
    )
    w.rep.rec(
        "㉞ 标题**没有**被货名冒充（`title` 仍为空：源页面没有「要办什么」这个信息）",
        not str(form.get("title") or "").strip(),
        f"title={form.get('title')!r}",
    )
    carry_hint = str(pg.get("carryHint") or "")
    w.rep.rec(
        "㉞ 界面说清了这几个值是「带过来的」（不让用户以为是服务端知道他填过）",
        "发布货源" in carry_hint,
        f"carryHint={carry_hint!r}",
    )
    w.rep.rec(
        "㉞ 唯一可用目标被自动选中（只有一个才直接选；多个必须问用户，见第三节）",
        pg.get("needPick") is False and str(pg.get("orgId") or "") != "",
        f"needPick={pg.get('needPick')} orgId={pg.get('orgId')!r} orgName={pg.get('orgName')!r}",
    )

    t_title = w.c.input_text('[data-field="title"]', INTAKE_TITLE_OK)
    time.sleep(0.9)
    pg_in = w.c.page_data()
    w.rep.rec(
        "㉞ 真实输入触发 `bindinput`（标题进了页面 data，不是只写进渲染层）",
        bool(t_title) and (pg_in.get("form") or {}).get("title") == INTAKE_TITLE_OK,
        f"input={t_title} title={(pg_in.get('form') or {}).get('title')!r}",
    )
    w.shot("34-3-受理屏-已填")

    dep_d = len(w.c.page_stack())
    t_submit = w.c.tap('[data-act-submit-intake="1"]')
    ok_detail = w.c.wait_path(DETAIL, 60)
    time.sleep(2.0)
    pg_det = w.wait_data(lambda x: x.get("view") == "ready" and bool(x.get("detail")), tries=30)
    w.shot("34-4-提交后-委托详情")
    detail = pg_det.get("detail") or {}
    new_id = str(pg_det.get("assignmentId") or "")
    w.rep.rec(
        "㉞ **replace（真实入口）**：提交成功后落到该委托详情，且**栈深不增**"
        "——返回键回不到一张已经提交过的表单",
        bool(t_submit and ok_detail) and len(w.c.page_stack()) == dep_d,
        f"{dep_d} → {len(w.c.page_stack())}（tap={t_submit} path={w.c.current_path()}）",
    )
    w.rep.rec(
        "㉞ 详情页显示的是刚提交的那张单（标题 = 本页填的那一句）",
        detail.get("title") == INTAKE_TITLE_OK and new_id != "",
        f"assignmentId={new_id!r} title={detail.get('title')!r}",
    )
    w.rep.rec(
        "㉞ 提交后状态为「待受理」（受理是显式动作，不会自动发生）",
        detail.get("status") == "submitted",
        f"status={detail.get('status')!r} label={detail.get('statusLabel')!r}",
    )
    w.rep.rec(
        "㉞ 详情页写明委托给了哪个组织（不是「未指定组织」）",
        bool(str(detail.get("orgId") or "")),
        f"orgId={detail.get('orgId')!r} orgText={detail.get('orgText')!r}",
    )
    key_after = draft_key()
    w.rep.rec(
        "㉞ 提交成功后在途载荷被清除（留着的话下次进来会续接一张**已经提交过**的草稿："
        "界面说「内容已锁定」、提交却被服务端以状态冲突拒绝，用户既改不了也提交不了）",
        key_after != "" and draft_raw(key_after) == "",
        f"key={key_after!r} raw={draft_raw(key_after)[:60]!r}",
    )

    # ===================== 二、草稿锁定 → 续接 → 不重复建单 =====================
    print("\n-- 二、草稿锁定 → 换实例续接 → 提交不重复建单 --", flush=True)
    clear_draft()
    w.c.nav("switchTab", "/" + SHIPPER, SHIPPER)
    time.sleep(1.6)
    # ⚠️ 这一跳**必须**与第一节共用 `open_cargo_entry()` / `to_intake()`：
    #    首跑时这里硬点了 `[data-key='publish']`，而第一节已经实测该锚点命中 0
    #    （自定义 tabBar 是组件）⇒ 本前置断言必然失败，读起来像"续接没生效"，
    #    实际是**链路根本没进去**，还连带把在途载荷那几条压成 NOT_RUN。
    ok1, via1 = open_cargo_entry()
    t2, ok2, _ = to_intake(carry=True, qty="88")
    w.rep.rec(
        "㉞ 第二节前置 · 再次进入受理屏（身份不变；进入方式见 note —— 它是证据等级）",
        bool(ok1 and t2 and ok2),
        f"path={w.c.current_path()}｜发布货源页：{via1}｜受理屏：真实点击={t2}",
    )
    if not ok2:
        w.rep.not_run("㉞ 在途载荷相关断言", "未再次进入受理屏")
    else:
        pg_lock = w.wait_data(lambda x: x.get("view") == "ready", tries=30)
        w.rep.rec(
            "㉞ 第二节前置 · 受理屏 ready（授权清单到达）",
            pg_lock.get("view") == "ready",
            f"view={pg_lock.get('view')!r} hint={pg_lock.get('viewHint')!r}",
        )
        w.c.input_text('[data-field="title"]', INTAKE_TITLE_LOCK)
        time.sleep(0.9)
        key_fill = draft_key()
        raw_fill = draft_raw(key_fill)
        w.rep.rec(
            "㉞ 只填了内容（草稿还没建）就已经落盘 —— 否则「填到一半被杀掉」会连内容带意图一起丢",
            key_fill != "" and INTAKE_TITLE_LOCK in raw_fill,
            f"key={key_fill!r} 含标题={'是' if INTAKE_TITLE_LOCK in raw_fill else '否'}",
        )

        # ⚠️ 这里**故意**用方法调用而不是点击：真实点击「提交委托」只有两种结局
        # （两步都成 / 在 ensureDraft 之前就被前置检查拦下），造不出「草稿已建、提交未成」
        # 这个中间态。证据等级低于点击，已在末尾按 limitation 登记。
        w.c.call_method(
            "ensureDraft",
            [
                {
                    "title": INTAKE_TITLE_LOCK,
                    "cargo_summary": INTAKE_CARRY_NAME,
                    "quantity": "88",
                    "quantity_unit": "吨",
                }
            ],
        )
        time.sleep(2.0)
        pg_locked = w.c.page_data()
        cid = str(pg_locked.get("createdId") or "")
        w.rep.rec(
            "㉞ [方法调用·证据等级低于点击] 草稿建立后页面进入锁定态，并拿到编号与版本",
            pg_locked.get("locked") is True
            and cid != ""
            and int(pg_locked.get("draftRevision") or 0) > 0,
            f"locked={pg_locked.get('locked')} createdId={cid!r} "
            f"rev={pg_locked.get('draftRevision')!r}",
        )
        if not cid:
            w.rep.not_run("㉞ 锁定 / 续接相关断言", "未拿到 createdId（ensureDraft 未生效）")
        else:
            key_lock = draft_key()
            raw_lock = draft_raw(key_lock)
            has4 = all(
                t in raw_lock for t in ("createKey", "submitKey", "createdId", "draftRevision")
            )
            w.rep.rec(
                "㉞ 在途载荷含**两把**幂等键 + 编号 + 版本"
                "（重试必须原样复用；重新取数会让「重试」变成「同键异体」409）",
                has4 and cid in raw_lock,
                f"key={key_lock!r} 四字段齐={has4} 含编号={cid in raw_lock}",
            )

            before_lock = str((w.c.page_data().get("form") or {}).get("title") or "")
            w.c.input_text('[data-field="title"]', "锁定后尝试改写")
            time.sleep(0.9)
            after_lock = str((w.c.page_data().get("form") or {}).get("title") or "")
            w.rep.rec(
                "㉞ 锁定后**真实**往标题框输入不会改变内容"
                "（模板 `disabled` + 处理函数在 `locked` 时早退，两层至少一层真的在挡）",
                before_lock == INTAKE_TITLE_LOCK and after_lock == INTAKE_TITLE_LOCK,
                f"{before_lock!r} → {after_lock!r}",
            )

            note = text_of(".draft-note")
            w.rep.rec(
                "㉞ 界面告知草稿已在服务端、内容已锁定，并给出「重新填写」出口",
                cid in note and "重新填写" in note,
                f"draft-note={note!r}",
            )
            n_restart = w.c.count('[data-act-intake-restart="1"]')
            w.rep.rec(
                "㉞ 「重新填写」入口可被唯一命中（与「提交」不是同类元素，仍单独登记锚点，"
                "防止模板改动把锚点悄悄弄丢）",
                n_restart == 1,
                f"命中 {n_restart} 个",
            )
            w.shot("34-5-受理屏-草稿已锁定")

            saved_id = cid
            saved_ck = str(pg_locked.get("createKey") or "")
            w.c.navigate("reLaunch", "/" + INTAKE)
            time.sleep(2.6)
            pg_resume = w.wait_data(lambda x: x.get("view") == "ready", tries=30)
            w.shot("34-6-换实例重进-已续接")
            resume_id = str(pg_resume.get("createdId") or "")
            resume_ck = str(pg_resume.get("createKey") or "")
            w.rep.rec(
                "㉞ **换页面实例**重进后自动续接同一张草稿（编号与创建键都复原）——"
                "「响应丢失后重进会重复建草稿」这条余量修没修，看的就是这一条",
                resume_id == saved_id and resume_ck == saved_ck,
                f"createdId {saved_id!r} → {resume_id!r} / "
                f"createKey 一致={'是' if resume_ck == saved_ck else '否'}",
            )
            w.rep.rec(
                "㉞ 续接后仍处于锁定态，且内容就是用户填过的那一份",
                pg_resume.get("locked") is True
                and str((pg_resume.get("form") or {}).get("title") or "") == INTAKE_TITLE_LOCK,
                f"locked={pg_resume.get('locked')} "
                f"title={(pg_resume.get('form') or {}).get('title')!r}",
            )
            w.rep.rec(
                "㉞ 续接时界面明确说明这是续接来的（静默恢复会让用户以为自己上次已经提交成功了）",
                "已续接" in str(pg_resume.get("carryHint") or ""),
                f"carryHint={pg_resume.get('carryHint')!r}",
            )
            t3 = w.c.tap('[data-act-submit-intake="1"]')
            ok3 = w.c.wait_path(DETAIL, 60)
            time.sleep(2.0)
            pg_r = w.wait_data(
                lambda x: x.get("view") == "ready" and bool(x.get("detail")), tries=30
            )
            w.shot("34-7-续接后提交-同一张委托")
            w.rep.rec(
                "㉞ 续接后**真实点击**提交，落到**同一张**委托的详情 ⇒ 服务端没有多出一张草稿"
                "（`ensureDraft` 复用了编号与版本，这正是修掉重复提交的那一处）",
                bool(t3 and ok3) and str(pg_r.get("assignmentId") or "") == saved_id,
                f"续接编号 {saved_id!r} / 详情编号 {pg_r.get('assignmentId')!r}",
            )

    # ===================== 三、授权给多个组织时不许替用户猜 =====================
    print("\n-- 三、授权给多个组织时不许替用户猜 --", flush=True)
    path = w.login_as(CODE_SHIPPER_ORGPICKER)
    if path != INDEX or not w.enter_role("shipper", SHIPPER):
        w.rep.rec(
            "㉞ 前置 · seed-shipper-orgpicker 登录并进入货主工作台",
            False,
            f"path={w.c.current_path()}",
        )
    else:
        clear_draft()
        w.c.nav("reLaunch", "/" + INTAKE, INTAKE)
        time.sleep(2.2)
        pg_multi = w.wait_data(lambda x: x.get("view") == "ready", tries=30)
        w.shot("34-8-多目标-未选")
        w.rep.rec(
            "㉞ 授权给两个组织时**不预选**、要求用户显式选择"
            "（`needPick` 为真且 `orgId` 为空）——平台不替你决定交给谁",
            pg_multi.get("needPick") is True and not str(pg_multi.get("orgId") or ""),
            f"needPick={pg_multi.get('needPick')} orgId={pg_multi.get('orgId')!r} "
            f"targets={len(pg_multi.get('targets') or [])}",
        )
        n_pill = w.c.count(".pick-pill")
        w.rep.rec(
            "㉞ 每个可用目标渲染成一颗可选 pill（数量和授权条数一致）",
            n_pill == len(pg_multi.get("targets") or []) and n_pill >= 2,
            f"pill={n_pill} targets={len(pg_multi.get('targets') or [])}",
        )
        first_org = str(((pg_multi.get("targets") or [{}])[0] or {}).get("orgId") or "")
        t_pick = w.c.tap(f'[data-org-id="{first_org}"]')
        time.sleep(1.0)
        pg_pick = w.c.page_data()
        w.shot("34-9-多目标-已选")
        w.rep.rec(
            "㉞ 真实点击目标 pill 后选中态落到 data（组织 id 与名称同时更新）",
            bool(t_pick)
            and str(pg_pick.get("orgId") or "") == first_org
            and bool(str(pg_pick.get("orgName") or "")),
            f"tap={t_pick} orgId={pg_pick.get('orgId')!r} orgName={pg_pick.get('orgName')!r}",
        )

    # ===================== 四、空态：没有可委托的服务主体 =====================
    print("\n-- 四、空态：没有可委托的服务主体 --", flush=True)
    path = w.login_as(CODE_MGR_MULTI)
    if path != INDEX or not w.enter_role("shipper", SHIPPER):
        w.rep.rec("㉞ 前置 · seed-mgr-multi 登录并进入货主工作台", False, w.c.current_path())
    else:
        clear_draft()
        w.c.nav("reLaunch", "/" + INTAKE, INTAKE)
        time.sleep(2.2)
        pg_empty = w.wait_data(lambda x: x.get("view") == "empty", tries=30)
        w.shot("34-10-空态")
        w.rep.rec(
            "㉞ 五态之 empty：没有生效委托授权时是**单独一态**，不是笼统的「没有数据」",
            pg_empty.get("view") == "empty"
            and INTAKE_EMPTY_TITLE in str(pg_empty.get("viewTitle") or ""),
            f"view={pg_empty.get('view')!r} title={pg_empty.get('viewTitle')!r}",
        )
        empty_hint = str(pg_empty.get("viewHint") or "")
        w.rep.rec(
            "㉞ 空态说清了下一步去哪儿解决（去找服务主体完成委托授权）",
            "授权" in empty_hint,
            f"hint={empty_hint[:60]!r}",
        )
        n_submit = w.c.count('[data-act-submit-intake="1"]')
        n_input = w.c.count(".form-input")
        w.rep.rec(
            "㉞ 空态下不渲染表单与提交入口（不给一个必然失败的按钮）",
            n_submit == 0 and n_input == 0,
            f"提交入口={n_submit} 输入框={n_input}",
        )
        w.rep.rec(
            "㉞ 空态下 targets 为空且没有残留的选中项（不继承上一个身份的选择）",
            not (pg_empty.get("targets") or []) and not str(pg_empty.get("orgId") or ""),
            f"targets={len(pg_empty.get('targets') or [])} orgId={pg_empty.get('orgId')!r}",
        )

    # ===================== 五、五态分支渲染（注入状态）=====================
    print("\n-- 五、五态分支渲染（注入状态；到达条件见 NOT_RUN）--", flush=True)
    w.rep.limitation(
        "㉞ 五态分支渲染用的是**注入状态**（`setData`），不是状态自然到达",
        "注入的只有 `view` / `viewTitle`：验的是「模板分支是否存在、文案对不对、"
        "该给的出口有没有给」；**到达条件**（真实 401 / 403 / 404 / 断网）见本节 NOT_RUN。",
    )
    branches = [
        ("loading", ".empty", "加载中", 0),
        ("expired", ".err-card", "登录已过期", 1),
        ("denied", ".err-card", "无查看权限", 0),
        ("error", ".err-card", "加载失败", 1),
        ("empty", ".err-card", INTAKE_EMPTY_TITLE, 0),
    ]
    for state, sel, want, want_btn in branches:
        w.c.set_data(
            {
                "view": state,
                "viewTitle": want,
                "viewHint": "（走查注入状态，非真实到达）",
            }
        )
        time.sleep(0.8)
        n_hit = w.c.count(sel)
        shown = want in text_of(sel)
        n_btn = w.c.count(".err-btn")
        w.rep.rec(
            f"㉞ 五态分支 · `{state}` 落在可见区且文案正确",
            n_hit == 1 and shown,
            f"{sel} 命中 {n_hit} / 文案含 {want!r}={shown}",
        )
        w.rep.rec(
            f"㉞ 五态分支 · `{state}` 的出口按钮与设计一致"
            f"（能靠重试 / 重登解决的才给按钮：{'有' if want_btn else '无'}）",
            n_btn == want_btn,
            f".err-btn={n_btn} 期望={want_btn}",
        )
        w.shot(f"34-11-五态-{state}")

    # ===================== 六、本节够不着的那几条（如实登记）=====================
    print("\n-- 六、诚实边界（不计入通过）--", flush=True)
    w.rep.limitation(
        "㉞ 「草稿已建、提交未成」这一中间态是用**方法调用**造出来的（不是真实点击）",
        "真实点击「提交委托」只有两种结局：两步都成，或在 `ensureDraft` 之前就被前置检查"
        "拦下（标题为空 / 组织未选）—— 造不出这个中间态。所以第二节那一步走的是 "
        "`call_method('ensureDraft')`，证据等级低于点击；断言读的仍是真机 data 与 storage。",
    )
    w.rep.limitation(
        "㉞ 「重新填写」/「放弃」在**确认后**清掉在途载荷",
        "两条都走原生 `wx.showModal`，确认键不在渲染树里、工具点不到 ⇒ 本节只证了"
        "入口存在、锚点唯一、提示文案正确，**不证明**点确认后清键。清理函数本身另有静态"
        "断言（verify_ui_interactions.js ⑩ 章）与「提交成功后载荷被清」的实机证据（第一节末）。",
    )
    w.rep.not_run(
        "㉞ 五态中 expired / denied / error 的**到达条件**",
        "需要真实的 401 / 403 / 404 / 网络中断：401 要等登录态自然过期（不可控），"
        "403 / 404 要在请求飞行中撤掉授权（本切片没有撤权入口），断网要改运行环境。"
        "第五节已用注入状态验证这三种的**分支渲染**，到达条件未取证。",
    )
    w.rep.not_run(
        "㉞ 并发双写（两端同时提交只应成功一次）",
        "单模拟器只有一个页面实例，做不出两个并发写者。该路径的取证在后端层："
        "backend/tests/test_entrust_s1_exit_criteria.py 的 "
        "`test_claim_race_only_one_writer_wins`（条件 UPDATE 的 rowcount 为 1 / 0）与 "
        "`test_claim_race_loser_cannot_overwrite_via_service_layer`。",
    )
    w.rep.not_run(
        "㉞ 真·跨进程续接（杀掉小程序但保留 storage）",
        "工具侧只能做到「换页面实例」（`reLaunch`，已 PASS）；`remove_storage` 会把要验的"
        "载荷一起删掉，无法表达「进程没了、storage 还在」。这一条只到「换实例仍续接」，"
        "进程级未取证。",
    )


def sec_35(w: Walker) -> None:
    """㉟ S1 出口判据 ②③④ 的**设备侧**取证。

    为什么另起一章、且换一条支线
    ----------------------------
    ㉞ 章验的是**客户侧**（能不能建草稿并提交），数据落在 `seed-shipper` 名下、
    目标是「演示经营主体·工作台」。本判据要的是**组织侧**：这张单进的是**哪个**
    队列、谁能受理、谁看不到。所以本章用 `seed-shipper-orgpicker`（在**甲**组织
    持有 `entrust:view` + `entrust:assignment:claim` 授权）把单提交到**甲**组织，
    再用甲组织的经理身份去看。三句话都要由设备侧证据回答：

    * ② `appears in the correct queue` —— 队列里出现的是**组织**的那一张；
    * ③ `can be claimed once` —— 队列上真实点击受理成功；别人抢先受理后，界面上
      迟到的那一下拿 **409** 并刷新成服务端的真实状态；
    * ④ `inaccessible to unrelated organization B` —— 仅乙组织的经理在队列里看不到
      它，直访详情拿 404。

    为什么"乙组织不可见"必须用 `seed-mgr-only-b`（不是 `seed-mgr-multi`）
    ------------------------------------------------------------------
    详情可见性判据是「该委托的组织 ∈ 调用者的**任一**组织」，而 `seed-mgr-multi`
    同时属于甲、乙 ⇒ 它切到乙视角照样看得到甲的单，拿它验这条会得到**假绿**。
    `seed-mgr-only-b`（仅乙，随本章补进种子）才验得出来。

    ⚠️ 本章**会真写**：两张委托（界面真实点击提交）+ 一次受理。故排在默认顺序末尾，
    且断言一律按 **assignment_id** 定位（不按标题、不按位置），历史数据再多也不干扰。
    """
    print(
        "\n== ㉟ S1 出口判据 ②③④：队列可见 / 受理 / 并发 409 / 乙组织不可见 ==",
        flush=True,
    )
    base_err = w.c.errors()

    def clear_draft_state() -> None:
        """清掉当前身份留在 storage 的在途载荷。

        ⚠️ 不清的话，上一次跑剩下的草稿会被受理屏**续接**，"新提交一张"就变成
        "续接旧的那张"，两张单实际是同一张 —— 那种失败看起来像"服务端没建单"。
        """
        uid = w.c.evaluate(
            "function(){var r=wx.getStorageSync('user_info');if(!r)return '';"
            "var u=null;try{u=JSON.parse(r)}catch(e){return ''}"
            "return (u&&u.user_id!=null)?String(u.user_id):'';}"
        )
        if uid:
            w.c.remove_storage("entrust_intake_draft_" + str(uid))

    def submit_to_org_a(title: str) -> str:
        """受理屏选**甲组织** → 真实输入标题 → 真实点击提交；返回新单 id（失败给空串）。"""
        w.c.nav("reLaunch", "/" + INTAKE, INTAKE)
        time.sleep(2.4)
        pg = w.wait_data(lambda x: x.get("view") in ("ready", "empty"), tries=30)
        if pg.get("view") != "ready":
            return ""
        org_a_id = ""
        for t in pg.get("targets") or []:
            if str((t or {}).get("orgName") or "") == ORG_A:
                org_a_id = str((t or {}).get("orgId") or "")
                break
        if not org_a_id:
            return ""
        # 双授权 ⇒ `needPick` 为真且不预选（㉞ 第三节已验），这里必须**显式选甲**。
        if pg.get("needPick"):
            w.c.tap(f'[data-org-id="{org_a_id}"]')
            time.sleep(0.9)
        w.c.input_text('[data-field="title"]', title)
        time.sleep(0.9)
        w.c.tap('[data-act-submit-intake="1"]')
        w.c.wait_path(DETAIL, 60)
        time.sleep(2.2)
        pd = w.wait_data(lambda x: x.get("view") == "ready" and bool(x.get("detail")), tries=30)
        return str(pd.get("assignmentId") or "")

    # ============ 一、把两张单经界面提交到甲组织（本章的取证数据源）============
    print("\n-- 一、界面提交到甲组织 --", flush=True)
    path = w.login_as(CODE_SHIPPER_ORGPICKER)
    if path != INDEX or not w.enter_role("shipper", SHIPPER):
        w.rep.rec(
            "㉟ 前置 · seed-shipper-orgpicker 登录并进入货主工作台",
            False,
            w.c.current_path(),
        )
        w.rep.not_run("㉟ 全部断言", "前置登录失败，链路断在这里")
        return

    clear_draft_state()
    id_race = submit_to_org_a(INTAKE_TITLE_RACE)
    clear_draft_state()
    id_queue = submit_to_org_a(INTAKE_TITLE_QUEUE)
    w.rep.rec(
        "㉟ 前置 · 两张委托都经**界面真实点击**提交到甲组织"
        "（合同要的是 a fresh **UI-created** assignment，不能用 API 代劳）",
        bool(id_race) and bool(id_queue) and id_race != id_queue,
        f"race={id_race!r} queue={id_queue!r}",
    )
    if not (id_race and id_queue):
        w.rep.not_run("㉟ 队列与受理相关断言", "界面提交未成功，链路断在这里")
        return
    w.shot("35-1-界面提交完成")

    # ============ 二、甲组织经理（单组织）进工作台 ============
    if not w.open_workbench(CODE_MGR_SINGLE, tag="㉟"):
        w.rep.not_run("㉟ 队列与受理相关断言", "甲组织经理未能进入工作台")
        return
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)

    # ============ 三、② appears in the correct queue ============
    print("\n-- 三、② 出现在正确的队列 --", flush=True)
    by_id = {str(x.get("assignmentId")): x for x in (d.get("items") or [])}
    row_q = by_id.get(id_queue) or {}
    w.rep.rec(
        "㉟ ② 刚提交的那张出现在**组织**队列里（`view=org`、按 org_id 限定的那个队列）",
        bool(row_q),
        f"队列 total={d.get('total')} 命中 queue={bool(row_q)} race={bool(by_id.get(id_race))}",
    )
    w.rep.rec(
        "㉟ ② 队列里的状态是「待受理」——受理是显式动作，进队列不会自动发生",
        row_q.get("status") == "submitted",
        f"status={row_q.get('status')!r} label={row_q.get('statusLabel')!r}",
    )
    sel_claim_q = f'[data-act-claim="{id_queue}"]'
    n_claim_q = w.c.count(sel_claim_q)
    w.rep.rec(
        "㉟ ② 待受理的卡上有「受理」入口且锚点**唯一命中**"
        "（多张卡都有这个按钮 ⇒ 必须按 id 定位，工具没有 index 参数）",
        n_claim_q == 1,
        f"命中 {n_claim_q}",
    )
    w.shot("35-2-组织队列-刚提交的那张")

    # ============ 四、③ 队列上真实点击受理（正例）============
    print("\n-- 四、③ 队列上受理（真实点击）--", flush=True)
    w.scroll_into(sel_claim_q)
    t_ask = w.c.tap(sel_claim_q)
    time.sleep(0.9)
    d_ask = w.c.page_data()
    w.rep.rec(
        "㉟ ③ 点「受理」在**页内**展开确认条（刻意不用原生弹层 —— 弹层不在渲染树里、"
        "工具点不到它的确认键，受理这条唯一改变业务状态的链路就永远拿不到设备证据）",
        bool(t_ask) and d_ask.get("claimOpenId") == id_queue,
        f"tap={t_ask} claimOpenId={d_ask.get('claimOpenId')!r} 期望={id_queue!r}",
    )
    sel_sub_q = f'[data-act-claim-submit="{id_queue}"]'
    n_sub_q = w.c.count(sel_sub_q)
    w.rep.rec(
        "㉟ ③ 确认条里「确认受理」锚点唯一命中（与「取消」是两个不同属性，"
        "共用一个属性名会让两者在断言里同形）",
        n_sub_q == 1,
        f"命中 {n_sub_q}",
    )
    w.shot("35-3-队列-确认条已展开")

    t_sub = w.c.tap(sel_sub_q)
    w.wait_data(
        lambda x: any(
            str(i.get("assignmentId")) == id_queue and i.get("status") == "claimed"
            for i in (x.get("items") or [])
        ),
        tries=40,
        gap=0.5,
    )
    time.sleep(1.0)
    tok_single = (api_login(CODE_MGR_SINGLE) or {}).get("access_token") or ""
    me_single = api_get("/auth/me", tok_single) or {}
    uid_single = str(me_single.get("id") or me_single.get("user_id") or "")
    truth_q = api_get(f"/entrust/assignments/{id_queue}", tok_single) or {}
    w.rep.rec(
        "㉟ ③ 受理成功，且**服务端真相**对得上：状态已受理、受理人＝这名甲组织经理"
        "（不只看界面渲染的那一行字）",
        bool(t_sub)
        and truth_q.get("status") == "claimed"
        and uid_single != ""
        and str(truth_q.get("claimed_by") or "") == uid_single,
        f"tap={t_sub} status={truth_q.get('status')!r} "
        f"claimed_by={truth_q.get('claimed_by')!r} 期望={uid_single!r}",
    )
    n_claim_after = w.c.count(sel_claim_q)
    w.rep.rec(
        "㉟ ③ 受理后那张卡**不再有**受理入口（界面上不留一个必然 409 的按钮）",
        n_claim_after == 0,
        f"命中 {n_claim_after}",
    )
    w.shot("35-4-受理成功")

    # ============ 五、③ 并发负例：别人抢先受理，界面那一下拿 409 ============
    print("\n-- 五、③ 并发负例：外部先受理，界面迟到的那一下 --", flush=True)
    sel_claim_r = f'[data-act-claim="{id_race}"]'
    w.scroll_into(sel_claim_r)
    t_ask_r = w.c.tap(sel_claim_r)
    time.sleep(0.9)
    d_race = w.c.page_data()
    w.rep.rec(
        "㉟ ③ 并发就位：界面上已展开另一张的确认条（**尚未**点确认）",
        bool(t_ask_r) and d_race.get("claimOpenId") == id_race,
        f"tap={t_ask_r} claimOpenId={d_race.get('claimOpenId')!r} 期望={id_race!r}",
    )
    tok_multi = (api_login(CODE_MGR_MULTI) or {}).get("access_token") or ""
    st_race, _ = api_post(
        f"/entrust/assignments/{id_race}/claim",
        tok_multi,
        {},
        idem_key=f"walk35-race-{int(time.time() * 1000)}",
    )
    w.rep.rec(
        "㉟ ③ 并发前置：**另一名**甲组织经理经 API 抢先受理成功"
        "（这一步刻意不走界面 —— 单模拟器做不出第二个界面实例）",
        st_race in (200, 201),
        f"HTTP {st_race}",
    )
    t_late = w.c.tap(f'[data-act-claim-submit="{id_race}"]')
    time.sleep(3.4)
    d_late = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=30)
    late_row = next(
        (x for x in (d_late.get("items") or []) if str(x.get("assignmentId")) == id_race),
        {},
    )
    n_claim_race = w.c.count(sel_claim_r)
    w.shot("35-5-并发-迟到的那一下之后")
    w.rep.rec(
        "㉟ ③ 迟到的那一下拿 **409**：队列被刷新成**服务端的真实状态**"
        "（该卡已受理、受理入口消失）—— 不是静默失败，也没把界面留在「点了没反应」",
        bool(t_late) and late_row.get("status") == "claimed" and n_claim_race == 0,
        f"tap={t_late} status={late_row.get('status')!r} 受理入口={n_claim_race}",
    )

    # ============ 六、④ inaccessible to unrelated organization B ============
    print("\n-- 六、④ 无关组织乙看不到 --", flush=True)
    if not w.open_workbench(CODE_MGR_ONLY_B, tag="㉟"):
        w.rep.not_run("㉟ ④ 乙组织不可见", "仅乙组织的经理未能进入工作台")
    else:
        # 仅乙组织经理的 token（**纯 API 调用，不动界面**）：本节要用它做
        # 「界面看不到」之外的**第二层直证** —— 界面证据可能被分页/筛选偶然
        # 掩盖，服务端载荷不会。
        tok_only_b = (api_login(CODE_MGR_ONLY_B) or {}).get("access_token") or ""
        d_b = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
        b_items = d_b.get("items") or []
        b_ids = [str(x.get("assignmentId")) for x in b_items]
        b_titles = [str(x.get("title") or "") for x in b_items]
        w.rep.rec(
            "㉟ ④ 仅乙组织的经理，队列里**看不到**甲组织的那两张"
            "（出口判据原文：remains inaccessible to unrelated organization B）",
            id_queue not in b_ids and id_race not in b_ids,
            f"乙队列 total={d_b.get('total')} 含甲单={id_queue in b_ids or id_race in b_ids}",
        )
        w.rep.rec(
            "㉟ ④ 但**看得到乙组织自己的**委托 —— 证明上一条是「跨组织隔离」，"
            "而不是「这个身份什么都看不到」那种假隔离",
            TITLE_B in b_titles,
            f"乙队列标题={b_titles[:4]}",
        )
        # 第二层直证：**服务端载荷**里也不含甲的两张。界面那一层可能被分页或
        # 前端筛选偶然掩盖（"没显示"不等于"没返回"），载荷不会。
        #
        # ⚠️ `view=org` **必须**带 `org_id`：后端明确不提供「我所属全部组织」这种
        #    无范围查询（DR-0014 §3.1，HO 禁止跨组织拼接），缺了它不是返回空表
        #    而是 400/422。这里用**界面正在用的那个组织**（页面 state 的
        #    `activeOrgId`）—— 与界面同源，避免我在测试侧重新解析一遍组织 id，
        #    两边解析逻辑一旦分叉就会互相掩盖。
        org_b = str(d_b.get("activeOrgId") or "")
        org_list_b = (
            api_get(f"/entrust/assignments?view=org&org_id={org_b}&size=50", tok_only_b) or {}
        )
        rows_b = org_list_b.get("items") or []
        # 后端 schema 是 `AssignmentOut.assignment_id`（已核对 `schemas.py`），
        # **不是** `id` / `assignmentId`。字段名写错会让 ids_b 变成空集 ——
        # 靠下面的 `len(ids_b) > 0` 前置拦住，否则 `id not in set()` **恒真**，
        # 就是一条永远通过的假绿断言。
        ids_b = {str((r or {}).get("assignment_id") or "") for r in rows_b}
        ids_b.discard("")
        w.rep.rec(
            "㉟ ④ 同一队列由 **API 直证**：`view=org&org_id=<界面当前组织>` 的服务端载荷里"
            "也不含甲组织那两张的 id（先断言载荷非空 —— 否则字段名不符时 `not in` 恒真，是假绿）",
            len(ids_b) > 0 and id_queue not in ids_b and id_race not in ids_b,
            f"org_id={org_b!r} 载荷行数={len(ids_b)} ids={sorted(ids_b)[:6]}",
        )
        w.shot("35-6-乙组织队列")
        w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={id_queue}", DETAIL)
        time.sleep(2.8)
        pd_b = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=30)
        # ⚠️ 分层取证（2026-09-16 修正）：首跑时这两层被混成**一条**断言 —— 文案写
        #    「服务端 404」，证据却只是 `view == 'denied'`。而 `utils/entrust.js`
        #    把 **400 / 403 / 404 三种都映射成 denied**，单看界面**区分不出**是哪一种：
        #    那是**超证**（断言声称的比证据多）。现在拆成两条，各带各的证据。
        w.rep.rec(
            "㉟ ④ 乙组织的经理**直访**甲组织委托详情 ⇒ 界面呈现为「拒绝」态"
            "（不是被渲染成「没有数据」那种业务空态，也不是白屏）",
            pd_b.get("view") == "denied",
            f"view={pd_b.get('view')!r} title={pd_b.get('viewTitle')!r} "
            f"hint={pd_b.get('viewHint')!r}",
        )
        # 直证：**状态码**。⚠️ 服务端对「开关关闭 / 不存在 / 非参与方」**刻意同码 404**
        #    （不泄漏存在性）⇒ 这里证的是「被服务端拒绝、且不泄漏存在性」，
        #    **不是**「这张委托不存在」。两者不可互换，文案也不许写成后者。
        code_b = api_status(f"/entrust/assignments/{id_queue}", tok_only_b)
        w.rep.rec(
            "㉟ ④ 同一访问由 **API 直证**：服务端 HTTP **404**"
            "（与「开关关闭 / 不存在」刻意同码、不泄漏存在性 ⇒ 界面**只能**说"
            "「功能未开放」，不得替服务端下「这张委托不存在」的结论）",
            code_b == 404,
            f"HTTP={code_b}（0＝请求未发出，不算被拒）",
        )
        w.shot("35-7-乙组织直访甲组织详情")

    # ============ 七、诚实边界（不计入通过）============
    print("\n-- 七、诚实边界（不计入通过）--", flush=True)
    w.rep.rec(
        "㉟ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        str(w.new_errors(base_err))[:200],
    )
    w.rep.limitation(
        "㉟ 并发负例里的「另一个写者」是**同进程的 API 调用**，不是第二个真机客户端",
        "单模拟器只有一个页面实例。本章证的是「界面上那一下迟到时拿 409、并把队列"
        "刷新成真实状态」这条**界面行为**；「两个真机同时提交只成功一次」的判据在"
        "后端层（`test_entrust_s1_exit_criteria.py` 的条件 UPDATE rowcount 用例）。"
        "两者是不同的东西，不能互相顶替。",
    )
    # ⚠️ 本条原是一条 LIMITATION：「成员（只读）身份也会看到受理按钮」。
    #    D-4 裁定（2026-09-16，**采纳**）之后判据已改成
    #    「可认领状态 ∧ 该委托所属组织内的认领权限」⇒ 该口径**不再成立**。
    #    这里改记一条**已闭环**的说明而不是删掉：读过上一版结论的人要能顺着它找到
    #    去向（正面取证在 ㊱ 章，实现见 `miniapp/utils/entrust.js` 的 `canClaimAssignment`）。
    #    ⚠️ 它**不是**在说"原问题通过了" —— 原条描述的是**修正前**的行为，
    #       两者是不同的事实，不能互相顶替。
    w.rep.rec(
        "㉟ 原 LIMITATION「member 也看得到受理按钮」已随 D-4 裁定**修正**"
        "（判据改为「可认领状态 ∧ 该委托所属组织的认领权限」）⇒ 本条只保留去向",
        True,
        "D-4 裁定 §1–§5；正面取证见 ㊱ 章；实现见 utils/entrust.js 的 canClaimAssignment()",
    )
    w.rep.not_run(
        "㉟ 受理后**迟到写入者**的终局（abandoned / lease_lost）在界面上的可见性",
        "那两个终局是服务端内部状态，本期没有面向经理的界面出口 ⇒ 真机侧无从观察。"
        "其断言完全在后端用例里（㉞ 章第五节亦同）。",
    )


def sec_36(w: Walker) -> None:
    """㊱ 受理入口的**展示判据**（D-4 裁定 §1–§5 的设备侧验收）。

    为什么另起一章
    --------------
    ㉟ 章验的是「队列可见性 / 能不能受理 / 并发 / 跨组织不可见」（出口判据 ②③④）；
    本章验的是**同一个动作的入口在什么条件下出现** —— D-4 裁定把
    `entrust:assignment:claim` 的**展示判断**授权给前端，同时立了六条约束。
    它需要一组 ㉟ 章没有的身份对照，所以另起一章；并且**接替** ㉟ 章第七条那条
    「member 也看得到受理按钮」的 LIMITATION（该口径已被本裁定修正）。

    为什么**零写入**（不提交、也不受理任何委托）
    ------------------------------------------
    只用种子里两张稳定样本：甲 `TITLE_A`、乙 `TITLE_B`（都要求 `submitted`）。
    理由：种子的 `_submitted_assignment` 按 `(owner, title)` 查**唯一行**，若该行不是
    `submitted` 就调 `submit_assignment` 重新提交 —— 而 `claimed` 的单**不能**重新提交
    （服务端回"只有草稿可提交"）⇒ **一旦受理掉，下一次跑种子就会抛异常**。
    所以裁定图 2 第 2 行的"正常受理"复用 ㉟ 章第三节的实证，本章不重复消耗数据。

    ⭐ 双向对照（本章最有价值的一条）
    -------------------------------
    要证明的是「按钮的出现与否取决于**该委托所属组织内的权限**」。单一方向的观察
    都留有漏洞，所以两向都做：

      对照 1（同一身份、两个队列）：`seed-mgr-multi`（甲 = manager / 乙 = member；
              种子自述它是 DR-0008「跨组织权限不得并集」的回归样本）——
              甲队列的单**有**入口、乙队列的单**无**。
              堵掉的漏洞：「这个身份本来就不显示按钮」。
      对照 2（同一队列、两个身份）：乙队列分别由 `seed-mgr-multi`（乙 member）与
              `seed-mgr-only-b`（乙 manager）看 —— 前者**无**、后者**有**。
              堵掉的漏洞：「乙组织的单因为别的原因（状态/数据）刚好不显示」。

    两向交叉之后，"差异来自组织权限"这个解释就没有对手了。
    """
    print("\n== ㊱ 受理入口判据（D-4）：组织权限 · 双向对照 · 403 ==", flush=True)
    base_err = w.c.errors()
    claim_perm = "entrust:assignment:claim"

    # ============ 一、API 直证：判据的数据源（/my-orgs）确实按组织分域 ============
    print("\n-- 一、/my-orgs 的权限投影：按组织分域 --", flush=True)
    tok_multi = (api_login(CODE_MGR_MULTI) or {}).get("access_token") or ""
    rows = (api_get("/entrust/my-orgs", tok_multi) or {}).get("items") or []
    # 按**组织名**定位（id 自增、重跑会变；名字是种子里写死的常量）
    by_name = {str((r or {}).get("name") or ""): (r or {}) for r in rows}
    oa, ob = by_name.get(ORG_A) or {}, by_name.get(ORG_B) or {}
    org_a_id, org_b_id = str(oa.get("org_id") or ""), str(ob.get("org_id") or "")
    perm_a = [str(p) for p in (oa.get("permissions") or [])]
    perm_b = [str(p) for p in (ob.get("permissions") or [])]

    w.rep.rec(
        "㊱ 前置：`seed-mgr-multi` 的两条身份都取到，且角色正是 甲=manager / 乙=member"
        "（本章对照设计的根基 —— 角色若被别的章节改走，结论就不成立）",
        bool(org_a_id)
        and bool(org_b_id)
        and str(oa.get("member_role")) == "manager"
        and str(ob.get("member_role")) == "member",
        f"甲={oa.get('member_role')}#{org_a_id} / 乙={ob.get('member_role')}#{org_b_id}",
    )
    w.rep.rec(
        "㊱ ①③ **API 直证**：`/my-orgs` 里甲含 `entrust:assignment:claim`、乙**不含** "
        "—— 这就是 D-4 判据的数据源，它必须按组织分域（跨组织并集是已修过的越权）",
        claim_perm in perm_a and claim_perm not in perm_b,
        f"甲={perm_a} / 乙={perm_b}",
    )
    # ⚠️ 先证"有"，再证"无"：乙的投影若为空集，"不含 claim" 只是没取到，那是假绿。
    w.rep.rec(
        "㊱ 乙组织的权限投影**非空**（否则「不含 claim」可能只是没取到 —— 假绿）",
        len(perm_b) > 0,
        f"乙={perm_b}",
    )

    # `find_submitted` 已提到模块级（㊲ 章要用的**是同一份**实现 —— 各留一份副本的话，
    # 将来只改一处就没人发现，两章对"样本单"的判据会悄悄分叉）。
    id_a = find_submitted(org_a_id, TITLE_A, tok_multi)
    id_b = find_submitted(org_b_id, TITLE_B, tok_multi)
    w.rep.rec(
        "㊱ 前置：甲、乙各找到一张**标题匹配且 status=submitted** 的样本单",
        bool(id_a) and bool(id_b),
        f"甲#{id_a}（{TITLE_A}） / 乙#{id_b}（{TITLE_B}）",
    )
    if not (id_a and id_b):
        # 样本缺失时后面的界面断言全部无意义 —— 直接收口，别让它退化成"看起来没按钮"。
        w.rep.not_run(
            "㊱ ①②③ 受理入口的界面可见性",
            "种子样本单缺失（见上一条）。先跑 `backend/scripts/seed_entrust_orgpicker.py` "
            "再重跑本章；种子会保证这两张单处于 submitted。",
        )
        return

    # ============ 二、对照 1：同一身份看两个队列 ============
    print("\n-- 二、对照 1：同一身份（甲 manager / 乙 member）看两个队列 --", flush=True)
    # ⚠️ 必须**先清掉「上次选中的组织」**：`pickOrg` 有 `saved` 分支（沿用上次选择，
    #    ⑯ 章段一后半专门验过它），而这个 Storage 键在开发者工具里**跨 IDE 重启保留**
    #    —— 上一次走查（⑯ 章末尾点了「乙」）留下的值会让本段一进来就走 `saved`，
    #    于是"不预选"必然落空。首次真跑就是这么红的（`orgs=['2','3'] active='3'`）。
    #    这不是为了好过而放宽断言：saved 分支由 ⑯ 章单独覆盖，本段要的是 ambiguous 分支。
    #    （清两次＝抗一次无声失败，⑯ 章同法。）
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.remove_storage(ORG_STORAGE_KEY)  # 双保险：确认清掉上次选择
    if not w.open_workbench(CODE_MGR_MULTI, tag="㊱"):
        w.rep.not_run("㊱ 对照 1", "未能进入经理工作台")
    else:
        # 多组织 ⇒ pickOrg 分支 ambiguous ⇒ 顶栏给药丸且**不预选**，队列停在 denied 态。
        d0 = w.wait_data(lambda x: bool(x.get("orgReason")), tries=40, gap=0.5)
        w.rep.rec(
            "㊱ 前置：多组织身份进工作台**不预选**（顶栏出现组织药丸）"
            "—— 正是种子对 multi 预期的 ambiguous 分支",
            len(d0.get("orgs") or []) >= 2
            and d0.get("orgReason") == "ambiguous"
            and not d0.get("activeOrgId"),
            f"orgs={[o.get('orgId') for o in (d0.get('orgs') or [])]} "
            f"reason={d0.get('orgReason')!r} active={d0.get('activeOrgId')!r}",
        )
        # 药丸上的中文标签本身就是投影层的产物：甲应含「受理委托」、乙应只有「查看委托」。
        pill_a = next((o for o in (d0.get("orgs") or []) if str(o.get("orgId")) == org_a_id), None)
        pill_b = next((o for o in (d0.get("orgs") or []) if str(o.get("orgId")) == org_b_id), None)
        w.rep.rec(
            "㊱ ①③ 页面 data 里的组织权限标签：甲含「受理委托」、乙不含"
            "（与 API 那一层同源 —— 同一份 `ctx.permissions_in_org`）",
            bool(pill_a)
            and bool(pill_b)
            and any("受理委托" in str(x) for x in (pill_a.get("permissions") or []))
            and not any("受理委托" in str(x) for x in (pill_b.get("permissions") or [])),
            f"甲={pill_a and pill_a.get('permissions')} / 乙={pill_b and pill_b.get('permissions')}",
        )

        # —— 乙组织（member）：走 storage 选组织，比点药丸稳（⑯ 章同法）——
        w.c.set_storage(ORG_STORAGE_KEY, org_b_id)
        if w.reenter_workbench() != WORKBENCH:
            w.rep.rec("㊱ 对照 1 乙侧", False, "重进工作台失败")
        else:
            db = w.wait_data(
                lambda x: (
                    x.get("view") not in (None, "", "loading")
                    and str(x.get("activeOrgId")) == org_b_id
                ),
                tries=40,
                gap=0.5,
            )
            b_items = db.get("items") or []
            b_row = next((x for x in b_items if str(x.get("assignmentId")) == id_b), None)
            w.rep.rec(
                "㊱ ①③ 乙队列里**看得到**那张 submitted 样本单（**先证有** —— 否则"
                "「没有受理入口」会被「看不到这张单」顶替，那是完全不同的结论）",
                b_row is not None and b_row.get("status") == "submitted",
                f"队列={[str(x.get('assignmentId')) for x in b_items][:6]} 目标#{id_b}",
            )
            w.rep.rec(
                "㊱ ①③ 投影层：该卡 `canClaim` 为 **false**（乙只是 member，无认领权限）",
                bool(b_row) and b_row.get("canClaim") is False,
                f"canClaim={b_row and b_row.get('canClaim')!r}",
            )
            n_btn_b = w.c.count(f'[data-act-claim="{id_b}"]')
            w.rep.rec(
                "㊱ ① 界面上该卡**没有**受理按钮（图 2 第 1 行：同组织只读成员 ⇒ 无认领按钮）",
                n_btn_b == 0,
                f'[data-act-claim="{id_b}"] 命中 {n_btn_b}',
            )
            w.shot("36-1-乙组织无受理入口")

        # —— 甲组织（manager）：**真实点击药丸**切（真入口，不靠 storage）——
        t_pill = w.c.tap(f'[data-org="{org_a_id}"]')
        time.sleep(3.0)
        da = w.wait_data(
            lambda x: (
                x.get("view") not in (None, "", "loading") and str(x.get("activeOrgId")) == org_a_id
            ),
            tries=40,
            gap=0.5,
        )
        a_items = da.get("items") or []
        a_row = next((x for x in a_items if str(x.get("assignmentId")) == id_a), None)
        w.rep.rec(
            "㊱ ② 甲队列里同状态的样本单：`canClaim` 为 **true**"
            "（同一身份、同一页面、同一份代码 —— 差异只来自组织角色）",
            bool(a_row) and a_row.get("canClaim") is True,
            f"canClaim={a_row and a_row.get('canClaim')!r} tap={t_pill}",
        )
        n_btn_a = w.c.count(f'[data-act-claim="{id_a}"]')
        w.rep.rec(
            "㊱ ② 界面上该卡**有**受理按钮（图 2 第 2 行；受理本身已由 ㉟ 章第三节实证，"
            "本章不重复消耗这张单）",
            n_btn_a == 1,
            f'[data-act-claim="{id_a}"] 命中 {n_btn_a}',
        )
        w.shot("36-2-甲组织有受理入口")

    # ============ 三、对照 2：同一队列、换身份（乙 manager）============
    print("\n-- 三、对照 2：乙队列换身份（seed-mgr-only-b，乙 manager）--", flush=True)
    if not w.open_workbench(CODE_MGR_ONLY_B, tag="㊱"):
        w.rep.not_run("㊱ 对照 2", "仅乙组织经理未能进入工作台")
    else:
        d_ob = w.wait_data(
            lambda x: x.get("view") not in (None, "", "loading"),
            tries=40,
            gap=0.5,
        )
        ob_items = d_ob.get("items") or []
        ob_row = next((x for x in ob_items if str(x.get("assignmentId")) == id_b), None)
        n_btn_ob = w.c.count(f'[data-act-claim="{id_b}"]')
        w.rep.rec(
            f"㊱ ①②③ 对照 2：**同一张单**（乙组织 #{id_b}）在乙经理（有认领权限）眼里"
            "**有**受理按钮 —— 与对照 1 里同一张单在 multi（乙 member）眼里**没有**形成对照。"
            "⇒ 按钮的出现只取决于「该组织的权限」，与单本身、页面、代码都无关",
            bool(ob_row) and ob_row.get("canClaim") is True and n_btn_ob == 1,
            f"canClaim={ob_row and ob_row.get('canClaim')!r} 命中={n_btn_ob}",
        )
        w.shot("36-3-乙组织经理有受理入口")

    # ============ 四、API 直证：无权限的直接调用仍被拒（图 2 ① 后半句）============
    print("\n-- 四、无权限直接调用 ⇒ 403 --", flush=True)
    st_deny, body_deny = api_post(
        f"/entrust/assignments/{id_b}/claim",
        tok_multi,
        {},
        idem_key=f"walk36-deny-{int(time.time() * 1000)}",
    )
    w.rep.rec(
        "㊱ ① 图 2 第 1 行后半句「**直接调用仍被拒绝**」：`seed-mgr-multi` 对乙组织的"
        "委托 POST claim ⇒ 服务端 **403**（藏按钮不等于放行 —— 写端独立校验，裁定 §3）",
        st_deny == 403,
        f"HTTP={st_deny} body={json.dumps(body_deny, ensure_ascii=False)[:160]}",
    )
    # ⚠️ 该次调用**没有**改变单据状态：403 在条件 UPDATE 之前抛出 ⇒ 不消耗样本。
    truth_b = api_get(f"/entrust/assignments/{id_b}", tok_multi) or {}
    w.rep.rec(
        "㊱ ④ 本次被拒的调用**没有**改动单据（仍为 submitted、仍无人认领）"
        "—— 负例不能留下副作用，否则下一次跑就没有样本了",
        str(truth_b.get("status")) == "submitted",
        f"status={truth_b.get('status')!r} claimed_by={truth_b.get('claimed_by')!r}",
    )

    # ============ 五、诚实边界（不计入通过）============
    print("\n-- 五、诚实边界（不计入通过）--", flush=True)
    w.rep.rec(
        "㊱ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        str(w.new_errors(base_err))[:200],
    )
    # ⭐ 2026-09-16：这两格原来是 `LIMITATION`，现在换成**去向记录**。
    #    ⚠️ **换去向 ≠ 原问题通过了**：它们描述的是 **㊱ 章本节**当时的能力边界 ——
    #    本节**仍然**只证到「服务端 403」，"界面刷新"那半句的完整链路是 ㊲ 章承担的
    #    （同一 token、同一页面、运行中撤权）。两条证据分别记录、互不顶替。
    w.rep.rec(
        "㊱ 裁定 §5 的「**权限被撤销** ⇒ 界面刷新」已另开通道取证 ⇒ **见 ㊲ 章**"
        "（本节仍只证到后端 403；㊲ 章用「运行中改成员角色」把刷新链路整条走完）",
        True,
        "去向：本文件 ㊲ 章（sec_37）· 通道：backend/scripts/flip_org_role.py",
    )
    w.rep.rec(
        "㊱ 对照 2 切身份用的是**重新登录**（`seed-mgr-multi` 与 `seed-mgr-only-b` 是两个"
        "账号）—— 这一点是**事实**而不是缺口：它证明的是「按钮取决于该组织的权限」；"
        "「同一会话内权限变化」已由 **㊲ 章**补上",
        True,
        "两账号事实不变；同会话内变化的证据在 ㊲ 章",
    )


def sec_37(w: Walker) -> None:
    """㊲ 受理入口的**权限撤销**边界（D-4 §5 第四条 / 图 2 第 4 行）：撤权 ⇒ 拒绝 ⇒ 界面刷新。

    为什么单独一章
    --------------
    ㊱ 章把这条分支如实记成 `LIMITATION`：它证明的是「同组织只读成员看不到按钮、
    直接调用被拒」，**不能**证明「入口**已经展示之后**权限被撤销，界面会跟着变」。
    后者需要在**运行中**改权限，而 `ent_org_member` / `ent_entrustment` **没有 HTTP 接口**
    ⇒ 只能落库（通道：`backend/scripts/flip_org_role.py`）。

    ⭐ 撤权对象怎么选（**首跑踩过，留档**）
    ------------------------------------
    首跑用 **甲组织**（`seed-mgr-multi`，manager）撤角色 ⇒ **写端仍返回 200**。
    用最小复现定位到根因（不依赖 IDE）：`access.resolve_context` 的权限是
    「**角色权限 ∪ 生效中的委托授权**」，而种子给 org 2（甲）的委托授权里**本来就含**
    `entrust:assignment:claim`：

        甲(org 2).permissions = ["entrust:view", "entrust:assignment:claim"]
        乙(org 3).permissions = ["entrust:view"]

    ⇒ 撤掉角色之后 claim 仍由**授权**给出 ⇒「撤角色」在甲组织**不是撤权**
    （200 是**正确**行为，不是产品缺陷）。本节的配方因此改为
    **乙组织 + `seed-mgr-only-b`**：那个组织的授权不含 claim ⇒ claim **只**来自角色
    ⇒ 撤角色＝真撤权。
    这条"必须先确认被撤的那条权限**没有别的来源**"的教训，比结论本身更容易被忘掉，
    所以甲组织那一侧的观察被**保留为一条事实断言**（前置②）。

    三件事必须**同时**成立，缺一条结论都不完整：

      ① 撤权**前**按钮**在**（本章起点 —— 否则「消失」无从谈起，那是从未展示过）；
      ② 撤权后**不刷新**时按钮**仍然在**：页面拿的是**旧权限投影**（前端不轮询权限，
         这不是缺陷，恰是「写端必须独立校验」这条裁定存在的原因），但**服务端已经拒绝**
         —— 同一个 token 直证 **403**（裁定 §3：隐藏按钮 ≠ 放行）；
      ③ 触发 D-4 §5 的真实链路（点确认受理 ⇒ 403 ⇒ 页面 `load()`）之后：按钮**消失**、
         `canClaim` 翻 false、单据**未被改动**（负例不留副作用），且这张单**仍看得见**
         （撤的是认领权限，不是查看权限 —— 裁定 §4）。

    ⚠️ ③ 的两条必须**成对**断言（按钮消失 + 单据状态未变）：若写端其实成功了（首跑就是），
    `canClaim` 会因为 `status` 变了而翻 false —— 断言照样"通过"，但它证的是另一件事。
    那是**超证**（声称的比证据多），必须在断言里一并堵住，否则这条 PASS 是假绿。

    最后**还原角色**并断言按钮**回来**：这是对照组，排掉「页面/工具本来就是坏的」这个
    替代解释，也证明链路**双向可逆**。

    ⚠️ 本章**会改库**（成员角色），且**必须在 `finally` 里还原** —— 种子脚本的
    `_member()` 是「有则跳过」，不会把角色改回来；不还原就是给下一次走查的 ㊱ 章埋雷
    （那边对照 2 要求 `seed-mgr-only-b` 在乙是 `manager`）。
    """
    print("\n== ㊲ 受理入口的权限撤销边界（D-4 §5）：撤权 ⇒ 403 ⇒ 界面刷新 ==", flush=True)
    base_err = w.c.errors()
    claim_perm = "entrust:assignment:claim"

    tok_multi = (api_login(CODE_MGR_MULTI) or {}).get("access_token") or ""
    tok_subj = (api_login(CODE_MGR_ONLY_B) or {}).get("access_token") or ""
    by_m = {
        str((r or {}).get("name") or ""): (r or {})
        for r in ((api_get("/entrust/my-orgs", tok_multi) or {}).get("items") or [])
    }
    by_s = {
        str((r or {}).get("name") or ""): (r or {})
        for r in ((api_get("/entrust/my-orgs", tok_subj) or {}).get("items") or [])
    }
    org_a = by_m.get(ORG_A) or {}
    org_b = by_s.get(ORG_B) or {}
    org_a_id, org_b_id = str(org_a.get("org_id") or ""), str(org_b.get("org_id") or "")
    perm_a = [str(p) for p in (org_a.get("permissions") or [])]
    perm_b = [str(p) for p in (org_b.get("permissions") or [])]

    w.rep.rec(
        "㊲ 前置①：撤权对象选 `seed-mgr-only-b`@**乙** —— 该身份在那里是 manager，"
        "且该组织的权限集里 `entrust:assignment:claim` **只**来自角色（没有第二条来源）。"
        "⚠️ 这条前后任一不成立，本节配方就得重新挑组织",
        bool(org_b_id) and str(org_b.get("member_role")) == "manager" and claim_perm in perm_b,
        f"乙#{org_b_id} role={org_b.get('member_role')!r} perms={perm_b}",
    )
    w.rep.rec(
        "㊲ 前置②（**事实，不是缺陷**）：**甲**组织的权限集里 claim 与角色**并存** ⇒ "
        "在甲撤角色**撤不掉** claim（权限 = 角色权限 ∪ 生效委托授权）。"
        "首跑就是在这里撞上 200 的 —— 本节据此换了撤权对象。"
        "⚠️ 这条同时是**前提守卫**：种子若改了甲的授权，本节配方要跟着复核",
        bool(org_a_id) and claim_perm in perm_a,
        f"甲#{org_a_id} role={org_a.get('member_role')!r} perms={perm_a}",
    )
    if str(org_b.get("member_role")) != "manager" or claim_perm not in perm_b:
        w.rep.not_run(
            "㊲ 撤权 — 刷新链路",
            "前置不成立：`seed-mgr-only-b` 在乙不是 manager，或该组织不含 claim 权限。"
            "先跑 backend/scripts/seed_entrust_orgpicker.py（或让本章的还原步骤跑一次）再重跑。",
        )
        return

    id_b = find_submitted(org_b_id, TITLE_B, tok_subj)
    w.rep.rec(
        "㊲ 前置③：乙组织里找到一张**标题匹配且 status=submitted** 的样本单"
        "（本章**不会**受理成功 —— 写端会被拒 ⇒ 整章零业务写入）",
        bool(id_b),
        f"乙#{id_b}（{TITLE_B}）",
    )
    if not id_b:
        w.rep.not_run(
            "㊲ 撤权 — 刷新链路",
            "乙组织样本单缺失（可能已被历史走查受理掉）。先跑 seed_entrust_orgpicker.py。",
        )
        return

    # ============ 一、撤权前：按钮在（本章起点）============
    print("\n-- 一、撤权前：受理入口在 --", flush=True)
    # 先清「上次选中的组织」再写死乙：`pickOrg` 有 `saved` 分支，而该 Storage 键跨 IDE
    # 重启保留 ⇒ 不显式指定就可能落在甲（那里 claim 撤不掉），本章的起点断言会直接失真。
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.remove_storage(ORG_STORAGE_KEY)  # 双保险：确认清掉上次选择
    w.c.set_storage(ORG_STORAGE_KEY, org_b_id)
    if not w.open_workbench(CODE_MGR_ONLY_B, tag="㊲"):
        w.rep.not_run("㊲ 撤权 — 刷新链路", "未能进入经理工作台")
        return
    w.wait_data(
        lambda x: (
            x.get("view") not in (None, "", "loading") and str(x.get("activeOrgId")) == org_b_id
        ),
        tries=40,
        gap=0.5,
    )
    n_before = w.c.count(f'[data-act-claim="{id_b}"]')
    w.rep.rec(
        "㊲ ① 撤权**前**：该卡**有**受理按钮（本章的起点 —— 没有它，「消失」无从谈起）",
        n_before == 1,
        f'[data-act-claim="{id_b}"] 命中 {n_before}',
    )
    w.shot("37-1-撤权前-按钮在")

    flipped = False
    try:
        # ============ 二、运行中撤权（全章唯一的写操作，且会被还原）============
        print("\n-- 二、运行中把乙组织的角色改成 member --", flush=True)
        rc, out = flip_org_role(CODE_MGR_ONLY_B, ORG_B, "member")
        flipped = rc == 0
        w.rep.rec(
            "㊲ ② 走查专用通道把该身份在**乙组织**的角色 manager → **member**"
            "（`ent_org_member` 无 HTTP 接口 ⇒ 只能落库；这一步是这条边界能取证的前提）",
            flipped,
            f"rc={rc} out={out[:200]}",
        )
        if not flipped:
            w.rep.not_run("㊲ 撤权 — 刷新链路", f"改角色失败：rc={rc} out={out[:200]}")
            return

        # —— ②a **先证权限真的被撤销**：同一 token 重取 /my-orgs ——
        # ⚠️ 这一步不能省：少了它，下一行的 403 就证明不了"是撤权导致的"
        #    （首跑在甲组织正是如此：权限没变，于是 200，而断言照样"看起来"通过了）。
        by2 = {
            str((r or {}).get("name") or ""): (r or {})
            for r in ((api_get("/entrust/my-orgs", tok_subj) or {}).get("items") or [])
        }
        ob2 = by2.get(ORG_B) or {}
        perm_b2 = [str(p) for p in (ob2.get("permissions") or [])]
        w.rep.rec(
            "㊲ ②a **先证权限真的被撤销了**：同一 token 重取 `/my-orgs` ⇒ 该组织权限集里 "
            "`entrust:assignment:claim` **已消失**（只剩 `entrust:view`）",
            str(ob2.get("member_role")) == "member" and claim_perm not in perm_b2,
            f"乙 role={ob2.get('member_role')!r} perms={perm_b2}",
        )

        # —— ②b 服务端已经拒绝：**同一个 token**，不重新登录 ——
        status, body = api_post(
            f"/entrust/assignments/{id_b}/claim",
            tok_subj,
            {},
            idem_key=f"walk37-revoke-{int(time.time() * 1000)}",
        )
        w.rep.rec(
            "㊲ ②b 撤权后直接调用受理 ⇒ 服务端 **403**（裁定 §3：隐藏按钮 ≠ 放行）。"
            "同一 token 且未重新登录 ⇒ 权限取自**库**，不是 token 里的角色快照",
            status == 403,
            f"HTTP={status} body={json.dumps(body, ensure_ascii=False)[:160]}",
        )
        truth = api_get(f"/entrust/assignments/{id_b}", tok_subj) or {}
        w.rep.rec(
            "㊲ ②c 被拒的调用**没有**改动单据（仍 submitted、仍无人认领）—— 负例不留副作用，"
            "否则下一次跑就没有样本了",
            str(truth.get("status")) == "submitted",
            f"status={truth.get('status')!r} claimed_by={truth.get('claimed_by')!r}",
        )

        # —— ②d 界面上那份**陈旧**的按钮仍在（这是被测事实，不是缺陷）——
        n_stale = w.c.count(f'[data-act-claim="{id_b}"]')
        w.rep.rec(
            "㊲ ②d 撤权后**未刷新**时按钮**仍在** —— 页面拿的是旧权限投影（前端不轮询权限）。"
            "这不算缺陷：它正是「服务端必须独立校验」这条裁定存在的原因",
            n_stale == 1,
            f'[data-act-claim="{id_b}"] 命中 {n_stale}',
        )
        w.shot("37-2-撤权后未刷新-按钮仍在")

        # ============ 三、走 D-4 §5 的真实链路：确认受理 ⇒ 403 ⇒ load() ============
        print("\n-- 三、点确认受理 ⇒ 403 ⇒ 页面自己刷新 --", flush=True)
        t_open = w.c.tap(f'[data-act-claim="{id_b}"]')
        time.sleep(1.5)
        d_open = w.c.page_data()
        w.rep.rec(
            "㊲ ③ 点「受理」在**页内**展开确认条（受理这条路不走原生弹层，见 ㉟ 章第三节）",
            bool(t_open) and d_open.get("claimOpenId") == id_b,
            f"tap={t_open} claimOpenId={d_open.get('claimOpenId')!r}",
        )
        t_sub = w.c.tap(f'[data-act-claim-submit="{id_b}"]')
        d_after = w.wait_data(
            lambda x: x.get("view") not in (None, "", "loading") and not x.get("claimingId"),
            tries=40,
            gap=0.5,
        )
        a_items = d_after.get("items") or []
        a_row = next((x for x in a_items if str(x.get("assignmentId")) == id_b), None)
        n_after = w.c.count(f'[data-act-claim="{id_b}"]')
        truth_ui = api_get(f"/entrust/assignments/{id_b}", tok_subj) or {}
        w.rep.rec(
            "㊲ ③ **后端拒绝之后页面自己刷新**（D-4 §5：显示明确提示并刷新状态）："
            "`canClaim` 翻成 **false**、受理按钮**消失** —— 而不是留一个点了必然 403 的按钮。"
            "⚠️ 与括号里那半句**成对**才算证到：单据**仍是 submitted** ⇒ 按钮消失只可能来自"
            "**权限被撤**，不是「这张单已经被受理了」（首跑正是后者，那条断言当时是**超证**）",
            bool(t_sub)
            and a_row is not None
            and a_row.get("canClaim") is False
            and n_after == 0
            and str(truth_ui.get("status")) == "submitted",
            f"tap={t_sub} canClaim={a_row and a_row.get('canClaim')!r} 命中={n_after} "
            f"单据 status={truth_ui.get('status')!r}",
        )
        w.rep.rec(
            "㊲ ③ 刷新后这张单**仍看得见**（撤掉的是**认领**权限，不是查看权限 —— "
            "裁定 §4：仍可查看其有权读取的内容）",
            a_row is not None,
            f"队列={[str(x.get('assignmentId')) for x in a_items][:6]} 目标#{id_b}",
        )
        w.shot("37-3-撤权后已刷新-按钮消失")
    finally:
        # ============ 四、还原（**必须在 finally**，否则给下一次走查埋雷）============
        rc_back, out_back = flip_org_role(CODE_MGR_ONLY_B, ORG_B, "manager")
        w.rep.rec(
            "㊲ 收尾：把角色**还原**成 manager —— 本章会改库，而种子脚本的 `_member()` 是"
            "「有则跳过」、不会自己改回来；不还原，下一次走查的 ㊱ 章对照 2 会红"
            "（那一节要求 `seed-mgr-only-b` 在乙是 manager）",
            rc_back == 0,
            f"rc={rc_back} out={out_back[:160]}",
        )

    # ============ 五、对照组：复权后按钮回来（排掉「页面/工具本来就是坏的」）============
    if flipped:
        print("\n-- 五、对照组：复权后按钮回来 --", flush=True)
        path_re = w.reenter_workbench()
        d_re = w.wait_data(
            lambda x: (
                x.get("view") not in (None, "", "loading") and str(x.get("activeOrgId")) == org_b_id
            ),
            tries=40,
            gap=0.5,
        )
        re_row = next(
            (x for x in (d_re.get("items") or []) if str(x.get("assignmentId")) == id_b),
            None,
        )
        n_re = w.c.count(f'[data-act-claim="{id_b}"]')
        w.rep.rec(
            "㊲ 对照组：**还原角色**后重进页面，按钮**回来**（`canClaim` 翻回 true）—— "
            "排掉「页面/工具本来就是坏的」这个替代解释，也证明链路**双向可逆**。"
            "⚠️ 这一步是**重新进入页面实例**，不等于原地刷新；原地刷新由第三节的 403 链路覆盖",
            bool(re_row) and re_row.get("canClaim") is True and n_re == 1,
            f"path={path_re} canClaim={re_row and re_row.get('canClaim')!r} 命中={n_re}",
        )
        w.shot("37-4-复权后-按钮回来")

    # ============ 六、诚实边界（不计入通过）============
    print("\n-- 六、诚实边界（不计入通过）--", flush=True)
    w.rep.rec(
        "㊲ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        str(w.new_errors(base_err))[:200],
    )
    w.rep.limitation(
        "㊲ 页面上的**提示文案**本身无法被工具断言",
        "403 的提示由请求层按服务端 `detail` 发出，而 toast / 弹层都不在渲染树里"
        "（与 ⑧b / ㉕D 同一个**工具**边界，与产品无关）。本章能证的是可观察的结果："
        "HTTP 403、该组织权限集里 claim 已消失、`canClaim` 翻 false、按钮从渲染树里消失、"
        "队列刷到服务端真实状态。",
    )


def sec_38(w: Walker) -> None:
    """㊳ 详情页「关键路径」的**页内形态**（2026-09-16 统一后的真机取证）。

    为什么单独一章
    --------------
    2026-09-16 把详情页两处关键路径从**原生弹层**改成**页内 DOM**：

      · 「受理委托」：`wx.showModal` 确认 ⇒ 页内确认条（展开 / 确认 / 取消三个锚点）；
      · 「记录任务」：`wx.showModal({editable: true})` ⇒ 页内输入条（input + 提交 / 取消）。

    改动的**理由就是可验证性本身**：原生弹层不在渲染树里（`.weui-dialog*` 命中 0），
    工具点不到它的确认键 ⇒「受理」这条**唯一会改变业务状态、且不可回退**的路径
    永远拿不到设备证据。所以本章不是"顺手补个覆盖"，它正是那次改写的**验收条件** ——
    页内形态若在真机上点不动，那次改写就只是把一种不可验证换成了另一种不可验证。

    与 ㊲ 章的分工：㊲ 走**队列卡片**上的受理入口（权限撤销边界）；本章走**详情页**上的
    受理入口（页内形态本身）。两处入口共用 `utils/entrust.js: canClaimAssignment()`，
    但**渲染路径不同**，必须各自有证据。

    ⚠️ 本章**会改库**（受理掉一张样本单 ⇒ `submitted → claimed`）：
      · 用**甲组织**样本单（`seed-mgr-multi` 在甲是 manager）；
      · 必须排在 ㊱ 章**之后**（㊱ 要求该单仍是 `submitted`，它才拿得到"有受理按钮"那一侧）；
      · 每次走查都是新临时库 ⇒ 不污染下一次。

    本章用到**两套身份、两张单**（2026-09-16 补；这是 ⑦ / ⑧ 拆开的原因）
    -----------------------------------------------------------------
    ①–⑦ 段：`seed-mgr-multi` @ **甲组织样本单** —— 验**页内形态**与**失败侧**；
    ⑧ 段：`seed-owner` @ **委托 #1**（`ENTRUST_ASSIGNMENT_ID`，工作台组织）—— 验**成功侧**。

    为什么不在一张单上把成功侧也验掉：`create_task` 的判权走 **owner 维度**
    （`AccessContext._delegated_by(货主, "entrust:task:dispatch")`），而授权行是**按
    「货主 → 组织」逐条写的** —— 甲组织的授权行里**没有** `task:dispatch`（它只拿到
    `["entrust:view", "entrust:assignment:claim"]`），所以在甲样本单上提交**必然 403**。
    那是权限模型的事实，不是本章要证的东西；要证「页内输入条提交成功 ⇒ 任务真的落库
    ⇒ 输入条复位」，就得换到**持有该授权**的组合上。两张单、两套身份，各证一侧。
    ⑦（失败侧）与 ⑧（成功侧）**互补且不可互相顶替**。
    """
    print("\n== ㊳ 详情页关键路径的页内形态：受理确认条 + 任务输入条 ==", flush=True)
    base_err = w.c.errors()

    tok_multi = (api_login(CODE_MGR_MULTI) or {}).get("access_token") or ""
    rows = (api_get("/entrust/my-orgs", tok_multi) or {}).get("items") or []
    by_name = {str((r or {}).get("name") or ""): (r or {}) for r in rows}
    oa = by_name.get(ORG_A) or {}
    org_a_id = str(oa.get("org_id") or "")
    w.rep.rec(
        "㊳ 前置：`seed-mgr-multi` 在甲组织是 **manager**（详情页才会渲染受理卡）",
        bool(org_a_id) and str(oa.get("member_role")) == "manager",
        f"甲#{org_a_id} role={oa.get('member_role')!r}",
    )
    if not org_a_id or str(oa.get("member_role")) != "manager":
        w.rep.not_run(
            "㊳ 详情页页内形态",
            "前置角色不是 manager。先跑 backend/scripts/seed_entrust_orgpicker.py 再重跑。",
        )
        return

    id_a = find_submitted(org_a_id, TITLE_A, tok_multi)
    w.rep.rec(
        "㊳ 前置③：甲组织里找到一张 `status=submitted` 的样本单"
        "（本章会**受理**它 ⇒ 必须排在 ㊱ 章之后 —— 那一章要求它仍是 submitted）",
        bool(id_a),
        f"甲#{id_a}（{TITLE_A}）",
    )
    if not id_a:
        w.rep.not_run(
            "㊳ 详情页页内形态",
            "甲组织样本单缺失或已被受理（历史走查吃过）。先跑 seed_entrust_orgpicker.py。",
        )
        return

    # ============ 一、打开详情页：受理卡在、且是页内的「受理委托」 ============
    print("\n-- 一、打开详情页：受理卡的页内形态 --", flush=True)
    # ⚠️ **先把页面身份切成 `seed-mgr-multi`**：本章第一版漏了这一步，而 ㊲ 章把页面会话
    #    留在了 `seed-mgr-only-b`（只属于乙组织）⇒ 甲组织的委托 #3 对它 **404** ⇒ 详情页
    #    渲染成拒绝态（截图：「功能未开放 · 委托发货当前未启用」），表现为"受理卡根本不出现"。
    #    而本章的 API 侧前置用的是 `tok_multi`，**照样通过** —— 症状因此很容易被误读成
    #    "页内确认条没实现"。教训：**页面会话与 API 会话是两条通道**，用 API 取数
    #    不会替页面登录；页面级断言之前必须先把页面那一条通道建立起来。
    if not w.open_workbench(CODE_MGR_MULTI, tag="㊳"):
        w.rep.not_run("㊳ 详情页页内形态", "未能以 seed-mgr-multi 进入经理工作台")
        return
    w.wait_data(lambda x: x.get("view") is not None, tries=30, gap=0.5)
    # 详情页的权限投影**自己**从 `/my-orgs` 按 org_id 建表（不依赖 `entrust_active_org`），
    # 这里仍先写死甲：将来详情页若引入"按当前组织"的口径，本节的起点不会悄悄失真。
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.set_storage(ORG_STORAGE_KEY, org_a_id)
    # ⚠️ 进详情页这一步是**脚本侧 navigate**，不是真实点击 —— 真实点击入口
    #    （队列卡片 → 详情）由 ㉟ / ㊱ 章覆盖；本章断言的对象是「进了详情页之后」。
    w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={id_a}", DETAIL)
    pd0 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    n_open0 = w.c.count('[data-act-claim-open="1"]')
    n_sub0 = w.c.count('[data-act-claim-submit="1"]')
    n_cancel0 = w.c.count('[data-act-claim-cancel="1"]')
    w.rep.rec(
        "㊳ ① 详情页渲染出**待受理**态：受理卡在，且入口是**页内**的「受理委托」；"
        "初始**未展开**（确认条的两个按钮都还没出现）",
        pd0.get("canClaim") is True and n_open0 == 1 and n_sub0 == 0 and n_cancel0 == 0,
        f"canClaim={pd0.get('canClaim')!r} open={n_open0} submit={n_sub0} cancel={n_cancel0}",
    )
    w.shot("38-1-详情页-受理卡页内形态")

    # ============ 二、真机点「受理委托」⇒ 确认条在页内展开 ============
    print("\n-- 二、点「受理委托」⇒ 页内确认条展开 --", flush=True)
    t_open = w.c.tap('[data-act-claim-open="1"]')
    time.sleep(1.2)
    d_open = w.c.page_data()
    n_sub = w.c.count('[data-act-claim-submit="1"]')
    n_cancel = w.c.count('[data-act-claim-cancel="1"]')
    n_open_gone = w.c.count('[data-act-claim-open="1"]')
    w.rep.rec(
        "㊳ ② 真机点「受理委托」⇒ 确认条在**页内**展开（`claimOpen` 翻 true，"
        "「确认受理」与「取消」同时出现在渲染树里、原展开按钮消失）。"
        "这条路**不经过原生弹层** —— 弹层不在渲染树里，工具点不到它的确认键",
        bool(t_open)
        and d_open.get("claimOpen") is True
        and n_sub == 1
        and n_cancel == 1
        and n_open_gone == 0,
        f"tap={t_open} claimOpen={d_open.get('claimOpen')!r} "
        f"submit={n_sub} cancel={n_cancel} open={n_open_gone}",
    )
    w.shot("38-2-受理确认条-页内展开")

    # ============ 三、点「取消」⇒ 收起且零副作用（保住样本） ============
    print("\n-- 三、点「取消」⇒ 收起，且单据未被改动 --", flush=True)
    t_cancel = w.c.tap('[data-act-claim-cancel="1"]')
    time.sleep(1.0)
    d_cancel = w.c.page_data()
    n_open_back = w.c.count('[data-act-claim-open="1"]')
    truth_mid = api_get(f"/entrust/assignments/{id_a}", tok_multi) or {}
    w.rep.rec(
        "㊳ ③ 点「取消」⇒ 确认条收起（`claimOpen` 翻回 false、展开按钮回来），"
        "且**单据没有被改动**（仍 `submitted`、仍无人认领）—— 负例不留副作用，"
        "否则这一章自己就把样本吃掉了",
        bool(t_cancel)
        and d_cancel.get("claimOpen") is False
        and n_open_back == 1
        and str(truth_mid.get("status")) == "submitted",
        f"tap={t_cancel} claimOpen={d_cancel.get('claimOpen')!r} open={n_open_back} "
        f"status={truth_mid.get('status')!r} claimed_by={truth_mid.get('claimed_by')!r}",
    )

    # ============ 四、重开 ⇒ 确认受理 ⇒ 真受理成功 ============
    print("\n-- 四、确认受理 ⇒ 服务端状态真的翻转 --", flush=True)
    w.c.tap('[data-act-claim-open="1"]')
    time.sleep(1.2)
    t_sub = w.c.tap('[data-act-claim-submit="1"]')
    d_done = w.wait_data(
        lambda x: x.get("view") not in (None, "", "loading") and not x.get("claiming"),
        tries=60,
        gap=0.5,
    )
    truth_after = api_get(f"/entrust/assignments/{id_a}", tok_multi) or {}
    n_open_done = w.c.count('[data-act-claim-open="1"]')
    n_sub_done = w.c.count('[data-act-claim-submit="1"]')
    w.rep.rec(
        "㊳ ④ 真机点「确认受理」⇒ 受理**成功**：服务端 `submitted → claimed` 且 "
        "`claimed_by` 已落（**API 直证**，不只看界面），页面 `canClaim` 翻 false、"
        "受理入口从渲染树里消失",
        bool(t_sub)
        and str(truth_after.get("status")) == "claimed"
        and bool(truth_after.get("claimed_by"))
        and d_done.get("canClaim") is False
        and n_open_done == 0
        and n_sub_done == 0,
        f"tap={t_sub} status={truth_after.get('status')!r} "
        f"claimed_by={truth_after.get('claimed_by')!r} "
        f"canClaim={d_done.get('canClaim')!r} open={n_open_done} submit={n_sub_done}",
    )
    w.shot("38-3-受理成功-入口消失")

    # ============ 五、记录任务：页内输入条（取代可编辑弹层）============
    print("\n-- 五、记录任务：页内输入条 --", flush=True)
    w.rep.rec(
        "㊳ ⑤ 受理成功后 `canCreateCase` 出现（受理前没有责任主体，`raise_case` 会 409 ⇒ "
        "受理前不摆必然失败的按钮）",
        d_done.get("canCreateCase") is True,
        f"canCreateCase={d_done.get('canCreateCase')!r}",
    )
    # 槽位动态挑一个「可用、有记录任务动作、不需要先选类型」的：
    # 写死 `procurement` 会在该槽位 `available=false` 时恒失败，而那是数据问题不是缺陷。
    cand = [
        s
        for s in (d_done.get("slots") or [])
        if s.get("available") and s.get("actionLabel") and not s.get("taskPick")
    ]
    slot = cand[0] if cand else {}
    slot_key = str(slot.get("key") or "")
    slot_type = str(slot.get("taskType") or "")
    w.rep.rec(
        "㊳ ⑤ 前置：详情页里存在一个「可用 + 有记录任务动作 + 不必先选类型」的槽位"
        "（写死某个 key 会在该槽位不可用时恒失败 —— 那是数据问题，不是缺陷）",
        bool(slot_key) and bool(slot_type),
        f"候选={[str(s.get('key')) for s in cand]} 选中={slot_key!r} taskType={slot_type!r}",
    )
    if not slot_key:
        w.rep.not_run("㊳ 记录任务的页内输入条", "没有可用槽位，无法验证输入条")
    else:
        tasks0 = (api_get(f"/entrust/tasks?assignment_id={id_a}&size=50", tok_multi) or {}).get(
            "items"
        ) or []
        t_rec = w.c.tap(f'[data-key="{slot_key}"]')
        time.sleep(1.0)
        d_form = w.c.page_data()
        n_input = w.c.count('[data-df="task-title"]')
        n_tsub = w.c.count(f'[data-act-task-submit="{slot_key}"]')
        n_tcancel = w.c.count(f'[data-act-task-cancel="{slot_key}"]')
        w.rep.rec(
            "㊳ ⑤ 点槽位上的「记录任务」⇒ **页内输入条**展开（`taskOpenKey` 是该槽位 key、"
            "输入框与提交 / 取消锚点同时出现）。取代的正是 `wx.showModal({editable: true})`"
            " —— 可编辑弹层同样不在渲染树里，确认键点不到",
            bool(t_rec)
            and d_form.get("taskOpenKey") == slot_key
            and n_input == 1
            and n_tsub == 1
            and n_tcancel == 1,
            f"tap={t_rec} taskOpenKey={d_form.get('taskOpenKey')!r} "
            f"form.type={((d_form.get('taskForm') or {}).get('type'))!r} "
            f"input={n_input} submit={n_tsub} cancel={n_tcancel}",
        )
        w.shot("38-4-记录任务-页内输入条")

        # 空标题 ⇒ **页内**提示（不是 toast），且没有创建任何任务
        t_empty = w.c.tap(f'[data-act-task-submit="{slot_key}"]')
        time.sleep(1.0)
        d_hint = w.c.page_data()
        tasks_empty = (
            api_get(f"/entrust/tasks?assignment_id={id_a}&size=50", tok_multi) or {}
        ).get("items") or []
        w.rep.rec(
            "㊳ ⑤ 空标题提交 ⇒ **页内**给出提示（`taskHint` 非空、输入条仍展开），"
            "且**没有**创建任务。提示刻意不走 toast：toast 几秒后消失"
            "（而「为什么没提交」正是此刻要一直看到的那句话），"
            "且同样不在渲染树里、不可断言",
            bool(t_empty)
            and bool(str(d_hint.get("taskHint") or "").strip())
            and d_hint.get("taskOpenKey") == slot_key
            and len(tasks_empty) == len(tasks0),
            f"tap={t_empty} taskHint={d_hint.get('taskHint')!r} "
            f"taskOpenKey={d_hint.get('taskOpenKey')!r} 任务数={len(tasks0)}→{len(tasks_empty)}",
        )
        w.shot("38-5-空标题-页内提示")

        # 真键入 ⇒ bindinput 真的接上了（验的是**输入通道**本身，与"提交成功"是两件事）
        task_title = "走查㊳·页内输入条样本"
        ok_input = w.c.input_text('[data-df="task-title"]', task_title)
        time.sleep(0.8)
        d_typed = w.c.page_data()
        typed = str((d_typed.get("taskForm") or {}).get("title") or "")
        w.rep.rec(
            "㊳ ⑤ 真机键入标题 ⇒ `bindinput` 真的接上了（页面 `taskForm.title` 收到文本）。"
            "验的是**输入通道**，不是「填个值」—— 用 `setData` 填值也能让字段非空，"
            "那验不出绑定",
            bool(ok_input) and typed == task_title,
            f"input ok={ok_input} taskForm.title={typed!r} 期望={task_title!r}",
        )
        # ⚠️ 本节断言的对象是**可观察的失败行为**，不是「功能正常」。实测链条（2026-09-16）：
        #    `create_task` 的判权是 `assert_can(perm=task:dispatch, owner_user_id=那单单主)`，
        #    而 `AccessContext._delegated_by()` 只认「**该货主**授予的授权行」里的权限。
        #    甲组织的授权行来自货主 `seed-shipper-orgpicker`，内容是
        #    `["entrust:view", "entrust:assignment:claim"]` ⇒ **不含** `task:dispatch` ⇒ 403。
        #    与此同时，同一身份在 `/my-orgs` 的**组织维度**权限集里**有** `task:dispatch`
        #    （来自 `ORG_ROLE_PERMISSIONS["manager"]`）⇒ **同一权限码，两个维度给出相反答案**。
        #    这是**实测事实**，归属待产品裁决；本节负责把它固定住，并证明前端在失败时
        #    **不留脏状态**（不假装成功，也不把用户已打的字吞掉）。
        print("\n-- 六、提交（甲组织无 owner 维度授权）--", flush=True)
        t_rej = w.c.tap(f'[data-act-task-submit="{slot_key}"]')
        time.sleep(2.5)
        d_rej = w.c.page_data()
        tasks_rej = (api_get(f"/entrust/tasks?assignment_id={id_a}&size=50", tok_multi) or {}).get(
            "items"
        ) or []
        kept_title = str((d_rej.get("taskForm") or {}).get("title") or "") == task_title
        w.rep.rec(
            "㊳ ⑦ 提交被后端拒 ⇒ 页面**不留脏状态**：输入条仍展开、用户已键入的标题仍在、"
            "任务数未变。失败时**故意不调 `load()`** —— 否则输入条一收，"
            "用户重试得把标题重打一遍",
            bool(t_rej)
            and d_rej.get("taskOpenKey") == slot_key
            and kept_title
            and len(tasks_rej) == len(tasks0),
            f"tap={t_rej} taskOpenKey={d_rej.get('taskOpenKey')!r} 标题保留={kept_title} "
            f"任务数={len(tasks0)}→{len(tasks_rej)}",
        )
        w.shot("38-6-提交被拒-页面不留脏状态")

        # ---- 拒因直证：不是随机失败，而是两个维度对同一权限码给出相反答案 ----
        st_direct, body_direct = api_post(
            f"/entrust/assignments/{id_a}/tasks",
            tok_multi,
            {"task_type": slot_type, "title": "走查㊳·拒因直证（不应落库）"},
            f"38-rej-{int(time.time() * 1000)}",
        )
        perms_a = list(oa.get("permissions") or [])
        w.rep.rec(
            "㊳ ⑦b 拒因**直证**（口径事实，不是缺陷判定）：同一身份在 `/my-orgs` 的"
            "**组织维度**权限集里**有** `entrust:task:dispatch`，而写端按 **owner 维度**判权 ⇒ "
            "**403**。前端的槽位动作只看 `board.status === 'claimed'`、**不查写权限**，"
            "所以界面会摆出一个**必然被拒**的写操作。本条断言的是「这个差异确实存在且可复现」，"
            "**不是**「功能正常」—— 归属待产品裁决，见 DEMO-1-r1-remainder.md",
            st_direct == 403 and "entrust:task:dispatch" in str(body_direct),
            f"HTTP={st_direct} body={str(body_direct)[:150]} 甲组织/my-orgs perms={perms_a}",
        )

    # ============ 八、成功路径：换到 owner 维度有授权的组合 ============
    #
    # 为什么必须换组合：`create_task` 走 **owner 维度**，而授权行是**按「货主 → 组织」
    # 逐条写的** —— 同一个权限码在不同组织之间可以不同。工作台那张样本单
    # （委托 #1，`claimed`）的货主是 `seed-shipper`，演示组织（`ORG_WORKBENCH`）拿到的
    # 是**完整 6 项**（含 `entrust:task:dispatch`）⇒ 该组织的经理 `seed-owner` 才是
    # "能真的把任务建出来"的身份。**这是权限模型定的组合，不是绕开它。**
    #
    # 本节也是本章**唯一**能证「页内输入条提交成功后由 `load()` 统一复位」的地方：
    # ⑤ 只证了形态，⑦ 证的是失败侧的不留脏状态。
    print("\n-- 八、提交成功路径（owner 维度有授权的组合）--", flush=True)
    tok_owner = (api_login(CODE_OWNER) or {}).get("access_token") or ""
    org_wb_id = ""
    for r in (api_get("/entrust/my-orgs", tok_owner) or {}).get("items") or []:
        if str((r or {}).get("name") or "") == ORG_WORKBENCH:
            org_wb_id = str((r or {}).get("org_id") or "")
    if not org_wb_id or not w.open_workbench(CODE_OWNER, tag="㊳⑧"):
        w.rep.not_run(
            "㊳ ⑧ 提交成功路径",
            f"未能以 {CODE_OWNER} 进入工作台（{ORG_WORKBENCH} org_id={org_wb_id!r}）",
        )
    else:
        w.wait_data(lambda x: x.get("view") is not None, tries=30, gap=0.5)
        w.c.remove_storage(ORG_STORAGE_KEY)
        w.c.remove_storage(ORG_STORAGE_KEY)
        w.c.set_storage(ORG_STORAGE_KEY, org_wb_id)
        w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={ENTRUST_ASSIGNMENT_ID}", DETAIL)
        pd_ok = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
        cand_ok = [
            s
            for s in (pd_ok.get("slots") or [])
            if s.get("available") and s.get("actionLabel") and not s.get("taskPick")
        ]
        slot_ok = cand_ok[0] if cand_ok else {}
        slot_ok_key = str(slot_ok.get("key") or "")
        tasks_wb0 = (
            api_get(
                f"/entrust/tasks?assignment_id={ENTRUST_ASSIGNMENT_ID}&size=50",
                tok_owner,
            )
            or {}
        ).get("items") or []
        w.rep.rec(
            "㊳ ⑧ 前置：在有授权的组合上（`seed-owner` @ 委托 #1），详情页给出可用的"
            "「记录任务」槽位（受理前没有责任主体，不摆必然失败的按钮）",
            bool(slot_ok_key) and pd_ok.get("canCreateCase") is True,
            f"单 #{ENTRUST_ASSIGNMENT_ID} canCreateCase={pd_ok.get('canCreateCase')!r} "
            f"候选={[str(s.get('key')) for s in cand_ok]} 选中={slot_ok_key!r}",
        )
        if not slot_ok_key:
            w.rep.not_run("㊳ ⑧ 提交成功路径", "该单上没有可用槽位")
        else:
            wb_title = "走查㊳·页内输入条样本（工作台）"
            t_ok1 = w.c.tap(f'[data-key="{slot_ok_key}"]')
            time.sleep(1.0)
            i_ok = w.c.input_text('[data-df="task-title"]', wb_title)
            time.sleep(0.8)
            t_ok2 = w.c.tap(f'[data-act-task-submit="{slot_ok_key}"]')
            d_ok = w.wait_data(
                lambda x: x.get("view") not in (None, "", "loading") and not x.get("taskOpenKey"),
                tries=60,
                gap=0.5,
            )
            tasks_wb1 = (
                api_get(
                    f"/entrust/tasks?assignment_id={ENTRUST_ASSIGNMENT_ID}&size=50",
                    tok_owner,
                )
                or {}
            ).get("items") or []
            titles_wb = [str((x or {}).get("title") or "") for x in tasks_wb1]
            w.rep.rec(
                "㊳ ⑧ 提交 ⇒ 任务**真的建出来了**（**API 直证**：任务列表里出现该标题、"
                "条数 +1），且输入条由 `load()` **统一复位**（`taskOpenKey` 清空 —— "
                "复位只在一处，不在两处各收一次）",
                bool(t_ok1 and i_ok and t_ok2)
                and wb_title in titles_wb
                and len(tasks_wb1) == len(tasks_wb0) + 1
                and not d_ok.get("taskOpenKey"),
                f"tap={t_ok1} input={i_ok} submit={t_ok2} 槽位={slot_ok_key!r} "
                f"任务数={len(tasks_wb0)}→{len(titles_wb)} 含目标={wb_title in titles_wb} "
                f"taskOpenKey={d_ok.get('taskOpenKey')!r}",
            )
            w.shot("38-8-提交成功-任务已记录")

    # ============ 九、诚实边界（不计入通过）============
    print("\n-- 九、诚实边界（不计入通过）--", flush=True)
    w.rep.rec(
        "㊳ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        str(w.new_errors(base_err))[:200],
    )
    w.rep.limitation(
        "㊳ 「进入详情页」这一步是**脚本侧 `navigate()`**，不是真实点击",
        "本章断言的对象是「点开详情**之后**」的页内形态（确认条 / 输入条）。详情页的"
        "**真实点击入口**（队列卡片 → 详情）由 ㉟ / ㊱ 章覆盖。两者是不同的事实，"
        "不能互相顶替。",
    )
    w.rep.limitation(
        "㊳ 成功 / 失败时的 **toast 文案**本身不可被工具断言",
        "与 ⑧b / ㉕D / ㊲ 同一个**工具**边界（不在渲染树里），与产品无关。本章能证的"
        "是可观察的结果：服务端状态翻转、`canClaim` / `taskOpenKey` 的取值、锚点在"
        "渲染树里的命中数、以及任务是否真的出现在 API 载荷里。",
    )


def sec_39(w: Walker) -> None:
    """㊴ 详情页受理入口的「**被抢认领**」界面路径（D-4 §5 第四条 · 图 2 第 4 行的 409 一半）。

    为什么还差这一条
    ----------------
    D-4 裁定的 §5 第四条有**两个**分支，都落在「入口展示之后、后端拒绝」这一格：

      · 「权限**被撤销** ⇒ 403 ⇒ 刷新」—— ㊲ 章（**队列卡片**入口）；
      · 「已被**他人抢先认领** ⇒ 409 ⇒ 刷新」—— ㉟ 章第五节（**队列卡片**入口）。

    ⚠️ 两处都**不是详情页**。而 2026-09-16 把详情页的「受理委托」从 `wx.showModal`
    改成**页内确认条**（㊳ 章）⇒ `detail.js: onSubmitClaim` 里
    `status === 403 || status === 409 ⇒ load()` 成了**一条新的代码路径**，
    此前没有任何设备侧证据。本章补的就是它。

    四个入口 × 两个分支，各归各的证据（**互不顶替**）
    -------------------------------------------------
    | 章 | 入口 | 分支 | 证据 |
    | --- | --- | --- | --- |
    | ㉟ 五 | 队列卡片 | 409 被抢 | 页内确认条 + 队列刷成服务端真实状态 |
    | ㊲ 三 | 队列卡片 | 403 撤权 | 页内确认条 + 刷新（成对断言单据未被改动） |
    | ㊳ ②④ | 详情页 | 无（成功侧） | 确认条展开 / 受理成功 |
    | **本章** | **详情页** | **409 被抢** | 确认条展开 ⇒ 点确认 ⇒ 409 ⇒ 刷新 |

    本章**真写一张单**（API 建单 + API 提交到甲组织 + 被抢一次受理）：
      · 货主用 `seed-shipper-orgpicker`（在甲组织有生效委托授权）；
      · **页面身份** `seed-mgr-single`（**仅甲**经理）、**抢单者** `seed-mgr-multi`（甲经理）
        —— 甲组织是种子里**唯一**有两个经理的组织，「被抢」只能在这里造；
      · 不新造**种子**（本机只需一张运行时的载体单）；
      · 放在全量序列最末，且**不依赖**其它章节留下的状态。

    ⚠️ 与 ㊳ 的样本必须**分开**：甲组织在种子里只有**一张** `submitted` 样本单
    （`TITLE_A`），㊳ 会把它受理掉 ⇒ 本章自己经 API 建一张，否则两章会抢同一张。
    """
    print("\n== ㊴ 详情页受理入口的「被抢认领」路径（409 ⇒ 刷新）==", flush=True)
    base_err = w.c.errors()

    tok_multi = (api_login(CODE_MGR_MULTI) or {}).get("access_token") or ""
    rows = (api_get("/entrust/my-orgs", tok_multi) or {}).get("items") or []
    oa = next((r or {} for r in rows if str((r or {}).get("name") or "") == ORG_A), {})
    org_a_id = str(oa.get("org_id") or "")

    tok_single = (api_login(CODE_MGR_SINGLE) or {}).get("access_token") or ""
    rows_s = (api_get("/entrust/my-orgs", tok_single) or {}).get("items") or []
    osingle = next((r or {} for r in rows_s if str((r or {}).get("name") or "") == ORG_A), {})
    w.rep.rec(
        "㊴ 前置：页面身份 `seed-mgr-single` 是**仅甲组织**的 manager、抢单者 "
        "`seed-mgr-multi` 也是甲组织 manager —— 甲组织是种子里**唯一**有两个经理的"
        "组织，「被抢」只能在这里造",
        bool(org_a_id)
        and str(osingle.get("member_role")) == "manager"
        and str(oa.get("member_role")) == "manager"
        and len(rows_s) == 1,
        f"甲#{org_a_id} single={osingle.get('member_role')!r}（组织数={len(rows_s)}）"
        f" multi={oa.get('member_role')!r}",
    )
    if not org_a_id or str(osingle.get("member_role")) != "manager":
        w.rep.not_run(
            "㊴ 详情页被抢认领路径",
            "前置身份不满足。先跑 backend/scripts/seed_entrust_orgpicker.py 再重跑。",
        )
        return

    # ---- 载体单：经 API 建单 + 提交到甲组织（**不是**界面提交，本章断言的对象是详情页）----
    ts = int(time.time() * 1000)
    tok_op = (api_login(CODE_SHIPPER_ORGPICKER) or {}).get("access_token") or ""
    st_new, created = api_post(
        "/entrust/assignments",
        tok_op,
        {
            "title": TITLE_RACE_DETAIL,
            "cargo_summary": "㊴ 章载体单：详情页「被抢认领」路径",
        },
        f"walk39-new-{ts}",
    )
    new_id = str((created or {}).get("assignment_id") or "")
    rev = int((created or {}).get("revision") or 0)
    st_sub, submitted = (0, None)
    if new_id and rev:
        st_sub, submitted = api_post(
            f"/entrust/assignments/{new_id}/submit",
            tok_op,
            {"org_id": int(org_a_id), "expected_revision": rev},
            f"walk39-sub-{ts}",
        )
    w.rep.rec(
        "㊴ 前置：载体单已建成并**提交到甲组织**（`submitted`）⇒ 本章与 ㊳ 各用各的单，"
        "不会互抢（甲组织种子里只有一张待受理样本单，㊳ 会把它受理掉）",
        st_new in (200, 201)
        and st_sub in (200, 201)
        and str((submitted or {}).get("status") or "") == "submitted",
        f"建单 HTTP={st_new} id={new_id!r} 提交 HTTP={st_sub} "
        f"status={(submitted or {}).get('status')!r}",
    )
    if not new_id or st_sub not in (200, 201):
        w.rep.not_run("㊴ 详情页被抢认领路径", "载体单未建成，链路断在这里")
        return

    # ============ 一、详情页起点：待受理 + 受理入口在（先证有）============
    print("\n-- 一、详情页起点（先证有）--", flush=True)
    if not w.open_workbench(CODE_MGR_SINGLE, tag="㊴"):
        w.rep.not_run("㊴ 详情页被抢认领路径", "未能以 seed-mgr-single 进入经理工作台")
        return
    w.wait_data(lambda x: x.get("view") is not None, tries=30, gap=0.5)
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.set_storage(ORG_STORAGE_KEY, org_a_id)
    w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={new_id}", DETAIL)
    pd0 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    n_open0 = w.c.count('[data-act-claim-open="1"]')
    w.rep.rec(
        "㊴ ① 起点：待受理态、**受理入口在** —— 必须先证有：没有它，「被抢之后按钮消失」"
        "就与「本来就没有按钮」分不开（那是完全不同的结论）",
        pd0.get("canClaim") is True and n_open0 == 1,
        f"canClaim={pd0.get('canClaim')!r} open={n_open0}",
    )
    w.shot("39-1-详情页-待受理-入口在")

    # ============ 二、页内确认条展开（把页面停在「用户已决定受理」的那一刻）============
    print("\n-- 二、确认条展开（抢单前）--", flush=True)
    t_open = w.c.tap('[data-act-claim-open="1"]')
    time.sleep(1.2)
    d_open = w.c.page_data()
    n_sub_open = w.c.count('[data-act-claim-submit="1"]')
    n_cancel_open = w.c.count('[data-act-claim-cancel="1"]')
    w.rep.rec(
        "㊴ ② 点「受理委托」⇒ 确认条在**页内**展开（确认 / 取消同时可点）。形态本身已由 "
        "㊳ ② 断言，这里只用它把页面停在「用户已经决定要受理」的那一刻",
        bool(t_open) and d_open.get("claimOpen") is True and n_sub_open == 1 and n_cancel_open == 1,
        f"tap={t_open} claimOpen={d_open.get('claimOpen')!r} "
        f"submit={n_sub_open} cancel={n_cancel_open}",
    )
    w.shot("39-2-确认条展开-抢单前")

    # ============ 三、抢单：外部先受理，页面还不知道 ============
    print("\n-- 三、外部先受理 ⇒ 页面仍旧 --", flush=True)
    st_race, _ = api_post(
        f"/entrust/assignments/{new_id}/claim", tok_multi, {}, f"walk39-race-{ts}"
    )
    truth_race = api_get(f"/entrust/assignments/{new_id}", tok_single) or {}
    w.rep.rec(
        "㊴ ③ 抢单前置：**另一名**甲组织经理（`seed-mgr-multi`）经 API 把这单受理走了"
        "（刻意不走界面 —— 单模拟器做不出第二个界面实例，与 ㉟ 章第五节同一条理由）",
        st_race in (200, 201) and str(truth_race.get("status") or "") == "claimed",
        f"抢单 HTTP={st_race} 服务端 status={truth_race.get('status')!r} "
        f"claimed_by={truth_race.get('claimed_by')!r}",
    )
    d_stale = w.c.page_data()
    n_sub_stale = w.c.count('[data-act-claim-submit="1"]')
    w.rep.rec(
        "㊴ ③ 而**页面还不知道**：确认条仍在、`canClaim` 仍为 true（前端不轮询状态/权限）"
        "—— 这正是 §5「以后端结果为准」存在的原因；此时按下去**必然**被拒",
        d_stale.get("canClaim") is True and n_sub_stale == 1,
        f"页面 canClaim={d_stale.get('canClaim')!r} 确认键={n_sub_stale}（服务端已 claimed）",
    )

    # ============ 四、迟到的那一下 ⇒ 后端拒绝 ⇒ 页面自己刷新 ============
    print("\n-- 四、迟到的那一下 ⇒ 刷新 ============", flush=True)
    t_late = w.c.tap('[data-act-claim-submit="1"]')
    d_after = w.wait_data(
        # ⚠️ 判据取 `canClaim is False`，**不取** `not claiming` —— 后者在 catch 里
        #    先被置回 false，而 `load()` 还没回来，会在"canClaim 仍为 true"的那一瞬间
        #    提前返回，把一条本会通过的断言判成红的（假红）。`wait_data` 在轮询用尽时
        #    返回最后一次读数 ⇒ 真要失败也带着现场数据，不会静默。
        lambda x: x.get("view") not in (None, "", "loading") and x.get("canClaim") is False,
        tries=60,
        gap=0.5,
    )
    n_open1 = w.c.count('[data-act-claim-open="1"]')
    n_sub1 = w.c.count('[data-act-claim-submit="1"]')
    n_cancel1 = w.c.count('[data-act-claim-cancel="1"]')
    w.rep.rec(
        "㊴ ④ 迟到的那一下 ⇒ 页面**自己刷新**（D-4 §5：以后端结果为准）：`canClaim` 翻 "
        "false、受理入口与确认条一起从渲染树里消失 —— 而不是留一个点了必然被拒的按钮。"
        "⚠️ 与括号里那半句**成对**才算证到：`canCreateCase` 同时翻 **true**（只有 "
        "`status=claimed` 才会出现「登记案件」入口）⇒ 这次的翻转**只可能**来自"
        "「服务端已受理」，不是「权限被撤」那种翻转",
        bool(t_late)
        and d_after.get("canClaim") is False
        and d_after.get("canCreateCase") is True
        and n_open1 == 0
        and n_sub1 == 0
        and n_cancel1 == 0,
        f"tap={t_late} canClaim={d_after.get('canClaim')!r} "
        f"canCreateCase={d_after.get('canCreateCase')!r} open={n_open1} "
        f"submit={n_sub1} cancel={n_cancel1}",
    )
    w.shot("39-3-被抢之后-页面刷新")

    # ============ 五、拒因直证：这个写操作确实"必然被拒" ============
    print("\n-- 五、拒因直证 --", flush=True)
    st_again, body_again = api_post(
        f"/entrust/assignments/{new_id}/claim", tok_single, {}, f"walk39-again-{ts}"
    )
    w.rep.rec(
        "㊴ ⑤ 拒因**直证**（口径事实，不是缺陷判定）：用**页面那一个身份**直接再调一次"
        "受理 ⇒ 服务端 **409**（`claim_assignment` 是单条 `UPDATE ... WHERE "
        "status='submitted'`，条件没打中即判冲突）。⇒ 页面刚才那一下「必然被拒」不是"
        "随机失败，就是这条已复现的冲突语义；本条断言的是**这个事实可复现**，"
        "**不是**「功能正常」",
        st_again == 409,
        f"HTTP={st_again} body={str(body_again)[:150]}",
    )

    # ============ 六、诚实边界（不计入通过）============
    print("\n-- 六、诚实边界（不计入通过）--", flush=True)
    w.rep.rec(
        "㊴ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        str(w.new_errors(base_err))[:200],
    )
    w.rep.rec(
        "㊴ 去向记录（**复用**，不是本章新证）：队列卡片入口上的同一分支由 "
        "**㉟ 章第五节**（409 被抢）与 **㊲ 章**（403 撤权）覆盖；本章只补**详情页**入口，"
        "不重复断言、也不顶替它们",
        True,
        "去向：㉟ 五 = 409 @ 队列 · ㊲ = 403 @ 队列 · 本章 = 409 @ 详情页",
    )
    w.rep.rec(
        "㊴ 缺口登记（**保留的历史事实**，**不是**本章的通过）：本章首次跑时，**详情页**入口上的"
        "「权限被撤销 ⇒ 403」分支**没有**独立证据 —— ㊲ 章证的是**队列卡片**入口。它与本章的 409 "
        "走的是同一个 `onSubmitClaim` 的 `if (status === 403 || status === 409) return self.load()`，"
        "但「403 也走这条」当时只有**代码阅读**。"
        "⚠️ 该缺口已于 2026-09-16 由 **㊵ 章**（sec_40）兑现为设备证据。本条**不删** ——"
        "「当时确实没有证据」本身是记录的一部分，删掉它等于伪造历史",
        True,
        "已兑现：㊵ = 403 @ 详情页；㊲ = 403 @ 队列 · ㉟ 五 = 409 @ 队列 · 本章 = 409 @ 详情页",
    )
    w.rep.limitation(
        "㊴ 本章的载体单经 **API 建单 / 提交**，不是界面提交",
        "本章断言的对象是详情页「点开之后」的页内形态与刷新链路，与这张单是**谁建的**"
        "无关；「界面真实点击提交到甲组织」这条由 ㉟ 章第一节覆盖。两者是不同的事实，"
        "不能互相顶替。",
    )
    w.rep.limitation(
        "㊴ 的「另一个写者」是**同进程 API 调用**，不是第二个真机客户端",
        "单模拟器做不出第二个界面实例（与 ㉟ 章第五节同一条边界）。本章证的是"
        "「后端拒绝 ⇒ 页面刷新」这条链路；**并发本身**由后端单条条件更新 ＋ "
        "MySQL 集成用例覆盖，不在本章范围内。",
    )
    w.rep.limitation(
        "㊴ 的 409 提示**文案**不可被工具断言",
        "与 ⑧b / ㉕D / ㊲ / ㊳ 同一个**工具**边界（toast 不在渲染树里），与产品无关。"
        "本章能证的是可观察结果：HTTP 409、`canClaim` / `canCreateCase` 的翻转、"
        "锚点在渲染树里的命中数。",
    )


def sec_40(w: Walker) -> None:
    """㊵ 详情页受理入口的「**权限撤销**」路径（403 ⇒ 刷新）—— 与 ㊴ 同入口、另一分支。

    为什么还差这一条
    ----------------
    ㊴ 章**当时**把这条缺口如实登记为「仍未取证」：详情页入口上的 **403** 分支只有**代码阅读**
    （`detail.js: onSubmitClaim` 的 `catch` 里 `if (status === 403 || status === 409) return self.load()`），
    设备证据只到 **409**。本章把它补成真证据 —— 于是「入口 × 分支」四格**全部有设备侧证据**：

    | 章 | 入口 | 分支 | 证据 |
    | --- | --- | --- | --- |
    | ㉟ 五 | 队列卡片 | 409 被抢 | 页内确认条 + 队列刷成服务端真实状态 |
    | ㊲ 三 | 队列卡片 | 403 撤权 | 页内确认条 + 刷新（成对断言单据未被改动） |
    | ㊴ | 详情页 | 409 被抢 | 确认条展开 ⇒ 点确认 ⇒ 409 ⇒ 刷新 |
    | **本章** | **详情页** | **403 撤权** | 确认条展开 ⇒ 点确认 ⇒ 403 ⇒ 刷新 |

    ⭐ 配方必须照抄 ㊲ 的**撤权对象选择**，不能照抄 ㊴ 的**组织**
    ---------------------------------------------------------
    「撤权」要求被撤的那条权限**没有第二条来源**。甲组织的委托授权里**本来就含**
    `entrust:assignment:claim` ⇒ 在甲撤角色**撤不掉** claim（㊲ 首跑正是撞上这个 200）。
    所以本章用 **乙组织 ＋ `seed-mgr-only-b`**：该组织 claim **只**来自角色 ⇒ 撤角色＝真撤权。

    ⚠️ 而「被抢认领」（㊴）需要**同组织两个经理**，种子里只有甲满足 ⇒ 两章的选址
    **恰好相反**。这不是笔误，是同一条理由（"被撤/被抢的那条权限有没有第二条来源"）
    在两个分支上给出的不同答案。

    ⚠️ 本章**会改库**（成员角色），且**必须在 `finally` 里还原** —— 与 ㊲ 同一条纪律：
    种子脚本的 `_member()` 是「有则跳过」，不还原就是给下一次走查的 ㊱ 章埋雷。

    ⭐ 本章与 ㊴ 合起来还能证一件单章证不了的事
    ------------------------------------------
    两个分支在详情页上留下的**页内痕迹不同**：`canCreateCase` 只看 `board.status === 'claimed'`。
    ㊴ 的 409 ⇒ 单据 `claimed` ⇒ 该字段翻 **true**；本章的 403 ⇒ 单据仍 `submitted` ⇒ 仍 **false**。
    ⇒ 两条合取，才排掉「页面把任何刷新都当成同一个结果」这种替代解释。
    """
    print("\n== ㊵ 详情页受理入口的「权限撤销」路径（403 ⇒ 刷新）==", flush=True)
    base_err = w.c.errors()
    claim_perm = "entrust:assignment:claim"

    tok_subj = (api_login(CODE_MGR_ONLY_B) or {}).get("access_token") or ""
    by_s = {
        str((r or {}).get("name") or ""): (r or {})
        for r in ((api_get("/entrust/my-orgs", tok_subj) or {}).get("items") or [])
    }
    org_b = by_s.get(ORG_B) or {}
    org_b_id = str(org_b.get("org_id") or "")
    perm_b = [str(p) for p in (org_b.get("permissions") or [])]

    w.rep.rec(
        "㊵ 前置①：撤权对象是 `seed-mgr-only-b`@**乙** —— 该组织里 "
        "`entrust:assignment:claim` **只**来自角色（没有第二条来源），撤角色＝真撤权。"
        "⚠️ 与 ㊴ 选的**甲组织恰好相反**，理由见本章 docstring",
        bool(org_b_id) and str(org_b.get("member_role")) == "manager" and claim_perm in perm_b,
        f"乙#{org_b_id} role={org_b.get('member_role')!r} perms={perm_b}",
    )
    if not org_b_id or str(org_b.get("member_role")) != "manager" or claim_perm not in perm_b:
        w.rep.not_run(
            "㊵ 详情页撤权路径",
            "前置不成立：`seed-mgr-only-b` 在乙不是 manager，或该组织不含 claim 权限。"
            "先跑 backend/scripts/seed_entrust_orgpicker.py（或等 ㊲ 章的还原步骤跑一次）再重跑。",
        )
        return

    id_b = find_submitted(org_b_id, TITLE_B, tok_subj)
    w.rep.rec(
        "㊵ 前置②：乙组织里找到标题匹配且 status=submitted 的样本单（**与 ㊲ 共用同一张**）——"
        "两章都只**尝试**受理、都被拒 ⇒ 零业务写入、不互相消耗，谁先跑都不影响谁",
        bool(id_b),
        f"乙#{id_b}（{TITLE_B}）",
    )
    if not id_b:
        w.rep.not_run(
            "㊵ 详情页撤权路径",
            "乙组织样本单缺失（可能已被历史走查受理掉）。先跑 seed_entrust_orgpicker.py。",
        )
        return

    def goto_detail(tag: str) -> dict:
        """钉住乙组织 → 打开该单的**详情页**，返回详情页的 page_data。

        ⚠️ 必须先清 `saved` 组织再写死乙：`pickOrg` 有 `saved` 分支，而该 Storage 键
        **跨 IDE 重启保留** ⇒ 不显式指定就可能落在甲（那里 claim 撤不掉），本章断言会失真。
        """
        w.c.remove_storage(ORG_STORAGE_KEY)
        w.c.remove_storage(ORG_STORAGE_KEY)  # 双保险：确认清掉上次选择
        w.c.set_storage(ORG_STORAGE_KEY, org_b_id)
        w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={id_b}", DETAIL)
        return w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)

    # ============ 一、详情页起点：入口在（先证有）============
    print("\n-- 一、详情页起点（先证有）--", flush=True)
    if not w.open_workbench(CODE_MGR_ONLY_B, tag="㊵"):
        w.rep.not_run("㊵ 详情页撤权路径", "未能以 seed-mgr-only-b 进入经理工作台")
        return
    w.wait_data(lambda x: x.get("view") is not None, tries=30, gap=0.5)
    pd0 = goto_detail("㊵-起点")
    n_open0 = w.c.count('[data-act-claim-open="1"]')
    w.rep.rec(
        "㊵ ① 起点：详情页上待受理态、**受理入口在** —— 必须先证有：没有它，"
        "「撤权之后入口消失」就与「本来就没有入口」分不开（那是完全不同的结论）",
        pd0.get("canClaim") is True and n_open0 == 1,
        f"canClaim={pd0.get('canClaim')!r} open={n_open0}",
    )
    w.shot("40-1-详情页-撤权前-入口在")

    # ============ 二、确认条展开（把页面停在「用户已决定受理」那一刻）============
    print("\n-- 二、确认条展开（撤权前）--", flush=True)
    t_open = w.c.tap('[data-act-claim-open="1"]')
    time.sleep(1.2)
    d_open = w.c.page_data()
    n_sub_open = w.c.count('[data-act-claim-submit="1"]')
    n_cancel_open = w.c.count('[data-act-claim-cancel="1"]')
    w.rep.rec(
        "㊵ ② 点「受理委托」⇒ 确认条在**页内**展开（确认 / 取消同时可点）。形态本身已由 "
        "㊳ ② 断言，这里只用它把页面停在「用户已经决定要受理」的那一刻",
        bool(t_open) and d_open.get("claimOpen") is True and n_sub_open == 1 and n_cancel_open == 1,
        f"tap={t_open} claimOpen={d_open.get('claimOpen')!r} "
        f"submit={n_sub_open} cancel={n_cancel_open}",
    )
    w.shot("40-2-确认条展开-撤权前")

    flipped = False
    try:
        # ============ 三、运行中撤权（全章唯一的写操作，且会被还原）============
        print("\n-- 三、运行中把乙组织的角色改成 member --", flush=True)
        rc, out = flip_org_role(CODE_MGR_ONLY_B, ORG_B, "member")
        flipped = rc == 0
        w.rep.rec(
            "㊵ ③ 走查专用通道把该身份在**乙组织**的角色 manager → **member**"
            "（`ent_org_member` 无 HTTP 接口 ⇒ 只能落库；这一步是这条边界能取证的前提）",
            flipped,
            f"rc={rc} out={out[:200]}",
        )
        if not flipped:
            w.rep.not_run("㊵ 详情页撤权路径", f"改角色失败：rc={rc} out={out[:200]}")
            return

        # —— ③a **先证权限真的被撤销**：同一 token 重取 /my-orgs ——
        # ⚠️ 不能省：少了它，后面的 403 就证明不了"是撤权导致的"（㊲ 首跑在甲组织正是如此）。
        by2 = {
            str((r or {}).get("name") or ""): (r or {})
            for r in ((api_get("/entrust/my-orgs", tok_subj) or {}).get("items") or [])
        }
        ob2 = by2.get(ORG_B) or {}
        perm_b2 = [str(p) for p in (ob2.get("permissions") or [])]
        w.rep.rec(
            "㊵ ③a **先证权限真的被撤销了**：同一 token 重取 `/my-orgs` ⇒ 该组织权限集里 "
            "`entrust:assignment:claim` **已消失**（只剩 `entrust:view`）",
            str(ob2.get("member_role")) == "member" and claim_perm not in perm_b2,
            f"乙 role={ob2.get('member_role')!r} perms={perm_b2}",
        )

        # —— ③b 服务端已经拒绝：**同一个 token**，不重新登录 ——
        status, body = api_post(
            f"/entrust/assignments/{id_b}/claim",
            tok_subj,
            {},
            idem_key=f"walk40-revoke-{int(time.time() * 1000)}",
        )
        w.rep.rec(
            "㊵ ③b 撤权后**直接调用**受理 ⇒ 服务端 **403**（裁定 §3：隐藏按钮 ≠ 放行）。"
            "同一 token 且未重新登录 ⇒ 权限取自**库**，不是 token 里的角色快照",
            status == 403,
            f"HTTP={status} body={json.dumps(body, ensure_ascii=False)[:160]}",
        )

        # —— ③c 被拒的调用没有改单据（负例不留副作用）——
        truth = api_get(f"/entrust/assignments/{id_b}", tok_subj) or {}
        w.rep.rec(
            "㊵ ③c 被拒的调用**没有**改动单据（仍 submitted、仍无人认领）—— 负例不留副作用，"
            "否则下一次跑就没有样本了",
            str(truth.get("status")) == "submitted",
            f"status={truth.get('status')!r} claimed_by={truth.get('claimed_by')!r}",
        )

        # —— ③d 界面上那份**陈旧**的确认条仍在（这是被测事实，不是缺陷）——
        d_stale = w.c.page_data()
        n_sub_stale = w.c.count('[data-act-claim-submit="1"]')
        w.rep.rec(
            "㊵ ③d 撤权后**未刷新**时确认条**仍在**、`canClaim` 仍为 true —— 页面拿的是"
            "旧权限投影（前端不轮询权限）。这不算缺陷：它正是「服务端必须独立校验」"
            "这条裁定存在的原因",
            d_stale.get("canClaim") is True and n_sub_stale == 1,
            f"页面 canClaim={d_stale.get('canClaim')!r} 确认键={n_sub_stale}（服务端已撤权）",
        )

        # ============ 四、迟到的那一下 ⇒ 403 ⇒ 页面自己刷新 ============
        print("\n-- 四、迟到的那一下 ⇒ 403 ⇒ 刷新 --", flush=True)
        t_late = w.c.tap('[data-act-claim-submit="1"]')
        d_after = w.wait_data(
            # 判据同 ㊴：取 `canClaim is False`，**不取** `not claiming`（后者在 catch 里
            # 先被置回 false，而 `load()` 还没回来 ⇒ 会提前返回并造成假红）。
            lambda x: x.get("view") not in (None, "", "loading") and x.get("canClaim") is False,
            tries=60,
            gap=0.5,
        )
        n_open1 = w.c.count('[data-act-claim-open="1"]')
        n_sub1 = w.c.count('[data-act-claim-submit="1"]')
        n_cancel1 = w.c.count('[data-act-claim-cancel="1"]')
        truth_ui = api_get(f"/entrust/assignments/{id_b}", tok_subj) or {}
        w.rep.rec(
            "㊵ ④ 迟到的那一下 ⇒ **403** ⇒ 页面**自己刷新**（`detail.js: onSubmitClaim` 的 "
            "`if (status === 403 || status === 409) return self.load()`）：`canClaim` 翻 **false**、"
            "受理入口与确认条一起从渲染树里消失，而不是留一个点了必然 403 的按钮。"
            "⚠️ 成对断言，且这一对**恰好与 ㊴ 相反**：`canCreateCase` 必须仍为 **false**"
            "（它只看 `board.status === 'claimed'`，撤权**不改状态**、单据仍 `submitted`）；"
            "㊴ 的 409 让同一字段翻 **true** ⇒ 两章合起来把「403 与 409 在详情页上留下**不同**"
            "页内痕迹」证成事实",
            bool(t_late)
            and d_after.get("canClaim") is False
            and d_after.get("canCreateCase") is False
            and n_open1 == 0
            and n_sub1 == 0
            and n_cancel1 == 0
            and str(truth_ui.get("status")) == "submitted",
            f"tap={t_late} canClaim={d_after.get('canClaim')!r} "
            f"canCreateCase={d_after.get('canCreateCase')!r} open={n_open1} "
            f"submit={n_sub1} cancel={n_cancel1} 单据 status={truth_ui.get('status')!r}",
        )
        w.shot("40-3-撤权后-页面刷新-入口消失")
    finally:
        # ============ 收尾：还原（**必须在 finally**，否则给下一次走查埋雷）============
        rc_back, out_back = flip_org_role(CODE_MGR_ONLY_B, ORG_B, "manager")
        w.rep.rec(
            "㊵ 收尾：把角色**还原**成 manager —— 本章会改库，而种子脚本的 `_member()` 是"
            "「有则跳过」、不会自己改回来；不还原，下一次走查的 ㊱ 章对照 2 会红"
            "（那一节要求 `seed-mgr-only-b` 在乙是 manager）",
            rc_back == 0,
            f"rc={rc_back} out={out_back[:160]}",
        )

    # ============ 五、对照组：复权后入口回来（排掉「页面/工具本来就是坏的」）============
    if flipped:
        print("\n-- 五、对照组：复权后入口回来 --", flush=True)
        if not w.open_workbench(CODE_MGR_ONLY_B, tag="㊵-对照"):
            w.rep.not_run("㊵ 对照组", "复权后未能重进经理工作台")
        else:
            w.wait_data(lambda x: x.get("view") is not None, tries=30, gap=0.5)
            d_re = goto_detail("㊵-对照")
            n_re = w.c.count('[data-act-claim-open="1"]')
            w.rep.rec(
                "㊵ 对照组：**还原角色**后重进**详情页**，入口**回来**（`canClaim` 翻回 true）—— "
                "排掉「页面/工具本来就是坏的」这个替代解释，也证明链路**双向可逆**。"
                "⚠️ 这一步是**重新进入页面实例**，不等于原地刷新；原地刷新由第四节覆盖",
                d_re.get("canClaim") is True and n_re == 1,
                f"canClaim={d_re.get('canClaim')!r} 命中={n_re}",
            )
            w.shot("40-4-复权后-入口回来")

    # ============ 六、诚实边界（不计入通过）============
    print("\n-- 六、诚实边界（不计入通过）--", flush=True)
    w.rep.rec(
        "㊵ 本章运行期无新增 console error",
        not w.new_errors(base_err),
        str(w.new_errors(base_err))[:200],
    )
    w.rep.rec(
        "㊵ 去向记录（**复用**，不是本章新证）：队列卡片入口上的同一分支由 **㊲ 章**"
        "（403 撤权）覆盖；本章只补**详情页**入口，不重复断言、也不顶替它。"
        "⚠️ 至此「四个入口 × 两个分支」的四格**全部有设备侧证据**；"
        "㊴ 章里那条「仍未取证」的去向记录由本章**兑现**",
        True,
        "去向：㉟ 五 = 409 @ 队列 · ㊲ = 403 @ 队列 · ㊴ = 409 @ 详情页 · 本章 = 403 @ 详情页",
    )
    w.rep.limitation(
        "㊵ 页面上的**提示文案**本身无法被工具断言",
        "403 的提示由请求层按服务端 `detail` 发出，而 toast / 弹层都不在渲染树里"
        "（与 ⑧b / ㉕D / ㊲ / ㊳ / ㊴ 同一个**工具**边界，与产品无关）。本章能证的是可观察"
        "结果：HTTP 403、该组织权限集里 claim 已消失、`canClaim` / `canCreateCase` 的取值、"
        "锚点在渲染树里的命中数、单据未被改动。",
    )


def sec_41(w: Walker) -> None:
    """㊶ 「我的委托」客户侧状态屏 —— 出口判据①的**独立**断言 ＋ 承接组织。

    本章补的是哪一格（以及**不**补哪一格）
    ------------------------------------
    S1 出口判据① 原文：`A fresh UI-created assignment survives reload`。
    「survives reload」这一半此前**只有两类证据**，都不是它字面要求的那一条：

    * ㉞ 章：换页面实例后**续接同一张草稿** —— 证的是"在途草稿没丢"，
      不是"已提交的单重新加载后仍在**列表 / 详情**里看得见"；
    * 后端（`test_exit_a_*`）：**换新会话读数据库** —— 证的是"真的落库了"，
      但它绕过了界面：界面完全可能因为可见性 / 投影 / 入口问题**看不见**它。

    ⇒ 本章补的正是缺的那一格：**重新加载后，这一单仍在列表里、也仍能打开详情**。
    ⚠️ 而「**UI-created**」那一半由 **㉞ 章** 覆盖（它在真机界面上建草稿并提交）。
    两半**互不替代**：㉞ 没有"重进之后"的断言，本章的载体单是经 API 建的。
    合起来才构成整句 —— **本章单独不成立这条判据**，汇报时不得说成"判据①已通过"。

    同时取证 S1 工作项 5「客户侧看到真实状态与**承接组织**」
    --------------------------------------------------------
    承接组织此前在界面上只能显示 `组织 #7` 这样的**裸编号**（`decorateDetail.orgText`），
    因为后端载荷里**只有 `org_id`、没有名字**。本切片给载荷补了 `org_name`，
    于是货主能看到的是组织名。本章在设备侧钉住两件事：

    * 有承接方 → 列表 / 详情显示**组织名**（本例 = `演示经营主体·甲`）；
    * 还没有承接方（**草稿**，`org_id` 为空）→ 显示「**尚未委托组织**」，
      **不是**空白、也不是编出来的名字。「未知保持未知」是这一格的重点：
      空白看起来像界面没渲染出来，而编一个名字会让货主以为已经有人接手了。

    载体单为什么经 API 建、而不是点界面表单
    --------------------------------------
    本章断言的对象是**列表页**与**重载之后**，不是建单表单（那是 ㉞ 的职责）。
    而经界面建单会在 `seed-shipper*` 名下留下**在途草稿**，与 ㉞ 章的持久化草稿
    互相消耗 —— 两章就不再各自独立成立。故本章只经 API 造**两张运行期载体单**
    （一张 submitted、一张 draft），不新增任何**种子**。

    ⚠️ 本章**零业务写入副作用**（只有建单，不改任何已存在的单据、不改库）；
    放在全量序列最末，且不依赖其它章节留下的状态。
    """
    print(
        "\n== ㊶ 「我的委托」客户侧状态屏（出口判据① 的独立断言 + 承接组织）==",
        flush=True,
    )
    base_err = w.c.errors()

    tok_op = (api_login(CODE_SHIPPER_ORGPICKER) or {}).get("access_token") or ""
    # ⚠️ 必须是 `/my-entrustments`（「我**授权出去**的组织」），**不是** `/my-orgs`
    #    （「我**所在**的组织」）。`seed-shipper-orgpicker` 是**货主** —— 它对甲 / 乙
    #    有生效的委托授权，但**不是**这两个组织的成员 ⇒ `/my-orgs` 对它返回**空列表**。
    #    本轮首跑正是在这里 FAIL（`授权组织数=0`）：拿"归属"去问"授权"，两个问题的
    #    答案当然不同（DR-0012「归属 ≠ 权限边界」）。受理屏自己用的也是这一个端点。
    ents = (api_get("/entrust/my-entrustments", tok_op) or {}).get("items") or []
    oa = next((r or {} for r in ents if str((r or {}).get("org_name") or "") == ORG_A), {})
    org_a_id = str(oa.get("org_id") or "")
    w.rep.rec(
        "㊶ 前置①：`seed-shipper-orgpicker` 对**甲组织**有生效委托授权（取 "
        "`/my-entrustments`，不是 `/my-orgs`）⇒ 它能把单提交到甲，"
        "而甲的组织名正是本章要断言的「承接组织」",
        bool(org_a_id),
        f"授权组织数={len(ents)} 甲#{org_a_id} 授权组织名="
        f"{[str((r or {}).get('org_name') or '') for r in ents]}",
    )
    if not org_a_id:
        w.rep.not_run(
            "㊶ 我的委托（客户侧状态屏）",
            "甲组织不在该身份的**授权**清单里（`/my-entrustments` 为空或不含甲）。"
            "先跑 backend/scripts/seed_entrust_orgpicker.py 再重跑。"
            "⚠️ 别拿 `/my-orgs`（我**所在**的组织）代替它 —— 货主不在组织里，那个端点对它恒为空。",
        )
        return

    # ---- 载体单 ×2：一张提交到甲（有承接组织）、一张留草稿（没有承接组织）----
    ts = int(time.time() * 1000)
    st_new, created = api_post(
        "/entrust/assignments",
        tok_op,
        {
            "title": TITLE_MINE_LIST,
            "cargo_summary": "㊶ 章载体单：客户侧列表可见性与承接组织",
        },
        f"walk41-new-{ts}",
    )
    new_id = str((created or {}).get("assignment_id") or "")
    rev = int((created or {}).get("revision") or 0)
    st_sub, submitted = (0, None)
    if new_id and rev:
        st_sub, submitted = api_post(
            f"/entrust/assignments/{new_id}/submit",
            tok_op,
            {"org_id": int(org_a_id), "expected_revision": rev},
            f"walk41-sub-{ts}",
        )
    w.rep.rec(
        "㊶ 前置②：载体单已建成并**提交到甲组织**（`submitted`）。标题取唯一值 —— "
        "列表里同时有种子单与本轮新单，靠标题 / id 才能把它们分开",
        st_new in (200, 201)
        and st_sub in (200, 201)
        and str((submitted or {}).get("status") or "") == "submitted",
        f"建单 HTTP={st_new} id={new_id!r} 提交 HTTP={st_sub} "
        f"status={(submitted or {}).get('status')!r}",
    )
    if not new_id or st_sub not in (200, 201):
        w.rep.not_run("㊶ 我的委托（客户侧状态屏）", "载体单未建成，链路断在这里")
        return

    st_draft, draft = api_post(
        "/entrust/assignments",
        tok_op,
        {"title": TITLE_MINE_DRAFT, "cargo_summary": "㊶ 章草稿载体单：承接组织为空"},
        f"walk41-draft-{ts}",
    )
    draft_id = str((draft or {}).get("assignment_id") or "")
    w.rep.rec(
        "㊶ 前置③：另建一张**草稿**载体单（`org_id` 为空）—— 它是「承接组织未知」"
        "这一格的载体：只有真的有这么一张单，才能证「尚未委托组织」不是编出来的文案",
        st_draft in (200, 201) and bool(draft_id) and (draft or {}).get("org_name") is None,
        f"建单 HTTP={st_draft} id={draft_id!r} org_name={(draft or {}).get('org_name')!r}",
    )

    # ============ 一、「我的」页上的货主侧入口（**真实点击**，不是脚本 navigate）============
    print("\n-- 一、「我的」页的货主侧入口（真实点击）--", flush=True)
    # ⚠️ `login_as()` 只做三件事：写 `dev_login_code`、清 token、`reLaunch` 回首页 ——
    #    **它本身不登录**。登录发生在 `enterRole()` 里，也就是**首页身份卡被点的那一刻**。
    #    只调 `login_as()` 会停在**未登录**状态（页面显示「未登录」、无 token）⇒
    #    `view=owner` 与 `view=org` 两个探测各拿一个 **401** ⇒ 两个入口一起隐藏。
    #    ⚠️ 此时断言会失败，但**失败的原因不是入口该不该显示**（隐藏反而是对的），
    #    而是**前置条件压根没建立** —— 本轮首跑正是这样踩的（截图 `41-1` 上是「未登录」）。
    #    故本前置必须与 `enter_role()` 成对（全脚本其它章节都如此，见 `open_workbench()`）。
    path = w.login_as(CODE_SHIPPER_ORGPICKER)
    if path != INDEX or not w.enter_role("shipper", SHIPPER):
        w.rep.rec(
            "㊶ 前置④：以 `seed-shipper-orgpicker`（**货主**）真的**登录进**小程序"
            "（`login_as` + `enter_role` 成对；只有 `login_as` ＝ 未登录）",
            False,
            f"停在 {path!r}、当前 {w.c.current_path()!r}",
        )
        w.rep.not_run("㊶ 我的委托（客户侧状态屏）", "货主身份没进到货主端，链路断在登录这一步")
        return
    w.c.nav("switchTab", "/" + MINE, MINE)
    n_entry = 0
    d_mine: dict = {}
    for _ in range(3):
        d_mine = w.wait_data(lambda x: x.get("showMineEntrust") is True, tries=20, gap=0.5)
        n_entry = w.c.count('[data-act-mine-entrust="1"]')
        if n_entry == 1:
            break
        # 探测由 `onShow` 触发；重新进「我的」让它再探一次（token 可能刚落地）
        w.c.navigate("reLaunch", "/" + INDEX)
        w.c.nav("switchTab", "/" + MINE, MINE)
    w.rep.rec(
        "㊶ ① 「我的」页上**货主侧**入口可见并真实渲染出来（`showMineEntrust=true` + 锚点命中 1）。"
        "⚠️ 它由**独立**探测驱动（`view=owner`），与同页的经理入口（`view=org`）不共用一个开关 ——"
        "共用一个开关时货主永远看不到自己的委托列表，而这类缺陷不会报错",
        d_mine.get("showMineEntrust") is True and n_entry == 1,
        f"showMineEntrust={d_mine.get('showMineEntrust')!r} 锚点命中={n_entry}",
    )
    w.shot("41-1-我的-货主侧入口-可见")
    if n_entry != 1:
        w.rep.not_run(
            "㊶ 我的委托（客户侧状态屏）",
            "货主侧入口未渲染（探测未放行）。先确认后端在 8000 上、且 `ENTRUST_ENABLED=true`。",
        )
        return

    t_entry = w.c.tap('[data-act-mine-entrust="1"]')
    d_list0 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    items0 = d_list0.get("items") or []
    row_new0 = next((it for it in items0 if str(it.get("assignmentId")) == new_id), None)
    row_draft0 = next((it for it in items0 if str(it.get("assignmentId")) == draft_id), None)
    w.rep.rec(
        "㊶ ② 真实点击入口 ⇒ 进入「我的委托」列表，五态落到 ready，且**刚提交的那一单在列表里**"
        "（这就是此前缺的那条：它证明界面能看见这张单，而不只是数据库里有它）",
        bool(t_entry) and d_list0.get("view") == "ready" and row_new0 is not None,
        f"tap={t_entry} view={d_list0.get('view')!r} 条目数={len(items0)} "
        f"命中={row_new0 is not None}",
    )
    w.shot("41-2-我的委托-列表-已提交单在列")

    # ============ 二、承接组织：有承接方 ⇒ 组织名；无承接方 ⇒ 显式说「尚未委托组织」============
    print("\n-- 二、承接组织两格（有 / 无）--", flush=True)
    w.rep.rec(
        "㊶ ③ 列表上该单的**承接组织显示为组织名**（不是 `组织 #7` 这类裸编号）。"
        "⚠️ 补这条断言是必然的：后端此前只给 `org_id`，界面只能把编号端给货主看",
        row_new0 is not None and str(row_new0.get("orgLabel")) == ORG_A,
        f"orgName={row_new0.get('orgName')!r} orgLabel={row_new0.get('orgLabel')!r}"
        if row_new0
        else "该单不在列表里",
    )
    w.rep.rec(
        "㊶ ④ **未知保持未知**：草稿载体单（没有承接组织）在列表上显示「尚未委托组织」，"
        "既**不是**空白、也**不是**一个编出来的组织名 —— 空白看起来像界面没渲染，"
        "而编一个名字会让货主以为已经有人接手了",
        row_draft0 is not None
        and str(row_draft0.get("orgName")) == ""
        and str(row_draft0.get("orgLabel")) == "尚未委托组织",
        f"orgName={row_draft0.get('orgName')!r} orgLabel={row_draft0.get('orgLabel')!r}"
        if row_draft0
        else "草稿载体单不在列表里",
    )
    w.shot("41-3-我的委托-承接组织两格")

    # ============ 三、**重进**（换页面实例）⇒ 仍在列表可见（出口判据① 的独立断言）============
    print("\n-- 三、reLaunch 重进列表 ⇒ 该单仍在 --", flush=True)
    w.c.navigate("reLaunch", "/" + MINE_LIST)
    d_list1 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    w.c.navigate("reLaunch", "/" + MINE_LIST)
    d_list2 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    items2 = d_list2.get("items") or []
    row_new2 = next((it for it in items2 if str(it.get("assignmentId")) == new_id), None)
    w.rep.rec(
        "㊶ ⑤ **出口判据① 的独立断言**：`reLaunch`（换页面实例、清空页面栈）**两次**之后，"
        "这一单**仍在返回的列表载荷里**，且状态仍是 `submitted`、承接组织仍是甲 ——"
        '⚠️ 这是本章存在的理由：㉞ 证的是"草稿续接"、后端证的是"落库"，'
        '**都不是**"重新加载后界面仍看得见它"',
        d_list1.get("view") == "ready"
        and d_list2.get("view") == "ready"
        and row_new2 is not None
        and str(row_new2.get("status")) == "submitted"
        and str(row_new2.get("orgLabel")) == ORG_A,
        f"两次 view={d_list1.get('view')!r}/{d_list2.get('view')!r} "
        f"第二次条目数={len(items2)} 命中={row_new2 is not None} "
        f"status={(row_new2 or {}).get('status')!r}",
    )
    w.shot("41-4-我的委托-reLaunch-重进后仍在")

    # ============ 四、从列表**真实点击**该行 ⇒ 详情页；再重进详情 ============
    print("\n-- 四、真点击该行 ⇒ 详情；详情再重进 --", flush=True)
    t_row = w.c.tap(f'[data-mine-id="{new_id}"]')
    # ⚠️ `tap()` 只保证"这一点位被点到了"，**不保证页面已经切过去**（导航是异步的）。
    #    紧接着 `wait_data(view != loading)` 会**读到列表页自己的 data** —— 列表页此刻的
    #    `view` 正好是 `ready` ⇒ 谓词**立刻为真** ⇒ 拿回来的是**上一页**的载荷
    #    （`assignmentId` / `detail` 全为 `None`），表现得像"详情页坏了"。
    #    （本轮第 2 跑正是这样 FAIL 的：`tap=True view='ready' assignmentId=None`。）
    #    ⇒ 先 `wait_path` 钉住**页面真的切到详情页**，再读 data。
    #    ⚠️ 别用 `nav(...)` 代替：那会把**真实点击**这条证据换成程序化导航（H2），
    #       本章第 ⑥ 格的全部价值就在"**点**这一下"。故这里是**修读数**，不是换入口。
    #    ⚠️ 判据本身一个字没动：照样要求 真点击 ∧ 编号对得上 ∧ 状态与承接组织一致。
    landed = w.c.wait_path(DETAIL, tries=40)
    d_det0 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    det0 = d_det0.get("detail") or {}
    w.rep.rec(
        "㊶ ⑥ 从列表**真实点击这一行** ⇒ 落到**这一张**的详情（编号对得上，而不是「列表第一条」），"
        "且状态与承接组织与服务端真相一致",
        bool(t_row)
        and landed
        and d_det0.get("view") == "ready"
        and str(d_det0.get("assignmentId")) == new_id
        and str(det0.get("status")) == "submitted"
        and str(det0.get("orgText")) == ORG_A,
        f"tap={t_row} 落点={'detail' if landed else '未切页'} "
        f"view={d_det0.get('view')!r} page 的 assignmentId="
        f"{d_det0.get('assignmentId')!r} status={det0.get('status')!r} orgText={det0.get('orgText')!r}",
    )
    w.shot("41-5-详情页-来自列表真实点击")

    w.c.navigate("reLaunch", f"/{DETAIL}?assignment_id={new_id}")
    # 与 ⑥ 同一种读数竞态：`reLaunch` 是异步的，紧接着读 data 可能拿到**上一页**的载荷。
    # 故同样先钉住落点（这一步是**程序化导航**，只用于验"重进后详情仍可见"，不承担"真实点击"）
    w.c.wait_path(DETAIL, tries=40)
    d_det1 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    det1 = d_det1.get("detail") or {}
    w.rep.rec(
        "㊶ ⑦ **详情页也重进一次**：换页面实例后详情仍可见、状态与承接组织不变 ——"
        '判据① 说的是 `survives reload`，而"列表看得见"与"详情看得见"是**两条**独立通路'
        "（列表走投影列表接口，详情走单张接口 + 工作台载荷）",
        d_det1.get("view") == "ready"
        and str(det1.get("status")) == "submitted"
        and str(det1.get("orgText")) == ORG_A,
        f"view={d_det1.get('view')!r} status={det1.get('status')!r} orgText={det1.get('orgText')!r}",
    )
    w.shot("41-6-详情页-reLaunch-重进后仍在")

    # ============ 五、负例：这不是"全表浏览"============
    print("\n-- 五、负例：另一个货主的列表里不含它 --", flush=True)
    tok_other = (api_login(CODE_SHIPPER) or {}).get("access_token") or ""
    other = api_get("/entrust/assignments?view=owner&page=1&size=20", tok_other) or {}
    other_ids = [str((it or {}).get("assignment_id")) for it in (other.get("items") or [])]
    w.rep.rec(
        "㊶ ⑧ 负例：**另一个货主**（`seed-shipper`）的 `view=owner` 列表里**不含**这张单 ——"
        '`view=owner` 的可见性边界是"我自己的"，不是"所有人的"。'
        '⚠️ 没有这条，上面所有"看得见"的断言都可能是"列表其实是全表"',
        new_id not in other_ids,
        f"对方 total={other.get('total')!r} 含本章载体单={new_id in other_ids}",
    )

    # ============ 六、诚实边界 ============
    w.rep.rec(
        "㊶ 去向记录（**复用**，不是本章新证）：出口判据① 里「**UI-created**」那一半由 "
        "**㉞ 章**覆盖（真机走真实入口建草稿 → 提交 → 落到详情），本章补的是"
        "「**重进后仍在列表 / 详情可见**」那一半。两半**互不替代** ⇒"
        "本章**单独不构成**判据① 通过；整句是否成立由 HO 判",
        True,
        "去向：㉞ = UI-created · 本章 = survives reload（列表 + 详情两条通路）",
    )
    w.rep.rec(
        "㊶ 本章 console 无未豁免 error（页面自身的运行期错误）",
        len(w.new_errors(base_err)) == 0,
        str(w.new_errors(base_err))[:200],
    )
    w.rep.limitation(
        "㊶ **下拉刷新（真手势）与分页第 2 页**无法由本工具触发",
        "本页 `enablePullDownRefresh=true`（`onPullDownRefresh` → `load()`），"
        "但下拉是**真实触摸手势**，走查工具只能发 tap / 程序化导航，造不出这个手势；"
        "分页第 2 页需要 >20 张单（`PAGE_SIZE=20`），而种子里没有那么多。"
        "⇒ `onPullDownRefresh` 与 `pageHint` 的多页分支**未取得设备证据**（与产品无关，"
        "是工具边界）。本章能证的是：首屏取数、五态、列表三项内容、真实点击进详情、"
        "两条通路各自的 reLaunch 重进、以及 `view=owner` 的可见性负例。",
    )


def sec_42(w: Walker) -> None:
    """㊷ 触底分页探针（DR-0018 的 A.P-2）。

    本章补的是 DR-0018 裁定二·A 的探针 P-2：「列表滚动到底 / 触发加载更早一页」。

    P-1 为什么**不在本章重跑**
    -------------------------
    矩阵原文里 P-1 的 `why_now` 写着「走查工具此前从未向 input 写入过文本
    （全仓 39 章无先例）」—— **这句是错的**。本轮复核发现 `input_text` 已被用过
    **6 处**（`[data-field="title"]` 三处、`[data-df="task-title"]` 两处、工作台
    表单标题一处），而且 **㉞ 章那条断言证的就是 P-1 问的那件事**：

        ㉞ 真实输入触发 `bindinput`（标题进了页面 data，不是只写进渲染层）

    再加上 ㉞ 章提交后详情页显示的正是本页填的那一句（文本真的落进了服务端），
    P-1 的两个分句（键入真的接上、文本真的落库）**都已有有效证据**。
    按裁定二·A·2「已有有效证据 ⇒ 不复做」（把「系统返回键」从清单删除用的
    就是同一条理由），P-1 记 PASS 并注明证据来源，本章**不重复劳动**。

    P-2 为什么需要一个新靶子
    ------------------------
    矩阵原文要断的是「更早一页进入列表 ＋ `onReachBottom` 真的跑过」。
    而复核发现：**全仓 `onReachBottom` 0 处** —— 这个探针此前**没有可观测对象**。
    更值得记的是，缺它的那一页正是本切片刚建的「我的委托」：`load()` 只取第 1 页
    （`PAGE_SIZE = 20`），界面却会诚实地显示「第 1/2 页」。⇒ 货主提了 25 张单时
    **第 21 张之后永远看不到**，而**缺陷长得像功能**（"有分页提示"）。
    分页提示不是分页能力 —— 这正是本章开头那条断言的来历。

    所以本章先给该页补上 `onReachBottom`（**修的是真缺陷，不是为探针造靶子**），
    再用工具的原生 `pageScrollTo` 驱动触底，断言第 2 页真的进来。

    驱动方式，以及这一章能证明什么
    ------------------------------
    `Client.scroll_to(top)` 的底层是 `automation_viewport_action pageScrollTo`
    —— **页面级真滚动**，与小程序 `onReachBottom` 是同一个触发源。
    ⚠️ 工具**没有滑动手势 API**（`swipe` / 触摸手势一个都没有），所以「下拉刷新」
    那条（P-3）仍造不出来；但「滚到底」有原生通路 —— P-2 与 P-3 看起来同类，
    可脚本化程度**并不相同**，这个差别本身就是本章要留下的设计约束。

    ⚠️ 副作用：本章经 API 建 `PROBE_PAGE_TARGET` 张**草稿**单（挂在
    `seed-shipper-orgpicker` 名下；不改任何已存在的单据、不改库）。
    故与 ㊶ 一样放在全量序列**最末**。
    """
    print("\n== ㊷ 触底分页探针（DR-0018 的 A.P-2）==", flush=True)
    base_err = w.c.errors()

    w.rep.rec(
        "㊷ P-1 去向记录（**复用**，不是本章新证）：会话输入框键入 ＋ 该文本真的落进服务端 —— "
        "证据在 ㉞ 章（`input_text` → `bindinput` → `data.form.title`，提交后详情页标题一致）；"
        "矩阵里「全仓无先例」那句原文**本轮已订正**（实为 6 处先例、其中一处已断言 `bindinput`）",
        True,
        "去向：㉞ 章「真实输入触发 bindinput」= P-1 的两个分句；本章不重跑（裁定二·A·2）",
    )

    tok = (api_login(CODE_SHIPPER_ORGPICKER) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("㊷ 触底分页探针", "API 登录失败，取不到该身份的 token ⇒ 前置不成立")
        return

    # ---- 前置①：把 `view=owner` 的条数抬到**超过一页** ----
    before = api_get("/entrust/assignments?view=owner&page=1&size=1", tok) or {}
    total0 = int(before.get("total") or 0)
    ts = int(time.time() * 1000)
    created = 0
    st_last = 0
    for i in range(PROBE_PAGE_TARGET):
        st_last, _ = api_post(
            "/entrust/assignments",
            tok,
            {
                "title": f"{TITLE_PROBE_PAGE} #{i + 1:02d}",
                "cargo_summary": "㊷ 章分页探针载体单（草稿，不提交）",
            },
            f"walk42-{i}-{ts}",
        )
        if st_last in (200, 201):
            created += 1
    after = api_get("/entrust/assignments?view=owner&page=1&size=1", tok) or {}
    total1 = int(after.get("total") or 0)
    w.rep.rec(
        "㊷ 前置①：经 API 批量建**草稿**载体单，使 `view=owner` 的总数**超过一页** —— "
        "没有这一步「第 2 页」无从产生，P-2 只能记「产品侧尚无实现」。"
        "⚠️ 建的是草稿：不动任何已存在的单据、不改库",
        created == PROBE_PAGE_TARGET and total1 > MINE_PAGE_SIZE,
        f"起点 total={total0} 新建 {created}/{PROBE_PAGE_TARGET}（末次 HTTP={st_last}）"
        f" ⇒ total={total1}（页长 {MINE_PAGE_SIZE}）",
    )
    if total1 <= MINE_PAGE_SIZE:
        w.rep.not_run(
            "㊷ 触底分页探针",
            f"总数 {total1} 未超过一页长 {MINE_PAGE_SIZE}，第 2 页**无从产生** —— 不硬断。",
        )
        return

    # ---- 前置②：页面通道登录（`login_as` + `enter_role` 成对）----
    path = w.login_as(CODE_SHIPPER_ORGPICKER)
    if path != INDEX or not w.enter_role("shipper", SHIPPER):
        w.rep.rec(
            "㊷ 前置②：以 `seed-shipper-orgpicker` 真的**登录进**小程序"
            "（`login_as` + `enter_role` 成对；只调 `login_as` ＝ 停在未登录 ⇒ 页面侧全拿 401）",
            False,
            f"停在 {path!r}、当前 {w.c.current_path()!r}",
        )
        w.rep.not_run("㊷ 触底分页探针", "页面通道没进到货主端，链路断在登录")
        return

    w.c.navigate("reLaunch", "/" + MINE_LIST)
    w.c.wait_path(MINE_LIST, tries=40)
    d1 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    items1 = d1.get("items") or []
    hint1 = str(d1.get("pageHint") or "")
    expect1 = min(total1, MINE_PAGE_SIZE)
    w.rep.rec(
        f"㊷ ① 首屏只取**第 1 页**（{len(items1)} 张 = min(总数 {total1}, 页长 {MINE_PAGE_SIZE})），"
        "且 `pageHint` 如实说明还有更早的（『第 1/N 页』）—— 这是触底之前应有的形态",
        d1.get("view") == "ready"
        and len(items1) == expect1
        and d1.get("hasMore") is True
        and ("1/" in hint1),
        f"view={d1.get('view')!r} 条目数={len(items1)}（期望 {expect1}）"
        f" hasMore={d1.get('hasMore')!r} reachCount={d1.get('reachCount')!r} "
        f"pageHint={hint1!r}",
    )
    w.shot("42-1-我的委托-第1页")
    if len(items1) != expect1:
        w.rep.not_run(
            "㊷ 触底分页探针",
            f"首屏条目数 {len(items1)} ≠ 期望 {expect1}，链路断在取数",
        )
        return

    # ---- 触底：原生 `pageScrollTo`（页面级真滚动）----
    print("\n-- 触底：原生 pageScrollTo --", flush=True)
    sc = w.c.scroll_to(MINE_SCROLL_BOTTOM)
    d2 = w.wait_data(lambda x: len(x.get("items") or []) > len(items1), tries=30, gap=0.5)
    items2 = d2.get("items") or []
    expect2 = min(total1, MINE_PAGE_SIZE * 2)
    w.rep.rec(
        "㊷ ② **触底真的把更早一页加载进来了**：原生 `pageScrollTo` 滚到底 ⇒ "
        "`onReachBottom` 触发 ⇒ 列表变长。⚠️ 这条能成立，是因为本轮先给该页补了 "
        "`onReachBottom`：在此之前它只取第 1 页，第 21 张之后的委托**永远看不到**",
        bool(sc) and len(items2) == expect2 and len(items2) > len(items1),
        f"scroll={sc} 条目数 {len(items1)} → {len(items2)}（期望 {expect2}）"
        f" reachCount={d2.get('reachCount')!r} loadingMore={d2.get('loadingMore')!r}",
    )
    w.shot("42-2-我的委托-触底后第2页")

    reach = int(d2.get("reachCount") or 0)
    w.rep.rec(
        "㊷ ③ 失败归因（**只在 ② 未过时才有信息量**，此处恒记以便归档）：触底之后 "
        "`onReachBottom` 的调用计数 —— `0` ⇒ 工具的 `pageScrollTo` **没能**触发页面"
        "触底事件（**工具边界**）；`≥1` 而列表没变长 ⇒ 事件到了、加载没成"
        "（**产品侧问题**）。⚠️ 两种原因不能混为一谈：混在一起就只剩一句"
        "『分页没验成』，分不清是工具不行还是功能不行",
        True,
        f"reachCount={reach}（0 = 工具没造出触底；≥1 = 造出了）",
    )

    hint2 = str(d2.get("pageHint") or "")
    w.rep.rec(
        "㊷ ④ 第 2 页到达后 `pageHint` 与 `hasMore` **同步**更新 —— 『分页提示』与"
        "『分页能力』必须同时为真：只改提示不改列表，就是本章开头说的那种『缺陷长得像功能』",
        ("2/" in hint2) and d2.get("hasMore") == (len(items2) < total1),
        f"pageHint={hint2!r} hasMore={d2.get('hasMore')!r} page={d2.get('page')!r} "
        f"（服务端总数 {total1}，已显示 {len(items2)}）",
    )

    # ---- 负例：界面第 2 页 == 服务端 page=2（逐条）----
    srv = api_get(f"/entrust/assignments?view=owner&page=2&size={MINE_PAGE_SIZE}", tok) or {}
    srv_ids = [str((it or {}).get("assignment_id")) for it in (srv.get("items") or [])]
    ui_ids = [str(it.get("assignmentId")) for it in items2[MINE_PAGE_SIZE:]]
    w.rep.rec(
        "㊷ ⑤ 负例（对照服务端真相）：界面第 2 页拿到的编号序列与 "
        "`GET /assignments?view=owner&page=2` **逐条一致** —— 没有这条，② 只证明"
        "『列表变长了』，不能证明『长出来的就是第 2 页』（多出来的也可能只是"
        "第 1 页被重复追加了一遍）",
        bool(srv_ids) and srv_ids == ui_ids,
        f"服务端 page2={srv_ids} 界面追加={ui_ids}",
    )

    w.rep.rec(
        "㊷ 本章 console 无未豁免 error（页面自身的运行期错误）",
        len(w.new_errors(base_err)) == 0,
        str(w.new_errors(base_err))[:200],
    )
    w.rep.rec(
        "㊷ 去向记录：**P-3（下拉刷新真手势）不在本章结论里** —— 工具的动作面只有 "
        "`text` / `tap` / `longpress` / `scrollTo` / `input` 五个 element action "
        "＋ 页面级 `pageScrollTo`，**没有任何 swipe / 触摸手势**（已逐方法核对 "
        "`wechatide_client.py` 的 `Client`）。P-3 的 `tool_limitation` / "
        "`alternative_path` / `design_constraint` 由本轮同一份矩阵文档回填，"
        "**不由真机章声称**",
        True,
        "去向：DEMO-1-gesture-matrix.yaml 的 P-3（status = LIMITATION）",
    )
    w.rep.limitation(
        "㊷ **下拉刷新（真手势）仍无法由本工具触发**",
        "本页 `enablePullDownRefresh=true`（`onPullDownRefresh` → `load()`），但下拉是"
        "**真实触摸手势**，而工具链只有上述五个 element action ＋ `pageScrollTo`，"
        "**没有 swipe**。⚠️ 本章新证的是它的**对照面**：同属『滚动类交互』，"
        "**触底有原生通路、下拉没有** ⇒ 两者的可脚本化程度并不相同。界面设计要按这个"
        "差别走：分页可以走触底（本章已证可造），而『刷新后可复现』这类断言需要"
        "页内刷新入口，不能只靠下拉。",
    )


def sec_43(w: Walker) -> None:
    """㊸ 主演示第 1–3 步（合同 §10.1 Witnessed business script）——设备侧运行取证。

    合同 §10.1 前三步的原文：

    1. Customer submits a new assignment; A1 claims it.
    2. A1 uploads the sample quotation and invokes AG-02.
    3. A1 corrects one field; open the same artifact from the workbench.

    HO 0917-3 的执行顺序把它列为「第 1–3 步运行取证」，并明确**不得把 runner 单测或
    Node e2e 称为设备侧完整链路** ⇒ 本章的价值就是「在真实模拟器上、用真实点击、
    把这三步**连着**走一遍」。

    为什么单独一章，而不是复用 ㉞ / ㊳ / ㊶ / ㊷
    ------------------------------------------
    既有各章各自只覆盖**一跳**（㉞ 客户提交、㊳ 受理、㊶㊷ 列表），且都止步于"状态变了"。
    合同第 3 步要的是**跨页的同一性**："更正一个字段，然后**从工作台打开同一份**成果"
    —— 这条只有把三步连起来走才成立：中间任何一环断裂（附件没提取 / 提案没产出 /
    更正没生效），D1-04「更正出现在所有共享视图」的证据就不成立，而在单跳章节里
    它照样会是绿的。

    本节覆盖（措辞与手段一一对应）
    ----------------------------
    一、第 1 步：客户（`seed-shipper`）**真实点击**建单提交 → A1（`seed-owner`）
        在工作台**真实点击**受理（服务端状态以 API 直证）。
    二、第 2 步：A1 在会话屏走「内置示例报价单 → 上传 → 提取 → 引用 → 调 AG-02」，
        等作业终态，断言产出了提案、且来源里含 `attachment_text`、无未核验来源。
    三、第 3 步：采纳为成果 → 成果页**真实编辑一个字段并保存新版本** →
        **设为生效版本**（页内确认条）→ **经工作台 → 委托卡 → 详情页 → 成果槽位**
        打开同一份成果，断言是同一个 `artifact_id` 且显示的是更正后的内容。

    ⚠️ 诚实边界（按档登记、**不计入通过**）
    * 原生文件选择器（`wx.chooseMessageFile`）是 OS 级弹层、不在渲染树里，走查工具
      够不着它的选择项 ⇒ 「从系统里选一个文件」这一格记 `LIMITATION`。本章另走
      **内置示例**通路：它复用同一条 `uploadQuote`，覆盖的是**真实上传链路**，
      但不是"从系统文件里挑一个"这一步 —— 两者不互相替代。
    * 「更正一个字段」按合同改的是**成果**上的字段（成果页的编辑态）。会话屏的采纳
      **刻意没有编辑态**（采纳＝确认模型产出；改内容属成果页），这条差异写进 note，
      不假装修正发生在会话屏。
    """
    print("\n== ㊸ 主演示第 1–3 步（合同 §10.1，真实点击）==", flush=True)

    err_base = w.c.errors()

    def my_org_id(code: str, name: str) -> str:
        """按组织名取 org_id（不写死 id：种子重铺会变）。"""
        tok = (api_login(code) or {}).get("access_token") or ""
        if not tok:
            return ""
        items = (api_get("/entrust/my-orgs", tok) or {}).get("items") or []
        for r in items:
            if str((r or {}).get("name") or "") == name:
                return str((r or {}).get("org_id") or "")
        return ""

    def clear_intake_draft() -> bool:
        """清当前身份的在途载荷。

        该 Storage 键**跨轮次保留**：不清的话上一次走查留下的草稿会让本节的
        "新建委托"变成"续接到旧草稿"，于是后面按标题找单会找不到那张**新的**。
        键按 `user_id` 推导（与 intake.js 同一条口径），不是"扫所有前缀"。
        """
        key = w.c.evaluate(
            "function(){var r=wx.getStorageSync('user_info');if(!r)return '';"
            "var u=null;try{u=JSON.parse(r)}catch(e){return ''}"
            "var id=(u&&u.user_id!=null)?String(u.user_id):'';"
            "return id?('entrust_intake_draft_'+id):'';}"
        )
        if not key:
            return False
        w.c.remove_storage(str(key))
        w.c.remove_storage(str(key))  # 双保险：抗一次无声失败
        return True

    def api_truth_status(aid: str, tok: str) -> dict:
        return api_get(f"/entrust/assignments/{aid}", tok) or {}

    # ==================== 一、第 1 步（前半）：客户提交 ====================
    print("\n-- 一、第 1 步 · 客户（seed-shipper）真实点击提交一张新委托 --", flush=True)
    if w.login_as(CODE_SHIPPER) != INDEX:
        w.rep.rec(
            "㊸ 前置 · seed-shipper 登录",
            False,
            f"未停在身份页（{w.c.current_path()}）",
        )
        return
    if not w.enter_role("shipper", SHIPPER):
        w.rep.rec("㊸ 前置 · 真点击身份卡进货主工作台", False, w.c.current_path())
        return
    time.sleep(1.2)
    w.rep.rec(
        "㊸ 前置 · 清掉按 `user_id` 推导的在途载荷（跨轮次残留会把'新建'变成'续接'）",
        clear_intake_draft(),
        "entrust_intake_draft_<user_id> 已清（清两次）",
    )

    n_center = w.c.count('[data-key="publish"]')
    if n_center == 1:
        w.c.tap('[data-key="publish"]')
        via_cargo = "真实点击自定义 tabBar 凸起"
    else:
        w.c.nav("navigateTo", "/" + PUBLISH_CARGO, PUBLISH_CARGO)
        via_cargo = f"URL 直进（凸起锚点命中 {n_center} 个 —— 自定义 tabBar 是组件）"
    ok_cargo = w.c.wait_path(PUBLISH_CARGO, 30)
    time.sleep(1.3)
    w.c.set_data({"form.cargo_name": CANON_CARGO, "form.weight_t": CANON_QTY})
    time.sleep(0.6)
    n_ent = w.c.count('[data-act-entrust="1"]')
    t_ent = w.c.tap('[data-act-entrust="1"]') if n_ent == 1 else False
    ok_intake = w.c.wait_path(INTAKE, 30)
    time.sleep(1.5)
    w.shot("43-1-受理屏")
    w.rep.rec(
        "㊸ 第1步 · 发布货源 → 受理屏（真实点击；货名/货量随草稿带过去）",
        bool(ok_cargo and t_ent and ok_intake),
        f"via={via_cargo} cargo_page={ok_cargo} 锚点命中 {n_ent} path={w.c.current_path()}",
    )
    if not ok_intake:
        w.rep.not_run("㊸ 第1步 · 客户提交", "未进入受理屏，链路断在这里")
        return

    pg_intake = w.wait_data(lambda x: x.get("view") == "ready", tries=40, gap=0.5)
    targets = pg_intake.get("targets") or []
    org_names = [str((t or {}).get("orgName") or "") for t in targets]
    tid = ""
    for t in targets:
        if str((t or {}).get("orgName") or "") == ORG_WORKBENCH:
            tid = str((t or {}).get("orgId") or "")
    w.rep.rec(
        "㊸ 第1步 · 受理屏的目标清单里有「演示经营主体·工作台」"
        "（客户 A 授权出去的组织 —— 不是「我所在的组织」，后者会给出能选但必然 403 的选项）",
        bool(tid),
        f"targets={org_names}",
    )
    if not tid:
        w.rep.not_run("㊸ 第1步 · 客户提交", f"目标组织缺失：targets={org_names}")
        return

    w.c.set_data({"form.title": A1_MAIN_TITLE})
    time.sleep(0.5)
    n_org = w.c.count(f'[data-org-id="{tid}"]')
    t_org = w.c.tap(f'[data-org-id="{tid}"]')
    time.sleep(0.4)
    t_submit = w.c.tap('[data-act-submit-intake="1"]')
    ok_detail = w.c.wait_path(DETAIL, 45)
    time.sleep(1.2)
    w.shot("43-2-提交后落详情")
    w.rep.rec(
        "㊸ 第1步 · 真实点击「提交委托」⇒ 落到该委托的详情页（不是停在原页、也不是落首页）",
        bool(t_submit and ok_detail),
        f"org={tid}（锚点 {n_org}、tap={t_org}）、submit={t_submit}、path={w.c.current_path()}",
    )

    # ⚠️ `api_login` 返回的是**整个响应 dict**，不是 token 串 ——
    #    直接把返回值当 token 传，会在 `"Bearer " + token` 处抛
    #    `TypeError: can only concatenate str (not "dict") to str`，
    #    而它崩在**章节中途**（前 6 项已 PASS），看起来像"第 7 项之后没跑"。
    #    既有章节一律写 `(api_login(code) or {}).get("access_token") or ""`，照抄。
    tok_owner = (api_login(CODE_OWNER) or {}).get("access_token") or ""
    org_id = my_org_id(CODE_OWNER, ORG_WORKBENCH)
    aid = prefer_anchor(find_submitted(org_id, A1_MAIN_TITLE, tok_owner) if org_id else "")
    w.rep.rec(
        "㊸ 第1步 · 该委托**已在库里**且是 `submitted`（按标题唯一命中，API 直证）",
        bool(aid),
        f"aid={aid} org={org_id}（{ORG_WORKBENCH}）",
    )
    if not aid:
        w.rep.not_run("㊸ 第1步 · A1 受理及其后全部断言", "未能按标题定位新建的委托")
        return

    # ==================== 一、第 1 步（后半）：A1 受理 ====================
    print("\n-- 一、第 1 步 · A1（seed-owner）在工作台真实点击受理 --", flush=True)
    if not w.open_workbench(CODE_OWNER, tag="㊸"):
        w.rep.not_run("㊸ 第1步 · A1 受理", "未能进入经理工作台")
        return
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)

    sel_claim = f'[data-act-claim="{aid}"]'
    w.c.scroll_into(sel_claim)
    n_claim = w.c.count(sel_claim)
    w.shot("43-3-工作台-待受理")
    w.rep.rec(
        "㊸ 第1步 · A1 在工作台看到这张**新提交**的委托，受理入口可被唯一命中",
        n_claim == 1,
        f"{sel_claim} 命中 {n_claim}",
    )
    if n_claim != 1:
        w.rep.not_run(
            "㊸ 第1步 · A1 真实点击受理",
            f"受理入口未出现（命中 {n_claim}）—— 队列可能未包含该单，或它已不是 submitted",
        )
        return
    t_claim_open = w.c.tap(sel_claim)
    time.sleep(0.5)
    sel_submit = f'[data-act-claim-submit="{aid}"]'
    n_confirm = w.c.count(sel_submit)
    t_claim_submit = w.c.tap(sel_submit)
    truth = {}
    for _ in range(40):
        truth = api_truth_status(aid, tok_owner)
        if str(truth.get("status")) == "claimed":
            break
        time.sleep(0.5)
    w.shot("43-4-受理成功")
    w.rep.rec(
        "㊸ 第1步 · 真实点击受理 ⇒ 服务端 `submitted → claimed` 且 `claimed_by` 已落"
        "（**API 直证**，不只看界面）",
        str(truth.get("status")) == "claimed" and bool(truth.get("claimed_by")),
        f"展开={t_claim_open}（确认条 {n_confirm} 个）提交={t_claim_submit} "
        f"status={truth.get('status')!r} claimed_by={truth.get('claimed_by')!r}",
    )

    # ==================== 二、第 2 步：上传样报价单 + 调 AG-02 ====================
    print("\n-- 二、第 2 步 · 会话屏：上传样报价单 → 提取 → 引用 → AG-02 --", flush=True)
    w.c.scroll_into(f'[data-act-session="{aid}"]')
    t_sess = w.c.tap(f'[data-act-session="{aid}"]')
    ok_sess = w.c.wait_path(SESSION, 30)
    time.sleep(1.5)
    pg_sess = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    w.shot("43-5-专属会话屏")
    w.rep.rec(
        "㊸ 第2步 · 工作台卡片的「会话」入口真实点击 ⇒ 进该委托的专属会话屏",
        bool(t_sess and ok_sess),
        f"path={w.c.current_path()} sessionId={pg_sess.get('sessionId')!r}",
    )

    # —— 原生文件选择器这一格：如实记「工具不可验证」——
    n_pick = w.c.count('[data-act-pick-quote="1"]')
    w.rep.limitation(
        "㊸ 第2步 · 「上传报价单（从系统里选文件）」",
        f"入口可被唯一命中（{n_pick} 个），但点击会打开 **OS 级原生文件选择器** —— "
        "它是系统弹层、不在小程序渲染树里，走查工具够不着它的选择项"
        "（技能 miniapp-device-walkthrough 的负例清单实测过）。**不计入通过**。"
        "要拿到这一格的证据需要 OS 级输入通道（本项目 ⑧b/㊶ 用过），"
        "或让界面另给一条内置样本通路 —— 本章走的是后者（下一格）。",
    )

    # ⭐ 沿链取"**第一个缺失事件**"（HO 2026-09-19）：点击是否命中 → 处理器是否真的跑了
    #    （`uploading` 翻真）→ 是否落了附件行 → 是否提取完成。四个读数缺一个，
    #    就分不清"没点到"／"点了但写样本文件失败"／"上传失败"／"上传成功但提取失败"。
    #    ⛔ 这不是新诊断系统，只是把这条链上本来就存在的四个事件**如实报出来**。
    n_sample = w.c.count('[data-act-sample-quote="1"]')
    d_before = w.c.page_data()
    att_before = len(d_before.get("attachments") or [])
    sess_before = str(d_before.get("sessionId") or "")
    t_sample = w.c.tap('[data-act-sample-quote="1"]')
    saw_uploading = False
    for _ in range(20):
        if w.c.page_data().get("uploading") is True:
            saw_uploading = True
            break
        time.sleep(0.15)
    pg_att = w.wait_data(
        lambda x: any(
            str((a or {}).get("extractStatus")) == "done" for a in (x.get("attachments") or [])
        ),
        tries=80,
        gap=0.5,
    )
    atts = pg_att.get("attachments") or []
    done_atts = [a for a in atts if str((a or {}).get("extractStatus")) == "done"]
    w.shot("43-6-附件已上传并提取")
    w.rep.rec(
        "㊸ 第2步 · 真实点击「用内置示例报价单」⇒ 上传 + **提取完成**"
        "（只有 done 的附件对 Agent 才是文本，否则它只是个文件名）",
        bool(t_sample) and len(done_atts) >= 1,
        # ⭐ 必须把 `tap=` 打进读数：`attachments=0` 同时对应「点击没落到元素上」与
        #    「点了但上传失败」两种原因（首跑就卡在这里 —— 后端日志显示那一轮**根本没发**
        #    `POST /entrust/attachments`，而读数里看不出是哪种）。
        f"锚点={n_sample} tap={t_sample} uploading={saw_uploading} sess={sess_before!r} "
        f"att_before={att_before} attachments={len(atts)} done={len(done_atts)} "
        f"notice={str(pg_att.get('attachNotice') or '')[:60]!r}",
    )
    notice = str(pg_att.get("attachNotice") or "")
    w.rep.rec(
        "㊸ 第2步 · 提取结果被**如实报出**（不是合成一句'上传成功'）",
        "Agent 已能读到" in notice,
        f"attachNotice={notice[:120]!r}",
    )
    if not done_atts:
        w.rep.not_run("㊸ 第2步 · 引用附件调 AG-02", "附件未提取完成（没有可引用的文本）")
        return

    t_use = w.c.tap('[data-act-use-attachment="1"]')
    pg_job = w.wait_data(
        lambda x: (
            bool(x.get("jobs"))
            and str(((x.get("jobs") or [{}])[0] or {}).get("status")) in ("succeeded", "failed")
        ),
        tries=120,
        gap=1.0,
    )
    jobs = pg_job.get("jobs") or []
    j0 = jobs[0] if jobs else {}
    w.shot("43-7-AG02-终态")
    w.rep.rec(
        "㊸ 第2步 · 真实点击「让 Agent 解析这份报价单」⇒ 作业跑到**终态**（不是只停在排队）",
        bool(t_use) and str(j0.get("status")) == "succeeded",
        f"status={j0.get('status')!r} jobId={j0.get('jobId')!r}",
    )
    w.rep.rec(
        "㊸ 第2步 · 作业产出**提案**并在页面上列出条数（提案 ≠ 成果，才需要人工采纳）",
        int(j0.get("proposalCount") or 0) >= 1,
        f"proposalCount={j0.get('proposalCount')}",
    )

    jid = str(j0.get("jobId") or "")
    jrow = (api_get(f"/entrust/agent/jobs/{jid}", tok_owner) or {}).get("job") or {} if jid else {}
    env = jrow.get("envelope") if isinstance(jrow, dict) else None
    env = env if isinstance(env, dict) else {}
    kinds = [str((r or {}).get("kind")) for r in (env.get("source_refs") or [])]
    unsrc = env.get("unverified_sources") or []
    w.rep.rec(
        "㊸ 第2步 · 作业来源里含 `attachment_text`（Agent 读的是**附件文本**，不是文件名）"
        "—— API 直证",
        "attachment_text" in kinds,
        f"kinds={kinds} jobId={jid}",
    )
    w.rep.rec(
        "㊸ 第2步 · 没有未核验来源（来源核对覆盖 `findings` 内的嵌套引用，见 PR #131）",
        len(unsrc) == 0,
        f"unverified_sources={str(unsrc[:3])[:160]}",
    )

    # ==================== 三、第 3 步：更正一个字段 + 跨视图同一份 ====================
    print("\n-- 三、第 3 步 · 采纳 → 更正一个字段 → 从工作台打开同一份成果 --", flush=True)
    if not j0.get("canAdopt"):
        w.rep.not_run("㊸ 第3步 · 采纳为成果", "页面未给出采纳入口（作业未成功或提案为空）")
        return
    t_adopt = w.c.tap('[data-act-adopt="1"]')
    time.sleep(0.4)
    n_adopt_cf = w.c.count('[data-act-adopt-confirm="1"]')
    t_adopt_cf = w.c.tap('[data-act-adopt-confirm="1"]')
    pg_card = w.wait_data(lambda x: len(x.get("cards") or []) > 0, tries=60, gap=0.5)
    cards = pg_card.get("cards") or []
    aid_art = str((cards[0] or {}).get("artifactId") or "")
    w.shot("43-8-采纳成成果")
    w.rep.rec(
        "㊸ 第3步 · 真实点击「采纳为成果」→ 页内确认条 → 确认 ⇒ 提案变成成果",
        bool(t_adopt and t_adopt_cf) and bool(aid_art),
        f"ask={t_adopt} confirm={t_adopt_cf}（确认条 {n_adopt_cf} 个）artifact_id={aid_art}",
    )
    if not aid_art:
        w.rep.not_run("㊸ 第3步 · 更正与跨视图", "未取到新成果的 artifact_id")
        return

    w.c.scroll_into(f'[data-artifact_id="{aid_art}"]')
    t_art = w.c.tap(f'[data-artifact_id="{aid_art}"]')
    ok_art = w.c.wait_path(ARTIFACT, 30)
    time.sleep(1.3)
    pg_a = w.wait_data(lambda x: x.get("artifact") is not None, tries=40, gap=0.5)
    w.shot("43-9-成果页")
    w.rep.rec(
        "㊸ 第3步 · 从**会话屏的成果卡**真实点击进入成果页（同一份）",
        bool(t_art and ok_art) and str(pg_a.get("artifactId")) == aid_art,
        f"path={w.c.current_path()} artifactId={pg_a.get('artifactId')!r}（期望 {aid_art}）",
    )

    art = pg_a.get("artifact") or {}
    rev_before = len(pg_a.get("revisions") or [])
    t_edit = w.c.tap('[data-act-edit="1"]')
    time.sleep(0.8)
    pg_form = w.wait_data(lambda x: bool(x.get("formFields")), tries=25, gap=0.4)
    form_fields = pg_form.get("formFields") or []
    # ⚠️ 取 `data-idx` 必须用**编辑态的 `formFields` 下标**，不能用查看态
    #    `artifact.fields` 的下标：两者不保证一一对应（编辑态会排除它不显示的字段），
    #    拿错下标会去改**另一个字段** —— 而页面照常保存成功，断言只会看到
    #    "值没变成我要的那个"，看起来像"保存没生效"。
    target_idx = None
    for i, f in enumerate(form_fields):
        if not (f or {}).get("unknown") and str((f or {}).get("kind") or "text") != "json":
            target_idx = i
            break
    if target_idx is None:
        w.rep.not_run(
            "㊸ 第3步 · 更正一个字段",
            f"编辑态没有可改的文本字段：formFields={len(form_fields)} 个",
        )
        return
    old_val = str((form_fields[target_idx] or {}).get("text") or "")
    new_val = (old_val + "（走查更正）") if old_val else "走查更正值"

    n_input = w.c.count(f'[data-idx="{target_idx}"]')
    t_input = w.c.input_text(f'[data-idx="{target_idx}"]', new_val)
    time.sleep(0.5)
    pg_b = w.wait_data(lambda x: x.get("dirty") is True, tries=20, gap=0.4)
    w.shot("43-10-编辑态-已改一个字段")
    w.rep.rec(
        "㊸ 第3步 · 真实点击「编辑内容」→ **真实输入**改一个字段（页面识别到有未保存改动）",
        bool(t_edit) and bool(t_input) and pg_b.get("dirty") is True,
        f"字段#{target_idx} 锚点 {n_input} 个 input={t_input} "
        f"{old_val!r} → {new_val!r} dirty={pg_b.get('dirty')!r}",
    )

    t_save = w.c.tap('[data-act-save="1"]')
    pg_c = w.wait_data(lambda x: len(x.get("revisions") or []) > rev_before, tries=60, gap=0.5)
    rev_after = len(pg_c.get("revisions") or [])
    w.rep.rec(
        "㊸ 第3步 · 保存 ⇒ **追加一个新版本**（append-only，不是就地改掉旧版本）",
        bool(t_save) and rev_after > rev_before,
        f"revisions {rev_before} → {rev_after} notice={str(pg_c.get('saveNotice'))[:70]!r}",
    )

    n_ask = w.c.count('[data-act-confirm-revision="1"]')
    t_ask = w.c.tap('[data-act-confirm-revision="1"]')
    time.sleep(0.4)
    n_ok = w.c.count('[data-act-confirm-revision-submit="1"]')
    w.shot("43-11-页内确认条")
    w.rep.rec(
        "㊸ 第3步 · 「设为生效版本」走**页内确认条**"
        "（原生弹层的确认键走查工具点不到 ⇒ 这条路径本来拿不到设备证据）",
        n_ask == 1 and bool(t_ask) and n_ok == 1,
        f"入口 {n_ask} 个、确认条 {n_ok} 个",
    )
    t_ok = w.c.tap('[data-act-confirm-revision-submit="1"]')
    pg_d = w.wait_data(
        lambda x: "生效版本已切换" in str(x.get("saveNotice") or ""), tries=40, gap=0.5
    )
    # 「先证有，再证相等」：先确认**刚保存的那一版**的版本号取到了，再断言服务端的
    # 生效版本就是它。少了前一半，`'' == ''` 也会通过 —— 本切片首跑正是如此：
    # 字段名读错 ⇒ 两边都空 ⇒ 断言照样绿，只有 note 里那行
    # `服务端 current_revision_no=''` 把真相写了出来（换个不看 note 的人就漏了）。
    rev_nos = [int((r or {}).get("revisionNo") or 0) for r in (pg_c.get("revisions") or [])]
    rev_new = str(max(rev_nos)) if rev_nos else ""
    art_truth = api_get(f"/entrust/artifacts/{aid_art}", tok_owner) or {}
    art_row = (
        art_truth.get("artifact") if isinstance(art_truth.get("artifact"), dict) else art_truth
    )
    # ⚠️ 形状是嵌套的 `current_revision.revision_no`，不是顶层的 `current_revision_no`。
    cur_no = str(((art_row or {}).get("current_revision") or {}).get("revision_no") or "")
    w.rep.rec(
        "㊸ 第3步 · 确认切换生效版本 ⇒ **服务端**的生效版本号 == 页面刚保存的那一版（API 直证）",
        bool(t_ok and rev_new)
        and cur_no == rev_new
        and "生效版本已切换" in str(pg_d.get("saveNotice") or ""),
        f"刚保存 v{rev_new} / 服务端 v{cur_no} / saveNotice={str(pg_d.get('saveNotice'))[:40]!r}",
    )

    # —— 从工作台打开同一份成果 ——
    print(
        "\n-- 三、第 3 步 · 从工作台 → 委托卡 → 详情页 → 成果槽位 打开同一份 --",
        flush=True,
    )
    if not w.open_workbench(CODE_OWNER, tag="㊸"):
        w.rep.not_run("㊸ 第3步 · 从工作台打开同一份成果", "未能回到经理工作台")
        return
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    sel_card = f'[data-id="{aid}"]'
    w.c.scroll_into(sel_card)
    t_card = w.c.tap(sel_card)
    ok_dt = w.c.wait_path(DETAIL, 30)
    time.sleep(1.4)
    pg_e = w.wait_data(lambda x: bool(x.get("slots")), tries=40, gap=0.5)
    w.shot("43-12-详情页-成果槽位")
    w.rep.rec(
        "㊸ 第3步 · 工作台 → 委托卡 → 详情页（真实点击，经页面的 `go()` 决策）",
        bool(t_card and ok_dt) and bool(pg_e.get("slots")),
        f"path={w.c.current_path()} slots={len(pg_e.get('slots') or [])}",
    )

    sel_ref = f'[data-kind="artifact"][data-id="{aid_art}"]'
    w.c.scroll_into(sel_ref)
    n_ref = w.c.count(sel_ref)
    t_ref = w.c.tap(sel_ref)
    ok_ref = w.c.wait_path(ARTIFACT, 30)
    time.sleep(1.3)
    pg_f = w.wait_data(lambda x: x.get("artifact") is not None, tries=40, gap=0.5)
    w.shot("43-13-从工作台打开同一份成果")
    w.rep.rec(
        "㊸ 第3步 · 槽位里的成果引用可点，且打开的是**被点的那一条**"
        "（同一 `artifact_id`，不是'打开最新一份'）",
        bool(t_ref and ok_ref) and str(pg_f.get("artifactId")) == aid_art,
        f"引用锚点 {n_ref} 个、期望 aid={aid_art}、落页 artifactId={pg_f.get('artifactId')!r}",
    )
    shown = json.dumps(pg_f.get("artifact") or {}, ensure_ascii=False)
    w.rep.rec(
        "㊸ 第3步 · 工作台侧看到的是**更正后**的内容"
        "（D1-04「更正出现在所有共享视图」—— 这是本步唯一真正的判据）",
        new_val in shown,
        f"期望页面字段值里含 {new_val!r}；实际{'命中' if new_val in shown else '未命中'}"
        f"（字段 {len(art.get('fields') or [])} 个，生效版本证据见上一条）",
    )

    errs = w.new_errors(err_base)
    if errs is None:
        w.rep.review_required(
            "㊸ 本章运行期 console 无未归因错误",
            "采集**失败**（返回 None，不是空串）—— 不能把'采不到'当成'没有错误'",
        )
    elif errs.strip():
        w.rep.review_required("㊸ 本章运行期 console 无未归因错误", errs[:400])
    else:
        w.rep.rec("㊸ 本章运行期 console 无未归因错误", True, "增量 0 条")


def sec_44(w: Walker) -> None:
    """㊹ 主演示第 6 步（合同 §10.1）—— `Release the offer; customer accepts its exact revision`。

    合同原文（第 6 步，一字不改）：

        Release the offer; customer accepts its exact revision

    为什么单独一章
    --------------
    ㊸ 章把第 1–3 步走通了，但它**止步于"成果更正"** —— 没有任何一次发布，也没有客户侧。
    而第 6 步的判据是两条**跨角色**的事实：
    ① 客户接受的是**那一个精确版本**（不是"最新版"，也不是"反正是某一版"）；
    ② 接受这个动作**真的落了库**（页面说成功不算数）。

    本节覆盖（措辞与手段一一对应）
    ----------------------------
    一、经理（`seed-owner`）：工作台 → 委托卡 → 详情 → 成果引用 → 成果页
        → **真实点击**「发布这一版给客户」→ 页内确认条 → 确认发布。
    二、门槛：首次发布会撞上**来源门槛**（未核验的来源不得作为已发布依据）。
        这一步**本身就是被测事实** —— 断言页面把待核验清单**显示出来**，
        而不是只丢一句"不能发布"。
    三、客户（`seed-shipper`，该委托**货主本人**）：我的 → 我的委托 → 该单 → 详情页，
        断言「对客报价」卡显示的是**那一版**（`revisionNo` 与服务端发布记录一致）、
        内容是发布时**冻结**的那份、来源标注与签署模式都在，然后**真实点击**
        「接受这一版」→ 页内展开条 → 确认提交。
    四、落库与负例：客户响应经 **API 直证**；经理响应客户发布 ⇒ **403**；
        同一次发布二次响应 ⇒ **409**；有响应后**撤回入口消失**。

    ⚠️ 诚实边界（按档登记，**不计入通过**）
    * 本章**依赖 ㊸ 建的委托**（那张单上的成果是本章唯一可发布的载体）。
      与 ㉖→㉗→㉘ 同一条做法：前置缺失时**明确 `NOT_RUN` 并说清缺什么**，
      不静默跳过、也不自造数据。⇒ 请用 `--section 43,44` 同跑。
    * ⭐ **载体必须是「客户可见类型」**（`customer_quote` / `contract_review`），
      与后端 `registry.CUSTOMER_VISIBLE_TYPES` 同源。2026-09-17 首跑把
      **`quote_parsed`（船东侧报价）**当载体去发布 ⇒ 被 400 拒
      `成果类型 quote_parsed 不在客户白名单投影内（投影为空），不能对客发布`。
      **这是设计，不是缺陷**：服务端自己在 AG-02 的 envelope 里就写了
      `NOTE_VENDOR_QUOTE`「本报价为供应商侧报价，不是对客报价，两者口径不同，
      **不得互相替代**」。⇒ 首跑那 4 条 FAIL 里有 3 条是**章节选题错**造成的假失败，
      已改为：先按白名单挑载体，挑不到就**把"服务端禁止发布内部成果"直证成一条负例**，
      再把正流程记 `NOT_RUN` 并交 HO（见下）。
    * ⚠️ **已记档的真实缺口（交 HO 裁决）**：主演示链路（§10.1 第 1–3 步 → 第 6 步）
      **缺「组装对客报价」这一步** —— ㊸ 的产出是 `quote_parsed`，而第 6 步要发布的是
      `customer_quote`；`ag02.py` 里 `customer_quote` 的产出条件是**作业输入带 `amount`**
      （对客口径金额），主链路从未给过该入参，§10.1 的 13 步里也没有这一步。
    * 「登记来源核验」**界面上没有入口**（`artifact.wxml` 里与 source/verify 相关的
      `data-act-*` 锚点一个都没有）⇒ 这一步**只能经 API** 完成，脚本里如实标注；
      该缺口单独记一条 `FAIL`，交 HO 裁决（补界面 / 改口径），**不当成通过**。
    * 发布确认条里的「授权附件 id」输入框没有专属锚点，取值用 `set_data` 注入
      （**输入路径被跳过**，提交与后续链路都是真的）—— 与技能里那条同源约定一致。
    """
    print("\n== ㊹ 主演示第 6 步（合同 §10.1，真实点击）==", flush=True)

    assignments_page = "pages/entrust/assignments/assignments"
    err_base = w.c.errors()

    def my_org_id(code: str, name: str) -> str:
        """按组织名取 org_id（不写死 id：种子重铺会变）。"""
        tok = (api_login(code) or {}).get("access_token") or ""
        if not tok:
            return ""
        items = (api_get("/entrust/my-orgs", tok) or {}).get("items") or []
        for r in items:
            if str((r or {}).get("name") or "") == name:
                return str((r or {}).get("org_id") or "")
        return ""

    def newest_by_title(org_id: str, title: str, token: str) -> str:
        """按标题取**最大** `assignment_id` 的那张单（**不限状态**）。

        ⚠️ 不能复用 `find_submitted()` —— 它强制 `status == 'submitted'`，而本章要用的是
        ㊸ 受理过的那张（`claimed`）。也不能只按标题命中第一条：同标题的历史单会累积，
        取"第一条"会拿到上一轮那张**没有本次成果**的旧单，于是后面全链条在测另一张单。
        """
        data = api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", token) or {}
        rows = [r for r in (data.get("items") or []) if str((r or {}).get("title") or "") == title]
        rows.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
        return str((rows[0] or {}).get("assignment_id") or "") if rows else ""

    def art_id_of(item: dict) -> str:
        """成果 id 的**防御式**取值：先把载荷原样打出来，再按候选键取。"""
        for k in ("artifact_id", "id", "artifactId"):
            v = (item or {}).get(k)
            if v not in (None, "", 0):
                return str(v)
        return ""

    def rev_row(d: dict, no: int) -> dict:
        for r in d.get("revisions") or []:
            if int((r or {}).get("revisionNo") or 0) == int(no):
                return r or {}
        return {}

    def rel_of(items: list, rid: str) -> dict:
        for it in items or []:
            if str((it or {}).get("release_id")) == str(rid):
                return it or {}
        return {}

    def idem(tag: str) -> str:
        return f"walk44-{tag}-{int(time.time() * 1000)}"

    # ==================== 前置：定位 ㊸ 建的那张委托 ====================
    print("\n-- 前置 · 定位 ㊸ 建的委托与其成果 --", flush=True)
    # ⚠️ 预取**显式声明所需角色**：`switch-role` 改的是**用户级**角色，不声明的话
    #    前面章节把它切成 shipper 会让本节的经理侧接口一律 403（坑 30）。
    tok_owner_raw = (api_login(CODE_OWNER) or {}).get("access_token") or ""
    tok_owner, role_note = ensure_role(tok_owner_raw, "owner")
    org_id = my_org_id(CODE_OWNER, ORG_WORKBENCH)
    aid = prefer_anchor(newest_by_title(org_id, A1_MAIN_TITLE, tok_owner) if org_id else "")
    if aid:
        w.rep.rec(
            "㊹ 前置 · 定位 ㊸ 建的那张委托（标题匹配取**最大** assignment_id，不限状态）",
            True,
            f"aid={aid} org={org_id}（{ORG_WORKBENCH}）token={role_note}",
        )
    else:
        # ⚠️ 这是**依赖缺失**、不是"行为违反预期" ⇒ 记 `NOT_RUN`。
        #    2026-09-18 订正：此前记 `FAIL`，于是单跑 `--section 44` 时本轮总
        #    `FAIL` 数被抬高两格（一条前置 + 一条全章），读者会以为"第 6 步跑错了"，
        #    而实情是**根本没跑**。与 ㊻ 那条过期 `LIMITATION` 是同一类读数错。
        w.rep.not_run(
            "㊹ 前置 · 定位 ㊸ 建的那张委托（标题匹配取**最大** assignment_id，不限状态）",
            f"org={org_id}（{ORG_WORKBENCH}）里没有标题为 {A1_MAIN_TITLE!r} 的委托 ⇒ "
            "本章依赖 ㊸ 先建单，本次没跑（正式取证用 `--section 43,44` 同跑）。",
        )
        w.rep.not_run(
            "㊹ 全章",
            f"未找到标题为 {A1_MAIN_TITLE!r} 的委托。本章依赖 ㊸ 建单，"
            "请用 `--section 43,44` 同跑；前置缺失时不自造数据。",
        )
        return

    art_list = api_get(f"/entrust/assignments/{aid}/artifacts", tok_owner) or {}
    items_art = art_list.get("items") or []
    typed = [(art_id_of(it), str((it or {}).get("artifact_type") or "")) for it in items_art]
    #: 客户可见类型 —— **必须与服务端 `registry.CUSTOMER_VISIBLE_TYPES` 同源**。
    #: ⚠️ 发布端点（`offers.release_offer`）的第一道闸就是
    #: `project_for_customer(artifact_type, payload)` 非空，否则 **400**
    #: （`成果类型 X 不在客户白名单投影内（投影为空），不能对客发布`）。
    #: 这条闸**不能绕**：它是"客户数据白名单投影不得先返回再隐藏"在发布口的落地。
    customer_visible = frozenset({"customer_quote", "contract_review"})
    aid_art, carrier_type = next(((i, t) for i, t in typed if t in customer_visible), ("", ""))
    w.rep.rec(
        "㊹ 前置 · 该委托下有**成果**（㊸ 的产出）",
        bool(typed),
        f"成果 {art_list.get('total')} 份：{typed or '无'}；"
        f"未归属 {art_list.get('unassigned_total')} 份（只统计归属恰好等于本委托的）",
    )
    # ⚠️ 前置**不成立**记 `NOT_RUN`，**不是** `FAIL`（2026-09-18 订正）。
    #    合同 §10.3 把 `FAIL` 定义为「行为违反预期」，而 `NOT_RUN` 是「检查未执行」；
    #    "这一单上没有可发布的载体"属于后者。混在一格会让**总 FAIL 数说错话**，
    #    而读者会据此去找一个并不存在的缺陷 —— 与 ㊻ 那条过期 `LIMITATION`
    #    同属**读数错误**（那一条的代价是整份走查从"全绿"变成"未跑"）。
    if aid_art:
        w.rep.rec(
            "㊹ 前置 · 该委托下有**客户可见**的成果（第 6 步只能发布客户可见类型）",
            True,
            f"命中类型={carrier_type} id={aid_art}；白名单={sorted(customer_visible)}",
        )
    else:
        w.rep.not_run(
            "㊹ 前置 · 该委托下有**客户可见**的成果（第 6 步只能发布客户可见类型）",
            f"客户可见白名单={sorted(customer_visible)}；命中类型=无 id=无。依据："
            "`offers.release_offer` 要求 `registry.project_for_customer(...)` 非空 —— "
            "内部成果（如 `quote_parsed` 船东报价）一律 400，这是**设计**而非缺陷。"
            "⇒ 前置不成立（不是行为违反预期），故记 `NOT_RUN` 而不是 `FAIL`。",
        )
    if not typed:
        w.rep.not_run(
            "㊹ 第6步 · 发布与客户接受",
            "该委托下没有成果 ⇒ 没有可发布的载体。㊸ 的成果若已存在，请确认标题常量与种子一致。",
        )
        return

    # ==================== 〇、载体：缺**客户可见**成果时，经界面组装一份 ====================
    #
    # 为什么要有这一步（这是本章最值钱的一段）
    # ------------------------------------------
    # 主链路（㊸：客户提单 → 上传样本 → AG-02 → 更正字段）产出的是 `quote_parsed`
    # （**船东侧**报价），它不在客户白名单内 ⇒ 第 6 步没有可发布的载体。
    # 而三点事实合起来说明"缺的是一步**本该有的**人工动作"：
    #   ① 服务端自己在 AG-02 的 envelope 里就写了 `NOTE_VENDOR_QUOTE`：
    #      「本报价为**供应商侧报价，不是对客报价**，两者口径不同，**不得互相替代**」；
    #   ② `customer_quote` 的产出条件是作业输入带 `amount`（对客口径金额），主链路没给过；
    #   ③ 「对客报价**可人工组装**」是 S3 的 DoD（`DEMO-1-plan.md` §5.2 / UI-06）与
    #      BP-03 第 3–8 条（内部规划 → **受控客户承诺**）明写的能力，界面入口就在**委托详情页**。
    #
    # ⇒ 本章**经界面**把它组装出来，而不是去借种子/夹具上的现成成果：
    #    借现成的会让"经理组装"这一步**永远没有证据** —— 而它恰是第 1–3 步与第 6 步之间
    #    那段隐含动作（合同 §10.1 的 13 步没写它，BP-03 隐含它）。
    # ⚠️ 诚实边界：这一步**证明的是第 6 步的前置可经界面达成**，
    #    它本身**不是** §10.1 的任何一步 ⇒ 单独编号「〇」，不与第 6 步合并计数。
    if not aid_art:
        print("\n-- 〇、经理经界面组装一份对客报价（第 6 步的载体）--", flush=True)
        if not w.open_workbench(CODE_OWNER, tag="㊹"):
            w.rep.not_run("㊹ 〇 经界面组装对客报价", "未能进入经理工作台")
        else:
            w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL)
            w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
            w.wait_data(lambda x: x.get("canAssembleQuote") is not None, tries=40, gap=0.5)
            n_qopen = w.c.count('[data-act-quote-open="1"]')
            w.rep.rec(
                "㊹ 〇 经理在该委托详情页有「组装对客报价」入口"
                "（判据＝授权可唯一定位 ∧ 所在组织内有 `entrust:quote:create`）",
                n_qopen == 1,
                f"入口数={n_qopen}",
            )
            if n_qopen == 1:
                w.c.scroll_into('[data-act-quote-open="1"]')
                w.c.tap('[data-act-quote-open="1"]')
                d_q = w.wait_data(lambda x: x.get("quoteOpen") is True, tries=20, gap=0.3)
                # ⚠️ `input_text` 是**键入**（往现有值后面追加）⇒ 只打**空框**。
                #    `currency` 已预填 `CNY`，一个字符都不打（打了会得到 `CNYCNY`）。
                typed_q = (
                    w.c.input_text('input[data-df="amount"]', "36800")
                    and w.c.input_text('input[data-df="includes"]', "船舶运输、装船、卸船")
                    and w.c.input_text('input[data-df="validUntil"]', "2026-12-31")
                )
                w.rep.rec(
                    "㊹ 〇 表单渲染出来且三个输入框真的接上 `bindinput`"
                    "（`data-df` 认领表；币种已预填故不键入）",
                    d_q.get("quoteOpen") is True and typed_q,
                    "三个输入框都键入成功" if typed_q else "有输入框没接上（漏映射时正是这个症状）",
                )
                w.c.scroll_into('[data-act-quote-submit="1"]')
                w.c.tap('[data-act-quote-submit="1"]')
                time.sleep(2.0)
            # 重新读成果清单：组装成功的判据是**服务端的成果里真的有**一份客户可见类型，
            # 不是"页面 toast 说成功了"（与"页面说成功不算数"同一条纪律）。
            art_list = api_get(f"/entrust/assignments/{aid}/artifacts", tok_owner) or {}
            typed2 = [
                (art_id_of(it), str((it or {}).get("artifact_type") or ""))
                for it in (art_list.get("items") or [])
            ]
            picked = next(((i, t) for i, t in typed2 if t in customer_visible), ("", ""))
            if picked[0]:
                aid_art, carrier_type = picked
            w.rep.rec(
                "㊹ 〇 经**界面**组装出的对客报价出现在服务端成果清单里，且类型在客户可见白名单内",
                bool(picked[0]) and picked[1] == "customer_quote",
                f"成果清单：{typed2 or '无'}；命中 {picked[1] or '无'} id={picked[0] or '无'}",
            )
            w.shot("44-0-组装对客报价")

    if not aid_art:
        # ① 先把"服务端确实禁止发布内部成果"这条**负例**直证出来 ——
        #    它本身就是六机制之一（客户白名单投影）的证据，不该因为主流程走不通就丢掉。
        #    取 entrustment_id：发布端点挂在 `entrustments/{id}/offer-releases` 下。
        art_detail = api_get(f"/entrust/artifacts/{typed[0][0]}", tok_owner) or {}
        eid_probe = str(art_detail.get("entrustment_id") or "")
        st_neg, body_neg = (
            api_post(
                f"/entrust/entrustments/{eid_probe}/offer-releases",
                tok_owner,
                {
                    "artifact_id": int(typed[0][0]),
                    "revision_no": 1,
                    "authorized_attachment_ids": [],
                },
                idem_key=idem("neg-internal"),
            )
            if eid_probe
            else (0, None)
        )
        detail_neg = str((body_neg or {}).get("detail") or "")
        if not eid_probe:
            # 发布端点挂在 `entrustments/{id}/offer-releases` 下 ⇒ 拿不到 id 时
            # URL 会变成 `//offer-releases`，得到 HTTP 0 —— 那**不是**"负例通过"，
            # 也**不是**"产品拒绝了"，而是这条负例**根本没执行**。记 `NOT_RUN`。
            # （首跑实测就是这么记成 `FAIL` 的：`HTTP 0；detail=''`。）
            w.rep.not_run(
                "㊹ 第6步 · **负例**：内部成果不得对客发布（客户白名单投影在发布口生效）",
                "该成果读不到 `entrustment_id` ⇒ 发布端点没有可用的挂载点，"
                "负例无法执行 ⇒ 记 `NOT_RUN`（不是 `FAIL`）。",
            )
        else:
            w.rep.rec(
                "㊹ 第6步 · **负例**：内部成果不得对客发布（客户白名单投影在发布口生效）",
                st_neg == 400 and "白名单投影" in detail_neg,
                f"POST /entrust/entrustments/{eid_probe}/offer-releases artifact={typed[0][0]}"
                f"（{typed[0][1]}）⇒ HTTP {st_neg}；detail={detail_neg[:120]!r}",
            )
        w.rep.not_run(
            "㊹ 第6步 · 发布与客户接受（**阻塞：主演示链路缺「组装对客报价」这一步**）",
            "㊸ 那条链路（客户提单 → 上传样本 → AG-02 → 更正字段）产出的是 "
            f"`{typed[0][1]}`（船东侧报价），它**不在客户可见白名单**内 ⇒ 第 6 步没有可发布的载体。\n"
            "           三条已直证的事实：\n"
            "           ① 服务端自身在 AG-02 的 envelope 里就写明 `NOTE_VENDOR_QUOTE`："
            "「本报价为**供应商侧报价，不是对客报价**，两者口径不同，**不得互相替代**」"
            '（作业输入实测只有 `{"attachment_id": 1}`）；\n'
            "           ② `customer_quote` 的产出条件在 `ag02.py` 里是**作业输入带 `amount`**"
            "（对客口径金额）—— 主链路没给过这个入参；\n"
            "           ③ 合同 §10.1 的 13 步里**没有**「组装对客报价」这一步，"
            "而 BP-03 第 3–8 条（内部规划 → 受控客户承诺）隐含它。\n"
            "           ⇒ 这是**主演示链路的缺口**，不是页面或断言的问题。\n"
            "           本轮处置：本章新增「〇」节，**经界面组装**一份对客报价来补这个前置。"
            "走到这里说明**那一步也没成**（组装入口不在 / 提交被拒 / 成果类型仍不达标）"
            "⇒ 按合同 §10.3 记 `NOT_RUN` 并交 HO 裁决。",
        )
        return

    # ==================== 一、经理打开成果页 ====================
    print("\n-- 一、经理（seed-owner）真实点击进入成果页 --", flush=True)
    if not w.open_workbench(CODE_OWNER, tag="㊹"):
        w.rep.not_run("㊹ 第6步 · 发布", "未能进入经理工作台")
        return
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)

    sel_card = f'[data-id="{aid}"]'
    w.c.scroll_into(sel_card)
    t_card = w.c.tap(sel_card)
    ok_dt = w.c.wait_path(DETAIL, 30)
    time.sleep(1.4)
    # 等详情页的**槽位**就位（成果引用挂在槽位上）；取值不参与断言，故不接收返回值。
    w.wait_data(lambda x: bool(x.get("slots")), tries=40, gap=0.5)
    sel_ref = f'[data-kind="artifact"][data-id="{aid_art}"]'
    w.c.scroll_into(sel_ref)
    n_ref = w.c.count(sel_ref)
    t_ref = w.c.tap(sel_ref)
    ok_art = w.c.wait_path(ARTIFACT, 30)
    time.sleep(1.3)
    pg_art = w.wait_data(lambda x: x.get("artifact") is not None, tries=40, gap=0.5)
    w.shot("44-1-成果页")
    w.rep.rec(
        "㊹ 第6步 · 工作台 → 委托卡 → 详情 → 成果引用 → 成果页"
        "（全程真实点击，经页面的 `go()` 决策）",
        bool(t_card and ok_dt and t_ref and ok_art) and str(pg_art.get("artifactId")) == aid_art,
        f"card={t_card} detail={ok_dt} 引用锚点 {n_ref} 个 ref={t_ref} artifact={ok_art} "
        f"落页 artifactId={pg_art.get('artifactId')!r}（期望 {aid_art}）",
    )
    eid = str(pg_art.get("entrustmentId") or "")
    if not eid:
        w.rep.not_run(
            "㊹ 第6步 · 发布",
            f"成果页未给出 entrustmentId（页面上是 {w.c.current_path()}）",
        )
        return

    # 挑要发布的版本：优先"生效版本且未发布过"；已发布过的版本页面上根本不给按钮（这是设计）。
    revs = pg_art.get("revisions") or []
    target = None
    for r in revs:
        rr = r or {}
        if rr.get("isCurrent") and not rr.get("published"):
            target = rr
            break
    if target is None:
        for r in revs:
            rr = r or {}
            if not rr.get("published"):
                target = rr
                break
    rev = int((target or {}).get("revisionNo") or 0)
    w.rep.rec(
        "㊹ 第6步 · 成果页列出可发布的版本，且选出的目标版本**未发布过**",
        bool(rev),
        f"版本 {len(revs)} 个：{[(r or {}).get('revisionNo') for r in revs]}；"
        f"目标 v{rev}（isCurrent={(target or {}).get('isCurrent')} "
        f"published={(target or {}).get('published')}）",
    )
    if not rev:
        w.rep.not_run(
            "㊹ 第6步 · 发布",
            "没有未发布过的版本（全部已发布）⇒ 无法在本轮制造一次新的发布。"
            "重跑 ㊸ 会追加新版本，或直接复跑 ㊸ 后再跑本章。",
        )
        return

    # ==================== 二、发布（先读门槛，再决定走哪条剧本） ====================
    print("\n-- 二、真实点击发布 → 来源门槛 --", flush=True)
    # ⭐ 判据先取**服务端事实**：这条载体的来源门槛到底过不过。
    #    • 人工组装（本章「〇」节）产出的是 **manual 直写**版本 ⇒ 没有待核验的模型声明
    #      ⇒ `gate.ok=True` ⇒ **一次发布即成功**；
    #    • AG-02 产出会写下声明行 ⇒ 首次发布应**被门槛拒绝**，要逐条核验才能发。
    #    ⚠️ 两种载体的**预期完全相反**：把"一次就成功"套进"被拒 ⇒ 核验 ⇒ 再发布"的剧本里，
    #    会把一次**成功**读成**失败**（2026-09-18 首跑就是这么得到 3 条 FAIL 的）。
    gate_pre = (
        api_get(f"/entrust/artifacts/{aid_art}/source-checks?revision_no={rev}", tok_owner) or {}
    ).get("gate") or {}
    gate_ok_pre = bool(gate_pre.get("ok"))
    w.rep.rec(
        "㊹ 第6步 · 发布前先取**服务端**的门槛结论（它决定下面走哪条剧本）",
        True,
        f"gate.ok={gate_ok_pre} declared={len(gate_pre.get('declared') or [])} "
        f"pending={len(gate_pre.get('pending') or [])} ⇒ "
        + ("人工直写：无待核验声明，一次发布即成功" if gate_ok_pre else "模型产出：首次发布应被拒"),
    )
    sel_rel = f'[data-act-release="1"][data-no="{rev}"]'
    w.c.scroll_into(sel_rel)
    n_rel = w.c.count(sel_rel)
    t_rel = w.c.tap(sel_rel)
    time.sleep(0.5)
    n_strip = w.c.count('[data-act-release-submit="1"]')
    w.shot("44-2-发布确认条")
    w.rep.rec(
        "㊹ 第6步 · 「发布这一版给客户」入口走**页内确认条**"
        "（发布会被客户与审计看到，确认动作必须可被真机验证，不用原生弹层）",
        n_rel == 1 and bool(t_rel) and n_strip == 1,
        f"版本行锚点 {n_rel} 个（`v{rev}` 唯一）、tap={t_rel}、确认条 {n_strip} 个",
    )
    if not (n_rel == 1 and t_rel and n_strip == 1):
        w.rep.not_run("㊹ 第6步 · 发布", "发布入口或确认条未就位")
        return

    # 授权附件：取该成果的附件清单（发布时**明确**授权客户能下载哪几个）
    att_list = api_get(f"/entrust/artifacts/{aid_art}/attachments", tok_owner) or {}
    att_items = att_list.get("items") or []
    auth_ids = [str(a.get("attachment_id") or a.get("id") or "") for a in att_items]
    auth_ids = [x for x in auth_ids if x]
    # ⚠️ 输入框没有专属锚点 ⇒ 用 set_data 注值（**输入路径被跳过**，提交链路是真的）
    w.c.set_data({"releaseAtts": ",".join(auth_ids)})
    time.sleep(0.4)
    t_rel_submit = w.c.tap('[data-act-release-submit="1"]')
    pg_gate = w.wait_data(
        lambda x: bool(rev_row(x, rev).get("published")) or bool(x.get("releaseHint")),
        tries=40,
        gap=0.5,
    )
    published_now = bool(rev_row(pg_gate, rev).get("published"))
    hint = str(pg_gate.get("releaseHint") or "")
    w.shot("44-3-发布结果")

    gate_blocked = not published_now
    # 400 的**原因要分类**：来源门槛被拒是预期设计；而"不在客户白名单投影内"
    # 说明本章的 `CUSTOMER_VISIBLE` 与服务端漂移了 —— 那是**章节配置错**，不是产品错。
    # 首跑就是被这一条挡住，却混在"门槛生效"的断言里，读数时不易一眼分开。
    drift = "白名单投影" in hint
    # ⭐ **服务端事实才是判据**：页面上的 `published` 是发布记录的**投影**，
    #    依赖 `load()` 完成与 `releasedRevisionOf` 的匹配。首跑实测到
    #    「服务端已有 `released` 记录、页面仍读到 `published=False`」
    #    ⇒ 只看页面会把一次**成功**的发布记成失败。这里去问服务端，页面那一位只作对照。
    rel_rows = (api_get(f"/entrust/entrustments/{eid}/offer-releases", tok_owner) or {}).get(
        "items"
    ) or []
    v_rows_now = [
        r
        for r in rel_rows
        if str((r or {}).get("artifact_id")) == aid_art
        and int((r or {}).get("revision_no") or 0) == rev
    ]
    released_srv = any(str((r or {}).get("status")) == "released" for r in v_rows_now)
    w.rep.rec(
        "㊹ 第6步 · 发布前**来源门槛**生效：未核验的来源不得作为已发布依据 —— "
        "被拒时页面把**待核验清单**显示出来（只说'不能发布'是没法干活的）",
        (released_srv and gate_ok_pre)
        or (gate_blocked and not drift and ("待核验" in hint or "门槛" in hint)),
        f"submit={t_rel_submit} published(页面)={published_now} released(服务端)={released_srv} "
        f"门槛预读={gate_ok_pre} 漂移={drift} releaseHint={hint[:160]!r}"
        "（人工直写的载体不在门槛下 ⇒ 预期是一次发布即成功）",
    )
    if drift:
        w.rep.not_run(
            "㊹ 第6步 · 发布与客户接受",
            f"载体被服务端判为**非客户可见**：{hint[:160]!r} ⇒ 本章 `CUSTOMER_VISIBLE` "
            "与后端 `registry.CUSTOMER_VISIBLE_TYPES` 已漂移，**先对齐白名单再跑**"
            "（这是章节配置错，不是产品缺陷）。",
        )
        return

    gate = gate_pre
    w.rep.rec(
        "㊹ 第6步 · 门槛状态经 **API 直证**（页面判定与后端同源）",
        bool(gate.get("ok")) == (released_srv or published_now),
        f"gate.ok={gate.get('ok')!r} declared={len(gate.get('declared') or [])} "
        f"pending={len(gate.get('pending') or [])} "
        f"missing_declaration={gate.get('missing_declaration')!r} ⇒ "
        f"页面 published={published_now} / 服务端 released={released_srv}",
    )

    if gate_blocked and not released_srv:
        # —— 缺口：界面没有登记核验的入口 ——
        pend_txt = (
            "、".join(f"{p.get('kind')}:{p.get('ref')}" for p in (gate.get("pending") or []))
            or f"{len(gate.get('pending') or [])} 条"
        )
        w.rep.rec(
            "㊹ 第6步 · 界面提供登记「来源核验」的入口（把待核验项变成已核验，才能发布）",
            False,
            "**缺口（新发现，交 HO 裁决）**：发布被门槛拒绝后，页面上只有一句提示 + 待核验清单，"
            "**没有任何可点击控件**能登记核验 —— `artifact.wxml` 里与 source/verify 相关的 "
            "`data-act-*` 锚点一个都没有。⇒ 经理在**界面内**无法走完第 6 步。"
            f"待核验：{pend_txt}。可选处置：① 成果页补页内核验条（依据必填）；"
            "② 若演示口径允许，把'内置样本'产出的来源标成免核验并写进合同口径。",
        )
        # —— 替代通路：经 API 补核验（如实标注"这一步不是界面"）——
        fixed = 0
        for i, p in enumerate(gate.get("pending") or []):
            st, _ = api_post(
                f"/entrust/artifacts/{aid_art}/source-checks",
                tok_owner,
                {
                    "revision_no": rev,
                    "source_kind": str(p.get("kind") or ""),
                    "source_ref": str(p.get("ref") or ""),
                    "state": "verified",
                    "method": "走查核验：内置示例报价单与附件文本已逐条比对（人工核对口径）",
                },
                idem_key=idem(f"chk{i}"),
            )
            if st in (200, 201):
                fixed += 1
        gate2 = (
            api_get(
                f"/entrust/artifacts/{aid_art}/source-checks?revision_no={rev}",
                tok_owner,
            )
            or {}
        ).get("gate") or {}
        w.rep.rec(
            "㊹ 第6步 · 【替代通路】经 API 逐条登记核验后，门槛转为通过",
            bool(gate2.get("ok")) and fixed == len(gate.get("pending") or []),
            f"核验登记 {fixed}/{len(gate.get('pending') or [])} 条；gate.ok={gate2.get('ok')!r} "
            "⚠️ **这一步不是界面**（界面无入口，见上一条缺口）——发布之后的所有动作仍是真实点击。",
        )
        # 重新走一次真实点击发布。
        # ⚠️ 上一次提交被拒后**确认条可能还开着** —— 条开着时版本行上的入口
        #    `[data-act-release="1"]` 就不再渲染（首跑实测：入口锚点 0 个 ⇒ 假失败）。
        #    ⇒ 先收条，再重开。
        n_cancel = w.c.count('[data-act-release-cancel="1"]')
        if n_cancel:
            w.c.tap('[data-act-release-cancel="1"]')
            time.sleep(0.6)
        n_rel2 = w.c.count(sel_rel)
        t_rel2 = w.c.tap(sel_rel)
        time.sleep(0.6)
        # ⚠️ 重开确认条会**重置**授权附件入参（页面逻辑刻意如此）⇒ 必须再注一次，
        #    否则第二次提交带着空的附件清单，失败原因会与门槛无关。
        if auth_ids:
            w.c.set_data({"releaseAtts": ",".join(auth_ids)})
            time.sleep(0.3)
        t_sub2 = w.c.tap('[data-act-release-submit="1"]')
        pg_gate = w.wait_data(lambda x: bool(rev_row(x, rev).get("published")), tries=60, gap=0.5)
        published_now = bool(rev_row(pg_gate, rev).get("published"))
        w.rep.rec(
            "㊹ 第6步 · 门槛通过后**重新真实点击**发布 ⇒ 该版本变为已发布",
            bool(t_rel2 and t_sub2 and published_now),
            f"首提交后确认条 {n_cancel} 个（先收条再重开）；入口锚点 {n_rel2} 个 "
            f"tap={t_rel2} submit={t_sub2} published={published_now}",
        )

    else:
        # 载体**不在门槛下**（人工直写）⇒ 上面那三格（被拒 / 核验入口缺口 / 二次发布）
        # **不适用**。如实记 `NOT_RUN` 并说清原因，不静默跳过 ——
        # 否则读者会以为"来源门槛这条路本轮已经验过了"。
        for _label in (
            "㊹ 第6步 · 界面提供登记「来源核验」的入口（把待核验项变成已核验，才能发布）",
            "㊹ 第6步 · 【替代通路】经 API 逐条登记核验后，门槛转为通过",
            "㊹ 第6步 · 门槛通过后**重新真实点击**发布 ⇒ 该版本变为已发布",
        ):
            w.rep.not_run(
                _label,
                f"本轮载体是**经界面人工组装**的对客报价（`aid={aid_art}`，来源 `manual`）⇒ "
                f"服务端没有待核验的模型声明（`gate.ok={gate_ok_pre}`、`pending` 0 条）"
                "⇒ 门槛一次就过，「被拒 ⇒ 逐条核验 ⇒ 再发布」这条剧本**没有可跑的对象**。"
                "⚠️ 上一轮本格记的是 `FAIL`，并据此报了「界面无核验入口」这个缺口 —— "
                "**那是误读**：当时把页面的投影延迟（`published=False`）当成了服务端拒绝，"
                "于是走进被拒分支、拿着 `pending=0` 去断言『页面上没有核验入口』。"
                "缺口**尚未在任何真实场景下被观察到**；它要由 AG-02 产出的载体"
                "（来源为模型声明 ⇒ 首次发布应被拒）来证伪或证实，见 "
                "`DEMO-1-readiness.md` §10.4 的 O-1。",
            )

    if not (published_now or released_srv):
        w.rep.not_run("㊹ 第6步 · 客户接受该版本", "发布未成功，客户侧没有可接受的版本")
        return

    rid = str(rev_row(pg_gate, rev).get("releaseId") or "")
    w.shot("44-4-已发布")
    w.rep.rec(
        "㊹ 第6步 · 版本行上出现**发布号与状态**，且该版本不再给发布入口"
        "（重复发布同一版只会把客户手上那份取代掉，没有收益）",
        bool(rid) and w.c.count(sel_rel) == 0,
        f"releaseId={rid!r} 该版本的发布入口剩余 {w.c.count(sel_rel)} 个",
    )

    # —— API 直证：发布记录冻结了**精确版本**与客户快照 ——
    rels = (api_get(f"/entrust/entrustments/{eid}/offer-releases", tok_owner) or {}).get(
        "items"
    ) or []
    # ⚠️ 两处收窄都要：**按 artifact**（同一授权下的发布可能跨多个成果 —— 首跑实测
    #    只按 `revision_no` 过滤会把别的成果的 v1 一起算进来，得到 4 条）
    #    + **按状态**（重发布会把前一条置为 `superseded` ⇒「该版本有 2 条记录」是正常的，
    #    要挑出 `released` 那条）。
    v_rows = [
        r
        for r in rels
        if str((r or {}).get("artifact_id")) == aid_art
        and int((r or {}).get("revision_no") or 0) == rev
    ]
    mine_rel = [r for r in v_rows if str((r or {}).get("status")) == "released"]
    snap = (mine_rel[0] or {}).get("customer_snapshot") if mine_rel else None
    snap = snap if isinstance(snap, dict) else {}
    w.rep.rec(
        "㊹ 第6步 · 服务端的发布记录指向**精确 revision**，且冻结了客户快照"
        "（`customer_snapshot.payload` 非空 —— 空快照是本切片踩过的静默降级）",
        len(mine_rel) == 1 and bool(snap.get("payload")),
        f"该版本发布 {len(v_rows)} 条（其中 released {len(mine_rel)} 条）；"
        f"快照键={sorted(snap.keys())[:6]} payload 字段数={len(snap.get('payload') or {})}",
    )
    row_rel = mine_rel[0] if mine_rel else {}
    rid = rid or str(row_rel.get("release_id") or "")
    if auth_ids:
        w.rep.rec(
            "㊹ 第6步 · 授权下载清单**冻结在发布记录上**（不是「同属一条委托就全开」）",
            [str(x) for x in (row_rel.get("authorized_attachment_ids") or [])] == auth_ids,
            f"页面注入 {auth_ids} ⇒ 服务端 {row_rel.get('authorized_attachment_ids')}",
        )
    else:
        # ⚠️ 两侧都是空时 `[] == []` **恒真** ⇒ 那一格是"真空通过"（2026-09-18 订正：
        #    此前本格就是这么绿的，日志写着「页面注入 [] ⇒ 服务端 None」）。
        #    判据要能失败才算判据 —— 载体没有附件时，这条**没什么可证**，如实记 `NOT_RUN`。
        w.rep.not_run(
            "㊹ 第6步 · 授权下载清单**冻结在发布记录上**（不是「同属一条委托就全开」）",
            f"本轮载体 `artifact#{aid_art}` **没有附件**（授权清单两侧都是空）⇒ 断言会"
            "**真空通过**（`[] == []` 恒真）⇒ 记 `NOT_RUN`。要取证这条，需让载体本身带附件；"
            "本章 〇 节组装的对客报价是没有附件的（㊸ 上传的附件挂在**会话**上，不在成果上）。",
        )

    # —— 负例：经理**响应**客户发布 ⇒ 403（看得见、无权）；与局外人的 404 区分 ——
    st_mgr_resp, _ = api_post(
        f"/entrust/offer-releases/{rid}/responses",
        tok_owner,
        {"decision": "accept", "note": "走查负例：经理不得冒充货主响应"},
        idem_key=idem("mgrresp"),
    )
    w.rep.rec(
        "㊹ 第6步 · 经理响应客户发布 ⇒ **403**（看得见、无权；冒充货主不是 404 而是明确拒绝）",
        st_mgr_resp == 403,
        f"HTTP={st_mgr_resp}（期望 403）",
    )

    # ==================== 三、客户：看到那一版并接受 ====================
    print("\n-- 三、客户（seed-shipper，货主本人）真实点击接受 --", flush=True)
    if w.login_as(CODE_SHIPPER) != INDEX:
        w.rep.not_run("㊹ 第6步 · 客户接受", f"未停在身份页（{w.c.current_path()}）")
        return
    if not w.enter_role("shipper", SHIPPER):
        w.rep.not_run("㊹ 第6步 · 客户接受", f"未进货主工作台（{w.c.current_path()}）")
        return
    time.sleep(1.2)
    tok_cust = (api_login(CODE_SHIPPER) or {}).get("access_token") or ""

    # 真实入口链：我的 → 我的委托 → 该单 → 详情
    # （`switchTab` 是 tabBar 页的正确动作；返回布尔值只说明"指令发出去了"，
    #   就位与否由下面的 `wait_path` 判定，故不接收它。）
    w.c.nav("switchTab", "/" + MINE, MINE)
    time.sleep(1.0)
    n_mine = w.c.count('[data-act-mine-entrust="1"]')
    t_mine_ent = w.c.tap('[data-act-mine-entrust="1"]') if n_mine == 1 else False
    ok_asg = w.c.wait_path(assignments_page, 30) if t_mine_ent else False
    via_asg = "真实点击「我的委托」"
    if not ok_asg:
        w.c.nav("navigateTo", "/" + assignments_page, assignments_page)
        ok_asg = w.c.wait_path(assignments_page, 30)
        via_asg = f"URL 直进（「我的委托」入口命中 {n_mine} 个）"
    time.sleep(1.2)
    w.c.scroll_into(f'[data-mine-id="{aid}"]')
    n_card = w.c.count(f'[data-mine-id="{aid}"]')
    t_card2 = w.c.tap(f'[data-mine-id="{aid}"]') if n_card == 1 else False
    ok_dt2 = w.c.wait_path(DETAIL, 30) if t_card2 else False
    time.sleep(1.4)
    pg_cust = w.wait_data(lambda x: x.get("offer") is not None, tries=40, gap=0.5)
    w.shot("44-5-客户侧-对客报价")
    w.rep.rec(
        "㊹ 第6步 · 客户经**真实入口**（我的 → 我的委托 → 该单）进详情页，"
        "并在页面上看到「对客报价」卡",
        bool(ok_asg and t_card2 and ok_dt2) and pg_cust.get("offer") is not None,
        f"via={via_asg} 我的委托入口 {n_mine} 个 列表页={ok_asg} 本单卡 {n_card} 个 "
        f"detail={ok_dt2} path={w.c.current_path()}",
    )
    off = pg_cust.get("offer") or {}
    if not off:
        w.rep.not_run(
            "㊹ 第6步 · 客户接受",
            "客户侧没有「对客报价」卡 —— 该单尚未发布给这位货主，或身份不是该委托的货主本人",
        )
        return

    w.rep.rec(
        "㊹ 第6步 · 客户看到的是**发布时那一版**（版本号与服务端发布记录逐字相等）",
        int(off.get("revisionNo") or 0) == rev and str(off.get("releaseId")) == rid,
        f"页面 v{off.get('revisionNo')}/发布#{off.get('releaseId')}；服务端 v{rev}/发布#{rid}",
    )
    w.rep.rec(
        "㊹ 第6步 · 内容是**发布时冻结**的那份（客户侧渲染出的字段非空）",
        bool(off.get("contentRows")) and not off.get("contentEmpty"),
        f"contentRows={len(off.get('contentRows') or [])} contentEmpty={off.get('contentEmpty')!r}",
    )
    w.rep.rec(
        "㊹ 第6步 · 数据来源标注常驻显示（`unknown` 照实说'来源未标注'，不折成人工录入）",
        bool(str(off.get("dataOriginLabel") or ""))
        and w.c.count('[data-act-offer-origin="1"]') == 1,
        f"mode={off.get('dataOriginMode')!r} label={off.get('dataOriginLabel')!r} 锚点 "
        f"{w.c.count('[data-act-offer-origin="1"]')} 个",
    )
    w.rep.rec(
        "㊹ 第6步 · 签署模式如实标注（不写这一句，'已接受'会被读成一份已生效的法律签署）",
        "样本签署" in str(off.get("signatureHint") or ""),
        f"signatureMode={off.get('signatureMode')!r} hint={str(off.get('signatureHint'))[:80]!r}",
    )
    w.rep.rec(
        "㊹ 第6步 · 客户**可以**响应（`canRespond=True` 且还没响应过）",
        off.get("canRespond") is True and off.get("decided") is False,
        f"canRespond={off.get('canRespond')!r} decided={off.get('decided')!r}",
    )

    t_acc = w.c.tap('[data-act-offer-accept="1"]')
    time.sleep(0.5)
    pg_form = w.wait_data(lambda x: str(x.get("offerForm") or "") == "accept", tries=20, gap=0.4)
    n_form = w.c.count('[data-act-offer-submit="1"]')
    w.shot("44-6-客户响应页内展开条")
    w.rep.rec(
        "㊹ 第6步 · 「接受这一版」走**页内展开条**（不是原生弹层）——"
        "这是唯一会改变业务事实、且后端用 `UNIQUE(release_id)` 钉死'只能响应一次'的动作",
        bool(t_acc) and str(pg_form.get("offerForm")) == "accept" and n_form == 1,
        f"tap={t_acc} offerForm={pg_form.get('offerForm')!r} 提交锚点 {n_form} 个",
    )
    t_sub_acc = w.c.tap('[data-act-offer-submit="1"]')
    pg_done = w.wait_data(
        lambda x: (x.get("offer") or {}).get("decided") is True, tries=60, gap=0.5
    )
    off2 = pg_done.get("offer") or {}
    w.shot("44-7-客户已接受")
    w.rep.rec(
        "㊹ 第6步 · 真实点击「确认提交」⇒ 页面转为**已响应**态并显示决定与时间",
        bool(t_sub_acc)
        and off2.get("decided") is True
        and str(off2.get("decisionLabel")) == "接受",
        f"submit={t_sub_acc} decided={off2.get('decided')!r} "
        f"decisionLabel={off2.get('decisionLabel')!r} respondedAt={off2.get('respondedAt')!r}",
    )
    w.rep.rec(
        "㊹ 第6步 · 已响应后**不再给响应表单**（响应不可修改，与后端'只能响应一次'一致）",
        w.c.count('[data-act-offer-accept="1"]') == 0
        and w.c.count('[data-act-offer-submit="1"]') == 0,
        f"接受入口 {w.c.count('[data-act-offer-accept="1"]')} 个、"
        f"提交入口 {w.c.count('[data-act-offer-submit="1"]')} 个",
    )

    # —— 落库直证（客户通道 + 经理通道各看一次）——
    my_rels = (api_get("/entrust/my-offer-releases", tok_cust) or {}).get("items") or []
    mine_my = rel_of(my_rels, rid)
    resp_my = mine_my.get("response") or {}
    w.rep.rec(
        "㊹ 第6步 · 客户响应**已落库**（API 直证，页面自述不算数）："
        "客户在「我收到的发布」里能看到自己的决定与备注",
        str(resp_my.get("decision")) == "accept",
        f"HTTP 载荷：release={mine_my.get('release_id')} "
        f"revision={mine_my.get('revision_no')} decision={resp_my.get('decision')!r} "
        f"responded_at={resp_my.get('responded_at')!r}",
    )
    rels2 = (api_get(f"/entrust/entrustments/{eid}/offer-releases", tok_owner) or {}).get(
        "items"
    ) or []
    mine2 = [r for r in rels2 if str((r or {}).get("release_id")) == str(rid)]
    resp2 = (mine2[0] or {}).get("response") or {} if mine2 else {}
    w.rep.rec(
        "㊹ 第6步 · 经理通道看到的**是同一条**发布与同一条响应（两条投影对得上）",
        str(resp2.get("decision")) == "accept"
        and int((mine2[0] or {}).get("revision_no") or 0) == rev,
        f"经理侧：v{(mine2[0] or {}).get('revision_no')} decision={resp2.get('decision')!r}；"
        f"客户侧：v{mine_my.get('revision_no')} decision={resp_my.get('decision')!r}",
    )

    # —— 负例：同一次发布二次响应 ⇒ 409（`UNIQUE(release_id)` 兜住）——
    st_twice, _ = api_post(
        f"/entrust/offer-releases/{rid}/responses",
        tok_cust,
        {"decision": "accept", "note": "走查负例：同一次发布只能响应一次"},
        idem_key=idem("twice"),
    )
    w.rep.rec(
        "㊹ 第6步 · 同一次发布二次响应 ⇒ **409**（判据是 `UNIQUE(release_id)`，不是前端拦）",
        st_twice == 409,
        f"HTTP={st_twice}（期望 409）",
    )

    # ==================== 四、有响应的发布不可撤回（界面层） ====================
    print("\n-- 四、有响应的发布：撤回入口消失（界面层复核）--", flush=True)
    if not w.open_workbench(CODE_OWNER, tag="㊹"):
        w.rep.not_run("㊹ 第6步 · 撤回入口消失", "未能回到经理工作台")
    else:
        w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
        w.c.scroll_into(f'[data-id="{aid}"]')
        t_card3 = w.c.tap(f'[data-id="{aid}"]')
        ok_dt3 = w.c.wait_path(DETAIL, 30) if t_card3 else False
        time.sleep(1.3)
        w.wait_data(lambda x: bool(x.get("slots")), tries=40, gap=0.5)
        w.c.scroll_into(sel_ref)
        t_ref3 = w.c.tap(sel_ref)
        ok_art3 = w.c.wait_path(ARTIFACT, 30) if t_ref3 else False
        time.sleep(1.3)
        pg_art3 = w.wait_data(lambda x: x.get("artifact") is not None, tries=40, gap=0.5)
        row3 = rev_row(pg_art3, rev)
        w.shot("44-8-经理侧-撤回入口消失")
        w.rep.rec(
            "㊹ 第6步 · 有客户响应的发布**不给撤回入口**"
            "（后端本来就 409；摆一个必然失败的按钮等于把业务规则说成'随机失败'）",
            bool(ok_dt3 and ok_art3)
            and w.c.count('[data-act-withdraw-open="1"]') == 0
            and str(row3.get("releaseDecisionLabel") or "") == "接受",
            f"detail={ok_dt3} artifact={ok_art3} 撤回入口 "
            f"{w.c.count('[data-act-withdraw-open="1"]')} 个；"
            f"版本行客户决定={row3.get('releaseDecisionLabel')!r}",
        )

    errs = w.new_errors(err_base)
    if errs is None:
        w.rep.review_required(
            "㊹ 本章运行期 console 无未归因错误",
            "采集**失败**（返回 None，不是空串）—— 不能把'采不到'当成'没有错误'",
        )
    elif errs.strip():
        w.rep.review_required("㊹ 本章运行期 console 无未归因错误", errs[:400])
    else:
        w.rep.rec("㊹ 本章运行期 console 无未归因错误", True, "增量 0 条")


def sec_45(w: Walker) -> None:
    """㊺ 运力确认闭环（BP-03 第 3 条 / 合同 §10.1 第 5 步）——设备侧运行取证。

    合同 §10.1 第 5 步的原文：

        Compare two quotations and record evidence-backed procurement confirmation

    为什么单独一章
    --------------
    §7.16 / §7.18 两轮把这条的**服务端**与**界面**都做完了，但证据全是**自动化**的
    （用例 + e2e 回放）。自动化证据回答得了"逻辑对不对"，回答不了"真机上点得动吗、
    打的字进得了 `data` 吗" —— 而本切片恰好踩过一个**只在真机上暴露**的缺陷：
    `data-df="cap-scope"` 在 handler 的映射表里没有对应项 ⇒ `bindinput` 照常触发、
    查表查不到就**静默空转**，范围一个字符都写不进去，而三个前端静态门禁**全绿**。
    ⇒ 本章存在的理由就是**把这一类沉默逼出来**。

    本节覆盖（四条闭环证据，每条对应一个可能静默失效的环节）
    ------------------------------------------------------
    一、**运力块渲染**：经理侧详情页真的渲染出运力块、登记入口**唯一可点**。
        没有入口时后面三条全都成立不了，故必须先证有（与 ㊵ ① 同一条做法）。
    二、**登记表单可输入**：逐字段真机打字 ⇒ 页面 `capForm` **真的被写入**。
        判据不是"输入框看得见"，而是"打完字 data 里有值" —— 直指上面那个缺陷。
    三、**409 两种分流**：规则不过 ⇒ 页面把**逐条**判定（含**通过项**）显示出来、
        且**不刷新**（刷新会把刚看到的那张判定表顶掉）；状态冲突 ⇒ 服务端 409 且带
        `existing_confirmation_id`。两种处置**相反**，混在一起会把用户送进死循环。
    四、**确认成功**：真实点击确认 ⇒ 确认卡出现（含成果引用与 `recheck` 入口），
        且**服务端读得回来**（API 直证）—— "页面说成功"不算数。

    ⚠️ 诚实边界（按档登记，**不计入通过**）
    * 本章**自足**：前置只用 `seed_entrust_demo.py` 铺的演示组织（`演示经营主体·工作台`）、
      `seed-owner`（该组织的经理，带 `entrust:quote:create`）与委托
      `演示委托·工作台样本`（`claimed`）。**刻意不依赖 ㊸ 章** —— 依赖一条长链会让本章的
      失败与 ㊸ 的失败混在一起，而两者的处置完全不同。
    * 本章**不做**客户侧：整组运力端点**都没有客户面**（见 `S3-运力确认与有效期切片.md` §6）。
      也**不验**「两个候选并排比较」—— 那是 §7.18 的界面，其设备证据另行登记。
    * 登记与确认会**真的写库**（候选 + 确认 + 成果版本）。走查用的是
      `run_walkthrough_devtools.py` 建的临时库，跑完即弃。
    """
    print("\n-- ㊺ 运力确认闭环（真实点击）--", flush=True)

    import datetime as _dt

    # console 基线：**只统计本章的增量**。IDE 可能被复用（`--skip-ide`），
    # 其上 console 是累计的 ⇒ 不取基线会把上一章的报错算到本章头上。
    err_base = w.c.errors()

    code_mgr = "seed-owner"
    org_name = "演示经营主体·工作台"
    title_main = "演示委托·工作台样本"
    cand_sel = '[data-df="cap-carrier"]'

    # ── 前置：全部**经 API 取**，不写死 id（种子重铺会变）──────────────────
    tok = (api_login(code_mgr) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("㊺ 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return
    org_id = ""
    for r in (api_get("/entrust/my-orgs", tok) or {}).get("items") or []:
        if str((r or {}).get("name") or "") == org_name:
            org_id = str((r or {}).get("org_id") or "")
    if not org_id:
        w.rep.not_run("㊺ 全部断言", f"seed-owner 的组织里没有「{org_name}」")
        return

    def _org_rows() -> list:
        data = api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", tok) or {}
        return data.get("items") or []

    def _newest_aid(title: str) -> str:
        hit = [r for r in _org_rows() if str((r or {}).get("title") or "") == title]
        hit.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
        return str((hit[0] or {}).get("assignment_id") or "") if hit else ""

    aid = prefer_anchor(_newest_aid(title_main))
    if not aid and not WALK_ANCHOR:
        w.rep.not_run("㊺ 全部断言", f"该组织下找不到「{title_main}」（种子未铺？）")
        return

    if not w.open_workbench(code_mgr, tag="㊺"):
        w.rep.not_run("㊺ 全部断言", "未能以 seed-owner 进入经理工作台")
        return
    # 组织显式钉住：`pickOrg` 的 `saved` 分支跨 IDE 重启保留 ⇒ 不钉就可能落在别的组织
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.set_storage(ORG_STORAGE_KEY, org_id)
    w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL)
    d0 = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)

    # ============ 一、运力块渲染（先证有）============
    print("\n-- 一、运力块渲染（先证有）--", flush=True)
    n_open = w.c.count('[data-act-cap-open="1"]')
    w.rep.rec(
        "㊺ ① 经理侧详情页真的渲染出运力块：**登记入口唯一可点**。"
        "没有它，后面三条（可输入 / 409 分流 / 确认成功）全部无从谈起",
        d0.get("canViewCapacity") is True and n_open == 1,
        f"canViewCapacity={d0.get('canViewCapacity')!r} 入口命中={n_open}",
    )
    w.shot("45-1-运力块-登记入口")

    # ============ 二、登记表单可输入（真机打字 ⇒ data 里真有值）============
    print("\n-- 二、登记表单可输入 --", flush=True)
    t_form = w.c.tap('[data-act-cap-open="1"]')
    time.sleep(1.2)
    d_form = w.c.page_data()
    n_sub = w.c.count('[data-act-cap-submit="1"]')
    n_cxl = w.c.count('[data-act-cap-cancel="1"]')
    w.rep.rec(
        "㊺ ②a 点「登记候选运力」⇒ 表单在**页内**展开（提交 / 取消同时可点）",
        bool(t_form) and d_form.get("capOpen") is True and n_sub == 1 and n_cxl == 1,
        f"tap={t_form} capOpen={d_form.get('capOpen')!r} submit={n_sub} cancel={n_cxl}",
    )

    stamp = time.strftime("%m%d-%H%M%S")
    carrier_ok = "㊺ 甲承运 " + stamp
    carrier_exp = "㊺ 过期承运 " + stamp
    # ⚠️ 基准日用 **UTC 日期**：与后端 `capacity.today_utc()` 同一口径。
    #    用本机时区的 `date.today()` 会在时区边界上与判定基准日差一天，
    #    而"有效期至"恰好卡在边界时，差一天就是从"通过"变成"过期"。
    today_utc = _dt.datetime.now(_dt.UTC).date()
    future = (today_utc + _dt.timedelta(days=90)).isoformat()
    past = (today_utc - _dt.timedelta(days=90)).isoformat()

    def _fill(carrier: str, valid: str) -> bool:
        """按登记表单的字段顺序真机打字。返回到目前为止的输入是否都成功。"""
        pairs = (
            ('[data-df="cap-carrier"]', carrier),
            ('[data-df="cap-vessel"]', "㊺-6688"),
            ('[data-df="cap-tonnes"]', "900"),
            ('[data-df="cap-vessels"]', "1"),
            ('[data-df="cap-rate"]', "45"),
            ('[data-df="cap-valid"]', valid),
            ('[data-df="cap-evidence-ref"]', "att:451"),
        )
        ok_all = True
        for sel, val in pairs:
            # ⚠️ 先**滚进视口**：`input_text` 自己**不滚动**（只有 `tap` 自带重试）。
            #    长页面里输入框掉到折叠线以下时，直接打会失败，而它只回一个
            #    `ok=false` —— 不看返回值就等于"什么都没发生"。本章第一轮就是
            #    这么在 ③/④ 段连着丢了两条证据（日志里只剩一句"判定行=0"）。
            w.scroll_into(sel)
            if not w.c.input_text(sel, val):
                ok_all = False
        # 装载口径与证据类别是**按钮**不是输入框（`data-act-cap-partial` / `-kind`）
        w.c.tap('[data-act-cap-partial="1"]')
        w.c.tap('[data-act-cap-kind="document"]')
        time.sleep(0.8)
        return ok_all

    typed_ok = _fill(carrier_ok, future)
    typed_ok = w.c.count(cand_sel) == 1 and typed_ok
    f = w.c.page_data().get("capForm") or {}
    ok_typed = (
        typed_ok
        and str(f.get("carrier") or "") == carrier_ok
        and str(f.get("capacityTonnes") or "") == "900"
        and str(f.get("validUntil") or "") == future
        and str(f.get("evidenceKind") or "") == "document"
        and f.get("allowsPartialLoad") is True
    )
    w.rep.rec(
        "㊺ ②b 真机打的字**真的进了页面 data**（判据是 capForm 里有值，不是"
        "「输入框看得见」）—— 本切片那个只在真机上暴露的缺陷（`data-df` 在 handler 的"
        "映射表里没有对应项 ⇒ `bindinput` 照常触发、查表查不到就静默空转）正是死在这一条上",
        ok_typed,
        f"carrier={f.get('carrier')!r} 吨位={f.get('capacityTonnes')!r} "
        f"有效期={f.get('validUntil')!r} 类别={f.get('evidenceKind')!r} "
        f"拆批={f.get('allowsPartialLoad')!r} 输入回执={typed_ok}",
    )
    w.shot("45-2-登记表单-已打字")

    # 真机提交 ⇒ 候选真的落库（**API 直证**，不看页面那张 toast）
    t_submit = w.c.tap('[data-act-cap-submit="1"]')
    got_cand: dict = {}

    def _cand_of(carrier: str) -> dict:
        got_cand.clear()
        items = api_get(f"/entrust/assignments/{aid}/capacity-candidates", tok) or {}
        rows = items if isinstance(items, list) else (items.get("items") or [])
        for r in rows:
            if str((r or {}).get("carrier") or "") == carrier:
                got_cand.update(r or {})
                return got_cand
        return {}

    ok_cand = False
    for _ in range(20):
        time.sleep(0.5)
        if _cand_of(carrier_ok):
            ok_cand = True
            break
    w.rep.rec(
        "㊺ ②c 真实点击「登记候选运力」⇒ 候选**真的落库**（API 直证；页面 toast 不算证据）",
        ok_cand,
        f"tap={t_submit} candidate_id={got_cand.get('candidate_id')!r} "
        f"status={got_cand.get('status')!r}",
    )
    w.shot("45-3-候选已登记")
    if not ok_cand:
        w.rep.not_run("㊺ ③④ 确认链路", "登记未落库 ⇒ 后面两条证据的前提不存在")
        return

    cid_ok = str(got_cand.get("candidate_id") or "")

    # ============ 三、409 分流之一：**规则不过**（判定表要照实显示、且不刷新）============
    print("\n-- 三、409 分流（规则不过）--", flush=True)
    w.c.tap('[data-act-cap-open="1"]')  # 表单可能已被提交后收起
    time.sleep(1.0)
    _fill(carrier_exp, past)
    w.c.tap('[data-act-cap-submit="1"]')
    got_exp: dict = {}
    for _ in range(20):
        time.sleep(0.5)
        if _cand_of(carrier_exp):
            got_exp.update(got_cand)
            break
    if not got_exp:
        w.rep.not_run("㊺ ③ 规则不过的 409 分流", "过期候选未登记成功，无法造出规则不过")
    else:
        cid_exp = str(got_exp.get("candidate_id") or "")
        t_open_exp = w.c.tap(f'[data-act-cap-confirm-open="{cid_exp}"]')
        time.sleep(1.2)
        d_ok_open = w.c.page_data()
        # ⚠️ 本断言原文写的是「范围 / 备注可输入 + 提交可点」，但第一版只看了
        #    `capConfirmKey`（**内部状态**）—— 那个量在"条根本没渲染出来"时也照样被置上，
        #    于是断言绿着、真机上却点不出任何输入框。判别依据必须落在**渲染树**上：
        #    范围框 / 备注框 / 提交键各命中 1 次，且 `wx:if` 的两侧类型一致
        #    （`capConfirmKey` 是字符串，`item.candidateId` 若给数字则 `===` 恒假）。
        n_scope_exp = w.c.count('[data-df="cap-scope"]')
        n_note_exp = w.c.count('[data-df="cap-note"]')
        n_csub_exp = w.c.count(f'[data-act-cap-confirm-submit="{cid_exp}"]')
        key_ok = (
            d_ok_open.get("capConfirmKey") in (cid_exp, int(cid_exp))
            if cid_exp.isdigit()
            else False
        )
        w.rep.rec(
            "㊺ ③a 点「确认这一条」⇒ 确认条在**页内**展开（范围 / 备注可输入 + 提交可点）"
            " —— 判据取**渲染树**：三个可点/可输入锚点各命中一次，"
            "不看内部状态键（它条都没渲染时也照样被置上）",
            key_ok and n_scope_exp == 1 and n_note_exp == 1 and n_csub_exp == 1,
            f"tap={t_open_exp} capConfirmKey={d_ok_open.get('capConfirmKey')!r} "
            f"期望={cid_exp!r} 范围框={n_scope_exp} 备注框={n_note_exp} 提交键={n_csub_exp}",
        )
        w.scroll_into('[data-df="cap-scope"]')
        i_scope_exp = w.c.input_text('[data-df="cap-scope"]', "㊺ 过期候选的范围")
        w.scroll_into(f'[data-act-cap-confirm-submit="{cid_exp}"]')
        t_csub_exp = w.c.tap(f'[data-act-cap-confirm-submit="{cid_exp}"]')
        time.sleep(3.0)
        d_exp = w.c.page_data()
        rows_exp = d_exp.get("capRuleRows") or []
        codes_exp = [str((r or {}).get("ruleCode") or "") for r in rows_exp]
        passed_exp = [
            c for c, r in zip(codes_exp, rows_exp, strict=True) if (r or {}).get("passed")
        ]
        w.rep.rec(
            "㊺ ③b 规则不过 ⇒ 页面把**逐条**判定照实显示（四条规则全在，"
            "**含通过项** —— 后端特意全给，前端不得过滤）",
            len(rows_exp) >= 4,
            f"判定行={len(rows_exp)} codes={codes_exp} "
            f"提交键 tap={t_csub_exp} 范围输入回执={i_scope_exp} "
            f"页内提示={d_exp.get('capHint')!r}",
        )
        w.rep.rec(
            "㊺ ③c 过期候选**只在有效期上**不通过（根因要分得出来："
            "「这一条为什么不行」与「这些规则都跑了」是两件事）",
            "validity" in codes_exp
            and len([r for r in rows_exp if not (r or {}).get("passed")]) == 1,
            f"通过项={passed_exp} 未过项="
            f"{[c for c, r in zip(codes_exp, rows_exp, strict=True) if not (r or {}).get('passed')]}",
        )
        w.shot("45-4-规则不过的逐条判定")

    # ============ 四、确认成功（合规候选）============
    print("\n-- 四、确认成功 --", flush=True)
    scope_text = "㊺ 全程 900 吨舱位"
    w.c.tap('[data-act-cap-cancel="1"]')  # 先把可能的展开条收起来
    time.sleep(0.8)
    t_open_ok = w.c.tap(f'[data-act-cap-confirm-open="{cid_ok}"]')
    time.sleep(1.2)
    # ⚠️ 与 ②③ 段同一坑：`input_text` **自己不滚动**，只回一个 `ok=false`。
    #    不先滚进视口 ⇒ 范围打不进去 ⇒ `onSubmitCapConfirm` 的页内校验
    #    （`if (!scope) setData({capHint:…}); return`）**静默拦住**，
    #    连 Http 都不会发 —— 症状是 ④a 落库失败 + ⑤ 拿到 200 而非 409，
    #    看起来像"后端没挡"，实际是"前端根本没提交"。第一轮就是这么红的。
    w.scroll_into('[data-df="cap-scope"]')
    n_scope_ok = w.c.count('[data-df="cap-scope"]')
    i_scope_ok = w.c.input_text('[data-df="cap-scope"]', scope_text)
    time.sleep(0.4)
    d_before = w.c.page_data()
    n_conf_before = len(d_before.get("capConfirmations") or [])
    w.scroll_into(f'[data-act-cap-confirm-submit="{cid_ok}"]')
    t_csub_ok = w.c.tap(f'[data-act-cap-confirm-submit="{cid_ok}"]')
    conf: dict = {}

    def _conf_of() -> dict:
        conf.clear()
        data = api_get(f"/entrust/assignments/{aid}/capacity-confirmations", tok) or {}
        rows = data if isinstance(data, list) else (data.get("items") or [])
        hit = [r for r in rows if str((r or {}).get("candidate_id") or "") == cid_ok]
        if hit:
            conf.update(hit[0] or {})
        return conf

    ok_conf = False
    for _ in range(24):
        time.sleep(0.5)
        if _conf_of():
            ok_conf = True
            break
    d_after_submit = w.c.page_data()
    w.rep.rec(
        "㊺ ④a 真实点击「确认运力」⇒ 确认**真的落库**（API 直证：服务端读得回来）"
        " —— 一条候选只能确认一次是库上的唯一约束，不是先查后写",
        ok_conf,
        f"confirmation_id={conf.get('confirmation_id')!r} "
        f"artifact_id={conf.get('artifact_id')!r} "
        f"rules={len(conf.get('rule_checks') or [])} "
        f"范围框={n_scope_ok} 打开 tap={t_open_ok} 提交 tap={t_csub_ok} "
        f"范围输入回执={i_scope_ok} 提交后页内提示={d_after_submit.get('capHint')!r}",
    )
    # ④b/④c/④d 都以「确认已落库」为前提。前提不在就记 `not_run`、不记 FAIL ——
    # 一条根因摊成四个 FAIL 会让人以为有四处缺陷（第一轮正是如此：`input_text`
    # 没滚动 ⇒ ④a④c④d 与 ⑤ 一起红，实际只有一个原因）。
    # `not_run` 不是通过，它只是**不虚报**。
    if ok_conf:
        w.rep.rec(
            "㊺ ④b 确认产出**采购确认成果**且逐规则判定落库（4 条；少一条＝有规则没跑）",
            bool(conf.get("artifact_id"))
            and len(conf.get("rule_checks") or []) == 4
            and str(conf.get("rule_set_version") or "") != "",
            f"artifact={conf.get('artifact_id')!r} "
            f"rules={len(conf.get('rule_checks') or [])} "
            f"ruleset={conf.get('rule_set_version')!r}",
        )
    else:
        w.rep.not_run("㊺ ④b 确认产出的成果与逐规则判定", "④a 未落库 ⇒ 没有成果可查")
    time.sleep(2.0)
    d_after = w.c.page_data()
    n_conf_after = len(d_after.get("capConfirmations") or [])
    if ok_conf:
        w.rep.rec(
            "㊺ ④c 确认成功后页面**重取并渲染**出确认卡（条数 +1），"
            "而不是前端往数组里塞一行 —— 候选状态也从 confirmed 回来",
            n_conf_after > n_conf_before,
            f"确认前={n_conf_before} 确认后={n_conf_after}",
        )
    else:
        w.rep.not_run("㊺ ④c 确认卡重取重渲染", "④a 未落库 ⇒ 页面上不会有新的确认卡")
    n_recheck = w.c.count(f'[data-act-cap-recheck="{conf.get("confirmation_id")}"]')
    if ok_conf:
        w.rep.rec(
            "㊺ ④d 确认卡上的「只读复算」入口可被**唯一命中**"
            "（D1-09 的可点形态：用当前事实重跑同一套规则，不改任何行）",
            n_recheck == 1,
            f"data-act-cap-recheck 命中={n_recheck}",
        )
    else:
        w.rep.not_run("㊺ ④d「只读复算」入口可被唯一命中", "④a 未落库 ⇒ 没有确认卡可点")

    # ④e / ④f：把「复算」**真的点一次**。只证"入口可点"是不够的 ——
    # 判定表的渲染条件是 `wx:if="{{capRecheckId === item.confirmationId && capRecheck}}"`，
    # 两侧同样是"字符串 ⇄ API 的 int"这一对（`capRecheckId` 来自 `String(dataset)`）。
    # 不点下去、不看渲染树，就正好漏掉 ③a 那一处缺陷的**同源第二例**。
    if ok_conf and n_recheck == 1:
        conf_before_rc = dict(conf)
        sel_rc = f'[data-act-cap-recheck="{conf.get("confirmation_id")}"]'
        w.scroll_into(sel_rc)
        t_rc = w.c.tap(sel_rc)
        n_verdict = 0
        for _ in range(24):
            time.sleep(0.5)
            n_verdict = w.c.count(f'[data-act-cap-recheck-verdict="{conf.get("confirmation_id")}"]')
            if n_verdict:
                break
        rc_payload = w.c.page_data().get("capRecheck") or {}
        w.rep.rec(
            "㊺ ④e 点「复算这条确认」⇒ **判定表真的渲染出来**（结果锚点唯一命中），"
            "而不是「请求发了、页面什么都没多出来」—— 「点了没反应」与「算出来不成立」"
            "在截图上长得一样，只有渲染树分得开",
            n_verdict == 1 and bool(rc_payload),
            f"tap={t_rc} 判定表锚点={n_verdict} "
            f"validLabel={rc_payload.get('validLabel')!r} 基准日={rc_payload.get('asOfDate')!r} "
            f"变化={rc_payload.get('changedText')!r}",
        )
        conf_after_rc = _conf_of() or {}
        diff_keys = sorted(
            k
            for k in set(conf_before_rc) | set(conf_after_rc)
            if conf_before_rc.get(k) != conf_after_rc.get(k)
        )
        w.rep.rec(
            "㊺ ④f 复算**不改任何行**（D1-09 的「只读」）：复算前后确认行逐字相同 —— "
            "「不再成立」是**按当时事实重跑**得出的结论，不是把这条确认作废掉",
            bool(conf_after_rc) and not diff_keys,
            f"不同的键={diff_keys} "
            f"confirmation_id={conf_after_rc.get('confirmation_id')!r} "
            f"artifact_id={conf_after_rc.get('artifact_id')!r} "
            f"rules={len(conf_after_rc.get('rule_checks') or [])}",
        )
    else:
        w.rep.not_run("㊺ ④e 复算判定表渲染", "④d 入口未命中 ⇒ 没有可点的复算键")
        w.rep.not_run("㊺ ④f 复算只读（不改行）", "④d 入口未命中 ⇒ 复算没发生过")
    w.shot("45-5-确认成功")

    # ============ 五、负例：同一候选二次确认 ⇒ 状态冲突（API 直证）============
    print("\n-- 五、负例：二次确认（状态冲突）--", flush=True)
    if not ok_conf:
        # 「二次」的前提是「一次」已经落库。前提不在 ⇒ 这一发只会**成功一条**，
        # 拿到 200 是**必然**、不是缺陷；如实记 not_run。
        w.rep.not_run("㊺ ⑤ 同一候选二次确认 ⇒ 409", "④a 未落库 ⇒ 不存在「第二次」")
    else:
        st, body = api_post(
            f"/entrust/assignments/{aid}/capacity-confirmations",
            tok,
            {
                "candidate_id": int(cid_ok) if cid_ok.isdigit() else cid_ok,
                "agreed_scope": "㊺ 二次确认",
            },
            "walk45-dup-" + stamp,
        )
        # ⚠️ 后端的 409 把结构化信息放在 **`detail` 里**（FastAPI 的 `HTTPException(detail=…)`
        #    会包一层）：规则不过 ⇒ `detail.rule_checks`；状态冲突 ⇒
        #    `detail.existing_confirmation_id` / `detail.existing_artifact_id`。
        #    第一版在这里按**顶层**读 `existing_confirmation_id`，于是实测 409 却判 FAIL ——
        #    是断言读错了层，不是服务端少了字段。响应体原样收进回执，避免再猜。
        det = (body or {}).get("detail") if isinstance(body, dict) else None
        det = det if isinstance(det, dict) else {}
        w.rep.rec(
            "㊺ ⑤ 同一候选二次确认 ⇒ **409**，且 `detail.existing_confirmation_id` 指回"
            "已存在的那条确认（客户端据此去读它，而不是拿一句「已经确认过了」把用户拦住）"
            " —— 界面对这一种要**刷新**，与「规则不过⇒不刷新」处置相反；"
            "混在一起会把用户送进死循环。注：界面侧的这条分支驱动不到"
            "（候选确认后 `confirmable=false`，确认条不再出现）⇒ 前端那一支由静态门禁守",
            st == 409 and bool(det.get("existing_confirmation_id")),
            f"status={st} 顶层keys={sorted((body or {}).keys()) if isinstance(body, dict) else body} "
            f"detail.message={det.get('message')!r} "
            f"existing_confirmation_id={det.get('existing_confirmation_id')!r} "
            f"existing_artifact_id={det.get('existing_artifact_id')!r} "
            f"（本次确认 id={conf.get('confirmation_id')!r}）",
        )

    # ============ 六、本章运行期 console 无未归因错误 ============
    errs = w.new_errors(err_base)
    if errs is None:
        w.rep.review_required(
            "㊺ 本章运行期 console 无未归因错误",
            "采集**失败**（返回 None，不是空串）—— 不能把'采不到'当成'没有错误'",
        )
    elif errs.strip():
        w.rep.review_required("㊺ 本章运行期 console 无未归因错误", errs[:400])
    else:
        w.rep.rec("㊺ 本章运行期 console 无未归因错误", True, "增量 0 条")


def sec_46(w: Walker) -> None:
    """㊻ 运输计划（合同 §10.1 第 4 步）——设备侧运行取证。

    合同 §10.1 第 4 步的原文：

        Show the road–water–road plan and required task prerequisites.

    为什么单独一章
    --------------
    §7.21 / §7.22 把这一条的**读模型、界面、写命令**都做完了，证据全是**自动化**的
    （后端用例 + e2e 真 HTTP + 三个前端静态门禁）。自动化答得了"逻辑对不对"，
    答不了两件事：① 真机上那块卡到底渲染出来没有；② 写命令打到**真后端**之后，
    **页面**读回来的是不是同一份事实。

    本节覆盖（两条，各自对应一个可能静默失效的环节）
    ------------------------------------------------
    一、**写→读闭环，经 API 写、经界面读**：经写命令真建一段（并改一次留版本），
        再让**页面**去读 —— 页面看到的段数、方式标签、段序必须与**服务端事实**逐项同源。
        这一条把"命令落地了但界面看不到／界面读的是另一份"挡掉。
    二、**两类行的渲染判据落在渲染树**：`.plan-leg` / `.plan-task` / `.plan-task-pre`
        三个类选择器（外加带值属性 `[data-plan-leg="值"]`）。⛔ **不用裸属性
        `[data-x]`** —— 本工具链对它静默回 0（`wechatide_client.count` 已直接拒绝）。
        ⚠️ 刻意**不看**页面内部状态键 ——
        状态键在"整块根本没渲染"时也可能被置上（㊺ 章实测过这种假绿）。

    ⚠️ 诚实边界（按档登记，**不计入通过**）
    * **本章的写操作全部经 API**（与 ㊼ 章的**分工**，不是缺口）：㊻ 答"写命令落地后页面读得对"，
      ㊼ 答"经界面写得进去"。⇒ 本章**不**声称"界面能建段"—— 那是 ㊼ 的证据，**章节之间不借证据**。
      （订正：本节曾把这一格记成 `LIMITATION`／"写侧界面不存在"；写侧界面已于 §7.24 落地、
      ㊼ 章已取证 ⇒ 那句话**过期**，留着它会把整个序列的 `RESULT` 误报成 `NOT_RUN`。）
    * **载体运行期造**（不新造种子）：`run_walkthrough_devtools.py` 的种子
      （`seed_demo` / `seed_entrust_demo` / `seed_entrust_orgpicker`）**不含**
      `seed_entrust_canonical.py`，而临时库里唯一可能带航段的就是它
      ⇒ 本章运行期经 API 建段（技能坑 48 的取向），并用一个**种子不会用到**的 `seq`。
    * 本章**会写库**（1 段航段 + 1 条版本历史）。走查用临时库、跑完即弃；
      共享库上重跑仍成立（读者按本次的 `seq` 定位），但会留下痕迹。
    * 本章**自足**：只依赖 `seed_entrust_demo.py` 铺的组织（`演示经营主体·工作台`）、
      `seed-owner` 与委托 `演示委托·工作台样本`（`claimed`），**刻意不依赖其它章节**
      ⇒ 可单跑：`--section 46`。
    """
    print("\n-- ㊻ 运输计划（第 4 步 · 真实渲染）--", flush=True)

    err_base = w.c.errors()

    code_mgr = "seed-owner"
    org_name = "演示经营主体·工作台"
    title_main = "演示委托·工作台样本"
    #: ⚠️ 取一个**种子不会用到**的 seq：种子那 7 个任务槽位与 canonical 的 1–3 都不占它，
    #: 于是本章可重复运行（第二次会因唯一键撞号 ⇒ 走"改段"这条，见下）。
    walk_seq = 88
    mode_raw = "air"  # 故意用一个**未登记**的方式：顺带验"未知保持未知，不兜底成公路"
    frm, to = "走查起点", "走查终点"

    def _patch(path: str, token: str, payload: dict, idem: str) -> tuple[int, dict | None]:
        """`api_post` 只发 POST，而改段是 PATCH ⇒ 这里就地补一个。

        ⚠️ `_http(req)` 是**函数**、返回 `(status, data)`，**不是上下文管理器** ——
        第一版在这里写了 `with _http(req):`，于是抛 TypeError 被兜底 `except` 吞掉、
        一路 `return 0, None`，断言里读到 `HTTP=0`（"请求好像没发出去"），
        而**服务端其实写成功了**（历史里两版都在）。
        ⇒ 这正是技能坑 39 的形态：**状态码取不到时，别让它伪装成"请求没发出"**。
        照抄 `api_post` 尾部那几行（同文件已有正确写法，别自己重想）。
        """
        body = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            API_BASE + path,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
                "Idempotency-Key": idem,
            },
            method="PATCH",
        )
        try:
            return _http(req)
        except urllib.error.HTTPError as exc:  # 409/404 要拿到状态码，不当成异常
            try:
                raw = exc.read().decode("utf-8", "replace")
                return int(exc.code), (json.loads(raw) if raw.strip() else None)
            except Exception:  # noqa: BLE001
                return int(exc.code), None
        except Exception:  # noqa: BLE001
            return 0, None

    # ── 前置：全部经 API 取，不写死 id（种子重铺会变）─────────────────────
    tok = (api_login(code_mgr) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("㊻ 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return
    org_id = ""
    for r in (api_get("/entrust/my-orgs", tok) or {}).get("items") or []:
        if str((r or {}).get("name") or "") == org_name:
            org_id = str((r or {}).get("org_id") or "")
    if not org_id:
        w.rep.not_run("㊻ 全部断言", f"seed-owner 的组织里没有「{org_name}」")
        return
    rows = (api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", tok) or {}).get(
        "items"
    ) or []
    hit = [r for r in rows if str((r or {}).get("title") or "") == title_main]
    hit.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
    if not hit and not WALK_ANCHOR:
        w.rep.not_run("㊻ 全部断言", f"该组织下找不到「{title_main}」（种子未铺？）")
        return
    aid = prefer_anchor(str((hit[0] or {}).get("assignment_id") or ""))

    # ── 写①：建段（经 API —— 写侧没有界面，见 docstring）───────────────
    st1, body1 = api_post(
        f"/entrust/assignments/{aid}/legs",
        tok,
        {
            "seq": walk_seq,
            "mode": mode_raw,
            "from_name": frm,
            "to_name": to,
            "change_note": "㊻ 章走查载体",
        },
        idem_key=f"walk46-create-{aid}",
    )
    if st1 == 409:
        # 共享库上重跑：那一 `seq` 已被上一次占用 ⇒ 这不是失败，改走"改段"。
        print("  （该 seq 已存在 ⇒ 走改段分支，属预期）", flush=True)
        legs_now = (api_get(f"/entrust/assignments/{aid}/plan", tok) or {}).get("legs") or []
        old = [x for x in legs_now if int((x or {}).get("seq") or 0) == walk_seq]
        body1 = old[0] if old else {}
        st1 = 200 if old else st1
    leg_id = str((body1 or {}).get("leg_id") or "")
    w.rep.rec(
        "㊻ ① 写命令真建段（**经 API**）：建段 + 写下第 1 版历史。"
        "⚠️ 写侧界面由 ㊼ 章取证（两章分工）⇒ 这一格证明的是**写链路**，**不是**「界面能建段」",
        st1 == 200 and bool(leg_id) and int((body1 or {}).get("revision_no") or 0) >= 1,
        f"HTTP={st1} leg_id={leg_id!r} revision_no={(body1 or {}).get('revision_no')!r}",
    )
    if not leg_id:
        w.rep.not_run("㊻ ② ～ ⑤", "建段没拿到 leg_id（后面的读侧断言没有锚点）")
        return

    # ── 写②：改段（必须**留版本**）─────────────────────────────────────
    st2, body2 = _patch(
        f"/entrust/assignments/{aid}/legs/{leg_id}",
        tok,
        {"to_name": to + "（改）", "change_note": "㊻ 章改段留版本"},
        f"walk46-update-{aid}-{leg_id}",
    )
    hist = api_get(f"/entrust/assignments/{aid}/legs/{leg_id}/revisions", tok) or []
    kinds = [str((r or {}).get("change_kind") or "") for r in hist]
    w.rep.rec(
        "㊻ ② 改段**留版本**（而不是覆盖）：改完之后历史里**两版都在**，"
        "且第 1 版记的仍是**当时**的到达地（快照，不是回连当前行）",
        st2 == 200 and len(hist) >= 2 and kinds[0] == "created" and kinds[-1] == "updated",
        f"HTTP={st2} 历史={len(hist)} 版 kinds={kinds} 第1版到达地={(hist[0].get('to_name') if hist else None)!r}",
    )

    # ── 读：服务端事实（对照组）─────────────────────────────────────────
    plan = api_get(f"/entrust/assignments/{aid}/plan", tok) or {}
    legs_srv = plan.get("legs") or []
    tasks_srv = plan.get("task_prerequisites") or []
    mine = [x for x in legs_srv if int((x or {}).get("seq") or 0) == walk_seq]

    if not w.open_workbench(code_mgr, tag="㊻"):
        w.rep.not_run("㊻ ③ ～ ⑤", "未能以 seed-owner 进入经理工作台")
        return
    # 组织显式钉住（`pickOrg` 的 `saved` 分支跨 IDE 重启保留）
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.set_storage(ORG_STORAGE_KEY, org_id)
    w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL)
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    # 计划是**第二轮并行取数**（`load()` 里与运力块并行）⇒ 必须等到它落到 data，
    # 否则会把"还没取回来"读成"没有计划"。
    d = w.wait_data(lambda x: x.get("plan") is not None, tries=60, gap=0.5)

    # ⚠️⚠️ 判据只用**类选择器**与**带值属性**两条通道数行数。
    # 第一轮只用了裸属性 `[data-plan-leg]`（当时 `count` 还没守卫），得 **0**；
    # 三条一起打的结果是：
    #     段行 按类=1  裸属性=0  值属性=1     （服务端 1 段，hasLegs=True）
    #     任务行 按类=7 裸属性=0 值属性=—      （服务端 7 条，hasTasks=True）
    # ⭐ 结论：**这个工具链不认「裸属性选择器」`[attr]`** —— `[attr="值"]` 认、
    #    类选择器认，而**不带 `=值` 的 `[attr]` 静默回 0**（不是报错、不是 -1
    #    ⇒ 与"元素不存在"同形）。㊺ 章一直用带值选择器，所以这条一直没暴露。
    # ⇒ 已把这条限制**收进代码**：`wechatide_client.count()` 遇到裸属性选择器直接
    #    抛 `ValueError`（写在注释里没人看，写在取值通道里才拦得住下一个人）。
    #    要复核"裸属性确实回 0"这个**证据**，就得显式走低层的
    #    `query_selector_all`（无守卫）—— 下面那一行就是这么用的：
    #    它是**取证**，不参与任何判据（判据只看 `n_*_cls` / `n_*_val`）。
    n_leg_cls = w.c.count(".plan-leg")
    n_leg_val = w.c.count(f'[data-plan-leg="{walk_seq}"]')
    _bare = w.c.query_selector_all("[data-plan-leg]")
    n_leg_attr = -1 if _bare is None else len(_bare)
    pl = d.get("plan") or {}
    w.rep.rec(
        "㊻ ③ 运输计划卡**真的渲染**：段行数 = 服务端段数（判据落渲染树，不看内部状态键）",
        n_leg_cls == len(legs_srv) and n_leg_cls >= 1,
        f"段行 按类={n_leg_cls} 裸属性={n_leg_attr} 值属性={n_leg_val}"
        f" 服务端段数={len(legs_srv)} hasLegs={pl.get('hasLegs')!r}"
        f" data.legs={len(pl.get('legs') or [])}",
    )
    ui_mine = [x for x in (pl.get("legs") or []) if str((x or {}).get("seqText")) == str(walk_seq)][
        :1
    ]
    w.rep.rec(
        "㊻ ④ 页面读到的那一段与**服务端事实**同源，且**未登记的运输方式原样回显**"
        "（`air` ⇒ 显示 `air`，不兜底成「公路」）",
        bool(ui_mine)
        and bool(mine)
        and str(ui_mine[0].get("modeText")) == str(mine[0].get("mode_label") or "")
        and str(ui_mine[0].get("modeText")) == mode_raw
        and str(ui_mine[0].get("routeText") or "").find(frm) >= 0,
        f"页面段={ui_mine[0] if ui_mine else None!r} 服务端方式标签={[x.get('mode_label') for x in mine]!r}",
    )
    n_task_cls = w.c.count(".plan-task")
    n_pre_cls = w.c.count(".plan-task-pre")
    # 同上：裸属性的两个读数只为**取证**（`count` 已拒绝这种选择器，走低层通道）。
    _bare_task = w.c.query_selector_all("[data-plan-task]")
    _bare_pre = w.c.query_selector_all("[data-plan-task-pre]")
    n_task_attr = -1 if _bare_task is None else len(_bare_task)
    n_pre_attr = -1 if _bare_pre is None else len(_bare_pre)
    w.rep.rec(
        "㊻ ⑤ 必需任务与**前置**逐行渲染（每一条任务行都带一行前置说明："
        "「无固定前置」或有具体前置 —— 判不了的那一格也必须自己说话）",
        n_task_cls == len(tasks_srv) and n_task_cls >= 1 and n_pre_cls == n_task_cls,
        f"任务行 按类={n_task_cls} 裸属性={n_task_attr}（服务端 {len(tasks_srv)}）"
        f" hasTasks={pl.get('hasTasks')!r}｜前置行 按类={n_pre_cls} 裸属性={n_pre_attr}",
    )

    w.shot("㊻-运输计划卡")
    errs = w.new_errors(err_base)
    w.rep.rec(
        "㊻ ⑥ 本章运行期**无新增 console 报错**",
        not errs.strip(),
        (errs.strip()[:300] if errs.strip() else "无"),
    )
    # ⚠️ **订正（2026-09-18）**：这一格原来记 `LIMITATION`（"写侧界面尚未实现 ⇒ 本章的写操作
    #   全部经 API"）。那句话**已经过期**：写侧界面已落地（§7.24），且有 ㊼ 章在真机上取证。
    #   继续留着它不是"保守"，而是**报错的读数** —— 它会让整个序列的 `RESULT` 归并成
    #   `NOT_RUN`（LIMITATION 的归并口径，见 runbook §8），而实际上两章全绿。
    #   ⇒ 改成一条**章内可自证**的断言：写入口就在渲染树里（界面**是**存在的，这是事实，
    #     不需要借 ㊼ 的结论）。⚠️ 「经界面真的写得进去」仍**不在本章** —— 那由 ㊼ 章取证，
    #     章节之间**不借证据**（不把别人的 PASS 记到这一格上）。
    n_leg_open_entry = w.c.count('[data-act-leg-open="1"]')
    w.rep.rec(
        "㊻ ⑦ 写入口（`data-act-leg-open`）**已在渲染树里** ⇒ 写侧界面存在"
        "（本章的写操作仍**经 API**：经界面写由 ㊼ 章取证，两章分工、不互相借证据）",
        n_leg_open_entry == 1,
        f"写入口元素数={n_leg_open_entry}（界面不存在时这里会是 0 —— 那才是需要记限制的形状）",
    )


def sec_47(w: Walker) -> None:
    """㊼ 航段命令的**写侧界面**（合同 §10.1 第 4 步）——设备侧运行取证。

    与 ㊻ 的分工（两章合起来才是"第 4 步可演示"）
    --------------------------------------------
    * ㊻ 验**读**：写命令经 API 落地之后，**页面**读回来的是同一份事实
      （并在那里把"写侧界面不存在"如实记为 `LIMITATION`）。
    * ㊼ 验**写**：建段与改段**全部经界面**（点按钮 → 填表 → 提交），
      再回到渲染树与服务端事实对账。

    ⛔ **㊻ 的 `LIMITATION` 由本章解除** —— 但"解除"的证据只能是**本章的结果**：
    §7.22 之后又落了写侧界面（`data-act-leg-open` / `-edit` / `-hist` / `-mode` /
    `-submit` / `-cancel`），本章就是它在真机上的取证。

    本节覆盖（四条，各对应一个会静默失效的环节）
    ----------------------------------------------
    一、**写入口在渲染树里**（`[data-act-leg-open="1"]`）+ 段行数 = 服务端段数。
    二、**经界面建段**：点开表单 → 输入（`input_text` 真触发 `bindinput`）→ 提交，
        页面段行数、段序、方式标签与**服务端事实**逐项同源；未登记的方式
        （`air`）原样显示（不兜底成「公路」）。
    三、**经界面改段**：表单**回填服务端原值**（不是显示文案）；「一个字段都没改」
        时**本地就拦下**（不发注定 400 的请求）；真改之后段行变成服务端的标签，
        且**服务端历史里两版都在**（改段留版本，不是覆盖）。
    四、**版本历史渲染出来**：`.plan-rev` 行数 ≥ 2，且**第 1 版仍是当时的方式** ——
        判据必须读第 1 版的内容：历史被当前值覆盖时，行数**照样**是 2。

    ⚠️ 诚实边界
    * **身份取 `seed-owner`**（经理）：这是详情页工作台能直接装载的身份。
      「货主本人也能建段」这条判据的证据在 **e2e**（`shipperToken` 经页面真写），
      **不在本章** —— 不把 e2e 的结论借来给设备侧记功。
    * **载体运行期造**：种子（`seed_demo` / `seed_entrust_demo` / `seed_entrust_orgpicker`）
      不含带航段的夹具，本章的段**由本章自己经界面建出来**（不新造种子）。
    * `seq` 取"服务端现有最大序号 + 1" ⇒ 共享库上重跑仍成立（不会撞号）。
    * 本章**会写库**（1 段航段 + 2 条版本历史）。临时库跑完即弃。
    * 本章**自足**：只依赖 `seed_entrust_demo.py` 铺的组织与委托
      `演示委托·工作台样本`，刻意不依赖其它章节（含 ㊻）⇒ 可单跑：`--section 47`。
    """
    print("\n-- ㊼ 航段命令写侧界面（第 4 步 · 真机建段/改段/版本历史）--", flush=True)

    err_base = w.c.errors()

    code_mgr = "seed-owner"
    org_name = "演示经营主体·工作台"
    title_main = "演示委托·工作台样本"

    # ── 前置：全部经 API 取（不写死 id —— 种子重铺会变）────────────────────
    tok = (api_login(code_mgr) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("㊼ 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return
    org_id = ""
    for r in (api_get("/entrust/my-orgs", tok) or {}).get("items") or []:
        if str((r or {}).get("name") or "") == org_name:
            org_id = str((r or {}).get("org_id") or "")
    if not org_id:
        w.rep.not_run("㊼ 全部断言", f"seed-owner 的组织里没有「{org_name}」")
        return
    rows = (api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", tok) or {}).get(
        "items"
    ) or []
    hit = [r for r in rows if str((r or {}).get("title") or "") == title_main]
    hit.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
    if not hit and not WALK_ANCHOR:
        w.rep.not_run("㊼ 全部断言", f"该组织下找不到「{title_main}」（种子未铺？）")
        return
    aid = prefer_anchor(str((hit[0] or {}).get("assignment_id") or ""))

    plan0 = api_get(f"/entrust/assignments/{aid}/plan", tok) or {}
    legs0 = plan0.get("legs") or []
    seq_next = max([int((x or {}).get("seq") or 0) for x in legs0] + [0]) + 1
    #: 故意用一个**未登记**的方式：页面必须**原样**显示它（投影侧不该把未知兜底成"公路"）。
    mode_raw = "air"
    frm = f"㊼起点{seq_next}"
    to1 = f"㊼终点{seq_next}"
    to2 = f"㊼改后终点{seq_next}"

    if not w.open_workbench(code_mgr, tag="㊼"):
        w.rep.not_run("㊼ 全部断言", "未能以 seed-owner 进入经理工作台")
        return
    # 组织显式钉住（`pickOrg` 的 `saved` 分支跨 IDE 重启保留）
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.set_storage(ORG_STORAGE_KEY, org_id)
    w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL)
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    d = w.wait_data(lambda x: x.get("plan") is not None, tries=60, gap=0.5)

    # ── ① 写入口与段行都在渲染树里 ────────────────────────────────────────
    n_open = w.c.count('[data-act-leg-open="1"]')
    n_leg0 = w.c.count(".plan-leg")
    w.rep.rec(
        "㊼ ① 写入口（「加一段」）与段行都在渲染树里（判据落渲染树，不看内部状态键）",
        n_open == 1 and n_leg0 == len(legs0),
        f"加一段={n_open} 段行={n_leg0} 服务端段数={len(legs0)} plan={(d.get('plan') or {}).get('assignmentId')!r}",
    )

    # ── ② 点开建段表单：渲染出来 + 顺序号预填「最大序号 + 1」──────────────
    w.c.scroll_into('[data-act-leg-open="1"]')
    if not w.c.tap('[data-act-leg-open="1"]'):
        w.rep.rec("㊼ ② 点「加一段」失败（按钮在渲染树里但点不动）", False, "")
    d = w.wait_data(lambda x: x.get("legOpen") is True, tries=20, gap=0.3)
    n_seq_input = w.c.count('input[data-df="leg-seq"]')
    form_seq = str(((d.get("legForm") or {}).get("seq")) or "")
    w.rep.rec(
        "㊼ ② 建段表单渲染出来，且顺序号预填「**最大序号 + 1**」"
        "（契约只要求 seq 唯一、不要求连续 —— 用「段数 + 1」在有空洞时会撞号）",
        n_seq_input == 1 and form_seq == str(seq_next),
        f"输入框={n_seq_input} 预填={form_seq!r} 期望={seq_next} 服务端序号={[x.get('seq') for x in legs0]}",
    )

    # ── ③ 经**界面**输入 + 提交（建段）────────────────────────────────────
    # ⚠️ `seq` **不键入**：它已经预填好了（上面刚验过）——而 `input_text` 是**键入**，
    #    往已有值后面追加，硬打一遍会得到 "99" ⇒ 撞号 409，看起来像产品缺陷。
    typed = (
        w.c.input_text('input[data-df="leg-mode"]', mode_raw)
        and w.c.input_text('input[data-df="leg-from"]', frm)
        and w.c.input_text('input[data-df="leg-to"]', to1)
        and w.c.input_text('input[data-df="leg-note"]', "㊼ 章真机建段")
    )
    w.rep.rec(
        "㊼ ③ 输入真的接上了（`input_text` 触发 `bindinput` ⇒ handler 按 `data-df` 认领到字段）",
        typed,
        "四个输入框都键入成功" if typed else "有输入框没接上（`data-df` 漏映射时正是这个症状）",
    )
    w.c.scroll_into('[data-act-leg-submit="1"]')
    w.c.tap('[data-act-leg-submit="1"]')
    # 提交成功后页面走整页 `load()` ⇒ 段数从服务端回来才算数
    d = w.wait_data(
        lambda x: len((x.get("plan") or {}).get("legs") or []) == len(legs0) + 1,
        tries=40,
        gap=0.5,
    )
    ui_legs = (d.get("plan") or {}).get("legs") or []
    srv_legs = (api_get(f"/entrust/assignments/{aid}/plan", tok) or {}).get("legs") or []
    n_leg1 = w.c.count(".plan-leg")
    n_anchor = w.c.count(f'[data-plan-leg="{seq_next}"]')
    row_wxml = w.c.outer_wxml(f'[data-plan-leg="{seq_next}"]')
    w.rep.rec(
        "㊼ ④ 经**界面**建段：页面段行数 = 服务端段数，新段那一行在渲染树里，"
        "且**未登记的方式原样显示**（`air` ⇒ `air`，不兜底成「公路」）",
        len(ui_legs) == len(srv_legs)
        and n_leg1 == len(srv_legs)
        and n_anchor == 1
        and mode_raw in row_wxml
        and frm in row_wxml
        and to1 in row_wxml,
        f"页面段={len(ui_legs)} 服务端段={len(srv_legs)} 类行={n_leg1} 值锚点={n_anchor} 行文本={row_wxml[:160]!r}",
    )
    added = [x for x in ui_legs if str((x or {}).get("seqText")) == str(seq_next)]
    leg_id = str(((added[0] if added else {}) or {}).get("legId") or "")
    if not leg_id:
        w.rep.not_run("㊼ ⑤ ～ ⑧", "建段后页面里找不到那一段的 legId（后面的断言没有锚点）")
        w.shot("㊼-建段未回读")
        errs = w.new_errors(err_base)
        w.rep.rec(
            "㊼ ⑧ 本章运行期**无新增 console 报错**", not errs.strip(), errs.strip()[:300] or "无"
        )
        return

    # ── ⑤ 点「改这一段」：表单**回填服务端原值** ──────────────────────────
    w.c.scroll_into(f'[data-act-leg-edit="{leg_id}"]')
    w.c.tap(f'[data-act-leg-edit="{leg_id}"]')
    d = w.wait_data(lambda x: str(x.get("legEditingId") or "") == str(leg_id), tries=20, gap=0.3)
    f = d.get("legForm") or {}
    w.rep.rec(
        "㊼ ⑤ 改段表单**回填服务端原值**（方式＝`air` 而不是显示文案）"
        "——回填标签会在库里造出「公路」这个取值，而它与 `road` 在界面上长得一模一样",
        str(f.get("mode")) == mode_raw and str(f.get("from")) == frm and str(f.get("to")) == to1,
        f"回填 seq={f.get('seq')!r} mode={f.get('mode')!r} from={f.get('from')!r} to={f.get('to')!r}",
    )

    # ── ⑥ 空改动的**本地闸门**：一个字段都没改 ⇒ 页内说清，且**不发请求** ──
    legs_before_noop = len((w.c.page_data().get("plan") or {}).get("legs") or [])
    w.c.tap('[data-act-leg-submit="1"]')
    d = w.wait_data(lambda x: "完全相同" in str(x.get("legHint") or ""), tries=8, gap=0.4)
    hint = str(d.get("legHint") or "")
    legs_after_noop = len((d.get("plan") or {}).get("legs") or [])
    w.rep.rec(
        "㊼ ⑥ 「一个字段都没改」在**本地**就被拦下：页内提示说清原因，"
        "且**没有**把注定 400 的请求发出去（段数未变、页面未刷新）",
        "完全相同" in hint and legs_after_noop == legs_before_noop,
        f"legHint={hint!r} 段数 {legs_before_noop} → {legs_after_noop}",
    )

    # ── ⑦ 真改一段：点快捷项「内河」+ 换终点 ⇒ 段行变、历史留两版 ─────────
    w.c.scroll_into('[data-act-leg-mode="water"]')
    w.c.tap('[data-act-leg-mode="water"]')
    d = w.wait_data(
        lambda x: str((x.get("legForm") or {}).get("mode")) == "water", tries=10, gap=0.3
    )
    w.rep.rec(
        "㊼ ⑦ 快捷项把方式改成已登记取值（`water` === 选中项的 key：两侧都是字符串）",
        str((d.get("legForm") or {}).get("mode")) == "water",
        f"legForm.mode={((d.get('legForm') or {}).get('mode'))!r}",
    )
    # ⚠️ 终点是**有值**的：`input_text` 会往后面追加 ⇒ 先用 `set_data` 清空。
    #    清空这一步**不验输入通道**，所以随后仍然**用输入通道**打新值 ——
    #    "输入真的接上了"这条判据不受影响（上面 ③ 已经在空框上验过一次）。
    w.c.set_data({"legForm.to": ""})
    w.c.input_text('input[data-df="leg-to"]', to2)
    w.c.scroll_into('[data-act-leg-submit="1"]')
    w.c.tap('[data-act-leg-submit="1"]')
    # 提交成功后 `load()` 会把表单收起 ⇒ 以它为"写完成 + 整页刷新"的信号
    d = w.wait_data(
        lambda x: (
            x.get("legOpen") is False
            and str(((x.get("plan") or {}).get("legs") or [{}])[0].get("modeText") or "") != ""
        ),
        tries=40,
        gap=0.5,
    )
    row2 = w.c.outer_wxml(f'[data-plan-leg="{seq_next}"]')
    hist = api_get(f"/entrust/assignments/{aid}/legs/{leg_id}/revisions", tok) or []
    kinds = [str((r or {}).get("change_kind") or "") for r in hist]
    w.rep.rec(
        "㊼ ⑧ 经**界面**改段：段行的方式标签变成服务端的「内河」、终点也换了；"
        "服务端历史里**两版都在**（改段留版本，不是覆盖）",
        "内河" in row2
        and to2 in row2
        and len(hist) >= 2
        and kinds[0] == "created"
        and kinds[-1] == "updated",
        f"行文本={row2[:160]!r} 历史={len(hist)}版 kinds={kinds}",
    )

    # ── ⑨ 版本历史**渲染出来**，且第 1 版仍是**当时**的取值 ────────────────
    w.c.scroll_into(f'[data-act-leg-hist="{leg_id}"]')
    w.c.tap(f'[data-act-leg-hist="{leg_id}"]')
    d = w.wait_data(lambda x: x.get("legHistory") is not None, tries=30, gap=0.4)
    n_rev = w.c.count(".plan-rev")
    first_rev = w.c.outer_wxml(".plan-rev")
    hist_ui = (d.get("legHistory") or {}).get("items") or []
    w.rep.rec(
        "㊼ ⑨ 版本历史在渲染树里（`.plan-rev` 行数 ≥ 2）且**第 1 版保留当时的方式 `air`**"
        "——历史被当前值覆盖时行数**照样**是 2，所以判据必须读第 1 版的内容",
        n_rev >= 2 and mode_raw in first_rev and len(hist_ui) == len(hist),
        f"版本行={n_rev} 页面版数={len(hist_ui)} 服务端={len(hist)} 第1版文本={first_rev[:160]!r}",
    )

    w.shot("㊼-航段命令写侧")
    errs = w.new_errors(err_base)
    w.rep.rec(
        "㊼ ⑩ 本章运行期**无新增 console 报错**", not errs.strip(), errs.strip()[:300] or "无"
    )


def sec_48(w: Walker) -> None:
    """㊽ **货主本人**经界面建段（第 4 步判据的另一半）。

    为什么单独一章
    --------------
    HO 2026-09-17 对"谁能建段"的裁定是**任意参与方**（该委托的货主本人，
    或该委托所属组织的 active 成员）⇒ 不新增权限常量、不设角色门槛。
    ㊼ 章的身份是 `seed-owner`（经理），只证明了"组织成员能建"那一半；
    "货主本人也能建"此前只有 **e2e** 的证据（页面 JS + 真 HTTP）。

    ⛔ 本章要的是**设备侧**证据：用货主的 token 登录小程序、在真机渲染树上
    **真的点**出一段来。e2e 那条证据借不来 —— 它跑的是页面 JS，
    不是渲染树与手势（这正是"静态门禁 + e2e 全绿、真机点不出来"那一类缺陷的藏身处）。

    覆盖
    ----
    ① 写入口（`[data-act-leg-open="1"]`）在**货主**看到的渲染树里，段行数 = 服务端段数；
    ② 表单打开，顺序号预填「最大序号 + 1」；
    ③ `input_text` 真触发 `bindinput` ⇒ 四个字段都接上；
    ④ 提交后**页面段数 = 服务端段数**，新段那一行在渲染树里（带值锚点）；
    ⑤ 服务端随后读到的段集合里**真的有**这一段（页面与服务端同源）；
    ⑥ 本章运行期无新增 console 报错。

    ⚠️ 诚实边界
    * 身份 `seed-shipper` —— canonical 委托「DEMO-1 canonical · 钢材 800 吨 南宁 → 贵港」
      的货主正是它（`seed_entrust_canonical.py` 的 `SHIPPER_CODE`）。
    * 本章**会写库**（1 段航段 + 1 条 created 版本）。
    * 自足：只依赖 `seed_entrust_canonical.py` 铺的那张委托 ⇒ 可单跑 `--section 48`。
    """
    print("\n-- ㊽ 货主本人经界面建段（第 4 步 · 参与方判据的另一半）--", flush=True)

    err_base = w.c.errors()
    code_shipper = "seed-shipper"
    demo_title = "DEMO-1 canonical · 钢材 800 吨 南宁 → 贵港"

    tok = (api_login(code_shipper) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("㊽ 全部断言", "拿不到 seed-shipper 的 token（后端未起或种子未铺）")
        return
    rows = (api_get("/entrust/assignments?view=owner&size=50", tok) or {}).get("items") or []
    hit = [r for r in rows if str((r or {}).get("title") or "") == demo_title]
    hit.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
    if not hit and not WALK_ANCHOR:
        w.rep.not_run(
            "㊽ 全部断言",
            f"seed-shipper 名下找不到「{demo_title}」（先跑 python scripts/seed_entrust_canonical.py）",
        )
        return
    aid = prefer_anchor(str((hit[0] or {}).get("assignment_id") or ""))

    plan0 = api_get(f"/entrust/assignments/{aid}/plan", tok) or {}
    legs0 = plan0.get("legs") or []
    seq_next = max([int((x or {}).get("seq") or 0) for x in legs0] + [0]) + 1
    mode_raw = "rail"  # 已登记取值；㊼ 用的是未登记的 air，本章换一个，避免两章互相依赖
    frm = f"㊽起点{seq_next}"
    to1 = f"㊽终点{seq_next}"

    # ⚠️ 必须走 `open_workbench`（＝ `login_as` **＋ 点身份卡进工作台**），不能只用 `login_as`：
    #    `login_as` 只写 `dev_login_code`、清 token、reLaunch 首页，**不点身份卡**
    #    ⇒ 那一刻还没有 token，直接深链详情页会停在 `expired` / `denied` 态，
    #    而症状恰好是「段行 0、写入口 0」—— 读起来像「货主看不到写入口」这条**产品结论**，
    #    实际是本章前置没做完。
    #    （2026-09-18 首跑实测：`加一段=0 段行=0 服务端段数=3`。
    #     对照 ㊼ 章用的是 `open_workbench`，所以它没这个问题。）
    if not w.open_workbench(code_shipper, tag="㊽"):
        w.rep.not_run("㊽ 全部断言", "未能以 seed-shipper 进入货主工作台")
        return
    w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL)
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    d = w.wait_data(lambda x: x.get("plan") is not None, tries=60, gap=0.5)

    # ① 写入口与段行都在**货主**的渲染树里
    n_open = w.c.count('[data-act-leg-open="1"]')
    n_leg0 = w.c.count(".plan-leg")
    w.rep.rec(
        "㊽ ① 货主看到的详情页里**也有**建段入口（判据＝参与方，不是角色）——段行数与服务端一致",
        n_open == 1 and n_leg0 == len(legs0),
        f"加一段={n_open} 段行={n_leg0} 服务端段数={len(legs0)}",
    )

    # ② 表单渲染 + 预填「最大序号 + 1」
    w.c.scroll_into('[data-act-leg-open="1"]')
    if not w.c.tap('[data-act-leg-open="1"]'):
        w.rep.rec("㊽ ② 点「加一段」失败（按钮在渲染树里但点不动）", False, "")
    d = w.wait_data(lambda x: x.get("legOpen") is True, tries=20, gap=0.3)
    form_seq = str(((d.get("legForm") or {}).get("seq")) or "")
    w.rep.rec(
        "㊽ ② 建段表单渲染出来，顺序号预填「最大序号 + 1」",
        w.c.count('input[data-df="leg-seq"]') == 1 and form_seq == str(seq_next),
        f"预填={form_seq!r} 期望={seq_next} 服务端序号={[x.get('seq') for x in legs0]}",
    )

    # ③ 输入真的接上（四个字段）
    typed = (
        w.c.input_text('input[data-df="leg-mode"]', mode_raw)
        and w.c.input_text('input[data-df="leg-from"]', frm)
        and w.c.input_text('input[data-df="leg-to"]', to1)
        and w.c.input_text('input[data-df="leg-note"]', "㊽ 章货主真机建段")
    )
    w.rep.rec(
        "㊽ ③ 货主侧的 `input_text` 真的接上了 `bindinput`（`data-df` 映射表认领到字段）",
        typed,
        "四个输入框都键入成功" if typed else "有输入框没接上（`data-df` 漏映射时正是这个症状）",
    )

    # ④ 提交 ⇒ 页面段数 = 服务端段数，新段在渲染树里
    w.c.scroll_into('[data-act-leg-submit="1"]')
    w.c.tap('[data-act-leg-submit="1"]')
    d = w.wait_data(
        lambda x: len((x.get("plan") or {}).get("legs") or []) == len(legs0) + 1, tries=40, gap=0.5
    )
    ui_legs = (d.get("plan") or {}).get("legs") or []
    srv_legs = (api_get(f"/entrust/assignments/{aid}/plan", tok) or {}).get("legs") or []
    n_anchor = w.c.count(f'[data-plan-leg="{seq_next}"]')
    row_wxml = w.c.outer_wxml(f'[data-plan-leg="{seq_next}"]')
    w.rep.rec(
        "㊽ ④ 货主经界面建段成功：页面段数 = 服务端段数，新段那一行在渲染树里",
        len(ui_legs) == len(srv_legs) and n_anchor == 1 and frm in row_wxml and "铁路" in row_wxml,
        f"页面段={len(ui_legs)} 服务端段={len(srv_legs)} 值锚点={n_anchor} 行文本={row_wxml[:160]!r}",
    )

    # ⑤ 服务端随后读到的段集合里真的有这一段（页面 ⇄ 服务端同源）
    seqs = [str((x or {}).get("seq") or "") for x in srv_legs]
    w.rep.rec(
        "㊽ ⑤ 服务端读回来的段集合里**真的有**这一段（不是前端往 `plan.legs` 里塞了一行）",
        str(seq_next) in seqs,
        f"服务端序号={seqs} 期望含={seq_next}",
    )

    w.shot("㊽-货主建段")
    errs = w.new_errors(err_base)
    w.rep.rec(
        "㊽ ⑥ 本章运行期**无新增 console 报错**", not errs.strip(), errs.strip()[:300] or "无"
    )


def sec_49(w: Walker) -> None:
    """㊾ 合同派生 + 签署证据的**界面取证**（§10.1 第 7 步 / D1-08）。

    本章要证明的三件事（各对应一个"看起来一样但其实不同"的陷阱）
    ------------------------------------------------------------
    一、**合同是派生出来的，不是手编的**：界面上那个按钮只发一个空请求体
        （`note` 之外没有任何业务入参）⇒ 合同的金额/范围/有效期全部来自
        **客户接受的那一版**报价。界面若允许填金额，第 7 步就退化成"手打一份合同"。
    二、**签署证据绑的是版本**：每一行都必须显示"第 N 版"。
        只写"有证据"等于把"客户签的是哪一版"这个问题留在库里没人回答。
    三、**标注是常驻的**：`labeled_sample` 那句声明必须出现在页面上 ——
        合同 §3.2 / D1-08 要求证据与状态**不得**等同于实时电子签，
        而"页面上没写"就会被读成"签过了"。

    覆盖
    ----
    ① 前置：存在一条**客户已接受**的对客报价发布（由 `seed_entrust_contract_flow.py` 铺）；
    ② 点「生成合同核对稿」⇒ 页面拿到派生结果，且与服务端 `GET …/contract` 同源；
    ③ 逐字段来源表渲染出来，行数 = 服务端来源行数（D1-08 的 inspection 面）；
    ④ 缺失项如实列出（"这份合同少写了什么"必须看得见）；
    ⑤ 经界面记一条签署证据 ⇒ 页面出现一行"第 1 版 · 样件扫描件 · 样件标注"，
       且与服务端 `GET /contracts/{id}/signature-evidence` 同源；
    ⑥ 同形态再记一次 ⇒ 页内说清"已经记过"（409 的语义，不是静默失败）；
    ⑦ 常驻声明在页面上（"不构成实时电子签署"）；
    ⑧ 本章运行期无新增 console 报错。

    ⚠️ 诚实边界
    * 身份 `seed-owner`（经理）：派生与证据两条通道**都只有经理侧**
      （客户看合同走已有发布通路，不为它新开客户面 —— 与派生端点同一纪律）。
    * 本章**会写库**（派生一份合同 + 记一条证据）。同一条已接受事实只能派生一次
      ⇒ 重跑时若已派生，② 走"读已有那份"的分支（不重复派生，也不报红）。
    * 依赖 `seed_entrust_contract_flow.py`：没跑它 ⇒ 整章 `NOT_RUN`（不假绿）。
    """
    print("\n-- ㊾ 合同派生与签署证据的界面取证（第 7 步）--", flush=True)

    err_base = w.c.errors()
    code_mgr = "seed-owner"
    org_name = "演示经营主体·工作台"
    demo_title = "DEMO-1 canonical · 钢材 800 吨 南宁 → 贵港"

    tok = (api_login(code_mgr) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("㊾ 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return
    org_id = ""
    for r in (api_get("/entrust/my-orgs", tok) or {}).get("items") or []:
        if str((r or {}).get("name") or "") == org_name:
            org_id = str((r or {}).get("org_id") or "")
    if not org_id:
        w.rep.not_run("㊾ 全部断言", f"seed-owner 的组织里没有「{org_name}」")
        return
    rows = (api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", tok) or {}).get(
        "items"
    ) or []
    hit = [r for r in rows if str((r or {}).get("title") or "") == demo_title]
    hit.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
    if not hit and not WALK_ANCHOR:
        w.rep.not_run(
            "㊾ 全部断言", f"该组织下找不到「{demo_title}」（先跑 seed_entrust_canonical.py）"
        )
        return
    aid = prefer_anchor(str((hit[0] or {}).get("assignment_id") or ""))

    # ① 前置：一条**已接受**的对客报价发布（种子铺；没有就整章 NOT_RUN）
    eid = ""
    ctx = api_get(f"/entrust/assignments/{aid}/session-context", tok) or {}
    if ctx.get("entrustment_id"):
        eid = str(ctx.get("entrustment_id"))
    if not eid:
        w.rep.not_run("㊾ 全部断言", f"委托 #{aid} 定位不到唯一授权（`session-context` 为空）")
        return
    rels = (api_get(f"/entrust/entrustments/{eid}/offer-releases", tok) or {}).get("items") or []
    # ⚠️ 判据读的是发布行**顶层的** `artifact_type`：它是 `OfferReleaseOut` 显式声明的
    #    字段（2026-09-18 起；此前只藏在 `customer_snapshot` 里，前端因此只能瞎猜）。
    #    ⭐ 有意**不去**"先查成果清单再按 `artifact_id` 对上"那种绕法：那样即使
    #    接口把字段弄丢了，本章照样通过 —— 于是"页面坏了、走查绿着"。
    #    走查章必须验**页面依赖的那份契约**，接口一改，两边一起红才是对的。
    #    （首跑实测：读了一个不存在的顶层键 ⇒ `accepted` 恒为空 ⇒ 整章 `NOT_RUN`，
    #      而夹具其实铺好了。读错键名不会报错，只会静默为 0。）
    accepted = [
        r
        for r in rels
        if str(((r or {}).get("response") or {}).get("decision") or "") == "accept"
        and str((r or {}).get("artifact_type") or "") == "customer_quote"
    ]
    accepted.sort(key=lambda r: int((r or {}).get("release_id") or 0), reverse=True)
    if not accepted:
        w.rep.not_run(
            "㊾ 全部断言",
            "没有「客户已接受的对客报价发布」—— 先跑 "
            "`python scripts/seed_entrust_contract_flow.py`（第 7 步夹具）",
        )
        return
    rid = str((accepted[0] or {}).get("release_id") or "")

    if not w.open_workbench(code_mgr, tag="㊾"):
        w.rep.not_run("㊾ 全部断言", "未能以 seed-owner 进入经理工作台")
        return
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.set_storage(ORG_STORAGE_KEY, org_id)
    w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL)
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    d = w.wait_data(
        lambda x: x.get("contract") is not None or x.get("contractHint"), tries=60, gap=0.5
    )

    already = api_get(f"/entrust/offer-releases/{rid}/contract", tok) or {}
    cart = str(already.get("contract_artifact_id") or "")

    # ② 派生（已派生过 ⇒ 直接读那份，不重复派生也不报红）
    if cart:
        w.rep.rec(
            "㊾ ② 该已接受报价**已经派生过**合同 ⇒ 本章读回已有那份"
            "（一份已接受事实只派生一份，重跑不该重复派生也不该报红）",
            str((d.get("contract") or {}).get("contractArtifactId") or "") == cart
            or w.c.count(".contract-card") >= 1,
            f"服务端合同=#{cart} 页面={(d.get('contract') or {}).get('contractArtifactId')!r}",
        )
    else:
        n_btn = w.c.count('[data-act-contract-derive="1"]')
        w.rep.rec(
            "㊾ ② 未派生时页面有「生成合同核对稿」入口，且它**不带**任何业务入参"
            "（合同内容只能来自已接受事实，界面不该能填金额）",
            n_btn == 1,
            f"入口数={n_btn}",
        )
        w.c.scroll_into('[data-act-contract-derive="1"]')
        w.c.tap('[data-act-contract-derive="1"]')
        d = w.wait_data(lambda x: x.get("contract") is not None, tries=40, gap=0.5)
        srv = api_get(f"/entrust/offer-releases/{rid}/contract", tok) or {}
        cart = str(srv.get("contract_artifact_id") or "")
        ui_cart = str((d.get("contract") or {}).get("contractArtifactId") or "")
        w.rep.rec(
            "㊾ ③ 经**界面**派生成功：页面拿到的合同与服务端**同一个成果同一版本**"
            "（报价版本与合同版本各自精确）",
            bool(cart) and ui_cart == cart,
            f"页面={ui_cart!r} 服务端={cart!r} 报价版本={srv.get('quote_revision_no')!r}",
        )

    if not cart:
        w.rep.not_run("㊾ ④ ～ ⑧", "派生没成功（后面没有可绑证据的合同）")
        errs = w.new_errors(err_base)
        w.rep.rec(
            "㊾ ⑧ 本章运行期**无新增 console 报错**", not errs.strip(), errs.strip()[:300] or "无"
        )
        return

    # ③ 逐字段来源表：行数 = 服务端来源行数
    src_btn = w.c.count('[data-act-contract-sources="1"]')
    if src_btn:
        w.c.scroll_into('[data-act-contract-sources="1"]')
        w.c.tap('[data-act-contract-sources="1"]')
        d = w.wait_data(lambda x: x.get("contractSourcesOpen") is True, tries=20, gap=0.3)
    n_src = w.c.count(".contract-src")
    srv_src = (api_get(f"/entrust/offer-releases/{rid}/contract", tok) or {}).get(
        "field_sources"
    ) or []
    w.rep.rec(
        "㊾ ④ 逐字段来源表渲染出来，行数 = 服务端来源行数"
        "（D1-08 要的是「能检查它怎么来的」，不是「有一份合同」）",
        n_src == len(srv_src) and len(srv_src) > 0,
        f"页面行数={n_src} 服务端={len(srv_src)}",
    )

    # ④ 缺失项如实列出（有缺失时必须看得见）
    absent = (api_get(f"/entrust/offer-releases/{rid}/contract", tok) or {}).get(
        "absent_quote_fields"
    ) or []
    card_wxml = w.c.outer_wxml(".contract-card")
    w.rep.rec(
        '㊾ ⑤ 「这份合同少写了什么」如实列出（有缺失项时页面上必须写得出"未提供"）'
        "—— 列出来而不是编默认值，这是派生与编造的分界",
        (not absent) or ("未提供" in card_wxml),
        f"服务端缺失项={absent} 卡片文本={card_wxml[:120]!r}",
    )

    # ⑤ 经界面记一条签署证据
    n_sig_open = w.c.count('[data-act-sig-open="1"]')
    w.rep.rec("㊾ ⑥ 合同卡上有「记一条签署证据」入口", n_sig_open == 1, f"入口数={n_sig_open}")
    w.c.scroll_into('[data-act-sig-open="1"]')
    w.c.tap('[data-act-sig-open="1"]')
    d = w.wait_data(lambda x: x.get("sigOpen") is True, tries=20, gap=0.3)
    w.c.scroll_into('[data-act-sig-kind="sample_scan"]')
    w.c.tap('[data-act-sig-kind="sample_scan"]')
    d = w.wait_data(
        lambda x: str((x.get("sigForm") or {}).get("kind")) == "sample_scan", tries=10, gap=0.3
    )
    typed = w.c.input_text('input[data-df="sig-note"]', "㊾ 章真机取证：客户签回的样件扫描件")
    w.rep.rec(
        '㊾ ⑦ 证据表单的输入接上了 `bindinput`（`data-df="sig-note"` 被认领）',
        typed and str((d.get("sigForm") or {}).get("kind")) == "sample_scan",
        f"kind={(d.get('sigForm') or {}).get('kind')!r} 键入={typed}",
    )
    w.c.scroll_into('[data-act-sig-submit="1"]')
    w.c.tap('[data-act-sig-submit="1"]')
    d = w.wait_data(
        lambda x: ((x.get("sig") or {}).get("items") or []) and x.get("sigOpen") is False,
        tries=40,
        gap=0.5,
    )
    items = (d.get("sig") or {}).get("items") or []
    srv_sig = api_get(f"/entrust/contracts/{cart}/signature-evidence", tok) or {}
    n_row = w.c.count(".sig-row")
    first_row = w.c.outer_wxml(".sig-row")
    sig_srv = srv_sig.get("items") or []
    sig_ok = (
        len(items) == len(sig_srv)
        and n_row == len(items)
        and "第 1 版" in first_row
        and "样件扫描件" in first_row
        and "样件标注" in first_row
    )
    # ⭐ O-8 归因（2026-09-18）：本条在**组合配方**（43,44,48,49）下曾 FAIL 而单跑全绿。
    #    纪律是「沿真实调用链取页面 view 与 console 原文」——
    #    ⛔ 不靠加等待/重跑刷绿灯，也不把"疑似 reload 失败"当成结论写出去。
    #    诊断**只在失败时**拼进 note（成功时零噪音，且**不增加断言数**）。
    diag = ""
    if not sig_ok:
        try:
            pdump = w.c.page_data() or {}
            contract = pdump.get("contract")
            hints = {
                k: pdump.get(k)
                for k in ("contractHint", "sigHint", "sigOpen", "loadError", "err")
                if k in pdump
            }
            sub = sorted(contract.keys()) if isinstance(contract, dict) else repr(contract)
            diag = (
                f"｜诊断 path={w.c.current_path()}"
                f"｜page_data 顶层键={sorted(pdump.keys())}"
                f"｜contract 子树={sub}"
                f"｜hint 类键={hints}"
                f"｜console 尾部={(w.c.errors() or '')[-240:]!r}"
                f"｜.contract-card={(w.c.outer_wxml('.contract-card') or '')[:240]!r}"
            )
        except Exception as exc:  # 诊断本身失败不能让这一格变成"脚本坏了"
            diag = f"｜诊断采集失败：{exc!r}"
    w.rep.rec(
        "㊾ ⑧ 经**界面**记一条签署证据：页面出现一行，且**绑的是版本**"
        "（「第 1 版」）+ 形态 + 样件标注；与服务端同源",
        sig_ok,
        f"页面={len(items)} 服务端={len(sig_srv)} 行={n_row} 首行={first_row[:160]!r}" + diag,
    )

    # ⑥ 同形态再记一次 ⇒ 页内说清"已经记过"（409 的语义，不是静默失败）
    #
    # ⚠️ **这一格必须能区分两种情形**（2026-09-18 O-8 定向验证的结论）：
    #   (a) 提交真的发生了、但页内没给提示； (b) 提交**根本没发生**（tap 落空）。
    #   两者都表现为 `sigHint=''` ＋ 条数不变 —— 只断言 hint 的话，判据分不出
    #   "产品没提示"与"工具没点到"，而这两种结论指向完全不同的下一步。
    #   ⇒ 先确认**提交键在渲染树上**（`count > 0`，tap 才有落点），点完若仍无提示
    #     就**补点一次**（与 `login_as` 补发 reLaunch、`enter_role` 先等卡片是同一手法），
    #     并把"点击时到底有没有落点"记进读数 —— 加固定等待只会把 (b) 也等成绿灯。
    n_before = len(items)
    tap_landed = False
    probe = ""
    d: dict = {}
    open_tries = 0
    opened = False
    if w.c.count('[data-act-sig-open="1"]'):
        # ⚠️ 这个锚点是**开关**，不是"展开键"：`detail.js` 的 `onToggleSig` 是
        #    `sigOpen: !this.data.sigOpen`，同一个锚点在展开时显示「取消」。
        #    ⇒ **盲点一下在已经展开时会把它收起来**，接下来 `[data-act-sig-kind]`
        #    与 `[data-act-sig-submit]` 全都不在树上，读数表现为
        #    `sigOpen=False / 提交键没落点` —— 看起来像"页面没给提示"。
        #    正确做法是**先读状态再点**（人也是先看标签），并把"点了几次才展开"记进读数：
        #    需要多于 1 次 ⇒ 说明点击有落空，那是走查侧的问题；一次都展开不了 ⇒ 才查页面。
        for _ in range(3):
            if (w.c.page_data() or {}).get("sigOpen") is True:
                opened = True
                break
            open_tries += 1
            w.c.scroll_into('[data-act-sig-open="1"]')
            w.c.tap('[data-act-sig-open="1"]')
            for _ in range(16):
                if (w.c.page_data() or {}).get("sigOpen") is True:
                    opened = True
                    break
                time.sleep(0.3)
            if opened:
                break
        if opened:
            w.c.scroll_into('[data-act-sig-kind="sample_scan"]')
            w.c.tap('[data-act-sig-kind="sample_scan"]')
            for _attempt in range(2):
                has_btn = w.c.count('[data-act-sig-submit="1"]') > 0
                if not has_btn:
                    time.sleep(0.6)
                    continue
                tap_landed = True
                w.c.scroll_into('[data-act-sig-submit="1"]')
                w.c.tap('[data-act-sig-submit="1"]')
                d = w.wait_data(
                    lambda x: "已经记过" in str(x.get("sigHint") or ""), tries=20, gap=0.4
                )
                if "已经记过" in str(d.get("sigHint") or ""):
                    break
    if not tap_landed:
        # ⚠️ 找不到提交键时**把"为什么"直接打出来**，不要只报"没落点"：
        #    提交键的渲染条件是 `canDeriveContract && contract && sigOpen`
        #    （`detail.wxml`：`.contract-card` → `wx:if="{{contract}}"` → `wx:if="{{sigOpen}}"`），
        #    这几个值里任何一个不成立都表现为"键不在树上"，而它们指向完全不同的排查方向。
        #    再附一段卡片原文，连"模板改了属性名"这种也一并排除。
        snap = w.c.page_data()
        sig = snap.get("sig") or {}
        probe = (
            f"｜探针 sigOpen={snap.get('sigOpen')} 表单展开={opened}（点了 {open_tries} 次）"
            f" contract={bool(snap.get('contract'))} "
            f"canDeriveContract={snap.get('canDeriveContract')} sig.hasItems={sig.get('hasItems')}"
            f"；卡片原文={w.c.outer_wxml('.contract-card')[:220]!r}"
        )
    if not d:
        # ⚠️ 一次都没点到 ⇒ `d` 还是空字典，直接 `len(d["sig"]["items"])` 会得到 **0**，
        #    读成"条数从 1 掉到 0"（看起来像"证据被删了"）。这里补一次真实读数，
        #    让"条数"这一列始终是**页面上的实际条数**，而不是"我们有没有取到数"。
        d = w.c.page_data()
    n_after = len((d.get("sig") or {}).get("items") or [])
    w.rep.rec(
        "㊾ ⑨ 同一版同一形态**再记一次** ⇒ 页内说清「已经记过」（服务端 409 的语义，"
        "不是静默失败，也没有记成两条互相打架的证据）",
        "已经记过" in str(d.get("sigHint") or "") and n_after == n_before,
        f"sigHint={(d.get('sigHint') or '')!r} 条数 {n_before} → {n_after} "
        f"表单展开={opened}（点了 {open_tries} 次）提交键有落点={tap_landed}"
        + (
            ""
            if tap_landed
            else "（**提交键没进渲染树 ⇒ 先看下面的探针：是「表单没展开」还是"
            "「展开键点了不生效」，两者修法不同；⛔ 不是「页面没提示」**）"
        )
        + probe,
    )

    # ⑦ 常驻声明在页面上
    w.rep.rec(
        "㊾ ⑩ 常驻声明真的渲染出来（「不构成实时电子签署」）—— 合同 §3.2 / D1-08 "
        "要求证据与状态不得等同于实时电子签；页面上没写就会被读成「签过了」",
        "不构成实时电子签署" in w.c.outer_wxml(".contract-card"),
        "卡片文本=" + w.c.outer_wxml(".contract-card")[:200] + "",
    )

    w.shot("㊾-合同派生与签署证据")
    errs = w.new_errors(err_base)
    w.rep.rec(
        "㊾ ⑪ 本章运行期**无新增 console 报错**", not errs.strip(), errs.strip()[:300] or "无"
    )


def sec_50(w: Walker) -> None:
    """㊿ 委托货量变更的**设备侧**取证（S6-1 / D1-09 / §10.1 第 8 步）。

    合同 §10.1 第 8 步原文是 `Apply the 800→950 change`；D1-09 的判据是
    「同一张委托上看到 `800 → 审批 → 950 → 原运力（900 吨候选）不适用 → 复核与重新确认`」。
    **预置另一张 950 吨的委托不算数** —— 那只能说明系统里有 950，说明不了
    "这张单被改过"，也说明不了审批、原子性、留痕与复核传播。

    覆盖
    ----
    ① 前置：canonical 夹具的货量是 800.000 吨（夹具声明的值，不是断言产品行为）；
    ② 变更前：那条 900 吨候选的运力确认**仍然成立**（`recheck.still_valid = true`）；
    ③ 经**界面**补登变更类别「货物数量与品类」，再经界面进入复核；
    ④ 经**界面**批准：三只输入（新值 / 单位 / 依据）真的写进页面 data，并落库；
    ⑤ 经**界面**应用 ⇒ 货量落库为 950、历史留一行 `800.000 吨 → 950.000 吨`；
    ⑥ 变更后：同一条确认 `still_valid = false`，且**只有** `capacity` 不过；
    ⑦ 复核传播在页面上（含「船型、运力及货物适配」——复核与重新确认的落点）；
    ⑧ 本章运行期零新增 console 报错。

    ⚠️ 诚实边界
    * 身份 `seed-owner`（经理）：货量变更的读写**两侧都只有经理侧**
      （`basis` 是经理写的变更依据，货量历史端点不给货主放行）。
    * **写侧**（登记变更请求 + 登记受影响项 + 运力确认）由接口铺 —— 它们是**前置**，
      不是本章要取的证。本章要证的是「**经界面**审批与应用」那两步
      （登记案件本身属 ⑯ 章与 `case-create` 页的取景范围）。
    * 依赖 `seed_entrust_canonical.py`（唯一带 900 吨候选与 800 吨货量的夹具）。
      没跑它 ⇒ 整章 `NOT_RUN`（不假绿）。
    * 重跑：本章会改库。货量已是 950 时，⑤ 的**精确**那对值记 `NOT_RUN`
      （要复现 `800 → 950` 请先重建库），其余断言按"当前值 → 当前值 + 100"照跑。
    """
    print("\n-- ㊿ 委托货量变更的设备侧取证（第 8 步）--", flush=True)

    err_base = w.c.errors()
    code_mgr = "seed-owner"
    org_name = "演示经营主体·工作台"
    demo_title = "DEMO-1 canonical · 钢材 800 吨 南宁 → 贵港"

    tok = (api_login(code_mgr) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("㊿ 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return
    org_id = ""
    for r in (api_get("/entrust/my-orgs", tok) or {}).get("items") or []:
        if str((r or {}).get("name") or "") == org_name:
            org_id = str((r or {}).get("org_id") or "")
    if not org_id:
        w.rep.not_run("㊿ 全部断言", f"seed-owner 的组织里没有「{org_name}」")
        return
    rows = (api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", tok) or {}).get(
        "items"
    ) or []
    hit = [r for r in rows if str((r or {}).get("title") or "") == demo_title]
    hit.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
    if not hit and not WALK_ANCHOR:
        w.rep.not_run(
            "㊿ 全部断言", f"该组织下找不到「{demo_title}」（先跑 seed_entrust_canonical.py）"
        )
        return
    aid = prefer_anchor(str((hit[0] or {}).get("assignment_id") or ""))

    ctx = api_get(f"/entrust/assignments/{aid}/session-context", tok) or {}
    eid = str(ctx.get("entrustment_id") or "")
    if not eid:
        w.rep.not_run("㊿ 全部断言", f"委托 #{aid} 定位不到唯一授权")
        return

    # ── ① 前置：canonical 的货量 ────────────────────────────────────────────
    det = api_get(f"/entrust/assignments/{aid}", tok) or {}
    cur_raw = det.get("quantity")
    cur_unit = str(det.get("quantity_unit") or "吨")
    if cur_raw is None:
        w.rep.not_run("㊿ 全部断言", f"委托 #{aid} 的货量是空的（canonical 夹具声明为 800）")
        return
    cur = Decimal(str(cur_raw))
    fresh = cur == Decimal("800")
    w.rep.rec(
        "㊿ 前置 · canonical 夹具的货量就是 800.000 吨（D1-09 的起点）"
        + (
            ""
            if fresh
            else f" —— ⚠️ 实为 {cur}，本章按「当前值 + 100」照跑，要复现 800→950 那对精确值需重建库"
        ),
        True,
        f"quantity={cur_raw} unit={cur_unit}（夹具声明 800.000 吨：{'一致' if fresh else '已被改过'}）",
    )

    # ── 前置 B：900 吨候选的**运力确认**（变更后要判它不再适用）──────────
    # ⚠️ 这两条端点返回的是**列表**（），不是分页对象 ——
    #    按  读会抛 AttributeError（本轮实测），而异常会被章节兜底
    #    记成「执行异常」，看起来像脚本坏了而不是读错形状。
    cands = api_get(f"/entrust/assignments/{aid}/capacity-candidates", tok) or []
    c900 = [c for c in cands if _same_dec((c or {}).get("capacity_tonnes"), "900.000")]
    if not c900:
        w.rep.not_run(
            "㊿ 全部断言",
            "该委托下没有 900 吨候选运力 —— canonical 夹具未铺（先跑 seed_entrust_canonical.py）",
        )
        return
    cand_id = int((c900[0] or {}).get("candidate_id") or 0)
    confs = api_get(f"/entrust/assignments/{aid}/capacity-confirmations", tok) or []
    conf = [c for c in confs if int((c or {}).get("candidate_id") or 0) == cand_id]
    if not conf:
        # **前置**用接口铺一次：本章要证的是"经界面审批与应用"，不是"经界面确认运力"
        # （后者是 ㊺ 章的范围）。⚠️ 它**不**计入本章的通过数 —— 只是让 ⑥ 有对象可判。
        st, body = api_post(
            f"/entrust/assignments/{aid}/capacity-confirmations",
            tok,
            {"candidate_id": cand_id, "agreed_scope": "整船包运（㊿ 前置）"},
            _idem("walk-50-conf"),
        )
        if st != 200:
            w.rep.not_run("㊿ 全部断言", f"无法确认 900 吨候选（HTTP {st}）：{body}")
            return
        conf = [body]
    conf_id = str((conf[0] or {}).get("confirmation_id") or "")

    # ── ② 变更前：这条确认还成立 ───────────────────────────────────────────
    pre = api_get(f"/entrust/capacity-confirmations/{conf_id}/recheck", tok) or {}
    w.rep.rec(
        "㊿ ② 变更前 · 900 吨候选的运力确认**成立**（基线：确认在 800 吨需求下判过）",
        pre.get("still_valid") is True,
        f"still_valid={pre.get('still_valid')!r} 不过的规则="
        f"{[r.get('rule_code') for r in (pre.get('rule_checks') or []) if r.get('outcome') == 'fail']}",
    )

    # ── 前置 C：登记变更请求 + 受影响项＝本委托（写侧前置）──────────────
    arts = (api_get(f"/entrust/assignments/{aid}/artifacts?size=50", tok) or {}).get("items") or []
    basis_art = [a for a in arts if (a or {}).get("current_revision_id")]
    if not basis_art:
        w.rep.not_run("㊿ 全部断言", f"委托 #{aid} 上没有带生效版本的成果（批准必须指向精确版本）")
        return
    basis_rev = int(basis_art[0]["current_revision_id"])

    st, case = api_post(
        f"/entrust/assignments/{aid}/exceptions",
        tok,
        {
            "kind": "change_request",
            "title": "走查·货量由 800 吨调整为 950 吨",
            "severity": "medium",
            "impact_kind": "review-required",
            # ⚠️ **有意不在这里给类别**：类别由界面在批准时补登（③），
            #    那正是"界面上有没有这个入口"的证据。
            "links": [{"target_kind": "assignment", "target_id": int(aid)}],
        },
        _idem("walk-50-case"),
    )
    if st != 200:
        w.rep.not_run("㊿ 全部断言", f"登记变更请求失败（HTTP {st}）：{case}")
        return
    cid = str((case or {}).get("case_id") or "")

    # ── 界面：进工作台 → 案件页 ─────────────────────────────────────────────
    if not w.open_workbench(code_mgr, tag="㊿"):
        w.rep.not_run("㊿ 全部断言", "未能以 seed-owner 进入经理工作台")
        return
    w.c.remove_storage(ORG_STORAGE_KEY)
    w.c.set_storage(ORG_STORAGE_KEY, org_id)
    w.c.nav("navigateTo", f"/{CASE}?case_id={cid}", CASE)
    d = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)

    # ── ③ 经界面**补登变更类别**（界面上此前没有这个入口 ⇒ 变更请求应用不了）──
    n_cat = w.c.count('[data-cat="cargo_quantity_category"]')
    w.rep.rec(
        "㊿ ③ 案件页有「变更类别」选择条，点它能把类别补登成「货物数量与品类」"
        "（没有这个入口，变更请求在界面上根本应用不了：应用要求类别已登记）",
        n_cat == 1,
        f"选择项数={n_cat}（页面上 categoryOptions={len(d.get('categoryOptions') or [])}）",
    )
    if n_cat == 1:
        w.c.scroll_into('[data-cat="cargo_quantity_category"]')
        w.c.tap('[data-cat="cargo_quantity_category"]')
        d = w.wait_data(
            lambda x: (
                str((x.get("decideForm") or {}).get("category") or "") == "cargo_quantity_category"
            ),
            tries=20,
            gap=0.4,
        )
        w.rep.rec(
            "㊿ ③b 选中类别后它真的落进了页面表单（补登的类别会随这次决定一起回传）",
            str((d.get("decideForm") or {}).get("category") or "") == "cargo_quantity_category",
            f"qty 输入框数={w.c.count('[data-df="qty"]')} 单位={w.c.count('[data-df="qty-unit"]')}",
        )

    # ── ③c 进入复核（`open → approved` 不是合法转移，必须两步）────────────
    w.c.tap('[data-status="in_review"]')
    w.c.scroll_into('[data-act-decide-submit="1"]')
    w.c.tap('[data-act-decide-submit="1"]')
    d = w.wait_data(lambda x: str(x.get("status") or "") == "in_review", tries=40, gap=0.5)
    w.rep.rec(
        "㊿ ③c 经界面进入复核（`open → approved` 不是合法转移，服务端会 409）",
        str(d.get("status") or "") == "in_review",
        f"status={d.get('status')!r} hint={d.get('decideHint')!r}",
    )

    # ── ④ 经界面批准（选已批准 → 三只输入 → 提交）────────────────────────
    #
    # ⚠️ **不重新导航**：`nav` 会开一个全新的页面实例（`decideForm` 清空）。
    #    那本身没错，但多一次冷加载就多一次"输入框还没渲染出来就 tap"的机会 ——
    #    本轮实测：第一版重新导航后提交，**后端日志里连那笔 POST 都没有**，
    #    而页面上 `decideHint` 是空的（症状＝"什么都没发生"，最难查的一类）。
    #    留在同一个实例上、等渲染树更新之后再点，路径最短。
    # ⚠️ 新值必须**超过 900 吨**（那条候选的容量），否则 `capacity` 仍然通过、
    #    这条判据就白跑一轮 —— 本轮实测：`800 + 100 = 900` 时 `still_valid` 仍是
    #    True（900 ≥ 900 装得下），而 D1-09 说的是 **950**。
    #    ⇒ 取 `max(当前 + 100, 950)`：首次是 950（D1-09 那对精确值），
    #      重跑（当前已是 950）则继续加 100，仍然超容量，断言不为夹具状态而红。
    new_qty = str(max(cur + 100, Decimal("950")))

    def _fill_and_submit() -> dict:
        """选「已批准」→ 填四只输入 → 提交。返回提交后的页面 data。"""
        w.c.tap('[data-status="approved"]')
        time.sleep(0.8)
        for sel, val in (
            ('[data-df="basis"]', str(basis_rev)),
            ('[data-df="qty"]', new_qty),
            ('[data-df="qty-unit"]', cur_unit),
            ('[data-df="qty-basis"]', "走查 货量变更（D1-09）"),
        ):
            w.c.scroll_into(sel)
            w.c.input_text(sel, val)
        w.c.scroll_into('[data-act-decide-submit="1"]')
        w.c.tap('[data-act-decide-submit="1"]')
        return w.wait_data(lambda x: str(x.get("status") or "") == "approved", tries=30, gap=0.5)

    # ── ④a 选「已批准」后，三只货量输入框**出现在渲染树上** ────────────────
    #    判据取 `count(...)` 而不是内部状态键：条没渲染时 `showQuantityChange`
    #    与 `decideForm.category` 照样是对的（本仓既有的一条纪律）。
    d = w.wait_data(lambda x: str(x.get("view") or "") == "ready", tries=40, gap=0.5)
    w.c.tap('[data-status="approved"]')
    time.sleep(0.8)
    n_qty = w.c.count('[data-df="qty"]')
    n_unit = w.c.count('[data-df="qty-unit"]')
    n_basis_input = w.c.count('[data-df="qty-basis"]')
    w.rep.rec(
        "㊿ ④a 选「已批准」后三只货量输入框**渲染出来了**（内部状态键对了不算数）",
        n_qty == 1 and n_unit == 1 and n_basis_input == 1,
        f"渲染树计数：新值={n_qty} 单位={n_unit} 依据={n_basis_input}"
        f"｜showQuantityChange={d.get('showQuantityChange')!r}"
        f"｜decideForm.to={((d.get('decideForm') or {}).get('to'))!r}"
        f"｜quantityTargetId={d.get('quantityTargetId')!r}",
    )

    # ── ④b 填四只输入（输入通道断了的话，提交出去的是空变更）────────────────
    for sel, val in (
        ('[data-df="basis"]', str(basis_rev)),
        ('[data-df="qty"]', new_qty),
        ('[data-df="qty-unit"]', cur_unit),
        ('[data-df="qty-basis"]', "走查 货量变更（D1-09）"),
    ):
        w.c.scroll_into(sel)
        w.c.input_text(sel, val)
    d = w.wait_data(
        lambda x: str((x.get("quantityForm") or {}).get("quantity") or "") == new_qty,
        tries=20,
        gap=0.4,
    )
    qf = d.get("quantityForm") or {}
    w.rep.rec(
        "㊿ ④b 三只输入框真的把值写进了页面 data",
        str(qf.get("quantity") or "") == new_qty
        and str(qf.get("unit") or "") == cur_unit
        and str(qf.get("basis") or "") == "走查 货量变更（D1-09）",
        f"quantityForm={qf!r}",
    )

    # ── ④c 提交批准（tap 落空时重试一次；判据是**状态真的变了**）────────────
    d = _fill_and_submit()
    if str(d.get("status") or "") != "approved":
        d = _fill_and_submit()
    w.rep.rec(
        "㊿ ④c 经界面批准落库（批准内容随这次请求进快照，`apply` 只认快照）",
        str(d.get("status") or "") == "approved",
        f"status={d.get('status')!r} hint={d.get('decideHint')!r}",
    )

    # 批准快照里必须记下"从哪改到哪" —— 应用确认条要拿它给人看
    ap = d.get("approval") or {}
    tgt = ((ap.get("targets") or []) or [{}])[0] or {}
    w.rep.rec(
        "㊿ ④d 应用确认条上能看到 `当前 → 将改为`（apply 不接受改写，不给对照就是盲操作）",
        bool(tgt.get("quantityChangeText")) and new_qty in str(tgt.get("quantityChangeText")),
        f"quantityChangeText={tgt.get('quantityChangeText')!r}",
    )

    # ── ⑤ 经界面应用 ───────────────────────────────────────────────────────
    w.c.scroll_into('[data-act-apply="1"]')
    w.c.tap('[data-act-apply="1"]')
    time.sleep(0.6)
    w.c.scroll_into('[data-act-apply-submit="1"]')
    w.c.tap('[data-act-apply-submit="1"]')
    d = w.wait_data(
        lambda x: (
            str(x.get("status") or "") == "applied" or len(x.get("quantityHistory") or []) > 0
        ),
        tries=60,
        gap=0.5,
    )
    after = api_get(f"/entrust/assignments/{aid}", tok) or {}
    now_qty = after.get("quantity")
    w.rep.rec(
        f"㊿ ⑤ 经界面应用后**货量真的落库**（{cur_raw} → {now_qty}）",
        _same_dec(now_qty, new_qty),
        f"服务端 quantity={now_qty!r}（期望 {new_qty}）· 页面 status={d.get('status')!r}",
    )
    hist = d.get("quantityHistory") or []
    mine = [h for h in hist if str(h.get("exceptionId") or "") == cid]
    row = mine[0] if mine else {}
    # ⚠️ 判据是**不变量**："那一行就是前后两段的拼接"，而不是我自己拼一个期望串 ——
    #    服务端把货量格式化成固定 3 位小数（`800` → `800.000`），
    #    拿接口原文那个 `800` 去拼期望串会差在**格式**上（本轮实测就这么红了一次）。
    w.rep.rec(
        "㊿ ⑤b 页面上出现那条对照（`<变更前> → <变更后>`，且来源案件号对得上）",
        str(row.get("changeText") or "")
        == f"{row.get('oldQuantityText')} → {row.get('newQuantityText')}"
        and "→" in str(row.get("changeText") or "")
        and str(row.get("exceptionId") or "") == cid,
        f"changeText={row.get('changeText')!r} source={row.get('sourceText')!r} "
        f"basis={row.get('basisText')!r}（历史共 {len(hist)} 行）",
    )
    if fresh:
        srv_hist = api_get(f"/entrust/assignments/{aid}/quantity-changes", tok) or []
        srv_row = [h for h in srv_hist if str(h.get("exception_id") or "") == cid]
        w.rep.rec(
            "㊿ ⑤c ⭐ D1-09 的**精确那对值**：`800.000 吨 → 950.000 吨`，且页面与服务端同源",
            bool(row.get("changeText"))
            and str(row.get("changeText")) == "800.000 吨 → 950.000 吨"
            and bool(srv_row)
            and _same_dec(srv_row[0].get("new_quantity"), "950.000"),
            f"页面={row.get('changeText')!r} 服务端="
            f"{(srv_row[0].get('old_quantity'), srv_row[0].get('new_quantity')) if srv_row else None}",
        )
    else:
        w.rep.not_run(
            "㊿ ⑤c D1-09 的精确那对值 `800.000 → 950.000`",
            f"canonical 夹具的货量已被改到 {cur_raw}（本机上一轮走查的副作用）—— "
            "要复现那对精确值请先重建库；本章 ⑤/⑤b 已按相对值取证",
        )

    # ── ⑥ 变更后：原运力不再适用（**只有** capacity 不过）──────────────────
    post = api_get(f"/entrust/capacity-confirmations/{conf_id}/recheck", tok) or {}
    fails = [
        r.get("rule_code") for r in (post.get("rule_checks") or []) if r.get("outcome") == "fail"
    ]
    w.rep.rec(
        "㊿ ⑥ 变更后 · 同一条运力确认**不再成立**，且**只有** `capacity` 不过"
        "（其余三条照旧通过 —— 否则「900 吨候选不适用」是一句无从定位的话）",
        post.get("still_valid") is False and fails == ["capacity"],
        f"still_valid={post.get('still_valid')!r} 不过的规则={fails} "
        f"changed_fields={post.get('changed_fields')!r}",
    )

    # ── ⑦ 复核传播在页面上 ─────────────────────────────────────────────────
    w.c.scroll_into('[data-act-apply="1"]')
    d = w.wait_data(lambda x: len(x.get("revalidation") or []) > 0, tries=40, gap=0.5)
    areas = [str(r.get("area") or "") for r in (d.get("revalidation") or [])]
    w.rep.rec(
        "㊿ ⑦ 页面上出现复核传播，含「船型、运力及货物适配」（＝复核与重新确认的落点）",
        any("船型" in a for a in areas),
        f"复核区域={areas}",
    )

    # ── ⑧ 零新增 console 报错 ──────────────────────────────────────────────
    err_now = w.c.errors()
    w.rep.rec(
        "㊿ ⑧ 本章运行期无新增 console 报错",
        err_now == err_base or not err_now,
        (err_now or "(空)")[-160:],
    )


def _same_dec(raw: object, want: str) -> bool:
    """定点比较：`"800.000"` / `800` / `Decimal("800")` 都算相等。

    ⛔ 不用 `str(raw) == want`：SQLite 与 MySQL 读回来一个是 `NUMERIC`、一个是
    `DECIMAL`，字符串形态可能差尾零（`800` vs `800.000`），拿字符串比会红在格式上。
    """
    try:
        return Decimal(str(raw)) == Decimal(want)
    except (TypeError, ValueError, InvalidOperation):
        return False


def _idem(tag: str) -> str:
    """走查用的幂等键：每次唯一（同键重放会命中幂等记录、返回首次响应）。"""
    return f"walk-{tag}-{time.time_ns()}"


def sec_51(w: Walker) -> None:
    """第 6 步**来源门槛的被拒剧本**：AG-02 载体 ⇒ 被拒 ⇒ 逐条核验 ⇒ 同版本发布成功。

    为什么单开一章而不塞进 ㊹
    ------------------------
    ㊹ 章的载体是**经界面人工组装**的 `customer_quote`（来源 `manual`）⇒ 服务端没有
    待核验的模型声明 ⇒ 门槛一次就过 ⇒ 那条 `else` 分支只能记 `NOT_RUN`
    （`DEMO-1-readiness.md` §10.4 的 O-1 残留分支）。而本剧本需要一个**来源为模型声明**
    的载体，两条剧本的载体**预期完全相反**，混在一章里必然互相污染读数
    （㊹ 首跑就是这么得到 3 条假 FAIL 的）。本章独立、自带载体，跑不跑都不影响 ㊹。

    覆盖
    ----
    ① 前置：canonical 委托在位；AG-02 会话可得（`session-context` 给授权 id，**不前端推导**）；
    ② 经接口发起一次**带 `amount`** 的 AG-02 作业 ⇒ 提案里含 `customer_quote`；
    ③ 采纳 ⇒ 成果的**版本来源是模型声明**，且服务端已写下**待核验声明**（≥1 条）；
    ④ 经**界面**点「发布这一版」⇒ **被拒**，且页面把待核验清单**显示出来**
       （只说"不能发布"是没法干活的）；
    ⑤ 页面上**没有**「登记来源核验」的入口（这是被测事实，见下"诚实边界"）；
    ⑥ 经接口逐条登记核验（必填依据）⇒ 门槛转为通过；
    ⑦ 回界面**重新点发布** ⇒ 同一版本发布成功，且与服务端发布记录同源。

    ⚠️ 诚实边界（都是被测事实的一部分，不是辩解）
    * **作业的 `amount` 只能经接口给**：界面上没有这个入参（`miniapp` 全仓无 `amount`
      输入锚点）。这正是"主链路缺『组装对客报价』"那条缺口的表现 —— 本章如实登记
      "输入路径经接口"，**不假装是界面点的**。其余动作（进成果页、点发布）都是真机点击。
    * **⑤ 是负例断言**（"页面上没有这个入口"）。它单独成立时没有信息量（任何页面都满足），
      所以必须与 ④ **同时成立**才有意义 —— ④ 证明页面确实处在"被拒且看得见待核验清单"
      的状态。这就是 O-1b 要的答案：**界面能告诉你为什么不能发，却给不了你解决它的入口**。
    * 依赖「组织队列里有一张委托」（`seed_entrust_demo.py` 即可）。**不绑 canonical** ——
      那是一个「要演示哪个状态就铺哪个」的可选夹具，把它当本章前置会让章节在标准配方下
      直接变成一条 `FAIL`（首跑实测 `aid=''`）。载体由本章自造，与夹具无关。
    * 本章**会写库**（建会话、发作业、采纳成果、核验、发布）。重跑：成果每次新采纳一份
      （幂等键带时间戳），不会撞唯一键。
    """
    print("\n-- 51 第 6 步来源门槛：被拒 ⇒ 逐条核验 ⇒ 同版本发布（S6-3）--", flush=True)

    code_mgr = CODE_OWNER
    org_name = "演示经营主体·工作台"

    err_base = w.c.errors()
    tok = (api_login(code_mgr) or {}).get("access_token") or ""
    if not tok:
        w.rep.not_run("51 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return

    def _rev_row(page: dict, no: int) -> dict:
        for r in page.get("revisions") or []:
            if int((r or {}).get("revisionNo") or 0) == no:
                return r or {}
        return {}

    # ---- ① 前置：组织队列里有一张委托 ----
    org_id = ""
    for o in (api_get("/entrust/my-orgs", tok) or {}).get("items") or []:
        if str((o or {}).get("name") or "") == org_name:
            org_id = str((o or {}).get("org_id") or "")
            break
    rows = (api_get(f"/entrust/assignments?view=org&org_id={org_id}&size=50", tok) or {}).get(
        "items"
    ) or []
    # ⭐ 不绑 canonical：本剧本的载体**自己造**（发作业 → 采纳），只需要队列里有一张委托。
    #    把章节钉在一个「要演示哪个状态就铺哪个」的可选夹具上，会让它在标准配方下直接
    #    变成一条 `FAIL`（首跑实测 `aid=''` 命中 0 张）—— 读者会以为产品坏了。
    rows.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
    aid = prefer_anchor(str((rows[0] or {}).get("assignment_id") or "") if rows else "")
    w.rep.rec(
        "51 前置 · 组织队列里有可用的委托（载体由本章自造，不依赖可选夹具）",
        bool(aid),
        f"aid={aid!r} status={(rows[0] or {}).get('status') if rows else None!r} 队列 {len(rows)} 张",
    )
    if not aid:
        w.rep.not_run("51 来源门槛剧本", "没有 canonical 委托 ⇒ 先跑 seed_entrust_canonical.py")
        return

    # ---- ② 会话 + 带 amount 的作业 ----
    ctx = api_get(f"/entrust/assignments/{aid}/session-context", tok) or {}
    eid = str(ctx.get("entrustment_id") or "")
    sid = ""
    for s in (
        api_get(f"/entrust/sessions?view=org&org_id={org_id}&assignment_id={aid}&size=50", tok)
        or {}
    ).get("items") or []:
        if str((s or {}).get("agent_specialty") or "") == "agent_02":
            sid = str((s or {}).get("session_id") or "")
            break
    if not sid and eid:
        st_sess, sess = api_post(
            f"/entrust/entrustments/{eid}/sessions",
            tok,
            {
                "assignment_id": int(aid),
                "agent_specialty": "agent_02",
                "title": "S6-3 来源门槛剧本",
            },
            uuid.uuid4().hex,
        )
        if st_sess in (200, 201):
            sid = str((sess or {}).get("session_id") or "")
    w.rep.rec(
        "51 前置 · 授权 id 由 `session-context` 给出（**不前端推导**），AG-02 会话可得",
        bool(eid) and bool(sid),
        f"entrustment_id={eid!r} session_id={sid!r}",
    )
    if not sid:
        w.rep.not_run("51 来源门槛剧本", "拿不到 AG-02 会话 ⇒ 后续无法制造模型来源的载体")
        return

    # ⚠️ 作业输入的 `amount` **只能经接口给**（界面无此入参）—— 如实登记，不假装是界面点的。
    st_job, job = api_post(
        f"/entrust/sessions/{sid}/jobs",
        tok,
        {
            "input": {
                "amount": "18600",
                "currency": "CNY",
                "includes": ["内河运费", "港杂费"],
                "quote_text": "承运人：贵港航运有限公司\n单价：18600 元/柜\n有效期：2026-12-31",
            }
        },
        uuid.uuid4().hex,
    )
    job_id = str((job or {}).get("job_id") or "")
    # 提交与执行是**两个动作**：不点 run，作业永远停在 `queued`（envelope 为 null）。
    if job_id:
        api_post(f"/entrust/agent/jobs/{job_id}/run", tok, {}, uuid.uuid4().hex)
    jrow = {}
    for _ in range(40):
        jrow = (
            ((api_get(f"/entrust/agent/jobs/{job_id}", tok) or {}).get("job") or {})
            if job_id
            else {}
        )
        if str(jrow.get("status") or "") in ("succeeded", "failed"):
            break
        time.sleep(1)
    proposals = [
        p
        for p in ((jrow.get("envelope") or {}).get("artifact_proposals") or [])
        if isinstance(p, dict)
    ]
    cq = [p for p in proposals if str(p.get("artifact_type") or "") == "customer_quote"]
    w.rep.rec(
        "51 ② 带 `amount` 的 AG-02 作业 ⇒ 提案里**有** `customer_quote`"
        "（`ag02.py` 的产出条件就是作业输入带对客金额）",
        str(jrow.get("status")) == "succeeded" and bool(cq),
        f"job={job_id!r} status={jrow.get('status')!r} 提案类型="
        f"{sorted({str(p.get('artifact_type')) for p in proposals})}",
    )
    if not cq:
        w.rep.not_run("51 来源门槛剧本", "作业没有产出 customer_quote ⇒ 没有可发布的载体")
        return

    # ---- ③ 采纳 ⇒ 模型来源的成果 ----
    st_adopt, art = api_post(
        f"/entrust/agent/jobs/{job_id}/adopt",
        tok,
        {
            "artifact_type": "customer_quote",
            "payload": cq[0].get("payload") or {},
            "note": "S6-3 来源门槛剧本：采纳 AG-02 的对客报价提案",
        },
        uuid.uuid4().hex,
    )
    art_id = str((art or {}).get("artifact_id") or "")
    if st_adopt not in (200, 201) or not art_id:
        w.rep.not_run("51 来源门槛剧本", f"采纳失败：st={st_adopt} {str(art)[:160]}")
        return
    rev = int((api_get(f"/entrust/artifacts/{art_id}", tok) or {}).get("current_revision_no") or 0)
    if not rev:
        rev = 1
    gate = (api_get(f"/entrust/artifacts/{art_id}/source-checks?revision_no={rev}", tok) or {}).get(
        "gate"
    ) or {}
    declared = gate.get("declared") or []
    w.rep.rec(
        "51 ③ 采纳 ⇒ 服务端写下**待核验声明**（这是「来源为模型声明」在数据上的样子）",
        len(declared) >= 1 and bool(gate.get("pending")),
        f"artifact={art_id!r} v{rev} declared={len(declared)} pending={len(gate.get('pending') or [])} "
        f"ok={gate.get('ok')!r}",
    )

    # ---- ④ 经界面进入该成果页并发起发布 ----
    if not w.open_workbench(code_mgr, tag="51"):
        w.rep.not_run("51 ④ 经界面发布", "未能进入经理工作台")
        return
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=40, gap=0.5)
    sel_card = f'[data-id="{aid}"]'
    w.c.scroll_into(sel_card)
    t_card = w.c.tap(sel_card)
    ok_dt = w.c.wait_path(DETAIL, 30)
    time.sleep(1.4)
    w.wait_data(lambda x: bool(x.get("slots")), tries=40, gap=0.5)
    sel_ref = f'[data-kind="artifact"][data-id="{art_id}"]'
    w.c.scroll_into(sel_ref)
    n_ref = w.c.count(sel_ref)
    t_ref = w.c.tap(sel_ref)
    ok_art = w.c.wait_path(ARTIFACT, 30)
    time.sleep(1.3)
    pg_art = w.wait_data(lambda x: x.get("artifact") is not None, tries=40, gap=0.5)
    w.shot("51-1-模型来源成果页")
    w.rep.rec(
        "51 ④ 「工作台 → 委托卡 → 详情 → 成果引用 → 成果页」全程真实点击，落到**模型来源**的成果",
        bool(t_card and ok_dt and t_ref and ok_art) and str(pg_art.get("artifactId")) == art_id,
        f"card={t_card} detail={ok_dt} 引用锚点 {n_ref} 个 ref={t_ref} artifact={ok_art} "
        f"落页 artifactId={pg_art.get('artifactId')!r}（期望 {art_id}）",
    )

    sel_rel = f'[data-act-release="1"][data-no="{rev}"]'
    w.c.scroll_into(sel_rel)
    n_rel = w.c.count(sel_rel)
    t_rel = w.c.tap(sel_rel)
    time.sleep(0.5)
    n_strip = w.c.count('[data-act-release-submit="1"]')
    t_sub = w.c.tap('[data-act-release-submit="1"]')
    pg_rej = w.wait_data(
        lambda x: bool(x.get("releaseHint")) or bool(_rev_row(x, rev).get("published")),
        tries=40,
        gap=0.5,
    )
    hint = str(pg_rej.get("releaseHint") or "")
    published = bool(_rev_row(pg_rej, rev).get("published"))
    w.shot("51-2-发布被门槛拒")
    w.rep.rec(
        "51 ④ 经界面点「发布这一版」⇒ **被来源门槛拒**，且页面把**待核验清单显示出来**"
        "（只说「不能发布」是没法干活的）",
        (not published) and ("待核验" in hint) and n_rel == 1 and bool(t_rel and t_sub and n_strip),
        f"入口 {n_rel} 个 tap={t_rel} 确认条 {n_strip} submit={t_sub} published={published} "
        f"releaseHint={hint[:140]!r}",
    )

    # ---- ⑤ 界面有没有「登记来源核验」的入口（O-1b 的判据）----
    n_entry = w.c.count('[data-act-source-check="1"]') + w.c.count('[data-act-source-verify="1"]')
    w.rep.rec(
        "51 ⑤ ⭐ 界面上**没有**「登记来源核验」的入口（O-1b 的答案）——"
        "页面能告诉你为什么不能发，却给不了你解决它的入口",
        n_entry == 0 and (not published) and ("待核验" in hint),
        f"核验入口锚点 {n_entry} 个；同一条断言与 ④ 同时成立才有信息量"
        f"（④ 证明页面确实处在「被拒且看得见清单」的状态）",
    )

    # ---- ⑥ 经接口逐条核验（必填依据）----
    ok_all = True
    for p in gate.get("pending") or []:
        st_v, _ = api_post(
            f"/entrust/artifacts/{art_id}/source-checks",
            tok,
            {
                "revision_no": rev,
                "source_kind": p.get("kind"),
                "source_ref": p.get("ref"),
                "state": "verified",
                "method": "S6-3 走查：逐条比对作业输入与提案载荷",
            },
            uuid.uuid4().hex,
        )
        ok_all = ok_all and st_v in (200, 201)
    gate2 = (
        api_get(f"/entrust/artifacts/{art_id}/source-checks?revision_no={rev}", tok) or {}
    ).get("gate") or {}
    w.rep.rec(
        "51 ⑥ 经接口逐条登记核验（`method` 必填：无依据的核验等于没核）⇒ 门槛转为通过",
        ok_all and bool(gate2.get("ok")),
        f"pending 由 {len(gate.get('pending') or [])} 条 → {len(gate2.get('pending') or [])} 条；"
        f"gate.ok={gate2.get('ok')!r}",
    )

    # ---- ⑦ 回界面重新点发布 ⇒ 同一版本成功 ----
    n_cancel = w.c.count('[data-act-release-cancel="1"]')
    if n_cancel:
        w.c.tap('[data-act-release-cancel="1"]')
        time.sleep(0.6)
    w.c.scroll_into(sel_rel)
    t_rel2 = w.c.tap(sel_rel)
    time.sleep(0.6)
    t_sub2 = w.c.tap('[data-act-release-submit="1"]')
    pg_ok = w.wait_data(lambda x: bool(_rev_row(x, rev).get("published")), tries=60, gap=0.5)
    published2 = bool(_rev_row(pg_ok, rev).get("published"))
    w.shot("51-3-同版本发布成功")
    rels = (api_get(f"/entrust/entrustments/{eid}/offer-releases", tok) or {}).get("items") or []
    same = [
        r
        for r in rels
        if str((r or {}).get("artifact_id") or "") == art_id
        and int((r or {}).get("revision_no") or 0) == rev
    ]
    w.rep.rec(
        "51 ⑦ 核验之后**回界面重新点发布** ⇒ 同一版本发布成功，且与服务端发布记录**同源**",
        published2 and bool(same) and bool(t_rel2 and t_sub2),
        f"tap={t_rel2} submit={t_sub2} published={published2}；"
        f"服务端同成果同版本发布记录 {len(same)} 条（releaseId={same[0].get('release_id') if same else None}）",
    )

    err_now = w.c.errors()
    w.rep.rec(
        "51 ⑧ 本章运行期零新增 console 报错",
        len(err_now) == len(err_base),
        f"console 报错 {len(err_base)} → {len(err_now)}",
    )


def sec_52(w: Walker) -> None:
    """财务与结算：费用 → 缺件补录 → 结算版本 → 内部确认 → 客户确认 → 收付 → 已结清。

    对应 §10.1 第 10–11 步（S7-1 费用 / S7-2 缺件 / S7-3 结算与收付），是裁定 **Q4=A**
    「第 10–12 步经现有界面演示」在**设备侧**的取证。

    为什么单开一章
    --------------
    `pages/entrust/finance/finance` 是这三片后端**唯一的界面入口**。它的判据此前只有
    「静态契约 ＋ 载荷驱动」两类（`verify_entrust_ui` / `verify_frontend_e2e`），
    ⛔ 两者都**点不到按钮**：合同要求的是"经界面演示"，所以必须有一章真机点击。

    ⚠️ 诚实边界（都是被测事实，不是辩解）
    * **带必需证据的任务只能经接口铺**（`onSubmitTask` 的表单没有 `required_evidence`
      入参）⇒ ⑦ 的**前置**是接口造的，**补录动作本身是真机点击**。如实写在读数里。
    * **客户确认必须换身份**：`customer-confirm` 只有货主本人能做 ⇒ ⑫ 以 `seed-shipper`
      重新登录、从**自己的**委托详情页进财务页点确认（不是拿经理身份调接口冒充客户）。
    * 依赖「货主名下有一张 `claimed` 委托」（`seed_entrust_demo` 会铺）⇒ **不绑 canonical**
      —— 那是"要演示哪个状态就铺哪个"的可选夹具，钉上去会让本章在标准配方下直接变 `FAIL`。
    * 本章**会写库**（建任务、记费用、出结算、记收付、客户确认）。重跑不受影响：
      每次新挑一张委托，幂等键带时间戳。
    """
    print("\n-- 52 财务与结算：第 10–11 步经界面走通（S7-1/S7-2/S7-3）--", flush=True)

    finance_path = "pages/entrust/finance/finance"
    err_base = w.c.errors()

    tok_mgr = (api_login(CODE_OWNER) or {}).get("access_token") or ""
    tok_shi = (api_login(CODE_SHIPPER) or {}).get("access_token") or ""
    if not tok_mgr or not tok_shi:
        w.rep.not_run(
            "52 全部断言", "拿不到 seed-owner / seed-shipper 的 token（后端未起或种子未铺）"
        )
        return

    def _has_text(payload: dict, needle: str) -> bool:
        for b in payload.get("blockers") or []:
            if needle in str((b or {}).get("text") or "") or needle in str(
                (b or {}).get("message") or ""
            ):
                return True
        return False

    # ---- ① 前置：货主名下的一张已受理委托（本章自己挑，不依赖可选夹具）----
    rows = (api_get("/entrust/assignments?view=owner&size=50", tok_shi) or {}).get("items") or []
    claimed = [r for r in rows if str((r or {}).get("status")) == "claimed"]
    claimed.sort(key=lambda r: int((r or {}).get("assignment_id") or 0), reverse=True)
    aid = prefer_anchor(str((claimed[0] or {}).get("assignment_id") or "") if claimed else "")
    w.rep.rec(
        "52 前置 · 货主名下有一张已受理（claimed）委托 —— 本章自己挑一张，不绑可选夹具",
        bool(aid),
        f"aid={aid!r} claimed {len(claimed)} 张 / 名下共 {len(rows)} 张",
    )
    if not aid:
        w.rep.not_run("52 财务与结算剧本", "货主名下没有 claimed 委托 ⇒ 先铺 seed_entrust_demo")
        return

    # ---- ② 前置夹具：一个**要求照片证据**的交接任务 ----
    _st_t, task = api_post(
        f"/entrust/assignments/{aid}/tasks",
        tok_mgr,
        {
            "task_type": "handover",
            "title": "卸货交接（52 章）",
            "required_evidence": ["photo"],
        },
        uuid.uuid4().hex,
    )
    tid = str((task or {}).get("task_id") or "")
    w.rep.rec(
        "52 前置 · 经**接口**铺一个「要求照片证据」的任务（界面建任务没有 required_evidence 入参，如实登记）",
        bool(tid),
        f"task_id={tid!r}",
    )
    if not tid:
        w.rep.not_run("52 财务与结算剧本", "任务未建成 ⇒ 缺件补录那一步无从演示")
        return

    # ---- ③ 经理侧：从委托详情点进「财务与结算」----
    if not w.open_workbench(CODE_OWNER, tag="52"):
        w.rep.not_run("52 财务与结算剧本", "未能以 seed-owner 进入经理工作台")
        return
    if not w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL):
        w.rep.not_run("52 财务与结算剧本", f"打不开委托详情页（aid={aid}）")
        return
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    has_entry = w.c.count('[data-act-open-finance="1"]') > 0
    w.c.scroll_into('[data-act-open-finance="1"]')
    w.c.tap('[data-act-open-finance="1"]')
    landed = w.c.wait_path(finance_path, 30)
    w.rep.rec(
        "52 ① 委托详情页上有「财务与结算」入口，且点得进 —— 这是第 10–11 步在**现有界面**上的入口",
        has_entry and landed,
        f"入口={has_entry} 落地={w.c.current_path()}",
    )
    if not landed:
        w.rep.not_run("52 财务与结算剧本", "未能进入财务与结算页")
        return

    pd = w.wait_data(lambda x: x.get("canViewInternal") is True, tries=40, gap=0.4)
    w.rep.rec(
        "52 ② 经理视角取到**内部**读数（费用 / 缺件 / 结算 / 财务状态四块都由这一页取）",
        pd.get("canViewInternal") is True,
        f"canViewInternal={pd.get('canViewInternal')} 费用 {len(pd.get('charges') or [])} 条",
    )
    if not pd.get("canViewInternal"):
        w.rep.not_run("52 财务与结算剧本", "经理侧拿不到内部读数（组织权限或种子问题）")
        return

    # ---- ④ 登记一条应收费用：**草稿不进合计** ----
    n0 = len(pd.get("charges") or [])
    counted0 = int(pd.get("chargeCounted") or 0)
    w.c.scroll_into('[data-act="toggle-charge"]')
    w.c.tap('[data-act="toggle-charge"]')
    w.wait_data(lambda x: x.get("chargeOpen") is True, tries=20, gap=0.3)
    w.c.input_text('[data-key="chargeKind"]', "freight")
    w.c.input_text('[data-key="amount"]', "100000")
    w.c.input_text('[data-key="basis"]', "合同附件三·费率表")
    w.c.tap('[data-act="submit-charge"]')
    pd = w.wait_data(lambda x: len(x.get("charges") or []) > n0, tries=40, gap=0.4)
    drafts = [c for c in (pd.get("charges") or []) if c.get("status") == "draft"]
    w.rep.rec(
        "52 ③ 界面上登记出**草稿**费用行，且**草稿不进合计**（合同 S4 段第 9 条的确认前状态）",
        len(drafts) >= 1 and int(pd.get("chargeCounted") or 0) == counted0,
        f"草稿 {len(drafts)} 条｜计入条数 {counted0} → {pd.get('chargeCounted')}",
    )
    cid = str((drafts[-1] or {}).get("chargeId") or "") if drafts else ""

    # ---- ⑤ 确认：确认后才进合计 ----
    if cid:
        w.c.scroll_into('[data-act="confirm-charge"]')
        w.c.tap('[data-act="confirm-charge"]')
        pd = w.wait_data(lambda x: int(x.get("chargeCounted") or 0) > counted0, tries=40, gap=0.4)
        totals_txt = " ".join(
            (t or {}).get("totalText") or "" for t in (pd.get("chargeTotals") or [])
        )
        w.rep.rec(
            "52 ④ 界面确认后该行进合计（合计金额由服务端算，页面只显示它给的字符串）",
            int(pd.get("chargeCounted") or 0) == counted0 + 1 and "100000" in totals_txt,
            f"计入 {pd.get('chargeCounted')} 条，合计={totals_txt.strip()!r}",
        )

    # ---- ⑥ 提争议：**争议行退出合计**（合同 S4 段第 10 条）----
    w.c.scroll_into('[data-act="open-dispute"]')
    w.c.tap('[data-act="open-dispute"]')
    w.wait_data(lambda x: x.get("disputeOpenKey") not in (None, ""), tries=20, gap=0.3)
    w.c.input_text('[data-key="reason"]', "客户认为吨位口径不一致")
    w.c.tap('[data-act="submit-dispute"]')
    pd = w.wait_data(lambda x: int(x.get("chargeCounted") or 0) == counted0, tries=40, gap=0.4)
    disputed = [c for c in (pd.get("charges") or []) if c.get("status") == "disputed"]
    w.rep.rec(
        "52 ⑤ 提争议后该行**退出合计**（合同 S4 段第 10 条：争议不进已确认总额）",
        len(disputed) >= 1 and int(pd.get("chargeCounted") or 0) == counted0,
        f"争议 {len(disputed)} 条｜计入条数回落 → {pd.get('chargeCounted')}",
    )

    # ---- ⑦ 处置：**显式给出是否计入 ＋ 最终金额**（裁定 Q2=B）----
    w.c.scroll_into('[data-act="open-resolve"]')
    w.c.tap('[data-act="open-resolve"]')
    w.wait_data(lambda x: x.get("resolveOpenKey") not in (None, ""), tries=20, gap=0.3)
    w.c.tap('[data-act="pick-outcome"][data-code="adjusted"]')
    w.c.input_text('[data-key="method"]', "按复查后的吨位重算")
    ok_counts = w.c.count('[data-act="pick-counts"][data-code="yes"]') > 0
    w.c.tap('[data-act="pick-counts"][data-code="yes"]')
    w.c.input_text('[data-key="finalAmount"]', "88000")
    w.c.tap('[data-act="submit-resolve"]')
    pd = w.wait_data(lambda x: int(x.get("chargeCounted") or 0) > counted0, tries=40, gap=0.4)
    resolved = [c for c in (pd.get("charges") or []) if c.get("status") == "resolved"]
    totals_txt = " ".join((t or {}).get("totalText") or "" for t in (pd.get("chargeTotals") or []))
    w.rep.rec(
        "52 ⑥ 处置时**显式选「计入」并给最终金额** ⇒ 合计按最终金额走（裁定 Q2=B："
        "「已解决」一词决定不了是否计入）",
        len(resolved) >= 1 and "88000" in totals_txt,
        f"已解决 {len(resolved)} 条｜「计入」选项在={ok_counts}｜合计={totals_txt.strip()!r}",
    )

    # ---- ⑧ 缺件：在原任务上补录（§1 交接证据挂在任务上）----
    gaps = pd.get("gaps") or []
    miss_before = [g for g in gaps if g.get("missing")]
    w.rep.rec(
        "52 ⑦-a 缺件视图把「还缺什么」算出来（派生，不新建实体）—— 夹具任务要求照片",
        any("photo" in (g.get("missing") or []) for g in gaps),
        "；".join(f"#{g.get('taskId')} 缺 {g.get('missingText')!r}" for g in miss_before[:3])
        or "无缺件",
    )
    w.c.scroll_into('[data-act="open-evidence"]')
    w.c.tap('[data-act="open-evidence"]')
    w.wait_data(lambda x: x.get("evidenceOpenKey") not in (None, ""), tries=20, gap=0.3)
    w.c.tap('[data-act="pick-evidence-kind"][data-code="photo"]')
    w.c.input_text('[data-key="ref"]', "att-discharge-52")
    w.c.input_text('[data-key="occurredAt"]', "2026-09-17T08:30:00")
    w.c.tap('[data-act="submit-evidence"]')
    pd = w.wait_data(
        lambda x: all(
            "photo" not in (g.get("missing") or [])
            for g in (x.get("gaps") or [])
            if int(g.get("taskId") or 0) == int(tid)
        ),
        tries=40,
        gap=0.4,
    )
    cleared = all(
        "photo" not in (g.get("missing") or [])
        for g in (pd.get("gaps") or [])
        if int(g.get("taskId") or 0) == int(tid)
    )
    hint = str(pd.get("evidenceHint") or "")
    w.rep.rec(
        "52 ⑦-b 在**原任务**上补录一条证据 ⇒ 该任务的缺项消失（业务发生时间界面上必填）",
        cleared and bool(hint),
        f"缺项清空={cleared} 页内提示={hint!r}",
    )

    # ---- ⑨ 结算版本：生成 → 内部确认 ----
    w.c.scroll_into('[data-act="create-settlement"]')
    w.c.tap('[data-act="create-settlement"]')
    pd = w.wait_data(lambda x: len(x.get("settlements") or []) >= 1, tries=40, gap=0.4)
    v1 = [s for s in (pd.get("settlements") or []) if int(s.get("versionNo") or 0) == 1]
    w.rep.rec(
        "52 ⑧ 界面上生成结算**版本** v1（快照当时的费用事实；旧版本不改）",
        len(v1) == 1,
        f"版本数={len(pd.get('settlements') or [])} v1 状态={((v1 or [{}])[0]).get('statusText')!r}",
    )
    w.c.scroll_into('[data-act="approve-settlement"]')
    w.c.tap('[data-act="approve-settlement"]')
    pd = w.wait_data(
        lambda x: any(s.get("status") == "approved" for s in (x.get("settlements") or [])),
        tries=40,
        gap=0.4,
    )
    w.rep.rec(
        "52 ⑨ 内部确认（draft → approved）—— 只有适用版本能被确认",
        any(s.get("status") == "approved" for s in (pd.get("settlements") or [])),
        "状态=" + str([s.get("statusText") for s in (pd.get("settlements") or [])]),
    )

    # ---- ⑩ 财务状态：此刻应当卡在「客户尚未确认」----
    w.rep.rec(
        "52 ⑩ 财务状态在客户确认之前**不是**已结清，且把原因说出来（不是只给一个状态码）",
        _has_text(pd, "客户尚未确认"),
        "状态="
        + str(pd.get("financialStatusText"))
        + "｜blockers="
        + str([b.get("text") for b in (pd.get("blockers") or [])])[:200],
    )

    # ---- ⑪ 记收付依据（合成样本，界面不宣称真实到账）----
    w.c.scroll_into('[data-act="open-payment"]')
    w.c.tap('[data-act="open-payment"]')
    w.wait_data(lambda x: x.get("paymentOpenKey") not in (None, ""), tries=20, gap=0.3)
    w.c.input_text('[data-key="amount"]', "88000")
    w.c.input_text('[data-key="ref"]', "SAMPLE-52-001")
    w.c.tap('[data-act="submit-payment"]')
    pd = w.wait_data(lambda x: "合成样本" in str(x.get("settlementHint") or ""), tries=40, gap=0.4)
    w.rep.rec(
        "52 ⑪ 记一条收付依据：页内明说它是**合成样本**（不接真实支付、不宣称资金到账）",
        "合成样本" in str(pd.get("settlementHint") or ""),
        f"页内提示={str(pd.get('settlementHint'))[:120]!r}",
    )

    # ---- ⑫ 客户侧：换**货主本人**的身份进来确认这一版 ----
    w.login_as(CODE_SHIPPER)
    # ⚠️ `login_as` 只写 storage ＋ reLaunch，**应用的登录是页面 onLoad 里异步做的**
    #    ⇒ 紧接着 `navigateTo` 会赶在拿到 token 之前，详情页按"未登录"处理
    #    （读不到数据、也就没有入口）。
    #    ⛔ 曾用"轮询 `wx.getStorageSync('access_token')` 非空"当就绪判据 —— 实测
    #    该读数**恒为空**（本轨读不到这个键），于是它对"登录到底成没成"什么都没说，
    #    只是把 20 秒等掉。改用本仓既有的就绪配方（见 ① / ② / ⑭ 章）：
    #    `login_as` → **`enter_role` 点身份卡落到货主首页** → 再导航。
    #    点身份卡本身就要求登录已经完成（首页没登录就没卡片可点），
    #    所以它比读那个键更接近"真的就绪"。
    if not w.enter_role("shipper", SHIPPER):
        w.rep.not_run(
            "52 ⑫ 客户确认",
            f"货主身份切不过去（点身份卡没落到货主首页，path={w.c.current_path()}）",
        )
        return
    if not w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL):
        w.rep.not_run("52 ⑫ 客户确认", "货主侧打不开这张委托的详情页")
        return
    # 就绪：等到详情页**真的取到数**。读数里带 `view` ＋ 入口判据的中间量，
    # 让"页面没取到数"与"取到了数但入口条件不成立"在读数上就分开 ——
    # 两者都表现为"入口是 0 个"，但指向完全不同的修法。
    shd = w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    has_entry_shi = w.c.count('[data-act-open-finance="1"]') > 0
    if not has_entry_shi:
        w.rep.not_run(
            "52 ⑫ 客户确认",
            f"货主侧详情页**没有**「财务与结算」入口"
            f"（view={shd.get('view')!r} 入口判据 canCreateCase={shd.get('canCreateCase')!r} "
            f"槽位={len(shd.get('slots') or [])} path={w.c.current_path()}）",
        )
        return
    w.c.scroll_into('[data-act-open-finance="1"]')
    w.c.tap('[data-act-open-finance="1"]')
    if not w.c.wait_path(finance_path, 30):
        w.rep.not_run("52 ⑫ 客户确认", f"点了入口但没到财务页（path={w.c.current_path()}）")
        return
    cpd = w.wait_data(lambda x: x.get("customerMode") is True, tries=40, gap=0.4)
    w.rep.rec(
        "52 ⑫-a 货主侧走**对客通道**：看得到待确认的是哪一版，且**看不到内部成本**"
        "（截图留存；页面本就不渲染 internal_total）",
        cpd.get("customerMode") is True and bool(cpd.get("customerSettlementId")),
        f"customerMode={cpd.get('customerMode')} 版本={cpd.get('customerVersionText')!r} "
        f"待确认={cpd.get('awaitingCustomer')}",
    )
    if not cpd.get("awaitingCustomer"):
        w.rep.not_run("52 ⑫-b 客户确认", "这一版当前不需要客户动作（可能已被历史轮次确认过）")
    else:
        w.c.scroll_into('[data-act="customer-confirm"]')
        w.c.tap('[data-act="customer-confirm"]')
        cpd2 = w.wait_data(
            lambda x: (
                "已接受" in str(x.get("settlementHint") or "") or x.get("awaitingCustomer") is False
            ),
            tries=40,
            gap=0.4,
        )
        w.rep.rec(
            "52 ⑫-b **客户本人**点了「确认这一版」⇒ 确认绑定在这一版上"
            "（旧确认替不了新版本过关是结构保证的）",
            cpd2.get("awaitingCustomer") is False,
            f"待确认={cpd2.get('awaitingCustomer')} 页内提示={str(cpd2.get('settlementHint'))[:120]!r}",
        )

    # ---- ⑬ 回经理侧：四条判据全过 ⇒ 已结清 ----
    # ⚠️ 同样不能 `login_as` 完就导航：应用侧登录是页面 onLoad 异步做的，
    #    而页面的 onLoad **只跑一次** —— 赶在 token 之前进财务页，它会按"未登录"
    #    取数失败并**停在错误态**（不会因为 token 后来到位而重取）⇒ 读到的是
    #    `financialStatusText=None`，看起来像"财务状态没算出来"。
    #    本仓既有就绪配方是 `open_workbench`（登录 → 身份卡 → 「我的」 → 轮询
    #    `showEntrust` 有值为止，㉟ 章也在复用），这里照用。
    if not w.open_workbench(CODE_OWNER, tag="52"):
        w.rep.not_run("52 ⑬ 财务状态复看", "回不到经理侧就绪态（登录/入口未就绪）")
    elif w.c.nav("navigateTo", f"/{finance_path}?assignment_id={aid}", finance_path):
        fpd = w.wait_data(lambda x: x.get("canViewInternal") is True, tries=40, gap=0.4)
        page_texts = [str((b or {}).get("text") or "") for b in (fpd.get("blockers") or [])]
        # 同时问一次**派生端点**：页面与派生两处必须同结论（页面自己算一份 blocker
        # 就是第二份判据，迟早与服务端漂移）。两条读的是**同一批事实**。
        api_state = api_get(f"/entrust/assignments/{aid}/financial-status", tok_mgr) or {}
        codes = [str((b or {}).get("code") or "") for b in (api_state.get("blockers") or [])]
        # ⚠️ 判据**只说我做过的这件事**：⑫-b 的客户确认是否真的让
        #    「客户尚未确认适用结算版本」这条未结成因消失。
        #    ⛔ 不断言"已结清" —— 本章夹具上还有未关闭的案件与未结余额
        #    （⑩ 的 blockers 就列着），把它们清掉属于**结案**那一步的演示（S4-b），
        #    在这里断言"已结清"会是外推，而且会逼着本章去补两个与第 10–11 步无关的动作。
        w.rep.rec(
            "52 ⑬ 客户确认之后，「客户尚未确认适用结算版本」这条未结成因**消失**；"
            "其余未结成因页面与派生端点两处同结论（不假装已结清）",
            "customer_not_confirmed" not in codes
            and not any("客户尚未确认" in t for t in page_texts),
            f"页面状态={fpd.get('financialStatusText')!r} 页面未结成因={page_texts} "
            f"／派生 status={api_state.get('financial_status')!r} codes={codes}",
        )
    else:
        w.rep.not_run("52 ⑬ 财务状态复看", "回不到财务页")

    w.shot("52-财务与结算")
    errs = w.new_errors(err_base)
    w.rep.rec(
        "52 ⑭ 本章运行期**无新增 console 报错**", not errs.strip(), errs.strip()[:300] or "无"
    )


def sec_53(w: Walker) -> None:
    """结案（合同 §6.4 / §10.1 第 12 步）：入口 → 五维清单 → 被拦 → 缺项可读。

    ⭐ 为什么值得单独一章：结案是**不可逆**动作，而 `complete` 的 409 只在
    **点下去之后**才说缺什么 ⇒ 必须证明"界面**先**把清单给出来"，而不是让人靠试。
    本章取三段证据：① 入口按状态与权限出现；② 清单与服务端**同结论**；
    ③ 点下去确实被拦、且拦的理由**逐条可读**。

    ⚠️ 诚实边界（都是被测事实，不是辩解）
    * **「齐备 ⇒ 结案成功」这一段本轮没有夹具**：它需要一张五维度都成立的委托
      （任务全部处置 ＋ 必需证据齐 ＋ 案件全部关闭 ＋ 结算已批准且**客户已确认** ＋
      余额结清），现成种子都停在前置不齐那一步 ⇒ 如实记 `NOT_RUN` 并说清缺什么。
      ⛔ 不用接口伪造一个"已齐备"的读数 —— 那等于把"结案能成功"写成"我以为能成功"。
    * 前置只用 `seed_entrust_demo` 那张 `claimed` 委托（**不绑**按需夹具，
      否则标准配方下本章直接变 `FAIL`，看起来像产品坏了）。
    * 本章**不改库**：点结案必然被服务端拒绝（前置不齐）⇒ 唯一的写入是幂等键，
      而被拒的请求会释放它。
    """
    print("\n-- 53 结案：第 12 步的入口与五维清单（S4-b 界面侧）--", flush=True)

    tok_mgr = (api_login(CODE_OWNER) or {}).get("access_token") or ""
    if not tok_mgr:
        w.rep.not_run("53 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return

    rows = (api_get("/entrust/assignments?view=org&size=50", tok_mgr) or {}).get("items") or []
    claimed = [r for r in rows if str((r or {}).get("status")) == "claimed"]
    claimed.sort(key=lambda r: int((r or {}).get("assignment_id") or 0))
    aid = prefer_anchor(str((claimed[0] or {}).get("assignment_id") or "") if claimed else "")
    w.rep.rec(
        "53 前置 · 队列里有一张已受理（claimed）委托 —— 本章自选一张，不绑按需夹具",
        bool(aid),
        f"aid={aid!r} claimed {len(claimed)} 张 / 队列共 {len(rows)} 张",
    )
    if not aid:
        w.rep.not_run("53 结案剧本", "队列里没有 claimed 委托 ⇒ 先铺 seed_entrust_demo")
        return

    srv = api_get(f"/entrust/assignments/{aid}/closure-readiness", tok_mgr) or {}
    srv_codes = [str((m or {}).get("code")) for m in (srv.get("missing") or [])]
    w.rep.rec(
        "53 ① 后端读端点可用：能结案的人拿到 missing[]（与结案命令**同一把锁**）",
        isinstance(srv.get("ready"), bool) and bool(srv_codes),
        f"ready={srv.get('ready')!r} missing={len(srv_codes)} codes={srv_codes[:6]}",
    )

    if not w.open_workbench(CODE_OWNER, tag="53"):
        w.rep.not_run("53 结案剧本", "未能以 seed-owner 进入经理工作台")
        return
    if not w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={aid}", DETAIL):
        w.rep.not_run("53 结案剧本", f"打不开委托详情页（aid={aid}）")
        return
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)

    has_entry = w.c.count('[data-act-complete-open="1"]') > 0
    mdd = w.c.page_data()
    closure = mdd.get("closure") or {}
    dims = closure.get("dimensions") or []
    w.rep.rec(
        "53 ② 详情页出现「结案」入口，且缺项清单**先于**点按钮就给出（不可逆动作要先看得见清单）",
        has_entry and bool(closure.get("summary")),
        f"入口={has_entry} canComplete={mdd.get('canComplete')!r} "
        f"summary={str(closure.get('summary'))[:70]!r}",
    )
    w.rep.rec(
        "53 ③ 五格恒出五格：界面按「五个维度」画，缺项为 0 的维度也在（否则像没人管）",
        len(dims) == 5 and all({"key", "label", "ok", "count"} <= set(d) for d in dims),
        "dimensions=" + ",".join(f"{d.get('label')}={d.get('count')}" for d in dims),
    )
    ui_codes = sorted({str((r or {}).get("code")) for d in dims for r in (d.get("rows") or [])})
    w.rep.rec(
        "53 ④ **两处同结论**：界面列出的缺项 code 与服务端读端点逐条一致（界面不自己编）",
        ui_codes == sorted(set(srv_codes)),
        f"界面={ui_codes[:6]} 服务端={sorted(set(srv_codes))[:6]}",
    )
    # 截图留证：「结案前先看得见清单」这一判据的**视觉**证据（与其它章节同一口径：
    # 读数之外要有一张能被人眼复核的图）。
    w.shot("53-结案五维清单")

    w.c.scroll_into('[data-act-complete-open="1"]')
    w.c.tap('[data-act-complete-open="1"]')
    time.sleep(0.5)
    opened = w.c.count('[data-act-complete-submit="1"]') > 0
    w.rep.rec(
        "53 ⑤ 「结案」是**页内确认条**（不是原生弹层 —— 弹层不在渲染树里，走查点不到它的确认键）",
        opened,
        f"确认条出现={opened}",
    )
    if opened:
        w.c.scroll_into('[data-act-complete-submit="1"]')
        w.c.tap('[data-act-complete-submit="1"]')
        time.sleep(2.5)
    mdd2 = w.c.page_data()
    hint = str(mdd2.get("completeHint") or "")
    w.rep.rec(
        "53 ⑥ 前置不齐时点结案 ⇒ 被服务端拦，且页面把**缺项**摆出来（不是一句「失败」）",
        ("还不能结案" in hint) and ("项" in hint),
        f"completeHint={hint[:130]!r}",
    )
    w.rep.rec(
        "53 ⑦ 被拦之后委托**没有**被改状态（不可逆动作不能半途生效）",
        str((mdd2.get("detail") or {}).get("status") or "") == "claimed",
        f"页面 detail.status={str((mdd2.get('detail') or {}).get('status'))!r}",
    )

    # ---- ④ 齐备 ⇒ 结案成功（**同一张委托连跑**：受理 → 任务 → 费用 → 结算 →
    #        客户确认 → 收付 → 结案，全部由 `seed_entrust_completion_ready.py` 经
    #        **真实业务命令**铺出；本章负责"齐备之后结案"这一段，并查验结案留痕）。
    #
    # ⚠️ 标题与夹具逐字一致（`seed_entrust_completion_ready.ASSIGNMENT_TITLE`）——
    #    改一处要改两处，否则这里会静默记 NOT_RUN。
    # 路径②：给了 --anchor 就直接用它，跳过"按标题找"
    ready_aid = WALK_ANCHOR
    org_rows = (
        []
        if WALK_ANCHOR
        else (api_get("/entrust/assignments?view=org&size=50", tok_mgr) or {}).get("items") or []
    )
    for r in org_rows:
        if str((r or {}).get("title") or "") == "演示委托·五维齐备（可结案）":
            ready_aid = str((r or {}).get("assignment_id") or "")
            break
    if not ready_aid:
        w.rep.not_run(
            "53 ⑧ 齐备 ⇒ 结案成功",
            "队列里没有「五维齐备」的委托 ⇒ 用 "
            "`--extra-seeds seed_entrust_completion_ready.py` 跑本章"
            "（那个夹具会自己断言 ready=True；⛔ 不在这里用接口伪造一个「已齐备」的读数）",
        )
        return
    if not w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={ready_aid}", DETAIL):
        w.rep.not_run("53 ⑧ 齐备 ⇒ 结案成功", f"打不开齐备委托的详情页（aid={ready_aid}）")
        return
    w.wait_data(lambda x: x.get("view") not in (None, "", "loading"), tries=60, gap=0.5)
    mdd3 = w.c.page_data()
    cl3 = mdd3.get("closure") or {}
    w.rep.rec(
        "53 ⑧-a 齐备的委托上，界面直接说「可以结案」（缺项清单为空）",
        cl3.get("ready") is True and not (cl3.get("missingTotal") or 0),
        f"ready={cl3.get('ready')!r} 缺项={cl3.get('missingTotal')!r} "
        f"summary={str(cl3.get('summary'))[:56]!r}",
    )
    w.shot("53-结案前（可结案）")
    w.c.scroll_into('[data-act-complete-open="1"]')
    w.c.tap('[data-act-complete-open="1"]')
    time.sleep(0.5)
    w.c.scroll_into('[data-act-complete-submit="1"]')
    w.c.tap('[data-act-complete-submit="1"]')
    d4 = w.wait_data(
        lambda x: str((x.get("detail") or {}).get("status")) == "completed", tries=60, gap=0.5
    )
    dd4 = d4.get("detail") or {}
    w.rep.rec(
        "53 ⑧-b **齐备 ⇒ 结案成功**：状态变 completed 且写下结案时间（不是「点了没反应」）",
        str(dd4.get("status")) == "completed" and bool(dd4.get("completedAt")),
        f"status={dd4.get('status')!r} completedAt={dd4.get('completedAt')!r}",
    )
    # 结案改变的是"后续还能做什么"的全部前置 ⇒ 页面重取之后，入口必须消失。
    entry_after = w.c.count('[data-act-complete-open="1"]')
    w.rep.rec(
        "53 ⑧-c 结案之后入口**消失**（已结案的单不该再显示「可以结案」）",
        entry_after == 0,
        f"结案入口数={entry_after}",
    )
    # ⚠️ `fields` 是页面 data 的**顶层**键（`setData({ fields })`），**不是** `detail` 的子键：
    # 首跑写成 `dd4.get("fields")` 得到空列表，报成"页面没产出这一行"——
    # 那是**断言写错**，不是页面缺陷（`detail.status` 与 `completedAt` 同一次读数里都对）。
    labels = [str((f or {}).get("label")) for f in (d4.get("fields") or [])]
    w.rep.rec(
        "53 ⑧-d 「保留历史」可查验：详情页多出「结案时间」一行"
        "（第 12 步 inspect retained history 的界面形态）",
        "结案时间" in labels,
        "字段行=" + ",".join(labels),
    )
    w.shot("53-结案后（留痕）")

    # ---- ⑤ 受控重开（S4-c / 设计 §5.5 Q2）：理由必填 ＋ 退回 claimed ＋ 入口复现 ----
    has_reopen = w.c.count('[data-act-reopen-open="1"]') > 0
    w.rep.rec(
        "53 ⑨-a 已结案的委托上出现「重开」入口（判据＝completed ∧ entrust:assignment:reopen）",
        has_reopen,
        f"入口={has_reopen} canReopen={d4.get('canReopen')!r}",
    )
    if not has_reopen:
        w.rep.not_run(
            "53 ⑨ 受控重开剧本", "重开入口未出现（权限或状态判据不满足）⇒ 后续三步无从演示"
        )
        return
    w.c.scroll_into('[data-act-reopen-open="1"]')
    w.c.tap('[data-act-reopen-open="1"]')
    time.sleep(0.6)
    # 负例：理由留空 ⇒ **页内拦住**（不发请求）
    w.c.scroll_into('[data-act-reopen-submit="1"]')
    w.c.tap('[data-act-reopen-submit="1"]')
    time.sleep(1.2)
    hint_empty = str(w.c.page_data().get("reopenHint") or "")
    w.rep.rec(
        "53 ⑨-b 理由为空 ⇒ 就地要求补理由（「受控」不是一句口号）",
        "理由" in hint_empty,
        f"reopenHint={hint_empty[:70]!r}",
    )
    w.shot("53-重开-理由必填")
    # 正例：填理由 → 提交 → 回到 claimed
    w.c.set_data({"reopenReason": "费用口径填错，需要纠正后重新结案"})
    time.sleep(0.5)
    w.c.tap('[data-act-reopen-submit="1"]')
    back = w.wait_data(
        lambda x: str((x.get("detail") or {}).get("status")) == "claimed", tries=60, gap=0.5
    )
    d9 = back.get("detail") or {}
    w.rep.rec(
        "53 ⑨-c **受控重开成功**：状态回到 claimed，且 `completed_at` 被清空",
        str(d9.get("status")) == "claimed" and not d9.get("completedAt"),
        f"status={d9.get('status')!r} completedAt={d9.get('completedAt')!r}",
    )
    entry_back = w.c.count('[data-act-complete-open="1"]')
    w.rep.rec(
        "53 ⑨-d 重开之后「结案」入口重新出现（它回到的正是可结案的那个状态）",
        entry_back > 0,
        f"结案入口数={entry_back}",
    )
    # ⚠️ 这一条只是**渲染层旁证**（页面不长出「撤回」入口）。Q2 的**硬证据**在 pytest 的
    #    `test_reopen_never_restores_cancel`（服务端拒绝）＋ 静态层**按模式扫描**
    #    （`verify_entrust_ui.js` 的四条「受控重开」）—— 三者互不替代。
    # ⚠️ **必须带正控**：`[data-act-cancel="1"]` 在本页**从来不存在**（那个命名只长在交易模块的
    #    orders.wxml，意思是"撤单"）⇒ 裸数它恒为 0，是**真空通过**。正控＝同一次读数里数一个
    #    此刻必然存在的锚点（`[data-act-complete-open="1"]`，上一条刚证明它 > 0），
    #    以证明「数得动」，让这条 0 有信息量。
    positive_control = w.c.count('[data-act-complete-open="1"]')
    cancel_entries = w.c.count('[data-act-cancel="1"]')
    w.rep.rec(
        "53 ⑨-e Q2 边界（渲染层旁证）：页面不出现「撤回」入口"
        "（带正控：`complete-open` 同时数得到 ⇒ 这条 0 不是「数不动」）",
        cancel_entries == 0 and positive_control > 0,
        f"撤回入口数={cancel_entries} 正控 complete-open={positive_control}"
        f"（硬证据＝pytest test_reopen_never_restores_cancel ＋ 静态扫描 verify_entrust_ui.js）",
    )
    w.shot("53-重开后（退回已受理）")


def sec_54(w: Walker) -> None:
    """**十三步的产物链（读侧断言）**：每一步的产物在不在、归属对不对、读侧看不看得见。

    ⛔ 本章**不声称**"13 步在同一张委托上连跑通过" —— 那是
    `docs/entrust/S4-b-结案命令切片.md` §7.1 里仍没做的那件事（需要给走查一个
    "本章用这张委托"的入口）。本章回答的是**它的前提**，而且此前从来没人回答过：

        链上每一环的产物是否真实存在、是否挂在**某张**委托上、读侧能否看见它。

    为什么值得单独一章：13 步此前是 13 份**互不相干**的读数 —— 没人知道
    "第 5 步的候选还在不在""第 7 步的合同是不是还挂着 assignment_id"。
    本章按**步序**串成一张清单，缺哪一步就点名到步。

    判据纪律（三条，别放松）：
    * **只读**（⛔ 不写库）；
    * **归属是必查项**：产物挂在别的委托上 ＝ 这一步在这张单上没有产物
      （本仓踩过"成果归属不到单张委托"）；
    * 缺产物记 `NOT_RUN` 并说清**缺哪一步、缺什么**（夹具没铺 / 该步没跑），
      ⛔ 不把"没有"读成"做到"。
    """
    print("\n-- 54 十三步的产物链（读侧）--", flush=True)

    tok_mgr = (api_login(CODE_OWNER) or {}).get("access_token") or ""
    if not tok_mgr:
        w.rep.not_run("54 全部断言", "拿不到 seed-owner 的 token（后端未起或种子未铺）")
        return

    def _items(payload):
        """宽容取值：列表端点的包裹键在不同模块里叫法不同（`items` / `artifacts` …）。

        ⚠️ 取不到就回空表并**把键名打进读数** —— 首跑即能看出真实结构，
        而不是以"空"的面目失败（那会像"这一步没做"）。
        """
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for k in ("items", "artifacts", "releases", "versions", "rows"):
                v = payload.get(k)
                if isinstance(v, list):
                    return v
        return []

    rows = (api_get("/entrust/assignments?view=org&size=50", tok_mgr) or {}).get("items") or []

    def pick(needle: str) -> str:
        for r in rows:
            if needle in str((r or {}).get("title") or ""):
                return str((r or {}).get("assignment_id") or "")
        return ""

    #: 第 12 步的载体（`--extra-seeds seed_entrust_completion_ready.py` 铺）与
    #: 第 4–8 步的载体（`seed_entrust_canonical.py` 铺）。两个都是**按需夹具**
    #: ⇒ 缺了就记 NOT_RUN 并点名，⛔ 不当成回归。
    ready_aid = prefer_anchor(pick("五维齐备"))
    canon_aid = prefer_anchor(pick("canonical"))
    any_claimed = [
        str((r or {}).get("assignment_id"))
        for r in rows
        if str((r or {}).get("status")) in ("claimed", "completed")
    ]

    # ① 第 1 步（创建/提交/受理）：委托本体 ＋ 授权可唯一定位
    ctx = api_get(f"/entrust/assignments/{ready_aid}/session-context", tok_mgr) if ready_aid else {}
    w.rep.rec(
        "54 ① 第 1 步（受理）：队列里有已受理的委托，且授权能被**唯一定位**",
        bool(any_claimed) and bool((ctx or {}).get("entrustment_id")),
        f"claimed/completed {len(any_claimed)} 张 queue={len(rows)} 张"
        f" ready_aid={ready_aid or '—'} entrustment_id={(ctx or {}).get('entrustment_id')!r}",
    )

    # ② 第 2–3 步（报价成果与版本）：成果存在且**挂在本单上**
    #
    # ⚠️ 载体是 **`seed_entrust_contract_flow.py`**（它把成果铺在 canonical 那张单上），
    #    ⛔ 不是 `seed_entrust_canonical.py` —— 后者只建委托与航段，**不建任何成果**
    #    （源码里 `artifact` 出现 0 次）。首跑就是把这两者记混了：漏铺 contract_flow
    #    ⇒ 成果 0 条，而断言报了 FAIL。⇒ 现在按"缺按需夹具"记 `NOT_RUN` **并点名**，
    #    ⛔ 不写成 FAIL（"缺夹具"与"读不出来"是两件事，混了会把夹具问题读成产品缺陷）。
    arts = (
        _items(api_get(f"/entrust/assignments/{canon_aid}/artifacts", tok_mgr)) if canon_aid else []
    )
    kinds = sorted(
        {str((a or {}).get("artifact_type") or (a or {}).get("kind") or "?") for a in arts}
    )
    if not arts:
        w.rep.not_run(
            "54 ② 第 2–3 步（报价成果与版本）",
            f"canonical 单（{canon_aid or '—'}）上读不到成果 ⇒ 缺按需夹具 "
            "`seed_entrust_contract_flow.py`（成果的载体是它，不是 canonical 种子）",
        )
    else:
        w.rep.rec(
            "54 ② 第 2–3 步（报价成果与版本）：canonical 单上有成果，且每条都带归属",
            all(int((a or {}).get("assignment_id") or 0) == int(canon_aid) for a in arts),
            f"canon_aid={canon_aid} 成果 {len(arts)} 条 kinds={kinds[:6]}"
            f" 归属齐={all(int((a or {}).get('assignment_id') or 0) == int(canon_aid) for a in arts)}",
        )

    # ③ 第 4 步（计划：航段 ＋ 必需任务前置）
    #
    # ⚠️ 分两半，载体不同（首跑把两件事混成一条，才会"因为没任务"整条红）：
    #   a) **航段与前置字段**读 **canonical 载体**的 `plan`（它由 canonical 种子铺）；
    #   b) **任务**读 `/entrust/tasks`（任务的权威读法；`plan.tasks` 只是投影）——
    #      任务由 `seed_entrust_demo.py` 或 ㊳ 章产生，**本章不代替它造**：
    #      一张带任务的单都没有 ⇒ 记 `NOT_RUN` **并点名**，⛔ 不把"没有产物"读成"读不出来"。
    plan = api_get(f"/entrust/assignments/{canon_aid}/plan", tok_mgr) if canon_aid else {}
    legs = _items((plan or {}).get("legs"))
    w.rep.rec(
        "54 ③-a 第 4 步（计划）：canonical 单上航段可读，且前置是响应里的一条事实",
        bool(legs) and "task_prerequisites_total" in (plan or {}),
        f"canon_aid={canon_aid or '—'} legs={len(legs)}"
        f" 前置总数={(plan or {}).get('task_prerequisites_total')!r}"
        f" 截断={(plan or {}).get('task_prerequisites_truncated')!r}",
    )
    task_aid = ""
    task_rows = []
    for probe_aid in [canon_aid, *[str((r or {}).get("assignment_id") or "") for r in rows]]:
        if not probe_aid:
            continue
        got_tasks = (
            api_get(f"/entrust/tasks?assignment_id={probe_aid}&size=50", tok_mgr) or {}
        ).get("items") or []
        if got_tasks:
            task_aid, task_rows = probe_aid, got_tasks
            break
    if not task_rows:
        w.rep.not_run(
            "54 ③-b 第 4 步（任务与前置）",
            "队列里没有任何一张委托带任务 ⇒ 缺载体（任务由 `seed_entrust_demo.py` 铺，"
            "或由 ㊳ 章在界面上建）；⛔ 不是回归：**没有产物**与**产物读不出来**是两件事",
        )
    else:
        w.rep.rec(
            "54 ③-b 第 4 步（任务与前置）：任务可读且**挂在它自己那张单上**",
            all(int((t or {}).get("assignment_id") or 0) == int(task_aid) for t in task_rows),
            f"载体=#{task_aid} 任务 {len(task_rows)} 条"
            f" 归属齐={all(int((t or {}).get('assignment_id') or 0) == int(task_aid) for t in task_rows)}",
        )

    # ④ 第 5 步（比价：≥2 条可比候选）
    cands = (
        _items(api_get(f"/entrust/assignments/{canon_aid}/capacity-candidates", tok_mgr))
        if canon_aid
        else []
    )
    w.rep.rec(
        "54 ④ 第 5 步（比价）：canonical 单上有 **≥2 条**可比候选（BP-03 第 2 条）",
        len(cands) >= 2,
        f"候选 {len(cands)} 条"
        + (f" 首个={str((cands[0] or {}).get('candidate_id'))!r}" if cands else ""),
    )

    # ⑤ 第 8 步（货量变更历史）—— 只在㊿ 章跑过（或经接口改过货量）之后才非空
    qcs = (
        _items(api_get(f"/entrust/assignments/{canon_aid}/quantity-changes", tok_mgr))
        if canon_aid
        else []
    )
    if qcs:
        w.rep.rec(
            "54 ⑤ 第 8 步（经审批的货量变更）：变更历史存在、挂在**本单**上，且新旧值可读",
            bool(qcs)
            and all(int((q or {}).get("assignment_id") or 0) == int(canon_aid) for q in qcs)
            # ⭐ 正控：新值文案**必定**有值（旧值允许是 None ＝"变更前未知"，见
            #    `project_quantity_change` 的 docstring）⇒ 用它挡"键名读错却静默 PASS"。
            #    2026-09-19 实测本条曾读成 `首条='None'→'None'` 而**仍然判 PASS**，
            #    因为当时只断言了条数 —— 读数全空却通过，是**真空通过**。
            and bool((qcs[0] or {}).get("new_quantity_text")),
            f"变更 {len(qcs)} 条"
            f" 首条={str((qcs[0] or {}).get('old_quantity_text'))!r}→"
            f"{str((qcs[0] or {}).get('new_quantity_text'))!r}"
            f" 挂本单={all(int((q or {}).get('assignment_id') or 0) == int(canon_aid) for q in qcs)}",
        )
    else:
        w.rep.not_run(
            "54 ⑤ 第 8 步（经审批的货量变更）",
            "canonical 单上还没有变更历史 ⇒ 该步还没跑过（㊿ 章会真做一次变更）。"
            "⛔ 不是回归：**没有产物**与**产物不对**是两件事。"
            "指引：先跑 ㊿ 章（它真做一次 800→950 的审批变更）本章即应转为 PASS，"
            "一条命令连跑即可验证 —— `--section 50,54`（㊿ 会往同一张 canonical 单上写）",
        )

    # ⑥ 第 10 步（费用行）
    charges = (
        _items(api_get(f"/entrust/assignments/{ready_aid}/charges", tok_mgr)) if ready_aid else []
    )
    w.rep.rec(
        "54 ⑥ 第 10 步（费用行）：齐备委托上有已确认的费用（缺了它结算无从谈起）",
        bool(charges),
        f"ready_aid={ready_aid or '—'} 费用 {len(charges)} 条"
        + (f" 状态={str((charges[0] or {}).get('status'))!r}" if charges else ""),
    )

    # ⑦ 第 11 步（结算版本 ＋ 客户确认 ＋ 收付 ⇒ 派生结清）
    stl = api_get(f"/entrust/assignments/{ready_aid}/settlements", tok_mgr) if ready_aid else {}
    versions = _items((stl or {}).get("items") or (stl or {}).get("versions"))
    fin = (
        api_get(f"/entrust/assignments/{ready_aid}/financial-status", tok_mgr) if ready_aid else {}
    )
    w.rep.rec(
        "54 ⑦ 第 11 步（结算与收付）：有结算版本，且派生口径是**已结清**",
        bool(versions) and str((fin or {}).get("financial_status")) == "settled",
        f"版本 {len(versions)} 个 financial_status={(fin or {}).get('financial_status')!r}"
        f" blockers={len((fin or {}).get('blockers') or [])}",
    )

    # ⑧ 第 12 步（结案齐备度）—— 这一环的前置是上面每一环
    cr = (
        api_get(f"/entrust/assignments/{ready_aid}/closure-readiness", tok_mgr) if ready_aid else {}
    )
    w.rep.rec(
        "54 ⑧ 第 12 步（结案齐备）：五维度全过（它是**前面每一环**的汇总，不是并列的一项）",
        (cr or {}).get("ready") is True,
        f"ready={(cr or {}).get('ready')!r} 缺项={len((cr or {}).get('missing') or [])}"
        f" 逐维度={(cr or {}).get('missing_by_dimension')!r}",
    )

    # ⑨ 第 13 步（重进可见）：**整页重取之后**仍看得到同一张单（不是内存里的残留）
    if ready_aid:
        w.c.nav("navigateTo", f"/{DETAIL}?assignment_id={ready_aid}", DETAIL)
        got = w.wait_data(
            lambda x: x.get("view") not in (None, "", "loading") and (x.get("detail") or {}),
            tries=60,
            gap=0.5,
        )
        det = got.get("detail") or {}
        w.rep.rec(
            "54 ⑨ 第 13 步（重进可见）：详情页重取之后仍读到同一张委托（含状态与版本）",
            str(det.get("assignmentId")) == str(ready_aid) and bool(det.get("status")),
            f"页面 assignmentId={det.get('assignmentId')!r} status={det.get('status')!r}"
            f" revision={det.get('revision')!r}",
        )
    else:
        w.rep.not_run("54 ⑨ 第 13 步（重进可见）", "没有齐备委托可用来做重进验证")


SECTIONS = {
    "smoke": sec_smoke,
    "43": sec_43,
    "44": sec_44,
    "45": sec_45,
    "46": sec_46,
    "47": sec_47,
    "48": sec_48,
    "49": sec_49,
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
    "31": sec_31,
    "32": sec_32,
    "33": sec_33,
    "34": sec_34,
    "35": sec_35,
    "36": sec_36,
    "37": sec_37,
    "38": sec_38,
    "39": sec_39,
    "40": sec_40,
    "41": sec_41,
    "42": sec_42,
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
    "50": sec_50,
    "51": sec_51,
    "52": sec_52,
    "53": sec_53,
    #: 54 = 十三步的产物链（**读侧**）：只断言"每一步的产物在不在、归属对不对"，
    #: ⛔ 它不是"13 步在同一张委托上连跑"的证明（后者见 S4-b 文档 §7.1）。
    "54": sec_54,
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
    # A2 五之四（ENT-041）：㉛ 真写造 approved 形态 → 应用；㉜ 依赖 ㉛ 留下的复核项。
    # ⚠️ 31 → 32 是一条链（32 从 REST 复原核项指向的成果），单跑 32 会明确失败。
    "31",
    "32",
    # DR-0011：栈深的**运行期**那一半（声明链深 ≤ 预算已在 CI 里证过）。
    # ⚠️ 会压栈到平台拒绝（按 URL 直进），最后 reLaunch 清栈；独立于其它章。
    "33",
    # S1 出口屏（UI-07）：客户受理 → 建草稿 → 提交的**全链真实点击**。
    # ⚠️ 放最后：它会真写委托（`seed-shipper` 名下多出两张 submitted），
    #    若排在前面会让后面按条数断言的章节变脆。
    "34",
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
    # S1 出口判据 ②③④ 的**设备侧**取证：真写两张委托（甲组织）并在界面上受理一张，
    # 再换「仅乙组织」身份验越权负例（判据 ④）。
    # ⚠️ 放**全量序列最末**：它会给甲组织队列添两张 submitted、其中一张转 claimed，
    #    排在前面会让后面按条数/按状态断言的章节变脆（"34" 也真写委托，但它在
    #    "4b"～"14" 之前；本节的增量写在它们之后，不再叠加污染）。
    "35",
    # ㊱ 受理入口判据（D-4）：**零写入** —— 不提交、也不受理任何委托，只观察受理
    #    入口的可见性，并调 API 取 403。但它会切换「上次选中的组织」这个 Storage
    #    键（`ORG_STORAGE_KEY`）⇒ 同样排在末尾，免得把后面按组织断言的章节搅乱。
    "36",
    # ㊲ 受理入口的**权限撤销**边界（D-4 §5）：撤权 ⇒ 403 ⇒ 界面刷新。
    # ⚠️ 本章**会改库**（把 multi 在甲组织的角色**临时**改成 member）⇒ 必须排在 ㊱ 之后，
    #    且**自带 finally 还原**：它不还原就会把 ㊱ 章的前置（multi 在甲是 manager）弄坏。
    "37",
    # S1 收尾（2026-09-16）：详情页两个关键路径改成页内 DOM 之后的**真机验收**。
    # ⚠️ 必须排在 ㊱ 之后：本章会**受理掉**甲组织那张样本单（㊱ 要求它仍是 submitted）。
    "38",
    # ㊴ 详情页受理入口的「**被抢认领**」路径（D-4 §5 第四条 · 图 2 第 4 行）。
    # ⚠️ 必须排在 ㊳ 之后：甲组织种子里只有**一张**待受理样本单，㊳ 会受理掉它；
    #    本章因此经 API 自建一张载体单（不新造种子），两章各用各的单。
    #    本章**不依赖**其它章节留下的状态，单跑也能成立（前置只是种子里的身份与组织）。
    "39",
    # ㊵ 详情页受理入口的「**权限撤销**」路径（D-4 §5 第四条）：403 ⇒ 刷新。
    # ⚠️ 与 ㊴ 同入口、另一分支。撤权对象必须选**乙组织**（该组织 claim 只来自角色），
    #    与 ㊴ 的选址**恰好相反** —— 理由见 sec_40 docstring。
    #    本章**会改库**（成员角色），自带 `finally` 还原；排在 ㊴ 之后，单跑也成立。
    "40",
    # ㊶ S1 工作项 5「我的委托」（货主侧状态屏）＋ 出口判据① 里「重进后仍在列表 / 详情
    #    可见」那一半的**独立**断言。
    # ⚠️ 本章**零副作用**（只经 API 建两张载体单，不改任何已存在的单据、不改库），
    #    且不依赖其它章节留下的状态 ⇒ 单跑也成立。放在最末是因为它会给
    #    `seed-shipper-orgpicker` 名下多出两张单（一张 submitted、一张 draft），
    #    排在前面会让按条数断言的章节变脆。
    "41",
    # ㊷ DR-0018 的 A.P-2（触底分页探针）：驱动原生 `pageScrollTo` 验第 2 页真的进来。
    # ⚠️ 与 ㊶ 同理放在最末：本章会经 API 建 25 张**草稿**单（同样挂在
    #    `seed-shipper-orgpicker` 名下），排在前面会让按条数 / 按页长断言的章节变脆。
    #    零改库、零改已存在的单据。**依赖**：页面须有 `onReachBottom`（本切片补的）。
    "42",
    # ㊸ 主演示第 1–3 步（合同 §10.1）——HO 0917-3 执行顺序点名的"运行取证"。
    # ⚠️ 放**全量序列最末**：它会真写一张委托并把这张单受理掉，还会上传附件、
    #    建一份成果并把它推进到 v2 生效 —— 污染面比 ㉞/㊶/㊷ 更大，
    #    排在前面会让后面按条数 / 按状态断言的章节一起变脆。
    "43",
    # ㊹ 主演示第 6 步（合同 §10.1）——`Release the offer; customer accepts its exact revision`。
    # ⚠️ **依赖 ㊸**：本章要发布的载体是 ㊸ 建的那张单上的成果，且 ㊸ 会把成果推进到
    #    下一个未发布版本。⇒ 单跑会在前置处明确 `NOT_RUN`（说清缺什么），不自造数据；
    #    正式取证请用 `--section 43,44`。
    # ⚠️ 副作用：会真发布一版（客户白名单冻结在发布记录上）并由客户**真接受**该版本
    #    ⇒ 这条发布此后既不可撤回也不可再响应，重跑会因"没有未发布版本"而 `NOT_RUN`
    #    （正确行为，不是缺陷）。排在 ㊸ 之后、全量序列最末。
    "44",
    # ㊺ 运力确认闭环（BP-03 第 3 条 / 合同 §10.1 第 5 步）——设备侧运行取证。
    # ⚠️ **自足**：只用 `seed_entrust_demo.py` 铺的演示组织与委托（`claimed`），
    #    刻意**不依赖 ㊸** —— 依赖一条长链会让本章的失败与 ㊸ 的失败混在一起，
    #    而两者的处置完全不同。故可单跑：`--section 45`。
    # ⚠️ 副作用：会真登记 2 条候选并确认其中 1 条（写 1 份采购确认成果 + 1 行确认
    #    + 4 行判定）。临时库跑完即弃；共享库上重跑会因候选累积而仍成立（断言按
    #    本次唯一的承运人名定位），但会在库里留下痕迹。
    "45",
    # ㊻ 运输计划（合同 §10.1 第 4 步）——设备侧运行取证。
    # ⚠️ **自足**：只依赖 `seed_entrust_demo.py` 的组织与委托 `演示委托·工作台样本`，
    #    刻意不依赖其它章节 ⇒ 可单跑：`--section 46`。
    # ⚠️ 本章的写操作**经 API**（与 ㊼ 章的分工）；界面侧只验渲染。
    #    曾把这句写成"写侧界面不存在 ⇒ LIMITATION"，写侧界面已于 §7.24 落地 ⇒ 已订正。
    # ⚠️ 副作用：会真建 1 段航段 + 1 条版本历史（`seq=88`，种子不占用）。临时库跑完即弃。
    "46",
    # ㊼ 航段命令的**写侧界面**（合同 §10.1 第 4 步）——设备侧运行取证。
    # ⚠️ **解除 ㊻ 的 `LIMITATION`**：㊻ 的写操作全部经 API（当时写侧界面不存在），
    #    本章把"建段 / 改段 / 版本历史"全部**经界面**走一遍。
    # ⚠️ **自足**：只依赖 `seed_entrust_demo.py` 的组织与 `演示委托·工作台样本`，
    #    段由本章自己经界面建出来（种子不含航段夹具）⇒ 可单跑：`--section 47`。
    # ⚠️ 副作用：会真建 1 段 + 改 1 次（2 条版本历史）；`seq` 取"现有最大序号 + 1"
    #    ⇒ 共享库上重跑仍成立（不撞号），但会留下痕迹。
    "47",
    # ㊽ **货主本人**经界面建段（第 4 步判据的另一半：参与方，不只是经理）。
    # ⚠️ 与 ㊼ 的分工：㊼ 的身份是经理，只证明了"组织成员能建"；本章用 canonical
    #    委托的货主 `seed-shipper` 在**真机**上点出一段 —— "货主也能建"此前只有 e2e。
    # ⚠️ **自足**：只依赖 `seed_entrust_canonical.py` 铺的那张委托 ⇒ 可单跑：`--section 48`。
    # ⚠️ 副作用：给 canonical 委托加 1 段（`seq` 取现有最大 +1 ⇒ 重跑不撞号）。
    "48",
    # ㊾ 合同派生 + 签署证据的**界面取证**（合同 §10.1 第 7 步 / D1-08）。
    # ⚠️ **依赖夹具**：`python scripts/seed_entrust_contract_flow.py`（一条客户已接受的
    #    对客报价发布）。没跑它 ⇒ 整章 `NOT_RUN`（**不假绿**）。
    # ⚠️ 排在 ㊽ 之后：同一张 canonical 委托，㊽ 加的那段会出现在本章派生出的
    #    `route_scope` 条款里 —— 这是"派生用的是派生那一刻的航段"的真实形态，不是污染。
    # ⚠️ 副作用：派生一份合同 + 记一条证据。已派生过 ⇒ 读回已有那份（不重复派生）。
    "49",
    "50",
    # 51 独立于 49/50：它自带载体（AG-02 经接口产出 customer_quote），不依赖前面几章留下的状态。
    "51",
    "52",
    # 53 = 结案 ＋ 受控重开（合同 §10.1 第 12 步）。
    # ⛔ 此前**不在本表**里 ⇒ `--section all` 会**静默**跳过整整一章（第 12 步的设备证据
    #    只剩"单独跑那一次"），而且 `--help` 里也列不到它 —— 属于本项目最忌讳的
    #    "静默漏项"：读数不会变红，只是少了一章。
    # ⚠️ 「齐备 ⇒ 结案成功」那一段要夹具 `seed_entrust_completion_ready.py`；标准配方下
    #    它记 `NOT_RUN` 并点名夹具（**不假绿**），其余断言照跑。
    "53",
    # 54 = 十三步的**产物链（读侧）**：只断言每一步的产物在不在、归属对不对。
    # ⛔ 它不是"13 步在同一张委托上连跑"的证明（见 `S4-b-结案命令切片.md` §7.1）。
    # ⚠️ ⑤ 需要 ㊿ 章先跑过 —— 本表里 50 排在它前面，顺序不可打乱。
    "54",
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
    #: 落进 `summary.json`：事后能回答「这次明细是哪条命令、什么时候跑的」。
    #: 明细目录本身带时间戳、不会被覆盖；这里补的是**可检索的元数据** ——
    #: 本轮对比「首轮 357 项 / 复跑 361 项」时，靠的就是两轮的 `summary.json`，
    #: 而没有时间与命令行就只能靠目录名猜。
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

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

    # HO D1：**只统计本轮的日志增量**。IDE 可能被复用（`--skip-ide`），
    # 其上 console 是累计的 ⇒ 先取基线，再把增量按「开窗阶段 / 业务阶段」切开，
    # 因为白名单**只对开窗阶段生效**。
    console_base = _errors_safe(client)
    if console_base is None:
        rep.review_required(
            "console 基线采集失败（采集不到 ⇒ 不能声称 console 无错误）",
            "client.errors() 抛错；本轮 console 结论为「待归因」，不计入通过",
        )
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
    # 开窗阶段结束的切点：此后产生的 console 条目都算「业务阶段」，**一律不豁免**。
    console_at_open = _errors_safe(client)

    for name in wanted:
        try:
            SECTIONS[name](w)
        except Exception as exc:  # noqa: BLE001
            # 单章异常不打断整轮：记录为失败，继续跑后面的章节
            rep.rec(f"章节 {name} 执行异常", False, repr(exc)[:300])

    print("\n================ 汇总 ================", flush=True)
    rep.print_summary()

    # ---------------------------------------------------------------- console 门禁
    # D1 = B：**未豁免的 error 阻止整轮判为通过**。
    console_final = _errors_safe(client)
    if console_final is None:
        collect_failed = True
        open_delta = biz_delta = ""
    else:
        collect_failed = console_base is None
        open_delta = _console_delta(console_at_open, console_base)
        biz_delta = _console_delta(console_final, console_at_open)

    open_waived, open_unwaived = classify_console(open_delta, CONSOLE_PHASE_OPEN)
    biz_waived, biz_unwaived = classify_console(biz_delta, CONSOLE_PHASE_BIZ)

    print(
        f"[运行期 console·本轮增量] 开窗阶段：豁免 {len(open_waived)} / 未豁免 {len(open_unwaived)}"
        f" ｜ 业务阶段：豁免 {len(biz_waived)}(按阶段规则恒为 0) / 未豁免 {len(biz_unwaived)}"
        f" ｜ 基线采集 {'失败' if collect_failed else '正常'}",
        flush=True,
    )
    for label, bucket in (
        ("console·开窗豁免", open_waived),
        ("console·开窗未豁免", open_unwaived),
        ("console·业务阶段未豁免", biz_unwaived),
    ):
        if bucket:
            print(
                f"[{label}] " + " ｜ ".join(x.replace("\n", " ")[:300] for x in bucket),
                flush=True,
            )
    if not (open_delta or biz_delta) and not collect_failed:
        print("[运行期 console error] 本轮增量：(无)", flush=True)
    print(
        "[console·原文（本轮增量）]",
        (open_delta + biz_delta)[:900] or "(空)",
        flush=True,
    )

    unattributed = open_unwaived + biz_unwaived
    if collect_failed:
        pass  # 上面已记 review_required
    elif unattributed:
        rep.review_required(
            f"运行期 console 有 {len(unattributed)} 条未豁免 error（D1：阻止整轮判为通过）",
            " ".join(x.replace("\n", " ")[:160] for x in unattributed[:3]),
        )
    print("[截图目录]", shots, flush=True)

    # ---------------------------------------------------------------- 落盘
    verdict = rep.verdict()
    tally = rep.tally()
    rollup = rep.rollup()
    prev_runs = _previous_runs(shots, wanted)
    print(
        f"[重跑关联] 同章节历史运行 {len(prev_runs)} 次（首次结果**保留不覆盖**）："
        + (
            "；".join(f"{(p.get('finishedAt') or '?')}={p.get('verdict')}" for p in prev_runs)
            or "无"
        ),
        flush=True,
    )

    with open(os.path.join(shots, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "sections": wanted,
                "project": os.path.abspath(args.project),
                "startedAt": started_at,
                "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "argv": sys.argv,
                "verdict": verdict,
                "tally": tally,
                "rollup": rollup,
                "passed": tally[Reporter.PASS],
                "notPassed": len(rep.results) - tally[Reporter.PASS],
                "attempt": len(prev_runs) + 1,
                "previousRuns": prev_runs,
                "results": rep.results,
                "console": {
                    "baselineCaptured": console_base is not None,
                    "collectFailed": collect_failed,
                    "openPhaseWaived": open_waived,
                    "openPhaseUnwaived": open_unwaived,
                    "bizPhaseUnwaived": biz_unwaived,
                    "whitelist": [dict(w) for w in ENV_NOISE_WHITELIST],
                },
                "consoleError": open_delta + biz_delta,
                "consoleErrorDetected": bool((open_delta + biz_delta).strip()),
                "consoleNoiseCount": len(open_waived),
                "consoleOtherCount": len(open_unwaived) + len(biz_unwaived),
                "shots": shots,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )

    print(
        "RESULT:",
        verdict,
        "（"
        + " / ".join(
            f"{k}={tally[k]}"
            for k in (
                Reporter.FAIL,
                Reporter.ENV_BLOCKED,
                Reporter.REVIEW_REQUIRED,
                Reporter.NOT_RUN,
                Reporter.LIMITATION,
            )
            if tally[k]
        )
        + ("）" if any(tally[k] for k in Reporter.NON_PASS) else "全为 PASS）"),
        flush=True,
    )
    # 只有**全部为 PASS** 才算通过：FAIL / 环境阻塞 / 待归因 / 未执行 一律非零退出。
    return 0 if verdict == Reporter.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
