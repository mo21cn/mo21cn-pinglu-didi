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

**`checks` 证明"语句能跑通"，不证明"数据对"**：执行器逐条 `exec_driver_sql(check)`，
一条 `SELECT` 引用了新列能跑通，就只能推出"该列存在"。真正的数据断言请写进 `backend/tests/`。

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
7. **模块名即执行顺序。** 执行器按**模块名字典序**应用
   （`load_entries` 里 `sorted(pkgutil.iter_modules(...), key=lambda m: m.name)`），
   **没有**声明式依赖。新模块若要对已有表做 `ALTER`，文件名必须**排在建表模块之后** ——
   `ent_assignment_completion.py` 排得进 `ent_assignment.py` 之后，而 `ent_capacity_*.py`
   会排在 `ent_commitment.py` **之前**。踩中的症状是**空库**报 `no such table`、
   而**既有库因为表已在而全绿**（最难在本地看见的一类）。
8. **长 `COMMENT` 绝不折行。** MySQL 不认 Python 的相邻字符串字面量拼接 —— 在三引号里把它
   折成两行就是两个相邻字面量，MySQL 报 `1064`。该写法会**同时打红两个 job**
   （「数据库迁移（MySQL 8.0 真实执行）」+「并发集成（MySQL 8.0 真实执行）」），
   而后者那批用例全是 setup 阶段的 `ERROR` ⇒ **没有一条断言真的执行过**，
   汇报口径只能是「未执行」，不能写成「用例红」。
9. **金额/吨位一律定点。** mysql 侧 `DECIMAL(p,s)`、sqlite 侧 `TEXT`
   （SQLite 是动态类型，落到 `REAL` 会让 `950 vs 900` 这类边界判定产生二进制误差，
   而本项目把容量/金额判定当**确定性事实**用）。两侧都写定点类型时，精度必须一致。

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

`ci.yml` 里与迁移相关的有**三个** job：

| job | 名字 | 作用 |
| --- | --- | --- |
| `db-migration` | 数据库迁移（SQLite 冒烟 + 幂等） | 全新 SQLite 库上 `--status`（应全部待执行）→ `--verify`（执行 + 校验 + 断言无待执行）→ **再跑一次** `--verify`（证明幂等） |
| `db-migration-mysql` | 数据库迁移（MySQL 8.0 真实执行） | 真实 MySQL 8.0 上执行同一批迁移并验幂等 |
| `pytest-mysql` | 并发集成（MySQL 8.0 真实执行） | `pytest -m mysql`（并发 / 隔离级别相关用例） |

后端 job 的类型检查已扩展为 `mypy app migrations migrate.py`。

MySQL 方言**已在 CI 真实执行**（DR-0002 已落地），所以早先那句"MySQL 只经人工评审、
CI 未真实执行"的说明**已过期**，不再是限制。

### 7.1 仓库内的两道**静态**闸门（都是 pytest 用例 ⇒ 不新增 CI 步骤）

| 文件 | 拦什么 |
| --- | --- |
| `backend/tests/test_migration_sql_literals.py` | 文本层面：列 `COMMENT` 折成相邻字面量；同一条目双方言的**列名**须一致 |
| `backend/tests/test_migration_static_guards.py` | ① InnoDB 单键 ≤ **3072** 字节（utf8mb4 上界）且 TEXT/BLOB 不得直接进索引；② 每个条目的方言分支必须 `mysql` + `sqlite` 成对（或给 `default`）；③ 数值列不得用浮点、不得裸写 `DECIMAL`、两侧定点精度须一致 |

它们存在的理由是本机**结构性失明**：本机没有 MySQL（无 `mysqld` / 无 `docker` / 3306 关闭），
`migrations/*.py` 的 `mysql` 分支**在本机没有任何执行路径** ⇒ 有一类缺陷本机永远绿、CI 才红。

> ⚠️ 它们**只拦"纯文本层面就能确定判错"**的那几类。列类型可移植性、索引与键长上限的**真实**
> 判定、字符集与排序规则，仍然**必须**由上面两个 MySQL job 真跑 —— 静态闸门替代不了 CI。
>
> 造这类闸门时，判据要**从本仓既有约定里读出来**：例：sqlite 侧定点列写 `TEXT` 是本仓约定
> （`金额/吨位一律定点`），不是违规；若把判据写成"两侧都必须长得像数值类型"，闸门第一次跑
> 就会是一台**假阳性机器**（2026-09-17 实况：5 项里红 2 项，全仓 sqlite 定点列被误报）。
> 同时，"仓库全绿"不能证明闸门有用 —— 每个闸门都要配一例**喂入伪造违规必须报红**的自证用例。

---

## 8. 新增一张表的完整步骤

1. 在 `backend/migrations/` 新建 `ent_<表名>.py`，按第 3 节模板写 `id: 1`。
2. 本地 `python migrate.py --status` 确认它被识别为待执行。
3. `python migrate.py` 执行，`--verify` 确认通过。
4. 若同时新增 ORM 模型：表名必须以 `ent_` 开头，且不依赖 `create_all` 建表。
5. 若新模块要对已有表做 `ALTER`：先确认模块名**排在建表模块之后**（§5 第 7 条）。
6. 跑两道静态闸门（或直接跑整套门禁）确认通过：
   `pytest tests/test_migration_static_guards.py tests/test_migration_sql_literals.py`。
7. 迁移文件与业务代码一起提交，PR 模板"数据库迁移"栏填写新增的 `module:id`。
