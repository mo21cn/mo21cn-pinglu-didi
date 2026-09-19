"""走查通道「取栈空」性质分类的 **CI 侧** 验证（DR-0020）。

**为什么放在这里**：`classify_page_stack()` 此前**只有真机侧在用**，而 CI 里没有 IDE
⇒ 真机日志打出"取栈空"时，CI 无从分辨它是 `our_timeout`（我们预算用尽）、
`channel_error`（IDE 自己回错）、还是 `empty_stack`（通道通、窗口没进小程序页）——
三者的**处置完全相反**（前者可以调大预算，后两者调预算纯属白等）。
本文件把这条可分辨性搬进 CI：**不打 IDE、不起子进程、不联网**，
只验证三件事：① 分类函数本身；② `page_stack_probe` 的注入链；
③ runner 侧摘要确实把 `kind` / `elapsed` 打出来。

⚠️ 判据都带**正控**：`empty_stack` 与 `ok` 必须给出**不同**的值，
否则"分类"是真空的（两侧同为 `[]` 的 `[] == []` 式通过）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# 仓库根/scripts 不在 python 搜索路径里（CI 的 CWD 是 backend），显式注入。
REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_walkthrough_devtools as runner  # noqa: E402
import wechatide_client as wc  # noqa: E402

#: 我们**自己**等到预算用尽（子进程被杀）—— `call_json` 在超时时就是这个形状。
OUR_TIMEOUT = {
    "ok": False,
    "__rc__": -1,
    "__raw__": "TIMEOUT after 90s: automation_runtime_info",
}
#: IDE **自己**回错（本机 ≈11.5s 内部超时）。⚠️ 这是**实测原文**（含嵌套 `detail`），
#: 不是简化形状 —— 夹具失真就抓不到真问题。注意消息里**也有** "timeout" 这个词。
IDE_CHANNEL_ERROR = {
    "ok": False,
    "errorType": "MCP_TOOL_ERROR",
    "message": "timeout waiting for automator response",
    "source": "mcp_business",
    "detail": {"rawResult": {"success": False, "error": "timeout waiting for automator response"}},
    "reason": "mcp_business_fail",
    "tool": "automation_runtime_info",
    "clientName": "WorkBuddy",
    "suggestions": "查看该工具完整参数, wechatide -c WorkBuddy automation_runtime_info -h",
}
#: runtime 还没注册（冷启动，约 40s 自愈）—— 同样归 `channel_error`。
RUNTIME_NOT_REGISTERED = {
    "ok": False,
    "errorType": "MCP_TOOL_ERROR",
    "message": r"cant find runtimeid by projectpath E:\pinglu-didi\miniapp",
}

STACK_ONE = [{"path": "pages/entrust/workbench/workbench"}]


@pytest.mark.parametrize(
    ("kind", "receipt", "stack"),
    [
        ("our_timeout", OUR_TIMEOUT, []),
        ("channel_error", IDE_CHANNEL_ERROR, []),
        ("channel_error", RUNTIME_NOT_REGISTERED, []),
        ("empty_stack", {"ok": True}, []),
        # ⭐ 正控：通道通 **且** 有栈 ⇒ 必须给出第四个值
        ("ok", {"ok": True}, STACK_ONE),
    ],
)
def test_classify_page_stack(kind: str, receipt: dict, stack: list) -> None:
    assert wc.classify_page_stack(receipt, stack) == kind


def test_classify_is_not_vacuous_on_ok_receipt() -> None:
    """同一个 `ok=true` 回执，栈空与栈非空必须**分类不同**（否则分类无意义）。"""
    empty = wc.classify_page_stack({"ok": True}, [])
    nonempty = wc.classify_page_stack({"ok": True}, STACK_ONE)
    assert empty == "empty_stack"
    assert nonempty == "ok"
    assert empty != nonempty


def test_timeout_word_is_disambiguated_by_receipt_shape() -> None:
    """两个回执的**消息里都含 `timeout`**，但性质相反 —— 这正是 DR-0020 的核心判定。

    只按关键字找 `timeout` 会把"我们等得不够久"与"IDE 内部超时"混成一件事，
    于是去修一个不存在的问题（加预算）。判据必须是**回执形状**（有无 `__rc__`/`__raw__`）。
    """
    ours = wc.classify_page_stack(OUR_TIMEOUT, [])
    ide = wc.classify_page_stack(IDE_CHANNEL_ERROR, [])
    assert ours == "our_timeout"
    assert ide == "channel_error"
    assert ours != ide
    assert "timeout" in str(IDE_CHANNEL_ERROR["message"]).lower()  # 前提：消息里确实有这个词


def _bare_client() -> Any:
    """绕过 `Client.__init__`（它要求本机装着 IDE），只验证纯逻辑注入链。"""
    return wc.Client.__new__(wc.Client)


def test_page_stack_probe_injects_kind_and_elapsed_on_our_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _bare_client()
    monkeypatch.setattr(client, "tool", lambda *a, **k: dict(OUR_TIMEOUT))

    stack, receipt = client.page_stack_probe(timeout=1)

    assert stack == []
    assert receipt["__kind__"] == "our_timeout"
    assert isinstance(receipt["__elapsed__"], float)
    assert receipt["__elapsed__"] >= 0


def test_page_stack_probe_reports_ok_for_nonempty_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⭐ 正控：**同一个入口**在能取到栈时必须报 `ok` —— 否则"恒报错"也会过。"""
    client = _bare_client()
    monkeypatch.setattr(
        client, "tool", lambda *a, **k: {"ok": True, "result": {"pageStack": STACK_ONE}}
    )

    stack, receipt = client.page_stack_probe(timeout=1)

    assert stack == STACK_ONE
    assert receipt["__kind__"] == "ok"


