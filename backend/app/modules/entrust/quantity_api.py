"""委托货量变更历史的读端点（`/api/v1/entrust` 下；D1-09 / §10.1 第 8 步）。

这个模块只有一条端点，但它回答的是 D1-09 的核心问题：

    同一张委托上，**800 是什么时候变成 950 的、谁批的、凭什么**？

写侧在 `exceptions.apply_case`（经审批的变更应用），本模块只是它的读路径。
为什么读路径必须单独存在：`ent_assignment_quantity_change` 只写不读的话，
「原来是 800」就只能顺着案件号去翻事件链 —— 而 D1-09 要的是**凭这张委托**就能看到
`800 → 950` 这条对照。一张没有读路径的历史表，等于把留痕藏在审计链深处。

⚠️ 为什么**不给货主本人放行**（与运力那一组同口径）
--------------------------------------------------
`basis` 是**经理写的变更依据**（"客户口头确认加货"、"上游舱位收紧"…），
它可能带内部口径。货量本身客户当然看得见（在 `GET /assignments/{id}` 里），
但"为什么改"是谁写的话，属于内部判断。

判据仍是那一句：**这条通道上有没有内部信息**，不是"是不是客户"。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import exceptions as case_svc
from app.modules.entrust import schemas as sm
from app.modules.entrust._http import require_entrust_enabled
from app.modules.entrust.authz import assert_can_view_org, load_assignment, not_found

router = APIRouter()


def _load_org_scope(db: Session, assignment_id: int) -> dict[str, Any]:
    """读委托单并要求它有服务经营主体（没有组织就没有"组织侧"这个视角）。

    与 `capacity_api._load_org_scope` **刻意各留一份**：两处判据眼下相同，
    但它们的理由不同（那边是"授权解析要有组织"，这边是"内部口径要有组织边界"），
    合并成一个共享助手会让其中一处想改判据时必须先说服另一处。
    """
    assignment = load_assignment(db, assignment_id)
    if assignment is None or assignment["org_id"] is None:
        raise not_found("委托不存在")
    return assignment


@router.get(
    "/assignments/{assignment_id}/quantity-changes",
    response_model=list[sm.AssignmentQuantityChangeOut],
    summary="该委托的货量变更历史（经理视角；append-only 的应用留痕）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_quantity_changes(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """按应用时间升序返回**全部**已应用的货量变更。

    ⚠️ **空列表是正常答复，不是 404**：绝大多数委托从没改过货量。
    这与 `GET /capacity-confirmations/{id}` 那个"不回空壳"的口径**相反** ——
    判据是资源本身：这里是**集合**（可以为空），那里是**单条记录**（不存在就是不存在）。
    把两者写成一样，会让"这单没改过货量"和"这个 id 不存在"长得一模一样。

    `old_quantity` 为 `null` = 变更前**未知**（不是 0）。
    """
    assignment = _load_org_scope(db, assignment_id)
    assert_can_view_org(
        db, user_id=int(user.id), org_id=int(assignment["org_id"]), detail="委托不存在"
    )
    return [
        sm.assignment_quantity_change_out(row).model_dump(mode="json")
        for row in case_svc.list_quantity_changes(db, assignment_id=assignment_id)
    ]
