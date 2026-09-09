# 环境隔离规范

> 本文档定义开发、测试、生产三环境的隔离机制，为持续集成/持续部署（CI/CD）做准备。核心原则：**同一份代码，不同环境配置**，敏感值不进代码库。

## 1. 环境划分

| 环境 | APP_ENV | 用途 | 数据 | 配置来源 |
|------|---------|------|------|---------|
| 开发（development） | `development` | 本地开发、联调 | 本地/共享开发库 | `.env.development` |
| 测试（test） | `test` | CI 门禁、集成测试 | 隔离测试库 | `.env.test` |
| 生产（production） | `production` | 线上运行 | 生产库（独立） | CI/CD secrets 注入 |

## 2. 隔离机制

### 2.1 配置加载（pydantic-settings）

后端配置统一由 `backend/app/core/config.py` 加载：

```python
APP_ENV = os.getenv("APP_ENV", "development")
# Settings 类 env_file = f".env.{APP_ENV}"，即自动加载对应环境文件
```

- 设置环境变量 `APP_ENV=test` 即切换到测试配置。
- 各环境差异（数据库名、DEBUG、日志级别、端口）写在对应 `.env.{env}` 文件。

### 2.2 配置优先级（由高到低）

```
1. 系统环境变量（CI secrets / 部署环境注入，最高优先级）
2. .env.{APP_ENV} 文件
3. Settings 类的默认值
```

### 2.3 敏感值管理

| 敏感项 | 处理 |
|--------|------|
| 微信 AppSecret / 支付密钥 | 不写入任何 `.env` 文件，由 CI secrets / 部署环境注入 |
| 数据库密码 | `.env.development` / `.env.test` 用占位弱口令；生产由 secrets 注入 |
| LLM / 向量库密钥 | 同上，经 CI secrets 注入 |

> `.env`（本地覆盖文件）已在 `.gitignore` 中排除；`.env.example` 与各环境基线 `.env.*` 入库（仅含非敏感值）。

## 3. 文件清单

| 文件 | 是否入库 | 说明 |
|------|---------|------|
| `backend/.env.example` | ✅ | 变量模板（全量字段 + 注释） |
| `backend/.env.development` | ✅ | 开发环境基线（非敏感） |
| `backend/.env.test` | ✅ | 测试环境基线（非敏感） |
| `backend/.env.production` | ✅ | 生产占位（敏感值标注 `<注入>`） |
| `.env`（本地覆盖） | ❌ | 开发者本地私有，不入库 |

## 4. CI/CD 衔接

### 4.1 CI（持续集成）

`.github/workflows/ci.yml`：push 到 `develop` 或 PR 到 `develop/main` 时触发，执行后端 `ruff lint` + `pytest`（`APP_ENV=test`）。

### 4.2 CD（持续部署，后续接入）

| 环境 | 触发 | 部署方式 |
|------|------|---------|
| 测试 | 合并到 `develop` | 自动构建镜像 → 部署测试环境 |
| 生产 | 打 tag `vX.Y.Z` | 人工审批 → 构建镜像 → 部署生产环境 |

生产部署需在 GitHub Secrets 中配置：`DB_PASSWORD`、`WX_APP_SECRET`、`WX_PAY_KEY` 等，运行时以环境变量注入容器。

## 5. 环境切换速查

```bash
# 本地开发（默认）
uvicorn app.main:app --reload

# 切换到测试环境
APP_ENV=test uvicorn app.main:app

# 切换到生产环境（配合 secrets）
APP_ENV=production uvicorn app.main:app
```

```powershell
# Windows PowerShell
$env:APP_ENV="test"; uvicorn app.main:app
```
