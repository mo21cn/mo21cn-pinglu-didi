"""费用行端点（§10.1 第 10 步 / 合同 S4 段第 9–10 条）。

两条通道的判据**相反**，各自写明理由（技能 §11.4：判据是「这条通道上有没有内部信息」，
不是「是不是客户」）：

* **读**（`GET /assignments/{id}/charges`）：费用行带 `counterparty` / `basis`
  —— 那是**内部成本口径**（谁付谁、按什么算）⇒ 与运力那一组同口径，用
  `assert_can_view_org`，**货主本人也一律 404**（没有货主旁路）。
* **写**：`entrust:settlement:create`（本仓已有的权限码，不新造），
  经 `assert_can_write_entrustment` 判定 —— 权限按 **(组织, 货主)** 作用域解析
  （DR-0008），因此取**任意一条生效授权**来做判定即可，不必逐条试。

客户侧投影（"只看对客费用与证据白名单"）是**裁定 Q5 第 2 条**的要求，
它需要"哪些费用是对客的"这个口径 ⇒ **不在本切片**，留 S7-3（如实登记，不假装已支持）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import charges as svc
from app.modules.entrust import schemas as sm
from app.modules.entrust._http import guard_or_400, require_entrust_enabled, run_write
from app.modules.entrust.access import PERM_SETTLEMENT_CREATE, find_active_entrustments
from app.modules.entrust.authz import (
    assert_can_view_org,
    assert_can_write_entrustment,
    load_assignment,
    load_entrustment,
    not_found,
)

router = APIRouter()

_SCOPE_CHARGE_RECORD = "entrust:charge:record"
_SCOPE_CHARGE_CONFIRM = "entrust:charge:confirm"
_SCOPE_CHARGE_DISPUTE = "entrust:charge:dispute"
_SCOPE_CHARGE_RESOLVE = "entrust:charge:resolve"


def _map_errors(exc: Exception) -> HTTPException | None:
    """领域异常 → HTTP（404 不区分"不存在"与"无权"）。"""
    if isinstance(exc, svc.ChargeNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.ChargeStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, svc.ChargeError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


def _load_assignment_org(db: Session, assignment_id: int) -> dict[str, Any]:
    """读委托单并要求它有服务经营主体（没有组织就没有"组织侧"这个视角）。"""
    assignment = load_assignment(db, assignment_id)
    if assignment is None or assignment["org_id"] is None:
        raise not_found("委托不存在")
    return assignment


def _assert_can_write_charge(db: Session, *, user_id: int, assignment: dict[str, Any]) -> None:
    """费用写的权限：组织成员 ＋（组织, 货主）作用域上有 `settlement:create`。

    ⚠️ 只取**一条**生效授权来做判定：`assert_can_write_entrustment` 里真正判权限的是
    `context.can(permission, owner_user_id=…)`（**按货主作用域**，DR-0008），
    授权记录本身只提供 `org_id` 与 `status` 两个字段。逐条试是**另一套**（按记录判），
    与 DR-0008 的口径不一致，别改成那样。
    """
    org_id = int(assignment["org_id"])
    owner_id = int(assignment["owner_user_id"])
    ids = find_active_entrustments(db, org_id=org_id, owner_user_id=owner_id)
    if not ids:
        raise not_found("委托不存在")
    entrustment = load_entrustment(db, int(ids[0]))
    if entrustment is None:
        raise not_found("委托不存在")
    assert_can_write_entrustment(
        db,
        user_id=user_id,
        permission=PERM_SETTLEMENT_CREATE,
        entrustment=entrustment,
        detail="委托不存在",
    )


def _charge_scope(db: Session, charge_id: int) -> tuple[int, dict[str, Any]]:
    """按费用行反查它的委托并做写权限判定（作用域自洽：不许拿别单的费用行来操作）。"""
    charge = svc.get_charge(db, charge_id=charge_id)
    assignment = _load_assignment_org(db, int(charge["assignment_id"]))
    return int(charge["assignment_id"]), assignment


@router.get(
    "/assignments/{assignment_id}/charges",
    response_model=sm.ChargeListOut,
    summary="该委托的费用行与合计（经理视角；合计按币种 × 收付方向分开）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_charges(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """费用行 + 合计。

    ⚠️ **空列表是正常答复，不是 404**（多数委托还没有费用行）—— 与其他**集合**端点同判据。
    ⚠️ 合计**不跨币种相加**（裁定 Q1=C），且**争议行不计入**（合同 S4 段第 10 条）；
    每组同时给出 `counted_lines` / `excluded_lines`，好让"这个合计是怎么得到的"可被复核。
    """
    assignment = _load_assignment_org(db, assignment_id)
    assert_can_view_org(
        db, user_id=int(user.id), org_id=int(assignment["org_id"]), detail="委托不存在"
    )
    data = svc.summarize_charges(db, assignment_id=assignment_id)
    return sm.charge_list_out(
        {"items": svc.list_charges(db, assignment_id=assignment_id), **data}
    ).model_dump(mode="json")


@router.post(
    "/assignments/{assignment_id}/charges",
    response_model=sm.ChargeOut,
    summary="登记一条费用行（组织成员，需 settlement:create；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def record_charge(
    assignment_id: int,
    data: sm.ChargeCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """登记后状态是 `draft` —— **草稿不进合计**，要显式确认才算数。"""
    key = guard_or_400(idempotency_key)
    assignment = _load_assignment_org(db, assignment_id)
    _assert_can_write_charge(db, user_id=int(user.id), assignment=assignment)
    payload = {"assignment_id": assignment_id, **data.model_dump(mode="json")}

    def _business() -> dict[str, Any]:
        charge = svc.record_charge(
            db,
            assignment_id=assignment_id,
            direction=data.direction,
            charge_kind=data.charge_kind,
            amount=data.amount,
            currency=data.currency,
            basis=data.basis,
            quantity=data.quantity,
            unit=data.unit,
            counterparty=data.counterparty,
            actor_id=int(user.id),
        )
        return sm.charge_out(charge).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_CHARGE_RECORD,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.post(
    "/charges/{charge_id}/confirm",
    response_model=sm.ChargeOut,
    summary="确认费用行（draft → confirmed；确认后才进合计）",
    dependencies=[Depends(require_entrust_enabled)],
)
def confirm_charge(
    charge_id: int,
    data: sm.ChargeTransitionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    _, assignment = _charge_scope(db, charge_id)
    _assert_can_write_charge(db, user_id=int(user.id), assignment=assignment)
    payload = {"charge_id": charge_id, **data.model_dump(mode="json")}

    return run_write(
        db,
        scope=_SCOPE_CHARGE_CONFIRM,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.charge_out(
            svc.confirm_charge(db, charge_id=charge_id, expected_revision=data.expected_revision)
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.post(
    "/charges/{charge_id}/dispute",
    response_model=sm.ChargeOut,
    summary="对已确认的费用提争议（confirmed → disputed；争议行不进合计）",
    dependencies=[Depends(require_entrust_enabled)],
)
def dispute_charge(
    charge_id: int,
    data: sm.ChargeDisputeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """争议**必须写理由**：只标"有争议"而不说为什么，合计的差异无从复核。"""
    key = guard_or_400(idempotency_key)
    _, assignment = _charge_scope(db, charge_id)
    _assert_can_write_charge(db, user_id=int(user.id), assignment=assignment)
    payload = {"charge_id": charge_id, **data.model_dump(mode="json")}

    return run_write(
        db,
        scope=_SCOPE_CHARGE_DISPUTE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.charge_out(
            svc.dispute_charge(
                db,
                charge_id=charge_id,
                reason=data.reason,
                expected_revision=data.expected_revision,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.post(
    "/charges/{charge_id}/resolve",
    response_model=sm.ChargeOut,
    summary="处置争议（disputed → resolved|rejected；必须给依据与是否计入）",
    dependencies=[Depends(require_entrust_enabled)],
)
def resolve_charge(
    charge_id: int,
    data: sm.ChargeResolveIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """⛔ `counts_in_total` **必须显式给**：裁定 Q2=B —— `resolved` 一词决定不了是否计入。"""
    key = guard_or_400(idempotency_key)
    _, assignment = _charge_scope(db, charge_id)
    _assert_can_write_charge(db, user_id=int(user.id), assignment=assignment)
    payload = {"charge_id": charge_id, **data.model_dump(mode="json")}

    return run_write(
        db,
        scope=_SCOPE_CHARGE_RESOLVE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.charge_out(
            svc.resolve_charge(
                db,
                charge_id=charge_id,
                outcome=data.outcome,
                method=data.method,
                counts_in_total=bool(data.counts_in_total),
                final_amount=data.final_amount,
                evidence_ref=data.evidence_ref,
                actor_id=int(user.id),
                expected_revision=data.expected_revision,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )
