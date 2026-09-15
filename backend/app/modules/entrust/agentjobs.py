"""Agent 作业表与 worker —— 持久化状态机 + 租约 + 有界重试（ENT-011）。

为什么不是内存队列
------------------
AC-15 要求"重启不丢任务、重试不重复副作用"。内存队列满足不了前半句，而
"重试不重复副作用"也不能靠队列语义解决 —— 它是**幂等键 + 显式确认**的职责。
所以这里只有一张表和一组状态转移函数，worker 是**可被任何调度器反复调用的一步**。

状态机
------
::

    queued ──claim──▶ running ──成功──▶ succeeded
                        │  ▲
                        │  └── 可重试失败且还有额度 ──┐
                        │                            │
                        └── 不可重试 / 额度用尽 ──▶ failed
    (queued|running) ──cancel──▶ cancelled

* **租约**（`lease_owner` + `lease_expires_at`）：worker 崩溃后租约到期，
  作业可被重新领取 —— 这就是"重启不丢任务"；
* **尝试次数在领取时消耗**（不是失败时才加）：崩溃循环也会被 `max_attempts` 兜住，
  否则一个必然崩溃的作业可以被无限领取；
* **失败分类决定是否重试**：超时/网络/限流/输出无法解析属于**外部抖动**，值得重试；
  鉴权失败、请求被拒、信封校验失败属于**确定性错误**，重试只会烧掉预算并把
  真正的修复时机往后拖 —— 直接失败，交回人工。

校验失败不产生业务变更
----------------------
`succeeded` 才会写 `envelope_json`。校验失败的作业**没有** `envelope_json`，
也没有任何成果行 —— 数据层面就不存在"半个事实"。这是 AC-08 的实现方式。

已知限制（如实记录）
--------------------
* 没有后台进程：`tick()` 需要由外部调度器（cron / 部署平台的定时任务）反复调用。
  本增量提供已实现的领取与执行逻辑，不假装已经有常驻 worker；
* `envelope_json` 只是**提案载体**，作业层没有任何写成果的代码路径（AC-09）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.agent.llm import LLMError
from app.modules.entrust import attachments as attachments_svc
from app.modules.entrust.agents.runner import (
    AgentRunOutcome,
    build_source_catalog,
    enforce_scope,
    run_agent,
    validate_outcome,
)
from app.modules.entrust.envelope import (
    EnvelopeValidationError,
    ValidationResult,
    project_envelope_for_operator,
)
from app.modules.entrust.sessions import AgentScope, build_agent_scope

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 120

#: 值得重试的失败分类（外部抖动）。其余一律直接失败。
RETRYABLE_KINDS: frozenset[str] = frozenset(
    {"llm_timeout", "llm_network", "llm_rate_limit", "llm_bad_response"}
)

ATTEMPT_SUCCEEDED = "succeeded"
ATTEMPT_FAILED = "failed"
#: 执行完了但结果被作废（租约已被接管者领走，见 `execute_claimed_job`）。
#: 单独一个值而不是复用 `failed`：这次尝试**不是失败**，是"跑完了但没人要" ——
#: 混进 `failed` 会让"失败分类决定是否重试"那条规则被误用（`lease_lost` 不在
#: RETRYABLE_KINDS 里，一旦被当成 failed 就可能触发不该有的重排）。
ATTEMPT_ABANDONED = "abandoned"

#: 租约被接管的失败分类。故意**不进** `RETRYABLE_KINDS`：作业已经有人接手在跑，
#: 再回队列只会制造第二个赢家（H7b「旧 worker 迟到写入」）。
ERROR_LEASE_LOST = "lease_lost"


class AgentJobError(RuntimeError):
    """作业服务基础异常。HTTP 层按语义转 4xx。"""


class AgentJobNotFoundError(AgentJobError):
    """作业不存在。HTTP 层应转 404。"""


class AgentJobStateError(AgentJobError):
    """作业当前状态不允许该操作（如已取消的作业不能重试）。HTTP 层应转 409。"""


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(_FMT)


def _text_ts(raw: Any) -> str | None:
    """时间列 → 统一文本（MySQL 取回 datetime、SQLite 取回 str，响应模型声明 str）。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


def _load_json(raw: Any) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return None


def _dump_json(payload: Any) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def is_retryable(error_kind: str | None) -> bool:
    return error_kind in RETRYABLE_KINDS


def classify_error(exc: Exception) -> str:
    """异常 → 失败分类（可读且稳定，供重试策略与前端提示共用一份口径）。"""
    if isinstance(exc, LLMError):
        return f"llm_{exc.kind}"
    if isinstance(exc, EnvelopeValidationError):
        return exc.kind
    return "internal"


