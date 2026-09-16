# DEMO-1 执行计划（新开发节奏）

| 项 | 值 |
| --- | --- |
| 对应合同 | `docs/entrust/milestones/DEMO-1-contract-v1.0.md`（ENTRUST-DEMO-1 v1.0） |
| 合同对应节 | §8.1 S0 切片；§8.2 首批实现交付物 |
| 本文件状态 | **生效（2026-09-16 经 HO 采纳）**；本文不代表任何完成度 |
| 基线差异表 | 本文件 §3（S0 交付物 1） |
| 工作项映射 | 本文件 §4（S0 交付物 2、3） |
| R1 余量台账 | `docs/entrust/milestones/DEMO-1-r1-remainder.md`（S0 交付物 5） |

> ⚠️ 本文**不写完成度百分比**。合同 §8.2 明文：
> `Do not make a new percentage-complete promise by counting PRs, tests, endpoints, or pages.
> Report which business exits are demonstrable.`
> 所以本文只回答一个问题：**哪些业务出口现在可演示、哪些不能**。

---

## §0 基线差异：合同基线 vs 当前 develop

| 项 | 值 |
| --- | --- |
| 合同声明的计划基线 | `e9a4a9d`（= PR #108 落地） |
| 本计划落笔时 `develop` | `57b6315`（= #108 + #109 + #110） |
| 差量内容 | #109 = H7b 并发守卫 / H3 两类证据 / 门禁入口统一；#110 = 提示词补「当天日期」/ H7a 真跑 / H3 原生弹层确认键证据 |
| 是否影响 DEMO-1 范围 | **否**。两个提交都在「验证与证据」范畴，未引入业务语义变更 |
| 是否要回退到 `e9a4a9d` | **不要**。合同 §2.1 明写：`This is a planning reference, not a requirement to revert later work.` |

⇒ **执行基线取当前 `develop`。** 合同给的是「写作时的参考点」，不是「必须落到的点」。

---

## §1 合同理解要义：中英对照与理解陷阱

这一节是本次「先把合同搞清楚」的产物。**我们已踩过的坑标 ★**；其余是从合同原文
反推出的、中文读法容易糊掉的地方。**每条都给了英文原句的定位**，便于回查。

| # | 合同原文（关键处） | 中文容易读成 | **正确口径** |
| --- | --- | --- | --- |
| P0 ★ | 用中文词去英文文档里检索 → 判定「需求未定义」 | 「文档里没有槽位定义」 | **检索口径错**。该文档对应词是 `slots`。判定「未定义」必须用**文档实际语言**检索（DR-0010 的真实根因） |
| P1 | `Document status: Proposed execution baseline, ready for HO adoption; no completion or acceptance is asserted` | 「基线已定，可以开工了」 | 「ready for adoption」= **待采纳**。登记 ≠ 采纳 ≠ 完成，三者各自独立 |
| P2 | §2.1 `This is a planning reference, not a requirement to revert later work` | 「基线是 e9a4a9d，要回退」 | 只是**计划参考**。回退会丢掉 #109/#110，明令禁止 |
| P3 | §7 `"Paused" means outside DEMO-1's additional implementation obligation. It does not authorize deleting existing working functionality or ignoring a defect that breaks the accepted demo path.` | 「暂停＝这块不用管了」 | 暂停**只豁免新增实现义务**；**不授权**删既有可用功能、**不授权**忽略会打断演示路径的缺陷 |
| P4 | §1.3 Closure 行 `a shortcut checking only exceptions is prohibited` | 「结案＝查一下有没有异常」 | **只查异常的实现被合同点名禁止**。必须是真实领域命令 + 服务端强制前置条件 + 带理由的拒绝 |
| P5 | §6.4 `Operational completion may be recorded before financial completion… "Assignment closed" must not imply both have passed` | 「结案了」 | **运营完成 ≠ 财务完成**，界面必须能区分；不得用「已结案」一语盖住两者 |
| P6 | §10.3 `LIMITATION — The method did not cover the claimed behavior — Counts as passed? **No**` | 「受限，但基本通过」 | **不计入通过**。与项目已采纳的 D1=B 六档口径一致 |
| P7 | §9 尾注 `For D1-03, deterministic fixtures do not satisfy the live invocation item. For D1-05, a live run does not prove offline/manual completeness.` | 「模型跑通了，说明没模型也能跑」 | 两条**互不替代**：live 证明「能连真模型」，offline 证明「没 Key 也能走完」 |
| P8 | §1.3 `A live-model credential problem blocks the live-AI acceptance item, not manual development or other packages.` | 「Key 有问题，整个 DEMO-1 卡住」 | 只阻塞 **D1-03 的 live 那一项**；其余包照常推进，但只能报**部分预览** |
| P9 | §2.1 `validations 10 and 20 become mandatory in DEMO-1. This does not retroactively change A2's accepted boundary.` | 「DR-0013 的 10/20 归 S3，跟现在无关」 | DEMO-1 里**必须做**；但**不追溯改** A2 已采纳的边界。两句都要说 |
| P10 | §8.2 末 `Do not make a new percentage-complete promise…` | 「做了大概六成」 | 只能用**业务出口是否可演示**表述 |
| P11 | §10.1 `Target a concise 15–20 minute main presentation… this is a presentation budget, not a software latency or development-duration promise` | 「工期 15–20 分钟/天」 | 是**演示时长预算**，与开发工期无关 |
| P12 | §8.3 `Do not make one huge five-package PR… Do not create a new mandatory HO checkpoint for every internal task.` | 单向理解（只看到「别搞大 PR」） | **两个方向都被禁**：既不许捆一个大 PR，也不许每个内部任务都拉 HO 签字 |
| P13 | §6.1.3 `Keep customer projections server-side. Check attachment access as well as JSON response fields.` | 「前端不显示就行」 | 必须**服务端**投影；且**附件访问**也要按客户身份校验（不只是 JSON 字段裁剪） |
| P14 | §3.2 `Quotation selected → Must not be represented as Supplier/resource confirmed` | 「选了报价＝运力定了」 | **选择与资源确认是两件事**；D1-06 要求正反例都取证 |
| P15 | §10.4 `A demonstration with blocked live AI, broken closure, missing customer confirmation, or unproven authorization is a partial preview.` | 「先验收，缺的后面补」 | 明确是**部分预览**，不是「带缺项的验收通过」 |
| P16 | §11.2 `"Not in DEMO-1," "not yet tested," and "already implemented" are separate facts.` | 「这个早就实现了，算过」 | **三个独立事实**；某个 demo 子例通过 ≠ 原 AC 整体通过 |
| P17 | §1.1 `It shall not depend on developers editing database rows, invoking hidden administrative actions, or replacing business actions with screenshots during the acceptance walkthrough.` | 「演示时我可以顺手改下数据」 | 验收现场**不得**改库行、不得调隐藏管理动作、不得用截图替代业务动作 |
| P18 | §5.3 `URL injection alone does not prove entrance reachability.` | 「页面能打开就行」 | 必须走**真实入口**（与项目已裁决的 H2 同义） |
| P19 | §2.2 `do not restore a blanket "no migration" restriction` | 「本项目不许加迁移」 | 需要时**按版本化迁移追加**（本项目是自研迁移执行器 `backend/migrations/*.py`，按 (模块, id) 去重） |
| P20 | §8.3 PR 描述必须含的 5 项 | 「写个标题就行」 | 必须写：BP/D1 影响；可见结果与领域规则变化；迁移/API/客户投影影响；已执行证据与**明确未执行的检查**；回滚/开关暴露与剩余限制 |

