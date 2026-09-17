# DEMO-1 接口 / 数据增量清单（S0-6）

| 项 | 值 |
| --- | --- |
| 建立日期 | 2026-09-16 |
| 建立时基线 | `develop` = `60a94b3`（PR #113 已合并） |
| 本文件性质 | **增量登记**（合同 §12 要求的 8 件之一）。**只登记"要改什么"，不声称"已改完"** |
| 触发原因 | S1 的第一个工作项（把「委托发货」入口接上受理实现）在第一处就撞到一个**接口面缺口**，不再"是空壳" |

> ⚠️ **口径**：本文件里的端点、字段、状态，凡标注 **【计划】** 的都是**尚未实现**的。
> 已存在的端点标注 **【现状】** 并给出源码位置。**不要把本文件当作已完成清单引用。**

---

## 1. 为什么现在就产出（而不是等 S3/S4）

`DEMO-1-plan.md` 的 S0-6 原写"待补 —— 随 S3/S4 的迁移一起产出（现在写会是空壳）"。
这个判断基于"接口面在 S1 够用"。**S1 一开工就被证伪**：见 §2。

⇒ 结论：S0-6 的**触发条件不是"S3/S4 的迁移"，而是"第一处接口面变更"**。
S1 就是第一处。计划里那条备注应当按此修正（已在 §5 提出）。

---

## 2. 撞到的缺口：货主看不到"可以委托给谁"

### 2.1 现状（已核实，非推测）

| 事实 | 证据 |
| --- | --- |
| 提交委托**必须**带 `org_id`，且服务端校验货主对该组织存在**生效委托授权** | `backend/app/modules/entrust/assignments.py:340` —— 无授权 ⇒ `AccessDeniedError`（403） |
| `ent_organization` / `ent_org_member` / `ent_entrustment` **没有 HTTP 接口** | `backend/scripts/seed_entrust_orgpicker.py:27`（脚本自述）；`backend/app/modules/entrust/` 下全部 `APIRouter` 的端点清单里**无** entrustment 相关路径（已逐个核对 8 个 router 文件） |
| 前端唯一能拿到"组织"的接口是 `GET /my-orgs` —— 它的语义是**我所在的组织** | `miniapp/utils/entrust.js:263`；后端 `router.py:411` 的 summary＝"我的组织清单（我所在的组织 + 我在每个组织内的权限）" |

### 2.2 为什么不能拿 `GET /my-orgs` 顶上

**因为"归属 ≠ 权限边界"（DR-0012 已采纳）。** 「我是这个组织的成员」与「我把委托授权给了这个组织」
是两张表、两件事：

- 成员关系在 `ent_org_member`，授权关系在 `ent_entrustment`；
- 提交校验读的是 `ent_entrustment`（`assignments.py:340` 走 `owner_has_active_entrustment`）；
- 一个人可以是组织成员却**没有**对该组织的委托授权；也可以把委托授权给一个自己不是成员的组织。

⇒ 用 `/my-orgs` 渲染提交目标，会出现**"能选但必然 403"**的选项。
这正是 DR-0012 要防的那类错误：**把归属当权限用**。

---

## 3. 增量清单

### 3.1 【计划】新增只读端点：`GET /api/v1/entrust/my-entrustments`

| 项 | 内容 |
| --- | --- |
| 方法 / 路径 | `GET /api/v1/entrust/my-entrustments` |
| 用途 | **货主侧**：列出"我有**生效**委托授权的组织"，供 UI-07 选择提交目标 |
| 鉴权 | 登录用户即可（只返回**自己**的授权，不做越权投影） |
| 请求参数 | 无（`active_only` 默认为真；不需要分页 —— 一个货主的生效授权数量级是 10⁰–10¹） |
| 响应字段 | `entrustment_id`、`org_id`、`org_name`、`permissions`（数组）、`status`、`granted_at` |
| 权限口径 | ⚠️ **只投影授权本身，不投影组织内部数据** —— 不得带组织成员、任务、成果等任何内部信息 |
| 排序 | `org_name` 升序（稳定；避免前端二次排序造成两侧不一致） |
| 空态 | 空数组 ⇔ "你还没有把委托授权给任何组织"（UI 据此**禁用**提交并给出去处说明） |
| 迁移 | **无**（`ent_entrustment` 表已存在，见 `migrations/ent_org_access.py`） |
| 幂等 / 写操作 | 不适用（纯读） |
| 事实来源 | 每条响应可回溯到 `ent_entrustment` 行（六条机制之"事实有来源"） |

**为什么是货主侧而不是管理侧**：本端点是「我授权出去的」，不是「别人授权给我的」。
后者属于组织工作台（经理人）视角，本切片不做。

### 3.2 【现状·复用】受理主链（已存在，不改语义）

| 步骤 | 端点 | 关键约束 |
| --- | --- | --- |
| 建草稿 | `POST /api/v1/entrust/assignments` | 幂等（`Idempotency-Key` 头）；`title` 必需；`quantity` 未知即 `NULL`，**绝不静默补 0**（PRD 5.1） |
| 提交 | `POST /api/v1/entrust/assignments/{id}/submit` | 需 `org_id` + `expected_revision`；需生效授权；条件更新（`revision` + `status` 双守卫） |
| 撤回 | `POST /api/v1/entrust/assignments/{id}/cancel` | 仅 draft / 待受理 |

### 3.3 【计划】前端新增

| 文件 | 内容 | 备注 |
| --- | --- | --- |
| `miniapp/utils/entrust.js` | `fetchMyEntrustments()` / `createAssignment(body, key)` / `submitAssignment(id, orgId, revision, key)` / `assignmentDraftBody(form)` | 现文件里**没有** `createAssignment` / `submitAssignment`（已核实：只有 `fetchAssignment` / `claimAssignment` / `createCase` / `createTask`） |
| `miniapp/pages/entrust/intake/intake.{js,json,wxml,wxss}` | **UI-07** 客户委托草稿 / 提交屏 | 未创建 |
| `miniapp/pages/publish/cargo/cargo.js` | `pickEntrustDelivery()` 由"打开占位预览页"改为经 `R.go()` 进入 UI-07，并把已填的货名/数量带过去当草稿初值 | 现状 `cargo.js:84-91` 是占位跳转、无接口调用 |

### 3.4 【计划】登记（新增页面的五处，**五处全加完再跑门禁**）

`app.json.pages` ／ `utils/routes.js` 的 `ROUTES`（含 `paramSchema`，覆盖 deepLink）与
`NAV_EDGES`（无证据的边标 `pending`）与 `MIGRATED_PAGES` ／
`scripts/verify_entrust_ui.js` 的 `ENTRUST_PAGES` + `PAGE_CSS_CHECKS`。

⚠️ 已知静默陷阱：`NAV_EDGES` 的 `pending` 会随代码接线**过期**，留旧 `pending` 会被
`verify_routes.js` 报"过期 pending"；WXML 里 `wx:if` 与 `wx:for` **不得同元素**
（整页白屏、四条前端门禁都抓不到，只有 `verify_wxml_directives.js` 能拦）。

---

## 4. 状态与取值域：本增量**不新增**

本增量**不引入**新的状态、枚举或字段映射 ⇒ 按 `AGENTS.md` §3.5 的"镜像表契约检查"口径，
**无需新增镜像表比对**。若实现过程中出现"前端再现后端取值域"，须同步登记镜像表，
**不得只在前端写一份**。

---

## 5. 由此产生的两处修正

1. ✅ **已办**：`DEMO-1-plan.md` 的 S0-6 备注"随 S3/S4 的迁移一起产出（现在写会是空壳）"
   ⇒ 已改为"**随第一处接口面变更产出**（S1 即触发）"。
2. ✅ **已办**：`DEMO-1-readiness.md` §4 的 **U-9** 把 `interface-delta` 列在"尚未落位"里
   ⇒ 已收窄为"**其余 4 份**仍未落位"（`runbook` / `walkthrough` / `acceptance` / `evidence-index`）。

---

## 6. 本文件写作时**没有**做的事（如实交代）

> ⚠️ **本节描述的是 2026-09-16 写文件那一刻的状态，不是当前状态。** 同日实现已落地 ⇒ 见 §7。
> **原句保留、不改写** —— 让"登记在先、实现于后"这件事本身留痕，这正是本文件存在的意义。

- 上述端点、前端文件**一行代码都还没写**；本文件是**契约登记**，不是完成证明。
- 未跑任何门禁、未开实现 PR。
- 未定义 UI-07 的视觉细节（沿用现有页面级类与组件；实现时按 `verify_entrust_ui.js` 的
  类名存在性检查走）。

---

## 7. 落地状态（2026-09-16 补 —— 同日实现的逐条对照）

分支 `feature/DEMO1-S1-customer-intake`：

| 契约条目 | 状态 |
| --- | --- |
| §3.1 `GET /api/v1/entrust/my-entrustments` | ✅ 已实现（`access.list_my_entrustments` + `MyEntrustmentOut` / `MyEntrustmentListOut` + `scope_matrix` **61 → 62**） |
| §3.2 受理主链复用 | ✅ 签名与语义均未改（`POST /assignments` / `{id}/submit` / `{id}/cancel`） |
| §3.3 前端 3 项 | ✅ `fetchMyEntrustments` / `createAssignment` / `submitAssignment` / `assignmentDraftBody` 已加；UI-07 四文件已建；`cargo.js` 已接线 |
| §3.4 五处登记 | ✅ 五处齐全（`app.json` **22 页** / `ROUTES` / `NAV_EDGES` / `MIGRATED_PAGES` / `verify_entrust_ui.js`） |
| §4 不新增状态与取值域 | ✅ 确未引入新枚举 ⇒ 无需新增镜像表比对 |
| §3.1 排序 `org_name` 升序 | ⚠️ **在 Python 侧排序，不在 SQL** —— MySQL `utf8mb4_unicode_ci` 与 SQLite 对中文名的比较顺序不同，交给数据库会造出"本地一个顺序、CI 另一个顺序" |

**两处与本文档条目不同的实现决定（如实登记，不是笔误）**：

1. **`cargo → preview` 的导航边没有删，标了 `pending`**：撤销调用后若把声明一并删掉，
   `pages/preview/preview`（`kind='detail'`）会违反注册表"detail 页必须有 push 入边"这条约束，
   且真机走查 ㉝ 章按声明链深断言。⚠️ 这是对 `pending` 语义的一次**挪用**：它原本表示"声明先行、
   实现未接"，此处表示"**调用已撤销、声明须留**"。已在 `routes.js` 注释里写明，
   **不得被后来者误读成"将来会接"**。
2. **UI-07 未声明 `keyContext:['org']`**：本屏的"组织"来自服务端探测结果，不来自本地
   `current_role` ⇒ 不参与 key 复用判定。

**两条曾经"仍未做到"、已在第二切片闭环**（2026-09-16，分支 `feature/DEMO1-S1-exit-criteria`）：

| 条目 | 处置 | 证据 |
| --- | --- | --- |
| ① UI-07 未做真机走查 | ✅ 真机 **㉞ 章**（69 项）：全链真实点击走到详情落地 | **PASS=62 / FAIL=0 / NOT_RUN=4 / LIMITATION=3** |
| ② 幂等键只活在页面实例里 | ✅ 按用户隔离落 `storage`（`entrust_intake_draft_<user_id>`）；`reLaunch` 换页面实例后续接**同一张**草稿 | 真机 PASS（编号 `6` → `6`、创建键一致） |

⚠️ 闭环的是"**这两条余量**"，**不是**"出口判据通过"：㉞ 章另有 4 项 `NOT_RUN` + 3 项 `LIMITATION`，
而六档口径下**只有 `PASS` 计入通过**。

### 7.1 第二切片新增 / 修正的接口面决定（如实登记）

| # | 条目 | 状态 |
| --- | --- | --- |
| 3 | **入口参数归一化**：`onLoad` 拿到的 query 是**百分号原样串**（框架不解码），页面重建 url 跑入口守卫前必须先过 `decodeParam()`（**有 `%` 才 decode**，非法序列 catch 原样返回 ⇒ 不误伤字面量 `含量50%`） | ✅ `routes.js` 已实现并接到 **7 个入口页**；⚠️ `pages/preview/preview` 仍直接取 `options.title`（应用内无调用方，只可能被外部深链命中）⇒ 记入技术债 **T-2** |
| 4 | `paramSchema.cargo_name.maxLength` **64 → 512** | ✅ 原值**比落地契约窄**：该值最终写成 `cargo_summary`，后端允许 **512** ⇒ 不修的话"源页面合法、目标页拒绝"这条矛盾会换个形式再犯 |
| 5 | `router.py` 的异常 → HTTP 映射**补捕 `AccessDeniedError`** | ✅ 此前只捕 `svc.AssignmentError` ⇒ 越权认领 / 无生效授权提交返回 **HTTP 500** 而非 403（这是一处**接口面偏离**，不是内部实现细节）；两份映射设施待收敛见 **T-1** |

### 7.2 第三切片新增 / 修正的接口面决定（如实登记）

分支 `feature/DEMO1-S1-org-queue-claim`（base `develop@236c63d`）：

