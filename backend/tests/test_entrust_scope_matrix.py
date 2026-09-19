"""ENT-010：声明式权限矩阵（`scope_matrix`）的自检。

这组断言不是"再测一遍业务"，而是让**结构性错误无法悄悄进入 develop**：

1. **双向覆盖** —— 新增端点不声明 → 失败；声明了不存在的端点 → 失败。
2. **权限常量有效性** —— 引用 `access` 里不存在的权限 → 失败。
3. **写端点幂等完备** —— 写端点既不声明幂等、也不进豁免表 → 失败。
4. **开关关闭全 404** —— `ENTRUST_ENABLED=false` 时**任何**端点仍可达 → 失败
   （AC-22 的结构性版本：此前只覆盖"三组端点"，现在覆盖全部，且新增端点自动纳入）。

## 本文件**不**做什么

不检查"某行代码里是否出现 `assert_can`"。守卫可以合法地落在端点、模块内助手
（如 `agent_api._session_write_guards`）或服务层（如 `tasks.authorize`），
源码匹配会随正当重构漂移 —— 那会把一次正确的重构判成失败，进而训练团队绕过门禁。
`note` 里记录了守卫落点供人审查，但它不是断言依据。
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.modules.entrust import access as access_mod
from app.modules.entrust import scope_matrix as sm

# ── 1. 双向覆盖 ────────────────────────────────────────────────────────────


def _openapi_entrust_routes() -> set[tuple[str, str]]:
    """从 openapi 取委托支线的全部 (方法, 相对路径)。

    本项目的 FastAPI 用 `_IncludedRouter` 惰性挂载，直接遍历 `app.routes` 只能拿到
    占位对象、拿不到子路由，所以必须走 `app.openapi()`（它会展开全部路由）。
    """
    from app.main import app

    prefix = sm.API_PREFIX
    routes: set[tuple[str, str]] = set()
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix) :] or "/"
        for method in ops:
            routes.add((method.upper(), relative))
    return routes


def test_matrix_covers_every_exposed_route():
    """openapi 暴露的每条路由都必须在矩阵里声明。"""
    missing = sorted(_openapi_entrust_routes() - set(sm.matrix_keys()))
    assert not missing, (
        f"以下端点已暴露但未在 scope_matrix.SCOPE_MATRIX 声明：{missing}。"
        "新增端点必须声明 scope（guard / permission / owner_scope / idempotent / note）。"
    )


def test_matrix_has_no_stale_entries():
    """矩阵里不能有已不存在的端点（僵尸条目会让人以为还有这条接口）。"""
    stale = sorted(set(sm.matrix_keys()) - _openapi_entrust_routes())
    assert not stale, f"scope_matrix 声明了不存在的端点：{stale}"


def test_matrix_keys_are_unique():
    keys = [sm.scope_key(item.method, item.path) for item in sm.SCOPE_MATRIX]
    assert len(keys) == len(set(keys)), "矩阵存在重复条目"


def test_matrix_size_matches_baseline():
    """条目数锁定为**基线值**（当前 74）。

    数量变化本身不是错误，但**必须是有意的**：增删端点时同时改这里，
    强制在 PR 里显式说明"为什么端点集合变了"。

    48 → 49（ENT-012 第二切片）：新增 `GET /my-orgs` —— 组织选择器的数据源。
    49 → 51（ENT-020 成果归属切片 / DR-0012）：新增
    `GET /assignments/{assignment_id}/artifacts`（单委托成果清单）与
    `POST /agent/jobs/{job_id}/adopt`（采纳作业提案为成果）。
    51 → 52（ENT-021 工作台首片 / DR-0010）：新增
    `GET /assignments/{assignment_id}/workbench`（UI-05 七槽位摘要投影）。
    52 → 60（ENT-030 异常与变更案件 / DR-0013 A1）：新增 8 条 ——
    登记案件、列案件、案件详情、登记/移除受影响项、记录决定、关闭、重开。
    `exceptions` 槽位此前是本支线**唯一**仍「本期未开放」的槽位，这 8 条是它开放的结构前提。
    60 → 61（ENT-033 A2 五之一）：新增 `POST /exceptions/{exception_id}/apply`（应用已批准变更）。
    61 → 62（S1 客户受理 / DEMO-1 §3.1）：新增 `GET /my-entrustments` ——
    货主侧"我授权出去的组织"，是 UI-07 选择**提交目标**的唯一合法数据源。
    `ent_entrustment` 此前无任何 HTTP 面，`/my-orgs` 读的是 `ent_org_member`，
    两者不是一回事（DR-0012），拿后者顶上会产出"能选但必然 403"的选项。
    62 → 63（S2 首片 / HO 0917-2 执行顺序 2）：新增
    `GET /assignments/{assignment_id}/session-context` —— 建会话要
    `entrustment_id`，而 `/my-orgs` 只回成员身份、`/my-entrustments` 只回货主自己
    授权出去的授权，经理两边都拿不到本单那一条。缺了它，前端只剩"猜一个 id 试到不报错
    为止"，而权限判定不能建立在猜测上（与 61→62 那条同源，都是"服务端说请指定、
    前端必须有合法手段拿到可选项"）。
    63 → 71（**S3 纵向切片**：发布 → 冻结内容 → 客户响应）：新增 8 条 ——
    发布指定成果版本（1）、按授权列发布记录（2）、客户列"我收到的发布"（3）、
    按身份给发布详情（4）、客户响应（5）、撤回发布（6）、来源台账读（7）与写（8）。
    ⚠️ 这批端点的**投影分叉**（经理 / 客户两条通道）由服务端决定，不靠前端藏字段；
    其中 `POST /offer-releases/{id}/responses` 是**唯一**一条"身份即权限"的写端点
    （货主本人；经理来调一律 403，不是 404 —— 他看得见，但无权替客户确认）。
    71 → 72（**S3 客户下载接线**，BP-03 第 10 条）：新增
    `GET /offer-releases/{id}/attachments/{aid}/download` —— 判据**只有**发布时冻结的
    `authorized_attachment_ids`，清单外 404。有意不复用
    `attachments_api.load_visible_attachment`：客户对整条授权下的附件都有可见性，
    复用会把内部底稿一并开给他（"客户能拿到什么"由**发布那一刻**决定，不由事后可见性决定）。
    72 → 74（**S3 合同派生**，BP-03 第 8 条 / D1-08）：新增 2 条 ——
    从已接受发布派生合同核对稿（1）、读派生关系与**逐字段来源表**（2）。
    ⚠️ 读取那条**刻意不给货主本人放行**（`assert_can_view_org`，没有货主旁路）：
    字段来源表里是 `release:12@v3` / `leg:4` 这类内部编号，客户看合同走的是
    **已有的发布通路**（把这份 contract_review 发布出去、读冻结快照）。
    这是上一轮修掉的那类投影泄漏的**结构性预防**：不是"两个入口判据不一致"，
    而是"这条通道本就不该有客户面"。
    74 → 80（**S3 运力确认与有效期**，BP-03 第 3 条 / D1-06）：新增 6 条 ——
    登记候选运力（1）、候选清单（2）、**确认运力**（3）、确认清单（4）、
    确认详情（5）、**只读复算**（6）。
    ⚠️ 这 6 条**一条都不给货主本人放行**，全部 `GUARD_ORG_MEMBER` / 写侧
    `GUARD_ENTRUSTMENT_WRITE`：候选行带承运人与供应商单价、确认行带
    `agreed_amount`/`supplier`、判定里写着需求量与缺口 —— 整组都是内部成本口径。
    客户了解商业承诺走的是**发布通路**（冻结快照），不是这一组。
    确认那条的状态码分工：判定不通过 ⇒ 409（回**逐条**判定，含通过的），
    已确认过 ⇒ 409（回已存在的记录 id，判据是 `UNIQUE(candidate_id)`），
    输入有毛病 ⇒ 400。程序性错误（闸门发现规则没跑全）刻意**不**接 —— 那必须是 500。
    80 → 81（**S3 运输计划读模型**，BP-03 第 1 条 / 合同 §10.1 第 4 步）：新增
    `GET /assignments/{assignment_id}/plan` —— 三段计划（`ent_leg`）+ 必需任务与固定前置。
    ⚠️ 这一条**与上面运力那 6 条取向相反**：它**给货主本人放行**
    （`GUARD_ENTRUSTMENT_VIEW` + `assert_can_view_assignment`），因为航段是客户
    自己交进来的起讫路线、任务标题与前置不含内部成本口径；而承运人 / 供应商单价 /
    需求量与缺口在运力那组，那组一条都不给货主放行。判据是「这条通道上有没有内部信息」，
    不是「是不是客户」—— 两处的模块文档各自记了理由，好让下一个加端点的人知道往哪边靠。
    另：**该切片当时只做读**。建段命令需口径裁定（谁能建 / 是否强制 公—水—公 /
    改段是否留版本），**不发明规则**，也不给一个"什么都能塞"的写口 ——
    没有规则约束的写口比没有写口更坏。§10.1 第 4 步是 `Show ...`，
    演示里计划来自**部署阶段的种子**。

    81 → 84（**航段命令**，HO 2026-09-17 裁定落地；口径正文见
    `migrations/ent_leg_revision.py` 的模块文档）：

    * `POST /assignments/{assignment_id}/legs` —— 建段（同时写第 1 版历史）；
    * `PATCH /assignments/{assignment_id}/legs/{leg_id}` —— 改段（旧版本保留）；
    * `GET /assignments/{assignment_id}/legs/{leg_id}/revisions` —— 版本历史。

    裁定三条：
    ① 谁能建段＝**任意验收者/测试者** ⇒ 判据**复用计划读通道那一份**
    （`GUARD_ENTRUSTMENT_VIEW` + `assert_can_view_assignment`，**有**货主旁路），
    **不新增权限常量、不设角色门槛**；⛔ 但**不等于**"任何登录用户"，
    非参与方一律 404。⚠️ 全矩阵只有这两个写端点是"**不要求写入类权限常量**"的 ——
    **这是裁定，不是遗漏**，看到它别顺手补一个 `PERM_*` 上去。
    ② **不强制 公–水–公**：不校验 `mode` 组合与段数，只做结构完整性
    （mode 非空、起终点非空、seq ≥1 且委托内唯一）。
    ③ **改段保留版本**：append-only 的 `ent_leg_revision`；`ent_leg` 继续存**当前**
    状态、其 `UNIQUE (assignment_id, seq)` **一个字不动**
    ⇒ 这也是"另开一张表、而不是给 `ent_leg` 加 `revision_no`"的原因
    （后者要重建表，越过"迁移只增不改"这条线）。

    84 → 86（**合同签署证据**，§10.1 第 7 步后半 / D1-08 的 `linked evidence`；
    口径正文见 `migrations/ent_contract_signature.py` 的模块文档）：

    * `POST /contracts/{contract_artifact_id}/signature-evidence` —— 就合同的某个版本记一条；
    * `GET  /contracts/{contract_artifact_id}/signature-evidence` —— 全部版本的清单。

    ⛔ `mode` **不是入参**：恒为 `labeled_sample`，由服务端写死 —— 让调用方能传
    `mode=live` 就等于让界面自称"已完成电子签署"，与合同 §3.2 / D1-08 直接冲突。
    两条都**只有经理通道**（与派生同一口径：证据行带内部编号与审计措辞，
    客户看合同走已有发布通路，不为它新开一条客户面）。

    87 → 92（**S7-1 费用与争议**，§10.1 第 10 步 / 合同 S4 段第 9–10 条；
    口径正文见 `migrations/ent_charge.py` 与 `charges.py` 的模块文档）：
    新增 5 条 —— 读清单与合计、登记、确认、提争议、处置。

    ⚠️ 这 5 条**一条都不给货主本人放行**（读 `GUARD_ORG_MEMBER`、写
    `GUARD_ENTRUSTMENT_WRITE`）：行上带 `counterparty` 与 `basis`
    —— 谁付谁、按什么算，是**内部成本口径**，与紧邻的运力那一组同型。
    客户侧投影（"只看对客费用及证据白名单"，裁定 Q5 第 2 条）**不在本切片**：
    它需要"哪些费用是对客的"这个口径，落在 S7-3。

    ⛔ `resolve` 那条**刻意要三个字段**（`outcome` / `counts_in_total` / `final_amount`）：
    裁定 Q2=B 明确 `resolved` 一词**决定不了**是否计入 —— 把"处置了 / 金额定了 /
    计不计入"压成一个状态，就是合计出现歧义的来源。

    92 → 94（**S7-2 交接与缺证据**，§10.1 第 10 步 / 合同 S4 段第 1、2、4、8 条）：

    * `GET  /assignments/{assignment_id}/evidence-gaps` —— 缺什么、谁在等、交接齐没齐；
      **派生**读数（不落库、不新建实体），**读不加严**：与任务列表同一格
      （`entrust:view` ＋ 货主旁路），因为"还缺什么"是参与方本来就该看见的事。
    * `POST /tasks/{task_id}/evidence` —— 在**原任务**上补录一条证据。

    ⛔ **不另造工作流**（HO 0918-2 边界 1）：缺件条件不是新实体，它就是任务自己的
    `waiting` ＋ `wait_reason`；补救出口也不是新流程，就是在**本任务**上补齐证据。
    因此这里**没有**第四个 guard 值、也**没有**新的权限常量 —— 补录与 `start`/`wait`/
    `complete` 同一格（执行是本职，被指派人本人可做）。
    ⛔ **也不新建"交接成果"实体**：交接＝`task_type = handover` 的任务
    （合同 S4 段 `Do not invent a "handover artifact"`），缺件视图只是把它汇总出来。

    94 → 102（**S7-3 结算与收付依据**，§10.1 第 11 步 / 合同 S4 段第 11 条；裁定 Q5）：
    新增 8 条，**分成三条通道**，判据刻意不同：

    * **内部**（版本清单 / 详情 / `financial-status`）—— `GUARD_ORG_MEMBER`，
      **货主本人也 404**：版本带 `internal_total`（我们付给供应商的成本），
      派生读数还带内部成本余额 ⇒ 与费用行同一口径；
    * **对客**（`GET /settlements/{id}/customer-view`）—— `GUARD_ENTRUSTMENT_VIEW`，
      **有货主旁路**：这条通道存在的意义就是"客户能看"；组织成员也放行是为了让经理
      能**预览客户看到的东西**（投影里本就没有内部字段）；
    * **仅客户本人**（`POST /settlements/{id}/customer-confirm`）—— `GUARD_OWNER_SELF`：
      裁定 Q5 第 4 条把"客户确认"定为独立事实，经理人不得代客户确认。
      ⚠️ 组织成员（看得见这个版本）来做 ⇒ **403**（看得见但这不是你能做的动作）；
      看不见的第三方 ⇒ 404。两者的区别是刻意的。

    ⭐ 三处的差别**不是配置**，是三个不同的业务问题；并成一条"是不是参与方"的判据，
    就会出现"经理能替客户确认"或"客户看不到自己的结算单"。

    ⚠️ S7-2 / S7-3 / 界面入口三片**已合并**（#160 ＋ #161 ＋ #162）⇒ 101；
    ⭐ S4-b 的 `complete`（合同 §6.4）**再 +1 ⇒ 103**：结案是**一条命令一个端点**，
    它的五个维度前置全部在服务层内评估（不新增端点），所以矩阵只多这一条。
    ⭐ S4-b 的**界面入口**（`GET …/closure-readiness`）**再 +1 ⇒ 104**：结案
    **不可逆**，界面要先让人看见清单再执行，所以补一个**只读前置**；它与命令
    **共用同一把锁**（`entrust:assignment:complete`）⇒ 权限码没有再多一个。
    ⭐ S4-c 的**受控重开**（`POST …/reopen`）**再 +1 ⇒ 105**：它另开一个权限码
    （`entrust:assignment:reopen`，能结案 ≠ 能撤销结案），并带**必填理由**与留痕。
    """
    assert len(sm.SCOPE_MATRIX) == 105


# ── 2. 声明本身的自洽性 ────────────────────────────────────────────────────


def test_declared_permissions_exist_in_access_module():
    known = {v for k, v in vars(access_mod).items() if k.startswith("PERM_") and isinstance(v, str)}
    unknown = sorted(sm.declared_permissions() - known)
    assert not unknown, f"矩阵引用了 access 模块里不存在的权限：{unknown}"


def test_guards_are_known():
    unknown = sorted({item.guard for item in sm.SCOPE_MATRIX} - sm.GUARDS)
    assert not unknown, f"矩阵使用了未定义的 guard：{unknown}"


def test_every_route_has_a_note():
    """每条都要有一句授权语义说明 —— 空 note 等于没声明。"""
    missing = [f"{i.method} {i.path}" for i in sm.SCOPE_MATRIX if not i.note.strip()]
    assert not missing, f"这些条目没有 note：{missing}"


def test_authorized_guards_declare_a_permission():
    """「经授权」类 guard 必须给出具体权限，否则等于把判定推给实现细节。"""
    weak = [
        f"{i.method} {i.path}"
        for i in sm.SCOPE_MATRIX
        if i.guard in (sm.GUARD_ENTRUSTMENT_WRITE,) and i.permission is None
    ]
    assert not weak, f"声明为 entrustment_write 却没给 permission：{weak}"


# ── 3. 写端点幂等完备 ─────────────────────────────────────────────────────


def test_write_routes_are_idempotent_or_exempt():
    offenders = [f"{i.method} {i.path}" for i in sm.write_routes_without_idempotency()]
    assert not offenders, (
        f"以下写端点既未声明 idempotent、也不在 IDEMPOTENT_EXEMPT：{offenders}。"
        "写操作重复提交会产生重复副作用，必须显式处理（加幂等键，或写清为什么不需要）。"
    )


def test_exemptions_have_reasons_and_are_real_routes():
    for key, reason in sm.IDEMPOTENT_EXEMPT.items():
        assert reason.strip(), f"豁免 {key} 没写理由"
        assert key in sm.matrix_keys(), f"豁免 {key} 不是矩阵里的真实端点"


def test_exemptions_are_not_redundant():
    """已声明幂等的端点不该再进豁免表 —— 两边都写会让人以为幂等是「可选」的。"""
    redundant = [k for k in sm.IDEMPOTENT_EXEMPT if (item := sm.describe(*k)) and item.idempotent]
    assert not redundant, f"这些端点已声明幂等，不该再进豁免表：{redundant}"


# ── 4. 开关关闭时全部端点不可达 ────────────────────────────────────────────


def _fill_path_params(path: str) -> str:
    """把 `{param}` 替换成具体值 1（只为拼出可请求的 URL）。"""
    return re.sub(r"\{[^}]+\}", "1", path)


def test_all_routes_return_404_when_switch_off(client: TestClient, monkeypatch):
    """`ENTRUST_ENABLED=false` → **全部**端点 404。

    `require_entrust_enabled` 是 FastAPI 依赖，在请求体解析**之前**执行，
    所以写端点不需要构造合法请求体也能观察到 404。
    """
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)

    bad: list[str] = []
    for item in sm.SCOPE_MATRIX:
        url = sm.API_PREFIX + _fill_path_params(item.path)
        payload = {} if item.method in sm.WRITE_METHODS else None
        resp = client.request(item.method, url, json=payload)
        if resp.status_code != 404:
            bad.append(f"{item.method} {item.path} -> {resp.status_code}")
    assert not bad, f"开关关闭时这些端点没有返回 404：{bad}"


def test_switch_off_does_not_hide_other_modules(client: TestClient, monkeypatch):
    """反向确认：开关只影响委托支线，不该把别的模块一起关掉。"""
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    resp = client.get("/healthz")
    assert resp.status_code == 200
