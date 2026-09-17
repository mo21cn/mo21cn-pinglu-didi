"""文档提取 API —— 触发提取 / 读提取文本 / 人工转录（S1 第 6 条 / ENT-013 / AC-17）。

- POST /api/v1/entrust/attachments/{att_id}/extract        触发提取（写权限，幂等）
- GET  /api/v1/entrust/attachments/{att_id}/text           读提取文本（可见性）
- POST /api/v1/entrust/attachments/{att_id}/transcription  人工转录提交（写权限，幂等）

同步执行，为什么不写 `pending` / `running`
-----------------------------------------
`extract_status` 的取值域里留着 `pending` / `running` —— 那是给"提取作业化"
（扔进 `ent_agent_job` 那类队列、由 worker 领取）预留的。**当前是同步端点**：
一次请求内跑完提取并直接落终态。同步执行下写一个 `running` 中间态毫无意义 ——
请求返回时它必然已经被覆盖，而外部永远观察不到，只会让人以为背后有 worker。
所以本端点**不写中间态**（写了才是说谎），并在 PR 的"未覆盖"里登记
"提取尚未作业化"这一限制。

三种结论各自的下一步
--------------------
| extract_status        | 含义                       | 下一步                        |
|-----------------------|----------------------------|-------------------------------|
| done                  | 已提取出文本                | 可直接引用（带 truncated 提示）|
| needs_transcription   | 有内容无机读文本层          | POST /transcription 人工补录   |
| unsupported           | 格式不在标准库解析能力内    | 转格式后重传，或人工转录       |
| failed                | 文件本身坏了                | 换文件；转录前先确认文件可用   |

权限与"权限判定唯一入口"
------------------------
可见性复用 `attachments_api.load_visible_attachment`（与元数据、下载**同一实现**）；
写权限与上传同一口径：授权链上需 `entrust:quote:create`（作用于该货主），
私有草稿则限归属人本人或该组织内有该权限的成员。**提取与转录都不新开权限**
—— 它们处理的还是同一份附件，凭空多一个权限只会多一处漂移。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import attachments as svc
from app.modules.entrust import extraction
from app.modules.entrust._http import (
    guard_or_400,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.access import (
    PERM_QUOTE_CREATE,
    resolve_context,
)
from app.modules.entrust.attachments_api import (
    load_visible_attachment,
    map_attachment_error,
)
from app.modules.entrust.authz import (
    assert_can_write_entrustment,
    load_entrustment,
    not_found,
)
from app.modules.entrust.schemas import (
    AttachmentTextOut,
    ExtractionResultOut,
    TranscriptionIn,
    attachment_text_out,
)

router = APIRouter()

_SCOPE_EXTRACT = "entrust:attachment:extract"
_SCOPE_TRANSCRIBE = "entrust:attachment:transcribe"

#: 提取结果里给前端看的预览长度（完整文本走 GET /text）
_PREVIEW_CHARS = 200


def _assert_can_modify_attachment(db: Session, *, attachment: dict[str, Any], user_id: int) -> None:
    """附件写权限：与上传同一套规则（授权链 → 作用域权限；私有草稿 → 归属人/组织）。"""
    if attachment["entrustment_id"] is not None:
        entrustment = load_entrustment(db, int(attachment["entrustment_id"]))
        if entrustment is None:
            raise not_found("附件不存在")
        assert_can_write_entrustment(
            db,
            user_id=user_id,
            permission=PERM_QUOTE_CREATE,
            entrustment=entrustment,
            detail="附件不存在",
        )
        return

    owner_user_id = int(attachment["owner_user_id"])
    if user_id == owner_user_id:
        return
    context = resolve_context(db, user_id=user_id)
    org_id = attachment["org_id"]
    if org_id is None or int(org_id) not in context.org_ids:
        raise not_found("附件不存在")
    if not context.can(PERM_QUOTE_CREATE, owner_user_id=owner_user_id):
        raise HTTPException(
            status_code=403,
            detail=f"用户 {user_id} 缺少权限 {PERM_QUOTE_CREATE}（作用于货主 {owner_user_id}）",
        )


def _read_attachment_bytes(attachment: dict[str, Any]) -> bytes:
    """按存储键读回附件字节；文件丢失 → 存储错误（不伪装成"提取失败"）。"""
    return svc.resolve_path(attachment).read_bytes()


def _max_chars() -> int:
    return int(get_settings().EXTRACTION_MAX_TEXT_CHARS)


def _result_payload(
    attachment_id: int,
    outcome: extraction.ExtractOutcome,
    *,
    persisted_status: str,
    persisted_error: str | None,
    extracted_chars: int | None,
    previous_text_source: str | None = None,
) -> dict[str, Any]:
    preview = None
    if outcome.text:
        preview = outcome.text[:_PREVIEW_CHARS]
    return {
        "attachment_id": attachment_id,
        "extract_status": persisted_status,
        "extract_error": persisted_error,
        "extracted_chars": extracted_chars,
        "media_kind": outcome.media_kind,
        "truncated": outcome.truncated,
        "detail": outcome.detail,
        "notes": outcome.notes,
        "text_preview": preview,
        # 覆盖前那份文本的来源（没有文本时为 None）。放在**响应**里而不是幂等载荷里：
        # 幂等载荷只能由请求派生，掺进当前状态会让同键重放变成"同键异体"409。
        "previous_text_source": previous_text_source,
    }


@router.post(
    "/attachments/{attachment_id}/extract",
    response_model=ExtractionResultOut,
    summary="触发附件文本提取（同步；图片/扫描件 → needs_transcription）",
    dependencies=[Depends(require_entrust_enabled)],
)
def extract_attachment(
    attachment_id: int,
    acknowledge_transcription_overwrite: Annotated[bool, Query()] = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """提取附件文本并落库。

    **可重复调用**（"重抽"就是同一个动作）：重复提取覆盖同一份文本，
    不产生多份互相矛盾的结果。因此不需要 `?refresh=true` 之类的开关。

    ⚠️ 但"覆盖"在**人工转录**上是有代价的：转录内容不可再生成，而失败分支
    还会 `drop_text`。所以当该附件当前的文本来自人工转录时，本端点**默认拒绝**
    （409），要求调用方先提示覆盖影响、再带
    `acknowledge_transcription_overwrite=true` 显式确认（HO 0917-3 裁定四）。
    """
    key = guard_or_400(idempotency_key)
    user_id = int(user.id)
    attachment = load_visible_attachment(db, attachment_id=attachment_id, user_id=user_id)
    _assert_can_modify_attachment(db, attachment=attachment, user_id=user_id)

    # 覆盖前的来源要能查得到：这是"保留已被作业使用的文本依据"的**最小可核对形态**
    # —— 附件文本是 1:1 一行、不留历史（见本文件顶部与 PR 的剩余限制），
    # 至少要让"这次覆盖掉的是人工转录还是机器提取"有据可查。
    #
    # ⚠️ 它只进**响应**，**绝不进幂等载荷**：幂等载荷一旦掺进"读自当前状态"的值，
    # 同一个键的第二次合法调用就会因为载荷不同而被判成"同键异体"→ 409。
    # 实测踩到过（`test_extract_is_idempotent_under_same_key` 当场红）。
    # **幂等载荷只能由请求派生**（附件 id / 内容指纹 / 参数）。
    previous = svc.get_text(db, attachment_id)
    previous_source = str(previous["source"]) if previous is not None else None

    def business() -> dict[str, Any]:
        if previous_source == svc.TEXT_SOURCE_MANUAL and not acknowledge_transcription_overwrite:
            raise svc.AttachmentTranscriptionOverwriteError(
                "该附件当前的文本来自人工转录，重新提取会覆盖它且不可恢复；"
                "确认覆盖请带 acknowledge_transcription_overwrite=true"
            )
        data = _read_attachment_bytes(attachment)
        outcome = extraction.extract(
            data,
            content_type=attachment["content_type"],
            filename=attachment["filename"],
            max_chars=_max_chars(),
        )

        if outcome.status == extraction.STATUS_DONE:
            assert outcome.text is not None
            svc.upsert_text(
                db,
                attachment_id=attachment_id,
                content=outcome.text,
                source=svc.TEXT_SOURCE_EXTRACTOR,
                truncated=outcome.truncated,
            )
            status = svc.EXTRACT_DONE
            error = None
            chars: int | None = outcome.chars
        else:
            # 状态与文本必须一致：状态说"没有文本"时，就不能还留着上次的文本，
            # 否则 UI 与 Agent 会读到一份与当前结论矛盾的内容。
            # 删的是**派生数据**（随时可由原文件重抽），不是原始证据。
            svc.drop_text(db, attachment_id)
            status = outcome.status
            error = outcome.detail[:250]
            chars = None

        updated = svc.set_extract_status(
            db,
            attachment_id=attachment_id,
            status=status,
            error=error,
            extracted_chars=chars,
        )
        return _result_payload(
            attachment_id,
            outcome,
            persisted_status=str(updated["extract_status"]),
            persisted_error=updated["extract_error"],
            extracted_chars=updated["extracted_chars"],
            previous_text_source=previous_source,
        )

    return run_write(
        db,
        scope=_SCOPE_EXTRACT,
        key=key,
        actor_user_id=user_id,
        payload={
            "attachment_id": attachment_id,
            "sha256": attachment["sha256"],
            "max_chars": _max_chars(),
            "acknowledge_transcription_overwrite": acknowledge_transcription_overwrite,
        },
        business=business,
        map_domain_error=map_attachment_error,
    )


@router.get(
    "/attachments/{attachment_id}/text",
    response_model=AttachmentTextOut,
    summary="读附件提取文本（可见性同元数据；无文本时返回状态而非 404）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_attachment_text(
    attachment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    attachment = load_visible_attachment(db, attachment_id=attachment_id, user_id=int(user.id))
    return attachment_text_out(attachment, svc.get_text(db, attachment_id))


@router.post(
    "/attachments/{attachment_id}/transcription",
    response_model=AttachmentTextOut,
    summary="人工转录补录（图片 / 扫描件 / 老式 xls 的降级路径）",
    dependencies=[Depends(require_entrust_enabled)],
)
def submit_transcription(
    attachment_id: int,
    payload: TranscriptionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """人工转录：把图片/扫描件的内容由人写成文本，作为**证据材料**入库。

    转录文本同样是不可信数据（AC-18）—— 它的来源是"人"，不是"系统"，
    所以 `source` 记为 `manual_transcription`，与机读提取区别开。
    """
    key = guard_or_400(idempotency_key)
    user_id = int(user.id)
    attachment = load_visible_attachment(db, attachment_id=attachment_id, user_id=user_id)
    _assert_can_modify_attachment(db, attachment=attachment, user_id=user_id)

    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="转录文本不能为空白")
    limit = _max_chars()
    if len(text) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"转录文本超过上限 {limit} 字符（当前 {len(text)}）",
        )

    def business() -> dict[str, Any]:
        svc.upsert_text(
            db,
            attachment_id=attachment_id,
            content=text,
            source=svc.TEXT_SOURCE_MANUAL,
            truncated=False,
            created_by=user_id,
        )
        updated = svc.set_extract_status(
            db,
            attachment_id=attachment_id,
            status=svc.EXTRACT_DONE,
            error=None,
            extracted_chars=len(text),
        )
        return attachment_text_out(updated, svc.get_text(db, attachment_id)).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_TRANSCRIBE,
        key=key,
        actor_user_id=user_id,
        payload={
            "attachment_id": attachment_id,
            "chars": len(text),
            # 用内容哈希做幂等体的一部分：同一键配不同文本必须被判为冲突，
            # 否则"同键重放"会把两次不同的转录悄悄合并成一次。
            "sha256": svc.hash_text(text),
        },
        business=business,
        map_domain_error=map_attachment_error,
    )


__all__ = ["router"]
