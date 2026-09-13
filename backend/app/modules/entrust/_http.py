"""委托支线 API 共享助手：特性开关、幂等包裹、异常→HTTP 映射。

供 `router.py`（受理链路）与 `artifacts_api.py`（成果版本）共用，
保证以下口径全支线一致、不漂移：

* `ENTRUST_ENABLED=false` → 整组端点 404（隐藏入口；开关 ≠ 访问控制）；
* 写操作必须带 `Idempotency-Key`（缺键 400 / 同键同体重放 / 同键异体 409 /
  失败释放可重试）；
* 服务层异常 → HTTP 语义集中映射（404 不区分"不存在"与"无权"、409 冲突、
  403 权限、400 其余业务错误）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.idempotency import IdempotencyError, idempotent, require_idempotency_key
from app.modules.entrust.access import AccessDeniedError


def require_entrust_enabled() -> None:
    """特性开关依赖：关闭时端点 404（AC-22；开关不是访问控制）。"""
    if not get_settings().ENTRUST_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


def guard_or_400(key: str | None) -> str:
    """幂等键缺失 → 400。"""
    try:
        return require_idempotency_key({"Idempotency-Key": key or ""})
    except Exception as exc:  # MissingIdempotencyKeyError
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def replay(replay_status: int, replay_body: Any) -> JSONResponse:
    """幂等命中：原样重放历史成功响应（含状态码）。"""
    return JSONResponse(status_code=replay_status, content=replay_body)


def map_access_denied(exc: Exception) -> HTTPException | None:
    """叠加层权限异常 → 403；非该类异常返回 None（由调用方继续分发）。"""
    if isinstance(exc, AccessDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    return None


def run_write(
    db: Session,
    *,
    scope: str,
    key: str,
    actor_user_id: int,
    payload: Any,
    business: Callable[[], dict[str, Any]],
    map_domain_error: Callable[[Exception], HTTPException | None],
) -> Any:
    """幂等包裹 + 统一异常映射（所有写端点共用）。

    * 同键同体重放历史成功响应（含状态码）；
    * 同键异体 / 上一次仍在处理中 → 409（`IdempotencyError` 会从上下文管理器的
      `__enter__` 抛出，必须在本层接住，否则变成 500）；
    * 业务异常交给 `map_domain_error`；映射不了的原样抛出（不吞真实 bug）；
    * 业务失败时幂等键被上下文管理器释放，客户端可同键重试。
    """
    try:
        with idempotent(
            db, scope=scope, key=key, actor_user_id=actor_user_id, payload=payload
        ) as guard:
            if guard.replay is not None:
                return replay(guard.replay.status_code, guard.replay.body)
            body = business()
            guard.succeed(200, body)
            return body
    except IdempotencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        mapped = map_domain_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


__all__ = [
    "guard_or_400",
    "map_access_denied",
    "require_entrust_enabled",
    "replay",
    "run_write",
]