def describe_error(exc: Exception) -> str:
    return str(exc)[:255]


# ── 行映射 ──────────────────────────────────────────────────────────────────

_JOB_COLS = (
    "id, session_id, entrustment_id, assignment_id, task_id, artifact_id, specialty, "
    "status, attempt_count, max_attempts, lease_owner, lease_expires_at, base_revision, "
    "input_json, envelope_json, error_kind, error_message, requires_review, created_by, "
    "started_at, finished_at, cancelled_at, created_at, updated_at"
)


def _row_to_job(row: Any) -> dict[str, Any]:
    return {
        "job_id": int(row["id"]),
        "session_id": int(row["session_id"]) if row["session_id"] is not None else None,
        "entrustment_id": (
            int(row["entrustment_id"]) if row["entrustment_id"] is not None else None
        ),
        "assignment_id": (int(row["assignment_id"]) if row["assignment_id"] is not None else None),
        "task_id": int(row["task_id"]) if row["task_id"] is not None else None,
        "artifact_id": int(row["artifact_id"]) if row["artifact_id"] is not None else None,
        "specialty": str(row["specialty"]),
        "status": str(row["status"]),
        "attempt_count": int(row["attempt_count"]),
        "max_attempts": int(row["max_attempts"]),
        "lease_owner": row["lease_owner"],
        "lease_expires_at": _text_ts(row["lease_expires_at"]),
        "base_revision": int(row["base_revision"]) if row["base_revision"] is not None else None,
        "input": _load_json(row["input_json"]),
        "envelope": _load_json(row["envelope_json"]),
        "error_kind": row["error_kind"],
        "error_message": row["error_message"],
        "requires_review": bool(row["requires_review"]),
        "created_by": int(row["created_by"]),
        "started_at": _text_ts(row["started_at"]),
        "finished_at": _text_ts(row["finished_at"]),
        "cancelled_at": _text_ts(row["cancelled_at"]),
        "created_at": _text_ts(row["created_at"]),
        "updated_at": _text_ts(row["updated_at"]),
    }


def _row_to_attempt(row: Any) -> dict[str, Any]:
    return {
        "attempt_no": int(row["attempt_no"]),
        "status": str(row["status"]),
        "error_kind": row["error_kind"],
        "error_message": row["error_message"],
        "latency_ms": int(row["latency_ms"]) if row["latency_ms"] is not None else None,
        "mocked": bool(row["mocked"]),
        "raw_output": row["raw_output"],
        "started_at": _text_ts(row["started_at"]),
        "finished_at": _text_ts(row["finished_at"]),
    }


def _get_job_row(session: Session, job_id: int) -> Any:
    return (
        session.execute(
            text(f"SELECT {_JOB_COLS} FROM ent_agent_job WHERE id = :jid"),
            {"jid": job_id},
        )
        .mappings()
        .first()
    )


def get_job(session: Session, job_id: int) -> dict[str, Any]:
    """作业详情（含尝试日志）。

    Raises:
        AgentJobNotFoundError: 作业不存在。
    """
    row = _get_job_row(session, job_id)
    if row is None:
        raise AgentJobNotFoundError(f"作业 {job_id} 不存在")
    job = _row_to_job(row)
    job["attempts"] = list_attempts(session, job_id)
    return job


def list_attempts(session: Session, job_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT attempt_no, status, error_kind, error_message, latency_ms, "
                "mocked, raw_output, started_at, finished_at "
                "FROM ent_agent_job_attempt WHERE job_id = :jid ORDER BY attempt_no"
            ),
            {"jid": job_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_attempt(r) for r in rows]


