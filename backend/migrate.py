"""轻量数据库迁移执行器 —— Alembic 的替代方案（决策见 docs/entrust/decisions/0001）。

借鉴 Alembic 的两个核心思想：**迁移文件版本化** 与 **执行历史持久化**；不引入新依赖，
迁移全部是手写 SQL，与业务代码同仓同评审。

目录约定::

    backend/
      migrate.py          # 本执行器
      migrations/
        <table>.py        # 每个（或每组）表一个迁移模块

迁移模块导出 `migrations` 列表，条目格式::

    {"id": 1, "description": "...", "sql": "..." | {"mysql": "...", "sqlite": "..."},
     "checks": ["SELECT COUNT(*) FROM `t`"]}

纪律（详见 docs/06-database-migration.md）：
  * ``id`` 只增不改，已上线条目的 SQL 严禁修改，新需求追加新 id；
  * 执行器按 ``(module, migration_id)`` 去重，重复执行是安全的；
  * 没有自动 downgrade，回退要显式追加反向迁移条目。

用法（工作目录 ``backend/``）::

    python migrate.py --status     # 只看状态，不执行
    python migrate.py --dry-run    # 打印将要执行的 SQL，不落库
    python migrate.py              # 执行所有待执行迁移
    python migrate.py --verify     # 执行 + 复跑 checks + 断言无待执行（CI 用）
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Engine, Inspector, create_engine, inspect, text
from sqlalchemy.engine import Connection

HISTORY_TABLE = "_migration_history"

SUPPORTED_DIALECTS = ("mysql", "sqlite")


class MigrationError(RuntimeError):
    """迁移定义或执行过程中的不可继续错误。"""


@dataclass(frozen=True)
class Entry:
    """一条迁移条目。"""

    module: str
    migration_id: int
    description: str
    sql: str | dict[str, str]
    checks: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.module}:{self.migration_id}"


def load_entries() -> list[Entry]:
    """按模块名排序加载全部迁移条目，并做结构与顺序校验。"""
    package = importlib.import_module("migrations")
    entries: list[Entry] = []

    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"migrations.{info.name}")
        raw = getattr(module, "migrations", None)
        if raw is None:
            raise MigrationError(f"迁移模块 migrations/{info.name}.py 未导出 `migrations`")
        entries.extend(_parse_entries(info.name, raw))

    return entries


def _parse_entries(module: str, raw: object) -> list[Entry]:
    if not isinstance(raw, list) or not raw:
        raise MigrationError(f"migrations/{module}.py 的 `migrations` 必须是非空列表")

    entries: list[Entry] = []
    seen: set[int] = set()

    for item in raw:
        if not isinstance(item, dict):
            raise MigrationError(f"migrations/{module}.py 的条目必须是 dict，实际为 {type(item)}")

        raw_id = item.get("id")
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            raise MigrationError(f"migrations/{module}.py 条目的 id 必须是 int，实际为 {raw_id!r}")
        if raw_id in seen:
            raise MigrationError(f"migrations/{module}.py 的 id {raw_id} 重复（id 只增不改）")
        seen.add(raw_id)

        sql = item.get("sql")
        if isinstance(sql, str):
            if not sql.strip():
                raise MigrationError(f"{module}:{raw_id} 的 sql 为空")
        elif isinstance(sql, dict) and sql:
            for dialect, statement in sql.items():
                if not isinstance(statement, str) or not statement.strip():
                    raise MigrationError(f"{module}:{raw_id} 方言 {dialect} 的 sql 无效")
        else:
            raise MigrationError(f"{module}:{raw_id} 的 sql 必须是非空 str 或非空 dict")

        checks = item.get("checks") or []
        if not isinstance(checks, list) or not all(
            isinstance(c, str) and c.strip() for c in checks
        ):
            raise MigrationError(f"{module}:{raw_id} 的 checks 必须是非空字符串列表")
        if not checks:
            raise MigrationError(f"{module}:{raw_id} 缺少 checks（迁移后的最后一道保险）")

        description = item.get("description") or ""
        if not isinstance(description, str) or not description.strip():
            raise MigrationError(f"{module}:{raw_id} 缺少 description（迁移历史需要可读的描述）")

        entries.append(
            Entry(
                module=module,
                migration_id=raw_id,
                description=description.strip(),
                sql=sql,
                checks=list(checks),
            )
        )

    entries.sort(key=lambda e: e.migration_id)
    return entries


def resolve_sql(entry: Entry, dialect: str) -> str:
    """按方言取 SQL；缺少对应方言即报错，不允许静默跳过。"""
    if isinstance(entry.sql, str):
        return entry.sql
    if dialect in entry.sql:
        return entry.sql[dialect]
    if "default" in entry.sql:
        return entry.sql["default"]
    raise MigrationError(f"{entry.ref} 未提供方言 {dialect} 的 SQL（已提供：{sorted(entry.sql)}）")


def split_statements(sql_text: str) -> list[str]:
    """按分号切分语句。DDL 中请勿在字符串字面量内使用分号。"""
    return [part.strip() for part in sql_text.split(";") if part.strip()]


def history_ddl(dialect: str) -> str:
    """迁移历史表的建表语句（按方言）。"""
    if dialect == "mysql":
        return f"""
