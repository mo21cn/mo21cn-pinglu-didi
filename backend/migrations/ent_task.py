"""工作任务表 —— S1 任务模型的数据底座（ENT-008）。

对应计划 §3.2 的 **WorkflowTask / Dependency**：类型、负责人、状态、到期、
**固定前置条件**、所需证据、执行代次（lease）。

R1 边界（计划 §3.6 / AC-13 本期子用例）
---------------------------------------
* **只支持固定前置条件**：一个任务至多有一个前置任务（`precondition_task_id`），
  **不声明支持通用 Dependency / DAG**；图形化依赖编辑延期到 R2；
* 只要允许录入依赖，**自依赖与循环检查必须存在** —— 该检查在服务层
  （`app/modules/entrust/tasks.py`），因为数据库约束表达不了传递闭包；

状态机::

    pending ──start──> in_progress ──complete──> done ──reopen──> pending
       │                    │
       ├──── wait ──────────┤                      （缺件 → waiting，不是硬报错）
       │        ↓           │
       │     waiting ──start┘
       └──────── cancel ────┴──────────────────> cancelled

字段纪律
--------
* `required_evidence` / `evidence_refs` 存 JSON 文本（与既有 `ent_` 表一致）；
* 时间一律 UTC 朴素时间；
* `lease_generation` 是**执行代次**（R9 的 fencing 落点）：人工接管/改派推进代次，
  持旧代次的执行者提交结果会被拒绝 —— 迟到结果不得覆盖人工改动；
* 不建外键：`assignment_id` 与 `precondition_task_id` 的引用完整性由服务层保证
  （与 `ent_artifact.current_revision_id` 同一理由 —— 避免 MySQL / SQLite 在外键
  行为上的方言差异，且循环依赖本就不该靠外键表达）。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": "创建工作流任务表 ent_workflow_task（固定前置条件 + 所需证据 + 执行代次）",
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_workflow_task` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单（ent_assignment.id，服务层保证）',
  `task_type` VARCHAR(32) NOT NULL
    COMMENT 'collect_documents/quote/purchase/contract/execution/handover/settlement',
  `title` VARCHAR(128) NOT NULL COMMENT '任务标题',
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending'
    COMMENT 'pending/in_progress/waiting/done/cancelled',
  `assignee_user_id` BIGINT NULL COMMENT '负责人（未指派保持 NULL，不静默补默认人）',
  `due_at` DATETIME NULL COMMENT '到期时间（未知保持 NULL，不编造）',
  `precondition_task_id` BIGINT UNSIGNED NULL
    COMMENT '固定前置条件：同委托内的单个前置任务（自依赖/循环由服务层拒绝）',
  `required_evidence` TEXT NULL COMMENT '所需证据类型 JSON 数组，空表示不强制',
  `evidence_refs` TEXT NULL COMMENT '完成时提交的证据 JSON 数组 [{kind,ref}]',
  `wait_reason` VARCHAR(255) NULL COMMENT '进入 waiting 的原因（缺件说明）',
  `reopen_count` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'reopen 次数（历史不删除）',
  `last_reopen_reason` VARCHAR(255) NULL COMMENT '最近一次 reopen 原因',
  `lease_generation` INT UNSIGNED NOT NULL DEFAULT 0
    COMMENT '执行代次：接管/改派 +1，旧代次提交被拒（R9 fencing）',
  `revision` INT UNSIGNED NOT NULL DEFAULT 1
    COMMENT '乐观锁版本号：命令带 expected_revision，不匹配即冲突',
  `created_by` BIGINT NOT NULL COMMENT '创建人（组织成员用户）',
  `started_at` DATETIME NULL,
  `completed_at` DATETIME NULL,
  `cancelled_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_task_assignment` (`assignment_id`, `status`),
  KEY `idx_ent_task_assignee` (`assignee_user_id`, `status`),
  KEY `idx_ent_task_precondition` (`precondition_task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_workflow_task` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `task_type` TEXT NOT NULL,
  `title` TEXT NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'pending',
  `assignee_user_id` INTEGER NULL,
  `due_at` TEXT NULL,
  `precondition_task_id` INTEGER NULL,
  `required_evidence` TEXT NULL,
  `evidence_refs` TEXT NULL,
  `wait_reason` TEXT NULL,
  `reopen_count` INTEGER NOT NULL DEFAULT 0,
  `last_reopen_reason` TEXT NULL,
  `lease_generation` INTEGER NOT NULL DEFAULT 0,
  `revision` INTEGER NOT NULL DEFAULT 1,
  `created_by` INTEGER NOT NULL,
  `started_at` TEXT NULL,
  `completed_at` TEXT NULL,
  `cancelled_at` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS `idx_ent_task_assignment`
  ON `ent_workflow_task` (`assignment_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_task_assignee`
  ON `ent_workflow_task` (`assignee_user_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_task_precondition`
  ON `ent_workflow_task` (`precondition_task_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_workflow_task`"],
    },
]
