# DEMO-1 Runbook —— 演示环境、夹具与复位

> **本文档的状态：S1 起步版（2026-09-16），不是 S5 收口版。**
>
> 合同 §8.1 要求 `Start BP-05 environment and fixtures during S0/S1. Do not defer
> integration to the final slice.` ⇒ 本文档在 S1 落地，覆盖「账户 / 组织 / 启动 /
> > 迁移 / 种子」五件的**现状**，并如实写明复位与夹具的完成范围。
>
> **更新（2026-09-16，DR-0018）**：§6 已产出**最小隔离复位**（范围＝探针能反复跑所需），
> 且原文那两处**过度主张已纠正**（见 §6 开头的口径说明）；§7 三种夹具状态**仍然缺失**。
>
> ⚠️ **不声称什么**：本文档**不是**「BP-05 完成」，也**不是** D1-17 完成 ——
> D1-17 还要求「fresh-run 与 seeded-checkpoint 的**证据可区分**」，那归 S5-1。
> 「有了一本 runbook」与「环境的复位与夹具已就绪」是两件不同的事，
> 后者才是合同要的东西。

---

## 1. 账户（登录码 → 身份）

演示账号**不是**预先写死的用户表，而是 `WECHAT_MOCK` 下由登录码**当场注册**的账号
（`backend/app/modules/auth/`）。开发期两种进入方式：

| 方式 | 怎么用 | 优先级 |
| --- | --- | --- |
| 身份卡 | 登录页点「货主 / 船东」，按 `utils/auth.js` 的 `DEV_ROLE_CODE` 映射到演示码 | 低 |
| Storage | 面板里设 `dev_login_code = <登录码>`，再 `reLaunch` 首页 | **最高**（退出登录时被清除） |

⚠️ **身份必须落在有数据的账号上**：换一个没跑过种子的登录码，后端会当场注册出
**空账号** —— 页面不报错、列表全空，极易被误判成"功能坏了"。

| 登录码 | 身份 / 组织 | 由哪份种子铺出 | 用途 |
| --- | --- | --- | --- |
| `seed-shipper` | 货主 | `seed_demo.py` | 自主发货 / 订单 / 港口域；委托支线里是"有工作台授权"的货主 |
| `seed-owner` | 船东 | `seed_demo.py` | 船东域；**同时**是 `seed_entrust_demo.py` 的经理 |
| `seed-port` | 港口方 | `seed_demo.py` | 港口运营台 |
| `seed-mgr-multi` | 甲组织 manager ＋ 乙组织 member | `seed_entrust_orgpicker.py` | 组织选择器 `ambiguous` 分支；**唯一**有两个经理的组织（甲）里的抢单者 |
| `seed-mgr-single` | 仅甲组织 manager | `seed_entrust_orgpicker.py` | 组织选择器 `only` 分支；甲组织受理路径的页面身份 |
| `seed-mgr-only-b` | 仅乙组织 manager | `seed_entrust_orgpicker.py` | 出口判据 ④「unrelated organization B」的**唯一**可行身份 |
| `seed-mgr-none` | 无任何组织身份 | `seed_entrust_orgpicker.py` | 组织选择器 `none` 分支 |
| `seed-shipper-orgpicker` | 货主，对甲 / 乙**各一条**生效授权 | `seed_entrust_orgpicker.py` | 「切换组织后队列真的变了」可断言的前提；S1 载体单的提交方 |

⚠️ **`seed-mgr-multi` 不能用来验"乙组织看不到甲的委托"**：`GET /assignments/{id}` 的
可见性判据是「该委托的组织 ∈ 调用者的任一组织」（`ctx.org_ids`，**不看当前选中哪个组织**），
而 multi 同时是甲和乙的成员 ⇒ 它**无论如何都看得到**甲的委托，拿它验会得到**假绿**。
跨组织负例只能用 `seed-mgr-only-b`。

---

## 2. 组织与授权

三张表构成权限叠加层（D-4 / DR-0012「归属 ≠ 权限边界」）：

| 表 | 含义 |
| --- | --- |
| `ent_organization` | 组织本体（`name` / `status`） |
| `ent_org_member` | 「我在这个组织里是谁」（`member_role`：manager / member） |
| `ent_entrustment` | 「货主把哪些能力授给这个组织」（`permissions` JSON + 生效窗口） |

