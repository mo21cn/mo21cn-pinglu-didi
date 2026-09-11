# 更新日志（Changelog）

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

## [0.5.1] - 2026-09-12

对外交付就绪度补丁：**发货方式选择弹窗** + **clone 即得完整演示态** + **CI 前端门禁补齐**。
不改业务能力 —— 修的是「甲方拿到会踩的坑」：默认分支的快速开始（SSH 克隆地址 / 缺种子步骤，第一步就卡）、
演示文档的可转发性、以及前端 574 条断言此前**完全不在 CI 里**。

### 新增

- **发货方式选择弹窗**：点击 tabBar 中间「+发货」进入发布货物页（二级页）后先弹出，
  1:1 复刻设计稿（橙/蓝渐变卡片 + 白圆纯 CSS 矢量图标 + 单选圈「默认」+ 说明文案）：
  - **自主发货** → 关闭弹窗，留在本页自行填写发布；
  - **委托发货** → 进入通用占位页 `pages/preview/preview`（一行小灰字「功能预览，即将开放」，
    可复用于其它未开放功能）；
  - 点遮罩可关闭，再次点「+发货」重新唤起。
- **纯前端实现**：不涉及任何后端改动、零接口调用；该批次未单独升版，随本 v0.5.1 补丁版一并发布。

### 工程 / CI

- **CI 补齐前端门禁**：原 `.github/workflows/ci.yml` **只有 `backend` 一个 job**，
  前端 4 个校验脚本（共 574 条断言）完全不在 CI 中、仅靠本机手工执行 —— 改前端 push 上去 CI 会绿，
  但断言其实没跑过。现新增两个 job：
  - `frontend-static`：`verify_miniapp`（20 JSON / 15 页面 / 38 路由 / 188 事件）
    + `verify_ui_interactions`（214 断言），零依赖、秒级；
  - `frontend-e2e`：真起后端（`APP_ENV=development`，入库的 `.env.development` 已内置
    `WECHAT_MOCK` / `LLM_MOCK`，无需任何 Key 或外部服务）→ `seed_demo.py` 铺数据 →
    `verify_login_flow`（22 断言）+ `verify_frontend_e2e`（176 断言）。
    后端与校验刻意放在**同一个 shell** —— 跨 step 的后台进程会被 runner 回收，分 step 必失败。
  - `verify_miniapp_device`（162 断言真机走查）依赖微信开发者工具模拟器，**无法在 CI 运行**，仍由本机执行。

### 修复

- **`verify_login_flow` 的「非空」断言名实不符**：三条断言（船东订单 / 货主订单 / 货主「我的货源」）
  名为「非空」，实现却写死 `>= 10` / `>= 5` —— 那是照本机累积数据（12 单 / 11 货）拍的数字，
  在**空库 + seed** 的干净环境（CI 首跑、甲方 clone）必然误报为 FAIL。已改为 `>= 1`，
  与断言声明的意图（脚本头部 ③「身份稳定 → 我的货源不是永远为空」）一致，并加注释防止被改回。
  该问题在把 CI 补齐后立即暴露 —— 正是加这道门禁的价值。

### 文档 / 配置

- **「clone 即得完整演示态」链路修复**（面向甲方评审）：
  - `backend/.env.development` 显式声明 `LLM_MOCK=true`——开发环境智能体走内置规则模板，
    **无需任何 Key**、零网络，四个智能入口（客服 / 一句话发货 / 合规预检 / 统一路由）全部可用且输出可复现；
  - `README` 快速开始纠错：克隆地址由 SSH 改 **HTTPS**（仓库为 public，甲方无需配 SSH Key）、
    移除无效的 `cp .env.example .env`（应用只读 `.env.{APP_ENV}` 与 `.env.local`）、
    补上 `python scripts/seed_demo.py` 种子数据步骤（不跑则各列表为空），并补「环境变量加载规则」与「演示身份」说明；
  - `backend/.env.example` 补齐智能体段（`LLM_MOCK` / `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`），
    与 `config.py` 默认值对齐。
  - 注：空 `LLM_API_KEY` 本就会触发 `llm.py` 的自动降级，故本项**不改变既有运行时行为**，
    属把隐式兜底改为显式声明 + 文档纠错，避免`README` 误导评审方。
