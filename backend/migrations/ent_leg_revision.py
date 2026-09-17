"""航段版本表（`ent_leg_revision`）—— 「改段留版本」的载体。

口径（HO 2026-09-17 裁定，三项一起看）
--------------------------------------
① **谁能建段**：任意验收者/测试者。落地＝**参与方**判据（该委托的货主本人，
   或该委托所属组织的 active 成员）⇒ **不新增权限常量、不设角色门槛**。
   ⛔ 但**不等于**"任何登录用户" —— 组织边界与货主归属仍是硬界（非参与方一律 404）。
② **不强制 公–水–公**：不校验 `mode` 的取值组合与顺序。合同要的是"road–water–road
   这个**方案**能被展示"，把它焊成写入口的约束，会让"两段公路"或"公路—铁路"
   这类合法方案**根本存不进来** —— 而 §10.1 第 4 步要的是"能展示方案"，
   不是"只能有一种方案"。⇒ 写入口只做**结构完整性**校验
   （`mode` 非空、起终点非空、委托内 `seq` 唯一且 ≥1），
   **不做**"必须三段的某种顺序"这类形式约束。
③ **改段留版本 = 保留**：本表 append-only —— 每建/改一次航段写一行；
   `ent_leg` 继续承载**当前**状态，它的 `UNIQUE (assignment_id, seq)` **一个字不动**。

为什么另开一张表，而不是给 `ent_leg` 加一个 `revision_no`
--------------------------------------------------------
`ent_leg` 的 `UNIQUE (assignment_id, seq)` 是**内联在建表语句里**的（两种方言都如此），
要让它容纳"同一个 `seq` 的多个版本"就必须**重建表** —— 那就越过了"迁移只增不改"
这条线。⇒ 用"当前表 + append-only 版本表"这一对，
与 `ent_artifact` + `ent_artifact_revision` 同型（本仓库已有的版本模式）。

`change_kind` 只有两个取值
---------------------------
`created` / `updated`。**没有 `deleted`**：删除航段不在本次裁定范围内
（裁定问的是"改段是否留版本"）。少一个取值比多一个猜测好 —— 真要支持删除，
得先裁定"被删掉的那一段上挂着的候选运力怎么办"（`ent_capacity_candidate.leg_id`
会指向一个不再存在的段）。

⚠️ 本迁移**不声称**任何验收通过；它只让"建段 / 改段"有地方落。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建航段版本表 ent_leg_revision（append-only：每建/改一次航段写一行，"
            "承载「改段留版本」；当前状态仍在 ent_leg，其唯一键不动）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_leg_revision` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `leg_id` BIGINT UNSIGNED NOT NULL COMMENT '对应航段（ent_leg.id）',
  `assignment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属委托单（按单查历史时不必回连 ent_leg）',
  `seq` INT NOT NULL COMMENT '该版本当时的航段顺序',
  `mode` VARCHAR(16) NOT NULL COMMENT '该版本当时的运输方式（road/water/rail 或其他；写入口不强制枚举，未知保持未知）',
  `from_name` VARCHAR(64) NOT NULL COMMENT '该版本当时的起点名称',
  `to_name` VARCHAR(64) NOT NULL COMMENT '该版本当时的终点名称',
  `revision_no` INT NOT NULL COMMENT '航段内自 1 起的版本号，与 leg_id 一起唯一',
  `change_kind` VARCHAR(16) NOT NULL COMMENT 'created（建段）/ updated（改段）',
  `change_note` VARCHAR(255) NULL COMMENT '改动说明（可空：留版本是硬要求，写理由不是）',
  `actor_user_id` BIGINT UNSIGNED NULL COMMENT '发起人（0=系统/种子；留痕用，不参与判权）',
  `created_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_leg_revision_leg_no` (`leg_id`, `revision_no`),
  KEY `idx_ent_leg_revision_assignment` (`assignment_id`, `seq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_leg_revision` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `leg_id` INTEGER NOT NULL,
  `assignment_id` INTEGER NOT NULL,
  `seq` INTEGER NOT NULL,
  `mode` TEXT NOT NULL,
  `from_name` TEXT NOT NULL,
  `to_name` TEXT NOT NULL,
  `revision_no` INTEGER NOT NULL,
  `change_kind` TEXT NOT NULL,
  `change_note` TEXT NULL,
  `actor_user_id` INTEGER NULL,
  `created_at` TEXT NOT NULL,
  UNIQUE (`leg_id`, `revision_no`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_leg_revision_assignment`
  ON `ent_leg_revision` (`assignment_id`, `seq`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_leg_revision`"],
    },
]
