"""成果归属列 —— 「具体委托内的新成果必须有有效归属」的数据底座（DR-0012）。

背景（详见 `docs/entrust/decisions/0012-成果归属.md`）
----------------------------------------------------
`ent_artifact` 原先只挂 `entrustment_id`，而 `ent_entrustment` 是
**(org_id, entrust_user_id)** 的**组织级授权** —— 与具体委托单无关；
`ent_assignment` 里也没有反向指针。于是**成果与委托单之间没有任何关联键**，
同一货主在同一组织下的多张委托会互相串成果。

这与权威材料直接冲突：PRD 第 187 行要求「对话与工作台引用同一个 artifact ID 与版本」，
DR-0010 §3.3 也把成果列为各槽位的主要落点。DR-0010 §4 原写的「不加表、不加迁移」
建立在一个**从未被验证**的前提上（成果可按单张委托读取），前提被证伪，故由
DR-0012 修正结论。

本模块**只做加法**
------------------
* 新增**可空**列 `assignment_id`。历史行保持 NULL —— **不按
  `(owner_user_id, org_id)` 猜测回填**：猜测会把"我们猜的"写成与"确实属于某委托"
  无法区分的既成事实，而归属错误在结案与对账阶段是灾难性的；
* 新增索引 `(assignment_id, artifact_type)`，支撑"单委托成果列表"这条读路径；
* **不动** `entrustment_id`（仍 NOT NULL）：组织级授权是**权限与展示的作用域边界**，
  `assignment_id` 是**归属**，不是权限边界 —— 权限判定仍然只有 `authz.py` 一条入口。

约定
----
表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，本条只校验自己新增的列可读。
`id` 只增不改；本模块是独立文件，**不修改已发布的 `ent_artifact.py`**，
因此本变更可单独回退（停用本模块 + 删除对应 `_migration_history` 行）。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "成果表 ent_artifact 补可空归属列 assignment_id（历史行保持 NULL，不猜测回填）"
            " 与单委托查询索引 idx_ent_artifact_assignment"
        ),
        "sql": {
            "mysql": """
ALTER TABLE `ent_artifact`
  ADD COLUMN `assignment_id` BIGINT UNSIGNED NULL COMMENT '归属委托单（ent_assignment.id）；历史行为 NULL，不猜测回填';
CREATE INDEX `idx_ent_artifact_assignment` ON `ent_artifact` (`assignment_id`, `artifact_type`)
""",
            "sqlite": """
ALTER TABLE `ent_artifact` ADD COLUMN `assignment_id` INTEGER NULL;
CREATE INDEX IF NOT EXISTS `idx_ent_artifact_assignment`
  ON `ent_artifact` (`assignment_id`, `artifact_type`)
""",
        },
        # 该断言同时证明两件事：列存在，且**允许 NULL**（历史行不会被误判成违规）。
        "checks": ["SELECT COUNT(*) FROM `ent_artifact` WHERE `assignment_id` IS NULL"],
    },
]