def list_jobs(
    session: Session,
    *,
    session_id: int | None = None,
    assignment_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[int, list[dict[str, Any]]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if session_id is not None:
        clauses.append("session_id = :sid")
        params["sid"] = session_id
    if assignment_id is not None:
        clauses.append("assignment_id = :aid")
        params["aid"] = assignment_id
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = int(
        session.execute(
            text(f"SELECT COUNT(*) AS c FROM ent_agent_job {where}"), params
        ).scalar_one()
    )
    rows = (
        session.execute(
            text(
                f"SELECT {_JOB_COLS} FROM ent_agent_job {where} "
                "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": size, "offset": (page - 1) * size},
        )
        .mappings()
        .all()
    )
    return total, [_row_to_job(r) for r in rows]


# ── 提交与状态转移 ──────────────────────────────────────────────────────────


def submit_job(
    session: Session,
    *,
    session_id: int | None,
    entrustment_id: int | None,
    assignment_id: int | None,
    specialty: str,
    created_by: int,
    task_id: int | None = None,
    artifact_id: int | None = None,
    base_revision: int | None = None,
    job_input: dict[str, Any] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """登记一个待执行作业（幂等由 API 层的 `Idempotency-Key` 承担）。

    **本函数不调用模型**：提交与执行分开，是为了让"提交成功但执行失败"可恢复
    （AC-15 的"重启不丢任务"）。
    """
    if max_attempts < 1:
        raise AgentJobError("max_attempts 至少为 1")
    current = now or utcnow_naive()
    ts = _fmt(current)
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_agent_job "
                "(session_id, entrustment_id, assignment_id, task_id, artifact_id, specialty, "
                " status, attempt_count, max_attempts, base_revision, input_json, "
                " requires_review, created_by, created_at, updated_at) "
                "VALUES (:sid, :eid, :aid, :tid, :artid, :spec, :status, 0, :maxa, :base, "
                " :input, 1, :by, :ts, :ts)"
            ),
            {
                "sid": session_id,
                "eid": entrustment_id,
                "aid": assignment_id,
                "tid": task_id,
                "artid": artifact_id,
                "spec": specialty,
                "status": STATUS_QUEUED,
                "maxa": max_attempts,
                "base": base_revision,
                "input": _dump_json(job_input),
                "by": created_by,
                "ts": ts,
            },
        ),
    )
    session.commit()
    return get_job(session, int(result.lastrowid or 0))


def reap_expired(session: Session, *, now: datetime | None = None) -> int:
    """把**租约过期且额度用尽**的作业判为失败，返回被判定的条数。

    额度未用尽的过期作业不在这里处理 —— 它们会被 `claim_next` 重新领取。
    """
    current = now or utcnow_naive()
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "UPDATE ent_agent_job SET status = :failed, error_kind = :kind, "
                " error_message = :msg, lease_owner = NULL, lease_expires_at = NULL, "
                " finished_at = :ts, updated_at = :ts "
                "WHERE status = :running AND lease_expires_at IS NOT NULL "
                " AND lease_expires_at < :now AND attempt_count >= max_attempts"
            ),
            {
                "failed": STATUS_FAILED,
                "kind": "lease_expired",
                "msg": "worker 租约过期且重试额度已用尽",
                "running": STATUS_RUNNING,
                "ts": _fmt(current),
                "now": _fmt(current),
            },
        ),
    )
    session.commit()
    return int(result.rowcount or 0)


def claim_next(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """领取一个可执行作业并**消耗一次尝试**（原子占用租约）。

    可领取的范围：`queued`，以及 **租约已过期** 的 `running`（worker 崩溃恢复）。
    返回 `None` 表示当前没有可执行作业。

    尝试次数在这里 +1，不是失败时才加 —— 否则"每次领取都崩溃"的作业会被无限重试。
    """
    current = now or utcnow_naive()
    reap_expired(session, now=current)
    ts = _fmt(current)
    lease_until = _fmt(current + timedelta(seconds=lease_seconds))

    candidates = (
        session.execute(
            text(
                # `status` 必须在列里：领取的乐观锁守卫要用它做期望值（H7b）。
                "SELECT id, status, attempt_count, max_attempts FROM ent_agent_job "
                "WHERE status = :queued "
                "   OR (status = :running AND lease_expires_at IS NOT NULL "
                "       AND lease_expires_at < :now) "
                "ORDER BY id"
            ),
            {"queued": STATUS_QUEUED, "running": STATUS_RUNNING, "now": ts},
        )
        .mappings()
        .all()
    )

    for row in candidates:
        job_id = int(row["id"])
        if int(row["attempt_count"]) >= int(row["max_attempts"]):
            # 理论上前面的 reaper 已处理；这里兜住"额度刚好用尽"的边界
            session.execute(
                text(
                    "UPDATE ent_agent_job SET status = :failed, error_kind = :kind, "
                    " error_message = :msg, lease_owner = NULL, lease_expires_at = NULL, "
                    " finished_at = :ts, updated_at = :ts WHERE id = :jid"
                ),
                {
                    "failed": STATUS_FAILED,
                    "kind": "attempts_exhausted",
                    "msg": "重试额度已用尽",
                    "ts": ts,
                    "jid": job_id,
                },
            )
            session.commit()
            continue

        result = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "UPDATE ent_agent_job SET status = :running, "
                    " attempt_count = attempt_count + 1, lease_owner = :worker, "
                    " lease_expires_at = :lease, started_at = COALESCE(started_at, :ts), "
                    " updated_at = :ts "
                    "WHERE id = :jid AND status = :expected_status "
                    " AND attempt_count = :expected_attempt"
                ),
                {
                    "running": STATUS_RUNNING,
                    "worker": worker_id,
                    "lease": lease_until,
                    "ts": ts,
                    "jid": job_id,
                    "expected_status": str(row["status"]),
                    "expected_attempt": int(row["attempt_count"]),
                },
            ),
        )
        session.commit()
        if int(result.rowcount or 0) == 0:
            # 读到与写入之间被别的 worker 领走了（或状态已变）：这一行不再是可领取的
            # 那一行。继续找下一个候选，而不是把它当成自己领到的。
            # 守卫条件是**乐观锁**：`status` + `attempt_count` 必须与刚才读到的完全一致，
            # 否则 UPDATE 命中 0 行 —— 无条件 `WHERE id = :jid` 会让两个 worker 同时领到
            # 同一个作业（H7b，真实 MySQL 上可复现）。
            continue
        return get_job(session, job_id)
    return None


