"""`wechatide` CLI 的 Python 直调客户端 —— 小程序真机走查的驱动层。

背景（DR-0009）
---------------
微信开发者工具 3.17.3 起，官方自动化入口是 IDE 自带的 `wechatide` 工具链
（`automation_*` / `simulator_screenshot` / `get_simulator_console` 等）；
旧的 `miniprogram-automator`（ws 协议）在本版本 IDE 上**已不再被服务**。

为什么不直接调 `wechatide.cmd`
-------------------------------
`.cmd` 只能由 cmd.exe 执行，而某些主机安全策略会拒绝从 shell 调 `cmd.exe`。
本模块复刻 `wechatide.cmd` 的三步语义，直接用 IDE 内的 Electron 当 Node 运行时：

1. 在 IDE 目录里取**最后一个** >50 MB 且不在排除名单里的 `.exe`（Electron 本体）。
   注意 `.cmd` 里的 `for %%F in (*.exe) do set ELECTRON=...` 是不断覆盖，取的是**最后**一个。
2. CLI 入口 = `resources/app.asar.unpacked/js/common/cli/skill-index.js`。
3. `ELECTRON_RUN_AS_NODE=1 <electron> -e <BOOTSTRAP> <CLI> -c <clientName> <tool> …`。

官方硬要求：**不得在沙箱中运行 `wechatide`** —— 调用方须以非沙箱方式执行。

前置条件
--------
* IDE 已启动并打开过本项目（`cli.bat open --project <miniapp>`，或用 `open_window()`）。
* 已完成**一次**人工授权（`wechatide auth -c WorkBuddy`，在 IDE 内点击批准）。
  授权是**持久**的：只有 IDE 退出登录或撤销授权后才需重来（实测 `tokenRequired: false`）。

用法
----
    from wechatide_client import Client

    c = Client(project=r"E:\\pinglu-didi\\miniapp")
    c.require_ready()          # 前置自检，不通就抛异常并给出可照做的指引
    c.open_window()
    print(c.page_data())
    c.tap('[data-org="2"]')
    c.screenshot(r"artifacts/step2.jpg")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time

DEFAULT_CLIENT = "WorkBuddy"

# CLI 入口相对 IDE 安装目录的路径（实测 3.17.3）
CLI_REL = os.path.join("resources", "app.asar.unpacked", "js", "common", "cli", "skill-index.js")

# IDE 安装目录候选：环境变量优先，其次常见安装位置
IDE_CANDIDATES = (
    os.environ.get("WECHAT_DEVTOOLS_HOME", ""),
    r"D:\Program Files (x86)\Tencent\微信web开发者工具",
    r"C:\Program Files (x86)\Tencent\微信web开发者工具",
    r"C:\Program Files\Tencent\微信web开发者工具",
    os.path.expanduser(r"~\AppData\Local\Programs\微信web开发者工具"),
)

# 这些是 IDE 自带的辅助进程，不是 Electron 本体
EXCLUDE_EXE = {
    "node.exe",
    "node-18.exe",
    "wxfilewatcher.exe",
    "wxfilewatcher_x64.exe",
    "notification_helper.exe",
    "wechatdevtools.exe",
}

# 供 CLI 把自己当成「带 --electron 参数的 Node 脚本」启动
_BOOTSTRAP = (
    "const e=process.argv[1],a=process.argv.slice(2)"
    ".filter(function(x){return x!=='--electron'});"
    "if(!process.env.cwd)process.env.cwd=process.cwd();"
    "process.argv=[process.execPath,e,'--electron'].concat(a);require(e)"
)

#: **裸属性选择器**（`[data-x]`，只判"有这个属性"、不带值）。
#: 微信开发者工具的自动化通道**不支持它**，而且是**静默回 0**：
#: 2026-09-17 实测同一页同一语义三条通道 ——
#:   类选择器 `.plan-leg` → **1**、带值属性 `[data-plan-leg="88"]` → **1**、
#:   裸属性 `[data-plan-leg]` → **0**（元素明明在渲染树里）。
#: 这与 `count` 文档里 `createSelectorQuery` 的"假 0"同类，但更坏：
#:   · 写成 `count('[data-x]') >= 1` ⇒ **假红**（看起来像模板没渲染）；
#:   · 写成 `count('[data-x]') == 0`（断言"不该有"）⇒ **假绿**，真出问题也照样绿。
#: ⇒ 与其在每章走查里反复踩，不如在入口**响亮地拒绝**：能改写成带值属性或类名，
#: 就说明本来也不需要"裸属性"这种筛法。
_BARE_ATTR_SELECTOR_RE = re.compile(r"^\[\s*[A-Za-z_][\w-]*\s*\]$")


def _reject_bare_attr(selector: str) -> None:
    """裸属性选择器 ⇒ 直接抛（带改写指引），**不**让它静默回 0。

    要看"这个属性存在与否"的证据（例如必须把这条工具链限制留在走查输出里），
    显式用 `query_selector_all`（低层、无守卫）并写明是这样用的 —— 那条路是
    "取值"，不是"判据"。
    """
    if _BARE_ATTR_SELECTOR_RE.match(str(selector).strip()):
        raise ValueError(
            f"裸属性选择器不支持：{selector!r}（本工具链静默回 0）"
            ' ⇒ 改用带值属性 [data-x="值"] 或类名 .cls（见 wechatide_client.count 文档）'
        )


# 在页面上下文取「匹配元素个数」
_COUNT_FN = (
    "function(sel){return new Promise(function(res){"
    "var ps=getCurrentPages();var cur=ps[ps.length-1];if(!cur){res(-1);return;}"
    "var q=wx.createSelectorQuery().in(cur);"
    "q.selectAll(sel).boundingClientRect();"
    "q.exec(function(r){res(((r||[])[0]||[]).length);});});}"
)

# 在页面上下文取「匹配元素几何（含 dataset）」
_RECTS_FN = (
    "function(sel){return new Promise(function(res){"
    "var ps=getCurrentPages();var cur=ps[ps.length-1];if(!cur){res([]);return;}"
    "var q=wx.createSelectorQuery().in(cur);"
    "q.selectAll(sel).boundingClientRect();"
    "q.exec(function(r){res(((r||[])[0])||[]);});});}"
)


def classify_page_stack(receipt: dict, stack: list) -> str:
    """把一次「取栈」回执分成四类 —— 此前它们**同名同姓**，都被读成 `timeout`。

    2026-09-19 实测：日志里 17 连击全是
    `MCP_TOOL_ERROR: timeout waiting for automator response`，而**取栈只花 11.6~13.2s**
    —— 我们的预算给的是 `GATE_PROBE_S=90`，**根本没吃满**。
    ⇒ 那个 timeout 是 **IDE 内部的超时**，不是"我们等得不够久"。
    两种情况处置完全相反（前者要查通道/窗口，后者只需加预算），
    却在日志里长得一模一样。故在入口处把性质钉下来：

    | kind | 判据 | 含义 |
    | --- | --- | --- |
    | `our_timeout` | 回执带 `__rc__=-1` / `__raw__` 以 TIMEOUT 开头 | **我们的**预算用尽，子进程被杀 |
    | `channel_error` | 有回执但 `ok=false` | IDE/通道回错（automator 未注册、授权、窗口…） |
    | `empty_stack` | `ok=true` 但栈为空 | 通道是**通的**，只是窗口没进小程序页 |
    | `ok` | `ok=true` 且栈非空 | 正常 |

    ⚠️ `our_timeout` 的耗时 ≈ 我们给的预算；`channel_error` 的耗时是**IDE 自己**的
    内部超时（本机 ≈11.5s）—— 所以「耗时」与「kind」要**一起**看，单看哪个都会误判。
    """
    if receipt.get("__rc__") == -1 or str(receipt.get("__raw__", "")).startswith("TIMEOUT"):
        return "our_timeout"
    if receipt.get("ok") is False:
        return "channel_error"
    if not stack:
        return "empty_stack"
    return "ok"


def resolve_ide_dir(explicit: str | None = None) -> str:
    """返回可用的 IDE 安装目录；找不到返回空串。"""
    for cand in (explicit, *IDE_CANDIDATES):
        if cand and os.path.isfile(os.path.join(cand, CLI_REL)):
            return cand
    return ""


def find_electron(ide_dir: str) -> str:
    """复刻 `.cmd` 的 for 循环：取最后一个 >50 MB 且不在排除名单的 .exe。"""
    last = ""
    for name in sorted(os.listdir(ide_dir)):
        if not name.lower().endswith(".exe") or name.lower() in EXCLUDE_EXE:
            continue
        path = os.path.join(ide_dir, name)
        try:
            if os.path.getsize(path) > 50_000_000:
                last = path
        except OSError:
            continue
    return last


class Client:
    """`wechatide` 的会话封装（通道 + 小程序语义便捷方法）。"""

    def __init__(
        self,
        project: str,
        client: str = DEFAULT_CLIENT,
        ide_dir: str | None = None,
        timeout: int = 150,
    ) -> None:
        self.project = os.path.abspath(project)
        self.client = client
        self.ide = resolve_ide_dir(ide_dir)
        if not self.ide:
            raise RuntimeError(
                "找不到微信开发者工具安装目录（其中应含 "
                f"{CLI_REL}）。可用环境变量 WECHAT_DEVTOOLS_HOME 指定。"
            )
        if not os.path.isdir(self.project):
            raise RuntimeError(f"小程序项目目录不存在：{self.project}")
        self.cli = os.path.join(self.ide, CLI_REL)
        self.electron = find_electron(self.ide)
        if not self.electron:
            raise RuntimeError(f"在 {self.ide} 里找不到 Electron 本体（>50MB 的 .exe）")
        self.timeout = timeout
        self._args_dir = tempfile.mkdtemp(prefix="wechatide-args-")
        self._args_file = os.path.join(self._args_dir, "args.json")

    # ------------------------------------------------------------------ 通道
    def call(self, *args: str, timeout: int | None = None) -> tuple[int, str, str]:
        """执行一条 `wechatide` 调用，返回 (rc, stdout, stderr)。

        超时不抛异常，返回 rc=-1 —— 单次超时（IDE 编译/切页时偶发）不该打断整轮走查。
        """
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env.pop("ELECTRON", None)
        env["cwd"] = self.ide
        cmd = [self.electron, "-e", _BOOTSTRAP, self.cli, "-c", self.client, *args]
        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                env=env,
                cwd=self.ide,
                timeout=timeout or self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            label = args[0] if args else "?"
            return -1, "", f"TIMEOUT after {timeout or self.timeout}s: {label}"
        return (
            p.returncode,
            (p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"),
        )

    def call_json(self, *args: str, timeout: int | None = None) -> dict:
        """执行并把回执里的 JSON 解析出来（CLI 会把日志混在前后）。

        CLI 打印的是**多行** JSON（含嵌套对象），所以不能简单地「从最后一个 `{` 起
        json.loads」——那会命中内层对象，让外层回执的 `ok` 字段凭空消失。
        改用 `raw_decode` 从左往右扫描：第一个能解码成功的对象就是外层回执。
        """
        rc, out, err = self.call(*args, timeout=timeout)
        text = out + ("\n" + err if err.strip() else "")
        decoder = json.JSONDecoder()
        found: list[dict] = []
        for m in re.finditer(r"\{", text):
            try:
                obj, _ = decoder.raw_decode(text, m.start())
            except Exception:  # noqa: BLE001
                continue
            if isinstance(obj, dict):
                found.append(obj)
        for obj in found:
            # 外层回执一定带 ok / result，优先返回它
            if "ok" in obj or "result" in obj:
                return obj
        if found:
            return found[0]
        return {"ok": False, "__rc__": rc, "__raw__": text[:600]}

    def tool(self, name: str, *keys: str, timeout: int | None = None) -> dict:
        """带 `--project` 的工具调用（绝大多数自动化工具都要求项目上下文）。"""
        return self.call_json(name, "--project", self.project, *keys, timeout=timeout)

    # ------------------------------------------------------------------ 前置
    def status(self, skill_version: str | None = None) -> dict:
        """门禁自检。注意本工具**不带** `--project`。"""
        keys = ("--skill-version", skill_version) if skill_version else ()
        return self.call_json("check_wechatide_status", *keys)

    def require_ready(self, skill_version: str | None = None) -> dict:
        """前置自检：不可用即抛异常，并把「下一步该做什么」写进错误信息。

        ⚠️ 2026-09-15 实测：`check_wechatide_status` 会**偶发**回
        `{ok: False, errorType: "CONNECT_ERROR"}`，而**同一时刻** `open_project_window`
        与 `page_stack` 都正常返回真实页面栈 —— 也就是说状态工具的 `ok` **不能**作为
        「自动化通道是否可用」的判据，照它 abort 会把一轮二十多分钟的走查白废掉。

        ⇒ 状态不 ok 时用**真实能力**复检（开窗 + 取页面栈）：能用就降级放行并返回
        `degraded=True`，由调用方打印提示；两支都不通才报错。
        """
        j = self.status(skill_version)
        if j.get("ok"):
            return j
        probe = self.open_window()
        if probe.get("ok"):
            return {"ok": True, "degraded": True, "status": j, "probe": probe}
        raise RuntimeError(
            "wechatide 不可用 —— 请依次确认：\n"
            f'  1) IDE 已启动：`"{os.path.join(self.ide, "cli.bat")}" open '
            f'--project "{self.project}"`\n'
            f"  2) 已授权：`wechatide auth -c {self.client}` 并在 IDE 内点击批准\n"
            f"  3) 助手工具不在沙箱中运行（官方硬要求）\n"
            f"原始返回：{str(j)[:300]}"
        )
        return j

    # -------------------------------------------------------------- 窗口/导航
    def open_window(self, timeout: int | None = None) -> dict:
        """打开（或复用）项目窗口；后续所有页面工具都依赖它。

        ⚠️ `timeout` 可**按调用点收窄**：就绪闸门里必须给一个小的值 —— 每次探测都用
        默认 150s 会让"一轮闸门"变成几十次 × 数分钟，实测出现过 **18 次探测耗掉 56 分钟**
        且日志看起来"像卡死"（见 `run_walkthrough_devtools.py::wait_ready`）。
        """
        return self.call_json("open_project_window", "--project", self.project, timeout=timeout)

    def window_info(self, timeout: int | None = None) -> dict:
        """`open_project_window` 回执里的**窗口标识**：`{"type": ..., "winId": ...}`。

        ⚠️ 2026-09-19 实测（`E:\\_diag\\probe_channel.out`，两轮**完全一致**）：

            {"ok": true, "tool": "open_project_window", "clientName": "WorkBuddy",
             "result": {"success": true, "type": "reuse", "winId": "s0"}}

        ⇒ 两个字段都在 **`result`** 里，**不在顶层**。此前按顶层读 ⇒ **恒为空字符串**
        —— 一个"看起来有观测、实际永远读不到"的**假读数**（与 54 ⑤ 那次真空通过同型）。
        ⇒ 判据与夹具都必须按**这个真实形状**来；⛔ 别用臆想的顶层形状。

        为什么要读它：取栈（`automation_runtime_info`）**不带窗口参数**，
        取的是哪一个窗口只有 IDE 自己知道 ⇒ "每轮重开窗、取栈却指向旧窗口"这类
        **目标窗口不一致**的猜测，只能靠这两个字段证伪
        （`type=open` 且 `winId` 递增 = 窗口在堆积；`type=reuse` 且 `winId` 恒定 = 同一个窗口）。
        缺失时一律返回空串（⛔ 不回 `None`、更不回 `"None"`，否则日志里的 `—` 与真值同形）。
        """
        j = self.open_window(timeout=timeout)
        res = j.get("result") or {}
        return {"type": str(res.get("type") or ""), "winId": str(res.get("winId") or "")}

    def page_stack(self, timeout: int | None = None) -> list:
        """当前页面栈。`--action currentPage` 在本版本会报错，故一律用 pageStack。"""
        j = self.tool("automation_runtime_info", "--action", "pageStack", timeout=timeout)
        return list(j.get("result", {}).get("pageStack") or [])

    def page_stack_probe(self, timeout: int | None = None) -> tuple[list, dict]:
        """页面栈 + **原始回执**。

        就绪闸门要能回答"为什么空"：`pageStack` 为空到底是**回执 `ok: false`**（通道/授权问题）、
        还是 **`ok: true` 但栈为空**（窗口没进小程序页）—— 两者处置完全不同，
        而只返回 `list` 的方法把区别吞掉了（`j.get("result")` 对失败回执同样给 `[]`）。
        """
        t0 = time.time()
        j = self.tool("automation_runtime_info", "--action", "pageStack", timeout=timeout)
        elapsed = time.time() - t0
        stack = list(j.get("result", {}).get("pageStack") or [])
        # ⚠️ 两个下划线前缀的键是**本封装注入的**（不是 IDE 回执的一部分）：
        #    耗时与性质必须跟着回执一起走到日志里，否则"空栈"与"通道坏"又会被压成同一行。
        j = dict(j)
        j["__elapsed__"] = round(elapsed, 2)
        j["__kind__"] = classify_page_stack(j, stack)
        return stack, j

    def current_path(self) -> str:
        stack = self.page_stack()
        return stack[-1].get("path", "") if stack else ""

    def navigate(self, action: str, url: str) -> dict:
        """action ∈ navigateTo / redirectTo / switchTab / reLaunch / navigateBack。"""
        return self.tool("automation_navigate", "--action", action, "--url", url)

    def refresh(self) -> dict:
        """让 IDE 重新编译（改过源码后必须调一次）。"""
        return self.tool("simulator_refresh")

    def back(self) -> dict:
        return self.tool("automation_navigate", "--action", "navigateBack")

    def set_data(self, patch: dict) -> dict:
        """直接改页面 data（走查里用于把弹窗复位到已知状态）。"""
        return self.tool(
            "automation_page_action",
            "--action",
            "setData",
            "--patch",
            json.dumps(patch, ensure_ascii=False),
        )

    def call_method(self, method: str, args: list | None = None) -> dict:
        """调用页面方法（`automation_element_action` 无法按序号点击时的替代手段）。

        ⚠️ 这是**方法调用**而非真实点击，证据等级低于点击：断言里必须写明，
        不能与「真实点击」混为一谈。
        """
        extra: tuple[str, ...] = ()
        if args is not None:
            with open(self._args_file, "w", encoding="utf-8") as fh:
                json.dump(args, fh, ensure_ascii=False)
            extra = ("--args-file", self._args_file)
        return self.tool(
            "automation_page_action", "--action", "callMethod", "--method", method, *extra
        )

    # -------------------------------------------------------------- 取值/交互
    def evaluate(self, fn_source: str, args: list | None = None) -> object:
        """在页面上下文执行函数，返回其返回值（失败返回 None）。"""
        extra: tuple[str, ...] = ()
        if args is not None:
            with open(self._args_file, "w", encoding="utf-8") as fh:
                json.dump(args, fh, ensure_ascii=False)
            extra = ("--args-file", self._args_file)
        j = self.tool("automation_evaluate", "--fn-source", fn_source, *extra)
        try:
            return j["result"]["result"]["result"]
        except Exception:  # noqa: BLE001
            return None

    def page_data(self) -> dict:
        """栈顶页面的 data。

        `automation_page_action --action getData` 在刚跳转的页面上不稳
        （有时只回 `__webviewId__`），故统一走 `evaluate` + `getCurrentPages()`。
        """
        val = self.evaluate(
            "function(){var ps=getCurrentPages();var c=ps[ps.length-1];return (c&&c.data)||{};}"
        )
        return val if isinstance(val, dict) else {}

    def query_selector_all(self, selector: str) -> list | None:
        j = self.tool(
            "automation_page_action", "--action", "querySelectorAll", "--selector", selector
        )
        els = j.get("result", {}).get("elements")
        return els if isinstance(els, list) else None

    def count(self, selector: str) -> int:
        """元素个数；查询失败返回 -1（与「查到 0 个」区分开）。

        ⚠️ **优先走工具的 `querySelectorAll`**（`automation_page_action`，
        2026-09-14 实测可靠：同一页上 `view` 84 个、`[class]` 137 个）。

        历史上这里优先走 `createSelectorQuery`（`evaluate` + `_COUNT_FN`），而那条路
        在同一页上 `selectAll('view')` / `selectAll('[class]')` / `selectAll('[data-role]')`
        **全部回 0**（只有 `.page` 这类简单类选择器回 1）。后果不是"少数断言不准"，
        而是**所有靠 `count` 的锚点断言一起变成 `n=0`** ——
        看起来像"锚点被搬走了/模板没渲染"，实际是取值通道回了一个**假 0**。
        （2026-09-14 因此产生 39 条假失败，跨 ⑯/㉕/㉖/㉗/㉘/㉙/㉚ 七章。）

        `createSelectorQuery` 保留为**兜底**：工具通道不可用时至少有个数
        （但它回 0 时不可信，勿用来下"元素不存在"的结论）。

        ⛔ **裸属性选择器在这里会被拒绝**（`ValueError`）—— 见 `_reject_bare_attr`。
        要用属性定位，写成 `[data-x="值"]`；只按"有这个属性"筛，改用类名。
        """
        _reject_bare_attr(selector)
        els = self.query_selector_all(selector)
        if els is not None:
            return len(els)
        n = self.evaluate(_COUNT_FN, [selector])
        return n if isinstance(n, int) else -1

    def rects(self, selector: str) -> list[dict]:
        """匹配元素的几何数组（视口坐标 + dataset）。

        这是「按序号操作元素」的唯一可靠途径：`automation_element_action` 只认
        selector 的第一个匹配项（实测 `--x/--y` 坐标派发与 CSS 伪类都被忽略），
        所以需要「第 i 个」时，先取 rects 定位语义，再找可用的唯一选择器。
        dataset 也一并返回（如 `{"org": "1"}`），可用来核对元素语义。
        """
        val = self.evaluate(_RECTS_FN, [selector])
        return [x for x in val if isinstance(x, dict)] if isinstance(val, list) else []

    def outer_wxml(self, selector: str) -> str:
        """元素的外层 WXML 文本。

        用于「读第 i 个元素的文本」：`--action text` 只能读第一个匹配项，
        拿整块 WXML 再断言包含关系是更稳的等价做法（文案断言足够）。
        """
        j = self.tool("automation_element_action", "--action", "outerWxml", "--selector", selector)
        val = j.get("result")
        return val if isinstance(val, str) else ""

    def wait_path(self, want: str, tries: int = 25, gap: float = 0.3) -> bool:
        """轮询当前页面路径直到等于 `want`（页面过渡是异步的）。"""
        for _ in range(tries):
            if self.current_path() == want:
                return True
            time.sleep(gap)
        return False

    def nav(self, action: str, url: str, want_path: str = "", tries: int = 3) -> bool:
        """导航 + 到达校验（带重试）。

        旧脚本的教训：模拟器在页面过渡/长任务时回执会迟到，首次超时往往页面
        其实已经跳过去了 —— 故以「目标路径是否就位」判定成功，而不是看回执。
        """
        for _ in range(tries):
            self.navigate(action, url)
            if not want_path or self.wait_path(want_path):
                return True
            time.sleep(1.2)
        return not want_path

    def scroll_to(self, top: int) -> bool:
        """页面滚动（原生能力，替代旧脚本手写的 `wx.pageScrollTo`）。"""
        return bool(
            self.tool(
                "automation_viewport_action", "--action", "pageScrollTo", "--scroll-top", str(top)
            ).get("ok")
        )

    def errors(self) -> str:
        """运行期 console 里的错误文本（PRD 要求「运行期零 console 报错」）。"""
        return self.console("grep -i error")

    def text(self, selector: str) -> str | None:
        j = self.tool("automation_element_action", "--action", "text", "--selector", selector)
        return j.get("result") if j.get("ok") else None

    def tap(self, selector: str) -> bool:
        """点击元素。

        工具**没有 index 参数**，「点第 N 个同名元素」要靠属性选择器精确定位
        （渲染层支持标准 CSS，如 `[data-org="2"]`）。
        """
        return bool(
            self.tool("automation_element_action", "--action", "tap", "--selector", selector).get(
                "ok"
            )
        )

    def longpress(self, selector: str) -> bool:
        """长按元素（订单卡的「长按预览合同」这类交互只能这样触发）。

        与 `tap` 同族：只认 selector 的第一个匹配项，需要「第 i 个」时依赖
        属性选择器（如 `[data-order-id="12"]`）。
        """
        return bool(
            self.tool(
                "automation_element_action", "--action", "longpress", "--selector", selector
            ).get("ok")
        )

    def scroll_into(self, selector: str) -> bool:
        """把元素滚进视口（元素级 `scrollTo`，比整页 `pageScrollTo` 精确）。

        为什么需要：长列表里第 N 张卡的按钮在折叠线外时，`tap` 会落空 ——
        旧脚本为此手写了 `scrollIntoView`。工具原生支持则不必自己算坐标。
        """
        return bool(
            self.tool(
                "automation_element_action", "--action", "scrollTo", "--selector", selector
            ).get("ok")
        )

    def input_text(self, selector: str, value: str) -> bool:
        """向输入框键入文本（`--action input`，会真实触发 `bindinput`）。

        退路：页面若用 `setData({input})` 注入更稳（不依赖输入框已聚焦），
        走查里两者都保留 —— 前者验「输入真的接上了」，后者只是填值。
        """
        return bool(
            self.tool(
                "automation_element_action",
                "--action",
                "input",
                "--selector",
                selector,
                "--value",
                value,
            ).get("ok")
        )

    def screenshot(self, path: str) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        j = self.tool("simulator_screenshot", "--path", path, timeout=180)
        return bool(j.get("ok")) and os.path.exists(path)

    def console(self, command: str = "grep -n .") -> str:
        """读取模拟器 console（官方能力，比自建钩子可靠）。"""
        j = self.tool("get_simulator_console", "--command", command, timeout=150)
        val = j.get("result")
        return val if isinstance(val, str) else ""

    # -------------------------------------------------------------- Storage
    def get_storage(self, key: str) -> str:
        val = self.evaluate("function(k){return wx.getStorageSync(k)||'';}", [key])
        return str(val) if val is not None else ""

    def set_storage(self, key: str, value: object) -> object:
        return self.evaluate("function(k,v){wx.setStorageSync(k,v);return true;}", [key, value])

    def remove_storage(self, key: str) -> object:
        return self.evaluate("function(k){wx.removeStorageSync(k);return true;}", [key])
