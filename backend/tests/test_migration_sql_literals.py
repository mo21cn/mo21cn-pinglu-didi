"""迁移 SQL 的文本闸门 —— 本机唯一能触到 **MySQL 分支**的手段。

为什么需要它（2026-09-17，真实事故，两个 CI job 因此变红）
==========================================================
`ent_contract:2` 的 MySQL DDL 里，某列的 `COMMENT` 被写成两行相邻字符串字面量：

    COMMENT '来源类别：… / assignment / leg /'
    ' organization / template',

三引号体内这就是 **SQL 原文**（"相邻字面量自动拼接"只对 Python 代码生效，对
`\"\"\"…\"\"\"` 里的字符不生效）。MySQL 收到 `'a' 'b'` 报 1064 语法错误，于是
CI 的「数据库迁移（MySQL 8.0 真实执行）」与「并发集成（MySQL 8.0 真实执行）」
**两个 job 同时红**；而并发 job 里 14 条用例全部是 **ERROR（setup 阶段建表失败）**，
没有一条并发断言真的执行过 —— 只看"红/绿"会把 setup 失败读成并发结论。

本机为什么全绿：本机 3306 关闭，**MySQL 分支的 SQL 从不被执行**；SQLite 分支
不写列注释、也不执行这段 SQL。⇒ SQLite 冒烟、pytest、13 项门禁全绿，
偏差的根因不是"没测 MySQL"，而是**本机对 MySQL 分支结构性失明**。

本用例用纯文本状态机把这层补上：**不连库、不引三方依赖、两个方言都查**。

判据
====
1. SQL 文本里不得出现「字面量闭合后，跳过空白又出现一个 `'`」—— 那就是相邻
   字符串字面量，任何 SQL 方言都不接受它（空串 `''`、转义 `''`、MySQL 的
   反斜杠转义 `\\'` 都不属于此形态，状态机逐个区分）。
2. 同名条目的 mysql / sqlite 两分支若都能解析出列名，**列名集合必须相同** ——
   防"只改一边"。本项目迁移必须双方言成对演进，而本地只跑 SQLite 分支，
   漏改 MySQL 侧的**唯一症状就是不存在的症状**。

本文件**不**做什么
==================
不校验 SQL 语义（列类型是否可移植、索引是否合理、utf8mb4 是否够用）——
那些必须由 CI 的 MySQL 8.0 job 真实执行。这里只拦"**纯文本层面就非法**"的写法，
即本机能在不连库的前提下**确定**判错的那一类。
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND / "migrations"


def _iter_sql_texts() -> Iterator[tuple[str, Any, str, str, int]]:
    """产出 (模块名, 条目 id, 方言, SQL 文本, 该文本在源文件里的起始行)。"""
    package = importlib.import_module("migrations")
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"migrations.{info.name}")
        source = (MIGRATIONS_DIR / f"{info.name}.py").read_text(encoding="utf-8")
        for item in getattr(module, "migrations", None) or []:
            sql = item.get("sql")
            if not isinstance(sql, dict):
                continue
            for dialect, text in sql.items():
                if not isinstance(text, str):
                    continue
                at = source.find(text)
                first_line = source.count("\n", 0, at) + 1 if at >= 0 else 0
                yield info.name, item.get("id"), dialect, text, first_line


def _adjacent_literals(sql: str) -> list[tuple[int, str]]:
    """状态机：检出「字面量闭合后（跳过空白）又出现一个 `'`」。返回 (SQL内行号, 片段)。

    退出字面量后紧跟另一段字面量，在 SQL 里就是两个相邻的字符串字面量。
    逐个区分三种**合法**形态，避免误报：

    * 空串 / 转义：字面量内遇到 `''` 视为一对，继续留在字面量里；
    * MySQL 反斜杠转义：字面量内遇到 `\\` 连带吃掉下一个字符；
    * 正常闭合：字面量内遇到单个 `'` 即闭合，之后跳过空白再看下一个非空白字符。
    """
    hits: list[tuple[int, str]] = []
    i, n = 0, len(sql)
    while i < n:
        if sql[i] != "'":
            i += 1
            continue
        i += 1
        while i < n:
            ch = sql[i]
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                i += 1
                break
            i += 1
        j = i
        while j < n and sql[j] in " \t\r\n":
            j += 1
        if j < n and sql[j] == "'":
            line = sql.count("\n", 0, j) + 1
            fragment = sql[max(0, j - 60) : j + 40].replace("\n", "⏎")
            hits.append((line, fragment))
            i = j
    return hits


def _declared_columns(statement: str) -> set[str]:
    """取 CREATE TABLE 里声明的列名；非建表语句（如 ALTER）返回空集，跳过比较。"""
    m = re.search(r"CREATE TABLE[^(]*\((.*)\)\s*(ENGINE|;|$)", statement, re.S | re.I)
    if m is None:
        return set()
    names: set[str] = set()
    for line in m.group(1).splitlines():
        mm = re.match(r"^`([a-z0-9_]+)`\s", line.strip())
        if mm:
            names.add(mm.group(1))
    return names


def test_migration_sql_has_no_adjacent_string_literals():
    """任何方言的迁移 SQL 都不得含相邻字符串字面量（MySQL 会 1064）。"""
    problems: list[str] = []
    for module, entry_id, dialect, sql, first_line in _iter_sql_texts():
        for sql_line, fragment in _adjacent_literals(sql):
            at = f"（源文件第 {first_line + sql_line - 1} 行）" if first_line else ""
            problems.append(
                f"migrations/{module}.py 的 id={entry_id} [{dialect}]"
                f" SQL 第 {sql_line} 行{at}：字面量相邻 ⇒ {fragment}"
            )

    assert not problems, (
        "迁移 SQL 里出现了相邻字符串字面量 —— 本机 SQLite 分支不会执行到它，"
        "但 MySQL 会直接报 1064（见本文件 docstring 里的事故记录）：\n  " + "\n  ".join(problems)
    )


def _sql_by_entry() -> dict[tuple[str, Any], dict[str, str]]:
    """{(模块名, 条目 id): {方言: SQL 文本}} —— 一次读全，避免用例里反复重扫。"""
    out: dict[tuple[str, Any], dict[str, str]] = {}
    for module, entry_id, dialect, sql, _ in _iter_sql_texts():
        out.setdefault((module, entry_id), {})[dialect] = sql
    return out


def test_migration_columns_match_across_dialects():
    """同名条目双方言都能解析出列名时，列名集合必须相同（防只改一边）。"""
    problems: list[str] = []
    for (module, entry_id), texts in _sql_by_entry().items():
        if "mysql" not in texts or "sqlite" not in texts:
            continue
        mysql_cols = _declared_columns(texts["mysql"])
        sqlite_cols = _declared_columns(texts["sqlite"])
        if not mysql_cols or not sqlite_cols:
            continue
        only_mysql = sorted(mysql_cols - sqlite_cols)
        only_sqlite = sorted(sqlite_cols - mysql_cols)
        if only_mysql or only_sqlite:
            problems.append(
                f"migrations/{module}.py 的 id={entry_id}："
                f"仅 MySQL 有 {only_mysql}，仅 SQLite 有 {only_sqlite}"
            )

    assert not problems, (
        "同一个迁移条目的两个方言列名集合不一致 —— 本机只跑 SQLite 分支，"
        "漏改 MySQL 侧不会有任何本地症状：\n  " + "\n  ".join(problems)
    )
