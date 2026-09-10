"""船域（船舶备案）业务逻辑（F3）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ship import Ship
from app.modules.ship.schemas import ShipCreate, ShipUpdate


class ShipStateError(Exception):
    """状态机非法转移。"""


def create_ship(db: Session, owner_id: int, data: ShipCreate) -> Ship:
    """船舶备案：新建即 pending_verify（待平台审核）。"""
    ship = Ship(
        owner_id=owner_id,
        ship_name=data.ship_name,
        ship_type=data.ship_type,
        deadweight_t=data.deadweight_t,
        length_m=data.length_m,
        width_m=data.width_m,
        draft_m=data.draft_m,
        home_port=data.home_port,
        cert_no=data.cert_no,
        cert_expiry=data.cert_expiry,
        status="pending_verify",
    )
    db.add(ship)
    db.commit()
    db.refresh(ship)
    return ship


def update_ship(db: Session, ship: Ship, data: ShipUpdate) -> Ship:
    """编辑备案：verified 状态下修改尺度/证书等关键信息 → 回到 pending_verify 重审。"""
    if ship.status == "rejected":
        raise ShipStateError("已驳回的备案不可编辑，请重新提交新备案")
    changes = data.model_dump(exclude_unset=True)
    critical = ("ship_name", "ship_type", "deadweight_t", "length_m", "width_m", "draft_m",
                "cert_no", "cert_expiry")
    touched_critical = any(field in changes for field in critical)
    for field, value in changes.items():
        setattr(ship, field, value)
    # 已通过审核的船改了关键信息 → 降级重审（保证撮合池数据可信）
    if ship.status == "verified" and touched_critical:
        ship.status = "pending_verify"
        ship.reject_reason = ""
    db.commit()
    db.refresh(ship)
    return ship


def verify_ship(db: Session, ship: Ship, approved: bool, reason: str = "") -> Ship:
    """审核：pending_verify → verified / rejected。"""
    if ship.status != "pending_verify":
        raise ShipStateError(f"当前状态 {ship.status} 不可审核")
    if approved:
        ship.status = "verified"
        ship.reject_reason = ""
    else:
        ship.status = "rejected"
        ship.reject_reason = reason or "未通过审核"
    db.commit()
    db.refresh(ship)
    return ship


def list_my_ships(
    db: Session, owner_id: int, ship_status: str | None = None, page: int = 1, size: int = 20
) -> tuple[int, list[Ship]]:
    """船东自己的船队列表（分页）。"""
    stmt = select(Ship).where(Ship.owner_id == owner_id)
    if ship_status:
        stmt = stmt.where(Ship.status == ship_status)
    total = len(db.execute(stmt).scalars().all())
    items = list(
        db.execute(
            stmt.order_by(Ship.id.desc()).offset((page - 1) * size).limit(size)
        ).scalars().all()
    )
    return total, items


def list_pending_ships(db: Session, page: int = 1, size: int = 20) -> tuple[int, list[Ship]]:
    """待审核船舶列表（平台侧）。"""
    stmt = select(Ship).where(Ship.status == "pending_verify")
    total = len(db.execute(stmt).scalars().all())
    items = list(
        db.execute(stmt.order_by(Ship.id.asc()).offset((page - 1) * size).limit(size))
        .scalars()
        .all()
    )
    return total, items


def get_ship(db: Session, ship_id: int) -> Ship | None:
    return db.get(Ship, ship_id)
