# 开发环境手册

> 本手册指导团队成员在一台新机器上从零搭建 `pinglu-didi` 开发环境。

## 1. 前置要求

| 工具 | 版本要求 | 说明 |
|------|---------|------|
| Git | ≥ 2.40 | 版本控制（含 Git Flow 工作流） |
| Python | 3.12+ | 后端运行时 |
| Node.js | ≥ 20 | 工程工具链（husky/commitlint） |
| Docker | 可选 | 本地起 MySQL/Redis（或自行安装） |
| 微信开发者工具 | 最新 | 小程序开发 |
| GitHub CLI（gh） | 可选 | 远程仓库操作 |

## 2. 环境搭建

### 2.1 克隆仓库

```bash
git clone git@github.com:mo21cn/pinglu-didi.git
cd pinglu-didi
git checkout develop
```

### 2.2 安装工程工具链（Git 钩子）

```bash
npm install   # 安装 husky + commitlint，自动配置 Git 钩子
```

### 2.3 后端环境

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt

# 准备环境配置
cp .env.example .env
# 如需切换环境：APP_ENV=test / development / production
```

### 2.4 启动服务

```bash
# 方式一：本地直跑
uvicorn app.main:app --reload

# 方式二：Docker（MySQL + Redis + backend 一键起）
docker compose -f deploy/docker-compose.yml up
```

### 2.5 小程序

用微信开发者工具导入 `miniapp/` 目录，`project.config.json` 中的 `appid` 替换为实际小程序 AppID。

## 3. 目录结构

```
pinglu-didi/
├── backend/            # 后端（模块化单体，FastAPI）
│   ├── app/
│   │   ├── main.py     # 入口
│   │   ├── core/       # 配置（环境隔离）、安全、依赖
│   │   └── modules/    # cargo/ship/port/match/order/payment/agent
│   └── tests/
├── miniapp/            # 微信小程序（三角色合一工作台）
├── docs/               # 工程规范文档
├── deploy/             # Docker / 部署编排
├── scripts/            # 辅助脚本
└── .github/            # CI/CD + PR 模板 + CODEOWNERS
```

## 4. 常用命令

| 命令 | 说明 |
|------|------|
| `ruff check app` | 后端 lint |
| `ruff format app` | 后端格式化 |
| `pytest` | 运行测试 |
| `mypy app` | 类型检查 |
| `git flow` / 分支命令 | 见 `docs/02-git-flow.md` |

## 5. 故障排查

| 问题 | 解决 |
|------|------|
| 提交被拒（commitlint） | 检查提交信息是否符合 Conventional Commits（`docs/03-commit-convention.md`） |
| 启动报数据库连接错误 | 确认 MySQL 已启动，`.env` 中 `DB_HOST/PORT/NAME/USER/PASSWORD` 正确 |
| husky 钩子不生效 | 确认已运行 `npm install`；检查 `git config core.hooksPath` 指向 `.husky` |
| 小程序无法登录 | 确认 `project.config.json` 的 `appid` 已替换，且后端 `WX_APP_ID/SECRET` 已配置 |