### 1.1 与既有验证包的口径对齐（防止误读为「DEMO-1 已卡住」）

| 既有事项 | 与 DEMO-1 的关系 | 结论 |
| --- | --- | --- |
| H7a `calibration` 未达标（`rc=1`，3 达标 / 1 未达标） | D1-03 只要求「一次 live AG-02 会话产出**类型化、带来源**的提案 + 可见 job 状态」。`calibration` 阈值是**内部质量门禁**，不是任何 D1 行 | **不阻塞 DEMO-1**；但它也**不等于** D1-03 通过——D1-03 还要会话 UI + 脱敏 live 证据 + job/message/artifact ID |
| ⑧b 原生弹层点击那条 `LIMITATION` | 支付确认键属**遗留运费支付**流程，不在 DEMO-1 的委托生命周期 13 步主脚本内 | **不阻塞 DEMO-1**；按合同 §7 P-02，live 支付被正式暂停，DEMO-1 用**标注为样本/人工**的付款证据替代 |
| 合同 §3.2 `Mock payment or sample receipt → Must not be represented as Actual receipt of money` | 直接约束 BP-04 第 11 项 | 结算演示必须**显式标注**是样本证据，不得表述成资金已到账 |

---

## §2 范围裁定：什么变成「必须」，什么被正式暂停

### 2.1 相对旧节奏的四项重大变化

| # | 变化 | 依据 |
| --- | --- | --- |
| 1 | **「结案」从「以后再说」变成必须实现**：委托级真实结案命令 + 五个前置判定 + 原子性 + MySQL 竞态证据；且**只查异常的捷径被点名禁止** | §1.3 Closure 行、§6.4、D1-13 |
| 2 | **「客户侧确认」从「归 S3」变成**现在必须**：释放不可变报价版本 + 客户按精确版本接受/拒绝 + 拒绝未释放/被取代版本 + 拒绝经理冒充 | §1.2、BP-03、D1-07 |
| 3 | **扩展被正式暂停并有再入条件**：公开注册、支付/电子签/预订/承运人集成、AG-03/04/05、通用依赖图、路线优化、HA/公开运营、性能基准 | §7 P-01～P-13 |
| 4 | **表述制度更换**：不再有「完成度百分比」，改为「哪些业务出口可演示」；且 `未在 DEMO-1`/`未测试`/`已实现` 三个事实必须分开记 | §8.2、§11.2 |

### 2.2 暂停清单的边界（不得静默实现、也不得静默豁免）

- P-01 公开注册/入驻 → 用**预置账号**替代；再入 = 下一份 R1 服务就绪合同。
- P-02 live 支付/电子签/预订/承运人集成 → 用**标注的样本或人工证据**；再入 = 有供应商访问权限的独立工作单。
- P-03 AG-03/04/05 与自主多 Agent 编排 → 用完整**人工**领域操作 + **一条** AG-02 会话。
- P-04 AG-01 交互广度扩展 → 保留既有行为，不扩。
- P-05 通用依赖 DAG / 可视化依赖编辑器 → 用既有固定前置条件与已采纳影响映射。
- P-06 路线优化 / ETA 预测 / live AIS → 用合成申报路线 + 人工事件记录。
- P-07 通用合同设计器 / 高级导出 / OCR 扩展 → 一个可用模板 + 既有抽取 + 人工兜底。
- P-08 广泛组合分析 / 多币种税务会计集成 → 选定夹具 + 真实费用行 + 限定范围的结算。
- P-09 性能专项与 100 委托/2000 任务目标 → 具响应性的演示数据集；**不得**把演示耗时当成该基准。
- P-10 生产 HA / 公开运营 / 设备矩阵 / SLA → 受控稳定环境 + 一个受支持的见证运行时。
- P-11 IDE 启动根因专项与工具链整体替换 → 已采纳的只读探测 + 复用 + 有界的自有实例回收。
- P-12 无关遗留 TODO 扩展 → 保留既有回归门禁，只修实际受影响的回归。
- P-13 每个小 PR 都重复全量人肉跑 → 风险靶向的 PR 验证 + 一次集成候选运行。
- ⚠️ §7 末：某个暂停项若变成**强制验收条件所必需**，必须报成**具体依赖**，不得静默做全，也不得静默豁免。

---

## §3 基线差异表（S0 交付物 1）

