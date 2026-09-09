"""订单模块业务逻辑（F6）。

核心纪律：
1. 防重复出单——同一货源至多一个 active（matched/shipped）订单；
   创建订单时对货源行加 FOR UPDATE 锁串行化并发出单
   （MySQL 生效；SQLite 测试环境单连接天然串行，镜像 F4 防超卖模式）。
2. 出单前复用撮合引擎硬约束（engine.pair_violation 单一事实来源），
   保证"进撮合池"与"能成交"判定一致。
3. 全链路留痕——每次状态迁移落时间戳；撤单记录原因。
4. 状态机（确定性内核，不经 LLM）：
   matched → shipped（船东启运）→ completed（货主签收）
   matched → cancelled（任一方撤单，货源释放回撮合池）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cargo import Cargo
from app.models.order import Order
from app.models.ship import Ship
from app.modules.match.engine import CargoInput, ShipInput, pair_violation

ACTIVE_STATUSES = ("matched", "shipped")


class OrderStateError(Exception):
    """订单状态机/前置条件错误。"""


class OrderConflictError(Exception):
    """货源已有生效订单（防重复出单拦截）。"""


def _engine_inputs(cargo: Cargo, ship: Ship) -> tuple[CargoInput, ShipInput]:
    """ORM → 撮合引擎快照（与 match.service 的转换保持一致）。"""
    return (
        CargoInput(
            id=cargo.id,
            cargo_type=cargo.cargo_type,
            weight_t=float(cargo.weight_t),
            origin_port=cargo.origin_port,
            dest_port=cargo.dest_port,
            expect_date=cargo.expect_date,
            status=cargo.status,
        ),
        ShipInput(
            id=ship.id,
            ship_type=ship.ship_type,
            deadweight_t=float(ship.deadweight_t),
            draft_m=float(ship.draft_m),
            home_port=ship.home_port,
            cert_expiry=ship.cert_expiry,
            status=ship.status,
        ),
    )


def create_order(db: Session, shipper_id: int, data) -> Order:
    """货主创建订单（基于撮合候选选船）。

    前置校验：
    - 货源存在、属于本人、已发布（published）；
    - 船舶存在且通过撮合硬约束（verified/证书/载重/船型）；
    - 货源无生效订单（FOR UPDATE 锁货源行串行化校验）。

    成交后货源状态 published → matched（退出撮合池）。
    """
    cargo = db.execute(
        select(Cargo).where(Cargo.id == data.cargo_id, Cargo.shipper_id == shipper_id)
    ).scalar_one_or_none()
    if cargo is None:
        raise OrderStateError("发货单不存在或不属于当前货主")
    if cargo.status == "matched":
        raise OrderConflictError("该货源已有生效订单，不可重复出单")
    if cargo.status != "published":
        raise OrderStateError(f"当前货源状态 {cargo.status} 不可出单，须为已发布")

    ship = db.get(Ship, data.ship_id)
    if ship is None:
        raise OrderStateError("船舶备案不存在")

    cargo_in, ship_in = _engine_inputs(cargo, ship)
    violation = pair_violation(cargo_in, ship_in)
    if violation is not None:
        raise OrderStateError(f"该组合不满足承运硬约束：{violation}")

    # 防重复出单：锁货源行，串行化同一货源的并发下单
    locked = db.execute(
        select(Cargo).where(Cargo.id == cargo.id).with_for_update()
    ).scalar_one()
    active = db.execute(
        select(func.count())
        .select_from(Order)
        .where(Order.cargo_id == cargo.id, Order.status.in_(ACTIVE_STATUSES))
    ).scalar_one()
    if active > 0:
        raise OrderConflictError("该货源已有生效订单，不可重复出单")

    order = Order(
        cargo_id=cargo.id,
        ship_id=ship.id,
        shipper_id=cargo.shipper_id,
        owner_id=ship.owner_id,
        freight_price=data.freight_price,
        status="matched",
    )
    locked.status = "matched"  # 货源退出撮合池
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def ship_order(db: Session, order: Order) -> Order:
    """船东启运：matched → shipped。"""
    if order.status != "matched":
        raise OrderStateError(f"当前状态 {order.status} 不可启运")
    order.status = "shipped"
    order.shipped_at = datetime.now()
    db.commit()
    db.refresh(order)
    return order


def complete_order(db: Session, order: Order) -> Order:
    """货主签收：shipped → completed。"""
    if order.status != "shipped":
        raise OrderStateError(f"当前状态 {order.status} 不可签收")
    order.status = "completed"
    order.completed_at = datetime.now()
    db.commit()
    db.refresh(order)
    return order


def cancel_order(db: Session, order: Order, reason: str) -> Order:
    """撤单：matched → cancelled，货源释放回撮合池（published）。

    仅 matched 可撤（启运后进入履约期，撤单属线下协商范畴）。
    支付联动（F7）：已支付 → 全额退款（refunded）；待支付 → 关闭（closed），
    资金流与订单流状态一致性由确定性内核驱动。
    """
    if order.status != "matched":
        raise OrderStateError(f"当前状态 {order.status} 不可撤单")
    order.status = "cancelled"
    order.cancel_reason = reason or "协商撤单"
    order.cancelled_at = datetime.now()

    from app.modules.payment.service import settle_on_cancel  # 延迟导入避免环

    settle_on_cancel(db, order)

    cargo = db.get(Cargo, order.cargo_id)
    if cargo is not None and cargo.status == "matched":
        cargo.status = "published"  # 释放回撮合池
    db.commit()
    db.refresh(order)
    return order


def list_orders(
    db: Session,
    user_id: int,
    role: str,
    order_status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[int, list[Order]]:
    """订单列表（角色视角）：货主看自己货源的单，船东看自己船舶的单。"""
    stmt = select(Order)
    if role == "shipper":
        stmt = stmt.where(Order.shipper_id == user_id)
    elif role == "owner":
        stmt = stmt.where(Order.owner_id == user_id)
    else:
        raise OrderStateError("仅货主/船东角色可查看订单列表")

    if order_status:
        stmt = stmt.where(Order.status == order_status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = list(
        db.execute(stmt.order_by(Order.id.desc()).offset((page - 1) * size).limit(size))
        .scalars()
        .all()
    )
    return total, items


def get_order(db: Session, order_id: int) -> Order | None:
    return db.get(Order, order_id)


def is_participant(order: Order, user_id: int) -> bool:
    """订单参与方判定（货主本人或船东本人）。"""
    return user_id in (order.shipper_id, order.owner_id)
