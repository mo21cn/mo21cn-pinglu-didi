"""智慧关务（关域）模块 —— `/api/v1/customs`（D1：人工登记 + 状态时间线）。

本模块是**新增**的，不改 `entrust/` 内部语义（PRD 附录 A 的隔离原则）：

* 不改旧接口、不碰 `User.current_role` 链路；
* 可见性判定复用委托支线的组织解析（`entrust.access.resolve_context`），
  **只读复用**，不反向依赖；
* 表与 `ent_*` 弱关联（存 `assignment_id`，无外键、无级联）。

首版能力边界（决策文档 PL-CUS-DEMO-D0 §3）：

* 不申报（Q-01）→ 不产出任何申报报文；
* 不接外部数据源（Q-02）→ 状态全部来自人工登记，`freshness` 明写"官方源不可用"；
* 只做出出口（Q-03）。
"""

from app.modules.customs.router import router

__all__ = ["router"]
