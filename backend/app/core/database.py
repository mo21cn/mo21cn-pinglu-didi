"""SQLAlchemy 数据库引擎与会话管理。

- 引擎按环境惰性创建（见 app.core.config.database_url）。
- 提供 `get_db` FastAPI 依赖：每请求一会话，请求结束自动关闭。
- 测试环境可用 SQLite（内存或文件），通过 DATABASE_URL 覆盖。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

connect_args = {}
if settings.database_url.startswith("sqlite"):
    # SQLite 需要关闭同线程检查（FastAPI 多线程访问）
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
