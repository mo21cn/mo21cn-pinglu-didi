"""撮合模块业务逻辑（F5 Stage1）：DB 取数 → 确定性引擎打分 → 组装响应。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cargo import Cargo
from app.models.ship import Ship
from app.modules.match import engine
from app.modules.match.engine import CargoInput, ShipInput
from app.modules.match.schemas import (
    CargoCandidate,
    CargoShipsMatchResponse,
    ShipCandidate,
    ShipCargosMatchResponse,
)


class MatchStateError(Exception):
    """撮合前置条件不满足（货源未发布 / 船舶未审核等）。"""


def match_for_cargo(db: Session, cargo: Cargo) -> CargoShipsMatchResponse:
    """为货源找候选船：全量 verified 船过引擎（Stage1 无索引裁剪，
    候选池规模下纯内存打分远低于 P99 200ms 预算；Stage2 再做预筛分区）。"""
    if cargo.status != "published":
        raise MatchStateError("货源尚未发布，请先发布进入撮合池")

    ships = list(db.execute(select(Ship)).scalars().all())
    cargo_in = CargoInput(
        id=cargo.id,
        cargo_type=cargo.cargo_type,
        weight_t=float(cargo.weight_t),
        origin_port=cargo.origin_port,
        dest_port=cargo.dest_port,
        expect_date=cargo.expect_date,
        status=cargo.status,
    )
    ships_in = [
        ShipInput(
            id=s.id,
            ship_type=s.ship_type,
            deadweight_t=float(s.deadweight_t),
            draft_m=float(s.draft_m),
            home_port=s.home_port,
            cert_expiry=s.cert_expiry,
            status=s.status,
        )
        for s in ships
    ]
    result = engine.match_cargo_to_ships(cargo_in, ships_in)

    ship_map = {s.id: s for s in ships}
    items = [
        ShipCandidate(
            ship_id=c.ref_id,
            ship_name=ship_map[c.ref_id].ship_name,
            ship_type=ship_map[c.ref_id].ship_type,
            deadweight_t=float(ship_map[c.ref_id].deadweight_t),
            home_port=ship_map[c.ref_id].home_port,
            score=c.score,
            breakdown=c.breakdown,  # type: ignore[arg-type]
        )
        for c in result.candidates
    ]
    return CargoShipsMatchResponse(
        cargo_id=cargo.id,
        total=len(items),
        filter_stats=result.filter_stats,
        items=items,
    )


def match_for_ship(db: Session, ship: Ship) -> ShipCargosMatchResponse:
    """为船找候选货源（船东视角反向撮合）。"""
    if ship.status != "verified":
        raise MatchStateError("船舶未通过审核，暂无撮合资格")

    cargos = list(db.execute(select(Cargo)).scalars().all())
    ship_in = ShipInput(
        id=ship.id,
        ship_type=ship.ship_type,
        deadweight_t=float(ship.deadweight_t),
        draft_m=float(ship.draft_m),
        home_port=ship.home_port,
        cert_expiry=ship.cert_expiry,
        status=ship.status,
    )
    cargos_in = [
        CargoInput(
            id=c.id,
            cargo_type=c.cargo_type,
            weight_t=float(c.weight_t),
            origin_port=c.origin_port,
            dest_port=c.dest_port,
            expect_date=c.expect_date,
            status=c.status,
        )
        for c in cargos
    ]
    result = engine.match_ship_to_cargos(ship_in, cargos_in)

    cargo_map = {c.id: c for c in cargos}
    items = [
        CargoCandidate(
            cargo_id=c.ref_id,
            cargo_name=cargo_map[c.ref_id].cargo_name,
            cargo_type=cargo_map[c.ref_id].cargo_type,
            weight_t=float(cargo_map[c.ref_id].weight_t),
            origin_port=cargo_map[c.ref_id].origin_port,
            dest_port=cargo_map[c.ref_id].dest_port,
            expect_date=cargo_map[c.ref_id].expect_date.isoformat(),
            score=c.score,
            breakdown=c.breakdown,  # type: ignore[arg-type]
        )
        for c in result.candidates
    ]
    return ShipCargosMatchResponse(
        ship_id=ship.id,
        total=len(items),
        filter_stats=result.filter_stats,
        items=items,
    )
