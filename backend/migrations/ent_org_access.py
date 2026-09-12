"""组织成员与委托授权表 —— 委托支线权限叠加层的数据底座。

委托支线的身份模型比既有 `User.current_role` 多两层：

1. **组织成员**（`ent_org_member`）：经理人属于某个组织，组织内角色决定他能做什么；
2. **委托授权**（`ent_entrustment`）：货主把自己在某个委托上的操作权，授予该组织。

既有 `current_role` 的语义**不变**（AC-01），本套表只做**叠加**：
`resolve_permissions()` 返回的是"组织成员角色 + 委托授权"带来的权限集合，
既有 router 里的 `_require_role(user.current_role)` 判断一律不碰。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。

注意：`checks` 是**按条目**执行的（每执行完一条就跑它的 checks），
所以每条只能校验自己创建的表 —— 否则第一条执行完就去查还没建的表，必然失败。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建组织表 ent_organization",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_organization` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(128) NOT NULL COMMENT '组织名称',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active/suspended',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_org_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_organization` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `name` TEXT NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'active',
  `created_at` TEXT NOT NULL
)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_organization`"],
    },
    {
        "id": 2,
        "description": "创建组织成员表 ent_org_member",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_org_member` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `org_id` BIGINT UNSIGNED NOT NULL,
  `user_id` BIGINT NOT NULL COMMENT '成员用户',
  `member_role` VARCHAR(32) NOT NULL COMMENT 'owner/admin/manager/member',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active/removed',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_org_member` (`org_id`, `user_id`),
  KEY `idx_ent_org_member_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_org_member` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `org_id` INTEGER NOT NULL,
  `user_id` INTEGER NOT NULL,
  `member_role` TEXT NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'active',
  `created_at` TEXT NOT NULL,
  UNIQUE (`org_id`, `user_id`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_org_member_user` ON `ent_org_member` (`user_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_org_member`"],
    },
    {
        "id": 3,
        "description": "创建委托授权表 ent_entrustment",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_entrustment` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `org_id` BIGINT UNSIGNED NOT NULL,
  `entrust_user_id` BIGINT NOT NULL COMMENT '委托方（货主）',
  `permissions` TEXT NOT NULL COMMENT '授权动作代码数组（JSON 文本）',
  `status` VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT 'draft/active/revoked/expired',
  `valid_from` DATETIME NULL,
  `valid_until` DATETIME NULL,
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_entrustment_org` (`org_id`, `status`),
  KEY `idx_ent_entrustment_user` (`entrust_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_entrustment` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `org_id` INTEGER NOT NULL,
  `entrust_user_id` INTEGER NOT NULL,
  `permissions` TEXT NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'draft',
  `valid_from` TEXT NULL,
  `valid_until` TEXT NULL,
  `created_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_entrustment_org` ON `ent_entrustment` (`org_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_entrustment_user` ON `ent_entrustment` (`entrust_user_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_entrustment`"],
    },
]