- **`docs/汇报演示脚本.md` 可安全转发修正**（原为内部主讲人手稿，有 3 处不适配转发）：
  - 第 26 / 43 行硬编码本机 venv 路径 → 改跨平台写法（`python scripts/...` + `.venv` 提示）；
  - 头部版本号 v0.4.0 → **v0.5.0**，Agent 口径对齐 CHANGELOG 的「领域覆盖 5/5」；
  - §1.3 `urlCheck` 口径**方向反了**：原文称「`project.config.json` 里是 `"urlCheck": true`，
    这正是此前走查不通过的根因」，实际该文件第 8 行已是 `false`（公共配置即默认关校验）。
    已改写为事实描述，并说明被忽略的 `project.private.config.json` 真正需要自带的是
    `appid: "touristappid"`（游客态，不必有真 appid）；
  - §2 第 ③ 步补上**必经交互**「发货方式选择弹窗」（原文档完全未提，演示当天必然遇到），
    含自主/委托/遮罩三条分支与「船东角色同一位置是发布空船、不弹此窗」的提示；
  - §5.5 汇报前自检由「只跑 1 个脚本」扩为四条命令清单，并补 CI 覆盖情况与 155/176 数字口径。
- **`backend/scripts/{seed_demo,e2e_smoke}.py`** docstring 里硬编码的本机 venv 路径 → 通用写法。

## [0.5.0] - 2026-09-12

UI V2 全量改版 + 交易三级页闭环 + **Agent 五领域补齐 5/5** + 第三方审计修正。
自 0.4.0（演示版框架）以来的主旋律：**前端体验从"能演示"打磨到"可交付"**。

### 新增

- **UI V2 全量改版**（PR #19/#20）：
  - 自定义 tabBar（`tabBar.custom=true` + `custom-tab-bar` 组件）：找船/找货
    第 1 位随角色变化、中间凸起位发布入口（货主发货物 / 船东发空船）
  - 品牌更名「船好多」，蓝紫渐变令牌（#3B5BDB → #7048E8）贯穿全站
- **交易三级页 L3**（PR #21–#25）：
  - 智能合同三级页（长按快览弹层 + Markdown 渲染 + 风险分级）
  - 支付详情页（时间轴 + 金额取支付单锁定值 + 面议拒付引导）
  - 撮合结果页（四项得分拆解 + 未入局原因计数）与港口域档期甘特
- **E2E 工具链**（PR #26/#27）：`verify_frontend_e2e`（真后端载荷驱动
  页面逻辑）+ `verify_miniapp_device`（miniprogram-automator 真机走查）
- **Agent 五领域补齐 5/5**（PR #38）：
  - F14 货源解析前端接线（`assistant` 三模式：客服/解析/搜索 + 草稿回填）
  - F17 合规初筛（2 端点 / 9 条确定性规则 / 零 LLM / 不阻断写入）
  - F18 商务条款类风险 R6–R9（滞期费/保险/违约金/在途不可抗力）
  - F20 统一意图路由 `/agent/route`（合同>合规>货源解析>客服兜底）
- **智能搜索页面化**（PR #39）：与智能客服同款对话外壳的第三模式
  `mode=search`，结果按意图渲染解析卡/合规卡/回答气泡

### 修复（第三方审计修正，22 项）

外部静态审计（结论 B+，P0=0 / P1=2 / P2=9 / P3=13）逐项核实后修正：

- **P1-1** 客服多轮上下文 `slice(-10)` 对齐后端 `max_length=10`
  （原第 6 轮发送起携带 11+ 条历史被 422 拒收，提示语无法定位）
