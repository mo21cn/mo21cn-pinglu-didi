"""IDE 原生 MCP 通道 —— 走查驱动层的第二条轨（2026-10-26 起默认）。

为什么要有这条轨
----------------
旧轨是"用 IDE 自带 Electron 当 Node 跑 `wechatide` CLI"（见 `wechatide_client.py`）。
它有两个硬依赖：**能读 IDE 安装目录**（`icudtl.dat` / `resources/.../skill-index.js`），
**能写 IDE 目录**（`debug.log`）。本机安全策略逐条拒绝后，整条轨直接死掉，
而"改规则"这条路在 UI 里根本找不到对应条目（文件安全表里 deny 条数为 0）——
不是配置问题，是**通道选错了**。

IDE 自己其实暴露了一个标准 MCP 服务端，证据在它自己的用户数据目录里：

    %LOCALAPPDATA%\\微信开发者工具\\User Data\\<hash>\\Default\\
        .ide          IDE 自动化端口（如 35531）
        .ide-status   服务端口开关（On/Off）
        mcp.json      {"mcpServers":{"wechat-devtools":{"command":"wechatide",
                        "args":["mcp","--token=<32 位 token>"]}}}

⇒ 直接 `POST http://127.0.0.1:<.ide>/mcp` + `Authorization: Bearer <token>`，
走 MCP Streamable HTTP。全程**不启动任何 exe、不读 IDE 安装目录**，
因此不受文件安全规则影响。

授权（一次，且必须"连着不放"）
------------------------------
未授权时回 `403 {"code":-32002,"data":{"status":"pending","taskId":"auth_…"}}`，
同时 IDE 会弹一个 420px 的授权小窗。**关键坑**：该窗口的生命周期绑在请求方连接上——
脚本"发一次就退出"会让 IDE 立刻 `forceClose` 并记 `authResult=deny`（实测 09:12，
窗口存在仅 1.3 秒，人来不及点）。所以必须复用同一条长连接轮询，等人工点「允许」。
授权按 clientName 持久保存，批过一次后同一名字直接 200。
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import time

# 与 CLI 轨保持同名，方便两端共用 `--client`
DEFAULT_CLIENT = "WorkBuddy"


# ------------------------------------------------------------------ 通道发现
def discover() -> tuple[int, str, str] | None:
    """返回 (ide_port, mcp_token, user_data_hash)；找不到返回 None。

    取**最近修改过 `.ide`** 的那个 User Data 目录：同机可能并存多份
    （本例 2 份，其中一份是 10-21 的旧快照），拿错就会去连一个已死的端口。

    ⭐ **显式注入**：`WECHATIDE_PORT` + `WECHATIDE_TOKEN` 同时存在时直接用，不做目录发现。
    用途是「本机安全策略禁止**子进程**读 IDE 配置目录」这一种情形 —— 此时自动发现会
    静默返回 None（`OSError` 被 `continue` 吞掉），表现为"找不到 MCP 配置"，与
    "IDE 没启动"极难区分。注入通道把这条路显式化：值由知道它的人给，语义不变。
    ⛔ token 不落盘、不打印（只在内存里传给 HTTP 头）。
    """
    env_port = os.environ.get("WECHATIDE_PORT", "").strip()
    env_tok = os.environ.get("WECHATIDE_TOKEN", "").strip()
    if env_port.isdigit() and env_tok:
        return int(env_port), env_tok, "env"
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "微信开发者工具", "User Data")
    if not os.path.isdir(base):
        return None
    best: tuple[float, int, str, str] | None = None
    for h in os.listdir(base):
        d = os.path.join(base, h, "Default")
        ide_f, mcp_f = os.path.join(d, ".ide"), os.path.join(d, "mcp.json")
        if not (os.path.isfile(ide_f) and os.path.isfile(mcp_f)):
            continue
        try:
            mt = os.path.getmtime(ide_f)
            with open(ide_f, encoding="utf-8") as fh:
                port = int(fh.read().strip())
            with open(mcp_f, encoding="utf-8") as fh:
                cfg = json.load(fh)
            args = cfg["mcpServers"]["wechat-devtools"]["args"]
            tok = next(a.split("=", 1)[1] for a in args if a.startswith("--token="))
        except (OSError, ValueError, KeyError, StopIteration):
            continue
        if best is None or mt > best[0]:
            best = (mt, port, tok, h)
    if best is None:
        return None
    return best[1], best[2], best[3]


def service_port_state() -> str:
    """`Default/.ide-status` 的内容（`On` / `Off`）；读不到回空串。"""
    got = discover()
    if not got:
        return ""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "微信开发者工具", "User Data")
    p = os.path.join(base, got[2], "Default", ".ide-status")
    try:
        return open(p, encoding="utf-8").read().strip()
    except OSError:
        return ""


class McpBackend:
    """与 `wechatide_client.Client` 同接口的 MCP 实现。

    `PUBLIC` 列出的方法会被 `Client.__init__` 逐个绑定到实例上（实例属性覆盖类方法），
    因此 14k 行走查脚本里 224 处 `w.c.tap(...)` 这类调用**一行都不用改**。
    """

    PUBLIC = (
        "tool",
        "status",
        "require_ready",
        "open_window",
        "window_info",
        "page_stack",
        "page_stack_probe",
        "current_path",
        "navigate",
        "refresh",
        "back",
        "set_data",
        "call_method",
        "evaluate",
        "page_data",
        "query_selector_all",
        "count",
        "rects",
        "outer_wxml",
        "wait_path",
        "nav",
        "scroll_to",
        "errors",
        "text",
        "tap",
        "longpress",
        "scroll_into",
        "input_text",
        "screenshot",
        "console",
        "get_storage",
        "set_storage",
        "remove_storage",
        "sdk_version",
        "channel",
    )

    def __init__(
        self,
        project: str,
        client: str = DEFAULT_CLIENT,
        timeout: int = 150,
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        got = discover()
        if got is None and (port is None or token is None):
            raise RuntimeError("找不到 IDE 的 MCP 配置（.ide + mcp.json）")
        if got is not None:
            self.port, self.token, self.uh = got
        else:
            self.port, self.token, self.uh = port or 0, token or "", "?"
        self.project = os.path.abspath(project)
        self.client = client
        self.timeout = timeout
        self.sid: str | None = None
        self._id = 0
        self._conn: http.client.HTTPConnection | None = None

    # -------------------------------------------------------------- 底层收发
    def channel(self) -> str:
        return "mcp"

    def _conn_get(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        return self._conn

    def _reset(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(OSError):
                self._conn.close()
        self._conn = None

    def _rpc(self, payload: dict) -> tuple[int, dict]:
        """发一条 JSON-RPC；返回 (http_code, 解析后的 body)。

        ⚠️ 连接断了要重建并**重试一次**：IDE 编译/切页时会短暂关掉 keep-alive，
        直接失败会让走查在无关的章节断掉。
        """
        for attempt in (0, 1):
            self._id += 1
            payload["id"] = self._id
            hdr = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
                "Connection": "keep-alive",
            }
            if self.sid:
                hdr["Mcp-Session-Id"] = self.sid
            try:
                conn = self._conn_get()
                conn.request("POST", "/mcp", body=json.dumps(payload), headers=hdr)
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8", "replace")
                sid = resp.getheader("Mcp-Session-Id")
                if sid:
                    self.sid = sid
                code = resp.status
            except (http.client.HTTPException, OSError) as exc:
                self._reset()
                if attempt:
                    raise RuntimeError(f"MCP 通道中断：{exc}") from exc
                continue
            if raw.lstrip().startswith("data:") or "\ndata:" in raw:
                parts = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
                raw = parts[-1] if parts else "{}"
            try:
                return code, json.loads(raw)
            except json.JSONDecodeError:
                return code, {"raw": raw[:500]}
        raise RuntimeError("MCP 通道不可用")

    def _ensure(self) -> None:
        if self.sid:
            return
        code, body = self._rpc(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": self.client, "version": "1.0"},
                },
            }
        )
        if code != 200:
            err = body.get("error", {})
            data = err.get("data", {})
            if data.get("status") == "pending":
                raise RuntimeError(
                    "IDE 正在等人工授权（客户端名 "
                    f"{self.client}）：请在 IDE 弹出的『是否允许连接』里点「允许」。\n"
                    "  ⚠️ 该窗口随请求方连接关闭而消失，所以要用 "
                    "`python scripts/ide_mcp_auth.py` 保持连接等你点。\n"
                    f"  taskId={data.get('taskId')}"
                )
            raise RuntimeError(
                f"MCP initialize 失败 {code}：{json.dumps(body, ensure_ascii=False)[:300]}\n"
                "  若提示 token 无效：IDE → 设置 → 安全 → 重新复制 MCP 配置。"
            )
        # notifications/initialized 是单向通知，不回包，故用 id=0 也不校验
        self._rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # ------------------------------------------------------------------ 工具层
    @staticmethod
    def _norm(structured: dict | None, text: str) -> dict:
        """把 MCP 回执统一成旧轨的形状：`{"ok": bool, "result": ..., ...}`。

        旧轨（CLI）回 `{ok, result}`，走查脚本大量按这两个键取值。MCP 回
        `{success, ...}`，不统一就等于把 500+ 处断言全改成新键名。
        """
        out: dict = {}
        if isinstance(structured, dict):
            out.update(structured)
            out["ok"] = bool(structured.get("success", structured.get("ok", True)))
            if "result" not in structured:
                rest = {k: v for k, v in structured.items() if k != "success"}
                if rest:
                    out["result"] = rest
        else:
            out["ok"] = bool(text) and "error" not in text[:40].lower()
            out["result"] = text
        return out

    def tool(self, name: str, *keys: str, timeout: int | None = None) -> dict:
        """兼容旧轨的 `tool("工具名", "--k", "v", ...)` 调用形态。"""
        args: dict = {}
        it = iter(keys)
        for k in it:
            if not k.startswith("--"):
                continue
            try:
                v = next(it)
            except StopIteration:
                break
            key = k[2:].replace("-", " ")
            parts = key.split()
            camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
            args[camel] = v
        return self.call(name, args, timeout=timeout)

    def call(self, name: str, args: dict, timeout: int | None = None) -> dict:
        """真实的一次 `tools/call`（自动补 project）。"""
        self._ensure()
        if timeout and timeout != self.timeout:
            self._conn_get().timeout = timeout
        payload = dict(args)
        if name != "check_wechatide_status" and "project" not in payload:
            payload["project"] = self.project
        code, body = self._rpc(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": payload},
            }
        )
        if code != 200 or "error" in body:
            err = body.get("error", body)
            return {
                "ok": False,
                "result": None,
                "__error__": json.dumps(err, ensure_ascii=False)[:400],
            }
        res = body.get("result", {})
        text = "".join(
            c.get("text", "") for c in (res.get("content") or []) if c.get("type") == "text"
        )
        return self._norm(res.get("structuredContent"), text)

    # ------------------------------------------------------------------ 前置
    def status(self, skill_version: str | None = None) -> dict:
        args = {"skill-version": skill_version} if skill_version else {}
        return self.call("check_wechatide_status", args)

    def require_ready(self, skill_version: str | None = None) -> dict:
        """通道自检。

        MCP 轨的失败模式与 CLI 轨完全不同：这里**不会**出现"读不到 IDE 目录"，
        只会有三种 —— 端口没开（`.ide` 缺失/服务端口 Off）、token 失效、等人工授权。
        所以文案必须按这三种写，⛔ 不要再提"IDE 未启动/非沙箱"（那是旧轨的话）。
        """
        j = self.status(skill_version)
        if j.get("ok"):
            return j
        stack, probe = self.page_stack_probe()
        if stack:
            return {"ok": True, "degraded": True, "status": j, "probe": probe}
        raise RuntimeError(
            "IDE MCP 通道不可用 —— 按顺序确认：\n"
            f"  1) 端口：{os.path.join(os.environ.get('LOCALAPPDATA', ''), '微信开发者工具', 'User Data', self.uh, 'Default', '.ide')}"
            f" = {self.port}（服务端口状态 {service_port_state() or '未知'}）\n"
            "  2) IDE → 设置 → 安全 →「服务端口」需开启，并已复制 MCP 配置（token 随复制更新）\n"
            f"  3) 授权：`python scripts/ide_mcp_auth.py --client {self.client}` 保持连接，"
            "在 IDE 弹窗点「允许」\n"
            f"  原始回执：{json.dumps(j, ensure_ascii=False)[:300]}"
        )

    def open_window(self, timeout: int | None = None) -> dict:
        return self.call("open_project_window", {"windowMode": "liteMode"}, timeout=timeout)

    def window_info(self, timeout: int | None = None) -> dict:
        return self.call("project_list", {}, timeout=timeout)

    # -------------------------------------------------------------- 页面/运行时
    def page_stack(self, timeout: int | None = None) -> list:
        j = self.call("automation_runtime_info", {"action": "pageStack"}, timeout=timeout)
        return list(j.get("pageStack") or [])

    def page_stack_probe(self, timeout: int | None = None) -> tuple[list, dict]:
        t0 = time.time()
        j = self.call("automation_runtime_info", {"action": "pageStack"}, timeout=timeout)
        elapsed = time.time() - t0
        stack = list(j.get("pageStack") or [])
        # 迟导入：`wechatide_client` 会反过来 import 本模块，模块级导入成环
        from wechatide_client import classify_page_stack

        j = dict(j)
        j["__elapsed__"] = round(elapsed, 2)
        j["__kind__"] = classify_page_stack(j, stack)
        return stack, j

    def current_path(self) -> str:
        stack = self.page_stack()
        return stack[-1].get("path", "") if stack else ""

    def sdk_version(self) -> str:
        """真实基础库版本（证据自证用）。

        CLI 轨拿不到这个值：`summary.json` 里四处都没有 SDKVersion，
        "跑在 3.17.2 上"只能靠口头转述。MCP 的 `systemInfo` 直接给。
        """
        j = self.call("automation_runtime_info", {"action": "systemInfo"})
        info = (j.get("systemInfo") or {}).get("result") or {}
        return str(info.get("SDKVersion") or "")

    def navigate(self, action: str, url: str) -> dict:
        return self.call("automation_navigate", {"action": action, "url": url})

    def refresh(self) -> dict:
        return self.call("simulator_refresh", {}, timeout=240)

    def back(self) -> dict:
        return self.call("automation_navigate", {"action": "navigateBack", "delta": 1})

    def set_data(self, patch: dict) -> dict:
        return self.call(
            "automation_page_action",
            {"action": "setData", "patch": json.dumps(patch, ensure_ascii=False)},
        )

    def call_method(self, method: str, args: list | None = None) -> dict:
        payload = {"action": "callMethod", "method": method}
        if args is not None:
            payload["args"] = args
        return self.call("automation_page_action", payload)

    # ------------------------------------------------------------------ 取值
    def evaluate(self, fn_source: str, args: list | None = None) -> object:
        payload = {"action": "evaluate", "fnSource": fn_source}
        if args is not None:
            payload["args"] = args
        j = self.call("automation_evaluate", payload)
        try:
            return j["result"]["result"]
        except (KeyError, TypeError):
            return None

    def page_data(self) -> dict:
        val = self.evaluate(
            "function(){var ps=getCurrentPages();var c=ps[ps.length-1];return (c&&c.data)||{};}"
        )
        return val if isinstance(val, dict) else {}

    def query_selector_all(self, selector: str) -> list | None:
        j = self.call(
            "automation_page_action", {"action": "querySelectorAll", "selector": selector}
        )
        els = j.get("elements")
        return els if isinstance(els, list) else None

    def count(self, selector: str) -> int:
        """元素个数；查询失败回 -1（与"查到 0 个"分开）。

        ⛔ 裸属性选择器同样拒绝 —— 与 CLI 轨同一条纪律（详见
        `wechatide_client._reject_bare_attr`）。
        """
        from wechatide_client import _COUNT_FN, _reject_bare_attr

        _reject_bare_attr(selector)
        els = self.query_selector_all(selector)
        if els is not None:
            return len(els)
        n = self.evaluate(_COUNT_FN, [selector])
        return n if isinstance(n, int) else -1

    def rects(self, selector: str) -> list[dict]:
        from wechatide_client import _RECTS_FN

        val = self.evaluate(_RECTS_FN, [selector])
        return [x for x in val if isinstance(x, dict)] if isinstance(val, list) else []

    def outer_wxml(self, selector: str) -> str:
        j = self.call("automation_element_action", {"action": "outerWxml", "selector": selector})
        for key in ("result", "outerWxml", "wxml", "value"):
            v = j.get(key)
            if isinstance(v, str) and v.strip():
                return v
        return ""

    def text(self, selector: str) -> str | None:
        j = self.call("automation_element_action", {"action": "text", "selector": selector})
        for key in ("result", "text", "value"):
            v = j.get(key)
            if isinstance(v, str):
                return v
        return None

    # ------------------------------------------------------------------ 等待
    def wait_path(self, want: str, tries: int = 25, gap: float = 0.3) -> bool:
        for _ in range(tries):
            if self.current_path() == want:
                return True
            time.sleep(gap)
        return False

    def nav(self, action: str, url: str, want_path: str = "", tries: int = 3) -> bool:
        for _ in range(tries):
            self.navigate(action, url)
            if not want_path or self.wait_path(want_path):
                return True
            time.sleep(1.2)
        return not want_path

    def scroll_to(self, top: int) -> bool:
        return bool(
            self.call(
                "automation_viewport_action", {"action": "pageScrollTo", "scrollTop": top}
            ).get("ok")
        )

    # ------------------------------------------------------------------ 交互
    def tap(self, selector: str) -> bool:
        return bool(
            self.call("automation_element_action", {"action": "tap", "selector": selector}).get(
                "ok"
            )
        )

    def longpress(self, selector: str) -> bool:
        return bool(
            self.call(
                "automation_element_action", {"action": "longpress", "selector": selector}
            ).get("ok")
        )

    def scroll_into(self, selector: str) -> bool:
        return bool(
            self.call(
                "automation_element_action", {"action": "scrollTo", "selector": selector}
            ).get("ok")
        )

    def input_text(self, selector: str, value: str) -> bool:
        return bool(
            self.call(
                "automation_element_action",
                {"action": "input", "selector": selector, "value": value},
            ).get("ok")
        )

    def screenshot(self, path: str) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        j = self.call("simulator_screenshot", {"path": os.path.abspath(path)}, timeout=180)
        return bool(j.get("ok")) and os.path.exists(path)

    def console(self, command: str = "grep -n .") -> str:
        j = self.call("get_simulator_console", {"command": command}, timeout=150)
        val = j.get("result")
        return val if isinstance(val, str) else ""

    def errors(self) -> str:
        return self.console("grep -i error")

    # ------------------------------------------------------------------ Storage
    def get_storage(self, key: str) -> str:
        val = self.evaluate("function(k){return wx.getStorageSync(k)||'';}", [key])
        return str(val) if val is not None else ""

    def set_storage(self, key: str, value: object) -> object:
        return self.evaluate("function(k,v){wx.setStorageSync(k,v);return true;}", [key, value])

    def remove_storage(self, key: str) -> object:
        return self.evaluate("function(k){wx.removeStorageSync(k);return true;}", [key])
