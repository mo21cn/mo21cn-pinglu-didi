"""变更复核传播 —— A2 五之二（验证 19 / AC-12）的数据底座。

背景（详见 `docs/entrust/decisions/0016-变更影响映射.md`）
------------------------------------------------------
PRD 第 285 行：**成果的依赖以 ID 与版本记录；相关事实变化时，把受影响的当前草稿／结果
标 `needs_revalidation`，展示**原因**与**复核任务**；不得改写已接受／已签／已执行的历史。**

DR-0016 已把「哪五类变更 → 哪些对象进入复核范围」冻结为五行映射。本模块只建**载体**，
映射本身在服务层（`app/modules/entrust/revalidation.py`）照抄 DR-0016 —— **不在此另存一份**，
否则两处迟早漂移（与 `ent_exception.py` 不另存取值域同一条理由）。

两张改动
--------
1. `ent_exception` 补**可空**列 `change_category`：变更类别（DR-0016 五行的机器可核对载体）。
   含**真实异常**在内的历史行保持 NULL —— **不猜测回填**：猜错类别会让"复核范围"
   整表选错，而复核范围选错是**静默**的（任务照样生成，只是复查了不相干的对象）。
   历史行保持 NULL 也保留了「这单当时没登记类别」这个事实（与 DR-0012 的
   `assignment_id` 同一条纪律：**未知保持未知**）。
2. 新建 `ent_revalidation`：**一行 = 一个待复核项**。

为什么一行是「待复核项」而不是「成果」
--------------------------------------
验证 19 有两句要求，一句落在成果上（标 `needs_revalidation`），一句落在任务上（生成复核任务）：

* **成果侧的标记**由本表**派生** —— 某成果"需要复核" ⇔ 存在一条
  `target_kind='artifact' AND target_id=<该成果> AND status='open'` 的行。
  刻意**不在 `ent_artifact` 上加布尔列**：那是一份**可以与本表不一致**的第二真相
  （表里有待复核项、列却写着 false，两边都"看起来对"）。
* **任务侧**：每行都指向**它自己**生成的复核任务（`review_task_id`）。
  DR-0016 §4.4 要求复核任务"至少记录：来源变更、目标对象、目标版本、复核区域"，
  且**不能只把这些写进任务标题** ⇒ 结构化关联就落在本行的
  `exception_id` / `target_kind`+`target_id` / `target_revision_id` / `area` 四组字段上。

`review_key` 为什么存在
----------------------
幂等键。`UNIQUE (exception_id, review_key)` 保证**同一案件的同一复核项只生成一次** ——
重放应用（A7 "成功重试不重复副作用"）时不会多出一批复核任务。
取值形如 `artifact:12` / `area:execution`。用**显式字符串**而不是
`UNIQUE(exception_id, area, IFNULL(target_id,0))`：后者在 MySQL 与 SQLite 上
对 NULL 的判重语义不同，会让幂等变成方言相关。

历史不被改写
------------
本模块**只建表**；「不改写已接受／已签／已执行历史」由服务层保证 ——
传播只**追加**（标记 + 任务），不 UPDATE 任何 `ent_artifact_revision`
（revision 表本就是 append-only），也不动 `ent_artifact.current_revision_id`。

约定
----
表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`（DR-0001）。
`id` 只增不改；本模块是独立文件，不修改任何已发布迁移，因此可单独回退。
`checks` 按条目执行，每条只校验自己创建的表/列。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "案件表 ent_exception 补可空列 change_category"
            "（DR-0016 五类变更的机器可核对载体；历史行保持 NULL，不猜测回填）"
        ),
        "sql": {
            "mysql": """
ALTER TABLE `ent_exception`
  ADD COLUMN `change_category` VARCHAR(32) NULL
    COMMENT '变更类别（DR-0016 五行）：cargo_quantity_category/origin_destination_mode/loading_delivery_window/selected_supplier_quote/approved_extra_charge；exception 类与历史行为 NULL'
""",
            "sqlite": """