| # | 条目 | 状态 |
| --- | --- | --- |
| 6 | **委托队列卡片新增「受理」消费方**：`POST /assignments/{id}/claim` 此前只有详情页在调，本切片起队列卡片也调 | ✅ **端点未改**（`scope_matrix` **62 条不变**）⇒ 这**不是**新增接口，只是**新增消费方**。但它带来一条前端投影决定（见下条） |
| 7 | `utils/entrust.js` 的 `decorateAssignment()` **新增 `canClaim`（纯投影字段）**：`canClaim = (status === 'submitted')` | ✅ 只作**展示**用，**不参与判权** —— 前端不假装知道当前身份有没有 `entrust:assignment:claim`（那取决于组织成员资格与授权，只有服务端知道）。⇒ 副作用是"**只读成员也看得到受理按钮**"，登记为待评审 **D-4**（见 `DEMO-1-r1-remainder.md` §四） |
| 8 | 受理入口**刻意用页内确认条，不用原生 `wx.showModal`** | ✅ 这是**可验证性**驱动的选择，不是审美：原生弹层**不在渲染树里**（`.weui-dialog*` 选择器命中 0），工具点不到它的确认键 ⇒ 用弹层承担的关键路径**永远拿不到设备证据**。「受理」是唯一改变业务状态的链路，不能落在不可验证的交互上 |
| 9 | 种子新增身份 `seed-mgr-only-b`（仅乙组织经理） | ✅ **演示数据**，不是接口面。为什么**必须有**它：`GET /assignments/{id}` 的可见性判据是「该委托的组织 ∈ 调用者的**任一**组织」，而 `seed-mgr-multi`（甲+乙双身份）**必然**看得到甲组织的单 ⇒ 拿它验"unrelated organization B 不可见"会得到**假绿** |

### 7.3 第四切片（D-4 裁定落地）的接口面决定（如实登记）

触发：`DEMO-1-r1-remainder.md` §四 **D-4**（队列卡片的「受理」入口只看委托状态、不判权限）
于 2026-09-16 由 HO **裁定采纳**（四条边界见该处）。本节只登记其中与**接口面**有关的部分。

| # | 条目 | 状态 |
| --- | --- | --- |
| 10 | 判据改为「**可认领状态**（`submitted`）**∧ 该委托所属组织内**有 `entrust:assignment:claim`」，**两个入口共用一份实现** | ✅ `utils/entrust.js` 新增 `canClaimAssignment(status, orgId, permitted)` 与 `permittedOrgIds(rawOrgs, perm)`；`decorateAssignment` / `decorateDetail` 加（透传）权限投影参数。⚠️ `decorateList` 必须**显式包一层** `map`，不能写 `rows.map(decorateAssignment)` —— `map` 会把 `index` 塞进第二参数，于是"权限表"是一个数字，而 `isPermittedOrg(0, …)` 恒为 false ⇒ 表现为"受理按钮从来不出现"，**静默**且与真实权限无关 |
| 11 | **不新增端点、不新增字段**：权限投影复用**已有**的 `GET /entrust/my-orgs` 的 `permissions` | ✅ `scope_matrix` **62 条不变**；`AssignmentOut` **不新增** `capabilities`。这是**最小修正**分支：`MyOrgOut.permissions` 与写端**同源**（`access.py` 的 `ctx.permissions_in_org`）⇒ 前端据此展示**不会**出现"界面说能、后端说不能"的反向不一致。代价是详情页多一路只读请求，故把它做成**可降级**：拿不到权限结论时**不展示**入口、但**仍渲染**该页有权读取的内容（裁定 §4 的代码形态） |
| 12 | 前端**按 `org_id` 分域**取权限，**严禁**跨组织并集 | ✅ `permittedOrgIds()` 产出的是一张**按组织 id 索引**的表（一页可跨组织）。⚠️ 入参必须是**原始载荷** —— 喂 `decorateOrgs()` 的产出（权限码已翻成中文标签）**恒为空表且静默**。跨组织并集是**已复现并修过**的越权（`access.py` 注释留痕），所以这一条是硬约束，不是风格 |
| 13 | 权限**未加载 / 加载失败** ⇒ **不展示**可执行按钮 | ✅ 两个入口都在取数**之前**显式置空投影（不依赖"恰好还没取到"这种偶然）；判据函数在第二参数缺席时**恒为 false**（保守缺省） |
| 14 | 写端**独立**校验 —— 隐藏按钮 ≠ 放行 | ✅ **后端未改**：`claim_assignment` 本就用 `ctx.can(PERM_ASSIGN_CLAIM, org_id=org_id)`，与展示判据**同源**。㊱ 章以**API 直证 403**（同组织只读成员直接调用）把这条落地为设备侧证据 |

### 7.4 第五切片（S2 首片：真实会话 → 消息/附件 → 持久化作业 → 重进恢复）

触发：HO 0917-2 执行顺序 2。本节只登记其中与**接口面**有关的部分。

| # | 条目 | 状态 |
| --- | --- | --- |
| 15 | **新增只读端点** `GET /entrust/assignments/{assignment_id}/session-context` | ✅ `scope_matrix` **62 → 63**。**为什么必须新增**：建会话是 `POST /entrustments/{eid}/sessions`，路径参数要求调用方**先知道授权 id**，而现有两条清单都答不了 —— `/my-orgs` 读 `ent_org_member`（只回成员身份），`/my-entrustments` 只列**货主自己授权出去**的授权。经理两者都拿不到本单那一条 ⇒ 前端只剩"猜一个 id 试到不报错为止"。⚠️ 本端点**只查不判**：响应模型里刻意**没有** `can_create`，因为能否建由 `assert_can_write_entrustment` 唯一决定，再给一个布尔值就是在权限上制造第二个真相 |
| 16 | 该查找**不按调用者身份过滤** | ✅ 新增 `access.find_active_entrustments(org_id, owner_user_id)`。初版写成复用 `resolve_context().delegations` 过滤，结果**货主本人**（不是任何组织的成员）拿到空元组 ⇒ 把"有授权"答成"没有"。**把调用者身份混进一次纯查找，会产出错误答案**；可见性由调用方先判（复用委托详情那条判据） |
| 17 | `ent_entrustment` **没有** (org_id, entrust_user_id) 唯一约束 | ✅ 同一对之间可并存多条生效授权（只有 `ent_org_member` 有唯一约束）。故多条时返回 `entrustment_id=null` + `note`，**一条都不自动选中** —— 猜错的后果是把会话挂到**另一条**授权上，数据边界随之改变，而界面上看不出来 |
| 18 | `GET /sessions` 新增 `assignment_id` 过滤 | ✅ 会话页从工作台进来时只有委托单号。反事实用例证明它**真在过滤**：换一张没有会话的单号必须为空，而不是"忽略参数返回全部" |
| 19 | **演示种子的授权补第 7 项权限** `entrust:agent:job` | ✅ `seed_entrust_demo.py` 的 `ORG_PERMISSIONS` 6 → 7。会话与 Agent 作业是**另一项**权限：缺了它，演示组织能看工作台、能认领，却在"进入专属会话"这一步直接 **403**（表现为"页面能打开但建不了会话"）。只读成员不该有这一项，而演示组织的经理正是要让 Agent 干活的那个人 |
| 20 | ⚠️ **发现但本轮未修**：作业投影没有 `mocked` | `mocked` 只存在于 `ent_agent_job_attempt`（`agentjobs._row_to_job` 不返回它），而会话页读的是**作业列表** ⇒ `decorateJob().mocked` 恒 `false`，界面上"本页含 fixture 结果（LLM_MOCK）"这句**永远不会出现**。要修得让作业投影带上它（取最近一次尝试的 `mocked`）⇒ 独立切片。**本轮不假装验过**：e2e 对这条只记 note |
| 21 | **前端缺陷（本轮修）**：`decorateJob` 的提案键写成 `proposals` | ✅ 后端信封的键是 **`artifact_proposals`**（`envelope.project_envelope_for_operator` 的返回）。写错**不报错**，只让"提案 N 条"永远是 **0** —— 界面上看起来像"模型没给出任何提案"，而事实是读错了键。由 e2e 新增的"作业成功 ⇒ 页面必须列出提案"断言抓到 |
| 22 | **前端缺陷（本轮修）**：`ensureSession` 用了组织选择器的 `pickEntrustment` | ✅ 它返回 `{orgId, needPick}`，**不是** `entrustmentId` ⇒ `entrustmentId` 恒 `undefined`，页面**必然**掉进"没有可用的委托授权"这一支，而真原因是取错了函数。改为只走 `session-context`（第 15 条） |
| 23 | e2e harness：新增取数函数**必须登记进 `requireStub`** | ✅ 漏登记的会落到真实 `request.js`，在 Node 里（无 `wx.request`）直接抛错、被页面 `.catch` 吞成空数组 ⇒ 字段永不产出。本轮 5 条取数 + 4 条写命令全部登记；bootstrap 新增 S2 真写段（真建会话 → 真发消息 → 真提交作业 → 真推进一次 → **复读**成回放载荷），走查新增 9 条断言（含"重进恢复"与"取数阶段不得发写请求"） |

### 7.5 第六切片（S2 第二片：人工采纳与跨视图同一份成果）

触发：BP-02 出口证据"**A correction appears in all shared views; stale output cannot
overwrite it**"；合同 §10.1 第 3 步。本节只登记与**接口面**有关的部分。

| # | 条目 | 状态 |
| --- | --- | --- |
| 24 | 作业投影新增 **`mocked`**（三态 `true/false/null`） | ✅ 补上 §7.4 第 20 条的缺口。实现取**最近一次尝试**的 `mocked`（重试后只有最后一次决定 envelope 是谁产出的）；**还没有尝试行 ⇒ `None`**，不补 `False` —— "没跑过"与"跑过且不是桩"是两句不同的话。此前投影完全没有这个字段 ⇒ 界面**无从判断**，只能把桩显示成真实结果（那是"数据没下发"，不是"界面没做"） |
| 25 | 采纳提案 = **复用既有** `POST /agent/jobs/{id}/adopt` | ✅ **端点未改、`scope_matrix` 63 条不变** —— 这是**新增消费方**，不是新增接口。前端 `adoptJobProposal` 此前后端已有、页面零调用：作业卡写着"需人工采纳"却**没有任何采纳入口**，是"后端就绪、前端未接"的又一例 |
| 26 | ⚠️ **本机 `.env.local` 会让本地复现的端到端真调模型**（不入库，含真 `LLM_API_KEY`，且把 `LLM_MOCK` 顶成 false） | 已在本地复现脚本里强制 `LLM_MOCK=true` + 清空 `LLM_API_KEY`（环境变量优先级高于 env_file）。**由此定一条判据纪律**：模式类断言**不得写死"必须是桩"**，要判"页面与后端**一致**" —— 写死会让同一份代码**本地红、CI 绿**，而那是环境差异、不是产品缺陷。原则同"评测要先还原成外部人的环境" |
| 27 | BP-02 要求"attachment selection"（§10.1 第 2 步"上传样报价单"） | ✅ **已实现**（§7.6 第 28–37 条落地上传链，§7.8 第 49 条补页内样本入口）。两处订正：① 当时写"前端没有任何上传入口（`chooseMessageFile` / `uploadFile` 全仓 0 处）"，现状是**两条通路都在**且复用同一条 `uploadQuote`；② **演示口径已裁定**（合同登记本「口径登记」第 1 行）：**内置样本＝主演示路径**，原生选择器入口保留、其 **OS 级自动化暂缓**（工具能力边界，详见 §7.8 第 49 条与 `runbook` §8.1） |

### 7.6 第七切片（S2 第三片：上传报价单 → 提取 → 引用进 AG-02）

触发：BP-02 的 attachment selection 与 §10.1 第 2 步「A1 uploads the sample quotation and
invokes AG-02」；编号接 §7.5（第 27 条）列出的那个缺口。本节只登记与**接口面**有关的部分。

