"""模型外键类型一致性（AC-25）。

**背景**：基线模型此前只在 SQLite（开发/测试库）上 `create_all`，从未在
MySQL 上建过表。MySQL 严格校验外键两侧列类型是否兼容，`users.id` 是
`BIGINT`（SQLite 变体为 `INTEGER`）而 `ships.owner_id` 等引用列是 `INT`，
于是报 `errno 3780`（Referencing column ... are incompatible）—— 该问题在
ENT-007 的 CI 首次真实执行 `create_all` 时暴露。

**本测试的作用**：按方言编译每一对外键的「引用列 / 被引用列」类型并逐一
比对，在普通 CI（不需要 MySQL 服务）即可拦住同类回归。真实 MySQL 上的
建表验证见 `tests/test_mysql_integration.py`（pytest-mysql job）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.engine.interfaces import Dialect

from app.core.config import get_settings
from app.models import Base

_DIALECTS: list[tuple[str, Dialect]] = [
    ("mysql", mysql.dialect()),
    ("sqlite", sqlite.dialect()),
]

#: 基线表（create_all 管理）——必须全部在 metadata 里，否则下述比对会漏检
_BASELINE_TABLES = frozenset(
    {"users", "ships", "cargo", "orders", "payments", "berths", "berth_appts", "agent_calls"}
)


def _mismatches(dialect: Dialect) -> list[str]:
    found: list[str] = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            left = fk.parent.type.compile(dialect=dialect)
            right = fk.column.type.compile(dialect=dialect)
            if left != right:
                found.append(
                    f"{table.name}.{fk.parent.name}({left}) -> {fk.target_fullname}({right})"
                )
    return sorted(found)


def test_metadata_exposes_all_baseline_tables() -> None:
    """所有基线表都在 metadata 中（模型新增后忘了在 __init__ 导出会漏检）。"""
    missing = sorted(_BASELINE_TABLES - set(Base.metadata.tables))
    assert missing == [], f"模型未导出：{missing}"


def test_migration_managed_tables_stay_out_of_metadata() -> None:
    """`ent_` 前缀的表由迁移管理，不得进 create_all（否则两套 DDL 打架）。"""
    prefix = get_settings().MIGRATION_MANAGED_TABLE_PREFIX
    leaked = sorted(n for n in Base.metadata.tables if n.startswith(prefix))
    assert leaked == [], f"迁移管理的表出现在 metadata 中：{leaked}"


@pytest.mark.parametrize("dialect_name, dialect", _DIALECTS, ids=[n for n, _ in _DIALECTS])
def test_foreign_key_column_types_are_consistent(dialect_name: str, dialect: Dialect) -> None:
    """每对外键的引用列与被引用列在该方言下编译出的类型必须一致。"""
    bad = _mismatches(dialect)
    assert bad == [], f"{dialect_name} 下外键类型不一致（MySQL 将拒绝建表）：{bad}"


def test_user_id_columns_share_the_primary_key_type() -> None:
    """引用 users.id 的列必须复用 USER_ID 类型（AC-25 的直接断言）。"""
    from sqlalchemy.dialects import mysql as _mysql

    users_id = Base.metadata.tables["users"].c.id
    referrers = [
        (table.name, fk.parent)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column is users_id
    ]
    assert len(referrers) == 8, f"users.id 的引用列数量变了，请同步本断言：{referrers}"
    for table_name, column in referrers:
        assert column.type.compile(dialect=_mysql.dialect()) == "BIGINT", (
            f"{table_name}.{column.name} 在 MySQL 上必须是 BIGINT"
        )