三态定义：**已有** = 可直接复用；**缺连接** = 基础设施在、缺关键接线或门禁；**缺失** = 不存在。
（本节结论来自对本仓库的只读走查，路径均为相对 `E:\pinglu-didi`。）

> ⚠️ 本表是 **S0 时点的基线快照，不回写** —— 它的用途是回答"开工前差在哪"。
> 某项后来做了不等于当初没缺。当前进度一律看 §4 各切片的**进度**行。
> （例：§3.1 里 UI-07 记"缺失"、入口记"缺连接"，而 S1 已经把这两条做出来了。）

### 3.1 BP-01 客户入口与经理认领

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 发布流程里的「委托发货」入口 | **缺连接** | `miniapp/pages/publish/cargo/cargo.js:pickEntrustDelivery` 当前是占位跳转，未接受理实现 |
| UI-07 客户委托草稿表单 | **缺失** | 未找到页面；只有 API `POST /assignments` |
| 提交校验（生效授权 + 乐观锁） | 已有 | `backend/app/modules/entrust/assignments.py:submit_assignment`、`owner_has_active_entrustment` |
| 组织受理队列 | 已有（后端） | `backend/app/modules/entrust/router.py:list_assignments`（`view=org`） |
| 经理认领单所有者/并发 | 已有（后端） | `assignments.py:claim_assignment`（单条 `UPDATE … WHERE status='submitted'`） |
| 客户可见状态与服务组织 | 已有（后端） | `router.py:get_assignment` + `schemas.assignment_out` |

### 3.2 BP-02 报价 Agent IM

| 项 | 状态 | 依据 |
| --- | --- | --- |
| UI-03 真实消息输入 / 历史 / 附件 / 任务进度 | **缺连接** | `miniapp/pages/entrust/session/session.js:load` 目前只有成果卡 |
| session/run 绑定（组织+委托+执行人+输入版本） | 已有 | `sessions.py:create_session`、`build_agent_scope`；`agentjobs.py:create_job` |
| 走 job/model 网关 | 已有 | `backend/app/modules/agent/runner.py:run_agent` → `agent/llm.py:chat_json` |
| 类型化提案校验 | 已有 | `envelope.py:validate_envelope` + `registry.py` |
| 编辑/采纳/重试/人工续作 | 已有（后端） | `artifacts_api.py:append_revision`、`confirm_artifact`；`agent_api.py:retry`、`adopt_job_proposal`；`tasks.py:takeover_task` |
| 跨视图同一 artifact ID + revision | 已有（后端+工具） | `workbench.py:build_workbench`、`miniapp/utils/entrust.js:decorateArtifact` |
| 超时与失败分类 | 已有 | `agentjobs.py:RETRYABLE_KINDS`、`tick` |
| 接管后围栏迟到输出 | 已有 | `agentjobs.py:_abandon_lost_lease`；`tasks.py` 的 `lease_generation` / `expected_generation` |
| 真实模型 vs CI 夹具分离 | 已有 | `llm.py:chat_json`（`LLM_MOCK`）+ `agents/ag01.py:mock_content`、`ag02.py` |
| 跨导航保持消息与 job 状态 | **缺连接** | `miniapp/utils/routes.js:NAV_EDGES` 有治理，但会话尚无「消息态」可保 |

### 3.3 BP-03 对客方案、采购确认与合同

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 三段式方案 + 任务编排 | **缺连接** | 只有类型常量 `tasks.py:TASK_TYPE_QUOTE/PURCHASE/CONTRACT`，无编排 |
| 两家供应商报价对比 | 已有（类型） | `registry.py:supplier_compare`（`candidates`）+ `agents/ag02.py` |
| **选择 ≠ 资源确认** 的门禁 | **缺连接** | `supplier_compare.selected_candidate` 与 `procurement_confirm` 是两个类型，但**无「选择→确认」门禁连接** |
| 资源确认的授权 / 证据 / 有效期 | **缺连接** | `registry.py:procurement_confirm`（`evidence_kinds`/`effective_from`）**无专用确认端点** |
| 服务端客户白名单投影 | **缺连接** | `registry.py:project_for_customer` 存在但**无调用方**（孤儿函数）；`envelope.py:project_envelope_for_operator` 只覆盖 Agent 侧 |
| **释放不可变报价版本** | **缺失** | 未找到；`artifacts_api.py` 只有 confirm / void |
| 客户按精确版本接受/拒绝（含三类负例） | **缺失** | 无 `/customer` 端点 |
| 从已接受事实生成合同草稿 | **缺失** | 未找到 |
| 签名证据记录（来源/模式标注） | **缺失** | 只有 `registry.py` 的 `EVIDENCE_CONFIRMATION` 枚举 |
| 历史版本保留 | 已有 | `artifacts.py` append-only + `artifacts_api.py:list_revisions` |
| 客户查看/下载字段与附件裁剪 | **缺连接** | `project_for_customer` 游离，客户端点未接 |

### 3.4 BP-04 履约、变更、结算与结案

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 计划 vs 实绩里程碑 | **缺失** | `ent_task` 无 planned/actual |
| 装卸/交接证据 | **缺连接** | `tasks.py:required_evidence` / `evidence_refs` + `TASK_TYPE_HANDOVER`，无装卸专项结构 |
| 业务时间 vs 记录时间分离 | **缺失** | 无 `occurred_at` / `recorded_at`，只有 `created_at` |
| 800→950 变更结构化审批与应用 | 已有 | `exceptions.py:decide_case` / `apply_case`（`CHANGE_CARGO`） |
| 影响映射与复核任务 | 已有 | `revalidation.py:IMPACT_MAP` / `apply_revalidation` |
| 候选运力不适用的**确定性容量校验** | **缺失** | 容量校验只在 `backend/app/modules/port/service.py:confirm`（与本支线无关） |
| A2 接管任务 | 已有 | `tasks.py:takeover_task` |
| 缺失卸货证据 → 阻塞条件 | **缺连接** | `tasks.py:wait_task` 只记 `wait_reason`；阻断集只认 `exceptions.py:is_blocking`（`impact_kind=execution-blocking`） |
| 应收应付行 + 争议费用（Decimal/单位/币种/依据/状态） | **缺连接** | `registry.py:settlement_draft` 的 `receivable_lines`/`payable_lines`/`disputed` 均为自由 `FIELD_LIST` |
| 争议排除在确认总额外 + 显式解决动作 | **缺失** | 未找到 |
| 结算草稿 + 内部/客户确认 + 标注样本的外部付款证据 | **缺失** | 确认语义未找到 |
| **委托级真实结案命令 + 前置判定 + 原子性** | **缺失** | 只有**案件级** `exceptions.py:close_case`；`exceptions.py:448` 自述 S3 结案校验未上线；无委托结案端点 |
| 重新打开语义 | 已有（task/case 级） | `tasks.py:reopen_task`、`exceptions.py:reopen_case`（同事务审计） |

