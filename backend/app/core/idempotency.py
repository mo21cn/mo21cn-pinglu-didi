"""幂等写操作公共机制（委托支线六条公共机制之二，见 AGENTS.md 3.2）。

解决的问题
----------
网络重试、用户连点、消息重复投递，都会让同一个写请求多次到达服务端。没有幂等保护时，
"发布对客报价"执行一次和三次的结果完全不同：多出两条版本记录、两次对外通知。
本模块把这件事收敛到一处 —— **同一个 (scope, idempotency_key) 只允许产生一次副作用**。

设计约定
--------
1. **键的作用域**：`scope` + `idempotency_key` 唯一。scope 由调用方给定，建议
   `域:对象:动作`（如 `entrust:quote:publish`）；不同 scope 下的相同键互不干扰。
2. **请求指纹**：同一键但请求体不同 = 客户端用错了键，属于**冲突**（不是重放）。
3. **成功才重放**：只有 `succeeded` 的记录会重放快照；`in_progress` 表示上一次请求
   还在处理中（并发）；失败的记录会被删除，允许立即重试。
4. **独立提交**：幂等记录在自己的事务里提交，**不跟随业务事务回滚** —— 否则并发请求
   看不到"进行中"的记录，保护形同虚设。
5. **时间**：所有时间字段是 **UTC 朴素时间**（无时区后缀），格式 `%Y-%m-%d %H:%M:%S`，
   与 `ent_idempotency.created_at` 的既有约定一致。
6. **快照格式**：`response_snapshot` 存 JSON 文本 `{"status": <int>, "body": <any>}`，
   以便重放时连 HTTP 状态码一起还原（表结构无独立状态码列，不为此改已发布的迁移）。

未覆盖（明确记录，不假装已做）
------------------------------
* **MySQL 上的并发正确性未在 CI 真实执行验证**（DR-0002 未决）。实现依赖
  `UNIQUE(scope, idempotency_key)` 约束 + `IntegrityError` 兜底，SQLite 上已有单测。
* 过期清理只提供 `purge_expired()`；调度要等作业框架就绪（仓库当前无后台 worker）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

DEFAULT_TTL_SECONDS = 24 * 60 * 60
"""幂等记录默认有效期：24 小时。过期只影响"重放窗口"，不影响业务数据。"""

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


class IdempotencyError(RuntimeError):
    """幂等保护的基类异常。调用方应转成 409（冲突）返回给客户端。"""


class IdempotencyConflictError(IdempotencyError):
    """同一个幂等键被用于不同的请求体 —— 客户端用错键。"""


class IdempotencyInProgressError(IdempotencyError):
    """同一幂等键的上一次请求仍在处理中 —— 客户端应稍后用同一键重试。"""


class MissingIdempotencyKeyError(IdempotencyError):
    """写操作未提供 `Idempotency-Key` 请求头。"""


@dataclass(frozen=True)
class Replay:
    """一次成功请求的响应快照，用于原样重放。"""

    status_code: int
    body: Any

    @classmethod
    def from_snapshot(cls, snapshot: str | None) -> Replay:
        """从 `response_snapshot` 还原。

        兼容纯文本快照（早期或人工写入）：此时状态码按 200 处理，body 为原字符串。
        """
        if snapshot is None:
            return cls(status_code=200, body=None)
        try:
            data = json.loads(snapshot)
        except (TypeError, ValueError):
            return cls(status_code=200, body=snapshot)
        if isinstance(data, dict) and "status" in data and "body" in data:
            return cls(status_code=int(data["status"]), body=data["body"])
        return cls(status_code=200, body=data)

    def to_snapshot(self) -> str:
        return json.dumps(
            {"status": self.status_code, "body": self.body}, ensure_ascii=False, default=str
        )


def utcnow_naive() -> datetime:
    """当前 UTC 时间（朴素，无时区后缀）—— 与表中时间字段的存储格式一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


