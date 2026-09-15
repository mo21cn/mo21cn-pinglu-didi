"""本地门禁预演与 `ci.yml` 的**逐字一致性**检查（ENT-037）。

为什么需要它
------------
项目已经两次踩到「**本地绿、CI 红**」，两次根因相同：**本地预演的范围与 ci.yml 不一致**。

* 一次是把仓库根脚本的 ruff 跑成 `ruff check scripts`（漏了
  `--config backend/pyproject.toml`），于是本地冒出一批 **CI 根本不查**的规则，
  照它去改会把代码改歪；
* 一次是只对自己**改过的那几个文件**跑 `ruff format --check`，而 CI 跑的是
  `app tests scripts` 全量 —— 后来编辑过的另一个文件没被格式化，本地全绿、CI 报红。

两次的共同点：**范围是"人记住的"，不是"机器读出来的"**。本脚本把范围从
`ci.yml` 里**读出来**，与一份冻结的契约表比对；不一致就报红，逼一次人工确认。
`--json` 输出可直接喂给本地预演脚本，让"本地跑什么"由 ci.yml 决定，而不是由记忆决定。

它**不**执行命令（执行需要后端 venv / Node，环境相关）—— 它只回答
「本地该跑的命令，与 CI 跑的是不是同一批」。执行由调用方按 `--json` 的输出来做。

用法::

    python scripts/verify_ci_parity.py              # 比对，不一致 rc=1
    python scripts/verify_ci_parity.py --json       # 输出机器可读的执行计划
    python scripts/verify_ci_parity.py --show       # 打印从 ci.yml 读到的范围

⚠️ 本脚本自身也在仓库根 `scripts/*.py` 的 lint 门禁内（见 ci.yml 的登记检查）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI_YML = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
PYPROJECT = "backend/pyproject.toml"

# ── 冻结契约：ci.yml 一旦改动，这里必须同步改（改不动就说明你改的是门禁范围）─────
# 只冻结"范围与命令"，不冻结 steps 顺序/注释 —— 注释可以随便写。
EXPECTED: dict[str, Any] = {
    # backend job（defaults.run.working-directory = backend）
    "backend.cwd": "backend",
    "backend.commands": [
        "ruff check app tests scripts",
        "ruff format --check app tests scripts",
        "mypy app migrations migrate.py",
        "pytest --tb=short --cov=app --cov-report=term-missing",
    ],
    # 仓库根 scripts/*.py 的独立 lint（该 step 的 working-directory = .）
    "root_scripts.cwd": ".",
    "root_scripts.linted": [
        "scripts/verify_miniapp_devtools.py",
        "scripts/wechatide_client.py",
        "scripts/verify_ci_parity.py",
        "scripts/run_walkthrough_devtools.py",
    ],
    "root_scripts.exempt": ["scripts/verify_baseline.py"],
    # 前端静态契约 job：逐个 node 脚本
    "frontend_static.scripts": [
        "verify_miniapp.js",
        "verify_ui_interactions.js",
        "verify_entrust_ui.js",
        "verify_routes.js",
        "verify_routes_behavior.js",
        "verify_tabbar_targets.js",
        "verify_require_paths.js",
        "verify_wxml_directives.js",
    ],
}

#: 本地预演时用的解释器/命令前缀（与 CI 的 `ruff` / `mypy` / `pytest` 对应）
LOCAL_BACKEND_BIN = {
    "ruff": ".venv/Scripts/ruff.exe",
    "mypy": ".venv/Scripts/mypy.exe",
    "pytest": ".venv/Scripts/python.exe -m pytest",
}


def read_ci() -> str:
    """读 ci.yml 并把行尾归一成 LF。

    ⚠️ 本机（Windows）检出时 `core.autocrlf` 会把 yml 写成 **CRLF**。
    若不归一化，所有以 `$` 结尾的正则都会**静默匹配不到**（`$` 只认 `\\n`），
    于是"读不到 linted/exempt 名单"这类报错看起来像 ci.yml 变了，实际是行尾问题。
    """
    with open(CI_YML, encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n").replace("\r", "\n")


def _job_block(text: str, job: str) -> str:
    """取出某个顶层 job 的文本块（到下一个同级 job 或文件末）。"""
    start = re.search(rf"^  {re.escape(job)}:\s*$", text, re.M)
    if not start:
        raise SystemExit(f"✗ ci.yml 里找不到 job `{job}`")
    rest = text[start.end() :]
    nxt = re.search(r"^  [a-z][\w-]*:\s*$", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def _steps(block: str) -> list[tuple[str, str, str | None]]:
    """从 job 块里取 [(step name, run 文本, 该 step 自己的 working-directory), ...]。

    ⚠️ `working-directory` 必须**按 step 取**，不能取 job 里第一个 ——
    本仓的「仓库根 scripts」step 正是在同一个 job 内用 `working-directory: .`
    覆盖 defaults 的 `backend`。取错会把"跑在仓库根"读成"跑在 backend"，
    于是本地预演的 cwd 与实际相反（而两者的 ruff 配置**不同**）。
    """
    out: list[tuple[str, str, str | None]] = []
    for m in re.finditer(r"^      - name: (.+?)\n(.*?)(?=^      - |\Z)", block, re.M | re.S):
        name = m.group(1).strip()
        body = m.group(2)
        run = re.search(r"^        run: \|\n((?:(?:          [^\n]*)?\n)+)", body, re.M)
        if run:
            # ⚠️ run 块里**可能有空行**（本仓的「仓库根 scripts」step 就有）。
            # 若只匹配"10 空格开头的行"，提取会在第一个空行处**静默截断** ——
            # 表现为"读不到 linted/exempt 名单"，看起来像 ci.yml 变了。
            text = "\n".join(
                line[10:] if line.startswith("          ") else line
                for line in run.group(1).splitlines()
            )
        else:
            one = re.search(r"^        run: (.+)$", body, re.M)
            text = one.group(1).strip() if one else ""
        wd = re.search(r"^        working-directory: (.+)$", body, re.M)
        out.append((name, text, wd.group(1).strip() if wd else None))
    return out


def parse_ci(text: str) -> dict[str, Any]:
    """从 ci.yml 读出实际范围。解析不到就**报错**（不静默给空集）。"""
    backend = _job_block(text, "backend")
    frontend = _job_block(text, "frontend-static")

    defaults_cwd = re.search(
        r"^    defaults:\n      run:\n        working-directory: (.+)$", backend, re.M
    )
    if not defaults_cwd:
        raise SystemExit("✗ 读不到 backend job 的 defaults.run.working-directory")

    commands: list[str] = []
    root_linted: list[str] = []
    root_exempt: list[str] = []
    root_cwd: str | None = None
    for name, run, wd in _steps(backend):
        if "仓库根" in name:
            # 该 step 自己有 working-directory（覆盖 defaults）
            m = re.search(r'^linted="(.+?)"$', run, re.M)
            e = re.search(r'^exempt="(.+?)"$', run, re.M)
            if not (m and e):
                raise SystemExit("✗ 读不到仓库根 scripts 的 linted/exempt 名单")
            root_linted = m.group(1).split()
            root_exempt = e.group(1).split()
            root_cwd = wd
            continue
        if run.startswith(("ruff ", "mypy ", "pytest ")):
            commands.append(run.strip())

    fs_scripts: list[str] = []
    for _name, run, _wd in _steps(frontend):
        for m in re.finditer(r"node (\S+\.js)", run):
            fs_scripts.append(os.path.basename(m.group(1)))

    return {
        "backend.cwd": defaults_cwd.group(1).strip(),
        "backend.commands": commands,
        "root_scripts.cwd": root_cwd,
        "root_scripts.linted": root_linted,
        "root_scripts.exempt": root_exempt,
        "frontend_static.scripts": fs_scripts,
    }


def compare(actual: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for key, want in EXPECTED.items():
        got = actual.get(key)
        if got != want:
            diffs.append(
                f"  ✗ {key}\n      期望（脚本内冻结）: {want}\n      实际（ci.yml）    : {got}"
            )
    return diffs


def build_plan(actual: dict[str, Any]) -> dict[str, Any]:
    """把 CI 范围翻成**本地**可执行计划（供预演脚本消费，避免手工抄范围）。"""
    backend_cmds: list[list[str]] = []
    for cmd in actual["backend.commands"]:
        head, *rest = cmd.split()
        local = LOCAL_BACKEND_BIN.get(head)
        if local is None:
            backend_cmds.append([head, *rest])
        else:
            backend_cmds.append([*local.split(), *rest])
    return {
        "backend": {"cwd": actual["backend.cwd"], "commands": backend_cmds},
        "root_scripts": {
            "cwd": actual["root_scripts.cwd"],
            "linted": actual["root_scripts.linted"],
            "exempt": actual["root_scripts.exempt"],
            "ruff_config": PYPROJECT,
        },
        "frontend_static": {"cwd": "scripts", "scripts": actual["frontend_static.scripts"]},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="本地门禁预演 vs ci.yml 的一致性检查")
    ap.add_argument("--json", action="store_true", help="输出本地执行计划（JSON）")
    ap.add_argument("--show", action="store_true", help="打印从 ci.yml 读到的范围")
    args = ap.parse_args()

    actual = parse_ci(read_ci())
    diffs = compare(actual)

    if args.show:
        print(json.dumps(actual, ensure_ascii=False, indent=2))
    if args.json:
        print(json.dumps(build_plan(actual), ensure_ascii=False))
        return 1 if diffs else 0

    if diffs:
        print("✗ 本地预演的冻结范围与 ci.yml 不一致 —— 先确认你改的是不是门禁范围：")
        print("\n".join(diffs))
        print(
            "\n  处置：① 若这是**有意的**门禁调整 ⇒ 改本文件 EXPECTED 并说明理由；\n"
            "        ② 若只是顺手改了 ci.yml ⇒ 撤回；\n"
            "        ③ 本地预演请用 `--json` 的输出驱动，不要手工抄范围。"
        )
        return 1

    n_fs = len(actual["frontend_static.scripts"])
    print(
        "✓ ci.yml 与本地预演范围一致："
        f"backend {len(actual['backend.commands'])} 条命令 / "
        f"根脚本 linted {len(actual['root_scripts.linted'])} + exempt {len(actual['root_scripts.exempt'])} / "
        f"前端静态 {n_fs} 个脚本"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
