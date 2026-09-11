# pinglu-didi · 平陆运河"滴滴打船"平台

借鉴网约车"车客匹配"逻辑改造内河航运，以**"货—船—港"**为业务主链路、**微信小程序三角色合一**为客户端、**五领域智能体**为智能化层的市场化交易撮合与增值服务平台。

> 项目背景与需求见《开发需求文档 V2.0》；开发与交付计划见《三合一 MVP 开发与交付计划》。

## 技术栈

| 层 | 技术选型 |
|----|---------|
| 客户端 | 微信小程序（原生，三角色合一工作台） |
| 后端 | Python 3.12+ / FastAPI（模块化单体，未来按域拆微服务） |
| 撮合引擎 | 约束满足（CSP）+ 多目标评分（MVP 阶段内置） |
| 智能体平台 | LLM 网关 + RAG + 五领域 Agent（Dify 验证 → LangGraph 承接） |
| 数据 | MySQL / Redis / 向量库（Milvus/pgvector） |
| 工程化 | Git Flow + Conventional Commits + husky/commitlint + GitHub Actions |

## 目录结构

```
pinglu-didi/
├── backend/            # 后端（模块化单体）
│   └── app/
│       ├── main.py     # FastAPI 入口
│       ├── core/       # 配置、安全、依赖（环境隔离核心）
│       └── modules/    # 业务模块：cargo/ship/port/match/order/payment/agent
├── miniapp/            # 微信小程序（三角色工作台）
├── docs/               # 工程规范文档
├── deploy/             # Docker / 部署配置
├── scripts/            # 开发辅助脚本
├── .github/            # CI/CD + PR 模板 + CODEOWNERS
└── .husky/             # Git 提交钩子
```

## 快速开始

```bash
# 1. 克隆
git clone git@github.com:mo21cn/mo21cn-pinglu-didi.git
cd mo21cn-pinglu-didi

# 2. 后端依赖
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. 环境配置（复制模板并按需修改）
cp .env.example .env

# 4. 启动
uvicorn app.main:app --reload
```

### 验证脚本

```bash
# 小程序静态校验（JSON 语法 / 页面四件套 / tabBar / 路由可达 / 事件处理函数存在性）
node scripts/verify_miniapp.js

# 前端动态校验（拉真实后端载荷驱动 13 个页面的取数与装饰逻辑，CI 可跑）
node scripts/verify_frontend_e2e.js

# UI 交互契约校验（组件 props/事件、弹层闭环、showActionSheet 长列表等静态与逻辑防线，无需 IDE）
node scripts/verify_ui_interactions.js

# 登录链路复现校验（真 HTTP 打后端，验链路顺序 / 鉴权头 / token 角色 / 失败可定位；需后端已起）
node scripts/verify_login_flow.js

# 真机走查（需开发者工具「设置 → 安全设置 → 服务端口」已开启）
# 前置：后端已起 + 已执行 backend/scripts/seed_demo.py
MINIAPP_AUTO_WS=ws://127.0.0.1:9421 node scripts/verify_miniapp_device.js
```

> ⚠️ **identity 进入失败这类缺陷要用 `verify_login_flow.js` 才抓得到**：其余脚本都只能
> 用一个假的 `request` 猜顺序，唯有它加载真实 `utils/auth.js` + `pages/index/index.js`
> 并让 `wx.request` 走真实 HTTP —— 「补了 bind 忘了 switch ⇒ 带旧角色 token ⇒ 全线 403」
> 与「wx.login 不可用 ⇒ 整条进入链路卡死」都是这样定位出来的。

> ⚠️ **港口选择不要用 `wx.showActionSheet`**：该 API 的 `itemList` 上限为 6 项，
> 超过会直接 fail 且页面通常没有 fail 兜底 → 表现为「点了没反应 / 没有下拉选择」。
> 港口共 13 个，请统一用 `components/port-picker`（底部滚动列表，可承载任意长度）。
> `scripts/verify_ui_interactions.js` 已把这条写成静态防线。

