# 更新日志（Changelog）

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-09-10

Agent 底座与首批智能体场景（F8-F10）：交易主链路延伸至小程序端 + LLM 智能化底座就绪。

### 新增

- **F8 小程序交易入口**（PR #8）：撮合页（货主找船可下单 / 船东找货只读，
  评分卡片 + 四项得分拆解 + filter_stats）、订单页（角色视角 + 状态过滤，
  支付/启运/签收/撤单全操作）、支付流（静默探测→创建→弹窗确认→mock-pay）
- **本地 E2E 联调工具链**（PR #9）：三角色种子脚本（固定 Mock code）、
  全链路冒烟脚本（场景 A 完整履约 + 场景 B 撤单退款联动）、
  小程序 dev_login_code 联调开关、DeepSeek 接入配置（.env.local 叠加，
  Key 不入库）
- **F9 Agent 底座**（PR #10）：
  - LLM 网关 `chat_json`：DeepSeek OpenAI 兼容协议（httpx 异步）、
    JSON 强约束输出、`LLM_MOCK` 规则模板模式（CI 零网络）、
    错误六分类降级（timeout/network/auth/rate_limit/bad_request/bad_response）
  - AgentCall 审计表：每次 Agent 调用留痕（用户/Agent 名/输入输出摘要/
    耗时/成败），工程底线 3 落点
  - 货源解析 Agent：`POST /agent/cargo-parse` 自然语言→结构化草稿，
    pydantic 严格校验 + 13 港合法性过滤，Agent 无直写（草稿不落库，
    工程底线 2）
- **F10 客服导购 + 一句话发货**（PR #11）：
  - 客服导购 Agent：`POST /agent/assistant` FAQ/航线/用法问答，
    纯读零直写、全角色可用，平台知识库注入 system prompt（RAG 替换点），
    多轮上下文
  - 小程序发布货源页"一句话发货"：草稿回填（picker 索引同步）+
    待确认字段提示，人工核对后提交

### 修复

- config.py env_file 多文件叠加（.env.{env} + .env.local，
  PR #9 中该改动意外丢失，PR #10 补齐）

### 工程质量

- 测试 106 例全绿（新增 agent 9 例：cargo-parse 6 + assistant 3）
- 真实 DeepSeek 冒烟通过（3.8s 首调 / 2.8s 客服；不编造缺失字段、
  拒答无依据行情）
- 三条工程底线全程持有：确定性内核零 LLM、Agent 无直写、全链路留痕

### 已知限制（计划内后续迭代）

- 登录/支付仍为 Mock 模式（真实微信生态接入待 appid）
- 撮合 Stage2（泊位档期约束、空驶里程成本）待开发
- 五领域 Agent 已落地 2 个（货源解析/客服导购）；合规初筛、运营分析、
  智能合同待开发
- 小程序 tabBar/导航 UI 修复（fix/e2e-ui 分支）待真机验证合并
- RAG/向量库、LangGraph 多 Agent 编排待接入（当前为 prompt 注入式知识库）

## [0.1.0] - 2026-09-10

首个 MVP 候选版本：货-船-港三态撮合交易主链路后端闭环（F1-F7）。

### 新增

- **F1 三角色登录**（PR #1）：微信 code2session + Mock 登录、JWT 鉴权、
  货主/船东/港口三角色绑定与切换、角色工作台
- **F2 货域**（PR #2）：发货单实体与状态机（draft→published→cancelled）、
  平陆运河 13 港代码体系
- **F3 船域**（PR #2）：船舶备案与审核（pending_verify→verified/rejected）、
  verified 改关键信息自动降级重审
- **F4 港域**（PR #3）：泊位实体（吨位/吃水/船型/并发容量约束）、
  泊位预约五态状态机、防超卖三重设计（pending 不占档期 + FOR UPDATE
  行锁重叠计数 409 + 撤销即释放）
- **F5 撮合引擎 Stage1**（PR #4）：确定性纯函数内核（不经 LLM）、
  货-船双向撮合、硬约束 CSP（verified/证书/载重/船型兼容矩阵）、
  多目标评分（载重利用率 40 + 船型适配 20 + 船籍港就近 20 + 证书余量 20）
- **F6 订单域**（PR #5）：订单状态机（matched→shipped→completed /
  cancelled）、防重复出单（FOR UPDATE 锁货源行）、出单前置复用撮合
  硬约束、撤单释放货源回撮合池
- **F7 支付域**（PR #6）：支付单状态机（pending→paid / closed /
  refunded）、一订单一支付单、回调幂等、撤单联动结算（已付退款/
  待付关闭）、金额锁定、渠道抽象（mock/wechat_mp）

### 工程质量

- 测试 97 例全绿（auth 10 + cargo 11 + ship 13 + port 20 + match 12 +
  order 16 + payment 14 + health 1）
- CI：GitHub Actions（ruff + pytest），push/pull_request 双触发
- 分支：Git Flow（main / develop / feature/* / release/*）
- 提交：Conventional Commits（husky v9 + commitlint）

### 已知限制（计划内后续迭代）

- 登录为 Mock 模式（真实联调需小程序 appid + WX_APP_ID/SECRET）
- 支付为 mock 渠道（真实微信支付回调、超时关单、分账提现待接入）
- 撮合 Stage2（泊位档期约束、空驶里程成本）与五领域 Agent 待开发
- 小程序端交易入口（下单/支付/启运/签收）页面待接入