**最终权限 = 角色权限 ∪ 生效中的委托授权**（`access.py:ORG_ROLE_PERMISSIONS`）。
这条并集有一个直接后果，走查与测试都踩过：**在某组织里撤掉角色，撤不掉由授权带来的权限**。
例如 `entrust:assignment:claim` 在甲组织的授权行里**本来就有** ⇒ 在甲撤 manager 角色
仍能受理（表现为该验的 403 变成了 200）；乙组织的授权行里**没有** claim ⇒ 撤角色＝真撤权。
⇒ 需要"撤权"的负例必须选**乙**，需要"抢单"的负例必须选**甲**（两章的选址恰好相反）。

演示组织（名称一律带「演示」前缀，便于辨认与清理）：

| 组织名 | 由哪份种子铺出 | 授权行内容 |
| --- | --- | --- |
| `演示经营主体·工作台` | `seed_entrust_demo.py` | 完整 6 项（含 `entrust:task:dispatch`） |
| `演示经营主体·甲` | `seed_entrust_orgpicker.py` | `entrust:view` + `entrust:assignment:claim` |
| `演示经营主体·乙` | `seed_entrust_orgpicker.py` | 仅 `entrust:view` |

⚠️ `ent_organization` / `ent_org_member` / `ent_entrustment` **没有 HTTP 接口** ——
它们是权限叠加层的数据，只能由组织管理动作写入。所以种子**直接落库**（这部分没有
可走的服务层）；而委托单仍走**真实服务层**（`assignments.create_assignment` +
`submit_assignment`），种子产生的委托与线上走同一套状态机与校验。

---

## 3. 启动

### 3.1 一次性初始化

```bash
bash scripts/setup-dev.sh
```

四步：`npm install`（husky + commitlint）→ 建 `backend/.venv` → `pip install -r
requirements.txt` → 由 `.env.example` 生成 `.env`（若不存在）。

### 3.2 后端

```bash
cd backend
uvicorn app.main:app --reload
```

**端口 8000 是约定，不是随意值**：前端 `miniapp/utils/request.js` 的 `BASE`、
两份种子脚本、走查脚本的 `HEALTHZ` / `API_BASE` 都写死 8000 —— 换端口要**一起**改。

### 3.3 小程序

微信开发者工具打开 `miniapp/`。真机走查见 §8。

### 3.4 环境变量

| 变量 | 作用 | 演示环境取值 |
| --- | --- | --- |
| `ENTRUST_ENABLED` | 委托支线总开关；关闭时整组 `/api/v1/entrust/*` 返回 **404** | `true` |
| `APP_ENV` | 只认 `development` / `test` / `production` | `development` |
| `DATABASE_URL` | 数据库；默认 SQLite 文件，交由 `migrate.py` 建表 | 见 §6 |
| `WECHAT_MOCK` | 开发期以登录码直接注册 / 登录，不走微信 | 开 |

⚠️ `ENTRUST_ENABLED=false` 时**必须**仍然 404 而不是降级到旧行为（`router.py:
require_entrust_enabled`）—— 这是 §2.2 的要求，也是走查里"功能未开放"那一态的判据。

---

## 4. 迁移

自研执行器 `backend/migrate.py`（**没有** alembic，也**没有**全局编号）：

```bash
cd backend
python migrate.py            # 应用所有 pending 迁移
```

* 迁移文件在 `backend/migrations/*.py`，每个文件自述 `description` + `statements` + `checks`。
* **幂等**：重复执行会跳过已应用的（靠 `ent_migration` 之类的记账表），
  所以"再跑一次"是安全的，不需要先清库。
* ⚠️ **已发布的迁移不改**，`id` 只增不改 —— 改一个已发布的迁移会让别人库里的
  状态与本机不一致，而执行器不会回头修正。

---

## 5. 种子

**顺序是硬约束**（后一份依赖前一份造出的账号 / 组织）：

```bash
cd backend
python scripts/seed_demo.py                # ① 自主发货 / 订单 / 港口域 + seed-shipper/owner/port
python scripts/seed_entrust_demo.py        # ② 委托支线演示（工作台组织 + 委托 #1 #2 + 任务）
python scripts/seed_entrust_orgpicker.py   # ③ 甲 / 乙组织 + 五个 manager 身份 + 两张样本单
python scripts/seed_contract_cases.py      # ④ 仅 ⑦b / ⑨b 章需要（智能合同规则命中）
```

