"""异常与变更案件 —— `exceptions` 槽位的数据模型（DR-0013，A1 期底座）。

背景（详见 `docs/entrust/decisions/0013-异常与变更案件.md`）
----------------------------------------------------------
`exceptions` 此前是本支线**唯一仍「本期未开放」的槽位**，走 `available=False` +
`unavailable_reason` 的独立通道（DR-0010 §3.8）—— 刻意不套空值四态：套四态会把
「这个能力还没做」说成「这单没有异常」，后者是**业务结论**，前者是**工程状态**。

DR-0013 更正了一个错误前提：PRD **有**字段级要求，只是**分散在多处**——
§5.5 第 255 行（字段清单）、§6.1 第 277 行（状态机六值）、§5.4 第 224 行（阻断语义）、
§6.2 第 285 行（变更传播）、§7 第 381 行（对象投影）。本模型按条文逐项落地。

三张表（为什么不是两张）
------------------------
HO 第 4 条明确「若公共审计机制不足，允许增加最小案件事件表，**不受『两张表』限制**」，
并把它与「重开的状态更新与审计必须同事务」绑在一起。本支线**没有**公共审计机制，
而「状态改了但没留痕」在案件这类对象上不可接受 ⇒ 采用第三张表。
范围**只到案件**，不做通用审计平台（AC-24 的通用口径不因此满足）。

    ent_exception        案件本体（异常 exception / 变更请求 change_request）
    ent_exception_link   受影响项（task / artifact）—— AC-12 的「受影响集合」权威来源
    ent_exception_event  最小案件事件表，append-only，与状态变更**同事务**

关键纪律（本模块只建表，不写业务规则）
--------------------------------------
* `assignment_id` 是**归属**（NOT NULL），不是权限边界 —— 权限判定仍只有 `authz.py`
  一条入口（DR-0012 §3.1）。**不新增** `entrustment_id`：那会造出第二个作用域。
* `org_id` 由服务端从 `assignment.org_id` **派生**，不接受客户端指定（DR-0013 §3.1.4）。
* 阻断只认 `impact_kind`，`severity` **不参与**任何 allow/deny（DR-0013 §3.3）——
  否则「改一个展示用字段」就成了权限级开关。
* `basis_revision_id`（决定依据的**成果**版本）与 `revision_no`（案件记录的**乐观锁**）
  是两件事，**都要**。混用会让「旧批准不得直接应用到新内容」这条校验失效。

约定
----
表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`（DR-0001）。
`id` 只增不改；本模块是独立文件，不修改任何已发布迁移，因此可单独回退。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

# 取值域（与 DR-0013 §3.1 一致）已逐列写进下方 COMMENT，**不在此另存一份常量** ——
# 本模块只建表，状态机与一致性约束在服务层实现；两处各写一份取值域迟早会漂移。

migrations = [
    {
        "id": 1,
        "description": (
            "创建异常与变更案件表 ent_exception"
            "（归属 assignment_id 必填、org_id 服务端派生、阻断只认 impact_kind）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_exception` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `org_id` BIGINT UNSIGNED NOT NULL COMMENT '作用域，由服务端从 assignment.org_id 派生（不接受客户端指定）',
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '归属委托单（ent_assignment.id）；案件必属于某张委托单',
  `kind` VARCHAR(16) NOT NULL COMMENT 'exception=真实异常 / change_request=变更请求',
  `title` VARCHAR(200) NOT NULL COMMENT '一句话摘要（PRD UI-08 Cause）',
  `cause` TEXT NULL COMMENT '原因',
  `severity` VARCHAR(16) NOT NULL COMMENT 'low/medium/high/critical；不参与任何 allow/deny 计算',
  `impact_kind` VARCHAR(24) NOT NULL COMMENT 'informational/review-required/execution-blocking；阻断的唯一判据',
  `status` VARCHAR(16) NOT NULL COMMENT 'open/in_review/approved/rejected/applied/closed；合法转移按 kind 分别定义',
  `owner_user_id` BIGINT NULL COMMENT '责任人（可空=尚未分配）',
  `raised_by_user_id` BIGINT NOT NULL COMMENT '提出人',
  `source` VARCHAR(24) NOT NULL COMMENT 'manual/chat/customer/agent_proposal（A1 只产生前三者）',
  `due_at` DATETIME NULL COMMENT '要求完成时间',
  `proposed_action` TEXT NULL COMMENT '提议的处置',
  `decision_note` TEXT NULL COMMENT '决定内容',
  `decided_by` BIGINT NULL COMMENT '决定人（审计四要素）',
  `decided_at` DATETIME NULL COMMENT '决定时间（审计四要素）',
  `basis_revision_id` BIGINT UNSIGNED NULL COMMENT '决定依据的成果版本（ent_artifact_revision.id）；approved 后必填。与 revision_no 不是一回事',
  `resolution_note` TEXT NULL COMMENT '处置说明（resolution evidence）',
  `closure_disposition` VARCHAR(32) NULL COMMENT 'resolved/accepted_residual/cancelled/duplicate/superseded；关闭时必填，不做默认值填充',
  `closed_by` BIGINT NULL COMMENT '关闭人',
  `closed_at` DATETIME NULL COMMENT '关闭时间',
  `revision_no` INT NOT NULL DEFAULT 1 COMMENT '案件记录自身的乐观锁，命令带 expected_revision',
  `raised_at` DATETIME NOT NULL COMMENT '业务发生时间（与录入时间分开）',
  `created_at` DATETIME NOT NULL COMMENT '录入时间',
  `updated_at` DATETIME NOT NULL COMMENT '录入时间',
  PRIMARY KEY (`id`),
  KEY `idx_ent_exception_assignment` (`assignment_id`, `status`),
  KEY `idx_ent_exception_org` (`org_id`, `status`),
  KEY `idx_ent_exception_due` (`due_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_exception` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `org_id` INTEGER NOT NULL,
  `assignment_id` INTEGER NOT NULL,
  `kind` TEXT NOT NULL,
  `title` TEXT NOT NULL,
  `cause` TEXT NULL,
  `severity` TEXT NOT NULL,
  `impact_kind` TEXT NOT NULL,
  `status` TEXT NOT NULL,
  `owner_user_id` INTEGER NULL,
  `raised_by_user_id` INTEGER NOT NULL,
  `source` TEXT NOT NULL,
  `due_at` TEXT NULL,
  `proposed_action` TEXT NULL,
  `decision_note` TEXT NULL,
  `decided_by` INTEGER NULL,
  `decided_at` TEXT NULL,
  `basis_revision_id` INTEGER NULL,
  `resolution_note` TEXT NULL,
  `closure_disposition` TEXT NULL,
  `closed_by` INTEGER NULL,
  `closed_at` TEXT NULL,
  `revision_no` INTEGER NOT NULL DEFAULT 1,
  `raised_at` TEXT NOT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_exception_assignment`
  ON `ent_exception` (`assignment_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_exception_org` ON `ent_exception` (`org_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_exception_due` ON `ent_exception` (`due_at`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_exception`"],
    },
    {
        "id": 2,
        "description": (
            "创建受影响项表 ent_exception_link"
            "（任务/成果，唯一约束防重复登记；applied_revision_id 是精确版本引用）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_exception_link` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `exception_id` BIGINT UNSIGNED NOT NULL COMMENT '所属案件（ent_exception.id）',
  `target_kind` VARCHAR(16) NOT NULL COMMENT 'task / artifact',
  `target_id` BIGINT UNSIGNED NOT NULL COMMENT '目标 id；必须与案件同属一张委托单，服务层校验',
  `applied_revision_id` BIGINT UNSIGNED NULL COMMENT '该受影响成果被应用到的精确版本（A2 写入）；写入即「阻断被真实解除」的证据',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_exception_link` (`exception_id`, `target_kind`, `target_id`),
  KEY `idx_ent_exception_link_target` (`target_kind`, `target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_exception_link` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `exception_id` INTEGER NOT NULL,
  `target_kind` TEXT NOT NULL,
  `target_id` INTEGER NOT NULL,
  `applied_revision_id` INTEGER NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`exception_id`, `target_kind`, `target_id`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_exception_link_target`
  ON `ent_exception_link` (`target_kind`, `target_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_exception_link`"],
    },
    {
        "id": 3,
        "description": (
            "创建最小案件事件表 ent_exception_event"
            "（append-only，与状态变更同事务；重开的两轮历史由此可判定）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_exception_event` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `exception_id` BIGINT UNSIGNED NOT NULL COMMENT '所属案件（ent_exception.id）',
  `seq` INT NOT NULL COMMENT '同一案件内从 1 递增；写入时取 MAX(seq)+1，受案件行锁保护',
  `event_kind` VARCHAR(24) NOT NULL COMMENT 'created/status_changed/decided/link_added/link_removed/applied/applied_rejected/closed/reopened',
  `from_status` VARCHAR(16) NULL COMMENT 'status_changed/closed/reopened 必填',
  `to_status` VARCHAR(16) NULL COMMENT 'status_changed/closed/reopened 必填',
  `actor_user_id` BIGINT NOT NULL COMMENT '谁做的',
  `note` TEXT NULL COMMENT '说明；重开的 reason 落这里（必填）',
  `evidence_ref` TEXT NULL COMMENT '证据引用；closed 必填',
  `basis_revision_id` BIGINT UNSIGNED NULL COMMENT '决定依据版本；decided/applied 必填',
  `payload_json` TEXT NULL COMMENT '附加结构化信息（link 目标、新 revision id 列表等）',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_exception_event` (`exception_id`, `seq`),
  KEY `idx_ent_exception_event_case` (`exception_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_exception_event` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `exception_id` INTEGER NOT NULL,
  `seq` INTEGER NOT NULL,
  `event_kind` TEXT NOT NULL,
  `from_status` TEXT NULL,
  `to_status` TEXT NULL,
  `actor_user_id` INTEGER NOT NULL,
  `note` TEXT NULL,
  `evidence_ref` TEXT NULL,
  `basis_revision_id` INTEGER NULL,
  `payload_json` TEXT NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`exception_id`, `seq`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_exception_event_case`
  ON `ent_exception_event` (`exception_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_exception_event`"],
    },
]
