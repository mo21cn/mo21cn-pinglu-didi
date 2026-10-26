"""关务（智慧关务 / 关域）D2 —— 商品归类辅助（含降级契约）。

对应决策文档《智慧关务-可演示最小化里程碑与Q1-Q7决定-20261025》§2.1 的 **D2**：
「归类建议 + 人工确认」。这里只建两张表，**不建商品库、查验、订阅**（属 D2 后半 / D3）。

## 为什么 D2 的第一件事是"降级"而不是"接模型"

归类建议依赖大模型。而模型是**会不可用**的（无 Key、限流、余额不足、超时）。若界面上
只有"候选列表"这一个出口，模型一挂页面就是**空列表** —— 空列表在演示现场既不像
"没有匹配"，也不像"出故障了"，事后也无从判断当时到底发生了什么。

所以本表把"模型为什么没给出建议"做成**一等字段**：

* `degraded_kind`：机器可读的失败类别（白名单，与 `agent.llm.LLMError.kind` 同源），
  用于统计与告警；⛔ 不存原始异常字符串（可能带 URL / 凭证片段）；
* `degraded_reason`：**给用户看的一句话**（中文、可复述），由白名单映射生成，
  ⛔ 不允许直接把第三方的报错原文塞进这里；
* `status` 增加 `manual_required` 一档：它**不是错误态**，而是"模型没帮上忙，请人来定"
  的正常分支。手工通路的入口由 `status` 与 `degraded_*` 共同决定，不靠前端猜。

## 只追加的候选，不容改的结论

* `cus_classification_candidate` 只追加：模型给的与人工填的**都是候选**，
  用 `source ∈ {model, manual}` 区分。⛔ 不允许把人工结果"覆盖"到模型那行上 ——
  那样就再也说不清"最终这个编码是模型给的还是人定的"，而演示最怕被问的就是这句；
* **确认是人的动作**：`hs_code`（最终结论）只有人工确认才写；模型建议永远只落在候选表里。
  模型直接落结论＝把责任转移给模型，PRD §4.4 的"状态只能由带来源的事件推进"同理。

## 表结构纪律

* **弱关联**：`declaration_id` 只存 id，不建外键、不级联删除（PRD §7）；
* **未知保持 NULL**：`hs_code` / `confidence` / `rationale` / `decided_by_user_id` 都可空，
  不补 0、不填空串；
* 时间存 UTC 朴素时间，格式与既有 `ent_*` / `cus_*` 表一致。

`cus_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建归类记录表 cus_classification（D2 · 含降级字段）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `cus_classification` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `declaration_id` BIGINT UNSIGNED NULL COMMENT '弱关联 cus_declaration.id，不建外键',
  `owner_user_id` BIGINT NOT NULL COMMENT '发起人 / 归属货主',
  `org_id` BIGINT UNSIGNED NULL COMMENT '服务经营主体（可空；空＝个人记录）',
  `product_name` VARCHAR(128) NOT NULL COMMENT '待归类商品名（人工输入的原文）',
  `product_desc` VARCHAR(1024) NULL COMMENT '补充描述（材质 / 用途等，可空）',
  `status` VARCHAR(24) NOT NULL DEFAULT 'pending'
    COMMENT 'pending / suggested / manual_required / confirmed',
  `hs_code` VARCHAR(16) NULL
    COMMENT '最终结论：只有人工确认才写；模型建议只落候选表，⛔ 不写这里',
  `degraded_kind` VARCHAR(24) NULL
    COMMENT '模型不可用的类别（白名单：timeout/network/auth/rate_limit/quota/bad_response/unknown）',
  `degraded_reason` VARCHAR(256) NULL
    COMMENT '给用户看的一句话（白名单映射生成，⛔ 不存原始异常串）',
  `model_name` VARCHAR(64) NULL COMMENT '出建议的模型名（降级时为空）',
  `model_mocked` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否规则模板输出（LLM_MOCK）',
  `decided_by_user_id` BIGINT NULL COMMENT '确认结论的人（模型永远不会出现在这里）',
  `decided_at` DATETIME NULL,
  `revision` INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '乐观锁版本号（保留位）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_cus_cls_owner` (`owner_user_id`, `status`),
  KEY `idx_cus_cls_org` (`org_id`, `status`),
  KEY `idx_cus_cls_decl` (`declaration_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `cus_classification` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `declaration_id` INTEGER NULL,
  `owner_user_id` INTEGER NOT NULL,
  `org_id` INTEGER NULL,
  `product_name` TEXT NOT NULL,
  `product_desc` TEXT NULL,
  `status` TEXT NOT NULL DEFAULT 'pending',
  `hs_code` TEXT NULL,
  `degraded_kind` TEXT NULL,
  `degraded_reason` TEXT NULL,
  `model_name` TEXT NULL,
  `model_mocked` INTEGER NOT NULL DEFAULT 0,
  `decided_by_user_id` INTEGER NULL,
  `decided_at` TEXT NULL,
  `revision` INTEGER NOT NULL DEFAULT 1,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_cus_cls_owner`
  ON `cus_classification` (`owner_user_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_cus_cls_org`
  ON `cus_classification` (`org_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_cus_cls_decl`
  ON `cus_classification` (`declaration_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `cus_classification`"],
    },
    {
        "id": 2,
        "description": "创建归类候选表 cus_classification_candidate（只追加 · model/manual 分源）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `cus_classification_candidate` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `classification_id` BIGINT UNSIGNED NOT NULL COMMENT '指向 cus_classification.id',
  `hs_code` VARCHAR(16) NOT NULL COMMENT '10 位或 8 位税则号列（按人工录入原文保存）',
  `label` VARCHAR(256) NULL COMMENT '商品名称 / 税则描述',
  `confidence` DECIMAL(5,4) NULL
    COMMENT '模型自评置信度；人工填写时为空（⛔ 不给人工结果编一个分数）',
  `source` VARCHAR(12) NOT NULL DEFAULT 'model'
    COMMENT 'model=模型建议 / manual=人工填写；⛔ 两者不可互相覆盖',
  `rank_no` INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '展示顺序（模型按自评排序，人工默认 1）',
  `rationale` VARCHAR(1024) NULL COMMENT '给出的理由（模型自述 or 人工填写的依据）',
  `actor_user_id` BIGINT NULL COMMENT '人工填写者；模型建议为空',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_cus_cand_cls` (`classification_id`, `source`, `rank_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `cus_classification_candidate` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `classification_id` INTEGER NOT NULL,
  `hs_code` TEXT NOT NULL,
  `label` TEXT NULL,
  `confidence` REAL NULL,
  `source` TEXT NOT NULL DEFAULT 'model',
  `rank_no` INTEGER NOT NULL DEFAULT 1,
  `rationale` TEXT NULL,
  `actor_user_id` INTEGER NULL,
  `created_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_cus_cand_cls`
  ON `cus_classification_candidate` (`classification_id`, `source`, `rank_no`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `cus_classification_candidate`"],
    },
]