| 种子 | 铺什么 | 幂等语义 |
| --- | --- | --- |
| `seed_demo.py` | 四态订单（已完成 / 待支付 / 已退款 / 面议）+ 演示泊位 + 4 条预约 | 连跑两次全「复用」、零重复数据 |
| `seed_entrust_demo.py` | `演示经营主体·工作台` + 货主授权 + 委托 `#1`（`claimed`）/ `#2`（`submitted`）+ 任务 | 同上 |
| `seed_entrust_orgpicker.py` | 甲 / 乙组织 + `seed-mgr-*` 四个身份 + `seed-shipper-orgpicker` + 甲/乙各一张标题不同的已提交单 | 同上 |
| `seed_contract_cases.py` | 智能合同仿真案例（货主 / 船东各 3 张专项订单） | 同上，但**需先跑 ①** |

⚠️ **"幂等"≠"复位"**（这是 §6 的核心）：

* 幂等保证的是"再跑一次不会重复造数据"；
* 它**不**保证"环境回到干净起点" —— 已存在的单被走查改过状态（例如某张 `submitted`
  被受理成 `claimed`）之后，再跑种子**不会**把它改回去（`_member()` / `_assignment()`
  这类实现是「**有则跳过**」，不是「有则校正」）。
* ⇒ 精确地说：**受污染的样本不会被幂等种子修复，只会被静默复用**；靠"重跑种子"
  复位会得到一份"看起来铺过了、实际状态是上一轮留下的"环境。

---

## 6. 隔离复位流程 —— **本轮完成「最小隔离复位」；D1-17 未完成**

> ⚠️ **本节的口径已按 DR-0018 纠正（2026-09-16）**：原文把「新建临时库」与「复位」写成
> **绝对对立**，并据此断言"换库 ≠ 复位、复位必须回到同一套环境" —— **约束过强**。
> 正确的**验收对象**是「**应用回到一致、可复制的起点**」：
> **重建专属库**或**恢复快照**都可以，**不必保留同一个数据库文件**。
> 要处理的是**一致性**，不是**文件的同一性**。

| 项 | 状态 |
| --- | --- |
| 环境专属的复位脚本 | **已产出并实测（自证见 §6.3） `backend/scripts/reset_demo_env.py`（最小版）**，范围＝"探针能反复跑"所需 |
| 是否等于 D1-17 完成 | **不是** —— D1-17 的另一半「fresh-run 与 seeded-checkpoint 的**证据可区分**」归 S5-1 |
| 本机走查在用的一体化脚本 | `_walk4c_env`（每轮新建临时库）—— **保留**，它仍是走查的隔离手段 |

### 6.1 复位的四条一致性（缺一条就不算复位）

| 面 | 复位要保证什么 |
| --- | --- |
| **数据库** | 表结构 ＋ 迁移记账 ＋ 关键表回到基线 |
| **附件** | 附件存储与业务引用一致 |
| **后台作业** | 无遗留 `running` / 租约未释放的作业行 |
| **客户端身份缓存** | 小程序 storage（`dev_login_code` / 在途草稿 / `current_role`） |

### 6.2 实际完成范围（本轮，最小版）

见 §6.3 的实测记录 —— ⚠️ **本轮只保证"探针能反复跑"**，不声称环境属性已就绪。

### 6.3 实测记录

**执行日**：2026-09-16 ｜ **命令**（`backend` 目录下）：

```bash
python scripts/reset_demo_env.py --db <隔离库绝对路径>.db --selftest --report <报告>.json
```

`--selftest` 跑的正是 DR-0018 §6 那条判据：**基线 → 改变状态 → 复位 → 再取基线，两轮比对**。
本轮结果 **`RESET: OK`**，五项判据全绿（下表为**实测**，非设计意图）。

| # | 判据 | 实测 |
| --- | --- | --- |
| ① | **复位清除改动**（两轮） | `[True, True]` —— 改动后 `ent_` 行数 **42**，复位后 **0** |
| ② | **迁移记账**复位后与种子后一致（两轮） | `[True, True]` —— 两侧均 **23** 条 |
| ③ | **两轮种子后基线一致** | `True` —— 逐表行数逐字节相同（见下表） |
| ④ | **两轮复位后指纹一致** | `True` |
| ⑤ | 两轮均无环境错误 | `True`（后端由本进程起、本进程收） |

