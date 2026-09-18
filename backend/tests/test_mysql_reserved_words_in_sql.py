"""运行期 SQL 的**零依赖文本闸**：`INSERT` / `UPDATE` 的列名不得是未加引号的 MySQL 保留字。

为什么需要它（一次真实事故）
============================
2026-09-18：`settlement.py` 的
`INSERT INTO ent_settlement (assignment_id, version_no, status, **lines**, ...)`
把 `lines` 写成**未加引号**的列名。`LINES` 是 **MySQL 8 的保留字**
（`LOAD DATA ... LINES TERMINATED BY`）：

* **SQLite 上完全正常**（开发库、测试库、演示库都是 SQLite）；
* **MySQL 上直接 1064 语法错** —— 也就是**生产方言**上一条结算版本都建不出来。

而且它在本机**和门禁里都看不见**：S7-3 的用例原先没有 `mysql` 标记，
`pytest -m mysql` 从来不会跑到那条语句。最后是 S4-b 新增的 MySQL 并发锚点
（它必须先造一个"可结案"的委托 ⇒ 必须调用 `create_settlement`）把它撞了出来。

⇒ 这类缺陷的形状是「**一种方言通过、另一种方言报错，而 CI 的默认 job 只跑前者**」，
正是"零依赖文本闸"该管的事（与 `test_migration_sql_literals.py` 同一族）。

判据与边界
----------
* 只扫 `app/modules/entrust/*.py` 里 `INSERT INTO` / `UPDATE` 的**列名位置**；
* 保留字表是**精选**的（只收录"可能被当成列名"的 MySQL 8 保留字）——
  ⛔ 刻意**不含** `MODE`：它在 MySQL 8 里是**非保留**关键字，收进来会造出
  5 条假警（本仓 `ent_settlement_payment.mode` 等列名都合法）。
  本文件的假阳性比假阴性贵：一条假警会让人开始"关闸"。
* 已经加反引号的列名**一律放行**（引号就是解法本身）。
"""

from __future__ import annotations

import re
from pathlib import Path

MODULES = Path(__file__).resolve().parents[1] / "app" / "modules" / "entrust"

#: 精选的 MySQL 8 保留字（只收"可能被当列名"的那些）。
#: ⚠️ 收录标准：**MySQL 8.0 关键字表里明确标为 reserved** 且**在业务列名里说得通**。
#: 想加新词时先确认它真的是 reserved（`MODE` / `SOURCE` / `STATUS` / `KIND` / `VALUE`
#: / `TYPE` / `LEVEL` / `POSITION` 都是**非**保留字，别加）。
RESERVED = frozenset(
    [
        "lines",
        "rows",
        "row",
        "groups",
        "rank",
        "system",
        "usage",
        "condition",
        "interval",
        "range",
        "read",
        "write",
        "lock",
        "cascade",
        "check",
        "partition",
        "precision",
        "repeat",
        "require",
        "signal",
        "specific",
        "sql",
        "table",
        "then",
        "values",
        "when",
        "window",
        "with",
        "long",
        "call",
        "change",
        "char",
        "column",
        "describe",
        "distinct",
        "drop",
        "else",
        "exists",
        "explain",
        "force",
        "grant",
        "ignore",
        "index",
        "infile",
        "inner",
        "join",
        "left",
        "like",
        "limit",
        "load",
        "match",
        "natural",
        "null",
        "optimize",
        "option",
        "outer",
        "primary",
        "procedure",
        "purge",
        "regexp",
        "release",
        "rename",
        "replace",
        "restrict",
        "revoke",
        "right",
        "schema",
        "select",
        "show",
        "spatial",
        "terminated",
        "to",
        "trigger",
        "true",
        "union",
        "unique",
        "unlock",
        "unsigned",
        "update",
        "use",
        "using",
        "where",
        "while",
        "xor",
    ]
)

_INSERT = re.compile(r'INSERT\s+INTO\s+`?(\w+)`?[\s"]*\(([^)]*)\)', re.S)
_UPDATE = re.compile(r'UPDATE\s+`?(\w+)`?\s+SET\s+([^"]*?)(?:"|WHERE)', re.S | re.I)


def _columns(raw: str) -> list[tuple[str, str]]:
    """`(原样, 去引号后的小写标识符)` 列表。空片段跳过。"""
    out: list[tuple[str, str]] = []
    for piece in raw.replace("\n", " ").split(","):
        name = piece.strip().strip('"').strip()
        if not name:
            continue
        out.append((name, name.strip("`").split()[0].lower()))
    return out


def _scan() -> list[str]:
    problems: list[str] = []
    for path in sorted(MODULES.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for pattern in (_INSERT, _UPDATE):
            for match in pattern.finditer(src):
                table, raw = match.group(1), match.group(2)
                line = src[: match.start()].count("\n") + 1
                for original, ident in _columns(raw):
                    if original.startswith("`"):
                        continue  # 加了引号就是解法本身
                    if ident in RESERVED:
                        problems.append(f"{path.name}:{line} 表 {table} 的列 {original!r}")
    return problems


def test_no_unquoted_mysql_reserved_word_as_column_name():
    """`INSERT` / `UPDATE` 里不得出现未加引号的保留字列名。

    发现即红，并指名到 `文件:行 表 列` —— 修法只有一条：**给列名加反引号**
    （⛔ 不是改列名：那要先动迁移，而这里是运行期 SQL 的书写问题）。
    """
    problems = _scan()
    assert not problems, (
        "运行期 SQL 里出现了未加引号的 MySQL 保留字列名：\n  "
        + "\n  ".join(problems)
        + "\n⇒ 给这些列名加反引号。⚠️ 这类缺陷 SQLite 上看不出来（开发/测试/演示库都是"
        " SQLite），只有真实 MySQL 会报 1064 —— 见本文件头部的事故记录。"
    )


def test_the_scan_can_actually_fail():
    """闸门自身要**能失败**：拿一段"故意坏掉"的 SQL 跑一遍同一判据。

    没有这一条，正则写错（例如漏掉多行拼接的写法）会让上面那条用例**永远绿** ——
    而它的绿会被人当成"没有保留字问题"。这正是本仓"判据要能失败"那条纪律。
    """
    sample = "INSERT INTO t (a, lines, `mode`) VALUES (1, 2, 3)"
    found = [
        ident
        for _original, ident in _columns(_INSERT.search(sample).group(2))  # type: ignore[union-attr]
        if ident in RESERVED
    ]
    assert found == ["lines"], f"判据没抓出样本里的 lines：{found}"
