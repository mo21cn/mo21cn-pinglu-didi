# S4-a：委托结案结构（**只落结构与迁移**）

| 字段 | 内容 |
| --- | --- |
| 状态 | **结构已落地、迁移已跑、闸门已建**；⛔ **结案能力未开放**（无命令、无端点、无界面）；⚠️ **界面与设备侧走查 `NOT_RUN`** |
| 日期 | 2026-09-17 |
| 口径来源 | `docs/entrust/S4-委托结案状态机口径设计.md`（HO 0917 Q1–Q4 裁定 + 0917-2 补充裁定） |
| 落点 | `backend/migrations/ent_assignment_completion.py`（新）、`backend/tests/test_entrust_s4a_schema_only.py`（新） |
| 相关 | `DEMO-1-interface-delta.md` §7.14；`DEMO-1-runbook.md` §7 第 3 行「已完成历史委托」夹具 |

## 1. 本切片交付了什么

| # | 交付 | 说明 |
| --- | --- | --- |
| 1 | `ent_assignment` 两个新列 | `completed_at DATETIME NULL`、`financial_status VARCHAR(16) NOT NULL DEFAULT 'not_started'` |
| 2 | `status` 列注释同步为五个取值 | `draft/submitted/claimed/completed/cancelled`（**列的事实**，不等于代码能产出第五个） |
| 3 | 索引 `idx_ent_assignment_status_completed (status, completed_at)` | 为「历史委托」列表准备；**本切片不建该列表** |
| 4 | 8 条闸门用例 | 把「边界」写成可执行断言（见 §4） |

迁移三条，**只做 DDL**：无一条 `UPDATE` / `INSERT` / `DELETE`（由用例强制，见 §4 第 3 条）。

## 2. 刻意**未做**的事（HO 指定的边界，不是待办）

| # | 未做 | 依据 | 若做了会怎样 |
| --- | --- | --- | --- |
| 1 | **不加 `complete` 命令 / 端点 / 五类前置** | 口径设计 §5.6 末条、0917-2 第 4 条 | 会造出一条**没有客户确认、没有幂等、没有并发保护**的完成路径 |
| 2 | **不生成「已完成历史委托」夹具** | HO 0917：夹具必须走**真实业务命令** | 手改状态产出的"已完成委托"不证明结案链路可用，会让 runbook §7 那一行变成**假绿** |
| 3 | **不给前端暴露 `financial_status`** | 口径设计 §5.3.2 末条 | 派生未接通时，列默认值 `not_started` 会被读成"这些委托财务未开始"这一**结论** |
| 4 | **不动 `_CANCELLABLE`** | Q1 裁定 | 迁移扩大可取消集会引入未经裁定的终止路径 |

### 2.1 一个需要 HO 认可的口径解释：**代码取值域不动**

口径设计 §7 把 S4-a 写成「状态与迁移：`completed` / `completed_at` / `financial_status`」。
本切片把其中的「状态」**落在 schema 与注释上**（列注释五个取值），
**没有**在 `assignments.py` 里加 `STATUS_COMPLETED`。理由是一条**连锁反应**，不是省事：

`scripts/verify_entrust_ui.js` 第 1 组断言强制前端状态镜像与后端 `STATUS_*` **逐格一致**
（"后端加状态，这里就红"）。而后端的 `STATUS_*` 一经新增，前端就必须同步
`STATUS_META` / `STATUS_ORDER` / `STATUS_HINT` 三张表 —— 其中 `STATUS_ORDER`
派生**工作台筛选条**（`workbench.js` 由它 `map` 出筛选片）。

⇒ 加常量的**必然结果**是界面上出现一个「已完成」筛选片，而此刻**没有任何数据能处于该状态**。
那是在暗示一条不存在的路径 —— 与 §5.6 末条「列存在但没有命令时，正确表现是这个能力还没有入口」
直接冲突。

**因此本切片的取舍是**：代码取值域与前端镜像留到 **S4-b**，与 `complete` 命令**同一个提交**里一起改。
⚠️ 这是一条**可争议的解释**，已登记待 HO 认可（改起来是一个常量 + 前端三张表，无迁移代价）。

## 3. 迁移清单（逐条）

| id | 做什么 | 方言差异 |
| --- | --- | --- |
| 1 | 追加 `completed_at` / `financial_status` 两列 | 双方言都执行（SQLite 两次 `ADD COLUMN`） |
| 2 | `status` 列注释同步为五个取值 | ⚠️ **MySQL 专有**：`MODIFY COLUMN ... COMMENT`；SQLite 无列注释概念 ⇒ 该条在 SQLite 上是**显式空操作**（`SELECT 1`），**不重建表、不搬数据** |
| 3 | 建 `(status, completed_at)` 索引 | MySQL 不支持 `CREATE INDEX IF NOT EXISTS` ⇒ 幂等靠执行器的 `(module, migration_id)` 历史表（与 `ent_artifact_assignment.py` 同一写法） |

⚠️ **模块名必须以 `ent_assignment_` 开头**：执行器按**模块名字典序**应用、没有声明式依赖
（`migrate.py:load_entries`）。本模块对 `ent_assignment` 表做 `ALTER`，名字排错会让迁移在**空库上**
先于建表执行 —— 而**既有库因为表已在而全绿**（这类"只在全新库暴露"的缺陷本切片之前踩过一次，
见 interface-delta #94 ①）。用例第 4 组把顺序变成断言。

SQLite 与 MySQL 的表结构在本机上无法同时验证：**本机没有 MySQL**（无 `mysqld`、无 `docker`、3306 关闭），
MySQL 分支的 SQL 只能由 CI 的「数据库迁移（MySQL 8.0 真实执行）」job 真跑。