| # | 条目 | 状态 |
| --- | --- | --- |
| 28 | **不新增任何端点、不改任何端点** | ✅ `scope_matrix` **63 条不变**。本片全部复用既有三条：`POST /attachments`（multipart）、`POST /attachments/{id}/extract`、`GET /entrustments/{eid}/attachments`。它们是**新增消费方**（此前只有后端/脚本在用，页面零调用） |
| 29 | ⚠️ 「**已上传**」≠「**Agent 读得到**」是**既有后端事实**，不是本片新增的规则 | ✅ `agentjobs._attach_text_excerpts` 只收 `extract_status='done'` 的附件文本；`runner.build_source_catalog` 也只在 `done` 时才把 `(attachment_text, <id>)` 放进来源目录 ⇒ **上传后不提取，附件对 Agent 只是"一个文件名"**。界面上因此必须把"上传"与"能读到"分成两句说 |
| 30 | 引用附件的作业**只能传 `input.attachment_id`，绝不能同时传 `quote_text`** | ✅ 后端 `ag02._quote_source` 的优先序刻意固定为「操作者粘贴文本 > 附件已提取文本」：带上 `quote_text` 时附件会被**静默忽略**，页面上一切正常而 Agent 读的根本不是那份附件。**界面口径由此确定**，并由后端用例 + e2e 双向钉住 |
| 31 | 前端**新增 `extract_status` 七态标签表**（`EXTRACT_STATUS_LABELS` / `EXTRACT_STATUS_ORDER`） | ✅ 原写法只有「`done` / 其它」两支 ⇒ `needs_transcription`（要找人转录）与 `failed`（提取真的坏了）在界面上**长得一模一样**，都显示"未提取"，而这两件事该做的下一步完全不同。七态取自后端 `attachments.EXTRACT_*`，并在 `verify_entrust_ui.js` 里**逐格比对**（含"顺序表与标签键集合相等"与"未知取值照实回显"两条） |
| 32 | `decorateAttachment` 新增 `hasText` / `canReference` / `referenceHint` / `sizeText` | ✅ 纯**展示**字段，**不参与判权**：能不能引用由后端来源目录决定（见第 29 条），前端只是把"能不能"和"为什么不能"说出来。未提取时界面**不摆出**可点的引用按钮（与"权限未加载就不摆按钮"同一条处置） |
| 33 | 合成夹具**落仓库受版本控制路径**并带数据标签 | ✅ `backend/scripts/fixtures/DEMO1-SYNTHETIC-sample-quotation.txt`（合成钢铁货报价单）+ `docs/entrust/milestones/DEMO-1-fixture-manifest.md`（合同 §7 要求的 labeled fixture manifest）。**演示与 CI 用同一个文件** —— 不存在"演示用 A、CI 用 B"的分叉 |
| 34 | CI 的端到端 job 里**真 multipart 上传 + 真提取** | ✅ harness 新增 `apiUpload()`（`fetch` + `FormData` + `Blob`；**不能**带 `Content-Type: application/json`，否则 FastAPI 按 JSON 解 multipart 体直接 422）；bootstrap 用夹具真上传并提取，走查再**驱动页面的上传路径**（`uploadQuote` → 真 multipart → 真提取），判据取**后端事实**而非页面文案 |
| 35 | ⚠️ **e2e 的固定 `tick(n)` 换成「等终态标志」**（新增 `waitUntil`） | ✅ **本轮实测**：页面驱动的写走真网络，链路越长（消息 → 作业 → 推进 → 重新取数）固定休眠越像赌博 —— 机器慢一点，断言就在"写还没落地"时读结果，报成**"页面少写了一笔"**，与真缺陷长得一模一样（本轮一次运行 6 条这样的假红）。判据改为"这件事到底成没成" |
| 36 | ⚠️ **页面方法必须 `return` 自己的 promise 链** | ✅ `uploadQuote` 原先没 `return` ⇒ `await this.uploadQuote(f)` 等到的是 `undefined`，调用方（含 e2e）立刻往下走，读到"上传完、提取还没回来"的中间态，表现成**"上传了但没提取"**。这是一条通用约定：**异步动作方法返回值就是它的完成信号** |
| 37 | 新导出登记进两处 harness 的桩 | ✅ `verify_frontend_e2e.js` 的 `requireStub`（新增 `uploadAttachment` / `extractAttachment`，两者都走**真网络**而非回放）+ `verify_ui_interactions.js` 的 `entrustStub`（`Object.assign({}, REAL_ENTRUST, …)` 派生，新增导出自动可用）。漏登记的后果见 §7.4 第 23 条 |
| 38 | ⚠️ **夹具与种子之间存在口径偏离，本片不自行裁定** | ✅ **已由 HO 0917-3 裁定一裁决**（`DEMO-1-fixture-manifest.md` §4）：**以合同为准** ⇒ 新增 canonical 夹具（钢材 **800 吨**、公—水—公、CNY 45.00 元/吨），旧件 F-1（1200 吨）**保留不动**作旧回归数据；本条文末的限制"不得声称 §3.1 夹具已完全对齐"**就此解除**（canonical 已对齐；旧件保留是**刻意**的，不是没做完） |

### 7.7 第八切片（S2 收口：来源完整性 + 附件恢复通道；HO 0917-3 裁定一/二/四）

触发：`近期HO 决策…0917-3.docx`。裁定一（夹具以合同为准）、裁定二（来源修复不能只补一句
"ref 写纯 ID"）、裁定四（重抽/人工转录最小通道）。本条登记与**接口面**有关的部分。

| # | 条目 | 状态 |
| --- | --- | --- |
| 39 | ⚠️ **`validate_envelope` 此前只遍历顶层 `source_refs`** | **真缺陷，已修**。`findings[].source_refs` 里的引用**完全没被核对** —— 而 findings 恰恰是模型解释"我为什么这么判"的地方（本次 live 输出的描述性引用就有两处在 findings 内）。现在核对**所有**声明的引用，并在 `unverified_sources` 里带上 `where`（`source_refs` / `findings[i].source_refs`）—— 经理要能一眼看到**哪里**需要核对 |
| 40 | **`query_parsed` 字段契约补 4 项**：`currency` / `rate_unit` / `includes` / `excludes` | **接口面变化（取值域扩张，纯增）**。此前它们不在契约里 ⇒ 真实模型给出的这四个字段被登记成**未知字段**，而它们恰好是 BP-02 要展示的业务内容。另外 `rate` 只说"45.00"、**不说这一价是每吨还是每柜** ⇒ 计价单位必须显式落在 `rate_unit` 上 |
| 41 | ⚠️ `_parse_quote_text` 把**计价单位**记成了 `quantity_unit` | **真缺陷，已修**。`元/吨` 的"吨"是**单价的分母**，不是数量：两者恰好同名时看不出问题，换成"每柜 3000 元"就会凭空造出一个不存在的数量事实。现在 `rate_unit`（计价单位）与 `quantity_unit`（数量单位）分开，并新增数量的确定性抽取与币种识别 |
| 42 | **提示词给出"可原样复制的来源目录"** | `runner.run_agent` 计算 `build_source_catalog` 后**同时**喂给提示词与来源核对（**同一个集合**）—— 提示词给 A、校验认 B 的话，模型照抄也会被判编造，比不给更坏。目录逐行渲染 `kind=... ref=...`，并在"文本来自附件提取"时点名必须出现 `attachment_text` |
| 43 | **`GET /attachments/{id}` 与两个列表新增 `text_source`** | **接口面变化（纯增字段）**。它让界面能区分"机读提取 / 人工转录"，并据此决定"重抽要不要先问"。采用 `LEFT JOIN ent_attachment_text` 一次带出，**不是**每条再查一次 text 端点（避免 N+1） |
| 44 | ⚠️ **重抽会覆盖、且失败分支还会 `drop_text`** ⇒ 人工转录可能被静默毁掉 | **新增守卫**：该附件当前文本来自 `manual_transcription` 时，`POST /attachments/{id}/extract` **默认拒绝（409）**，要求显式带 `acknowledge_transcription_overwrite=true`。新增异常 `AttachmentTranscriptionOverwriteError` → **409**（请求合法、与当前状态冲突）。响应新增 `previous_text_source`，让"这次覆盖掉的是人工还是机器文本"可查 |
| 45 | ⚠️ **幂等载荷里掺进了"读自当前状态"的值** | **我自己引入又当场修掉的真缺陷**：把 `previous_text_source` 放进幂等 `payload` ⇒ 同一个键的第二次**合法**调用因载荷不同被判成"同键异体"→ 409（`test_extract_is_idempotent_under_same_key` 当场红）。**幂等载荷只能由请求派生**（附件 id / 内容指纹 / 参数）；状态类信息放**响应** |
| 46 | **前端补两张镜像表 + 逐格比对** | `TEXT_SOURCE_LABELS`（⇄ `attachments.TEXT_SOURCE_*`）与 `SOURCE_KIND_LABELS`（⇄ `runner.KIND_*`），并断言「`attachment` 与 `attachment_text` 必须是两个不同的词」「未知取值照实回显」「只有 done 才 canReference」「只有人工转录才需要在重抽前确认」 |
| 47 | **夹具：canonical 配置落地（裁定一）** | 新增 `backend/scripts/fixtures/demo1_canonical.json` + `DEMO1-canonical-sample-quotation.txt`（钢材 **800 吨**、公—水—公、CNY 45.00 元/吨、**单船承运不拆批**），候选 C-900（变更后不适用）与 C-1200（变更后仍可完成）。CI 端到端**改用 canonical**；旧件 F-1（1200 吨）**保留不动**作为旧回归数据。F-Q1/F-Q2 记为**已裁决**（`fixture-manifest` §4） |
| 48 | **e2e 新增"未确认的重抽不得毁掉人写的内容"这条不变量** | 判据写成两种合法形态之一：① 页面知道来源是人写的 ⇒ 停在确认态、不发请求；② 页面信息过期（例如别人刚转录完）⇒ 请求发出但**服务端 409 拒掉**并进入确认态。**②不是缺陷** —— 它正是服务端守卫的意义；但"未确认的重抽**成功了**"必须红 |



### 7.8 第九切片（主演示第 1–3 步设备走查 + 由此产生的界面调修；2026-09-17）

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 49 | ⚠️ **上传报价单此前只有 `wx.chooseMessageFile` 一条通路** | **界面缺口，已补**。原生文件选择器是 **OS 级弹层**、不在小程序渲染树里 ⇒ 自动走查够不着它的选择项，"演示第 2 步（上传样报价单）"**永远拿不到设备证据**。技能 `miniapp-device-walkthrough` 把这条写成**界面要求**：附件入口必须另给一条"预置样本"的页内通路。新增「用内置示例报价单」入口，**复用同一条 `uploadQuote`**（另写一份上传逻辑，会让"示例通路能过、真实通路没过"这种分叉在演示当天才暴露）。样本文本 `SAMPLE_QUOTE_TEXT` **由脚本从后端 canonical 夹具生成**（不是手抄），并由 `verify_entrust_ui.js` 做**逐字节比对**防漂移 |
| 50 | ⚠️ **成果页「设为生效版本」用原生 `wx.showModal`** | **真缺口，已改**。确认键是原生控件、走查工具点不到，而合同 §10.1 第 3 步「更正一个字段 → **从工作台打开同一份成果**」**必须先让更正的版本生效** ⇒ 这条路径本来拿不到设备证据。改为**页内确认条**（受理 / 应用变更 / 记录任务之后的**第 4 处**）。**保留函数名 `onConfirm`** ⇒ 三处调用点零改动；文案仍由 `confirmCard()` 生成（判据**不放松**：仍须点名「成果 #N · vK」） |
| 51 | **走查锚点登记 11 条**（session 5 + artifact 4 + 确认条 2） | 登记进 `verify_miniapp.js` 的 `WALK_ANCHORS`。⚠️ **不登记 ⇒ 下次改模板把锚点弄丢时不会有任何提示**，走查会静默退化成"点不到"（技能把这条列为强制）。锚点此前**不存在**：`.att-btn` 有三个同类、`.btn-primary` 有两个、`.confirm-ok` 有多个，而工具**没有 index 参数** ⇒ 无唯一锚点就点不到指定那一个 |
| 52 | **e2e 的成果确认驱动段同步改造** | 从"读 `wx.showModal` 的入参"改为"读页面 `confirmRevNo` / `confirmText`"，驱动走 `onConfirmSubmit()`（**页面真实的确认路径**，不绕过确认条直接打后端）。判据**不放松**：仍要求确认文案点名成果 ID 与精确版本 |
| 53 | **e2e 新增一条"前端副本 ↔ 后端夹具逐字节一致"** | 前端带了一份 canonical 样报价单的副本；它由脚本生成、但**生成也会过期**（夹具改了、前端没重新生成 ⇒ "演示用一份、CI 用另一份"，两边都对不上）。这条比对是唯一拦得住这种分叉的东西 |
| 54 | **㊸ 章设备走查 + 驱动脚本**（`scripts/verify_miniapp_devtools.py` 的 `sec_43`） | 合同 §10.1 前三步的**连贯**演出（既有 ㉞/㊳/㊶/㊷ 各只覆盖一跳）。走查驱动在**工作区**（不入库）：`_q8_walk43.py`，四件事同进程（起 IDE + 起后端 + 铺种子 + 跑走查），并在子进程 env 里强制 **fixture 模式**（`LLM_MOCK=true` + 清空 Key）⇒ **本次设备走查不是 live 证据**，真实模型路径另见 D1-03 |
| 55 | ⚠️ **闸门预算与"假耗时"两处修**（走查驱动脚本） | ① 模板给的 24 次（≈120s）预算在**冷 `CompileCache`** 下**必然假阴性**（IDE 要 ~9 分钟编译；实测 120s 报恒空 → 清场重起 → 又 120s 恒空，而**同一时刻** IDE 日志里 `W.loadPage` / `onInstanceLoaded` 正在刷）；② 用"次数 × 常数"冒充耗时会把"慢"显示成"卡死"（技能坑 33）。已改为：预算 150 次、打印**真实耗时**、**打印回执**（`ok` / `errorType` / `message`，用 `page_stack_probe()` —— 只返回 list 的方法会把"通道问题"与"空栈"压成同一个 `[]`） |

### 7.9 第十切片（口径统一 + 第 1–3 步证据收口；2026-09-17）

