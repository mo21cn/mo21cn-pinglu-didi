"""用户模型（F1 三角色登录）。

三合一 MVP 的角色枚举：
- shipper 货主 / owner 船东 / port 港口方

一个微信用户（openid 唯一）可绑定多个角色，登录时选定当前角色。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """全局 ORM 基类。"""


class User(Base):
    """平台用户（以微信 openid 为主键关联）。"""

    __tablename__ = "users"

    # SQLite（开发/测试）无 BigInteger 自增，用 Integer 变体兼容
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    openid: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="微信 openid")
    unionid: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="微信 unionid")
    nickname: Mapped[str] = mapped_column(String(64), default="", comment="昵称")
    avatar: Mapped[str] = mapped_column(String(512), default="", comment="头像 URL")
    phone: Mapped[str] = mapped_column(String(20), default="", comment="实名手机号（认证后回填）")
    # 角色列表，如 ["shipper", "owner"]；JSON 便于 MVP 阶段扩展（后期迁独立关联表）
    roles: Mapped[list] = mapped_column(JSON, default=list, comment="已绑定角色列表")
    current_role: Mapped[str] = mapped_column(String(16), default="shipper", comment="当前活跃角色")
    status: Mapped[str] = mapped_column(String(16), default="active", comment="active|banned")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    def bind_role(self, role: str) -> None:
        """绑定角色（幂等）。"""
        if role not in (self.roles or []):
            self.roles = [*(self.roles or []), role]
