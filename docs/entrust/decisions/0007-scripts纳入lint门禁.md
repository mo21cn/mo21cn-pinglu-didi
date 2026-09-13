# DR-0007：`backend/scripts/` 纳入 ruff 门禁（`mypy` 有意排除）

- 日期：2026-09-13
- 提出人：`chore/scripts-lint-gate`
- 决策人：HO
- 状态：**已采纳**
- 关联：DR-0004（工具链版本锁定）；PR #50（开启 `ruff format --check` 门禁）；规范四「CI 门禁」——禁止对必需检查设 `continue-on-error`

## 背景

`backend/scripts/` 长期处于一个夹缝位置：

- **CI 会执行它** —— `frontend-e2e` job 里跑 `python scripts/seed_demo.py` 铺演示数据；
- **门禁管不到它** —— `ci.yml` 的 `Lint` 与 `格式门禁` 两步都只扫 `app tests`。

这不是推测，是 2026-09-13 实际暴露出来的：用 `ruff format --check .` 做本地预演时，
`scripts/` 下 4 个文件（`bind_demo_to_openid.py` / `e2e_smoke.py` / `seed_contract_cases.py` /
`seed_demo.py`）报待格式化，而 CI 全绿。这 4 个文件最后修改时间**都早于 PR #50 的全量格式化** ——
也就是说 PR #50 声称的"全量"实际只覆盖了 `app tests`，恰好与当时的 CI 范围一致。

两个具体风险：

1. **看起来像环境问题**。本地红 / CI 绿极易被误判成工具链版本或网络代理故障，
   而真实原因是**两边扫描范围不同**。（PR #50 之所以专门写了 DR-0004 锁定 ruff 版本，
   正是因为"随机变红"已经训练出怀疑门禁的习惯 —— 范围不一致会喂出同一种习惯。）
2. **纪律上的自相矛盾**。一边禁止对必需检查设 `continue-on-error`，
   一边让 **CI 真正跑起来的脚本**游离在所有检查之外。

## 候选方案

| 方案 | 描述 | 优点 | 缺点 | 风险 |
| --- | --- | --- | --- | --- |
| **A** | `scripts/` 纳入 `ruff check` + `ruff format --check`，一次格式化到位；`mypy` 仍不含 | 消除范围不一致；CI 执行的代码有同一标准；改动机械、可证明语义不变 | 一次性 421 增 / 154 删的 diff（纯排版） | 低：`ruff format` 是规范化 formatter，可用 AST 比对证明语义等价 |
| B | 维持现状（`scripts/` 不纳入） | 零改动 | 夹缝继续存在；下次仍会出现"本地红 CI 绿"并浪费排查时间 | 中：会反复诱发对门禁的不信任 |
| C | 同时把 `scripts/` 纳入 `mypy`（`strict = true`） | 类型覆盖完整 | 需改约 **31 处**（`no-untyped-def` / `no-any-return` / `type-arg`）；这些脚本**刻意写得松**（`json` dict 拼装、SQLAlchemy `Row` 直接索引），补注解的收益与成本不匹配 | 中：门禁被大量"为了过检查"的弱注解稀释，反而降低信号密度 |

## 决策

选择：**方案 A —— `scripts/` 纳入 ruff 门禁，`mypy` 有意排除。**

理由：

1. **判定标准是"这份代码是否在 CI 的执行路径上"**，而不是"它是不是生产代码"。
   `scripts/seed_demo.py` 会在每次 `frontend-e2e` 里真实运行，它坏掉 CI 就红 ——
   那么它至少应当满足与 `app`/`tests` 同样的**机械可判定**标准（lint + 格式）。
2. **成本可证明为一次性**。`ruff format` 是规范化 formatter：同一输入必得同一输出，
   且不改变语义。本轮用 **AST 逐字比对**证明了 4 个文件格式化前后语法树完全一致
   （见"验证方式"），所以这是一次可审计的排版提交，没有行为风险。
3. **`mypy` 的排除是显式决策，不是漏配**。差别在于"机械标准"与"类型标准"的性价比：
   - lint / 格式：**机械、零语义风险、一次到位** → 值得纳入；
   - `strict` 类型：需要为演示脚本补充大量注解，其中多数是为过检查而写；
     而这些脚本的类型风险本身很低（不参与运行时服务、不进生产镜像）。
   在 `ci.yml` 与 `AGENTS.md` 里都写明这一点，避免后人误以为漏配。
   **触发条件**：若将来这些脚本进入生产路径（被服务端导入或用于正式数据操作），
   再单独立 DR 把 `mypy` 一并纳入。

## 影响

- 后端（工具链配置）：
  - `.github/workflows/ci.yml`：`ruff check app tests` → `ruff check app tests scripts`；
    `ruff format --check app tests` → `ruff format --check app tests scripts`；
    两步各加注释说明范围理由。`mypy` 保持 `app migrations migrate.py` 并注明有意排除 `scripts/`。
  - `backend/scripts/` 4 个文件：`ruff format` 全量重排（**单独一个 `style` 提交**，
    与配置改动分开，便于 review 时区分"范围变化"与"排版变化"）。
- 前端：无影响。
- 数据库迁移：无影响。
- 功能开关：无影响。
- 对旧流程的影响：无。`scripts/` 不参与运行时路径，格式化不改变任何行为。
- 需要同步修改的文档：
  - `AGENTS.md`：常用命令里的 lint / 格式范围改为 `app tests scripts`。
  - `docs/01-development-environment.md`：常用命令表同步。
  - 本决策记录。

## 回退方式

- **代码回退**：把 `ci.yml` 两处的 `scripts` 去掉即可；已格式化的 4 个文件**不必回退**
  （格式化后的形态同时也是通过 lint 的形态，保留无害）。
- **数据兼容性**：无。
- **外部能力**：无。

**注意**：回退本决策等于接受"CI 会执行的代码不受门禁约束"，以及背景中描述的
"本地红 / CI 绿"排查成本 —— 需要在回退说明里写明这一点。

## 验证方式

| 命令 | 预期结果 |
| --- | --- |
| `ruff format --check app tests scripts` | 退出码 0，无待格式化文件 |
| `ruff check app tests scripts` | 退出码 0 |
| `mypy app migrations migrate.py` | `Success: no issues found`（不含 `scripts/`） |
| `pytest` | 全绿（与本次改动无关，作回归确认） |
| AST 比对（旧版 vs 格式化后） | 4 个文件的语法树**逐字一致** → 格式化语义中性 |
| CI job `backend` | `Lint`、`格式门禁`、`类型检查` 三步均绿 |

**AST 比对方法**（本轮实际执行）：

```python
old = git show HEAD:<file>          # 格式化前的版本
new = open(<file>)                  # 格式化后的版本
assert ast.dump(ast.parse(old)) == ast.dump(ast.parse(new))
```

未执行项及原因：无。本机已跑 lint / format / mypy / pytest 全量验证，
CI 由 GitHub Actions 在推送后自动执行。
