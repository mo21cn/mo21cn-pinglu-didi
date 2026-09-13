"""成果版本 API —— ENT-004 服务层的 HTTP 面（ENT-006）。

R1 最小接口集（计划 §3.3「成果」组）的底座部分：

- POST   /api/v1/entrust/entrustments/{eid}/artifacts      创建成果（v1 即生效，幂等）
- GET    /api/v1/entrust/artifact-types                    成果类型注册表（取值域 + 字段契约）
- GET    /api/v1/entrust/artifacts/{aid}                   成果详情（含生效版本 + 缺项）
- GET    /api/v1/entrust/artifacts/{aid}/revisions         版本历史（append-only 审计视图）
- GET    /api/v1/entrust/artifacts/{aid}/changes           字段变化清单（按注册表字段比对）
- POST   /api/v1/entrust/artifacts/{aid}/revisions         追加新版本（编辑，不改生效版本，幂等）
- POST   /api/v1/entrust/artifacts/{aid}/confirm           确认绑定精确版本（幂等）
- POST   /api/v1/entrust/artifacts/{aid}/void              作废（作废后不可确认/追加，幂等）

可见性与权限（AC-10，全部服务端校验，统一走 `authz.py` 这条唯一入口）：
* 成果挂在**委托授权**（`ent_entrustment`）下：
  - **货主本人**（授权的 entrust_user_id）：只读（详情 + 版本历史 + 变化清单），不可写；
  - **组织成员**：写操作要求叠加层按货主作用域授予权限
    （`assert_can_write_entrustment`），且操作者必须是**该授权所属组织**的成员；
  - **其他人**：一律 404，不区分"不存在"与"无权"。
* 权限代码：创建/追加/确认用 `entrust:quote:create`；作废用 `entrust:quote:publish`。
* 内容校验：未知类型 / 未知字段一律 400（取值域由 `registry.py` 固定），
  缺必填字段不报错但会进入 `missing_fields`。
* `ENTRUST_ENABLED=false` 时整组 404；写操作全部幂等。

已知限制（如实记录，不假装已做）：
* payload 级客户白名单投影在 S3 对客报价（customer_offer）落地；当前货主只读
  端点返回完整 payload —— 因此 R1 中**挂到成果上的内部成本类字段必须由调用方
  自律**，或等 S3 的白名单投影完成后再向货主开放该类成果。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import artifacts as art
from app.modules.entrust import registry as reg
from app.modules.entrust._http import (
    guard_or_400,
    map_access_denied,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.access import (
    PERM_QUOTE_CREATE,
    PERM_QUOTE_PUBLISH,
)
from app.modules.entrust.authz import (
    assert_can_view_entrustment,
    assert_can_write_entrustment,
    load_entrustment,
    not_found,
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


def _artifact_with_entrustment(
    db: Session, artifact_id: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """读成果及其所属授权；任一不存在 → 404（不泄漏存在性）。"""
    try:
        artifact = art.get_artifact(db, artifact_id)
    except art.ArtifactError as exc:
        raise _map_artifact_error(exc) or exc from exc
    entrustment = load_entrustment(db, artifact["entrustment_id"])
    if entrustment is None:
        raise not_found("成果不存在")
    return artifact, entrustment


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
    entrustment = load_entrustment(db, entrustment_id)
    if entrustment is None:
        raise not_found("委托授权不存在")
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_CREATE,
        entrustment=entrustment,
        detail="成果不存在",
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
    assert_can_view_entrustment(db, user_id=int(user.id), entrustment=entrustment)
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
    assert_can_view_entrustment(db, user_id=int(user.id), entrustment=entrustment)
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
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_CREATE,
        entrustment=entrustment,
        detail="成果不存在",
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
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_CREATE,
        entrustment=entrustment,
        detail="成果不存在",
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
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_PUBLISH,
        entrustment=entrustment,
        detail="成果不存在",
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


@router.get(
    "/artifact-types",
    summary="成果类型注册表（取值域 + 字段契约 + 内部字段 + 可绑证据类别）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_artifact_types(user: User = Depends(get_current_user)) -> Any:
    """列出全部成果类型契约。

    这是**静态契约**，不是租户数据：任何登录用户都可以读到"有哪些成果类型、
    每种类型有哪些字段、哪些字段属于内部字段"。前端据此渲染表单与字段标签，
    服务端据此校验内容 —— 两端用同一份取值域，避免各写一套。
    """
    return {
        "items": reg.list_specs(),
        "customer_visible_types": sorted(reg.CUSTOMER_VISIBLE_TYPES),
        "evidence_kinds": sorted(reg.ALL_EVIDENCE_KINDS),
    }


@router.get(
    "/artifacts/{artifact_id}/changes",
    summary="两个版本之间的字段变化清单（同可见性；内部字段被标注）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_changes(
    artifact_id: int,
    from_revision: int = Query(ge=1),
    to_revision: int = Query(ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """字段级变化清单（计划 §3.3「成果」组的「字段变化清单」）。

    只比较注册表声明的已知字段；`internal` 标记该字段是否属于内部成本口径 ——
    货主看清单时前端据此折叠内部字段（真正的白名单投影在 S3 的客户投影落地）。
    """
    artifact, entrustment = _artifact_with_entrustment(db, artifact_id)
    assert_can_view_entrustment(db, user_id=int(user.id), entrustment=entrustment)
    try:
        reg.get_spec(artifact["artifact_type"])
    except reg.UnknownArtifactTypeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    revisions = {
        int(item["revision_no"]): item["payload"] for item in art.list_revisions(db, artifact_id)
    }
    missing = [no for no in (from_revision, to_revision) if no not in revisions]
    if missing:
        raise not_found(f"成果 {artifact_id} 不存在版本 {sorted(missing)}")
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact["artifact_type"],
        "from_revision": from_revision,
        "to_revision": to_revision,
        "changes": reg.diff_payloads(
            artifact["artifact_type"], revisions[from_revision], revisions[to_revision]
        ),
    }
