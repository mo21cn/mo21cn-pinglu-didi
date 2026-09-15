"""本地门禁一键跑 —— **pytest 的判据取 junitxml，不取进程退出码**。

用法（仓库根）：

    .venv/Scripts/python.exe scripts/verify_local_gates.py
    .venv/Scripts/python.exe scripts/verify_local_gates.py --only pytest
    python scripts/verify_local_gates.py --ci-parity-only      # CI 的 parity 步骤用

`--ci-parity-only` 是**用途标识**，不是又一种过滤：CI 的「门禁范围一致性」步骤
走的是**同一个入口**（而不是绕过本脚本直接调 `verify_ci_parity.py`）。
这样本脚本的参数解析、分组与退出码在 CI 里也被真实执行一次 ——
否则它只被 ruff 检查过语法，本地与 CI 的口径会各自演进（本地改了分组、
CI 那步浑然不觉），而它恰恰是"防止两边分叉"的那个脚本，自己却没人跑。

为什么不能拿 pytest 的退出码当判据（本机实测，2026-09-15）
--------------------------------------------------------
`python -m pytest` 退出时清理 `%TEMP%` 会触发本机**批量删除护栏**
（一次 3699 个待删文件，远高于阈值 50）⇒ 进程 **rc=1**，
而 `--junitxml` 里 `testsuite@failures=0 / errors=0`、`670 passed + 8 skipped`。
⇒ 只看 rc 会把「**全过**」读成「**门禁红**」，进而去修一个不存在的失败。

判据
----
* pytest：**`failures + errors == 0`**（skipped 不计失败；`tests` 属性 = passed + skipped）。
  进程 rc 会**如实打印**作参考，但不参与判定。
* 其余各项：进程 rc == 0。

与 CI 的关系
------------
本脚本**不替代** CI：它把 CI 里那几组检查在本地按同一口径跑一遍，方便提交前自检。
参数、范围与 `backend/pyproject.toml`、`.github/workflows/ci.yml` 保持一致；
两者若出现分叉，以 `scripts/verify_ci_parity.py` 的登记一致性检查为准。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

#: 前端静态契约脚本（与 ci.yml 的 frontend-static job 同集合；新增一个必须同步登记）。
FRONTEND_STATIC = (
    "verify_miniapp.js",
    "verify_ui_interactions.js",
    "verify_entrust_ui.js",
    "verify_routes.js",
    "verify_routes_behavior.js",
    "verify_tabbar_targets.js",
    "verify_require_paths.js",
    "verify_wxml_directives.js",
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"APP_ENV": "test", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return env


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    r = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _tail(text: str, n: int = 2) -> str:
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return " / ".join(lines[-n:])[:220]


def _junit_verdict(path: Path) -> tuple[bool, str]:
    """按 junitxml 判定 pytest：`failures + errors == 0`。"""
    if not path.exists():
        return False, "未生成 junitxml（无法判定；**不得**据此认为通过）"
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:  # noqa: BLE001
        return False, f"junitxml 解析失败：{exc}"
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return False, "junitxml 缺少 testsuite 节点"
    tests = int(suite.get("tests") or 0)
    failures = int(suite.get("failures") or 0)
    errors = int(suite.get("errors") or 0)
    skipped = int(suite.get("skipped") or 0)
    passed = tests - failures - errors - skipped
    ok = failures == 0 and errors == 0
    return ok, f"{tests} 项（通过 {passed} / 跳过 {skipped} / 失败 {failures} / 错误 {errors}）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地门禁（pytest 判据取 junitxml）")
    ap.add_argument("--only", default="", help="只跑某一组：backend / pytest / frontend / parity")
    ap.add_argument(
        "--ci-parity-only",
        action="store_true",
        help="用途标识：只跑「范围一致性」一组（CI 的 parity 步骤用）。与 --only 互斥。",
    )
    ap.add_argument(
        "--junitxml",
        default=str(Path(tempfile.gettempdir()) / "pl_local_gates_pytest.xml"),
        help="pytest 的 junitxml 落点（判据出处）",
    )
    args = ap.parse_args(argv)

    if args.ci_parity_only and args.only.strip():
        print("✗ --ci-parity-only 与 --only 不能同时给出（两种口径会叠加，结论无法解释）")
        return 2

    only = (
        {"parity"}
        if args.ci_parity_only
        else {x.strip() for x in args.only.split(",") if x.strip()}
    )
    purpose = "CI 范围一致性（--ci-parity-only）" if args.ci_parity_only else "本地全量自检"
    py = sys.executable
    ruff = BACKEND.parent / ".venv" / ("Scripts/ruff.exe" if os.name == "nt" else "bin/ruff")
    mypy = BACKEND.parent / ".venv" / ("Scripts/mypy.exe" if os.name == "nt" else "bin/mypy")

    rows: list[tuple[str, bool, str]] = []

    def want(group: str) -> bool:
        return not only or group in only

    if want("backend"):
        for label, cmd in (
            ("后端 ruff check", [str(ruff), "check", "app", "tests", "scripts"]),
            (
                "后端 ruff format --check",
                [str(ruff), "format", "--check", "app", "tests", "scripts"],
            ),
            ("后端 mypy", [str(mypy), "app", "migrations", "migrate.py"]),
        ):
            rc, out = _run(cmd, BACKEND)
            rows.append((label, rc == 0, f"rc={rc} {_tail(out)}"))

    if want("pytest"):
        junit = Path(args.junitxml)
        junit.parent.mkdir(parents=True, exist_ok=True)
        if junit.exists():
            junit.unlink()
        rc, out = _run([py, "-m", "pytest", "-q", f"--junitxml={junit}"], BACKEND)
        ok, detail = _junit_verdict(junit)
        # 判据是 junitxml；rc 只如实打印（本机 rc 常为 1，见模块 docstring）。
        note = f"{detail}｜进程 rc={rc}（**不参与判定**）"
        if rc != 0 and ok:
            note += "｜rc≠0 是本机清理 %TEMP% 触发批量删除护栏所致"
        rows.append(("后端 pytest（junitxml 判据）", ok, note))
        if not ok:
            rows.append(("pytest 输出尾部", False, _tail(out, 4)))

    if want("frontend"):
        for name in FRONTEND_STATIC:
            rc, out = _run(["node", str(ROOT / "scripts" / name)], ROOT)
            rows.append((f"前端静态 {name}", rc == 0, f"rc={rc} {_tail(out, 1)}"))

    if want("parity"):
        rc, out = _run([py, str(ROOT / "scripts" / "verify_ci_parity.py")], ROOT)
        rows.append(("范围一致性 verify_ci_parity", rc == 0, f"rc={rc} {_tail(out, 2)}"))

    print(f"\n================ 本地门禁（用途：{purpose}）================")
    for label, ok, note in rows:
        print(f"{'PASS' if ok else 'FAIL'} | {label} | {note}")
    bad = [r for r in rows if not r[1]]
    print(
        f"汇总：{len(rows)} 项，PASS {len(rows) - len(bad)}，FAIL {len(bad)}",
        flush=True,
    )
    print(json.dumps({"total": len(rows), "failed": len(bad)}, ensure_ascii=False))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