def fingerprint_of(payload: Any) -> str:
    """计算请求体指纹：规范化 JSON 的 sha256。

    规范化规则：键排序、紧凑分隔、非 JSON 原生类型退化为 `str`。
    这样"字段顺序不同但内容相同"的请求会被判定为同一个操作。
    """
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_idempotency_key(headers: Mapping[str, str]) -> str:
    """从请求头取幂等键；缺失或空白即报错。

    写操作**必须**带 `Idempotency-Key`。静默放行等于把"重复提交"的风险转嫁给业务方。
    """
    key = headers.get("Idempotency-Key") or headers.get("idempotency-key")
    if key is None or not key.strip():
        raise MissingIdempotencyKeyError("写操作必须提供 Idempotency-Key 请求头")
    return key.strip()


def begin(
    session: Session,
    *,
    scope: str,
    key: str,
    actor_user_id: int,
    fingerprint: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> Replay | None:
    """登记一次写操作。

    Returns:
        `None` —— 这是一次新操作，调用方继续执行业务，完成后必须调用 `succeed()`；
        `Replay` —— 该键此前已成功，直接重放快照，调用方**不要**再执行业务。

    Raises:
        IdempotencyConflict: 同键不同指纹。
        IdempotencyInProgress: 同键且仍在处理中。
    """
    current = now or utcnow_naive()
    expires_at = current + timedelta(seconds=ttl_seconds)

    try:
        session.execute(
            text(
                "INSERT INTO ent_idempotency "
                "(scope, idempotency_key, actor_user_id, request_fingerprint, status, "
                " response_snapshot, created_at, expires_at) "
                "VALUES (:scope, :key, :actor, :fingerprint, :status, NULL, :created_at, :expires_at)"
            ),
            {
                "scope": scope,
                "key": key,
                "actor": actor_user_id,
                "fingerprint": fingerprint,
                "status": STATUS_IN_PROGRESS,
                "created_at": current.strftime(_TS_FORMAT),
                "expires_at": expires_at.strftime(_TS_FORMAT),
            },
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        return _replay_existing(session, scope=scope, key=key, fingerprint=fingerprint)

    return None


def _is_expired(expires_at: Any, *, now: datetime | None = None) -> bool:
    """过期判定。未设置过期时间（`NULL`）视为永不过期。"""
    if expires_at is None:
        return False
    current = now or utcnow_naive()
    raw = str(expires_at).replace("T", " ").rstrip("Z")
    try:
        deadline = datetime.strptime(raw[:19], _TS_FORMAT)
    except ValueError:
        return False
    return current > deadline


def _replay_existing(session: Session, *, scope: str, key: str, fingerprint: str) -> Replay | None:
    """唯一冲突后读取已有记录并决定：重放 / 冲突 / 进行中 / 允许重试。"""
    row = (
        session.execute(
            text(
                "SELECT request_fingerprint, status, response_snapshot, expires_at "
                "FROM ent_idempotency WHERE scope = :scope AND idempotency_key = :key"
            ),
            {"scope": scope, "key": key},
        )
        .mappings()
        .first()
    )
    if row is None:
        # 唯一冲突但读不到行：并发删除的极小概率窗口。保守处理为"进行中"，
        # 让客户端稍后重试同一键，而不是放行一次可能重复的写。
        raise IdempotencyInProgressError(f"幂等键 {scope}/{key} 正在处理中，请稍后重试")

    if _is_expired(row["expires_at"]):
        # 快照已过保质期：不再重放，释放该键让本次当作新操作执行。
        # 过期只影响重放窗口，已产生的业务数据不受影响。
        session.execute(
            text("DELETE FROM ent_idempotency WHERE scope = :scope AND idempotency_key = :key"),
            {"scope": scope, "key": key},
        )
        session.commit()
        return None

    if str(row["request_fingerprint"]) != fingerprint:
        raise IdempotencyConflictError(
            f"幂等键 {scope}/{key} 已用于另一个请求体，请更换 Idempotency-Key 后重试"
        )

    status = str(row["status"])
    if status == STATUS_SUCCEEDED:
        return Replay.from_snapshot(row["response_snapshot"])
    if status == STATUS_IN_PROGRESS:
        raise IdempotencyInProgressError(f"幂等键 {scope}/{key} 正在处理中，请稍后重试")

    # failed：上一次执行已失败，删除记录让客户端可以立即用同一键重试
    session.execute(
        text("DELETE FROM ent_idempotency WHERE scope = :scope AND idempotency_key = :key"),
        {"scope": scope, "key": key},
    )
    session.commit()
    return None


def succeed(
    session: Session,
    *,
    scope: str,
    key: str,
    status_code: int = 200,
    body: Any = None,
) -> None:
    """标记该键对应的操作已成功，并保存响应快照供重放。"""
    snapshot = Replay(status_code=status_code, body=body).to_snapshot()
    session.execute(
        text(
            "UPDATE ent_idempotency SET status = :status, response_snapshot = :snapshot "
            "WHERE scope = :scope AND idempotency_key = :key"
        ),
        {
            "status": STATUS_SUCCEEDED,
            "snapshot": snapshot,
            "scope": scope,
            "key": key,
        },
    )
    session.commit()


def fail(session: Session, *, scope: str, key: str) -> None:
    """标记该键对应的操作失败：删除记录，允许客户端用同一键重试。

    只删"未成功"的记录 —— 已成功的重放快照必须保留到过期，否则重试会真的再执行一次。
    """
    session.execute(
        text(
            "DELETE FROM ent_idempotency "
            "WHERE scope = :scope AND idempotency_key = :key AND status != :done"
        ),
        {"scope": scope, "key": key, "done": STATUS_SUCCEEDED},
    )
    session.commit()


def purge_expired(session: Session, *, now: datetime | None = None) -> int:
    """清理已过期的幂等记录，返回删除行数。

    只删过期行；未过期的成功快照是重放窗口的保证，不能动。
    """
    current = (now or utcnow_naive()).strftime(_TS_FORMAT)
    result = cast(
        "CursorResult[Any]",
        session.execute(
            text("DELETE FROM ent_idempotency WHERE expires_at IS NOT NULL AND expires_at < :now"),
            {"now": current},
        ),
    )
    session.commit()
    return int(result.rowcount or 0)


@dataclass
class IdempotencyGuard:
    """`idempotent()` 上下文管理器的状态载体。

    用法::

        with idempotent(db, scope="entrust:quote:publish", key=key,
                        actor_user_id=user.id, payload=body) as guard:
            if guard.replay is not None:
                return JSONResponse(guard.replay.status_code, guard.replay.body)
            result = publish_quote(...)
            guard.succeed(200, result)
            return result
    """

    session: Session
    scope: str
    key: str
    actor_user_id: int
    fingerprint: str
    replay: Replay | None = None
    _completed: bool = field(default=False, init=False)

    def succeed(self, status_code: int = 200, body: Any = None) -> None:
        """业务成功：写入快照。未调用则在退出时视为失败并释放该键。"""
        succeed(
            self.session,
            scope=self.scope,
            key=self.key,
            status_code=status_code,
            body=body,
        )
        self._completed = True

    def release(self) -> None:
        """业务失败：释放该键，允许客户端用同一键重试。"""
        fail(self.session, scope=self.scope, key=self.key)
        self._completed = True


@contextmanager
def idempotent(
    session: Session,
    *,
    scope: str,
    key: str,
    actor_user_id: int,
    payload: Any,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Iterator[IdempotencyGuard]:
    """包住一段写业务逻辑，自动处理重放与失败释放。

    退出语义：
      * 命中历史成功记录 → `guard.replay` 非空，业务代码必须直接返回它；
      * 业务抛异常 / 未调用 `succeed()` → 删除记录，客户端可用同一键重试；
      * 调用 `succeed()` → 保存快照，后续同键请求直接重放。
    """
    guard = IdempotencyGuard(
        session=session,
        scope=scope,
        key=key,
        actor_user_id=actor_user_id,
        fingerprint=fingerprint_of(payload),
    )
    guard.replay = begin(
        session,
        scope=scope,
        key=key,
        actor_user_id=actor_user_id,
        fingerprint=guard.fingerprint,
        ttl_seconds=ttl_seconds,
    )
    try:
        yield guard
    except Exception:
        # 业务异常：释放键，让客户端可以重试（不吞异常，继续向外抛）
        if guard.replay is None:
            guard.release()
        raise
    else:
        if guard.replay is None and not guard._completed:
            guard.release()
