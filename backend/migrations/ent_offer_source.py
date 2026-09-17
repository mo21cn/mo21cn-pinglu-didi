"""S3 发布前**来源核验台账** —— 让"未核验提案不得被当作已核实依据"可判定。

为什么必须单独有这张表（而不是在发布记录里加个布尔）
----------------------------------------------------
HO 0917-3 的裁定二要求在**发布环节**落实一件事：发布前必须能区分"**已核对**的来源"与
"未核对的来源"。落地时撞上一个硬事实 ——

* 来源只存在于**作业信封**（`ent_agent_job.envelope_json` 的 `source_refs` /
  `unverified_sources`）；
* 而 `ent_artifact` / `ent_artifact_revision` 上**没有任何溯源列**（只有
  `payload_json` / `source` / `note`），成果→作业的关联列**也还没有**
  （DR-0012 未完成项里已登记）。

⇒ 只看成果，**根本判不出**"这份报价的来源核过没有"。所以"在发布命令里加个
`verified` 勾选"必然是假的：勾了也没有可核对的对象。本表把两件事分开存：

1. **声明**（`state='declared'`）：采纳提案时由**服务端**把信封里声明过的来源
   逐条写入 —— 核验对象就是这些 `(kind, ref)`，不由人随便指定；
2. **核验记录**（`state='verified'` / `'rejected'`）：人工核对后追加一行，
   必须带 `method`（核验依据：对照原件 / 上游系统 / 回访…）与 `checked_by` / `checked_at`。

即"核验要留**对象 + 记录**，不是一个勾选"：对象来自声明行，记录来自核验行。

判定口径（`offers.source_gate` 唯一实现）
----------------------------------------
* 该 `(artifact_id, revision_no)` 下的**每一条** `declared` 来源，都必须有一条对应的
  `verified` 行，否则发布被拒（400）并列出**待核验清单**；
* 出现 `rejected` 行 ⇒ 同样拒绝发布（并单独说明"这条来源被判定不可用"）——
  "拒绝"与"还没核"是两句不同的话，不能让它们长得一样；
* 没有声明来源的版本（人工直写/人工修改）⇒ 无待核验项，门槛不拦 —— 它本来就不是
  "模型提案"，不能把人工产出也算成待核验。

为什么 append-only 且 `state` 进唯一键
--------------------------------------
`UNIQUE (artifact_id, revision_no, source_kind, source_ref, state)`：同一条来源同一状态
只留一行（幂等），但 **declared → verified / rejected 是追加新行**，不 UPDATE 旧行 ——
"谁在什么时候核了什么"必须留痕，不能因为后来核过了就抹掉"当初是未核验声明"。

第 2 张表 `ent_artifact_origin`：**数据来源标注**（合同 BP-03 第 9 条 / §11.1 的
"labeled synthetic/manual/live sources"）
--------------------------------------------------------------------------------------
与上面的来源核验**是两件不同的事**，所以不共用一张表：

* `ent_offer_source_check` 回答"**人核过没有**"（门槛用它拦发布）；
* `ent_artifact_origin` 回答"**这份内容是怎么产出的**"（机器事实，客户要看到的标注）。

混在一张表里会让门槛判定被污染：一条 `agent_job` 的产出事实若被写成 `declared`，
就会永远卡在"未核验"上（人没法"核验"一个作业行的 mocked 标志）。

`mode` 的判定**只认事实**：来自作业行的 `mocked` 列（服务端写的），**不采信调用方自述** ——
"让经理自己选 live/合成"就是把标注变成一句口号。无依据时写 `unknown`，**不得猜**。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建来源核验台账 ent_offer_source_check（声明来自作业信封，核验留对象+记录；"
            "发布前门槛按它判定，不靠布尔勾选）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_offer_source_check` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '所属成果（ent_artifact.id）',
  `revision_no` INT NOT NULL COMMENT '所属精确版本（ent_artifact_revision.revision_no）',
  `source_kind` VARCHAR(32) NOT NULL
    COMMENT '来源类别（如 attachment_text / assignment / task）；与信封 source_refs[].kind 同一取值域',
  `source_ref` VARCHAR(160) NOT NULL
    COMMENT '来源编号（**原样 id**，不是文件名/描述串）；与信封 source_refs[].ref 同一取值域',
  `state` VARCHAR(16) NOT NULL
    COMMENT 'declared（声明待核）/ verified（已核验）/ rejected（核验判定不可用）',
  `method` VARCHAR(255) NULL
    COMMENT '核验依据（对照原件/上游系统/回访…）；verified 与 rejected 必填 —— 无依据的核验等于没核',
  `checked_by` BIGINT UNSIGNED NULL COMMENT '核验人（verified/rejected 必填）',
  `checked_at` DATETIME NULL COMMENT '核验时间（verified/rejected 必填）',
  `note` VARCHAR(255) NULL COMMENT '备注',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_offer_source_check` (`artifact_id`, `revision_no`, `source_kind`, `source_ref`, `state`),
  KEY `idx_ent_offer_source_check_rev` (`artifact_id`, `revision_no`, `state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_offer_source_check` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `artifact_id` INTEGER NOT NULL,
  `revision_no` INTEGER NOT NULL,
  `source_kind` TEXT NOT NULL,
  `source_ref` TEXT NOT NULL,
  `state` TEXT NOT NULL,
  `method` TEXT NULL,
  `checked_by` INTEGER NULL,
  `checked_at` TEXT NULL,
  `note` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`artifact_id`, `revision_no`, `source_kind`, `source_ref`, `state`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_offer_source_check_rev`
  ON `ent_offer_source_check` (`artifact_id`, `revision_no`, `state`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_offer_source_check`"],
    },
    {
        "id": 2,
        "description": (
            "创建成果版本的数据来源标注 ent_artifact_origin（live / synthetic / manual，"
            "由服务端按作业行的 mocked 事实推导；不采信调用方自述）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_artifact_origin` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '所属成果（ent_artifact.id）',
  `revision_no` INT NOT NULL COMMENT '所属精确版本（ent_artifact_revision.revision_no）',
  `mode` VARCHAR(16) NOT NULL
    COMMENT 'live（真实模型调用）/ synthetic（桩/夹具）/ manual（人工产出）/ unknown（无事实依据，不猜）',
  `basis_json` TEXT NULL
    COMMENT '判定依据（可复核的事实，如作业行 id 与其 mocked 标志）；**不是**调用方自述',
  `recorded_by` BIGINT NULL COMMENT '记录者（采纳时＝采纳人）',
  `recorded_at` DATETIME NOT NULL,
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_artifact_origin` (`artifact_id`, `revision_no`),
  KEY `idx_ent_artifact_origin_mode` (`mode`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_artifact_origin` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `artifact_id` INTEGER NOT NULL,
  `revision_no` INTEGER NOT NULL,
  `mode` TEXT NOT NULL,
  `basis_json` TEXT NULL,
  `recorded_by` INTEGER NULL,
  `recorded_at` TEXT NOT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`artifact_id`, `revision_no`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_artifact_origin_mode` ON `ent_artifact_origin` (`mode`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_artifact_origin`"],
    },
]
