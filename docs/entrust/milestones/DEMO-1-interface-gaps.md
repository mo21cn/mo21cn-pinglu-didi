# DEMO-1 界面可达性与缺口清单（**HO 可界面运行的截止点**）

> 用途：回答两件事 —— ① **HO 拿现有界面能自己走到哪一步**（截止点在哪儿）；
> ② 界面还缺什么、本轮修了什么、剩下什么要进计划。
>
> 口径：**一切以当前代码为准**（`miniapp/pages/entrust/**` 的 `data-act-*` ／ `data-act="…"`
> 锚点 ＋ 表单字段），⛔ **不引用脚本里的历史标注** —— 那两条已过期（见 §5）。
> 基线：`a1791ea`（#187 合并后的 `develop`）｜核实时间：2026-09-21

---

## 1. ⭐ 截止点（结论）

**13 步里，HO 可用界面自助完成 10 步；会停下来的有 3 步，其中 2 步可修、1 步已裁定不修。**

| 会停的地方 | 步 | 界面缺什么 | 性质 | 能不能补 |
| --- | --- | --- | --- | --- |
| **任务前置关系** | 第 4 步 | 「记录任务」表单只有**标题＋类型**，没有 `precondition_task_id` | **产品决定**（未被裁定禁止；脚本 `9718` 只是如实登记"经接口建"） | ⚠️ **需 HO 点头**才能补（后端已支持，`schemas.py:206-207`） |
| **任务必需证据** | 第 4 步 | 同一张表单**没有** `required_evidence` | 同上（脚本 `13200`＝如实登记） | ⚠️ 需 HO 点头（前端已有证据类别取值域 `utils/entrust.js:3745`） |
| **作业金额 `amount`** | 第 6 步 | 会话页可发起作业，但**没有金额入参** | 同上（脚本 `12842`＝如实登记） | ⚠️ 需 HO 点头 |
| **核验登记** | 第 6 步 | 全仓**没有任何「核验」入口** | ⛔ **O-1b 已裁定**：界面**不提供**自服务核验入口 | ⛔ **不补** —— 脚本 `51 ⑤` 正把「核验入口 0 命中」当**断言**用（补了这条判据就失效，且推翻 HO 裁定） |
| **人工接管** | 第 9 步后半 | 界面**无接管入口** | ⛔ **O-1b 已裁定**：接管维持"经接口登记" | ⛔ **不补** —— 脚本 `55 ⑤` 同样断言「接管锚点 0 命中」 |

> ⇒ ⛔ **别看错方向**：**核验与接管这两处不是"缺口"，是 HO 裁定要求保持的形态** ——
> 补界面入口会同时**推翻裁定**并**让两条已有判据失效**。
> 剩下三处（前置／必需证据／作业金额）**才是可补的**，但补它们＝**变更产品决定** ⇒
> **要 HO 明确点头**才动手（详见 `DEMO-1-next-stage-plan.md` §7 的 P-UI1 与阻塞评估）。

## 2. 界面可达性矩阵（13 步 × 章节，**以锚点为准**）

