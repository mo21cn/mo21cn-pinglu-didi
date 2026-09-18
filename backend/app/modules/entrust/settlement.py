"""结算版本与收付依据（合同 S4 段第 11 条 / HO 0918-2 裁定 Q5；§10.1 第 11 步）。

本模块回答一个问题：**客户确认的到底是哪一版结算？**

为什么"哪一版"是重点
--------------------
裁定 Q5 选了第四种口径：结案检查**客户对「最终适用结算版本」的确认**。
⛔ 原报价接受记录**不能替代** —— 它发生在履约前，证明不了客户接受后来产生的
等候费、费用调整与最终结算。

⇒ 于是「确认」必须挂在**一个具体版本的行**上，而不是挂在委托上、也不是挂在
"最新版本"这个**概念**上。这一条决定了本模块的全部结构：

    ⭐ **旧确认替不了新版本过关，不是靠一条 if 判断维持的，是靠"确认挂在哪一行"维持的。**

版本链
------

    （费用事实变化）
        ↓  create_settlement  ← 快照当时的"计入合计"费用行
    v1  draft ──approve──> approved ──customer-confirm──> accepted / rejected
        ↓ 再 create_settlement（费用变了就必须出新版，v1 **一个字不改**）
    v2  draft ──approve──> approved ──customer-confirm──> …
        ↑ 适用版本＝**最大版本号**那一行

`financial_status` 的派生（§5.3.2 的四条判据）
----------------------------------------------
四条"未结"的成因逐条落成 `blockers`；**四个都不成立**才给 `settled`：

| # | 成因 | 本模块的判据 |
| --- | --- | --- |
| 1 | 已有费用未结 | 存在 `draft` / `disputed` 的费用行 |
| 2 | 结算**待批准** | 没有适用版本；或适用版本未内部确认；或**客户未确认该精确版本**；或**适用版本已过期**（当前费用事实与它的快照不一致） |
| 3 | **未决争议** | 存在未关闭（`status != closed`）的案件 |
| 4 | **缺少收付依据** | 余额未结清（某方向累计收付 < 该方向合计）—— 本期"显式处置"只有"收付依据覆盖全额"一条路径 |

⚠️ **`not_started` 不是"派生还没接好"的遮羞布**：它**只能**表示
"这张委托上确实还没有任何费用 / 结算 / 收付事实"。本模块接通后，
`API` 侧才允许展示该字段（`ent_assignment_completion.py` 写明"派生未接通时不展示"）。

⛔ **本模块不做的事**：不接真实支付（`mode` 恒 `labeled_sample`）；不做多币种兑换、
税务、发票、银行接入、授信、通用会计账簿（HO 边界 4）；**不建**"完成确认"回路（Q5 明令）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust import charges as charge_svc

# ── 版本状态 ────────────────────────────────────────────────────────────────

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
ALL_STATUSES = (STATUS_DRAFT, STATUS_APPROVED)

# ── 客户决定 ────────────────────────────────────────────────────────────────

DECISION_ACCEPTED = "accepted"
DECISION_REJECTED = "rejected"
ALL_DECISIONS = (DECISION_ACCEPTED, DECISION_REJECTED)

# ── 收付证据模式 ────────────────────────────────────────────────────────────
# ⚠️ 合法取值**只有一个**：与合同签署证据同一口径（`offers.SIGNATURE_MODE_LABELED_SAMPLE`）。
# 让调用方能传 `live` 就等于让系统自称"资金已真实到账"，与裁定 Q5 第 5 条直接冲突。

MODE_LABELED_SAMPLE = "labeled_sample"

# ── `financial_status` 派生取值（§5.3 的取值域，一字不改）────────────────────

FINANCIAL_NOT_STARTED = "not_started"
FINANCIAL_OPEN = "open"
FINANCIAL_SETTLED = "settled"

# ── 对客投影白名单（裁定 Q5 第 2 条）────────────────────────────────────────
# ⛔ 用**白名单**而不是黑名单：`ent_charge` 以后加列时，黑名单会**默认**把新列漏给客户，
# 而漏出去的内部成本是收不回来的。白名单的默认是"不出去"。
CUSTOMER_LINE_FIELDS = (
    "charge_id",
    "charge_kind",
    "quantity",
    "unit",
    "amount",
    "currency",
    "counterparty",
    "basis",
)

#: 对客方向：**只有应收**出客户面（`payable` 是我们付给供应商的内部成本）。
CUSTOMER_VISIBLE_DIRECTION = charge_svc.DIRECTION_RECEIVABLE

_SCALE = Decimal("0.0001")
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

_SETTLEMENT_COLS = (
    "s.id, s.assignment_id, s.version_no, s.status, s.lines, s.currency, "
    "s.customer_total, s.internal_total, s.approved_by, s.approved_at, "
    "s.customer_confirmed_by, s.customer_confirmed_at, s.customer_decision, "
    "s.customer_note, s.revision, s.created_by, s.created_at, s.updated_at, "
    "a.owner_user_id AS owner_user_id, a.org_id AS org_id, a.status AS assignment_status"
)


# ── 异常（HTTP 层按语义映射） ────────────────────────────────────────────────


class SettlementError(RuntimeError):
    """结算服务的基础异常。"""


class SettlementNotFoundError(SettlementError):
    """结算版本不存在，或调用方无权知晓其存在。HTTP 层应转 404。"""


class SettlementValidationError(SettlementError):
    """入参或业务规则不合法（无费用行 / 多币种 / 方向未知 / 超收付）。→400。"""


class SettlementStateError(SettlementError):
    """当前状态不允许该操作（未批准就确认、已确认再确认、版本过期等）。→409。"""


class SettlementCustomerOnlyError(SettlementError):
    """这个动作**只能由客户本人**做 —— 经理人不得代客户确认。→403。"""


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FORMAT)


def _dec_text(value: Any) -> str | None:
    """金额 → 字符串（按列定点位数 `quantize`，**不经 float**）。"""
    return charge_svc.money_text(None if value is None else Decimal(str(value)))


def _row_to_settlement(row: Any) -> dict[str, Any]:
    return {
        "settlement_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "version_no": int(row["version_no"]),
        "status": str(row["status"]),
        "lines": json.loads(row["lines"]) if row["lines"] else [],
        "currency": str(row["currency"]),
        "customer_total": _dec_text(row["customer_total"]),
        "internal_total": _dec_text(row["internal_total"]),
        "approved_by": int(row["approved_by"]) if row["approved_by"] is not None else None,
        "approved_at": row["approved_at"],
        "customer_confirmed_by": (
            int(row["customer_confirmed_by"]) if row["customer_confirmed_by"] is not None else None
        ),
        "customer_confirmed_at": row["customer_confirmed_at"],
        "customer_decision": row["customer_decision"],
        "customer_note": row["customer_note"],
        "revision": int(row["revision"]),
        "created_by": int(row["created_by"]) if row["created_by"] is not None else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        # 授权上下文（不投影给前端，仅服务层使用）
        "owner_user_id": int(row["owner_user_id"]),
        "org_id": int(row["org_id"]) if row["org_id"] is not None else None,
        "assignment_status": str(row["assignment_status"]),
    }


# ── 读 ──────────────────────────────────────────────────────────────────────


def get_settlement(session: Session, *, settlement_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                f"SELECT {_SETTLEMENT_COLS} FROM ent_settlement s "
                "JOIN ent_assignment a ON a.id = s.assignment_id WHERE s.id = :sid"
            ),
            {"sid": settlement_id},
        )
        .mappings()
        .first()
    )
    return _row_to_settlement(row) if row is not None else None


def list_settlements(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托的**全部版本**，按版本号升序（旧版本不删，历史可读）。"""
    rows = (
        session.execute(
            text(
                f"SELECT {_SETTLEMENT_COLS} FROM ent_settlement s "
                "JOIN ent_assignment a ON a.id = s.assignment_id "
                "WHERE s.assignment_id = :aid ORDER BY s.version_no ASC"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_settlement(row) for row in rows]


def applicable_settlement(session: Session, *, assignment_id: int) -> dict[str, Any] | None:
    """**适用版本** ＝ 最大版本号那一行。

    ⚠️ 它是**推导**出来的，不是存下来的："哪个版本适用"如果也存一个字段，
    就会出现"字段说有、链上没有"的分叉。最大版本号只有一个来源（`version_no`）。
    """
    versions = list_settlements(session, assignment_id=assignment_id)
    return versions[-1] if versions else None


def list_payments(session: Session, *, settlement_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT id, settlement_id, assignment_id, direction, amount, currency, "
                "occurred_at, mode, ref, note, recorded_by, recorded_at "
                "FROM ent_settlement_payment WHERE settlement_id = :sid ORDER BY id ASC"
            ),
            {"sid": settlement_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_payment(row) for row in rows]


def _row_to_payment(row: Any) -> dict[str, Any]:
    return {
        "payment_id": int(row["id"]),
        "settlement_id": int(row["settlement_id"]),
        "assignment_id": int(row["assignment_id"]),
        "direction": str(row["direction"]),
        "amount": _dec_text(row["amount"]),
        "currency": str(row["currency"]),
        "occurred_at": row["occurred_at"],
        "mode": str(row["mode"]),
        "ref": row["ref"],
        "note": row["note"],
        "recorded_by": int(row["recorded_by"]),
        "recorded_at": row["recorded_at"],
    }


def _all_payments(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托上的**全部**收付依据（**跨版本**）。

    ⭐ 收付挂在版本上是为了说清"依据哪一版"，而**余额是委托级的** ——
    出了新版本，已经收到的钱不会退回来。把余额按"单版本"算，就会出现
    "v1 收了 8 万、v2 又从零开始"的假象。
    """
    rows = (
        session.execute(
            text(
                "SELECT id, settlement_id, assignment_id, direction, amount, currency, "
                "occurred_at, mode, ref, note, recorded_by, recorded_at "
                "FROM ent_settlement_payment WHERE assignment_id = :aid ORDER BY id ASC"
            ),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_payment(row) for row in rows]


# ── 快照 ────────────────────────────────────────────────────────────────────


def _snapshot(counted: list[tuple[dict[str, Any], Decimal]]) -> list[dict[str, Any]]:
    """把"此刻计入合计"的费用行**冻结**成快照。

    快照必须**自足**：费用行后来被改（`revision` 变了）时，这一版仍要说得清
    "我当时依据的是哪条、当时算多少钱"。所以每条都带 `revision` 与**计入金额**。
    """
    out: list[dict[str, Any]] = []
    for row, amount in counted:
        out.append(
            {
                "charge_id": int(row["charge_id"]),
                "revision": int(row["revision"]),
                "direction": str(row["direction"]),
                "charge_kind": str(row["charge_kind"]),
                "quantity": row["quantity"],
                "unit": row["unit"],
                "amount": _dec_text(amount),
                "currency": str(row["currency"]),
                "counterparty": row["counterparty"],
                "basis": row["basis"],
            }
        )
    return out


def _snapshot_total(lines: list[dict[str, Any]], *, direction: str) -> Decimal:
    total = Decimal(0)
    for line in lines:
        if line["direction"] != direction:
            continue
        total += Decimal(str(line["amount"]))
    return total


def _snapshot_signature(lines: list[dict[str, Any]]) -> set[tuple[int, int, str]]:
    """快照的**身份指纹**：`(charge_id, revision, 计入金额)` 的集合。

    用来判"费用事实是否已经变了"。只比 `charge_id` 不够：同一条费用行的金额被改过
    （走 dispute → resolve 调减）时 id 没变、**钱变了** —— 只比 id 会漏掉这种
    "改过金额"的场景，而那恰恰是 Q5 点名要防的（"确认后修改相关费用"）。
    """
    return {(int(line["charge_id"]), int(line["revision"]), str(line["amount"])) for line in lines}


# ── 命令 ────────────────────────────────────────────────────────────────────


def create_settlement(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按**当前**计入合计的费用行生成一个**新版本**（`draft`）。

    费用变了就再调一次 —— **旧版本一个字不改**（旧确认因此保留）。

    Raises:
        SettlementValidationError: 没有可结算的费用行；或计入行跨多个币种。
        SettlementStateError: 委托不在 `claimed`（受理前的委托没有责任主体）。
    """
    current = now or utcnow_naive()
    assignment = (
        session.execute(
            text("SELECT id, status FROM ent_assignment WHERE id = :aid"), {"aid": assignment_id}
        )
        .mappings()
        .first()
    )
    if assignment is None:
        raise SettlementNotFoundError(f"委托 {assignment_id} 不存在")
    if str(assignment["status"]) != "claimed":
        raise SettlementStateError(
            f"委托状态为 {assignment['status']}，结算只在委托为 claimed 时生成"
            "（受理前没有责任主体，结案后不再出新版本）"
        )

    counted = charge_svc.counted_charges(session, assignment_id=assignment_id)
    if not counted:
        raise SettlementValidationError(
            "这张委托上还没有**计入合计**的费用行，不能生成结算版本"
            "（草稿与有争议的费用行不进合计；先确认费用再出结算）"
        )
    lines = _snapshot(counted)
    currencies = sorted({line["currency"] for line in lines})
    if len(currencies) > 1:
        raise SettlementValidationError(
            f"计入合计的费用行跨了 {len(currencies)} 个币种（{'、'.join(currencies)}），"
            "而一个结算版本只有一个币种 —— 请先按币种拆开"
            "（DEMO-1 仅支持 CNY，跨币种相加是明确不做的）"
        )
    currency = currencies[0]

    customer_total = _snapshot_total(lines, direction=charge_svc.DIRECTION_RECEIVABLE)
    internal_total = _snapshot_total(lines, direction=charge_svc.DIRECTION_PAYABLE)

    next_no = int(
        session.execute(
            text(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM ent_settlement "
                "WHERE assignment_id = :aid"
            ),
            {"aid": assignment_id},
        ).scalar()
        or 1
    )
    insert = cast(
        CursorResult[Any],
        session.execute(
            text(
                "INSERT INTO ent_settlement "
                "(assignment_id, version_no, status, lines, currency, customer_total, "
                " internal_total, approved_by, approved_at, customer_confirmed_by, "
                " customer_confirmed_at, customer_decision, customer_note, revision, "
                " created_by, created_at, updated_at) "
                "VALUES (:aid, :vno, :status, :lines, :cur, :ct, :it, NULL, NULL, NULL, "
                " NULL, NULL, NULL, 1, :actor, :ts, :ts)"
            ),
            {
                "aid": assignment_id,
                "vno": next_no,
                "status": STATUS_DRAFT,
                "lines": json.dumps(lines, ensure_ascii=False),
                "cur": currency,
                "ct": str(customer_total),
                "it": str(internal_total),
                "actor": actor_id,
                "ts": _fmt(current),
            },
        ),
    )
    new_id = int(insert.lastrowid or 0)
    session.commit()
    created = get_settlement(session, settlement_id=new_id)
    assert created is not None
    return created


def _transition(
    session: Session,
    *,
    settlement_id: int,
    from_statuses: tuple[str, ...],
    sets: list[str],
    params: dict[str, Any],
    expected_revision: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """条件更新 ＋ `revision` 乐观锁；`rowcount == 0` ⇒ 409。

    与 S7-1 的费用行同一口径：**两个写者同时确认版本时，必须有一个拿到 409**，
    不许后者静默覆盖前者。
    """
    current = now or utcnow_naive()
    placeholders = ", ".join(f":st_{i}" for i in range(len(from_statuses)))
    full: dict[str, Any] = {"sid": settlement_id, "ts": _fmt(current), **params}
    full.update({f"st_{i}": value for i, value in enumerate(from_statuses)})
    where = f"id = :sid AND status IN ({placeholders})"
    if expected_revision is not None:
        where += " AND revision = :rev"
        full["rev"] = int(expected_revision)
    result = cast(
        CursorResult[Any],
        session.execute(
            text(
                f"UPDATE ent_settlement SET {', '.join(sets)}, revision = revision + 1, "
                f"updated_at = :ts WHERE {where}"
            ),
            full,
        ),
    )
    if int(result.rowcount or 0) == 0:
        session.rollback()
        fresh = get_settlement(session, settlement_id=settlement_id)
        if fresh is None:
            raise SettlementNotFoundError(f"结算版本 {settlement_id} 不存在")
        raise SettlementStateError(
            f"结算版本 {settlement_id} 当前状态为 {fresh['status']}、版本 {fresh['revision']}，"
            "不允许该操作（状态不对，或你自己的版本已过期）"
        )
    session.commit()
    updated = get_settlement(session, settlement_id=settlement_id)
    assert updated is not None
    return updated


def approve_settlement(
    session: Session,
    *,
    settlement_id: int,
    actor_id: int,
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """**内部确认**（`draft → approved`）。

    ⚠️ 只能确认**适用版本**（最大版本号那一行）：批准一个已经被新版取代的版本，
    在业务上没有任何意义，只会让"适用版本是批准了吗"多出一个答案。
    """
    settlement = get_settlement(session, settlement_id=settlement_id)
    if settlement is None:
        raise SettlementNotFoundError(f"结算版本 {settlement_id} 不存在")
    applicable = applicable_settlement(session, assignment_id=settlement["assignment_id"])
    if applicable is None or applicable["settlement_id"] != settlement_id:
        latest = applicable["version_no"] if applicable else "（无）"
        raise SettlementStateError(
            f"结算版本 v{settlement['version_no']} 不是这张委托的适用版本"
            f"（适用版本＝最大版本号，当前是 v{latest}）—— "
            "出厂顺序有意义：确认一个已被取代的版本，会让「适用版本批准了吗」有两个答案"
        )
    return _transition(
        session,
        settlement_id=settlement_id,
        from_statuses=(STATUS_DRAFT,),
        sets=["status = :to", "approved_by = :actor", "approved_at = :ts"],
        params={"to": STATUS_APPROVED, "actor": actor_id},
        expected_revision=expected_revision,
        now=now,
    )


def confirm_settlement(
    session: Session,
    *,
    settlement_id: int,
    customer_user_id: int,
    decision: str,
    note: str | None = None,
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """**客户确认该精确版本**（裁定 Q5 第 1、4 条）。

    三条硬约束：

    1. ⛔ **只能由客户本人做** —— 经理人不得代客户确认（"委托是替客户办事，
       运营方单方面宣布完成会让完成失去对客效力"）。非货主 ⇒ `SettlementCustomerOnlyError`（403）。
    2. **必须先内部确认**：`draft` 上的客户确认没有意义（我们还没确认这是我们的口径）。
    3. **一版只确认一次**：已有决定 ⇒ 409。想改口径就出**新版本** ——
       允许改写会让"客户当时接受了什么"变成可编辑的，而它正是结案的依据。
    """
    if decision not in ALL_DECISIONS:
        raise SettlementValidationError(
            f"未知客户决定 {decision!r}；取值域：{sorted(ALL_DECISIONS)}"
        )
    settlement = get_settlement(session, settlement_id=settlement_id)
    if settlement is None:
        raise SettlementNotFoundError(f"结算版本 {settlement_id} 不存在")
    if settlement["owner_user_id"] != customer_user_id:
        raise SettlementCustomerOnlyError(
            f"结算版本 {settlement_id} 的客户是用户 {settlement['owner_user_id']}，"
            f"当前用户 {customer_user_id} 不是客户本人 ⇒ 不能代客户确认"
        )
    if settlement["customer_confirmed_at"] is not None:
        raise SettlementStateError(
            f"结算版本 v{settlement['version_no']} 的客户决定已记录为 "
            f"{settlement['customer_decision']}，一版只确认一次"
            "（要改口径请生成新版本，旧确认保留但不能替新版本过关）"
        )
    return _transition(
        session,
        settlement_id=settlement_id,
        from_statuses=(STATUS_APPROVED,),
        sets=[
            "customer_confirmed_by = :actor",
            "customer_confirmed_at = :ts",
            "customer_decision = :decision",
            "customer_note = :note",
        ],
        params={
            "actor": customer_user_id,
            "decision": decision,
            "note": (note or "").strip() or None,
        },
        expected_revision=expected_revision,
        now=now,
    )


def record_payment(
    session: Session,
    *,
    settlement_id: int,
    actor_id: int,
    direction: str,
    amount: Any,
    ref: str,
    currency: str | None = None,
    occurred_at: Any = None,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """记一条**收付依据**（合同 S4 段第 11 条；`mode` 恒为合成样本）。

    四条口径：

    * 只能挂在**已批准**的版本上（收付依据要说清依据哪一版）；
    * `mode` **不是入参** —— 恒 `labeled_sample`，让调用方能传 `live` 就等于让系统自称
      "资金已真实到账"（裁定 Q5 第 5 条：不接真实支付、不宣称真实资金到账）；
    * `ref` 必填（没有凭据的收付不可核对）；
    * 累计收付**不得超过该方向合计** —— 超出会让余额变成负数，而负数余额没有任何业务含义。
    """
    if direction not in charge_svc.ALL_DIRECTIONS:
        raise SettlementValidationError(
            f"未知收付方向 {direction!r}；取值域：{sorted(charge_svc.ALL_DIRECTIONS)}"
        )
    cleaned_ref = str(ref or "").strip()
    if not cleaned_ref:
        raise SettlementValidationError("收付凭据引用（ref）必填 —— 没有凭据的收付不可核对")
    try:
        value = Decimal(str(amount))
    except Exception as exc:
        raise SettlementValidationError(f"金额无法解析：{amount!r}") from exc
    if value <= 0:
        raise SettlementValidationError(f"收付金额必须为正数，实际 {value}")

    settlement = get_settlement(session, settlement_id=settlement_id)
    if settlement is None:
        raise SettlementNotFoundError(f"结算版本 {settlement_id} 不存在")
    if settlement["status"] != STATUS_APPROVED:
        raise SettlementStateError(
            f"结算版本 v{settlement['version_no']} 状态为 {settlement['status']}，"
            "只有内部已确认的版本才能记收付依据"
        )
    if currency is not None and str(currency) != settlement["currency"]:
        raise SettlementValidationError(
            f"收付币种 {currency!r} 与结算版本币种 {settlement['currency']!r} 不一致"
            "（跨币种不相加，本期不做兑换）"
        )

    direction_total = _snapshot_total(settlement["lines"], direction=direction)
    already = sum(
        (
            Decimal(str(p["amount"]))
            for p in _all_payments(session, assignment_id=settlement["assignment_id"])
            if p["direction"] == direction
        ),
        Decimal(0),
    )
    if already + value > direction_total:
        raise SettlementValidationError(
            f"{direction} 方向的累计收付将达 {(already + value)}，"
            f"超过该方向合计 {direction_total}（已记 {already}）—— "
            "超出会让余额变成负数，而负数余额没有业务含义"
        )

    current = now or utcnow_naive()
    insert = cast(
        CursorResult[Any],
        session.execute(
            text(
                "INSERT INTO ent_settlement_payment "
                "(settlement_id, assignment_id, direction, amount, currency, occurred_at, "
                " mode, ref, note, recorded_by, recorded_at) "
                "VALUES (:sid, :aid, :direction, :amount, :cur, :occurred, :mode, :ref, :note, "
                " :actor, :ts)"
            ),
            {
                "sid": settlement_id,
                "aid": settlement["assignment_id"],
                "direction": direction,
                "amount": str(value),
                "cur": settlement["currency"],
                "occurred": _parse_ts(occurred_at),
                "mode": MODE_LABELED_SAMPLE,
                "ref": cleaned_ref,
                "note": (note or "").strip() or None,
                "actor": actor_id,
                "ts": _fmt(current),
            },
        ),
    )
    session.commit()
    payment_id = int(insert.lastrowid or 0)
    for row in list_payments(session, settlement_id=settlement_id):
        if row["payment_id"] == payment_id:
            return row
    raise SettlementError(f"收付记录 {payment_id} 写入后读不到")


def _parse_ts(raw: Any) -> str | None:
    """业务发生时间 → `YYYY-MM-DD HH:MM:SS`；**空值原样回 `None`**（不用"现在"顶上）。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        moment = raw
    else:
        value = str(raw).strip()
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SettlementValidationError(
                f"业务发生时间无法解析：{raw!r}（应给 ISO 8601，例如 2026-09-18T09:30:00）"
            ) from exc
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return _fmt(moment)


# ── 对客投影（白名单）──────────────────────────────────────────────────────


def customer_view(session: Session, *, settlement_id: int) -> dict[str, Any]:
    """**客户侧投影**：只出对客（应收）费用行，字段按**白名单**裁剪。

    裁定 Q5 第 2 条：客户只看对客费用及证据白名单，**不暴露内部供应商成本**。
    ⛔ 投影是**新建字典**，不是"从内部投影里删几个键" —— 后者在加列时会默认漏出去。
    """
    settlement = get_settlement(session, settlement_id=settlement_id)
    if settlement is None:
        raise SettlementNotFoundError(f"结算版本 {settlement_id} 不存在")
    applicable = applicable_settlement(session, assignment_id=settlement["assignment_id"])
    lines: list[dict[str, Any]] = []
    for raw in settlement["lines"]:
        if raw.get("direction") != CUSTOMER_VISIBLE_DIRECTION:
            continue
        lines.append({key: raw.get(key) for key in CUSTOMER_LINE_FIELDS})
    return {
        "settlement_id": settlement["settlement_id"],
        "assignment_id": settlement["assignment_id"],
        "version_no": settlement["version_no"],
        "is_applicable": bool(
            applicable and applicable["settlement_id"] == settlement["settlement_id"]
        ),
        "status": settlement["status"],
        "currency": settlement["currency"],
        "total": settlement["customer_total"],
        "line_count": len(lines),
        "lines": lines,
        "customer_confirmed_at": settlement["customer_confirmed_at"],
        "customer_decision": settlement["customer_decision"],
    }


# ── `financial_status` 派生（§5.3.2）───────────────────────────────────────


def _open_cases(session: Session, *, assignment_id: int) -> tuple[int, list[int]]:
    """未关闭的案件数与**前 20 个** id。

    返回 `(总数, 样本 id)`：总数用 `COUNT(*)` 单独取，**不靠"取回来几条"推断** ——
    取回被截断时，前者的结论仍然对（这正是 O-9 那一族缺陷的教训）。
    判据与 `exceptions` 模块一致：未关闭 ＝ `status != closed`。
    """
    total = int(
        session.execute(
            text(
                "SELECT COUNT(*) FROM ent_exception WHERE assignment_id = :aid AND status != :closed"
            ),
            {"aid": assignment_id, "closed": "closed"},
        ).scalar()
        or 0
    )
    if total == 0:
        return 0, []
    ids = [
        int(r[0])
        for r in session.execute(
            text(
                "SELECT id FROM ent_exception WHERE assignment_id = :aid AND status != :closed "
                "ORDER BY id ASC LIMIT 20"
            ),
            {"aid": assignment_id, "closed": "closed"},
        ).all()
    ]
    return total, ids


def _balances(
    settlement: dict[str, Any] | None, payments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按方向算余额：`合计 − 已收付`（**跨版本**累计，见 `_all_payments`）。"""
    out: list[dict[str, Any]] = []
    for direction in charge_svc.ALL_DIRECTIONS:
        total = (
            _snapshot_total(settlement["lines"], direction=direction)
            if settlement is not None
            else Decimal(0)
        )
        paid = sum(
            (Decimal(str(p["amount"])) for p in payments if p["direction"] == direction),
            Decimal(0),
        )
        out.append(
            {
                "direction": direction,
                "currency": settlement["currency"] if settlement is not None else None,
                "total": _dec_text(total),
                "settled": _dec_text(paid),
                "outstanding": _dec_text(total - paid),
            }
        )
    return out


def derive_financial_status(session: Session, *, assignment_id: int) -> dict[str, Any]:
    """`financial_status` 的**唯一**一处派生（§5.3.2；HO 明令不得只按余额判定）。

    四条"未结"的成因逐条落成 `blockers`；**四个都不成立**才给 `settled`。
    ⚠️ `not_started` 只能表示"确实还没有任何费用/结算/收付事实"，
    ⛔ **不是**"派生还没接好"的遮羞布。
    """
    charges = charge_svc.list_charges(session, assignment_id=assignment_id)
    settlements = list_settlements(session, assignment_id=assignment_id)
    payments = _all_payments(session, assignment_id=assignment_id)

    # ⚠️ `not_started` **提前返回**，不列 blocker：一件事都还没开始的时候，
    # "还没有结算版本"是**同义反复**而不是"待办"—— 把它列出来只会让读的人
    # 以为"已经有结算了、只差批准"。状态本身就是答案。
    if not charges and not settlements and not payments:
        return {
            "assignment_id": assignment_id,
            "financial_status": FINANCIAL_NOT_STARTED,
            "blockers": [],
            "applicable_settlement": None,
            "balances": _balances(None, []),
            "settlement_versions": 0,
            "charge_count": 0,
            "payment_count": 0,
        }

    applicable = settlements[-1] if settlements else None
    balances = _balances(applicable, payments)
    blockers: list[dict[str, Any]] = []

    # ① 已有费用未结：草稿与有争议的费用行都还没有结论。
    unsettled = [
        c["charge_id"]
        for c in charges
        if c["status"] in (charge_svc.STATUS_DRAFT, charge_svc.STATUS_DISPUTED)
    ]
    if unsettled:
        blockers.append(
            {
                "code": "unsettled_charges",
                "message": (
                    f"有 {len(unsettled)} 条费用行还没有结论"
                    "（草稿未确认 / 有争议未处置），合计此刻不是最终口径"
                ),
                "detail": {"charge_ids": unsettled[:20], "count": len(unsettled)},
            }
        )

    # ② 结算待批准（含"客户未确认该精确版本"与"适用版本已过期"两种子情形）。
    if applicable is None:
        blockers.append(
            {
                "code": "settlement_missing",
                "message": "还没有结算版本 —— 客户没有可确认的对象",
                "detail": {},
            }
        )
    elif applicable["status"] != STATUS_APPROVED:
        blockers.append(
            {
                "code": "settlement_not_approved",
                "message": (
                    f"适用结算版本 v{applicable['version_no']} 尚未内部确认"
                    f"（当前 {applicable['status']}）"
                ),
                "detail": {"settlement_id": applicable["settlement_id"]},
            }
        )
    elif not (
        applicable["customer_confirmed_at"] is not None
        and applicable["customer_decision"] == DECISION_ACCEPTED
    ):
        blockers.append(
            {
                "code": "customer_not_confirmed",
                "message": (
                    f"客户尚未确认适用结算版本 v{applicable['version_no']}"
                    "—— 原报价接受记录**不能替代**（它发生在履约前）"
                ),
                "detail": {
                    "settlement_id": applicable["settlement_id"],
                    "customer_decision": applicable["customer_decision"],
                },
            }
        )
    else:
        # ⭐ 适用版本是否**仍然对应当前费用事实**。
        # 这一格是"确认后修改相关费用必须形成新版本"从**口号**变成**机制**的地方：
        # 没有它，改完费用后旧版本的快照仍然"看起来没问题"，结案会拿一份过期口径过关。
        current_counted = charge_svc.counted_charges(session, assignment_id=assignment_id)
        current_lines = _snapshot(current_counted)
        if _snapshot_signature(current_lines) != _snapshot_signature(applicable["lines"]):
            blockers.append(
                {
                    "code": "settlement_stale",
                    "message": (
                        f"适用结算版本 v{applicable['version_no']} 的快照与当前费用事实不一致"
                        "（费用被新增 / 改动 / 撤出过）⇒ 必须生成新版本并重新取得客户确认"
                    ),
                    "detail": {
                        "settlement_id": applicable["settlement_id"],
                        "version_no": applicable["version_no"],
                    },
                }
            )

    # ③ 未决争议 / 异常处置。
    open_total, open_ids = _open_cases(session, assignment_id=assignment_id)
    if open_total:
        blockers.append(
            {
                "code": "open_cases",
                "message": f"有 {open_total} 条案件尚未关闭（未决的争议 / 异常处置）",
                "detail": {"case_ids": open_ids, "count": open_total},
            }
        )

    # ④ 缺少收付依据 = 余额未结清（本期"显式处置"只有"收付依据覆盖全额"一条路径）。
    outstanding = [b for b in balances if Decimal(str(b["outstanding"])) != 0]
    if outstanding:
        blockers.append(
            {
                "code": "balance_unsettled",
                "message": (
                    "未结余额非零："
                    + "、".join(
                        f"{b['direction']} 尚差 {b['outstanding']} {b['currency'] or ''}".strip()
                        for b in outstanding
                    )
                    + "（余额为零不等于已结清，但余额非零一定未结清）"
                ),
                "detail": {"balances": balances},
            }
        )

    status = FINANCIAL_OPEN if blockers else FINANCIAL_SETTLED

    return {
        "assignment_id": assignment_id,
        "financial_status": status,
        "blockers": blockers,
        "applicable_settlement": (
            {
                "settlement_id": applicable["settlement_id"],
                "version_no": applicable["version_no"],
                "status": applicable["status"],
                "customer_decision": applicable["customer_decision"],
                "customer_confirmed_at": applicable["customer_confirmed_at"],
            }
            if applicable is not None
            else None
        ),
        "balances": balances,
        "settlement_versions": len(settlements),
        "charge_count": len(charges),
        "payment_count": len(payments),
    }


__all__ = [
    "ALL_DECISIONS",
    "ALL_STATUSES",
    "CUSTOMER_LINE_FIELDS",
    "CUSTOMER_VISIBLE_DIRECTION",
    "DECISION_ACCEPTED",
    "DECISION_REJECTED",
    "FINANCIAL_NOT_STARTED",
    "FINANCIAL_OPEN",
    "FINANCIAL_SETTLED",
    "MODE_LABELED_SAMPLE",
    "STATUS_APPROVED",
    "STATUS_DRAFT",
    "SettlementCustomerOnlyError",
    "SettlementError",
    "SettlementNotFoundError",
    "SettlementStateError",
    "SettlementValidationError",
    "applicable_settlement",
    "approve_settlement",
    "confirm_settlement",
    "create_settlement",
    "customer_view",
    "derive_financial_status",
    "get_settlement",
    "list_payments",
    "list_settlements",
    "record_payment",
    "utcnow_naive",
]