触发：HO 0917-3 待裁决清单（原生选择器与内置样本 / D1-03 真实模型跑 / fixture 解析误判 /
S3 的 A、B / 发布前来源核验 / S3、S4 排期）。本节只登记与**接口面与口径**有关的部分。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 56 | ⭐ **演示口径统一**：内置样本＝**主演示路径**；原生文件上传**入口保留**；**OS 级自动化暂缓** | 登记三处：合同登记本「口径登记」第 1 行、`runbook` §8.1、`readiness` §9.12③。**合同正文一字未改** —— §10.1 第 2 步原文即 `uploads the sample quotation`（**样本**），本裁定不改动任何 mandatory business outcome，故不触发 §8.4 修正；原生选择器的自动化按 §7 暂停语义处置，且**若日后成为强制条件须按 §7 末段作为具体依赖上报**。⇒ 这一格从"待裁决"变成"有明确处置"，**不再挡第 2 步的演示** |
| 57 | ⭐ **D1-03 live 证据改走「真实会话／作业入口」** | 新增 `backend/scripts/demo1_d1_03_live_session.py`：`POST /assignments` → `submit` → `claim` → 上传 multipart → `extract` → 建会话 → **提交作业 → 推进作业** → 采纳为成果，**六个 id**（assignment / entrustment / session / **job** / attachment / **artifact+revision**）一并落盘。**不新增任何端点**。判据取**作业行的 `mocked`**（不取调用方 env）；`unverified_sources` 非空 ⇒ **记 FAIL 且非 0 退出**（不靠重跑到碰巧通过）。实测 2 次真实调用均 `[]`、`source_kinds=['attachment_text']` ⇒ **PR #131 的提示词契约修正在真实会话里生效**（08:48 那轮"模型给描述串 ⇒ 核对不上"未复现）。证据：`evidence/D1-03-live-session-20260917T115436.json` + `-notes.md` |
| 58 | ⚠️ **AG-02 规则模板的抽取边界（"fixture 解析误判"）** | **真缺陷，已修**（`ag02._CARRIER_RE` / 航线规则）：① 公司名后缀由**可选**改为**必须**、且左右不得紧挨汉字 —— 旧写法把夹具首部那句「…也不是真实航运报价」切成了 `carrier="也不是真实航运"`；② 无标签航线规则改为**只认「整行就是一个箭头短语」**、且只认 `→` / `->` —— 旧写法把「运输方案（公路 — 内河 — 公路）」里的并列短语当成了 `route="公路→内河"`（真值 `南宁→贵港`）。**抽错比抽不到更坏**：抽错会变成"看起来正常的字段"一路流到客户看的报价上。**夹具文本一字未改**（它是对外承诺的一部分） |
| 59 | ⚠️ **回归钉子必须读"真夹具"而不是删减副本** | 既有用例用的是一份 **5 行**的 `_SAMPLE_QUOTE`，**不含**上述两句（数据标签句 / 运输方案括号行）⇒ 两个缺陷在它上面**永远是绿的**。新增 `test_quote_parsing_on_the_real_fixtures_does_not_slice_sentences`（parametrize 读 canonical 与 synthetic **两份真文件**），钉住 `carrier` / `route` / `rate` / `rate_unit` / `quantity` / `valid_until`。**红绿对照已实测**：把旧规则还原后该断言红、新规则绿 —— 证明它钉的是真缺陷、不是恒真断言 |

### 7.10 第十一切片（S3 纵向切片：发布 → 冻结内容 → 客户响应；2026-09-17）

触发：HO 0917-3 待裁决清单与执行顺序第 3 条 —— *「下一条完整交付应是：经理发布指定版本 →
货主查看冻结内容 → 接受／拒绝 → 经理看到响应。权限、失效版本、重复请求和并发处理随这条
业务一起完成。」* 本条只登记与**接口面**有关的部分；出口与正负例判据见
`docs/entrust/S3-发布与客户响应数据设计.md` §5/§6。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 60 | **新增 8 条端点**（`scope_matrix` **63 → 71**） | 发布 / 按授权列发布记录 / 客户列"我收到的发布" / 按身份给发布详情 / 客户响应 / 撤回 / 来源台账读 / 来源台账写。双向覆盖自检（openapi ↔ 矩阵）自动通过；条目数基线按惯例显式改。 |
| 61 | **两条通道一个路径、服务端选投影** | `GET /offer-releases/{id}` 由**身份**决定返回客户投影还是经理投影（客户投影不含 `released_by` / `artifact_id` / `customer_snapshot` / 来源台账）。这与 AC-26「不得先返回前端再隐藏」是同一条：投影在服务端算完再发。 |
| 62 | ⚠️ **一处刻意的不对称**：局外人 **404**、**经理冒充客户 403** | 经理**知道**这条发布存在（他有权看），但没有替客户确认的权限 ⇒ 用 403 说清"看得见、无权"，而不是用 404 把权限结论伪装成缺数据。note 已写进矩阵。 |
| 63 | ⚠️ **响应模型与服务层键名不一致会静默降级**（本轮实测缺陷） | 发布端点最初把服务层原始 dict 直接喂给响应模型：服务层用 `snapshot`，响应模型要 `customer_snapshot` ⇒ pydantic **不报错**，用默认值 ⇒ **"发布成功、客户快照是空的"**，而库里那份快照好好存着。修法：端点走 `project_release_for_manager` 再出模型；并加**两道**钉子 —— ① 用例断言"响应里必须带客户快照与门槛状态"；② 服务层写读后若快照为空**直接中止**，不留"已发布但内容为空"的记录。 |
| 64 | ⚠️ **权限按 (组织, 货主) 作用域解析**（DR-0008），不是按"某一条授权记录" | 用例初版按"每条授权独立"来写 ⇒ 期望 403、实得 200（给同一个 (org, owner) 再插一条只读授权**不会**收回权限）。这是一条**容易把用例写绿**的口径，已在用例注释里写清。 |
| 65 | **发布前来源门槛：新增台账表 + 拒发带清单** | 见 §7.10 上方说明与 `S3-发布与客户响应数据设计.md` §6.2。`ent_artifact`/`ent_artifact_revision` **没有溯源列** ⇒ 门槛改由"声明行（服务端写）+ 核验行（人写，带依据）"两张事实共同判定；`missing_declaration` 那条兜住"采纳与台账不在一个事务"的缝。 |
| 66 | **客户下载仍未接线**（如实登记） | `authorized_attachment_ids` 已随发布冻结，但"按这份清单放行下载"的端点属后续切片 ⇒ **不得**声称 BP-03 第 10 条已完成。 |
| 67 | **界面未做**（如实登记） | 客户侧「待确认报价 / 响应记录」与经理侧"发布这一版 / 看响应"均未实现 ⇒ 本切片目前**只能用 API 走通**，按 HO 口径**不算"按业务结果可演示"**。 |

### 7.11 第十二切片（S3 纵向切片收口：客户下载白名单 + 数据来源标注 + 界面上线；2026-09-17）

触发：上一轮的 5 条建议第 2/3/4 条 —— 即把第十一片留下的三处"如实登记未做"逐条兑现，
使这条链**按业务结果可演示**（而不是"按钮出现了"）。第 1 条（合入 PR #135）不在本条。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 68 | **新增第 9 条端点：客户按发布清单下载附件**（`scope_matrix` **71 → 72**） | `GET /offer-releases/{release_id}/attachments/{attachment_id}/download`。判据**只有**发布那一刻冻结的 `authorized_attachment_ids`。⇒ 兑现合同 BP-03 第 10 条。 |
| 69 | ⚠️ **清单外一律 404，不用 403** | 与 `attachments_api.load_visible_attachment` **刻意不复用**：那个是"按当前可见性放行"，而这里要求"**由发布那一刻决定**，不由事后可见性决定"。用 403 等于承认"这个文件存在、只是你不能下"—— 对客户而言内部底稿的存在性本身就是信息 ⇒ 用 404 一并抹掉。 |
| 70 | ⚠️ **再加一道跨单兜底** | 只判清单仍有缝：清单被改到**别的委托**上时，持有该 id 的人会拿到跨单文件。因此在清单判定之后**再**要求 `same_entrustment or same_assignment`。 |
| 71 | **数据来源标注落地**（合同 BP-03 第 9 条 / §11.1 的 `labeled synthetic/manual/live sources`） | 新增迁移 `ent_offer_source:2` → 表 `ent_artifact_origin`；`mode` ∈ `live`/`synthetic`/`manual`/`unknown`。**与来源核验台账 `ent_offer_source_check` 不共用一张表**：混在一张表会让发布门槛被污染（作业行的 mocked 事实会被当成永远卡住的待核验项）。 |
| 72 | ⭐ **判据只认事实，不采信调用方自述；`unknown` 不猜成 `live`** | 标注由**采纳作业**时按 `job.mocked`（`True`⇒`synthetic` / `False`⇒`live` / 其它⇒`unknown`）写入，不由请求体声明。解析顺序：有标注行 ⇒ 用它；`source=='manual'` ⇒ `manual`；其余 ⇒ `unknown`。**把 `unknown` 折成 `live` 等于替审计做了判断。** |
| 73 | **签署证据模式随快照冻结** | `SIGNATURE_MODE_LABELED_SAMPLE = "labeled_sample"` 写进发布快照，客户投影给 `signature_mode`。界面必须常驻显示这一行（藏进提示气泡里，"已接受"就会被读成一份已生效的法律签署）。 |
| 74 | **两条投影各给各的**（AC-26） | 经理投影给 `data_origin`（mode + **依据** `basis` + 内部 id，"凭什么说这是 live"要有据可查）；客户投影**只给 `data_origin_mode`**，不给 `basis`、不给内部 id。 |
| 75 | **界面上线**（第十一片的 `#67` 就此关闭） | 客户侧：委托详情新增「对客报价」卡（内容**一律**读发布快照、来源标注、样张签署说明、授权附件下载、页内响应表单）。经理侧：成果页在**版本行**上给"发布这一版 / 撤回"。三处**全部走页内确认条**，不用 `wx.showModal` —— 弹层的确认键工具点不到（技能 `miniapp-device-walkthrough` 的负面清单）。 |
| 76 | ⚠️ **静默缺陷：界面文案里的 Markdown 星号（本轮连踩 3 处）** | wxml 文本节点与 js 字符串**都不是 Markdown 渲染器**：写在注释里无害，写进文案就原样渲染成「**样本签署**」。**已修 3 处**（客户已响应行 / 签署模式说明 / 发布确认条文案），并**加了一道静态闸**（挂在 `verify_entrust_ui.js`，不新增 CI 步骤）。这道闸**已做红绿对照实测**：临时插入探针文件 ⇒ rc=1 且报出精确 `文件:行号`；删除后 rc=0。唯一豁免是会话屏的内置样报价单 —— 它是夹具文件的逐字节副本，星号属夹具原文。 |
| 77 | ⚠️ **`withdrawReleaseId` 的字符串归一（本轮实测缺陷）** | 装饰器把 id 经 `_sid()` 归一成**字符串**，而撤回条的展开判据写成数字 ⇒ `0 !== '0'`，撤回条**永远展不开**（且不报错，看起来像"按钮没接线"）。修法：data 位与比较两侧统一字符串，并在注释里写明这条坑。 |
| 78 | **走查锚点新增 12 条**（`verify_miniapp` 锚点 **75 → 87**） | 客户侧 `data-act-offer-accept/-reject/-submit/-cancel/-download/-origin`；经理侧 `data-act-release/-submit/-cancel` 与 `data-act-withdraw-open/-submit/-cancel`。`-origin` 登记为 **`static`**（纯展示、无 `bindtap`），其余为 `act`。`-download` 的值是**附件 id**（写成常量会让选择器退化成"第一个"，而"点第 2 份能不能下"正是本切片要证明的事）。 |

### 7.12 第十三切片（S3 合同派生：从已接受事实派生 + 逐字段来源表；2026-09-17）

触发：`S3-范围与依赖评估.md` §2 第 8 条与 **§3 依赖 E** —— *"第 8 条要求'从已接受事实派生'，
需要一个**可核对的字段来源表**（哪些字段来自哪份已接受版本）"*。出口与判据见
`docs/entrust/S3-合同派生切片.md`。本条只登记与**接口面**有关的部分。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 79 | **新增 2 条端点**（`scope_matrix` **72 → 74**） | `POST /offer-releases/{release_id}/contract`（从**已接受的那条发布**派生合同核对稿；幂等）与 `GET /offer-releases/{release_id}/contract`（读派生关系 + **逐字段来源表**）。双向覆盖自检（openapi ↔ 矩阵）自动通过；条目数基线按惯例显式改并写明原因。 |
| 80 | **权限取"产出成果"那一档**（`entrust:quote:create`），**不是** `quote:publish` | 派生 = 产出成果（与 `POST /entrustments/{eid}/artifacts`、`POST /agent/jobs/{id}/adopt` 同类）。拟稿与对客发布是可以分给两个人的两个动作，合用一个权限会抹掉这条区分。 |
| 81 | ⚠️ **读取端点刻意不给货主本人放行**（`assert_can_view_org`，**无货主旁路**） | 字段来源表里是 `release:12@v3` / `leg:4` / `assignment:7` 这类**内部编号**。`assert_can_view_entrustment` / `assert_can_view_assignment` 都有"是货主本人就直接通过"的旁路 ⇒ 用它就会把内部审计信息送出去。客户要看合同走**已有的发布通路**（把 `contract_review` 发布出去、读冻结快照），不为它新开通道 —— 否则"客户能看到什么"会有两个判据（与 #69 白名单下载同一条纪律）。用例把这条决定钉死：谁换成 `assert_can_view_entrustment`，那条断言会红。 |
| 82 | ⚠️ **一处刻意的不对称沿用**：未响应 / 已拒绝 ⇒ **409**；被接受的不是对客报价 ⇒ **400**；已派生过 ⇒ **409 且回 `existing_contract_artifact_id`** | 三者的语义不同（当前事实不允许 / 请求本身就错位 / 已经有一份），用一个码盖住会让客户端只能"再试一次"。409 里带上已有那份合同的 id：**"已经有一份了"是一句没用的拒绝**，客户端需要能直接去读它。 |
| 83 | **请求体刻意不接任何业务入参** | `ContractDeriveIn` 只有一个可选 `note`。合同内容**不能**由调用方给：它必须全部来自已接受发布（金额/费用范围/有效期）与委托单（当事方）。若将来有人想加 `amount` / `parties` 之类的入参，那等于允许手工编造合同条款 —— 与 BP-03 第 8 条直接冲突。这条**写在 schema 的 docstring 里**，不靠口头约定。 |
| 84 | 随本分支携带：**第十一片 #61 的"列表版"补齐**（PR #138） | `GET /entrustments/{id}/offer-releases` 原先**恒回经理投影**，而 `assert_can_view_entrustment` 对货主本人直接放行 ⇒ 客户能拿到 `data_origin.basis`（含 `job_id` / `job_mocked`）、`source_gate` 明细与 `released_by`。同一模块的详情端点**本来就做对了**（先判"是不是客户本人"）⇒ **同一份数据两个入口、判据不一致 = 其中一个泄漏**。修法：列表版照抄详情版的写法（不是各写一遍），并补一条**逐字段断言内部字段不出现**的用例。⚠️ 这是"两个入口不一致"型；#81 是"这条通道本不该有客户面"型 —— 同源不同型，都归 AC-26「不得先返回前端再隐藏」。 |
| 85 | **界面未做 / 设备侧走查未跑**（如实登记） | 本切片目前**只能用 API 走通** ⇒ 不得声称 BP-03 第 8 条"按业务结果可演示"，也不得声称合同 §10.1 第 7 步 PASS。另：第 7 步后半 `record labeled sample signature evidence` **仍是"标注有了、取证动作没有"**（`SIGNATURE_MODE_LABELED_SAMPLE` 随快照冻结；而附件上传路径**不接收 `evidence_kind`**，本轮已 grep 核实）。 |

