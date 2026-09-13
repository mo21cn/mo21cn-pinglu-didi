"""成果版本表 —— 公共机制「成果有版本」的数据底座（ENT-004）。

对应开发规范 3.4 第一条：**成果有版本** —— 编辑产生新 revision，确认绑定精确版本。

表：
1. `ent_artifact`：一份成果（如：报价解析稿 / 对客报价 / 合同核对稿）。
   `current_revision_id` 指向**精确的版本**（确认动作写入，永不模糊指向"最新"）；
2. `ent_artifact_revision`：成果的版本历史，**append-only** —— 编辑永远追加新行，
   任何流程不得 UPDATE / DELETE 历史 revision。

`current_revision_id` 故意**不加外键**：它与 `ent_artifact_revision.id` 的绑定由
服务层（`app/modules/entrust/artifacts.py`）保证，避免 MySQL 与 SQLite 在外键行为
（立即性 / 级联）上的方言差异影响幂等的版本指针更新。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建成果表 ent_artifact",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_artifact` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `entrustment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托授权（ent_entrustment.id）',
  `artifact_type` VARCHAR(48) NOT NULL COMMENT '成果类型：quote_parsed/customer_quote/contract_review/...',
  `current_revision_id` BIGINT UNSIGNED NULL COMMENT '当前生效的精确版本（confirm 写入）',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active/void',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_artifact_entrust` (`entrustment_id`, `artifact_type`),
  KEY `idx_ent_artifact_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_artifact` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `entrustment_id` INTEGER NOT NULL,
  `artifact_type` TEXT NOT NULL,
  `current_revision_id` INTEGER NULL,
  `status` TEXT NOT NULL DEFAULT 'active',
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_artifact_entrust`
  ON `ent_artifact` (`entrustment_id`, `artifact_type`);
CREATE INDEX IF NOT EXISTS `idx_ent_artifact_status` ON `ent_artifact` (`status`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_artifact`"],
    },
    {
        "id": 2,
        "description": "创建成果版本表 ent_artifact_revision（append-only）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_artifact_revision` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `artifact_id` BIGINT UNSIGNED NOT NULL,
  `revision_no` INT UNSIGNED NOT NULL COMMENT '成果内递增版本号，从 1 开始',
  `payload_json` LONGTEXT NOT NULL COMMENT '成果内容（JSON 文本）',
  `source` VARCHAR(16) NOT NULL COMMENT 'agent=模型产出 / manual=人工产出或修改',
  `note` VARCHAR(255) NULL COMMENT '版本说明（编辑原因等）',
  `created_by` BIGINT NOT NULL COMMENT '产出者（用户或代理主体）',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_artifact_revision` (`artifact_id`, `revision_no`),
  KEY `idx_ent_artifact_rev_artifact` (`artifact_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_artifact_revision` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `artifact_id` INTEGER NOT NULL,
  `revision_no` INTEGER NOT NULL,
  `payload_json` TEXT NOT NULL,
  `source` TEXT NOT NULL,
  `note` TEXT NULL,
  `created_by` INTEGER NOT NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`artifact_id`, `revision_no`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_artifact_rev_artifact`
  ON `ent_artifact_revision` (`artifact_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_artifact_revision`"],
    },
]
