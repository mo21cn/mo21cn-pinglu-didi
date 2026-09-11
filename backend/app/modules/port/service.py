"""港域（泊位与泊位预约）业务逻辑（F4）。

防超卖设计（稀缺资源型实体的核心纪律）：
1. 预约申请（pending）不占档期——防恶意占位；
2. 港口方确认时才做容量校验：同一泊位、时间窗重叠的 confirmed
   预约数 < concurrent_capacity 才允许 confirm，否则 409；
3. MySQL 生产环境确认时对泊位行加 FOR UPDATE 行锁串行化并发确认；
   SQLite 测试环境单连接天然串行。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.port import Berth, BerthAppt
from app.models.ship import Ship
from app.modules.port.schemas import BerthApptCreate, BerthCreate, BerthUpdate


class PortStateError(Exception):
    """港域状态机/资源约束错误。"""


class BerthConflictError(Exception):
    """泊位档期冲突（防超卖拦截）。"""


# ---------- 泊位 ----------

def create_berth(db: Session, data: BerthCreate) -> Berth:
    """新建泊位（默认 active）。"""
    exists = db.execute(
        select(Berth).where(
            Berth.port_code == data.port_code, Berth.berth_no == data.berth_no
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise PortStateError(f"泊位 {data.port_code}-{data.berth_no} 已存在")
    berth = Berth(
        port_code=data.port_code,
        berth_no=data.berth_no,
        berth_name=data.berth_name,
        max_dwt=data.max_dwt,
        max_draft=data.max_draft,
        allowed_ship_types=list(data.allowed_ship_types),
        concurrent_capacity=data.concurrent_capacity,
        status="active",
    )
    db.add(berth)
    db.commit()
    db.refresh(berth)
    return berth


def update_berth(db: Session, berth: Berth, data: BerthUpdate) -> Berth:
    """编辑泊位。已有 confirmed 预约不受影响，新预约按新参数校验。"""
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(berth, field, value)
    db.commit()
    db.refresh(berth)
    return berth


def list_berths(
    db: Session, port_code: str | None = None, berth_status: str | None = None,
    page: int = 1, size: int = 20,
) -> tuple[int, list[Berth]]:
    """泊位列表（可按港/状态过滤）。"""
    stmt = select(Berth)
    if port_code:
        stmt = stmt.where(Berth.port_code == port_code)
    if berth_status:
        stmt = stmt.where(Berth.status == berth_status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = list(
        db.execute(stmt.order_by(Berth.id.asc()).offset((page - 1) * size).limit(size))
        .scalars()
        .all()
    )
    return total, items


def get_berth(db: Session, berth_id: int) -> Berth | None:
    return db.get(Berth, berth_id)


# ---------- 泊位预约 ----------

def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    """时间窗重叠判定（左闭右开语义：首尾相接不算重叠）。"""
    return start < other_end and end > other_start


def count_confirmed_overlap(db: Session, berth_id: int, start: datetime, end: datetime) -> int:
    """统计与 [start, end) 重叠的 confirmed 预约数。"""
    stmt = (
        select(func.count())
        .select_from(BerthAppt)
        .where(
            BerthAppt.berth_id == berth_id,
            BerthAppt.status == "confirmed",
            BerthAppt.plan_start < end,
            BerthAppt.plan_end > start,
        )
    )
    return db.execute(stmt).scalar_one()


def create_appt(db: Session, applier_id: int, data: BerthApptCreate) -> BerthAppt:
    """船东申请泊位预约。

    硬校验（撮合 Stage1 约束前置）：
    - 船舶存在、属于申请人、且 verified；
    - 泊位存在且 active；
    - ship.deadweight_t ≤ berth.max_dwt（吨位咬合）；
    - ship.draft_m ≤ berth.max_draft（吃水校验）；
    - ship.ship_type ∈ berth.allowed_ship_types（适靠船型）。
    """
    ship = db.get(Ship, data.ship_id)
    if ship is None or ship.owner_id != applier_id:
        raise PortStateError("船舶不存在或不属于当前船东")
    if ship.status != "verified":
        raise PortStateError("仅已通过审核的船舶可预约泊位")

    berth = db.get(Berth, data.berth_id)
    if berth is None:
        raise PortStateError("泊位不存在")
    if berth.status != "active":
        raise PortStateError("泊位已停用")

    # 吨位咬合（Numeric 与 float 比较）
    if float(ship.deadweight_t) > float(berth.max_dwt):
        raise PortStateError(
            f"船舶载重吨 {float(ship.deadweight_t):.0f}t 超过泊位上限 {float(berth.max_dwt):.0f}t"
        )
    if float(ship.draft_m) > float(berth.max_draft):
        raise PortStateError(
            f"船舶吃水 {float(ship.draft_m):.2f}m 超过泊位允许吃水 {float(berth.max_draft):.2f}m"
        )
    if ship.ship_type not in (berth.allowed_ship_types or []):
        raise PortStateError(f"泊位不适靠 {ship.ship_type} 型船舶")

    appt = BerthAppt(
        berth_id=data.berth_id,
        ship_id=data.ship_id,
        applier_id=applier_id,
        plan_start=data.plan_start,
        plan_end=data.plan_end,
        remark=data.remark,
        status="pending",
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt


def confirm_appt(db: Session, appt: BerthAppt) -> BerthAppt:
    """港口方确认预约——防超卖关键路径。

    对泊位行加 FOR UPDATE 锁（MySQL 生效），再统计重叠 confirmed 数；
    超过 capacity-1 即拒绝（本次确认后不得超过 capacity）。
    """
    if appt.status != "pending":
        raise PortStateError(f"当前状态 {appt.status} 不可确认")

    # 行锁泊位，串行化同泊位的并发确认（SQLite 单连接天然串行）
    berth = db.execute(
        select(Berth).where(Berth.id == appt.berth_id).with_for_update()
    ).scalar_one()
    if berth.status != "active":
        raise PortStateError("泊位已停用，无法确认预约")

    overlap = count_confirmed_overlap(db, appt.berth_id, appt.plan_start, appt.plan_end)
    if overlap >= berth.concurrent_capacity:
        raise BerthConflictError(
            f"档期冲突：该泊位 [{appt.plan_start:%m-%d %H:%M}~{appt.plan_end:%m-%d %H:%M}) "
            f"已有 {overlap} 个确认预约，并发容量 {berth.concurrent_capacity}"
        )

    appt.status = "confirmed"
    appt.reject_reason = ""
    db.commit()
    db.refresh(appt)
    return appt


def reject_appt(db: Session, appt: BerthAppt, reason: str) -> BerthAppt:
    """港口方驳回（仅 pending）。"""
    if appt.status != "pending":
        raise PortStateError(f"当前状态 {appt.status} 不可驳回")
    appt.status = "rejected"
    appt.reject_reason = reason or "未通过港口方审核"
    db.commit()
    db.refresh(appt)
    return appt


def cancel_appt(db: Session, appt: BerthAppt, operator_id: int) -> BerthAppt:
    """撤销预约（船东本人撤 pending/confirmed；confirmed 撤销即释放档期）。"""
    if appt.status not in ("pending", "confirmed"):
        raise PortStateError(f"当前状态 {appt.status} 不可撤销")
    if appt.applier_id != operator_id:
        raise PortStateError("仅申请人本人可撤销预约")
    appt.status = "cancelled"
    db.commit()
    db.refresh(appt)
    return appt


def complete_appt(db: Session, appt: BerthAppt) -> BerthAppt:
    """港口方核销（confirmed → completed）。"""
    if appt.status != "confirmed":
        raise PortStateError(f"当前状态 {appt.status} 不可核销")
    appt.status = "completed"
    db.commit()
    db.refresh(appt)
    return appt


def list_my_appts(
    db: Session, applier_id: int, appt_status: str | None = None,
    page: int = 1, size: int = 20,
) -> tuple[int, list[BerthAppt]]:
    """船东自己的预约列表。"""
    stmt = select(BerthAppt).where(BerthAppt.applier_id == applier_id)
    if appt_status:
        stmt = stmt.where(BerthAppt.status == appt_status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = list(
        db.execute(stmt.order_by(BerthAppt.id.desc()).offset((page - 1) * size).limit(size))
        .scalars()
        .all()
    )
    return total, items


def list_berth_appts(
    db: Session, berth_id: int | None = None, appt_status: str | None = None,
    page: int = 1, size: int = 20,
) -> tuple[int, list[BerthAppt]]:
    """港口方查看预约（默认待确认）。"""
    stmt = select(BerthAppt)
    if berth_id:
        stmt = stmt.where(BerthAppt.berth_id == berth_id)
    if appt_status:
        stmt = stmt.where(BerthAppt.status == appt_status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = list(
        db.execute(stmt.order_by(BerthAppt.id.asc()).offset((page - 1) * size).limit(size))
        .scalars()
        .all()
    )
    return total, items


def get_berth_schedule(db: Session, berth: Berth) -> list[BerthAppt]:
    """泊位档期：全部 confirmed 预约（按开始时间排序）。"""
    return list(
        db.execute(
            select(BerthAppt)
            .where(BerthAppt.berth_id == berth.id, BerthAppt.status == "confirmed")
            .order_by(BerthAppt.plan_start.asc())
        )
        .scalars()
        .all()
    )


def get_appt(db: Session, appt_id: int) -> BerthAppt | None:
    return db.get(BerthAppt, appt_id)
