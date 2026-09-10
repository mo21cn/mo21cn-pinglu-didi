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

## 工程规范（务必先读）

| 规范 | 文档 |
|------|------|
| 开发环境 | [docs/01-development-environment.md](docs/01-development-environment.md) |
| 分支管理（Git Flow） | [docs/02-git-flow.md](docs/02-git-flow.md) |
| 提交规范（Conventional Commits） | [docs/03-commit-convention.md](docs/03-commit-convention.md) |
| 代码审查 | [docs/04-code-review.md](docs/04-code-review.md) |
| 环境隔离 | [docs/05-environment-isolation.md](docs/05-environment-isolation.md) |

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
| S0 `[ ]` | 地基：L3 路由注册 + 页面四件套骨架 + 静态校验脚本扩展 | `app.json`、`scripts/verify_miniapp.js` | 校验全绿 |
| S1 `[ ]` | **合同三级页 ★ Agent 亮点**（含订单页合同弹层双入口） | `pages/trade/contract/` | 真实订单出合同 + 风险点 + 占位底栏 |
| S2 `[ ]` | 交易闭环页组：支付详情、撮合结果页正式化 | `pages/trade/payment/`、`pages/trade/match/` | 单据状态机 + 四项评分拆解 |
| S3 `[ ]` | 港口域详情页：泊位档期、预约审核详情（可裁剪） | `pages/port/berth/`、`pages/port/appt/` | 档期可视化 + 防超卖演示 |
| S4 `[ ]` | 汇报打磨：三态兜底（加载/空/错误）+ 演示数据贯通 + 演示脚本 | 全局 | 演示路径可一次跑通 |

**节奏约定**：一个批次 = 一个 feature 分支 = 一个 PR，CI 双绿后 squash 合入 develop；本地平铺分支名、推送映射远程斜杠名；每完成一个页面四件套立即 `git add`（防 file-rollback）。

### 三级页候选清单（按落地成本分档）

**A 档 · 接口现成，零后端改动（8 项）**

| 三级页 | 挂载点 | 依据接口 |
|---|---|---|
| 合同详情 ★ | 订单 | `POST /agent/contract/generate` |
| 支付详情 | 订单 | `GET /payment/payments/{id}`、`GET /payment/payments/order/{oid}` |
| 撮合结果 | 找船 | `POST /match/cargos/{id}/ships` |
| 货源详情 / 编辑 | 找船 | `GET/PATCH /cargo/shipments/{id}` |
| 船舶备案详情 | 找货 | `GET/PATCH /ship/registry/{id}` |
| 泊位档期详情 | 港口 | `GET /port/berths/{id}/schedule` |
| 预约审核详情 | 港口 | `GET /port/appts-review`、`POST /port/appts/{id}/confirm\|reject\|cancel\|complete` |
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

### 汇报演示锚点（第一次汇报）

- **主线三条**：身份切换 → 货主发单/船东找货 → 支付·合同·客服闭环。
- **技术亮点**：合同 Agent（LLM 零数字：模板渲染核心条款 + 规则引擎产风险）、港口防超卖（行锁 + 重叠计数）、RAG 客服（25 chunk 检索）、自定义 tabBar（随角色变化）。
- **数据准备**：`WECHAT_MOCK=true` 时 Storage 设 `dev_login_code`（`seed-shipper`/`seed-owner`/`seed-port`）快速切身份。
