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
| 27 | BP-02 要求"attachment selection"（§10.1 第 2 步"上传样报价单"） | ⚠️ **本轮未做**。`POST /attachments` **已在**（multipart：`file` + `entrustment_id`（或 `assignment_id`）二选一 + 可选 `source_event_at`，权限 `entrust:quote:create`，幂等键必填），但**前端没有任何上传入口**（`chooseMessageFile` / `uploadFile` 全仓 0 处）。列为下一片 —— 它是演示第 2 步的**字面要求** |

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
| 38 | ⚠️ **夹具与种子之间存在口径偏离，本片不自行裁定** | 合同 §3.1 写「初期 **800 吨** → 变更后 **950 吨**」，而演示种子 `ASSIGNMENT_MAIN` 是 **1200 吨**，且当前变更样本是**收货港变化**而非数量变化。两侧**均保持原状**，登记为待裁决（`DEMO-1-fixture-manifest.md` §4 的 F-Q1/F-Q2）。⇒ 在此之前**不得**声称"§3.1 夹具已完全对齐" |

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
