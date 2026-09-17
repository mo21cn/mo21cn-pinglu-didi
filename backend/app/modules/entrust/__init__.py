"""委托发货模块。

目录边界见 AGENTS.md 第 2 节：委托后端模块 `backend/app/modules/entrust/`。
本模块只放委托支线自己的业务，公共机制（幂等、成果版本等）在 `app/core/` 与各公共模块。

路由组织：`router.py` 是受理链路（ENT-005），`artifacts_api.py` 是成果版本与
成果类型注册表（ENT-006 / ENT-009），`tasks_api.py` 是任务模型（ENT-008），
`attachments_api.py` 是附件上传与授权下载（ENT-009），`extraction_api.py` 是
文档提取与人工转录（ENT-013），`agent_api.py` 是会话与 Agent 作业（ENT-011），
`exceptions_api.py` 是异常与变更案件（ENT-030 / DR-0013），
`offers_api.py` 是对客发布与客户响应（S3 / BP-03 第 4/5/6/7/10 条），
`contracts_api.py` 是合同派生（S3 / BP-03 第 8 条），
它们挂到同一个 `router` 下，`main.py` 只注册一次 `/api/v1/entrust`。

挂载顺序：`extraction_api` 必须排在 `attachments_api` **之后** ——
它复用后者的 `load_visible_attachment` / `map_attachment_error`（同一套可见性与
异常映射），先加载前者会在导入期就形成反向依赖。
"""

from app.modules.entrust.agent_api import router as agent_api_router
from app.modules.entrust.artifacts_api import router as artifacts_api_router
from app.modules.entrust.attachments_api import router as attachments_api_router
from app.modules.entrust.contracts_api import router as contracts_api_router
from app.modules.entrust.exceptions_api import router as exceptions_api_router
from app.modules.entrust.extraction_api import router as extraction_api_router
from app.modules.entrust.offers_api import router as offers_api_router
from app.modules.entrust.router import router
from app.modules.entrust.tasks_api import router as tasks_api_router

router.include_router(agent_api_router)
router.include_router(artifacts_api_router)
router.include_router(attachments_api_router)
router.include_router(contracts_api_router)
router.include_router(exceptions_api_router)
router.include_router(extraction_api_router)
router.include_router(offers_api_router)
router.include_router(tasks_api_router)

__all__ = ["router"]
