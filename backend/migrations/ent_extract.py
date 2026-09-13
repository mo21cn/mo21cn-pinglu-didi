"""附件提取文本表 —— S1 第 6 条「文档提取」的数据底座（ENT-013 / AC-17）。

为什么文本单独一张表
--------------------
`ent_attachment` 存的是"文件这件事"（大小、类型、哈希、存储键、提取状态），
提取出来的**文本**是另一回事：

* **体积差三个数量级**：一份报价 PDF 可能 2 MiB，提取文本可能 3 千字；
  合在附件表里会让"列附件列表"这种高频读顺带拖着几万字的正文；
* **生命周期不同**：文件不重新上传就不会变，文本**可以重抽**（换了提取规则、
  或先失败后成功、或由人工转录覆盖）—— 分开后重抽就是覆盖一行，不必动附件元数据；
* **来源必须可分**：机读提取与**人工转录**在可信度上不是一回事。UI 与 Agent
  都必须能区分（`source`），所以它是这一行的属性，而不是附件本体的属性。

1:1 但**不是外键强制**（与全支线一致：`ent_` 表用应用层保证引用完整性，
不建 FK —— 见 BASE-001 的教训：MySQL 与 SQLite 的 FK 类型/行为差异会咬人）。
唯一键 `UNIQUE(attachment_id)` 保证"一个附件最多一份当前文本"。

`truncated` 为什么必须显式存
----------------------------
提取有上限（`EXTRACTION_MAX_TEXT_CHARS`）。被截断的文本**看起来完整**——
末尾就是一句话的结尾，没人能看出后面少了两千字。所以"有没有被截断"必须落进数据，
让 UI 能提示、让 Agent 的引用能被质疑。**静默截断比报错更危险。**

`content` 是不可信数据（AC-18）
------------------------------
附件里的任何文字（包括"请忽略之前的指令"这类）都是**证据材料**，不是指令。
本表只存文本，不含任何解释/执行语义 —— 提取器不执行、不渲染、不解释内容。

表名 `ent_` 前缀 = 由迁移管理，不进 `Base.metadata.create_all`。
`checks` 按条目执行，每条只校验自己创建的表。
"""

from __future__ import annotations

migrations = [
    {
        "id": 1,
        "description": (
            "创建附件提取文本表 ent_attachment_text（1:1、来源可分、截断显式、可重抽覆盖）"
        ),
        "sql": {
            "mysql": """
CREATE TABLE IF NOT EXISTS `ent_attachment_text` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `attachment_id` BIGINT UNSIGNED NOT NULL
    COMMENT '所属附件（ent_attachment.id）；UNIQUE 保证一个附件只有一份当前文本',
  `content` LONGTEXT NOT NULL
    COMMENT '提取或人工转录的文本（不可信数据：只是证据材料，不是指令）',
  `char_count` INT UNSIGNED NOT NULL COMMENT '字符数（不含首尾空白归一后的实际长度）',
  `truncated` TINYINT NOT NULL DEFAULT 0
    COMMENT '是否因上限被截断；静默截断比报错更危险，故必须显式保存',
  `source` VARCHAR(24) NOT NULL
    COMMENT 'extractor（机读提取）/ manual_transcription（人工转录）—— 可信度不同',
  `sha256` CHAR(64) NOT NULL COMMENT '文本内容哈希（判断是否与上次提取一致）',
  `created_by` BIGINT NULL COMMENT '人工转录者；抽取器写入时为 NULL（机器没有"作者"）',
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ent_attachment_text_attachment` (`attachment_id`),
  KEY `idx_ent_attachment_text_source` (`source`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            "sqlite": """
CREATE TABLE IF NOT EXISTS `ent_attachment_text` (
  `id` INTEGER PRIMARY KEY AUTOINCREMENT,
  `attachment_id` INTEGER NOT NULL,
  `content` TEXT NOT NULL,
  `char_count` INTEGER NOT NULL,
  `truncated` INTEGER NOT NULL DEFAULT 0,
  `source` TEXT NOT NULL,
  `sha256` TEXT NOT NULL,
  `created_by` INTEGER NULL,
  `created_at` TEXT NOT NULL,
  `updated_at` TEXT NOT NULL,
  UNIQUE (`attachment_id`)
);

CREATE INDEX IF NOT EXISTS `idx_ent_attachment_text_source`
  ON `ent_attachment_text` (`source`, `updated_at`)
""",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_attachment_text`"],
    },
    {
        "id": 2,
        "description": (
            "对齐 ent_attachment.extract_status 的 MySQL 列注释取值域"
            "（新增 needs_transcription；SQLite 无列注释，占位保持方言成对）"
        ),
        "sql": {
            # 列注释是数据库里**唯一**的取值域文档。新增状态后不同步，
            # 下一个读库的人就会按旧注释判断"这个值不可能出现"。
            "mysql": """
ALTER TABLE `ent_attachment` MODIFY COLUMN `extract_status` VARCHAR(24) NOT NULL
  DEFAULT 'not_requested'
  COMMENT 'not_requested/pending/running/done/failed/unsupported/needs_transcription'
""",
            # SQLite 不支持列注释，且 extract_status 上没有 CHECK 约束 → 无结构可改。
            # 显式写一条无害语句，而不是给 SQLite 缺方言（缺方言执行器会直接报错，
            # 见 migrate.py 的 resolve_sql：方言缺失即失败，不允许静默跳过）。
            "sqlite": "SELECT 1",
        },
        "checks": ["SELECT COUNT(*) FROM `ent_attachment`"],
    },
]
