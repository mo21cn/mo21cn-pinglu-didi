# S4-b 结案命令切片（`complete` ＋ 合同 §6.4 五维度前置）

> 口径来源：`DEMO-1-contract-v1.0.md` **§6.4**（Closure: required implementation semantics）
> ＋ `S4-委托结案状态机口径设计.md` §5.3.1 / §5.3.2 / §5.4 / §5.6
> ＋ `S7-第10-12步切片设计.md` §3 表第 4 行（S4-b 的交付定义）。
> 实现：`backend/app/modules/entrust/closure.py`（领域）＋ `closure_api.py`（端点）
> ＋ `locks.py`（行锁的方言守卫）。

---

## 1. 交付清单

| # | 交付 | 落点 |
| --- | --- | --- |
| 1 | `complete` 命令（`claimed → completed`） | `closure.complete_assignment` |
| 2 | 五维度前置 ＋ **逐条报缺**（409 的 `detail` 是**对象**，带 `missing[]`） | `closure._evaluate` |
| 3 | 只读齐备度（读写共用同一份评估） | `closure.closure_readiness` |
| 4 | 端点 ＋ 幂等作用域 ＋ 结构化异常映射 | `closure_api.py` / `_http.map_closure_error` |
| 5 | 新权限码 `entrust:assignment:complete`（经理人） | `access.py` |
| 6 | 权限矩阵条目（+1 ⇒ 103） | `scope_matrix.py` |
| 7 | 代码取值域第五个值 ＋ **前端三张状态表**（同一提交） | `assignments.STATUS_COMPLETED` / `miniapp/utils/entrust.js` |
| 8 | 投影新增 `completed_at`（运营维度；与财务维度分开） | `assignments._row_to_assignment` / `schemas.AssignmentOut` |
| 9 | 并发的两把锁 ＋ 条件 UPDATE（含登记案件一侧） | `locks.py` / `exceptions.raise_case` |
| 10 | 用例：20 条真机无关的业务用例 ＋ 2 条 MySQL 并发锚点 | `tests/test_entrust_closure.py` / `test_mysql_integration.py` |

---

## 2. 两处**可争议口径**（先写明依据，便于 HO 复核）

### 2.1 `cases_not_closed`：为什么"未关闭的非阻断案件"也报成缺项

§5.4 第 3 条要求「无未解决的**阻塞性**案件；**非关键残留须有显式 disposition**」。
合同 §6.4 只写了 `No unresolved blocking case`。两者的差额正是"非关键残留"。

**读法**：仓库里 disposition 的载体是 `ent_exception.closure_disposition`，而它的列注释
明写 `关闭时必填`（`migrations/ent_exception.py`）⇒ 在当前结构下，"这条残留已经有显式
disposition"**唯一可核对的形态**就是"这条案件已经关闭"（关闭命令会强制 disposition
＋ 证据，见 `exceptions.assert_closure_requirements`）。

⇒ 因此本切片把"未关闭的案件"整体报成缺项，要求调用方**去关闭它**，而不是把它留在开着。
**副作用**：这比合同 §6.4 的"只拦阻断案件"更严一格。取这个读法的另一个理由是
**两处同结论**：`settlement.derive_financial_status` 的 `open_cases` 本来就把未关闭案件
算作"未结"；若结案放行而财务派生说未结清，界面与命令会给出互相矛盾的答案 ——
那比"两处都太严"糟得多。

⚠️ 若 HO 认为应当放宽到"只拦阻断类"，改法是 `_case_dimension_missing` 里去掉最后一段
（`cases_not_closed`），同时必须一并处理 `open_cases` 的去重口径 —— 请勿只删一半。

### 2.2 `ready=True` **不代表** `complete` 会成功

`closure_readiness` 的 `ready` 只回答"五个维度是否成立"；`complete` 还要求委托处于
`claimed`（已结案 ⇒ 409，未受理 ⇒ 409）。把状态前置塞进 `ready` 会让一个字段同时回答
两个问题（"可以结吗"与"现在能结吗"），调用方就分不清该去补事实还是该先重开。
两条判据的差别由 `test_readiness_and_command_agree` 钉住：**结案之后 `ready` 仍为 True**。

---

## 3. 三个**"不算缺"**的边界（都容易写反）

| 情形 | 判据 | 为什么 |
| --- | --- | --- |
| 已取消的任务仍缺证据 | **不算缺** | 任务已被合法处置，它的证据要求随任务消失。否则"拿不到证据就取消"反而更结不了案，会把人逼回改库造证据 |
| 没有任何交接任务 | **不算缺** | 无从判断 ⇏ 不满足（与 S7-2 的 `satisfied=None` 同源）。⛔ 不把"没有"读成"没做到" |
| 财务派生 `not_started` 且 `blockers=[]` | **算缺**（`financial_not_started`） | ⭐ 这是"只按 blocker 归类"会漏掉的一格：什么都没做 ⇏ 已结清。§5.3.2 的正向判据是 `settled` |

