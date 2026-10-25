"""关务（智慧关务 / 关域）D1 最小骨架 —— 报关记录与状态事件。

对应决策文档《智慧关务-可演示最小化里程碑与Q1-Q7决定-20261025》§2.1 的 **D1**：
人工登记 → 状态时间线。本迁移只建两张表，**不建归类、商品库、查验、订阅**四类表
（属 D2/D3）。

设计纪律（逐条都能在上游文档里找到出处）：

* **弱关联**：只存 `assignment_id`，不建外键、不跨模块级联删除（PRD §7）；
* **事件只追加**：`cus_status_event` 没有 UPDATE 路径 —— 更正＝新增一条
  `corrects_event_id` 指向原事件，原记录保留（PRD AC-06）；
* **来源与获取时间不可空**：`source` / `source_kind` / `fetched_at` 都 NOT NULL。
  这不是"多存几个字段"，而是 PRD AC-09「所有展示状态带来源与获取时间」的落点 ——
  允许为空就等于允许页面出现一条"不知道哪来的状态"；
* **未知保持 NULL**：`org_id` / `assignment_id` / `note` 都可空，不补 0、不填空串；
* 时间存 UTC 朴素时间，格式与既有 `ent_*` 表一致。

`cus_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建报关记录表 cus_declaration（D1 · 人工登记）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `cus_declaration` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `declaration_no` VARCHAR(64) NOT NULL COMMENT '报关单号 / 提运单号 / 舱单号（人工登记原文）',
  `kind` VARCHAR(12) NOT NULL DEFAULT 'export'
    COMMENT '只启用 export（Q-03）；import 未开放，状态机本身与流向无关',
  `status` VARCHAR(24) NOT NULL COMMENT '由事件派生，见 service._derive_status',
  `owner_user_id` BIGINT NOT NULL COMMENT '登记人 / 归属货主',
  `org_id` BIGINT UNSIGNED NULL COMMENT '服务经营主体（可空；空＝个人记录）',
  `assignment_id` BIGINT UNSIGNED NULL COMMENT '弱关联 ent_assignment.id，不建外键',
  `note` VARCHAR(512) NULL COMMENT '备注（未知保持 NULL）',
  `revision` INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '乐观锁版本号（D1 只读派生，保留位）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_cus_declaration_no` (`declaration_no`),
  KEY `idx_cus_declaration_owner` (`owner_user_id`, `status`),
  KEY `idx_cus_declaration_org` (`org_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `cus_declaration` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `declaration_no` TEXT NOT NULL,
  `kind` TEXT NOT NULL DEFAULT 'export',
  `status` TEXT NOT NULL,
  `owner_user_id` INTEGER NOT NULL,
  `org_id` INTEGER NULL,
  `assignment_id` INTEGER NULL,
  `note` TEXT NULL,
  `revision` INTEGER NOT NULL DEFAULT 1,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_cus_declaration_no`
  ON `cus_declaration` (`declaration_no`);
CREATE INDEX IF NOT EXISTS `idx_cus_declaration_owner`
  ON `cus_declaration` (`owner_user_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_cus_declaration_org`
  ON `cus_declaration` (`org_id`, `status`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `cus_declaration`"],
    },
    {
        "id": 2,
        "description": "创建状态事件表 cus_status_event（只追加 · AC-06）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `cus_status_event` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `object_type` VARCHAR(24) NOT NULL DEFAULT 'declaration',
  `object_id` BIGINT UNSIGNED NOT NULL COMMENT '指向 cus_declaration.id（首版只有这一种）',
  `status` VARCHAR(24) NOT NULL
    COMMENT 'unknown/manifest_filed/declared/inspection/released/held/rejected',
  `occurred_at` DATETIME NOT NULL COMMENT '事件发生时间（外部事实的时间）',
  `fetched_at` DATETIME NOT NULL COMMENT '我们拿到这条事实的时间（不可空 · AC-09）',
  `source` VARCHAR(128) NOT NULL COMMENT '来源原文，如「报关行口述」「查验通知单照片」',
  `source_kind` VARCHAR(12) NOT NULL DEFAULT 'manual' COMMENT 'manual / official',
  `actor_user_id` BIGINT NULL COMMENT '登记人（系统回执可为空）',
  `corrects_event_id` BIGINT UNSIGNED NULL COMMENT '更正事件指向被更正的原事件',
  `note` VARCHAR(512) NULL,
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_cus_event_object` (`object_type`, `object_id`, `occurred_at`),
  KEY `idx_cus_event_source` (`source_kind`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `cus_status_event` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `object_type` TEXT NOT NULL DEFAULT 'declaration',
  `object_id` INTEGER NOT NULL,
  `status` TEXT NOT NULL,
  `occurred_at` TEXT NOT NULL,
  `fetched_at` TEXT NOT NULL,
  `source` TEXT NOT NULL,
  `source_kind` TEXT NOT NULL DEFAULT 'manual',
  `actor_user_id` INTEGER NULL,
  `corrects_event_id` INTEGER NULL,
  `note` TEXT NULL,
  `created_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_cus_event_object`
  ON `cus_status_event` (`object_type`, `object_id`, `occurred_at`);
CREATE INDEX IF NOT EXISTS `idx_cus_event_source`
  ON `cus_status_event` (`source_kind`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `cus_status_event`"],
    },
]