### 3.5 BP-05 演示环境、复位与验收证据包

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 构建/启动/迁移 | 已有 | `backend/migrate.py`、`scripts/setup-dev.sh`、`README.md`、`.github/workflows/ci.yml` |
| 种子脚本 | 已有 | `backend/scripts/seed_demo.py`、`seed_entrust_demo.py`、`seed_entrust_orgpicker.py`、`seed_contract_cases.py`、`bind_demo_to_openid.py` |
| 隔离复位流程 | **缺失** | 未找到复位/清库脚本（目前以种子的幂等性替代） |
| 三种夹具状态 | **缺失** | 未找到（干净起点 / 变更复核检查点 / 已完成历史委托） |
| 走查脚本 | 已有 | `scripts/run_walkthrough_devtools.py`、`scripts/wechatide_client.py`、`scripts/verify_miniapp_devtools.py` |
| 证据/产物约定 | 已有 | `miniapp-device-artifacts/<run-ts>/`（截图 + `summary.json`）、`artifacts/*.json` |

### 3.6 横切

| 项 | 状态 | 依据 |
| --- | --- | --- |
| UI-01 经理首页 / UI-02 抽屉 / UI-07 客户委托 | **缺失** | 未找到页面/组件 |
| UI-03 / UI-04 / UI-05 / UI-06 / UI-08 | 缺连接 / 已有 / 已有 / 已有 / 已有 | `pages/entrust/session` / `workbench` / `detail` / `artifact` / `case`(+`case-create`) |
| 工作台七槽位 | 已有 | `backend/app/modules/entrust/workbench.py:SLOT_SPECS` ↔ `miniapp/utils/entrust.js:WORKBENCH_SLOTS` |
| 路由与导航边登记 | 已有 | `miniapp/utils/routes.js`（`ROUTES` / `NAV_EDGES` / `MIGRATED_PAGES`） |
| feature flag | 缺连接 | `backend/app/core/config.py:ENTRUST_ENABLED`（默认 False；关闭时整组 404，见 `router.py:require_entrust_enabled`）；小程序侧无开关 |
| 幂等 | 已有 | `backend/app/core/idempotency.py:idempotent` + `backend/migrations/ent_idempotency.py` |
| 客户数据投影 | 缺连接 | `registry.py:project_for_customer`、`exceptions.py:project_case_for_customer`（后者有用，前者孤儿） |
| 迁移 | 已有 | `backend/migrations/*.py`（自研执行器，无全局编号；新增最近为 `ent_revalidation.py`、`ent_artifact_assignment.py`） |
| CI | 已有 | `.github/workflows/ci.yml`，jobs：`backend`、`db-migration`、`db-migration-mysql`、`pytest-mysql`、`frontend-static`、`frontend-e2e` |

### 3.7 三个最容易被低估的缺口

1. **客户侧整条闭环完全缺失**：释放不可变版本 → 客户接受/拒绝 → 合同草稿 → 签名，
   无端点、无页面，`project_for_customer` 是**没有调用方的孤儿函数**。而合同把「对客确认」
   列为必经环节（D1-07 / D1-08 / D1-11）。
2. **结算与结案目前只有类型壳**：`settlement_draft` 是自由 JSON 列表，没有 Decimal / 单位 /
   币种 / 争议排除口径；也没有委托级结案命令与前置判定（`exceptions.py:448` 自认未上线）。
   合同 §1.3 又**点名禁止**「只查异常」的结案捷径。
3. **UI-01 / UI-02 / UI-07 三屏不存在**，UI-03 只有成果卡。生命周期演示的**首尾两端都缺**：
   客户提交无处可做，经理主入口与抽屉不存在，会话无法演示。

---

## §4 切片节奏 S0–S5（S0 交付物 2、3）

> 估算口径：**不给日历承诺**（合同 §8.2 `No calendar deadline or payment amount is invented
> by this contract`）。用「工作项数 + 相对规模」表述，并单列**外部等待**。
> 每个工作项都挂上 BP 与 D1 ID。

### S0 — 锁定执行基线

| 工作项 | 内容 | 状态 |
| --- | --- | --- |
| S0-1 | 登记合同（`DEMO-1-contract-v1.0.md`，正文逐字照录 + 登记元数据） | **本轮完成** |
| S0-2 | 基线差异表（本文件 §3） | **本轮完成** |
| S0-3 | 工作项映射 BP / D1（本文件 §4） | **本轮完成** |
| S0-4 | 估算区间与依赖路径（本文件 §4 / §5） | **本轮完成** |
| S0-5 | R1 余量台账（`DEMO-1-r1-remainder.md`，AC-01～26 + P-01～13 逐行） | **本轮完成** |
| S0-6 | API/schema 变更清单（`DEMO-1-interface-delta.md`） | **已产出（2026-09-16）** —— 触发条件由"S3/S4 的迁移"修正为"**第一处接口面变更**"：S1 第一个工作项即撞到缺口（货主侧无任何接口能列出自己的生效委托授权），见该文件 §2 |

