"""运力确认与有效期 —— 确定性判据（BP-03 第 3 条 / D1-06 / §10.1 第 5 步）。

合同原文（逐字）
----------------
* BP-03 第 3 条：`Record selection separately from resource confirmation.
  Confirmation requires an authorized action and identified evidence; expired or
  unsuitable resources cannot be confirmed without legitimate renewal/change.`
* BP-03 Exit evidence：`A chosen quotation alone does not create confirmed capacity.`
* D1-06：`Two quotes can be compared and a valid resource confirmed only with proper
  evidence`，判据 `Validity/capacity/evidence positive and negative cases`。
* §10.2 负例：`unsuitable/expired resource confirmation;`
* BP-04 第 6 条（同一套规则的第二个用途）：容量适用性是**确定性规则**，
  `an LLM opinion is not the rule`。

本模块的立场：**"确认"与"选中"分得开，靠的不是两个端点，而是一条规则闸门。**
`confirm_capacity` 在写库前跑 `_assert_every_rule_reported`：规则集里**每一条**都必须
产出一条判定，且**没有一条**是 fail。少一条就当场炸 —— 否则"某条规则这次没跑"
在数据上与"跑了且通过"完全一样（确认记录照样存在，逐规则判定表少一行而已）。
这与 `contracts._assert_every_field_has_source` 是同一类闸门，只是对象从"字段"换成"规则"。

容量规则的逐字来源（不是我的发明）
----------------------------------
`backend/scripts/fixtures/demo1_canonical.json` 的 `deterministic_capacity_rule`：

    "候选适用于某数量 ⇔ candidate.capacity_tonnes ≥ 该水运段的实际承运量；若候选声明
     single_vessel 且 no_partial_load，则该承运量必须由一条船装下（不允许拆批）。"

以及 `DEMO-1-fixture-manifest.md` 第 25 行：**「若允许拆批或多船承运，950 吨未必装不下」**。
本模块据此把三种形态分开（`capacity` 规则的三条分支），而不是笼统地比大小：

| 候选形态 | 判据 | 依据 |
| --- | --- | --- |
| 单船 + 不拆批 | `capacity_tonnes ≥ 需求` | 整批必须一船一程装下（canonical C-900：950 > 900 ⇒ 不适用） |
| 多船（`vessel_count > 1`） | `capacity_tonnes × vessel_count ≥ 需求` | 可分配到多条船 |
| 允许拆批（`allows_partial_load`） | 通过，并**记下需几趟** | 可多趟承运 ⇒ "未必装不下"；但趟数必须写在判定里，不能变成一句"通过" |

⚠️ 拆批分支为什么给"通过 + 趟数"而不是"不通过"：`allows_partial_load` 是**资源自己的
属性**（"这条船接受不足整船的货"），把它读成"必须走变更案件"等于替业务做了决定。
若 HO 认为拆批也必须落在正式变更里，处置是把它归入 fail 并追加一条业务变更 ——
一行判定 + 一条用例，无迁移代价。

**需求量取 `ent_assignment.quantity`**（整单货量），不按航段分摊：canonical 是
公—水—公同一批货过驳，水运段承担的正是整单货量。若将来要按航段分摊，
处置是给 `ent_leg` 加 `quantity` 列并改 `_assignment_demand` 这一处。

判定输入为什么整体冻结在确认行上
--------------------------------
`evaluate` 读的每一个值（需求量、吨位、船数、拆批、有效期、证据类别与引用）都**复制**
到 `ent_capacity_confirmation` 的同名列上。理由不是冗余，而是：候选行**可以被改写**，
而确认是一条**已经作出的商业事实**。"确认还在、判据已经不成立"是这类系统最难发现的状态，
冻结输入后 `recheck_confirmation` 能把"当时按什么判的"和"现在是什么"**并排**摆出来，
而不是二选一。

`recheck_confirmation` **只读**：它回答"这条确认现在还成立吗"，**不改**任何行。
正式的重做属于 S4 的变更流程（BP-04），本切片不越界 —— 但确定性内核在这里，
S4 复用同一套 `evaluate`，不另写一份（"适用性"有两个定义是必然出错的）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Final, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.entrust import artifacts as art
from app.modules.entrust import registry as reg
from app.modules.entrust.authz import load_assignment

# ── 规则集（取值域由本模块固定；新增规则必须同时改 RULESET_VERSION 与用例）────

RULE_DEMAND_KNOWN = "demand_known"
RULE_EVIDENCE = "evidence"
RULE_VALIDITY = "validity"
RULE_CAPACITY = "capacity"

#: **有序**：顺序即评估顺序与展示顺序。先问"需求口径对不对"再问"资源适不适用" ——
#: 反过来会出现"吨位不合格"与"单位不同口径"同时报，而后者才是根因。
ALL_RULE_CODES: Final[tuple[str, ...]] = (
    RULE_DEMAND_KNOWN,
    RULE_EVIDENCE,
    RULE_VALIDITY,
    RULE_CAPACITY,
)

#: 规则集版本，随确认一并冻结。规则会演进（S4 要把同一套容量规则用到变更影响上），
#: 旧确认必须能说清"它当年按哪一版判的" —— 否则事后复算会得到不同的结论而无从解释。
RULESET_VERSION: Final = "v1"

OUTCOME_PASS: Final = "pass"
OUTCOME_FAIL: Final = "fail"

#: 确认产出的成果类型（注册表里已声明字段契约与内部字段）。
CONFIRM_TYPE: Final = "procurement_confirm"

CANDIDATE_STATUS_CANDIDATE: Final = "candidate"
CANDIDATE_STATUS_CONFIRMED: Final = "confirmed"
CANDIDATE_STATUS_WITHDRAWN: Final = "withdrawn"
ALL_CANDIDATE_STATUSES: Final = frozenset(
    {
        CANDIDATE_STATUS_CANDIDATE,
        CANDIDATE_STATUS_CONFIRMED,
        CANDIDATE_STATUS_WITHDRAWN,
    }
)

#: 吨的书面写法。**只认这一组**，不做单位换算：`800 件` 与 `900 吨` 之间没有可判定的
#: 换算关系，硬转一个比例出来就是编造。认不出 ⇒ 该规则**不通过**并说明原因。
TONNE_UNITS: Final = frozenset({"吨", "公吨", "t", "ton", "tonne", "tonnes", "mt"})

_FMT = "%Y-%m-%d %H:%M:%S"
_DATE_FMT = "%Y-%m-%d"


class CapacityError(RuntimeError):
    """运力确认的基础异常。HTTP 层按语义转 4xx（默认 400）。"""


class CapacityNotFoundError(CapacityError):
    """候选运力 / 确认记录 / 委托单不存在（或不属于该委托）。HTTP 层应转 404。"""


class CapacityStateError(CapacityError):
    """当前事实不允许确认（已确认过 / 候选已作废）。HTTP 层应转 409。"""

    def __init__(
        self,
        message: str,
        *,
        existing_confirmation_id: int | None = None,
        existing_artifact_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.existing_confirmation_id = existing_confirmation_id
        self.existing_artifact_id = existing_artifact_id


class CapacityRuleError(CapacityError):
    """**判定不通过**：过期 / 装不下 / 证据未指定 / 需求口径不明。HTTP 层应转 409。

    刻意与 `CapacityError`（400，输入有毛病）分开：前者是"当前事实不允许"，
    后者是"这次请求本身不对"。合成一个码会让客户端无法区分"改输入"与"改业务"。

    `evaluations`：**每一条规则的判定**（通过的也在内）——
    "只说没过的那条"会让调用方修完这处再撞下一处。
    """

    def __init__(self, message: str, *, evaluations: tuple[RuleResult, ...] = ()) -> None:
        super().__init__(message)
        self.evaluations = evaluations


class CapacityInvariantError(RuntimeError):
    """**程序性错误**（闸门发现规则没跑全）。

    刻意**不**继承 `CapacityError`：它不该被 `_map_errors` 映射成 4xx ——
    它意味着代码写错了，必须是一个响亮的 500，而不是一条"你的请求有问题"。
    """


@dataclass(frozen=True)
class RuleResult:
    """一条规则的判定。`detail` 必须写出**实际比较的数**，不能只说"通过"。"""

    rule_code: str
    outcome: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.outcome == OUTCOME_PASS


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 `ent_` 表时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def today_utc() -> date:
    """判定基准日。

    与"记录时间"分开：业务上"这条报价过期了没有"取决于**哪一天在看**，
    而记录时间只说明那条记录什么时候写的。两者混用会让同一个判定
    在不同时区/不同重放时刻给出不同结论而无法解释。
    """
    return utcnow_naive().date()


# ── 取值规范化（读进来的值是 DB 原样：MySQL 给 Decimal/date，SQLite 给 str）──


def _decimal(value: Any) -> Decimal | None:
    """能当作定点数读出来才返回，否则 `None`。

    ⛔ `bool` 在 Python 里是 `int` 的子类，不挡住会把 `True` 当成吨位 1。
    ⛔ `float('nan')` / `inf` 也挡住：它们写进 DECIMAL 列会在 MySQL 上炸，
    而在 Python 里一路算下去只会得到"比较结果为 False"这种看不出错的结果。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # 走 `str()` 而不是 `Decimal(float)`：后者会把二进制误差原样带进来
        # （`Decimal(0.1)` = 0.1000000000000000055511151231257827…）。
        if value != value or value in (float("inf"), float("-inf")):
            return None
        try:
            parsed = Decimal(str(value))
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    """TINYINT/INTEGER/布尔文本 → bool。认不出一律 **False**（不猜成"允许"）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "t", "是", "允许"}


def _is_tonne_unit(unit: Any) -> bool:
    return str(unit or "").strip().casefold() in TONNE_UNITS


def _as_date(value: Any) -> date | None:
    """DATE 列 → `date`。

    ⚠️ `datetime` 是 `date` 的**子类**，所以先判 `datetime` 再判 `date` —— 反过来写
    `isinstance(value, date)` 会先命中，`datetime` 的时区/时分秒就被当成日期，
    而 MySQL 的 DATE 列读回来是 `date`、SQLite 是 `str`，本地与 CI 会走不同分支。
    （与 `contracts._fmt` 踩过的那个坑同源：写入路径全绿、读回来才炸。）
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _fmt(dt: Any) -> str:
    """与 `offers._fmt` / `contracts._fmt` **逐字一致**（SQLite 把 DATETIME 读回来是 str）。"""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return str(dt.strftime(_FMT))


