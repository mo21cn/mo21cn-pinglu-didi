"""合同签署证据表（`ent_contract_signature_evidence`）—— §10.1 第 7 步的后半。

原文与缺口
----------
§10.1 第 7 步：`Create the contract and record labeled sample signature evidence.`

前半（派生合同）已落（`ent_contract_derivation` + `ent_contract_field_source`）。
**本表补的是后半**：把"签署证据"这件事**记下来并绑到合同的某个版本**上，
使 D1-08 的判据 `Contract version and linked evidence inspection` 有东西可查 ——
"合同版本"与"关联证据"在此之前只存在前者。

为什么另开一张表，而不是复用 `ent_artifact_attachment`
------------------------------------------------------
`ent_artifact_attachment` 只绑 `artifact_id`，**没有 revision 概念**，而本合同要求
的是"**这一版**合同的签署证据"：合同被编辑出第 2 版之后，第 1 版上那份证据
属于哪一版必须仍然说得清（否则"客户签的是哪一版"这件事在库里是模糊的）。
给它补 `revision_id` 会让"附件"这一通用概念长出合同语义 —— 与"迁移只增不改"
同一条线，故选新表。

为什么 `mode` 的取值域**只有一个**
-----------------------------------
合法取值只有 `labeled_sample`（与 `offers.SIGNATURE_MODE_LABELED_SAMPLE` 同一常量）。
合同 §3.2 明写：`Mock payment or sample receipt → Must not be represented as
Actual receipt of money`；D1-08 亦要求 `evidence and status remain distinct from
live e-signature`。⇒ **当前不存在第二个合法取值**，写入口对未知 mode 一律 400。

⚠️ 这与航段 `mode`（自由文本、未登记原样显示）是**相反**的口径，别照抄：
航段的 `mode` 描述的是**外部世界的既成事实**（用户说空运就是空运）；
签署证据的 `mode` 是**本系统对证据性质的断言**，断言的内容必须是我们登记过的
那一种 —— 一个未登记的 mode 会被下游当成"某种签署方式"读走，而没人知道它是什么。

`evidence_kind` 为什么是三个固定取值
-------------------------------------
`sample_scan`（样件扫描件）/ `written_confirmation`（书面确认）/ `manual_record`
（人工记录）。同样**不接受未知值**：这三个是"证据形态"的枚举，
未知形态进库之后，"这份证据到底存不存在实物"这个问题就再也没有答案。

唯一键为什么是 `(contract_revision_id, evidence_kind)`
------------------------------------------------------
同一版合同、同一种形态，**只记一条** ⇒ 重放（幂等重发 / 双击）被唯一约束挡住
（409），而不是悄悄记成两条互相打架的证据。要补第二份同形态的材料，
先改 `note` 或换形态 —— 那是**编辑**语义，本表现在不提供（与航段同口径：
本表 append-only，改动留新行而不是就地改）。

⚠️ 本迁移**不声称**任何验收通过：它只让"签署证据"有地方落。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建合同签署证据表 ent_contract_signature_evidence（绑到合同的**具体版本**，"
            "mode 只接受 labeled_sample，与 offers.SIGNATURE_MODE_LABELED_SAMPLE 同一常量）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_contract_signature_evidence` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单（按单查证据时不必回连合同成果）',
  `entrustment_id` BIGINT UNSIGNED NULL COMMENT '发起派生时记下的委托授权；为空表示当时的发布未记授权',
  `contract_artifact_id` BIGINT UNSIGNED NOT NULL COMMENT '合同成果（ent_artifact.id，类型恒为 contract_review）',
  `contract_revision_id` BIGINT UNSIGNED NOT NULL COMMENT '合同的**具体版本**（ent_artifact_revision.id）—— 证据绑的是这一版，不是成果整体',
  `contract_revision_no` INT NOT NULL COMMENT '合同版本号（与 contract_revision_id 同源，读时不回连即可显示第几版）',
  `mode` VARCHAR(32) NOT NULL COMMENT '签署证据模式；当前唯一合法取值 labeled_sample（样件标注），未知取值写入口一律拒绝',
  `evidence_kind` VARCHAR(32) NOT NULL COMMENT '证据形态：sample_scan（样件扫描件）/ written_confirmation（书面确认）/ manual_record（人工记录）',
  `note` VARCHAR(255) NULL COMMENT '这句证据指的是什么（可空；为空时界面显示「未写说明」，不替业务编理由）',
  `recorded_by` BIGINT UNSIGNED NULL COMMENT '记录人（0=系统/种子；留痕用，不参与判权）',
  `recorded_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_cse_revision_kind` (`contract_revision_id`, `evidence_kind`),
  KEY `idx_ent_cse_contract` (`contract_artifact_id`),
  KEY `idx_ent_cse_assignment` (`assignment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_contract_signature_evidence` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `entrustment_id` INTEGER NULL,
  `contract_artifact_id` INTEGER NOT NULL,
  `contract_revision_id` INTEGER NOT NULL,
  `contract_revision_no` INTEGER NOT NULL,
  `mode` TEXT NOT NULL,
  `evidence_kind` TEXT NOT NULL,
  `note` TEXT NULL,
  `recorded_by` INTEGER NULL,
  `recorded_at` TEXT NOT NULL,
  UNIQUE (`contract_revision_id`, `evidence_kind`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_cse_contract`
  ON `ent_contract_signature_evidence` (`contract_artifact_id`);

CREATE INDEX IF NOT EXISTS `idx_ent_cse_assignment`
  ON `ent_contract_signature_evidence` (`assignment_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_contract_signature_evidence`"],
    },
]
