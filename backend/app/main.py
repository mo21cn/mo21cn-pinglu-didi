"""FastAPI 应用入口。

启动：
    cd backend
    uvicorn app.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.database import engine
from app.models import Base
from app.modules.agent import router as agent_router
from app.modules.auth import router as auth_router
from app.modules.cargo import router as cargo_router
from app.modules.match import router as match_router
from app.modules.order import router as order_router
from app.modules.payment import router as payment_router
from app.modules.port import router as port_router
from app.modules.ship import router as ship_router

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
)


@app.on_event("startup")
def on_startup() -> None:
    """启动钩子：MVP 阶段自动建表（正式迁移由 Alembic 接管后移除）。"""
    Base.metadata.create_all(bind=engine)


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """健康检查端点，供负载均衡与 CI 探活。"""
    return {"status": "ok", "env": settings.APP_ENV}


# ---- 业务路由注册（MVP 迭代逐步挂载） ----
app.include_router(agent_router, prefix=f"{settings.API_PREFIX}/agent", tags=["agent"])
app.include_router(auth_router, prefix=f"{settings.API_PREFIX}/auth", tags=["auth"])
app.include_router(cargo_router, prefix=f"{settings.API_PREFIX}/cargo", tags=["cargo"])
app.include_router(ship_router, prefix=f"{settings.API_PREFIX}/ship", tags=["ship"])
app.include_router(port_router, prefix=f"{settings.API_PREFIX}/port", tags=["port"])
app.include_router(match_router, prefix=f"{settings.API_PREFIX}/match", tags=["match"])
app.include_router(order_router, prefix=f"{settings.API_PREFIX}/order", tags=["order"])
app.include_router(payment_router, prefix=f"{settings.API_PREFIX}/payment", tags=["payment"])
