"""幂等操作记录表 —— 委托支线公共机制之二（见 AGENTS.md 3.2）。

用途：写操作（报价发布、任务派发、结算生成等）携带客户端幂等键，服务端按
`(scope, idempotency_key)` 唯一约束保证重复请求只产生一次副作用；
`request_fingerprint` 用于识别"同键不同请求体"的冲突（不是同一操作的重放）。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
"""

from __future__ import annotations

sql = [
    {
        "id": 1,
        "description": "创建幂等操作记录表 ent_idempotency",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_idempotency` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `scope` VARCHAR(64) NOT NULL COMMENT '操作域，如 entrust:quote:publish',
  `idempotency_key` VARCHAR(128) NOT NULL COMMENT '客户端幂等键',
  `actor_user_id` BIGINT NOT NULL COMMENT '操作者',
  `request_fingerprint` VARCHAR(64) NOT NULL COMMENT '请求体指纹，同键不同体视为冲突',
  `status` VARCHAR(16) NOT NULL DEFAULT 'in_progress' COMMENT 'in_progress/succeeded/failed',
  `response_snapshot` TEXT NULL COMMENT '成功响应快照，重放时原样返回',
  `created_at` DATETIME NOT NULL,
  `expires_at` DATETIME NULL COMMENT '过期时间，到点可被清理',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_scope_key` (`scope`, `idempotency_key`),
  KEY `idx_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_idempotency` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `scope` TEXT NOT NULL,
  `idempotency_key` TEXT NOT NULL,
  `actor_user_id` INTEGER NOT NULL,
  `request_fingerprint` TEXT NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'in_progress',
  `response_snapshot` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `expires_at` TEXT NULL,
  UNIQUE (`scope`, `idempotency_key`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_idempotency_expires` ON `ent_idempotency` (`expires_at`)
""",
        },
    },
]

checks = [
    "SELECT COUNT(*) FROM `ent_idempotency`",
]

# ── 执行器兼容格式，请勿修改 ──
migrations = [{**item, "checks": checks} for item in sql]
