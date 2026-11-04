"""关务路由 —— `/api/v1/customs`（D1 骨架 + D2 归类）。

D1（人工登记 → 状态时间线）五个端点（比决策文档 §2.1 的"3 条"多两条，理由见下）：

- `GET    /sources`                        数据源状态（Q-02 的显式化，先于页面实现）
- `POST   /declarations`                   人工登记一条记录 + 首条事件（幂等）
- `GET    /declarations?no=`               按单号查（按归属过滤）
- `GET    /declarations/{id}`              详情 + 时间线 + 新鲜度
- `POST   /declarations/{id}/events`       追加一条状态事件（幂等）

D2（商品归类辅助）四个端点：

- `POST   /classifications`                发起归类建议（幂等）
- `GET    /classifications`                列表（可按报关记录 / 状态过滤）
- `GET    /classifications/{id}`           详情：候选 + 降级原因 + 人工通路
- `POST   /classifications/{id}/manual`    人工填写并确认结论（幂等 · 不碰模型）

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
from app.modules.customs import classifications as cls
from app.modules.customs import declarations as svc
from app.modules.customs import sources
from app.modules.customs.schemas import (
    ClassificationCreate,
    ClassificationManualCreate,
    DeclarationCreate,
    DeclarationEventCreate,
)
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


def _cls_http_error(exc: cls.ClassificationError) -> HTTPException:
    """归类异常 → HTTP。

    ⚠️ 降级**不是** HTTP 错误：模型不可用时分类接口仍然 201，响应体里
    `status="manual_required"` + `degraded.reason` 说清原因，`manual_entry.available=true`
    给出出口。做成 5xx 会逼前端写"失败页"，而失败页里没有手工填写入口 ——
    那正是本次要消灭的形态。
    """
    if isinstance(exc, cls.ClassificationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, cls.ClassificationForbiddenError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


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


# ---------------------------------------------------------------------------
# D2：商品归类辅助（模型建议 + 人工定稿）
#
# 端点为什么是四条：`POST /classifications`（发起）+ `GET /classifications`（列表）
# + `GET /classifications/{id}`（详情）+ `POST .../manual`（人工定稿）。
# 前三条对应页面要渲染的三块（发起 / 历史 / 详情），第四条是**降级时的唯一出口** ——
# 少了它，"模型挂了怎么办"在接口层就没有答案，页面只能显示一句"服务不可用"。
# ---------------------------------------------------------------------------


@router.post(
    "/classifications",
    status_code=201,
    summary="发起归类建议（模型不可用时降级为 manual_required，而不是 5xx）",
)
async def create_classification_ep(
    payload: ClassificationCreate,
    db: DbDep,
    user: UserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")] = "",
) -> Any:
    try:
        key = require_idempotency_key({"Idempotency-Key": idempotency_key})
        with idempotent(
            db,
            scope="customs:classification:create",
            key=key,
            actor_user_id=int(user.id),
            payload=payload.model_dump(mode="json"),
        ) as guard:
            if guard.replay is not None:
                return _replay(guard.replay.status_code, guard.replay.body)
            body = await cls.propose(
                db,
                user_id=int(user.id),
                org_ids=_org_ids(db, user),
                product_name=payload.product_name,
                product_desc=payload.product_desc,
                declaration_id=payload.declaration_id,
                org_id=payload.org_id,
            )
            guard.succeed(201, body)
            return JSONResponse(status_code=201, content=body)
    except IdempotencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except cls.ClassificationError as exc:
        raise _cls_http_error(exc) from exc


@router.get("/classifications", summary="归类记录列表（可按报关记录 / 状态过滤）")
def list_classifications_ep(
    db: DbDep,
    user: UserDep,
    declaration_id: Annotated[int | None, Query(ge=1)] = None,
    status: Annotated[str | None, Query(max_length=24)] = None,
) -> dict[str, Any]:
    rows = cls.list_classifications(
        db,
        user_id=int(user.id),
        org_ids=_org_ids(db, user),
        declaration_id=declaration_id,
        status=status,
    )
    return {
        "items": rows,
        "total": len(rows),
        "query": {"declaration_id": declaration_id, "status": status},
    }


@router.get(
    "/classifications/{classification_id}", summary="归类详情（候选 + 降级原因 + 人工通路）"
)
def get_classification_ep(classification_id: int, db: DbDep, user: UserDep) -> dict[str, Any]:
    try:
        return cls.get_detail(
            db,
            user_id=int(user.id),
            org_ids=_org_ids(db, user),
            classification_id=classification_id,
        )
    except cls.ClassificationError as exc:
        raise _cls_http_error(exc) from exc


@router.post(
    "/classifications/{classification_id}/manual",
    summary="人工填写并确认结论（不依赖模型可用性 · 幂等）",
)
def submit_manual_ep(
    classification_id: int,
    payload: ClassificationManualCreate,
    db: DbDep,
    user: UserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")] = "",
) -> Any:
    try:
        key = require_idempotency_key({"Idempotency-Key": idempotency_key})
        with idempotent(
            db,
            scope=f"customs:classification:{classification_id}:manual",
            key=key,
            actor_user_id=int(user.id),
            payload=payload.model_dump(mode="json"),
        ) as guard:
            if guard.replay is not None:
                return _replay(guard.replay.status_code, guard.replay.body)
            body = cls.submit_manual(
                db,
                user_id=int(user.id),
                org_ids=_org_ids(db, user),
                classification_id=classification_id,
                hs_code=payload.hs_code,
                label=payload.label,
                rationale=payload.rationale,
            )
            guard.succeed(200, body)
            return body
    except IdempotencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except cls.ClassificationError as exc:
        raise _cls_http_error(exc) from exc