| 步 | 章 | 界面可达 | 主要锚点（可核对） |
| --- | --- | --- | --- |
| 1–3 | `43` | ✅ 全界面 | `data-act-submit-intake`／`data-act-claim`／`data-act-sample-quote`／`data-act-use-attachment`／`data-act-adopt`／`data-act-adopt-confirm`／`data-act-confirm-revision-submit` |
| 4 | `chain4` | ⚠️ **部分**：航段 ✅ ／ 任务与前置 ❌ | 航段 `data-act-leg-open`／`leg-mode`／`leg-submit`；任务 `data-act-task-open`／`task-type`／`task-submit`（⚠️ 表单**无前置字段**） |
| 5 | `45` | ✅ 界面 | `data-act-cap-open`／`cap-kind`／`cap-submit`／`cap-confirm-open`／`cap-confirm-submit`／`cap-recheck`／`cap-recheck-verdict` |
| 6 | `44` | ⚠️ **部分**：组装/发布/客户接受 ✅ ／ 作业金额 ❌ ／ 核验 ❌ | `data-act-quote-open`／`quote-submit`／`offer-submit`／`offer-accept`／`offer-reject`／`offer-download` |
| 7 | `49` | ✅ 界面 | `data-act-contract-derive`／`contract-sources`／`sig-open`／`sig-kind`／`sig-submit` |
| 8 | `50` | ✅ 界面 | `data-act-create-case`／`decide-submit`／`decide-category`／`decide-to`／`confirm-revision-submit` |
| 9 前半 | `chain9` | ✅ 界面 | `data-act-cap-recheck`／`cap-confirm-open`／`quote-submit`／`offer-submit`（替换候选后重新确认/重新发布） |
| 9 后半 | `55` | ❌ **接管经接口** | 无（O-1b 裁定） |
| 10 | `52` | ✅ 界面 | `data-act=create-settlement`／`confirm-charge`／`submit-charge`／`approve-settlement`／`customer-confirm`／`open-payment`／`submit-payment`（finance 页 24 个锚点） |
| 10 后半 | `chain11` | ✅ 界面 | case 页「处置」区 ＋ `data-act-close-submit`／`close-disposition`；任务逐条处置在 `case.wxml` 处置区 |
| 11 | `53` | ✅ 界面 | `data-act-complete-open`／`complete-submit`／`reopen-open`／`reopen-submit` |
| 12–13 | `chain12` | ✅ 纯读 | —（本节只读，零写入正控） |

⚠️ **一处口径待确认**：`DEMO-1-delivery-package.md` §6 写的"**运力确认的前置**：经接口"，
在本轮脚本里**找不到对应标注**（`45` 章的候选登记与确认都是真实点击）。
⇒ 本表按脚本口径记为 **第 5 步界面可达**；若 HO 指的是另一个动作，请点名，我按点回填。

## 3. A 类缺口：**界面做不到**的业务动作（4 处，逐条可核对）

| # | 缺口 | 证据（代码位置） | 性质 |
| --- | --- | --- | --- |
| A1 | 任务**前置关系** | `detail.wxml` 记录任务表单 ＝ `data-df="task-title"` ＋ 类型选择，**无** `precondition_task_id` | 产品决定（未被裁定禁止） |
| A2 | 任务**必需证据** | 同上表单**无** `required_evidence` | 同上 |
| A3 | **作业金额** | 会话页可发起作业，但 `miniapp` 全仓**无** `amount` 输入 | 同上 |
| A4 | **核验登记** | `miniapp/pages/entrust/**/*.wxml` 里**搜不到「核验」二字** | ⛔ **O-1b 裁定的形态**（见 §3.1） |
| A5 | **人工接管** | 无接管锚点 | ⛔ **O-1b 裁定的形态**（见 §3.1） |

### 3.1 ⛔ A4／A5 **不是缺口**：它们是 HO 裁定要求保持的形态

`O-1b` 的裁定是「**界面无自服务入口**」，落点正是这两处，而且**已经被写进判据**：

| 断言 | 位置 | 判据 |
| --- | --- | --- |
| `51 ⑤` 界面上**没有**「登记来源核验」的入口 | `verify_miniapp_devtools.py:13120` 附近 | 核验入口锚点 **0 命中** 才 PASS（与 ④「页面确实处在被拒且看得见清单的状态」同时成立才有信息量） |
| `55 ⑤` 页内**没有**接管入口 | 同上（`55` 章） | 接管锚点命中 **= 0** |

⇒ 补这两个入口会**同时**：① 推翻 HO 裁定；② 让两条已取证判据失效。
⛔ 因此本轮**不动**，且演示口径继续写"**经接口**"，⛔ **不得**写成"界面上可以核验／可以接管"。
（本轮**没有**改这两个页面的这两处 —— 见 §6 的自查。）

## 4. B 类缺口：**有交互但没有锚点**（走查/自动化点不到）

> 判据：该元素有 `bindtap`，但自身与包裹层都没有 `data-act`（脚本按精确属性选择器点按，
> 所以**没锚点 = 点不到**）。⛔ 这不是"界面坏了"，而是"演示与取证有盲区"。