ALTER TABLE `ent_exception` ADD COLUMN `change_category` TEXT NULL
""",
        },
        # 该断言同时证明两件事：列存在，且**允许 NULL**（历史行不会被误判成违规）。
        "checks": ["SELECT COUNT(*) FROM `ent_exception` WHERE `change_category` IS NULL"],
    },
    {
        "id": 2,
        "description": (
            "创建变更复核表 ent_revalidation"
            "（一行 = 一个待复核项；结构化关联含来源变更/目标对象/目标版本/复核区域；"
            "review_key 幂等）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_revalidation` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `exception_id` BIGINT UNSIGNED NOT NULL COMMENT '来源变更（ent_exception.id）',
  `review_key` VARCHAR(64) NOT NULL COMMENT '幂等键：artifact:<id> / area:<task_type>',
  `target_kind` VARCHAR(16) NULL COMMENT '目标对象类型 artifact/task；无具体对象时为 NULL（DR-0016 §4.3）',
  `target_id` BIGINT UNSIGNED NULL COMMENT '目标对象 id；与 target_kind 同时为空或同时非空',
  `target_revision_id` BIGINT UNSIGNED NULL
    COMMENT '目标版本（成果的精确 revision id）；任务目标与无对象时 NULL，不编造',
  `area` VARCHAR(120) NOT NULL COMMENT '复核区域（DR-0016 §3 的复核内容标签）',
  `task_type` VARCHAR(32) NOT NULL
    COMMENT '复核任务类型，复用现有 7 类；其用途为复核而非新增运输作业（DR-0016 §4.4）',
  `review_task_id` BIGINT UNSIGNED NOT NULL COMMENT '本项生成的复核任务（ent_workflow_task.id）',
  `status` VARCHAR(16) NOT NULL DEFAULT 'open' COMMENT 'open=待复核 / resolved=已复核',
  `note` TEXT NULL COMMENT '追加说明（原因等）',
  `created_at` DATETIME NOT NULL,
  `resolved_at` DATETIME NULL COMMENT '复核完成时间（未知保持 NULL）',
  `resolved_by` BIGINT NULL COMMENT '复核人（未知保持 NULL）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_revalidation_key` (`exception_id`, `review_key`),
  KEY `idx_ent_revalidation_target` (`target_kind`, `target_id`, `status`),
  KEY `idx_ent_revalidation_case` (`exception_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_revalidation` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `exception_id` INTEGER NOT NULL,
  `review_key` TEXT NOT NULL,
  `target_kind` TEXT NULL,
  `target_id` INTEGER NULL,
  `target_revision_id` INTEGER NULL,
  `area` TEXT NOT NULL,
  `task_type` TEXT NOT NULL,
  `review_task_id` INTEGER NOT NULL,
  `status` TEXT NOT NULL DEFAULT 'open',
  `note` TEXT NULL,
  `created_at` TEXT NOT NULL,
  `resolved_at` TEXT NULL,
  `resolved_by` INTEGER NULL,
  UNIQUE (`exception_id`, `review_key`)
);
CREATE INDEX IF NOT EXISTS `idx_ent_revalidation_target`
  ON `ent_revalidation` (`target_kind`, `target_id`, `status`);
CREATE INDEX IF NOT EXISTS `idx_ent_revalidation_case`
  ON `ent_revalidation` (`exception_id`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_revalidation`"],
    },
    {
        "id": 3,
        "description": (
            "对齐 ent_exception_event.event_kind 的 MySQL 列注释取值域"
            "（新增 revalidation_planned；SQLite 无列注释，占位保持方言成对）"
        ),
        "sql": {
            # 列注释是数据库里**唯一**的取值域文档。新增事件类型后不同步，
            # 下一个读库的人就会按旧注释判断"这个值不可能出现"。
            "mysql": """
ALTER TABLE `ent_exception_event` MODIFY COLUMN `event_kind` VARCHAR(24) NOT NULL
  COMMENT 'created/status_changed/decided/link_added/link_removed/applied/applied_rejected/revalidation_planned/closed/reopened'
""",
            # SQLite 不支持列注释，且 event_kind 上没有 CHECK 约束 → 无结构可改。
            # 显式写一条无害语句，而不是给 SQLite 缺方言（缺方言执行器会直接报错）。
            "sqlite": "SELECT 1",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_exception_event`"],
    },
]
