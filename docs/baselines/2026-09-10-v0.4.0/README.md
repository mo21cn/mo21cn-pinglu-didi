# A 阶段验证基线报告

执行日期：2026-09-10。范围：恢复 v0.4.0 的验证环境并盘点演示场景，未实施 B 阶段缺陷修复。

## 结论

已在仓库独立 Python 3.12.13 环境复现 **117 passed**，后端语句覆盖率 **90%**，ruff 通过。mypy 为 **82 errors / 19 files**，保留为当前冻结代码在本次工具版本下的类型检查基线；不将其描述为全绿。12 个小程序 JavaScript 文件语法检查通过。

两份需求文档按用户决定视为已通过 HO 评审；本版本范围以 v0.4.0 实现为准，未实现能力不列为欠交付项。

## 代码与资料基线

| 项目 | 核验结果 |
| --- | --- |
| 冻结提交 | e1074b435db3adf4971191b33992917419700bbc |
| 远端 main、develop、v0.4.0 | git ls-remote 只读核验，三者均指向上述提交 |
| 本地 main | 保留初始化提交 581def0；未强行同步或覆盖 |
| 工作分支 | 已从 v0.4.0 建立 codex/baseline-a |
| 业务代码、原测试、小程序代码 | 与 v0.4.0 无差异 |
| 原 Word 文档 | 保留原文件及未跟踪状态；来源为仓库根目录，已记录 SHA256 |
| 原项目虚拟环境 | 保留；新环境在仓库 .venv，未修改原环境 |

两份文档和原测试数据库的前后 SHA256 相同。原开发数据库被其他进程占用，未能获取文件哈希；只能确认前后大小及修改时间相同。本次验证使用内存数据库，没有连接这两个本地数据库，也没有启动或停止既有服务。

资料指纹及保存证据：[执行前](sources-before.json)、[执行后](sources-after.json)。

## 环境与可重复命令

- 新环境：仓库根目录 `.venv`，Python 3.12.13，与 CI 的 Python 3.12 主次版本一致。
- 安装来源：原有 `backend/requirements.txt`，共 42 个包，未新增产品依赖。
- 版本核对：42 个包与此前项目环境的包名及版本全部一致；原环境 Python 为 3.13.14。
- 依赖兼容性：`uv pip check` 通过。
- 本次版本快照：[installed-versions.txt](installed-versions.txt)。该文件用于重建本次验证环境，不替代项目依赖范围策略。

在仓库根目录执行以下 PowerShell 命令复跑已安装环境：

```powershell
& .\.venv\Scripts\python.exe .\scripts\verify_baseline.py
```

