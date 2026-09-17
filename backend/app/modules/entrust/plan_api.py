"""运输计划端点（`/api/v1/entrust` 下；BP-03 第 1 条 / 合同 §10.1 第 4 步）。

一条端点：

| 端点 | 做什么 |
| --- | --- |
| `GET /assignments/{id}/plan` | 该委托的**三段计划**（`ent_leg`）+ **必需任务与其固定前置**（`ent_workflow_task`） |

为什么只有一条，而且只有读
--------------------------
* "必需任务前置"**早就可读**（`GET /assignments/{id}/tasks`，且工作台 `plan_tasks`
  槽位也带 `precondition_task_id`）⇒ 缺的只有**航段**。本条把两者**合成一份读模型**，
  理由是 §10.1 第 4 步要的是"计划**与**前置"同屏可读，拆成两次取数会让
  "计划有、前置没拿到"变成一个界面上的**半真**状态（而它们本来在同一步里展示）。
* **建段命令本轮未做**（见 `plan.py` 模块文档）：谁能建段 / 是否强制 公—水—公 /
  改段是否留版本，都要口径裁定。本切片**不发明**这些规则，也不给一个"什么都能塞"
  的写口 —— 一个没有规则约束的写口比没有写口更坏。
  §10.1 第 4 步是 `Show ...`，演示里计划来自**部署阶段的种子**。

⚠️ 为什么这条**给货主本人放行**（与运力 / 合同两组**相反**）
------------------------------------------------------------
`authz.assert_can_view_assignment` 对"货主本人"直接通过 —— 本端点**就是要用它**：

* 航段是**委托单自己的起讫三段**（`厂区→南宁港→贵港港→卸货地`），是客户
  **自己交进来的信息**；把客户自己的路线挡在客户外面没有道理；
* 任务标题与前置也不含任何内部成本口径 —— 承运人、供应商单价、需求量与缺口
  在**运力那一组**，而那组（`capacity_api`）与合同派生读取**一律不给货主放行**。

⇒ 判据是"**这条通道上有没有内部信息**"，不是"是不是客户"。同一条纪律两个方向，
写成一句话：**要按数据判，不要按身份判**。两处的模块文档各自记了理由，
就是为了让下一个加端点的人知道该往哪边靠。

读路径**不要**写权限：展示动作本身不需要 `entrust:quote:create`
（§10.1 第 4 步原文是 `Show ...`）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import plan as svc
from app.modules.entrust import schemas as sm
from app.modules.entrust._http import require_entrust_enabled
from app.modules.entrust.authz import assert_can_view_assignment, load_assignment, not_found

router = APIRouter()


@router.get(
    "/assignments/{assignment_id}/plan",
    response_model=sm.AssignmentPlanOut,
    summary="该委托的三段计划与必需任务前置（合同 §10.1 第 4 步）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_assignment_plan(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """读三段计划 + 必需任务前置。

    ⚠️ 委托不存在、或调用方看不见它 ⇒ **一律 404**（不区分"不存在"与"无权知晓"）。
    `assert_can_view_assignment` 已经把两件事合成一次判定：
    非货主本人且不是该组织成员 ⇒ 404；是成员但没有 `entrust:view` ⇒ 也是 404。

    ⚠️ **空计划不是错误**：`legs` 为空回 `[]`。把"这张委托还没结构化计划"报成 4xx，
    会让它与"端点坏了"在客户端长得一样 —— 而前者是一种正常状态
    （`ent_leg` 只被最小化落地，没有回溯填充历史数据）。
    """
    assignment = load_assignment(db, assignment_id)
    if assignment is None:
        raise not_found("委托不存在")
    assert_can_view_assignment(db, user_id=int(user.id), assignment=assignment)
    return sm.assignment_plan_out(svc.build_plan(db, assignment=assignment)).model_dump(mode="json")