---

## 4. 并发与原子性（合同 §6.4 第二、三段）

合同要求「evaluate all applicable predicates **atomically** with its state transition」
以及「Close-versus-new-blocking-case concurrency must be safe on MySQL」。

**落法**（三件套，缺一不可）：

1. **`expected_revision` 必填** ＋ 条件 UPDATE（`WHERE id AND status AND revision`）⇒
   两个结案者恰好一个成功。⛔ 只判状态是"碰巧对"：中途任何一次编辑都会让版本先变。
2. **`ent_assignment` 行锁**（`locks.lock_assignment_row`，MySQL 生效）⇒ 拿锁后**重读**
   状态与版本（锁前读到的是旧快照），两个结案者由此串行。
3. **登记案件一侧取同一把锁并复核状态**（`exceptions.raise_case`）⇒ 两条竞态路径各有
   明确的拒绝者：案件先提交则结案 409（`blocking_cases_open`）；结案先提交则登记被拒。

**证据载体**：`tests/test_mysql_integration.py` 的
`test_complete_race_exactly_one_winner` 与 `test_close_versus_new_blocking_case_is_serialized`
（`pytest -m mysql`，真实 MySQL 8.0）。⚠️ **SQLite 上的绿灯不是这条的证据**：
它单写者、且 `FOR UPDATE` 是空操作 —— 这正对应合同那句
`An exception query existing in the code is not this proof`。

---

## 5. ⛔ 本切片**刻意不做**的事（不是待办）

* **界面入口**：⏩ **已在同一 PR 的界面侧补齐** —— 委托详情页的**结案卡**
  （五格清单先于按钮 ＋ 页内确认条）＋ 走查第 **53** 章。原本的顺序是"先有命令、后有入口"
  （与 S4-a 一致），一起做的理由是：第 12 步是**唯一**在业务口径上完全空白的格子，
  而"命令有了、界面零调用"正是那份进度分析点名的"出口接得太晚"。
* **只读端点**：⏩ **已补** `GET /assignments/{id}/closure-readiness`。加它的理由不是
  "顺手"：结案**不可逆**，而 `complete` 的 409 只在**点下去之后**才说缺什么 ⇒
  界面必须先能拿到清单。判据与命令**同一把锁**（看得见缺项的人就是能结案的人）。
* **S4-c 重开**：`completed → claimed` 及其理由/授权/留痕仍未开放（设计 §5.5 Q2）。
  本切片的"已结案 ⇒ 409"是**有意**的终点，不是缺一块。
* **终止/取消的独立处置规则**（S4-e）：Q1 裁定不在 DEMO-1，**不要**读成待办。
* **授信路径**（`completed` ＋ `open`）：§5.3.1 明令本期不开放。

---

## 6. 验证分档（截至本切片）

| 档位 | 内容 |
| --- | --- |
| **已实现** | 命令 ＋ 端点 ＋ **只读前置端点**（`closure-readiness`）＋ 权限 ＋ 矩阵（**104**）＋ 取值域 ＋ 前端三张表 ＋ 投影 `completed_at` ＋ **详情页结案卡**（五格清单 / 页内确认条）＋ 走查第 **53** 章 |
| **已自动化验证** | 门禁 16 项；`pytest` 含 **24** 条结案用例（读端点四档权限 / 与派生**逐字段**一致 / `ready` 与命令两条判据的差别 / 五维度键恒在 / 空缺项 200 而非 4xx）；端到端（真后端）**含注入验证** —— 往模板注入不存在字段 ⇒ `rc=1` 且精确点名 |
| **CI 覆盖** | 前端静态契约（状态域逐格比对 ＋ 走查锚点 **180** 处）；`pytest -m mysql` 跑两条并发锚点 |
| **仍待验收** | ① **「齐备 ⇒ 结案成功」的设备侧证据**：缺一张五维齐备的委托夹具（第 53 章 ⑧ 如实记 `NOT_RUN`）；② MySQL 并发锚点在本机的**执行结果**（本机无 MySQL，只由 CI 提供）；③ §2.1 的口径解释待 HO 认可 |

**设备侧读数（第 53 章，2026-09-18）**：`PASS=11 / FAIL=0 / NOT_RUN=1 / LIMITATION=0`
（截图 `miniapp-device-artifacts/walk-20260918-233709/`，含 `53-结案五维清单.jpg`）。
入口、五格清单、**两处同结论**、页内确认条、**被拦且缺项逐条可读**、被拦后状态未变
—— 都有设备证据（读数之外还有一张可被人眼复核的图）。
⚠️ 首跑被 IDE 残留实例（21 个）挡住，清理后复跑成功（与 O-8 同一环境故障）。