若需新建同类环境，可使用已安装的 Python 3.12 与 uv：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'tmp\uv-cache'
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r docs/baselines/2026-09-10-v0.4.0/installed-versions.txt
uv pip check --python .venv/Scripts/python.exe
```

仅在 `.venv` 不存在时执行新建命令，保留已有环境。安装需要包仓库网络访问。

[验证脚本](../../../scripts/verify_baseline.py)强制 APP_ENV=test、DATABASE_URL=sqlite://、WECHAT_MOCK=true、LLM_MOCK=true；覆盖真实服务凭据与地址，并阻断 httpx 的网络传输。TestClient 继续使用进程内传输。实测外部 HTTP 尝试为 0，证据见 [isolation.json](isolation.json)。该防护针对当前业务使用的 httpx，不宣称是通用网络沙箱。

原 `.env.local` 中有模型配置，本次没有调用真实模型，也没有把任何密钥值写入报告。验证脚本只在子进程内设置测试环境，完整运行日志写入 `tmp/baseline-a`；本报告目录保存首次运行的证据副本。

## 检查结果

| 检查 | 本次结果 | 证据 |
| --- | --- | --- |
| pytest | 117 passed，21.73 秒，4 warnings | [pytest.log](pytest.log) |
| coverage | 90%，1967 条语句，196 条未覆盖 | [pytest.log](pytest.log) |
| ruff app/tests | 通过 | [ruff.log](ruff.log) |
| mypy app | 82 项错误，涉及 19 个文件，共检查 52 个文件 | [mypy.log](mypy.log) |
| 新增验证脚本 lint | 通过 | 执行 `python -m ruff check scripts/verify_baseline.py --no-cache` |
| 小程序 JavaScript | 12 个文件 `node --check` 全部通过 | 本次工具输出；不代表页面或真机验收 |
| 依赖一致性 | 42 个包兼容；与原项目环境版本一致 | [版本快照](installed-versions.txt) |

测试数量：Agent 20、auth 9、cargo 13、health 1、match 12、order 16、payment 14、port 20、ship 12，共 117。模块分布以本次实际收集为准，不沿用旧版本日志的分组数字。

类型问题分布：未标注类型 51、泛型缺少参数 15、参数类型不匹配 9、联合类型属性 4、属性错误 2、返回值类型 1。现有 CI 使用 `mypy app || true`，因此此前“CI 通过”不等于类型检查通过。本阶段记录基线，不修改业务代码消除这些错误。

4 条警告来自 TestClient/httpx、AnyIO 别名和 FastAPI startup 事件弃用提示。本阶段不新增依赖或迁移框架 API。

总结果与运行时信息：[summary.json](summary.json)。验证脚本退出码由 pytest 与 ruff 决定，mypy 单独记录，沿用当前 CI 的非阻塞策略。

## 有限演示验收清单

下表为已有能力映射及后续 B—E 阶段的操作清单，**不是本次已完成的真机验收记录**。所有业务 API 均带 `/api/v1` 前缀。

| 场景 | 已有页面或 API | 准备方式与边界 |
| --- | --- | --- |
| 登录、角色工作台 | pages/index/index；auth/login、auth/switch-role、auth/me | 三角色预置 Mock 用户；不新增角色申请流程 |
| 货源草稿、发布、列表 | pages/shipper/shipper；cargo/shipments | 测试货源；合法港口、未来日期；现有测试覆盖接口 |
| 船舶备案、船东查看 | pages/owner/owner；ship/registry | 待审核与已审核船舶各一；审核可由现有 API 准备 |
| 港口泊位、待审预约 | pages/port/port；port/berths、port/appts | 使用现有 API 准备预约；不补建船东申请页面 |
| 船货撮合 | pages/trade/match/match；match/cargos/{id}/ships、match/ships/{id}/cargos | 已发布货源与已审核船舶；船东找货保持现有只读行为 |
| 下单、模拟支付、履约 | pages/trade/orders/orders；order/orders、payment/payments | 货主下单付款、船东启运、货主签收；不涉及真实资金 |
| 撤单退款状态联动 | 同一订单页面与 API | 支付前关闭、模拟支付后退款；并发核验留给 B/D |
| 一句话货源解析 | 货主工作台；agent/cargo-parse | Mock 解析返回草稿，人工确认后才提交 |
| 客服与 RAG | pages/assistant/assistant；agent/assistant | 全角色问答；常规验证用 Mock，真实模型本次未测 |
| 合同草稿与风险 | 订单页合同弹层；agent/contract/generate | 订单参与方，展示草稿和风险；不增加电子签 |
| 消息、我的及底部入口 | pages/message/message、pages/mine/mine；四个 tabBar | 两页为占位能力，只验导航与真实文案，不新增通知/个人中心工作流 |

账号来源：[seed_demo.py](../../../backend/scripts/seed_demo.py)，Mock code 为 `seed-shipper`、`seed-owner`、`seed-port`；`e2e_smoke.py` 使用 `e2e-shipper` 和 `seed-owner`。这些是联调标识，不是生产账号凭据。

种子脚本意图创建 2 个货源、2 条船、1 个泊位，不创建预约。其调用地址固定为本机 8000，且会写入业务数据、包含固定日期及固定泊位编号；本次没有运行，后续只能对明确隔离的演示实例执行，不能把重复执行等同于幂等重置。

现有 [e2e_smoke.py](../../../backend/scripts/e2e_smoke.py)提供完整履约与撤单退款两条 HTTP 冒烟流程；本次复跑的是隔离 pytest，不是该 HTTP 脚本，更不是微信端到端测试。

## 部署资源盘点

| 资源 | 当前证据 | 后续处理 |
| --- | --- | --- |
| 小程序 AppID | project.config.json 是占位值 | E 前配置实际项目身份 |
| 微信 AppSecret | 本机 .env.local 未发现配置 | 配合实际登录联调；本次只跑 Mock |
| 模型配置 | 本机存在 API Key 与模型地址配置 | 仅确认存在，未确认额度或可用性，未真实调用 |
| 小程序请求地址 | 固定 http://127.0.0.1:8000 | E 阶段配置目标服务地址 |
| 目标服务器、域名、证书 | 当前材料无法确认是否已具备 | 记录为待核验，不宣称未购买或不可用 |
| 小程序开发者权限、真机 | 当前无法确认 | D/E 阶段验收前落实 |
| Docker/MySQL/Redis | 有配置文件；未验证目标实例 | E 阶段连接配置及实例验证；B/D 另做 MySQL 并发验证 |
| 微信支付商户配置 | 本机 .env.local 未发现配置 | 真实支付不在本版范围，不作为 A 阶段阻塞 |

## 后续安排

A 阶段完成：可以进入 B，先复现并修复已有关键缺陷，再推进 UI 方案与页面打磨。MySQL 并发、真实模型、小程序真机和部署验收仍是后续任务，不能由本次 117 个测试替代。

工程投入粗估：B 2—4 工作日，C 3—5 工作日，D 2—3 工作日，E 1—2 工作日，合计约 8—14 工作日；这是基于当前页面规模的计划区间，不是已确认交期。并发缺陷尚未复现，服务器/AppID 等外部等待不包含在该估算中，B 完成后更新。

本阶段新增验证脚本与证据文档，未提交或推送 Git，未修改业务功能，未执行部署。
