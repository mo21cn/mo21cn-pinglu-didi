"""智慧关务（关域）D1 服务层 —— 人工登记 + 状态时间线（无外部数据源）。

## 这一层刻意做小的三件事

1. **只读派生状态**：`cus_declaration.status` 永远由事件派生（`derive_status`），
   不允许直接写。写状态＝绕过留痕，PRD §4.4「状态只能由带来源的事件推进」就废了；
2. **事件只追加**：本模块没有 update/delete 事件的代码路径。更正＝新增一条
   `corrects_event_id` 指向原事件（AC-06）；
3. **来源不可空**：`source` / `source_kind` / `fetched_at` 三件套缺一不可（AC-09）。

## 可见性

* 详情：登记人本人，或**记录所属组织**的成员；不可见一律 404
  （「不泄漏存在性」，与委托支线同一口径；AC-07 要的是"不能 200"，不是"必须 403"）；
* 登记时指定 `org_id`：必须是操作者**所在**的组织，否则 403（这是越权写，不是"看不到"）。

## 为什么 D1 没有官方数据源

Q-02（数据源授权）未定 ⇒ 拿不到单一窗口/舱单的正式回传。此时唯一合法数据来源是人工
登记。`freshness()` 会把这件事**明写在响应里**：`official_state=unavailable` +
"以上状态全部来自人工登记"。演示时这就是 AC-02 的证据，而不是需要藏起来的缺陷。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: 只追加事件表里允许出现的状态（与 schemas.DeclarationStatus 对齐）
STATUSES = (
    "unknown",
    "manifest_filed",
    "declared",
    "inspection",
    "released",
    "held",
    "rejected",
)

#: 终态：一旦出现，乱序到达的旧事件不得把它顶回去（PRD §4.4 规则 2）
TERMINAL_STATUSES = ("released",)

TS_FMT = "%Y-%m-%d %H:%M:%S"
OBJECT_TYPE = "declaration"


class DeclarationError(Exception):
    """关务领域异常基类（router 负责翻成 HTTP，不在这里吞掉）。"""


class DeclarationNotFoundError(DeclarationError):
    """记录不存在，**或**对当前用户不可见（两者不区分）。"""


class DeclarationConflictError(DeclarationError):
    """同一声明号在该归属下已存在。"""


class DeclarationForbiddenError(DeclarationError):
    """越权写：例如把自己登记到不属于自己的组织。"""


def utcnow_naive() -> datetime:
    """UTC 朴素时间（与 ent_ 表同口径：库里不存时区，展示层负责转本地）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _ts(value: datetime | None = None) -> str:
    return (value or utcnow_naive()).strftime(TS_FMT)