### 7.13 第十四切片（S3 运力确认与有效期：候选事实 → 独立确认 → 逐规则判定；2026-09-17）

触发：`S3-范围与依赖评估.md` §2 第 3 条与 **§3 依赖 C** —— *"D1-06 要求'选中 ≠ 确认运力'，
且'过期/不适用不得确认'。这需要一个可判定的数据来源（确定性规则），否则只能用 LLM 意见 ——
合同明令禁止"*。出口与判据见 `docs/entrust/S3-运力确认与有效期切片.md`。
本条只登记与**接口面**有关的部分。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 86 | **新增 6 条端点**（`scope_matrix` **74 → 80**） | 写侧：`POST /assignments/{aid}/capacity-candidates`（登记**候选事实**）、`POST /assignments/{aid}/capacity-confirmations`（确认，幂等）。读侧：两条 `GET` 列表、`GET /capacity-confirmations/{cid}`（含**逐规则判定**）、`GET /capacity-confirmations/{cid}/recheck`（用**当前事实**重跑同一套规则，**只读**）。双向覆盖自检（openapi ↔ 矩阵）自动通过；条目数基线按惯例显式改并写明原因。 |
| 87 | **权限取"产出成果"那一档**（`entrust:quote:create`），**不新增权限常量** | 确认 = 产出成果（与 `POST /entrustments/{eid}/artifacts`、派生合同同类）。另：`registry.procurement_confirm` **本就有**类型契约（`supplier` / `agreed_scope` 必填，`evidence_kinds` 限 `receipt` / `payment` / `document`）⇒ 这一档不是新造，是**把既有契约第一次接上命令**。 |
| 88 | ⚠️ **整组 6 条端点都不给货主本人放行**（`assert_can_view_org`，**无货主旁路**） | 比 §7.12 的 #81 更硬：不只是"读取端点不放行"，而是**这条通道本就不该有客户面**。理由：候选行带承运人与供应商单价、确认行带 `agreed_amount` / `supplier`、判定文案里写着**需求量与缺口吨数**。客户要看采购结论**没有通道** —— 客户看到的是**对客报价的冻结快照**。用例把这条钉死：谁换成 `assert_can_view_entrustment`，那条断言会红。 |
| 89 | **写侧判据顺序固定**：组织成员（404）→ **唯一**生效授权（409）→ 写权限（403/409） | 顺序不是风格问题：成员资格排在授权解析**之前**，非成员探测只拿到 404，**不泄漏"这条委托存在"**。授权**多于一条不猜**（`find_active_entrustments` 是纯查找、不带身份过滤）—— 猜一条等于替 HO 决定权限口径。 |
| 90 | ⚠️ **两处刻意的不对称**（写在迁移文档里，不靠口头约定） | ① 逐规则判定表 `ent_capacity_rule_check` **没有 `outcome` 列** —— **有行就是通过**；② 确认**失败时不写任何行**（判定表只承载通过的那些）。理由：若把失败判定也落库，"这条规则当时通过了没有"就得再读一列才知道，而 `recheck` 会变成两套语义（冻结的失败判定 vs 当下的失败判定）。代价是"当时为什么没过"只存在于那一次 409 的响应体里 —— 这是**有意的**：一次被拒的确认**不是**需要留档的商业事实。 |
| 91 | **请求体只带三样**（`candidate_id` / `agreed_scope` / `note`） | 事实（承运人、吨位、船数、拆批、单价、有效期、证据类别与引用）**全部取自候选行**并**整体冻结**到确认记录上，调用方只能给"范围"这类业务判断。若将来有人想加 `capacity_tonnes` 之类的入参，那等于允许手工编造一条运力事实 —— 与 BP-03 第 3 条的 `identified evidence` 直接冲突。这条**写在 schema 的 docstring 里**。 |
| 92 | **闸门 `_assert_every_rule_reported`**：规则集四条必须**逐条**产出判定且**无 fail**，缺一条即中止 | ⚠️ 该异常（`CapacityInvariantError`）**不继承** `CapacityError`、也**不接进** `_map_errors` ⇒ 必然是 **500**。理由：`CapacityRuleError`（409）是"业务不允许"，`CapacityInvariantError` 是"**我们写错了**"；把后者映射成 4xx 等于把缺陷伪装成一句业务拒绝。与 `contracts._assert_every_field_has_source` 同一条立场：**"某件东西没跑"必须与"跑了且通过"在数据上可区分**，否则这种缺口**没有任何下游症状**、必然静默。 |
| 93 | **一列 `evidence_ref` 补在 `ent_capacity_candidate` 上**（本切片唯一的 ALTER） | BP-03 第 3 条的 `identified evidence` = 类别 + **引用**。只有 `evidence_kind` 时，"证据已指定"与"随便填了个类别"在库里**同形**。补列比另建表便宜；历史行允许 NULL（迁移的 `checks` 只断言列存在且可为空）。 |
| 94 | ⚠️ **三条实测缺陷**（都是"本机全绿、CI 会红"那一类，逐条留档） | ① **迁移按模块名排序**：初版文件名 `ent_capacity.py` 排在 `ent_commitment.py` **之前** ⇒ 新库 `no such table: ent_capacity_candidate`，而**既有库因为表已在而全绿**；改名为 `ent_commitment_capacity.py`，并把这条隐含规则显式写进模块 docstring 与切片文档。② **`IntegrityError` 一律翻译成"已被确认过"**：并发预跑里两个线程**都**拿到该结论，而库中**零确认行**（真因是 `ent_artifact.entrustment_id` NOT NULL 违例）；修法是**先查确认行、查不到就原样 `raise`**，并补回归用例。③ **数值列跨后端类型差异**（MySQL 读回 `Decimal` / SQLite 读回 `str`）⇒ 在 `_row_to_*` 里归一到**定长文本**（吨位 3 位 / 单价 4 位），否则响应模型声明 `str` 时 MySQL 上直接校验失败。 |
| 95 | **界面未做 / 设备侧走查未跑**（如实登记） | 本切片目前**只能用 API 走通** ⇒ 不得声称 BP-03 第 3 条"按业务结果可演示"，也不得声称合同 §10.1 **第 5 步** PASS（该步原文 *`Compare two quotations and record evidence-backed procurement confirmation`*）。§10.2 负例 *`unsuitable/expired resource confirmation`* 现在**只有自动化证据**。 |
| 96 | **两条口径解释待 HO 认可**（改起来是一行判定 + 一条用例，**无迁移代价**） | ① **登记只拒"录错了"**（承运人空 / 吨位非正 / 船数 < 1 / 单价与计价单位半边缺 / 证据类别不在取值域 / 航段跨单），**不拒**"还没证据、还没有效期" —— 那些是**确认时**的判定；否则 *`expired or unsuitable resources cannot be confirmed`* 这条规则**永远触发不了**。② **允许拆批 ⇒ 判定通过 + 记下趟数**（`ROUND_CEILING`），而不是"不通过"：`allows_partial_load` 是**资源自己的属性**，读成"必须走变更"等于替业务做了决定。依据是 `demo1_canonical.json` 的 `deterministic_capacity_rule` 与 `DEMO-1-fixture-manifest.md`:25「若允许拆批或多船承运，950 吨未必装不下」。 |
| 97 | **未擅自补 canonical 的 C-900 / C-1200 种子** | `demo1_canonical.json` 的候选是 900 吨**单船**；夹具第 25 行那句"不一定装不下"要用**多船或允许拆批**的候选才演得出来。补种子会**动既有基线**（CI 与本机共用那份夹具）⇒ 待 HO 定口径再动，不在本切片里夹带。 |
| 98 | 随本分支携带：**第十二、第十三切片未合并**（PR #138 / #139 等授权） | `S3-quote-assemble`（#138）与 `S3-contract-derive`（#139）均仍 `OPEN`；本切片叠在 #139 之上 ⇒ **合并顺序不能反**（`required_linear_history=true` ⇒ 只能 squash 且逐个来）。 |
| 99 | ⚠️ **本切片继承了 #139 的首轮 CI 红，已同步修复**（2026-09-17 补记） | #139 引入的 MySQL 语法错误（`ent_contract_field_source.source_kind` 的列注释折成两行相邻字面量 ⇒ `1064`）让**两个 MySQL job 双红**；本切片从 #139 旧 head 建出 ⇒ **同因**红。已把 #139 的三笔修正 **cherry-pick** 进本分支（`e03dff0` / `4a6355b` / `64ae978`）⇒ 两分支 CI 现均 **6/6 绿**。⭐ 副产物：`并发集成（MySQL 8.0）` job 本轮**第一次真正执行**（上一轮全是 setup 阶段的 `ERROR`）—— 本切片的 `test_capacity_confirmation_race_exactly_one_confirmation` 与 #139 的 `test_contract_derivation_race_exactly_one_contract` **首次获得实测证据**。合并顺序仍为 **#138 → #139 → #140**。 |

### 7.14 第十五切片（S4-a 委托结案结构：只落结构与迁移；2026-09-17）

触发：`DEMO-1-runbook.md` §7 第 3 行「已完成历史委托」夹具的阻塞理由 —— 不是缺一条命令，
而是**委托状态机没有终态**（`claimed` 是死状态、无出边）。HO 0917 Q4 裁定「必须进 S4」，
口径见 `docs/entrust/S4-委托结案状态机口径设计.md`，本切片是其 **S4-a**（结构部分）。
出口与判据见 `docs/entrust/S4-a-结构与迁移切片.md`。本条只登记与**接口面**有关的部分。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 100 | **`ent_assignment` 追加两列 + 一条索引**（本切片唯一的表结构变更） | `completed_at DATETIME NULL`、`financial_status VARCHAR(16) NOT NULL DEFAULT 'not_started'`、`idx_ent_assignment_status_completed (status, completed_at)`。三条迁移**只做 DDL**：无 `UPDATE/INSERT/DELETE`（由用例强制）⇒ 历史 `claimed` 行**不被推断成 completed**（§5.6：旧数据不推测完成）。 |
| 101 | ⚠️ **迁移条目 2 是一条方言不对称的条目**（MySQL 真做、SQLite 显式空操作） | `status` 列注释要同步为五个取值，而**列注释是 MySQL 概念**：SQLite 的列定义存在 `sqlite_master` 的 DDL 文本里，无法 ALTER。处理：MySQL 走 `MODIFY COLUMN ... COMMENT`，SQLite 分支写成**显式空操作** `SELECT 1`（**不重建表、不搬数据**）。为什么不用「省略 sqlite 键」：省略会让 `resolve_sql` 在**运行期**才抛错，而显式空操作是**静态可见**的。 |
| 102 | **模块名 `ent_assignment_completion` 就是执行顺序契约** | 执行器按**模块名字典序**应用（`migrate.py:load_entries`，**无声明式依赖**）⇒ 名字必须落在 `ent_assignment` 之后、`ent_attachment` 之前。排错会让迁移在**空库上**先于建表执行，而**既有库因为表已在而全绿**（同类缺陷见 #94 ①）。用例第 4 组把顺序变成断言。 |
| 103 | ⚠️ **代码取值域与前端镜像本切片不动**（待 HO 认可的口径解释） | 口径设计 §7 把 S4-a 写作「状态与迁移」。本切片把「状态」落在 **schema 与列注释**（五个取值），**没有**在 `assignments.py` 加 `STATUS_COMPLETED`。连锁反应是必然的：`verify_entrust_ui.js` 强制前端 `STATUS_META` / `STATUS_ORDER` / `STATUS_HINT` 与后端 `STATUS_*` **逐格一致**，而 `STATUS_ORDER` 派生**工作台筛选条** ⇒ 加常量的必然结果是界面上出现一个「已完成」筛选片，而**此刻没有任何数据能处于该状态**（等于暗示一条不存在的路径）。⇒ 留到 **S4-b 与 `complete` 命令同一个提交**里一起改。改起来是一个常量 + 前端三张表，**无迁移代价**。 |
| 104 | ⛔ **委托层不新增任何结案 / 重开端点** | `/assignments/` 前缀下无 `complete` / `close` / `reopen`（用例第 6 组）。⚠️ **判据必须限定层级**：`/tasks/{id}/complete`、`/exceptions/{id}/close` 是**任务层与案件层**的既有能力，一直都在，且**不得互相替代**（§5.7）。首版断言写宽（对全支线路径查关键词）⇒ **当场红**，真因正是这三层被混在一起 —— 断言的价值恰恰在于区分层级。 |
| 105 | **`financial_status` / `completed_at` 不进服务层投影** | `assignments.get_assignment()` 的返回字典里没有这两个键（用例第 7 组）。该模块本来就用**显式列清单**（`_ASSIGNMENT_COLS`）取数，新列不会自动漏出；用例把这条**钉住**，防以后有人图省事改成 `SELECT *`。理由：派生未接通时展示 `not_started` 等于把「还不知道」渲染成一个结论（§5.3.2 末条 / 规范 3.3）。 |
| 106 | **8 条闸门用例**（`test_entrust_s4a_schema_only.py`） | 把 §2 的边界写成可执行断言，而不是叙述。⚠️ **这类切片的失效方式是没有下游症状**：列加上了、默认值填上了，一切「看起来做完了」；而顺手多做的两件事（加常量、塞投影）在任何既有用例里都不会红。 |
| 107 | ⚠️ **界面与设备侧走查 `NOT_RUN`；「已完成历史委托」夹具仍不产出** | 本切片**没有可点的界面**（能力未开放 ⇒ 正确表现就是「没有入口」）。HO 0917 硬约束：该夹具必须走**真实业务命令**、不得直接改状态造出来 ⇒ 只能等 S4-b。**不得**把本切片读成「结案能力已就绪」，也**不得**据此声称合同 §10.1 第 8–12 步有任何进展。 |
| 108 | 随本轮携带：**#138 / #139 / #140 已按 HO 授权按序合并**（2026-09-17） | HO 指令「授权合并」⇒ 三支按 `#138 → #139 → #140` squash 合入 develop（`3ebc9af` / `82e600d` / `86b76430`），三条分支（本地 + 远端）已清理。⚠️ #140 合并前**因堆叠 + squash 产生 9 文件冲突**（`mergeable_state=dirty`），处置是**重建分支**（丢弃 patch-id 相同的冗余修正提交后 cherry-pick 自有提交），**不是**手工解冲突 —— 处理依据与无损证明见 PR #140 正文（⚠️ 该经过**只留在 PR 里**：原稿此处引用的「§7.15」当时并未落笔；其后 §7.15 被用作**第十六切片**，与本行无关 —— 勿据本节号回查）。 |

