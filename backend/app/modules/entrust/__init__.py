"""委托发货模块。

目录边界见 AGENTS.md 第 2 节：委托后端模块 `backend/app/modules/entrust/`。
本模块只放委托支线自己的业务，公共机制（幂等、成果版本等）在 `app/core/` 与各公共模块。

路由组织：`router.py` 是受理链路（ENT-005），`artifacts_api.py` 是成果版本
（ENT-006），后者挂到同一个 `router` 下，`main.py` 只注册一次 `/api/v1/entrust`。
"""

from app.modules.entrust.artifacts_api import router as artifacts_api_router
from app.modules.entrust.router import router

router.include_router(artifacts_api_router)

__all__ = ["router"]