**出口判据**：对「必须做的演示动作」与「结案规则」**不存在未解歧义**。
估算：文档级，**S**。外部等待：**无**。

### S1 — 客户到经理路径（BP-01 + 最小 BP-05）

| # | 工作项 | D1 |
| --- | --- | --- |
| 1 | 把真实发布流程里的「委托发货」入口接上受理实现（现状是占位跳转） | D1-01 |
| 2 | 新建 UI-07 客户委托草稿/提交屏（货量+单位、起讫、时间要求、承接组织、备注附件） | D1-01 |
| 3 | 提交后进入组织真实受理队列（后端已有，接 UI） | D1-01 / D1-02 |
| 4 | A1 认领接 UI；补并发负例证据（后端已有单所有者） | D1-02 |
| 5 | 客户侧看到真实状态与承接组织 | D1-01 |
| 6 | BP-05 起步：账户/组织/启动/迁移/种子流程文档化（合同要求 **S0/S1 就开动**，不得押到最后） | D1-01 / D1-17 |

**出口判据（合同原文）**：`A fresh UI-created assignment survives reload, appears in the
correct queue, can be claimed once, and remains inaccessible to unrelated organization B.`
估算：**M**（新增 1 屏 + 3 处接线 + 1 个并发测试）。外部等待：**无**。

**进度（2026-09-16，第二切片 —— 分支 `feature/DEMO1-S1-exit-criteria`，base `develop@d932c43`）**：
第一切片已由 **PR #115** squash 合并（`d932c43`）。第二切片把上一轮登记的三条"不得算作完成"
闭环两条、另一条口径收窄，并把出口判据从"一条都还没验"推进到"后端 8 条可执行 + 真机 62 项 PASS"。
工作项 **3～6 仍未开始**。

| 项 | 落地内容 | 证据等级 |
| --- | --- | --- |
| 1 / 1′ / 2 | 同第一切片（真实发布入口接受理实现、只读端点、UI-07 四文件 + 五处登记） | 已随 PR #115 合并 |
| 出口判据 ① survives reload | 受理屏在途状态按用户隔离持久化（`entrust_intake_draft_<user_id>`），`reLaunch` 换页面实例后自动续接**同一张**草稿（编号与创建键都复原） | 真机 PASS：`㉞ 换页面实例重进后自动续接同一张草稿` |
| 出口判据 ② correct queue | 受理屏可选目标来自**服务端探测**的「我授权出去的组织」（不得用本地 `current_role`） | 真机 PASS：`targets=1 ['演示经营主体·工作台']` |
| 出口判据 ③ claimed once | 条件 UPDATE 的 `rowcount` 判据；迟到写入记 `abandoned` / `lease_lost`（不静默丢弃） | 后端用例 2 条（`test_claim_race_only_one_writer_wins` / `..._loser_cannot_overwrite_via_service_layer`）；真机侧 `NOT_RUN` |
| 出口判据 ④ B 组织不可见 | 越权认领 → **403**、越权详情 → 404 | 后端用例；真机侧 `NOT_RUN` |
| 出口判据（整体） | `backend/tests/test_entrust_s1_exit_criteria.py` **8 条**（文件型 SQLite；终局一律**换新会话读**，见 DR-0002） | 后端用例 8 条全过 |
| UI-07 真机走查 | `verify_miniapp_devtools.py` 新增 **㉞ 章**（69 项断言、六档登记） | **PASS=62 / FAIL=0 / NOT_RUN=4 / LIMITATION=3** |
| 幂等键持久化 | `intake.js` 7 处在途状态落 `storage`；仅在提交成功 / 用户放弃时清除 | 真机 PASS（含"提交成功后在途载荷被清"） |
| 3～4 | 组织受理队列接 UI、A1 认领接 UI（含并发负例的 UI 侧） | **未开始** |
| 5～6 | 客户侧真实状态与承接组织、BP-05 文档化 | **未开始** |

⚠️ **三条如实交代**（汇报一律**双句**，不得只写后一句）：

1. **㉞ 章的章节结论是 `NOT_RUN`，不等于"没跑"。** 62 项 PASS、FAIL=0；4 项 `NOT_RUN`
   （五态到达条件 / 并发双写 / 真·跨进程续接 / tabBar 凸起锚点真实点击）与 3 项 `LIMITATION`
   （注入状态、方法调用造中间态、原生弹层确认键）按**六档**如实登记，**只有 `PASS` 计入通过**
   ⇒ 本章**不能**写成"通过"。
2. **走查过程中发现并修掉了一个真实产品缺陷**（不是走查脚本的问题）：小程序把 `navigateTo` url 的
   query **原样（百分号串）**交给 `onLoad`，而 UI-07 按"值已解码"重建本页 url 去跑入口守卫
   ⇒ **二次编码**：中文货名 8 字膨胀到 **69 字符**，撞上 `cargo_name.maxLength` ⇒ 被测页把自己的
   入口判成「入口参数不合法」，整页只剩错误态，后续断言连锁失败。**ASCII 参数上 `encodeURIComponent`
   是恒等变换**，所以此前六个只带 id 的页面都没暴露。修法：`routes.js` 新增 `decodeParam()`
   （**有 `%` 才 decode**，非法序列 catch 原样返回 ⇒ 不误伤字面量 `含量50%`）+ 把 `maxLength`
   对齐落地字段的真实上限（**512**，与后端 `cargo_summary` 一致，原先随手写的 64 比契约窄）。
3. **`router.py` 的 500 也是真缺陷**：只捕获 `svc.AssignmentError` 会漏掉 `AccessDeniedError`
   （两者**无共同基类**：`access.py` 不能反向 import `assignments.py`）⇒ 越权认领与无生效授权提交
   返回 **HTTP 500** 而非 403（本函数里那个 `→ 403` 分支根本没机会执行）。已修，回归用例见
   `test_entrust_s1_exit_criteria.py`；两份映射设施待收敛见 §8.1 T-1。

### S2 — 报价会话（BP-02）

