"""智能体域路由（F9-F11）。

- POST /api/v1/agent/cargo-parse    货源解析（自然语言 → 结构化草稿，货主角色）
- POST /api/v1/agent/assistant      客服导购问答（FAQ/航线/用法，纯读，全角色）
- POST /api/v1/agent/contract/generate  合同草稿生成（订单参与方，核心条款零 LLM）

后续迭代挂载：运营分析 / 合规初筛 等 Agent。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.agent import service
from app.modules.agent.schemas import (
    AssistantRequest,
    AssistantResult,
    CargoParseRequest,
    CargoParseResult,
    ContractDraftResult,
    ContractGenerateRequest,
)
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


@router.post(
    "/assistant",
    response_model=AssistantResult,
    summary="客服导购问答（FAQ/航线/用法咨询）",
)
async def assistant(
    body: AssistantRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """平台客服/导购问答；纯读零直写，全角色可用。"""
    try:
        return await service.answer_question(
            db, user_id=user.id, question=body.question, history=body.history
        )
    except AgentServiceError as exc:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.kind, status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=f"智能客服暂不可用（{exc.kind}），请稍后重试",
        ) from exc


@router.post(
    "/contract/generate",
    response_model=ContractDraftResult,
    summary="合同草稿生成（订单 → 合同 + 风险点）",
)
async def contract_generate(
    body: ContractGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """订单参与方生成合同草稿；核心条款来自订单数据（零 LLM），草稿不落库。"""
    try:
        return await service.generate_contract(db, user_id=user.id, order_id=body.order_id)
    except AgentServiceError as exc:
        code = _ERROR_STATUS.get(exc.kind, status.HTTP_500_INTERNAL_SERVER_ERROR)
        if exc.kind == "bad_request":
            code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc
