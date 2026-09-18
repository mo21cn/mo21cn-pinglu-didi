"""委托货量变更历史表（`ent_assignment_quantity_change`）—— 「保留原历史」的载体。

为什么另开一张表（而不是只改 `ent_assignment.quantity`）
------------------------------------------------------
`ent_assignment.quantity` 只有**当前值**。把 800 改成 950 之后，「原来是 800」
这句话在库里**无处可查** —— 而 D1-09 要的恰恰是「同一张委托上看到 800 → 950」
这条**对照**。审计链上只剩 950，"缺 150 吨" 这件事就没法被复核，
也无法回答「复核是按哪个数判的」。

与航段同型：`ent_leg` 承载**当前**状态，`ent_leg_revision` append-only 留版本
（本仓已有的模式，见 `ent_leg_revision.py` 的同一段理由）。
`ent_assignment` 的列**一个字不动** —— 迁移只增不改。

为什么 `exception_id` 是唯一键
------------------------------
本表一行 = **一次经审批的变更应用**，不是"某次随便改了数"。
`UNIQUE (exception_id)` 让「同一个案件应用两次」在**数据库层**就写不进去 ——
应用入口（`apply_case`）的状态机已拦一次，这里是第二道，防的是绕过服务层的写入。
`base_revision` 单独存（而不是靠 `exception_id` 反查）：它记的是**变更前**委托的
乐观锁值，出问题时要能一眼看出"这一改是从第几版出发的"，而不是再去案件事件链里拼。

`old_quantity` 可空、`new_quantity` 非空
----------------------------------------
旧值**未知就是 NULL**（同 `ent_assignment.quantity` 的口径：未知保持未知，
绝不静默补 0）—— 从"未知"改成一个确定的数是一次合法变更。
新值**必填**：没有新值的"变更"不是变更，不该占一行。

⚠️ 本迁移**不声称**任何验收通过；它只让「货量变更」这条动作有地方留痕。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建委托货量变更历史表 ent_assignment_quantity_change（append-only："
            "一次经审批的变更应用写一行，承载「保留原历史」；"
            "当前值仍在 ent_assignment.quantity，其列与索引一个字不动）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_assignment_quantity_change` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '委托单（ent_assignment.id）',
  `exception_id` BIGINT UNSIGNED NOT NULL COMMENT '来源变更案件（ent_exception.id）；唯一，防同一案件应用两次',
  `base_revision` INT UNSIGNED NOT NULL COMMENT '变更前委托的乐观锁值（应用以它做条件更新，能看出这一改从第几版出发）',
  `old_quantity` DECIMAL(14,3) NULL COMMENT '变更前货量（未知保持 NULL，绝不静默为 0）',
  `old_quantity_unit` VARCHAR(24) NULL COMMENT '变更前单位，与 old_quantity 成对',
  `new_quantity` DECIMAL(14,3) NOT NULL COMMENT '变更后货量（必填：没有新值的变更不是变更）',
  `new_quantity_unit` VARCHAR(24) NOT NULL COMMENT '变更后单位，与 new_quantity 成对',
  `basis` VARCHAR(255) NOT NULL COMMENT '变更依据（为什么改；来自批准快照，不由应用时临时给）',
  `applied_by` BIGINT UNSIGNED NULL COMMENT '应用人（0=系统/种子；留痕用，不参与判权）',
  `applied_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_assignment_quantity_change_case` (`exception_id`),
  KEY `idx_ent_assignment_quantity_change_assignment` (`assignment_id`, `applied_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_assignment_quantity_change` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `assignment_id` INTEGER NOT NULL,
  `exception_id` INTEGER NOT NULL,
  `base_revision` INTEGER NOT NULL,
  `old_quantity` NUMERIC(14,3) NULL,
  `old_quantity_unit` TEXT NULL,
  `new_quantity` NUMERIC(14,3) NOT NULL,
  `new_quantity_unit` TEXT NOT NULL,
  `basis` TEXT NOT NULL,
  `applied_by` INTEGER NULL,
  `applied_at` TEXT NOT NULL,
  UNIQUE (`exception_id`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_assignment_quantity_change_assignment`
  ON `ent_assignment_quantity_change` (`assignment_id`, `applied_at`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_assignment_quantity_change`"],
    },
]
