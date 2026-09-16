"""会话与消息的服务层，以及**Agent 执行范围**的构造器（ENT-011）。

会话是什么
----------
会话是"某个人在某个授权上下文的某个专业槽位里的一次连续对话"。三件事必须显式绑定，
而不是隐含在 URL 里：

1. **授权上下文**（`entrustment_id` / `assignment_id`）：决定数据边界；
2. **操作者**（`created_by`）：决定"谁让 Agent 干活"，审计要回答这个问题；
3. **专业槽位**（`agent_specialty`）：决定 Agent 能做哪些动作、能产出哪些成果类型。

绑定不可变
----------
本模块**不提供任何修改绑定关系的接口**。跨屏不变量「已产生任务绑定成果的会话不得
改绑到另一张委托」因此是结构性成立的，而不是靠每次操作前检查一遍。

Agent 执行范围（公共机制）
--------------------------
`AgentScope` 把"绑定组织·委托·操作者·允许动作"收敛成一个不可变对象，由
`build_agent_scope()` 在**权限校验通过之后**构造。作业层拿到的只有这个对象 ——
它没有 `db`，没有用户 token，能做的事被限制在 `allowed_actions` /
`allowed_artifact_types` 两个集合里。这样"Agent 越权"要么是范围声明写错
（可被测试抓住），要么根本无从表达。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.modules.entrust.envelope import (
    SPECIALTY_AG01,
    SPECIALTY_AG02,
    assert_specialty_open,
    specialty_label,
)

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

ROLE_USER = "user"
ROLE_AGENT = "agent"
ROLE_SYSTEM = "system"

SOURCE_MANUAL = "manual"
SOURCE_AGENT = "agent"
SOURCE_DETERMINISTIC = "deterministic"

_ALLOWED_ROLES = frozenset({ROLE_USER, ROLE_AGENT, ROLE_SYSTEM})
_ALLOWED_SOURCES = frozenset({SOURCE_MANUAL, SOURCE_AGENT, SOURCE_DETERMINISTIC})

#: 各专业允许产出的成果类型（注册表取值域的子集）。
#: 委托助理只做理解与建议，**不产出任何成果** —— 所以它是空集，而不是"顺便允许"。
SPECIALTY_ARTIFACT_TYPES: dict[str, frozenset[str]] = {
    SPECIALTY_AG01: frozenset(),
    SPECIALTY_AG02: frozenset({"quote_parsed", "supplier_compare", "customer_quote"}),
}

#: 各专业允许建议的动作。这些是**建议**，不是执行能力（AC-09）。
SPECIALTY_ACTIONS: dict[str, frozenset[str]] = {
    SPECIALTY_AG01: frozenset({"ask_missing_field", "suggest_next_step", "summarize_tasks"}),
    SPECIALTY_AG02: frozenset({"parse_quote", "compare_suppliers", "assemble_customer_quote"}),
}


class SessionError(RuntimeError):
    """会话服务基础异常。HTTP 层按语义转 4xx。"""


class SessionNotFoundError(SessionError):
    """会话不存在。HTTP 层应转 404。"""


class SessionArchivedError(SessionError):
    """会话已归档，不再接受新消息/作业。HTTP 层应转 409。"""


class SessionBindingError(SessionError):
    """会话绑定上下文非法。HTTP 层应转 400。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 ent_ 表时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(_FMT)


