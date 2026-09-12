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
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

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
        "artifact_type": str(row["artifact_type"]),
        "current_revision_id": (
            int(row["current_revision_id"]) if row["current_revision_id"] is not None else None
        ),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _get_artifact_row(session: Session, artifact_id: int) -> Any:
    return (
        session.execute(
            text(
                "SELECT id, entrustment_id, artifact_type, current_revision_id, "
                "status, created_at, updated_at "
                "FROM ent_artifact WHERE id = :aid"
            ),
            {"aid": artifact_id},
        )
        .mappings()
        .first()
    )


def get_artifact(session: Session, artifact_id: int) -> dict[str, Any]:
    """读取成果（含当前生效版本的完整内容）。

    Raises:
        ArtifactNotFoundError: 成果不存在。
    """
    row = _get_artifact_row(session, artifact_id)
    if row is None:
        raise ArtifactNotFoundError(f"成果 {artifact_id} 不存在")
    artifact = _row_to_artifact(row)
    artifact["current_revision"] = None
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
            artifact["current_revision"] = _row_to_revision(cur)
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


def create_artifact(
    session: Session,
    *,
    entrustment_id: int,
    artifact_type: str,
    payload: dict[str, Any],
    created_by: int,
    source: str = SOURCE_MANUAL,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """创建成果，首个版本 `revision_no = 1` 并立即绑定生效。

    Args:
        source: `agent`（模型产出）或 `manual`（人工产出）。
    """
    if not artifact_type or not artifact_type.strip():
        raise ArtifactError("artifact_type 不能为空")
    if source not in (SOURCE_AGENT, SOURCE_MANUAL):
        raise ArtifactError(f"未知来源 {source!r}，必须是 agent 或 manual")
    current = now or utcnow_naive()
    ts = _fmt(current)

    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_artifact "
                "(entrustment_id, artifact_type, current_revision_id, status, created_at, updated_at) "
                "VALUES (:eid, :atype, NULL, :status, :ts, :ts)"
            ),
            {
                "eid": entrustment_id,
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
) -> dict[str, Any]:
    """追加新版本（编辑动作）。

    **不改变生效版本** —— 生效版本只能被显式确认改变。
    历史版本永不改写（append-only）。

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
    session.commit()
    return {
        "revision_id": int(rev.lastrowid or 0),
        "artifact_id": artifact_id,
        "revision_no": next_no,
        "superseding_current": False,
    }


def confirm_revision(
    session: Session,
    *,
    artifact_id: int,
    revision_no: int,
    actor_id: int,
    as_source: str = SOURCE_MANUAL,
    now: datetime | None = None,
) -> dict[str, Any]:
    """确认并把生效版本绑定到 `revision_no` 指向的**精确版本**。

    Args:
        as_source: 确认动作的发起侧。`agent` 表示由 Agent 流程驱动确认 ——
            当生效版本是人工产出时会被拒绝（人工接管优先）。

    Raises:
        ArtifactNotFoundError: 成果或版本不存在。
        ArtifactVoidError: 成果已作废（失效成果不可确认，AC-12 前置）。
        ManualTakeoverError: 人工接管中，Agent 不得替换生效版本。
    """
    if as_source not in (SOURCE_AGENT, SOURCE_MANUAL):
        raise ArtifactError(f"未知来源 {as_source!r}，必须是 agent 或 manual")
    row = _get_artifact_row(session, artifact_id)
    if row is None:
        raise ArtifactNotFoundError(f"成果 {artifact_id} 不存在")
    if str(row["status"]) == STATUS_VOID:
        raise ArtifactVoidError(f"成果 {artifact_id} 已作废，不能确认任何版本")

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
