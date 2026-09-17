"""委托单的**结案结构**追加迁移（S4-a）—— 只落结构与迁移，不开放任何结案能力。

口径来源：`docs/entrust/S4-委托结案状态机口径设计.md`（HO 2026-09-17「0917 决策」Q1–Q4
已裁定 + 「0917-2」补充裁定）。该文件定义了本切片能做什么、**不能**做什么。

## 为什么模块名以 `ent_assignment_` 开头

迁移执行器按**模块名的字典序**应用（`migrate.py:load_entries` 用
`sorted(pkgutil.iter_modules(...), key=lambda m: m.name)`），**没有声明式依赖**。
本模块对 `ent_assignment` 表做 `ALTER`，而该表由 `ent_assignment.py` 创建
⇒ 模块名必须排在 `ent_assignment` **之后**、`ent_attachment` **之前**
（`"ent_assignment" < "ent_assignment_completion" < "ent_attachment"`）。
名字乱取会让迁移在**空库上**先于建表执行，症状是 `no such table: ent_assignment`。

## 本切片**刻意不做**的事（不是待办，是 HO 指定的边界）

* ⛔ **不开放完成命令 / 完成按钮**：没有 `complete` 端点、没有五类前置检查
  ⇒ 正确表现是"这个能力还没有入口"，不是"给个按钮点了报 409"；
* ⛔ **不改 `status` 的代码取值域**：`assignments.STATUS_*` 仍是四个
  （`draft/submitted/claimed/cancelled`）、`ACTIVE_STATUSES` 与 `_CANCELLABLE`
  **一字未改**（Q1 裁定：DEMO-1 不新增通用取消）。列注释写五个取值是**列的事实**，
  不代表代码能产出第五个 —— 两者分别有测试盯着（`test_entrust_s4a_schema_only.py`）；
* ⛔ **不向前端暴露 `financial_status`**：按 §5.3.2 末条，派生逻辑接通前不得展示该字段，
  否则 `not_started` 会被当成"这些委托财务未开始"这一**结论**。
  本迁移给的默认值**只是列默认值**；
* ⛔ **不推测历史**：既有 `claimed` 行保持 `claimed`、`completed_at` 保持 NULL，
  没有任何 `UPDATE`（§5.6：旧数据不推测完成）。

## 各条目的 checks 是什么性质

本执行器的 `checks` 只是**逐条执行**（`apply_pending` 里 `conn.exec_driver_sql(check)`），
它断言的是"语句能跑通"⇒ 引用新列的查询跑通即证明**列已存在**。
它**不**断言取值分布（那属于业务用例），本条说明是为了避免把 checks 读成"数据正确性证明"。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "ent_assignment 追加结案结构（completed_at / financial_status）",
        "sql": {
            "mysql": """
ALTER TABLE `ent_assignment`
  ADD COLUMN `completed_at` DATETIME NULL COMMENT '运营完成时间（S4：只由结案命令写入，本迁移不推测历史）',
  ADD COLUMN `financial_status` VARCHAR(16) NOT NULL DEFAULT 'not_started' COMMENT '财务结案：not_started/open/settled；由费用/结算/收付/处置事实派生，不接受直接 PATCH'
""",
            "sqlite": """
ALTER TABLE `ent_assignment` ADD COLUMN `completed_at` TEXT NULL;
ALTER TABLE `ent_assignment` ADD COLUMN `financial_status` TEXT NOT NULL DEFAULT 'not_started'
""",
        },
        "checks": [
            # 引用两个新列 ⇒ 列不存在时本查询直接报错（迁移失败即回滚）。
            "SELECT COUNT(*) FROM `ent_assignment` "
            "WHERE `financial_status` = 'not_started' AND `completed_at` IS NULL",
        ],
    },
    {
        "id": 2,
        "description": "ent_assignment.status 列注释同步为五个取值（含 completed）",
        "sql": {
            "mysql": """
ALTER TABLE `ent_assignment`
  MODIFY COLUMN `status` VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT 'draft/submitted/claimed/completed/cancelled'
""",
            # SQLite 没有列注释这个概念（列定义存在 sqlite_master 的 DDL 文本里，无法 ALTER），
            # 所以本条在 SQLite 上是**显式空操作**：不重建表、不搬数据、不改任何行。
            # 用空操作而不是省略 sqlite 键，是为了让"双方言成对演进"这条约定在两条分支上都可见
            # （省略方言键会让 `resolve_sql` 在执行时抛错，那是在运行期才发现，不是静态可查）。
            "sqlite": """
SELECT 1
""",
        },
        "checks": [
            "SELECT COUNT(*) FROM `ent_assignment` "
            "WHERE `status` IN ('draft', 'submitted', 'claimed', 'completed', 'cancelled')",
        ],
    },
    {
        "id": 3,
        "description": "历史委托列表所需索引 (status, completed_at)",
        "sql": {
            # MySQL 的 CREATE INDEX 不支持 IF NOT EXISTS；幂等性由执行器的
            # `(module, migration_id)` 历史表保证（与 ent_artifact_assignment.py 同一写法）。
            "mysql": """
CREATE INDEX `idx_ent_assignment_status_completed`
  ON `ent_assignment` (`status`, `completed_at`)
""",
            "sqlite": """
CREATE INDEX IF NOT EXISTS `idx_ent_assignment_status_completed`
  ON `ent_assignment` (`status`, `completed_at`)
""",
        },
        "checks": [
            "SELECT COUNT(*) FROM `ent_assignment` "
            "WHERE `status` = 'claimed' AND `completed_at` IS NULL",
        ],
    },
]
