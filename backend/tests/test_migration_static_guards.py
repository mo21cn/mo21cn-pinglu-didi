"""迁移的**静态闸门**：补三处「本机看不见」的 MySQL 盲区。

背景（为什么需要它）
====================
本机**没有 MySQL**（无 `mysqld`、无 `docker`、3306 关闭）⇒ `migrations/*.py` 的
**mysql 分支从不执行**；SQLite 分支既不知道 utf8mb4、也没有列注释、索引键长也不受
3072 字节上限约束。于是有一类缺陷**本机永远绿、CI 才红**。已实测到的两例：

* `ent_contract:2` 的列 `COMMENT` 折成两行**相邻字符串字面量** ⇒ MySQL `1064`
  （「数据库迁移（MySQL 8.0）」与「并发集成（MySQL 8.0）」**两个 job 双红**，
  而并发 job 里那 14 条用例全部是 setup 阶段的 ERROR、**一条断言都没跑过**）；
  ⇒ 已由 `test_migration_sql_literals.py` 补上。
* `ent_capacity.py` 的文件名排在 `ent_commitment.py` **之前** ⇒ 空库上
  `no such table: ent_capacity_candidate`，而**既有库因为表已在而全绿**。

本文件补另外三处**纯静态就能判定**的盲区：

1. **索引键长上限**：InnoDB 单键 ≤ **3072 字节**；utf8mb4 下 `VARCHAR(768)` 就已触顶，
   超了在 MySQL 建表时报 `1071 Specified key was too long`。SQLite 完全不看这个。
2. **方言成对**：`migrate.resolve_sql` 只在**运行期**、对**实际使用的方言**报错 ⇒
   漏写 mysql 分支的症状是"本机全绿、CI 红"。这个纯静态可判定，必须在本地拦住。
3. **数值列的方言一致性**：不得出现浮点、不得出现裸 `DECIMAL`（无精度）、
   同名列两侧精度必须一致。

第 3 条的口径**以本仓既有约定为准**（不是从外部搬来的规矩）
------------------------------------------------------------
本仓把金额/吨位一律当**定点**存：mysql 侧 `DECIMAL(p,s)`，sqlite 侧 `TEXT`
（直证：`ent_commitment.py` 里 `capacity_tonnes DECIMAL(12,3)` 与 `TEXT NOT NULL`
是同一张表的两个方言分支，该条目注释原文即「**金额/吨位一律定点**」）。
SQLite 是动态类型：若把这类值落到 `REAL`，`950 vs 900` 的边界判定就会有二进制误差 ——
而本项目把容量/金额判定当**确定性事实**用。

⇒ 因此本闸门**允许** sqlite 侧是 `TEXT`（本仓约定），只拦这三件**确定是错的**：
浮点类型、裸 `DECIMAL`、两侧带精度类型却精度不一致。**不**强求 sqlite 必须是 `TEXT`
（那会把另一种合法写法判红，属于用闸门绑架设计）。

⚠️ **本文件的第一次草稿就是一台假阳性机器**（2026-09-17，落盘后首跑 5 项里红 2 项）：
判据写成"同名数值列在两侧都必须是数值类型"，于是把全仓所有 sqlite 侧 `TEXT` 的定点列
一次性全部报红。教训：**写"跨方言一致性"的闸门时，判据必须从本仓的既有约定里读出来，
而不是从"两个方言应该长得一样"这种直觉里推出来。** 闸门红的时候先怀疑闸门自己。

闸门自己也必须被验证（`_detects_*` 三例）
==========================================
"当前仓库全绿"这件事**不能**证明闸门有用 —— 它也可能是"什么都检测不到"。
所以扫描逻辑与断言分开：`_index_key_problems()` / `_dialect_problems()` /
`_numeric_problems()` 接收任意条目序列，`test_*` 拿真仓库断言为空，
`test_*_detects_*` 拿**伪造的违规 DDL** 断言必须报出。加了新闸门请照此配一例。

本文件**不**做什么
==================
**不做** MySQL 语法解析、不判断索引是否有用、不评价类型选得对不对 ——
那些必须由 CI 的 MySQL 8.0 job **真实执行**。这里只拦"纯文本层面就能**确定**判错"的三类，
判据宁可窄、也要零误报：误报会训练团队绕过闸门。

已知限制（**必须写出来**，否则是静默漏检）
==========================================
* 键定义要求**一行一个子句**（本仓迁移 DDL 全部如此）；无法解析的键定义会被显式报出，
  不会静默跳过；
* 列类型 → 字节数用的是 MySQL 的**上界**（utf8mb4 = 4 字节/字符），
  因此判据是"不超过 3072"的**必要**条件，不是充分条件 —— 但足以拦住真实踩过的坑；
* 第 3 条只看**列声明**：SQLite 是动态类型，声明成 `TEXT` 的列照样能被绑进一个 float
  ⇒「声明合规」不等于「写进去的解析结果合规」。那要靠用例而不是文本闸门。
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
INDEX_KEY_LIMIT_BYTES = 3072
UTF8MB4_MAX_BYTES_PER_CHAR = 4

#: 条目序列的类型别名：(模块名, 条目 id, 方言, SQL 文本, 起始行)
Entries = Iterable[tuple[str, Any, str, str, int]]

#: 定长类型的字节数（MySQL 存储上界）。变长类型由专用函数算。
_FIXED_BYTES: dict[str, int] = {
    "BIGINT": 8,
    "INT": 4,
    "INTEGER": 4,
    "MEDIUMINT": 3,
    "SMALLINT": 2,
    "TINYINT": 1,
    "BOOLEAN": 1,
    "BOOL": 1,
    "DATETIME": 8,
    "TIMESTAMP": 8,
    "DATE": 3,
    "TIME": 3,
    "YEAR": 1,
    "BIT": 8,
    "ENUM": 2,
    "SET": 8,
}

#: 这些类型**不能**直接进索引（必须先给前缀长度），否则 MySQL 报 1170。
_NEEDS_PREFIX = (
    "TEXT",
    "TINYTEXT",
    "MEDIUMTEXT",
    "LONGTEXT",
    "BLOB",
    "TINYBLOB",
    "MEDIUMBLOB",
    "LONGBLOB",
    "JSON",
)

#: `CREATE TABLE <任意表名> (` —— 定位到**左括号之后**。
_CREATE_TABLE_HEAD_RE = re.compile(r"CREATE\s+TABLE\b[^(]*\(", re.I)

#: 列定义：`列名` 类型(参数)
_COLUMN_RE = re.compile(r"^`([a-z0-9_]+)`\s+([A-Za-z]+)\s*(?:\(([^()]*)\))?")

#: 键定义起始（含 FULLTEXT / SPATIAL，避免"只认 KEY 就静默漏检"）
_KEY_START_RE = re.compile(
    r"^(?:PRIMARY\s+KEY|UNIQUE\s+KEY|UNIQUE\s+INDEX|FULLTEXT\s+KEY|SPATIAL\s+KEY|KEY|INDEX)\b",
    re.I,
)
#: 键子句的括号清单；⚠️ 真实 DDL 里键子句**带尾逗号**（`KEY `k` (`a`),`）
_KEY_PARENS_RE = re.compile(r"\(((?:[^()]|\([^()]*\))*)\)\s*,?\s*$")

#: 带精度的定点类型（mysql 侧的正常写法）。
_NUMERIC_RE = re.compile(r"^(?:DECIMAL|NUMERIC|DEC)$", re.I)
#: 浮点类型 —— 金额/数量落到这里就是错的（`950 vs 900` 会有二进制误差）。
_FLOATY_RE = re.compile(r"^(?:FLOAT|DOUBLE|REAL)$", re.I)


def _iter_sql_texts() -> Iterator[tuple[str, Any, str, str, int]]:
    """产出 (模块名, 条目 id, 方言, SQL 文本, 起始行)。

    与 `test_migration_sql_literals.py` 里的同名函数**刻意各留一份**：
    两个闸门要能独立演进（一个扫描器的内部改动不该悄悄改变另一个闸门的判据）。
    这段逻辑很短、且是纯函数，重复的成本低于耦合的成本。
    """
    package = importlib.import_module("migrations")
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"migrations.{info.name}")
        source = (BACKEND / "migrations" / f"{info.name}.py").read_text(encoding="utf-8")
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


def _decimal_bytes(precision: int) -> int:
    """MySQL DECIMAL 的存储字节数：每 9 位十进制占 4 字节，余数按下表。"""
    whole, rest = divmod(precision, 9)
    return whole * 4 + [0, 1, 1, 2, 2, 3, 3, 4, 4][rest]


def _column_bytes(type_name: str, args: str | None) -> int | None:
    """列的索引占用字节数；**无法判定**时返回 None（由调用方显式报出，不静默跳过）。"""
    upper = type_name.upper()
    if upper in ("VARCHAR", "CHAR", "VARBINARY", "BINARY"):
        if not args:
            return None
        head = args.split(",")[0].strip()
        if not head.isdigit():
            return None
        n = int(head)
        return n if upper in ("VARBINARY", "BINARY") else n * UTF8MB4_MAX_BYTES_PER_CHAR
    if upper in ("DECIMAL", "NUMERIC", "DEC"):
        if not args:
            return None
        head = args.split(",")[0].strip()
        if not head.isdigit():
            return None
        return _decimal_bytes(int(head))
    return _FIXED_BYTES.get(upper)


def _create_table_bodies(text: str) -> list[str]:
    """取一段 SQL 文本里**每个** `CREATE TABLE` 的括号体（列定义 + 键定义）。

    ⚠️ 必须先按 `;` 切分语句：本仓 sqlite 分支一个条目里常写成
    `CREATE TABLE …;` 紧跟 `CREATE INDEX …;`。若直接在整段文本上用贪婪 `.*`，
    后面的 `CREATE INDEX` 会被吞进表体、它的列清单会被当成该表的键 ⇒
    凭空报出"键引用了未在同表声明的列"（假阳性）。
    """
    bodies: list[str] = []
    for statement in text.split(";"):
        head = _CREATE_TABLE_HEAD_RE.search(statement)
        if head is None:
            continue
        rest = statement[head.end() :]
        end = rest.rfind(")")
        bodies.append(rest if end < 0 else rest[:end])
    return bodies


def _parse_body(body: str) -> tuple[dict[str, tuple[str, str | None]], list[tuple[int, str]]]:
    """解析表体 → ({列名: (类型, 参数)}, [(键所在行号, 键子句)])。"""
    columns: dict[str, tuple[str, str | None]] = {}
    keys: list[tuple[int, str]] = []

    for offset, raw in enumerate(body.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if _KEY_START_RE.match(line):
            keys.append((offset + 1, line))
            continue
        col = _COLUMN_RE.match(line)
        if col:
            columns[col.group(1).lower()] = (col.group(2).upper(), col.group(3))

    return columns, keys


def _columns_of(text: str) -> dict[str, tuple[str, str | None]]:
    """一段方言文本的全部列（多张表时**按列名合并**；同名同义，够用且更简单）。"""
    out: dict[str, tuple[str, str | None]] = {}
    for body in _create_table_bodies(text):
        columns, _ = _parse_body(body)
        out.update(columns)
    return out


def _key_parts(clause: str) -> list[str] | None:
    """取键子句里括号内的列清单；解析不出返回 None（调用方报出，不静默跳过）。"""
    m = _KEY_PARENS_RE.search(clause)
    if m is None:
        return None
    return [part.strip() for part in m.group(1).split(",") if part.strip()]


def _part_bytes(part: str, columns: dict[str, tuple[str, str | None]]) -> tuple[int | None, str]:
    """一个键成员的字节数 + 说明（None = 无法判定）。"""
    m = re.match(r"^`([a-z0-9_]+)`\s*(?:\((\d+)\))?$", part, re.I)
    if m is None:
        return None, f"无法解析的键成员 {part!r}"

    name, prefix = m.group(1).lower(), m.group(2)
    if prefix is not None:
        # 前缀长度按**字节**计（utf8mb4 下 `col(10)` = 10 字节，不是 10 字符）。
        return int(prefix), f"{name} 前缀 {prefix} 字节"

    if name not in columns:
        return None, f"键引用了未在同表声明的列 {name!r}"
    type_name, args = columns[name]
    if type_name in _NEEDS_PREFIX:
        return None, (
            f"列 {name!r} 是 {type_name}：不能直接进索引，必须给前缀长度（否则 MySQL 报 1170）"
        )
    size = _column_bytes(type_name, args)
    if size is None:
        return None, f"列 {name!r} 的类型 {type_name} 无法判定索引字节数（请补进 _FIXED_BYTES）"
    return size, f"{name} {type_name}={size} 字节"


def _index_key_problems(entries: Entries) -> list[str]:
    """扫描 MySQL 建表语句里的索引键长；返回问题清单（空 = 通过）。"""
    problems: list[str] = []

    for module, entry_id, dialect, sql, _ in entries:
        if dialect != "mysql":
            continue
        for body in _create_table_bodies(sql):
            columns, keys = _parse_body(body)
            if not columns:
                continue

            for _, clause in keys:
                parts = _key_parts(clause)
                if parts is None:
                    problems.append(
                        f"migrations/{module}.py id={entry_id}：无法解析键定义 ⇒ {clause}"
                    )
                    continue

                total = 0
                notes: list[str] = []
                for part in parts:
                    size, note = _part_bytes(part, columns)
                    notes.append(note)
                    if size is None:
                        problems.append(
                            f"migrations/{module}.py id={entry_id} 键「{clause}」：{note}"
                        )
                        continue
                    total += size

                if total > INDEX_KEY_LIMIT_BYTES:
                    problems.append(
                        f"migrations/{module}.py id={entry_id} 键「{clause}」= {total} 字节 "
                        f"> {INDEX_KEY_LIMIT_BYTES}（{' + '.join(notes)}）"
                    )

    return problems


def _dialect_problems(entries: Entries) -> list[str]:
    """每个条目的方言分支必须成对；返回问题清单（空 = 通过）。"""
    seen: dict[tuple[str, Any], set[str]] = {}
    for module, entry_id, dialect, _text, _ in entries:
        seen.setdefault((module, entry_id), set()).add(dialect)

    bad: list[str] = []
    for (module, entry_id), dialects in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        unknown = dialects - {"mysql", "sqlite", "default"}
        if unknown:
            bad.append(f"migrations/{module}.py id={entry_id}：出现未知方言键 {sorted(unknown)}")
        if not ({"mysql", "sqlite"} <= dialects or "default" in dialects):
            bad.append(
                f"migrations/{module}.py id={entry_id}：方言分支不全 ⇒ {sorted(dialects)}"
                "（必须 mysql + sqlite 成对，或给出 default）"
            )
    return bad


def _norm(args: str | None) -> str:
    return (args or "").replace(" ", "")


def _numeric_problems(entries: Entries) -> list[str]:
    """数值列不得浮点 / 不得裸精度 / 两侧定点精度须一致；返回问题清单（空 = 通过）。"""
    seen: dict[tuple[str, Any], dict[str, str]] = {}
    for module, entry_id, dialect, sql, _ in entries:
        seen.setdefault((module, entry_id), {})[dialect] = sql

    problems: list[str] = []
    for (module, entry_id), texts in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        numeric: dict[str, dict[str, str]] = {}
        for dialect, text in texts.items():
            columns = _columns_of(text)
            numeric[dialect] = {
                name: _norm(args)
                for name, (type_name, args) in columns.items()
                if _NUMERIC_RE.match(type_name)
            }
            for name, (type_name, args) in columns.items():
                if _FLOATY_RE.match(type_name):
                    problems.append(
                        f"migrations/{module}.py id={entry_id} [{dialect}]：列 {name!r} 用 "
                        f"{type_name}（浮点）—— 金额/数量必须定点"
                    )
                if _NUMERIC_RE.match(type_name) and not _norm(args):
                    problems.append(
                        f"migrations/{module}.py id={entry_id} [{dialect}]：列 {name!r} 的 "
                        f"{type_name} 未写精度"
                    )

        mysql_num = numeric.get("mysql", {})
        sqlite_num = numeric.get("sqlite", {})
        for name in sorted(set(mysql_num) & set(sqlite_num)):
            if mysql_num[name] != sqlite_num[name]:
                problems.append(
                    f"migrations/{module}.py id={entry_id}：列 {name!r} 精度不一致 "
                    f"mysql=({mysql_num[name]}) sqlite=({sqlite_num[name]})"
                )
        for name in sorted(set(sqlite_num) - set(mysql_num)):
            problems.append(
                f"migrations/{module}.py id={entry_id}：列 {name!r} 只在 sqlite 侧是定点列"
                "（mysql 侧漏了精度？）"
            )

    return problems


# --------------------------------------------------------------------------
# 真仓库断言
# --------------------------------------------------------------------------


def test_mysql_index_keys_within_the_innodb_limit():
    """每个 MySQL 索引的键字节数必须 ≤ 3072（utf8mb4），且 TEXT/BLOB 不得直接进索引。"""
    problems = _index_key_problems(_iter_sql_texts())
    assert not problems, (
        "MySQL 索引键超长或无法判定 —— 本机 SQLite 分支不受 3072 字节约束、"
        "不会红，而 MySQL 建表会报 1071：\n  " + "\n  ".join(problems)
    )


def test_every_entry_declares_both_dialects():
    """每个条目的 `sql` 字典必须同时给出 mysql 与 sqlite（或给出 `default`）。

    `migrate.resolve_sql` 只在**运行期**、对**实际方言**报错 ⇒ 漏写 mysql 分支时
    本机 SQLite 全绿、CI 的 MySQL job 才炸。静态即可判定，所以必须在这里拦。
    """
    bad = _dialect_problems(_iter_sql_texts())
    assert not bad, (
        "迁移条目的方言分支不成对 —— 本机只跑 SQLite，漏写 mysql 侧不会有本地症状，"
        "而 CI 的 MySQL job 会直接失败：\n  " + "\n  ".join(bad)
    )


def test_numeric_columns_do_not_use_floats_or_bare_precision():
    """数值列：不得浮点、不得裸 `DECIMAL`、两侧定点精度须一致（判据出自本仓约定）。"""
    problems = _numeric_problems(_iter_sql_texts())
    assert not problems, (
        "数值列的声明有问题 —— 浮点 / 裸精度 / 两侧精度不一致，本机只跑一侧、"
        "不会有本地症状：\n  " + "\n  ".join(problems)
    )


# --------------------------------------------------------------------------
# 闸门自证：喂入**伪造的违规 DDL**，必须报出来
#   否则"仓库全绿"也可能只是"什么都检测不到"。
# --------------------------------------------------------------------------


def _fake(module: str, entry_id: Any, dialect: str, sql: str) -> tuple[str, Any, str, str, int]:
    return module, entry_id, dialect, sql, 1


def test_index_key_guard_detects_too_long_key_and_text_key_and_bad_syntax():
    """3200 字节的键、TEXT 直入索引、无法解析的键定义，三种都必须被报出。"""
    too_long = _fake(
        "synthetic",
        1,
        "mysql",
        "CREATE TABLE `t` (\n"
        "  `id` BIGINT UNSIGNED NOT NULL,\n"
        "  `name` VARCHAR(800) NOT NULL,\n"
        "  PRIMARY KEY (`id`),\n"
        "  KEY `k_name` (`name`),\n"
        ") ENGINE=InnoDB\n",
    )
    got = _index_key_problems([too_long])
    assert len(got) == 1, got
    assert "3200 字节" in got[0], got

    text_key = _fake(
        "synthetic",
        2,
        "mysql",
        "CREATE TABLE `t` (\n  `body` TEXT NOT NULL,\n  KEY `k_body` (`body`),\n) ENGINE=InnoDB\n",
    )
    got2 = _index_key_problems([text_key])
    assert len(got2) == 1 and "不能直接进索引" in got2[0], got2

    # 键子句里的列清单**不在行尾**（`… USING BTREE,`）⇒ 解析不出 ⇒ 必须显式报出，
    # 而不是静默跳过（静默跳过 = 这条键永远不受检）。
    unparsable = _fake(
        "synthetic",
        3,
        "mysql",
        "CREATE TABLE `t` (\n"
        "  `id` BIGINT NOT NULL,\n"
        "  KEY `k_id` (`id`) USING BTREE,\n"
        ") ENGINE=InnoDB\n",
    )
    got3 = _index_key_problems([unparsable])
    assert len(got3) == 1 and "无法解析键定义" in got3[0], got3

    # 边界两侧都钉住：单列 `VARCHAR(768)` = **恰好 3072** 字节（判据是 `>`，到顶不算超）
    # ⇒ 不能误报；
    at_limit = _fake(
        "synthetic",
        4,
        "mysql",
        "CREATE TABLE `t` (\n"
        "  `name` VARCHAR(768) NOT NULL,\n"
        "  KEY `k_name` (`name`),\n"
        ") ENGINE=InnoDB\n",
    )
    assert _index_key_problems([at_limit]) == []

    # ⇒ 但同一列一进**复合键**（再 +BIGINT 8 字节 = 3080）就超，必须报。
    over = _fake(
        "synthetic",
        5,
        "mysql",
        "CREATE TABLE `t` (\n"
        "  `id` BIGINT NOT NULL,\n"
        "  `name` VARCHAR(768) NOT NULL,\n"
        "  UNIQUE KEY `uk_name` (`id`, `name`),\n"
        ") ENGINE=InnoDB\n",
    )
    got4 = _index_key_problems([over])
    assert len(got4) == 1 and "3080 字节" in got4[0], got4


def test_dialect_guard_detects_missing_and_unknown_dialects():
    """只给 mysql、只给 sqlite、给出未知方言键 —— 三种都必须被报出。"""
    only_mysql = _dialect_problems([_fake("synthetic", 1, "mysql", "SELECT 1")])
    assert len(only_mysql) == 1 and "方言分支不全" in only_mysql[0], only_mysql

    only_sqlite = _dialect_problems([_fake("synthetic", 2, "sqlite", "SELECT 1")])
    assert len(only_sqlite) == 1, only_sqlite

    # 未知方言键会**同时**触发两条（未知 + 分支不全）—— 这是有意的双报，不是重复：
    # 一条说"这个方言键我不认识"，另一条说"合起来仍不满足成对要求"。
    unknown = _dialect_problems([_fake("synthetic", 3, "oracle", "SELECT 1")])
    assert len(unknown) == 2, unknown
    assert any("未知方言键" in x for x in unknown), unknown
    assert any("方言分支不全" in x for x in unknown), unknown

    paired = _dialect_problems(
        [
            _fake("synthetic", 4, "mysql", "SELECT 1"),
            _fake("synthetic", 4, "sqlite", "SELECT 1"),
        ]
    )
    assert paired == []

    via_default = _dialect_problems([_fake("synthetic", 5, "default", "SELECT 1")])
    assert via_default == [], "给出 default 的条目不应被判红"


def test_numeric_guard_detects_float_bare_decimal_and_precision_drift():
    """浮点、裸 DECIMAL、两侧精度不一致 —— 三种都必须被报出；sqlite 用 TEXT 不算违规。"""
    floaty = _numeric_problems(
        [_fake("synthetic", 1, "sqlite", "CREATE TABLE `t` (\n`qty` REAL NULL\n);\n")]
    )
    assert len(floaty) == 1 and "浮点" in floaty[0], floaty

    bare = _numeric_problems(
        [_fake("synthetic", 2, "mysql", "CREATE TABLE `t` (\n`rate` DECIMAL NULL\n);\n")]
    )
    assert len(bare) == 1 and "未写精度" in bare[0], bare

    drift = _numeric_problems(
        [
            _fake(
                "synthetic", 3, "mysql", "CREATE TABLE `t` (\n`qty` DECIMAL(12,3) NOT NULL\n);\n"
            ),
            _fake(
                "synthetic", 3, "sqlite", "CREATE TABLE `t` (\n`qty` NUMERIC(14,3) NOT NULL\n);\n"
            ),
        ]
    )
    assert len(drift) == 1 and "精度不一致" in drift[0], drift

    only_sqlite_fixed = _numeric_problems(
        [
            _fake("synthetic", 4, "mysql", "CREATE TABLE `t` (\n`qty` VARCHAR(32) NOT NULL\n);\n"),
            _fake(
                "synthetic", 4, "sqlite", "CREATE TABLE `t` (\n`qty` NUMERIC(12,3) NOT NULL\n);\n"
            ),
        ]
    )
    assert len(only_sqlite_fixed) == 1 and "只在 sqlite 侧" in only_sqlite_fixed[0], (
        only_sqlite_fixed
    )

    # 本仓约定：mysql DECIMAL ↔ sqlite TEXT —— **不能**报红
    convention = _numeric_problems(
        [
            _fake(
                "synthetic", 5, "mysql", "CREATE TABLE `t` (\n`qty` DECIMAL(12,3) NOT NULL\n);\n"
            ),
            _fake("synthetic", 5, "sqlite", "CREATE TABLE `t` (\n`qty` TEXT NOT NULL\n);\n"),
        ]
    )
    assert convention == [], convention
