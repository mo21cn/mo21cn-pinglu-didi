# DEMO-1 Runbook —— 演示环境、夹具与复位

> **本文档的状态：S1 起步版（2026-09-16），不是 S5 收口版。**
>
> 合同 §8.1 要求 `Start BP-05 environment and fixtures during S0/S1. Do not defer
> integration to the final slice.` ⇒ 本文档在 S1 落地，覆盖「账户 / 组织 / 启动 /
> 迁移 / 种子」五件的**现状**，并把 **§6 隔离复位** 与 **§7 三种夹具状态** 这两项
> **仍然缺失**的事实如实写明。
>
> ⚠️ **不声称什么**：本文档**不是**「BP-05 完成」。§6 / §7 两节的标题就是「缺失」，
> 它们由 S5 的两个工作项（`DEMO-1-plan.md` §5 的 S5-1 / S5-2）承担。
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

## 6. 隔离复位流程 —— **缺失**

| 项 | 状态 |
| --- | --- |
| 环境专属的复位 / 清库脚本 | **缺失**（全仓未找到；`DEMO-1-plan.md` §3.5 同结论） |
| 替代做法（本机走查实际在用的） | 见下 |

**现状替代做法**：走查一体化脚本（`_walk4c_env`）为每一轮**新建一个临时 SQLite 库**
（`DATABASE_URL=sqlite:///<临时库>`），再 `migrate.py` + 三份种子。这带来两个属性：

* ✅ **隔离**：本轮走查的写入不会污染上一轮，也不会污染开发库；
* ❌ **不是复位**：它换的是**数据库**，而合同要的是**同一套环境回到干净起点**。
  两者在"演示环境"这个语境下不等价 —— 前者只在本机脚本里成立，
  BP-05 的演示环境是**一台共享的、有固定连接串的服务**，没有"换一个库"这条路。

**S5 要做的事**（S5 工作项 2，`DEMO-1-plan.md` §5）：一个**环境专属的管理脚本**
（**不对普通用户开放**），把环境回到干净起点。它需要回答三个本切片没有回答的问题：
① 哪些表要清、按什么顺序（外键）；② 清了之后**谁**重铺（三份种子的顺序不能变）；
③ 幂等种子「有则跳过」的语义在复位后是否还成立。

---

## 7. 三种夹具状态 —— **缺失**

合同要求演示环境具备三种**夹具状态**（`DEMO-1-plan.md` §5 的 S5-1）：

| 夹具状态 | 现状 | 说明 |
| --- | --- | --- |
| **干净起点** | **缺失** | 需要"没有任何委托 / 案件"的可重复起点。现状最接近的是临时库（§6），但那是本机脚本行为，不是环境属性 |
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

1. **§6 隔离复位**：缺失 —— 归 S5-2。
2. **§7 三种夹具状态**：缺失 —— 归 S5-1；其中"已完成历史委托"还卡在
   **委托级结案命令未实现**。
3. `seed_contract_cases.py` 只在 ⑦b / ⑨b 章需要，**尚未纳入 §5 的标准启动序列**的说明
   （本文档第 ④ 行已注明，但"什么时候必须跑"应当由 S5 的环境脚本决定）。