**"改变状态"是真实业务动作，不是直接写库**：以探针身份调 `POST /auth/login`
（HTTP **200**，`users` 新增一行），再调 `POST /entrust/assignments`
（HTTP **200**，`assignment_id=5`）—— 两步都要过权限与 UUID 幂等头。

**基线（两轮逐字节一致；`seeded.tables=29`）**

| 表 | 行 | 表 | 行 | 表 | 行 |
| --- | --- | --- | --- | --- | --- |
| `ent_assignment` | 4 | `ent_organization` | 3 | `ent_org_member` | 5 |
| `ent_entrustment` | 3 | `ent_workflow_task` | 7 | `ent_artifact` | 6 |
| `ent_artifact_revision` | 6 | `ent_exception` | 2 | `ent_exception_event` | 3 |
| `ent_exception_link` | 1 | `users` | 8 | `orders` | 4 |
| `payments` | 3 | `ships` | 3 | `cargo` | 4 |
| `berths` | 2 | `berth_appts` | 4 | `_migration_history` | 23 |

⚠️ `ent_attachment` / `ent_attachment_text` / `ent_agent_job` / `ent_agent_job_attempt` /
`ent_session` / `ent_session_message` / `ent_idempotency` / `ent_revalidation` 实测均为 **0**，
`agent_calls` 亦为 0 —— 这是**当前种子的覆盖范围**，不是缺陷。

**复位后的库形**（两轮一致）：`tables=21`、`_migration_history=23`、`ent_` 行数 **0**。

⚠️ **21 而不是 29 是预期的**：21 = 19 张 `ent_` 表 ＋ `_migration_history` ＋ `sqlite_sequence`。
基线表（`users` / `orders` / `payments` / `ships` / `cargo` / `berths` / `berth_appts`）
由应用启动钩子的 `create_all` 建（DR-0001 明确把 `ent_` 前缀排除在外），而复位只跑
`migrate.py` ⇒ 复位后到"起后端"之间，基线表**本就不存在**。**这不是缺表**：
"复位完成"的准确含义是"**下次启动会展成基线**"，所以 `--seed` 会把"起后端"排进复位序列。

**安全闸：拒绝路径逐条实测**（退出码 **2**，且**未做任何改动**）

| 场景 | 实测 |
| --- | --- |
| 未传 `--db` | `REFUSED` |
| 非 SQLite（`.txt`） | `REFUSED` |
| 目标＝`backend/pinglu_didi_dev.db` | `REFUSED`（且**不可解除**） |
| 目标位于仓库工作树内 | `REFUSED` |
| `APP_ENV=production` | `REFUSED` |
| 合法隔离目标 ＋ `--dry-run` | 放行，`RESET: DRY_RUN`，**目标文件未被创建** |

**四条一致性逐面结论**（能负责的与不能负责的**分开写**）

| 面 | 结论 | 依据 |
| --- | --- | --- |
| **数据库** | **PASS** | `migrate.py --verify` rc=0；两轮表数与迁移记账一致（判据 ②④） |
| **附件** | **LIMITATION** | 默认目录 `backend/var/attachments` 是**多环境共享**的 ⇒ 清它就会波及别的环境。本库重建后 `ent_attachment`=0，**引用一致性自动成立**；孤儿文件需 `--attachments-dir <环境专属目录>` 才清。**不记 PASS** |
| **后台作业** | **PASS** | `ent_agent_job` 无未释放租约（`lease_owner IS NOT NULL` 计数 0） |
| **身份与授权** | **PASS** | `users`=8 / `ent_org_member`=5 / `ent_entrustment`=3，两轮一致 |
| **客户端身份缓存** | **NOT_APPLICABLE** | 小程序 storage 属**客户端**状态，后端脚本够不着。走查侧替代做法：每轮用**全新 IDE 实例**（全新 storage），不靠"清缓存" |

⚠️ **不声称什么**：本节**不是** BP-05 完成，**也不是** D1-17 全部完成（DR-0018 §2.2-6）。

**一处刻意加严（超出行文要求，但会挡掉最难查的假绿）**：铺种子前若 8000 端口上已有
**不是本进程起的**后端在跑，脚本**判环境错误并拒绝**（退出码 2）。理由：那个进程连的是
**别的库** —— 在它上面跑种子会得到"种子成功 ＋ 目标库依旧为空"的组合，看起来全绿。