| # | 工作项 | D1 |
| --- | --- | --- |
| 1 | UI-03 补真实消息输入 / 历史 / 附件选择 / job 进度（现状只有成果卡） | D1-03 / D1-04 |
| 2 | 会话与 run 绑定接线 + 越权负例（后端 `build_agent_scope` 已有） | D1-11 |
| 3 | 类型化提案卡渲染（`validate_envelope` 已有） | D1-03 |
| 4 | 编辑 / 采纳 / 重试 / 人工续作四个动作接 UI（后端均已有） | D1-04 / D1-12 |
| 5 | 聊天 / 成果详情 / 工作台**同一** artifact ID + revision 的跨视图一致 | D1-04 |
| 6 | 人工接管后迟到输出的冲突在 UI 可见（H7b 乐观锁已有） | D1-12 |
| 7 | 无模型 Key 的离线 / 人工续作走查 | D1-05 |
| 8 | **live 模型调用**接进会话路径 + 脱敏留痕（H7a 已打通，额度已通） | D1-03 |

**出口判据**：一条真实报价会话产出类型化提案；人工纠正后三处视图同版本；迟到输出不能覆盖。
估算：**M–L**（1 屏重写 + 4 动作接线 + 2 条证据链）。外部等待：**无**（live 额度已验证可用）。
依赖：S1 的委托上下文与工作台入口。

### S3 — 商业承诺（BP-03）⚠️ 现状最薄的一环

| # | 工作项 | D1 |
| --- | --- | --- |
| 1 | 三段式方案 + 任务编排（缺失） | D1-06 |
| 2 | 两家供应商报价对比接 UI（类型已有） | D1-06 |
| 3 | **选择 ≠ 资源确认** 的门禁：授权 + 证据 + 有效期 + 过期/不适用负例 | D1-06 |
| 4 | **释放不可变对客报价版本**（缺失；新端点 + 迁移） | D1-07 |
| 5 | **客户按精确版本接受/拒绝**（缺失；客户侧 UI + 三类负例：未释放 / 被取代 / 经理冒充） | D1-07 |
| 6 | 服务端客户白名单投影**接上调用方**（`project_for_customer` 目前是孤儿） | D1-11 |
| 7 | 从已接受事实生成合同草稿 + 签名证据记录（来源/模式标注） | D1-08 |
| 8 | 客户侧查看/下载不泄漏内部数据——**含附件访问** | D1-11 |

**出口判据**：客户接受绑定到精确的已释放版本；仅「选择了一份报价」**不产生**已确认运力。
估算：**L**（4 项新域能力 + 2 屏 + 迁移）。外部等待：**无**。
⚠️ 风险：这是唯一「客户侧 0 实现」的环，且 S2/S4 的演示脚本都要用到它。

### S4 — 履约到结案（BP-04）

| # | 工作项 | D1 |
| --- | --- | --- |
| 1 | 计划/实绩里程碑 + `occurred_at` / `recorded_at` 分离（缺失；需迁移） | D1-10 |
| 2 | 装卸/交接证据结构（现只有通用 evidence_refs） | D1-10 |
| 3 | 800→950 变更走审批/应用（已有）+ 影响映射复核（已有 `revalidation`） | D1-09 |
| 4 | 900 吨候选不适用改为**确定性容量校验**（不得用 LLM 意见当规则） | D1-09 |
| 5 | 缺失卸货证据 → **阻塞条件**（现状不进阻断集，需接线） | D1-13 |
| 6 | 应收应付行 + 一笔争议等候费（Decimal / 单位 / 币种 / 依据 / 状态；现状是自由 JSON） | D1-10 |
| 7 | 争议排除在确认总额外 + 显式解决动作 | D1-10 / D1-13 |
| 8 | 结算草稿 + 内部/客户确认 + 标注为样本的外部付款证据 | D1-10 |
| 9 | **委托级真实结案命令**：五个前置判定 + 原子性 + MySQL 竞态证据（含 DR-0013 验证 10/20） | D1-13 |
| 10 | 委托级重新打开 + 受影响就绪态重新评估（现只有 task/case 级） | D1-13 |

**出口判据（合同原文）**：`The scenario reaches legitimate closure; missing evidence,
a blocking case, and unresolved financial conditions each prevent the applicable closure.`
估算：**L**（10 项，含 2 处迁移 + MySQL 竞态证据）。外部等待：**无**。

### S5 — 集成候选（BP-05）

| # | 工作项 | D1 |
| --- | --- | --- |
| 1 | 三种夹具状态（干净起点 / 变更复核检查点 / 已完成历史委托） | D1-17 |
| 2 | 隔离复位流程（缺失；环境专属管理脚本，**不对普通用户开放**） | D1-17 |
| 3 | 走查脚本 13 步（合同 §10.1）+ 角色 / 预期可见结果 / 诚实的回退标签 | D1-14 |
| 4 | 焦点负例 7 条（合同 §10.2）逐条取证 | D1-11/12/13 |
| 5 | **见证人改一个值**（数量/费率/输入）看下游重算并跨刷新存活 | D1-15 |
| 6 | 一次见证完整运行 + 证据索引 + 验收报告 + 余量台账收口 | D1-16 / D1-18 |

**出口判据**：全部 D1 行**有结果报告**（不是「假定通过」）；候选可供 HO 验收。
估算：**M–L**（脚本 + 一次完整见证 + 证据包）。外部等待：见证人时间（HO/委托方）。

### 4.1 文档落位对照（合同 §12 要求 8 件）