## 4. 闸门与判据（`test_entrust_s4a_schema_only.py`，8 条）

| # | 用例 | 断言什么 | 为什么需要 |
| --- | --- | --- | --- |
| 1 | `test_completion_columns_land_with_the_documented_defaults` | 两列存在；`completed_at` 可空、`financial_status` NOT NULL | 结构确实落地 |
| 2 | `test_existing_rows_keep_their_status_and_get_no_completion_time` | 历史 `claimed` 行保持 `claimed`、`completed_at` 保持 NULL | **不推测历史**（§5.6） |
| 3 | `test_migration_is_append_only_and_writes_no_data` | 新模块只做 DDL（无 `UPDATE/INSERT/DELETE/MERGE/REPLACE/TRUNCATE`） | 防"用迁移把状态改成 completed"造出假数据 |
| 4 | `test_migration_module_applies_after_the_module_that_creates_the_table` | 字典序排在建表模块之后 | 防"只在全新库暴露"的执行顺序缺陷 |
| 5 | `test_status_domain_and_cancellable_set_are_unchanged` | 代码取值域仍是四个；`ACTIVE_STATUSES` / `_CANCELLABLE` 原样 | 把 §2.1 的取舍变成断言 |
| 6 | `test_no_closure_or_completion_endpoint_exists_yet` | **委托层**（`/assignments/`）无 `complete/close/reopen` 端点 | ⚠️ 判据必须限定在委托层：`/tasks/{id}/complete`、`/exceptions/{id}/close` 是**任务层与案件层**的既有能力，不得互相替代（§5.7） |
| 7 | `test_financial_status_and_completed_at_are_not_projected` | `get_assignment()` 返回的字典里**没有**这两个键 | 防"新列自动漏进响应"（该模块用显式列清单，本断言把它钉住） |
| 8 | `test_rerun_of_migrations_is_a_no_op` | 复跑 `apply_pending` 返回空 | 幂等 |

⚠️ 第 6 条一开始写宽了（对所有委托支线路径查 `complete/close/reopen`）—— **当场红**，
真因是任务层与案件层本就有这些端点。这说明该断言的价值恰恰在于**区分层级**：
写宽了会永远红，写歪了会放过"委托层没做"这件事。收窄后通过。

## 5. 证据（本机实测）

| 项 | 结果 |
| --- | --- |
| 新用例 | `test_entrust_s4a_schema_only.py` **8 passed** |
| 迁移闸门（文本层） | `test_migration_sql_literals.py` 通过（新迁移的列注释都在**单行**内，无相邻字面量） |
| 全量门禁 | 13 项（见 §5.1 实测值） |
| MySQL 真执行 | ⛔ 本机**做不到**（无 MySQL） ⇒ 由 CI job 验证 |

### 5.1 全量门禁实测

| 项 | 结果 |
| --- | --- |
| 本机门禁 `scripts/verify_local_gates.py` | **13 项 PASS 13 / FAIL 0** |
| pytest（junitxml 判据） | **815 项 = 通过 801 / 跳过 14 / 失败 0 / 错误 0**。⚠️ 计数口径：本切片新增 **8** 条；`#140` 分支实测的 806 **不含 #138**，而 #138 恰好新增 **1** 条（`test_entrustment_release_list_picks_projection_by_identity`）⇒ **806 + 1（#138）+ 8（本切片）= 815**，逐笔对账。 |
| mypy（`app migrations migrate.py`，strict） | **109 source files**，no issues |
| ruff | `check` rc=0；`format --check` **144 files** |

⚠️ **实测到一次 mypy 的「假红」**：门禁首跑报 `FAIL | 后端 mypy | rc=2` 并附
`please use --show-traceback to print a traceback when reporting a bug` —— 这是 mypy 的
**内部错误**（rc=2 在 mypy 里表示自身崩溃），**不是类型错误**。单独复跑
`mypy app migrations migrate.py` 得 `rc=0 / 109 source files`，重跑整套门禁得 13/13。
留档原因：本机门禁把"进程 rc"当判据时，这类崩溃会被读成代码问题，
**下次见到 rc=2 应当先单独复跑 mypy 再定性**，不要直接去改代码。

（本机不代跑 CI 的「前端端到端（真后端载荷驱动）」job —— 它**不在** 13 项内，
动用页面取数或模板字段契约时必须本地复现。）

## 6. 对下游的影响

| 下游 | 影响 |
| --- | --- |
| **S4-b**（`complete` 命令） | 列已在 ⇒ 只需加命令、五类前置、逐条报缺、客户确认、幂等、并发保护；同时**一并**改代码取值域与前端三张镜像表（§2.1） |
| `financial_status` 的派生 | 接通前**不展示**；接通时须按 §5.3.2 的**四条成因**判定，不得只按余额 |
| 「已完成历史委托」夹具 | 仍走 S4-b 的真实命令；本切片**不**提供任何造该夹具的捷径 |
| 前端状态镜像 | 本切片**不同步**（否则会多出一个筛不出来的筛选片）；S4-b 同步并触发 `verify_entrust_ui.js` 的逐格比对 |
| 合同 §10.1 第 8–12 步 | 依赖结案能力；Q4 已裁"必须进 S4" ⇒ 不再被"是否做"阻塞，改由实现排期决定 |

## 7. 本文件**不**声称什么

1. **不声称任何 AC / D1 通过**；
2. **不声称 BP-03 或 PRD 的结案规则已实现** —— 本切片只有结构；
3. **不声称合同 §10.1 任何一步 PASS**；
4. `financial_status` 的默认值**不是**关于任何一条委托财务状态的结论；
5. 界面与设备侧走查 **`NOT_RUN`**（本切片没有可点的界面）。
