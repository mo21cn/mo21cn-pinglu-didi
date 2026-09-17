"""航段命令的 HTTP 面：建段 / 改段（留版本）/ 版本历史。

为什么这三条**不**与运力那一组共用判据（这是本模块最需要说清的一件事）
----------------------------------------------------------------------
`capacity_api` 整组走 `assert_can_view_org` / `assert_can_write_entrustment`
（**没有**货主旁路），因为那边通道上跑的是**内部成本口径**：承运人、供应商单价、
需求量与缺口、逐规则判定的依据。货主一律 404。

航段不同：它是**客户自己交进来的方案事实**（起讫路线）。这条通道上没有任何内部成本口径,
所以本组按**参与方**判定 —— 与紧邻的 `GET /assignments/{id}/plan`（读模型）**同一取向**：

    判据是"这条通道上有没有内部信息"，不是"是不是客户"。

HO 2026-09-17 的口径进一步把它定死为「**任意验收者/测试者**都能建段」：

* 落地＝`assert_can_view_assignment`（该委托的**货主本人**，或**所属组织的 active 成员**）；
* **不新增权限常量、不设角色门槛** —— 演示的两个身份（货主 `seed-shipper` /
  经理 `seed-owner`）**都能**建段，验收与测试期间不必先配权限；
* ⛔ 但**不等于"任何登录用户"**：组织边界与货主归属仍是硬界，
  非参与方一律 **404**（不是 403 —— 不泄漏"这张单存在"）。
  「不设门槛」说的是"参与方内部不再细分角色"，不是"取消边界"。

`assignment.org_id` 为空也**不拦**：那是草稿的常态（组织在**提交时**才落上去），
而航段是委托自身的方案事实 —— 货主给自己的草稿填起讫路线不该被"还没选组织"挡住。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import legs as svc
from app.modules.entrust import schemas as sm
from app.modules.entrust._http import guard_or_400, require_entrust_enabled, run_write
from app.modules.entrust.authz import assert_can_view_assignment, load_assignment, not_found

router = APIRouter()

_SCOPE_LEG_CREATE = "entrust:leg:create"
_SCOPE_LEG_UPDATE = "entrust:leg:update"


def _map_errors(exc: Exception) -> HTTPException | None:
    """领域异常 → HTTP。

    * `LegNotFoundError` ⇒ 404（"不存在"与"不属于这张单"**不区分**）；
    * `LegStateError` ⇒ 409（`seq` 撞号）；
    * `LegError`（含上面两个）⇒ 400。

    ⚠️ 顺序必须是"更具体的先判"：`LegStateError` / `LegNotFoundError` 都是 `LegError`
    的子类，先判基类会把 409/404 一起吞成 400 —— 而这三档的处置完全不同
    （400 改请求、404 换对象、409 去改那一段）。
    """
    if isinstance(exc, svc.LegNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.LegStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, svc.LegError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


def _assert_party(db: Session, *, user_id: int, assignment_id: int) -> dict[str, Any]:
    """参与方判据（读与写共用同一个 —— 理由见模块文档）。

    非参与方拿 **404**：这个端点不泄漏"某张委托单存在"。
    """
    assignment = load_assignment(db, assignment_id)
    if assignment is None:
        raise not_found("委托不存在")
    assert_can_view_assignment(db, user_id=user_id, assignment=assignment)
    return assignment


@router.post(
    "/assignments/{assignment_id}/legs",
    response_model=sm.LegOut,
    summary="建一段航段（参与方，幂等；不强制公–水–公）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_leg(
    assignment_id: int,
    data: sm.LegCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """建段。同时写下该段的**第 1 版**历史（`change_kind=created`）。

    ⚠️ 本端点**不**校验"必须三段 / 必须公路—内河—公路"（HO 口径：不强制）。
    只做结构完整性：运输方式非空、起终点非空、`seq` ≥1 且委托内唯一。
    """
    key = guard_or_400(idempotency_key)
    _assert_party(db, user_id=int(user.id), assignment_id=assignment_id)
    payload = {"assignment_id": assignment_id, **data.model_dump(mode="json")}

    def _business() -> dict[str, Any]:
        leg = svc.create_leg(
            db,
            assignment_id=assignment_id,
            seq=data.seq,
            mode=data.mode,
            from_name=data.from_name,
            to_name=data.to_name,
            actor_user_id=int(user.id),
            change_note=data.change_note,
        )
        return sm.leg_out(leg).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_LEG_CREATE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.patch(
    "/assignments/{assignment_id}/legs/{leg_id}",
    response_model=sm.LegOut,
    summary="改一段航段（参与方，幂等；旧版本保留）",
    dependencies=[Depends(require_entrust_enabled)],
)
def update_leg(
    assignment_id: int,
    leg_id: int,
    data: sm.LegUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """改段。**旧版本不覆盖** —— 每改一次追加一版历史（`change_kind=updated`）。

    两处 400 是有意的（见 `legs.update_leg`）：一个字段都没传、或传了但与当前值相同。
    前者多半是请求组错了；后者不是一次改动 —— 两者都不该在历史里留一版。
    """
    key = guard_or_400(idempotency_key)
    _assert_party(db, user_id=int(user.id), assignment_id=assignment_id)
    payload = {
        "assignment_id": assignment_id,
        "leg_id": leg_id,
        **data.model_dump(mode="json"),
    }

    def _business() -> dict[str, Any]:
        leg = svc.update_leg(
            db,
            assignment_id=assignment_id,
            leg_id=leg_id,
            mode=data.mode,
            from_name=data.from_name,
            to_name=data.to_name,
            seq=data.seq,
            actor_user_id=int(user.id),
            change_note=data.change_note,
        )
        return sm.leg_out(leg).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_LEG_UPDATE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.get(
    "/assignments/{assignment_id}/legs/{leg_id}/revisions",
    response_model=list[sm.LegRevisionOut],
    summary="某一段航段的版本历史（参与方；改段留版本的读侧）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_leg_revisions(
    assignment_id: int,
    leg_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """按 `revision_no` 升序返回该段的**全部**历史版本。

    ⚠️ 空列表与 404 是两件事：这里**不会**返回空列表 —— 航段存在就至少有一版
    （建段即写第 1 版）。拿不到行时是 404（航段不属于这张单 / 不存在）。
    """
    _assert_party(db, user_id=int(user.id), assignment_id=assignment_id)
    try:
        rows = svc.list_leg_revisions(db, assignment_id=assignment_id, leg_id=leg_id)
    except svc.LegError as exc:
        # ⚠️ **读端点没有 `run_write` 那一层**，领域异常不会自己变成 HTTP ——
        # 少了这个转换，"航段不属于这张单"会漏成一个 **500**（而它应当是 404）。
        # 这一条是 `test_leg_of_another_assignment_is_404` 当场抓出来的：
        # 写端点走 `run_write(map_domain_error=...)` 没事，读端点直接炸。
        mapped = _map_errors(exc)
        if mapped is None:
            # 不可达（`LegError` 全族都在 `_map_errors` 里）；留着是为了"没接住就响亮地炸"，
            # 而不是被一句笼统的 400/500 吞掉。
            raise
        raise mapped from exc
    return [sm.leg_revision_out(r).model_dump(mode="json") for r in rows]
