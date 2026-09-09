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
git clone git@github.com:mo21cn/pinglu-didi.git
cd pinglu-didi

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