### 7.15 第十六切片（canonical 运力候选夹具：补上 §10.1 第 5 步的**数据侧**；2026-09-17）

触发：`DEMO-1-runbook.md` §7 的「夹具缺口」段 + `S3-运力确认与有效期切片.md` §9 第 4 条
「**不生成 seed 数据** …… ⚠️ **未擅自补 seed** —— 应由 HO 定口径」。**HO 已同意方案 A**
（按需种子，不动 CI 共用夹具）。⚠️ 本条只登记与**数据面 / 接口面**有关的部分；
夹具形状与实测见 `DEMO-1-runbook.md` §7.2，清单见 `DEMO-1-fixture-manifest.md`。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 109 | **新增按需种子 `backend/scripts/seed_entrust_canonical.py`** | 与 `seed_contract_cases.py`（④）、`seed_entrust_revalidation.py`（⑤）同属**按需追加**，**不进** `reset_demo_env.py` 的 `SEED_ORDER`：复位要的是「干净起点」，而夹具是**有状态的数据**，塞进基线会让 §6.3 那张基线表逐格失效。 |
| 110 | ⛔ **只新增一张委托，一个既有行都不改** | `demo1_canonical.json` 的 `does_not_replace` 逐字写着旧种子的 1200 吨主单与「收货港变更」样本**继续作为旧回归数据存在**（HO 0917-3 明令不要全局替换，会让既有测试基线漂移）⇒ 本切片**不改写** `seed_entrust_demo.py` 铺的委托 `#1` / `#2`。 |
| 111 | **航段靠直接 INSERT**（`ent_leg` 没有服务层建段函数） | 三段 `road` / `water` / `road`。它是"最小结构化航段对象"，既有建段只出现在**用例与迁移**里 ⇒ 种子按 `(assignment_id, seq)`（表上有唯一键）幂等写入。候选挂到**水运段**（容量问题出在这一段）。 |
| 112 | **3 条候选，三条口径写死在脚本里** | C-900（900.000／1 船／不拆批／45.00／2026-12-31）、C-1200（1200.000／同／47.50／2026-12-31）、**C-EXPIRED**（950.000／同／43.00／**2026-01-31**）。① 容量口径落**数据字段**（`capacity_tonnes`／`vessel_count`／`allows_partial_load`）—— 夹具第 25 行已说明只写「900」不够；② 过期用**绝对日期**，不用"今天减 N 天"（相对日期会让「过期」随运行日漂移，历史证据无法复算）；③ `evidence_kind` + `evidence_ref` 必须指向**具体夹具文件**。 |
| 113 | ⭐ **种子自带"规则预演"自检：跑真实 `capacity.evaluate()`** | 判据不是"我读了一遍行、觉得没问题"，而是**服务层写库前用的同一个 `evaluate`**。自己另写一份判断就得到第二份"适用性"定义 —— 两个定义必然有一天结论不一致。实测三条：C-900 变更前全过、变更后**仅 `capacity` 不过**；C-1200 前后**都全过**；C-EXPIRED **仅 `validity` 不过**。任一条与声明不符 ⇒ 种子**当场失败**（不打印"看起来就绪"的清单）。 |
| 114 | ⭐ **只登记候选、不做任何确认** —— Exit evidence 的正面证据 | 隔离库实测 `ent_capacity_confirmation` = **0**、`ent_capacity_rule_check` = **0**、三条候选 `status` 全是 `candidate`。这正是 BP-03 的 `A chosen quotation alone does not create confirmed capacity.`。⚠️ 脚本因此**刻意不提供**任何"顺手确认一下"的开关：那样会把这条证据毁掉。 |
| 115 | **两份新夹具，只增不改**（`DEMO-1-fixture-manifest.md` 的 F-3 / F-4） | `DEMO1-canonical-candidate-C1200-quotation.txt`（"变更后仍适用"那一家的证据）、`DEMO1-canonical-candidate-EXPIRED-quotation.txt`（§10.2 `expired` 负例的证据）。⚠️ 原夹具的**一份**报价单是**单一供应商**（西江航运 45.00 元/吨）⇒ 它只对应 C-900；把 C-1200 / C-EXPIRED 的 `evidence_ref` 也指向它，会让**引用内容与候选行自相矛盾**（单价/有效期都对不上），而那种矛盾在界面上看不出来。既有三份文件（F-1 / F-2）一个字节都没动。 |
| 116 | ⚠️ **两条必须知道的事实（不静默）** | ① **时效性**：两组日期都是绝对的 ⇒ **2027 年**起 C-900 / C-1200 会一起"过期"，"变更后仍适用"那一半就演示不出来。脚本会在**真实基准日**下重跑规则，与固定演示基准日结论不一致时**显式告警**；届时的处置是**重新生成**夹具日期（允许改绝对值、**不允许**改相对日期），且夹具正文与候选行 `valid_until` **两处必须同改**。② **已知缺口**：夹具用 `candidate_id`（`C-900` / `C-1200`）标识候选，但 `ent_capacity_candidate` **没有业务编号列** ⇒ 该编号在库里**没有落点**；脚本只把它当打印用的 `label`，**不塞进任何列**。 |
| 117 | 随本轮携带：**#141 / #142 已按 HO 授权按序合并**（2026-09-17） | `#141`（S4-a 结构迁移）→ `#142`（三道迁移静态闸门）squash 合入 develop（`7429143` / `47afbf5`）。#142 因 base 前移走 **server 端 `update-branch`**（`2824b96` → `e899838`），**未在本机做 git merge**（规避文件回滚故障）。⚠️ 早先一轮曾把 `mergeable_state=blocked` 误当成"不可合并"——它其实是**新 CI 尚在 pending** 的中间态，等 CI 全绿后自动变 `clean`。 |

⚠️ 本切片**不声称** §10.1 第 5 步验收通过：**界面与设备走查仍未做**。
它只让该步从「没有对象」变成「有对象、且判据能复算」。

### 7.16 第十七切片（S3 运力确认与有效期 · 界面：候选事实 → 逐条判定 → 独立确认 → 只读复算；2026-09-17）

触发：§7.13（第十四切片）把「确认 ≠ 选中」落在了服务端（四条确定性规则 + 逐规则判定），
但**界面面为空** ⇒ BP-03 第 3 条只能经 API 走通。本条只登记与**接口面**有关的部分，
出口与判据见 `docs/entrust/S3-运力确认与有效期切片.md`。
⚠️ §7.15 末句「界面与设备走查仍未做」写于本切片**合并之前**（第十六切片只补数据侧）——
界面见本节；设备侧走查见下条。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 118 | **落点复用既有详情页，不新增页面**（`pages/entrust/detail/`） | 运力是**一张委托**的属性（候选与确认都挂在 `assignment_id` 上），另起一页会立刻需要"从委托带过来"的参数、返回路径与一套重复的权限投影。代价是这四个文件变长（`detail.js` 836 → 1332 行、`detail.wxml` 407 → 715 行、`detail.wxss` 251 → 359 行），处置是把新增的取数、投影与文案**全部下沉到 `utils/entrust.js`**（3958 → 4346 行）：页面只调投影，不拼文案。 |
| 119 | **新增第三份权限投影 `permittedViewOrgs`（`entrust:view`）** | 详情页原有两份：`permittedOrgIds`（`entrust:assignment:claim`，认领）与 `permittedQuoteOrgs`（`entrust:quote:create`，组装报价）。运力**读**是**第三种**能力 —— 由 `assert_can_view_org` 判定，且**没有货主旁路**。三份投影各管一段，**不能互相代替**（混用会让"能看"与"能写"在界面上同时错）。 |
| 120 | ⚠️ **「该不该发这次取数」必须在请求之前判** | 客户侧发运力请求**必然 403**，而 403 被 `.catch` 吞掉与"这一单没有候选运力"**同形** ⇒ 静默降级成一个错误结论（"本单无候选"）。故 `load()` 分**两轮**：第一轮取五样（`fetchAssignment` / `fetchWorkbench` / `fetchMyOrgs` / `fetchMyOfferReleases` / `fetchSessionContext`，后三样失败各自降级、不打整页错误态），第二轮 `loadCapacity()` 依赖第一轮拿到的组织权限投影。 |
| 121 | **写侧判据取「授权可唯一定位 ∧ 该组织有 `entrust:quote:create`」** | 与后端 `_write_scope` 逐条对齐（成员 404 → **唯一**生效授权 409 → 写权限 403/409）。授权 id 的来源是 `GET /assignments/{aid}/session-context` 的 `entrustment_id` —— **不得在前端推导**（同一 (组织, 货主) 可能有多条）。代价是"取不到上下文就少一个入口"，取**保守**一侧（写入口宁可不显示，也不显示一个必然 409 的按钮）。实测坑见 #123 ②。 |
| 122 | ⚠️ **两种 409 必须分流**（处置**相反**，混在一起会把用户送进死循环） | 带 `rule_checks` ⇒ **规则不过**：把**逐条**判定照实显示（**含通过项** —— 后端特意全给，前端不得过滤），且**不刷新**（刷新会把用户刚看到的那张判定表顶掉）；带 `existing_confirmation_id` / `existing_artifact_id` ⇒ **状态冲突**：**必须刷新**（页面上那个按钮已经没有意义了，不刷新用户会对着它反复点）。 |
| 123 | ⛔ **两处实测缺陷**（都由本地复现 CI 的 e2e 抓到，**三个静态门禁照绿**） | ① **`data-df="cap-scope"` / `cap-note` 在 `onCapInput` 的映射表里没有对应项** ⇒ `bindinput` 照常触发、`ds.df` 照常读到，查表查不到就**静默空转**：真机上「本次确认覆盖的范围」一个字符都写不进去 ⇒ `onSubmitCapConfirm` 在页内校验处直接返回，**运力确认永远提交不了**。修法：映射表的值由裸字段名改成 **setData 路径**（`capForm.xxx` / `capScope` / `capNote`）。② **e2e 的 `session-context` 回放表只按 ⑮ 段挑中的那张委托写了键**，而本段挑的是"组织侧可达的第一张" ⇒ 缺键 → 被页面 `.catch` 吞成 `null` → `canRecordCapacity` 变假；症状**极具误导性**（长得像"该组织缺 `entrust:quote:create`"），而同段第 ③ 步却把三条候选直接写进去了。修法：选完 `aid` 后用真接口补一份进回放表，取不到即**显式失败**。⭐ 共同教训：**"工具回 success ≠ 落盘正确"** —— 模板上的 `data-df`、回放表里的键，都是"看着有、其实没接上"，而两者的表现都是**沉默**。详见 `S3-运力确认与有效期切片.md` §8.4。 |
| 124 | **投影层产出整句引用文案 `artifactRefText`，模板不拼 `成果 #` / `案件 #`** | 静态闸门 `verify_entrust_ui.js` 的「引用行的文案只有一份」会红：模板里写 `采购确认成果 #{{item.artifactId}} r{{item.artifactRevisionNo}}` ⇒ 同一句文案在**模板与投影两处**各有一份，改一处就静默不一致。修法：整句在 `decorateCapacityConfirmation` 里拼好，模板只渲染 `{{item.artifactRefText}}`。（该闸门**当场红**，不是它过严 —— 真缺陷。） |
| 125 | ⚠️ **`agreed_scope` 只落进成果载荷，读模型上没有它** | `procurement_confirm` 成果的载荷里有它（`capacity.py`），而确认行的读模型**没有** ⇒ 界面**只能经成果引用把用户带过去**（`artifact_id` / `artifact_revision_no`），不能就地显示"当时确认的范围"。这是**已登记的口径缺口**：不做前端推断、也不伪造一个字。处置见切片文档 §9。⚠️ **该口径已落地**（见 §7.18 条目 133–135）：确认行读模型现在带 `agreed_scope`（**仍不加列** —— 按冻结的成果版本投影）。 |
| 126 | **三处登记 + 9 条走查锚点**（先登记，设备走查未跑） | `verify_frontend_e2e.js` 的 `requireStub` 新增 4 条取数（走**真 HTTP**，证"写完立刻读得回来"）+ 2 条写命令（走**同一道 `WRITE_ENABLED` 闸门**，避免在"读"的段里污染后面的基线）；`fetchCapacityConfirmation` 单条**刻意不登记**（页面不用它）。`verify_ui_interactions.js` 的新桩**故意不打取数日志**（该脚本的日志断言要求 `:A-1_2$` 形态）。`WALK_ANCHORS` 新增 **9 条** `data-act-cap-*`，其中 `data-act-cap-partial` 的**两个命中值不同**（`'0'` / `'1'`）⇒ 按登记表语义**不写 `value`**（与已有条目的处理一致），并把"先登记、第 ㊺ 章还没写"写进注释。 |
| 127 | ⚠️ **界面已做 ≠ 第 5 步 PASS**（结论不变，理由换了） | 本轮证据是**自动化**的：本地隔离库 + 真后端 + 真 HTTP 写，`verify_frontend_e2e.js` ⑰ 段 7 步全过（`OK 436 · FAIL 0`）。**设备侧走查 `NOT_RUN`** ⇒ 按 HO 口径**不得**声称 BP-03 第 3 条"按业务结果可演示"，也**不得**声称 §10.1 第 5 步 PASS。§10.2 的 `unsuitable/expired resource confirmation` 负例同样**只有自动化证据**（用例 + 409 响应体），设备侧没有。 |
| 128 | **"两条候选并排比较"仍未做** | 界面上候选是**逐条**卡片（吨位 / 装载口径 / 单价 / 有效期 / 证据三态齐备），但没有把两条摆在同一屏里 ⇒ §10.1 第 5 步前半 `Compare two quotations` 仍只算完成后半。另：**`Compare` 的动作面**（谁比、按什么排序、比不过怎么办）本切片未定义，不在界面里夹带。⚠️ 本行写于 §7.18 之前，原文保留；**并排比较已在 §7.18 补上**，现状以该节为准。 |