> `verify_miniapp_device.js` 依赖 `miniprogram-automator`（未入库），按需 `npm i -g miniprogram-automator`；
> 付费点击等会产生副作用的步骤默认关闭，用 `WALK_PAY=1` 显式开启。

## 工程规范（务必先读）

| 规范 | 文档 |
|------|------|
| 开发环境 | [docs/01-development-environment.md](docs/01-development-environment.md) |
| 分支管理（Git Flow） | [docs/02-git-flow.md](docs/02-git-flow.md) |
| 提交规范（Conventional Commits） | [docs/03-commit-convention.md](docs/03-commit-convention.md) |
| 代码审查 | [docs/04-code-review.md](docs/04-code-review.md) |
| 环境隔离 | [docs/05-environment-isolation.md](docs/05-environment-isolation.md) |
| 汇报演示脚本 | [docs/汇报演示脚本.md](docs/汇报演示脚本.md) |
| 汇报演示脚本（操作手册 + 答辩口径） | [docs/汇报演示脚本.md](docs/汇报演示脚本.md) |
| 三级页效果预览（HTML） | [合同页](docs/三级页-合同页-效果预览.html) · [S2 支付与撮合](docs/三级页-S2-支付与撮合-效果预览.html) · [S3 港口域](docs/三级页-S3-港口域-效果预览.html) |

## 三条工程底线

1. **确定性内核不经 LLM**：撮合、支付、防超卖、订单状态机永不经大模型。
2. **Agent 无直写**：智能体写操作一律经业务领域 API，事务可回滚可审计。
3. **交易全链路留痕**：实名、留痕、复核三标配。

---

## 迭代需求（活文档）

> 本节是冲刺排期与待办的**事实来源**，新会话请从此处提取上下文。状态：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成。

### 当前冲刺：三级页冲刺（目标：第一次汇报原型可用）

**已拍板基线（2026-09-11）**：合同三级页采用 **A 方案（零后端改动）**——复用 `POST /api/v1/agent/contract/generate` 单端点（进页即生成）、草稿不落库、签署/PDF 为占位。原型优先；真正的权威留痕（`contracts` 表）留待后续版本，见 TODO-03。

| 批次 | 内容 | 交付物（页面/文件） | 验收 |
|---|---|---|---|
| S0 `[x]` | 地基：L3 路由注册 + 页面四件套骨架 + 静态校验脚本扩展 | `app.json`、`scripts/verify_miniapp.js` | 校验全绿 |
| S1 `[x]` | **合同三级页 ★ Agent 亮点**（含订单页合同弹层双入口） | `pages/trade/contract/` | 真实订单出合同 + 风险点 + 占位底栏 |
| S2 `[x]` | 交易闭环页组：支付详情、撮合结果页正式化 | `pages/trade/payment/`、`pages/trade/match/` | 单据状态机 + 四项评分拆解 |
| S3 `[x]` | 港口域详情页：泊位档期、预约审核详情（展示性轻量化） | `pages/port/berth/`、`pages/port/appt/` | 档期甘特 + 容量预检 + 防超卖演示 |
| S4 `[x]` | 汇报打磨：三态兜底（加载/空/错误）+ 演示数据贯通 + 演示脚本 | 全局、`backend/scripts/seed_demo.py`、[docs/汇报演示脚本.md](docs/汇报演示脚本.md) | 演示路径可一次跑通 |

**已完成批次记录**