- **P2-1** 发布空船页「选船」改 port-picker 弹层
  （原 `wx.showActionSheet` 承载无上限数据源，船队 >6 艘静默失败）
- **P2-2** 5 个页面补 `enablePullDownRefresh`（原下拉刷新手势静默无效）
- **P2-3** 新增 `utils/dates.js` 本地日期工具，替换 7 处
  `toISOString().slice`（UTC 在东八区 0–8 点会取到「昨天」）
- **P2-4** 发布货源成功后保持提交态至跳转（防 700ms 窗口内重复建单）
- **P2-5/P2-9** 港口数据源与标签全站统一 13 港全量口径
  （原 port 页仅 10 港、owner 筛选器短标签两套文案）
- **P2-6** 泊位创建补提交守卫；**P2-7** request.js 401 注释对齐实现
- **P3 批量清理**：常量收敛 `utils/constants.js`（8 文件、修正 tanker
  文案漂移「油船→液货船」）；死代码（app.js 路由映射/死样式）；消息
  `wx:key` 改自增 id（同毫秒键重复）；`getUser` 容错；泊位参数 null
  兜底；15 处静默吞错加可定位日志；列表页驳回改填原因（与详情页统一）；
  合同页下拉不再重复触发 LLM 生成；我的页未登录中性占位；首屏双探活
- **静态防线补 3 条**：showActionSheet 动态表达式白名单拦截 /
  onPullDownRefresh⇔json 开关一致性 / 业务日期禁 `toISOString().slice`

### 已知边界

- P2-8（订单页状态栏留白）在审计落地前已被 PR #39 顺带修复
- P1-2 上线前收口项按计划保留占位：真实 AppID / HTTPS 域名 /
  `DEV_STABLE_IDENTITY=false` / 微信支付真实渠道 / JWT 密钥 secrets 化
- `lazyCodeLoading` 试开后回滚：与 miniprogram-automator 不兼容
  （页面节点树找不到、走查 FATAL），待框架适配后再评估
- 货源大厅仍为演示数据（后端无「公开货源」接口）；合同不落库（TODO-03）

### 验证

- `ruff` 全过｜`pytest` **142 passed**｜`mypy app` 0 error（后端本批零改动，作回归）
- `verify_miniapp` 19 JSON / 14 页面 / 46 路由 / 185 事件
- `verify_ui_interactions` **199 / 0**（审计前 197 → +2）
- `verify_frontend_e2e` **176 / 0**｜`verify_login_flow` **22 / 0**
- 真机走查 **140 / 140 · 0 运行期 console.error**

## [0.4.0] - 2026-09-10

RAG 知识检索 + 底部客服对话入口（F12-F13）：Agent 从 prompt 硬编码转检索式注入，小程序底部 tabBar 接通客服。

### 新增

- **F12 RAG 知识检索**（PR #15）：
  - `knowledge.py`：知识库 chunk 化 25 篇（13 港一港一篇 + 角色/主流程/
    发布/支付/合同/五货类/撮合规则/船舶认证），支持单港精确命中
  - 检索引擎 = 字符 bigram TF-IDF 余弦（纯标准库、零外部依赖、
    确定性可测、预计算索引微秒级查询）
  - assistant prompt 拆为规则 prompt + RAG 动态拼装（top-4 检索注入
    「参考资料」节；召回 <2 自动回退 roles+flow-main 核心文档）
  - EMBEDDING_PROVIDER 为向量检索替换点（后续接真实 embedding 仅
    换 search 实现层，文档层与拼装逻辑零改动）
