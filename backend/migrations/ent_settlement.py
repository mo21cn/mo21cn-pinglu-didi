"""结算版本与收付依据（`ent_settlement` / `ent_settlement_payment`）。

为什么需要这两张表（合同 S4 段第 11 条 ＋ HO 0918-2 裁定 Q5）
------------------------------------------------------------
合同要求「结算草稿 ＋ 内部/客户确认 ＋ 外部付款证据」，并**明标本期可用合成样本**。
裁定 Q5 选了**第四种口径**：结案检查**客户对「最终适用结算版本」的确认**
—— ⛔ 原报价接受记录**不能替代**（它发生在履约前，证明不了客户接受后来产生的等候费、
费用调整与最终结算）；⛔ 也**不另建**独立的"完成确认"回路，`S4-b` 直接读这里的结果。

⇒ 所以「结算」必须是一个**有版本的对象**，而不是一段文本：`settlement_draft` 成果类型
只是**文本载体**，说不出"客户确认的是哪一版"。

五条语义（裁定 Q5 第 1–5 条）直接落在结构上
-------------------------------------------
1. **客户确认绑定精确结算版本** ⇒ 确认字段（人 / 时间 / 决定）挂在 `ent_settlement`
   **这一行**上，不在委托上、也不在"最新版本"这个概念上；
2. **客户只看对客费用及证据白名单** ⇒ 快照里同时存对客与内部成本行，
   但对客投影**只出 `direction = receivable` 的那些**（内部供应商成本不出库到客户面）；
3. ⭐ **确认后修改相关费用，必须形成新结算版本** ⇒ 版本号 `version_no` 单调递增、
   `UNIQUE (assignment_id, version_no)`；**旧版本行不被改写**（旧确认保留），
   而"适用版本"是**最大版本号**那一行 ⇒ 旧确认**结构上**不可能替新版本过关
   —— 这不是靠一条 if 判断维持的，是靠"确认挂在哪一行"维持的；
4. **客户确认 / 记录收付证据 / 经理执行结案，是三个独立事实** ⇒ 三个动作各自落字段与
   各自的表：确认落在本表，收付落在 `ent_settlement_payment`，结案在 `S4-b`（另开切片）；
5. **收付可用明确标注的合成样本证据** ⇒ `ent_settlement_payment.mode` 的合法取值
   **只有一个**：`labeled_sample`（写入口对未知取值一律 400，与合同签署证据同一口径）。

`financial_status` 的派生（§5.3.2）
----------------------------------
本迁移**只建表**：派生逻辑在 `settlement.py` 里接通之后才允许在界面/接口上展示该字段
（`ent_assignment_completion.py` 已写明"派生未接通时不展示默认值"）。本迁移**不动**
`ent_assignment.financial_status` 一个字节。

本切片**刻意不做**的事
----------------------
* ⛔ 不接真实支付、不宣称资金到账（`mode` 只有合成样本一个取值）；
* ⛔ 不做多币种兑换、税务、发票、银行接入、授信、通用会计账簿（§5 边界 4）；
* ⛔ 不改 `ent_charge` 的任何列 —— 费用行的口径在 S7-1 已冻结；
* ⛔ 不建"完成确认"回路（裁定 Q5 明令）。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建结算版本表 ent_settlement（版本号单调递增 + 费用行快照 + 内部确认 + "
            "客户对该精确版本的确认；确认挂在这一行上 ⇒ 旧确认结构上替不了新版本过关）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_settlement` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '委托单（ent_assignment.id）',
  `version_no` INT UNSIGNED NOT NULL COMMENT '结算版本号，委托内从 1 单调递增；适用版本＝最大版本号那一行',
  `status` VARCHAR(16) NOT NULL COMMENT '版本状态：draft 草稿 / approved 内部已确认（批准后改费用必须出新版本）',
  `lines` TEXT NOT NULL COMMENT '费用行**快照** JSON：[{charge_id,revision,direction,charge_kind,amount,currency,counterparty,basis}]；快照自足，费用行后来被改也不影响本版当时算的是什么',
  `currency` VARCHAR(8) NOT NULL COMMENT '结算币种（DEMO-1 仅 CNY；合计不跨币种）',
  `customer_total` DECIMAL(18,4) NOT NULL COMMENT '对客应收合计（direction=receivable 的行），出客户面的唯一金额口径',
  `internal_total` DECIMAL(18,4) NOT NULL COMMENT '内部成本合计（direction=payable 的行），不进客户面',
  `approved_by` BIGINT UNSIGNED NULL COMMENT '内部确认人（留痕用，不参与判权）',
  `approved_at` DATETIME NULL COMMENT '内部确认时间',
  `customer_confirmed_by` BIGINT UNSIGNED NULL COMMENT '客户确认人（货主本人；经理人不得代客户确认）',
  `customer_confirmed_at` DATETIME NULL COMMENT '客户确认时间',
  `customer_decision` VARCHAR(16) NULL COMMENT '客户决定：accepted 接受 / rejected 不接受',
  `customer_note` VARCHAR(255) NULL COMMENT '客户备注（可选）',
  `revision` INT UNSIGNED NOT NULL COMMENT '乐观锁（每次状态变更 +1，条件更新用）',
  `created_by` BIGINT UNSIGNED NULL COMMENT '生成人（留痕用，不参与判权）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ent_settlement_version` (`assignment_id`, `version_no`),
  KEY `idx_ent_settlement_assignment` (`assignment_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_settlement` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `version_no` INTEGER NOT NULL,
  `status` TEXT NOT NULL,
  `lines` TEXT NOT NULL,
  `currency` TEXT NOT NULL,
  `customer_total` NUMERIC(18,4) NOT NULL,
  `internal_total` NUMERIC(18,4) NOT NULL,
  `approved_by` INTEGER NULL,
  `approved_at` TEXT NULL,
  `customer_confirmed_by` INTEGER NULL,
  `customer_confirmed_at` TEXT NULL,
  `customer_decision` TEXT NULL,
  `customer_note` TEXT NULL,
  `revision` INTEGER NOT NULL,
  `created_by` INTEGER NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`assignment_id`, `version_no`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_settlement_assignment`
  ON `ent_settlement` (`assignment_id`, `status`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_settlement`"],
    },
    {
        "id": 2,
        "description": (
            "创建收付依据表 ent_settlement_payment（append-only；mode 恒为 labeled_sample "
            "—— 不接真实支付、不宣称资金到账）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_settlement_payment` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `settlement_id` BIGINT UNSIGNED NOT NULL COMMENT '所依据的结算版本（ent_settlement.id）',
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '冗余委托单（按委托取数不必回表；服务层保证一致）',
  `direction` VARCHAR(16) NOT NULL COMMENT '收付方向：receivable 已向客户收款 / payable 已向供应商付款',
  `amount` DECIMAL(18,4) NOT NULL COMMENT '收付金额（Decimal；累计不得超过该方向合计）',
  `currency` VARCHAR(8) NOT NULL COMMENT '币种（与结算版本一致；不跨币种相加）',
  `occurred_at` DATETIME NULL COMMENT '业务发生时间（款项实际收付之日；未知保持 NULL，不默认成记录时间）',
  `mode` VARCHAR(32) NOT NULL COMMENT '证据模式；当前唯一合法取值 labeled_sample（合成样本，已标注），写入口拒绝未知取值',
  `ref` VARCHAR(255) NOT NULL COMMENT '凭据引用（水单号 / 附件标识 / 说明）—— 无来源的收付不可核对',
  `note` VARCHAR(255) NULL COMMENT '备注',
  `recorded_by` BIGINT UNSIGNED NOT NULL COMMENT '记录人（服务端写，不接受提交方自称）',
  `recorded_at` DATETIME NOT NULL COMMENT '记录时间（服务端写；与业务发生时间分开）',
  PRIMARY KEY (`id`),
  KEY `idx_ent_settlement_payment` (`settlement_id`, `direction`),
  KEY `idx_ent_settlement_payment_assignment` (`assignment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_settlement_payment` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `settlement_id` INTEGER NOT NULL,
  `assignment_id` INTEGER NOT NULL,
  `direction` TEXT NOT NULL,
  `amount` NUMERIC(18,4) NOT NULL,
  `currency` TEXT NOT NULL,
  `occurred_at` TEXT NULL,
  `mode` TEXT NOT NULL,
  `ref` TEXT NOT NULL,
  `note` TEXT NULL,
  `recorded_by` INTEGER NOT NULL,
  `recorded_at` TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS `idx_ent_settlement_payment`
  ON `ent_settlement_payment` (`settlement_id`, `direction`);
CREATE INDEX IF NOT EXISTS `idx_ent_settlement_payment_assignment`
  ON `ent_settlement_payment` (`assignment_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_settlement_payment`"],
    },
]