| 批次 | 提交 | 静态校验 | 真实接口冒烟 |
|---|---|---|---|
| S0 | PR #21 `ccf2727` | 16 JSON / 11 页面 / 41 路由 / 144 事件 | — |
| S1 | PR #21 + #22 `6cb3a11` | 同上 + 事件处理函数存在性 | 订单 #1 → 200/885 字符/风险 0；#3 → 200/**2 项高风险**；已撤单 → 400；非参与方 → 400 |
| S2 | 本次 PR | 16 JSON / **12 页面** / **43 路由** / **152 事件** | 支付全链：无单 404 → 发起 201 → 重复发起 409 → 模拟支付 200 → 幂等 paid_at 不变 → 撤单退款 refunded；面议拒发起 400；非参与方 404 |
| S3 | 本次 PR | 18 JSON / **14 页面** / **46 路由** / **164 事件** | 港域档期：3 条重叠预约确认前两条成功、**第三条 409 档期冲突**；档期接口返回 3 条 confirmed（峰值 2/2 容量）；货主访问 `/port/appts` → 403。纯逻辑断言 **87 项通过**（真实载荷驱动） |
| S4 | 本次 PR | 18 JSON / 14 页面 / 46 路由 / **170 事件** | 三态兜底补齐 4 页；样式去重净减 242 行；`seed_demo.py` 改造为幂等（连跑两次全"复用"、零重复数据），播下 4 态订单 + 4 条预约并复现 409 |
| 走查 | PR #26 `2bac9b1` | `scripts/verify_frontend_e2e.js` | **169 项断言全绿**：在真实后端拉真载荷驱动 13 个页面的取数/装饰逻辑（不抛异常、不卡 loading、不误报错误、关键列表有数据），并核对「模板读的字段 vs 产出字段」——全站无渲染空白；顺带复现甘特几何边界、支付金额取锁定值、预约容量预检与服务端同口径 |
| 真机走查 | PR #27 `8be74c2` | `scripts/verify_miniapp_device.js` | **70 项断言全绿、运行期零 console 报错**：驱动真实模拟器逐屏真实点击并截图，覆盖三角色工作台、支付三级页、合同三级页与弹层、撮合双向下单确认、港口运营台（预约 409 拦截、档期甘特峰值 2/2） |
| 合同降级 | 本次 PR | 18 JSON / 14 页面 / 46 路由 / 170 事件 | TODO-10 闭环：LLM 故障时降级（注入 `LLMError`）→ **200 + `degraded=true/network`**，正文保留核心金额与内置标准四条、风险点照常；审计单行 `success=False`。正常路径与 `LLM_MOCK` 结果不变 |

**三者状态机口径（可直接用于汇报答辩）**

| 域 | 状态机 | 幂等 / 并发防线 | 留痕 |
|---|---|---|---|
| 订单 F6 | `matched → shipped → completed` / `matched → cancelled` | 货源至多一个 active 订单（创建时 `FOR UPDATE` 锁货源行） | 五时间戳 |
| 支付 F7 | `pending → paid` / `pending → closed` / `paid → refunded` | 一订单一支付单（`order_id` 唯一约束 + 锁订单行）；回调幂等（重复回调不改 `paid_at`） | 双流水号 + 四时间戳 |
| 撮合 F5 | 无状态（只读推荐，不落库不出单） | 硬约束 CSP 先行过滤；纯函数内核结果可复现 | — |

**支付三级页的展示主线**：一张单据的完整资金留痕（创建锁价 → 支付 → 退款/关闭），用来证明「资金流与订单流一致性由确定性内核驱动」——退款不开放独立端点，只由撤单联动，避免两条状态机各说各话。

**节奏约定**：一个批次 = 一个 feature 分支 = 一个 PR，CI 双绿后 squash 合入 develop；本地平铺分支名、推送映射远程斜杠名；每完成一个页面四件套立即 `git add`（防 file-rollback）。

### 三级页候选清单（按落地成本分档）

**A 档 · 接口现成，零后端改动（8 项）**

