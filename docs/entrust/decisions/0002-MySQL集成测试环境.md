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

## 附：MySQL 集成用例的两条编写纪律（2026-09-16 补）

范围界定里的第 1 条（pytest 对接 MySQL）已由 H7b 落地
（`backend/tests/test_mysql_integration.py`，`-m mysql`）。踩到的两个坑与用法无关、
只与**隔离级别**有关，写在这里是因为它们**在 SQLite 上完全复现不出来** ——
不记下来，下一个写 MySQL 用例的人会再花一轮 CI 才明白。

### 1. 断言终局必须换**新会话**读（REPEATABLE READ 的旧快照）

MySQL 默认隔离级别是 **REPEATABLE READ**，而 SQLAlchemy 的 `Session`
**不会自动提交纯读**：一个会话做过 `SELECT` 之后事务就一直开着，后续读都落在
**同一个快照**上。于是「A 会话写并提交、再用 B 会话读」会读到 A 提交**之前**的数据，
表现为"刚写进去的那行没了"。

- 反例（H7b 首跑 CI 红）：`db` 在读过一次作业后事务未提交，它的快照早于
  `stale_db` 的提交 ⇒ 用 `db` 读，看不见那行 `abandoned` 留痕，
  报错是 `作废未留痕: [('succeeded', None)]` —— **看起来像没写进去，实际是眼睛是旧的**。
- 正解：把写过/读过的会话都 `close()`，终局用**新开的会话**读。
- 失败信息里同时打印"写入方自己读到的内容"，下次再缺痕才能一眼区分
  「没写进去」与「读的是旧快照」。

### 2. 本机没有 MySQL/Docker 时怎么验并发缺陷

用「两个 `Session` 交替 `SELECT → UPDATE`」重放并发窗口：同一段 SQL，
先 A 读、再 B 读、再 A 更新、再 B 更新，看**第二次 UPDATE 的 `rowcount`**
是不是 0。判据与底层是哪个库无关：**守卫正确 ⇒ 必为 0**。
（H7b 加守卫前实测 `(1, 1)`，加之后 `(1, 0)`。）

真并发下的结论**仍以 CI 的 `pytest-mysql` 为准** —— 本机这步只用来复现与定位，
不用来宣布"并发已验证"。
