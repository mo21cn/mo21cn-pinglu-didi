"""数据库迁移执行器的行为契约测试。

覆盖四件事：
1. 迁移条目的结构纪律（id 唯一、description 与 checks 必填）；
2. 待执行 → 执行 → 再次执行 的幂等链路；
3. 方言字典解析（sqlite / mysql）与缺方言时报错；
4. `ent_` 前缀表不进 `create_all`，只能由迁移创建。
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from migrate import (
    Entry,
    MigrationError,
    _parse_entries,
    apply_pending,
    load_entries,
    resolve_sql,
    split_entries,
    split_statements,
)


def _memory_engine():
    """全新的 SQLite 内存库（同一连接共享，便于断言）。"""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_entries_are_well_formed():
    entries = load_entries()
    assert entries, "migrations/ 下至少要有一条迁移"

    seen: dict[str, list[int]] = {}
    for entry in entries:
        assert entry.description, f"{entry.ref} 缺少 description"
        assert entry.checks, f"{entry.ref} 缺少 checks"
        seen.setdefault(entry.module, []).append(entry.migration_id)

    for module, ids in seen.items():
        assert len(ids) == len(set(ids)), f"migrations/{module}.py 的 id 重复：{ids}"
        assert ids == sorted(ids), f"migrations/{module}.py 的 id 必须递增：{ids}"


def test_pending_then_applied_then_idempotent():
    engine = _memory_engine()
    entries = load_entries()

    applied, pending = split_entries(engine, entries)
    assert not applied, "全新库不应有任何已执行记录"
    assert len(pending) == len(entries)

    executed = apply_pending(engine)
    assert len(executed) == len(entries)

    applied, pending = split_entries(engine, entries)
    assert not pending, "执行后不应仍有待执行条目"
    assert len(applied) == len(entries)

    # 重复执行：不报错、不新增历史记录
    assert apply_pending(engine) == []
    with engine.begin() as conn:
        rows = list(conn.exec_driver_sql("SELECT COUNT(*) FROM _migration_history"))
    assert rows[0][0] == len(entries)


def test_checks_pass_after_apply():
    engine = _memory_engine()
    apply_pending(engine)

    with engine.begin() as conn:
        for entry in load_entries():
            for check in entry.checks:
                conn.exec_driver_sql(check)  # 不抛异常即通过


def test_split_statements_ignores_trailing_semicolon():
    assert split_statements("CREATE TABLE t (id INTEGER);") == ["CREATE TABLE t (id INTEGER)"]
    assert split_statements("CREATE TABLE a (id INTEGER);\nCREATE INDEX b ON a (id);") == [
        "CREATE TABLE a (id INTEGER)",
        "CREATE INDEX b ON a (id)",
    ]


def test_resolve_sql_by_dialect():
    entry = Entry(
        module="demo",
        migration_id=1,
        description="demo",
        sql={"mysql": "SELECT 1", "sqlite": "SELECT 2"},
        checks=[],
    )
    assert resolve_sql(entry, "sqlite") == "SELECT 2"
    assert resolve_sql(entry, "mysql") == "SELECT 1"

    missing = Entry(module="demo", migration_id=2, description="demo", sql={"mysql": "SELECT 1"})
    try:
        resolve_sql(missing, "sqlite")
    except MigrationError as exc:
        assert "sqlite" in str(exc)
    else:
        raise AssertionError("缺少对应方言时应抛 MigrationError")


def test_malformed_entries_are_rejected():
    base = {"id": 1, "description": "ok", "sql": "SELECT 1", "checks": ["SELECT 1"]}

    for broken, keyword in [
        ({**base, "checks": []}, "checks"),
        ({**base, "description": "  "}, "description"),
        ({**base, "sql": "   "}, "sql"),
        ({**base, "id": "1"}, "id"),
    ]:
        try:
            _parse_entries("demo", [broken])
        except MigrationError as exc:
            assert keyword in str(exc)
        else:
            raise AssertionError(f"非法条目未被拒绝：{broken}")

    try:
        _parse_entries("demo", [base, base])
    except MigrationError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("重复 id 未被拒绝")


def test_migration_managed_tables_not_created_by_create_all():
    """`ent_` 前缀的表必须只能由迁移创建，`create_all` 不得代劳。"""
    from app.core.config import get_settings
    from app.models import Base

    engine = _memory_engine()
    Base.metadata.create_all(bind=engine)

    prefix = get_settings().MIGRATION_MANAGED_TABLE_PREFIX
    assert prefix == "ent_"

    managed = [name for name in inspect(engine).get_table_names() if name.startswith(prefix)]
    assert managed == [], f"create_all 不应创建迁移管理的表，实际创建了：{managed}"

    apply_pending(engine)
    names = inspect(engine).get_table_names()
    assert "ent_idempotency" in names
    assert "_migration_history" in names