### 4.1 本轮已补 **24 处**（105 → 129）

| 页 | 补的锚点 | 为什么值得补 |
| --- | --- | --- |
| `session` | `send`（发送并解析）／`open-artifact`／`adopt-cancel`／`transcribe-cancel`／`reextract-open`／`reextract-cancel`／`reextract-confirm` | 「发送」是真实会话的**主交互**，此前点不到；其余是确认条的两个键（只锚了提交键） |
| `detail` | `task-open`（展开记录任务）／`task-type`（选任务类型） | 第 4 步表单的**入口与类型**此前无锚点 |
| `case` | `decide-category`／`decide-to`／`close-disposition` | 第 8／10 步的**必选字段**此前无锚点（只能靠默认值） |
| `case-create` | `link-add`／`link-remove` | 建案件时的关联目标增删 |
| `workbench` | `queue`（两条队列）／`filter`／`case-scope`／`case-kind`／`pick-org`／`open-assignment`／`open-case` | HO 演示的**第一屏**：切队列、筛选、切组织、打开单子 |
| `assignments` | `open-mine` | 「我的委托」列表此前**整页 0 锚点** |
| `intake` | `pick-org` | 受理前的组织选择 |

⭐ 命名全部为**新名字**（脚本与界面此前都没有），因此**不会改变任何既有选择器的命中数**；
且 `verify_entrust_ui.js` 的命名约定（只约束 `*-cancel` 与 `withdraw|revoke`）**未被触碰**。

### 4.2 未补 **8 处**（都在辅助路径，已登记）

| 页 | 交互 | 为何暂不补 |
| --- | --- | --- |
| `detail` | `onOpenRef`（打开引用对象） | 跨视图跳转，走查用直链即可 |
| `case` | `onAddLinkTarget`／`onRemoveLink` | 案件页关联编辑，演示主链不经过 |
| `case-create` | `onPick`×3／`onClearDue` | 建案件表单的次要字段 |
| 各页 | `onBack`／`onRelogin`／`onRetry` | **导航/重试类**，⛔ 不建议为它加锚点（走查按路由直连；`finance` 已有的 `data-act=back/relogin` 是历史形态） |

## 5. 修订：**两条已过期的界面描述**

脚本里有两处"界面无入口"是**当时切片的事实**，界面上后来已经补齐 —— 引用它们会误判界面能力：

| 脚本位置 | 原文 | 现状（本轮核实） |
| --- | --- | --- |
| `verify_miniapp_devtools.py:12472` | "任务处置与案件关闭在本切片**没有界面入口**" | **已补齐**：任务处置在 `case.wxml` 「处置」区；关案件 `data-act-close-submit` ＋ `close-disposition` |
| `verify_miniapp_devtools.py:12785` | "没有一键关闭；⛔ 界面在本切片没有这个入口" | 同上（**仍然没有"一键"关闭** —— 必须给处置与证据，这条设计不变，✅ 是对的） |

⇒ 处置：**不改历史脚本**（它记录的是当时事实，改了会让已取证读数对不上号）；
本文件作为**现行口径**，`DEMO-1-delivery-package.md` §6 相应行已按现状重写。

## 6. 改动的验证与证据要求（⛔ 别漏这条）

1. 本轮 24 处锚点是**纯属性新增**：不加事件、不改数据流、不改渲染条件 ⇒ 业务行为不变。
2. 验证：本机门禁 **16/16**（含 8 个前端静态契约脚本）＋ `ruff`／pytest 全量。
3. ⛔ **但交付物变了**：`0baad07`／`a1791ea` 上的设备读数**不是**这 24 处锚点所在提交的读数。
   ⇒ 演示或终验前须在**新提交**上重跑一次主链（`runbook` §8.9 的配方），
   否则只能说"界面可观测性已补，设备证据待在新提交上重取"，⛔ **不得写"已重跑"**。
4. ⛔ 补锚点 ≠ 补能力：A 类 4 处缺口**不因本轮改动而关闭**。
