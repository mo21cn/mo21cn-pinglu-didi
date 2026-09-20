# -*- coding: utf-8 -*-
"""P2 复现实验（仓库外）：定位 `reset_db()` 里 `unlink` 的占用来源。

手法：起后端（连隔离库）→ terminate + wait（复刻 stop_backend）→ **立刻** unlink；
      失败则 +1s / +3s 各重试一次，并数一数当时还活着几个 python 进程。

判据（先立后看）：
  * 立刻失败 / 稍后成功 ⇒ **刚被杀进程的句柄释放竞态**（或 AV/索引器瞬时锁）⇒ 有界等待即可；
  * 一直失败           ⇒ 存在**存活进程**持库 ⇒ 必须先回收进程，再谈删文件。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path("E:/pinglu-didi")
BACKEND = ROOT / "backend"
VENV_PY = ROOT / ".venv/Scripts/python.exe"
DB = Path("E:/_diag/p2probe/probe.db")
DB.parent.mkdir(parents=True, exist_ok=True)


def env_for(db: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        APP_ENV="development",
        ENTRUST_ENABLED="true",
        DATABASE_URL="sqlite:///" + str(db).replace("\\", "/"),
        LLM_MOCK="true",
        WECHAT_MOCK="true",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    env.pop("LLM_API_KEY", None)
    return env


def health() -> bool:
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open("http://127.0.0.1:8000/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def n_python() -> int:
    try:
        out = subprocess.run(["tasklist", "/fo", "csv"], capture_output=True, timeout=20)
        txt = out.stdout.decode("gbk", "replace")
    except Exception:
        return -1
    return sum(1 for line in txt.splitlines() if "python.exe" in line.lower())


def try_unlink(tag: str) -> bool:
    ok = True
    detail = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(DB) + suffix)
        if not p.is_file():
            continue
        t0 = time.time()
        try:
            p.unlink()
            detail.append(f"{p.name} ok({(time.time() - t0) * 1000:.0f}ms)")
        except PermissionError as e:
            ok = False
            detail.append(f"{p.name} **PermissionError** {e}")
    print(f"   [{tag}] => {'OK' if ok else 'FAIL'} | " + " ; ".join(detail))
    return ok


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    fails_immediate = 0
    for n in range(1, rounds + 1):
        print(f"=== 第 {n} 轮 ===")
        for suf in ("", "-wal", "-shm", "-journal"):
            p = Path(str(DB) + suf)
            if p.is_file():
                try:
                    p.unlink()
                except PermissionError:
                    time.sleep(1)
                    p.unlink()
        subprocess.run(
            [str(VENV_PY), "migrate.py", "--verify"],
            cwd=str(BACKEND),
            env=env_for(DB),
            capture_output=True,
            timeout=300,
        )
        log = open(str(DB.parent / "uv.log"), "a", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=str(BACKEND),
            env=env_for(DB),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        up = False
        for _ in range(60):
            if health():
                up = True
                break
            if proc.poll() is not None:
                break
            time.sleep(1)
        print(f"   后端 {'就绪' if up else '未就绪'} pid={proc.pid}；python 进程数={n_python()}")

        proc.terminate()
        try:
            proc.wait(timeout=10)
            print("   terminate→wait() 返回 rc =", proc.returncode)
        except Exception as e:  # noqa: BLE001
            print("   wait 超时/异常：", e, "⇒ 退到 kill()")
            proc.kill()

        if not try_unlink("立刻"):
            fails_immediate += 1
            time.sleep(1.0)
            if not try_unlink("+1s"):
                time.sleep(2.0)
                try_unlink("+3s")
            print(f"   当时 python 进程数={n_python()}")
        log.close()
    print(f"=== 结束：立刻删除失败 {fails_immediate}/{rounds} 轮 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
