"""S3 对客发布与客户响应端点（`/api/v1/entrust` 下；BP-03 / D1-07 / D1-11）。

八条端点，两条通道**各走各的投影**：

| 通道 | 端点 | 拿到的内容 |
| --- | --- | --- |
| 经理 | `POST/GET /entrustments/{eid}/offer-releases`、`GET /offer-releases/{id}`、`POST /offer-releases/{id}/withdraw`、`GET/POST /artifacts/{id}/source-checks` | 发布控制信息 + **客户看到的快照** + 来源门槛 + 响应 |
| 客户 | `GET /my-offer-releases`、`GET /offer-releases/{id}`（本人那条）、`POST /offer-releases/{id}/responses` | **只有发布时冻结的那份投影** |

两条通道的分叉是**服务端投影**（`offers.project_release_for_*`）决定的，不是让前端藏字段
（合同 §3.2 / AC-26：不得"先返回前端再隐藏"）。

一处刻意的**不对称**（写进 `scope_matrix` 的 note）
----------------------------------------------------
* 局外人读一条发布 ⇒ **404**（不泄漏存在性）；
* **经理**去响应客户的发布 ⇒ **403**。
  这不是"顺手统一"，而是两件不同的事：经理**知道**这条发布存在（他有权看），
  但他**没有**代替客户确认的权限 —— 裁定三原话"组织经理权限本身不能代替客户确认权限"。
  若也返回 404，客户端只会得到"这条不存在"，把一条明确的权限结论伪装成缺数据。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import artifacts as art
from app.modules.entrust import attachments as att_svc
from app.modules.entrust import offers as svc
from app.modules.entrust import schemas as sm
from app.modules.entrust._http import guard_or_400, require_entrust_enabled, run_write
from app.modules.entrust.access import PERM_QUOTE_PUBLISH
from app.modules.entrust.authz import (
    assert_can_view_assignment,
    assert_can_view_entrustment,
    assert_can_write_entrustment,
    load_assignment,
    load_entrustment,
    not_found,
)

router = APIRouter()

_SCOPE_RELEASE = "entrust:offer:release"
_SCOPE_WITHDRAW = "entrust:offer:withdraw"
_SCOPE_RESPOND = "entrust:offer:respond"
_SCOPE_SOURCE_CHECK = "entrust:offer:source-check"


def _map_errors(exc: Exception) -> HTTPException | None:
    """服务层异常 → HTTP 语义（**一处集中**，免得同一异常在不同端点有不同状态码）。"""
    from app.modules.entrust.access import AccessDeniedError

    if isinstance(exc, svc.OfferForbiddenError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, svc.OfferNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, art.ArtifactNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.OfferStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, svc.OfferSourceGateError):
        # 门槛未过是 **400**（请求合法、当前事实不允许），但**必须带清单** ——
        # 只说"未核验"等于没给出可执行的下一步。
        return HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "pending_sources": exc.pending,
                "rejected_sources": exc.rejected,
            },
        )
    if isinstance(exc, svc.OfferError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AccessDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    return None


def _artifact_scope(db: Session, artifact_id: int) -> tuple[dict[str, Any], dict[str, Any], int]:
    """读成果并定位它的授权与委托单（三者的关系必须自洽，否则 404）。"""
    artifact = art.get_artifact(db, artifact_id)
    entrustment_id = int(artifact["entrustment_id"])
    entrustment = load_entrustment(db, entrustment_id)
    if entrustment is None:
        raise not_found("成果不存在")
    assignment_id = artifact["assignment_id"]
    if assignment_id is None:
        raise HTTPException(status_code=400, detail="该成果没有归属委托单，不能对客发布")
    return artifact, entrustment, int(assignment_id)


# ── 经理：发布 ──────────────────────────────────────────────────────────────


@router.post(
    "/entrustments/{entrustment_id}/offer-releases",
    response_model=sm.OfferReleaseCreatedOut,
    summary="发布指定的成果版本给客户（经理人，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_offer_release(
    entrustment_id: int,
    data: sm.OfferReleaseCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """把**指定的那一个**成果版本发布给客户（合同 BP-03 第 5 条）。

    ⛔ 不接受"发布最新版本"。⛔ 发布前必须过**来源门槛**（未核验的提案不得作为已发布依据）。
    ⛔ 内部确认新版本**不会**自动撤销客户手上那份（裁定三冻结细节①）—— 只有这里的新一次发布
    或显式的撤回，才改变旧报价的可响应状态。
    """
    key = guard_or_400(idempotency_key)
    entrustment = load_entrustment(db, entrustment_id)
    if entrustment is None:
        raise not_found("委托授权不存在")
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_PUBLISH,
        entrustment=entrustment,
        detail="成果不存在",
    )
    # 作用域自洽：路径上的授权必须**就是**该成果所属的授权，否则 A 的授权能发布 B 的数据
    artifact = art.get_artifact(db, data.artifact_id)
    if int(artifact["entrustment_id"]) != int(entrustment_id):
        raise not_found("成果不存在")

    payload = {"entrustment_id": entrustment_id, **data.model_dump(mode="json")}

    def _business() -> dict[str, Any]:
        """发布 → **经理投影** → 响应。

        ⚠️ 不能把服务层的原始 dict 直接喂给响应模型：服务层用的是内部键名
        （`snapshot`），而响应模型要的是投影后的键（`customer_snapshot` / `source_gate`）。
        键名对不上时 pydantic **不报错**，而是静默用默认值 —— 表现为"发布成功、
        客户快照却是空的"，而在库里那份快照其实好好存着。**这类静默降级只能靠
        断言响应里真的有快照来发现。**
        """
        created = svc.release_offer(
            db,
            artifact_id=data.artifact_id,
            revision_no=data.revision_no,
            authorized_attachment_ids=data.authorized_attachment_ids,
            note=data.note,
            actor_user_id=int(user.id),
        )
        projected = svc.project_release_for_manager(db, created, response=None)
        projected["superseded_release_ids"] = created.get("superseded_release_ids") or []
        return sm.offer_release_created_out(projected).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_RELEASE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.get(
    "/entrustments/{entrustment_id}/offer-releases",
    summary="该授权的发布记录（按调用者身份走不同投影）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_entrustment_releases(
    entrustment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """一条路径，两个投影 —— 判据与 `GET /offer-releases/{id}` **逐字一致**。

    ⚠️ 这里曾经是**恒回经理投影**的。它不算越权（货主本人当然读得到自己的发布），
    但算**投影泄漏**：`project_release_for_manager` 里带着 `data_origin.basis`
    （`job_id` 与 `job_mocked`）、`source_gate` 的待核验明细与 `released_by`/
    `close_reason` —— 那些是内部台账，客户不该拿到。六机制里"客户白名单投影
    **不得先返回前端再隐藏**"约束的正是服务端这一刻的取舍：前端不显示不等于没返回。

    为什么不是"货主干脆 404"：客户在**自己那一屏**上需要能看到"这条委托下发过什么"，
    否则客户侧的入口只剩 `/my-offer-releases` 一张全量列表，页面拿它就分不出单。
    因此与详情端点同一条规则：先判"是不是客户本人"，是 ⇒ **客户投影**（只含冻结内容），
    不是 ⇒ 按委托单可见性判定（经理）⇒ **经理投影**；都不可见才 404。
    """
    entrustment = load_entrustment(db, entrustment_id)
    if entrustment is None:
        raise not_found("委托授权不存在")
    rows = svc.list_releases(db, entrustment_id=entrustment_id)

    # ⚠️ 顺序是有意的（同 `get_offer_release`）：先判"是不是客户本人"。同一个账号既是
    #    组织经理又是货主时，在**这一屏**上他就是客户 —— 按具体委托归属判断。
    if int(user.id) == int(entrustment["entrust_user_id"]):
        mine = [r for r in rows if int(user.id) == int(r["customer_user_id"])]
        # 变量名**有意与下面那一支不同**（`cust_items` / `mgr_items`）：同名会让 mypy
        # 把两支的元素类型统一成一个并集，然后在第二支上如实报 arg-type ——
        # 那个报错是对的，它说明"同一个名字承担了两种投影"，而这在运行期看不出来。
        cust_items = [
            sm.offer_release_customer_out(
                svc.project_release_for_customer(
                    r, response=svc.response_of(db, release_id=r["release_id"])
                )
            )
            for r in mine
        ]
        return sm.OfferReleaseCustomerListOut(total=len(cust_items), items=cust_items).model_dump(
            mode="json"
        )

    assert_can_view_entrustment(db, user_id=int(user.id), entrustment=entrustment)
    mgr_items = [
        sm.offer_release_out(
            svc.project_release_for_manager(
                db, r, response=svc.response_of(db, release_id=r["release_id"])
            )
        )
        for r in rows
    ]
    return sm.OfferReleaseListOut(total=len(mgr_items), items=mgr_items).model_dump(mode="json")


# ── 客户：我的发布 ──────────────────────────────────────────────────────────


@router.get(
    "/my-offer-releases",
    response_model=sm.OfferReleaseCustomerListOut,
    summary="我（货主本人）收到的发布（客户视角，只含冻结内容）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_my_offer_releases(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """客户入口：只列 `customer_user_id == 我` 的发布记录。

    归属由服务端从登录身份推导（不接受客户端传入 `customer_user_id`）⇒ 调用方无从越权。
    """
    rows = svc.list_releases(db, customer_user_id=int(user.id))
    items = [
        svc.project_release_for_customer(
            r, response=svc.response_of(db, release_id=r["release_id"])
        )
        for r in rows
    ]
    return sm.OfferReleaseCustomerListOut(
        total=len(items), items=[sm.offer_release_customer_out(i) for i in items]
    )


@router.get(
    "/offer-releases/{release_id}",
    summary="一条发布记录（按调用者身份走不同投影）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_offer_release(
    release_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """**两条通道一个路径**，由服务端按身份选投影。

    * 调用者就是该委托货主 ⇒ **客户投影**（只有冻结内容）；
    * 否则按委托单可见性判定（经理）⇒ **经理投影**；不可见则 404。

    ⚠️ 顺序是有意的：先判"是不是客户本人"。同一个账号既是组织经理又是货主时，
    在**这一屏**上他就是客户 —— 裁定三说的"按具体委托归属判断"就是这个意思。
    """
    release = svc.get_release(db, release_id=release_id)
    if release is None:
        raise not_found("发布记录不存在")
    response = svc.response_of(db, release_id=release_id)
    if int(user.id) == int(release["customer_user_id"]):
        return sm.offer_release_customer_out(
            svc.project_release_for_customer(release, response=response)
        ).model_dump(mode="json")
    assignment = load_assignment(db, release["assignment_id"])
    if assignment is None:
        raise not_found("发布记录不存在")
    assert_can_view_assignment(db, user_id=int(user.id), assignment=assignment)
    return sm.offer_release_out(
        svc.project_release_for_manager(db, release, response=response)
    ).model_dump(mode="json")


# ── 客户：按发布清单下载附件（BP-03 第 10 条）──────────────────────────────


@router.get(
    "/offer-releases/{release_id}/attachments/{attachment_id}/download",
    summary="按发布冻结的授权清单下载附件（清单之外一律 404，客户唯一出口）",
    dependencies=[Depends(require_entrust_enabled)],
)
def download_offer_attachment(
    release_id: int,
    attachment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """**这条端点的判据只有一个**：发布时冻结的 `authorized_attachment_ids`。

    刻意**不**复用 `attachments_api.load_visible_attachment`：那个函数按"对委托授权
    有可见性"放行 —— 而客户对该授权下的**所有**附件都有可见性（他本来就是参与方），
    复用它等于把内部报价底稿、核验材料一起开给客户。合同要求的是**裁剪视图**：
    "客户能看到哪些附件"由**发布那一刻的授权**决定，不由事后可见性决定。

    经理也可以调用这条端点，看到的是**客户视角能看到的那几份**（便于核对
    "客户到底拿得到什么"）。经理要下内部附件走 `/attachments/{id}/download`。
    """
    release = svc.get_release(db, release_id=release_id)
    if release is None:
        raise not_found("发布记录不存在")

    # 可见性：客户本人，或该委托单的可见者（经理）。两者再一起过同一份白名单。
    if int(user.id) != int(release["customer_user_id"]):
        assignment = load_assignment(db, release["assignment_id"])
        if assignment is None:
            raise not_found("发布记录不存在")
        try:
            assert_can_view_assignment(db, user_id=int(user.id), assignment=assignment)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise
            raise not_found("发布记录不存在") from exc

    if int(attachment_id) not in set(release["authorized_attachment_ids"]):
        # 404（不是 403）：白名单外的附件对**这条通道**而言就是"不存在"。
        # 用 403 会告诉调用方"确实有这份文件、只是不给你" —— 那等于泄漏内部附件的存在性。
        raise not_found("附件不存在")

    attachment = att_svc.get_attachment(db, attachment_id)
    if attachment is None:
        raise not_found("附件不存在")
    # 白名单是发布时写下的 id 列表；若附件后来被挪到别的委托（数据异常），
    # 必须拒绝而不是照旧放行 —— 否则一处写错的清单会变成一条跨单泄漏。
    same_entrustment = (
        release["entrustment_id"] is not None
        and attachment["entrustment_id"] is not None
        and int(attachment["entrustment_id"]) == int(release["entrustment_id"])
    )
    same_assignment = attachment["assignment_id"] is not None and int(
        attachment["assignment_id"]
    ) == int(release["assignment_id"])
    if not (same_entrustment or same_assignment):
        raise not_found("附件不存在")

    try:
        path = att_svc.resolve_path(attachment)
    except att_svc.AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        path=path,
        media_type=str(attachment["content_type"]),
        filename=str(attachment["filename"]),
    )


# ── 客户：响应 ──────────────────────────────────────────────────────────────


@router.post(
    "/offer-releases/{release_id}/responses",
    response_model=sm.OfferResponseOut,
    summary="客户接受／拒绝该已发布版本（只有该委托货主本人，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_offer_response(
    release_id: int,
    data: sm.OfferResponseCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """D1-07 的正例面。三类负例走同一入口并各自失败：

    * **经理冒充客户** ⇒ 403（他有权看，但无权替客户确认）；
    * **未发布 / 已被取代 / 已撤回**的版本 ⇒ 409；
    * **同一发布重复响应** ⇒ 同键重放返回首次结果；不同键被 `UNIQUE(release_id)` 挡住 ⇒ 409。
    """
    key = guard_or_400(idempotency_key)
    release = svc.get_release(db, release_id=release_id)
    if release is None:
        raise not_found("发布记录不存在")
    if int(user.id) != int(release["customer_user_id"]):
        # 先分清"看不见"（404）与"看得见但无权替客户决定"（403）
        assignment = load_assignment(db, release["assignment_id"])
        if assignment is None:
            raise not_found("发布记录不存在")
        try:
            assert_can_view_assignment(db, user_id=int(user.id), assignment=assignment)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise
            raise not_found("发布记录不存在") from exc
        raise HTTPException(
            status_code=403,
            detail="只有该委托的货主本人可以接受／拒绝这份发布 —— 组织（经理）权限不能代替客户确认",
        )
    payload = {"release_id": release_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_RESPOND,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.offer_response_out(
            svc.respond_to_offer(
                db,
                release_id=release_id,
                decision=data.decision,
                note=data.note,
                actor_user_id=int(user.id),
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.post(
    "/offer-releases/{release_id}/withdraw",
    response_model=sm.OfferReleaseOut,
    summary="撤回发布（经理人，理由必填，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def withdraw_offer_release(
    release_id: int,
    data: sm.OfferWithdrawIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """显式撤回。**已被客户响应的发布不能撤回**（接受事实永久保留，要改只能发新版本）。"""
    key = guard_or_400(idempotency_key)
    release = svc.get_release(db, release_id=release_id)
    if release is None:
        raise not_found("发布记录不存在")
    entrustment = load_entrustment(db, int(release["entrustment_id"] or 0))
    if entrustment is None:
        raise not_found("发布记录不存在")
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_PUBLISH,
        entrustment=entrustment,
        detail="发布记录不存在",
    )
    payload = {"release_id": release_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_WITHDRAW,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.offer_release_out(
            svc.project_release_for_manager(
                db,
                svc.withdraw_release(
                    db, release_id=release_id, reason=data.reason, actor_user_id=int(user.id)
                ),
                response=svc.response_of(db, release_id=release_id),
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


# ── 经理：来源台账（发布前门槛的读写口）────────────────────────────────────


@router.get(
    "/artifacts/{artifact_id}/source-checks",
    response_model=sm.SourceCheckListOut,
    summary="成果的来源核验台账与门槛状态（经理视角）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_artifact_source_checks(
    artifact_id: int,
    revision_no: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """让界面能回答"这份报价还差哪几条来源没核" —— 只说"不能发布"是没法干活的。"""
    _artifact, entrustment, _aid = _artifact_scope(db, artifact_id)
    assert_can_view_entrustment(db, user_id=int(user.id), entrustment=entrustment)
    items = svc.list_source_checks(db, artifact_id=artifact_id, revision_no=revision_no)
    rev = revision_no if revision_no is not None else _latest_revision_no(db, artifact_id)
    gate = svc.source_gate(db, artifact_id=artifact_id, revision_no=rev)
    return sm.SourceCheckListOut(
        artifact_id=artifact_id,
        total=len(items),
        items=[sm.source_check_out(i) for i in items],
        gate=sm.SourceGateOut.model_validate(gate),
    )


def _latest_revision_no(db: Session, artifact_id: int) -> int:
    revisions = art.list_revisions(db, artifact_id)
    return int(revisions[-1]["revision_no"]) if revisions else 0


@router.post(
    "/artifacts/{artifact_id}/source-checks",
    response_model=sm.SourceCheckListOut,
    summary="登记一条来源核验（经理人，必须写明依据，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_artifact_source_check(
    artifact_id: int,
    data: sm.SourceCheckCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """「核验要留**对象 + 记录**，不是一个勾选」—— 这条端点就是「记录」的入口。

    * 对象（`source_kind` / `source_ref`）必须与**服务端采纳时写入的声明**一致，
      否则记了也不算数（`source_gate` 只认声明过的那几条）；
    * **依据必填**（`method`）：无依据的核验等于没核。
    """
    key = guard_or_400(idempotency_key)
    _artifact, entrustment, _aid = _artifact_scope(db, artifact_id)
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_PUBLISH,
        entrustment=entrustment,
        detail="成果不存在",
    )
    payload = {"artifact_id": artifact_id, **data.model_dump(mode="json")}

    def _business() -> dict[str, Any]:
        svc.record_source_check(
            db,
            artifact_id=artifact_id,
            revision_no=data.revision_no,
            source_kind=data.source_kind,
            source_ref=data.source_ref,
            state=data.state,
            method=data.method,
            note=data.note,
            actor_user_id=int(user.id),
        )
        items = svc.list_source_checks(db, artifact_id=artifact_id, revision_no=data.revision_no)
        gate = svc.source_gate(db, artifact_id=artifact_id, revision_no=data.revision_no)
        return sm.SourceCheckListOut(
            artifact_id=artifact_id,
            total=len(items),
            items=[sm.source_check_out(i) for i in items],
            gate=sm.SourceGateOut.model_validate(gate),
        ).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_SOURCE_CHECK,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


__all__ = ["router"]
