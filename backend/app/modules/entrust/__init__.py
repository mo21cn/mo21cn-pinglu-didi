"""委托发货模块。

目录边界见 AGENTS.md 第 2 节：委托后端模块 `backend/app/modules/entrust/`。
本模块只放委托支线自己的业务，公共机制（幂等、成果版本等）在 `app/core/` 与各公共模块。
"""

from app.modules.entrust.router import router

__all__ = ["router"]