| §12 要求 | 落位 | 状态 |
| --- | --- | --- |
| Milestone contract | `docs/entrust/milestones/DEMO-1-contract-v1.0.md` | 本轮登记 |
| Execution plan | `docs/entrust/milestones/DEMO-1-plan.md` | 本文件 |
| R1 remainder ledger | `docs/entrust/milestones/DEMO-1-r1-remainder.md` | 本轮登记 |
| API/data delta | `docs/entrust/milestones/DEMO-1-interface-delta.md` | **已产出（S0-6，2026-09-16）**；其 §3.1 的唯一新增端点已于 S1 落地（见 §4 S1 进度行） |
| Runbook | `docs/entrust/milestones/DEMO-1-runbook.md` | 待补（S1 起步、S5 收口） |
| Walkthrough | `docs/entrust/milestones/DEMO-1-walkthrough.md` | 待补（S5） |
| Acceptance report | `docs/entrust/milestones/DEMO-1-acceptance.md` | 待补（S5） |
| Evidence index | `docs/entrust/milestones/DEMO-1-evidence-index.md` | 待补（S5） |

合同允许「更新并链接既有等价文档，而不必复制」——例如 H3 记录、H7a 验证包、
`04-A2期切片计划.md` 可在证据索引里链接，不必重写。

---

## §5 依赖路径与关键串行点

```
S0 基线
 └─> S1 客户→经理 ──> S2 报价会话 ──> S3 商业承诺 ──> S4 履约到结案 ──> S5 集成候选
                └──────────┴──────────────┴──────────────┘
                     均向 S5 供给证据（不得押到最后才集成）
```

| 关键串行点 | 说明 |
| --- | --- |
| **S1 → S2** | S2 的会话必须绑定到一个已提交、已认领的委托；S1 不出，S2 无从演示 |
| **S3 是咽喉** | 客户侧闭环（释放版本 → 客户响应）现在**零实现**；S4 的「变更后需重获客户确认」也依赖它（D1-09） |
| **S4 的结案是第二咽喉** | 结案既是 D1-13，也是 D1-10 的收口；且合同点名禁止「只查异常」的实现 |
| **S5 不得押后** | 合同 §8.1：`Start BP-05 environment and fixtures during S0/S1. Do not defer integration to the final slice.` ⇒ S1 起就要把启动/种子/复位做起来 |
| **外部等待** | 仅两项：① live 模型额度（**已通**）；② S5 见证人时间。除此无外部阻塞 |

---

## §6 制度闸门（沿用既有，不新造）

| 制度 | 口径 |
| --- | --- |
| PR 边界 | 一个包可拆多个小 PR；**不得**捆一个五包大 PR；也**不得**为每个内部任务设 HO 检查点（§8.3） |
| PR 描述 | 必须含 §1 表中 P20 的 5 项（含**明确未执行的检查**） |
| 合并授权 | 仍只有 HO 授权可合并；**PR 作者不自批**（DR-0003，合同 §1.4 重申） |
| 切片出口 | 每个切片结束必须有**可演示增量**，不得只交内部重构 |
| 证据分档 | 沿用项目已采纳 D1=B 六档（`PASS`/`FAIL`/`ENV_BLOCKED`/`REVIEW_REQUIRED`/`NOT_RUN`/`LIMITATION`），**只有 PASS 计入通过**；汇报用**双句**写法 |
| 门禁 | 既有 CI 与仓库控制**不动**；不得关检查、删测试、放宽断言、改写共享历史（§6.5） |
| 范围蔓延 | 新发现的范围进余量台账；删掉 live-AI 或结案判据**必须**走可见的合同修订，不得改称「实现选择」（§8.4） |
| 变更控制 | 只有四类变更需要显式修订：改强制业务结果、接受原先禁止的捷径、新增实质性外部承诺、改动已采纳的横切规则（§8.4） |
| 阻塞上报 | 报「被阻塞的 D1 ID + 观测证据 + 最小可行替代 + 推荐选择 + 对下一份合同的影响」，**并继续做不受影响的工作**（§8.4） |

---

## §7 本轮明确不做（防范围蔓延）

1. 不实现任何 P-01～P-13 里的暂停项（公开注册、live 支付/电子签/承运人集成、AG-03/04/05、通用依赖图、路线优化、HA/性能专项、IDE 根因专项…）。
2. 不改动既有验证包的历史结论（H7a 的 `calibration` 未达标、⑧b 的那条 `LIMITATION` **保持原样**）。
3. 不为尚未存在的域能力预写空适配器（§11.1 `Do not build empty adapters for hypothetical providers`）。
4. 不动已发布迁移、不改 `id`、不删既有可用功能。
5. 不填写合同 §13 的验收记录表（合同明令不得预填）。

---

## §8 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| S3 客户侧闭环被持续低估 | 演示链路断在「对客确认」，D1-07/08/11 全空 | S3 前置：**先**把释放版本与客户响应两个域能力做出来，再补界面 |
| 结案被写成「查一下异常」的捷径 | 直接违反 §1.3，D1-13 无效 | 结案命令以**五个前置判定**逐条落测；反例（缺证据/阻塞案件/未决金额）必须各自被拒 |
| 结算沿用自由 JSON 列表 | 金额正确性无法证明，违反 §6.3 | 费用行落成结构化字段（Decimal/单位/币种/依据/状态）+ 争议不进确认总额 |
| `ENTRUST_ENABLED` 关闭时整组 404，小程序侧无开关 | 开关行为无法验证，D1-11/16 有风险 | S1 就把小程序侧开关探测纳入；关闭时**不得**绕过后端授权或静默回落到旧行为（§2.2） |
| 一次演示跑通被当成 AC 整体通过 | 违反 §11.2 | 余量台账逐 AC 记「未覆盖子例」，汇报只用「业务出口」表述 |
| 迁移与 CI 的门禁打架 | D1-16 红 | 每处 schema 变更都按既有自研迁移执行器追加；干净库 + 升级库两条路都测（§6.5） |

### 8.1 技术债登记（**欠着的活**，不是风险）

风险是"可能出的事"，这里记的是"已经知道欠着、且不会自己消失"的活。判据是**不阻塞当前切片**
但**必须有人认领**；每一条都要写清"为什么现在不做"。

