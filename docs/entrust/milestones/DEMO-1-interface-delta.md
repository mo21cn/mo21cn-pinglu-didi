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

**仍未做到的两条**（与 `DEMO-1-plan.md` §4 S1 的余量台账一致）：① UI-07 未做真机走查；
② 幂等键只活在页面实例里（"响应丢失 + 杀掉小程序重进"会重复提交）。