| 三级页 | 挂载点 | 依据接口 |
|---|---|---|
| 合同详情 ★ `[x]` | 订单 | `POST /agent/contract/generate` |
| 支付详情 `[x]` | 订单 | `GET /payment/payments/{id}`、`GET /payment/payments/order/{oid}` |
| 撮合结果 `[x]` | 找船 | `POST /match/cargos/{id}/ships` |
| 货源详情 / 编辑 | 找船 | `GET/PATCH /cargo/shipments/{id}` |
| 船舶备案详情 | 找货 | `GET/PATCH /ship/registry/{id}` |
| 泊位档期详情 `[x]` | 港口 | `GET /port/berths/{id}/schedule` |
| 预约审核详情 `[x]` | 港口 | `GET /port/appts-review`、`POST /port/appts/{id}/confirm\|reject\|cancel\|complete` |
| 客服对话 | 顶栏 | `POST /agent/assistant` |

**B 档 · 需小改后端（3 项）**：订单详情·运输时间轴（`OrderOut` 缺 cargo/ship 摘要）、合同留痕/版本（需 `contracts` 表）、实名认证中心（需资质表）。
**C 档 · 无接口占位**：消息中心、搜索、会员卡与资产行、港口 4 组服务、空船发布。

### 已知限制与待办

| ID | 限制 | 影响 | 处置 |
|---|---|---|---|
| TODO-01 | 货源大厅为演示数据，后端无公开货源池接口 | 船东端看不到其他货主的真实货源 | 新增 `GET /cargo/pool` |
| TODO-02 | `OrderOut` 仅含 `cargo_id`/`ship_id` | 订单卡路线靠前端富化，船东视角降级为「货源 #id」 | `OrderOut` 增加 cargo 摘要字段 |
| TODO-03 | 合同草稿不落库、无 GET 端点 | 无签署快照与版本，与底线 3「留痕」有落差 | 新增 `contracts` 表 + `GET` 读快照 |
| TODO-04 | 发布空船无接口 | 落本机 Storage 草稿 | 新增空船发布单接口 |
| TODO-05 | 实名/资质认证无接口 | 「我的」页相关入口占位 | 资质表 + 审核流 |
| TODO-06 | 消息中心、搜索、会员资产无接口 | 占位 | 后续版本 |
| TODO-07 | 真实微信登录与支付未联调 | 需小程序 appid + 商户号 | 联调阶段 |
| TODO-08 | 撮合 Stage2 未做 | 评分用船籍港二值近似，未接泊位档期与空驶里程 | 迭代 |
| TODO-09 | `OrderCreate.freight_price` 不继承货源 `offer_price` | 不传即落库为面议单，无法发起支付；小程序下单始终显式传值故不受影响 | **决议：不改动**（schema 注释已订正）。不传即面议本身就是有语义的显式选择，服务端静默兜底会让「漏传」与「面议」不可区分，反而掩盖调用方遗漏；需要默认值应由前端预填——小程序下单已显式传值 |
| TODO-10 | ~~合同 Agent 在 LLM 故障时直接 503~~ | 已闭环 | **已补降级分支**：补充条款回退内置标准四条、接口 200 并带 `degraded`/`degraded_reason`，合同正文显式标注；`mocked` 语义收窄为「LLM_MOCK 规则模板」。审计不掩盖故障（`success=False`） |

### 汇报演示锚点（第一次汇报）

