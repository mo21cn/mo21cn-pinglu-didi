"""货域（发货单）业务逻辑（F2）。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cargo import Cargo
from app.modules.cargo.schemas import CargoCreate, CargoUpdate


class CargoStateError(Exception):
    """状态机非法转移。"""


def create_cargo(db: Session, shipper_id: int, data: CargoCreate) -> Cargo:
    """创建发货单；publish_now=True 时直接进入 published。"""
    if data.expect_date < date.today():
        raise ValueError("期望装货日期不能早于今天")
    cargo = Cargo(
        shipper_id=shipper_id,
        cargo_name=data.cargo_name,
        cargo_type=data.cargo_type,
        weight_t=data.weight_t,
        volume_m3=data.volume_m3,
        origin_port=data.origin_port,
        dest_port=data.dest_port,
        expect_date=data.expect_date,
        offer_price=data.offer_price,
        remark=data.remark,
        status="published" if data.publish_now else "draft",
    )
    db.add(cargo)
    db.commit()
    db.refresh(cargo)
    return cargo


def update_cargo(db: Session, cargo: Cargo, data: CargoUpdate) -> Cargo:
    """编辑发货单（仅 draft 状态）。"""
    if cargo.status != "draft":
        raise CargoStateError("仅草稿状态的发货单可编辑")
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(cargo, field, value)
    if cargo.origin_port == cargo.dest_port:
        db.rollback()
        raise ValueError("起运港与目的港不能相同")
    if cargo.expect_date < date.today():
        db.rollback()
        raise ValueError("期望装货日期不能早于今天")
    db.commit()
    db.refresh(cargo)
    return cargo


def publish_cargo(db: Session, cargo: Cargo) -> Cargo:
    """发布：draft → published（进入撮合池）。"""
    if cargo.status != "draft":
        raise CargoStateError(f"当前状态 {cargo.status} 不可发布，仅草稿可发布")
    cargo.status = "published"
    db.commit()
    db.refresh(cargo)
    return cargo


def cancel_cargo(db: Session, cargo: Cargo) -> Cargo:
    """取消：draft/published → cancelled（matched 之后由订单模块接管）。"""
    if cargo.status not in ("draft", "published"):
        raise CargoStateError(f"当前状态 {cargo.status} 不可取消")
    cargo.status = "cancelled"
    db.commit()
    db.refresh(cargo)
    return cargo


def list_my_cargo(
    db: Session, shipper_id: int, status: str | None = None, page: int = 1, size: int = 20
) -> tuple[int, list[Cargo]]:
    """货主自己的发货单列表（分页）。"""
    stmt = select(Cargo).where(Cargo.shipper_id == shipper_id)
    if status:
        stmt = stmt.where(Cargo.status == status)
    total = len(db.execute(stmt).scalars().all())
    items = list(
        db.execute(stmt.order_by(Cargo.id.desc()).offset((page - 1) * size).limit(size))
        .scalars()
        .all()
    )
    return total, items


def get_owned(db: Session, cargo_id: int, shipper_id: int) -> Cargo | None:
    """取货主名下的发货单（越权返回 None）。"""
    return db.execute(
        select(Cargo).where(Cargo.id == cargo_id, Cargo.shipper_id == shipper_id)
    ).scalar_one_or_none()