### 7.18 第十八切片（S3 运力确认 · 范围口径落地 + 两条候选并排比较；2026-09-17）

触发：§7.16 留下两处缺口 —— 条目 **125**（`agreed_scope` 只落进成果载荷，确认行的读模型上
没有它）与条目 **128**（"两条候选并排比较仍未做"）。本条把两处一起收口：前者是 §10.1 第 5 步
**后半**的读数口径，后者是**前半** `Compare two quotations` 的载体。
⚠️ 条目 125 / 128 写于本切片**合并之前**（那是当时的事实，原文保留）；现状以**本节**为准。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 133 | **范围按「冻结的成果版本」投影，不给确认表加列** | `agreed_scope` 的事实来源是这次确认产出的 `procurement_confirm` 成果版本，而确认行上**冻结着** `artifact_revision_id` ⇒ 按它取那一版载荷（`capacity._scope_from_payload`）**永远是"当时那一版"**，不随成果后来改版而变。**不加 `ent_capacity_confirmation.agreed_scope` 列**：加了就是同一事实在库里存两份，改一处就静默不一致 —— 与 `contracts._assert_every_field_has_source` 防的是同类病。代价是一条**相关子查询**（每行执行一次）；一张委托上的确认行是个位数（`UNIQUE(candidate_id)` ⇒ 一条候选只能确认一次，见 §7.16 条目 118 那节的规则），不值得为它改写成 JOIN。载荷解析不了时**不静默返回 `None`**（抛）：载荷是本模块自己经 `registry.validate_payload` 写进去的，解析不了说明数据坏了，把**坏了**说成**没写**是"未知保持未知"的反面。 |
| 134 | ⛔ **实测缺陷：`project_confirmation` 是显式白名单，只加读模型、忘了登记投影 ⇒ 三个读端点齐声少掉那一项** | POST 确认 / `GET` 单条 / `GET` 清单**全都经这层投影出去**。症状：成果载荷里 `agreed_scope` **明明有值**（直接查库可证）、确认响应里却是 `null`，其余二十多个字段全部正确 ⇒ 表现成"**写进去了却读不出来**"。定位靠两条诊断先**二分**（"事实来源有没有" / "子查询取不取得到"，两条都通过）才缩到"响应的组装路径" —— **不先二分就会去改读模型，而读模型是对的**。修法：在投影里用**下标**登记（键缺失就该 `KeyError`，不静默）。并补一条**结构性用例**（用例 ⑥：读模型有的业务字段，投影必须都有）—— 它不钉某个字段、钉的是这条缝，且**反向验证过**（给读模型注入一个投影里没有的假字段 ⇒ 判据精确指名它；恢复后逐字相同、复跑全绿）。详见切片文档 §8.4。 |
| 135 | **界面落点：`scopeText` + 空值显式说「未记录」** | `decorateCapacityConfirmation` 加 `scopeText`（**只搬运、不推导**），模板在成果引用行下渲染。空值时**不编一句话**，而是明说「本次确认的范围未记录（确认时必填 —— 缺失说明那一版成果载荷异常）」—— 与候选卡片的三态证据同一条取向：**判不了的那一格必须自己说话**。同时把该函数里那段**已过期的注释**（原写"`agreedScope` 不在确认行的读模型上，这是一处已登记的口径缺口"）改成新口径：注释留在旧结论上，比没有注释更坏。引新节号时不写没落笔的号（§7.15 那次教训）。 |
| 136 | **「两家可比」的载体：列 = 候选 / 行 = 维度的并排表** | 卡片头那句「两家可比（吨位 / 装载口径 / 单价口径 / 有效期 / 证据）」在只有**纵向列表**时是**没有载体的承诺**：两家以上要上下滚动 + 靠记忆对齐，而"它们在哪一点上不同"正是要不要选它的依据。新投影 `buildCapacityComparison` 产出该表（`capCompareCols` / `capCompareRows`），落在候选清单**下方**、`WALK_ANCHORS` 不动（无新锚点）。**≥2 条才渲染**（一条候选没有"并排"可言），判据直接取行数组的长度，不引入额外的布尔量（少一个可能与列表不同步的开关）。 |
| 137 | **只标「有差异」，不标「哪条更好」** | 哪条更合适取决于货量、时效、能否拆批 —— 那是**方案判断**，投影给"推荐"等于替用户做商业决定。与 `capacity.list_candidates` 不按吨位排序是同一条取向。同理**行序固定为定义序**、不按"有差异"重排：顺序随数据变 ⇒ 同一份数据两次打开长得不一样。差异标记用**静态 class**（`cap-compare-diff`），不走"投影算 class"那条路 —— `class="{{...}}"` 是静态闸门扫不到的形态，少给一次运行期才知道的机会。空值**参与比较**（"这家没写有效期"本身就是差异）。 |
| 138 | **投影的输入是同一批已装饰对象** | `buildCapacityComparison(cands)` 的入参就是 `capCandidates` 那份数组，不重新装饰一遍：两份投影共用同一批对象，否则"卡片上写的"与"比较表里写的"会各自演化（同一个事实两个落点）。空态（客户侧 / 取数失败）**一律把两行数据一起清空** —— 留着上一次的表＝拿旧数据当现状展示，而这一格恰恰是决策依据。 |
| 139 | **e2e 只钉不变量**（⑰ 段 +5 条） | 列数 = 候选数（且 ≥2）；行序固定五项；每行列数 = 列数、且行上**不得多出**"推荐"之类的字段（`Object.keys` 精确比对）；`diff` 与 `cells` 取值**自洽**（用它的输出来判，不重抄一遍维度定义）；至少一行标了差异（防判据空转）。**不钉"哪一维该有差异"** —— 那等于把夹具的具体数值焊进断言，改夹具就红，而红的理由与产品无关。三处前端静态门禁照绿（`verify_entrust_ui.js` 断言数 **1105 → 1115**，新 class 全部在 `detail.wxss` 里有定义）。 |
| 140 | ⚠️ **结论不变：不得声称 §10.1 第 5 步 PASS，也不得声称 BP-03 第 3 条"按业务结果可演示"** | 本轮证据仍是**自动化**的：本地隔离库 + 真后端 + 真 HTTP 写，`verify_frontend_e2e.js` ⑰ 段全过（`OK 442 · FAIL 0`）。**设备侧走查 `NOT_RUN`** ⇒ 按 HO 口径**只有 PASS 计入通过**。⚠️ `Compare` 的**动作面**（谁比、按什么排序、比不过怎么办）仍未定义，本切片**不夹带**。 |

⚠️ 本节**不声称** §10.1 第 5 步验收通过：该步的前半与后半如今都只有**自动化证据**，
**设备侧走查仍未做**。

⚠️ **§7.19 之后这句话的档位变了**：**前半**（证据支撑的采购确认）已有**设备侧读数**；
**后半**（两条候选并排比较）仍只有自动化证据 ⇒ 整步结论**不变**（不得声称 PASS）。原文保留。

### 7.19 第十九切片（S3 运力确认 · 设备侧走查 ㊺ 章 + 由此产生的三处界面调修；2026-09-17）

触发：§7.16 / §7.18 把这条的**服务端**与**界面**都做完了，但证据全是**自动化**的
（用例 + e2e 回放）。自动化证据回答得了"逻辑对不对"，回答不了"**真机上点得动吗、
打的字进得了 `data` 吗**" —— 而 §7.16 恰好踩过一个**只在真机上暴露**的缺陷
（`data-df="cap-scope"` 不在 handler 的映射表里 ⇒ `bindinput` 照常触发、查表查不到就
**静默空转**，而三个静态门禁全绿）。本条把设备侧证据补上，并处置由此暴露的三处缺陷。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 141 | **走查章 ㊺ 自足：刻意不依赖 ㊸ 章** | 前置只用 `seed_entrust_demo.py` 铺的演示组织（`演示经营主体·工作台`）、`seed-owner`（该组织的经理）与委托 `演示委托·工作台样本`，**全部经 API 取 id、不写死**（种子重铺会变）。依赖一条长链会让本章的失败与 ㊸ 的失败混在一起，而两者的处置完全不同。新增 `sec_45`（`--section 45`），注册进 `SECTIONS` 与 `DEFAULT_ORDER` 末项。**避让已核实**：远端那条 `S3-step6-carrier-gate`（`8641f18`）改的是 `sec_44` 的函数体，且**其内容早已经 #137 进 develop**（`5e27125`），与本条不冲突。 |
| 142 | **判据取向：能落在渲染树上的，就不看内部状态** | ③a 原断言写的是「范围 / 备注可输入 + 提交可点」，实际只看了 `capConfirmKey` —— 而那个键**在确认条根本没渲染时也照样被置上** ⇒ 断言绿着、真机上什么都点不出来。已改成三个锚点（`cap-scope` / `cap-note` / `cap-confirm-submit`）**各命中 1 次**。同理 ④d 只证"复算键可点"（恒为 1，判据空转），拆成 ④d 入口 / **④e 点下去并数结果锚点** / ④f 复算前后确认行逐字相同。⭐ 教训：**内部状态键的绿是"代码跑了"，不是"用户看得见"**。 |
| 143 | ⛔ **三处"静态门禁全绿、真机上点不出来"的缺陷**（一处投影 / 一处投影 / 一处传输层） | ① `decorateCapacityCandidate.candidateId` 原样透传 API 的 `int`，而模板 `wx:if="{{capConfirmKey === item.candidateId}}"` 左侧是 `dataset` + `String()` 归一 ⇒ **`'2' === 2` 恒假** ⇒ 确认条整块不渲染（实测：`capConfirmKey='2'` 已置上、三个锚点命中数**全为 0**）。② `decorateCapacityConfirmation.confirmationId` 同病（对面是 `onCapRecheck` 的 `String(id)`）⇒ 「复算这条确认」点下去、请求也发了，**判定表不出现**。③ `utils/request.js` 的 `httpError` 统一 `String(detail)` ⇒ 后端 409 的**结构体** detail（`rule_checks` / `existing_confirmation_id`）在**传输层**就被压成 `'[object Object]'` ⇒ 页面那两条分支**都是死代码**，用户只看到「确认未提交（服务端返回 409）：[object Object]」，**而服务端响应体里四条判定一条不少**。修法：投影侧 id 类字段一律 `String()`（与 `taskOpenKey === item.key` 同口径）；传输层 `err.detail` 原样保留结构、需要字符串的地方显式调新增的 `detailText()`。⚠️ 四条前端静态门禁与 e2e **全程绿** —— 它们查得到"锚点写在模板里"，查不到运行时类型；**e2e 更是把传输层换成了桩**，结构被压平这一处它天生看不见。详见 `S3-运力确认与有效期切片.md` §8.4。 |
| 144 | **不许回归：新增两条可执行的判据（都做了双向反向验证）** | `verify_entrust_ui.js` **第 12 节**：登记「状态键 ↔ 投影字段」两对（`capConfirmKey↔candidateId`、`capRecheckId↔confirmationId`），逐对断言"模板里确有该判据原文"+"投影侧是字符串"+"两侧在 `===` 下**确实相等**"；只登记**有故障证据**的两对、不做全仓模糊扫描（其余同类比较按"未知保持未知"在案，但一旦取证必须补进表）。`verify_ui_interactions.js` **⑦ 节**：直接 `require` `request.js` 调 `httpError`，断言结构体 detail 不被压平（并顺手把 `describeError` 拼对象也会得到 `[object Object]` 一并改掉）。**反向验证**：摘掉一处 `String()` ⇒ 判据**红且精确指名**（`OK 333 · FAIL 2`，打印出被压成的字符串）；把 `e.detail` 改回 `detailText(...)` ⇒ 指名 `rule_checks` / `existing_confirmation_id`；两处**恢复后逐字相同、复跑全绿**。 |
| 145 | **可验证性优先：给复算判定表补 `data-act-cap-recheck-verdict` 锚点** | 判定表（`.cap-verdict`）原先**没有任何 `data-` 锚点** ⇒ 设备侧只能证到"复算键可点"，而「结果不出现」恰恰是实测到的那一版。锚点值取 `item.confirmationId`（与复算键同源），使"结果真的渲染出来了"成为可数事实。⭐ 与既有取向一致：**判不了的那一格必须自己说话** —— 这里连"这一格在不在"都要能判。 |
| 146 | ⚠️ **走查工具事实（`input_text` 自己不滚动）** | `Client.input_text` 只回一个 `ok=false`，**不滚动、不重试**（`tap` 有重试）。长页面里输入框掉到折叠线以下时打字**静默失败** ⇒ 范围为空 ⇒ `onSubmitCapConfirm` 被页内校验（`if (!scope) { setData({capHint:…}); return }`）**静默拦住**，连 Http 都不发 —— 症状是"④a 没落库 **且** ⑤ 拿到 200 而非 409"，看起来像"后端没挡"，实际是"前端根本没提交"。修法：所有 `input_text` 前显式 `w.scroll_into(sel)`，并把返回值收进断言明细（不看返回值等于"什么都没发生"）。⚠️ 冷编译约 9 分钟、就绪闸门总预算 `GATE_BUDGET_S = 900`。 |
| 147 | **前提不在就记 `not_run`，不记 FAIL** | ④b/④c/④d/④e/④f/⑤ 都以「④a 已落库」为前提。前提不在就如实记 `not_run` —— **一条根因不该摊成六个 FAIL**（第一轮 6 FAIL 实际只有一个原因：`input_text` 没滚动）。`not_run` **不是通过**，它只是**不虚报**；四档汇报里它单列，不计入 PASS 也不计入 FAIL。 |
| 148 | ⚠️ **结论不变：不得声称 §10.1 第 5 步 PASS，也不得声称 BP-03 第 3 条"按业务结果可演示"** | ㊺ 章实测 **`PASS=22 / FAIL=0 / NOT_RUN=0 / LIMITATION=0`**，22 条全是渲染树或 API 直证的读数（截图目录 `miniapp-device-artifacts/walk-20260917-215133/`）⇒ **"设备侧走查 `NOT_RUN`"这个读数已过期**，按"只有 PASS 才改口径"更新。但设备侧证据回答的是「真机上点得动、写进去读得回来」，**业务验收是 HO 的裁定**。另：界面侧「状态冲突 ⇒ 刷新」那一支**驱动不到**（候选确认后 `confirmable=false`，确认条不再出现）⇒ 由静态门禁守，**不得**记成"设备侧验过"。⚠️ 本轮**不验**「两条候选并排比较」（§7.18 的界面，证据另行登记）。 |