def _norm_time(value: Any) -> str:
    """库里的时间列统一成 `YYYY-MM-DD HH:MM:SS` 字符串。

    SQLite 返回 TEXT、MySQL 返回 datetime —— 两种都要能比较与序列化，且**字符串可
    直接按字典序比大小**（这正是 `derive_status` 排序所依赖的，格式必须定长）。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime(TS_FMT)
    return str(value)


def _event_out(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "status": str(row["status"]),
        "occurred_at": _norm_time(row["occurred_at"]),
        "fetched_at": _norm_time(row["fetched_at"]),
        "source": str(row["source"]),
        "source_kind": str(row["source_kind"]),
        "actor_user_id": row["actor_user_id"],
        "corrects_event_id": row["corrects_event_id"],
        "note": row["note"],
    }


def _declaration_out(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "declaration_no": str(row["declaration_no"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "owner_user_id": int(row["owner_user_id"]),
        "org_id": row["org_id"],
        "assignment_id": row["assignment_id"],
        "note": row["note"],
        "revision": int(row["revision"]),
        "created_at": _norm_time(row["created_at"]),
        "updated_at": _norm_time(row["updated_at"]),
    }


def derive_status(events: list[dict[str, Any]]) -> str:
    """由事件派生当前状态。

    规则（PRD §4.4）：

    * 没有事件 ⇒ `unknown`（未知就是未知，不猜）；
    * 出现 `released` ⇒ 固定为 `released`：放行是**官方终态**，晚到的乱序旧事件
      （例如一条补录的 `inspection`）不得把它顶回去；
    * 其余情况取**发生时间最晚**的事件；同一时间取 id 较大者（后登记的为准）。
    """
    if not events:
        return "unknown"
    if any(e["status"] in TERMINAL_STATUSES for e in events):
        return TERMINAL_STATUSES[0] if len(TERMINAL_STATUSES) == 1 else "released"
    latest = max(events, key=lambda e: (e["occurred_at"], e["id"]))
    return str(latest["status"])


def freshness(events: list[dict[str, Any]]) -> dict[str, Any]:
    """数据源新鲜度 —— 演示里"数据源不可用"那块牌子的数据源（AC-02 / AC-09）。

    返回的 `message` 是给页面**直接展示**的：宁可写"官方源未授权、以下为人工登记"，
    也不能让一条人工录入的状态看起来像官方最新回执。
    """
    if not events:
        return {
            "official_state": "unavailable",
            "official_state_label": "暂不可用",
            "last_fetched_at": None,
            "last_source": None,
            "source_kind": None,
            "message": "尚无任何状态事件：先登记一条，时间线才会出现",
        }

    latest = max(events, key=lambda e: (e["occurred_at"], e["id"]))
    has_official = any(e["source_kind"] == "official" for e in events)
    return {
        "official_state": "available" if has_official else "unavailable",
        "official_state_label": "已接入" if has_official else "暂不可用",
        "last_fetched_at": latest["fetched_at"],
        "last_source": latest["source"],
        "source_kind": latest["source_kind"],
        "message": (
            "含官方回执事件"
            if has_official
            else "官方数据源未授权（Q-02 未决），以上状态全部来自人工登记，以官方结果为准"
        ),
    }


def _events_of(session: Session, declaration_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT id, status, occurred_at, fetched_at, source, source_kind, "
            "actor_user_id, corrects_event_id, note "
            "FROM cus_status_event "
            "WHERE object_type = :t AND object_id = :oid "
            "ORDER BY occurred_at ASC, id ASC"
        ),
        {"t": OBJECT_TYPE, "oid": declaration_id},
    ).mappings()
    return [_event_out(r) for r in rows]


def _refresh_status(session: Session, declaration_id: int) -> str:
    """把派生状态写回 `cus_declaration.status`（唯一允许写 status 的地方）。"""
    status = derive_status(_events_of(session, declaration_id))
    session.execute(
        text("UPDATE cus_declaration SET status = :s, updated_at = :u WHERE id = :id"),
        {"s": status, "u": _ts(), "id": declaration_id},
    )
    return status


def _visible_clause(org_ids: frozenset[int] | set[int]) -> tuple[str, dict[str, Any]]:
    """可见性谓词：本人 OR 所属组织成员。组织集合为空时就只剩"本人"。"""
    ids = sorted(int(x) for x in org_ids)
    if not ids:
        return "d.owner_user_id = :uid", {}
    placeholders = ", ".join(f":org{i}" for i in range(len(ids)))
    params = {f"org{i}": oid for i, oid in enumerate(ids)}
    return (
        f"(d.owner_user_id = :uid OR (d.org_id IS NOT NULL AND d.org_id IN ({placeholders})))",
        params,
    )


def create_declaration(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    declaration_no: str,
    status: str,
    source: str,
    kind: str = "export",
    occurred_at: datetime | None = None,
    note: str | None = None,
    org_id: int | None = None,
    assignment_id: int | None = None,
) -> dict[str, Any]:
    """人工登记一条报关记录，并写入它的第一条状态事件。"""
    if status not in STATUSES:
        raise DeclarationError(f"未知状态 {status!r}")
    if org_id is not None and int(org_id) not in {int(x) for x in org_ids}:
        raise DeclarationForbiddenError("只能登记到自己所属的组织")

    no = declaration_no.strip()
    if not no:
        raise DeclarationError("单号不能为空")

    dup = session.execute(
        text(
            "SELECT id FROM cus_declaration "
            "WHERE declaration_no = :no AND owner_user_id = :uid "
            "AND COALESCE(org_id, 0) = COALESCE(:org, 0)"
        ),
        {"no": no, "uid": user_id, "org": org_id},
    ).first()
    if dup is not None:
        raise DeclarationConflictError(f"单号 {no} 已登记过（id={int(dup[0])}）")

    now = _ts()
    occurred = _ts(occurred_at)
    ins = session.execute(
        text(
            "INSERT INTO cus_declaration "
            "(declaration_no, kind, status, owner_user_id, org_id, assignment_id, note, "
            " revision, created_at, updated_at) "
            "VALUES (:no, :kind, :status, :uid, :org, :aid, :note, 1, :c, :u)"
        ),
        {
            "no": no,
            "kind": kind,
            "status": status,
            "uid": user_id,
            "org": org_id,
            "aid": assignment_id,
            "note": note,
            "c": now,
            "u": now,
        },
    )
    declaration_id = int(ins.lastrowid or 0)
    if not declaration_id:
        raise DeclarationError("登记失败：未能取得记录 id")

    session.execute(
        text(
            "INSERT INTO cus_status_event "
            "(object_type, object_id, status, occurred_at, fetched_at, source, "
            " source_kind, actor_user_id, corrects_event_id, note, created_at) "
            "VALUES (:t, :oid, :status, :occ, :fetch, :src, 'manual', :uid, NULL, :note, :c)"
        ),
        {
            "t": OBJECT_TYPE,
            "oid": declaration_id,
            "status": status,
            "occ": occurred,
            "fetch": now,
            "src": source.strip(),
            "uid": user_id,
            "note": note,
            "c": now,
        },
    )
    _refresh_status(session, declaration_id)
    session.commit()
    return get_detail(session, user_id=user_id, org_ids=org_ids, declaration_id=declaration_id)


def append_event(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    declaration_id: int,
    status: str,
    source: str,
    occurred_at: datetime | None = None,
    note: str | None = None,
    corrects_event_id: int | None = None,
) -> dict[str, Any]:
    """给已有记录追加一条状态事件（只追加；更正填 `corrects_event_id`）。"""
    if status not in STATUSES:
        raise DeclarationError(f"未知状态 {status!r}")
    # 先过可见性：看不见的记录连"能不能追加"都不告诉对方
    get_detail(session, user_id=user_id, org_ids=org_ids, declaration_id=declaration_id)

    now = _ts()
    session.execute(
        text(
            "INSERT INTO cus_status_event "
            "(object_type, object_id, status, occurred_at, fetched_at, source, "
            " source_kind, actor_user_id, corrects_event_id, note, created_at) "
            "VALUES (:t, :oid, :status, :occ, :fetch, :src, 'manual', :uid, :fix, :note, :c)"
        ),
        {
            "t": OBJECT_TYPE,
            "oid": declaration_id,
            "status": status,
            "occ": _ts(occurred_at),
            "fetch": now,
            "src": source.strip(),
            "uid": user_id,
            "fix": corrects_event_id,
            "note": note,
            "c": now,
        },
    )
    _refresh_status(session, declaration_id)
    session.commit()
    return get_detail(session, user_id=user_id, org_ids=org_ids, declaration_id=declaration_id)


def list_declarations(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    no: str | None = None,
) -> list[dict[str, Any]]:
    """按可见性列出记录；给了 `no` 就按单号精确查（查不到＝空表，不是伪造一条空记录）。"""
    clause, extra = _visible_clause(org_ids)
    params: dict[str, Any] = {"uid": user_id, **extra}
    sql = (
        "SELECT d.id, d.declaration_no, d.kind, d.status, d.owner_user_id, d.org_id, "
        "d.assignment_id, d.note, d.revision, d.created_at, d.updated_at "
        "FROM cus_declaration d WHERE "
    )
    if no:
        sql += "d.declaration_no = :no AND "
        params["no"] = no.strip()
    sql += clause + " ORDER BY d.created_at DESC, d.id DESC"
    rows = session.execute(text(sql), params).mappings()
    return [_declaration_out(r) for r in rows]


def get_detail(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    declaration_id: int,
) -> dict[str, Any]:
    """详情 + 时间线 + 新鲜度。不可见与不存在都返回同一种错误。"""
    clause, extra = _visible_clause(org_ids)
    row = (
        session.execute(
            text(
                "SELECT d.id, d.declaration_no, d.kind, d.status, d.owner_user_id, d.org_id, "
                "d.assignment_id, d.note, d.revision, d.created_at, d.updated_at "
                f"FROM cus_declaration d WHERE d.id = :id AND {clause}"
            ),
            {"id": declaration_id, "uid": user_id, **extra},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise DeclarationNotFoundError("记录不存在或无权限查看")

    events = _events_of(session, declaration_id)
    return {
        "declaration": _declaration_out(row),
        "timeline": events,
        "freshness": freshness(events),
        # 派生值也返回一份：页面不必自己再算一遍（算错就会显示成旧值，AC-02 的坑）
        "derived_status": derive_status(events),
    }
