"""启动期铺演示数据 —— 仅当环境变量 `SEED_ON_BOOT=true` 时执行。

为什么需要它
------------
云托管容器**没有 Shell**（「云端调试」只是 HTTP 调试器），`scripts/seed_*.py`
无法人工执行；而演示数据**只能在容器内铺** —— `seed_entrust_*.py` 是**直连库**的，
云库又是内网地址（`10.1.105.83`），容器外根本连不上。

执行顺序（对齐 CI 的种子序列，但**去掉 `seed_demo.py`**）
--------------------------------------------------------
1. `seed_entrust_demo.py`                    —— 委托支线演示（直连库、幂等）
2. `seed_entrust_canonical.py`               —— 规范夹具（直连库）
3. `seed_entrust_contract_flow.py --derive`  —— 合同派生夹具（直连库）

⚠️ 为什么不跑 `seed_demo.py`：它走 HTTP 且以 `code=seed-shipper` 登录，
云端 `APP_ENV=production` 会去**真实调微信 code2session** ⇒ 必然失败；
而放开 `seed-*` 直通，等于让任何人凭一个固定字符串就能登录成演示账号 —— 不可接受。
平台侧演示数据（货源/船舶/订单）需另行设计。

⚠️ 这是一次性动作
----------------
铺完请把云侧 `SEED_ON_BOOT` 改回 `false`（或删除），避免每次部署重复插入。

为什么失败**不**阻塞服务
----------------------
与 `migrate.py` 不同：迁移决定"服务能否正确工作"（缺表 ⇒ 接口整组 500），
必须 fail fast；而种子只决定"界面有没有数据"，服务本身是好的。
让服务因为"演示数据没铺上"而起不来，是把小问题放大成大故障。
⇒ 失败**明确打印**（不静默），但不影响 uvicorn 启动。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HEALTH_URL = "http://127.0.0.1:8000/healthz"

SEED_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # ⛔ 这里**故意不含 `seed_demo.py`**：它走 HTTP 并以 `code=seed-shipper` 登录，
    #    而云端是 `APP_ENV=production` ⇒ 会去真实调微信 code2session ⇒ 必然失败。
    #    （`seed-*` 直通只在**非生产**生效，且**不能**放开 —— 否则任何人都能用
    #     `seed-shipper` 登录成演示账号。）平台侧演示数据（货源/船舶/订单）待单独设计。
    ("seed_entrust_demo.py", ()),
    ("seed_entrust_canonical.py", ()),
    ("seed_entrust_contract_flow.py", ("--derive",)),
)


def _wait_backend(timeout_seconds: int = 180) -> bool:
    """等后端健康检查通过（seed_demo 走 HTTP，必须等服务就绪）。"""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(2)
    return False


def main() -> int:
    """按序铺种子；始终返回 0（不阻塞服务启动）。"""
    if os.getenv("SEED_ON_BOOT", "").strip().lower() != "true":
        print("[boot_seed] SEED_ON_BOOT 未开启，跳过铺种子", flush=True)
        return 0

    print("[boot_seed] SEED_ON_BOOT=true，等待后端就绪 …", flush=True)
    if not _wait_backend():
        print("[boot_seed] !! 后端未就绪，放弃铺种子（不影响服务）", flush=True)
        return 0

    failed: list[str] = []
    for name, extra in SEED_STEPS:
        cmd = [sys.executable, os.path.join("scripts", name), *extra]
        print(f"[boot_seed] → {' '.join(cmd)}", flush=True)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, check=False
            )
        except subprocess.TimeoutExpired:
            failed.append(name)
            print(f"[boot_seed] !! {name} 超时（>600s）", flush=True)
            continue
        except OSError as exc:
            failed.append(name)
            print(f"[boot_seed] !! {name} 无法执行: {exc}", flush=True)
            continue

        for line in (proc.stdout or "").strip().splitlines()[-3:]:
            print(f"[boot_seed]    {line}", flush=True)
        if proc.returncode != 0:
            failed.append(name)
            print(f"[boot_seed] !! {name} 退出码 {proc.returncode}", flush=True)
            for line in (proc.stderr or "").strip().splitlines()[-5:]:
                print(f"[boot_seed]    ERR {line}", flush=True)

    if failed:
        print(f"[boot_seed] 结束，但以下步骤失败：{failed}", flush=True)
    else:
        print("[boot_seed] 全部种子执行完成 ✓", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
