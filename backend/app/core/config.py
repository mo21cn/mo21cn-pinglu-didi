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
        env_file=f".env.{APP_ENV}",
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
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_NAME: str = "pinglu_didi"
    DB_USER: str = "root"
    DB_PASSWORD: str = ""

    # ---- Redis ----
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # ---- 微信生态 ----
    WX_APP_ID: str = ""
    WX_APP_SECRET: str = ""
    WX_MCH_ID: str = ""
    WX_PAY_KEY: str = ""

    # ---- 撮合引擎 ----
    MATCH_STAGE1_TIMEOUT_MS: int = 200

    # ---- 智能体 ----
    LLM_GATEWAY_URL: str = ""
    VECTOR_DB_URL: str = ""

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


@lru_cache
def get_settings() -> Settings:
    """进程内单例配置（惰性加载）。"""
    return Settings()