本地复现记录（收尾时）：13 项门禁 **13/13 PASS**（pytest 822 项：808 通过 / 14 跳过 / 0 失败 / 0 错误；
mypy 109 files；ruff 146 files）、CI 的「前端端到端」job 本地复现 **`OK 442 · FAIL 0`**
（`verify_login_flow` `OK 23 · FAIL 0`、revalidation 夹具连跑两遍走复用分支）。
⚠️ 本机 Windows 控制台是 **GBK** ⇒ `migrate.py` 会在**打完所有迁移之后**崩在 `print('✓ …')`，
脚本看到 rc=1 而迁移其实成功（CI 是 Linux/UTF-8，不受影响）⇒ 本地复现要带
`PYTHONIOENCODING=utf-8`（走查 runner 早已如此处理：`run_walkthrough_devtools.py:152`）。

⚠️ **新踩到一道不在 13 项里的门禁：CI 的「后端 lint + test」job 第 8 步对根脚本 `scripts/*.py`
跑 `ruff check` ＋ `ruff format --check`**，且显式带 `--config backend/pyproject.toml`。两个要点：
① **不带 `--config` 时本机默认规则集只报 6 条、带上后同一份代码现出 9 条**（`N806` ×6 /
`UP017` / `B905` ×2，**全部出在新增的 `sec_45`**）⇒ 本地 lint 绿不代表 CI 绿；
② 该步还查"根脚本必须已纳入 lint 或已登记豁免"的**登记完整性**。
⇒ **13/13 全绿仍会 CI 红**（本 PR 实证：唯一失败项、29s）。修法是纯惰性改动：id → 小写、
删局部 `DETAIL` 复用模块级同值常量、`_dt.timezone.utc` → `_dt.UTC`、`zip` 补 `strict=True`、
按 CI 规则重排 7 处换行（全落在 `sec_45` 区间内）。
⭐ **修完复跑证据**：㊺ 章第 6 轮（`walk-20260917-220518`）与第 5 轮读数**归一化后逐行完全相同**
（只归一会话内必然变化的三项：`pageId` / 探针耗时 / 输入框里的时间戳串）⇒
那 22 条读数不是"某一次碰巧跑出来的"，改名与格式化对判定**无影响**。

### 7.20 第二十切片（把两道"会漏的缝"收进本地门禁；2026-09-17）

触发：本轮在 ㊺ 章设备走查与 #147 的 CI 失败里各暴露出一处**门禁盲区** —— 两者都不是产品缺陷，是"门禁自己看不见"。本条处置它们。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 149 | **CI 的根脚本 lint 步原先不在本地门禁里**（13 项 → 16 项） | CI「后端 lint + test」job 的**第 8 步**对**仓库根** `scripts/*.py` 跑 `ruff check` ＋ `ruff format --check`（显式 `--config backend/pyproject.toml`），外加"根脚本必须已纳入 lint 或已登记豁免"的**登记完整性**。这三件事**原先一项都不在** `verify_local_gates.py` 里 ⇒ **13/13 全绿仍会 CI 红**（#147 实证：唯一失败项、29s）。现补为第 4 组 `rootscripts`。 |
| 150 | **范围不抄第二份**：由 `verify_ci_parity.py --json` 从 `ci.yml` 读出 | 该脚本的 docstring 早就写着"输出可直接喂给本地预演脚本，让'本地跑什么'由 ci.yml 决定"，但**从来没人消费它**。本次把 `root_scripts.{cwd,linted,exempt,ruff_config}` 接进来：范围只有一个来源。取不到计划就**记 FAIL**（不静默跳过 —— 跳过等于把这一组变回不存在）；`cwd` 不是 `.` 也记 FAIL（`--config` 是相对路径，cwd 一变整条命令的含义就变了）。`--ci-parity-only` **不跑**该组：CI 自己已经在跑。 |
| 151 | **两个半步各被独立证明会红**（mutation test） | 注入函数内大写变量 ⇒ 只有 `check` 红（⚠️ 第一次选错了位置：**模块级**大写变量不违反 N806，那次只证明了 format 半步）；注入 `vals=[1,2]` ⇒ 只有 `format --check` 红；造一个未登记的 `scripts/_zz_probe_unregistered.py` ⇒ 只有**登记完整性**红并指名它。三次都**字节级恢复**、复跑回全绿。⭐ 这也顺手证明了「`check` 通过 ≠ `format` 通过」是**真的**（注入一个时另一个纹丝不动）。 |
| 152 | ⛔ **e2e 看不见传输层**：它的取数助手把 `utils/request.js` **整个换成了桩** | 桩里只有 4 个导出（`request` / `getToken` / `setToken` / `clearToken`），真实 `httpError` / `detailText` / `describeError` **根本不在执行路径上** ⇒ 把 `e.detail = detail` 改回 `String(detail)`，e2e 照样全绿。这正是 ㊺ 章那处缺陷（409 结构体被压平）能活到真机的原因。修法：**桩保留真实导出**（`Object.assign({}, real, {request: 桩})`），只把取数换成回放。 |
| 153 | **写通道的错误形状与真机同源**：新增 `errorFromResponse` | 原先 `rejectIfNotOk` 自己拼 `new Error('写请求被拒 409')`、`pageUpload` 更是 `String(detail)` —— 与真机**不同形**，而两条函数上方的注释都写着"形状与 `utils/request.js` 一致"。注释承诺一致、实现却是第二份 ⇒ 把「取 detail + 兜底 + 构造错误」抽成 `request.js` 的 `errorFromResponse`，传输层的 `request()` 与校验脚本的写通道**调用同一段代码**：形状一致由机器保证，不靠人记得。 |
| 154 | **e2e 新增 9 条「传输层契约」断言**（⑰ 段后、`auditTemplates()` 前） | ①结构体 detail 不被压平 ②message 仍是一句人话 ③`httpStatus` 挂上 ④纯字符串原样透传 ⑤无 `message` 的结构体返回空串、**不编话** ⑥`errorFromResponse` 保留结构 ⑦空 detail 走「请求失败」兜底 ⑧⭐**驱动本脚本自己的写通道**（假 409 响应 ⇒ `rejectIfNotOk` 产出的错误必须带结构体 detail —— 这条同时证明"写通道用的是真实实现"）⑨`describeError` 不拼出 `[object Object]`。断言数 442 → **451**；`verify_ui_interactions.js` ⑦ 节同步 +3 条（364）。 |
| 155 | **反向验证（本条的核心主张）** | 把缺陷**重新制造出来**（`e.detail = detailText(detail)`）⇒ e2e 从"完全看不见"变成 **`OK 444 · FAIL 7`**，其中 3 条精确指名（`结构体 detail 被压平` / `errorFromResponse 未保留结构` / **`写通道的错误形状与真机不同源`**）；静态门禁 `OK 361 · FAIL 3`。恢复后**字节级相同**、e2e `OK 451 · FAIL 0`、静态门禁 `OK 364 · FAIL 0`。⚠️ 反向验证时 `run_e2e.sh`（`set -euo pipefail`，复刻 CI 配方）会被 `verify_login_flow.js` **先**拦下（它也在测传输层）⇒ 另建本机脚本只跑 e2e；⛔ **不去改 `run_e2e.sh`**（它是 CI 配方的复刻，不能为本地验证而偏离）。 |
| 156 | ⚠️ **结论不变**：本条修的是**发现能力**，不改任何业务结论 | 两项都不改变 §10.1 第 5 步的档位、不改变 BP-03 第 3 条的口径、不新增任何"已验收"的声称。它们的价值是**下一次**同类缺陷会在推 PR 之前红，而不是在真机上被用户发现。本地复现：门禁 **16/16 PASS**（pytest 822＝808 通过/14 跳过/0 失败/0 错误、mypy 109、ruff 146）；CI 的 e2e 本地复现 `OK 451 · FAIL 0`、登录链路 `OK 23 · FAIL 0`。 |

### 7.17 第十八切片（`data-df` 输入通道守护：让"模板写了、handler 不认"这类静默空转在 CI 里红掉；2026-09-17）

触发：§7.16 #123 ① 的实测缺陷（`cap-scope` / `cap-note` 不在 `onCapInput` 的映射表里
⇒ 真机上运力确认永远提交不了），而**三个静态门禁当时全绿**。本条只登记与**门禁面**
有关的部分；缺陷本身与出口见 `docs/entrust/S3-运力确认与有效期切片.md` §8.4 / §9。

| # | 项 | 性质与处置 |
| --- | --- | --- |
| 129 | **`verify_ui_interactions.js` 新增第 ⑧ 节**（`OK 328 → 354`，26 项断言） | 扫 `miniapp/pages/**/*.wxml`，对每一处 `data-df="x"` 找**同一标签**上的 `bindinput="H"`，实例化该页面后**用哨兵值驱动 `H`**，断言哨兵确实落进 `data` 的某处。⚠️ 解析必须取**完整标签范围**（到该标签的 `>`）—— 取固定窗口会漏，本轮实测漏过 3 处（`cap-tonnes` / `cap-vessels` / `task-title`），原因是属性的书写顺序不定。覆盖：21 处 `data-df`、2 个页面。 |
| 130 | ⭐ **判据打在「行为」上，不是「写法」上** | 反例是两个**正确**却会被字面量检查误判的 handler：`onQuoteInput` 用 `'quoteForm.' + ds.df` **拼路径**（任何 df 都能写、无需字面量）、`onTaskInput` 干脆**不读 `df`**（模板里只有它一个框，写死 `taskForm.title`）。若按"df 的字面量出现在 handler 源文本里"来判，这两处会**红**，而它们没有任何问题 —— 那种"写法检查"会逼着后来的人为过闸门去改正确的代码。故判据取运行时行为：**驱动一次、看哨兵落没落进 `data`**。 |
| 131 | **第二条判据：同一 handler 下不同 df 的落点必须互不相同** | 抓的是另一种静默失败 —— handler 不读 `df`（写死一个字段）时两个框互相覆盖，而逐框断言各自"通过"。当前实测：`onDecideInput` 2 个 / `onCloseInput` 2 个 / `onQuoteInput` 4 个 / `onCapInput` 11 个，落点两两不同。 |
| 132 | ⭐ **反向验证（mutation test）：判据有效性的现场证明** | 一道"永远全绿"的闸门与没有闸门**等价**，所以判据只能用「设一个它该抓的错、看它抓不抓得到」来证明。处置：把 §7.16 #123 ① 那处缺陷**重新制造出来**（从 `onCapInput` 映射表摘掉 `cap-scope`）→ 该节**变红**且**精确指名**（`data-df="cap-scope" → onCapInput` / "键入被静默丢弃：handler 没往任何地方写"）→ 逐字写回原文 → 复跑回全绿。⚠️ 恢复的判据必须是**字节级**：`read_text` / `write_text` 会把 CRLF 归一成 LF，在本机 `core.autocrlf=true` 下表现为一个**假的 `M`**（`git diff` 无差异、`git status` 却报改）⇒ 用 `git add` 刷新 stat 缓存即消失，**不要**因此去动那个文件。 |
