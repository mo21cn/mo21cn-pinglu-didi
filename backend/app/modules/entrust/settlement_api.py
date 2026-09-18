"""结算与收付依据端点（§10.1 第 11 步 / 合同 S4 段第 11 条；HO 0918-2 裁定 Q5）。

八条端点，**三条通道的判据各不相同**，每一条都写明理由
（技能 §11.4：判据是「这条通道上有没有内部信息／这个动作谁能做」，不是「是不是客户」）：

| 通道 | 端点 | 判据 | 为什么 |
| --- | --- | --- | --- |
| **内部** | 版本清单 / 版本详情 / `financial-status` | `assert_can_view_org`（**货主也 404**） | 版本带 `internal_total`（我们付给供应商的成本），派生读数还带内部成本余额 —— 与费用行同一口径，没有货主旁路 |
| **对客** | `GET /settlements/{id}/customer-view` | `assert_can_view_assignment`（**有货主旁路**） | 这条通道存在的意义就是"客户能看"；组织成员也放行是为了让经理能**预览客户看到的东西**（投影里没有任何内部字段） |
| **仅客户本人** | `POST /settlements/{id}/customer-confirm` | 只有 `owner_user_id` 能做 | 裁定 Q5 第 4 条：客户确认是独立事实，经理人不得代客户确认 |

⭐ 三处的差别**不是配置**，是三条不同的业务问题；把它们并成一条"是不是参与方"的判据，
就会出现"经理能替客户确认"或"客户看不到自己的结算单"。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import schemas as sm
from app.modules.entrust import settlement as svc
from app.modules.entrust._http import guard_or_400, require_entrust_enabled, run_write
from app.modules.entrust.access import PERM_SETTLEMENT_CREATE, find_active_entrustments
from app.modules.entrust.authz import (
    assert_can_view_assignment,
    assert_can_view_org,
    assert_can_write_entrustment,
    load_assignment,
    load_entrustment,
    not_found,
)

router = APIRouter()

_SCOPE_SETTLEMENT_CREATE = "entrust:settlement:create"
_SCOPE_SETTLEMENT_APPROVE = "entrust:settlement:approve"
_SCOPE_SETTLEMENT_CONFIRM = "entrust:settlement:confirm"
_SCOPE_SETTLEMENT_PAYMENT = "entrust:settlement:payment"


def _map_errors(exc: Exception) -> HTTPException | None:
    """领域异常 → HTTP 语义（404 不区分"不存在"与"无权看见"）。"""
    if isinstance(exc, svc.SettlementNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.SettlementCustomerOnlyError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, svc.SettlementStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, svc.SettlementError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


def _load_assignment_org(db: Session, assignment_id: int) -> dict[str, Any]:
    assignment = load_assignment(db, assignment_id)
    if assignment is None or assignment["org_id"] is None:
        raise not_found("委托不存在")
    return assignment


def _assert_can_write_settlement(db: Session, *, user_id: int, assignment: dict[str, Any]) -> None:
    """结算写的权限：组织成员 ＋（组织, 货主）作用域上有 `settlement:create`。

    复用费用行那一处的判据与权限码（不新造 `entrust:settlement:approve` 之类的码）：
    结算与费用是同一件财务动作的两半，拆成两个权限码只会让"谁能出结算、谁不能确认"
    变成一个没人说得清的问题。
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


# ── 内部通道（组织成员；货主本人也 404）────────────────────────────────────


