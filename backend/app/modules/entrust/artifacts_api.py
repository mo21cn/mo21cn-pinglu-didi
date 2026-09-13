"""成果版本 API —— ENT-004 服务层的 HTTP 面（ENT-006）。

R1 最小接口集（计划 §3.3「成果」组）的底座部分：

- POST   /api/v1/entrust/entrustments/{eid}/artifacts      创建成果（v1 即生效，幂等）
- GET    /api/v1/entrust/artifacts/{aid}                   成果详情（含生效版本）
- GET    /api/v1/entrust/artifacts/{aid}/revisions         版本历史（append-only 审计视图）
- POST   /api/v1/entrust/artifacts/{aid}/revisions         追加新版本（编辑，不改生效版本，幂等）
- POST   /api/v1/entrust/artifacts/{aid}/confirm           确认绑定精确版本（幂等）
- POST   /api/v1/entrust/artifacts/{aid}/void              作废（作废后不可确认/追加，幂等）

可见性与权限（AC-10，全部服务端校验）：
* 成果挂在**委托授权**（`ent_entrustment`）下：
  - **货主本人**（授权的 entrust_user_id）：只读（详情 + 版本历史），不可写；
  - **组织成员**：写操作要求叠加层按货主作用域授予权限
    （`assert_can(..., owner_user_id=授权的货主)`），且操作者必须是
    **该授权所属组织**的成员 —— 防止"拿到 A 货主授权的组织成员操作 A 货主
    挂在另一组织下的成果"；
  - **其他人**：一律 404，不区分"不存在"与"无权"。
* 权限代码：创建/追加/确认用 `entrust:quote:create`；作废用 `entrust:quote:publish`。
* `ENTRUST_ENABLED=false` 时整组 404；写操作全部幂等。

已知限制（如实记录，不假装已做）：
* payload 级客户白名单投影在 S3 对客报价（customer_offer）落地；当前货主只读
  端点返回完整 payload —— 因此 R1 中**挂到成果上的内部成本类字段必须由调用方
  自律**，或等 S3 的白名单投影完成后再向货主开放该类成果。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import artifacts as art
from app.modules.entrust._http import (
    guard_or_400,
    map_access_denied,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.access import (
    PERM_QUOTE_CREATE,
    PERM_QUOTE_PUBLISH,
    AccessContext,
    resolve_context,
)

router = APIRouter()

_SCOPE_CREATE = "entrust:artifact:create"
_SCOPE_APPEND = "entrust:artifact:append"
_SCOPE_CONFIRM = "entrust:artifact:confirm"
_SCOPE_VOID = "entrust:artifact:void"


class ArtifactCreate(BaseModel):
    artifact_type: str = Field(min_length=1, max_length=48)
    payload: dict[str, Any]
    note: str | None = Field(default=None, max_length=255)


class ArtifactAppend(BaseModel):
    payload: dict[str, Any]
    note: str | None = Field(default=None, max_length=255)


class ArtifactConfirm(BaseModel):
    revision_no: int = Field(ge=1)


class ArtifactVoid(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


def _map_artifact_error(exc: Exception) -> HTTPException | None:
    """成果服务异常 → HTTP 语义；无法识别返回 None（不吞真实 bug）。"""
    mapped = map_access_denied(exc)
    if mapped is not None:
        return mapped
    if isinstance(exc, art.ArtifactNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (art.ArtifactVoidError, art.ManualTakeoverError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, art.ArtifactError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


_ENTRUSTMENT_COLS = "id, org_id, entrust_user_id, status"


def _get_entrustment(db: Session, entrustment_id: int) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(f"SELECT {_ENTRUSTMENT_COLS} FROM ent_entrustment WHERE id = :eid"),
            {"eid": entrustment_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _assert_org_member(ctx_org_ids: frozenset[int], entrustment: dict[str, Any]) -> None:
    """操作者必须是该授权所属组织的成员（叠加层作用域之外的定位检查）。"""
    if int(entrustment["org_id"]) not in ctx_org_ids:
        raise HTTPException(status_code=404, detail="成果不存在")


def _artifact_with_entrustment(
    db: Session, artifact_id: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """读成果及其所属授权；任一不存在 → 404（不泄漏存在性）。"""
    try:
        artifact = art.get_artifact(db, artifact_id)
    except art.ArtifactError as exc:
        raise _map_artifact_error(exc) or exc from exc
    entrustment = _get_entrustment(db, artifact["entrustment_id"])
    if entrustment is None:
        raise HTTPException(status_code=404, detail="成果不存在")
    return artifact, entrustment


def _assert_can_view(
    db: Session,
    *,
    user_id: int,
    artifact: dict[str, Any],
    entrustment: dict[str, Any],
) -> None:
    """读可见性：货主本人，或该授权所属组织的成员。"""
    owner_user_id = int(entrustment["entrust_user_id"])
    if user_id == owner_user_id:
        return
    ctx = resolve_context(db, user_id=user_id)
    _assert_org_member(ctx.org_ids, entrustment)
    if not ctx.can("entrust:view"):
        raise HTTPException(status_code=404, detail="成果不存在")


def _assert_can_write(
    db: Session,
    *,
    user_id: int,
    permission: str,
    entrustment: dict[str, Any],
) -> AccessContext:
    """写权限：组织成员 + 按货主作用域的生效授权 + 授权自身处于 active。

    顺序有讲究：先做成员资格判断（非本组织成员 → 404，不泄漏授权存在性），
    再做作用域权限判断（是成员但没有该货主的生效授权 → 403）。
    """
    ctx = resolve_context(db, user_id=user_id)
    _assert_org_member(ctx.org_ids, entrustment)
    if str(entrustment["status"]) != "active":
        raise HTTPException(status_code=409, detail="委托授权未生效或已失效，不能操作成果")
    owner_user_id = int(entrustment["entrust_user_id"])
    if not ctx.can(permission, owner_user_id=owner_user_id):
        raise HTTPException(
            status_code=403,
            detail=f"用户 {user_id} 缺少权限 {permission}（作用于货主 {owner_user_id}）",
        )
    return ctx


@router.post(
    "/entrustments/{entrustment_id}/artifacts",
    summary="创建成果（组织成员，按货主作用域授权，v1 即生效，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_artifact(
    entrustment_id: int,
    data: ArtifactCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    entrustment = _get_entrustment(db, entrustment_id)
    if entrustment is None:
        raise HTTPException(status_code=404, detail="委托授权不存在")
    _assert_can_write(
        db, user_id=int(user.id), permission=PERM_QUOTE_CREATE, entrustment=entrustment
    )
    payload = data.model_dump(mode="json")
    return run_write(
        db,
        scope=_SCOPE_CREATE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: art.create_artifact(
            db,
            entrustment_id=entrustment_id,
            artifact_type=data.artifact_type.strip(),
            payload=data.payload,
            created_by=int(user.id),
            source=art.SOURCE_MANUAL,
            note=data.note,
        ),
        map_domain_error=_map_artifact_error,
    )


@router.get(
    "/artifacts/{artifact_id}",
    summary="成果详情（货主本人或该授权组织成员）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_artifact(
    artifact_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    artifact, entrustment = _artifact_with_entrustment(db, artifact_id)
    _assert_can_view(db, user_id=int(user.id), artifact=artifact, entrustment=entrustment)
    return artifact


@router.get(
    "/artifacts/{artifact_id}/revisions",
    summary="版本历史（append-only 审计视图，与详情同可见性）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_revisions(
    artifact_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    artifact, entrustment = _artifact_with_entrustment(db, artifact_id)
    _assert_can_view(db, user_id=int(user.id), artifact=artifact, entrustment=entrustment)
    return {"items": art.list_revisions(db, artifact_id)}


@router.post(
    "/artifacts/{artifact_id}/revisions",
    summary="追加新版本（编辑动作，不改变生效版本，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def append_revision(
    artifact_id: int,
    data: ArtifactAppend,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    _, entrustment = _artifact_with_entrustment(db, artifact_id)
    _assert_can_write(
        db, user_id=int(user.id), permission=PERM_QUOTE_CREATE, entrustment=entrustment
    )
    payload = data.model_dump(mode="json")
    return run_write(
        db,
        scope=_SCOPE_APPEND,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: art.append_revision(
            db,
            artifact_id=artifact_id,
            payload=data.payload,
            actor_id=int(user.id),
            source=art.SOURCE_MANUAL,
            note=data.note,
        ),
        map_domain_error=_map_artifact_error,
    )


@router.post(
    "/artifacts/{artifact_id}/confirm",
    summary="确认并绑定精确版本（作废成果拒绝；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def confirm_revision(
    artifact_id: int,
    data: ArtifactConfirm,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    _, entrustment = _artifact_with_entrustment(db, artifact_id)
    _assert_can_write(
        db, user_id=int(user.id), permission=PERM_QUOTE_CREATE, entrustment=entrustment
    )
    payload = data.model_dump(mode="json")
    return run_write(
        db,
        scope=_SCOPE_CONFIRM,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: art.confirm_revision(
            db,
            artifact_id=artifact_id,
            revision_no=data.revision_no,
            actor_id=int(user.id),
            as_source=art.SOURCE_MANUAL,
        ),
        map_domain_error=_map_artifact_error,
    )


@router.post(
    "/artifacts/{artifact_id}/void",
    summary="作废成果（作废后不可确认/追加；历史版本保留可审计；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def void_artifact(
    artifact_id: int,
    data: ArtifactVoid,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    _, entrustment = _artifact_with_entrustment(db, artifact_id)
    _assert_can_write(
        db, user_id=int(user.id), permission=PERM_QUOTE_PUBLISH, entrustment=entrustment
    )
    payload = data.model_dump(mode="json")
    return run_write(
        db,
        scope=_SCOPE_VOID,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: art.void_artifact(
            db, artifact_id=artifact_id, actor_id=int(user.id), reason=data.reason
        ),
        map_domain_error=_map_artifact_error,
    )
