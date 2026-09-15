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
⇒ 起 IDE 前、闸门失败重试前、判环境错退出前，都要 `kill_ide_procs()` 清场。

**③ 就绪闸门不能以 `check_wechatide_status().ok` 为判据。**
它偶发回 `CONNECT_ERROR`，而**同一时刻** `open_project_window` / `page_stack` 都正常。
⇒ 判据是「**能开窗 + `pageStack` 非空**」，且**要轮询**（实测前几次可能为空）。

**④ 闸门的"轮询"必须带*墙钟预算*，单次探测必须*收窄超时*。**
客户端默认超时是 150s；早先闸门按**次数**轮询（24 次）× 每次最多两调
⇒ 一轮最坏 ≈ `24 × 2 × 150s = 120 分钟`。实测一次 `--section 8,8b --pay`：
**18 次探测烧掉 56 分钟**、页面栈全程为空，而日志只有一行行 `pageStack=[]`
（**不打印耗时 ⇒ 完全看不出慢在哪**），于是被读成"卡死 / 网络抖动"。
⇒ 现改为 `GATE_PROBE_S=45`（单次）+ `GATE_BUDGET_S=240`（总预算），
并打印真实耗时、单次调用耗时、进程数与**回执**：`ok=true` 且栈空 = 窗口没进小程序页；
`ok=false` = 通道/授权问题 —— **两者处置不同，不能只看空列表**。

用法
----
    python scripts/run_walkthrough_devtools.py --section all
    python scripts/run_walkthrough_devtools.py --section 8,8b --pay
    python scripts/run_walkthrough_devtools.py --section 25 --work D:\\tmp\\walk

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
        """起 IDE 用的环境：**摘掉 `ELECTRON_RUN_AS_NODE`**（见模块 docstring 坑 ①）。"""
        return {k: v for k, v in os.environ.items() if k.upper() != "ELECTRON_RUN_AS_NODE"}


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ── 进程与端口 ────────────────────────────────────────────────────────────────


def ide_procs() -> list[str]:
    """当前 IDE 进程的 PID 列表（`tasklist` 输出是 GBK）。"""
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True)
    text = r.stdout.decode("gbk", errors="replace")
    return [line.split('","')[1] for line in text.splitlines() if "微信开发者工具" in line]


def kill_ide_procs(wait_s: int = 25) -> int:
    """杀掉**所有**残留 IDE 进程，返回等待后仍剩的个数（见 docstring 坑 ②）。"""
    subprocess.run(["taskkill", "/F", "/IM", IDE_PROC_NAME], capture_output=True)
    for _ in range(wait_s):
        if not ide_procs():
            return 0
        time.sleep(1)
    return len(ide_procs())


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


def start_ide(env: Env, wait_s: int = 60):
    """起 IDE 并等它有进程；起不来返回 None（调用方判环境错）。先清场再起。"""
    left = kill_ide_procs()
    log(f"    清理残留 IDE：剩下 {left}")
    if left:
        log(f"    ⚠️ 仍有 {left} 个进程没清掉 —— 闸门大概率不通")
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
    for i in range(wait_s // 3):
        time.sleep(3)
        n = len(ide_procs())
        if n:
            log(f"    [{(i + 1) * 3}s] IDE 进程 {n} 个")
            return proc
        log(f"    [{(i + 1) * 3}s] 还没起来…")
    return None


#: 就绪闸门：**单次探测**超时与**总预算**（秒）。
#: ⚠️ 都别沿用客户端默认的 150s —— 见 `wait_ready` 的 docstring（实测 18 次探测烧掉 56 分钟）。
GATE_PROBE_S = 45
GATE_BUDGET_S = 240


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
        time.sleep(3)
    log(f"    ✗ 闸门预算 {budget_s}s 用尽，页面栈仍为空。最后回执：{_brief(last)}")
    return False


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
    if not args.skip_ide:
        log("\n① 起 IDE（摘掉 ELECTRON_RUN_AS_NODE）")
        ide = start_ide(env)
        log("\n② 就绪闸门（open_window + pageStack 非空）")
        ready = bool(ide) and wait_ready(env)
        if not ready:
            # 先**彻底清场**再重来：只 kill 父进程会残留子进程 ⇒ 直接重起会变成
            # 两个实例互抢单实例锁，越试越不通（见 docstring 坑 ②）。
            log("    首次闸门未过，先清场内所有 IDE 实例再试一轮…")
            log(f"    清理后剩 {kill_ide_procs()}")
            time.sleep(5)
            ide = start_ide(env)
            ready = bool(ide) and wait_ready(env)
        if not ready:
            log("模拟器没就绪（pageStack 恒空）—— 环境错误，不是业务结论")
            kill_ide_procs()
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
        if not env.keep_db and env.db.exists():
            with contextlib.suppress(OSError):
                env.db.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
