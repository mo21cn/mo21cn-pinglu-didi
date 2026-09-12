# AGENTS.md

> 给在本仓库工作的 AI Agent 与新人开发者的硬约束。与《委托发货 Agent IM 开发规范 v1.0》《docs/entrust/》配套使用。
> 本文只写**必须遵守**的部分；可协商的细节见规范原文的"非强制"清单。

## 1. 仓库与业务

- 仓库：`mo21cn/mo21cn-pinglu-didi`（本地 `E:\pinglu-didi`）。**不新建仓库**，委托发货作为新业务模块并入。
- 技术栈：后端 Python 3.12 + FastAPI + SQLAlchemy（业务大量原生 SQL，未深度使用 ORM）；前端微信小程序（原生 JS，无框架）。
- 冻结基线：tag `v0.5.1` = `0a2bbed`，**仅用于回归追溯，不要重置到它**。
- 委托支线文档：`docs/entrust/`（基线报告、决策记录、PRD 与交付计划）。

## 2. 目录边界

| 用途 | 位置 |
| --- | --- |
| 委托后端模块 | `backend/app/modules/entrust/` |
| 委托后台作业 | `backend/app/modules/entrust/jobs/` |
| 委托接口 | `/api/v1/entrust`（**不得改变旧接口既有含义**） |
| 经理人页面 | `miniapp/pages/manager/` |
| 客户委托页面 | `miniapp/pages/entrust/` |
| 数据库迁移 | `backend/migrations/`（执行器 `backend/migrate.py`） |
| 支线文档 | `docs/entrust/` |

## 3. 强制约束（违反即打回）

### 3.1 权限与身份

- 权限校验**始终在后端执行**；前端不得成为唯一权限或规则执行点。
- 经理人通过**组织成员 + 委托授权**身份操作，**不得冒充货主调用旧接口**。
- 跨领域调用走业务服务，**不得绕过其他模块的状态与权限校验**。
- 既有鉴权是 JWT + `User.current_role`，被 8 个 router 与多个页面直接使用。新增组织/委托权限必须做成**叠加层**，不得替换或改写 `current_role` 的既有语义（AC-01）。

### 3.2 六条公共机制（禁止每个 Agent 各实现一遍）

1. **成果有版本** —— 编辑产生新 revision，客户确认绑定精确版本。
2. **操作幂等** —— 写操作必须带幂等键（底座见 `backend/migrations/ent_idempotency.py`）。
3. **Agent 有执行范围** —— 绑定组织、委托、操作者、允许动作。
4. **人工接管优先** —— 人工接管后的修改不得被旧 Agent 结果覆盖。
5. **客户数据白名单投影** —— 内部成本与会话在**服务端**投影过滤，不能先返回前端再隐藏。
6. **业务事实有来源** —— 报价、交接、收付关联证据；模拟数据必须明确标记。

### 3.3 数据与金额

- 金额一律 `Decimal`／定点精度，**禁止浮点计算**。
- 数量必须带单位。
- **未知值保持未知**，不得用 0 / 空串 / 占位值冒充已知。
- 时区明确存储；事件时间与录入时间分开。
- 错误分类处理，**不得静默吞异常**。

### 3.4 数据库

- 每次模型变更**必须**提供版本化迁移文件，**不依赖 `create_all` 升级旧库**。
- 委托支线新增表统一 `ent_` 前缀，由迁移创建，不进 `create_all`。
- 已发布的迁移条目**禁止修改**；修正用新增条目。id 只增不改。
- 破坏性变更走「新增字段 → 迁移数据 → 切换读写 → 后续清理」。
- 详见 `docs/06-database-migration.md`。

### 3.5 代码分层与风格

- 接口层：校验 / 身份 / 转换。服务层：状态、权限、事务。Agent 层：只做理解与提案，经受限服务写入。
- Python：Ruff（`line-length=100`）+ `ruff format` + mypy `strict`。
- 小程序：沿用现有 JS 风格，**第一阶段不顺带重写前端**。
- 新增页面须注册路由，且通过 `node scripts/verify_miniapp.js` 路由可达检查。

### 3.6 提交、分支与 PR

- 分支：`feature/ENT-<编号>-<描述>`，从最新 `develop` 创建，PR 合回 `develop`。**不建长期不合并的平行分支**，每个分支完成一个可验收增量、尽早集成。
- Conventional Commits，一次提交一个目的；功能修改与大范围格式化／依赖升级分开提交。
- **禁止**：改写共享分支历史、移动已发布标签、用 `--no-verify` 或删测试解决业务代码失败。
- PR 按五段模板填写（问题与结果 / 关联范围 / 实现与影响 / 验证 / 回退与限制）。**未执行的验证必须写明**，`not-run` 不等于通过。
- AI 出具的审查报告**不得**伪装成第二名独立审查人；PR 作者不能批准自己的 PR。

### 3.7 CI 门禁

- 默认使用隔离数据库 + 确定性模型 fixture，**不依赖真实密钥与付费模型**；真实模型冒烟独立运行并明确报告是否执行。
- **MySQL 并发测试不能由 SQLite 结果代替**。
- 缺必需种子数据应失败，**不得静默跳过后宣称通过**。
- **禁止对必需检查设 `continue-on-error`**；失败／取消／异常跳过均不得误判为通过。
- CI 不得触发生产部署、真实通知、采购或资金操作。

### 3.8 发布与验收的三种结论

**PR 可合并** ／ **R1 可验收** ／ **版本可发布** —— 三者不等价。CI 绿 ≠ 需求完成。

## 4. 常用命令

```bash
cd backend
ruff check app tests            # lint
ruff format --check app tests   # 格式门禁（CI 阻塞）
mypy app                        # 类型检查
pytest                          # 单测（APP_ENV=test）
python migrate.py --status      # 迁移状态
python migrate.py --verify      # 执行迁移 + 校验 + 断言无待执行（CI 用）

cd ..
node scripts/verify_miniapp.js        # 小程序结构 / 路由 / 事件
node scripts/verify_ui_interactions.js
node scripts/verify_frontend_e2e.js
```

- 本地开发无需任何 Key：`.env.development` 已入库（`WECHAT_MOCK=true`、`LLM_MOCK=true`）。
- 配置加载顺序：`.env.{APP_ENV}` → `.env.local`（后者不入库）。

## 5. 不要做的事

- 不要为了让检查通过而删除或跳过测试、关闭检查、放宽断言。
- 不要把内部成本、Agent 会话内容返回给客户端再"前端隐藏"。
- 不要在没有迁移文件的情况下改表结构。
- 不要用浮点处理金额；不要用占位值填充未知字段。
- 不要在未确认来源的情况下把 Agent 产出当作业务事实。
