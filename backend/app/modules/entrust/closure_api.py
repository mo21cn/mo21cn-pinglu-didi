"""委托结案端点（S4-b）—— 合同 §6.4 的 `complete`。

一条命令，五个维度（任务处置 / 交付证据 / 异常与重评 / 结算 / 余额与争议）＋
**逐条报缺**。判据与派生全在 `closure.py`，本层只做三件事：
**拿幂等键 → 交给服务层 → 把领域异常映射成 HTTP**（口径与结算/费用那两族一致）。

⛔ 本层**不**做任何前置判断：判在端点、执行在服务层，就会出现"端点说可以、
服务层说不行"或反过来。之所以把这一步写进 docstring，是因为"加一个前置检查"
看起来总是无害的 —— 它在这里是有害的。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import closure as cl
from app.modules.entrust._http import (
    guard_or_400,
    map_closure_error,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.schemas import (
    AssignmentCompleteIn,
    AssignmentOut,
    ClosureReadinessOut,
    assignment_out,
    closure_readiness_out,
)

router = APIRouter()

#: 幂等作用域：与权限码同串（`_run_write` 的 scope 只用于"同键同体"的判定，
#: 不参与授权）。用同一个串是为了让审计记录里"这次结案"一眼可辨。
_SCOPE_COMPLETE = "entrust:assignment:complete"


@router.post(
    "/assignments/{assignment_id}/complete",
    response_model=AssignmentOut,
    summary="结案：五维度前置逐条校验，缺项即 409 ＋ missing[]（经理，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def complete_assignment(
    assignment_id: int,
    body: AssignmentCompleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """`claimed → completed`，**原子**评估合同 §6.4 的五个维度。

    三类响应要分清（调用方据此决定做什么）：

    | 码 | 含义 | 调用方该做什么 |
    | --- | --- | --- |
    | **200** | 结案成功（或同键重放历史成功响应） | 读 `completed_at` 与 `status` |
    | **409 ＋ `missing[]`** | **前置未满足**（或 `expected_revision` 过期 / 已结案） | 按 `missing[].code` 逐条补；补完用**同一幂等键**重试 |
    | **403 / 404** | 无 `entrust:assignment:complete` ／ 非参与方（不区分存在性） | 换身份，别重试 |

    ⛔ 没有任何"跳过前置"的入参；也给不出 200＋未结案的组合 ——
    §5.3.1 的分期裁定：DEMO-1 期间 `completed` 与 `settled` 同时成立。
    """
    key = guard_or_400(idempotency_key)
    return run_write(
        db,
        scope=_SCOPE_COMPLETE,
        key=key,
        actor_user_id=int(user.id),
        # `expected_revision` 进 payload：同键**异体**必须 409，而不是拿旧响应重放
        # （否则"改完再提交同一个键"会拿到上一次的结果，看起来像提交成功了）。
        payload={"assignment_id": assignment_id, "expected_revision": body.expected_revision},
        business=lambda: assignment_out(
            cl.complete_assignment(
                db,
                assignment_id=assignment_id,
                actor_id=int(user.id),
                expected_revision=body.expected_revision,
            )
        ).model_dump(mode="json"),
        map_domain_error=map_closure_error,
    )


@router.get(
    "/assignments/{assignment_id}/closure-readiness",
    response_model=ClosureReadinessOut,
    summary="结案齐备度：五维度逐条报缺（与结案命令同一把锁）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_closure_readiness(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """`ready` ＋ `missing[]` ＋ 逐维度计数 —— `complete` 的**只读前置**。

    为什么要有它（而不是让界面直接试着结案）：`complete` 的 409 只在**点下去之后**
    才说缺什么，而结案是**不可逆**动作 ⇒ 界面应当先让人看见清单、再执行。
    它与命令**同一把锁**（`entrust:assignment:complete`）—— 看得到缺项的人，就是
    能结案的人；否则会出现"看得见、点下去 403"的形态。

    ⚠️ `ready=True` **不等于**现在能结：命令还要求委托处于 `claimed`（已结案 ⇒ 409，
    未受理 ⇒ 409）。按钮态请按**委托自身**的 `status` 判断，别把这个字段读成"能结"。
    ⛔ 不做任何写入、不落库（纯派生）。

    | 码 | 含义 |
    | --- | --- |
    | **200** | 齐备度（`missing=[]` 即五条前置成立） |
    | **403** | 无 `entrust:assignment:complete` |
    | **404** | 非参与方／不存在（不区分） |
    """
    try:
        data = cl.read_closure_readiness(db, assignment_id=assignment_id, user_id=int(user.id))
    except Exception as exc:  # 领域异常 → HTTP：与写端点复用**同一条**映射
        mapped = map_closure_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return closure_readiness_out(data).model_dump(mode="json")