**S5 仍要做的事**（S5 工作项 2，`DEMO-1-plan.md` §5）：把复位从"最小版"补成**环境属性**
（共享环境上的固定复位入口 + 三种夹具状态），并回答三个本切片没有回答的问题：
① 哪些表要清、按什么顺序（外键）；② 清了之后**谁**重铺（三份种子的顺序不能变）；
③ 幂等种子「有则跳过」的语义在复位后是否还成立。

## 7. 三种夹具状态 —— **缺失**

合同要求演示环境具备三种**夹具状态**（`DEMO-1-plan.md` §5 的 S5-1）：

| 夹具状态 | 现状 | 说明 |
| --- | --- | --- |
| **干净起点** | **部分**（2026-09-16 更新） | 本轮产出**最小隔离复位**（§6）⇒「回到一致、可复制的起点」这**一半**已可做到；但**环境属性**那一半（共享环境上的固定复位入口）仍**缺失**，归 S5-1。**原表述保留**：需要"没有任何委托 / 案件"的可重复起点。现状最接近的是临时库（§6），但那是本机脚本行为，不是环境属性 |
| **变更复核检查点** | **缺失** | 需要一张处于"变更待复核"（`revalidation` open）的单。种子里**没有**专门夹具 |
| **已完成历史委托** | **缺失** | 需要一张走完生命周期（含结案）的委托。种子里**没有**；且**委托级结案命令本身尚未实现**（`DEMO-1-plan.md` §3.4 已登记），所以这一项在 S5 之前**做不出来** |

⚠️ 这里**不做**任何"近似替代"的宣称：种子里确实有 `claimed` / `submitted` / 案件
`open` 若干状态**可复用**，但它们**不是**上面这三种夹具状态的定义 ——
把"有状态的数据"说成"有夹具"，正是本 runbook 要避免的那种涂绿。

---

## 8. 走查与证据约定

```bash
python scripts/run_walkthrough_devtools.py                       # 全部章节
python scripts/run_walkthrough_devtools.py --section 41          # 只跑指定章节
python scripts/run_walkthrough_devtools.py --section all --pay   # 含 ⑧b 的支付流转
```

* ⚠️ `--section all` 与 `--pay` 是**两个独立开关**：`all` 会把 ⑧b 排进顺序但记
  `NOT_RUN`，真跑必须显式加 `--pay`（或 `WALK_PAY=1`）。
* **产物约定**：截图与 `summary.json` 落在 `miniapp-device-artifacts/walk-<ts>/`
  （`.gitignore` 内、不进库）；结构性产物落在 `artifacts/*.json`。
* **六档登记**：`PASS` / `FAIL` / `ENV_BLOCKED` / `REVIEW_REQUIRED` / `NOT_RUN` /
  `LIMITATION`，**只有 `PASS` 计入通过**。
  ⚠️ 汇报口径上 `LIMITATION` 会被**归并进 `NOT_RUN`** ⇒ 章节结论显示 `RESULT: NOT_RUN`
  时，**不表示"没跑"**（可能是"跑了 N 项、其中若干项工具不可验证"），汇报必须双句。
* **后端与 IDE 的生命周期**：本机实测 `Popen` 起的 uvicorn / IDE **随发起命令的
  shell 结束被回收** ⇒ 「起后端」与「跑走查」必须在**同一个进程**里（见各切片
  `_walk*_run.py`），否则走查里所有页面会显示"无法连接后端"，看起来像页面全坏了。

---

## 9. 本切片（S1）未闭合的项

1. **§6 隔离复位**：**最小版已完成**（`backend/scripts/reset_demo_env.py`，2026-09-16，DR-0018）；
   ⚠️ **不等于 D1-17 完成** —— "环境属性"那一半与「fresh-run / seeded-checkpoint 可区分」归 S5-1 / S5-2。
2. **§7 三种夹具状态**：缺失 —— 归 S5-1；其中"已完成历史委托"还卡在
   **委托级结案命令未实现**。
3. `seed_contract_cases.py` 只在 ⑦b / ⑨b 章需要，**尚未纳入 §5 的标准启动序列**的说明
   （本文档第 ④ 行已注明，但"什么时候必须跑"应当由 S5 的环境脚本决定）。
