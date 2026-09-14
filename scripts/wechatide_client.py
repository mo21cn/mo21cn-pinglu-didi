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
        """前置自检：不可用即抛异常，并把「下一步该做什么」写进错误信息。"""
        j = self.status(skill_version)
        if not j.get("ok"):
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
    def open_window(self) -> dict:
        """打开（或复用）项目窗口；后续所有页面工具都依赖它。"""
        return self.call_json("open_project_window", "--project", self.project)

    def page_stack(self) -> list:
        """当前页面栈。`--action currentPage` 在本版本会报错，故一律用 pageStack。"""
        j = self.tool("automation_runtime_info", "--action", "pageStack")
        return list(j.get("result", {}).get("pageStack") or [])

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
        """
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
