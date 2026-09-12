# DR-0002：MySQL 集成测试环境

| 字段 | 内容 |
| --- | --- |
| 编号 | DR-0002 |
| 日期 | 2026-09-13 |
| 状态 | **已采纳** |
| 决策人 | HO（对话授权） |
| 关联 | 基线报告 D2；AC-22（MySQL 方言可执行）；开发规范五「CI：MySQL 并发测试不能由 SQLite 结果代替」 |

## 背景

开发/测试库是 SQLite，生产是 MySQL。迁移条目的 `sql` 支持双方言字典
（`{"mysql": ..., "sqlite": ...}`，DR-0001），但在 DR-0002 之前，MySQL 分支的 SQL
**只经过人工评审，从未真实执行过**——语法错误、保留字冲突、字符集/排序规则差异都
要到生产才能暴露。AC-22 对应子项因此只能记 `not-run`。

## 选项

| 选项 | 内容 | 取舍 |
| --- | --- | --- |
| A | CI 起 MySQL service container，迁移在真实 MySQL 上执行并验证 | 标准做法；GitHub hosted runner 原生支持，无外部依赖 |
| B | 仅本地可复现脚本 + CI 标注 not-run | 不增加 CI 时长，但门禁永远看不到 MySQL，等于没有门禁 |

## 决策

**采纳 A**：CI 新增 `db-migration-mysql` job：

- MySQL 8.0 service container（`MYSQL_ROOT_PASSWORD=root`，库 `entrust_test`，health check 通过后再跑）；
- 驱动用 `pymysql`（纯 Python，无编译依赖），job 内单独安装，不进 `requirements.txt`
  （生产驱动选型不在本决策范围）；
- 执行序列与 SQLite job 一致：`--status` → `--verify` → `--verify`，证明
  **MySQL 方言 SQL 可执行 + 幂等**；
- 该 job 为**阻塞检查**，不允许 `continue-on-error`。

## 范围界定（防止过度声明）

本决策覆盖的是**迁移 SQL 的 MySQL 真实执行**。以下仍属后续增量，不在本 job 内：

1. **pytest 对接 MySQL 的集成/并发测试**（开发规范五的最终要求）——需要 conftest 支持
   `DATABASE_URL` 指向 MySQL 的测试会话与确定性种子，随对应 ENT 任务落地；
2. 生产库的字符集/排序规则约定（建议 `utf8mb4` + `utf8mb4_0900_ai_ci`，随首个真实
   业务表迁移在 SQL 中显式声明）。

## 验收影响

- AC-22「MySQL 方言可执行」相应子项：`not-run` → **由 CI db-migration-mysql job 覆盖**；
- 「并发正确性」类子项：仍待 pytest-MySQL 集成（后续任务），届时才可记通过。

## 回退

删除该 job 即可，不影响其他门禁；迁移 SQL 本身双方言并存，无回退成本。
