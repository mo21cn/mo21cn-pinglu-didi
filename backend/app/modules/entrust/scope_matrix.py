"""委托支线的**声明式权限矩阵**（ENT-010 / S1 第 8 条 / AC-10 的结构性防线）。

## 为什么需要这张表

权限校验合法地落在三处：端点函数、模块内助手（如 `agent_api._session_write_guards`）、
服务层（如 `tasks.authorize`）。这让"这个端点到底要什么权限"只能靠读代码回答，
更危险的是**新增端点时忘记加校验**——没有任何机制会失败。

本表把"每个端点需要什么"变成**可枚举、可断言的数据**，配套自检见
`tests/test_entrust_scope_matrix.py`：

* **双向覆盖**：openapi 暴露的每条路由都必须在此声明（新增端点不声明 → CI 红）；
  声明了却不存在的端点同样 CI 红（防僵尸条目）。
* **权限常量有效性**：`permission` 必须是 `access` 模块里真实存在的取值（防拼写错误）。
* **写端点幂等性完备**：每个写端点要么声明 `idempotent=True`，要么进 `IDEMPOTENT_EXEMPT`
  并写清为什么不需要（豁免必须**显式**，不能靠沉默）。
* **开关关闭全 404**：`ENTRUST_ENABLED=false` 时**全部**端点 404（AC-22 的结构性版本，
  不再只覆盖"三组端点"；新增端点自动纳入）。

## 本表**不**做什么

不做源码级断言（"某行必须出现 `assert_can`"）。守卫可以合法地落在那三处的任一处，
源码匹配会随重构漂移，把一个正确的重构判成失败——那会训练团队绕过门禁。
`note` 里记录**守卫落点**供人审查，但它不是断言依据。
"""

from __future__ import annotations

from dataclasses import dataclass

# ── 路由前缀（`main.py` 注册时使用；本表的 path 为**相对**该前缀的模板）──────────
API_PREFIX = "/api/v1/entrust"

# ── guard：谁可以访问 ───────────────────────────────────────────────────────
GUARD_AUTHENTICATED = "authenticated"
"""登录即可（不含客户数据，或数据归属由调用方自身决定）。"""

GUARD_OWNER_SELF = "owner_self"
"""仅数据归属的**货主本人**（他人可见性一律 404，不泄漏存在性）。"""

GUARD_ORG_MEMBER = "org_member"
"""委托所属**组织成员**，通常再叠加一项权限。"""

GUARD_ENTRUSTMENT_VIEW = "entrustment_view"
"""经**委托授权链**可见：货主本人，或持该货主生效授权的组织成员（需 `entrust:view`）。"""

GUARD_ENTRUSTMENT_WRITE = "entrustment_write"
"""经**委托授权链**可写：持该货主生效授权且权限足够。"""

GUARDS = frozenset(
    {
        GUARD_AUTHENTICATED,
        GUARD_OWNER_SELF,
        GUARD_ORG_MEMBER,
        GUARD_ENTRUSTMENT_VIEW,
        GUARD_ENTRUSTMENT_WRITE,
    }
)


@dataclass(frozen=True, slots=True)
class RouteScope:
    """一条路由的授权声明。"""

    method: str
    path: str
    guard: str
    permission: str | None = None
    owner_scope: bool = False
    idempotent: bool = False
    note: str = ""


def _r(
    method: str,
    path: str,
    guard: str,
    permission: str | None = None,
    *,
    owner_scope: bool = False,
    idempotent: bool = False,
    note: str = "",
) -> RouteScope:
    return RouteScope(
        method=method,
        path=path,
        guard=guard,
        permission=permission,
        owner_scope=owner_scope,
        idempotent=idempotent,
        note=note,
    )