def claim_job(
    session: Session,
    *,
    job_id: int,
    worker_id: str = "inline",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """领取**指定**作业（API 的"推进一次"用）。

    与 `claim_next` 的区别只是选谁：这里由调用方指名。语义完全一致 ——
    同样先回收过期租约、同样在领取时消耗一次尝试、同样拒绝被取消/已结束的作业。

    Returns:
        领取成功返回作业；不可领取（已被别人持有租约、已结束、额度用尽、
        不存在）返回 `None`。
    """
    current = now or utcnow_naive()
    reap_expired(session, now=current)
    ts = _fmt(current)

    row = (
        session.execute(
            text(
                "SELECT id, status, attempt_count, max_attempts, lease_expires_at "
                "FROM ent_agent_job WHERE id = :jid"
            ),
            {"jid": job_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    status = str(row["status"])
    if status not in ACTIVE_STATUSES:
        return None
    # 租约仍在有效期内：别人正在跑，不抢
    if (
        status == STATUS_RUNNING
        and row["lease_expires_at"] is not None
        and str(row["lease_expires_at"]) >= ts
    ):
        return None
    if int(row["attempt_count"]) >= int(row["max_attempts"]):
        return None

    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "UPDATE ent_agent_job SET status = :running, "
                " attempt_count = attempt_count + 1, lease_owner = :worker, "
                " lease_expires_at = :lease, started_at = COALESCE(started_at, :ts), "
                " updated_at = :ts "
                "WHERE id = :jid AND status = :expected_status "
                " AND attempt_count = :expected_attempt"
            ),
            {
                "running": STATUS_RUNNING,
                "worker": worker_id,
                "lease": _fmt(current + timedelta(seconds=lease_seconds)),
                "ts": ts,
                "jid": job_id,
                "expected_status": str(row["status"]),
                "expected_attempt": int(row["attempt_count"]),
            },
        ),
    )
    session.commit()
    if int(result.rowcount or 0) == 0:
        # 与 claim_next 同一条乐观锁守卫：这一段是「读到的那一行」才认。
        # 并发下输掉的一方**不重试**本作业（返回 None），把选择权交回调用方。
        return None
    return get_job(session, job_id)


def cancel_job(
    session: Session, *, job_id: int, actor_id: int, now: datetime | None = None
) -> dict[str, Any]:
    """取消作业（幂等）。已结束的作业不可取消 → 409。"""
    row = _get_job_row(session, job_id)
    if row is None:
        raise AgentJobNotFoundError(f"作业 {job_id} 不存在")
    status = str(row["status"])
    if status == STATUS_CANCELLED:
        return get_job(session, job_id)
    if status in (STATUS_SUCCEEDED, STATUS_FAILED):
        raise AgentJobStateError(f"作业 {job_id} 已{status}，不能取消")
    current = now or utcnow_naive()
    ts = _fmt(current)
    session.execute(
        text(
            "UPDATE ent_agent_job SET status = :cancelled, cancelled_at = :ts, "
            " lease_owner = NULL, lease_expires_at = NULL, updated_at = :ts WHERE id = :jid"
        ),
        {"cancelled": STATUS_CANCELLED, "ts": ts, "jid": job_id},
    )
    session.commit()
    return get_job(session, job_id)


def retry_job(
    session: Session, *, job_id: int, actor_id: int, now: datetime | None = None
) -> dict[str, Any]:
    """把**失败**的作业放回队列（显式重试，不自动）。

    只有 `failed` 可重试：

    * `queued` / `running` → 409（还在跑，不需要重试）；
    * `succeeded` → 409（重试会重复副作用；要再跑一次就新建作业）；
    * `cancelled` → 409（取消是人的终局决定，"重试"会把取消悄悄撤销掉）。

    重置尝试计数是有意的：显式重试意味着"人判断这次值得再试一次"，
    与"worker 自动重试"是两件事 —— 后者受 `max_attempts` 约束。
    """
    row = _get_job_row(session, job_id)
    if row is None:
        raise AgentJobNotFoundError(f"作业 {job_id} 不存在")
    status = str(row["status"])
    if status in ACTIVE_STATUSES:
        raise AgentJobStateError(f"作业 {job_id} 仍在{status}，无需重试")
    if status == STATUS_SUCCEEDED:
        raise AgentJobStateError(f"作业 {job_id} 已成功，重试会重复副作用；请新建作业")
    if status == STATUS_CANCELLED:
        raise AgentJobStateError(f"作业 {job_id} 已取消，不能重试；如仍需执行请新建作业")
    current = now or utcnow_naive()
    ts = _fmt(current)
    session.execute(
        text(
            "UPDATE ent_agent_job SET status = :queued, attempt_count = 0, "
            " error_kind = NULL, error_message = NULL, finished_at = NULL, "
            " cancelled_at = NULL, lease_owner = NULL, lease_expires_at = NULL, "
            " updated_at = :ts WHERE id = :jid"
        ),
        {"queued": STATUS_QUEUED, "ts": ts, "jid": job_id},
    )
    session.commit()
    return get_job(session, job_id)


# ── 只读上下文与执行 ────────────────────────────────────────────────────────


def collect_context(session: Session, job: dict[str, Any]) -> dict[str, Any]:
    """为作业组装**只读**业务事实快照（Agent 看不到库，只能看这个字典）。"""
    context: dict[str, Any] = {
        "entrustment_id": job.get("entrustment_id"),
        "assignment": None,
        "tasks": [],
        "artifacts": [],
        "attachments": [],
    }
    assignment_id = job.get("assignment_id")
    if assignment_id is not None:
        row = (
            session.execute(
                text(
                    "SELECT id, owner_user_id, org_id, title, cargo_summary, quantity, "
                    "quantity_unit, status, revision FROM ent_assignment WHERE id = :aid"
                ),
                {"aid": assignment_id},
            )
            .mappings()
            .first()
        )
        if row is not None:
            context["assignment"] = {
                "assignment_id": int(row["id"]),
                "owner_user_id": int(row["owner_user_id"]),
                "org_id": int(row["org_id"]) if row["org_id"] is not None else None,
                "title": str(row["title"]),
                "cargo_summary": row["cargo_summary"],
                "quantity": row["quantity"],
                "quantity_unit": row["quantity_unit"],
                "status": str(row["status"]),
                "revision": int(row["revision"]),
            }
        context["tasks"] = [
            {
                "task_id": int(r["id"]),
                "task_type": str(r["task_type"]),
                "title": str(r["title"]),
                "status": str(r["status"]),
                "assignee_user_id": (
                    int(r["assignee_user_id"]) if r["assignee_user_id"] is not None else None
                ),
            }
            for r in session.execute(
                text(
                    "SELECT id, task_type, title, status, assignee_user_id "
                    "FROM ent_workflow_task WHERE assignment_id = :aid ORDER BY id"
                ),
                {"aid": assignment_id},
            ).mappings()
        ]

    entrustment_id = job.get("entrustment_id")
    if entrustment_id is not None:
        context["artifacts"] = [
            {
                "artifact_id": int(r["id"]),
                "artifact_type": str(r["artifact_type"]),
                "status": str(r["status"]),
                "current_revision_id": (
                    int(r["current_revision_id"]) if r["current_revision_id"] is not None else None
                ),
                "revision_no": int(r["revision_no"]) if r["revision_no"] is not None else None,
            }
            for r in session.execute(
                text(
                    "SELECT a.id, a.artifact_type, a.status, a.current_revision_id, "
                    "       r.revision_no "
                    "FROM ent_artifact a "
                    "LEFT JOIN ent_artifact_revision r ON r.id = a.current_revision_id "
                    "WHERE a.entrustment_id = :eid ORDER BY a.id"
                ),
                {"eid": entrustment_id},
            ).mappings()
        ]
        context["attachments"] = [
            {
                "attachment_id": int(r["id"]),
                "filename": str(r["filename"]),
                "content_type": str(r["content_type"]),
                "extract_status": str(r["extract_status"]),
            }
            for r in session.execute(
                text(
                    "SELECT id, filename, content_type, extract_status FROM ent_attachment "
                    "WHERE entrustment_id = :eid ORDER BY id"
                ),
                {"eid": entrustment_id},
            ).mappings()
        ]
    elif assignment_id is not None:
        # 未挂授权的私有附件（客户上传但还没提交）也能被引用为该作业的来源
        context["attachments"] = [
            {
                "attachment_id": int(r["id"]),
                "filename": str(r["filename"]),
                "content_type": str(r["content_type"]),
                "extract_status": str(r["extract_status"]),
            }
            for r in session.execute(
                text(
                    "SELECT id, filename, content_type, extract_status FROM ent_attachment "
                    "WHERE assignment_id = :aid AND entrustment_id IS NULL ORDER BY id"
                ),
                {"aid": assignment_id},
            ).mappings()
        ]

    _attach_text_excerpts(session, context["attachments"])
    return context


def _attach_text_excerpts(session: Session, attachments: list[dict[str, Any]]) -> None:
    """给附件补上**已提取的文本**（ENT-013），供 Agent 直接读文件内容。

    只有 `extract_status == 'done'` 的附件才会有文本 —— 没提取过的附件对 Agent
    而言只是"一个文件名"，这既让来源目录不虚（`attachment_text` 只在真有文本时
    才出现在目录里），也避免把一堆空字符串塞进提示词。

    截断用 `AGENT_ATTACHMENT_TEXT_CHARS`（比落库上限更严）：提示词还要留给任务、
    成果与来源目录，不能让一段报价文本独占。被截断的事实随
    `text_truncated` 一起进上下文，让模型知道"这不是全文"。
    """
    if not attachments:
        return
    limit = int(get_settings().AGENT_ATTACHMENT_TEXT_CHARS)
    texts = attachments_svc.get_texts(
        session, [int(item["attachment_id"]) for item in attachments], limit_chars=limit
    )
    for item in attachments:
        row = texts.get(int(item["attachment_id"]))
        item["text_excerpt"] = row["content"] if row is not None else None
        item["text_truncated"] = bool(row["truncated"]) if row is not None else False
        item["text_source"] = row["source"] if row is not None else None


def scope_for_job(session: Session, job: dict[str, Any], *, operator_user_id: int) -> AgentScope:
    """由作业字段构造执行范围。

    会话内作业沿用会话的绑定；无会话作业直接用作业自带的 `org_id` / 货主。
    """
    if job.get("session_id") is not None:
        from app.modules.entrust.sessions import get_session

        session_row = get_session(session, int(job["session_id"]))
        scope = build_agent_scope(session_row, operator_user_id=operator_user_id)
        # 作业自带的更精确的上下文优先（会话可能只绑到委托单，作业绑到具体任务）
        return AgentScope(
            session_id=scope.session_id,
            specialty=scope.specialty,
            operator_user_id=operator_user_id,
            entrustment_id=job.get("entrustment_id") or scope.entrustment_id,
            assignment_id=job.get("assignment_id") or scope.assignment_id,
            org_id=scope.org_id,
            owner_user_id=scope.owner_user_id,
            allowed_actions=scope.allowed_actions,
            allowed_artifact_types=scope.allowed_artifact_types,
        )

    context = collect_context(session, job)
    assignment = context.get("assignment") or {}
    return build_agent_scope(
        {
            "session_id": None,
            "agent_specialty": job["specialty"],
            "owner_user_id": job.get("owner_user_id") or assignment.get("owner_user_id") or 0,
            "entrustment_id": job.get("entrustment_id"),
            "assignment_id": job.get("assignment_id"),
            "org_id": job.get("org_id") or assignment.get("org_id"),
        },
        operator_user_id=operator_user_id,
    )


def _record_attempt(
    session: Session,
    *,
    job_id: int,
    attempt_no: int,
    status: str,
    started: datetime,
    finished: datetime,
    error_kind: str | None = None,
    error_message: str | None = None,
    latency_ms: int | None = None,
    mocked: bool = False,
    raw_output: str | None = None,
) -> None:
    session.execute(
        text(
            "INSERT INTO ent_agent_job_attempt "
            "(job_id, attempt_no, status, error_kind, error_message, latency_ms, mocked, "
            " raw_output, started_at, finished_at) "
            "VALUES (:jid, :no, :status, :kind, :msg, :lat, :mocked, :raw, :started, :finished)"
        ),
        {
            "jid": job_id,
            "no": attempt_no,
            "status": status,
            "kind": error_kind,
            "msg": error_message,
            "lat": latency_ms,
            "mocked": 1 if mocked else 0,
            "raw": (raw_output or "")[:20000] or None,
            "started": _fmt(started),
            "finished": _fmt(finished),
        },
    )


async def execute_claimed_job(
    session: Session,
    *,
    job_id: int,
    scope: AgentScope,
    worker_id: str | None = None,
    attempt_no: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """执行一个**已被领取**的作业（调用方已完成 `claim_next` / `claim_job`）。

    成功 → `succeeded` + `envelope_json`；失败 → 按分类决定回队列还是 `failed`。
    **任何分支都不写成果**（AC-09）。

    租约归属守卫（H7b「旧 worker 迟到写入」）
    ----------------------------------------
    终局写入带 `AND lease_owner = <本次领取者>`：租约若已被接管者领走，条件不成立、
    更新 0 行，本次执行的**结果整份作废** —— 迟到的旧 worker 不能把新持有者的作业
    改回自己的结局。作废不是静默丢弃：尝试日志留一行 `abandoned` / `lease_lost`，
    让"跑过但没生效"这件事可追溯。返回的作业字典带 `lease_lost = True`，
    调用方可据此区分「我做完了」与「我白做了」。

    `worker_id` 省略时退化为「以进入本函数时读到的 `lease_owner` 为准」，
    与既有调用点（先 `claim_*` 再立即 `execute_claimed_job`）语义一致。

    `attempt_no` 同理应由领取方传入（它知道自己领的是第几次尝试）。省略时按
    库里的 `attempt_count` 取 —— 正常路径两者相同；只有**租约被接管的旧 worker**
    才会不一致，而它恰好需要的是自己那一次，不是接管者的那一次。
    """
    started = now or utcnow_naive()
    job = get_job(session, job_id)
    attempt_no = int(attempt_no) if attempt_no is not None else int(job["attempt_count"])
    lease_owner = job.get("lease_owner")
    # 没在租约下的作业（未领取却被直接执行）不加守卫 —— 保持既有行为，
    # 也不该在这里悄悄放宽"必须先领取"的契约。
    guard_owner = worker_id if worker_id is not None else lease_owner
    lease_guard = " AND lease_owner = :owner" if guard_owner is not None else ""
    context = collect_context(session, job)
    job_input = job.get("input") or {}
    known_refs = build_source_catalog(context, job_input)
    ts = _fmt(started)

    error: Exception | None = None
    outcome: AgentRunOutcome | None = None
    validation: ValidationResult | None = None
    scope_report: dict[str, Any] = {"dropped_actions": []}
    try:
        outcome = await run_agent(scope=scope, context=context, job_input=job_input)
        raw = dict(outcome.raw)
        # 业务层回填：模型无权决定"这是哪张委托的哪个版本"
        raw["assignment_id"] = job.get("assignment_id")
        raw["task_id"] = job.get("task_id")
        raw["base_revision"] = job.get("base_revision") or 0
        outcome.raw = raw
        validation = validate_outcome(outcome, known_source_refs=known_refs)
        scope_report = enforce_scope(validation, scope)
    except Exception as exc:  # noqa: BLE001 —— 分类处理，不静默吞
        error = exc

    finished = utcnow_naive()
    if error is None and outcome is not None and validation is not None:
        envelope = project_envelope_for_operator(validation)
        envelope["scope"] = {**scope.to_dict(), **scope_report}
        result = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "UPDATE ent_agent_job SET status = :ok, envelope_json = :env, "
                    " error_kind = NULL, error_message = NULL, requires_review = 1, "
                    " lease_owner = NULL, lease_expires_at = NULL, finished_at = :ts, "
                    f" updated_at = :ts WHERE id = :jid{lease_guard}"
                ),
                {
                    "ok": STATUS_SUCCEEDED,
                    "env": _dump_json(envelope),
                    "ts": ts,
                    "jid": job_id,
                    "owner": guard_owner,
                },
            ),
        )
        if int(result.rowcount or 0) == 0:
            return _abandon_lost_lease(
                session,
                job_id=job_id,
                attempt_no=attempt_no,
                started=started,
                outcome=outcome,
            )
        _record_attempt(
            session,
            job_id=job_id,
            attempt_no=attempt_no,
            status=ATTEMPT_SUCCEEDED,
            started=started,
            finished=finished,
            latency_ms=outcome.latency_ms,
            mocked=outcome.mocked,
            raw_output=outcome.raw_text,
        )
        session.commit()
        return get_job(session, job_id)

    assert error is not None  # 走到这里必定是失败分支
    kind = classify_error(error)
    message = describe_error(error)
    requeue = is_retryable(kind) and attempt_no < int(job["max_attempts"])
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                (
                    "UPDATE ent_agent_job SET status = :queued, error_kind = :kind, "
                    " error_message = :msg, lease_owner = NULL, lease_expires_at = NULL, "
                    f" updated_at = :ts WHERE id = :jid{lease_guard}"
                )
                if requeue
                else (
                    "UPDATE ent_agent_job SET status = :failed, error_kind = :kind, "
                    " error_message = :msg, lease_owner = NULL, lease_expires_at = NULL, "
                    f" finished_at = :ts, updated_at = :ts WHERE id = :jid{lease_guard}"
                )
            ),
            {
                "queued": STATUS_QUEUED,
                "failed": STATUS_FAILED,
                "kind": kind,
                "msg": message,
                "ts": ts,
                "jid": job_id,
                "owner": guard_owner,
            },
        ),
    )
    if int(result.rowcount or 0) == 0:
        return _abandon_lost_lease(
            session,
            job_id=job_id,
            attempt_no=attempt_no,
            started=started,
            outcome=outcome,
            error_kind=kind,
            error_message=message,
        )
    _record_attempt(
        session,
        job_id=job_id,
        attempt_no=attempt_no,
        status=ATTEMPT_FAILED,
        started=started,
        finished=finished,
        error_kind=kind,
        error_message=message,
        latency_ms=getattr(outcome, "latency_ms", None) if outcome else None,
        mocked=bool(getattr(outcome, "mocked", False)) if outcome else False,
        raw_output=getattr(outcome, "raw_text", None) if outcome else None,
    )
    session.commit()
    return get_job(session, job_id)


