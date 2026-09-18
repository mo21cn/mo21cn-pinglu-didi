"""费用行（§10.1 第 10 步 / 合同 S4 段第 9–10 条）—— 应收/应付、争议与处置。

本模块只回答一件事：**这张委托上有哪些费用行、它们各自算不算进合计**。
它**不**回答"结算该怎么批、余额算不算结清"（那是 S7-3）；也**不**发对客投影
（客户只看对客费用与证据白名单 —— 裁定 Q5 第 2 条，落在 S7-3）。

「计入合计」的规则是**从合同条文推导的**，不是本模块自订的口径
------------------------------------------------------------
| 状态 | 是否计入 | 依据 |
| --- | --- | --- |
| `draft` | 否 | 还没确认的草稿不能进合计（合同 S4 段第 9 条要求行上带 `status`） |
| `confirmed` | 是（按 `amount`） | 已确认即计入 |
| `disputed` | **否** | 合同 S4 段第 10 条原文：`Keep disputed charges out of confirmed totals` |
| `resolved` | **看 `counts_in_total`** | 裁定 Q2=B：`resolved` 一词**决定不了**是否计入，必须显式给出 |
| `rejected` | 否 | 被拒绝的行不是"待收/待付" |

⭐ 关键的一处刻意为难：`resolved` 行的计入与否**只读 `counts_in_total`**，
**绝不**从"有没有 `resolution_amount`"或"状态是不是 resolved"反推 ——
把三个独立事实（处置了 / 金额定了 / 计不计入）压成一个，正是裁定要避免的歧义。

金额一律 `Decimal`
------------------
入参与出参都走 `Decimal`：`amount` / `resolution_amount` / 合计值都**不经过 float**。
出参用**字符串**（`_dec_text`）—— 跨语言的消费者（小程序 JSON）拿到的是字面值，
不会因 IEEE754 丢精度。

并发
----
每次状态变更都是 `WHERE id = :id AND revision = :expected AND status = :from` 的**条件更新**；
`rowcount == 0` 一律 409（不许后者静默覆盖前者 —— 与合同 S4 段第 3 条对结案的要求同源）。
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

#: 收付方向。合计**按方向分开**（裁定 Q1=C）—— 应收与应付相加没有业务含义。
DIRECTION_RECEIVABLE = "receivable"
DIRECTION_PAYABLE = "payable"
ALL_DIRECTIONS = (DIRECTION_RECEIVABLE, DIRECTION_PAYABLE)

STATUS_DRAFT = "draft"
STATUS_CONFIRMED = "confirmed"
STATUS_DISPUTED = "disputed"
STATUS_RESOLVED = "resolved"
STATUS_REJECTED = "rejected"
ALL_STATUSES = (STATUS_DRAFT, STATUS_CONFIRMED, STATUS_DISPUTED, STATUS_RESOLVED, STATUS_REJECTED)

OUTCOME_ACCEPTED = "accepted"
OUTCOME_ADJUSTED = "adjusted"
OUTCOME_REJECTED = "rejected"
ALL_OUTCOMES = (OUTCOME_ACCEPTED, OUTCOME_ADJUSTED, OUTCOME_REJECTED)

#: 裁定 Q3=A：`waiting_time` 是**普通费用行**的取值，不另建实体、不建计费引擎。
#: ⛔ 它**不是**白名单：`charge_kind` 是自由字符串，未登记的取值原样保留
#: （与 `plan.MODE_LABELS` 对 `mode` 的处置同型）。
CHARGE_KIND_WAITING_TIME = "waiting_time"

#: 演示期只支持 CNY（裁定 Q1 的最小实现边界）。⛔ 不做汇率折算 —— 合计按币种分开。
DEFAULT_CURRENCY = "CNY"

_COLS = (
    "id, assignment_id, direction, charge_kind, quantity, unit, amount, currency, "
    "counterparty, basis, status, disputed_reason, resolution_outcome, resolution_amount, "
    "counts_in_total, resolution_method, resolution_evidence_ref, resolved_by, resolved_at, "
    "revision, created_by, created_at, updated_at"
)


class ChargeError(RuntimeError):
    """费用行的领域错误（HTTP 层转 400）。"""


class ChargeNotFoundError(ChargeError):
    """费用行不存在（HTTP 层转 404；**不**区分"不存在"与"无权"）。"""


class ChargeStateError(ChargeError):
    """状态/并发冲突（HTTP 层转 409）。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 `ent_` 表时间字段的存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _dec(value: Any, *, field: str, allow_none: bool = False) -> Decimal | None:
    """入参 → `Decimal`。空值在 `allow_none=False` 时报错（而不是静默当 0）。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_none:
            return None
        raise ChargeError(f"{field}必填")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ChargeError(f"{field}不是合法数字：{value!r}") from exc
    if not out.is_finite():
        raise ChargeError(f"{field}必须是有限数字：{value!r}")
    return out


#: 列定义里的定点位数（`amount` / `resolution_amount` 是 (18,4)，`quantity` 是 (14,3)）。
#: ⚠️ **读出时按它 quantize**：SQLite 的 NUMERIC 亲和性会把 `"12000.0000"` 存成整数
#: `12000`（末尾零丢失），而 MySQL 的 `DECIMAL(18,4)` 会保留 —— 不抹平的话，同一条契约
#: 在两种方言下给出**不同的字面值**，而客户端拿到的是字符串，差异会一路带到界面。
_MONEY_SCALE = Decimal("0.0001")
_QTY_SCALE = Decimal("0.001")


def _dec_text(value: Any, *, scale: Decimal | None = None) -> str | None:
    """`Decimal` → 字符串（**不经 float**）。`None` 原样回 `None`（未知保持未知）。"""
    if value is None:
        return None
    out = value if isinstance(value, Decimal) else Decimal(str(value))
    if scale is not None:
        # 超出列精度时原样回，不抛（数据库本该先拦）
        with contextlib.suppress(InvalidOperation):
            out = out.quantize(scale)
    return format(out, "f")


def _row_to_charge(row: Any) -> dict[str, Any]:
    return {
        "charge_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "direction": str(row["direction"]),
        "charge_kind": str(row["charge_kind"]),
        "quantity": _dec_text(row["quantity"], scale=_QTY_SCALE),
        "unit": row["unit"],
        "amount": _dec_text(row["amount"], scale=_MONEY_SCALE),
        "currency": str(row["currency"]),
        "counterparty": row["counterparty"],
        "basis": str(row["basis"]),
        "status": str(row["status"]),
        "disputed_reason": row["disputed_reason"],
        "resolution_outcome": row["resolution_outcome"],
        "resolution_amount": _dec_text(row["resolution_amount"], scale=_MONEY_SCALE),
        "counts_in_total": (
            None if row["counts_in_total"] is None else bool(int(row["counts_in_total"]))
        ),
        "resolution_method": row["resolution_method"],
        "resolution_evidence_ref": row["resolution_evidence_ref"],
        "resolved_by": row["resolved_by"],
        "resolved_at": str(row["resolved_at"]) if row["resolved_at"] is not None else None,
        "revision": int(row["revision"]),
        "created_by": row["created_by"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def get_charge(session: Session, *, charge_id: int) -> dict[str, Any]:
    row = (
        session.execute(text(f"SELECT {_COLS} FROM ent_charge WHERE id = :cid"), {"cid": charge_id})
        .mappings()
        .first()
    )
    if row is None:
        raise ChargeNotFoundError(f"费用行 {charge_id} 不存在")
    return _row_to_charge(row)


def list_charges(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    """该委托的**全部**费用行（按 id 升序）。

    ⚠️ 空列表是正常答复（多数委托还没有任何费用行），**不是** 404 —— 与
    `GET /assignments/{id}/quantity-changes` 同一条判据：这里是**集合**。
    """
    rows = (
        session.execute(
            text(f"SELECT {_COLS} FROM ent_charge WHERE assignment_id = :aid ORDER BY id ASC"),
            {"aid": assignment_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_charge(r) for r in rows]


# ── 写入 ────────────────────────────────────────────────────────────────────


def record_charge(
    session: Session,
    *,
    assignment_id: int,
    direction: str,
    charge_kind: str,
    amount: Any,
    currency: str | None = None,
    basis: str,
    quantity: Any = None,
    unit: str | None = None,
    counterparty: str | None = None,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """登记一条费用行（状态 `draft`）。

    ⚠️ **不校验金额正负**：负数的语义（冲销？红字？）**没有裁定过**，
    本切片不自造约束 —— 留到 S7-3 的口径里定。这里只保证"是合法的有限 Decimal"。

    `quantity` 与 `unit` **必须成对**：只给数量不给单位，等于记了一个无法核对口径的数。
    """
    if direction not in ALL_DIRECTIONS:
        raise ChargeError(f"收付方向只能是 {ALL_DIRECTIONS} 之一，收到 {direction!r}")
    kind = str(charge_kind or "").strip()
    if not kind:
        raise ChargeError("费用类别必填")
    amt = _dec(amount, field="金额")
    cur = str(currency or DEFAULT_CURRENCY).strip().upper()
    if not cur:
        raise ChargeError("币种必填")
    bas = str(basis or "").strip()
    if not bas:
        raise ChargeError("计费依据必填（没有依据的费用行不可核对）")
    qty = _dec(quantity, field="数量", allow_none=True)
    unit_text = str(unit).strip() if isinstance(unit, str) and unit.strip() else None
    if (qty is None) != (unit_text is None):
        raise ChargeError("数量与单位必须成对给出（只给其一无法核对口径）")
    now = utcnow_naive()
    res = session.execute(
        text(
            "INSERT INTO ent_charge (assignment_id, direction, charge_kind, quantity, unit, amount,"
            " currency, counterparty, basis, status, revision, created_by, created_at, updated_at)"
            " VALUES (:aid, :dir, :kind, :qty, :unit, :amt, :cur, :cp, :basis, :st, 1, :by,"
            " :now, :now)"
        ),
        {
            "aid": assignment_id,
            "dir": direction,
            "kind": kind,
            "qty": str(qty) if qty is not None else None,
            "unit": unit_text,
            "amt": str(amt),
            "cur": cur,
            "cp": counterparty,
            "basis": bas,
            "st": STATUS_DRAFT,
            "by": actor_id,
            "now": now,
        },
    )
    charge_id = int(cast("CursorResult[Any]", res).lastrowid or 0)
    session.commit()
    return get_charge(session, charge_id=charge_id)


def _transition(
    session: Session,
    *,
    charge_id: int,
    expected_revision: int | None,
    from_status: str,
    to_status: str,
    sets: dict[str, Any],
    conflict_detail: str,
) -> dict[str, Any]:
    """状态迁移的**唯一**实现：条件更新 + `revision` 乐观锁。

    `expected_revision` 为 `None` 时只按状态条件更新（调用方明确表示"不参与版本竞争"，
    例如种子）。任何时候 `rowcount == 0` ⇒ 409 —— 那意味着"要么状态不对、要么版本过期"，
    两者都不该被静默吞掉。
    """
    params: dict[str, Any] = {
        "cid": charge_id,
        "to": to_status,
        "now": utcnow_naive(),
        "from_status": from_status,
    }
    # ⚠️ 等值而非 `IN :froms`：`text()` 里的 `IN :param` 需要
    # `bindparam(expanding=True)`，漏了会在 SQLite 上报 `near "?": syntax error`（实测）。
    where = "id = :cid AND status = :from_status"
    if expected_revision is not None:
        where += " AND revision = :rev"
        params["rev"] = int(expected_revision)
    assigns = ["status = :to", "revision = revision + 1", "updated_at = :now"]
    for key, value in sets.items():
        assigns.append(f"{key} = :{key}")
        params[key] = value
    res = session.execute(text(f"UPDATE ent_charge SET {', '.join(assigns)} WHERE {where}"), params)
    if int(cast("CursorResult[Any]", res).rowcount or 0) == 0:
        session.rollback()
        raise ChargeStateError(conflict_detail)
    session.commit()
    return get_charge(session, charge_id=charge_id)


def confirm_charge(
    session: Session, *, charge_id: int, expected_revision: int | None = None
) -> dict[str, Any]:
    """`draft → confirmed`：确认之后这条费用才进合计。"""
    return _transition(
        session,
        charge_id=charge_id,
        expected_revision=expected_revision,
        from_status=STATUS_DRAFT,
        to_status=STATUS_CONFIRMED,
        sets={},
        conflict_detail=(
            f"费用行 {charge_id} 不能确认：它不在 draft 状态，或 revision 已过期"
            "（并发下只有一个写者能成功）"
        ),
    )


def dispute_charge(
    session: Session,
    *,
    charge_id: int,
    reason: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """`confirmed → disputed`：**争议行不进合计**（合同 S4 段第 10 条）。

    只能对**已确认**的费用提争议：`draft` 本来就不计入，"对草稿提争议"没有业务含义
    （想撤回草稿是另一件事，本切片没有这条命令 —— 如实记在模块文档里）。
    """
    text_reason = str(reason or "").strip()
    if not text_reason:
        raise ChargeError("争议理由必填（只写「有争议」而不写理由，合计的差异无从复核）")
    return _transition(
        session,
        charge_id=charge_id,
        expected_revision=expected_revision,
        from_status=STATUS_CONFIRMED,
        to_status=STATUS_DISPUTED,
        sets={"disputed_reason": text_reason},
        conflict_detail=(
            f"费用行 {charge_id} 不能提争议：它不在 confirmed 状态，或 revision 已过期"
        ),
    )


def resolve_charge(
    session: Session,
    *,
    charge_id: int,
    outcome: str,
    method: str,
    counts_in_total: bool,
    final_amount: Any = None,
    evidence_ref: str | None = None,
    actor_id: int | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """`disputed → resolved|rejected`：**显式处置 ＋ 依据**（裁定 Q2=B）。

    ⛔ `counts_in_total` **必须由调用方显式给出**，不从 `outcome` 推导：
    认可可能是"全额计入"、也可能是"认可但本期不计入"，调减是"按新金额计入"，
    拒绝是"不计入" —— 把三者压成一条规则，正是裁定要消除的歧义。

    `counts_in_total=True` 时 `final_amount` **必填**（要计入就得有个数）；
    为 `False` 时允许留空（不进合计的金额写出来只会误导）。
    """
    if outcome not in ALL_OUTCOMES:
        raise ChargeError(f"处置结果只能是 {ALL_OUTCOMES} 之一，收到 {outcome!r}")
    method_text = str(method or "").strip()
    if not method_text:
        raise ChargeError("处置依据必填（无依据的处置等于没处置）")
    final = _dec(final_amount, field="最终金额", allow_none=True)
    if counts_in_total and final is None:
        raise ChargeError("计入合计时必须给出最终金额")
    ref = (
        str(evidence_ref).strip()
        if isinstance(evidence_ref, str) and evidence_ref.strip()
        else None
    )
    to_status = STATUS_REJECTED if outcome == OUTCOME_REJECTED else STATUS_RESOLVED
    return _transition(
        session,
        charge_id=charge_id,
        expected_revision=expected_revision,
        from_status=STATUS_DISPUTED,
        to_status=to_status,
        sets={
            "resolution_outcome": outcome,
            "resolution_amount": str(final) if final is not None else None,
            "counts_in_total": 1 if counts_in_total else 0,
            "resolution_method": method_text,
            "resolution_evidence_ref": ref,
            "resolved_by": actor_id,
            "resolved_at": utcnow_naive(),
        },
        conflict_detail=(f"费用行 {charge_id} 不能处置：它不在 disputed 状态，或 revision 已过期"),
    )


# ── 合计（按币种 × 方向分开）────────────────────────────────────────────────


def _counts(row: dict[str, Any]) -> tuple[bool, Decimal | None]:
    """这条费用行"算不算进合计、按哪个金额算" —— 全模块**唯一**一处判据。"""
    status = row["status"]
    if status == STATUS_CONFIRMED:
        return True, Decimal(str(row["amount"]))
    if status == STATUS_RESOLVED:
        if row["counts_in_total"] is True:
            amount = row["resolution_amount"]
            return True, (Decimal(str(amount)) if amount is not None else None)
        return False, None
    # draft / disputed / rejected ⇒ 不计入（disputed 由合同 S4 段第 10 条点名）
    return False, None


def money_text(value: Decimal | None) -> str | None:
    """金额 → 字符串（按列定点位数 `quantize`，**不经 float**；`None` 原样回）。"""
    return _dec_text(value, scale=_MONEY_SCALE)


def counted_charges(
    session: Session, *, assignment_id: int
) -> list[tuple[dict[str, Any], Decimal]]:
    """**计入合计**的费用行及其**计入金额**（`resolved` 行取处置后的最终金额）。

    给结算快照用。判据复用本模块唯一那一处 `_counts` —— ⛔ 结算绝不能自带一套
    "哪些行算数"：费用页说 100、结算页说 80，两边都"对"，加起来对不上却没人能说清。
    """
    out: list[tuple[dict[str, Any], Decimal]] = []
    for row in list_charges(session, assignment_id=assignment_id):
        include, amount = _counts(row)
        if include and amount is not None:
            out.append((row, amount))
    return out


def summarize_charges(session: Session, *, assignment_id: int) -> dict[str, Any]:
    """按 `(币种, 收付方向)` 分组合计 —— ⛔ **不跨币种相加**（裁定 Q1=C）。

    返回 `{"groups": [...], "counted_lines": n, "excluded_lines": n}`；
    每组给 `total`（字符串，Decimal）、`counted_lines` 与 `excluded_lines`
    —— "计入了几条 / 排除了几条"必须能被看出来，否则一个合计值没法被复核。
    """
    rows = list_charges(session, assignment_id=assignment_id)
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    counted = 0
    excluded = 0
    for row in rows:
        include, amount = _counts(row)
        key = (str(row["currency"]), str(row["direction"]))
        bucket = buckets.setdefault(
            key,
            {
                "currency": key[0],
                "direction": key[1],
                "total": Decimal(0),
                "counted_lines": 0,
                "excluded_lines": 0,
            },
        )
        if include and amount is not None:
            bucket["total"] = bucket["total"] + amount
            bucket["counted_lines"] += 1
            counted += 1
        else:
            bucket["excluded_lines"] += 1
            excluded += 1
    groups = [
        {
            "currency": b["currency"],
            "direction": b["direction"],
            "total": _dec_text(b["total"], scale=_MONEY_SCALE),
            "counted_lines": b["counted_lines"],
            "excluded_lines": b["excluded_lines"],
        }
        for b in sorted(buckets.values(), key=lambda x: (x["currency"], x["direction"]))
    ]
    return {"groups": groups, "counted_lines": counted, "excluded_lines": excluded}


__all__ = [
    "ALL_DIRECTIONS",
    "ALL_OUTCOMES",
    "ALL_STATUSES",
    "CHARGE_KIND_WAITING_TIME",
    "ChargeError",
    "ChargeNotFoundError",
    "ChargeStateError",
    "DEFAULT_CURRENCY",
    "DIRECTION_PAYABLE",
    "DIRECTION_RECEIVABLE",
    "OUTCOME_ACCEPTED",
    "OUTCOME_ADJUSTED",
    "OUTCOME_REJECTED",
    "STATUS_CONFIRMED",
    "STATUS_DISPUTED",
    "STATUS_DRAFT",
    "STATUS_REJECTED",
    "STATUS_RESOLVED",
    "confirm_charge",
    "counted_charges",
    "dispute_charge",
    "get_charge",
    "list_charges",
    "money_text",
    "record_charge",
    "resolve_charge",
    "summarize_charges",
    "utcnow_naive",
]
