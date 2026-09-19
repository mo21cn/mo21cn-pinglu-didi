"""委托重开历史表（`ent_assignment_reopen`）—— 「撤销过一次结案」的载体。

为什么重开必须另留一张表
------------------------
`ent_assignment.completed_at` 承载**当前**状态：重开时它被清成 NULL（委托回到未结案）。
可是"**曾经结过案**"这句话在库里就**无处可查**了 —— 而合同要求
`retain the complete history`，设计 §5.5 Q2 也明说重开要"带理由 + 事件留痕"。

⇒ 本表一行 = **一次受控重开**：被撤销的那次结案（`previous_completed_at`）、
谁重开的（`actor_user_id`）、为什么（`reason`，**非空**）、当时基于哪一版（`previous_revision`）。
⛔ 迁移只增不改：撤销动作**不改**任何既有列的含义，`ent_assignment` 的列一个字不动。

`reason` 为什么非空
-------------------
没有理由的重开，在审计上等于"结论可以随时改"。它与 `completed → claimed` 这条边一起，
构成"受控"这个词的全部内容（另两条：要权限、要留痕）。

⚠️ 本迁移**不声称**任何验收通过；它只让「重开」这条动作有地方留痕。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建委托重开历史表 ent_assignment_reopen（append-only：一次受控重开写一行，"
            "承载「撤销了哪一次结案、为什么」；当前状态仍在 ent_assignment，其列一字不动）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_assignment_reopen` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '委托单（ent_assignment.id）',
  `actor_user_id` BIGINT UNSIGNED NULL COMMENT '执行重开的人（0=系统/种子；留痕用，不参与判权）',
  `reason` VARCHAR(512) NOT NULL COMMENT '重开理由（必填：没有理由的重开等于结论可以随时改）',
  `previous_completed_at` DATETIME NOT NULL COMMENT '被撤销的那次结案时间（撤销了什么，一眼可查）',
  `previous_revision` INT UNSIGNED NOT NULL COMMENT '重开前委托的乐观锁值（这一撤从第几版出发）',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ent_assignment_reopen_assignment` (`assignment_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_assignment_reopen` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `actor_user_id` INTEGER NULL,
  `reason` TEXT NOT NULL,
  `previous_completed_at` TEXT NOT NULL,
  `previous_revision` INTEGER NOT NULL,
  `created_at` TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS `idx_ent_assignment_reopen_assignment`
  ON `ent_assignment_reopen` (`assignment_id`, `created_at`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_assignment_reopen`"],
    },
]
