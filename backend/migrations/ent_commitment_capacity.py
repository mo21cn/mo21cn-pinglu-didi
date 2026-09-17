"""运力确认与有效期 —— BP-03 第 3 条 / D1-06 的**确定性判据**数据底座。

合同原文（逐字，`DEMO-1-contract-v1.0.md` §4 BP-03）
---------------------------------------------------
* 第 2 条：`Compare two supplier quotations, with capacity, price scope, quantity
  unit, validity, and evidence visible.`
* 第 3 条：`Record selection separately from resource confirmation. Confirmation
  requires an authorized action and identified evidence; expired or unsuitable
  resources cannot be confirmed without legitimate renewal/change.`
* Exit evidence：`A chosen quotation alone does not create confirmed capacity.`
* §10.2 负例：`unsuitable/expired resource confirmation;`
* D1-06：`Two quotes can be compared and a valid resource confirmed only with proper
  evidence`，判据 `Validity/capacity/evidence positive and negative cases`。

为什么"确认"必须是**它自己的记录**，不能只是候选行上的一个状态值
--------------------------------------------------------------
`ent_capacity_candidate.status` 的取值域里本来就有 `confirmed`。但它**承载不了**第 3 条：

| 第 3 条要求 | 候选行的一个状态值为什么不够 |
| --- | --- |
| `an authorized action` | 状态值没有 actor 与时间 —— 谁确认的、什么时候，全都没有 |
| `identified evidence` | 状态值没有证据引用（"有证据"与"证据是这一份"是两件事） |
| 判据要能**复算** | 候选行的吨位/有效期/证据**可以被改**；改完再读，确认还在，判据已经不成立了，而记录上看不出来 |

⇒ 确认落成**独立的、只增不改的记录**，并把**判定读到的那几个值整体冻结**在行上。
这不是冗余：冻结的是"当时拿这些输入判的"，而不是"候选行现在长什么样"。
（同一条论据见 `ent_offer_release.snapshot_json`、`ent_contract_field_source`。）

为什么规则判定要**逐条落行**，而不是在确认行上塞一个 JSON
--------------------------------------------------------
判定结果可以由（冻结输入 + 规则集版本 + 代码）**重算**，所以本表存的不是"结论"，
而是**哪几条规则被评估过、各自比较了什么**。规则集会演进（S4 要把同一套容量规则用到
变更影响上），届时旧确认必须还能说清"它当年是按哪几条规则判的" ——
塞一个 JSON 会把这件事变成"读代码才知道当时有几条规则"。

两条刻意的**不对称**（与发布/响应那组同一种形态）
------------------------------------------------
1. **本表只记通过的判定**。判定失败**不落库** —— 拒绝的证据是 409 响应体里的逐条原因
   （与落库行**同一种形状**，客户端一套渲染），不是一条历史行。
   ⇒ 库里 `ent_capacity_rule_check` 的每一行都意味着"这条规则通过了"，
   **因此没有 `outcome` 列**：有行就是通过，写一个恒为 `pass` 的列是造一个假字段。
   ⚠️ 但**响应模型里有 `outcome`** —— 那是为了让失败与成功在客户端形状一致。
2. **`UNIQUE (candidate_id)`**：一条候选运力只能被确认一次。要改结论或改资源，
   登记**新的候选行**（它的吨位/有效期/证据都是新的）再确认 —— 与 `ent_` 支线
   "不改历史、只追加"的既有约定一致。并发下"同一候选被确认两次"由这个约束挡住，
   而不是应用层"先查后写"（后者在并发下必然漏，见 `ent_offer_response` 的同一论据）。

`evidence_ref` 为什么必须补到候选行上
------------------------------------
第 3 条要 `identified evidence`。原表只有 `evidence_kind`（"是单据"），
没有 `evidence_ref`（"是**哪一份**"）—— 只有类别不构成"identified"。
补列走**追加迁移**（本模块 id=3），不动 `ent_commitment.py`（已发布，只增不改）。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建/改动的对象。

⚠️ **模块名为什么以 `ent_commitment_` 开头**（不是笔误）
------------------------------------------------------
迁移条目按**模块文件名排序**应用（`migrate.py:load_entries` 用
`sorted(pkgutil.iter_modules(...), key=lambda m: m.name)`），**没有**声明式依赖。
本模块 id=3 要 `ALTER ent_capacity_candidate`，而那张表由 `ent_commitment.py` 建 ——
若把本模块起名 `ent_capacity*.py`，它会排在 `ent_commitment.py` **之前**，
于是在一张还不存在的表上做 ALTER（实测症状：`no such table: ent_capacity_candidate`，
且只在**全新库**上出现，已有库因为表已存在而全绿）。
既有 ALTER 型模块都恰好满足这条（`ent_artifact_assignment` > `ent_artifact`、
`ent_extract` > `ent_attachment`、`ent_revalidation` > `ent_exception`），
本条只是把这条隐含规则显式写下来。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建运力确认记录表 ent_capacity_confirmation（独立记录 + 判定输入冻结快照 + "
            "证据引用；UNIQUE(candidate_id) 保证同一候选只被确认一次）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_capacity_confirmation` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单（ent_assignment.id）',
  `entrustment_id` BIGINT UNSIGNED NULL COMMENT '确认时所在的委托授权（审计用）',
  `candidate_id` BIGINT UNSIGNED NOT NULL
    COMMENT '被确认的候选运力（ent_capacity_candidate.id）；UNIQUE ⇒ 同一候选只能确认一次',
  `leg_id` BIGINT UNSIGNED NULL COMMENT '冗余：候选对应的航段（ent_leg.id）',
  `carrier` VARCHAR(64) NOT NULL COMMENT '承运人（冻结副本：候选行可被改写，确认事实不能）',
  `vessel_name` VARCHAR(64) NULL COMMENT '船名（冻结副本）',
  `capacity_tonnes` DECIMAL(12,3) NOT NULL COMMENT '可用舱位吨位（冻结副本）',
  `vessel_count` INT NOT NULL COMMENT '承运船数（冻结副本）',
  `allows_partial_load` TINYINT NOT NULL COMMENT '是否允许拆批（冻结副本）',
  `rate` DECIMAL(12,4) NULL COMMENT '单价（冻结副本；供应商侧口径，属内部信息）',
  `rate_unit` VARCHAR(16) NULL COMMENT '计价单位（冻结副本）',
  `currency` VARCHAR(8) NULL COMMENT '币种（冻结副本）',
  `valid_until` DATE NULL COMMENT '报价有效期（冻结副本；判定基准日与它比较）',
  `evidence_kind` VARCHAR(32) NULL COMMENT '证据类别（冻结副本）',
  `evidence_ref` VARCHAR(255) NULL COMMENT '证据引用（冻结副本）—— identified evidence 的落点',
  `demand_tonnes` DECIMAL(14,3) NULL
    COMMENT '判定时的需求量快照（来自 ent_assignment.quantity）；不复算就无法回答"当时按多少吨判的"',
  `demand_unit` VARCHAR(24) NULL COMMENT '判定时的需求单位快照（口径不同不得直接比较）',
  `demand_ref` VARCHAR(64) NOT NULL COMMENT '需求量来自哪一行，如 assignment:7.quantity',
  `as_of_date` DATE NOT NULL COMMENT '判定基准日（有效期与它比较；与记录时间分开，业务时间可复算）',
  `rule_set_version` VARCHAR(16) NOT NULL
    COMMENT '规则集版本；规则会演进，旧确认必须能说清它按哪一版判的',
  `artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '确认成果（ent_artifact.id，类型 procurement_confirm）',
  `artifact_revision_id` BIGINT UNSIGNED NOT NULL COMMENT '成果版本（ent_artifact_revision.id）',
  `artifact_revision_no` INT NOT NULL COMMENT '冗余的版本号，便于单看本行自证',
  `confirmed_by` BIGINT UNSIGNED NOT NULL COMMENT '确认人（经理人；授权的动作要有 actor）',
  `confirmed_at` DATETIME NOT NULL,
  `note` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_capacity_confirmation_candidate` (`candidate_id`),
  KEY `idx_ent_capacity_confirmation_assignment` (`assignment_id`, `confirmed_at`),
  KEY `idx_ent_capacity_confirmation_artifact` (`artifact_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_capacity_confirmation` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `entrustment_id` INTEGER NULL,
  `candidate_id` INTEGER NOT NULL,
  `leg_id` INTEGER NULL,
  `carrier` TEXT NOT NULL,
  `vessel_name` TEXT NULL,
  `capacity_tonnes` TEXT NOT NULL,
  `vessel_count` INTEGER NOT NULL,
  `allows_partial_load` INTEGER NOT NULL,
  `rate` TEXT NULL,
  `rate_unit` TEXT NULL,
  `currency` TEXT NULL,
  `valid_until` TEXT NULL,
  `evidence_kind` TEXT NULL,
  `evidence_ref` TEXT NULL,
  `demand_tonnes` TEXT NULL,
  `demand_unit` TEXT NULL,
  `demand_ref` TEXT NOT NULL,
  `as_of_date` TEXT NOT NULL,
  `rule_set_version` TEXT NOT NULL,
  `artifact_id` INTEGER NOT NULL,
  `artifact_revision_id` INTEGER NOT NULL,
  `artifact_revision_no` INTEGER NOT NULL,
  `confirmed_by` INTEGER NOT NULL,
  `confirmed_at` TEXT NOT NULL,
  `note` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`candidate_id`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_capacity_confirmation_assignment`
  ON `ent_capacity_confirmation` (`assignment_id`, `confirmed_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_capacity_confirmation_artifact`
  ON `ent_capacity_confirmation` (`artifact_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_capacity_confirmation`"],
    },
    {
        "id": 2,
        "description": (
            "创建运力确认的逐规则判定表 ent_capacity_rule_check（哪几条规则被评估过、"
            "各自比较了什么；只记通过的判定，故无 outcome 列）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_capacity_rule_check` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `confirmation_id` BIGINT UNSIGNED NOT NULL
    COMMENT '所属确认记录（ent_capacity_confirmation.id）',
  `rule_code` VARCHAR(32) NOT NULL
    COMMENT '规则代码（demand_known / evidence / validity / capacity）',
  `seq` INT NOT NULL COMMENT '评估与展示顺序（顺序有意义：先问需求口径，再问证据与适用性）',
  `detail` VARCHAR(500) NOT NULL
    COMMENT '判定说明，含实际比较的数（如「900 < 950，缺口 50」）；空说明等于没判',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_capacity_rule_check` (`confirmation_id`, `rule_code`),
  KEY `idx_ent_capacity_rule_check_rule` (`rule_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_capacity_rule_check` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `confirmation_id` INTEGER NOT NULL,
  `rule_code` TEXT NOT NULL,
  `seq` INTEGER NOT NULL,
  `detail` TEXT NOT NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`confirmation_id`, `rule_code`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_capacity_rule_check_rule`
  ON `ent_capacity_rule_check` (`rule_code`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_capacity_rule_check`"],
    },
    {
        "id": 3,
        "description": (
            "候选运力表 ent_capacity_candidate 补可空证据引用列 evidence_ref "
            "（BP-03 第 3 条的 identified evidence：只有类别不构成「identified」）"
        ),
        "sql": {
            "mysql": """
ALTER TABLE `ent_capacity_candidate`
  ADD COLUMN `evidence_ref` VARCHAR(255) NULL
    COMMENT '证据引用（附件 id / 外部编号 / 说明）—— BP-03 第 3 条要求 identified evidence'
""",
            "sqlite": """
ALTER TABLE `ent_capacity_candidate` ADD COLUMN `evidence_ref` TEXT NULL
""",
        },
        # 该断言同时证明两件事：列存在，且**允许 NULL**（历史行与"尚未附证据"的候选不违规）。
        "checks": ["SELECT COUNT(*) FROM `ent_capacity_candidate` WHERE `evidence_ref` IS NULL"],
    },
]
