"""附件 API —— 上传 / 元数据 / **授权下载**（S1 第 5 条 / ENT-009）。

- POST   /api/v1/entrust/attachments                          上传附件（multipart，幂等）
- GET    /api/v1/entrust/attachments/{att_id}                附件元数据
- GET    /api/v1/entrust/attachments/{att_id}/download       授权下载（**唯一**出口）
- GET    /api/v1/entrust/entrustments/{eid}/attachments      按委托授权列附件
- GET    /api/v1/entrust/artifacts/{aid}/attachments         按成果列附件
- POST   /api/v1/entrust/artifacts/{aid}/attachments         绑定附件到成果（幂等）

下载为什么必须走这里
--------------------
附件落在 `ATTACHMENT_STORAGE_DIR`（公共静态路径之外），**没有任何静态路由指向它**。
下载的唯一入口是本文件的 `download_attachment`：先做可见性判定（非参与方 404），
再读盘返回。这样"忘了校验的下载接口"在最坏情况下也只是拿不到文件，
而不是把文件暴露给任何知道 URL 的人。

归属与权限（AC-10）
------------------
* 挂在**委托授权**下（`entrustment_id`）：参与方按叠加层作用域权限；
* **私有未绑定草稿**（`assignment_id`）：授权链上只有委托单，无授权行，
  按"归属于该委托单的货主 + 该组织成员"判定；
* 上传是写操作：需要 `entrust:quote:create` 且作用于该货主。
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import attachments as svc
from app.modules.entrust._http import (
    guard_or_400,
    map_access_denied,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.access import (
    PERM_QUOTE_CREATE,
    resolve_context,
)
from app.modules.entrust.artifacts import ArtifactError, get_artifact
from app.modules.entrust.authz import (
    assert_can_view_entrustment,
    assert_can_view_scoped_object,
    assert_can_write_entrustment,
    load_assignment,
    load_entrustment,
    not_found,
)
from app.modules.entrust.schemas import (
    AttachmentBindOut,
    AttachmentListOut,
    AttachmentOut,
    attachment_out,
)

router = APIRouter()

_SCOPE_UPLOAD = "entrust:attachment:upload"
_SCOPE_BIND = "entrust:artifact:attach"


def _map_attachment_error(exc: Exception) -> HTTPException | None:
    """附件服务异常 → HTTP 语义；无法识别返回 None（不吞真实 bug）。"""
    mapped = map_access_denied(exc)
    if mapped is not None:
        return mapped
    if isinstance(exc, svc.AttachmentNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.AttachmentValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, svc.AttachmentStorageError):
        # 存储故障是服务端问题，不能伪装成"业务拒绝"
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, svc.AttachmentError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


def _authorize_download(db: Session, *, attachment: dict[str, Any], user_id: int) -> None:
    """下载授权（与元数据读取同口径）。"""
    if attachment["entrustment_id"] is not None:
        entrustment = load_entrustment(db, int(attachment["entrustment_id"]))
        if entrustment is None:
            raise not_found("附件不存在")
        assert_can_view_entrustment(
            db, user_id=user_id, entrustment=entrustment, detail="附件不存在"
        )
        return
    assert_can_view_scoped_object(
        db,
        user_id=user_id,
        owner_user_id=int(attachment["owner_user_id"]),
        org_id=attachment["org_id"],
        detail="附件不存在",
    )


def _load_visible_attachment(db: Session, *, attachment_id: int, user_id: int) -> dict[str, Any]:
    attachment = svc.get_attachment(db, attachment_id)
    if attachment is None:
        raise not_found("附件不存在")
    _authorize_download(db, attachment=attachment, user_id=user_id)
    return attachment


@router.post(
    "/attachments",
    response_model=AttachmentOut,
    summary="上传附件（挂委托授权或私有未绑定草稿，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def upload_attachment(
    file: Annotated[UploadFile, File()],
    entrustment_id: Annotated[int | None, Form()] = None,
    assignment_id: Annotated[int | None, Form()] = None,
    source_event_at: Annotated[str | None, Form()] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """上传附件。

    归属二选一：`entrustment_id`（委托授权，参与方协作）或 `assignment_id`
    （私有未绑定草稿，入库后被委托单的货主/组织约束）。两者都给或都不给 → 400：
    归属含糊的附件没有可见性规则可言，不能默认放行。
    """
    key = guard_or_400(idempotency_key)
    user_id = int(user.id)

    if (entrustment_id is None) == (assignment_id is None):
        raise HTTPException(
            status_code=400,
            detail="必须且只能指定一个归属：entrustment_id（委托授权）或 assignment_id（委托单）",
        )

    owner_user_id: int
    org_id: int | None
    if entrustment_id is not None:
        entrustment = load_entrustment(db, entrustment_id)
        if entrustment is None:
            raise not_found("委托授权不存在")
        assert_can_write_entrustment(
            db,
            user_id=user_id,
            permission=PERM_QUOTE_CREATE,
            entrustment=entrustment,
            detail="委托授权不存在",
        )
        owner_user_id = int(entrustment["entrust_user_id"])
        org_id = int(entrustment["org_id"])
    else:
        assert assignment_id is not None
        assignment = load_assignment(db, assignment_id)
        if assignment is None:
            raise not_found("委托单不存在")
        owner_user_id = int(assignment["owner_user_id"])
        org_id = int(assignment["org_id"]) if assignment["org_id"] is not None else None
        if user_id != owner_user_id:
            # 经理侧上传到私有草稿：必须是该组织成员且具备创作权限
            context = resolve_context(db, user_id=user_id)
            if org_id is None or org_id not in context.org_ids:
                raise not_found("委托单不存在")
            if not context.can(PERM_QUOTE_CREATE, owner_user_id=owner_user_id):
                raise HTTPException(
                    status_code=403,
                    detail=f"用户 {user_id} 缺少权限 {PERM_QUOTE_CREATE}"
                    f"（作用于货主 {owner_user_id}）",
                )

    data = svc.read_limited(file.file)
    digest = hashlib.sha256(data).hexdigest()
    payload = {
        "entrustment_id": entrustment_id,
        "assignment_id": assignment_id,
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(data),
        "sha256": digest,
        "source_event_at": source_event_at,
    }
    return run_write(
        db,
        scope=_SCOPE_UPLOAD,
        key=key,
        actor_user_id=user_id,
        payload=payload,
        business=lambda: attachment_out(
            svc.store_bytes(
                db,
                uploader_user_id=user_id,
                owner_user_id=owner_user_id,
                org_id=org_id,
                filename=file.filename,
                content_type=file.content_type,
                data=data,
                entrustment_id=entrustment_id,
                assignment_id=assignment_id,
                source_event_at=source_event_at,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_attachment_error,
    )


@router.get(
    "/attachments/{attachment_id}",
    response_model=AttachmentOut,
    summary="附件元数据（参与方；非参与方 404）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_attachment(
    attachment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    attachment = _load_visible_attachment(db, attachment_id=attachment_id, user_id=int(user.id))
    return attachment_out(attachment)


@router.get(
    "/attachments/{attachment_id}/download",
    summary="授权下载（唯一出口；非参与方 404；文件名已安全化）",
    dependencies=[Depends(require_entrust_enabled)],
)
def download_attachment(
    attachment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    attachment = _load_visible_attachment(db, attachment_id=attachment_id, user_id=int(user.id))
    try:
        path = svc.resolve_path(attachment)
    except svc.AttachmentError as exc:
        raise _map_attachment_error(exc) or exc from exc
    return FileResponse(
        path=path,
        media_type=str(attachment["content_type"]),
        filename=str(attachment["filename"]),
    )


@router.get(
    "/entrustments/{entrustment_id}/attachments",
    response_model=AttachmentListOut,
    summary="按委托授权列附件（参与方）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_entrustment_attachments(
    entrustment_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    entrustment = load_entrustment(db, entrustment_id)
    if entrustment is None:
        raise not_found("委托授权不存在")
    assert_can_view_entrustment(
        db, user_id=int(user.id), entrustment=entrustment, detail="委托授权不存在"
    )
    total, items = svc.list_attachments(db, entrustment_id=entrustment_id, page=page, size=size)
    return AttachmentListOut(
        total=total, page=page, size=size, items=[attachment_out(i) for i in items]
    )


@router.get(
    "/artifacts/{artifact_id}/attachments",
    response_model=AttachmentListOut,
    summary="按成果列附件（与成果同可见性）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_artifact_attachments(
    artifact_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _artifact_and_entrustment(db, artifact_id=artifact_id, user_id=int(user.id), write=False)
    items = svc.list_for_artifact(db, artifact_id)
    total = len(items)
    start = (page - 1) * size
    return AttachmentListOut(
        total=total,
        page=page,
        size=size,
        items=[attachment_out(i) for i in items[start : start + size]],
    )


@router.post(
    "/artifacts/{artifact_id}/attachments",
    response_model=AttachmentBindOut,
    summary="绑定附件到成果（需成果写权限；附件必须属于同一委托授权；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def bind_attachment(
    artifact_id: int,
    attachment_id: Annotated[int, Form()],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    artifact, _ = _artifact_and_entrustment(
        db, artifact_id=artifact_id, user_id=int(user.id), write=True
    )
    attachment = svc.get_attachment(db, attachment_id)
    if attachment is None:
        raise not_found("附件不存在")
    if attachment["entrustment_id"] != artifact["entrustment_id"]:
        # 跨委托绑定会造出"客户看得到但归属别家"的证据链，一律拒绝
        raise HTTPException(status_code=409, detail="附件与成果不在同一委托授权下，不能绑定")

    payload = {"artifact_id": artifact_id, "attachment_id": attachment_id}
    return run_write(
        db,
        scope=_SCOPE_BIND,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: svc.bind_to_artifact(
            db, artifact_id=artifact_id, attachment_id=attachment_id, actor_id=int(user.id)
        ),
        map_domain_error=_map_attachment_error,
    )


def _artifact_and_entrustment(
    db: Session, *, artifact_id: int, user_id: int, write: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """取成果与其授权，并按需做写权限校验（读时用授权链的可见性规则）。"""
    try:
        artifact = get_artifact(db, artifact_id)
    except ArtifactError as exc:
        raise not_found("成果不存在") from exc
    entrustment = load_entrustment(db, int(artifact["entrustment_id"]))
    if entrustment is None:
        raise not_found("成果不存在")
    if write:
        assert_can_write_entrustment(
            db,
            user_id=user_id,
            permission=PERM_QUOTE_CREATE,
            entrustment=entrustment,
            detail="成果不存在",
        )
    else:
        assert_can_view_entrustment(
            db, user_id=user_id, entrustment=entrustment, detail="成果不存在"
        )
    return artifact, entrustment


__all__ = ["router"]
