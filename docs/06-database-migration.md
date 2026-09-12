# 06 · 数据库迁移

> 适用范围：`backend/` 全部表结构变更。
> 决策依据：[`docs/entrust/decisions/0001-数据库迁移机制选型.md`](entrust/decisions/0001-数据库迁移机制选型.md)（不引入 Alembic，采用轻量自建执行器）。

---

## 1. 为什么不用 Alembic

Alembic 的核心收益是 `autogenerate`（基于 ORM 元数据自动比对差异）。本项目业务层大量原生 SQL，
ORM 主要用于建表与少量 CRUD，autogenerate 省不下多少事，却要付出 `env.py` / `alembic.ini` /
版本链的完整学习成本，并引入新依赖。

因此本仓库只借鉴 Alembic 的两个思想，**自己实现**：

| Alembic 思想 | 本仓库实现 |
| --- | --- |
| 迁移文件版本化 | `backend/migrations/<table>.py`，条目带只增不改的 `id` |
| 执行历史持久化 | 库内 `_migration_history` 表，按 `(module, migration_id)` 去重 |

---

## 2. 目录结构

```
backend/
  migrate.py                 # 执行器（CLI）
  migrations/
    __init__.py
    ent_idempotency.py       # 幂等操作记录表
    ent_<表名>.py            # 委托支线后续新增表
```

- 一个模块对应一张表（或一组强耦合的表）。
- **模块名就是迁移历史里的 `module`**，一旦上线不可改名。

---

## 3. 迁移文件写法

```python
"""<这张表是干什么的 —— 一句话>"""
from __future__ import annotations

sql = [
    {
        "id": 1,
        "description": "创建 <表> 表",
        "sql": {
            "mysql": """...""",
            "sqlite": """...""",
        },
    },
]

checks = [
    "SELECT COUNT(*) FROM `<表>`",
]

# ── 执行器兼容格式，请勿修改 ──
migrations = [{**item, "checks": checks} for item in sql]
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 同一模块内唯一，**只增不改**。已上线条目的 SQL 严禁修改 |
| `description` | ✅ | 写入迁移历史，供人读。空串会被执行器拒绝 |
| `sql` | ✅ | 字符串（所有方言通用）或 `{"mysql": ..., "sqlite": ...}` 字典。缺少当前方言即报错，不静默跳过 |
| `checks` | ✅ | 迁移后执行的校验语句，最后一道保险。空列表会被执行器拒绝 |

**方言字典为什么是必需的**：开发/测试默认 SQLite，生产 MySQL。两者 DDL 差异很大
（`AUTO_INCREMENT` vs `AUTOINCREMENT`、`DATETIME` vs `TEXT`、索引语法等），
只写一种方言必然在另一个环境炸掉。

---

## 4. 命令

工作目录均为 `backend/`：

| 命令 | 作用 |
| --- | --- |
| `python migrate.py --status` | 打印已执行 / 待执行，不执行任何 SQL |
| `python migrate.py --dry-run` | 打印将要执行的语句，不落库 |
| `python migrate.py` | 执行所有待执行迁移 |
| `python migrate.py --module <名>` | 只处理指定模块 |
| `python migrate.py --verify` | 执行 + 复跑全部 `checks` + 断言无待执行；不满足则退出码非 0（CI 用） |

数据库连接来自 `app.core.config` 的 `DATABASE_URL`（未设置时按 `DB_*` 拼 MySQL URL），
与运行环境一致，不需要单独配置。

---

## 5. 纪律（评审时逐条对照）

1. **id 只增不改。** 已上线的条目改 SQL 或改 id，会让执行器认不出它，可能重复执行或漏执行。
2. **新需求追加新 id。** 加字段不是改 `id: 1`，而是新增 `id: 2` 写 `ALTER TABLE`。
3. **无自动 downgrade。** 需要回退就**追加一条反向迁移**（如 `id: 3` 写 `DROP COLUMN`），
   不要假设"撤销上一次执行"是安全的。
4. **迁移与业务代码同仓同评审。** 迁移文件必须和业务代码在同一个 PR 里。
5. **迁移是非事务的。** MySQL DDL 隐式提交，一条失败即停止并报错，执行器不回滚 —— 失败要人工介入。
6. **DDL 里不要在字符串字面量中使用分号**（执行器按 `;` 切分语句）。

---

## 6. 与 `create_all` 的边界

- `app/main.py` 启动钩子仍会 `Base.metadata.create_all`，但**排除** `ent_` 前缀的表
  （前缀由 `MIGRATION_MANAGED_TABLE_PREFIX` 配置，默认 `ent_`）。
- 委托支线新增表统一用 `ent_` 前缀，从第一天起就由迁移创建，
  避免"先 `create_all` 再补迁移"的中间态。
- 既有表（users / ships / cargos / orders / payments / ports 等）维持 `create_all` 现状，本次不动。
- 测试：`tests/conftest.py` 在建完内存库后显式调用 `apply_pending()`，
  保证测试库结构与 `migrations/` 一致。

---

## 7. CI 门禁

`ci.yml` 的 `db-migration` job 在全新 SQLite 库上：

1. `python migrate.py --status`（应全部待执行）
2. `python migrate.py --verify`（执行 + 校验 + 断言无待执行）
3. 再跑一次 `--verify`（**证明幂等**：无待执行、不重复建表）

后端 job 的类型检查已扩展为 `mypy app migrations migrate.py`。

> ⚠️ **已知限制**：MySQL 方言的 SQL 目前只经过人工评审，CI 未真实执行
> （取决于 DR-0002「MySQL 集成测试环境」）。在此之前，相关验收子项记 `not-run`，不得记为通过。

---

## 8. 新增一张表的完整步骤

1. 在 `backend/migrations/` 新建 `ent_<表名>.py`，按第 3 节模板写 `id: 1`。
2. 本地 `python migrate.py --status` 确认它被识别为待执行。
3. `python migrate.py` 执行，`--verify` 确认通过。
4. 若同时新增 ORM 模型：表名必须以 `ent_` 开头，且不依赖 `create_all` 建表。
5. 迁移文件与业务代码一起提交，PR 模板"数据库迁移"栏填写新增的 `module:id`。
