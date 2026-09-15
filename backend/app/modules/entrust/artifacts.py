"""成果版本机制 —— 公共能力「成果有版本」的服务层（ENT-004）。

规范 3.4 第一条的完整语义：

1. **编辑产生新 revision**：任何修改都追加 `ent_artifact_revision` 新行；
   历史 revision 是 append-only 的，永不改写、永不删除 —— 客户确认过的东西
   必须能原样回放；
2. **确认绑定精确版本**：`current_revision_id` 只能由显式的确认动作写入，
   指向一个具体 revision，而不是"最新"这种会漂移的指针；
3. **人工接管优先**：当前生效版本是人工产出（`manual`）时，Agent 侧不得把
   自己的产出确认成生效版本 —— 旧结果（模型产出）不得覆盖接管后的人工修改，
   必须由人工再次确认才可替换；
4. **失效成果不可确认**：`void` 状态的成果，服务端拒绝任何确认动作
   （这是"服务端阻止失效成果被确认或执行"的最小落点，AC-12 的前置）。

与幂等（ENT-002）、权限（ENT-003）的关系：本模块只做版本语义，不做权限判断、
不做幂等去重 —— 那两者是 router 层的依赖组合，本模块被组合调用。

归属（`assignment_id`，DR-0012 新增）
------------------------------------
成果原先只挂 `entrustment_id`（组织级授权），没有通向**单张委托**的关联键，
同一货主同一组织的多张委托会互相串成果。本模块现在多一个**可空**的
`assignment_id`：

* 它是**归属**，不是权限边界 —— 权限判定仍然只有 `authz.py` 一条入口；
* 历史行保持 NULL。NULL 表示"这份成果产生于归属机制之前"，**不表示它属于谁**；
  读取侧必须把 NULL 与"属于某委托"分开呈现，不得猜测回填；
* 归属的**有效性**（委托存在、与授权同货主同组织）由 HTTP 层在写入前校验；
  本模块只保证"写进去的就是传进来的"，不替调用方编造归属。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust.registry import (
    UnknownArtifactTypeError,
    unknown_fields,
    validate_payload,
)

SOURCE_AGENT = "agent"
SOURCE_MANUAL = "manual"

STATUS_ACTIVE = "active"
STATUS_VOID = "void"


class ArtifactError(RuntimeError):
    """成果版本机制的基础异常。HTTP 层按语义转 4xx。"""


class ArtifactNotFoundError(ArtifactError):
    """成果或版本不存在。HTTP 层应转 404。"""


class ArtifactVoidError(ArtifactError):
    """成果已作废。HTTP 层应转 409。"""


class ManualTakeoverError(ArtifactError):
    """人工接管中，Agent 不得替换生效版本。HTTP 层应转 409。"""


class ArtifactRevalidationError(ArtifactError):
    """成果有待复核项，不得设为生效版本。HTTP 层应转 409。

    PRD 第 297 行：「Before acceptance/execution, revalidate against current versions.」
    以及「a late Agent result … cannot **silently** become the current confirmed result」
    —— 所以这里不是"禁止修改"，而是**禁止静默成为生效版本**：
    解除路径是完成对应的复核任务（`ent_revalidation.status → resolved`），
    或让变更应用把它一并处理掉。
    """


class ArtifactPayloadError(ArtifactError):
    """成果内容不符合该类型的字段契约（未知字段 / 未知类型）。HTTP 层应转 400。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 ent_ 表时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(_FMT)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _load(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _row_to_revision(row: Any) -> dict[str, Any]:
    return {
        "revision_id": int(row["id"]),
        "artifact_id": int(row["artifact_id"]),
        "revision_no": int(row["revision_no"]),
        "payload": _load(row["payload_json"]),
        "source": str(row["source"]),
        "note": row["note"],
        "created_by": int(row["created_by"]),
        "created_at": str(row["created_at"]),
    }


def _row_to_artifact(row: Any) -> dict[str, Any]:
    return {
        "artifact_id": int(row["id"]),
        "entrustment_id": int(row["entrustment_id"]),
        "assignment_id": (int(row["assignment_id"]) if row["assignment_id"] is not None else None),
        "artifact_type": str(row["artifact_type"]),
        "current_revision_id": (
            int(row["current_revision_id"]) if row["current_revision_id"] is not None else None
        ),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


_ARTIFACT_COLS = (
    "id, entrustment_id, assignment_id, artifact_type, current_revision_id, "
    "status, created_at, updated_at"
)


def _get_artifact_row(session: Session, artifact_id: int) -> Any:
    return (
        session.execute(
            text(f"SELECT {_ARTIFACT_COLS} FROM ent_artifact WHERE id = :aid"),
            {"aid": artifact_id},
        )
        .mappings()
        .first()
    )


def get_artifact(session: Session, artifact_id: int) -> dict[str, Any]:
    """读取成果（含当前生效版本的完整内容）。

    `missing_fields` 由注册表的字段契约**即时派生**（不落库）：
    缺项是"这份成果当前还缺什么"，随每次编辑自然变化；一旦落库就会与内容漂移。

    Raises:
        ArtifactNotFoundError: 成果不存在。
    """
    row = _get_artifact_row(session, artifact_id)
    if row is None:
        raise ArtifactNotFoundError(f"成果 {artifact_id} 不存在")
    artifact = _row_to_artifact(row)
    artifact["current_revision"] = None
    artifact["missing_fields"] = []
    artifact["unknown_fields"] = []
    if artifact["current_revision_id"] is not None:
        cur = (
            session.execute(
                text(
                    "SELECT id, artifact_id, revision_no, payload_json, source, "
                    "note, created_by, created_at "
                    "FROM ent_artifact_revision WHERE id = :rid"
                ),
                {"rid": artifact["current_revision_id"]},
            )
            .mappings()
            .first()
        )
        if cur is not None:
            revision = _row_to_revision(cur)
            artifact["current_revision"] = revision
            try:
                artifact["missing_fields"] = validate_payload(
                    artifact["artifact_type"], revision["payload"]
                )
                artifact["unknown_fields"] = unknown_fields(
                    artifact["artifact_type"], revision["payload"]
                )
            except UnknownArtifactTypeError:
                # 历史数据可能早于注册表（类型当时未受约束）：如实标记，不假装合规
                artifact["missing_fields"] = []
                artifact["unknown_fields"] = []
                artifact["registry_status"] = "unknown_type"
    return artifact


def list_revisions(session: Session, artifact_id: int) -> list[dict[str, Any]]:
    """按版本号升序列出全部历史版本（append-only 审计视图）。"""
    rows = session.execute(
        text(
            "SELECT id, artifact_id, revision_no, payload_json, source, "
            "note, created_by, created_at "
            "FROM ent_artifact_revision WHERE artifact_id = :aid ORDER BY revision_no"
        ),
        {"aid": artifact_id},
    ).mappings()
    return [_row_to_revision(r) for r in rows]


_ASSIGNMENT_ITEM_COLS = (
    "a.id, a.assignment_id, a.entrustment_id, a.artifact_type, a.status, "
    "a.current_revision_id, r.revision_no AS current_revision_no, "
    "a.created_at, a.updated_at"
)


def _row_to_assignment_item(row: Any) -> dict[str, Any]:
    return {
        "artifact_id": int(row["id"]),
        "entrustment_id": int(row["entrustment_id"]),
        "assignment_id": int(row["assignment_id"]) if row["assignment_id"] is not None else None,
        "artifact_type": str(row["artifact_type"]),
        "status": str(row["status"]),
        "current_revision_id": (
            int(row["current_revision_id"]) if row["current_revision_id"] is not None else None
        ),
        "current_revision_no": (
            int(row["current_revision_no"]) if row["current_revision_no"] is not None else None
        ),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def list_by_assignment(
    session: Session, *, assignment_id: int, page: int = 1, size: int = 20
) -> tuple[int, list[dict[str, Any]]]:
    """单委托成果列表（DR-0012）。

    **精确匹配 `assignment_id`**，不做任何"回退到货主或组织"的放宽 —— 同一货主在同一
    组织下可能有多张委托，放宽一步就是把别的委托的成果显示在这张委托的工作台上。
    归属为 NULL 的历史成果**不在结果内**（它们还没有归属），由 `count_unassigned`
    单独如实报数：界面能说"另有 N 份历史成果尚未归属"，而不是把它们静默隐藏。

    `current_revision_id` 与 `current_revision_no` 成对返回，是"双入口同成果同版本"的
    取数依据：工作台与聊天卡引用这一对精确值，而不是各自再取一次"最新"。
    """
    total_row = (
        session.execute(
            text("SELECT COUNT(*) AS c FROM ent_artifact WHERE assignment_id = :aid"),
            {"aid": assignment_id},
        )
        .mappings()
        .first()
    )
    total = int(total_row["c"]) if total_row is not None else 0
    rows = session.execute(
        text(
            f"SELECT {_ASSIGNMENT_ITEM_COLS} FROM ent_artifact a "
            "LEFT JOIN ent_artifact_revision r ON r.id = a.current_revision_id "
            "WHERE a.assignment_id = :aid "
            "ORDER BY a.updated_at DESC, a.id DESC LIMIT :limit OFFSET :offset"
        ),
        {"aid": assignment_id, "limit": size, "offset": (page - 1) * size},
    ).mappings()
    return total, [_row_to_assignment_item(r) for r in rows]


def count_unassigned(session: Session, *, owner_user_id: int, org_id: int | None) -> int:
    """同一 (货主, 组织) 授权范围内**归属为空**的成果数（历史存量）。

    只报数、不改数据 —— 它是"归属机制上线前的存量"在界面上唯一的出口。
    按 (货主, 组织) 统计而非全表：口径被限定在调用者本就可见的授权范围内；
    用 `IN (子查询)` 而不是先挑一条 `ent_entrustment`：同一 (org, owner) 可能有多条
    授权记录，挑一条就是猜测 —— 这里只要计数，不需要也不该选边。
    """
    if org_id is None:
        return 0
    row = (
        session.execute(
            text(
                "SELECT COUNT(*) AS c FROM ent_artifact "
                "WHERE assignment_id IS NULL AND entrustment_id IN "
                "(SELECT id FROM ent_entrustment "
                " WHERE org_id = :org AND entrust_user_id = :owner)"
            ),
            {"org": org_id, "owner": owner_user_id},
        )
        .mappings()
        .first()
    )
    return int(row["c"]) if row is not None else 0


def create_artifact(
    session: Session,
    *,
    entrustment_id: int,
    artifact_type: str,
    payload: dict[str, Any],
    created_by: int,
    source: str = SOURCE_MANUAL,
    note: str | None = None,
    assignment_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """创建成果，首个版本 `revision_no = 1` 并立即绑定生效。

    内容按**注册表的字段契约**校验：未知类型、未知字段一律 400；
    缺必填字段不算错误（草稿允许不完整），但会在读取时如实出现在 `missing_fields`。

    Args:
        source: `agent`（模型产出）或 `manual`（人工产出）。
        assignment_id: 归属的委托单。`None` 表示不归属任何委托单（历史行与
            未绑定会话的产出）。**本函数不校验归属有效性** —— 那需要读委托单与
            授权链，属 HTTP 层职责（见 `artifacts_api._resolve_attribution`）；
            在这里悄悄"修正"一个不一致的归属，会让调用方永远发现不了自己的错误。
    """
    if not artifact_type or not artifact_type.strip():
        raise ArtifactError("artifact_type 不能为空")
    if source not in (SOURCE_AGENT, SOURCE_MANUAL):
        raise ArtifactError(f"未知来源 {source!r}，必须是 agent 或 manual")
    artifact_type = artifact_type.strip()
    try:
        validate_payload(artifact_type, payload)
    except UnknownArtifactTypeError as exc:
        raise ArtifactPayloadError(str(exc)) from exc
    current = now or utcnow_naive()
    ts = _fmt(current)

    result = cast(
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
                "atype": artifact_type.strip(),
                "status": STATUS_ACTIVE,
                "ts": ts,
            },
        ),
    )
    artifact_id = int(result.lastrowid or 0)

    rev = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_artifact_revision "
                "(artifact_id, revision_no, payload_json, source, note, created_by, created_at) "
                "VALUES (:aid, 1, :payload, :source, :note, :by, :ts)"
            ),
            {
                "aid": artifact_id,
                "payload": _dump(payload),
                "source": source,
                "note": note,
                "by": created_by,
                "ts": ts,
            },
        ),
    )
    revision_id = int(rev.lastrowid or 0)

    # 首版本即生效：创建本身就是"对这个版本的确认"
    session.execute(
        text("UPDATE ent_artifact SET current_revision_id = :rid WHERE id = :aid"),
        {"rid": revision_id, "aid": artifact_id},
    )
    session.commit()
    return get_artifact(session, artifact_id)


def append_revision(
    session: Session,
    *,
    artifact_id: int,
    payload: dict[str, Any],
    actor_id: int,
    source: str,
    note: str | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """追加新版本（编辑动作）。

    **不改变生效版本** —— 生效版本只能被显式确认改变。
    历史版本永不改写（append-only）。

    Args:
        commit: 是否在本函数内提交。**默认 `True`**（既有调用方行为不变）。
            跨对象写路径（如 A2 的「应用变更」）传 `False`，由外层统一提交 ——
            否则"第 1 个成果已提交、第 2 个失败"会留下**部分生效**（HO 裁决 P4-A7）。

    Raises:
        ArtifactNotFoundError: 成果不存在。
        ArtifactError: 成果已作废（作废后不再接受新版本）或来源未知。
    """
    if source not in (SOURCE_AGENT, SOURCE_MANUAL):
        raise ArtifactError(f"未知来源 {source!r}，必须是 agent 或 manual")
    row = _get_artifact_row(session, artifact_id)
    if row is None:
        raise ArtifactNotFoundError(f"成果 {artifact_id} 不存在")
    if str(row["status"]) == STATUS_VOID:
        raise ArtifactVoidError(f"成果 {artifact_id} 已作废，不能再追加版本")
    try:
        missing = validate_payload(str(row["artifact_type"]), payload)
    except UnknownArtifactTypeError as exc:
        raise ArtifactPayloadError(str(exc)) from exc

    current = now or utcnow_naive()
    ts = _fmt(current)
    max_no = (
        session.execute(
            text(
                "SELECT COALESCE(MAX(revision_no), 0) AS m FROM ent_artifact_revision "
                "WHERE artifact_id = :aid"
            ),
            {"aid": artifact_id},
        )
        .mappings()
        .first()
    )
    next_no = int(max_no["m"]) + 1 if max_no is not None else 1

    rev = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_artifact_revision "
                "(artifact_id, revision_no, payload_json, source, note, created_by, created_at) "
                "VALUES (:aid, :no, :payload, :source, :note, :by, :ts)"
            ),
            {
                "aid": artifact_id,
                "no": next_no,
                "payload": _dump(payload),
                "source": source,
                "note": note,
                "by": actor_id,
                "ts": ts,
            },
        ),
    )
    session.execute(
        text("UPDATE ent_artifact SET updated_at = :ts WHERE id = :aid"),
        {"ts": ts, "aid": artifact_id},
    )
    if commit:
        session.commit()
    return {
        "revision_id": int(rev.lastrowid or 0),
        "artifact_id": artifact_id,
        "revision_no": next_no,
        "superseding_current": False,
        "missing_fields": missing,
    }