def _abandon_lost_lease(
    session: Session,
    *,
    job_id: int,
    attempt_no: int,
    started: datetime,
    outcome: Any = None,
    error_kind: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """租约已被接管：本次执行**不产生任何终局**，只留一行可追溯的 `abandoned`。

    为什么还要记一行：静默丢弃会让"跑了但没生效"这件事在库里完全不存在，
    事后查「这个作业为什么没结果」时无从下手。记 `abandoned` 而不是
    `failed`，是因为这次尝试并没有失败 —— 它只是不再被需要。
    """
    finished = utcnow_naive()
    _record_attempt(
        session,
        job_id=job_id,
        attempt_no=attempt_no,
        status=ATTEMPT_ABANDONED,
        started=started,
        finished=finished,
        error_kind=ERROR_LEASE_LOST,
        error_message="租约已被其他 worker 接管，本次执行结果作废",
        latency_ms=getattr(outcome, "latency_ms", None) if outcome else None,
        mocked=bool(getattr(outcome, "mocked", False)) if outcome else False,
        raw_output=getattr(outcome, "raw_text", None) if outcome else None,
    )
    session.commit()
    abandoned = get_job(session, job_id)
    abandoned["lease_lost"] = True
    abandoned["discarded_error_kind"] = error_kind
    abandoned["discarded_error_message"] = error_message
    return abandoned


async def tick(
    session: Session,
    *,
    worker_id: str = "inline",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """worker 单步：领取一个作业并执行。返回被处理的作业（无作业则为 `None`）。

    这是给外部调度器调的入口。R1 没有常驻进程 —— 部署时由 cron 或平台定时任务
    周期性调用即可（见模块 docstring 的"已知限制"）。
    """
    claimed = claim_next(session, worker_id=worker_id, lease_seconds=lease_seconds, now=now)
    if claimed is None:
        return None
    scope = scope_for_job(session, claimed, operator_user_id=int(claimed["created_by"]))
    return await execute_claimed_job(
        session,
        job_id=int(claimed["job_id"]),
        scope=scope,
        worker_id=worker_id,
        attempt_no=int(claimed["attempt_count"]),
    )


__all__ = [
    "ACTIVE_STATUSES",
    "ATTEMPT_ABANDONED",
    "ATTEMPT_FAILED",
    "ATTEMPT_SUCCEEDED",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "ERROR_LEASE_LOST",
    "RETRYABLE_KINDS",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "AgentJobError",
    "AgentJobNotFoundError",
    "AgentJobStateError",
    "cancel_job",
    "claim_job",
    "claim_next",
    "classify_error",
    "collect_context",
    "describe_error",
    "execute_claimed_job",
    "get_job",
    "is_retryable",
    "list_attempts",
    "list_jobs",
    "reap_expired",
    "retry_job",
    "scope_for_job",
    "submit_job",
    "tick",
    "utcnow_naive",
]
