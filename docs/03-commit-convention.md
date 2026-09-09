# Git Commit 提交规范

> 本项目采用 **Conventional Commits**（约定式提交），由 `husky` + `commitlint` 在提交时自动校验，不符合规范的提交将被拒绝。

## 1. 提交信息格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

| 部分 | 必填 | 说明 |
|------|------|------|
| type | ✅ | 提交类型（见下表） |
| scope | 可选 | 影响范围（模块/功能编号），如 `F5`、`cargo`、`miniapp` |
| subject | ✅ | 一句话描述（≤ 100 字符，祈使句） |
| body | 可选 | 详细说明（为什么改、影响面） |
| footer | 可选 | 关联需求/关闭 issue，如 `Closes #12` |

## 2. 提交类型（type）

| type | 含义 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(F5): 实现 Stage1 CSP 硬约束过滤` |
| `fix` | 缺陷修复 | `fix(payment): 修复分账金额精度丢失` |
| `docs` | 文档变更 | `docs: 更新环境隔离规范` |
| `style` | 代码格式（不影响逻辑） | `style: 统一缩进为 4 空格` |
| `refactor` | 重构（非新功能、非修 bug） | `refactor(cargo): 抽取货源校验器` |
| `perf` | 性能优化 | `perf(match): 优化 Stage1 过滤至 P99<200ms` |
| `test` | 测试相关 | `test: 补充撮合引擎单元测试` |
| `build` | 构建/依赖变更 | `build: 升级 FastAPI 至 0.115` |
| `ci` | CI/CD 配置 | `ci: 新增生产环境部署工作流` |
| `chore` | 杂项（不涉及源码） | `chore: 更新 .gitignore` |
| `revert` | 回滚 | `revert: 回滚 feat(F5) 的改动` |

## 3. 提交示例

```bash
# 标准功能提交
git commit -m "feat(F1): 实现微信 code2session 三角色登录"

# 带正文与关联
git commit -m "fix(berth): 修复泊位预约超卖问题" -m "引入数据库唯一约束 + 分布式锁，预约即锁定" -m "Closes #23"

# 破坏性变更（footer 标注）
git commit -m "refactor(match): 重构撮合评分接口" -m "BREAKING CHANGE: ScoreStrategy 接口签名变更"
```

## 4. 校验机制

| 层 | 机制 | 位置 |
|----|------|------|
| 本地提交 | husky `commit-msg` 钩子 → commitlint 校验 | `.husky/commit-msg` |
| 本地预检 | husky `pre-commit` 钩子 → 冲突标记/敏感文件检查 | `.husky/pre-commit` |
| 规则配置 | Conventional Commits 规则 | `.commitlintrc.yml` |

## 5. 常见错误与修正

| 错误 | 原因 | 修正 |
|------|------|------|
| `subject may not be empty` | 缺少描述 | 补充 `<type>: <subject>` |
| `type must be one of [...]` | 类型不在枚举内 | 使用规范的 type（feat/fix/...） |
| header 超 100 字符 | 标题过长 | 精简 subject，细节写入 body |

> 若确需绕过校验（如自动合并提交），用 `git commit --no-verify`，但须谨慎并说明原因。