| # | 债 | 为什么现在不做 | 认领条件 |
| --- | --- | --- | --- |
| T-1 | **异常 → HTTP 的映射有两份实现**：`entrust/router.py:_http_error` 与 `entrust/_http.py:map_access_denied`（`artifacts_api.py` 走后者，口径正确） | 收敛需要给两个域异常找共同基类，而 `access.py` **不能**反向 import `assignments.py`（循环 import）⇒ 要么把基类下沉到新模块、要么改成分层注册。这是一次结构性改动，混在 S1 出口切片里会让 PR 的"一个目的"失效 | S2 开工前（`entrust` 模块第二次扩张时） |
| T-2 | **入口参数归一化只覆盖 7 个页面**：`routes.js:decodeParam()` 已就位并接到了 entrust 的 7 个入口 + UI-07，但 `pages/preview/preview` 仍直接取 `options.title` | 该页应用内**无调用方**（只可能被外部深链命中），且不参与任何演示路径 | 有真实深链需求时；在此之前**不得**把它当作"已归一化" |
| T-3 | **`.workbuddy/`（仓库根）既未忽略也未跟踪**：里面是本机跑门禁/走查的临时脚本，`git add -A` 会把它们提交上去（与 `.env.local.bak-*` 同型的坑） | 改 `.gitignore` 属仓库卫生，与 S1 出口判据无关；且需先确认项目级技能目录是否要入库 | 下一次仓库卫生提交；在此之前**一律显式 `git add <路径>`** |

---

## §9 下一步（编号）

1. ~~**HO 采纳本合同与本文**……~~ ⇒ ✅ **已于 2026-09-16 闭环**：HO 答复「同意这个安排，下面继续执行」，
   采纳记录已写入 `DEMO-1-contract-v1.0.md` 登记本（日期 + 依据 + 范围），本文状态升为**生效**。
   ⚠️ 采纳的是**执行节奏**；合同 §13 验收记录表**仍留空**，未声称任何完成或验收。顺带说明：
   S0 的另一处自述（「这是 S0 的唯一未闭环项」）也随之不再成立，见 `DEMO-1-readiness.md` §7 PR-1。
2. ~~**开 S1 的第一条分支**（`feature/DEMO1-S1-customer-intake`）：先做「真实发布入口接受理实现 + UI-07 草稿屏」两个工作项，PR 描述按 §1 P20 的五项写。~~
   ⇒ ✅ **已于 2026-09-16 闭环**：分支已开、两个工作项已写码、门禁全绿、**PR #115 已按 HO 授权 squash 合并**（`405bd29..b2145d4` ⇒ merge commit **`d932c43`**），本地 `develop` 已同步
   （`refs/rorigin/develop` == `d932c43`）。出口判据与真机走查见 §4 S1 的**进度**行（第二切片）。
   ⚠️ 合并**不等于**出口判据通过：第一切片合并时"出口判据一条都还没验证"，那是第二切片才补的。
3. ~~**补 S0-6**：随 S1 的第一处 schema 变更一起产出 `DEMO-1-interface-delta.md`（现在写会是空壳，合同也不许写空适配器）。~~
   ⇒ ✅ **已于 2026-09-16 产出**：触发条件修正为「**第一处接口面变更**」——S1 第一个工作项
   即撞到缺口（货主侧无任何接口能列出自己授权出去的组织），因此**不需要**等 schema 变更。
   ⚠️ 该文件 §3.1 的端点此后已实现，但**文件里"【计划】"的标注保留原样**：它记录的是
   "写这份清单时它还没实现"，回改成【已实现】会让"清单 → 实现"这条链失去可追溯性。
4. **把 H3「人手点击」收口**：这条不在 DEMO-1 的 13 步主脚本内（属遗留运费支付、已由 P-02 暂停），但作为**既有证据债**仍需闭环——做法是把「弹层弹出」之前的所有步骤自动化，**只把最后一下点击留给人**，人点完由脚本自动取证并回填记录表。
5. **H7a 的 `calibration` 判据重设计**：先写进 `THRESHOLDS` 再跑（补 3～5 条易错样本使错答组 n≥5，或换 AUC/ECE）；它是**内部质量门禁**，不阻塞 DEMO-1，但**也不等于** D1-03 通过。
6. **接 S1 工作项 3～4**（本轮唯一还没动的两块）：① 组织受理队列接 UI（后端 `GET /assignments?view=org` 已有，
   缺列表页与"待受理"入口）；② A1 认领接 UI（后端 `claim_next` / `claim_job` 已改乐观锁，缺界面与并发负例的 UI 侧证据）。
   落位：`miniapp/pages/entrust/workbench/`；**新增页面按五处登记**（`app.json.pages` / `ROUTES` /
   `NAV_EDGES` / `MIGRATED_PAGES` / `verify_entrust_ui.js`），五处全加完再跑门禁。
7. **把 ㉞ 章那 4 条 `NOT_RUN` 逐条降到"有证据"**——按性价比排序：① 真·跨进程续接（需
   "杀掉小程序但保留 storage"的通道，工具侧目前做不到）；② tabBar 凸起「发布货物」的真实点击
   （需 OS 级鼠标通道，与 ⑧b 原生弹层同一类手段，可一次把两处都补上）；③ 五态到达条件
   （`expired` 需登录态自然过期 ⇒ 不可控；`denied`/`error` 可用"请求飞行中撤权 / 起一个坏后端"造）。
   **`LIMITATION` 不是"以后会过"**：在补齐之前它会一直不计入通过。
8. **在 `.gitignore` 里处置仓库根 `.workbuddy/`**（见 §8.1 T-3）：它是本机跑门禁的临时脚本目录，
   既未忽略也未跟踪 ⇒ `git add -A` 会误提交。在决定忽略之前，**一律显式 `git add <路径>`**。
9. **授权合并本切片 PR**（`feature/DEMO1-S1-exit-criteria` ⇒ `develop`）：作者不自批（DR-0003），
   需 HO 明确授权后才可 squash。合并前**不得**在汇报里说"出口判据已通过"——正确表述是
   "后端 8 条可执行 + 真机 62 项 PASS、7 项如实登记未取证"。
