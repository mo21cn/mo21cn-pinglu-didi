"""启动期的**演示环境准备**（铺种子 ＋ 一次性动作）。

本脚本同时承载两类事，**各自看自己的环境变量、互不依赖**：

| 环境变量 | 动作 | 语义 |
| --- | --- | --- |
| `SEED_ON_BOOT=true` | 按序铺委托侧种子（幂等） | 见下方"执行顺序" |
| `BIND_DEMO_IDENTITY=<openid｜user:<id>>` | 把演示账号的 openid **过继**给真实登录账号 | 一次性 |
| `DEMO_GRANT_ORG_MEMBER=<openid｜user:<id>>` | 给该账号补**组织成员资格**（受理台才有数据） | 一次性 |

⛔ 一次性动作**不接受** `auto` / `true`（无法证明那条账号就是操作者本人的微信号）；
⛔ 一次性动作**独立于** `SEED_ON_BOOT` 与播种守卫 —— 过继之后播种守卫会跳过铺种子，
若把一次性动作写在守卫之后并由守卫早退，它们会被一并吞掉（2026-09-24 实测的陷阱）。

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

⚠️ 重复铺种子的防护（2026-09-23 评审后补）
----------------------------------------
身份过继（见 `scripts/bind_demo_identity.py`）会把演示账号 `mock-openid-seed-shipper`
的 openid **换成真实 openid**。此后若再跑一次种子，种子会**按 openid 找不到演示账号
→ 新建一个 `mock-openid-seed-shipper` 账号**，而它的 `user_id` 与既有 `ent_*` 数据不同
⇒ 判定为"没铺过"⇒ **把同一批样本再铺一遍**（第二套样本）。

因此铺种子前先探一次演示账号状态（`_seed_skip_reason`）：
- `fresh`（从未铺过）→ 正常铺；
- `seeded`（演示账号原样）→ 正常铺（种子幂等）；
- `bound`（演示账号不在、但委托数据在）→ **跳过**，避免第二套样本；
- `unknown`（库不可达/缺表）→ **跳过**（宁可少铺，也不要铺重）。

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


def _demo_state() -> str:
    """探测演示账号状态（`seeded` / `bound` / `fresh` / `unknown`）。

    直接复用 `scripts/bind_demo_identity.demo_state`，避免两处判定逻辑漂移。
    任何异常都归为 `unknown` —— 调用方对 `unknown` 一律**跳过铺种子**（fail-safe）。
    """
    try:
        from app.core.database import SessionLocal
        from scripts.bind_demo_identity import demo_state

        db = SessionLocal()
        try:
            return demo_state(db)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — 探测失败不该让服务起不来
        print(f"[boot_seed] !! 探测演示账号状态失败: {type(exc).__name__}: {exc}", flush=True)
        return "unknown"


def _seed_skip_reason(state: str) -> str | None:
    """返回需要跳过铺种子的原因；None = 可以铺。"""
    if state in {"fresh", "seeded"}:
        return None
    if state == "bound":
        return (
            "演示账号的 openid 已被过继（找不到 mock-openid-seed-shipper，但委托数据已存在）"
            "⇒ 跳过铺种子，避免把同一批样本再铺一遍"
        )
    return "无法判定演示账号状态（库不可达或缺表）⇒ 跳过铺种子（宁可少铺，也不要铺重）"


def _run_script(name: str, extra: tuple[str, ...] = (), *, timeout: int = 600) -> bool:  # noqa: FBT001,FBT002
    """跑一个 scripts/ 下的脚本，回显尾部输出；返回是否成功。"""
    cmd = [sys.executable, os.path.join("scripts", name), *extra]
    print(f"[boot_seed] → {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"[boot_seed] !! {name} 超时（>{timeout}s）", flush=True)
        return False
    except OSError as exc:
        print(f"[boot_seed] !! {name} 无法执行: {exc}", flush=True)
        return False

    tail = (proc.stdout or "").strip().splitlines()
    for line in tail[-8:]:
        print(f"[boot_seed]    {line}", flush=True)
    if proc.returncode != 0:
        print(f"[boot_seed] !! {name} 退出码 {proc.returncode}", flush=True)
        for line in (proc.stderr or "").strip().splitlines()[-5:]:
            print(f"[boot_seed]    ERR {line}", flush=True)
        return False
    return True


def _explicit_target_args(raw: str, *, mode: str) -> tuple[tuple[str, ...] | None, str | None]:
    """把 `<openid>` / `user:<id>` 翻成脚本参数；`auto` / `true` 之类一律拒绝。

    ⛔ 为什么拒绝：那两个值会落到"自动挑一个账号"的语义上 —— 无法证明挑中的就是
    操作者本人的微信号（详见 `scripts/bind_demo_identity.py` 的模块说明）。
    返回 `(args, None)` 表示可用；`(None, 原值)` 表示非法。
    """
    value = raw.strip()
    if value.lower() in {"auto", "true", "1", "yes"}:
        return None, value
    if value.startswith("user:"):
        return ("--mode", mode, "--user-id", value[len("user:") :]), None
    return ("--mode", mode, "--openid", value), None


#: 启动期的**一次性动作**：`(环境变量名, 脚本名, 子命令, 超时秒)`
ONESHOTS: tuple[tuple[str, str, str, int], ...] = (
    # 把演示账号的 openid 过继给真实登录账号（种子数据归属切换）
    ("BIND_DEMO_IDENTITY", "bind_demo_identity.py", "bind", 300),
    # 给演示账号补组织成员资格（让"受理台"也有数据：一个微信号走完整条链）
    ("DEMO_GRANT_ORG_MEMBER", "grant_demo_membership.py", "grant", 300),
)


def _run_oneshots() -> list[str]:
    """跑所有**已配置**的一次性动作，返回失败项。

    ⚠️ 刻意**独立于** `SEED_ON_BOOT` 与播种守卫：过继之后播种守卫会跳过铺种子
    （避免第二套样本），但"补组织成员资格"这类动作仍需要在**同一个容器启动**里执行 ——
    若把它放在守卫之后并由守卫早退，就会被一起吞掉（2026-09-24 实测的设计陷阱）。
    """
    failed: list[str] = []
    for env_name, script, mode, timeout in ONESHOTS:
        raw = os.getenv(env_name, "").strip()
        if not raw:
            continue
        extra, bad = _explicit_target_args(raw, mode=mode)
        if extra is None:
            failed.append(f"{script}(参数非法)")
            print(
                f"[boot_seed] ⛔ {env_name}={bad} 不接受：auto/true 无法证明那条账号是你本人的",
                flush=True,
            )
            print(f"[boot_seed]    请改用显式值：{env_name}=<openid>（或 user:<id>）", flush=True)
            continue
        if not _run_script(script, extra, timeout=timeout):
            failed.append(script)
    return failed


def main() -> int:
    """按序铺种子 + 跑一次性动作；**始终返回 0**（不阻塞服务启动）。"""
    failed: list[str] = []

    if os.getenv("SEED_ON_BOOT", "").strip().lower() == "true":
        print("[boot_seed] SEED_ON_BOOT=true，等待后端就绪 …", flush=True)
        if not _wait_backend():
            print("[boot_seed] !! 后端未就绪，放弃铺种子（不影响服务）", flush=True)
        else:
            # 铺之前先确认「没被过继过」——否则会铺出第二套样本（见模块 docstring）
            state = _demo_state()
            print(f"[boot_seed] 演示账号状态 = {state}", flush=True)
            reason = _seed_skip_reason(state)
            if reason:
                print(f"[boot_seed] ⛔ {reason}", flush=True)
                print(
                    "[boot_seed]    如需重铺：先在隔离库上核对，再手工处理演示账号后再开 SEED_ON_BOOT",
                    flush=True,
                )
            else:
                for name, extra in SEED_STEPS:
                    if not _run_script(name, extra):
                        failed.append(name)
    else:
        print("[boot_seed] SEED_ON_BOOT 未开启，跳过铺种子", flush=True)

    failed.extend(_run_oneshots())

    if failed:
        print(f"[boot_seed] 结束，但以下步骤失败：{failed}", flush=True)
    else:
        print("[boot_seed] 启动期任务执行完成 ✓", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
