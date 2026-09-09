# Git Flow 分支管理规范

> 本文档定义 `pinglu-didi` 的 Git 分支管理策略。以 **Git Flow** 为主干开发模型，所有开发、发布、修复均须遵循本规范。

## 1. 分支模型

```
main        生产分支（稳定，随时可发布）
  │
develop     开发主线（集成分支）
  │
  ├── feature/<编号>-<简述>   功能分支（从 develop 切出，合并回 develop）
  │
  ├── release/vX.Y.Z          发布分支（从 develop 切出，合并回 main + develop）
  │
  └── hotfix/<简述>           热修复分支（从 main 切出，合并回 main + develop）
```

## 2. 分支职责

| 分支 | 来源 | 合并回 | 生命周期 | 说明 |
|------|------|--------|---------|------|
| `main` | — | — | 长期 | 生产代码，只接受 `release/*` 与 `hotfix/*` 合并；每次合并打 tag |
| `develop` | — | — | 长期 | 开发主线，`feature/*` 的集成地 |
| `feature/*` | develop | develop | 短期 | 单一功能/需求，完成后删除 |
| `release/*` | develop | main + develop | 短期 | 版本发布准备（版本号、CHANGELOG、收尾测试） |
| `hotfix/*` | main | main + develop | 短期 | 生产紧急修复，完成后删除 |

## 3. 标准工作流

### 3.1 功能开发（feature）

```bash
git checkout develop
git pull --rebase
git checkout -b feature/F1-01-wechat-login
# ... 开发与提交（提交信息遵循 Conventional Commits）...
git push -u origin feature/F1-01-wechat-login
# 在 GitHub 发起 Pull Request → develop，通过代码审查 + CI 后合并
```

### 3.2 版本发布（release）

```bash
git checkout -b release/v0.1.0 develop
# 更新版本号、CHANGELOG，执行收尾测试
# 通过后：
#   1) 合并回 main，并打 tag：git tag v0.1.0
#   2) 合并回 develop，保持主线同步
```

### 3.3 紧急修复（hotfix）

```bash
git checkout -b hotfix/payment-timeout main
# ... 修复 ...
# 合并回 main（打 tag），再合并回 develop
```

## 4. 命名规范

| 分支类型 | 命名规则 | 示例 |
|---------|---------|------|
| feature | `feature/<需求编号>-<小写简述>` | `feature/F5-01-csp-filter` |
| release | `release/v<主>.<次>.<修订>` | `release/v0.1.0` |
| hotfix | `hotfix/<小写简述>` | `hotfix/berth-oversell` |

> 需求编号对应《开发需求文档 V2.0》的功能点编号（F1—F20），便于追溯。

## 5. 分支保护规则

在 GitHub 仓库 Settings → Branches 配置（`gh api` 或网页）：

| 分支 | 直接 push | 强制 PR | 最少审查 | CI 必须通过 |
|------|-----------|---------|---------|------------|
| `main` | ❌ 禁止 | ✅ | 2 人 approve | ✅ |
| `develop` | ❌ 禁止 | ✅ | 1 人 approve | ✅ |
| `feature/*` | 允许（个人分支） | — | — | — |

配置命令（待 gh 登录后执行）：

```bash
# main 分支保护
gh api repos/mo21cn/pinglu-didi/branches/main/protection \
  -X PUT -f required_status_checks.strict=true \
  -f required_pull_request_reviews.required_approving_review_count=2 \
  -f enforce_admins=true

# develop 分支保护
gh api repos/mo21cn/mo21cn-pinglu-didi/branches/develop/protection \
  -X PUT -f required_status_checks.strict=true \
  -f required_pull_request_reviews.required_approving_review_count=1 \
  -f enforce_admins=true
```

## 6. 标签（Tag）规范

- 采用语义化版本 `vX.Y.Z`：主版本.次版本.修订号。
- 仅在 `main` 分支上打 tag，发布时由 `release/*` 或 `hotfix/*` 合并触发。

```bash
git tag -a v0.1.0 -m "MVP 首个可演示版本"
git push origin v0.1.0
```

## 7. 合并策略

- `feature/*` → `develop`：**Squash merge**（保持主线历史干净）。
- `release/*` / `hotfix/*` → `main`：**普通 merge**（保留完整发布历史）。
- 合并前须确保 CI 通过、审查通过、无合并冲突。
