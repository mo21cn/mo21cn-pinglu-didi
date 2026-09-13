"""应用配置 —— 三环境隔离的核心。

环境切换机制：
- 通过环境变量 APP_ENV 指定目标环境（development | test | production）。
- 对应加载 `.env.{APP_ENV}` 文件（默认 development）。
- 敏感值（密钥、密码）不写入 .env 文件，由 CI/CD secrets 或部署环境注入覆盖。

使用：
    from app.core.config import get_settings
    settings = get_settings()   # 进程内单例，惰性加载
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 目标环境：由环境变量 APP_ENV 决定，默认 development
APP_ENV = os.getenv("APP_ENV", "development").lower()
assert APP_ENV in {"development", "test", "production"}, f"未知环境: {APP_ENV}"


class Settings(BaseSettings):
    """全局配置模型。字段可从对应 `.env.{APP_ENV}` 或系统环境变量读取。"""

    model_config = SettingsConfigDict(
        # 多文件叠加：.env.{APP_ENV} 为环境基线，.env.local 为本机敏感值最高优先级覆盖
        # （.env.local 已在 .gitignore，不入库；列表越靠后优先级越高）
        env_file=(f".env.{APP_ENV}", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 基础 ----
    APP_NAME: str = "pinglu-didi"
    APP_ENV: str = APP_ENV
    DEBUG: bool = False
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # ---- 服务 ----
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ---- 数据库（MySQL） ----
    # 生产为 MySQL；开发/测试可通过 DATABASE_URL 覆盖为 SQLite（免装 MySQL 即可跑通）
    DATABASE_URL: str = ""  # 非空时优先生效，如 sqlite:///./pinglu_didi_dev.db
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_NAME: str = "pinglu_didi"
    DB_USER: str = "root"
    DB_PASSWORD: str = ""

    # ---- 数据库迁移 ----
    # 由 backend/migrations/ 管理的表名前缀；这些表不进 create_all，
    # 必须经 `python migrate.py` 创建（见 docs/06-database-migration.md）
    MIGRATION_MANAGED_TABLE_PREFIX: str = "ent_"

    # ---- 委托发货支线（AC-22） ----
    # 特性开关：关闭时 /api/v1/entrust 全部端点返回 404（隐藏入口，不删除已有数据）。
    # 开关与服务端权限相互独立：开启后每个端点仍各自做叠加层权限校验。
    ENTRUST_ENABLED: bool = False

    # ---- 附件（S1 第 5 条）----
    # 20 MiB 上限**可配置**（计划 §3.2 建模红线），不是硬编码常量。
    ATTACHMENT_MAX_BYTES: int = 20 * 1024 * 1024
    # 存储目录：**公共静态路径之外**（默认 backend/var/attachments，.gitignore 排除）。
    # 相对路径按 backend/ 解析；下载必须走授权端点，不允许任何静态目录直出。
    ATTACHMENT_STORAGE_DIR: str = "var/attachments"
    # 允许的 MIME 白名单（逗号分隔）。空字符串表示不限制 —— 生产不应留空。
    ATTACHMENT_ALLOWED_TYPES: str = (
        "text/plain,text/csv,text/markdown,application/pdf,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.ms-excel,image/jpeg,image/png,image/webp"
    )

    # ---- 文档提取（S1 第 6 条 / ENT-013）----
    # 单份附件提取文本的字符上限。**被截断的文本看起来是完整的**，所以超限时必须
    # 落 `truncated=1`，不能只写日志。上限存在的意义是防止单份文件撑爆提示词与响应。
    EXTRACTION_MAX_TEXT_CHARS: int = 200_000
    # 附件文本进入 Agent 上下文时的字符上限（比落库上限更严：
    # 提示词要给任务、成果、来源目录都留位置，不能一段报价文本独占）。
    AGENT_ATTACHMENT_TEXT_CHARS: int = 8_000

    # ---- 认证（JWT） ----
    JWT_SECRET_KEY: str = "change-me-in-env"  # 生产由 CI/CD secrets 注入
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 12  # access token 有效期（12 小时）

    # ---- Redis ----
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # ---- 微信生态 ----
    WX_APP_ID: str = ""
    WX_APP_SECRET: str = ""
    WX_MCH_ID: str = ""
    WX_PAY_KEY: str = ""
    # Mock 开关：true 时不真实调用 code2session（本地开发/CI 无 appid 时使用）
    WECHAT_MOCK: bool = False

    # ---- 撮合引擎 ----
    MATCH_STAGE1_TIMEOUT_MS: int = 200

    # ---- 智能体 ----
    LLM_GATEWAY_URL: str = ""
    VECTOR_DB_URL: str = ""
    # LLM 供应商（DeepSeek 等 OpenAI 兼容协议；LLM_MOCK=true 时走规则模板，供无 Key 开发/CI）
    LLM_PROVIDER: str = "deepseek"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_MOCK: bool = False
    LLM_TIMEOUT_SECONDS: int = 60

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


@lru_cache
def get_settings() -> Settings:
    """进程内单例配置（惰性加载）。"""
    return Settings()