# ── 声明式矩阵（48 条，与 openapi 暴露的路由一一对应）──────────────────────
SCOPE_MATRIX: tuple[RouteScope, ...] = (
    # ── 受理链路（router.py）────────────────────────────────────────────
    _r(
        "POST",
        "/assignments",
        GUARD_OWNER_SELF,
        idempotent=True,
        note="货主建自己的草稿；owner_user_id 取自登录用户，不接受客户端传入",
    ),
    _r(
        "GET",
        "/assignments",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="owner 视角看自己的；org 视角需为组织成员且有 entrust:view（router.py 队列判定）",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="货主本人或组织内 entrust:view；非参与方 404",
    ),
    _r(
        "PATCH",
        "/assignments/{assignment_id}",
        GUARD_OWNER_SELF,
        note="仅草稿、仅货主本人；并发靠 expected_revision 乐观锁（见豁免表）",
    ),
    _r(
        "POST",
        "/assignments/{assignment_id}/submit",
        GUARD_OWNER_SELF,
        owner_scope=True,
        idempotent=True,
        note="货主本人提交，且要求该货主在本组织存在生效授权",
    ),
    _r(
        "POST",
        "/assignments/{assignment_id}/claim",
        GUARD_ORG_MEMBER,
        "entrust:assignment:claim",
        idempotent=True,
        note="组织成员 + entrust:assignment:claim；单条条件 UPDATE 保证原子认领（双认领后者 409）",
    ),
    _r(
        "POST",
        "/assignments/{assignment_id}/cancel",
        GUARD_OWNER_SELF,
        idempotent=True,
        note="仅货主本人撤回；已认领的不能由货主单方撤回",
    ),
    # ── 成果版本与类型注册表（artifacts_api.py）──────────────────────────
    _r(
        "GET",
        "/artifact-types",
        GUARD_AUTHENTICATED,
        note="全局成果类型字典，非客户数据（entrust:view 不适用）",
    ),
    _r(
        "POST",
        "/entrustments/{entrustment_id}/artifacts",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="assert_can_write_entrustment（作用域到该委托货主）",
    ),
    _r(
        "GET",
        "/artifacts/{artifact_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="assert_can_view_entrustment；非参与方 404",
    ),
    _r(
        "GET",
        "/artifacts/{artifact_id}/revisions",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="同成果详情",
    ),
    _r(
        "POST",
        "/artifacts/{artifact_id}/revisions",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="追加版本（append-only，编辑产生新 revision）",
    ),
    _r(
        "GET",
        "/artifacts/{artifact_id}/changes",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="字段变化清单",
    ),
    _r(
        "POST",
        "/artifacts/{artifact_id}/confirm",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="确认绑定**精确版本**；失效成果由服务端阻止确认",
    ),
    _r(
        "POST",
        "/artifacts/{artifact_id}/void",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:publish",
        owner_scope=True,
        idempotent=True,
        note="作废要求发布级权限（entrust:quote:publish）",
    ),
    # ── 附件上传与授权下载（attachments_api.py）──────────────────────────
    _r(
        "POST",
        "/attachments",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="有委托走授权链；私有草稿附件仅归属人或组织内有权限成员可写",
    ),
    _r(
        "GET",
        "/entrustments/{entrustment_id}/attachments",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="经授权可见",
    ),
    _r(
        "GET",
        "/artifacts/{artifact_id}/attachments",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="经授权可见",
    ),
    _r(
        "POST",
        "/artifacts/{artifact_id}/attachments",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="把已上传附件绑定到成果",
    ),
    _r(
        "GET",
        "/attachments/{attachment_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="可见性复用 load_visible_attachment（同一规则只有一处实现）",
    ),
    _r(
        "GET",
        "/attachments/{attachment_id}/download",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="**授权下载**：先判可见性再落盘读出",
    ),
    # ── 文档提取与人工转录（extraction_api.py）───────────────────────────
    _r(
        "POST",
        "/attachments/{attachment_id}/extract",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="复用上传权限（不新增权限）；可重复调用即“重抽”",
    ),
    _r(
        "GET",
        "/attachments/{attachment_id}/text",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="无文本时返回状态而非 404（“没提取”与“不存在”必须可区分）",
    ),
    _r(
        "POST",
        "/attachments/{attachment_id}/transcription",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="扫描件的人工录入降级路径",
    ),
    # ── 任务模型（tasks_api.py）─────────────────────────────────────────
    _r(
        "POST",
        "/assignments/{assignment_id}/tasks",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="派单动作需要 entrust:task:dispatch",
    ),
    _r(
        "GET",
        "/tasks",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="货主本人或组织内 entrust:view",
    ),
    _r(
        "GET",
        "/tasks/{task_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="同任务列表",
    ),
    _r(
        "PATCH",
        "/tasks/{task_id}",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        note="管理动作；并发靠 expected_revision（见豁免表）",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/assign",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="改派（代次 +1，旧代次提交被 fence）",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/start",
        GUARD_ORG_MEMBER,
        owner_scope=True,
        idempotent=True,
        note="**执行是本职，不需要派单权限**；限被指派人",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/wait",
        GUARD_ORG_MEMBER,
        owner_scope=True,
        idempotent=True,
        note="缺件等待；同 start",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/complete",
        GUARD_ORG_MEMBER,
        owner_scope=True,
        idempotent=True,
        note="完成需满足证据要求；同 start",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/takeover",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="人工接管优先：接管后代次 +1，旧 Agent 结果不得覆盖",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/precondition",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="设置固定前置条件（自依赖/循环由服务端拒绝）",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/cancel",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="撤回任务",
    ),
    _r(
        "POST",
        "/tasks/{task_id}/reopen",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="reopen 保留历史（不删除已完成记录）",
    ),
    # ── 会话与 Agent 作业（agent_api.py）────────────────────────────────
    _r(
        "POST",
        "/entrustments/{entrustment_id}/sessions",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:agent:job",
        owner_scope=True,
        idempotent=True,
        note="建会话需要 Agent 作业权限（assert_can_write_entrustment + PERM_AGENT_JOB）",
    ),
    _r(
        "GET",
        "/sessions",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="org 视角队列需 entrust:view",
    ),
    _r(
        "GET",
        "/sessions/{session_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="_visible_session：挂授权走授权链，否则归属人/组织内判定；不可见 404",
    ),
    _r(
        "POST",
        "/sessions/{session_id}/messages",
        GUARD_ORG_MEMBER,
        "entrust:agent:job",
        owner_scope=True,
        idempotent=True,
        note="_session_write_guards；未挂授权的私有会话仅创建者本人可写",
    ),
    _r(
        "POST",
        "/sessions/{session_id}/jobs",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:agent:job",
        owner_scope=True,
        idempotent=True,
        note="_session_write_guards；作业执行范围绑定组织·委托·操作者",
    ),
    _r(
        "POST",
        "/sessions/{session_id}/archive",
        GUARD_ORG_MEMBER,
        "entrust:agent:job",
        owner_scope=True,
        idempotent=True,
        note="_session_write_guards",
    ),
    _r(
        "GET",
        "/agent/specialties",
        GUARD_AUTHENTICATED,
        note="Agent 专业清单（静态配置，非客户数据）",
    ),
    _r(
        "GET",
        "/agent/jobs",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="作业列表",
    ),
    _r(
        "GET",
        "/agent/jobs/{job_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="_visible_job（可见性随会话/委托/委托单走）；**raw_output 不外泄**",
    ),
    _r(
        "POST",
        "/agent/jobs/{job_id}/run",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="经 _visible_job 判定可见性；**有意不要求 Idempotency-Key**（见豁免表）",
    ),
    _r(
        "POST",
        "/agent/jobs/{job_id}/retry",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        idempotent=True,
        note="经 _visible_job；再叠加“会话内或创建者本人”判定",
    ),
    _r(
        "POST",
        "/agent/jobs/{job_id}/cancel",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        idempotent=True,
        note="经 _visible_job；再叠加“会话内或创建者本人”判定",
    ),
)


