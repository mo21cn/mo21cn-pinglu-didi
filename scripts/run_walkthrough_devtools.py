"""真机走查的一站式运行器：**IDE + 后端 + 种子 + 走查，同生共死**。

为什么必须写在一个脚本里
------------------------
1. **IDE**：当前环境在命令结束后会**回收该命令产生的所有后代进程**（实测：摘掉
   `ELECTRON_RUN_AS_NODE` 后 IDE 能起、4 个进程齐；脚本一结束就全没了 ——
   即使 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` 也一样）。
2. **后端**：同一条纪律 —— `Popen` 起的 uvicorn 随发起命令的 shell 结束被回收，
   于是走查里**所有页面**都变成 `view=error` + `viewHint=无法连接后端…`，
   看起来像页面全坏了，实际一条业务缺陷都没有。

⇒ 四步（起 IDE / 起后端 / 铺种子 / 跑走查）必须由本脚本同时持有。

⭐ 三个必须知道的环境坑（都实测踩过，代价是好几轮白跑）
----------------------------------------------------
**① `ELECTRON_RUN_AS_NODE` 会让 IDE 静默退出。**
本机 shell 环境可能被注入 `ELECTRON_RUN_AS_NODE=1`；任何 Electron 程序带着它启动都会
**以 Node 模式运行**：不建窗口、不初始化 GUI 日志、无输出，**3 秒内以 `rc=0` 主动退出**。
指纹（四条同时成立，缺一条就要重新怀疑）：`poll()` 3 秒内返回 **0**（不是崩溃码）；
安装目录 `debug.log` **mtime 不变**；事件日志**无** Crash；`tasklist` 里**没有**进程。
⇒ **起 IDE 前必须把这个变量摘掉**；而**调 `cli.js` 工具链时又需要它**
（`cli.bat` 自己会 `set`）—— 两者要求**相反**，别搞混。

**② IDE 实例会累积，多实例互抢单实例锁。**
`Popen.kill()` **只杀父进程**，Electron 的 renderer / GPU / utility 子进程残留 ⇒
「闸门失败 → 重起一轮」每重试一次就多一个实例。多实例共享同一 `User Data` ⇒
自动化通道拿不到页面栈：实测 `pageStack` 连续 **95 秒**为空、进程累积到 **16 个**，
而 `open_window()` 每次都"成功"返回 —— **看起来像网络抖动**。
⇒ 处置（HO 2026-09-15 对 D2 的补充**收窄了作用域**）：
**只收本轮自己起的实例** —— `kill_owned_ide_procs()`（按 PID + `taskkill /T` 连子进程）。
起 IDE 前不再"按进程名清光全场"，因为那会**关掉 HO 正在使用的其他窗口**；
也确实需要全局清理时，必须显式传 `--kill-all-ide`（会先打印影响说明）。

**③ 就绪闸门不能以 `check_wechatide_status().ok` 为判据。**
它偶发回 `CONNECT_ERROR`，而**同一时刻** `open_project_window` / `page_stack` 都正常。
⇒ 判据是「**能开窗 + `pageStack` 非空**」，且**要轮询**（实测前几次可能为空）。

**④ 闸门的"轮询"必须带*墙钟预算*，单次探测必须*收窄超时*。**
客户端默认超时是 150s；早先闸门按**次数**轮询（24 次）× 每次最多两调
⇒ 一轮最坏 ≈ `24 × 2 × 150s = 120 分钟`。实测一次 `--section 8,8b --pay`：
**18 次探测烧掉 56 分钟**、页面栈全程为空，而日志只有一行行 `pageStack=[]`
（**不打印耗时 ⇒ 完全看不出慢在哪**），于是被读成"卡死 / 网络抖动"。
⇒ 现改为 `GATE_PROBE_S=90`（单次）+ `GATE_BUDGET_S=900`（总预算），
并打印真实耗时、单次调用耗时、进程数与**回执**：`ok=true` 且栈空 = 窗口没进小程序页；
`ok=false` = 通道/授权问题 —— **两者处置不同，不能只看空列表**。
⚠️ 2026-09-17 把单次超时从 45s 调**大**到 90s：冷启动**第一次**调用实测就要 ~61s，
45s 会让每一次都超时 ⇒ 把"还没起来"读成"通道坏了"。

**⑤ 闸门恒空时：先探测，再用 `--skip-ide`，别急着重启 IDE。**
2026-09-15 实证（同一个 IDE）：运行器**新起**的实例上，闸门两次 240s 预算内恒为
`MCP_TOOL_ERROR: timeout waiting for automator response`；而对**当时已在运行**的
那一个做只读探测，`check_wechatide_status` ok、`open_project_window` 回
`{type: "reuse", winId: "s0"}`、`automation_runtime_info` **直接返回 `pageStack`**。
⇒ 现在把处置固化成三步：**① 先只读探测**（`probe_existing_ide()`，不启不停）
→ **② 能用就复用**（`--skip-ide`；实测 31/32 两章 1 分 43 秒跑完）
→ **③ 不能用才清场重起**，且重起后仍要按 ② 复检。
配套还加了两条：
* `wait_ready` 命中「automation runtime 未注册」签名**连续 `AUTOMATION_DEAD_MAX` 次即早退**
  （不再空等满预算），并打印上面那套处置；
  ⭐ **2026-09-17 补了墙钟下限 `AUTOMATION_DEAD_MIN_ELAPSED_S`**：冷启动期间那条签名
  是**必然命中**，按次数收手会把"还在编译"误判成"通道坏了"。实测同一天两轮分别在
  119s / 163s 早退，而 IDE 日志在同一分钟正写着 `[pageframe] finish load user code`
  —— **早退恰好发生在项目刚加载完的那一刻**。⇒ 低于墙钟下限只记录不早退。
* 起 IDE 的环境**不再传本机沙箱代理**（`ide_env()`）—— 那个代理指向每会话换端口的
  沙箱网关，服务的是命令行；实测由本项目起的 IDE 界面会反复闪「网络故障」。
  ⚠️ **但要诚实**：这**不是**已证实的根因（反证：同一次探测里 CLI 带着同一个代理也成功了），
  本改动只是**拆掉一个没有正当理由的耦合**，逐条证据见 DR-0009 §8.5⑨。

用法
----
    python scripts/run_walkthrough_devtools.py --section all
    python scripts/run_walkthrough_devtools.py --section 8,8b --pay
    python scripts/run_walkthrough_devtools.py --section 25 --work D:\\tmp\\walk
    python scripts/run_walkthrough_devtools.py --section 31,32 --skip-ide   # 复用已在跑的 IDE

⚠️ **`--section all` 与 `--pay` 是两个独立开关**（都写进 README 了，此处是原因）：
`--section`（默认 `all`）决定**排哪些章节**，`--pay` 只决定要不要设 `WALK_PAY=1`。
所以 `--section all` **不带** `--pay` 时，⑧b 会被排进去但记 **NOT_RUN**（"需显式开关"），
在汇总里显示为**未执行**、不计入通过 —— 这不是"⑧b 通过"，也不是失败。
要真跑 ⑧b 必须两个一起给：`--section all --pay`（或 `--section 8b --pay`）。

⚠️ **不得在沙箱里运行**（`wechatide` 官方硬要求）；仅 Windows。
⚠️ 这是**本机工具**，不进 CI（依赖 GUI 模拟器）。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

#: 微信开发者工具的常见安装位置（可用 `--ide` 覆盖）
IDE_CANDIDATES = (
    Path(r"D:\Program Files (x86)\Tencent\微信web开发者工具\微信开发者工具.exe"),
    Path(r"C:\Program Files (x86)\Tencent\微信web开发者工具\微信开发者工具.exe"),
    Path(r"C:\Program Files\Tencent\微信web开发者工具\微信开发者工具.exe"),
    Path(r"D:\Program Files\Tencent\微信web开发者工具\微信开发者工具.exe"),
)

#: 进程名（中文；`tasklist` 输出是 GBK，解析时要用 gbk 解码）
IDE_PROC_NAME = "微信开发者工具.exe"

#: 种子脚本的**固定顺序**（`seed_entrust_demo` 依赖 `seed_demo` 造的基础数据）
SEEDS = ("seed_demo.py", "seed_entrust_demo.py", "seed_entrust_orgpicker.py")
#: 可选种子（存在才跑）
OPTIONAL_SEEDS = ("seed_contract_cases.py",)


class Env:
    """一次走查运行所需的路径与开关。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.repo = Path(args.repo).resolve()
        self.backend = self.repo / "backend"
        self.miniapp = self.repo / "miniapp"
        self.python = Path(args.python) if args.python else self._default_python()
        self.ide = Path(args.ide) if args.ide else self._default_ide()
        stamp = time.strftime("%H%M%S")
        self.work = (
            Path(args.work).resolve() if args.work else self.repo / "miniapp-device-artifacts"
        )
        self.work.mkdir(parents=True, exist_ok=True)
        self.shots = self.work / f"walk-{time.strftime('%Y%m%d')}-{stamp}"
        self.db = self.work / f"_walk_{stamp}.db"
        self.out = self.work / f"_walk_{stamp}_out.txt"
        self.uvlog = self.work / "_uvicorn.log"
        self.section = args.section
        self.pay = args.pay
        self.keep_db = args.keep_db

    def _default_python(self) -> Path:
        cand = self.repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if cand.exists():
            return cand
        raise SystemExit(f"找不到 venv 解释器 {cand}；用 --python 指定")

    def _default_ide(self) -> Path:
        for p in IDE_CANDIDATES:
            if p.exists():
                return p
        raise SystemExit("找不到微信开发者工具；用 --ide 指定可执行文件路径")

    def backend_env(self, db: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["APP_ENV"] = "development"
        env["ENTRUST_ENABLED"] = "true"
        env["DATABASE_URL"] = "sqlite:///" + (db or str(self.db)).replace("\\", "/")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def ide_env(self) -> dict[str, str]:
        """起 IDE 用的环境：摘掉 `ELECTRON_RUN_AS_NODE`，**并且不把本机沙箱代理传给它**。

        ⚠️ ① `ELECTRON_RUN_AS_NODE`（见模块 docstring 坑 ①）：Electron 会**以 Node 模式启动**
        —— 不建窗口、不写 GUI 日志、3 秒内 `rc=0` 主动退出。

        ⚠️ ② 代理是**显式决定，不是顺手删**：本机 `http_proxy` 指向一个**每个会话都换端口**的
        沙箱网关（实测 …→ 56351 → 61242），它服务的是**沙箱里的命令行**；而 IDE 是 GUI 程序，
        自己也要联网（登录态校验、扩展清单、资源拉取）。把一个"给命令行用"的代理塞给 GUI
        程序没有正当理由 —— 而且**实测由本项目起的 IDE，界面上会反复闪「网络故障」**。

        ⚠️ **诚实边界（有对照实测）**：这**不是**"闸门恒空"的原因 —— 2026-09-15 做了
        带/不带代理各起一次 IDE 的对照实测，**两边都没注册 automator**
        （各 1 样本，不构成因果证明，但足以否掉"代理是主因"这个猜测；
        反证还有一条：同一次只读探测里 CLI 带着同一个代理也成功了）。
        ⇒ 保留本改动的理由是**拆掉一个没有正当理由的耦合**（GUI 程序不该继承
        为沙箱命令行准备的代理），并顺带消掉界面上的「网络故障」闪提示，
        **而不是**宣称修好了闸门。

        ⚠️ 另有一条**踩过**的坑：Windows 上 `os.environ.get("http_proxy")` 是**大小写
        不敏感**的（实际存的键是 `HTTP_PROXY`），但 `dict(os.environ)` 拿到的是**原样**
        的键名 —— 用 `dict` 里查小写拼法会得到 `None`，于是"看起来没传代理"。
        所以这里一律用 `k.upper()` 比对，别用精确键名。
        """
        drop = {"ELECTRON_RUN_AS_NODE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
        return {k: v for k, v in os.environ.items() if k.upper() not in drop}


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ── 进程与端口 ────────────────────────────────────────────────────────────────


def ide_procs() -> list[str]:
    """当前 IDE 进程的 PID 列表（`tasklist` 输出是 GBK）。"""
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True)
    text = r.stdout.decode("gbk", errors="replace")
    return [line.split('","')[1] for line in text.splitlines() if "微信开发者工具" in line]


#: **本轮明确拥有的** IDE 进程 PID（HO 2026-09-15 D2 的恢复边界）。
#: 只有这里面的进程才允许被本运行器杀掉；别人的窗口（可能是 HO 正在用的）一律不碰。
OWNED_IDE_PIDS: set[int] = set()


def kill_all_ide_procs(wait_s: int = 25) -> int:
    """杀掉机器上**所有** `微信开发者工具` 进程。

    ⚠️ **默认不调用**。HO 2026-09-15 对 D2 的补充说得很直接：本运行器原先存在
    「按进程名清理全部实例」的路径，于是**自动恢复可能关掉 HO 正在使用的其他窗口**。
    ⇒ 只有操作方**显式**要求（`--kill-all-ide`）时才允许走这里，且必须在调用前
    说明影响。返回等待后仍剩的个数。
    """
    subprocess.run(["taskkill", "/F", "/IM", IDE_PROC_NAME], capture_output=True)
    for _ in range(wait_s):
        if not ide_procs():
            return 0
        time.sleep(1)
    return len(ide_procs())


def kill_owned_ide_procs(wait_s: int = 25) -> int:
    """**只**杀掉本轮自己起的 IDE（连子进程），返回仍属于本轮的剩余个数。

    为什么不按进程名一把清（见 `kill_all_ide_procs` 的说明）：Electron 是多进程，
    `Popen.kill()` 只杀父进程会残留子进程 ⇒ 我们需要 `taskkill /T` 按 **PID** 连子进程
    一起收；但「按 PID」也天然限定了作用域 —— **只收自己的**。
    """
    pids = sorted(OWNED_IDE_PIDS)
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    for _ in range(wait_s):
        alive = {int(p) for p in ide_procs()} & OWNED_IDE_PIDS
        if not alive:
            OWNED_IDE_PIDS.clear()
            return 0
        time.sleep(1)
    left = {int(p) for p in ide_procs()} & OWNED_IDE_PIDS
    return len(left)


def port_open(port: int = 8000) -> bool:
    import socket

    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def health(timeout: float = 2.0) -> bool:
    import urllib.request

    # ⚠️ 必须绕开代理：本机 http_proxy 会接管 127.0.0.1 并回 502（看起来像"后端没起来"）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8000/healthz", timeout=timeout) as resp:
            return int(resp.status) == 200
    except Exception:  # noqa: BLE001
        return False


# ── 生命周期 ──────────────────────────────────────────────────────────────────


def start_ide(env: Env, wait_s: int = 60, kill_all: bool = False):
    """起 IDE 并等它有进程；起不来返回 None（调用方判环境错）。

    ⚠️ **默认不杀别人的实例**（HO D2 的恢复边界）：本函数只**记录**自己新起的 PID，
    之后只允许 `kill_owned_ide_procs()` 收自己。需要真的把机器上其它实例也清掉时，
    必须走 `kill_all=True`（由 `--kill-all-ide` 显式指定）并先打印影响说明。
    """
    if kill_all:
        log("    ⚠️ --kill-all-ide：将按进程名清理**所有**微信开发者工具实例")
        log("       （会一并关掉人工打开的窗口；仅在你确认没有正在用的窗口时这样做）")
        left = kill_all_ide_procs()
        log(f"    全局清理后剩 {left}")
    else:
        # 只读观测：别人有几个实例我们**不动**，但要记下来，方便事后判断
        # "闸门不通是不是因为多实例互抢单实例锁"。
        others = sorted({int(p) for p in ide_procs()} - OWNED_IDE_PIDS)
        if others:
            log(f"    机器上已有 {len(others)} 个非本轮 IDE 进程 {others}（**不清理**，见 HO D2）")

    before = {int(p) for p in ide_procs()}
    try:
        proc = subprocess.Popen(
            [str(env.ide)],
            cwd=str(env.ide.parent),
            env=env.ide_env(),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x00000008 | 0x00000200 | 0x08000000,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"    启动异常: {str(exc)[:160]}")
        return None
    log(f"    pid {proc.pid}")
    # ⚠️ **立刻认领 launcher pid 本身**：Electron 的 renderer / GPU / utility 子进程
    # 是在随后几秒里陆续 spawn 的，早期版本只认"启动后第一次探测到的那几个"
    # ⇒ 后 spawn 的十几个进程没人认领，`kill_owned` 收不干净、残留下来
    # 和下一轮的实例互抢单实例锁（2026-09-16 实测：一次跑了 15 个漏网进程，
    # 下一轮闸门直接恒空）。`taskkill /T` 是**按树**杀，只要根 pid 在手里就够。
    OWNED_IDE_PIDS.add(int(proc.pid))
    for i in range(wait_s // 3):
        time.sleep(3)
        now = {int(p) for p in ide_procs()}
        OWNED_IDE_PIDS.update(now - before)  # 顺带把已出现的也认领
        n = len(now)
        if n:
            log(f"    [{(i + 1) * 3}s] IDE 进程 {n} 个（本轮拥有 {len(OWNED_IDE_PIDS)} 个）")
            return proc
        log(f"    [{(i + 1) * 3}s] 还没起来…")
    return None


#: 就绪闸门：**单次探测**超时与**总预算**（秒）。
#: ⚠️ 都别沿用客户端默认的 150s —— 见 `wait_ready` 的 docstring（实测 18 次探测烧掉 56 分钟）。
#: ⚠️ **单次超时不要收窄到 45s**（2026-09-17 改正）：冷启动**第一次**调用实测就要 ~61s，
#: 45s 会让**每一次**都超时，于是"还没起来"被读成"通道坏了"。取 90s。
GATE_PROBE_S = 90
#: 总预算：冷 `CompileCache` 下本项目编译约需 **9 分钟**（技能坑 49），故取 15 分钟。
GATE_BUDGET_S = 900

#: IDE 侧「automation runtime 未注册」的签名（回执里出现任一条即命中）。
#: 实测原文长这样（同一个 IDE 上连续 12+ 次都一样）：
#:   `errorType=MCP_TOOL_ERROR message=timeout waiting for automator response`
#:   `errorType=MCP_TOOL_ERROR message=cant find runtimeid by projectpath <项目路径>`
AUTOMATION_DEAD_SIGNS = (
    "timeout waiting for automator response",
    "cant find runtimeid by projectpath",
)
#: 连续命中多少次就**早退**（不再等满 `GATE_BUDGET_S`）。
#: 取 4 是有依据的，不是拍脑袋：两次**成功**的实测里，命中次数分别是
#: 「2 次命中后第 3 次成功」与「1 次命中 + 1 次 ok-empty 后第 4 次成功」；
#: 而失败的那两次是**连续 12～13 次**全命中。⇒ 4 不会误杀会自愈的冷启动，
#: 又能把失败判定从 240s 压到约 40～60s。
AUTOMATION_DEAD_MAX = 4
#: ⭐ **早退的墙钟下限**（秒）—— 2026-09-17 加这一条，因为"连续 4 次"在**冷启动**期间
#: 是个**必然命中**的计数：项目加载完成前每一次探测都会命中该签名。
#: 实测（同一天两轮）：闸门分别在 **162.6s** 与 **119.4s** 早退，
#: 而 IDE 自己的日志在同一分钟里正写着
#:   `13:39:19.882 [pageframe] finish load user code` / `13:39:20.374 [devtools] webview page ready`
#: —— **早退恰好发生在项目刚加载完的那一刻**。
#: ⇒ 给早退加一个"先等够 6 分钟"的前置条件：低于它**只记录不早退**，
#: 高于它才允许按连续计数收手。这样既不误杀冷启动，也保住了"真坏了就别等满预算"。
AUTOMATION_DEAD_MIN_ELAPSED_S = 360


def _brief(obj: object) -> str:
    """把回执压成一行关键信息（`ok` / `errorType` / 截断原文），供日志打印原因。"""
    if not isinstance(obj, dict):
        return str(obj)[:140]
    bits = [f"ok={obj.get('ok')}"]
    for key in ("errorType", "error", "message"):
        if obj.get(key):
            bits.append(f"{key}={str(obj[key])[:70]}")
    if obj.get("__raw__"):
        bits.append(f"raw={str(obj['__raw__'])[:90]}")
    return " ".join(bits)


def wait_ready(
    env: Env,
    budget_s: int = GATE_BUDGET_S,
    probe_s: int = GATE_PROBE_S,
) -> bool:
    """就绪判据：**能开窗 + `pageStack` 非空**，且轮询（见 docstring 坑 ③）。

    ⚠️ 2026-09-15 实测：早先按**次数**轮询（`tries=24`）且每次调用都吃满默认 150s 超时
    ⇒ 一轮闸门最坏 `24 × 2 × 150s ≈ 120 分钟`。实际一次 `--section 8,8b --pay`
    里 **18 次探测耗掉 56 分钟**、页面栈**全程为空**：日志只有一行行 `pageStack=[]`，
    **耗时不写进去 ⇒ 完全看不出"慢在哪"**，于是被读成"卡死 / 网络抖动"。

    ⇒ 改成**按墙钟预算**轮询 + **收窄单次探测超时**，并打印三件事：
    真实耗时、单次调用耗时、以及**回执原文**（`ok` / `errorType`）—— 让"为什么空"可见。
    `pageStack=[]` 且 `ok=true`（窗口没进小程序页）与 `ok=false`（通道/授权问题）
    处置完全不同，**不能只看空列表**。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wechatide_client import Client  # noqa: PLC0415

    client = Client(project=str(env.miniapp), timeout=probe_s)
    t0 = time.time()
    last: dict = {}
    dead = 0
    while time.time() - t0 < budget_s:
        t1 = time.time()
        win = client.open_window(timeout=probe_s)
        t2 = time.time()
        stack, receipt = client.page_stack_probe(timeout=probe_s)
        t3 = time.time()
        last = receipt or {}
        hit = receipt if not receipt.get("ok") else win
        log(
            f"    [{t3 - t0:6.1f}s] pageStack={str(stack)[:110]}"
            f"  (开窗 {t2 - t1:5.1f}s / 取栈 {t3 - t2:5.1f}s / IDE {len(ide_procs())} 个)"
            f"  回执 {_brief(hit)}"
        )
        if stack:
            return True
        # ⚠️ **早退**：命中"automation runtime 未注册"的签名时，等满预算也不会好 ——
        #    同一个 IDE 重试不会自愈（实测连续 12+ 次全命中），正确动作是**换一个
        #    已经注册好的实例**（`--skip-ide`），见 docstring 坑 ⑤ / DR-0009 §8.5⑨。
        if any(s in str(hit) for s in AUTOMATION_DEAD_SIGNS):
            dead += 1
            # ⭐ 早退只在**等够墙钟下限**之后才允许（见 `AUTOMATION_DEAD_MIN_ELAPSED_S`）：
            #    冷启动期间该签名是必然命中，按次数收手会把"还在编译"误判成"通道坏了"。
            if dead >= AUTOMATION_DEAD_MAX and t3 - t0 >= AUTOMATION_DEAD_MIN_ELAPSED_S:
                log(
                    f"    ✗ 连续 {dead} 次命中「automation runtime 未注册」"
                    f"（{_brief(hit)}），且已等够 {AUTOMATION_DEAD_MIN_ELAPSED_S}s"
                    f" ⇒ 早退，不再等满 {budget_s}s 预算。"
                )
                log("      ⇒ 处置：① 只读探测**已在运行**的 IDE，能用就 `--skip-ide` 复用它；")
                log("              ② 不能用再看 IDE 窗口是否有弹层/网络提示，然后清场重起。")
                return False
            if dead >= AUTOMATION_DEAD_MAX:
                log(
                    f"    ⏳ 连续 {dead} 次命中同一签名，但只过了 {t3 - t0:.0f}s "
                    f"（< {AUTOMATION_DEAD_MIN_ELAPSED_S}s）⇒ **不早退**，"
                    "按冷启动继续等（项目可能仍在编译）。"
                )
        else:
            dead = 0
        time.sleep(3)
    log(f"    ✗ 闸门预算 {budget_s}s 用尽，页面栈仍为空。最后回执：{_brief(last)}")
    return False


def probe_existing_ide(env: Env, probe_s: int = 25) -> tuple[bool, str]:
    """**只读**探测：当前**已在运行**的 IDE 能不能给出 `pageStack`？

    ⚠️ 本函数**不启动、不杀**任何进程 —— 这正是它的价值：在"清场重起"之前，
    先问一句"现成的那个能不能用"。实测（2026-09-15）：同一个 IDE 上运行器起的实例
    闸门恒空，而对**已在运行**的那一个只读探测立刻拿到 `pageStack`
    ⇒ 该 IDE 是可用的、**应该复用它**（`--skip-ide`），省掉"起 IDE + 闸门"两段
    （那一次 31/32 两章 **1 分 43 秒**跑完 24 项断言）。

    返回 `(是否可用, 一句话原因)`；原因要能区分"没有 IDE"、"探测异常"与"回执说不行"。
    """
    if not ide_procs():
        return False, "没有正在运行的 IDE 进程"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wechatide_client import Client  # noqa: PLC0415

    try:
        client = Client(project=str(env.miniapp), timeout=probe_s)
        stack, receipt = client.page_stack_probe(timeout=probe_s)
    except Exception as exc:  # noqa: BLE001
        return False, f"探测异常：{str(exc)[:120]}"
    if stack:
        return True, f"pageStack={str(stack)[:90]}"
    return False, f"回执 {_brief(receipt)}"


def start_backend(env: Env, wait_s: int = 45):
    """起后端并等它真能应答；返回 Popen（调用方负责 stop）。"""
    # ⚠️ 这里**故意不用 `with`**：句柄要活到后端进程结束。局部变量被 GC 后，
    #    Windows 上可能连带关掉子进程的 stdout，于是日志只写一半。
    handle = open(env.uvlog, "a", encoding="utf-8", errors="replace")  # noqa: SIM115
    handle.write(f"\n===== {time.strftime('%H:%M:%S')} 起后端 =====\n")
    handle.flush()
    # ⚠️ 句柄要保住引用：局部变量被 GC 后，Windows 上可能连带关掉子进程的 stdout
    proc = subprocess.Popen(
        [str(env.python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(env.backend),
        env=env.backend_env(),
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    proc._log_handle = handle  # type: ignore[attr-defined]
    for _ in range(wait_s):
        if health():
            return proc
        time.sleep(1)
    return proc


def stop_backend(proc) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            proc.kill()


def run_step(
    env: Env,
    args: list[str],
    label: str,
    cwd: Path | None = None,
    timeout: int = 1800,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    """跑一步子进程（与后端同环境），并回显尾部输出。"""
    child_env = env.backend_env()
    if env_extra:
        child_env.update(env_extra)
    done = subprocess.run(
        args,
        cwd=str(cwd or env.backend),
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    log(f"--- {label} rc={done.returncode}")
    for line in [x for x in (done.stdout or "").splitlines() if x.strip()][-14:]:
        log(f"    {line}")
    if done.returncode:
        log((done.stderr or "")[-2000:])
    return done


# ── 主流程 ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真机走查一站式运行器（IDE + 后端 + 种子 + 走查）")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument(
        "--work",
        default=None,
        help="产物目录（db / 日志 / 截图）；默认 repo/miniapp-device-artifacts",
    )
    parser.add_argument("--section", default="all", help="走查章节，如 all / 8,8b / 25")
    parser.add_argument(
        "--pay", action="store_true", help="跑有副作用的支付流转（⑧b；会消耗演示锚点）"
    )
    parser.add_argument("--ide", default=None, help="微信开发者工具可执行文件路径")
    parser.add_argument("--python", default=None, help="后端解释器（默认 repo/.venv）")
    parser.add_argument("--keep-db", action="store_true", help="跑完保留临时库（便于事后查数据）")
    parser.add_argument("--skip-ide", action="store_true", help="只起后端跑走查（IDE 已在跑时用）")
    parser.add_argument(
        "--kill-all-ide",
        action="store_true",
        help="⚠️ 允许按进程名清理**所有**微信开发者工具实例（会关掉人工打开的窗口）",
    )
    args = parser.parse_args(argv)

    if os.name != "nt":
        log("本脚本依赖 Windows（tasklist/taskkill + 微信开发者工具），不支持当前平台")
        return 2

    env = Env(args)
    log(f"repo    : {env.repo}")
    log(f"work    : {env.work}")
    log(f"shots   : {env.shots}")
    log(f"ide     : {env.ide}")
    log(f"python  : {env.python}")

    ide = None
    reuse = bool(args.skip_ide)
    if not reuse:
        # ⚠️ 先问一句"现成的能不能用"，再决定要不要清场重起 —— 见 docstring 坑 ⑤。
        log("\n⓪ 先探一次：现成的 IDE 能用吗？（只读探测，**不启不停**任何进程）")
        ok, why = probe_existing_ide(env)
        log(f"    {'✅ 能用' if ok else '✗ 不能用'}：{why}")
        if ok:
            log("    ⇒ 自动改用**复用**模式（等价 --skip-ide）：省掉「起 IDE + 闸门」两段")
            reuse = True
        else:
            log("    ⇒ 按常规清场重起")
    if not reuse:
        log("\n① 起 IDE（摘掉 ELECTRON_RUN_AS_NODE，且不传本机沙箱代理）")
        ide = start_ide(env, kill_all=bool(args.kill_all_ide))
        log("\n② 就绪闸门（open_window + pageStack 非空）")
        ready = bool(ide) and wait_ready(env)
        if not ready:
            # **有界恢复**：只收**本轮自己起的**实例再试一轮。
            # ⚠️ 不再"按进程名清光全场"——那会关掉 HO 正在用的窗口（HO 2026-09-15 对 D2 的补充）。
            # 只 kill 父进程会残留子进程 ⇒ 重起会变成两个实例互抢单实例锁（坑 ②），
            # 所以这里用 `kill_owned_ide_procs()`（按 PID + `/T` 连子进程，作用域仅限自己）。
            log("    首次闸门未过 ⇒ 有界恢复：只收**本轮自己起的**实例，再试一轮…")
            log(f"    本轮实例清理后剩 {kill_owned_ide_procs()}")
            time.sleep(5)
            ide = start_ide(env, kill_all=False)
            ready = bool(ide) and wait_ready(env)
        if not ready:
            others = sorted({int(p) for p in ide_procs()} - OWNED_IDE_PIDS)
            log("模拟器没就绪（pageStack 恒空）—— **环境阻塞（ENV_BLOCKED）**，不是业务结论")
            log("  处置顺序：① 只读探测**已在运行**的 IDE，能用就 --skip-ide 复用它；")
            log("            ② 不能用再看 IDE 窗口是否有弹层/网络提示；")
            log("            ③ 仍不通且确认没有人工窗口在用，才用 --kill-all-ide 显式全局清理。")
            if others:
                log(
                    f"  ⚠️ 机器上还有 {len(others)} 个**非本轮** IDE 进程 {others} —— "
                    "本运行器**不清理**它们（可能是人工正在用的窗口）。"
                )
            log("  退出码 2（环境阻塞）：这条不代表任何业务结论，也不计入通过。")
            kill_owned_ide_procs()
            return 2
        log("    ✅ 模拟器已就绪")

    log(f"\n③ 起后端（8000，临时库 {env.db.name}）")
    proc = start_backend(env)
    if not health():
        log("后端没起来，终止")
        stop_backend(proc)
        return 2
    log("    后端就绪")

    rc = 0
    try:
        log("\n④ 迁移 + 种子（顺序固定）")
        rc |= run_step(env, [str(env.python), "migrate.py"], "migrate").returncode
        for name in SEEDS:
            rc |= run_step(
                env, [str(env.python), str(Path("scripts") / name)], name.removesuffix(".py")
            ).returncode
        for name in OPTIONAL_SEEDS:
            if (env.backend / "scripts" / name).exists():
                rc |= run_step(
                    env, [str(env.python), str(Path("scripts") / name)], name.removesuffix(".py")
                ).returncode
        if rc:
            log("种子阶段失败，终止")
            return 1

        log(f"\n⑤ 跑走查章节 {env.section}（pay={env.pay}）")
        extra = {"WALK_PAY": "1"} if args.pay else None
        done = run_step(
            env,
            [
                str(env.python),
                str(Path("scripts") / "verify_miniapp_devtools.py"),
                "--shots",
                str(env.shots),
                "--section",
                env.section,
            ],
            "verify_miniapp_devtools",
            cwd=env.repo,
            timeout=5400,
            env_extra=extra,
        )
        with open(env.out, "w", encoding="utf-8") as fh:
            fh.write(
                f"===== 走查 stdout =====\n{done.stdout or '(空)'}\n"
                f"===== 走查 stderr =====\n{done.stderr or '(空)'}\n"
            )
        log(f"\n走查 rc={done.returncode}；截图 {env.shots}")
        log(f"完整输出已写入 {env.out}")
        return done.returncode
    finally:
        stop_backend(proc)
        log("后端已停止")
        # 退出时**只收本轮自己起的** IDE（HO D2：不得默认清场）。
        # 复用模式下 `OWNED_IDE_PIDS` 为空 ⇒ 一个都不动，人工窗口完好。
        if OWNED_IDE_PIDS:
            log(f"清理本轮 IDE 实例（{len(OWNED_IDE_PIDS)} 个）：剩 {kill_owned_ide_procs()}")
        else:
            log("本轮未起过 IDE（复用模式）⇒ 不触碰任何 IDE 进程")
        if not env.keep_db and env.db.exists():
            with contextlib.suppress(OSError):
                env.db.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
