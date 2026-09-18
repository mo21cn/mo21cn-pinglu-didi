"""费用行表（`ent_charge`）—— 「应收/应付 ＋ 一条等待时间费用」的载体。

为什么需要这张表（合同 S4 段第 9/10 条）
----------------------------------------
合同要求把 `receivable/payable lines` 与 `one extra waiting-time charge` **记成可核对的
财务事实**，并明确：**争议费用不得进已确认总额**。实测本仓在这之前**没有任何费用实体**
（`settlement_draft` 只是成果类型＝文本载体，不是可核对的行）⇒ 结案的「结算已批准 /
余额已结清」两条前置没有判据。本表补的正是这个载体。

三条口径来自 HO 0918-2 的裁定，直接落在列上
------------------------------------------
1. **合计按「币种 × 收付方向」分开**（Q1=C）⇒ `currency` 与 `direction` 都是行上的字段，
   合计**不做跨币种相加**（本期只支持 CNY，但结构上不留这个坑）。
2. **争议的解决必须给出「最终金额」与「是否计入」**（Q2=B）⇒ `resolution_outcome`
   （认可/调减/拒绝）、`resolution_amount`、`counts_in_total` **三列并存**。
   ⛔ 刻意**不**从 `status='resolved'` 推导 `counts_in_total`：一个 `resolved` 一词
   决定不了"这笔记不记进合计"，那正是裁定要避免的歧义。
3. **`waiting_time` 用普通费用行**（Q3=A）⇒ `charge_kind` 是**自由字符串**，
   本表**不为它建独立实体、也不建计费引擎**（未登记的取值原样保留，见 `MODE_LABELS` 同型先例）。

`basis` 与处置依据都必填
------------------------
`basis`（计费依据）`NOT NULL`：没有依据的费用行不可核对，"先记上再说"会让合计失去意义。
处置侧同理：`resolution_method` 必填 —— 与来源核验「无依据的核验等于没核」同一条纪律。

`revision` 与状态机
-------------------
状态只能沿 `draft → confirmed → disputed → resolved|rejected` 前进；
每次变更 `revision + 1`，写入口用 `WHERE revision = :expected` 做**条件更新**
（并发下必须有一个拿到 409，不许后者静默覆盖前者 —— 合同 S4 段第 3 条对结案的同一条要求）。

⚠️ 本迁移**不声称**任何验收通过；它只让「费用行」这件事有地方落。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建费用行表 ent_charge（应收/应付行 ＋ 争议与处置的金额结果；"
            "合计按「币种 × 收付方向」分开；争议行是否计入由处置时显式给出，不由状态推导）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_charge` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '委托单（ent_assignment.id）',
  `direction` VARCHAR(16) NOT NULL COMMENT '收付方向：receivable 应收 / payable 应付（合计按方向分开）',
  `charge_kind` VARCHAR(32) NOT NULL COMMENT '费用类别（自由字符串；waiting_time 是普通费用行，不另建实体）',
  `quantity` DECIMAL(14,3) NULL COMMENT '数量（未知保持 NULL，绝不静默为 0）',
  `unit` VARCHAR(24) NULL COMMENT '数量单位，与 quantity 成对',
  `amount` DECIMAL(18,4) NOT NULL COMMENT '金额（Decimal）',
  `currency` VARCHAR(8) NOT NULL COMMENT '币种（DEMO-1 仅 CNY；合计按币种分开，跨币种不相加）',
  `counterparty` VARCHAR(128) NULL COMMENT '对手方（应收的付款人 / 应付的收款人）',
  `basis` VARCHAR(255) NOT NULL COMMENT '计费依据（必填：没有依据的费用行不可核对）',
  `status` VARCHAR(16) NOT NULL COMMENT '状态：draft 草稿 / confirmed 已确认 / disputed 有争议 / resolved 已解决 / rejected 已拒绝',
  `disputed_reason` VARCHAR(255) NULL COMMENT '争议理由（进 disputed 时必填）',
  `resolution_outcome` VARCHAR(16) NULL COMMENT '处置结果：accepted 认可 / adjusted 调减 / rejected 拒绝',
  `resolution_amount` DECIMAL(18,4) NULL COMMENT '处置后的最终金额，与 resolution_outcome 成对',
  `counts_in_total` TINYINT(1) NULL COMMENT '是否计入合计（处置时显式给出，不由状态推导）',
  `resolution_method` VARCHAR(255) NULL COMMENT '处置依据（必填：无依据的处置等于没处置）',
  `resolution_evidence_ref` VARCHAR(128) NULL COMMENT '处置证据引用（附件/成果/外部凭证标识）',
  `resolved_by` BIGINT UNSIGNED NULL COMMENT '处置人（留痕用，不参与判权）',
  `resolved_at` DATETIME NULL COMMENT '处置时间（记录时间，与业务发生时间区分）',
  `revision` INT UNSIGNED NOT NULL COMMENT '乐观锁（每次状态变更 +1，条件更新用）',
  `created_by` BIGINT UNSIGNED NULL COMMENT '登记人（留痕用，不参与判权）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_charge_assignment` (`assignment_id`, `direction`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_charge` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `direction` TEXT NOT NULL,
  `charge_kind` TEXT NOT NULL,
  `quantity` NUMERIC(14,3) NULL,
  `unit` TEXT NULL,
  `amount` NUMERIC(18,4) NOT NULL,
  `currency` TEXT NOT NULL,
  `counterparty` TEXT NULL,
  `basis` TEXT NOT NULL,
  `status` TEXT NOT NULL,
  `disputed_reason` TEXT NULL,
  `resolution_outcome` TEXT NULL,
  `resolution_amount` NUMERIC(18,4) NULL,
  `counts_in_total` INTEGER NULL,
  `resolution_method` TEXT NULL,
  `resolution_evidence_ref` TEXT NULL,
  `resolved_by` INTEGER NULL,
  `resolved_at` TEXT NULL,
  `revision` INTEGER NOT NULL,
  `created_by` INTEGER NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS `idx_ent_charge_assignment`
  ON `ent_charge` (`assignment_id`, `direction`, `status`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_charge`"],
    },
]
