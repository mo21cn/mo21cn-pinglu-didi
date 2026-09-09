"""FastAPI 应用入口。

启动：
    cd backend
    uvicorn app.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
)


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """健康检查端点，供负载均衡与 CI 探活。"""
    return {"status": "ok", "env": settings.APP_ENV}


# TODO: 各业务模块路由在此注册（MVP 迭代逐步挂载）
# from app.modules.cargo import router as cargo_router
# app.include_router(cargo_router, prefix=f"{settings.API_PREFIX}/cargo", tags=["cargo"])
