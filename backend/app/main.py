"""FastAPI 应用入口。

启动：
    cd backend
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.database import engine
from app.models import Base
from app.modules.agent import router as agent_router
from app.modules.auth import router as auth_router
from app.modules.cargo import router as cargo_router
from app.modules.entrust import router as entrust_router
from app.modules.match import router as match_router
from app.modules.order import router as order_router
from app.modules.payment import router as payment_router
from app.modules.port import router as port_router
from app.modules.ship import router as ship_router

settings = get_settings()

#: 应用日志格式。**带 logger 名**：云端日志里 uvicorn 访问行与业务行混排，
#: 没有 logger 名就只能靠文案猜"这条属于哪一层"。
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _resolve_level(level: str) -> int:
    """把配置里的级别字符串翻成 logging 常量；无法识别 ⇒ INFO（不静默变成 NOTSET）。"""
    value = getattr(logging, str(level or "").strip().upper(), None)
    return value if isinstance(value, int) else logging.INFO


def configure_logging(level: str) -> None:
    """把**应用自身**的日志接到 stdout。

    ## 为什么必须有这一步（2026-09-24 实测的排查成本）

    此前全仓没有任何 `basicConfig` / root handler —— `uvicorn` 只配置**它自己的**
    logger，于是应用 logger 的日志在云端**只剩 WARNING 以上可见**（Python 的
    `lastResort` 处理器只管 WARNING+）。后果不是"少几条日志"，而是**关键事实缺失**：

    * `auth/service.py` 里那条「云托管通道采信平台注入身份: openid=…」是 `logger.info`
      ⇒ 云端看不见 ⇒ 排查"这次登录的是哪个账号"时**没有任何日志证据**，
      只能反推（2026-09-24 甲方「委托发货」空队列故障即因此多绕了数步）；
    * 而 `logger.error` 的「code2session 网络异常」看得见 ⇒ 症状是"只有报错、没有上下文"。

    接上 root handler 后，`LOG_LEVEL`（云侧 `INFO`）才真的生效 —— 环境变量写了
    而无人读取，等于没配。

    ⚠️ **不能只调 `logging.basicConfig`**：root 上**已有 handler** 时它是**静默 no-op**
    （`basicConfig` 只在"无 handler"时才动 root）。测试框架、宿主进程、uvicorn 的
    `dictConfig` 都可能事先装上 handler ⇒ 级别悄悄不变，症状与"没接线"完全一样，
    且更难查（看起来代码是对的）。所以这里分两路：无 handler 才交给 `basicConfig`
    （顺带装上我们的格式），有 handler 就**显式抬级别**。
    """
    numeric = _resolve_level(level)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=numeric, format=LOG_FORMAT)
        return
    root.setLevel(numeric)


configure_logging(settings.LOG_LEVEL)

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
)


@app.on_event("startup")
def on_startup() -> None:
    """启动钩子：自动建表，但**排除迁移管理的表**。

    委托支线新增表统一使用 `ent_` 前缀（`MIGRATION_MANAGED_TABLE_PREFIX`），
    由 `backend/migrations/` + `python migrate.py` 创建，不在这里自动建，
    以保证结构变更始终有版本记录（决策见 docs/entrust/decisions/0001）。
    """
    prefix = settings.MIGRATION_MANAGED_TABLE_PREFIX
    auto_tables = [t for t in Base.metadata.sorted_tables if not t.name.startswith(prefix)]
    Base.metadata.create_all(bind=engine, tables=auto_tables)


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
# 委托支线：ENTRUST_ENABLED=false 时端点自行返回 404（见 entrust.router.require_entrust_enabled）
app.include_router(entrust_router, prefix=f"{settings.API_PREFIX}/entrust", tags=["entrust"])