def _text_ts(raw: Any) -> str | None:
    """时间列 → 统一文本表示。

    响应模型声明的是 `str`，而 MySQL 的 DATETIME 取回是 `datetime`、SQLite 是
    `str`。不做归一会出现在"开发库全绿、生产库 500"的经典方言缺陷（BASE-002）。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _as_int(raw: Any) -> int | None:
    return int(raw) if raw is not None else None


def _row_to_session(row: Any) -> dict[str, Any]:
    return {
        "session_id": int(row["id"]),
        "entrustment_id": _as_int(row["entrustment_id"]),
        "assignment_id": _as_int(row["assignment_id"]),
        "owner_user_id": int(row["owner_user_id"]),
        "org_id": _as_int(row["org_id"]),
        "created_by": int(row["created_by"]),
        "agent_specialty": row["agent_specialty"],
        "agent_specialty_label": (
            specialty_label(str(row["agent_specialty"])) if row["agent_specialty"] else None
        ),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "revision": int(row["revision"]),
        "created_at": _text_ts(row["created_at"]),
        "updated_at": _text_ts(row["updated_at"]),
    }


def _row_to_message(row: Any) -> dict[str, Any]:
    return {
        "message_id": int(row["id"]),
        "session_id": int(row["session_id"]),
        "seq": int(row["seq"]),
        "role": str(row["role"]),
        "content": str(row["content"]),
        "source": str(row["source"]),
        "job_id": _as_int(row["job_id"]),
        "created_by": int(row["created_by"]),
        "created_at": _text_ts(row["created_at"]),
    }


_SESSION_COLS = (
    "id, entrustment_id, assignment_id, owner_user_id, org_id, created_by, "
    "agent_specialty, title, status, revision, created_at, updated_at"
)


def get_session(session: Session, session_id: int) -> dict[str, Any]:
    """读会话。

    Raises:
        SessionNotFoundError: 会话不存在。
    """
    row = (
        session.execute(
            text(f"SELECT {_SESSION_COLS} FROM ent_session WHERE id = :sid"),
            {"sid": session_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise SessionNotFoundError(f"会话 {session_id} 不存在")
    return _row_to_session(row)


def create_session(
    session: Session,
    *,
    entrustment_id: int | None,
    assignment_id: int | None,
    owner_user_id: int,
    org_id: int | None,
    created_by: int,
    specialty: str | None,
    title: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """创建会话。

    Args:
        specialty: 专业代码；`None` 表示通用会话壳（可对话，但没有专业能力）。
            非 `None` 时必须是**已开放**的专业（`assert_specialty_open`）。

    Raises:
        SessionBindingError: 既没有授权也没有委托单（无上下文的会话无处取数）。
        SpecialtyNotOpenError: 专业未知或未开放。
    """
    if entrustment_id is None and assignment_id is None:
        raise SessionBindingError("会话必须绑定委托授权或委托单之一")
    if not title or not title.strip():
        raise SessionBindingError("会话标题不能为空")
    if specialty is not None:
        specialty = assert_specialty_open(specialty)

    current = now or utcnow_naive()
    ts = _fmt(current)
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_session "
                "(entrustment_id, assignment_id, owner_user_id, org_id, created_by, "
                " agent_specialty, title, status, revision, created_at, updated_at) "
                "VALUES (:eid, :aid, :owner, :org, :by, :spec, :title, :status, 1, :ts, :ts)"
            ),
            {
                "eid": entrustment_id,
                "aid": assignment_id,
                "owner": owner_user_id,
                "org": org_id,
                "by": created_by,
                "spec": specialty,
                "title": title.strip(),
                "status": STATUS_ACTIVE,
                "ts": ts,
            },
        ),
    )
    session.commit()
    return get_session(session, int(result.lastrowid or 0))


def list_sessions(
    session: Session,
    *,
    owner_user_id: int | None = None,
    org_id: int | None = None,
    created_by: int | None = None,
    agent_specialty: str | None = None,
    status: str | None = None,
    assignment_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[int, list[dict[str, Any]]]:
    """分页列会话（过滤条件全部可选，由调用方按可见性传参）。

    `assignment_id` 是 S2 首片加的：会话页从工作台进来时手里只有委托单号，
    没有委托授权号 —— 没有它，页面就得把整页会话拉下来在前端筛，
    既多传数据又会把"该组织别的单子的会话"一并暴露给前端。
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if assignment_id is not None:
        clauses.append("assignment_id = :aid")
        params["aid"] = assignment_id
    if owner_user_id is not None:
        clauses.append("owner_user_id = :owner")
        params["owner"] = owner_user_id
    if org_id is not None:
        clauses.append("org_id = :org")
        params["org"] = org_id
    if created_by is not None:
        clauses.append("created_by = :by")
        params["by"] = created_by
    if agent_specialty is not None:
        clauses.append("agent_specialty = :spec")
        params["spec"] = agent_specialty
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = int(
        session.execute(text(f"SELECT COUNT(*) AS c FROM ent_session {where}"), params).scalar_one()
    )
    rows = (
        session.execute(
            text(
                f"SELECT {_SESSION_COLS} FROM ent_session {where} "
                "ORDER BY updated_at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": size, "offset": (page - 1) * size},
        )
        .mappings()
        .all()
    )
    return total, [_row_to_session(r) for r in rows]


