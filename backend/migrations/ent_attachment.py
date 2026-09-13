"""附件表与成果-附件关联表 —— S1 第 5 条的数据底座（ENT-009）。

建模红线（计划 §3.2）
--------------------
* **附件二进制存于公共静态路径之外**：`storage_key` 只是服务端内部键，
  文件落在 `ATTACHMENT_STORAGE_DIR`（默认 `backend/var/attachments`，已在
  `.gitignore`），**不经任何静态目录对外暴露**；下载必须走授权端点；
* **20 MiB 上限可配置**（`ATTACHMENT_MAX_BYTES`），类型校验用白名单；
* **文件名安全化**：入库的 `filename` 已剥路径分隔符与控制字符，
  下载响应头再经一次转义 —— 避免 `../` 与头注入；
* **禁止执行上传内容**：本表只存元数据与存储键，没有任何"运行/预览解释器"字段；
* **来源事件时间与上传时间分开保存**（`source_event_at` vs `created_at`）。

附件有两种归属（R1 都要）：
1. **挂在委托授权下**（`entrustment_id` 非空）：参与方按叠加层权限读写；
2. **私有未绑定草稿**（`assignment_id` 非空、`entrustment_id` 为空）：
   只有上传者自己可见，用于"客户还没提交/经理先存一份材料"的场景。

`extract_status` 是**下一步增量（S1 第 6 条：文本/CSV/文本型 PDF 提取）的落点**：
本增量只建字段与状态取值域，不实现提取器 —— 状态默认 `not_requested`，
不假装已经提取过。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建附件表 ent_attachment（元数据 + 存储键 + 提取状态 + 来源事件时间）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_attachment` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `entrustment_id` BIGINT UNSIGNED NULL
    COMMENT '所属委托授权（ent_entrustment.id）；私有未绑定草稿为 NULL',
  `assignment_id` BIGINT UNSIGNED NULL COMMENT '所属委托单（ent_assignment.id，可空）',
  `owner_user_id` BIGINT NOT NULL COMMENT '数据归属货主（授权作用域判定的锚）',
  `org_id` BIGINT UNSIGNED NULL COMMENT '服务经营主体（提交后由委托单带入）',
  `uploader_user_id` BIGINT NOT NULL COMMENT '上传者（组织成员用户）',
  `filename` VARCHAR(255) NOT NULL COMMENT '安全化后的展示文件名（无路径分隔符与控制字符）',
  `content_type` VARCHAR(128) NOT NULL COMMENT 'MIME 类型（白名单校验）',
  `size_bytes` BIGINT UNSIGNED NOT NULL COMMENT '字节数（上传时实测，不信客户端声明）',
  `sha256` CHAR(64) NOT NULL COMMENT '内容哈希（去重与完整性校验）',
  `storage_key` VARCHAR(255) NOT NULL COMMENT '服务端存储键（公共静态路径之外，站内不可直接访问）',
  `extract_status` VARCHAR(24) NOT NULL DEFAULT 'not_requested'
    COMMENT 'not_requested/pending/running/done/failed/unsupported',
  `extract_error` VARCHAR(255) NULL COMMENT '提取失败原因（失败必须可解释）',
  `extracted_chars` INT UNSIGNED NULL COMMENT '已提取字符数（未提取保持 NULL，不填 0）',
  `source_event_at` DATETIME NULL COMMENT '来源事件时间（与上传时间分开保存）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_attachment_entrustment` (`entrustment_id`, `created_at`),
  KEY `idx_ent_attachment_assignment` (`assignment_id`, `created_at`),
  KEY `idx_ent_attachment_uploader` (`uploader_user_id`, `created_at`),
  KEY `idx_ent_attachment_sha256` (`sha256`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_attachment` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `entrustment_id` INTEGER NULL,
  `assignment_id` INTEGER NULL,
  `owner_user_id` INTEGER NOT NULL,
  `org_id` INTEGER NULL,
  `uploader_user_id` INTEGER NOT NULL,
  `filename` TEXT NOT NULL,
  `content_type` TEXT NOT NULL,
  `size_bytes` INTEGER NOT NULL,
  `sha256` TEXT NOT NULL,
  `storage_key` TEXT NOT NULL,
  `extract_status` TEXT NOT NULL DEFAULT 'not_requested',
  `extract_error` TEXT NULL,
  `extracted_chars` INTEGER NULL,
  `source_event_at` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_attachment_entrustment`
  ON `ent_attachment` (`entrustment_id`, `created_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_attachment_assignment`
  ON `ent_attachment` (`assignment_id`, `created_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_attachment_uploader`
  ON `ent_attachment` (`uploader_user_id`, `created_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_attachment_sha256` ON `ent_attachment` (`sha256`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_attachment`"],
    },
    {
        "id": 2,
        "description": "创建成果-附件关联表 ent_artifact_attachment（一个附件可挂到多份成果）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_artifact_attachment` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '成果（ent_artifact.id）',
  `attachment_id` BIGINT UNSIGNED NOT NULL COMMENT '附件（ent_attachment.id）',
  `created_by` BIGINT NOT NULL COMMENT '绑定操作人',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_artifact_attachment` (`artifact_id`, `attachment_id`),
  KEY `idx_ent_artifact_attachment_attachment` (`attachment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_artifact_attachment` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `artifact_id` INTEGER NOT NULL,
  `attachment_id` INTEGER NOT NULL,
  `created_by` INTEGER NOT NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`artifact_id`, `attachment_id`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_artifact_attachment_attachment`
  ON `ent_artifact_attachment` (`attachment_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_artifact_attachment`"],
    },
]
