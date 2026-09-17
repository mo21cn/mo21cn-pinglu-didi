"""S3 发布与客户响应 + 最小航段 / 候选运力 —— **数据底座**（HO 0917-3 裁定三）。

本文件只建**结构**，不实现任何命令、端点或界面。裁定三明确："S3 的数据设计和迁移
准备可提前开展，不必等待设备走查结果" —— 但**界面与端点属后续切片**。

四张表与它们各自要挡住的那件事
------------------------------

1. `ent_leg`（最小结构化航段）
   裁定三：三段运输**需要最小结构化对象**，不能只写在任务说明里 —— 至少有稳定 ID、
   所属委托、运输方式、起终点和顺序，任务与候选运力引用对应航段。
   ⛔ 不建通用路线图、不做路径优化引擎（裁定三明确）。

2. `ent_capacity_candidate`（候选运力事实载体）
   合同 BP-03 第 3 条 + BP-04 第 6 条：**选中报价 ≠ 确认运力**，且"900 吨候选在
   变更后不适用"必须是**确定性容量检查**，不是 LLM 意见（"an LLM opinion is not
   the rule"）。所以可用吨位、承运船数、是否允许拆批都是**数据字段** ——
   只写一个"900"在文档里，规则无处可算。

3. `ent_offer_release`（对客发布记录）
   合同 BP-03 第 5 条要"release a specific immutable offer revision"；裁定三把它
   定为**独立记录**，绑定**精确成果 revision**，并保存**当时的客户白名单快照**。
   * 为什么必须存快照：客户看到的内容必须在**发布那一刻**就被固定。若发布后按
     "现在的投影规则"重算，改一次内部字段就可能悄悄改变客户已看到的内容 ——
     而客户是照着**当时**那份做决定的。
   * `status` 三态（released / withdrawn / superseded）：裁定三冻结两条语义 ——
     **新建内部草稿不等于自动撤销旧报价**（只有显式重新发布或撤回才改变旧报价的
     可响应状态）；**已接受的旧版本永久保留**（后续变更要新的客户确认，
     不能覆盖此前的接受事实）。因此"被取代"是**显式写下来的事实**，
     不是从"有更新的记录"推出来的。
   * `authorized_attachment_ids`：裁定三要求**客户下载必须限定到该发布记录明确
     授权的文件** —— 不能因为客户属于同一委托授权，就把内部供应商报价和其他附件
     全部开放。授权清单随发布一起冻结。

4. `ent_offer_response`（客户响应）
   绑定发布记录，记录接受／拒绝、**客户身份**、时间；裁定三要求**幂等且事务内
   处理并发**。"同一发布只能被响应一次"是**唯一约束**保证的，不是应用层
   "先查后写"——后者在并发下必然漏。
   `responded_revision_id` 与发布记录的 `revision_id` 冗余一列是有意的：
   合同 D1-07 要证明"客户接受的是**那个精确版本**"，冗余后单看响应行即可自证，
   不必回头 join（也就不会因为 join 写错而看起来像"版本对得上"）。

客户身份口径（裁定三）
----------------------
**客户 = 当前委托的货主本人**，复用现有账号与 `owner_user_id`；**不新增全局
customer 角色**。所以这里存的是 `customer_user_id`，且**按具体委托归属判权**，
不只看全局角色 —— 组织经理权限本身**不能**代替客户确认权限（裁定三原话）。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建航段表 ent_leg（稳定 ID + 所属委托 + 运输方式 + 起终点 + 顺序；"
            "只做最小结构化对象，不做路线图/优化引擎）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_leg` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单（ent_assignment.id）',
  `seq` INT NOT NULL COMMENT '航段顺序，自 1 起、委托内唯一（判据要能按顺序读）',
  `mode` VARCHAR(16) NOT NULL COMMENT 'road（公路）/ water（内河）/ rail（铁路）',
  `from_name` VARCHAR(64) NOT NULL COMMENT '起点名称（任务与候选运力按航段引用，不按名字匹配）',
  `to_name` VARCHAR(64) NOT NULL COMMENT '终点名称',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_leg_assignment_seq` (`assignment_id`, `seq`),
  KEY `idx_ent_leg_mode` (`mode`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_leg` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `seq` INTEGER NOT NULL,
  `mode` TEXT NOT NULL,
  `from_name` TEXT NOT NULL,
  `to_name` TEXT NOT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`assignment_id`, `seq`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_leg_mode` ON `ent_leg` (`mode`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_leg`"],
    },
    {
        "id": 2,
        "description": (
            "创建候选运力表 ent_capacity_candidate（可用吨位/承运船数/是否拆批是**数据**，"
            "容量适用性才能是确定性规则）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_capacity_candidate` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单',
  `leg_id` BIGINT UNSIGNED NULL COMMENT '对应航段（ent_leg.id）；空=未绑定到具体航段',
  `carrier` VARCHAR(64) NOT NULL COMMENT '承运人（供应商侧口径）',
  `vessel_name` VARCHAR(64) NULL COMMENT '船名（单船承运时它是判据的一部分）',
  `capacity_tonnes` DECIMAL(12,3) NOT NULL
    COMMENT '可用舱位吨位；D1-09「900 吨候选在变更后不适用」就靠它算，金额/吨位一律定点',
  `vessel_count` INT NOT NULL DEFAULT 1 COMMENT '本次承运需要的船数（>1 即多船）',
  `allows_partial_load` TINYINT NOT NULL DEFAULT 0
    COMMENT '是否允许拆批；0 + vessel_count=1 时「950 > 900」才真的等于装不下',
  `rate` DECIMAL(12,4) NULL COMMENT '单价（供应商侧）',
  `rate_unit` VARCHAR(16) NULL COMMENT '计价单位（吨/柜…）；只有金额无法确定费用',
  `currency` VARCHAR(8) NULL COMMENT '币种（CNY/USD…）',
  `valid_until` DATE NULL COMMENT '报价有效期；过期末重新确认的报价不支撑新的确认采购',
  `evidence_kind` VARCHAR(32) NULL COMMENT '证据类别（报价单/邮件/照片…）；合同要求"有证据支撑"',
  `status` VARCHAR(16) NOT NULL DEFAULT 'candidate'
    COMMENT 'candidate（候选）/ selected（已选中）/ confirmed（已确认运力）/ withdrawn',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_capacity_assignment` (`assignment_id`, `status`),
  KEY `idx_ent_capacity_leg` (`leg_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_capacity_candidate` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `leg_id` INTEGER NULL,
  `carrier` TEXT NOT NULL,
  `vessel_name` TEXT NULL,
  `capacity_tonnes` TEXT NOT NULL,
  `vessel_count` INTEGER NOT NULL DEFAULT 1,
  `allows_partial_load` INTEGER NOT NULL DEFAULT 0,
  `rate` TEXT NULL,
  `rate_unit` TEXT NULL,
  `currency` TEXT NULL,
  `valid_until` TEXT NULL,
  `evidence_kind` TEXT NULL,
  `status` TEXT NOT NULL DEFAULT 'candidate',
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS `idx_ent_capacity_assignment`
  ON `ent_capacity_candidate` (`assignment_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_capacity_leg`
  ON `ent_capacity_candidate` (`leg_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_capacity_candidate`"],
    },
    {
        "id": 3,
        "description": (
            "创建对客发布记录表 ent_offer_release（绑定精确成果 revision + 客户白名单快照 + "
            "授权附件清单；显式撤回/取代，不从「有更新的记录」推断）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_offer_release` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单',
  `entrustment_id` BIGINT UNSIGNED NULL COMMENT '发布时所在的委托授权（审计用）',
  `artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '被发布的成果（ent_artifact.id）',
  `revision_id` BIGINT UNSIGNED NOT NULL
    COMMENT '**精确**版本（ent_artifact_revision.id）；D1-07 要证明客户接受的是"那一个版本"',
  `revision_no` INT NOT NULL COMMENT '冗余的版本号，便于单看本行自证精确版本',
  `customer_user_id` BIGINT UNSIGNED NOT NULL
    COMMENT '客户 = 当前委托的货主本人（复用现有账号，不新增全局 customer 角色）',
  `snapshot_json` LONGTEXT NOT NULL
    COMMENT '发布那一刻的**客户白名单投影快照**；发布后按"现在的规则"重算会悄悄改变客户已看到的内容',
  `authorized_attachment_ids` TEXT NULL
    COMMENT '本发布明确授权的附件 id（JSON 数组）；客户下载**只**限这些，不能因同属一条委托授权就全开',
  `status` VARCHAR(16) NOT NULL DEFAULT 'released'
    COMMENT 'released（可响应）/ withdrawn（已撤回）/ superseded（已被显式重新发布取代）',
  `released_by` BIGINT UNSIGNED NOT NULL COMMENT '发布人（经理人）',
  `released_at` DATETIME NOT NULL,
  `closed_by` BIGINT UNSIGNED NULL COMMENT '撤回/取代的操作人',
  `closed_at` DATETIME NULL COMMENT '撤回/取代时间；status 非 released 时必须非空',
  `close_reason` VARCHAR(255) NULL COMMENT '撤回/取代理由（留痕，不靠猜）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_offer_release_assignment` (`assignment_id`, `status`),
  KEY `idx_ent_offer_release_artifact` (`artifact_id`, `revision_no`),
  KEY `idx_ent_offer_release_customer` (`customer_user_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_offer_release` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `entrustment_id` INTEGER NULL,
  `artifact_id` INTEGER NOT NULL,
  `revision_id` INTEGER NOT NULL,
  `revision_no` INTEGER NOT NULL,
  `customer_user_id` INTEGER NOT NULL,
  `snapshot_json` TEXT NOT NULL,
  `authorized_attachment_ids` TEXT NULL,
  `status` TEXT NOT NULL DEFAULT 'released',
  `released_by` INTEGER NOT NULL,
  `released_at` TEXT NOT NULL,
  `closed_by` INTEGER NULL,
  `closed_at` TEXT NULL,
  `close_reason` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS `idx_ent_offer_release_assignment`
  ON `ent_offer_release` (`assignment_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_offer_release_artifact`
  ON `ent_offer_release` (`artifact_id`, `revision_no`);
CREATE INDEX IF NOT EXISTS `idx_ent_offer_release_customer`
  ON `ent_offer_release` (`customer_user_id`, `status`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_offer_release`"],
    },
    {
        "id": 4,
        "description": (
            "创建客户响应表 ent_offer_response（唯一约束保证同一次发布只被响应一次 —— "
            "并发下的判据必须是约束，不是先查后写）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_offer_response` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `release_id` BIGINT UNSIGNED NOT NULL COMMENT '所响应的发布记录（ent_offer_release.id）',
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '冗余：便于按委托直接列出响应史',
  `artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '冗余：被响应的成果',
  `responded_revision_id` BIGINT UNSIGNED NOT NULL
    COMMENT '冗余：客户实际接受/拒绝的精确版本；冗余后单看响应行即可自证 D1-07',
  `decision` VARCHAR(16) NOT NULL COMMENT 'accept（接受）/ reject（拒绝）',
  `customer_user_id` BIGINT UNSIGNED NOT NULL
    COMMENT '响应人（须为该委托的货主本人）；组织经理不得代替客户确认',
  `note` VARCHAR(500) NULL COMMENT '客户备注（拒绝理由等）',
  `responded_at` DATETIME NOT NULL COMMENT '客户作出响应的时间（业务时间）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_offer_response_release` (`release_id`),
  KEY `idx_ent_offer_response_assignment` (`assignment_id`, `decision`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_offer_response` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `release_id` INTEGER NOT NULL,
  `assignment_id` INTEGER NOT NULL,
  `artifact_id` INTEGER NOT NULL,
  `responded_revision_id` INTEGER NOT NULL,
  `decision` TEXT NOT NULL,
  `customer_user_id` INTEGER NOT NULL,
  `note` TEXT NULL,
  `responded_at` TEXT NOT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`release_id`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_offer_response_assignment`
  ON `ent_offer_response` (`assignment_id`, `decision`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_offer_response`"],
    },
]
