"""运力确认与有效期端点（`/api/v1/entrust` 下；BP-03 第 3 条 / D1-06 / §10.1 第 5 步）。

六条端点，**全部只有组织侧一个通道**：

| 端点 | 做什么 |
| --- | --- |
| `POST /assignments/{id}/capacity-candidates` | 登记一条候选运力事实（**不是**确认；幂等） |
| `GET  /assignments/{id}/capacity-candidates` | 候选清单（第 2 条"两家可比"要看的运力/单价口径/有效期/证据） |
| `POST /assignments/{id}/capacity-confirmations` | 按确定性规则判定 ⇒ 通过才产出采购确认成果（幂等） |
| `GET  /assignments/{id}/capacity-confirmations` | 该委托的确认清单 |
| `GET  /capacity-confirmations/{id}` | 确认详情：冻结输入 + **逐规则判定** |
| `GET  /capacity-confirmations/{id}/recheck` | **只读复算**：这条确认现在还成立吗（不改任何行） |

⚠️ 为什么整组都**不给货主本人放行**
------------------------------------
`authz.assert_can_view_assignment` / `assert_can_view_entrustment` 都有"是货主本人
就直接通过"的旁路。这一组**一条都不用它**，一律走 `assert_can_view_org`（**没有**货主旁路）：

* 候选运力带**承运人**与**供应商单价**（`rate` / `rate_unit` / `currency`）；
* 确认记录带 `agreed_amount` / `currency` / `supplier` —— 注册表已把这三个字段
  标为 `internal_fields`；
* 确认详情里的**逐规则判定**写着需求量、运力、缺口这些内部比较过程。

把任何一条交给客户，就是把内部成本口径送出去。**客户不是没有渠道了解商业承诺** ——
他看到的是**对客报价的冻结快照**（`/my-offer-releases`），那份内容由发布那一刻的
服务端白名单投影决定。这与 §6.3 白名单下载、以及合同派生读取端点不给货主放行
是同一条纪律：**"客户能看到什么"只能有一个判据**，而那个判据在发布通路上。

合同原文的落点
--------------
* `Confirmation requires an authorized action` ⇒ 写端点走
  `assert_can_write_entrustment`（组织成员 → 授权 active → 按货主作用域有权限）；
* `and identified evidence` ⇒ `evidence` 规则要求**类别 + 引用**都属于候选行；
* `expired or unsuitable resources cannot be confirmed` ⇒ `validity` 与 `capacity`
  两条规则，判定基准日显式落在确认行上；
* `Record selection separately from resource confirmation` ⇒ 这两个动作**没有共用端点**：
  选中是对客/内部成果里的一个字段，确认是本文件里的一条命令，且确认**只读候选行的事实**。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import artifacts as art
from app.modules.entrust import capacity as svc
from app.modules.entrust import schemas as sm
from app.modules.entrust._http import guard_or_400, require_entrust_enabled, run_write
from app.modules.entrust.access import (
    PERM_QUOTE_CREATE,
    AccessDeniedError,
    find_active_entrustments,
    resolve_context,
)
from app.modules.entrust.authz import (
    assert_can_view_org,
    assert_can_write_entrustment,
    assert_org_member,
    load_assignment,
    load_entrustment,
    not_found,
)

router = APIRouter()

_SCOPE_CANDIDATE = "entrust:capacity:candidate"
_SCOPE_CONFIRM = "entrust:capacity:confirm"


def _map_errors(exc: Exception) -> HTTPException | None:
    if isinstance(exc, svc.CapacityNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.CapacityStateError):
        # 409 是"当前事实不允许"，且**带上已存在的那条记录 id** ——
        # "已经确认过了"是一句没用的拒绝，客户端需要能直接去读它。
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "existing_confirmation_id": exc.existing_confirmation_id,
                "existing_artifact_id": exc.existing_artifact_id,
            },
        )
    if isinstance(exc, svc.CapacityRuleError):
        # ⚠️ **全部**判定都回（通过的也在内），不是只回没过的那几条：
        #    "只差一条"的拒绝会让人改完再撞下一条；而且通过的规则也带比较值，
        #    那是"这条规则确实跑过"的证据。
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "rule_checks": [
                    {
                        "rule_code": e.rule_code,
                        "seq": seq,
                        "outcome": e.outcome,
                        "detail": e.detail,
                    }
                    for seq, e in enumerate(exc.evaluations, start=1)
                ],
            },
        )
    if isinstance(exc, art.ArtifactPayloadError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, svc.CapacityError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AccessDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    # ⚠️ 刻意**不**接 `svc.CapacityInvariantError`：那是程序性错误（闸门发现规则没跑全），
    #    必须是一个响亮的 500，而不是"你的请求有问题"。
    return None


def _load_org_scope(db: Session, assignment_id: int) -> dict[str, Any]:
    """读委托单并**要求它有服务经营主体**（否则无从判组织成员资格）。"""
    assignment = load_assignment(db, assignment_id)
    if assignment is None or assignment["org_id"] is None:
        # 没选定组织 ⇒ 这个委托在本支线里不存在：没有组织就没有"组织侧"这个视角，
        # 也没有可解析的授权。报 404 而不是 400 —— 不告诉调用方"它其实在，只是缺个字段"。
        raise not_found("委托不存在")
    return assignment


def _write_scope(
    db: Session, *, user_id: int, assignment_id: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """写路径：定位唯一的生效授权并判定写权限。

    顺序**有意**排成这样（每一档的状态码都不一样，且都不泄漏存在性）：

    1. 组织成员资格（`assert_org_member`）⇒ 非成员 **404**；
    2. `(货主, 组织)` 之间必须**恰好一条**生效授权 ⇒ 否则 **409**。
       多于一条时**不猜**（与 `get_session_context` 同一条理由：猜错会把数据挂到
       另一条授权上，边界随之改变而界面上看不出来）。
       这一步排在成员资格之后，所以 409 只可能被组织成员看到 —— 非成员探测
       委托 id 只会拿到 404，拿不到"这个 id 存在"。
    3. `assert_can_write_entrustment` ⇒ 授权失效 409、缺权限 403。
    """
    assignment = _load_org_scope(db, assignment_id)
    org_id = int(assignment["org_id"])
    owner_user_id = int(assignment["owner_user_id"])

    assert_org_member(resolve_context(db, user_id=user_id), org_id=org_id, detail="委托不存在")

    matches = find_active_entrustments(db, org_id=org_id, owner_user_id=owner_user_id)
    if len(matches) != 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "这单的货主与组织之间没有唯一的生效委托授权，无法执行该操作（"
                + ("没有生效授权" if not matches else "存在多条生效授权，需先收敛为一条")
                + "）—— 运力候选与采购确认都要挂在明确的授权上"
            ),
        )
    entrustment = load_entrustment(db, matches[0])
    if entrustment is None:
        raise not_found("委托不存在")
    assert_can_write_entrustment(
        db,
        user_id=user_id,
        permission=PERM_QUOTE_CREATE,
        entrustment=entrustment,
        detail="委托不存在",
    )
    return assignment, entrustment


def _assert_read_scope(db: Session, *, user_id: int, assignment_id: int) -> dict[str, Any]:
    """读路径：组织成员 + `entrust:view`，**没有货主旁路**（理由见模块文档）。"""
    assignment = _load_org_scope(db, assignment_id)
    assert_can_view_org(db, user_id=user_id, org_id=int(assignment["org_id"]), detail="委托不存在")
    return assignment


@router.post(
    "/assignments/{assignment_id}/capacity-candidates",
    response_model=sm.CapacityCandidateOut,
    summary="登记一条候选运力（经理人，幂等；这不等于确认运力）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_capacity_candidate(
    assignment_id: int,
    data: sm.CapacityCandidateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """登记候选运力事实（第 2 条要比的吨位/单价口径/有效期/证据都落在这一行）。

    ⚠️ 本端点**不确认**任何东西。确认是下一条命令，且要被逐规则判定。
    把两者合成一个动作，就等于让"选中一条报价"直接产生"已确认运力" ——
    而合同 BP-03 的 Exit evidence 明确写着
    `A chosen quotation alone does not create confirmed capacity.`
    """
    key = guard_or_400(idempotency_key)
    _assignment, _entrustment = _write_scope(db, user_id=int(user.id), assignment_id=assignment_id)
    # `mode="json"`：幂等键的载荷要做哈希，`Decimal` / `date` 都不能直接序列化。
    payload = {"assignment_id": assignment_id, **data.model_dump(mode="json")}

    def _business() -> dict[str, Any]:
        created = svc.record_candidate(
            db,
            assignment_id=assignment_id,
            actor_user_id=int(user.id),
            carrier=data.carrier,
            capacity_tonnes=data.capacity_tonnes,
            vessel_count=data.vessel_count,
            allows_partial_load=data.allows_partial_load,
            leg_id=data.leg_id,
            vessel_name=data.vessel_name,
            rate=data.rate,
            rate_unit=data.rate_unit,
            currency=data.currency,
            valid_until=data.valid_until,
            evidence_kind=data.evidence_kind,
            evidence_ref=data.evidence_ref,
        )
        return sm.capacity_candidate_out(created).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_CANDIDATE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.get(
    "/assignments/{assignment_id}/capacity-candidates",
    response_model=list[sm.CapacityCandidateOut],
    summary="候选运力清单（经理视角）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_capacity_candidates(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """该委托的候选运力，按登记顺序。含 `status`（`confirmed` 由确认命令写）。

    不按吨位排序：哪一种排序更"对"是**方案判断**；这里要的是稳定、可复现的清单。
    """
    _assert_read_scope(db, user_id=int(user.id), assignment_id=assignment_id)
    return [
        sm.capacity_candidate_out(c).model_dump(mode="json")
        for c in svc.list_candidates(db, assignment_id=assignment_id)
    ]


@router.post(
    "/assignments/{assignment_id}/capacity-confirmations",
    response_model=sm.CapacityConfirmationCreatedOut,
    summary="确认运力（经理人，幂等；过期/不适用/无证据一律拒绝）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_capacity_confirmation(
    assignment_id: int,
    data: sm.CapacityConfirmIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """确认运力：跑规则闸门 → 通过才产出 `procurement_confirm` 成果 + 确认记录。

    **请求体只带 `candidate_id` 与 `agreed_scope`**：一切事实（承运人、吨位、船数、
    是否拆批、单价、有效期、证据）都从候选行读。让请求体带这些值会造出
    "确认的内容"与"候选运力"可以不一致的状态，而那条不一致在界面上看不出来 ——
    于是"确认"退化成一次自述。

    判定不通过 ⇒ **409**，响应体里带**逐条**判定（含通过的），不是一句"不适用"。
    """
    key = guard_or_400(idempotency_key)
    _assignment, entrustment = _write_scope(db, user_id=int(user.id), assignment_id=assignment_id)
    payload = {
        "assignment_id": assignment_id,
        "candidate_id": data.candidate_id,
        "agreed_scope": data.agreed_scope,
        "note": data.note,
    }

    def _business() -> dict[str, Any]:
        created = svc.confirm_capacity(
            db,
            assignment_id=assignment_id,
            candidate_id=data.candidate_id,
            actor_user_id=int(user.id),
            agreed_scope=data.agreed_scope,
            entrustment_id=int(entrustment["id"]),
            note=data.note,
        )
        projected = svc.project_confirmation(db, created)
        projected["rule_check_count"] = int(created.get("rule_check_count") or 0)
        return sm.capacity_confirmation_created_out(projected).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_CONFIRM,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.get(
    "/assignments/{assignment_id}/capacity-confirmations",
    response_model=list[sm.CapacityConfirmationOut],
    summary="该委托的运力确认清单（经理视角）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_capacity_confirmations(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """该委托已作出的运力确认（含逐规则判定）。

    清单里**带**逐规则判定而不是只回 id：确认的核心价值就是"凭什么判为适用"，
    让调用方为每条确认再发一次请求，只会让界面选择少拿那一块。
    """
    _assert_read_scope(db, user_id=int(user.id), assignment_id=assignment_id)
    return [
        sm.capacity_confirmation_out(svc.project_confirmation(db, c)).model_dump(mode="json")
        for c in svc.list_confirmations(db, assignment_id=assignment_id)
    ]


@router.get(
    "/capacity-confirmations/{confirmation_id}",
    response_model=sm.CapacityConfirmationOut,
    summary="运力确认详情（冻结输入 + 逐规则判定；经理视角）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_capacity_confirmation(
    confirmation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """确认详情。**不存在 ⇒ 404**（不回空壳：空壳会让"没做过确认"与
    "做了一条内容为空的确认"长得一模一样，而后者才是本切片最该被发现的缺陷）。"""
    confirmation = svc.get_confirmation(db, confirmation_id=confirmation_id)
    if confirmation is None:
        raise not_found("运力确认记录不存在")
    _assert_read_scope(db, user_id=int(user.id), assignment_id=int(confirmation["assignment_id"]))
    return sm.capacity_confirmation_out(svc.project_confirmation(db, confirmation)).model_dump(
        mode="json"
    )


@router.get(
    "/capacity-confirmations/{confirmation_id}/recheck",
    response_model=sm.CapacityRecheckOut,
    summary="只读复算：这条运力确认现在还成立吗（不改任何行）",
    dependencies=[Depends(require_entrust_enabled)],
)
def recheck_capacity_confirmation(
    confirmation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """用**当前事实**重跑同一套规则。**只读** —— 正式的重做属 S4 的变更流程。

    这个端点存在的理由就是 D1-09 的那句话：`makes the 900-tonne candidate
    unsuitable`。它的全部内容就是同一套规则换一组输入之后不通过了；把它做成
    只读复算，S4 就不必再写第二份"适用性"定义（两个定义必然有一天结论不一致）。
    """
    confirmation = svc.get_confirmation(db, confirmation_id=confirmation_id)
    if confirmation is None:
        raise not_found("运力确认记录不存在")
    _assert_read_scope(db, user_id=int(user.id), assignment_id=int(confirmation["assignment_id"]))
    result = svc.recheck_confirmation(db, confirmation_id=confirmation_id)
    return sm.capacity_recheck_out(result).model_dump(mode="json")