def next_seq(session: Session, session_id: int) -> int:
    """会话内下一条消息序号（`UNIQUE(session_id, seq)` 兜住并发重复）。"""
    current = session.execute(
        text("SELECT COALESCE(MAX(seq), 0) AS m FROM ent_session_message WHERE session_id = :sid"),
        {"sid": session_id},
    ).scalar_one()
    return int(current) + 1


def append_message(
    session: Session,
    *,
    session_id: int,
    role: str,
    content: str,
    source: str,
    created_by: int,
    job_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """追加一条消息（append-only）。

    归档会话**不再接受新消息** —— 归档是"这次对话结束了"，不是"隐藏起来"。

    Raises:
        SessionNotFoundError / SessionArchivedError / SessionError(取值域非法)
    """
    if role not in _ALLOWED_ROLES:
        raise SessionError(f"未知消息角色 {role!r}；允许：{sorted(_ALLOWED_ROLES)}")
    if source not in _ALLOWED_SOURCES:
        raise SessionError(f"未知消息来源 {source!r}；允许：{sorted(_ALLOWED_SOURCES)}")
    if not content or not content.strip():
        raise SessionError("消息内容不能为空")

    row = (
        session.execute(
            text("SELECT id, status FROM ent_session WHERE id = :sid"), {"sid": session_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        raise SessionNotFoundError(f"会话 {session_id} 不存在")
    if str(row["status"]) == STATUS_ARCHIVED:
        raise SessionArchivedError(f"会话 {session_id} 已归档，不能再追加消息")

    current = now or utcnow_naive()
    ts = _fmt(current)
    seq = next_seq(session, session_id)
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_session_message "
                "(session_id, seq, role, content, source, job_id, created_by, created_at) "
                "VALUES (:sid, :seq, :role, :content, :source, :job, :by, :ts)"
            ),
            {
                "sid": session_id,
                "seq": seq,
                "role": role,
                "content": content.strip(),
                "source": source,
                "job": job_id,
                "by": created_by,
                "ts": ts,
            },
        ),
    )
    session.execute(
        text("UPDATE ent_session SET updated_at = :ts WHERE id = :sid"),
        {"ts": ts, "sid": session_id},
    )
    session.commit()
    return {
        "message_id": int(result.lastrowid or 0),
        "session_id": session_id,
        "seq": seq,
        "role": role,
        "content": content.strip(),
        "source": source,
        "job_id": job_id,
        "created_by": created_by,
        "created_at": ts,
    }


def list_messages(session: Session, session_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    """按序号升序列出消息（时间线视图）。"""
    rows = (
        session.execute(
            text(
                "SELECT id, session_id, seq, role, content, source, job_id, created_by, created_at "
                "FROM ent_session_message WHERE session_id = :sid ORDER BY seq LIMIT :limit"
            ),
            {"sid": session_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_row_to_message(r) for r in rows]


def archive_session(
    session: Session, *, session_id: int, actor_id: int, now: datetime | None = None
) -> dict[str, Any]:
    """归档会话（幂等：已归档再调一次不报错，也不改 `updated_at` 语义）。"""
    row = (
        session.execute(
            text("SELECT id, status FROM ent_session WHERE id = :sid"), {"sid": session_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        raise SessionNotFoundError(f"会话 {session_id} 不存在")
    if str(row["status"]) == STATUS_ARCHIVED:
        return get_session(session, session_id)
    current = now or utcnow_naive()
    session.execute(
        text(
            "UPDATE ent_session SET status = :status, revision = revision + 1, updated_at = :ts "
            "WHERE id = :sid"
        ),
        {"status": STATUS_ARCHIVED, "ts": _fmt(current), "sid": session_id},
    )
    session.commit()
    return get_session(session, session_id)


# ── Agent 执行范围 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentScope:
    """一次 Agent 作业的**执行范围**（公共机制：Agent 有执行范围）。

    作业层只拿得到这个对象：没有数据库会话、没有用户令牌。它做不了范围之外的事，
    不是因为它"被要求不要做"，而是因为范围内没有那个能力。
    """

    session_id: int | None
    specialty: str
    operator_user_id: int
    entrustment_id: int | None
    assignment_id: int | None
    org_id: int | None
    owner_user_id: int
    allowed_actions: frozenset[str] = field(default_factory=frozenset)
    allowed_artifact_types: frozenset[str] = field(default_factory=frozenset)

    def allows_action(self, action: str) -> bool:
        return action in self.allowed_actions

    def allows_artifact_type(self, artifact_type: str) -> bool:
        return artifact_type in self.allowed_artifact_types

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "specialty": self.specialty,
            "specialty_label": specialty_label(self.specialty),
            "operator_user_id": self.operator_user_id,
            "entrustment_id": self.entrustment_id,
            "assignment_id": self.assignment_id,
            "org_id": self.org_id,
            "owner_user_id": self.owner_user_id,
            "allowed_actions": sorted(self.allowed_actions),
            "allowed_artifact_types": sorted(self.allowed_artifact_types),
        }


def build_agent_scope(session_row: dict[str, Any], *, operator_user_id: int) -> AgentScope:
    """由会话行构造执行范围。

    **调用本函数之前必须已完成权限校验**（`authz.assert_can_write_entrustment`
    等）。本函数只做"声明范围"，不做授权判断 —— 把它当成授权入口会漏掉组织与作用域约束。
    """
    specialty = session_row.get("agent_specialty")
    if not specialty:
        raise SessionBindingError("该会话没有专业槽位，不能提交 Agent 作业")
    specialty = assert_specialty_open(str(specialty))
    session_id = session_row.get("session_id")
    return AgentScope(
        session_id=int(session_id) if session_id is not None else None,
        specialty=specialty,
        operator_user_id=operator_user_id,
        entrustment_id=session_row.get("entrustment_id"),
        assignment_id=session_row.get("assignment_id"),
        org_id=session_row.get("org_id"),
        owner_user_id=int(session_row["owner_user_id"]),
        allowed_actions=SPECIALTY_ACTIONS.get(specialty, frozenset()),
        allowed_artifact_types=SPECIALTY_ARTIFACT_TYPES.get(specialty, frozenset()),
    )


def dump_input(payload: dict[str, Any] | None) -> str | None:
    """作业输入落库前的序列化（紧凑 JSON，中文不转义）。"""
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "ROLE_AGENT",
    "ROLE_SYSTEM",
    "ROLE_USER",
    "SOURCE_AGENT",
    "SOURCE_DETERMINISTIC",
    "SOURCE_MANUAL",
    "SPECIALTY_ACTIONS",
    "SPECIALTY_ARTIFACT_TYPES",
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "AgentScope",
    "SessionArchivedError",
    "SessionBindingError",
    "SessionError",
    "SessionNotFoundError",
    "append_message",
    "archive_session",
    "build_agent_scope",
    "create_session",
    "dump_input",
    "get_session",
    "list_messages",
    "list_sessions",
    "next_seq",
    "utcnow_naive",
]