IDEMPOTENT_EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/agent/jobs/{job_id}/run"): (
        "作业状态机本身保证幂等：重复调用时 claim_job 因“已结束”或“租约仍被持有”返回 None（→ 409），"
        "连一次尝试都不会多消耗。这比用键重放旧响应更强 —— 键重放只防重复提交。"
        "见 agent_api.run_job 文档字符串。"
    ),
    ("PATCH", "/assignments/{assignment_id}"): (
        "靠 expected_revision 乐观锁：重放同一请求会因版本过期得到 409，不会产生第二次副作用。"
    ),
    ("PATCH", "/tasks/{task_id}"): (
        "同受理单：PATCH 走 expected_revision 乐观锁（tasks_api 模块说明第 21 行）。"
    ),
}

WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def scope_key(method: str, path: str) -> tuple[str, str]:
    """矩阵键：(大写方法, 相对 API_PREFIX 的路径模板)。"""
    return (method.upper(), path)


def matrix_keys() -> frozenset[tuple[str, str]]:
    return frozenset(scope_key(item.method, item.path) for item in SCOPE_MATRIX)


def declared_permissions() -> frozenset[str]:
    return frozenset(item.permission for item in SCOPE_MATRIX if item.permission)


def describe(method: str, path: str) -> RouteScope | None:
    key = scope_key(method, path)
    for item in SCOPE_MATRIX:
        if scope_key(item.method, item.path) == key:
            return item
    return None


def write_routes_without_idempotency() -> list[RouteScope]:
    """返回「未声明幂等且未进豁免表」的写端点 —— 自检断言它为空。"""
    exempt = set(IDEMPOTENT_EXEMPT)
    return [
        item
        for item in SCOPE_MATRIX
        if item.method in WRITE_METHODS
        and not item.idempotent
        and scope_key(item.method, item.path) not in exempt
    ]


__all__ = [
    "API_PREFIX",
    "GUARD_AUTHENTICATED",
    "GUARD_ENTRUSTMENT_VIEW",
    "GUARD_ENTRUSTMENT_WRITE",
    "GUARD_ORG_MEMBER",
    "GUARD_OWNER_SELF",
    "GUARDS",
    "IDEMPOTENT_EXEMPT",
    "SCOPE_MATRIX",
    "WRITE_METHODS",
    "RouteScope",
    "declared_permissions",
    "describe",
    "matrix_keys",
    "scope_key",
    "write_routes_without_idempotency",
]
