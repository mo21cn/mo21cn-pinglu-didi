"""委托单表 —— S1 受理链路的主干聚合（ENT-005）。

对应计划 §3.2 的 **Assignment**（总委托单）：客户提交委托 → 经理认领的最小承载。
本增量只建模受理阶段需要的字段；方案/履约/结算阶段所需的事实（运输段、时间窗等）
随后续增量追加新列或新表，**不修改本条迁移**。

状态机（受理链路）::

    draft ──submit──> submitted ──claim──> claimed
      │                   │
      └──────cancel───────┴──────────────> cancelled

并发约束：
* `revision` 是可变聚合的乐观锁版本号（AC-11）：命令必须带 `expected_revision`，
  不匹配即 409；
* 认领是**原子操作**（AC-03）：服务层用单条
  `UPDATE ... SET status='claimed' WHERE id=:id AND status='submitted'` 完成，
  以受影响行数判定成败 —— 两个经理同时认领，只有一个能成功。

字段纪律（开发规范 3.4 / PRD 5.1）：
* 数量带单位：`quantity` 与 `quantity_unit` 成对；未知就是 NULL，不静默补 0；
* 时间一律 UTC 朴素时间，格式与既有 `ent_` 表一致。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建委托单表 ent_assignment（受理链路主干）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_assignment` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `owner_user_id` BIGINT NOT NULL COMMENT '货主（创建人）',
  `org_id` BIGINT UNSIGNED NULL COMMENT '服务经营主体（提交时选定，ent_organization.id）',
  `title` VARCHAR(128) NOT NULL COMMENT '委托标题',
  `cargo_summary` VARCHAR(512) NULL COMMENT '货物概述（未知保持 NULL）',
  `quantity` DECIMAL(14,3) NULL COMMENT '数量（未知保持 NULL，绝不静默为 0）',
  `quantity_unit` VARCHAR(24) NULL COMMENT '数量单位（吨/件/箱…，与 quantity 成对）',
  `status` VARCHAR(16) NOT NULL DEFAULT 'draft'
    COMMENT 'draft/submitted/claimed/cancelled',
  `revision` INT UNSIGNED NOT NULL DEFAULT 1
    COMMENT '乐观锁版本号：命令带 expected_revision，不匹配即冲突',
  `claimed_by` BIGINT NULL COMMENT '认领经理（组织成员用户）',
  `claimed_at` DATETIME NULL,
  `submitted_at` DATETIME NULL,
  `cancelled_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_assignment_owner` (`owner_user_id`, `status`),
  KEY `idx_ent_assignment_org` (`org_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_assignment` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `owner_user_id` INTEGER NOT NULL,
  `org_id` INTEGER NULL,
  `title` TEXT NOT NULL,
  `cargo_summary` TEXT NULL,
  `quantity` NUMERIC(14,3) NULL,
  `quantity_unit` TEXT NULL,
  `status` TEXT NOT NULL DEFAULT 'draft',
  `revision` INTEGER NOT NULL DEFAULT 1,
  `claimed_by` INTEGER NULL,
  `claimed_at` TEXT NULL,
  `submitted_at` TEXT NULL,
  `cancelled_at` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_assignment_owner`
  ON `ent_assignment` (`owner_user_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_assignment_org`
  ON `ent_assignment` (`org_id`, `status`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_assignment`"],
    },
]
