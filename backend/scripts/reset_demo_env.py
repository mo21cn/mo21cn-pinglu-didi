"""最小隔离复位：把一个**专属隔离库**拉回「一致、可复制的起点」（DR-0018 §2.2 B）。

它解决什么问题
--------------
真机走查 / 交互探针要**反复**跑，就需要一个能**反复**回到同一起点的环境。
本机原先是"每轮新建一个临时库"（`_walk4c_env.py`），那对**单次**走查够用，
但回答不了 DR-0018 §6 要求的那条判据 ——
「**基线 → 改变状态 → 复位 → 再取基线**」**两轮比对是否一致**。

用法（backend 目录下）::

    # 只复位（删库 → 迁移 → 四条一致性报告），不铺种子
    python scripts/reset_demo_env.py --db <绝对路径>.db

    # 复位 + 依次重铺种子（**自己起后端、自己收掉**，见下方"为什么必须同进程"）
    python scripts/reset_demo_env.py --db <绝对路径>.db --seed --report <报告>.json

    # 可重复性自证（DR-0018 §6 的判据）：跑两轮并比对基线
    python scripts/reset_demo_env.py --db <绝对路径>.db --selftest --report <报告>.json

    # 先看清楚将要做什么，不落盘
    python scripts/reset_demo_env.py --db <绝对路径>.db --dry-run

四道安全闸（DR-0018 §2.2-2 / §5：对开发主库与生产**拒绝执行**）
--------------------------------------------------------------
只要下列任一条不成立就**拒绝执行并返回退出码 2**（不是警告，是不做）：

1. **必须显式 `--db`** —— 绝不隐式决定复位哪个库；
2. 目标后缀必须是 `.db`（本版只支持 SQLite；MySQL 目标直接拒绝）；
3. 目标不得是 `backend/pinglu_didi_dev.db` / `pinglu_didi_test.db`；
4. 目标不得位于**仓库工作树内**（`--allow-in-repo` 可解除这一条，
   但第 3 条**不可解除**）；
   另：`APP_ENV=production` 直接拒绝。

为什么"起后端"与"跑种子"必须同一个进程
--------------------------------------
本机实测（`_walk4c_env.py` 的头注）：`subprocess.Popen` 起的 uvicorn 会随**发起它的那次
shell 结束**被一并回收 —— 记下的 pid 还在，端口却已经不通。
所以"起后端"和"用后端"必须由**同一个进程**持有：本脚本自己起、自己等健康、自己 `stop()`。

顺序为什么是这样
----------------
基线表（`users` / ships / ...）由 `app/main.py` 启动钩子的 `create_all` 建，
而 `ent_` 前缀表**只**由 `migrate.py` 建（DR-0001）。于是：

    删库 → migrate.py（建 ent_ 表）→ 起后端（create_all 建基线表）→ 依次种子

⚠️ 本脚本**不是**「BP-05 完成」，也**不是** D1-17 全部完成（DR-0018 §2.2-6）：
D1-17 的另一半「fresh-run 与 seeded-checkpoint 的证据可区分」归 S5-1。

退出码：`0` = 成功；`1` = 执行期错误；`2` = 安全闸拒绝 / 前置未满足。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"

BASE_URL = "http://127.0.0.1:8000"
HEALTHZ = BASE_URL + "/healthz"
API = BASE_URL + "/api/v1"

HISTORY_TABLE = "_migration_history"
ENT_PREFIX = "ent_"
DEFAULT_ATTACH_DIR = "var/attachments"

#: 种子顺序是**硬约束**（`DEMO-1-runbook.md` §5）：后者依赖前者铺出的身份 / 组织。
SEED_ORDER = ("seed_demo.py", "seed_entrust_demo.py", "seed_entrust_orgpicker.py")
SEED_OPTIONAL = ("seed_contract_cases.py",)

#: 自证用的探针身份：以它登录会在 `users` 里新落一行，构成一次**应用级**状态改变。
PROBE_CODE = "probe-reset-owner"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

_LOG_HANDLE: Any = None  # 保住句柄引用：局部变量被 GC 后 Windows 上可能连带关掉子进程 stdout
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ------------------------------------------------------------------ 进程环境


def _py() -> str:
    """子进程（migrate / 种子）用的解释器：优先仓库内 venv，否则退回当前解释器。

    ⚠️ 只是**子进程**用它；本脚本自身无第三方依赖（只用标准库 sqlite3 / urllib）。
    """
    return str(VENV_PY) if VENV_PY.is_file() else sys.executable


def env_for(db: Path) -> dict[str, str]:
    """构造子进程环境：指向目标库，并**强制脱网**。

    `LLM_MOCK=true` + 摘掉 `LLM_API_KEY` 是刻意的：本机 `.env.local` 的优先级**高于**
    `.env.development`，里面是真实 Key 且额度已耗尽 —— 不压住它，种子就可能真发请求、
    拿到 HTTP 402 并把一次复位污染成"红"。复位只该关心库的状态。
    """
    env = os.environ.copy()
    env["APP_ENV"] = "development"
    env["ENTRUST_ENABLED"] = "true"
    env["DATABASE_URL"] = "sqlite:///" + str(db).replace("\\", "/")
    env["LLM_MOCK"] = "true"
    env["WECHAT_MOCK"] = "true"
    env.pop("LLM_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _run(cmd: list[str], *, env: dict[str, str], timeout: int = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(BACKEND),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）：{' '.join(cmd)}"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


# ------------------------------------------------------------------ 库指纹


def _connect(db: Path) -> sqlite3.Connection:
    """**只读**打开：取指纹的路径绝不写库。"""
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def tables_of(db: Path) -> list[str]:
    if not db.is_file():
        return []
    with contextlib.closing(_connect(db)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [str(r[0]) for r in rows.fetchall()]


def counts_of(db: Path) -> dict[str, int]:
    names = tables_of(db)
    if not names:
        return {}
    with contextlib.closing(_connect(db)) as conn:
        return {t: int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in names}


def fingerprint(db: Path) -> dict[str, Any]:
    """库指纹：逐表行数 + 迁移记账数。

    ⚠️ 只取**计数**，不取时间戳 —— 两轮之间的差异只应来自"有没有被改过"，
    不该来自"什么时候跑的"。
    """
    if not db.is_file():
        return {"exists": False, "counts": {}, "ent_rows": 0, "migration_history": 0}
    c = counts_of(db)
    return {
        "exists": True,
        "counts": c,
        "tables": len(c),
        "ent_rows": sum(int(v) for k, v in c.items() if k.startswith(ENT_PREFIX)),
        "migration_history": int(c.get(HISTORY_TABLE, 0)),
    }


# ------------------------------------------------------------------ 安全闸


def safety_gates(
    db_arg: str | None, *, allow_in_repo: bool
) -> tuple[Path | None, list[dict[str, Any]]]:
    """四道闸逐条判定；**任一为 False 即拒绝执行**（调用方返回退出码 2）。"""
    gates: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        gates.append({"gate": name, "ok": bool(ok), "detail": detail})

    if not db_arg:
        add("① 显式指定 --db", False, "未传 --db：绝不隐式决定复位哪个库")
        return None, gates
    db = Path(db_arg).expanduser()
    add("① 显式指定 --db", True, str(db))

    add("② 目标是 SQLite 文件（.db）", db.suffix.lower() == ".db", f"suffix={db.suffix or '(空)'}")

    forbidden = {
        str((BACKEND / "pinglu_didi_dev.db").resolve()),
        str((BACKEND / "pinglu_didi_test.db").resolve()),
    }
    in_forbidden = str(db.resolve()) in forbidden if db.suffix.lower() == ".db" else False
    add("③ 不是开发 / 测试主库", not in_forbidden, "开发主库与测试库一律拒绝，且**不可解除**")

    env_name = os.environ.get("APP_ENV", "development").lower()
    add("④ 不是生产环境", env_name != "production", f"APP_ENV={env_name}")

    try:
        inside = db.resolve().is_relative_to(REPO.resolve())
    except (OSError, ValueError):
        inside = False
    add(
        "⑤ 不在仓库工作树内",
        (not inside) or allow_in_repo,
        "目标位于仓库内（--allow-in-repo 可解除）" if inside else "目标在仓库外（隔离目录）",
    )
    return db, gates


def gates_ok(gates: list[dict[str, Any]]) -> bool:
    return all(g["ok"] for g in gates)


# ------------------------------------------------------------------ 复位动作


#: `unlink` 的**有界重试**参数。
#:
#: 实测（2026-09-21，`E:\_diag\p2probe`，3/3 轮确定复现）：被 `stop_backend` 杀掉的那一个
#: —— **本脚本自己起的后端** —— 在 `Popen.wait()` 返回之后，仍会**短暂持有库文件句柄**：
#: 立刻 `unlink` **必**抛 `PermissionError [WinError 32]`，**+1s 后必成功（0–1 ms）**。
#: ⇒ 这既不是永久占用、也不是"换个目录"能绕过的缺陷：正确做法是**有界等待 + 重试**，
#: 并把**实际等待时长**写进读数（这样"等过"与"没等过"在证据里可区分）。
UNLINK_TRIES = 40
UNLINK_GAP_S = 0.25  # 上限约 10s


def unlink_with_wait(p: Path) -> int:
    """删除单个文件；遇 Windows 句柄未释放**有界重试**。返回实际等待的毫秒数。

    ⛔ **不吞错**：到上限仍失败就抛，并带上"重试了几次、等了多久"——
    那时它就不是释放竞态，而是有进程**长期持有**该文件。
    """
    t0 = time.time()
    for attempt in range(UNLINK_TRIES):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except PermissionError as err:
            if attempt + 1 >= UNLINK_TRIES:
                raise PermissionError(
                    f"{p}：重试 {UNLINK_TRIES} 次（约 {UNLINK_TRIES * UNLINK_GAP_S:.1f}s）仍被占用 "
                    f"⇒ 存在**长期持有**该文件的进程，不是句柄释放竞态。原始错误：{err}"
                ) from err
            time.sleep(UNLINK_GAP_S)
            continue
        return int((time.time() - t0) * 1000)
    return int((time.time() - t0) * 1000)  # pragma: no cover


def reset_db(db: Path, *, dry_run: bool) -> dict[str, Any]:
    """① 删除库文件（含 `-wal` / `-shm` / `-journal`）② 重跑 `migrate.py --verify`。"""
    removed: list[str] = []
    release_ms: dict[str, int] = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(db) + suffix)
        if p.is_file():
            removed.append(p.name)
            if not dry_run:
                release_ms[p.name] = unlink_with_wait(p)
    if not dry_run:
        db.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return {
            "removed": removed,
            "handle_release_ms": release_ms,
            "migrate": {"rc": None, "out": "（--dry-run 未执行）"},
        }

    rc, out = _run([_py(), "migrate.py", "--verify"], env=env_for(db))
    return {
        "removed": removed,
        "handle_release_ms": release_ms,
        "migrate": {"rc": rc, "out": out[-2000:]},
    }


def attachments_face(*, explicit_dir: str | None, dry_run: bool) -> dict[str, Any]:
    """附件面：**只清环境专属目录**；共享默认目录一律不碰（工具边界，如实记录）。

    复位后 `ent_attachment` 必然为空，所以"附件与业务引用一致"这一面是**自动成立**的；
    需要处理的只是"上一轮留下的孤儿文件"。而默认目录 `backend/var/attachments` 是**多环境共享**
    的 —— 清它就会波及别的环境，所以宁可记 `LIMITATION` 也不越界。
    """
    shared = (BACKEND / DEFAULT_ATTACH_DIR).resolve()

    if explicit_dir:
        d = Path(explicit_dir).expanduser()
        source = "--attachments-dir"
    else:
        raw = os.environ.get("ATTACHMENT_STORAGE_DIR")
        if not raw:
            return {
                "verdict": "LIMITATION",
                "dir": str(shared),
                "removed_files": 0,
                "detail": "共享默认目录且未被 ATTACHMENT_STORAGE_DIR 覆盖 ⇒ 不清理（会波及其它环境）。"
                "本库重建后 ent_attachment 为空，附件一致性成立；要连文件一起清请传 --attachments-dir",
            }
        d = Path(raw)
        if not d.is_absolute():
            d = BACKEND / d
        source = "ATTACHMENT_STORAGE_DIR"

    if d.resolve() == shared:
        return {
            "verdict": "LIMITATION",
            "dir": str(d),
            "source": source,
            "removed_files": 0,
            "detail": "解析结果就是共享默认目录 ⇒ 不清理（工具边界：无法只清本环境的那一份）",
        }

    fired = 0
    if d.is_dir():
        for f in sorted(d.rglob("*")):
            if f.is_file():
                fired += 1
                if not dry_run:
                    unlink_with_wait(f)  # 同一族的句柄释放竞态，用同一套有界重试
    return {"verdict": "PASS", "dir": str(d), "source": source, "removed_files": fired}


def jobs_face(db: Path) -> dict[str, Any]:
    """后台作业面：不得有**遗留的未释放租约**（作业行本身可以是种子铺出来的正常活）。"""
    if not db.is_file():
        return {"verdict": "FAIL", "detail": "库不存在，取不到作业面"}
    with contextlib.closing(_connect(db)) as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM ent_agent_job").fetchone()[0])
        leased = int(
            conn.execute(
                "SELECT COUNT(*) FROM ent_agent_job WHERE lease_owner IS NOT NULL"
            ).fetchone()[0]
        )
        running = int(
            conn.execute("SELECT COUNT(*) FROM ent_agent_job WHERE status = 'running'").fetchone()[
                0
            ]
        )
    ok = leased == 0
    return {
        "verdict": "PASS" if ok else "LIMITATION",
        "rows": total,
        "running": running,
        "held_leases": leased,
        "detail": "无未释放租约" if ok else f"{leased} 行仍持有租约（不是复位缺陷，但需如实记）",
    }


def identity_face(db: Path) -> dict[str, Any]:
    """身份 / 授权面：复位**只跑迁移**时基线表还没建（要等应用 `create_all`）—— 如实说明。"""
    names = tables_of(db)
    if "users" not in names:
        return {
            "verdict": "BOUNDARY",
            "detail": "基线表（users / ent_org_member / ent_entrustment）尚未创建："
            "复位只跑了 migrate.py，基线表由应用启动钩子的 create_all 建 ⇒ 带 --seed 时才会出现",
        }
    c = counts_of(db)
    return {
        "verdict": "PASS",
        "users": c.get("users", 0),
        "org_member": c.get("ent_org_member", 0),
        "entrustment": c.get("ent_entrustment", 0),
        "detail": "身份 / 成员 / 授权三张表均按种子重铺",
    }


def client_cache_face() -> dict[str, Any]:
    """客户端身份缓存面：**本脚本管不到**，也不该假装管到。"""
    return {
        "verdict": "NOT_APPLICABLE",
        "detail": "小程序 storage（dev_login_code / current_role / 在途草稿）属**客户端**状态，"
        "后端脚本够不着。走查侧的处理方式在 DEMO-1-runbook.md §6.1 第四条："
        "每轮用全新 IDE 实例（全新 storage），不靠「清缓存」",
    }


# ------------------------------------------------------------------ 后端生命周期


def port_open(port: int = 8000) -> bool:
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
    try:
        with _OPENER.open(HEALTHZ, timeout=timeout) as r:
            return int(r.status) == 200
    except Exception:  # noqa: BLE001  —— 探活失败就是 False，不需要区分异常种类
        return False


def start_backend(db: Path, wait_s: int = 60, *, need_own: bool = True) -> tuple[Any, str, bool]:
    """起后端并等它真的能应答。返回 (proc, note, env_err)。

    ⚠️ 走 `ProxyHandler({})`：本机 `http_proxy` 会接管对 `127.0.0.1:8000` 的请求并回 502，
    看起来像"后端没起来"。

    ⚠️ `need_own=True`（要铺种子的调用方**必须**用它）：**别人起的**后端连的是**别的库** ——
    在它上面跑种子，会得到"种子成功 + 目标库依旧是空的"这种最难查的假绿。
    宁可**判环境错误**，也不要把结论建立在一个来历不明的进程上。
    """
    global _LOG_HANDLE
    if health():
        if need_own:
            return (
                None,
                "已有后端在跑且**不是本进程起的** ⇒ 无法保证它连的是目标库，拒绝铺种子",
                True,
            )
        return None, "已有后端在跑（复用，不接管其生命周期）", False
    if port_open(8000):
        return None, "8000 端口被占但 /healthz 不通 ⇒ 环境错误，不擅自杀进程", True
    if not VENV_PY.is_file():
        return None, f"缺 venv 解释器：{VENV_PY}", True
    # 句柄**必须**比函数活得久：局部变量被 GC 后 Windows 上会连带关掉子进程 stdout
    (BACKEND / "var").mkdir(parents=True, exist_ok=True)
    _LOG_HANDLE = open(  # noqa: SIM115
        BACKEND / "var" / "reset-uvicorn.log", "a", encoding="utf-8", errors="replace"
    )
    _LOG_HANDLE.write(f"\n===== {time.strftime('%H:%M:%S')} reset_demo_env 起后端 =====\n")
    _LOG_HANDLE.flush()
    proc = subprocess.Popen(
        [_py(), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(BACKEND),
        env=env_for(db),
        stdout=_LOG_HANDLE,
        stderr=subprocess.STDOUT,
    )
    for _ in range(wait_s):
        if health():
            return proc, f"本进程起（pid={proc.pid}），稍后由本进程 stop()", False
        if proc.poll() is not None:
            return proc, f"后端进程提前退出（rc={proc.returncode}）", True
        time.sleep(1)
    return proc, f"{wait_s}s 内 /healthz 未就绪", True


def stop_backend(proc: subprocess.Popen | None) -> None:
    """只回收**本进程起的**那一个（DR-0018 / HO D2：不按进程名清光全场）。

    ⚠️ `kill()` 之后**必须**再 `wait()`：只发信号不回收的话，进程对象与它打开的
    文件句柄都可能还没放掉 —— 紧接着 `reset_db()` 的 `unlink` 就会撞
    `PermissionError [WinError 32]`（实测 3/3 轮复现）。真正的兜底在
    `unlink_with_wait()` 的有界重试，这里的 `wait()` 只是把窗口缩到最小。
    """
    if proc is None:
        return
    with contextlib.suppress(Exception):
        proc.terminate()
        proc.wait(timeout=10)
        return
    with contextlib.suppress(Exception):
        proc.kill()
        proc.wait(timeout=10)


# ------------------------------------------------------------------ 种子与改状态


def run_seeds(db: Path, *, with_contract_cases: bool) -> list[dict[str, Any]]:
    names = list(SEED_ORDER) + (list(SEED_OPTIONAL) if with_contract_cases else [])
    env = env_for(db)
    out: list[dict[str, Any]] = []
    for name in names:
        rc, txt = _run([_py(), "scripts/" + name], env=env)
        out.append({"seed": name, "rc": rc, "tail": txt[-400:]})
        if rc != 0:
            break
    return out


def _api(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> tuple[dict[str, Any], int]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}"), int(r.status)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        with contextlib.suppress(Exception):
            return json.loads(raw), int(e.code)
        return {"__raw__": raw[:300]}, int(e.code)
    except Exception as exc:  # noqa: BLE001
        return {"__err__": str(exc)}, 0


def mutate_via_api() -> dict[str, Any]:
    """在**运行中的应用**上改状态：探针身份登录 + 建一张委托草稿。

    两步都是真实业务动作（都要过权限与 UUID 幂等头），不是直接写库 ——
    这样"复位把改动抹掉了"才是有分量的结论。
    """
    steps: list[dict[str, Any]] = []
    body, st = _api("POST", "/auth/login", body={"code": PROBE_CODE})
    steps.append({"step": "login", "status": st, "ok": st == 200})
    token = body.get("access_token") if isinstance(body, dict) else None

    if token:
        key = f"probe-reset-{int(time.time() * 1000)}"
        created, st2 = _api(
            "POST",
            "/entrust/assignments",
            body={"title": "复位自证的探针委托", "cargo_summary": "reset-selftest"},
            token=str(token),
            headers={"Idempotency-Key": key},
        )
        steps.append(
            {
                "step": "create_assignment",
                "status": st2,
                "ok": st2 in (200, 201),
                "assignment_id": created.get("assignment_id")
                if isinstance(created, dict)
                else None,
            }
        )
    return {"steps": steps, "ok": all(bool(s["ok"]) for s in steps)}


# ------------------------------------------------------------------ 一轮完整周期


def one_cycle(db: Path, args: argparse.Namespace, round_no: int) -> dict[str, Any]:
    """一轮：复位 → 起后端 → 种子 → **取基线** → 改状态 → 复位 → 取复位后指纹。"""
    first = reset_db(db, dry_run=False)
    proc, note, env_err = start_backend(db, need_own=True)
    fp_seeded: dict[str, Any] = {}
    mutation: dict[str, Any] = {"steps": [], "ok": False}
    fp_mutated: dict[str, Any] = {}
    seeds: list[dict[str, Any]] = []
    try:
        if not env_err:
            seeds = run_seeds(db, with_contract_cases=args.with_contract_cases)
            fp_seeded = fingerprint(db)
            mutation = mutate_via_api()
            fp_mutated = fingerprint(db)
    finally:
        stop_backend(proc)
    second = reset_db(db, dry_run=False)
    fp_reset = fingerprint(db)

    ent_after = int(fp_reset.get("ent_rows", -1))
    check_erased = bool(mutation.get("ok")) and ent_after == 0
    check_history = fp_reset.get("migration_history") == fp_seeded.get("migration_history")
    return {
        "round": round_no,
        "backend_note": note,
        "env_error": env_err,
        "reset_1": first,
        "seeds": seeds,
        "seeded": fp_seeded,
        "mutation": mutation,
        "mutated": fp_mutated,
        "reset_2": second,
        "after_reset": fp_reset,
        "check_mutation_erased": check_erased,
        "check_migration_history_match": check_history,
    }


# ------------------------------------------------------------------ 命令


def _print_gates(gates: list[dict[str, Any]]) -> None:
    print("-- 安全闸 --")
    for g in gates:
        print(f"   [{'✓' if g['ok'] else '✗'}] {g['gate']}：{g['detail']}")


def _print_faces(faces: dict[str, Any]) -> None:
    print("-- 四条一致性 --")
    for name, f in faces.items():
        print(f"   {name}：{f['verdict']} —— {f['detail'][:150]}")


def cmd_reset(args: argparse.Namespace) -> int:
    db, gates = safety_gates(args.db, allow_in_repo=args.allow_in_repo)
    _print_gates(gates)
    if not gates_ok(gates) or db is None:
        print("RESET: REFUSED（安全闸未全过，未做任何改动）")
        return EXIT_REFUSED

    report: dict[str, Any] = {
        "mode": "reset",
        "db": str(db),
        "gates": gates,
        "dry_run": args.dry_run,
    }
    report["before"] = fingerprint(db)
    report["reset"] = reset_db(db, dry_run=args.dry_run)
    report["after_reset"] = fingerprint(db)

    migrate_rc = report["reset"]["migrate"]["rc"]
    if args.dry_run:
        db_face: dict[str, Any] = {
            "verdict": "DRY_RUN",
            "detail": f"未执行；将删除 {report['reset']['removed'] or '（无库文件）'} 并重跑 migrate.py --verify",
        }
    else:
        db_face = {
            "verdict": "PASS" if migrate_rc == 0 else "FAIL",
            "detail": f"migrate.py --verify rc={migrate_rc}；表数={report['after_reset'].get('tables')}、"
            f"迁移记账={report['after_reset'].get('migration_history')}",
        }

    faces: dict[str, Any] = {
        "数据库": db_face,
        "附件": attachments_face(explicit_dir=args.attachments_dir, dry_run=args.dry_run),
        "后台作业": {"verdict": "DRY_RUN", "detail": "未执行（--dry-run）"}
        if args.dry_run
        else jobs_face(db),
        "身份与授权": identity_face(db),
        "客户端身份缓存": client_cache_face(),
    }

    env_err = False
    if args.seed and not args.dry_run:
        proc, note, env_err = start_backend(db, need_own=True)
        report["backend_note"] = note
        report["environment_error"] = env_err
        try:
            report["seeds"] = (
                [] if env_err else run_seeds(db, with_contract_cases=args.with_contract_cases)
            )
        finally:
            stop_backend(proc)
        report["after_seed"] = fingerprint(db)
        if not env_err:
            faces["后台作业"] = jobs_face(db)
            faces["身份与授权"] = identity_face(db)

    report["faces"] = faces
    _print_faces(faces)

    hard_fail = any(f["verdict"] == "FAIL" for f in faces.values())
    if env_err:
        report["result"] = "ENV_BLOCKED"
        rc = EXIT_REFUSED
    else:
        report["result"] = "FAIL" if hard_fail else ("DRY_RUN" if args.dry_run else "OK")
        rc = EXIT_ERROR if hard_fail else EXIT_OK
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写：{args.report}")
    print(f"RESET: {report['result']}")
    return rc


def cmd_selftest(args: argparse.Namespace) -> int:
    db, gates = safety_gates(args.db, allow_in_repo=args.allow_in_repo)
    _print_gates(gates)
    if not gates_ok(gates) or db is None:
        print("RESET: REFUSED（安全闸未全过，未做任何改动）")
        return EXIT_REFUSED

    rounds = [one_cycle(db, args, 1), one_cycle(db, args, 2)]
    r1, r2 = rounds

    baseline_match = r1["seeded"] == r2["seeded"]
    reset_match = r1["after_reset"] == r2["after_reset"]
    ok = (
        all(c["check_mutation_erased"] for c in rounds)
        and all(c["check_migration_history_match"] for c in rounds)
        and baseline_match
        and reset_match
        and not any(c["env_error"] for c in rounds)
    )
    report = {
        "mode": "selftest",
        "db": str(db),
        "gates": gates,
        "criterion": "DR-0018 §6：基线 → 改变状态 → 复位 → 再取基线，两轮比对一致",
        "rounds": rounds,
        # ⭐ 读数（不是装饰）：`unlink` 前**实际等了多久**才拿到句柄。
        # 实测（2026-09-21）：被杀掉的后端仍短暂持库 ⇒ 立刻删必失败、+1s 必成功。
        # 把等待写下来，才能区分"本来就没占用"与"等到了释放"。
        "handle_release_ms": {
            f"round{c['round']}": {
                "reset_1": c["reset_1"].get("handle_release_ms", {}),
                "reset_2": c["reset_2"].get("handle_release_ms", {}),
            }
            for c in rounds
        },
        "checks": {
            "① 复位清除改动（两轮）": [c["check_mutation_erased"] for c in rounds],
            "② 迁移记账复位后与种子后一致（两轮）": [
                c["check_migration_history_match"] for c in rounds
            ],
            "③ 两轮种子后基线一致": baseline_match,
            "④ 两轮复位后指纹一致": reset_match,
            "⑤ 两轮均无环境错误": not any(c["env_error"] for c in rounds),
        },
        "result": "OK" if ok else "FAIL",
    }

    print("-- 自证判据（DR-0018 §6）--")
    for k, v in report["checks"].items():
        print(f"   [{'✓' if v is True or v == [True, True] else '✗'}] {k}：{v}")
    print(
        f"   round1 种子后 ent_rows={r1['seeded'].get('ent_rows')} / "
        f"round2={r2['seeded'].get('ent_rows')}"
    )
    delta = int(r1["mutated"].get("ent_rows", 0)) - int(r1["seeded"].get("ent_rows", 0))
    print(f"   round1 改动后 ent_rows={r1['mutated'].get('ent_rows')}（较种子后 +{delta}）")
    for c in rounds:
        for half in ("reset_1", "reset_2"):
            ms = c[half].get("handle_release_ms") or {}
            worst = max(ms.values()) if ms else 0
            print(
                f"   round{c['round']} {half} 删库前等待句柄释放：最长 {worst} ms"
                f"{'（含等待 ⇒ 见 UNLINK_TRIES 注释）' if worst else '（无等待）'}"
            )
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写：{args.report}")
    print(f"RESET: {report['result']}")
    return EXIT_OK if ok else EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="最小隔离复位：把专属隔离库拉回一致、可复制的起点（DR-0018 §2.2 B）"
    )
    ap.add_argument("--db", default=None, help="目标隔离库的**绝对路径**（.db）；不传即拒绝执行")
    ap.add_argument("--seed", action="store_true", help="复位后依次重铺种子（自己起后端、自己收）")
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="两轮「基线 → 改变状态 → 复位 → 再基线」自证（DR-0018 §6），隐含 --seed",
    )
    ap.add_argument(
        "--with-contract-cases",
        action="store_true",
        help="种子追加 seed_contract_cases.py（㊴/㊶ 章需要）",
    )
    ap.add_argument("--attachments-dir", default=None, help="显式指定环境专属附件目录并清空")
    ap.add_argument("--allow-in-repo", action="store_true", help="允许目标位于仓库工作树内")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不落盘")
    ap.add_argument("--report", default=None, help="把 JSON 报告写到该路径")
    args = ap.parse_args(argv)

    if args.selftest:
        args.seed = True
        return cmd_selftest(args)
    return cmd_reset(args)


if __name__ == "__main__":
    raise SystemExit(main())