- **主线三条**：身份切换 → 货主发单/船东找货 → 支付·合同·客服闭环。
- **技术亮点**：合同 Agent（LLM 零数字：模板渲染核心条款 + 规则引擎产风险）、港口防超卖（行锁 + 重叠计数）、RAG 客服（25 chunk 检索）、自定义 tabBar（随角色变化）。
- **数据准备**：`WECHAT_MOCK=true` 时 Storage 设 `dev_login_code`（`seed-shipper`/`seed-owner`/`seed-port`）快速切身份。
- **三级页看点**：合同预览（Agent 头卡「核心条款零 LLM」+ 风险卡）、支付详情（金额头卡 + 资金状态留痕时间轴）、撮合结果（引擎头卡「确定性内核 · 零 LLM」+ 四项评分进度条 + 未入局原因）、泊位档期（甘特条 + 峰值占用徽章 + 前置校验链）、预约审核（容量预检算式 + 409 防超卖）。
- **演示脚本**：完整操作顺序与答辩口径见 [docs/汇报演示脚本.md](docs/汇报演示脚本.md)（含前置、12 步演示路径、预期提问口径、故障兜底）。
- **演示数据一键就绪**：`python scripts/seed_demo.py`（幂等，可反复执行）。铺出四态订单（已完成 / 待支付 / 已退款 / 面议）+ 演示独占泊位 `NNG-DEMO-01`（满档，容量 2）/ `GGU-DEMO-02`（空档）+ 4 条预约，并对第三条复现 **409 档期冲突**。登录身份 `seed-shipper` / `seed-owner` / `seed-port`。
- **智能合同仿真案例（订单页 → 查看合同）**：`python scripts/seed_contract_cases.py`（幂等，需先跑 `seed_demo.py`）。为货主与船东各铺 3 张专项订单，点开即可检查合同风险引擎的规则命中：`R3 装货日期临近`（期望装货日 +2 天）、`R4 船舶证书临期`（专用船证书 20 天到期）、`R5 液货/危险品`（液货船 + tanker 货类）；再叠加 `seed_demo` 的已完成单（无风险）/ 待支付单（R1）/ 面议单（R1+R2），**R1–R5 五条规则全覆盖**。订单归属货主、承运船归属船东，两个角色都能在「订单」页看到。已撤单订单后端拒绝生成合同（400），页面上也不显示「查看合同」。
- **汇报前提醒**：后端仍建议以 `LLM_MOCK=true` 起——合同 Agent 已具备降级（TODO-10 已闭环，LLM 故障时接口仍 200、补充条款回退内置模板并带 `degraded` 标识），但模板模式能保证演示文字逐次一致、可复现。
- **小程序侧域名校验（已根治）**：`miniapp/project.config.json` 的 `setting.urlCheck` 已改为 `false`——本项目后端固定是 `http://127.0.0.1:8000`（非 HTTPS、未备案域名），开启校验时请求会被直接拦掉。原先靠 `project.private.config.json` 覆盖，但该文件被 `.gitignore` 忽略，**换机器 clone 后必然复现「连不上后端」**，故直接在仓库配置里关掉。若你的工具仍拦截，可在「详情 → 本地设置」勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」。
- **开发期登录身份（无需真实 AppID）**：仓库 `appid` 是占位值，开发者工具拿不到真实微信身份；而后端 `WECHAT_MOCK=true` 时 `openid = mock-openid-{code}`，**code 本身就是身份**。为避免 `wx.login` 每次返回不同 code（→ 每次登录都新建用户 →「我的货源 / 我的订单」永远为空）以及微信登录服务不可达时的「登录失败」，`utils/auth.js` 的开发期开关 `DEV_STABLE_IDENTITY` 会固定使用 `dev_device_code`（默认 `devtools-local`）作为身份，**不调用 `wx.login`**。接入真实 AppID 后须将其置为 `false`。
  - 想用种子数据演示，在 Storage 面板设 `dev_login_code`（`seed-shipper` / `seed-owner` / `seed-port`）——它的优先级高于开发固定身份，退出登录时会被清除。
  - 失败提示已按环节区分：弹窗会写明「失败环节（登录 / 绑定身份 / 切换身份 / 进入工作台）+ 真实原因 + 处置建议」，首页还会先做一次 `/healthz` 预检，连不上后端时提前提示（可点重试），不再只有一句「登录失败」。
- **船东端演示入口**：`我的船队 → 桂平航 6688（散货 · 1500 载重吨）→ 智能找货`，得 3 条候选可按评分看排序；同队 `横州集运 101`（900 载重吨集装箱船）0 候选是硬约束的正确结果，非缺陷。
