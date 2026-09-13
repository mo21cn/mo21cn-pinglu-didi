"""委托发货模块。

目录边界见 AGENTS.md 第 2 节：委托后端模块 `backend/app/modules/entrust/`。
本模块只放委托支线自己的业务，公共机制（幂等、成果版本等）在 `app/core/` 与各公共模块。

路由组织：`router.py` 是受理链路（ENT-005），`artifacts_api.py` 是成果版本与
成果类型注册表（ENT-006 / ENT-009），`tasks_api.py` 是任务模型（ENT-008），
`attachments_api.py` 是附件上传与授权下载（ENT-009），`agent_api.py` 是会话与
Agent 作业（ENT-011），五者挂到同一个 `router` 下，`main.py` 只注册一次
`/api/v1/entrust`。
"""

from app.modules.entrust.agent_api import router as agent_api_router
from app.modules.entrust.artifacts_api import router as artifacts_api_router
from app.modules.entrust.attachments_api import router as attachments_api_router
from app.modules.entrust.router import router
from app.modules.entrust.tasks_api import router as tasks_api_router

router.include_router(agent_api_router)
router.include_router(artifacts_api_router)
router.include_router(attachments_api_router)
router.include_router(tasks_api_router)

__all__ = ["router"]