def test_brief_surfaces_kind_and_elapsed() -> None:
    """runner 的日志摘要必须把 `kind` / `elapsed` 打出来（否则读数还是看不见）。"""
    line = runner._brief({"ok": False, "__kind__": "channel_error", "__elapsed__": 11.62})
    assert "kind=channel_error" in line
    assert "elapsed=11.62s" in line


def test_brief_surfaces_raw_and_message() -> None:
    """摘要仍要保留既有字段（`__raw__` / `message`）—— 这次改动**只加不减**。"""
    line = runner._brief(dict(OUR_TIMEOUT))
    assert "ok=False" in line
    assert "TIMEOUT after 90s" in line


#: `open_project_window` 的**真实回执**（2026-09-19 实测原文，两轮完全一致；
#: 取证脚本 `E:\_diag\probe_channel.py`，输出 `probe_channel.out`）。
#: ⚠️ 夹具**必须是这个形状**：`type` 与 `winId` 在 `result` 里，**不在顶层**。
REAL_OPEN_WINDOW = {
    "ok": True,
    "tool": "open_project_window",
    "clientName": "WorkBuddy",
    "result": {"success": True, "type": "reuse", "winId": "s0"},
}


def test_window_info_reads_fields_from_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口标识可读 —— 它是"开窗与取栈是否同一窗口"的**唯一**观测入口。"""
    client = _bare_client()
    monkeypatch.setattr(client, "open_window", lambda *a, **k: REAL_OPEN_WINDOW)
    assert client.window_info() == {"type": "reuse", "winId": "s0"}


def test_window_info_must_read_result_not_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐ 守护**读取位置**：同样两个字段放在**顶层**时必须读到**空**。

    这是**反向**断言，它锁死"读数来自 `result`"。2026-09-19 此前按顶层读 ⇒ **恒为空串**，
    而**没有任何东西会因此变红**（"看起来有观测、实际永远读不到"，与 54 ⑤ 那次
    `None→None` 的真空通过同型）。有了这条，以后再读错位置会**当场暴露**。
    """
    client = _bare_client()
    top_level = {"ok": True, "type": "reuse", "winId": "s0", "result": {}}
    monkeypatch.setattr(client, "open_window", lambda *a, **k: top_level)
    assert client.window_info() == {"type": "", "winId": ""}


def test_window_info_degrades_to_empty_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败回执 ⇒ 空串（⛔ 不回 `None`、更不回字符串 `"None"`，否则日志里与真值同形）。"""
    client = _bare_client()
    monkeypatch.setattr(client, "open_window", lambda *a, **k: {"ok": False})
    assert client.window_info() == {"type": "", "winId": ""}


# ─────────────────────── 闸门里「主动刷新」的策略（2026-09-19 对照实验） ───────────────────────


def test_should_auto_refresh_triggers_on_first_empty_stack() -> None:
    """**一次** `empty_stack` 就触发刷新。

    实测：`empty_stack` 是**通道通、窗口没进小程序页** ⇒ 调一次 refresh 后连续 5 次
    取栈全转 `ok`（1.3~2.9s）并持续 ≥210s；而**紧接的第二次探测就转 `channel_error`**
    （通道被打堵，且静默救不回来）⇒ 必须**趁通道还通的时候**救，故阈值降到 1。
    """
    assert runner.should_auto_refresh(["empty_stack"], 0) is True
    assert runner.should_auto_refresh(["empty_stack", "empty_stack"], 0) is True


def test_should_auto_refresh_never_fires_when_stack_is_ok() -> None:
    """⭐ **反向**断言：历史里**只有 `ok`**（即取到过栈）时绝不刷。

    ⚠️ 刻意**不**用 `["empty_stack", "ok"]` 这种组合当判据 —— 那是**测错了层**：
    `wait_ready()` 在 `if stack: return True` 就返回了，**根本走不到**刷新的判断。
    这条测试只回答"这个纯函数在什么样的历史下会/不会说该刷"。
    """
    assert runner.should_auto_refresh(["ok"], 0) is False
    assert runner.should_auto_refresh(["ok", "ok"], 0) is False
    assert runner.should_auto_refresh(["channel_error"], 0) is False


def test_should_auto_refresh_is_not_vacuous() -> None:
    """同一长度下，两种历史必须给出**不同**结论（否则策略等于常量）。"""
    bad = runner.should_auto_refresh(["empty_stack", "channel_error"], 0)
    good = runner.should_auto_refresh(["ok", "ok"], 0)
    assert bad is True
    assert good is False
    assert bad != good


def test_should_auto_refresh_respects_max() -> None:
    """刷够次数就停（⛔ 不许无限刷/刷屏）。"""
    hist = ["empty_stack"] * 9
    assert runner.should_auto_refresh(hist, 0) is True
    assert runner.should_auto_refresh(hist, runner.GATE_AUTO_REFRESH_MAX) is False
    assert runner.should_auto_refresh(hist, runner.GATE_AUTO_REFRESH_MAX + 5) is False


def test_should_auto_refresh_ignores_channel_error() -> None:
    """⭐ **`channel_error` 不触发刷新**（判据按实测结果改，不是凭直觉）。

    2026-09-19 全量走查（**单一控制者**，无并发）实测：对 `channel_error` 连刷两次
    `simulator_refresh`，取栈**仍然** 11.7~11.9s 超时 ⇒ 对"已堵"的通道 refresh **无效**，
    刷它只是白等两轮（每轮 ≈11.6s）。
    """
    assert runner.should_auto_refresh(["channel_error"], 0) is False
    assert runner.should_auto_refresh(["channel_error", "channel_error"], 0) is False
    assert runner.should_auto_refresh(["our_timeout", "channel_error"], 0) is False
