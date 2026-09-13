"""会话、消息、Agent 作业与尝试日志 —— S2「作业表 + worker」的数据底座（ENT-011）。

为什么是"表 + worker"而不是队列中间件
------------------------------------
计划 §3.4 明确：**持久化作业表 + worker 即足够**，不做微调、不做多模型集成、
不新增向量库。R1 需要的是"重启不丢任务、重试不重复副作用"（AC-15）——
这靠**作业行的状态机 + 租约 + 幂等键**实现，而不是靠内存队列。

四张表
------
1. `ent_session`：会话。绑定一个**授权上下文**（`entrustment_id`）与专业
   （R1 只开放 `agent_01` / `agent_02`，其余专业在服务层被拒 —— 不是"能建但不可用"）；
2. `ent_session_message`：消息，`seq` 在会话内单调递增，**append-only**。
   `source` 区分 `manual` / `agent` / `deterministic` —— UI 要标注不同来源，
   不得把确定性计算标成 LLM 推理（计划 §3.4 最后一条）；
3. `ent_agent_job`：作业状态机 `queued → running → succeeded / failed / cancelled`。
   * **租约**（`lease_owner` + `lease_expires_at`）：进程崩溃后租约过期，作业可被
     重新领取 —— 这是"重启不丢任务"的落点；
   * **有界重试**（`attempt_count` / `max_attempts`）：超过上限转 `failed`，
     不无限重试；
   * **输出现场**（`envelope_json`）：校验通过的信封原样保存；
     校验失败只写 `error_kind` / `error_message`，**不写任何业务成果** ——
     这是 AC-08「输出校验失败等于不做业务变更」的数据层保证；
4. `ent_agent_job_attempt`：每次尝试一行（`UNIQUE(job_id, attempt_no)`），
   保留原始输出与耗时，供审计与"重试到底发生了什么"的复盘。

`ent_session` 的绑定不可变
--------------------------
会话一旦创建，`entrustment_id` / `assignment_id` **不提供任何修改接口** ——
这直接满足跨屏不变量「已产生任务绑定成果的会话不得改绑到另一张委托」：
不可变是比"每次检查有没有改绑过"更强的保证。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建会话表 ent_session（绑定授权上下文 + 开放专业 + 归档状态）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_session` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `entrustment_id` BIGINT UNSIGNED NULL COMMENT '所属委托授权（ent_entrustment.id，可空）',
  `assignment_id` BIGINT UNSIGNED NULL COMMENT '所属委托单（ent_assignment.id，可空）',
  `owner_user_id` BIGINT NOT NULL COMMENT '数据归属货主（授权作用域判定的锚）',
  `org_id` BIGINT UNSIGNED NULL COMMENT '服务经营主体（组织侧会话非空）',
  `created_by` BIGINT NOT NULL COMMENT '创建者',
  `agent_specialty` VARCHAR(16) NULL COMMENT 'agent_01/agent_02；NULL=通用会话壳（无专业能力）',
  `title` VARCHAR(128) NOT NULL COMMENT '会话标题（上下文芯片的文本来源）',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active/archived',
  `revision` INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '乐观锁版本（AC-11）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_session_entrustment` (`entrustment_id`, `created_at`),
  KEY `idx_ent_session_assignment` (`assignment_id`, `created_at`),
  KEY `idx_ent_session_creator` (`created_by`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_session` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `entrustment_id` INTEGER NULL,
  `assignment_id` INTEGER NULL,
  `owner_user_id` INTEGER NOT NULL,
  `org_id` INTEGER NULL,
  `created_by` INTEGER NOT NULL,
  `agent_specialty` TEXT NULL,
  `title` TEXT NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'active',
  `revision` INTEGER NOT NULL DEFAULT 1,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_session_entrustment`
  ON `ent_session` (`entrustment_id`, `created_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_session_assignment`
  ON `ent_session` (`assignment_id`, `created_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_session_creator`
  ON `ent_session` (`created_by`, `created_at`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_session`"],
    },
    {
        "id": 2,
        "description": "创建会话消息表 ent_session_message（append-only，seq 会话内单调）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_session_message` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `session_id` BIGINT UNSIGNED NOT NULL COMMENT '所属会话（ent_session.id）',
  `seq` INT UNSIGNED NOT NULL COMMENT '会话内序号，从 1 开始单调递增',
  `role` VARCHAR(16) NOT NULL COMMENT 'user/agent/system',
  `content` LONGTEXT NOT NULL COMMENT '消息正文',
  `source` VARCHAR(16) NOT NULL COMMENT 'manual/agent/deterministic —— UI 按来源标注',
  `job_id` BIGINT UNSIGNED NULL COMMENT '关联作业（agent 消息非空）',
  `created_by` BIGINT NOT NULL COMMENT '发送者（system 消息为触发者）',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_session_message_seq` (`session_id`, `seq`),
  KEY `idx_ent_session_message_job` (`job_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_session_message` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `session_id` INTEGER NOT NULL,
  `seq` INTEGER NOT NULL,
  `role` TEXT NOT NULL,
  `content` TEXT NOT NULL,
  `source` TEXT NOT NULL,
  `job_id` INTEGER NULL,
  `created_by` INTEGER NOT NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`session_id`, `seq`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_session_message_job`
  ON `ent_session_message` (`job_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_session_message`"],
    },
    {
        "id": 3,
        "description": "创建 Agent 作业表 ent_agent_job（状态机 + 租约 + 有界重试 + 信封现场）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_agent_job` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `session_id` BIGINT UNSIGNED NULL COMMENT '所属会话（可空，允许无会话的作业）',
  `entrustment_id` BIGINT UNSIGNED NULL COMMENT '授权上下文（无授权链的作业为 NULL）',
  `assignment_id` BIGINT UNSIGNED NULL COMMENT '委托单上下文',
  `task_id` BIGINT UNSIGNED NULL COMMENT '任务上下文',
  `artifact_id` BIGINT UNSIGNED NULL COMMENT '成果上下文（在已有成果上作业）',
  `specialty` VARCHAR(16) NOT NULL COMMENT 'agent_01/agent_02（R1 仅两个开放专业）',
  `status` VARCHAR(16) NOT NULL DEFAULT 'queued'
    COMMENT 'queued/running/succeeded/failed/cancelled',
  `attempt_count` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已尝试次数',
  `max_attempts` INT UNSIGNED NOT NULL DEFAULT 3 COMMENT '有界重试上限',
  `lease_owner` VARCHAR(64) NULL COMMENT '租约持有者（worker 标识）',
  `lease_expires_at` DATETIME NULL COMMENT '租约到期时间；过期即可被重新领取',
  `base_revision` INT UNSIGNED NULL COMMENT '产出所依据的版本（同源同版本锚点）',
  `input_json` LONGTEXT NULL COMMENT '作业输入（调用方提供的上下文文本等）',
  `envelope_json` LONGTEXT NULL COMMENT '校验通过的类型化信封（唯一允许成为业务事实的载体）',
  `error_kind` VARCHAR(32) NULL COMMENT '失败分类：llm_timeout/llm_auth/invalid_output/...',
  `error_message` VARCHAR(255) NULL COMMENT '失败说明（失败必须可解释）',
  `requires_review` TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'R1 恒为 1：提案一律需人工复核',
  `created_by` BIGINT NOT NULL COMMENT '发起人',
  `started_at` DATETIME NULL,
  `finished_at` DATETIME NULL,
  `cancelled_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_agent_job_queue` (`status`, `lease_expires_at`),
  KEY `idx_ent_agent_job_session` (`session_id`, `created_at`),
  KEY `idx_ent_agent_job_assignment` (`assignment_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_agent_job` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `session_id` INTEGER NULL,
  `entrustment_id` INTEGER NULL,
  `assignment_id` INTEGER NULL,
  `task_id` INTEGER NULL,
  `artifact_id` INTEGER NULL,
  `specialty` TEXT NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'queued',
  `attempt_count` INTEGER NOT NULL DEFAULT 0,
  `max_attempts` INTEGER NOT NULL DEFAULT 3,
  `lease_owner` TEXT NULL,
  `lease_expires_at` TEXT NULL,
  `base_revision` INTEGER NULL,
  `input_json` TEXT NULL,
  `envelope_json` TEXT NULL,
  `error_kind` TEXT NULL,
  `error_message` TEXT NULL,
  `requires_review` INTEGER NOT NULL DEFAULT 1,
  `created_by` INTEGER NOT NULL,
  `started_at` TEXT NULL,
  `finished_at` TEXT NULL,
  `cancelled_at` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_agent_job_queue`
  ON `ent_agent_job` (`status`, `lease_expires_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_agent_job_session`
  ON `ent_agent_job` (`session_id`, `created_at`);
CREATE INDEX IF NOT EXISTS `idx_ent_agent_job_assignment`
  ON `ent_agent_job` (`assignment_id`, `created_at`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_agent_job`"],
    },
    {
        "id": 4,
        "description": "创建作业尝试日志表 ent_agent_job_attempt（每次尝试一行，保留原始输出）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_agent_job_attempt` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `job_id` BIGINT UNSIGNED NOT NULL COMMENT '所属作业（ent_agent_job.id）',
  `attempt_no` INT UNSIGNED NOT NULL COMMENT '第几次尝试，从 1 开始',
  `status` VARCHAR(16) NOT NULL COMMENT 'succeeded/failed',
  `error_kind` VARCHAR(32) NULL COMMENT '失败分类（成功为 NULL）',
  `error_message` VARCHAR(255) NULL,
  `latency_ms` INT UNSIGNED NULL COMMENT '本次调用的耗时',
  `mocked` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否走确定性 fixture（非真实模型）',
  `raw_output` LONGTEXT NULL COMMENT '模型原始输出文本（审计用；不参与任何业务判断）',
  `started_at` DATETIME NOT NULL,
  `finished_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_agent_job_attempt` (`job_id`, `attempt_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_agent_job_attempt` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `job_id` INTEGER NOT NULL,
  `attempt_no` INTEGER NOT NULL,
  `status` TEXT NOT NULL,
  `error_kind` TEXT NULL,
  `error_message` TEXT NULL,
  `latency_ms` INTEGER NULL,
  `mocked` INTEGER NOT NULL DEFAULT 0,
  `raw_output` TEXT NULL,
  `started_at` TEXT NOT NULL,
  `finished_at` TEXT NOT NULL,
  UNIQUE (`job_id`, `attempt_no`)
)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_agent_job_attempt`"],
    },
]