def confirm_revision(
    session: Session,
    *,
    artifact_id: int,
    revision_no: int,
    actor_id: int,
    as_source: str = SOURCE_MANUAL,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """确认并把生效版本绑定到 `revision_no` 指向的**精确版本**。

    Args:
        as_source: 确认动作的发起侧。`agent` 表示由 Agent 流程驱动确认 ——
            当生效版本是人工产出时会被拒绝（人工接管优先）。
        commit: 同 `append_revision`。跨对象写路径传 `False`，由外层统一提交。

    Raises:
        ArtifactNotFoundError: 成果或版本不存在。
        ArtifactVoidError: 成果已作废（失效成果不可确认，AC-12 前置）。
        ArtifactRevalidationError: 成果有未完成的复核项（AC-12 后半条）——
            "确认"正是"成为生效版本"的那一步，而 PRD 第 297 行要求
            执行/接受**之前**必须按当前版本复核过。解除路径见该异常类说明。
        ManualTakeoverError: 人工接管中，Agent 不得替换生效版本。
    """
    if as_source not in (SOURCE_AGENT, SOURCE_MANUAL):
        raise ArtifactError(f"未知来源 {as_source!r}，必须是 agent 或 manual")
    row = _get_artifact_row(session, artifact_id)
    if row is None:
        raise ArtifactNotFoundError(f"成果 {artifact_id} 不存在")
    if str(row["status"]) == STATUS_VOID:
        raise ArtifactVoidError(f"成果 {artifact_id} 已作废，不能确认任何版本")

    # ── AC-12 后半条：有待复核项时不得**静默**成为生效版本 ──────────────────
    # ⚠️ 与 `apply_case` 的顺序是安全的：那里先 confirm、**之后**才写传播标记
    # （同一事务内），所以"变更应用"不会被自己刚生成的标记挡住。
    from app.modules.entrust import revalidation as reval_svc  # 局部导入：避免成环

    blockers = reval_svc.open_for_artifact(session, artifact_id)
    if blockers:
        cases = sorted({int(b["exception_id"]) for b in blockers})
        areas = sorted({str(b["area"]) for b in blockers})
        raise ArtifactRevalidationError(
            f"成果 {artifact_id} 有未完成的复核项（案件 {cases}；区域：{'、'.join(areas)}），"
            "不能设为生效版本 —— 请先完成对应复核任务，或由变更应用一并处理"
        )

    rev = (
        session.execute(
            text(
                "SELECT id, artifact_id, revision_no, payload_json, source, "
                "note, created_by, created_at "
                "FROM ent_artifact_revision "
                "WHERE artifact_id = :aid AND revision_no = :no"
            ),
            {"aid": artifact_id, "no": revision_no},
        )
        .mappings()
        .first()
    )
    if rev is None:
        raise ArtifactNotFoundError(f"成果 {artifact_id} 不存在版本 {revision_no}")

    current_id = row["current_revision_id"]
    if as_source == SOURCE_AGENT and current_id is not None:
        cur = (
            session.execute(
                text("SELECT source FROM ent_artifact_revision WHERE id = :rid"),
                {"rid": int(current_id)},
            )
            .mappings()
            .first()
        )
        if cur is not None and str(cur["source"]) == SOURCE_MANUAL:
            raise ManualTakeoverError(
                f"成果 {artifact_id} 生效版本为人工产出，Agent 不能替换；需人工确认"
            )

    current = now or utcnow_naive()
    session.execute(
        text(
            "UPDATE ent_artifact SET current_revision_id = :rid, updated_at = :ts WHERE id = :aid"
        ),
        {"rid": int(rev["id"]), "ts": _fmt(current), "aid": artifact_id},
    )
    if commit:
        session.commit()
    return get_artifact(session, artifact_id)


def void_artifact(
    session: Session,
    *,
    artifact_id: int,
    actor_id: int,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """作废成果。作废后：不可确认任何版本、不可追加新版本。

    历史版本仍可读（审计），这是"服务端阻止失效成果被确认或执行"的开关。
    """
    row = _get_artifact_row(session, artifact_id)
    if row is None:
        raise ArtifactNotFoundError(f"成果 {artifact_id} 不存在")
    current = now or utcnow_naive()
    session.execute(
        text("UPDATE ent_artifact SET status = :st, updated_at = :ts WHERE id = :aid"),
        {"st": STATUS_VOID, "ts": _fmt(current), "aid": artifact_id},
    )
    session.commit()
    artifact = get_artifact(session, artifact_id)
    artifact["void_reason"] = reason
    artifact["voided_by"] = actor_id
    return artifact