CREATE TABLE IF NOT EXISTS `{HISTORY_TABLE}` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `module` VARCHAR(64) NOT NULL,
  `migration_id` INT UNSIGNED NOT NULL,
  `description` VARCHAR(255) NOT NULL DEFAULT '',
  `executed_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_module_migration` (`module`, `migration_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
    if dialect == "sqlite":
        return f"""
CREATE TABLE IF NOT EXISTS `{HISTORY_TABLE}` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `module` TEXT NOT NULL,
  `migration_id` INTEGER NOT NULL,
  `description` TEXT NOT NULL DEFAULT '',
  `executed_at` TEXT NOT NULL,
  UNIQUE (`module`, `migration_id`)
)
"""
    raise MigrationError(f"不支持的数据库方言：{dialect}（支持：{', '.join(SUPPORTED_DIALECTS)}）")


def ensure_history(conn: Connection, dialect: str) -> None:
    for statement in split_statements(history_ddl(dialect)):
        conn.exec_driver_sql(statement)


def applied_ids(conn: Connection, module: str) -> set[int]:
    rows = conn.execute(
        text(f"SELECT migration_id FROM {HISTORY_TABLE} WHERE module = :module"),
        {"module": module},
    )
    return {int(row[0]) for row in rows}


def split_entries(
    engine: Engine, entries: list[Entry] | None = None
) -> tuple[list[Entry], list[Entry]]:
    """把条目拆成 (已执行, 待执行)。历史表不存在时全部视为待执行。"""
    entries = entries if entries is not None else load_entries()
    inspector: Inspector = inspect(engine)
    if not inspector.has_table(HISTORY_TABLE):
        return [], list(entries)

    done: dict[str, set[int]] = {}
    with engine.begin() as conn:
        for module in {e.module for e in entries}:
            done[module] = applied_ids(conn, module)

    applied = [e for e in entries if e.migration_id in done.get(e.module, set())]
    pending = [e for e in entries if e.migration_id not in done.get(e.module, set())]
    return applied, pending


def apply_pending(
    engine: Engine,
    *,
    dry_run: bool = False,
    module_filter: str | None = None,
) -> list[Entry]:
    """执行所有待执行迁移，返回本次实际执行的条目。"""
    entries = [e for e in load_entries() if module_filter in (None, e.module)]
    _, pending = split_entries(engine, entries)

    if not pending:
        return []

    dialect = str(engine.dialect.name)
    if dialect not in SUPPORTED_DIALECTS:
        raise MigrationError(
            f"不支持的数据库方言：{dialect}（支持：{', '.join(SUPPORTED_DIALECTS)}）"
        )

    if dry_run:
        for entry in pending:
            print(f"[dry-run] {entry.ref} {entry.description}")
            for statement in split_statements(resolve_sql(entry, dialect)):
                print(f"    {statement}")
        return []

    executed: list[Entry] = []
    with engine.begin() as conn:
        ensure_history(conn, dialect)
        for entry in pending:
            print(f"→ {entry.ref} {entry.description}")
            for statement in split_statements(resolve_sql(entry, dialect)):
                conn.exec_driver_sql(statement)
            for check in entry.checks:
                conn.exec_driver_sql(check)
            conn.execute(
                text(
                    f"INSERT INTO {HISTORY_TABLE} "
                    "(module, migration_id, description, executed_at) "
                    "VALUES (:module, :migration_id, :description, :executed_at)"
                ),
                {
                    "module": entry.module,
                    "migration_id": entry.migration_id,
                    "description": entry.description,
                    "executed_at": datetime.now(UTC),
                },
            )
            executed.append(entry)

    return executed


def verify(engine: Engine) -> int:
    """执行迁移、复跑全部 checks、断言无待执行。返回进程退出码。"""
    apply_pending(engine)
    entries = load_entries()
    _, pending = split_entries(engine, entries)
    if pending:
        print("✗ 迁移执行后仍存在待执行条目：")
        for entry in pending:
            print(f"    {entry.ref} {entry.description}")
        return 1

    dialect = str(engine.dialect.name)
    with engine.begin() as conn:
        for entry in entries:
            for check in entry.checks:
                conn.exec_driver_sql(check)
    print(f"✓ {len(entries)} 条迁移全部就位，{dialect} 方言 checks 通过")
    return 0


def get_engine() -> Engine:
    """按应用配置创建引擎（DATABASE_URL 优先，开发/测试可指向 SQLite）。"""
    from app.core.config import get_settings

    url = get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="轻量数据库迁移执行器")
    parser.add_argument("--status", action="store_true", help="只打印已执行/待执行，不执行")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的 SQL")
    parser.add_argument("--verify", action="store_true", help="执行后断言无待执行并复跑 checks")
    parser.add_argument("--module", default=None, help="只处理指定迁移模块（文件名不含 .py）")
    args = parser.parse_args(argv)

    engine = get_engine()
    dialect = str(engine.dialect.name)

    if args.status:
        applied, pending = split_entries(
            engine, [e for e in load_entries() if args.module in (None, e.module)]
        )
        print(f"方言：{dialect}")
        print(f"已执行：{len(applied)}")
        for entry in applied:
            print(f"    {entry.ref} {entry.description}")
        print(f"待执行：{len(pending)}")
        for entry in pending:
            print(f"    {entry.ref} {entry.description}")
        return 0

    if args.verify:
        return verify(engine)

    executed = apply_pending(engine, dry_run=args.dry_run, module_filter=args.module)
    if args.dry_run:
        return 0
    print(f"✓ 本次执行 {len(executed)} 条迁移（方言：{dialect}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
