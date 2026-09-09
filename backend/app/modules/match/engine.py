"""撮合引擎 Stage1（F5）：货 × 船硬约束过滤 + 多目标评分。

工程底线：撮合属确定性内核，不经 LLM（LLM 仅在 Agent 层做解释与导购）。

设计：
- engine 为纯函数模块，输入/输出均为 dataclass，不依赖 ORM 与 DB——
  可独立单测、可内联计算（Stage1 候选池规模下 P99 << 200ms）。
- 硬约束（CSP）一票否决：任一不满足即出局，并计入 filter_stats 供观测。
- 多目标评分为加权线性合成（Stage1 固定权重；Stage2 引入航道/泊位档期
  维度后再演进为带约束的优化问题）。

评分模型（满分 100）：
- 载重利用率 40 分：weight_t / deadweight_t，越高越优（抑制大船小货）
- 船型适配度   20 分：精确匹配 1.0，兼容匹配按 CARGO_SHIP_COMPAT 系数
- 船籍港就近   20 分：home_port == 起运港得满（Stage1 二值近似，Stage2 接
  航位推算空驶里程）
- 证书余量     20 分：证书有效期覆盖装货日之外剩余天数，90 天封顶
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---- 船型 × 货类兼容矩阵：cargo_type -> {ship_type: 适配系数} ----
# 危险品液货（tanker）仅液货船可承运；散货/件杂货允许船型互济但降分。
CARGO_SHIP_COMPAT: dict[str, dict[str, float]] = {
    "bulk": {"bulk": 1.0, "general": 0.6},
    "general": {"general": 1.0, "bulk": 0.6, "container": 0.6},
    "container": {"container": 1.0},
    "tanker": {"tanker": 1.0},
    "other": {"bulk": 1.0, "general": 1.0},
}

# 评分权重（和为 100）
W_LOAD_UTILIZATION = 40.0
W_TYPE_FIT = 20.0
W_HOME_PORT = 20.0
W_CERT_MARGIN = 20.0

# 证书余量满分天数
CERT_MARGIN_FULL_DAYS = 90


@dataclass(slots=True)
class CargoInput:
    """撮合所需的货源快照（与 ORM 解耦）。"""

    id: int
    cargo_type: str
    weight_t: float
    origin_port: str
    dest_port: str
    expect_date: date
    status: str


@dataclass(slots=True)
class ShipInput:
    """撮合所需的船舶快照（与 ORM 解耦）。"""

    id: int
    ship_type: str
    deadweight_t: float
    draft_m: float
    home_port: str
    cert_expiry: date
    status: str


@dataclass(slots=True)
class Candidate:
    """通过硬约束的候选（含得分与拆解）。"""

    ref_id: int
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MatchResult:
    """撮合结果：排序后的候选列表 + 未入局原因统计。"""

    candidates: list[Candidate] = field(default_factory=list)
    filter_stats: dict[str, int] = field(default_factory=dict)


def _cert_margin(expect_date: date, cert_expiry: date) -> float:
    """证书余量得分：覆盖装货日之外剩余天数 / 90 天封顶。"""
    days = (cert_expiry - expect_date).days
    if days <= 0:
        return 0.0
    return min(days / CERT_MARGIN_FULL_DAYS, 1.0)


def _score(cargo: CargoInput, ship: ShipInput) -> tuple[float, dict[str, float]]:
    """多目标加权评分（仅对已通过硬约束的组合调用）。"""
    utilization = min(cargo.weight_t / ship.deadweight_t, 1.0) if ship.deadweight_t else 0.0
    type_factor = CARGO_SHIP_COMPAT[cargo.cargo_type][ship.ship_type]
    home_port_hit = 1.0 if ship.home_port and ship.home_port == cargo.origin_port else 0.0
    cert_margin = _cert_margin(cargo.expect_date, ship.cert_expiry)

    breakdown = {
        "load_utilization": round(W_LOAD_UTILIZATION * utilization, 2),
        "type_fit": round(W_TYPE_FIT * type_factor, 2),
        "home_port": round(W_HOME_PORT * home_port_hit, 2),
        "cert_margin": round(W_CERT_MARGIN * cert_margin, 2),
    }
    score = round(sum(breakdown.values()), 2)
    return score, breakdown


def match_cargo_to_ships(cargo: CargoInput, ships: list[ShipInput]) -> MatchResult:
    """为货源找候选船（货主视角）。

    硬约束：
    1. 船舶已审核通过（verified）
    2. 证书有效期覆盖装货日（cert_expiry >= expect_date）
    3. 载重能力足够（deadweight_t >= weight_t）
    4. 船型与货类兼容（见 CARGO_SHIP_COMPAT）
    """
    result = MatchResult()
    for ship in ships:
        if ship.status != "verified":
            result.filter_stats["ship_not_verified"] = result.filter_stats.get("ship_not_verified", 0) + 1
            continue
        if ship.cert_expiry < cargo.expect_date:
            result.filter_stats["cert_expired"] = result.filter_stats.get("cert_expired", 0) + 1
            continue
        if ship.deadweight_t < cargo.weight_t:
            result.filter_stats["deadweight_insufficient"] = result.filter_stats.get(
                "deadweight_insufficient", 0
            ) + 1
            continue
        compat = CARGO_SHIP_COMPAT.get(cargo.cargo_type, {})
        if ship.ship_type not in compat:
            result.filter_stats["type_incompatible"] = result.filter_stats.get("type_incompatible", 0) + 1
            continue

        score, breakdown = _score(cargo, ship)
        result.candidates.append(Candidate(ref_id=ship.id, score=score, breakdown=breakdown))

    result.candidates.sort(key=lambda c: (-c.score, c.ref_id))
    return result


def match_ship_to_cargos(ship: ShipInput, cargos: list[CargoInput]) -> MatchResult:
    """为船找货源（船东视角，反向撮合）。

    硬约束（与正向镜像）：
    1. 货源已发布（published）
    2. 装货日不晚于证书有效期
    3. 货重不超过载重吨
    4. 货类与船型兼容
    """
    result = MatchResult()
    if ship.status != "verified":
        result.filter_stats["ship_not_verified"] = 1
        return result

    for cargo in cargos:
        if cargo.status != "published":
            result.filter_stats["cargo_not_published"] = result.filter_stats.get(
                "cargo_not_published", 0
            ) + 1
            continue
        if cargo.expect_date > ship.cert_expiry:
            result.filter_stats["cert_expired"] = result.filter_stats.get("cert_expired", 0) + 1
            continue
        if cargo.weight_t > ship.deadweight_t:
            result.filter_stats["deadweight_insufficient"] = result.filter_stats.get(
                "deadweight_insufficient", 0
            ) + 1
            continue
        compat = CARGO_SHIP_COMPAT.get(cargo.cargo_type, {})
        if ship.ship_type not in compat:
            result.filter_stats["type_incompatible"] = result.filter_stats.get("type_incompatible", 0) + 1
            continue

        score, breakdown = _score(cargo, ship)
        result.candidates.append(Candidate(ref_id=cargo.id, score=score, breakdown=breakdown))

    result.candidates.sort(key=lambda c: (-c.score, c.ref_id))
    return result
