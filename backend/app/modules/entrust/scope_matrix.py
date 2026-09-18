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


# ── 声明式矩阵（103 条，与 openapi 暴露的路由一一对应）──────────────────────
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
        "GET",
        "/assignments/{assignment_id}/workbench",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="工作台七槽位摘要（UI-05，ENT-021）；可见性与单委托成果清单同一判据（"
        "authz.assert_can_view_assignment），非参与方 404",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}/session-context",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="会话上下文（S2 首片）：查这单所属(货主,组织)与唯一匹配的生效授权，"
        "给前端一个**合法**的授权 id 来源（否则只能猜）。可见性复用委托详情那条判据，"
        "非参与方 404；本端点只查不判 —— 能否建会话仍由 POST "
        "/entrustments/{eid}/sessions 的 assert_can_write_entrustment 决定",
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
        "/assignments/{assignment_id}/complete",
        GUARD_ORG_MEMBER,
        "entrust:assignment:complete",
        owner_scope=True,
        idempotent=True,
        note="**结案**（合同 §6.4 / S4-b）：必须同时过五个维度（任务处置 / 交付证据 / "
        "异常与重评 / 结算含客户对适用版本的确认 / 余额与争议）；缺项即 409 且 `detail` "
        "是**对象**（`{message, missing[]}`）不是字符串。"
        "⛔ 无「跳过前置」入参（PRD：hard checks cannot be bypassed）。"
        "并发靠 `expected_revision` ＋ 条件 UPDATE（两个结案者其一 409）。"
        "货主本人**不能**结案（这是运营方对客户的宣告）⇒ 不带货主旁路。",
    ),
    _r(
        "POST",
        "/assignments/{assignment_id}/cancel",
        GUARD_OWNER_SELF,
        idempotent=True,
        note="仅货主本人撤回；已认领的不能由货主单方撤回",
    ),
    _r(
        "GET",
        "/my-orgs",
        GUARD_AUTHENTICATED,
        note="只列调用者自己的 active 成员关系（org_id / name / member_role / permissions）；"
        "不含他人数据，故登录即可 —— 要求业务权限反而会让新加入组织的人看不到自己的组织",
    ),
    _r(
        "GET",
        "/my-entrustments",
        GUARD_AUTHENTICATED,
        note="只列调用者**自己授权出去**的生效委托（entrustment_id / org_id / org_name / "
        "permissions / status / granted_at）；读 ent_entrustment 而非 ent_org_member"
        "（DR-0012 归属≠权限边界），只投影授权本身、不含组织内部数据，故登录即可",
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
    _r(
        "GET",
        "/assignments/{assignment_id}/artifacts",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="assert_can_view_assignment（货主本人或所属组织内 entrust:view）；"
        "只返回归属恰好等于该委托单的成果，跨单一律 404（DR-0012）",
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
        "GET",
        "/assignments/{assignment_id}/evidence-gaps",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="缺件与交接证据的派生读数；同任务列表（读不加严）",
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
        "/tasks/{task_id}/evidence",
        GUARD_ORG_MEMBER,
        owner_scope=True,
        idempotent=True,
        note="补录证据（缺件的补救入口）；同 start —— 执行是本职，"
        "被指派人本人可做，管理动作另需 dispatch",
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
    # ── 异常与变更案件（exceptions_api.py / ENT-030 / DR-0013）────────────
    _r(
        "POST",
        "/assignments/{assignment_id}/exceptions",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="登记异常/变更案件取**管理动作**权限（entrust:task:dispatch）：DR-0013 没有新增"
        "权限码，而新增权限码要动 ORG_ROLE_PERMISSIONS，等于顺带改变既有角色语义；"
        "org_id 由服务端从 assignment.org_id 派生，请求带不一致的值 → 403",
    ),
    _r(
        "GET",
        "/exceptions",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="缺省视图的可见性前置复用 authz.assert_can_view_assignment（与工作台、成果清单同一"
        "判据），归属按 assignment_id **精确等值**，不放宽到货主或组织（DR-0012）；"
        "`view=org` 分支（DR-0014 §3.1）改走 authz.assert_can_view_org —— 按**单个** org_id 精确"
        "限定（不做 IN、也不接受「我所属全部组织」），两视图参数分别校验（混用 400 / 缺范围 422），"
        "且授权先于计数与分页",
    ),
    _r(
        "GET",
        "/exceptions/{exception_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="svc.load_visible_case 经 authz 单一入口；非参与方 404，不区分「不存在」与「无权知晓」",
    ),
    _r(
        "POST",
        "/exceptions/{exception_id}/links",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="受影响项须与案件同属本委托（跨委托 403）；乐观锁用**案件**的 revision_no，"
        "案件已关闭时拒绝（需先 reopen）",
    ),
    _r(
        "DELETE",
        "/exceptions/{exception_id}/links/{link_id}",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="移除后**重新校验 C2**：不能把 execution-blocking 案件移除成空转阻断；"
        "保留「移除」是为了登记错了能更正，否则 resolved 永不可达",
    ),
    _r(
        "POST",
        "/exceptions/{exception_id}/decision",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="两套状态机**分别**判定转移（同一个 rejected 在两种 kind 下语义相反）；"
        "approved 必须给 basis_revision_id；接口层不含 severity/impact_kind（C3 的形态）",
    ),
    _r(
        "POST",
        "/exceptions/{exception_id}/apply",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="A2 五之一：只认批准快照（不接受临时替换修改内容）、逐目标核对基础版本"
        "（过期 ⇒ 409 + applied_rejected，不产生部分生效）、成果新版本与确认与 link 回写"
        "与状态与事件**同一事务**；纯任务目标不虚构成果版本；"
        "业务开放须等五之二传播闭环（APPLY_OPEN）",
    ),
    _r(
        "POST",
        "/exceptions/{exception_id}/close",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="必须给 closure_disposition 与 evidence_ref（没有一键关闭）；exception 从 "
        "rejected/approved 关闭**不允许** resolved/accepted_residual（驳回不解除真实异常）",
    ),
    _r(
        "POST",
        "/exceptions/{exception_id}/reopen",
        GUARD_ORG_MEMBER,
        "entrust:task:dispatch",
        owner_scope=True,
        idempotent=True,
        note="重开的状态更新与审计追加**同事务**（DR-0013 §3.6 / HO 第 4 条）；reason 必填",
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
    _r(
        "POST",
        "/agent/jobs/{job_id}/adopt",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        owner_scope=True,
        idempotent=True,
        note="采纳 = 创建成果，故权限同创建（assert_can_write_entrustment + entrust:quote:create）；"
        "归属取自作业行的 assignment_id，不由请求体声明（DR-0012）；"
        "同时把信封里声明过的来源记成待核验项（发布门槛的**对象**，见 offers.source_gate）",
    ),
    # ── 对客发布与客户响应（offers_api.py / S3 / BP-03 第 4/5/6/7/10 条）────
    _r(
        "POST",
        "/entrustments/{entrustment_id}/offer-releases",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:publish",
        idempotent=True,
        note="经理人发布**指定成果版本**；额外做作用域自洽（路径授权 == 成果所属授权，"
        "否则 404）。来源门槛在服务层（offers.source_gate）",
    ),
    _r(
        "GET",
        "/entrustments/{entrustment_id}/offer-releases",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        note="经理视角列表：含客户当初看到的快照、来源门槛与响应"
        "（投影由服务端给，不是让前端藏字段）",
    ),
    _r(
        "GET",
        "/my-offer-releases",
        GUARD_OWNER_SELF,
        note="客户入口：只列 customer_user_id == 登录用户 的发布；"
        "归属由服务端从登录身份推导，不接受客户端传入客户 id",
    ),
    _r(
        "GET",
        "/offer-releases/{release_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        note="两条通道同一路径，由服务端按身份选投影：本人那条走**客户投影**，"
        "其余按委托单可见性走经理投影；不可见一律 404。"
        "⚠️ 客户投影不依赖 entrust:view（货主本人直接通过）",
    ),
    _r(
        "POST",
        "/offer-releases/{release_id}/responses",
        GUARD_OWNER_SELF,
        idempotent=True,
        note="只有该委托货主本人可响应：局外人 404、**经理 403**（看得见但无权替客户确认）；"
        "可响应状态只认 released（未发布/已撤回/已被取代一律 409）；"
        "同一次发布只能响应一次，判据是 DB 唯一约束 UNIQUE(release_id)",
    ),
    _r(
        "POST",
        "/offer-releases/{release_id}/withdraw",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:publish",
        idempotent=True,
        note="显式撤回，理由必填；**已被客户响应的发布不能撤回**（接受事实永久保留）",
    ),
    _r(
        "GET",
        "/artifacts/{artifact_id}/source-checks",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        note="来源台账 + 门槛状态：界面要能回答'还差哪几条来源没核'",
    ),
    _r(
        "POST",
        "/artifacts/{artifact_id}/source-checks",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:publish",
        idempotent=True,
        note="登记核验：权限同发布（谁能发布谁负责核验）；**依据必填**，"
        "且只能记 declared 的核验结果（verified/rejected），不能手工造声明",
    ),
    _r(
        "GET",
        "/offer-releases/{release_id}/attachments/{attachment_id}/download",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        note="客户按**发布冻结的授权清单**下载附件（BP-03 第 10 条）。"
        "判据**只有**那份清单：清单外一律 404（不用 403 —— 403 会承认文件存在）。"
        "⚠️ 有意**不**复用 attachments_api.load_visible_attachment：客户对整条授权下的附件"
        "都有可见性，复用等于把内部底稿一起开给他。经理走本端点看到的是**客户视角**那几份",
    ),
    # ── 合同派生（contracts_api.py / S3 / BP-03 第 8 条 / D1-08）──────────────
    _r(
        "POST",
        "/offer-releases/{release_id}/contract",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        idempotent=True,
        note="从**已接受的那条发布**派生合同核对稿（BP-03 第 8 条：合同从已接受事实派生）。"
        "权限取「产出成果」那一档（quote:create），**不是** quote:publish —— "
        "拟稿与对客发布是可以分给两个人的两个动作，合用一个权限会抹掉这条区分。"
        "三类前置：未响应 / 已拒绝 ⇒ 409，被接受的不是对客报价 ⇒ 400，"
        "已派生过 ⇒ 409 并回已存在的那份合同 id（判据是 UNIQUE(release_id)，不是先查后写）",
    ),
    _r(
        "GET",
        "/offer-releases/{release_id}/contract",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="派生关系 + **逐字段来源表**（D1-08 的 inspection 面）；未派生过 ⇒ 404"
        "（回空壳会让「还没派生」与「派生了一份空合同」长得一样，而后者最该被发现）。"
        "⚠️ 有意**不**给货主本人放行：来源表里是 release:12@v3 / leg:4 这类**内部编号**，"
        "客户看合同走已有发布通路（冻结快照）。故用 assert_can_view_org（无货主旁路）",
    ),
    _r(
        "POST",
        "/contracts/{contract_artifact_id}/signature-evidence",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        idempotent=True,
        note="就合同的**某个版本**记一条签署证据（§10.1 第 7 步后半 / D1-08 的 linked evidence）。"
        "⛔ `mode` **不是入参**：恒为 labeled_sample 由服务端写死 —— 让调用方能传 mode=live "
        "就等于让界面自称「已完成电子签署」，与合同 §3.2 / D1-08 直接冲突。"
        "证据形态只接受三个登记取值（未知形态 ⇒ 400）：未登记的形态进库后，"
        "「这份证据到底存不存在实物」就再无答案。同一版同一形态只记一条 ⇒ 重放 409（带已存在的 id）",
    ),
    _r(
        "GET",
        "/contracts/{contract_artifact_id}/signature-evidence",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="该合同**全部版本**的签署证据清单（D1-08 inspection 面）。"
        "一份都没记过 ⇒ 空列表 + has_items=false（**不是 404**）："
        "「合同存在但还没记证据」是正常中间态，与「这个 id 没有对应物」语义不同。"
        "⚠️ 同派生一样**不**给货主本人放行：证据行带内部编号与审计措辞，"
        "客户看合同走已有发布通路（冻结快照）",
    ),
    # ── 运力确认与有效期（capacity_api.py / S3 / BP-03 第 3 条 / D1-06）───────
    _r(
        "POST",
        "/assignments/{assignment_id}/capacity-candidates",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        idempotent=True,
        note="登记候选运力事实（吨位/船数/是否拆批/单价口径/有效期/证据）。"
        "**登记不是确认** —— 本端点不产生任何「已确认运力」（BP-03 Exit evidence："
        "A chosen quotation alone does not create confirmed capacity）。"
        "只拒绝结构上不可能有意义的输入（承运人空、吨位非正数、单价与计价单位半边缺）；"
        "「还没有证据/还没有效期」不在这里拒 —— 它们在**确认时**由规则判，"
        "压到登记上会让 expired cannot be confirmed 那条规则永远触发不了",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}/capacity-candidates",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="候选运力清单（第 2 条「两家可比」要看的运力/单价口径/有效期/证据）。"
        "⚠️ 有意**不**给货主本人放行：候选行带承运人与**供应商单价**（供应商侧成本口径），"
        "客户侧只有对客报价的冻结快照",
    ),
    _r(
        "POST",
        "/assignments/{assignment_id}/capacity-confirmations",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:quote:create",
        idempotent=True,
        note="**确认运力**：确定性规则闸门（需求口径/证据/有效期/容量适用性四条，"
        "全部评估、逐条带比较值）通过才产出 procurement_confirm 成果 + 确认记录。"
        "过期或不适用 ⇒ 409 并回逐条判定（不是一句「不适用」）；"
        "已确认过 ⇒ 409 并回已存在的那条（判据是 UNIQUE(candidate_id)，不是先查后写）。"
        "请求体只带 candidate_id 与 agreed_scope：事实一律从候选行读，"
        "否则「确认的内容」与「候选运力」可以不一致而界面上看不出来",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}/capacity-confirmations",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="该委托的运力确认清单（含逐规则判定）。同一理由不给货主放行："
        "判定里写着需求量、运力、缺口这些内部比较过程",
    ),
    _r(
        "GET",
        "/capacity-confirmations/{confirmation_id}",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="确认详情：**冻结输入**（确认那一刻的吨位/有效期/证据快照）+ 逐规则判定。"
        "不存在 ⇒ 404，不回空壳（空壳会让「没做过确认」与「做了一条空确认」长得一样）",
    ),
    _r(
        "GET",
        "/capacity-confirmations/{confirmation_id}/recheck",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="**只读复算**：用当前事实重跑同一套规则，回答「这条确认现在还成立吗」，"
        "并把当前值与冻结值的差异列出来（changed_fields）。**不写任何行** —— "
        "正式重做属 S4 变更流程；这里只是让 D1-09 的「900 吨候选变更后不再适用」"
        "可被看到，而不是只存在于模型意见里",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}/quantity-changes",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="委托货量变更历史（append-only，D1-09 第 8 步）。写侧在 `exceptions.apply_case`"
        "—— 经审批的变更应用到 `ent_assignment.quantity` 时同步留一行。"
        "⚠️ 读侧与运力那一组同口径**不给货主本人放行**：`basis` 是经理写的变更依据，"
        "可能带内部口径（货量本身客户在委托详情里看得到）。"
        "空列表是正常答复（这单没改过货量），**不是** 404 —— 与 "
        "`GET /capacity-confirmations/{id}` 的「不回空壳」口径相反，判据是资源本身："
        "集合可以为空，单条记录不存在就是不存在",
    ),
    # ── 运输计划与必需任务前置（plan_api.py）─────────────────────────────
    _r(
        "GET",
        "/assignments/{assignment_id}/plan",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="三段计划（ent_leg）+ 必需任务与固定前置（ent_workflow_task）；"
        "可见性复用委托详情那条判据（authz.assert_can_view_assignment），非参与方 404。"
        "⚠️ 与紧邻上面的运力那一组**取向相反**：这组**给货主本人放行** —— "
        "航段是客户自己交进来的起讫路线、任务标题与前置不含内部成本口径；"
        "而承运人/供应商单价/需求量与缺口在运力那组，那组一律不给货主放行。"
        "判据是「这条通道上有没有内部信息」，不是「是不是客户」。"
        "读路径不带写权限：§10.1 第 4 步原文是 Show ...",
    ),
    # ── 航段命令：建段 / 改段（留版本）/ 版本历史（legs_api.py）──────────────
    # HO 2026-09-17 裁定三条：谁能建段＝**任意验收者/测试者**、**不强制 公–水–公**、
    # **改段保留版本**。三条一起落地成下面这三条登记 —— 口径的正文在
    # migrations/ent_leg_revision.py 的模块文档，这里只登记接口面。
    _r(
        "POST",
        "/assignments/{assignment_id}/legs",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        idempotent=True,
        note="**建一段航段**，同时写下第 1 版历史（ent_leg_revision，append-only）。"
        "判据与上面的计划读通道**完全同一份**（authz.assert_can_view_assignment）："
        "货主本人或所属组织成员 ⇒ 「任意验收者/测试者都能建段」。"
        "⛔ 这不是「任何登录用户」：非参与方一律 404（不泄漏存在性）。"
        "⚠️ 这是本支线**唯一**一条「写动作不要求写入类权限常量」的命令 —— "
        "依据是 HO 口径，且航段是客户自己交进来的方案事实（与会改内部成本口径的"
        "报价/确认不同类）。**不是遗漏**：看到它别顺手补一个 PERM_* 上去。"
        "⚠️ 不校验 mode 取值组合与段数（裁定：不强制 公–水–公）；只做结构完整性 —— "
        "mode 非空、起终点非空、seq ≥1 且委托内唯一（唯一键在 DB 上，撞号 409）。",
    ),
    # ── 费用行：登记 / 确认 / 争议 / 处置 / 读合计（charges_api.py）────────────
    # HO 0918-2 裁定：Q1=C（合计按**币种 × 收付方向**分开，不跨币种相加）、
    # Q2=B（争议必须**显式处置 ＋ 证据**，并给出「最终金额 ＋ 是否计入」——
    # `resolved` 一词决定不了是否计入）、Q3=A（`waiting_time` 用**普通费用行**，
    # 不另建实体、不建计费引擎）。
    # 判据取「这条通道上有没有内部信息」：费用行带 `counterparty` 与 `basis`
    # —— 谁付谁、按什么算，是**内部成本口径** ⇒ **一条都不给货主本人放行**
    # （与紧邻上面的运力那一组同型；而计划那一组相反，理由写在那条 note 里）。
    # ⚠️ 客户侧投影（"只看对客费用与证据白名单"，裁定 Q5 第 2 条）**本切片没有**
    # —— 它需要"哪些费用是对客的"这个口径，落在 S7-3；这里不假装已支持。
    _r(
        "GET",
        "/assignments/{assignment_id}/charges",
        GUARD_ORG_MEMBER,
        "entrust:view",
        note="费用行清单 ＋ 合计（按币种 × 收付方向分组）。"
        "判据＝有组织边界、**不给货主本人放行**：行上带对手方与计费依据，是内部成本口径。"
        "空列表是正常答复（这单还没记过费用），**不是** 404。"
        "每组回 `counted_lines` / `excluded_lines`，让合计数可被复核。",
    ),
    _r(
        "POST",
        "/assignments/{assignment_id}/charges",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:settlement:create",
        idempotent=True,
        note="登记一条费用行（状态 draft ⇒ **草稿不进合计**）。"
        "权限沿用本仓已有的 `entrust:settlement:create`，不新造权限码。"
        "`basis` 必填（没有依据的费用行不可核对）；`quantity` 与 `unit` 必须成对。"
        "⚠️ **不校验金额正负**：负数的语义没有裁定过，本切片不自造这条约束。",
    ),
    _r(
        "POST",
        "/charges/{charge_id}/confirm",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:settlement:create",
        idempotent=True,
        note="`draft → confirmed`（确认之后才进合计）。"
        "状态机与 `revision` 乐观锁都在服务层的**唯一一处** `_transition` 里："
        "`rowcount == 0` ⇒ 409（要么状态不对、要么版本过期），不静默覆盖。",
    ),
    _r(
        "POST",
        "/charges/{charge_id}/dispute",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:settlement:create",
        idempotent=True,
        note="`confirmed → disputed`（**争议行不进合计** —— 合同 S4 段第 10 条原文）。"
        "只能对**已确认**的费用提争议（草稿本就不计入）。`reason` 必填："
        "只标「有争议」而不写为什么，合计的差异无从复核。",
    ),
    _r(
        "POST",
        "/charges/{charge_id}/resolve",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:settlement:create",
        idempotent=True,
        note="`disputed → resolved|rejected`：**显式处置 ＋ 依据**（裁定 Q2=B）。"
        "⛔ `counts_in_total` 必填且**不从 `outcome` 推导** —— 认可可能是「全额计入」、"
        "也可能是「认可但不计入」，调减是「按新金额计入」，拒绝是「不计入」；"
        "把这三件事压成一条规则正是裁定要消除的歧义。"
        "`counts_in_total=True` 时 `final_amount` 必填；为假时允许留空。",
    ),
    # ── 结算与收付依据（settlement_api.py）──────────────────────────────────
    # ⚠️ 这 8 条**分成三条通道**，判据刻意不同（详见 settlement_api.py 模块文档）：
    #    内部（版本清单/详情/派生）＝ 组织成员、**货主也 404**（含内部成本）；
    #    对客（customer-view）＝ **有货主旁路**（这条通道的意义就是"客户能看"）；
    #    仅客户本人（customer-confirm）＝ 只有 `owner_user_id` 能做。
    _r(
        "POST",
        "/assignments/{assignment_id}/settlements",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:settlement:create",
        owner_scope=True,
        idempotent=True,
        note="按**当前计入合计**的费用行生成一个**新**结算版本（快照）。"
        "⚠️ 费用变了就再调一次 ⇒ v2；**旧版本一个字不改**，旧确认因此保留、"
        "但结构上替不了新版本过关。没有可结算的费用行 ⇒ 400（空版本不该存在）；"
        "计入行跨多币种 ⇒ 400（本期只支持 CNY，跨币种相加是明确不做的）。",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}/settlements",
        GUARD_ORG_MEMBER,
        owner_scope=True,
        note="版本链（升序）＋ `applicable_settlement_id`（＝**最大版本号**那一行）。"
        "⚠️ 货主本人也 404：版本带 `internal_total`（我们付给供应商的成本）。"
        "「适用版本」是**推导**出来的、不存字段 —— 存一个就会出现"
        "「字段说有、链上没有」的分叉。",
    ),
    _r(
        "GET",
        "/settlements/{settlement_id}",
        GUARD_ORG_MEMBER,
        owner_scope=True,
        note="内部详情（含内部成本合计与**快照行**：每条带 charge_id ＋ 当时 revision ＋"
        " 计入金额，所以费用行后来被改也不影响「这一版当时算的是什么」）。",
    ),
    _r(
        "POST",
        "/settlements/{settlement_id}/approve",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:settlement:create",
        owner_scope=True,
        idempotent=True,
        note="内部确认（`draft → approved`）。⛔ 只能确认**适用版本**（最大版本号）："
        "批准一个已被取代的版本在业务上没有意义，只会让「适用版本批准了吗」多出一个答案。",
    ),
    _r(
        "GET",
        "/settlements/{settlement_id}/customer-view",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="**对客投影**（裁定 Q5 第 2 条）：只出 `direction=receivable` 的行、"
        "且字段按**白名单**裁剪 —— ⛔ 投影是**新建字典**而不是「从内部投影里删几个键」，"
        "后者在加列时会默认把新列漏给客户，而漏出去的内部成本收不回来。"
        "⚠️ 组织成员也放行：让经理能**预览客户看到的东西**（这个投影里本就没有内部字段）。",
    ),
    _r(
        "POST",
        "/settlements/{settlement_id}/customer-confirm",
        GUARD_OWNER_SELF,
        owner_scope=True,
        idempotent=True,
        note="**客户确认该精确版本**（裁定 Q5 第 1、4 条）—— 确认挂在**这一行**上，"
        "不在委托上、也不在「最新版本」这个概念上。⛔ 一版只确认一次（要改口径请出**新版本**）；"
        "⛔ 必须先内部确认（`draft` 上的客户确认没有意义）。"
        "⚠️ 组织成员（看得见这个版本）来做 ⇒ **403**（看得见但这不是你能做的动作）；"
        "看不见的第三方 ⇒ 404。",
    ),
    _r(
        "POST",
        "/settlements/{settlement_id}/payments",
        GUARD_ENTRUSTMENT_WRITE,
        "entrust:settlement:create",
        owner_scope=True,
        idempotent=True,
        note="记一条收付依据。⛔ `mode` **不是入参**（恒 `labeled_sample`）："
        "能传 `live` 就等于让系统自称「资金已真实到账」（裁定 Q5 第 5 条）。"
        "只能挂在**已确认**的版本上（要说清依据哪一版）；`ref` 必填（没凭据的收付不可核对）；"
        "累计收付**不得超过该方向合计** —— 超出会让余额变负，而负数余额没有业务含义。",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}/financial-status",
        GUARD_ORG_MEMBER,
        owner_scope=True,
        note="`financial_status` 派生（§5.3.2 四条判据逐条落成 `blockers`）："
        "① 有草稿/有争议的费用行；② 结算待批准（含**客户未确认该精确版本**与"
        "**适用版本已过期**两种子情形）；③ 有未关闭的案件；④ 余额未结清。"
        "四个都不成立才 `settled`。⚠️ `not_started` **只能**表示「确实没有任何"
        "费用/结算/收付事实」，⛔ 不是「派生还没接好」的遮羞布；"
        "含内部成本余额 ⇒ **货主本人也 404**。",
    ),
    _r(
        "PATCH",
        "/assignments/{assignment_id}/legs/{leg_id}",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        idempotent=True,
        note="**改一段航段**；旧版本**不覆盖**，每次改追加一版历史（裁定第三条）。"
        "两处 400 是有意的：一个字段都没传、或传了但与当前值完全相同 —— "
        "两者都不该在历史里留一版（版本历史是给人读「改过什么」的，"
        "空改动会把它变成噪音）。改 seq 撞到别的段 ⇒ 409（由 DB 唯一键判，不先查后写）。"
        "路径上的 assignment_id **必须**就是该航段的所属委托，否则 404（作用域自洽）。",
    ),
    _r(
        "GET",
        "/assignments/{assignment_id}/legs/{leg_id}/revisions",
        GUARD_ENTRUSTMENT_VIEW,
        "entrust:view",
        owner_scope=True,
        note="某一段的**全部历史版本**（按 revision_no 升序 = 改动先后）。"
        "每行是**当时的快照**，不是「指向当前行」—— 否则 ent_leg 一改历史也跟着变，"
        "版本就白留了。⚠️ 读历史前先确认该航段属于这张委托单："
        "否则「历史」会变成绕过可见性判定的后门。",
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
