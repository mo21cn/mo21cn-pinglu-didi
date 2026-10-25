"""关务路由 —— `/api/v1/customs`（D1 最小骨架）。

四个端点（比决策文档 §2.1 的"3 条"多一条，理由见下）：

- `GET    /sources`                        数据源状态（Q-02 的显式化，先于页面实现）
- `POST   /declarations`                   人工登记一条记录 + 首条事件（幂等）
- `GET    /declarations?no=`               按单号查（按归属过滤）
- `GET    /declarations/{id}`              详情 + 时间线 + 新鲜度
- `POST   /declarations/{id}/events`       追加一条状态事件（幂等）

**为什么多出第 5 个端点**：只有 3 条接口时，"同一票货的第二条状态"无处可写 ——
演示脚本第 2 步（再登记一条查验通知）就会退化成"再建一条记录"，那样时间线上永远只有
一条事件，AC-06/AC-09 都演不出来。这不叫超范围，叫把"时间线"这条能力做完整。

横切约束：
* 写命令一律带 `Idempotency-Key`（ENT-002 同口径）；
* 不可见与不存在**同一种响应**（404，不泄漏存在性）；
* 错误码区分：401 未登录 / 403 越权写 / 404 不可见 / 409 冲突 / 422 字段不合法。
  ⛔ 不允许一律 500。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.idempotency import IdempotencyError, idempotent, require_idempotency_key
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.customs import declarations as svc
from app.modules.customs import sources
from app.modules.customs.schemas import DeclarationCreate, DeclarationEventCreate
from app.modules.entrust.access import resolve_context

router = APIRouter()

DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


def _org_ids(db: Session, user: User) -> frozenset[int]:
    """当前用户所在的组织集合（只读复用委托支线的解析器）。"""
    return resolve_context(db, user_id=int(user.id)).org_ids


def _http_error(exc: svc.DeclarationError) -> HTTPException:
    if isinstance(exc, svc.DeclarationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.DeclarationForbiddenError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, svc.DeclarationConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _replay(status_code: int, body: Any) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body)


@router.get("/sources", summary="数据源状态（官方源是否可用 / 人工登记是否可用）")
def get_sources(_user: UserDep) -> dict[str, Any]:
    """数据源状态条的数据源。

    要求登录：来源清单本身不含业务数据，但它决定页面渲染哪些查询入口 ——
    未登录会话根本不该看到关务页的任何一块（与 PRD §3「入口按服务端探测渲染」同源）。
    """
    return sources.snapshot()


@router.post("/declarations", summary="人工登记一条报关记录（幂等）")
def create_declaration(
    payload: DeclarationCreate,
    db: DbDep,
    user: UserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")] = "",
) -> Any:
    # 幂等键按 (actor, scope) 隔离：别人拿到同一个键不能重放我的登记结果
    try:
        key = require_idempotency_key({"Idempotency-Key": idempotency_key})
        with idempotent(
            db,
            scope="customs:declaration:create",
            key=key,
            actor_user_id=int(user.id),
            payload=payload.model_dump(mode="json"),
        ) as guard:
            if guard.replay is not None:
                return _replay(guard.replay.status_code, guard.replay.body)
            body = svc.create_declaration(
                db,
                user_id=int(user.id),
                org_ids=_org_ids(db, user),
                declaration_no=payload.declaration_no,
                status=payload.status,
                source=payload.source,
                kind=payload.kind,
                occurred_at=payload.occurred_at,
                note=payload.note,
                org_id=payload.org_id,
                assignment_id=payload.assignment_id,
            )
            guard.succeed(201, body)
            return JSONResponse(status_code=201, content=body)
    except IdempotencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except svc.DeclarationError as exc:
        raise _http_error(exc) from exc


@router.get("/declarations", summary="按单号查询（未查到＝空表，不返回空壳成功记录）")
def list_declarations_ep(
    db: DbDep,
    user: UserDep,
    no: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    rows = svc.list_declarations(db, user_id=int(user.id), org_ids=_org_ids(db, user), no=no)
    return {"items": rows, "total": len(rows), "query": {"no": no}}


@router.get("/declarations/{declaration_id}", summary="详情 + 状态时间线 + 数据源新鲜度")
def get_declaration_ep(declaration_id: int, db: DbDep, user: UserDep) -> dict[str, Any]:
    try:
        return svc.get_detail(
            db, user_id=int(user.id), org_ids=_org_ids(db, user), declaration_id=declaration_id
        )
    except svc.DeclarationError as exc:
        raise _http_error(exc) from exc


@router.post("/declarations/{declaration_id}/events", summary="追加状态事件（幂等 · 只追加）")
def append_event_ep(
    declaration_id: int,
    payload: DeclarationEventCreate,
    db: DbDep,
    user: UserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")] = "",
) -> Any:
    try:
        key = require_idempotency_key({"Idempotency-Key": idempotency_key})
        with idempotent(
            db,
            scope=f"customs:declaration:{declaration_id}:event",
            key=key,
            actor_user_id=int(user.id),
            payload=payload.model_dump(mode="json"),
        ) as guard:
            if guard.replay is not None:
                return _replay(guard.replay.status_code, guard.replay.body)
            body = svc.append_event(
                db,
                user_id=int(user.id),
                org_ids=_org_ids(db, user),
                declaration_id=declaration_id,
                status=payload.status,
                source=payload.source,
                occurred_at=payload.occurred_at,
                note=payload.note,
                corrects_event_id=payload.corrects_event_id,
            )
            guard.succeed(200, body)
            return body
    except IdempotencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except svc.DeclarationError as exc:
        raise _http_error(exc) from exc
