"""智能体域路由（F9）。

- POST /api/v1/agent/cargo-parse   货源解析（自然语言 → 结构化草稿，货主角色）

后续迭代挂载：客服导购 / 运营分析 / 合规初筛 / 智能合同 等 Agent。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.agent import service
from app.modules.agent.schemas import CargoParseRequest, CargoParseResult
from app.modules.agent.service import AgentServiceError
from app.modules.auth.dependencies import get_current_user

router = APIRouter()

_ERROR_STATUS = {
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "network": status.HTTP_503_SERVICE_UNAVAILABLE,
    "rate_limit": status.HTTP_503_SERVICE_UNAVAILABLE,
    "auth": status.HTTP_502_BAD_GATEWAY,
    "bad_response": status.HTTP_502_BAD_GATEWAY,
}


@router.post(
    "/cargo-parse",
    response_model=CargoParseResult,
    summary="货源解析（自然语言 → 结构化草稿）",
)
async def cargo_parse(
    body: CargoParseRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """货主口语描述 → 草稿；Agent 无直写，落库须走 /cargo/shipments。"""
    if user.current_role != "shipper":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="货源解析仅货主角色可用，请先切换角色",
        )
    try:
        return await service.parse_cargo(db, user_id=user.id, text=body.text)
    except AgentServiceError as exc:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.kind, status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=f"Agent 暂不可用（{exc.kind}），请稍后重试或手动填写货源信息",
        ) from exc
