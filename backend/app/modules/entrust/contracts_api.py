"""S3 合同派生端点（`/api/v1/entrust` 下；BP-03 第 8 条 / D1-08 / §10.1 第 7 步）。

两条端点，**都只有经理侧一个通道**：

| 端点 | 做什么 |
| --- | --- |
| `POST /offer-releases/{release_id}/contract` | 从**这条已接受发布**派生一份合同核对稿（幂等） |
| `GET  /offer-releases/{release_id}/contract` | 读派生关系 + **逐字段来源表**（D1-08 的 inspection 面） |
| `POST /contracts/{aid}/signature-evidence` | 就合同的某个版本记一条签署证据（幂等，§10.1 第 7 步后半） |
| `GET  /contracts/{aid}/signature-evidence` | 读该合同**全部版本**的证据清单 |

⚠️ 为什么读取端点**刻意不给货主本人放行**
------------------------------------------
`authz.assert_can_view_entrustment` / `assert_can_view_assignment` 都有一个旁路：
"是货主本人就直接通过"。本模块**不能**用它们 —— 字段来源表里是
`release:12@v3` / `leg:4` / `assignment:7` 这类**内部编号**，把它交给客户，
就是把内部审计信息当成客户可见内容送出去。

这正是上一轮修掉的那类**投影泄漏**（`GET /entrustments/{id}/offer-releases`
曾恒回经理投影，而货主本人被放行 ⇒ 客户拿到 `data_origin.basis` 与 `source_gate` 明细）。
那次是"两个入口判据不一致"；这一次是**一条通道本就不该有客户面**，
所以用 `assert_can_view_org`（**没有货主旁路**，见它的 docstring）：
不是组织成员一律 **404**。

客户要看合同，走的是**已有的发布通路**：把这份 `contract_review` 发布出去
（`POST /entrustments/{eid}/offer-releases`，`contract_review` 本就在
`registry.CUSTOMER_VISIBLE_TYPES` 里），客户在 `/my-offer-releases` 读**冻结快照**。
不为"客户看合同"新开通道 —— 否则"客户能看到什么"会有两个判据
（与 §6.3 白名单下载同一条纪律）。

权限为什么是 `entrust:quote:create`
-----------------------------------
派生 = **产出成果**（和 `POST /entrustments/{eid}/artifacts`、`POST /agent/jobs/{id}/adopt`
同一类动作），所以用创建权限。它**不是** `entrust:quote:publish`：发布是把成果发给客户的
另一个动作，两者可以不同的人做（经理拟定、负责人发布），合用一个权限会抹掉这条区分。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import artifacts as art
from app.modules.entrust import contracts as svc
from app.modules.entrust import offers as offers_svc
from app.modules.entrust import schemas as sm
from app.modules.entrust._http import guard_or_400, require_entrust_enabled, run_write
from app.modules.entrust.access import PERM_QUOTE_CREATE
from app.modules.entrust.authz import (
    assert_can_view_org,
    assert_can_write_entrustment,
    load_assignment,
    load_entrustment,
    not_found,
)

router = APIRouter()

_SCOPE_DERIVE = "entrust:contract:derive"


def _map_errors(exc: Exception) -> HTTPException | None:
    from app.modules.entrust.access import AccessDeniedError

    if isinstance(exc, svc.ContractNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.ContractStateError):
        # 409 是"当前事实不允许"，且**必须带上已存在的那份合同 id** ——
        # "已经有一份了"是一句没用的拒绝，客户端需要能直接去读它。
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "existing_contract_artifact_id": exc.existing_contract_artifact_id,
            },
        )
    # ⚠️ 子类必须**先于**父类判：`SignatureEvidenceNotFoundError` 与
    # `SignatureEvidenceStateError` 都是 `SignatureEvidenceError` 的子类，
    # 顺序写反会把 404/409 全压成 400 —— 客户端分不清"没有这个版本"与"填错了形态"。
    if isinstance(exc, svc.SignatureEvidenceNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, svc.SignatureEvidenceStateError):
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "existing_evidence_id": exc.existing_evidence_id,
            },
        )
    if isinstance(exc, svc.SignatureEvidenceError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, art.ArtifactNotFoundError):
        # 服务层 `art.get_artifact` 抛的"成果不存在" ⇒ 404，不让它落到 500
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, art.ArtifactPayloadError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, svc.ContractError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AccessDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    return None


def _load_release_scope(
    db: Session, release_id: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """读发布并定位它的授权与委托单；**三者不自洽一律 404**。

    为什么不自洽要当"不存在"而不是 400：这类不一致只可能来自数据问题
    （发布记录的 `entrustment_id` 与委托单的归属对不上），而把一条**内部不一致**
    的记录用 400 说出来，等于告诉调用方"这个 id 确实存在、只是有问题" ——
    对没权限的人不该有这个信息。与 `assert_org_member` 用 404 而非 403 同一条理由。
    """
    release = offers_svc.get_release(db, release_id=release_id)
    if release is None:
        raise not_found("发布记录不存在")
    entrustment_id = release["entrustment_id"]
    if entrustment_id is None:
        # 发布时没记下授权 ⇒ 没有授权链可判权限，不能靠"这个成果属于谁"去猜
        raise not_found("发布记录不存在")
    entrustment = load_entrustment(db, int(entrustment_id))
    if entrustment is None:
        raise not_found("发布记录不存在")
    assignment = load_assignment(db, int(release["assignment_id"]))
    if assignment is None:
        raise not_found("发布记录不存在")
    if int(assignment["owner_user_id"]) != int(entrustment["entrust_user_id"]) or (
        assignment["org_id"] is None or int(assignment["org_id"]) != int(entrustment["org_id"])
    ):
        raise not_found("发布记录不存在")
    return release, entrustment, assignment


@router.post(
    "/offer-releases/{release_id}/contract",
    response_model=sm.ContractDerivationCreatedOut,
    summary="从已接受的对客报价派生合同核对稿（经理人，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_contract_derivation(
    release_id: int,
    data: sm.ContractDeriveIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """把**这条已接受发布的冻结快照**派生成一份合同核对稿（BP-03 第 8 条）。

    三类前置各自给不同的状态码（详见 `contracts.derive_contract` 的表）：
    未响应 409、被拒绝 409、被接受的不是对客报价 400、已派生过 409（并回那份合同的 id）。
    """
    key = guard_or_400(idempotency_key)
    _release, entrustment, _assignment = _load_release_scope(db, release_id)
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_CREATE,
        entrustment=entrustment,
        detail="发布记录不存在",
    )
    note = data.note if data is not None else None
    payload = {"release_id": release_id, "note": note}

    def _business() -> dict[str, Any]:
        """派生 → **经理投影** → 响应。

        ⚠️ 走投影而不是把服务层的原始 dict 直接喂给响应模型：服务层用内部键名
        （`derivation_id` / `field_sources` 由投影补齐），键名对不上时 pydantic
        **不报错**、静默用默认值 ⇒ "派生成功但来源表是空的"，而库里其实有。
        这类静默降级只能靠断言响应里**真的有**来源行来发现。
        """
        created = svc.derive_contract(
            db, release_id=release_id, actor_user_id=int(user.id), note=note
        )
        projected = svc.project_derivation(db, created)
        projected["field_source_count"] = int(created.get("field_source_count") or 0)
        return sm.contract_derivation_created_out(projected).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_DERIVE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.get(
    "/offer-releases/{release_id}/contract",
    response_model=sm.ContractDerivationOut,
    summary="该已接受报价派生出的合同与逐字段来源（经理视角）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_contract_derivation(
    release_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """读派生关系 + 逐字段来源；**还没有派生过 ⇒ 404**。

    为什么"没派生过"是 404 而不是回一份空壳：回空壳会让"这份报价还没派生合同"与
    "派生了一份内容为空的合同"在客户端长得一模一样，而后者是本切片**最该被发现**的缺陷。
    要区分"尚未派生"，客户端看的是 `POST` 返回的 409 或成果清单 —— 不需要一个
    语义模糊的 200。
    """
    _release, entrustment, _assignment = _load_release_scope(db, release_id)
    # ⛔ 有意**不**用 assert_can_view_entrustment：它对"货主本人"直接放行，
    #    而本端点回的是内部编号（见模块文档）。
    assert_can_view_org(db, user_id=int(user.id), org_id=int(entrustment["org_id"]))
    derivation = svc.get_derivation_by_release(db, release_id=release_id)
    if derivation is None:
        raise not_found("该发布还没有派生合同")
    return sm.contract_derivation_out(svc.project_derivation(db, derivation)).model_dump(
        mode="json"
    )


# ── 签署证据（§10.1 第 7 步后半 / D1-08 的 `linked evidence`）───────────────
#
# 两条端点，与派生**同一条**权限与可见性口径（都只有经理侧一个通道）：
#
# | 端点 | 做什么 |
# | --- | --- |
# | `POST /contracts/{aid}/signature-evidence` | 就合同的某个版本记一条签署证据（幂等） |
# | `GET  /contracts/{aid}/signature-evidence` | 读该合同**全部版本**的证据清单 |
#
# 为什么按**合同成果**寻址，而不是按委托单
# ----------------------------------------
# 证据绑的是"这一版合同"，不是"这一单"。同一单上可能有历史派生出的多份合同
# （严格说一份已接受事实只派生一份，但合同被编辑出第 2 版、将来也可能有第二份），
# 按单寻址会让"这份证据属于哪一版"在库里模糊 —— 而那正是本切片唯一要回答的问题。

_SCOPE_SIGNATURE = "entrust:contract:signature"


def _load_contract_scope(
    db: Session, contract_artifact_id: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """定位合同成果 → 委托单 → 授权；**不自洽一律 404**（理由同 `_load_release_scope`）。"""
    contract = art.get_artifact(db, contract_artifact_id)
    # ⛔ 类型不对**也当不存在**：说"这个 id 存在但不是合同"等于把内部 id 空间
    # 泄露给没权限的人（与 `_load_release_scope` 用 404 而非 400 同一条纪律）。
    if str(contract["artifact_type"]) != svc.CONTRACT_TYPE:
        raise not_found("合同不存在")
    assignment_id = contract["assignment_id"]
    if assignment_id is None:
        raise not_found("合同不存在")
    assignment = load_assignment(db, int(assignment_id))
    if assignment is None:
        raise not_found("合同不存在")
    entrustment_id = contract["entrustment_id"]
    entrustment = None
    if entrustment_id is not None:
        entrustment = load_entrustment(db, int(entrustment_id))
    if entrustment is None:
        # 没有授权链 ⇒ 无从判权限 ⇒ 不能靠"这个成果属于谁"去猜
        raise not_found("合同不存在")
    if int(assignment["owner_user_id"]) != int(entrustment["entrust_user_id"]) or (
        assignment["org_id"] is None or int(assignment["org_id"]) != int(entrustment["org_id"])
    ):
        raise not_found("合同不存在")
    return contract, assignment, entrustment


@router.post(
    "/contracts/{contract_artifact_id}/signature-evidence",
    response_model=sm.SignatureEvidenceOut,
    summary="就合同的某个版本记一条签署证据（经理人，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def record_signature_evidence(
    contract_artifact_id: int,
    data: sm.SignatureEvidenceIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """记一条**标注为样件**的签署证据（§10.1 第 7 步后半）。

    ⛔ `mode` **不是入参**（见 `sm.SignatureEvidenceIn` 的说明）：
    模式恒为 `labeled_sample`，由服务端写死 —— 允许调用方传 `mode=live`
    就等于允许界面自称"已完成电子签署"，与合同 §3.2 / D1-08 直接冲突。
    """
    key = guard_or_400(idempotency_key)
    _contract, _assignment, entrustment = _load_contract_scope(db, contract_artifact_id)
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_CREATE,
        entrustment=entrustment,
        detail="合同不存在",
    )
    payload = {
        "contract_artifact_id": contract_artifact_id,
        "evidence_kind": data.evidence_kind,
        "revision_no": data.revision_no,
        "note": data.note,
    }

    def _business() -> dict[str, Any]:
        created = svc.record_signature_evidence(
            db,
            contract_artifact_id=contract_artifact_id,
            evidence_kind=data.evidence_kind,
            actor_user_id=int(user.id),
            note=data.note,
            revision_no=data.revision_no,
        )
        # 走投影而不是把服务层原始 dict 直接喂给响应模型（与派生同一理由：
        # 键名对不上时 pydantic 不报错、静默用默认值 ⇒ "记成功但内容是空的"）。
        return sm.signature_evidence_out(created).model_dump(mode="json")

    return run_write(
        db,
        scope=_SCOPE_SIGNATURE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_business,
        map_domain_error=_map_errors,
    )


@router.get(
    "/contracts/{contract_artifact_id}/signature-evidence",
    response_model=sm.SignatureEvidenceListOut,
    summary="该合同各版本的签署证据清单（经理视角）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_signature_evidence(
    contract_artifact_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """读证据清单。**一份都没记过 ⇒ 空列表 + `has_items=false`**（不是 404）。

    为什么这里与派生不同（派生未派生过是 404）：合同存在但尚未记证据是**正常中间态**，
    而"派生记录不存在"意味着那个 id 根本没有对应物 —— 两者的语义不同，
    所以一个回空列表、一个回 404（与"发布时间"这类"有/无"的判据同一条纪律）。
    """
    _contract, _assignment, entrustment = _load_contract_scope(db, contract_artifact_id)
    assert_can_view_org(db, user_id=int(user.id), org_id=int(entrustment["org_id"]))
    return sm.signature_evidence_list_out(
        svc.project_signature_evidence_list(db, contract_artifact_id=contract_artifact_id)
    ).model_dump(mode="json")