def _dec_text(value: Decimal | None, places: int) -> str | None:
    """定点数值 → **固定小数位**的文本。

    固定位数是为了让 SQLite（TEXT）与 MySQL（DECIMAL）读回来是**同一个字符串** ——
    否则同一份数据在两个后端上"看起来不同"，逐字段核对就要先解释格式差异。
    """
    if value is None:
        return None
    return f"{value:.{places}f}"


TONNE_PLACES: Final = 3
RATE_PLACES: Final = 4


# ── 规则：纯函数，不读库、不写库、不抛业务异常（坏数据 ⇒ 不通过，不是 500）──


def evaluate(
    *,
    demand_tonnes: Any,
    demand_unit: Any,
    demand_ref: str,
    capacity_tonnes: Any,
    vessel_count: Any,
    allows_partial_load: Any,
    valid_until: Any,
    evidence_kind: Any,
    evidence_ref: Any,
    as_of: date,
) -> tuple[RuleResult, ...]:
    """逐规则判定。**四条全跑**（不做 fail-fast），返回顺序 = `ALL_RULE_CODES`。

    为什么不全跑就返回：调用方拿到"只差一条"的拒绝，改完再撞下一条，
    一次演示要来回四五轮。逐条如实报是同一份工作量，但让人一次改完。

    规则之间**允许因同一根因同时不通过**（需求量未知 ⇒ `demand_known` 与 `capacity`
    都不通过）。刻意**不**做"根因归并"：归并后"一共几条规则被判过"就不再等于
    `ALL_RULE_CODES`，而那道闸门正是本模块的核心不变式。
    """
    results: list[RuleResult] = []

    # ① 需求口径：不知道需求、或单位不同口径，后面两条都无从判定。
    demand = _decimal(demand_tonnes)
    demand_ok = demand is not None and demand > 0 and _is_tonne_unit(demand_unit)
    if demand is None:
        results.append(
            RuleResult(
                RULE_DEMAND_KNOWN,
                OUTCOME_FAIL,
                "需求量未知（或不是数）—— 未知保持未知，不得按 0 判过，"
                "也不得凭候选运力反推一个需求出来",
            )
        )
    elif not _is_tonne_unit(demand_unit):
        results.append(
            RuleResult(
                RULE_DEMAND_KNOWN,
                OUTCOME_FAIL,
                f"需求单位是 {str(demand_unit or '').strip() or '（空）'!r}，"
                "与水运段的吨位不是同一口径，不能直接比大小"
                "（本模块不做单位换算：不同口径之间没有可判定的换算关系）",
            )
        )
    elif demand <= 0:
        results.append(
            RuleResult(RULE_DEMAND_KNOWN, OUTCOME_FAIL, f"需求量 {_dec_text(demand, 3)} 不是正数")
        )
    else:
        results.append(
            RuleResult(
                RULE_DEMAND_KNOWN,
                OUTCOME_PASS,
                f"需求量 {_dec_text(demand, 3)} 吨，来自 {demand_ref}",
            )
        )

    # ② 证据：BP-03 第 3 条 `identified evidence` = 类别 + **引用**。
    kind = str(evidence_kind or "").strip()
    ref = str(evidence_ref or "").strip()
    if not kind:
        results.append(
            RuleResult(
                RULE_EVIDENCE,
                OUTCOME_FAIL,
                "候选运力没有登记证据类别 —— 确认需要有据可依，不能凭选中就确认",
            )
        )
    elif kind not in reg.ALL_EVIDENCE_KINDS:
        results.append(
            RuleResult(
                RULE_EVIDENCE,
                OUTCOME_FAIL,
                f"证据类别 {kind!r} 不在取值域 {sorted(reg.ALL_EVIDENCE_KINDS)} 内",
            )
        )
    elif not ref:
        results.append(
            RuleResult(
                RULE_EVIDENCE,
                OUTCOME_FAIL,
                f"证据只有类别 {kind!r}，没有引用哪一份 —— "
                "「identified evidence」要能指着具体那一份，"
                "只写「有单据」与「有这一份单据」在事后核对时差别就是全部",
            )
        )
    else:
        results.append(RuleResult(RULE_EVIDENCE, OUTCOME_PASS, f"证据 {kind}／{ref}"))

    # ③ 有效期：过期不得确认。
    if valid_until is None or str(valid_until).strip() == "":
        results.append(
            RuleResult(
                RULE_VALIDITY,
                OUTCOME_FAIL,
                "候选运力没有登记有效期 —— 「未知有效期不等于未过期」，"
                "把两者折成一个结论等于替审计做了判断",
            )
        )
    else:
        expiry = _as_date(valid_until)
        if expiry is None:
            results.append(
                RuleResult(
                    RULE_VALIDITY,
                    OUTCOME_FAIL,
                    f"有效期 {str(valid_until).strip()!r} 读不成日期，无法判定是否过期",
                )
            )
        elif expiry < as_of:
            results.append(
                RuleResult(
                    RULE_VALIDITY,
                    OUTCOME_FAIL,
                    f"已于 {expiry.isoformat()} 过期（判定基准日 {as_of.isoformat()}）—— "
                    "过期资源不得确认，须先取得合法续期（续期＝登记新的候选，"
                    "而不是把老候选的有效期改掉：改掉之后旧确认的判据就无从复核了）",
                )
            )
        else:
            results.append(
                RuleResult(
                    RULE_VALIDITY,
                    OUTCOME_PASS,
                    f"有效期至 {expiry.isoformat()}（判定基准日 {as_of.isoformat()}）",
                )
            )

    # ④ 容量适用性：确定性算术，**不得**由模型意见决定（BP-04 第 6 条）。
    capacity = _decimal(capacity_tonnes)
    vessels = _int_or_none(vessel_count)
    if capacity is None or capacity <= 0:
        results.append(
            RuleResult(
                RULE_CAPACITY,
                OUTCOME_FAIL,
                "可用吨位"
                + ("未知" if capacity is None else f"为 {_dec_text(capacity, 3)}（非正数）")
                + " ⇒ 谈不上适用",
            )
        )
    elif vessels is None or vessels < 1:
        results.append(
            RuleResult(
                RULE_CAPACITY,
                OUTCOME_FAIL,
                f"承运船数 {str(vessel_count).strip()!r} 不是正整数 ⇒ 无法算可用运力",
            )
        )
    elif demand is None or not demand_ok:
        # `demand is None` 是**重复书写**：`demand_ok` 为假时它必定成立（见规则①）。
        # 写出来的理由只有一个 —— mypy 不从一个 bool 变量反推 `demand` 的类型，
        # 不这样写，下面三个分支里的 `demand / capacity` 之类会被判 `Decimal | None`。
        results.append(
            RuleResult(
                RULE_CAPACITY,
                OUTCOME_FAIL,
                "需求量或它的单位不可用（见 demand_known），无法判定容量适用性",
            )
        )
    elif _truthy(allows_partial_load):
        trips = (demand / capacity).to_integral_value(rounding=ROUND_CEILING)
        results.append(
            RuleResult(
                RULE_CAPACITY,
                OUTCOME_PASS,
                f"允许拆批 ⇒ 超出部分可分趟承运，需 {int(trips)} 趟"
                f"（单船 {_dec_text(capacity, 3)} 吨 × {vessels} 条船；"
                f"需求 {_dec_text(demand, 3)} 吨）",
            )
        )
    else:
        supply = capacity * vessels
        if supply >= demand:
            results.append(
                RuleResult(
                    RULE_CAPACITY,
                    OUTCOME_PASS,
                    f"可用运力 {_dec_text(capacity, 3)} × {vessels} = "
                    f"{_dec_text(supply, 3)} 吨 ≥ 需求 {_dec_text(demand, 3)} 吨",
                )
            )
        else:
            shortfall = demand - supply
            results.append(
                RuleResult(
                    RULE_CAPACITY,
                    OUTCOME_FAIL,
                    f"可用运力 {_dec_text(capacity, 3)} × {vessels} = "
                    f"{_dec_text(supply, 3)} 吨 < 需求 {_dec_text(demand, 3)} 吨，"
                    f"缺口 {_dec_text(shortfall, 3)} 吨"
                    + ("（单船承运且不拆批 ⇒ 整批必须一船装下）" if vessels == 1 else ""),
                )
            )

    return tuple(results)


def failed_rules(evaluations: tuple[RuleResult, ...]) -> tuple[RuleResult, ...]:
    """不通过的规则（按评估顺序）。"""
    return tuple(e for e in evaluations if not e.passed)


def _assert_every_rule_reported(evaluations: tuple[RuleResult, ...]) -> None:
    """**闸门**：规则集里每一条都必须产出一条判定，且没有 fail。

    这是本模块的核心不变式 —— 缺一条判定，在数据上与"跑了且通过"完全一样
    （确认记录照样存在，逐规则判定表少一行），而这种缺口**没有任何下游症状**。
    所以它必须在写库之前炸，不能等有人来核对。
    """
    codes = tuple(e.rule_code for e in evaluations)
    if codes != ALL_RULE_CODES:
        raise CapacityInvariantError(
            "运力确认中止：规则集与判定对不上 —— 期望 "
            f"{list(ALL_RULE_CODES)}，实得 {list(codes)}。"
            "少一条判定意味着「这条规则这次没跑」，而那在库里看不出区别。"
        )
    bad = failed_rules(evaluations)
    if bad:
        raise CapacityInvariantError(
            "运力确认中止：判定里仍有未通过的规则却走到了写库（调用方应先拒绝）："
            + "、".join(f"{e.rule_code}={e.detail}" for e in bad)
        )


# ── 读 ──────────────────────────────────────────────────────────────────────

_CANDIDATE_COLS = (
    "id, assignment_id, leg_id, carrier, vessel_name, capacity_tonnes, vessel_count, "
    "allows_partial_load, rate, rate_unit, currency, valid_until, evidence_kind, "
    "evidence_ref, status, created_at, updated_at"
)

_CONFIRMATION_COLS = (
    "id, assignment_id, entrustment_id, candidate_id, leg_id, carrier, vessel_name, "
    "capacity_tonnes, vessel_count, allows_partial_load, rate, rate_unit, currency, "
    "valid_until, evidence_kind, evidence_ref, demand_tonnes, demand_unit, demand_ref, "
    "as_of_date, rule_set_version, artifact_id, artifact_revision_id, "
    "artifact_revision_no, confirmed_by, confirmed_at, note, created_at, updated_at"
)


def _row_to_candidate(row: Any) -> dict[str, Any]:
    # 有效期只解析一次：写成 `_as_date(...).isoformat() if _as_date(...)` 时 mypy 收不窄
    # （两次调用之间的类型关系它不认），会报 union-attr。与 `_row_to_confirmation` 写法一致。
    expiry = _as_date(row["valid_until"])
    return {
        "candidate_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "leg_id": int(row["leg_id"]) if row["leg_id"] is not None else None,
        "carrier": str(row["carrier"]),
        "vessel_name": row["vessel_name"],
        # ⚠️ 数值列**在这里就归一成定长文本**：MySQL 把 DECIMAL 读回来是 `Decimal`、
        #    SQLite 读回来是 `str`。不归一的话，"同一份数据"在两个后端上类型不同 ——
        #    响应模型声明 `str` 时，MySQL 上会直接校验失败（本地全绿、CI 红）。
        #    这与 `_fmt`（时间列）踩过的坑同源，只是换成数值列。
        "capacity_tonnes": _dec_text(_decimal(row["capacity_tonnes"]), TONNE_PLACES),
        "vessel_count": int(row["vessel_count"]),
        "allows_partial_load": _truthy(row["allows_partial_load"]),
        "rate": _dec_text(_decimal(row["rate"]), RATE_PLACES),
        "rate_unit": row["rate_unit"],
        "currency": row["currency"],
        "valid_until": expiry.isoformat() if expiry is not None else None,
        "evidence_kind": row["evidence_kind"],
        "evidence_ref": row["evidence_ref"],
        "status": str(row["status"]),
        "created_at": _fmt(row["created_at"]),
        "updated_at": _fmt(row["updated_at"]),
    }


def _row_to_confirmation(row: Any) -> dict[str, Any]:
    expiry = _as_date(row["valid_until"])
    as_of = _as_date(row["as_of_date"])
    return {
        "confirmation_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "entrustment_id": (
            int(row["entrustment_id"]) if row["entrustment_id"] is not None else None
        ),
        "candidate_id": int(row["candidate_id"]),
        "leg_id": int(row["leg_id"]) if row["leg_id"] is not None else None,
        "carrier": str(row["carrier"]),
        "vessel_name": row["vessel_name"],
        # 数值列归一成定长文本，理由同 `_row_to_candidate`
        "capacity_tonnes": _dec_text(_decimal(row["capacity_tonnes"]), TONNE_PLACES),
        "vessel_count": int(row["vessel_count"]),
        "allows_partial_load": _truthy(row["allows_partial_load"]),
        "rate": _dec_text(_decimal(row["rate"]), RATE_PLACES),
        "rate_unit": row["rate_unit"],
        "currency": row["currency"],
        "valid_until": expiry.isoformat() if expiry else None,
        "evidence_kind": row["evidence_kind"],
        "evidence_ref": row["evidence_ref"],
        "demand_tonnes": _dec_text(_decimal(row["demand_tonnes"]), TONNE_PLACES),
        "demand_unit": row["demand_unit"],
        "demand_ref": str(row["demand_ref"]),
        "as_of_date": as_of.isoformat() if as_of else str(row["as_of_date"]),
        "rule_set_version": str(row["rule_set_version"]),
        "artifact_id": int(row["artifact_id"]),
        "artifact_revision_id": int(row["artifact_revision_id"]),
        "artifact_revision_no": int(row["artifact_revision_no"]),
        "confirmed_by": int(row["confirmed_by"]),
        "confirmed_at": _fmt(row["confirmed_at"]),
        "note": row["note"],
    }


def get_candidate(session: Session, *, candidate_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {_CANDIDATE_COLS} FROM ent_capacity_candidate WHERE id = :cid"),
            {"cid": candidate_id},
        )
        .mappings()
        .first()
    )
    return _row_to_candidate(row) if row is not None else None


