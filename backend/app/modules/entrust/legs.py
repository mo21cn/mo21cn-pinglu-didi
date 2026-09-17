"""航段命令：建段 / 改段（留版本）/ 版本历史 —— 纯业务，可单测。

口径来源见 `backend/migrations/ent_leg_revision.py` 的模块文档（HO 2026-09-17 裁定）：
**谁能建段＝参与方**（不设角色门槛）、**不强制 公–水–公**、**改段保留版本**。

本模块的三个取舍（都是"为什么不那样写"）
----------------------------------------
1. **写入口不校验 `mode` 取值域**，只校验"非空 + 长度"。
   `road/water/rail` 只是既有数据用到的取值，不是**写入口的枚举**：
   把它做成枚举，等于用一行代码把"只能有一种方案"固化进系统 ——
   而裁定明确说不强制。读侧对未知 `mode` 的处理是**原样回显**
   （`plan.mode_label`），所以"未知"不会变成空白，也不会被猜成"公路"。
2. **`seq` 重复交给数据库唯一键判**，不做"先查有没有再插"。
   本表有 `UNIQUE (assignment_id, seq)`；并发下先查后插必然漏 ——
   判据必须是约束，`IntegrityError` 就是那条判据（技能 §2）。
3. **"没有改动"要报错，不能静默写一版**。
   版本历史是给人读的（"改过什么"）；写进一版"什么都没改"的记录，
   会让这条历史失去可读性。⇒ 至少一个字段真的变了才允许落库。

⚠️ 本模块**不**判权限。谁能写由端点层决定（`legs_api._assert_party`）。
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.entrust.access import utcnow_naive
from app.modules.entrust.plan import mode_label

_TS = "%Y-%m-%d %H:%M:%S"

#: 与迁移里的列宽一致（超长由 Python 侧先拦，好给出一句人话而不是 DB 的 1406）。
MAX_MODE_LEN = 16
MAX_NAME_LEN = 64
MAX_NOTE_LEN = 255

CHANGE_CREATED = "created"
CHANGE_UPDATED = "updated"

#: 一次最多读多少条版本行。与 `plan.TASK_LIMIT` 同口径：**有界**，
#: 免得一条被人改了几千次的航段把响应撑爆。
REVISION_LIMIT = 200


class LegError(RuntimeError):
    """入参不合法。HTTP 层转 400。"""


class LegNotFoundError(LegError):
    """航段不存在（**或不属于这张委托单**）。HTTP 层转 404 —— 不区分两者，不泄漏存在性。"""


class LegStateError(LegError):
    """与当前状态冲突（如同一 `seq` 已被占用）。HTTP 层转 409。"""


def _stamp() -> str:
    return utcnow_naive().strftime(_TS)


def _clean_mode(raw: Any) -> str:
    """运输方式：**只**去空白 + 查长度，**不做枚举校验**（见模块文档第 1 条）。"""
    value = str(raw).strip()
    if not value:
        raise LegError("运输方式不能为空")
    if len(value) > MAX_MODE_LEN:
        raise LegError(f"运输方式过长（上限 {MAX_MODE_LEN} 字符）")
    return value


def _clean_name(raw: Any, label: str) -> str:
    value = str(raw).strip()
    if not value:
        raise LegError(f"{label}不能为空")
    if len(value) > MAX_NAME_LEN:
        raise LegError(f"{label}过长（上限 {MAX_NAME_LEN} 字符）")
    return value


def _clean_seq(raw: Any) -> int:
    try:
        seq = int(raw)
    except (TypeError, ValueError) as exc:
        raise LegError("航段顺序必须是整数") from exc
    if seq < 1:
        # `seq` 自 1 起是**读侧的判据**（按 seq 排序即方案顺序）⇒ 0 与负数没有语义。
        raise LegError("航段顺序自 1 起（不能是 0 或负数）")
    return seq


def _clean_note(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        # 空串与"没写"是同一件事，统一存 NULL —— 免得库里同时存在两种"没写"。
        return None
    if len(value) > MAX_NOTE_LEN:
        raise LegError(f"改动说明过长（上限 {MAX_NOTE_LEN} 字符）")
    return value


def load_leg(session: Session, *, assignment_id: int, leg_id: int) -> dict[str, Any] | None:
    """按 `(航段, 委托)` 读当前行（含**当前版本号**）。

    ⚠️ 两个条件缺一不可：只按 `leg_id` 查，会让"路径上的委托单"变成一个
    **不参与判定的装饰** —— 于是 A 单的编号能改到 B 单的航段上（作用域自洽）。

    `revision_no` 用子查询取**该段的当前版本号**（不是 `ent_leg` 的列 ——
    `ent_leg` 只存当前状态，版本在 append-only 的 `ent_leg_revision` 里）。
    写在响应里是有用的：调用方据此知道"我这一改是第几版"，
    而不用再多发一次请求去数历史。
    """
    row = (
        session.execute(
            text(
                "SELECT l.id AS leg_id, l.assignment_id, l.seq, l.mode, l.from_name, l.to_name, "
                "l.created_at, l.updated_at, "
                "(SELECT COALESCE(MAX(r.revision_no), 0) FROM ent_leg_revision r "
                " WHERE r.leg_id = l.id) AS revision_no "
                "FROM ent_leg l "
                "WHERE l.id = :leg_id AND l.assignment_id = :assignment_id"
            ),
            {"leg_id": leg_id, "assignment_id": assignment_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def leg_view(leg: dict[str, Any]) -> dict[str, Any]:
    """把 `ent_leg` 行投影成对外的航段对象。

    字段面与 `plan.list_legs`（读模型）**保持同一份**（6 个键）＋「当前版本号」与
    「更新时间」两个写侧才有的键。多一个字段就多一个会各自演化的落点，
    所以这里刻意**只**加这两个 —— 而且它们只在**写响应**里出现。
    """
    return {
        "leg_id": int(leg["leg_id"]),
        "seq": int(leg["seq"]),
        "mode": str(leg["mode"]),
        "mode_label": mode_label(str(leg["mode"])),
        "from_name": str(leg["from_name"]),
        "to_name": str(leg["to_name"]),
        "revision_no": int(leg["revision_no"]),
        "updated_at": str(leg["updated_at"]),
    }


def _require_leg(session: Session, *, assignment_id: int, leg_id: int) -> dict[str, Any]:
    leg = load_leg(session, assignment_id=assignment_id, leg_id=leg_id)
    if leg is None:
        raise LegNotFoundError("航段不存在")
    return leg


def _next_revision_no(session: Session, *, leg_id: int) -> int:
    """该段的**下一个**版本号（1 起）。

    ⚠️ 用 `.scalar()` 而不是 `.first()[0]`：`first()` 的返回类型是
    `Row[Any] | None`，mypy 会如实报"不可索引" —— 而那正是它想提醒的事
    （查询可能一行都不返回，必须先处理 None）。`COALESCE` 保证这里一定有一个值。
    """
    current = session.execute(
        text("SELECT COALESCE(MAX(revision_no), 0) FROM ent_leg_revision WHERE leg_id = :leg_id"),
        {"leg_id": leg_id},
    ).scalar()
    return int(current or 0) + 1


def _write_revision(
    session: Session,
    *,
    leg_id: int,
    assignment_id: int,
    seq: int,
    mode: str,
    from_name: str,
    to_name: str,
    change_kind: str,
    change_note: str | None,
    actor_user_id: int,
    created_at: str,
) -> int:
    """把"这一刻的航段状态"写进 append-only 版本表，返回版本行 id。

    版本行存的是**快照**（当时的 seq/mode/起终点），不是"指向当前行" ——
    否则 `ent_leg` 一改，历史也跟着变，版本就白留了（与
    `ent_artifact_revision` 同一条理由）。
    """
    res = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_leg_revision "
                "(leg_id, assignment_id, seq, mode, from_name, to_name, "
                " revision_no, change_kind, change_note, actor_user_id, created_at) "
                "VALUES (:leg_id, :assignment_id, :seq, :mode, :from_name, :to_name, "
                " :revision_no, :change_kind, :change_note, :actor_user_id, :created_at)"
            ),
            {
                "leg_id": leg_id,
                "assignment_id": assignment_id,
                "seq": seq,
                "mode": mode,
                "from_name": from_name,
                "to_name": to_name,
                "revision_no": _next_revision_no(session, leg_id=leg_id),
                "change_kind": change_kind,
                "change_note": change_note,
                "actor_user_id": actor_user_id,
                "created_at": created_at,
            },
        ),
    )
    return int(res.lastrowid or 0)


def create_leg(
    session: Session,
    *,
    assignment_id: int,
    seq: int,
    mode: str,
    from_name: str,
    to_name: str,
    actor_user_id: int = 0,
    change_note: str | None = None,
) -> dict[str, Any]:
    """建一段航段（同时写下它的第 1 版）。

    ⛔ **不校验"必须公–水–公"**（裁定第二条）：`mode` 只要求非空，
    段数也不限制。约束只保留结构完整性 —— 起终点非空、`seq` ≥1 且委托内唯一。

    `seq` 冲突走**数据库唯一键**：`UNIQUE (assignment_id, seq)` 命中 ⇒
    `LegStateError`（409）并提示"要改它请用改段命令（会留版本）"。
    提示里点出改段，是因为"建段撞号"最常见的来意其实是"我想改那一段"。
    """
    mode_v = _clean_mode(mode)
    from_v = _clean_name(from_name, "起点")
    to_v = _clean_name(to_name, "终点")
    seq_v = _clean_seq(seq)
    note_v = _clean_note(change_note)
    ts = _stamp()

    try:
        res = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_leg "
                    "(assignment_id, seq, mode, from_name, to_name, created_at, updated_at) "
                    "VALUES (:assignment_id, :seq, :mode, :from_name, :to_name, :ts, :ts)"
                ),
                {
                    "assignment_id": assignment_id,
                    "seq": seq_v,
                    "mode": mode_v,
                    "from_name": from_v,
                    "to_name": to_v,
                    "ts": ts,
                },
            ),
        )
    except IntegrityError as exc:
        session.rollback()
        raise LegStateError(
            f"这张委托单上已经有第 {seq_v} 段了 —— 要改它请用改段命令（会保留版本）"
        ) from exc

    leg_id = int(res.lastrowid or 0)
    _write_revision(
        session,
        leg_id=leg_id,
        assignment_id=assignment_id,
        seq=seq_v,
        mode=mode_v,
        from_name=from_v,
        to_name=to_v,
        change_kind=CHANGE_CREATED,
        change_note=note_v,
        actor_user_id=actor_user_id,
        created_at=ts,
    )
    session.commit()
    return leg_view(_require_leg(session, assignment_id=assignment_id, leg_id=leg_id))


def update_leg(
    session: Session,
    *,
    assignment_id: int,
    leg_id: int,
    mode: str | None = None,
    from_name: str | None = None,
    to_name: str | None = None,
    seq: int | None = None,
    actor_user_id: int = 0,
    change_note: str | None = None,
) -> dict[str, Any]:
    """改一段航段 —— **旧版本保留**（append-only），当前行就地更新。

    两个 400 是**有意**的（都是"别让版本历史变噪音"）：

    * **一个字段都没传** ⇒ 400："没有要修改的字段"。空 PATCH 多半是调用方
      把请求组错了，静默成功会让它以为改过了；
    * **传了但值与当前版本完全相同** ⇒ 400。判据是"值"，不是"传没传" ——
      `{"to_name": "南宁港"}` 而当前就是"南宁港"，那不是一次改动。

    ⚠️ 改 `seq` 可能与**别的段**撞号 ⇒ 同样由唯一键判，映射成 409。
    """
    leg = _require_leg(session, assignment_id=assignment_id, leg_id=leg_id)

    fields: dict[str, Any] = {}
    if mode is not None:
        fields["mode"] = _clean_mode(mode)
    if from_name is not None:
        fields["from_name"] = _clean_name(from_name, "起点")
    if to_name is not None:
        fields["to_name"] = _clean_name(to_name, "终点")
    if seq is not None:
        fields["seq"] = _clean_seq(seq)

    if not fields:
        raise LegError("没有要修改的字段 —— 改段至少要带一个字段（会留下一版历史）")

    changed = {k: v for k, v in fields.items() if leg.get(k) != v}
    if not changed:
        raise LegError("传入的值与当前版本完全相同 —— 这不是一次改动，不落版本")

    note_v = _clean_note(change_note)
    ts = _stamp()
    new_values = {**{k: leg[k] for k in ("seq", "mode", "from_name", "to_name")}, **changed}

    try:
        session.execute(
            text(
                "UPDATE ent_leg SET seq = :seq, mode = :mode, from_name = :from_name, "
                "to_name = :to_name, updated_at = :ts "
                "WHERE id = :leg_id AND assignment_id = :assignment_id"
            ),
            {
                "seq": new_values["seq"],
                "mode": new_values["mode"],
                "from_name": new_values["from_name"],
                "to_name": new_values["to_name"],
                "ts": ts,
                "leg_id": leg_id,
                "assignment_id": assignment_id,
            },
        )
    except IntegrityError as exc:
        session.rollback()
        raise LegStateError(
            f"这张委托单上已经有第 {new_values['seq']} 段了 —— 两个段不能占用同一个顺序号"
        ) from exc

    _write_revision(
        session,
        leg_id=leg_id,
        assignment_id=assignment_id,
        seq=int(new_values["seq"]),
        mode=str(new_values["mode"]),
        from_name=str(new_values["from_name"]),
        to_name=str(new_values["to_name"]),
        change_kind=CHANGE_UPDATED,
        change_note=note_v,
        actor_user_id=actor_user_id,
        created_at=ts,
    )
    session.commit()
    return leg_view(_require_leg(session, assignment_id=assignment_id, leg_id=leg_id))


def list_leg_revisions(
    session: Session, *, assignment_id: int, leg_id: int
) -> list[dict[str, Any]]:
    """某一段的**全部**历史版本（按 `revision_no` 升序 = 改动的先后）。

    ⚠️ 先 `_require_leg` 一次：**历史不能成为绕过可见性判定的后门** ——
    否则"航段不属于这张单"这件事在读历史时被跳过，路径上的委托单又成了装饰。
    """
    _require_leg(session, assignment_id=assignment_id, leg_id=leg_id)
    rows = (
        session.execute(
            text(
                "SELECT id, leg_id, assignment_id, seq, mode, from_name, to_name, "
                "revision_no, change_kind, change_note, actor_user_id, created_at "
                "FROM ent_leg_revision "
                "WHERE leg_id = :leg_id AND assignment_id = :assignment_id "
                "ORDER BY revision_no LIMIT :lim"
            ),
            {"leg_id": leg_id, "assignment_id": assignment_id, "lim": REVISION_LIMIT},
        )
        .mappings()
        .all()
    )
    # `mode_label` 在这里补：标签表在本模块已 import（`plan`），
    # 而 schemas 是纯校验层、不该去依赖业务模块算标签（见 `leg_revision_out`）。
    return [{**dict(r), "mode_label": mode_label(str(r["mode"]))} for r in rows]


__all__ = [
    "CHANGE_CREATED",
    "CHANGE_UPDATED",
    "REVISION_LIMIT",
    "LegError",
    "LegNotFoundError",
    "LegStateError",
    "create_leg",
    "leg_view",
    "list_leg_revisions",
    "load_leg",
    "update_leg",
]