@router.post(
    "/assignments/{assignment_id}/settlements",
    response_model=sm.SettlementOut,
    summary="按当前费用事实生成一个**新**结算版本（组织成员，需 settlement:create；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_settlement(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """生成 `draft` 版本，快照**此刻计入合计**的费用行。

    ⚠️ 费用变了就**再调一次**（产生 v2）—— 旧版本一个字不改，因此旧确认保留、
    但结构上替不了新版本过关。
    """
    key = guard_or_400(idempotency_key)
    assignment = _load_assignment_org(db, assignment_id)
    _assert_can_write_settlement(db, user_id=int(user.id), assignment=assignment)
    return run_write(
        db,
        scope=_SCOPE_SETTLEMENT_CREATE,
        key=key,
        actor_user_id=int(user.id),
        payload={"assignment_id": assignment_id},
        business=lambda: sm.settlement_out(
            svc.create_settlement(db, assignment_id=assignment_id, actor_id=int(user.id))
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.get(
    "/assignments/{assignment_id}/settlements",
    response_model=sm.SettlementListOut,
    summary="该委托的结算版本链（经理视角；含内部成本合计）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_settlements(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """⚠️ **空列表是正常答复，不是 404**（多数委托还没有结算版本）。"""
    assignment = _load_assignment_org(db, assignment_id)
    assert_can_view_org(
        db, user_id=int(user.id), org_id=int(assignment["org_id"]), detail="委托不存在"
    )
    items = svc.list_settlements(db, assignment_id=assignment_id)
    return sm.settlement_list_out(
        {
            "total": len(items),
            "applicable_settlement_id": items[-1]["settlement_id"] if items else None,
            "items": [sm.settlement_out(i) for i in items],
        }
    ).model_dump(mode="json")


@router.get(
    "/settlements/{settlement_id}",
    response_model=sm.SettlementOut,
    summary="结算版本详情（经理视角；含内部成本与快照行）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_settlement(
    settlement_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    settlement = svc.get_settlement(db, settlement_id=settlement_id)
    if settlement is None:
        raise not_found("结算版本不存在")
    assignment = _load_assignment_org(db, int(settlement["assignment_id"]))
    assert_can_view_org(
        db, user_id=int(user.id), org_id=int(assignment["org_id"]), detail="结算版本不存在"
    )
    return sm.settlement_out(settlement).model_dump(mode="json")


@router.post(
    "/settlements/{settlement_id}/approve",
    response_model=sm.SettlementOut,
    summary="内部确认结算版本（draft → approved；只能确认适用版本，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def approve_settlement(
    settlement_id: int,
    data: sm.SettlementApproveIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """只有**适用版本**（最大版本号）能被确认 —— 确认一个已被取代的版本没有业务含义。"""
    key = guard_or_400(idempotency_key)
    settlement = svc.get_settlement(db, settlement_id=settlement_id)
    if settlement is None:
        raise not_found("结算版本不存在")
    assignment = _load_assignment_org(db, int(settlement["assignment_id"]))
    _assert_can_write_settlement(db, user_id=int(user.id), assignment=assignment)
    payload = {"settlement_id": settlement_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_SETTLEMENT_APPROVE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.settlement_out(
            svc.approve_settlement(
                db,
                settlement_id=settlement_id,
                actor_id=int(user.id),
                expected_revision=data.expected_revision,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.post(
    "/settlements/{settlement_id}/payments",
    response_model=sm.SettlementPaymentOut,
    summary="记一条收付依据（只能挂在已确认的版本上；mode 恒为合成样本，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def record_payment(
    settlement_id: int,
    data: sm.SettlementPaymentIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """⛔ `mode` 不是入参（恒 `labeled_sample`）—— 不接真实支付、不宣称资金到账。"""
    key = guard_or_400(idempotency_key)
    settlement = svc.get_settlement(db, settlement_id=settlement_id)
    if settlement is None:
        raise not_found("结算版本不存在")
    assignment = _load_assignment_org(db, int(settlement["assignment_id"]))
    _assert_can_write_settlement(db, user_id=int(user.id), assignment=assignment)
    payload = {"settlement_id": settlement_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_SETTLEMENT_PAYMENT,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.settlement_payment_out(
            svc.record_payment(
                db,
                settlement_id=settlement_id,
                actor_id=int(user.id),
                direction=data.direction,
                amount=data.amount,
                ref=data.ref,
                currency=data.currency,
                occurred_at=data.occurred_at,
                note=data.note,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.get(
    "/assignments/{assignment_id}/financial-status",
    response_model=sm.FinancialStatusOut,
    summary="财务结案状态（派生；blockers 逐条给出未结成因）",
    dependencies=[Depends(require_entrust_enabled)],
)
def financial_status(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """§5.3.2 的四条判据逐条落成 `blockers`；四个都不成立才是 `settled`。

    ⚠️ 含内部成本余额 ⇒ **货主本人也 404**（与费用行读同一口径）。
    """
    assignment = _load_assignment_org(db, assignment_id)
    assert_can_view_org(
        db, user_id=int(user.id), org_id=int(assignment["org_id"]), detail="委托不存在"
    )
    return sm.FinancialStatusOut.model_validate(
        svc.derive_financial_status(db, assignment_id=assignment_id)
    ).model_dump(mode="json")


# ── 对客通道（货主本人 ＋ 组织成员；投影里没有内部字段）─────────────────────


@router.get(
    "/settlements/{settlement_id}/customer-view",
    response_model=sm.CustomerSettlementOut,
    summary="结算版本的对客投影（只出对客费用与白名单字段）",
    dependencies=[Depends(require_entrust_enabled)],
)
def customer_view(
    settlement_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """裁定 Q5 第 2 条：客户只看对客费用及证据白名单，**不暴露内部供应商成本**。

    ⚠️ 组织成员也放行 —— 让经理能**预览客户看到的东西**；这不会泄漏内部信息，
    因为这个投影里本来就没有内部字段（`internal_total` / `direction` 都不在其中）。
    """
    settlement = svc.get_settlement(db, settlement_id=settlement_id)
    if settlement is None:
        raise not_found("结算版本不存在")
    assignment = _load_assignment_org(db, int(settlement["assignment_id"]))
    assert_can_view_assignment(
        db, user_id=int(user.id), assignment=assignment, detail="结算版本不存在"
    )
    return sm.CustomerSettlementOut.model_validate(
        svc.customer_view(db, settlement_id=settlement_id)
    ).model_dump(mode="json")


# ── 仅客户本人 ──────────────────────────────────────────────────────────────


@router.post(
    "/settlements/{settlement_id}/customer-confirm",
    response_model=sm.SettlementOut,
    summary="客户确认该精确版本（**只有货主本人**能做；一版只确认一次，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def confirm_settlement(
    settlement_id: int,
    data: sm.SettlementConfirmIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """裁定 Q5 第 1、4 条：客户确认绑定**精确版本**，且是独立事实。

    ⛔ **经理人不得代客户确认**：组织成员（看得见这个版本）来做 ⇒ **403**；
    完全看不见的第三方 ⇒ **404**（不泄漏存在性）。两者的区别是刻意的 ——
    "看得见但这不是你能做的动作"与"这里没有这个东西"不是一回事。
    """
    key = guard_or_400(idempotency_key)
    settlement = svc.get_settlement(db, settlement_id=settlement_id)
    if settlement is None:
        raise not_found("结算版本不存在")
    assignment = _load_assignment_org(db, int(settlement["assignment_id"]))
    if int(user.id) != int(assignment["owner_user_id"]):
        # 先按"是否看得见"分流 404，再由服务层给出可读的 403（客户专属动作）。
        assert_can_view_org(
            db, user_id=int(user.id), org_id=int(assignment["org_id"]), detail="结算版本不存在"
        )
    payload = {"settlement_id": settlement_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_SETTLEMENT_CONFIRM,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: sm.settlement_out(
            svc.confirm_settlement(
                db,
                settlement_id=settlement_id,
                customer_user_id=int(user.id),
                decision=data.decision,
                note=data.note,
                expected_revision=data.expected_revision,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )
