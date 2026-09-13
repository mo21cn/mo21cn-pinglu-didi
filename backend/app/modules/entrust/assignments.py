"""委托受理链路 —— 委托单（Assignment）的服务层（ENT-005）。

对应计划 §5.2 S1 第 2～3 条与 12 步闭环的第 1～2 步：

1. **客户创建/编辑/提交委托**（草稿可以不完整；提交时必须选定服务经营主体，
   且货主对该主体存在**生效**的委托授权 —— 授权是提交的门槛，不是 UI 选项）；
2. **经理认领（原子操作，AC-03）**：两个经理同时认领，只有一个成功，
   后到者得到冲突 —— 不静默覆盖、不产生双负责人。

状态机::

    draft ──submit──> submitted ──claim──> claimed
      │                   │
      └──────cancel───────┴──────────────> cancelled

与其他公共机制的关系：
* **乐观锁**：可变聚合带 `revision`（计划 §1.1 硬约束 5）；编辑/提交等命令必须带
  `expected_revision`，不匹配抛 `RevisionConflictError`（HTTP 409）；
* **未知就是未知**：数量与单位未知保持 NULL，本模块不做任何静默补 0 / 补 true；
* **权限**：货主侧动作只校验"操作者就是创建人"；经理侧动作（认领）由叠加层
  `resolve_context()` 校验组织成员身份与 `PERM_ASSIGN_CLAIM` 权限。本模块不碰
  `User.current_role`。

与 artifacts.py 的边界：委托单是**受理**对象；成果（报价/方案/合同）挂在
委托授权（ent_entrustment）下，由 artifacts.py 负责，两者不在一张表里混写。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust.access import (
    PERM_ASSIGN_CLAIM,
    AccessContext,
    AccessDeniedError,
    resolve_context,
)

STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_CLAIMED = "claimed"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (STATUS_DRAFT, STATUS_SUBMITTED, STATUS_CLAIMED)

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

_CANCELLABLE = (STATUS_DRAFT, STATUS_SUBMITTED)


class AssignmentError(RuntimeError):
    """委托单服务的基础异常。HTTP 层按语义转 4xx。"""


class AssignmentNotFoundError(AssignmentError):
    """委托单不存在（或调用方无权知晓其存在）。HTTP 层应转 404。"""


class AssignmentStateError(AssignmentError):
    """状态不允许该操作（如认领非 submitted 的委托）。HTTP 层应转 409。"""


class RevisionConflictError(AssignmentError):
    """`expected_revision` 与当前版本不一致 —— 数据已被他人修改。HTTP 层应转 409。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 ent_ 表时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FORMAT)


def _text_ts(raw: Any) -> str | None:
    """时间列 → 统一文本表示（响应模型声明 `str | None` 的方言归一）。

    SQLite 的 DATETIME 以 TEXT 存放，取出来就是 `str`；MySQL 的 DATETIME 由
    驱动转换成 `datetime`。若不归一，同一个响应模型在 SQLite 上通过、在 MySQL
    上因类型校验失败而降级成 500/422 —— 这类"只在生产库暴露"的缺陷必须由
    本层统一吸收（与 `tasks._text_ts` 同一口径）。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _row_to_assignment(row: Any) -> dict[str, Any]:
    quantity = row["quantity"]
    return {
        "assignment_id": int(row["id"]),
        "owner_user_id": int(row["owner_user_id"]),
        "org_id": int(row["org_id"]) if row["org_id"] is not None else None,
        "title": str(row["title"]),
        "cargo_summary": row["cargo_summary"],
        # 数量保持原样返回（str/None）：Decimal 精度不在这里做二次加工
        "quantity": None if quantity is None else str(quantity),
        "quantity_unit": row["quantity_unit"],
        "status": str(row["status"]),
        "revision": int(row["revision"]),
        "claimed_by": int(row["claimed_by"]) if row["claimed_by"] is not None else None,
        "claimed_at": _text_ts(row["claimed_at"]),
        "submitted_at": _text_ts(row["submitted_at"]),
        "cancelled_at": _text_ts(row["cancelled_at"]),
        "created_at": _text_ts(row["created_at"]),
        "updated_at": _text_ts(row["updated_at"]),
    }


_ASSIGNMENT_COLS = (
    "id, owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, "
    "status, revision, claimed_by, claimed_at, submitted_at, cancelled_at, "
    "created_at, updated_at"
)


def get_assignment(session: Session, assignment_id: int) -> dict[str, Any] | None:
    """按 ID 读取委托单；不存在返回 None。"""
    row = (
        session.execute(
            text(f"SELECT {_ASSIGNMENT_COLS} FROM ent_assignment WHERE id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .first()
    )
    return _row_to_assignment(row) if row is not None else None


def _get_or_404(session: Session, assignment_id: int) -> dict[str, Any]:
    assignment = get_assignment(session, assignment_id)
    if assignment is None:
        raise AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
    return assignment


def _check_expected_revision(assignment: dict[str, Any], expected_revision: int) -> None:
    if expected_revision != assignment["revision"]:
        raise RevisionConflictError(
            f"版本冲突：当前 revision={assignment['revision']}，"
            f"请求基于 revision={expected_revision}（数据可能已被他人修改）"
        )


def create_assignment(
    session: Session,
    *,
    owner_user_id: int,
    title: str,
    cargo_summary: str | None = None,
    quantity: Decimal | str | None = None,
    quantity_unit: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """创建委托草稿。

    草稿允许不完整：货物概述、数量、单位、服务主体都可以缺省（计划 §2.1 第 1 步
    "草稿可先不完整"）。数量未知就是 NULL，**绝不静默补 0**（PRD 5.1）。
    """
    if not title or not title.strip():
        raise AssignmentError("委托标题不能为空")
    current = now or utcnow_naive()
    ts = _fmt(current)
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_assignment "
                "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, "
                " status, revision, created_at, updated_at) "
                "VALUES (:owner, NULL, :title, :cargo, :qty, :unit, :status, 1, :ts, :ts)"
            ),
            {
                "owner": owner_user_id,
                "title": title.strip(),
                "cargo": cargo_summary,
                "qty": str(quantity) if quantity is not None else None,
                "unit": quantity_unit,
                "status": STATUS_DRAFT,
                "ts": ts,
            },
        ),
    )
    session.commit()
    assignment_id = int(result.lastrowid or 0)
    created = _get_or_404(session, assignment_id)
    assert created is not None
    return created


def update_draft(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    expected_revision: int,
    title: str | None = None,
    cargo_summary: str | None = None,
    quantity: Decimal | str | None = None,
    quantity_unit: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """编辑草稿（货主本人）。仅 `draft` 状态可编辑；成功则 revision + 1。

    字段语义：显式给出（含 None）即覆盖，未提供的键保持原值 —— 由 router 层用
    `model_fields_set` 区分"未提供"与"显式置空"。
    """
    assignment = _get_or_404(session, assignment_id)
    if assignment["owner_user_id"] != actor_id:
        raise AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
    if assignment["status"] != STATUS_DRAFT:
        raise AssignmentStateError(
            f"委托单 {assignment_id} 状态为 {assignment['status']}，只有草稿可编辑"
        )
    _check_expected_revision(assignment, expected_revision)

    current = now or utcnow_naive()
    sets = ["revision = revision + 1", "updated_at = :ts"]
    params: dict[str, Any] = {"aid": assignment_id, "ts": _fmt(current)}
    if title is not None:
        if not title.strip():
            raise AssignmentError("委托标题不能为空")
        sets.append("title = :title")
        params["title"] = title.strip()
    if cargo_summary is not None:
        sets.append("cargo_summary = :cargo")
        params["cargo"] = cargo_summary
    if quantity is not None:
        sets.append("quantity = :qty")
        params["qty"] = str(quantity)
    if quantity_unit is not None:
        sets.append("quantity_unit = :unit")
        params["unit"] = quantity_unit

    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                f"UPDATE ent_assignment SET {', '.join(sets)} "
                "WHERE id = :aid AND revision = :expected_rev"
            ),
            {**params, "expected_rev": expected_revision},
        ),
    )
    if int(result.rowcount or 0) == 0:
        # 条件更新没打中：读取间隙里 revision 被并发修改（乐观锁真正的裁决点）。
        # 检查阶段只是快速失败；这里才是保证"过期命令必然 409"的地方。
        fresh = get_assignment(session, assignment_id)
        if fresh is None:
            raise AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
        raise RevisionConflictError(
            f"版本冲突：当前 revision={fresh['revision']}，"
            f"请求基于 revision={expected_revision}（数据可能已被他人修改）"
        )
    session.commit()
    updated = _get_or_404(session, assignment_id)
    assert updated is not None
    return updated


def owner_has_active_entrustment(
    session: Session,
    *,
    owner_user_id: int,
    org_id: int,
    now: datetime | None = None,
) -> bool:
    """货主对某组织是否存在**生效**的委托授权。

    生效 = 组织 active + 授权 status=active + 当前时间在窗口内。
    提交委托时必须通过这道检查 —— 授权是提交的门槛（服务端校验，不是 UI 选项）。
    """
    current = now or utcnow_naive()
    row = (
        session.execute(
            text(
                "SELECT e.valid_from AS valid_from, e.valid_until AS valid_until "
                "FROM ent_entrustment e "
                "JOIN ent_organization o ON o.id = e.org_id "
                "WHERE e.org_id = :org AND e.entrust_user_id = :owner "
                "  AND e.status = 'active' AND o.status = 'active'"
            ),
            {"org": org_id, "owner": owner_user_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return False
    valid_from = _parse_ts(row["valid_from"])
    valid_until = _parse_ts(row["valid_until"])
    if valid_from is not None and current < valid_from:
        return False
    return not (valid_until is not None and current > valid_until)


def _parse_ts(raw: Any) -> datetime | None:
    """解析窗口边界；空值或不可解析返回 None（与 access._parse 同规则）。"""
    if raw is None:
        return None
    try:
        return datetime.strptime(str(raw).replace("T", " ").rstrip("Z")[:19], _TS_FORMAT)
    except ValueError:
        return None


def submit_assignment(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    org_id: int,
    expected_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """提交委托到服务经营主体（货主本人）：draft → submitted。

    前置条件（全部服务端校验）：
    1. 操作者是创建人；
    2. 状态为 draft；
    3. `expected_revision` 匹配；
    4. 货主对该组织存在生效委托授权（无授权 → AccessDeniedError，403）。
    """
    assignment = _get_or_404(session, assignment_id)
    if assignment["owner_user_id"] != actor_id:
        raise AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
    if assignment["status"] != STATUS_DRAFT:
        raise AssignmentStateError(
            f"委托单 {assignment_id} 状态为 {assignment['status']}，只有草稿可提交"
        )
    _check_expected_revision(assignment, expected_revision)
    if not owner_has_active_entrustment(session, owner_user_id=actor_id, org_id=org_id, now=now):
        raise AccessDeniedError(f"货主 {actor_id} 对组织 {org_id} 没有生效的委托授权，不能提交")

    current = now or utcnow_naive()
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "UPDATE ent_assignment SET org_id = :org, status = :status, "
                "submitted_at = :ts, revision = revision + 1, updated_at = :ts "
                "WHERE id = :aid AND revision = :expected_rev AND status = :from_status"
            ),
            {
                "org": org_id,
                "status": STATUS_SUBMITTED,
                "ts": _fmt(current),
                "aid": assignment_id,
                "expected_rev": expected_revision,
                "from_status": STATUS_DRAFT,
            },
        ),
    )
    if int(result.rowcount or 0) == 0:
        # 提交也是条件更新：状态被并发改变或 revision 过期都在这里统一裁决
        fresh = get_assignment(session, assignment_id)
        if fresh is None:
            raise AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
        if fresh["revision"] != expected_revision:
            raise RevisionConflictError(
                f"版本冲突：当前 revision={fresh['revision']}，"
                f"请求基于 revision={expected_revision}（数据可能已被他人修改）"
            )
        raise AssignmentStateError(
            f"委托单 {assignment_id} 状态为 {fresh['status']}，只有草稿可提交"
        )
    session.commit()
    submitted = _get_or_404(session, assignment_id)
    assert submitted is not None
    return submitted


def claim_assignment(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """经理认领委托：submitted → claimed（**原子操作，AC-03**）。

    权限：操作者必须是该委托所属组织的 active 成员，且具备 `entrust:assignment:claim`
    （叠加层 `resolve_context`，不改 `current_role`）。

    并发：认领是**单条 UPDATE ... WHERE status='submitted'**，以受影响行数判定成败。
    两个经理同时认领，数据库层保证只有一个成功，后到者收到状态冲突 ——
    不读-判-写、不静默覆盖（双认领不覆盖是 S4 并发回归与 MySQL 集成测试的锚点）。
    """
    assignment = _get_or_404(session, assignment_id)
    if assignment["status"] != STATUS_SUBMITTED:
        raise AssignmentStateError(
            f"委托单 {assignment_id} 状态为 {assignment['status']}，只有待受理可认领"
        )
    org_id = assignment["org_id"]
    if org_id is None:
        raise AssignmentStateError(f"委托单 {assignment_id} 未选定服务主体，不能认领")

    ctx: AccessContext = resolve_context(session, user_id=actor_id, now=now)
    # 权限必须按**该委托所属组织**判定。曾用 ctx.can(PERM_ASSIGN_CLAIM)（跨组织并集），
    # 导致"在 A 组织是经理、在 B 组织只是普通成员"的用户能认领 B 组织的委托单 —— 已复现并修复。
    if org_id not in ctx.org_ids or not ctx.can(PERM_ASSIGN_CLAIM, org_id=org_id):
        raise AccessDeniedError(f"用户 {actor_id} 不是组织 {org_id} 的成员或缺少认领权限")

    current = now or utcnow_naive()
    ts = _fmt(current)
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "UPDATE ent_assignment SET status = :status, claimed_by = :uid, "
                "claimed_at = :ts, revision = revision + 1, updated_at = :ts "
                "WHERE id = :aid AND status = :from_status"
            ),
            {
                "status": STATUS_CLAIMED,
                "uid": actor_id,
                "ts": ts,
                "aid": assignment_id,
                "from_status": STATUS_SUBMITTED,
            },
        ),
    )
    if int(result.rowcount or 0) == 0:
        # 条件更新没打中：期间状态已被别人改走（典型 = 双认领竞争）
        raise AssignmentStateError(f"委托单 {assignment_id} 已被他人认领或状态已变化，认领失败")
    session.commit()
    claimed = _get_or_404(session, assignment_id)
    assert claimed is not None
    return claimed


def cancel_assignment(
    session: Session,
    *,
    assignment_id: int,
    actor_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """货主撤回委托：draft/submitted → cancelled。

    已认领（claimed）的委托不能由货主单方撤回 —— 需要走异常/变更流程（后续增量）。
    """
    assignment = _get_or_404(session, assignment_id)
    if assignment["owner_user_id"] != actor_id:
        raise AssignmentNotFoundError(f"委托单 {assignment_id} 不存在")
    if assignment["status"] not in _CANCELLABLE:
        raise AssignmentStateError(
            f"委托单 {assignment_id} 状态为 {assignment['status']}，不能撤回"
        )
    current = now or utcnow_naive()
    session.execute(
        text(
            "UPDATE ent_assignment SET status = :status, cancelled_at = :ts, "
            "revision = revision + 1, updated_at = :ts WHERE id = :aid"
        ),
        {"status": STATUS_CANCELLED, "ts": _fmt(current), "aid": assignment_id},
    )
    session.commit()
    cancelled = _get_or_404(session, assignment_id)
    assert cancelled is not None
    return cancelled


#: LIKE 转义字符。用户输入里的 `%` / `_` / `!` 必须当成**字面字符**：
#: 不转义的话，搜一个 `%` 会匹配到全部委托 —— 那看起来像"搜到了很多"，
#: 实际是过滤条件被静默丢掉了，任何"有结果就算通过"的断言都发现不了。
#:
#: **刻意不用反斜杠**：MySQL 在字符串字面量里把 `\` 当 C 风格转义符，于是
#: `ESCAPE '\'` 的结束引号会被吃掉，服务端报语法错误（SQLite 却完全正常）。
#: 开发/测试库是 SQLite、生产是 MySQL，而 CI 的 MySQL job 不跑这条查询路径，
#: 用反斜杠会让这个问题一路绿灯到生产。`!` 在所有目标方言里都是普通字符，
#: 也不受 MySQL `NO_BACKSLASH_ESCAPES` 影响。
_LIKE_ESCAPE = "!"


def _like_pattern(keyword: str | None) -> str | None:
    """搜索词 → LIKE 模式；空白词返回 `None`（不加过滤，而不是"匹配空串"）。"""
    if keyword is None:
        return None
    word = keyword.strip()
    if not word:
        return None
    escaped = (
        word.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def list_assignments(
    session: Session,
    *,
    owner_user_id: int | None = None,
    org_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[int, list[dict[str, Any]]]:
    """按视角列出委托单。

    货主视角传 `owner_user_id`，组织视角传 `org_id`（经理工作台队列）。
    两者都给则取交集（用于"该组织中我作为货主的委托"这类查询）；
    都不给返回空集 —— 列表必须有明确的可见性边界，不做全表浏览。

    `keyword` 是**在已限定可见范围之内**再做一次文本过滤（UI-02 的委托搜索）。
    它不参与可见性判定：可见范围仍然只由 `owner_user_id` / `org_id` 决定，
    所以搜索不可能被用来"扫"到范围外的委托。
    """
    if owner_user_id is None and org_id is None:
        return 0, []

    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if owner_user_id is not None:
        where.append("owner_user_id = :owner")
        params["owner"] = owner_user_id
    if org_id is not None:
        where.append("org_id = :org")
        params["org"] = org_id
    if status is not None:
        where.append("status = :status")
        params["status"] = status
    pattern = _like_pattern(keyword)
    if pattern is not None:
        # 标题或货物概述命中即算（经理找人时通常记得其中一个，记不全两个）。
        where.append("(title LIKE :kw ESCAPE '!' OR cargo_summary LIKE :kw ESCAPE '!')")
        params["kw"] = pattern
    clause = " AND ".join(where)

    total_row = (
        session.execute(text(f"SELECT COUNT(*) AS c FROM ent_assignment WHERE {clause}"), params)
        .mappings()
        .first()
    )
    total = int(total_row["c"]) if total_row is not None else 0

    rows = session.execute(
        text(
            f"SELECT {_ASSIGNMENT_COLS} FROM ent_assignment WHERE {clause} "
            "ORDER BY updated_at DESC, id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": size, "offset": (page - 1) * size},
    ).mappings()
    return total, [_row_to_assignment(r) for r in rows]
