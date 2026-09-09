# 更新日志（Changelog）

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
