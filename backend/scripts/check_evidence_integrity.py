#!/usr/bin/env python
"""证据完整性核对：**文件名 NUL 安全解析** ＋ **内容哈希逐件比对**。

为什么需要一个专门的小工具（2026-09-21 实测踩坑）
--------------------------------------------------
"证据是否逐字节入库"的习惯查法是：

    git ls-files -s docs/entrust/evidence/ | while read -r mode sha stage path; do
      [ "$(git hash-object "$path")" = "$sha" ] || echo "不一致 $path"
    done

它有两个**静默误判源**：

1. **`core.quotepath`（默认 true）会把非 ASCII 文件名转成八进制转义** ——
   中文名会变成 `"docs/.../15-\345\247\224\346\211\230..."`（带引号、带反斜杠转义）。
   逐行 `hash-object` 拿到的是**不存在的路径** ⇒ 空哈希 ⇒ **全部中文名文件被判"不一致"**。
   本仓实测：124 件证据里 **70 件**（全是中文名 `.jpg`）被误报，真实不一致 **0 件**。
2. `read` 按**空白**切分字段，而路径里可能有空格；`-z` 才不会有这个问题。

`core.quotepath=false` 只是**改善显示**，真正的修复是**正确解析文件名** ——
本工具统一用 `git ... ls-files -s -z`（NUL 分隔、不转义）＋ **字节**（`bytes`）传递路径，
不做任何字符串解码，所以中文名、空格名、以及任意字节序列都不会被改写。

用法
----
    python backend/scripts/check_evidence_integrity.py                # 默认核 docs/entrust/evidence/
    python backend/scripts/check_evidence_integrity.py -- <路径...>    # 核指定路径
    python backend/scripts/check_evidence_integrity.py --json         # 机器可读

判据
----
* 索引里有、工作区**缺文件** ⇒ MISSING（这是"文件被物理删除"那类故障的信号）；
* 索引 blob ≠ 工作区内容哈希 ⇒ MISMATCH；
* **一件都没核到 ⇒ `EMPTY`（rc=1）** —— ⛔ 不能算 OK：路径写错、目录改名、
  或 `.gitignore` 把整棵树挡在库外时，"核 0 件"看起来和"全过"一模一样
  （本项目已经有过多起"静默漏项"）。要显式核空集合时传 `--allow-empty`。
* 两者都为 0 且总数 > 0 ⇒ `OK`，退出码 0；否则退出码 1。

⛔ 本工具**不接进任何门禁或 CI**（2026-09-21 HO 明确：属小修复，不作为交付前置条件）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHSPEC = "docs/entrust/evidence/"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """跑 git 并拿**字节**输出 —— 不让任何一层做编码转换。"""
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd),
        capture_output=True,
        check=False,
    )


def index_entries(pathspec: list[str], cwd: Path) -> list[tuple[bytes, str]]:
    """读索引条目，返回 `[(路径字节, 索引 blob sha), ...]`。

    ⚠️ `-z`：记录用 NUL 分隔、路径**不转义**；`read`/`split()` 都不参与。
    """
    out = _git("ls-files", "-s", "-z", "--", *pathspec, cwd=cwd).stdout
    entries: list[tuple[bytes, str]] = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        parts = meta.split()
        if len(parts) < 2:
            continue
        entries.append((path, parts[1].decode("ascii")))
    return entries


def worktree_blob(path: bytes, cwd: Path) -> str | None:
    """取工作区文件的内容哈希（git 的 blob 口径）。文件不存在返回 None。

    ⚠️ 用 `os.fsdecode()` 把**字节路径**还原成文件系统路径 —— 不经过
    `utf-8 + replace`（那会把不可解码的字节**悄悄改写**成别的文件名，于是"没读到"
    与"读到了另一个文件"就分不清了）。
    """
    if not (cwd / os.fsdecode(path)).exists():
        return None
    out = _git("hash-object", "--", os.fsdecode(path), cwd=cwd).stdout
    return out.decode("ascii").strip() or None


def check(pathspec: list[str], cwd: Path) -> dict[str, list[str]]:
    entries = index_entries(pathspec, cwd)
    missing: list[str] = []
    mismatch: list[str] = []
    for raw_path, want in entries:
        have = worktree_blob(raw_path, cwd)
        shown = raw_path.decode("utf-8", "replace")
        if have is None:
            missing.append(shown)
        elif have != want:
            mismatch.append(f"{shown}（索引 {want[:12]} vs 工作区 {have[:12]}）")
    return {
        "pathspec": pathspec,
        "total": [str(len(entries))],
        "missing": missing,
        "mismatch": mismatch,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="证据完整性核对（NUL 安全 ＋ 内容哈希）")
    ap.add_argument("paths", nargs="*", help=f"要核的路径（默认 {DEFAULT_PATHSPEC}）")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    ap.add_argument("--repo", default=str(REPO_ROOT), help="仓库根（默认按脚本位置推断）")
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="允许「一件都没核到」也算通过（默认算 FAIL：空的绿会掩盖写错的路径）",
    )
    args = ap.parse_args(argv)

    pathspec = args.paths or [DEFAULT_PATHSPEC]
    result = check(pathspec, Path(args.repo))
    total = int(result["total"][0])
    empty = total == 0 and not args.allow_empty
    ok = not result["missing"] and not result["mismatch"] and not empty

    if args.json:
        print(json.dumps({**result, "ok": ok, "empty": empty}, ensure_ascii=False, indent=2))
    else:
        print(f"证据完整性：核 {total} 件（{', '.join(pathspec)}）")
        if empty:
            print("  ⛔ EMPTY：一件都没核到 —— 路径写错／目录改名／被 .gitignore 挡住？")
        print(f"  工作区缺文件：{len(result['missing'])} 件")
        for p in result["missing"]:
            print(f"    MISSING  {p}")
        print(f"  内容不一致：{len(result['mismatch'])} 件")
        for p in result["mismatch"]:
            print(f"    MISMATCH {p}")
        print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
