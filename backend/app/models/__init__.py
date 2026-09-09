"""数据模型包。"""
from app.models.cargo import Cargo
from app.models.ship import Ship
from app.models.user import Base, User

__all__ = ["Base", "Cargo", "Ship", "User"]