def list_candidates(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托的候选运力（含 `status`），按登记顺序。

    按 `id` 而不是按"吨位从大到小"排序：哪一种排序更"对"是**方案判断**，
    而这里要的是稳定、可复现的清单（界面自己排）。顺序不稳定时，
    "两次看到的是同一批候选"这句话在断言上都写不出来。
    """
    rows = (
        session.execute(
            text(
                f"SELECT {_CANDIDATE_COLS} FROM ent_capacity_candidate "
                "WHERE assignment_id = :aid ORDER BY id"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_candidate(r) for r in rows]


def get_confirmation(session: Session, *, confirmation_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {_CONFIRMATION_COLS} FROM ent_capacity_confirmation WHERE id = :cid"),
            {"cid": confirmation_id},
        )
        .mappings()
        .first()
    )
    return _row_to_confirmation(row) if row is not None else None


def get_confirmation_by_candidate(session: Session, *, candidate_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                f"SELECT {_CONFIRMATION_COLS} FROM ent_capacity_confirmation "
                "WHERE candidate_id = :cid"
            ),
            {"cid": candidate_id},
        )
        .mappings()
        .first()
    )
    return _row_to_confirmation(row) if row is not None else None


def list_confirmations(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                f"SELECT {_CONFIRMATION_COLS} FROM ent_capacity_confirmation "
                "WHERE assignment_id = :aid ORDER BY id"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_confirmation(r) for r in rows]


def list_rule_checks(session: Session, *, confirmation_id: int) -> list[dict[str, Any]]:
    """该确认的逐规则判定（按 `seq`）。**有行就是通过**（见迁移文档的两条不对称）。"""
    rows = (
        session.execute(
            text(
                "SELECT rule_code, seq, detail, created_at FROM ent_capacity_rule_check "
                "WHERE confirmation_id = :cid ORDER BY seq"
            ),
            {"cid": confirmation_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "rule_code": str(r["rule_code"]),
            "seq": int(r["seq"]),
            "outcome": OUTCOME_PASS,
            "detail": str(r["detail"]),
            "created_at": _fmt(r["created_at"]),
        }
        for r in rows
    ]


def _assignment_demand(session: Session, assignment_id: int) -> tuple[Any, Any, str]:
    """判定的**需求量**来源：`ent_assignment.quantity`。

    返回 `(quantity, quantity_unit, ref)`；`ref` 写成 `assignment:<id>.quantity` ——
    判定说明里必须能指着某一行的某一列，否则"需求是 800 吨"这句话没有出处。
    不按航段分摊（理由见模块文档）。
    """
    row = (
        session.execute(
            text("SELECT quantity, quantity_unit FROM ent_assignment WHERE id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None, None, f"assignment:{assignment_id}.quantity"
    return row["quantity"], row["quantity_unit"], f"assignment:{assignment_id}.quantity"


# ── 命令：登记候选运力 ──────────────────────────────────────────────────────


def record_candidate(
    session: Session,
    *,
    assignment_id: int,
    actor_user_id: int,
    carrier: str,
    capacity_tonnes: Any,
    vessel_count: int = 1,
    allows_partial_load: bool = False,
    leg_id: int | None = None,
    vessel_name: str | None = None,
    rate: Any = None,
    rate_unit: str | None = None,
    currency: str | None = None,
    valid_until: Any = None,
    evidence_kind: str | None = None,
    evidence_ref: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """登记一条**候选运力事实**（不是确认）。

    登记只拒绝**结构上不可能有意义**的输入（录错了），**不**拒绝"还没证据/还没有效期" ——
    那些是**确认时**的判定：证据可以后补，有效期会随时间从"能用"变成"过期"。
    如果把这两类都压在登记上，第 3 条的 `expired ... cannot be confirmed` 就永远
    触发不了（过期数据根本进不来），那条规则也就变成了装饰。

    Raises:
        CapacityError: 输入有毛病（400）。
        CapacityNotFoundError: 指定的航段不属于本委托（400 语义，HTTP 层按 not-found 处理）。
    """
    name = str(carrier or "").strip()
    if not name:
        raise CapacityError("承运人不能为空 —— 没有承运人的候选运力无法作为确认对象")

    tonnes = _decimal(capacity_tonnes)
    if tonnes is None or tonnes <= 0:
        raise CapacityError(
            f"可用吨位 {str(capacity_tonnes).strip()!r} 不是正数 —— "
            "容量适用性全靠它算，录一个非正数进来只会让后面的判定变成噪音"
        )
    vessels = _int_or_none(vessel_count)
    if vessels is None or vessels < 1:
        raise CapacityError(f"承运船数 {str(vessel_count).strip()!r} 不是正整数")

    rate_dec = _decimal(rate)
    if rate is not None and str(rate).strip() != "" and rate_dec is None:
        raise CapacityError(f"单价 {str(rate).strip()!r} 不是数")
    unit_text = str(rate_unit or "").strip() or None
    if (rate_dec is None) != (unit_text is None):
        # 与注册表里 `rate` / `rate_unit` 的同一条论据：只有金额无法确定费用。
        raise CapacityError("单价与计价单位必须成对出现（只有金额无法确定费用）")

    kind = str(evidence_kind or "").strip() or None
    if kind is not None and kind not in reg.ALL_EVIDENCE_KINDS:
        raise CapacityError(f"证据类别 {kind!r} 不在取值域 {sorted(reg.ALL_EVIDENCE_KINDS)} 内")

    expiry = None
    if valid_until is not None and str(valid_until).strip() != "":
        parsed = _as_date(valid_until)
        if parsed is None:
            raise CapacityError(f"有效期 {str(valid_until).strip()!r} 读不成日期")
        expiry = parsed

    if leg_id is not None:
        leg = (
            session.execute(
                text("SELECT assignment_id FROM ent_leg WHERE id = :lid"), {"lid": leg_id}
            )
            .mappings()
            .first()
        )
        if leg is None or int(leg["assignment_id"]) != assignment_id:
            # 跨单引用航段是数据错误，且会让"这条运力属于哪一单"变得可争
            raise CapacityError(f"航段 {leg_id} 不属于委托 {assignment_id}")

    now = utcnow_naive()
    stamp = _fmt(now)
    row = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_capacity_candidate "
                "(assignment_id, leg_id, carrier, vessel_name, capacity_tonnes, vessel_count, "
                " allows_partial_load, rate, rate_unit, currency, valid_until, evidence_kind, "
                " evidence_ref, status, created_at, updated_at) "
                "VALUES (:aid, :leg, :carrier, :vessel, :tonnes, :vessels, :partial, :rate, "
                " :unit, :cur, :expiry, :kind, :ref, :status, :ts, :ts)"
            ),
            {
                "aid": assignment_id,
                "leg": leg_id,
                "carrier": name,
                "vessel": (str(vessel_name or "").strip() or None),
                "tonnes": _dec_text(tonnes, TONNE_PLACES),
                "vessels": vessels,
                "partial": 1 if _truthy(allows_partial_load) else 0,
                "rate": _dec_text(rate_dec, RATE_PLACES),
                "unit": unit_text,
                "cur": (str(currency or "").strip() or None),
                "expiry": expiry.isoformat() if expiry else None,
                "kind": kind,
                "ref": (str(evidence_ref or "").strip() or None),
                "status": CANDIDATE_STATUS_CANDIDATE,
                "ts": stamp,
            },
        ),
    )
    candidate_id = int(row.lastrowid or 0)
    _ = actor_user_id  # 登记人不落库：候选运力是**事实**，不宣示谁录的（确认才要 actor）
    if commit:
        session.commit()
    created = get_candidate(session, candidate_id=candidate_id)
    if created is None:
        raise CapacityError("候选运力写入后读不到（不该发生）")
    return created


# ── 命令：确认运力 ──────────────────────────────────────────────────────────


def confirm_capacity(
    session: Session,
    *,
    assignment_id: int,
    candidate_id: int,
    actor_user_id: int,
    agreed_scope: str,
    entrustment_id: int,
    note: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """确认运力：跑规则闸门 → 产出 `procurement_confirm` 成果 + 确认记录（一个事务）。

    `entrustment_id` **必填**（不给默认值）：它要写进 `ent_artifact.entrustment_id`，
    而那一列是 NOT NULL —— 给一个 `None` 默认值，报错会迟到一个数据库层，
    且形态是 `IntegrityError`（见下方 `except` 的处理：**只把"确实已有确认行"
    的那一种翻译成 409，其余一律原样抛出**）。

    调用方（`capacity_api._write_scope`）负责解析出**唯一**那条生效授权。
    本函数不做权限判定 —— 权限只有 `authz.py` 一条入口。

    幂等由调用方的 `run_write` 负责；**并发**由 `UNIQUE(candidate_id)` 兜住。

    Raises:
        CapacityNotFoundError: 候选运力不存在 / 不属于该委托（404）。
        CapacityStateError: 已确认过（409，带上已存在的那条确认与成果 id）/ 候选已作废（409）。
        CapacityRuleError: 判定不通过（409，带**逐条**判定）。
        CapacityError: 入选范围为空等输入问题（400）。
        reg.ArtifactPayloadError: 产出的成果不符合注册表字段契约（400）。
    """
    scope_text = str(agreed_scope or "").strip()
    if not scope_text:
        raise CapacityError(
            "确认范围（agreed_scope）不能为空 —— 采购确认要写清这一确认覆盖什么范围，"
            "「确认了」三个字不构成可复核的商务事实"
        )

    candidate = get_candidate(session, candidate_id=candidate_id)
    if candidate is None or int(candidate["assignment_id"]) != assignment_id:
        # 不属于本委托的候选按"不存在"处理：跨单确认必须失败，且不能说破那个 id 存在
        raise CapacityNotFoundError("候选运力不存在")

    if str(candidate["status"]) == CANDIDATE_STATUS_WITHDRAWN:
        raise CapacityStateError(
            f"候选运力 {candidate_id} 已作废，不能确认 —— 已作废的资源要重新确认，"
            "须先重新登记（它的吨位/有效期/证据都变了，不该沿用旧行）"
        )

    existing = get_confirmation_by_candidate(session, candidate_id=candidate_id)
    if existing is not None:
        raise CapacityStateError(
            f"候选运力 {candidate_id} 已经确认过（确认记录 #{existing['confirmation_id']}，"
            f"成果 #{existing['artifact_id']}）—— 一条候选只能确认一次；"
            "要改资源或改结论，登记新的候选再确认（已作出的确认不能被覆盖）",
            existing_confirmation_id=int(existing["confirmation_id"]),
            existing_artifact_id=int(existing["artifact_id"]),
        )

    assignment = load_assignment(session, assignment_id)
    if assignment is None:
        raise CapacityNotFoundError("委托不存在")

    basis = as_of or today_utc()
    demand_tonnes, demand_unit, demand_ref = _assignment_demand(session, assignment_id)
    evaluations = evaluate(
        demand_tonnes=demand_tonnes,
        demand_unit=demand_unit,
        demand_ref=demand_ref,
        capacity_tonnes=candidate["capacity_tonnes"],
        vessel_count=candidate["vessel_count"],
        allows_partial_load=candidate["allows_partial_load"],
        valid_until=candidate["valid_until"],
        evidence_kind=candidate["evidence_kind"],
        evidence_ref=candidate["evidence_ref"],
        as_of=basis,
    )
    bad = failed_rules(evaluations)
    if bad:
        raise CapacityRuleError(
            "运力不能确认：" + "；".join(f"[{e.rule_code}] {e.detail}" for e in bad),
            evaluations=evaluations,
        )
    _assert_every_rule_reported(evaluations)

    # 成果内容：**事实全部取自候选行**（冻结副本），调用方只能给"范围"这类业务判断。
    # 让请求体带 `supplier`/`agreed_amount` 会让"确认的内容"与"候选运力"可以不一致，
    # 而那条不一致在界面上看不出来 —— 于是"确认"变成一次自述。
    payload: dict[str, Any] = {
        "supplier": str(candidate["carrier"]),
        "agreed_scope": scope_text,
    }
    rate_dec = _decimal(candidate["rate"])
    if rate_dec is not None:
        payload["agreed_amount"] = float(rate_dec)
    if candidate["currency"]:
        payload["currency"] = str(candidate["currency"])
    payload["effective_from"] = basis.isoformat()
    reg.validate_payload(CONFIRM_TYPE, payload)  # 契约校验不因"自己写库"而省略

    now = utcnow_naive()
    stamp = _fmt(now)
    note_final = note or (
        f"运力确认：{candidate['carrier']}"
        + (f"／{candidate['vessel_name']}" if candidate["vessel_name"] else "")
        + f"（{candidate['capacity_tonnes']} 吨 × {candidate['vessel_count']} 条船"
        + ("，允许拆批" if candidate["allows_partial_load"] else "，不拆批")
        + f"；有效期至 {candidate['valid_until'] or '未登记'}"
        + f"；按规则集 {RULESET_VERSION} 判定）"
    )

    try:
        artifact_row = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_artifact "
                    "(entrustment_id, assignment_id, artifact_type, current_revision_id, "
                    " status, created_at, updated_at) "
                    "VALUES (:eid, :aid, :atype, NULL, :status, :ts, :ts)"
                ),
                {
                    "eid": entrustment_id,
                    "aid": assignment_id,
                    "atype": CONFIRM_TYPE,
                    "status": art.STATUS_ACTIVE,
                    "ts": stamp,
                },
            ),
        )
        artifact_id = int(artifact_row.lastrowid or 0)

        revision_row = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_artifact_revision "
                    "(artifact_id, revision_no, payload_json, source, note, created_by, created_at) "
                    "VALUES (:aid, 1, :payload, :source, :note, :by, :ts)"
                ),
                {
                    "aid": artifact_id,
                    "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    # `manual` 而不是 `agent`：这是**确定性判定 + 人工确认**，不是模型产出。
                    # 标成 agent 会掉进 source_gate 的"模型产出却无声明 ⇒ 拒发"分支，
                    # 而可核对性由本模块的逐规则判定表承担。
                    "source": art.SOURCE_MANUAL,
                    "note": note_final[:500],
                    "by": actor_user_id,
                    "ts": stamp,
                },
            ),
        )
        revision_id = int(revision_row.lastrowid or 0)
        session.execute(
            text("UPDATE ent_artifact SET current_revision_id = :rid WHERE id = :aid"),
            {"rid": revision_id, "aid": artifact_id},
        )

        confirmation_row = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_capacity_confirmation "
                    "(assignment_id, entrustment_id, candidate_id, leg_id, carrier, vessel_name, "
                    " capacity_tonnes, vessel_count, allows_partial_load, rate, rate_unit, "
                    " currency, valid_until, evidence_kind, evidence_ref, demand_tonnes, "
                    " demand_unit, demand_ref, as_of_date, rule_set_version, artifact_id, "
                    " artifact_revision_id, artifact_revision_no, confirmed_by, confirmed_at, "
                    " note, created_at, updated_at) "
                    "VALUES (:aid, :eid, :cid, :leg, :carrier, :vessel, :tonnes, :vessels, "
                    " :partial, :rate, :unit, :cur, :expiry, :kind, :ref, :demand, :dunit, "
                    " :dref, :asof, :rsv, :art, :revid, :revno, :by, :now, :note, :now, :now)"
                ),
                {
                    "aid": assignment_id,
                    "eid": entrustment_id,
                    "cid": candidate_id,
                    "leg": candidate["leg_id"],
                    "carrier": str(candidate["carrier"]),
                    "vessel": candidate["vessel_name"],
                    "tonnes": _dec_text(_decimal(candidate["capacity_tonnes"]), TONNE_PLACES),
                    "vessels": int(candidate["vessel_count"]),
                    "partial": 1 if candidate["allows_partial_load"] else 0,
                    "rate": _dec_text(rate_dec, RATE_PLACES),
                    "unit": candidate["rate_unit"],
                    "cur": candidate["currency"],
                    "expiry": candidate["valid_until"],
                    "kind": candidate["evidence_kind"],
                    "ref": candidate["evidence_ref"],
                    "demand": _dec_text(_decimal(demand_tonnes), TONNE_PLACES),
                    "dunit": demand_unit,
                    "dref": demand_ref,
                    "asof": basis.isoformat(),
                    "rsv": RULESET_VERSION,
                    "art": artifact_id,
                    "revid": revision_id,
                    "revno": 1,
                    "by": actor_user_id,
                    "now": stamp,
                    "note": note_final[:500],
                },
            ),
        )
        confirmation_id = int(confirmation_row.lastrowid or 0)

        for seq, result in enumerate(evaluations, start=1):
            session.execute(
                text(
                    "INSERT INTO ent_capacity_rule_check "
                    "(confirmation_id, rule_code, seq, detail, created_at) "
                    "VALUES (:cid, :code, :seq, :detail, :ts)"
                ),
                {
                    "cid": confirmation_id,
                    "code": result.rule_code,
                    "seq": seq,
                    "detail": result.detail[:500],
                    "ts": stamp,
                },
            )

        # 候选行的 status 是**派生状态**：权威来源是上面那条确认记录。
        # 写它是因为"这条运力确认了吗"是候选清单每行都要显示的东西，
        # 逐行回查确认表会让清单变成 N+1。只有本函数写这一列（见模块文档与用例）。
        session.execute(
            text(
                "UPDATE ent_capacity_candidate SET status = :status, updated_at = :ts WHERE id = :cid"
            ),
            {"status": CANDIDATE_STATUS_CONFIRMED, "ts": stamp, "cid": candidate_id},
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # ⚠️ **只把"确实已有确认行"的那一种翻译成 409**，其余原样抛出。
        #
        #    这里踩过一次：初版无条件 `raise CapacityStateError("...已被确认过...")`，
        #    于是**任何** IntegrityError 都被说成"已被确认过"。实测触发它的是一次
        #    漏传 `entrustment_id`（`ent_artifact.entrustment_id` NOT NULL）——
        #    两个并发线程**都**拿到"已被确认过"，而库里一条确认行都没有。
        #    把约束错误一律改写成语义错误，等于把"代码写错了"伪装成"业务不允许"，
        #    而那类伪装在下一次会以同样的面目出现（"并发时偶发 409，重试即可"）。
        found = get_confirmation_by_candidate(session, candidate_id=candidate_id)
        if found is None:
            raise
        raise CapacityStateError(
            f"候选运力 {candidate_id} 已被确认过（并发/重复请求被唯一约束挡住）—— "
            "一条候选只能确认一次",
            existing_confirmation_id=int(found["confirmation_id"]),
            existing_artifact_id=int(found["artifact_id"]),
        ) from exc

    created = get_confirmation(session, confirmation_id=confirmation_id)
    if created is None:
        raise CapacityError("确认记录写入后读不到（不该发生）")
    artifact = art.get_artifact(session, artifact_id)
    if artifact is None or str(artifact["artifact_type"]) != CONFIRM_TYPE:
        # 写读一致性自检：不留"有确认记录、没有采购确认成果"的悬空态
        raise CapacityError(
            "确认产出的成果类型不是采购确认（写读键名不一致）—— 已中止，不留悬空确认记录"
        )
    created["rule_check_count"] = len(evaluations)
    created["evaluations"] = evaluations
    return created


# ── 复算（只读）：这条确认现在还成立吗 ──────────────────────────────────────


def recheck_confirmation(
    session: Session, *, confirmation_id: int, as_of: date | None = None
) -> dict[str, Any]:
    """用**当前事实**重跑同一套规则，回答"这条确认现在还成立吗"。**不写任何行**。

    为什么把这个只读复算放进本切片：D1-09 要看到"900 吨候选在变更后不再适用"，
    而那句话的全部内容就是**同一套规则在换了一组输入之后不通过了**。
    把它放在这里复用 `evaluate`，是为了让 S4 不必再写第二份"适用性"定义 ——
    "适用"有两个定义时，两个结论不一致的那天无法判定谁对。

    `changed_fields`：当前值与确认当时冻结值的差异（**只比判定读到的那些列**）。
    没有它，"为什么不成立了"只能靠人对照两屏数据。
    """
    confirmation = get_confirmation(session, confirmation_id=confirmation_id)
    if confirmation is None:
        raise CapacityNotFoundError("确认记录不存在")

    assignment_id = int(confirmation["assignment_id"])
    candidate = get_candidate(session, candidate_id=int(confirmation["candidate_id"]))
    if candidate is None:
        # 候选行不该消失（没有删除路径），但真消失时**不得**当作"仍然有效"：
        # 判据没了 ⇒ 结论没了（未知保持未知）。
        return {
            "confirmation_id": confirmation_id,
            "still_valid": False,
            "as_of_date": (as_of or today_utc()).isoformat(),
            "candidate_missing": True,
            "changed_fields": [],
            "rule_checks": [],
            "frozen_rule_checks": list_rule_checks(session, confirmation_id=confirmation_id),
        }

    basis = as_of or today_utc()
    demand_tonnes, demand_unit, demand_ref = _assignment_demand(session, assignment_id)
    evaluations = evaluate(
        demand_tonnes=demand_tonnes,
        demand_unit=demand_unit,
        demand_ref=demand_ref,
        capacity_tonnes=candidate["capacity_tonnes"],
        vessel_count=candidate["vessel_count"],
        allows_partial_load=candidate["allows_partial_load"],
        valid_until=candidate["valid_until"],
        evidence_kind=candidate["evidence_kind"],
        evidence_ref=candidate["evidence_ref"],
        as_of=basis,
    )

    # 只比**判定读到的那几个值**：多比一个列，就会多出一条"变了但不影响结论"的噪音，
    # 让人以为确认失效了。
    current_inputs = {
        "demand_tonnes": _dec_text(_decimal(demand_tonnes), TONNE_PLACES),
        "demand_unit": demand_unit,
        "capacity_tonnes": _dec_text(_decimal(candidate["capacity_tonnes"]), TONNE_PLACES),
        "vessel_count": int(candidate["vessel_count"]),
        "allows_partial_load": bool(candidate["allows_partial_load"]),
        "valid_until": candidate["valid_until"],
        "evidence_kind": candidate["evidence_kind"],
        "evidence_ref": candidate["evidence_ref"],
    }
    frozen_inputs = {
        "demand_tonnes": _dec_text(_decimal(confirmation["demand_tonnes"]), TONNE_PLACES),
        "demand_unit": confirmation["demand_unit"],
        "capacity_tonnes": _dec_text(_decimal(confirmation["capacity_tonnes"]), TONNE_PLACES),
        "vessel_count": int(confirmation["vessel_count"]),
        "allows_partial_load": bool(confirmation["allows_partial_load"]),
        "valid_until": confirmation["valid_until"],
        "evidence_kind": confirmation["evidence_kind"],
        "evidence_ref": confirmation["evidence_ref"],
    }
    changed = sorted(k for k in frozen_inputs if current_inputs[k] != frozen_inputs[k])

    return {
        "confirmation_id": confirmation_id,
        "still_valid": not failed_rules(evaluations),
        "as_of_date": basis.isoformat(),
        "candidate_missing": False,
        "changed_fields": changed,
        "rule_checks": [
            {
                "rule_code": e.rule_code,
                "seq": seq,
                "outcome": e.outcome,
                "detail": e.detail,
            }
            for seq, e in enumerate(evaluations, start=1)
        ],
        "frozen_rule_checks": list_rule_checks(session, confirmation_id=confirmation_id),
    }


# ── 投影 ────────────────────────────────────────────────────────────────────


def project_confirmation(session: Session, confirmation: dict[str, Any]) -> dict[str, Any]:
    """确认详情：冻结输入 + 逐规则判定 + 指向的成果版本。

    **不做客户投影**：候选运力含供应商单价与承运人，属内部事实
    （`registry.procurement_confirm.internal_fields` 已把它们标为内部字段）。
    客户要看采购结论没有通道；客户看到的是**对客报价**的冻结快照。
    """
    out = {
        "confirmation_id": int(confirmation["confirmation_id"]),
        "assignment_id": int(confirmation["assignment_id"]),
        "entrustment_id": confirmation["entrustment_id"],
        "candidate_id": int(confirmation["candidate_id"]),
        "leg_id": confirmation["leg_id"],
        "carrier": str(confirmation["carrier"]),
        "vessel_name": confirmation["vessel_name"],
        "capacity_tonnes": confirmation["capacity_tonnes"],
        "vessel_count": int(confirmation["vessel_count"]),
        "allows_partial_load": bool(confirmation["allows_partial_load"]),
        "rate": confirmation["rate"],
        "rate_unit": confirmation["rate_unit"],
        "currency": confirmation["currency"],
        "valid_until": confirmation["valid_until"],
        "evidence_kind": confirmation["evidence_kind"],
        "evidence_ref": confirmation["evidence_ref"],
        "demand_tonnes": confirmation["demand_tonnes"],
        "demand_unit": confirmation["demand_unit"],
        "demand_ref": str(confirmation["demand_ref"]),
        "as_of_date": str(confirmation["as_of_date"]),
        "rule_set_version": str(confirmation["rule_set_version"]),
        "artifact_id": int(confirmation["artifact_id"]),
        "artifact_revision_id": int(confirmation["artifact_revision_id"]),
        "artifact_revision_no": int(confirmation["artifact_revision_no"]),
        "confirmed_by": int(confirmation["confirmed_by"]),
        "confirmed_at": str(confirmation["confirmed_at"]),
        "note": confirmation["note"],
        "rule_checks": list_rule_checks(
            session, confirmation_id=int(confirmation["confirmation_id"])
        ),
    }
    if "rule_check_count" in confirmation:
        out["rule_check_count"] = int(confirmation["rule_check_count"])
    return out


__all__ = [
    "ALL_CANDIDATE_STATUSES",
    "ALL_RULE_CODES",
    "CANDIDATE_STATUS_CANDIDATE",
    "CANDIDATE_STATUS_CONFIRMED",
    "CANDIDATE_STATUS_WITHDRAWN",
    "CONFIRM_TYPE",
    "CapacityError",
    "CapacityInvariantError",
    "CapacityNotFoundError",
    "CapacityRuleError",
    "CapacityStateError",
    "OUTCOME_FAIL",
    "OUTCOME_PASS",
    "RULESET_VERSION",
    "RULE_CAPACITY",
    "RULE_DEMAND_KNOWN",
    "RULE_EVIDENCE",
    "RULE_VALIDITY",
    "RuleResult",
    "TONNE_UNITS",
    "confirm_capacity",
    "evaluate",
    "failed_rules",
    "get_candidate",
    "get_confirmation",
    "get_confirmation_by_candidate",
    "list_candidates",
    "list_confirmations",
    "list_rule_checks",
    "project_confirmation",
    "recheck_confirmation",
    "record_candidate",
    "today_utc",
]