- **F13 底部客服对话入口**（PR #16）：
  - `pages/assistant/` 四件套：聊天气泡 UI（user-select 可复制、换行保留）
    + 5 个高频问句快捷 chip + Storage 持久化（chat_history_v1，最多 50 条）
    + 多轮上下文（最近 6 轮）+ 自动滚到底 + 错误态降级
  - `app.json` tabBar 4 项 = 首页 / 客服 / 消息 / 我的（首项 text
    工作台→首页，与角色卡入口语义对齐）
  - 顶部说明栏明示「纯只读，AI 不代替您执行任何写操作」
  - 后端零改动：F9/F10 已落定 assistant 只读 + AgentCall 审计
- chat_json 网关 mock_content 注入参数（PR #11 起）：各 Agent 在
  LLM_MOCK 下输出与真实模式同构，CI 可全链路验证

### 演示版范围（本里程碑起）

主功能框架（前后端）已完整交付：**5 个核心域**（auth/cargo/ship/
port/match/order/payment）+ **3 个 Agent**（货源解析/客服导购/智能
合同）+ **1 个 RAG 知识检索层** + **完整小程序入口**。后续进入
**前端 + UI 打磨 + 测试**阶段。

- 合规初筛：暂不开发独立 Agent，**下放至客服导购响应**（RAG 知识库
  内可承载禁运品/风险词等提示，无须独立 endpoint）
- 撮合 Stage2（泊位档期 + 空驶成本）：暂搁，匹配精度可由 Stage1
  引擎与 Agent 协同补足

### 验证

- 全量 117 tests passed，ruff 全绿，CI 双绿
- 真实 DeepSeek 冒烟：RAG 检索注入可观测、回答严格基于检索资料、
  运价等未覆盖信息正确拒答
- F12+F11+F13 协同：液货咨询回答自动带「合同标注风险」自然衔接

### 已知限制

- 演示模式（LLM_MOCK=false + DeepSeek 真实调用）仅本机联调
- 合同草稿无签署/存证流程（后续接电子签）
- assistant 检索为词法（bigram TF-IDF），未接真实向量
- 小程序 UI 联调仍需用户侧真机/模拟器验证
- 撮合 Stage1 引擎，无泊位档期/空驶成本建模
- fix/e2e-ui 分支（tabBar 旧版）未合入

## [0.3.0] - 2026-09-10

智能合同 Agent（F11）：中风险场景三重防线范式落地，Agent 版图 3/5。

### 新增

- **F11 智能合同 Agent**（PR #13）：
  - `POST /agent/contract/generate`：订单 → 运输合同草稿 + 风险点提示，
    订单参与方限定（非参与方/已撤销订单/订单不存在均 400）
  - **确定性内核**（contract.py）：合同核心条款（甲乙方/货物/航线/运费
    金额/日期/船舶证书）由订单链数据模板渲染——金额、日期、港口
    **零 LLM**；风险点 100% 规则引擎（未支付/面议未锁价/装货日期临近
    或已过/证书临期或过期/液货危险品），扫描订单事实不发明风险
  - **LLM 严格受限**：仅产出不可抗力/违约责任/争议解决/安全环保四条
    补充条款，prompt 严禁具体金额日期港口船名人名；编号由拼接层统一
  - **无法律效力**：草稿不落库 + 显著免责声明（Agent 无直写，工程
    底线 2）；审计落库 AgentCall（agent_name=contract，工程底线 3）
  - 小程序订单页「生成合同」入口：底部弹层展示草稿全文 + 风险分级
    徽标（高/中/低）+ 免责提示
- chat_json 网关 mock_content 注入参数（PR #11）：各 Agent 在
  LLM_MOCK 下输出与真实模式同构，CI 可全链路验证

### 验证

- 全量 112 tests passed，ruff 全绿，CI 双绿
- 真实 DeepSeek 冒烟 3.0s：面议单 4 风险全命中，LLM 补充条款
  零具体数字

### 已知限制

- 合同草稿无签署/存证流程（后续接电子签）
- assistant 知识库为 prompt 硬编码（RAG 替换点未启用）
- 小程序 tabBar/导航修复（fix/e2e-ui）待真机验证后合入
- 生成式 API 未做幂等/限流（单用户维度）

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
