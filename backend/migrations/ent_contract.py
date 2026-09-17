"""S3 合同派生 —— 让"合同从**已接受事实**派生"成为可核对的事实。

背景（合同 BP-03 第 8 条 / D1-08 / §10.1 第 7 步）
--------------------------------------------------
* BP-03 第 8 条原文：`Generate one structured contract draft/template from the accepted
  facts and approved scope.`
* D1-08 原文：`Contract draft derives from accepted facts; evidence and status remain
  distinct from live e-signature`，判据是 `Contract version and linked evidence
  inspection` —— 注意它要的不是"有一份合同"，而是**能逐项检查它是怎么来的**。
* 包边界原文：`One domestic contract template is sufficient.` ⇒ 模板可以是**常量**，
  不需要模板设计器；真正需要的是**派生关系可查**。

为什么必须有一张"**字段来源表**"，而不是把来源塞进合同正文
----------------------------------------------------------
`S3-范围与依赖评估.md` 把这一条的依赖写得很直白：

    E | 合同生成规则 | 第 8 条要求"从已接受事实派生"，需要一个**可核对的字段来源表**
      |（哪些字段来自哪份已接受版本）

三种"看起来也能用"的做法都不成立：

1. **在合同 payload 里给每个字段挂一个 `source` 键** —— 合同是要发布给客户看的
   （`contract_review` 在 `registry.CUSTOMER_VISIBLE_TYPES` 里），把 `release:12@v3`
   这类内部编号写进正文，等于把审计信息当业务内容发出去；而客户投影**不会**帮你
   剔掉它（投影按字段名裁剪，payload 里多出来的键是"未知字段"，注册表只能报不能删）。
2. **只在派生记录上写一个 `derived_from`** —— 那是"整份合同有一个来源"，不是"**每个
   字段**各有来源"。一份合同里，金额来自客户接受的那一版报价、当事方来自委托单、
   生效日来自接受时间、正文框架来自模板 —— 它们**来源不同**，混成一条就无法核对。
3. **不落库，读的时候现推** —— 现推只能推出"按现在的规则会是什么"；而客户接受的是
   **当时那一版**（裁定三冻结细节②：已接受的旧版本永久保留）。事后重推会在数据被
   编辑后悄悄给出一个**不同**的答案，而记录上仍写着"同一个版本"。

所以第 2 张表是**字段来源表**：逐 (`field_path`, `source_kind`, `source_ref`) 一行。
它同时是"派生"与"编造"的分界线 —— 服务层的不变式是
**"写进合同的每个字段都必须有来源行，没有来源行的字段不得写入"**，
用例对这条做全覆盖断言（少一条来源就红）。

为什么 `release_id` 要 UNIQUE：一份已接受事实只派生一份合同
------------------------------------------------------------
并行/重试是真实存在的（经理点两次、网络重发、两个经理同时点）。
判据必须是 **DB 约束**：`UNIQUE (release_id)`。
先查有没有再插，在并发下两个请求可以同时通过检查 —— 于是同一份客户接受
派生出一式两份合同，而两份都"看起来"合法，后续版本链还各自独立。
（与 `ent_offer_response` 的 `UNIQUE (release_id)` 同一条理由：
裁定三原话"幂等且事务内处理并发"。）

为什么派生记录与合同成果必须**同一个事务**
-------------------------------------------
既有 `artifacts.create_artifact` **内部自己 commit**（它被很多路径复用）。
若在这里复用它，会变成"先提交合同成果、再提交派生记录"两次提交，
中途失败就留下**一份没有派生记录的合同草稿** —— 而"这份合同从哪来"正是本切片的
全部价值。⇒ `contracts.derive_contract` 自己在**一个事务**里写四张表
（`ent_artifact` / `ent_artifact_revision` / `ent_contract_derivation` /
`ent_contract_field_source`），最后一次性提交。
注册表的字段契约校验不因此丢掉（照旧调 `registry.validate_payload`）。

不动既有表、不加 FK（沿用 `ent_` 支线"应用层保证引用完整性"的既有约定，
见 `ent_extract.py` / `ent_commitment.py` 的同一论据）。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建合同派生记录表 ent_contract_derivation（绑定**已接受的那一条发布**+"
            "精确报价版本+产出的合同版本；UNIQUE(release_id) 让'一份已接受事实只派生"
            "一份合同'落在数据库约束上，而不是先查后写）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_contract_derivation` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单（ent_assignment.id）',
  `entrustment_id` BIGINT UNSIGNED NULL COMMENT '派生时所在的委托授权（审计用）',
  `release_id` BIGINT UNSIGNED NOT NULL
    COMMENT '**已接受**的那一条发布（ent_offer_release.id）；唯一 —— 一份已接受事实只派生一份合同',
  `response_id` BIGINT UNSIGNED NOT NULL
    COMMENT '客户的接受响应（ent_offer_response.id，decision=accept）；无它就不是"已接受事实"',
  `quote_artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '被接受的报价成果（ent_artifact.id）',
  `quote_revision_id` BIGINT UNSIGNED NOT NULL COMMENT '被接受的**精确**报价版本（ent_artifact_revision.id）',
  `quote_revision_no` INT NOT NULL COMMENT '冗余的版本号，便于单看本行自证"派生自哪一版"',
  `contract_artifact_id` BIGINT UNSIGNED NOT NULL
    COMMENT '派生出的合同成果（ent_artifact.id，artifact_type=contract_review）',
  `contract_revision_id` BIGINT UNSIGNED NOT NULL COMMENT '合同首个版本（ent_artifact_revision.id）',
  `contract_revision_no` INT NOT NULL COMMENT '合同的版本号（派生时为 1）',
  `template_code` VARCHAR(64) NOT NULL COMMENT '合同模板标识（BP-03 包边界：一份国内运输模板即可）',
  `template_version` VARCHAR(32) NOT NULL COMMENT '模板版本；模板改了要能分辨"当时用的是哪一版"',
  `effective_date` VARCHAR(32) NULL COMMENT '合同生效日（取自客户接受时间，见字段来源表）',
  `derived_by` BIGINT UNSIGNED NOT NULL COMMENT '派生操作人（经理人）',
  `derived_at` DATETIME NOT NULL COMMENT '派生时间（业务时间）',
  `note` VARCHAR(500) NULL COMMENT '派生说明（留痕）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_contract_derivation_release` (`release_id`),
  KEY `idx_ent_contract_derivation_assignment` (`assignment_id`),
  KEY `idx_ent_contract_derivation_contract` (`contract_artifact_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_contract_derivation` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `entrustment_id` INTEGER NULL,
  `release_id` INTEGER NOT NULL,
  `response_id` INTEGER NOT NULL,
  `quote_artifact_id` INTEGER NOT NULL,
  `quote_revision_id` INTEGER NOT NULL,
  `quote_revision_no` INTEGER NOT NULL,
  `contract_artifact_id` INTEGER NOT NULL,
  `contract_revision_id` INTEGER NOT NULL,
  `contract_revision_no` INTEGER NOT NULL,
  `template_code` TEXT NOT NULL,
  `template_version` TEXT NOT NULL,
  `effective_date` TEXT NULL,
  `derived_by` INTEGER NOT NULL,
  `derived_at` TEXT NOT NULL,
  `note` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`release_id`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_contract_derivation_assignment`
  ON `ent_contract_derivation` (`assignment_id`);
CREATE INDEX IF NOT EXISTS `idx_ent_contract_derivation_contract`
  ON `ent_contract_derivation` (`contract_artifact_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_contract_derivation`"],
    },
    {
        "id": 2,
        "description": (
            "创建合同字段来源表 ent_contract_field_source（逐字段可核对的来源 —— "
            "D1-08 要的是'能检查它怎么来的'，不是'有一份合同'）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_contract_field_source` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `derivation_id` BIGINT UNSIGNED NOT NULL COMMENT '所属派生记录（ent_contract_derivation.id）',
  `field_path` VARCHAR(160) NOT NULL
    COMMENT '合同里的字段路径，如 parties[1].name / clauses[0].text / effective_date；逐字段可查',
  `value_text` TEXT NOT NULL
    COMMENT '该字段**实际写入合同**的值（文本形式）；与合同内容对不上就说明派生不可信',
  `source_kind` VARCHAR(32) NOT NULL
    COMMENT '来源类别：accepted_release（已接受发布）/ customer_response / assignment / leg / organization / template',
  `source_ref` VARCHAR(160) NOT NULL
    COMMENT '来源标识（内部编号，如 release:12@v3；**只在本表出现，不进合同正文**）',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_contract_field_source` (`derivation_id`, `field_path`, `source_kind`, `source_ref`),
  KEY `idx_ent_contract_field_source_derivation` (`derivation_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_contract_field_source` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `derivation_id` INTEGER NOT NULL,
  `field_path` TEXT NOT NULL,
  `value_text` TEXT NOT NULL,
  `source_kind` TEXT NOT NULL,
  `source_ref` TEXT NOT NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`derivation_id`, `field_path`, `source_kind`, `source_ref`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_contract_field_source_derivation`
  ON `ent_contract_field_source` (`derivation_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_contract_field_source`"],
    },
]
