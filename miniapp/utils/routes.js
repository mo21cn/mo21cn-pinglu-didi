// 路由注册表 + 导航策略 + 页面栈预算 + 深链参数契约（R1 / ENT-015 → ENT-018）
//
// ── 为什么需要这个模块 ────────────────────────────────────────────────
// 小程序 `wx.navigateTo` 的页面栈上限是 **10 层**，超限时 `navigateTo` 直接失败，
// 而失败形态是「点了没反应」而不是报错（`utils/agent-entry.js` 里那句 fail 兜底
// 就是为它写的）。委托支线要在现有 17 个页面上继续加页面（UI-01～UI-05），
// 如果「新增页面」不需要声明任何东西就能进 `app.json`，栈深只会在某天突然撞上限，
// 而且很难反推出是哪条链叠到第 10 层的。
//
// ── ENT-018 的修订（DR-0011「修正版 B」，HO 2026-09-13 处置）──────────
// 初版把 `parents` 同时当成「界面层级」与「所有来路」用，于是产品上完全合理的
// 「工作台 ↔ 会话」双向切换在链深计算里变成环 → `Infinity`（HO 实测复现）。
// 修订后**分开建模**：
//
//   · `NAV_EDGES`  —— **导航边的唯一真相**：所有真实可达的调用关系，每条带策略
//                     （push / replace / back / reuse / reset / switchTab）。
//   · `ROUTES[p].parents` —— **层级**：只表达「静态上可以从哪一层 push 进来」，
//                     用于链深计算。**由 NAV_EDGES 里 strategy==='push' 的边派生**，
//                     不手工维护 ⇒ 不存在两份数据打架。
//   · 链深同时纳入 `replace` 边（权重 0：替换当前页，不增层）。
//     `back` / `reuse` 的往返**不构成环**——只有 `push` 会让层数增长。
//   · `kind`（tab / root / detail）**不再被当成自动清栈**：清不清栈由**实际调用的
//     导航操作**决定，不由页面类别决定。
//
// ── ENT-019 的修订：把声明层接到运行期（页面接入时暴露的三个缺口）────────
// 接入 `pages/entrust/workbench` 与 `pages/entrust/detail` 时发现，模型里已经**声明**的
// 语义在 `go()` 里没有执行路径 —— 声明与事实又分家了。三条都在本次补齐：
//
//   ① `reset` 策略**没有对应动作**。`resolveNavigation()` 只可能返回
//      switchTab / reuse / replace / push，于是 `strategy:'reset'` 的边
//      （workbench→index、detail→index）落到第 ④ 步被当成 **push** 执行成
//      `wx.navigateTo` —— 又压了一层首页，而不是 `wx.reLaunch` 重置栈。
//   ② `reset` 边**会被复用分支降级**成 `wx.navigateBack`。冷启动深链进工作台时栈里
//      只有工作台自己没有首页，退化成 push；而栈底有首页时会变成 navigateBack(2)，
//      看起来"也到了首页"，但栈没有清空 —— 与「重置栈、重走身份链路」不是一回事。
//      ⇒ 复用分支只对 push / replace / back / reuse 生效，`reset` 优先。
//   ③ 页面里没有任何一处调用 `go()` / `guardEntry()`（`miniapp/` 内对 routes 零引用），
//      也就是说初版的页面栈预算与深链契约**运行期完全没生效**。本步起两个委托页
//      改走 `go()`，并在 `onLoad` 执行入口守卫。
//
// ── 接入范围（`MIGRATED_PAGES`）────────────────────────────────────────
// 「已接入运行期治理」是一份**可被 CI 核对**的名单，而不是口头结论：
// 名单内的页面不得再出现裸 `wx.navigateTo / redirectTo / reLaunch`（由
// `verify_routes.js` 强制）。旧页面（发布 / 撮合 / 港口 / 我的 …）不在名单内，
// 按 HO 第 4 条「不要求全量改造旧页面」保持现状，逐批迁移。
//
// ── CI 强制约束（`scripts/verify_routes.js`）────────────────────────────
//   1. `app.json` 的每个页面都必须登记，反之注册表里也不得有多余项（禁止偷偷加页）
//   2. 声明链深 `chainDepth()` 不得超过 `STACK_BUDGET`
//   3. 实际导航边必须已被声明在 `NAV_EDGES` 里；push/replace 边若无代码证据必须标 `pending`
//   4. 深链参数契约：`paramSchema` 必须覆盖 `deepLink` 的强度（非空 + 类型／枚举）
//   5. `MIGRATED_PAGES` 里的页面不得再直接调用 `wx.navigateTo / redirectTo / reLaunch`
//      （必须经 `go()`），且必须真的出现 `go(` 调用 —— 防「名单写了但代码没接」
//
// ── 本模块是纯数据 + 纯函数 ───────────────────────────────────────────
// 可在 Node 里直接 `require`（CI 静态校验与行为测试要用），**模块加载期不触碰 `wx`**；
// 只有 `stackSnapshot()` / `go()` / `currentDepth()` 会用到运行时 API，且都带存在性判断。

// 页面栈硬上限（小程序平台固定值，不要改）
const MAX_STACK = 10

// 声明链深预算：留 2 层给「临时页」（如 preview 预览、assistant 浮层再叠一层）。
// ENT-018 起这个值**必须真的被运行期用到**：`go()` 在 depth >= STACK_BUDGET 时进入
// 策略链（复用 / 替换 / 提示 / 明确失败），而不是直接压栈。初版它只是 CI 的声明口径。
const STACK_BUDGET = 8

// 边的策略枚举（DR-0011 §3.2）
const STRATEGY = {
  PUSH: 'push', // 正常进入下一级（wx.navigateTo）        —— 层数 +1
  REPLACE: 'replace', // 明确允许替换的同级页面（wx.redirectTo）—— 层数 0（替换）
  BACK: 'back', // 返回栈中已有页面（wx.navigateBack）      —— 不参与链深
  REUSE: 'reuse', // 目标已在栈中且上下文相同，复用并刷新     —— 不参与链深
  RESET: 'reset', // wx.reLaunch，重置栈                    —— 目标必为 tab/root
  SWITCH_TAB: 'switchTab' // wx.switchTab，tabBar 页        —— 目标必为 tab
}

const STRATEGY_VALUES = Object.keys(STRATEGY).map(function (k) {
  return STRATEGY[k]
})

// 参与链深计算的两类边及其权重（其余策略对链深无贡献）
const DEPTH_WEIGHT = { push: 1, replace: 0 }

// 目标必为 tabBar 页 / 必为 tab 或 root 页的策略
const STRATEGY_TARGET_TAB = [STRATEGY.SWITCH_TAB]
const STRATEGY_TARGET_TAB_OR_ROOT = [STRATEGY.SWITCH_TAB, STRATEGY.RESET]

/**
 * 导航边：**所有真实可达的调用关系**，每条带策略。
 *
 * 字段：
 *   from / to  已登记页面路径（不含前导 `/`）
 *   strategy   STRATEGY 之一
 *   reason     为什么是这条策略（非 push 边必填，说明"为什么不算成进下一级"）
 *   pending    还没有代码证据时的说明（如"ENT-020 接入会话入口后生效"）。
 *              push / replace 边**没有代码证据就必须标 pending**，否则 CI 报错——
 *              这条规则是为了让「声明」与「事实」不会被混为一谈（HO 第 4 条）。
 *   note       其它补充（可选）
 *
 * ⚠️ 已知盲区（见 DR-0011 §7）：`utils/agent-entry.js` 这类**工具函数**发起的导航，
 *    其调用方页面无法静态解析；目标由变量决定的调用（如 `ROLE_META[role].page`）
 *    同样无法静态解析。两者都由 `verify_routes.js` 打印为 `[工具来源]` / `[变量目标]`
 *    备注并**不计入链深证据**，且**不要求**在此登记。
 */
const NAV_EDGES = [
  // ── 入口域 → tabBar 页 ─────────────────────────────────────────────
  // 注：`pages/index/index.js` 用 `ROLE_META[role].page` 变量跳转，目标不可静态解析，
  //     故这些边无法在此登记。**但它不再是无验证的盲区**：其取值域由
  //     `scripts/verify_tabbar_targets.js` 驱动真实页面逻辑断言（捕获的每个目标都
  //     必须落在 app.json 的 tabBar.list 内），并接入 CI frontend-static。
  //     静态侧仍保留「[变量目标]」备注（见 verify_routes.js）——那是中立记录，不是判错。
  //     switchTab 会把栈归 1，不影响链深。

  // ── 工作台（tabBar）之间的切换 ─────────────────────────────────────
  { from: 'pages/owner/owner', to: 'pages/mine/mine', strategy: 'switchTab' },
  { from: 'pages/owner/owner', to: 'pages/shipper/shipper', strategy: 'switchTab' },
  { from: 'pages/shipper/shipper', to: 'pages/owner/owner', strategy: 'switchTab' },
  { from: 'pages/shipper/shipper', to: 'pages/mine/mine', strategy: 'switchTab' },
  { from: 'pages/publish/ship/ship', to: 'pages/owner/owner', strategy: 'switchTab' },
  { from: 'pages/trade/match/match', to: 'pages/trade/orders/orders', strategy: 'switchTab' },
  { from: 'pages/trade/payment/payment', to: 'pages/trade/orders/orders', strategy: 'switchTab' },

  // ── 我的 → 各入口 ──────────────────────────────────────────────────
  {
    from: 'pages/mine/mine',
    to: 'pages/entrust/workbench/workbench',
    strategy: 'push',
    reason: '委托发货入口挂在「我的」'
  },
  { from: 'pages/mine/mine', to: 'pages/publish/ship/ship', strategy: 'push' },
  { from: 'pages/mine/mine', to: 'pages/assistant/assistant', strategy: 'push' },

  // ── 发布域 / 交易域 ────────────────────────────────────────────────
  { from: 'pages/assistant/assistant', to: 'pages/publish/cargo/cargo', strategy: 'push' },
  { from: 'pages/owner/owner', to: 'pages/publish/ship/ship', strategy: 'push' },
  { from: 'pages/owner/owner', to: 'pages/assistant/assistant', strategy: 'push' },
  { from: 'pages/shipper/shipper', to: 'pages/assistant/assistant', strategy: 'push' },
  { from: 'pages/owner/owner', to: 'pages/trade/match/match', strategy: 'push' },
  { from: 'pages/shipper/shipper', to: 'pages/trade/match/match', strategy: 'push' },
  {
    from: 'pages/publish/cargo/cargo',
    to: 'pages/trade/match/match',
    strategy: 'replace',
    reason: '发布成功后的「自动跳转」是 redirectTo：刻意替换发布页，不让返回键回到已提交的表单'
  },
  { from: 'pages/publish/cargo/cargo', to: 'pages/preview/preview', strategy: 'push' },
  { from: 'pages/trade/orders/orders', to: 'pages/trade/payment/payment', strategy: 'push' },
  { from: 'pages/trade/orders/orders', to: 'pages/trade/contract/contract', strategy: 'push' },

  // ── 港口域 ────────────────────────────────────────────────────────
  { from: 'pages/port/port', to: 'pages/port/appt/appt', strategy: 'push' },
  { from: 'pages/port/port', to: 'pages/port/berth/berth', strategy: 'push' },
  { from: 'pages/port/appt/appt', to: 'pages/port/berth/berth', strategy: 'push' },

  // ── 委托域 ────────────────────────────────────────────────────────
  { from: 'pages/entrust/workbench/workbench', to: 'pages/entrust/detail/detail', strategy: 'push' },
  // ENT-023：从工作台槽位里的成果引用进入成果页。**push**（进入下一级）而不是 replace ——
  // 用户看完成果还要回到这张工作台继续处理别的槽位，替换掉它等于把人踢出上下文。
  { from: 'pages/entrust/detail/detail', to: 'pages/entrust/artifact/artifact', strategy: 'push' },
  // ENT-030 切片四之四：案件详情（UI-08）。三条进入边对应 PRD 第 155 行的
  // 「accessible from workbench and chat」—— 委托工作台的「异常与变更」槽、
  // UI-04 组合工作台的异常队列、会话里引用的案件。
  // 三条都**先声明、后接线**：页面已落地，但入口代码在切片四之五 / 四之六，
  // 故标 `pending`（有代码证据时**必须**移除该标记，否则 verify_routes.js 报"过期 pending"）。
  {
    from: 'pages/entrust/detail/detail',
    to: 'pages/entrust/case/case',
    strategy: 'push',
    pending: '切片四之五（槽位引用可点）接入后产生真实调用；当前无代码证据',
    reason: '从委托工作台「异常与变更」槽的案件引用进入案件详情'
  },
  {
    from: 'pages/entrust/workbench/workbench',
    to: 'pages/entrust/case/case',
    strategy: 'push',
    pending: '切片四之六（UI-04 异常队列）接入后产生真实调用；当前无代码证据',
    reason: '从组合工作台（UI-04）的异常 / 变更队列进入案件详情'
  },
  {
    from: 'pages/assistant/assistant',
    to: 'pages/entrust/case/case',
    strategy: 'push',
    pending: '会话侧的案件引用尚未接入（PRD 第 155 行要求可从 chat 进入）',
    reason: '会话里引用的案件直接打开详情；进入后返回走 navigateBack / reset，不声明回边'
  },

  // ── 重置栈（回首页重走身份链路）────────────────────────────────────
  { from: 'pages/mine/mine', to: 'pages/index/index', strategy: 'reset' },
  { from: 'pages/entrust/workbench/workbench', to: 'pages/index/index', strategy: 'reset' },
  { from: 'pages/entrust/detail/detail', to: 'pages/index/index', strategy: 'reset' },
  { from: 'pages/entrust/artifact/artifact', to: 'pages/index/index', strategy: 'reset' },
  { from: 'pages/entrust/case/case', to: 'pages/index/index', strategy: 'reset' },

  // ── 工作台 ↔ 会话：产品要求「经理人可在会话/工作台/成果之间反复切换」────
  //    HO 明确指出：有循环的业务导航不一定无限压栈，不能把所有循环判成错误。
  //    这里 push 一次进会话，从会话回工作台走 reuse（复用栈中已有的工作台），
  //    于是 push 图仍然无环，双向切换合法。
  {
    from: 'pages/entrust/workbench/workbench',
    to: 'pages/assistant/assistant',
    strategy: 'push',
    pending: 'ENT-020（UI-05）接入「打开会话」入口后产生真实调用；当前无代码证据',
    reason: '工作台内发起会话'
  },
  {
    from: 'pages/assistant/assistant',
    to: 'pages/entrust/workbench/workbench',
    strategy: 'reuse',
    pending: 'ENT-020（UI-05）接入后由 go() 的复用分支命中；当前无代码证据',
    reason:
      '从工作台进入会话后返回工作台，应复用栈里那一个工作台（同一组织上下文），' +
      '而不是再压一层——这正是 HO 指出的「双向切换不应被判为非法循环」'
  }
]

// 页面类型：
//   tab    —— tabBar 页，只能用 switchTab 进入，进入即**重置**页面栈
//   root   —— 可作为「全新栈」首页（reLaunch / 冷启动），进入即重置页面栈
//   detail —— 需要被 push 进入的页，必须声明非空 parents（由 NAV_EDGES 派生）
//
// 深链策略：
//   allow          —— 允许被外部深链打开（paramSchema 只做"带了就校验"的可选约束）
//   require-params —— 允许深链，但必须带齐 paramSchema 里 required 的参数
//   deny           —— 依赖不可靠的本地上下文（当前身份），深链无意义且会误导
//
// paramSchema 条目：{ type, required, values?, min?, maxLength?, deepLinkOnly?, note? }
//   type: 'id'（字母/数字/_/-）| 'int'（非负整数）| 'enum'（需 values）| 'string'
//   required: true  —— 缺失/空值即校验失败
//   deepLinkOnly: true —— **只在冷启动/外部深链时**要求（应用内 go() 不强制）。
//                        用于「深链必须带、应用内可选」的模式参数，避免把内部调用误拦。
const ROUTES = {
  // ── 入口域 ─────────────────────────────────────────────────────────
  'pages/index/index': {
    kind: 'root',
    deepLink: 'allow',
    paramSchema: {},
    domain: 'entry',
    note: '身份选择入口，冷启动与 reLaunch 的落点'
  },

  // ── tabBar（5 项） ──────────────────────────────────────────────────
  'pages/shipper/shipper': {
    kind: 'tab', deepLink: 'deny', paramSchema: {}, domain: 'workspace',
    note: '货主工作台'
  },
  'pages/owner/owner': {
    kind: 'tab', deepLink: 'deny', paramSchema: {}, domain: 'workspace',
    note: '船东工作台'
  },
  'pages/trade/orders/orders': {
    kind: 'tab', deepLink: 'deny', paramSchema: {}, domain: 'trade',
    note: '订单列表'
  },
  'pages/port/port': {
    kind: 'tab', deepLink: 'deny', paramSchema: {}, domain: 'port',
    note: '港口工作台'
  },
  'pages/mine/mine': {
    kind: 'tab', deepLink: 'deny', paramSchema: {}, domain: 'mine',
    note: '我的；委托发货入口挂在这里'
  },

  // ── 发布域 ─────────────────────────────────────────────────────────
  'pages/publish/cargo/cargo': {
    kind: 'detail', deepLink: 'allow', paramSchema: {}, domain: 'publish',
    note: '发布货源'
  },
  'pages/publish/ship/ship': {
    kind: 'detail', deepLink: 'allow', paramSchema: {}, domain: 'publish',
    note: '发布空船'
  },

  // ── 交易域 ─────────────────────────────────────────────────────────
  'pages/trade/match/match': {
    kind: 'detail', deepLink: 'require-params', domain: 'trade',
    paramSchema: {
      mode: { type: 'enum', values: ['cargo', 'ship'], required: true, note: 'cargo=货主找船，ship=船东找货' },
      refId: { type: 'int', min: 1, required: true, note: '货物/船舶 id；为 0 时页面取不到候选' }
    },
    note: '撮合结果；缺 mode/refId 或 refId=0 会取不到候选'
  },
  'pages/trade/contract/contract': {
    kind: 'detail', deepLink: 'require-params', domain: 'trade',
    paramSchema: { order_id: { type: 'id', required: true } },
    note: '合同预览'
  },
  'pages/trade/payment/payment': {
    kind: 'detail', deepLink: 'require-params', domain: 'trade',
    paramSchema: { order_id: { type: 'id', required: true } },
    note: '支付详情'
  },

  // ── 港口域 ─────────────────────────────────────────────────────────
  'pages/port/appt/appt': {
    kind: 'detail', deepLink: 'require-params', domain: 'port',
    paramSchema: { id: { type: 'id', required: true } },
    note: '泊位预约'
  },
  'pages/port/berth/berth': {
    kind: 'detail', deepLink: 'require-params', domain: 'port',
    paramSchema: { id: { type: 'id', required: true } },
    note: '泊位详情'
  },

  // ── 公共域 ─────────────────────────────────────────────────────────
  'pages/assistant/assistant': {
    kind: 'detail', deepLink: 'require-params', domain: 'shared',
    paramSchema: {
      // ⚠️ deepLinkOnly：应用内 `goAssistant()` 是不带 mode 进来的（页面自己有默认态），
      //    所以 mode 只对**外部深链**是必需的。不这样区分就会把内部调用误拦。
      mode: {
        type: 'enum',
        values: ['parse', 'search'],
        required: true,
        deepLinkOnly: true,
        note: 'parse=货源解析，search=意图路由'
      }
    },
    // ⚠️ 已知静态盲区：`utils/agent-entry.js` 的 openSmartEntry() 也会跳到这里，
    //    但它的**调用方页面**无法静态解析（工具函数），故这里只登记可确认的页面来源。
    //    新增页面若调用 openSmartEntry，请把该页面补一条 push 边进来。
    note: '智能客服 / 统一入口（mode=parse|search）'
  },
  'pages/preview/preview': {
    kind: 'detail', deepLink: 'allow', paramSchema: {}, domain: 'shared',
    note: '占位预览页'
  },

  // ── 委托发货域（ENT-xxx） ───────────────────────────────────────────
  'pages/entrust/workbench/workbench': {
    kind: 'detail',
    // ENT-018 修订：初版声明 `deny`，但**代码本身**是支持深链的 ——
    // `workbench.js` onLoad 写着「组织来源优先级：**深链参数 > 上次选择 > 由服务端清单推导**，
    // 深链带 org_id 是有意的（例如从消息直接进入某个组织的队列）」。
    // 且 HO 的口径是「缺少上次选择的组织**可以进入选择流程**，并非必须禁止该页深链」，
    // 而本页本身就是组织选择器（自带「需要选择服务经营主体」「还没有加入服务经营主体」两态）。
    // 所以声明改为 allow + org_id 可选校验：带了就查格式，没带就让页面走自己的选择态。
    deepLink: 'allow',
    domain: 'entrust',
    paramSchema: { org_id: { type: 'id', required: false, note: '来自消息/外部入口时指定组织' } },
    // ⚠️ `org_id` **不**进复用键（否则"带 org_id 进来的那一页"与"不带 org_id 的目标"
    //    会被算成两个不同上下文，工作台就永远复用不上）。组织这一维由 keyContext 统一
    //    从 ctx.orgId 取；栈里那一页则取它自己的 org_id（见 resolveNavigation）。
    keyParams: [],
    // 业务上下文里包含「当前组织」：复用键必须带上它，否则 A 组织的工作台会被误判成
    // 同一页而复用给 B 组织（同一路径不同组织 = 不同业务上下文，见 DR-0011 §3.5）。
    keyContext: ['org'],
    note: '委托工作台（「我的」入口进入；也支持带 org_id 的深链）'
  },
  'pages/entrust/detail/detail': {
    kind: 'detail', deepLink: 'require-params', domain: 'entrust',
    paramSchema: { assignment_id: { type: 'id', required: true } },
    note: '委托详情'
  },
  'pages/entrust/artifact/artifact': {
    kind: 'detail', deepLink: 'require-params', domain: 'entrust',
    // `artifact_id` 必需：没有它页面无从知道该读哪份成果，而"读最新的那份"是
    // 一种**会漂移**的推断（工作台引用的是精确 ID 与版本，见 PRD 第 187 行）。
    paramSchema: { artifact_id: { type: 'id', required: true } },
    note: '成果详情（查看 / 编辑 / 确认；从工作台槽位的成果引用进入）'
  },
  'pages/entrust/case/case': {
    kind: 'detail', deepLink: 'require-params', domain: 'entrust',
    // `case_id` 必需：没有它页面无从知道该读哪宗案件，而"读最新那宗"是一种
    // **会漂移**的推断（与 `artifact_id` 同理）。
    // ⚠️ 它与接口路径参数名 `{exception_id}` **不同名但同值** —— DR-0014 §7 有意
    //    如此（路径已发布，改名会牵动它），别"顺手统一"。
    paramSchema: { case_id: { type: 'id', required: true } },
    // ⚠️ 刻意**不**声明 `keyContext: ['org']`：案件所属组织由 `case_id` 唯一决定，
    //    它不是"当前选中的组织"。算进复用键会让同一宗案件在不同组织上下文下被当成
    //    两个页面，白压一层栈 —— 这正是 keyContext 用在 workbench 上的反面。
    note: '案件详情（异常 / 变更；从委托工作台异常槽、UI-04 队列或会话进入）'
  }
}

// 由 NAV_EDGES 派生 parents（层级）。语义已收窄为「静态上可以从哪一层 push 进来」，
// 因此**不再**包含 back / reuse / replace 的来源——那些不再作为链深证据。
for (const _p of Object.keys(ROUTES)) {
  ROUTES[_p].parents = NAV_EDGES.filter(function (e) {
    return e.to === _p && e.strategy === STRATEGY.PUSH
  }).map(function (e) {
    return e.from
  })
}

// tabBar 页集合（供运行期与 CI 共用，避免各页各写一份）
const TAB_BAR_PAGES = Object.keys(ROUTES).filter(function (p) {
  return ROUTES[p].kind === 'tab'
})

/**
 * 已接入运行期治理的页面（ENT-019 起）。
 *
 * 语义：这些页面的**所有跳转都经 `go()`**，且 `onLoad` 执行 `guardEntry()`。
 * 它们不得再出现裸 `wx.navigateTo / redirectTo / reLaunch` —— 那会让页面栈预算
 * （`STACK_BUDGET`）与深链参数契约重新变成"只在 CI 里成立的声明"。
 *
 * 这是一份**增量名单**：HO 第 4 条明确「不要求全量改造旧页面，但新增委托页面必须
 * 从首个切片起接入」。每迁移一个页面就往这里加一行，CI 会同时核对两件事：
 * 名单内页面有 `go(` 调用、且没有裸 wx 导航调用。
 */
const MIGRATED_PAGES = [
  // ENT-019：委托支线第一个切片的两个页面
  'pages/entrust/workbench/workbench',
  'pages/entrust/detail/detail',
  // ENT-023：成果页（查看 / 编辑 / 确认）从落地起就接入，避免"新增页面绕过预算"
  'pages/entrust/artifact/artifact',
  // ENT-030 切片四之四：案件详情（UI-08）从落地起接入 —— 它是**被 push 进入**的
  // 三级页，恰恰是"页面栈预算"最容易被绕过的一类（本页只读、没有表单，
  // 但预算与深链契约与该页有没有表单无关）
  'pages/entrust/case/case'
]

/** 去掉前导 `/`、查询串与 hash，得到注册表口径的页面路径 */
function normalize(url) {
  const raw = String(url == null ? '' : url)
  const noHash = raw.split('#')[0]
  const noQuery = noHash.split('?')[0]
  return noQuery.replace(/^\//, '')
}

/** 取出查询串（不含 `?`） */
function queryOf(url) {
  const raw = String(url == null ? '' : url)
  const i = raw.indexOf('?')
  if (i === -1) return ''
  return raw.slice(i + 1).split('#')[0]
}

/**
 * 解析查询串。`text` 可以是完整 url（`/a/b?x=1`）、纯查询串（`x=1`）或空。
 * 返回普通对象；同名键后者覆盖前者（本注册表不声明重复键参数）。
 */
function parseQuery(text) {
  const s = String(text == null ? '' : text)
  const q = s.indexOf('?') !== -1 ? queryOf(s) : s
  const out = {}
  if (!q) return out
  for (const kv of q.split('&')) {
    if (!kv) continue
    const i = kv.indexOf('=')
    const k = i === -1 ? kv : kv.slice(0, i)
    // 纯路径被误当成查询串时，键里会带 `/`；这类键一律忽略（不静默变成参数）
    if (!k || k.indexOf('/') !== -1) continue
    let v = i === -1 ? '' : kv.slice(i + 1)
    try {
      out[decodeURIComponent(k)] = decodeURIComponent(v)
    } catch (e) {
      out[k] = v
    }
  }
  return out
}

/** 查一条路由；未登记返回 null */
function routeOf(url) {
  const p = normalize(url)
  return Object.prototype.hasOwnProperty.call(ROUTES, p) ? ROUTES[p] : null
}

/** 是否 tabBar 页（用 switchTab 还是 navigateTo 的判据） */
function isTabBarPage(url) {
  return TAB_BAR_PAGES.indexOf(normalize(url)) !== -1
}

/** 查一条导航边（精确匹配 from/to）；没有返回 null */
function edgeOf(from, to) {
  const f = normalize(from)
  const t = normalize(to)
  for (const e of NAV_EDGES) {
    if (e.from === f && e.to === t) return e
  }
  return null
}

/**
 * 声明链深：从「全新栈的首个页面」到该页面最少要叠几层。
 * tab / root 进入即重置栈，故为 1；其余页面 = max(入向 push 边源链深 + 1,
 * 入向 replace 边源链深 + 0)。replace 不增层（替换当前页），但**参与**链深——
 * 否则 `cargo --redirectTo--> match` 这种替换会被漏算。
 * 环或未登记来源返回 Infinity（CI 会单独报出来，不要靠它兜底）。
 */
function chainDepth(url, seen) {
  const p = normalize(url)
  const r = routeOf(p)
  if (!r) return Infinity
  if (r.kind === 'tab' || r.kind === 'root') return 1
  const visited = seen || []
  if (visited.indexOf(p) !== -1) return Infinity
  let max = -1
  for (const e of NAV_EDGES) {
    if (e.to !== p) continue
    const w = DEPTH_WEIGHT[e.strategy]
    if (w === undefined) continue
    const d = chainDepth(e.from, visited.concat([p]))
    const cand = d === Infinity ? Infinity : d + w
    if (cand > max) max = cand
  }
  return max < 0 ? Infinity : max
}

/** 当前页面栈层数；不在小程序运行时（如 CI 里 require）返回 0 */
function currentDepth() {
  if (typeof getCurrentPages !== 'function') return 0
  try {
    const stack = getCurrentPages()
    return (stack && stack.length) || 0
  } catch (e) {
    return 0
  }
}

/**
 * 页面栈快照：`[{ route, options }]`，栈底在前。
 * 可在小程序外调用（返回 `[]`），也可由调用方注入以便测试。
 */
function stackSnapshot() {
  if (typeof getCurrentPages !== 'function') return []
  try {
    const stack = getCurrentPages() || []
    return stack.map(function (pg) {
      return { route: normalize(pg && pg.route), options: (pg && pg.options) || {} }
    })
  } catch (e) {
    return []
  }
}

/** 该页面的复用键参数（显式声明优先；未声明则取 paramSchema 的全部键） */
function keyParamsOf(path) {
  const r = routeOf(path)
  if (!r) return []
  if (Array.isArray(r.keyParams)) return r.keyParams.slice()
  return Object.keys(r.paramSchema || {})
}

/**
 * 上下文复用键：**不能只比较路径**。
 * 同一个成果详情页可能分别显示两张委托，所以键 = 页面路径 + 上下文参数 + 组织。
 * 键不等 ⇒ 不复用（改走压栈或声明式替换）。
 */
function pageKey(path, query, ctx) {
  const p = normalize(path)
  const r = routeOf(p)
  const q = query && typeof query === 'object' ? query : parseQuery(String(query == null ? '' : query))
  const options = ctx || {}
  const parts = keyParamsOf(p)
    .slice()
    .sort()
    .map(function (k) {
      return k + '=' + (q[k] == null ? '' : String(q[k]))
    })
  const contexts = (r && r.keyContext) || []
  if (contexts.indexOf('org') !== -1) {
    parts.push('org=' + (options.orgId == null ? '' : String(options.orgId)))
  }
  return p + '|' + parts.join('&')
}

/**
 * 单参数校验。返回 '' 表示通过，否则返回人类可读的原因。
 * 空值只在 `required` 时才算错——**空字符串等于缺失**，这正是初版的漏洞
 * （HO 实测：`assignment_id=` 当时被判为"通过"，因为它只查键是否出现）。
 */
function validateParamValue(key, spec, raw) {
  const spec0 = spec || {}
  const empty = raw === undefined || raw === null || String(raw) === ''
  if (empty) return spec0.required ? '缺少必需参数（不可为空）' : ''
  const v = String(raw)
  switch (spec0.type) {
    case 'id':
      if (!/^[A-Za-z0-9_-]+$/.test(v)) return '格式非法（应为标识符：字母/数字/_/-）'
      return ''
    case 'int':
      if (!/^\d+$/.test(v)) return '格式非法（应为非负整数）'
      if (spec0.min != null && Number(v) < spec0.min) return '取值小于下限 ' + spec0.min
      return ''
    case 'enum':
      if (!(spec0.values || []).length) return '枚举未声明可选值（注册表配置错误）'
      if ((spec0.values || []).indexOf(v) === -1) {
        return '取值不在允许集合内：' + spec0.values.join(' / ')
      }
      return ''
    case 'string':
      if (v.length > (spec0.maxLength || 200)) return '超长（上限 ' + (spec0.maxLength || 200) + '）'
      return ''
    default:
      return '未声明的参数类型：' + spec0.type
  }
}

/**
 * 参数校验。
 * @param {string} path 页面路径或 url（会先 normalize）
 * @param {object|string} query 参数对象，或完整 url / 纯查询串
 * @param {object} [opts] { deepLink: true } 时一并检查 `deepLinkOnly` 的参数
 * @returns {{ok:boolean, errors:Array<{key:string,reason:string}>, missing:string[], invalid:string[]}}
 */
function validateParams(path, query, opts) {
  const options = opts || {}
  const p = normalize(path)
  const r = routeOf(p)
  if (!r) {
    return {
      ok: false,
      errors: [{ key: '', reason: '未登记的路由：' + p }],
      missing: [],
      invalid: []
    }
  }
  const q = query && typeof query === 'object' ? query : parseQuery(String(query == null ? '' : query))
  const schema = r.paramSchema || {}
  const errors = []
  for (const key of Object.keys(schema)) {
    const spec = schema[key] || {}
    if (spec.deepLinkOnly && !options.deepLink) continue
    const present = Object.prototype.hasOwnProperty.call(q, key)
    if (!present) {
      if (spec.required) errors.push({ key: key, reason: '缺少必需参数' })
      continue
    }
    if (String(q[key]) === '') {
      // **显式给了空值 ≠ 没给**。HO 实测的 `assignment_id=` 正是这一类：
      // 初版只查"键是否出现"，于是空值被判为通过；这里把它归到"缺少"处理。
      // 可选参数也一样 —— 写了 `?org_id=` 就是写错了，不该被静默当成"没带"。
      errors.push({ key: key, reason: '缺少参数值（显式给了空值）' })
      continue
    }
    const reason = validateParamValue(key, spec, q[key])
    if (reason) errors.push({ key: key, reason: reason })
  }
  return {
    ok: errors.length === 0,
    errors: errors,
    missing: errors.filter(function (e) { return e.reason.indexOf('缺少') === 0 }).map(function (e) { return e.key }),
    invalid: errors.filter(function (e) { return e.reason.indexOf('缺少') !== 0 }).map(function (e) { return e.key })
  }
}

/** 该页面深链时**必需**的参数名 */
function requiredParamsOf(path) {
  const r = routeOf(path)
  if (!r) return []
  const schema = r.paramSchema || {}
  return Object.keys(schema).filter(function (k) {
    return !!schema[k].required
  })
}

/** 导航失败时的兜底去处（HO 第 ⑤ 步：给出「返回工作台」操作） */
const FALLBACK = { label: '返回工作台', url: '/pages/entrust/workbench/workbench' }

/**
 * 决定「该用哪个导航 API、目标 url 长什么样」，**不真正跳转**。
 *
 * 按 DR-0011 §3.4 的策略链，命中即停：
 *   0) 未登记 / 参数非法            → blocked
 *   1) tabBar 页                    → switchTab（必须剥掉查询串）
 *   1.5) 边声明为 reset             → reset（reLaunch，**重置栈**）
 *   2) 目标已在栈中且上下文相同        → reuse / back（返回并刷新目标成果）
 *   3) 边/页面声明为 replace          → replace（未保存编辑先提示）
 *   4) 预算内（depth < STACK_BUDGET） → push
 *   5) 有未保存编辑                  → confirm-unsaved（不静默卸载）
 *   6) 仍无法安全进入                → blocked + fallback（返回工作台）
 *
 * ⚠️ 1.5 必须排在 2 之前：`reset` 的语义就是「清空页面栈重来」，若先过复用分支，
 *    目标恰好在栈底时（冷启动深链：index → mine → workbench）会被降级成
 *    `navigateBack(2)` —— 落点看起来一样，但栈没清、身份链路没重走。
 *    同理 `reset` 也不能落到第 4 步（那是 push，会再叠一层首页）。
 *
 * ⚠️ 第 4 步排在"未保存编辑"（第 5 步）之前，**这是刻意的**：`push` 不卸载当前页，
 *    未保存的编辑还在栈里、没丢。真正会丢编辑的只有 `replace`（第 3 步）与
 *    `reset` / `switchTab`（重置栈）、以及栈满之后的兜底。所以"有未保存编辑"只在
 *    **替换**与**预算耗尽**两处拦截 —— 否则会把"往下走一层"也变成一次弹窗，
 *    用户会被训练成一律点确定（那比不提示更糟）。这条语义由行为测试钉住。
 *
 * 与 HO 五步的对应关系：① ≡ 2、② ≡ 4、③ ≡ 3、④ ≡ 5、⑤ ≡ 6。
 * ⚠️ 有意与 HO 字面顺序不同的一点：HO 把「压栈」写在「声明式替换」之前，
 *    本实现按**边的声明分流**——`strategy==='replace'` 的边直接替换，其余边在
 *    预算内压栈。理由：`strategy` 描述的是这条导航**是什么**（替换同级 vs 进入下一级），
 *    不是压不进去时的兜底；若按「先压栈」，`cargo --redirectTo--> match` 这类
 *    刻意保持栈浅的替换会在预算充足时被悄悄改成压栈，等于改了产品行为。
 *
 * @param {string} url 目标（带 `/` 前缀与查询串，与 wx 原生写法一致）
 * @param {object} [ctx] { stack, from, key, ctx:{orgId}, hasUnsaved, fallback }
 */
function resolveNavigation(url, ctx) {
  const options = ctx || {}
  const p = normalize(url)
  const query = queryOf(url)
  const full = '/' + p + (query ? '?' + query : '')
  const r = routeOf(p)

  if (!r) {
    return { ok: false, action: 'blocked', code: 'unregistered', path: p, url: full, reason: '未登记的路由：' + p }
  }

  // 0) 参数契约（内部导航也查"带了就合法"；deepLinkOnly 的参数不在此强制）
  const v = validateParams(p, query, { deepLink: false })
  if (!v.ok) {
    return {
      ok: false,
      action: 'blocked',
      code: 'bad-params',
      path: p,
      url: full,
      reason: '参数校验未通过：' + v.errors.map(function (e) { return e.key + ' ' + e.reason }).join('；'),
      errors: v.errors
    }
  }

  // 1) tabBar 页：switchTab 不接受查询串（带上会被忽略甚至报错），这里主动剥掉
  if (r.kind === 'tab') {
    return {
      ok: true,
      action: 'switchTab',
      strategy: STRATEGY.SWITCH_TAB,
      path: p,
      url: '/' + p,
      reason: 'tabBar 页：switchTab 且剥掉查询串（进入即重置页面栈）'
    }
  }

  const stack = options.stack || stackSnapshot()
  const from = options.from != null ? normalize(options.from) : (stack.length ? stack[stack.length - 1].route : '')
  const depth = stack.length
  const edge = from ? edgeOf(from, p) : null
  const strategy = (edge && edge.strategy) || (options.strategy || STRATEGY.PUSH)

  // 1.5) 声明为 reset：`wx.reLaunch`，清空页面栈重来。
  //      reLaunch **可以**带查询串（与 switchTab 不同），故 url 原样保留。
  //      位置在复用分支之前的原因是语义性的，不是顺手：见函数头 ⚠️。
  if (strategy === STRATEGY.RESET) {
    return {
      ok: true,
      action: 'reset',
      strategy: STRATEGY.RESET,
      path: p,
      url: full,
      reason: '声明为重置栈（reLaunch）：清空页面栈并重建，不走复用/压栈'
    }
  }

  // 2) 目标已在栈中且业务上下文相同 → 返回／复用，并刷新目标成果
  const key = options.key != null ? options.key : pageKey(p, query, options.ctx)
  let found = -1
  for (let i = stack.length - 1; i >= 0; i--) {
    if (stack[i].route !== p) continue
    // 栈里那一页是**带着它自己的上下文**进去的：组织取它自己的 org_id（有的话），
    // 不能用"当前 ctx 的组织"去比，否则 A 组织的工作台会被误判成与 B 组织同一个页。
    const own = stack[i].options && stack[i].options.org_id
      ? Object.assign({}, options.ctx, { orgId: stack[i].options.org_id })
      : options.ctx
    const k = pageKey(p, stack[i].options || {}, own)
    if (k === key) { found = i; break }
  }
  if (found !== -1) {
    const delta = stack.length - 1 - found
    return {
      ok: true,
      action: 'reuse',
      strategy: delta === 0 ? STRATEGY.REUSE : STRATEGY.BACK,
      path: p,
      url: full,
      delta: delta,
      reason: delta === 0
        ? '目标就是当前页：原地刷新成果'
        : '目标已在栈中且上下文相同：返回第 ' + delta + ' 层并刷新目标成果'
    }
  }

  // ⚠️ `back` 策略**只在上面的复用分支被消费**：目标在栈中就 `navigateBack`，
  //    不在栈中则继续往下走 —— 此时唯一能到它的办法就是压栈，故退化为 push
  //    （并留下 reason，见第 4 步）。本注册表当前没有 `back` 边；将来真要声明时
  //    请注意这条退化路径，别以为写了 back 就一定会"返回"。
  //
  // 3) 边/页面声明为 replace：替换当前页（未保存编辑先提示，不静默卸载）
  if (strategy === STRATEGY.REPLACE) {
    const replacePlan = { ok: true, action: 'replace', strategy: STRATEGY.REPLACE, path: p, url: full, reason: '声明为替换的同级页面' }
    if (options.hasUnsaved) {
      return {
        ok: false,
        action: 'confirm-unsaved',
        code: 'unsaved-edit',
        strategy: STRATEGY.REPLACE,
        path: p,
        url: full,
        // ENT-019：用户答复"放弃编辑"之后**该执行的那个计划**。
        // 页面用 `performPlan(plan.afterConfirm)` 执行它，从而不必重新
        // `resolveNavigation()` —— 重算会让 `hasUnsaved` 从 true 变 false 而在
        // 预算类分支上落进另一条路径，提示与结果自相矛盾（见 performPlan 注释）。
        afterConfirm: replacePlan,
        reason: '该目标是声明式替换（同级），但当前页有未保存编辑：先保存或明确放弃'
      }
    }
    return replacePlan
  }

  // 4) 预算内 → 正常压栈
  if (depth < STACK_BUDGET) {
    return { ok: true, action: 'push', strategy: STRATEGY.PUSH, path: p, url: full, reason: '' }
  }

  // 5) 有未保存编辑 → 不静默卸载
  if (options.hasUnsaved) {
    return {
      ok: false,
      action: 'confirm-unsaved',
      code: 'unsaved-edit',
      path: p,
      url: full,
      // 预算已满：**放弃编辑也进不去**（第 6 步会给出兜底去处），所以 afterConfirm
      // 是一个 blocked 计划而不是 push —— 页面必须把这件事如实说出来，不能假装
      // "存下来就能进"。
      afterConfirm: {
        ok: false,
        action: 'blocked',
        code: 'stack-budget',
        path: p,
        url: full,
        reason: '页面栈已达项目预算 ' + STACK_BUDGET + ' 层（平台硬限 ' + MAX_STACK + '），且该目标未声明为可替换',
        fallback: options.fallback || FALLBACK
      },
      reason: '页面栈已达项目预算 ' + STACK_BUDGET + ' 层，且当前页有未保存编辑：先保存或明确放弃'
    }
  }

  // 6) 仍无法安全进入 → 明确失败 + 返回工作台
  return {
    ok: false,
    action: 'blocked',
    code: 'stack-budget',
    path: p,
    url: full,
    reason: '页面栈已达项目预算 ' + STACK_BUDGET + ' 层（平台硬限 ' + MAX_STACK + '），且该目标未声明为可替换',
    fallback: options.fallback || FALLBACK
  }
}

/**
 * 统一导航入口：按 `resolveNavigation()` 的策略链执行。
 *
 * 与初版的差别：**不再把 `redirectTo` 当成栈快满时的通用默认动作**
 * （那会卸载尚未保存的编辑页，也会改变返回路径）。替换必须**由声明允许**。
 *
 * @param {string} url 目标（带 `/` 前缀与查询串）
 * @param {object} [opts]
 *   from                当前页路径（不传则取页面栈顶；页面**应当显式传**）
 *   fail()              失败回调
 *   stack               注入页面栈（测试用）
 *   ctx.orgId           当前组织（复用键用）
 *   hasUnsaved          当前页是否有未保存编辑
 *   onUnsaved(plan)     action==='confirm-unsaved' 时回调（页面决定如何提示）
 *   onBlocked(plan)     action==='blocked' 时回调
 *   blockedTip          兜底 toast 文案
 *   fallback            blocked 时的兜底去处（默认 FALLBACK）
 * @returns {object} 策略计划（含 performed：是否真的发起了 wx 调用）
 */
function go(url, opts) {
  const options = opts || {}
  const plan = resolveNavigation(url, options)
  plan.performed = false

  if (typeof wx === 'undefined') return plan

  if (!plan.ok) {
    console.warn('[routes] ' + (plan.action || 'blocked') + ' ' + plan.path + ' —— ' + plan.reason)
    if (plan.action === 'confirm-unsaved') {
      if (typeof options.onUnsaved === 'function') options.onUnsaved(plan)
      return plan
    }
    if (typeof options.onBlocked === 'function') options.onBlocked(plan)
    else if (wx.showToast) {
      wx.showToast({ title: options.blockedTip || plan.reason, icon: 'none' })
    }
    return plan
  }

  return performPlan(plan, options)
}

/**
 * 执行一个**已经解析好**的策略计划（不发散、不重新判定）。
 *
 * 页面一般不需要直接调用它 —— 只有一种情形例外：`confirm-unsaved` 提示得到用户
 * 「确认放弃编辑」之后。此时必须执行**原来那个计划**，不能重新 `resolveNavigation()`：
 * 重算可能因为 `hasUnsaved` 从 true 变 false 而落进另一条分支（典型是"预算是唯一阻碍"
 * 的情形：用户刚说要放弃编辑，却又被告知栈满），于是交互与提示自相矛盾。
 *
 * @param {object} plan resolveNavigation() 的返回值（必须 ok）
 * @param {object} [opts] 同 go() 的 fail / onBlocked / onReuse
 * @returns {object} 同一个 plan（performed 表示是否真的发起了 wx 调用）
 */
function performPlan(plan, opts) {
  const options = opts || {}
  const out = plan || {}
  out.performed = false
  if (!out.ok || typeof wx === 'undefined') return out

  const fail = options.fail
  switch (out.action) {
    case 'switchTab':
      wx.switchTab({ url: out.url, fail: fail })
      break
    case 'reset':
      // ENT-019 补：声明为 reset 的边必须真的重置栈。初版没有这个分支，
      // 于是 `strategy:'reset'` 会落到 default 变成 navigateTo（多压一层首页）。
      wx.reLaunch({ url: out.url, fail: fail })
      break
    case 'reuse':
      if (out.delta > 0) {
        // 返回栈中已有的目标页；目标页在 onShow 里刷新自己的成果
        wx.navigateBack({ delta: out.delta })
      } else if (typeof options.onReuse === 'function') {
        options.onReuse(out)
      } else {
        // 目标就是当前页（delta=0）而页面没给 onReuse：**什么都不会发生**。
        // 这种"点了没反应"必须留下痕迹，否则会被当成随机的 UI 卡顿排查。
        console.warn('[routes] ' + out.path + ' 就是当前页，且未提供 onReuse —— 页面需要自己刷新成果')
      }
      break
    case 'replace':
      wx.redirectTo({ url: out.url, fail: fail })
      break
    case 'push':
    default:
      if (currentDepth() >= MAX_STACK) {
        // 平台硬限兜底：到这里说明策略链与真实栈已经脱节，必须留下可归因的日志
        console.warn('[routes] 页面栈已达平台硬限 ' + MAX_STACK + '，' + out.path + ' 无法进入')
        if (typeof options.onBlocked === 'function') options.onBlocked(out)
        return out
      }
      wx.navigateTo({ url: out.url, fail: fail })
      break
  }
  out.performed = true
  return out
}

/**
 * 深链是否可接受（供页面 onLoad 自检 / 分享路径判断）。
 * @returns {{ok:boolean, reason:string, errors:Array}}
 */
function canDeepLink(url) {
  const p = normalize(url)
  const r = routeOf(p)
  if (!r) return { ok: false, reason: '未登记的路由：' + p, errors: [] }
  if (r.deepLink === 'deny') return { ok: false, reason: p + ' 声明为不可深链', errors: [] }
  const v = validateParams(p, url, { deepLink: true })
  if (!v.ok) {
    return {
      ok: false,
      reason: '参数校验未通过：' + v.errors.map(function (e) { return e.key + ' ' + e.reason }).join('；'),
      errors: v.errors
    }
  }
  return { ok: true, reason: '', errors: [] }
}

/**
 * 目标页入口守卫。**深链校验必须落在目标页**：`go()` 只覆盖应用内导航，
 * 冷启动 / 外部深链（分享、消息、扫码）时目标页仍要在 `onLoad` 自检。
 *
 * @param {string} url 页面自身 url（通常由 onLoad(options) 重建）
 * @param {object} [ctx]
 *   coldStart     是否冷启动/外部进入；不给则按「页面栈 ≤ 1 层」推断
 *   orgId         当前组织（本页声明 keyContext 含 org 时参与判定）
 *   hasPermission 已知的服务端授权结论（false → 拦）；未知就别传
 *   belongs       成果/任务/会话是否归属所给委托（false → 拦）；未知就别传
 * @returns {{ok:boolean, action:'continue'|'redirect-org'|'blocked', code:string, path:string, reason:string}}
 */
function guardEntry(url, ctx) {
  const options = ctx || {}
  const p = normalize(url)
  const r = routeOf(p)
  if (!r) {
    return { ok: false, action: 'blocked', code: 'unregistered', path: p, reason: '未登记的路由：' + p }
  }

  // 参数合法（非空 + 类型／枚举）——无论是否冷启动都查，缺参是编程错误
  const v = validateParams(p, url, { deepLink: true })
  if (!v.ok) {
    return {
      ok: false,
      action: 'blocked',
      code: 'bad-params',
      path: p,
      reason: '参数校验未通过：' + v.errors.map(function (e) { return e.key + ' ' + e.reason }).join('；'),
      errors: v.errors
    }
  }

  const cold = typeof options.coldStart === 'boolean' ? options.coldStart : currentDepth() <= 1
  if (!cold) {
    return {
      ok: true,
      action: 'continue',
      code: 'internal',
      path: p,
      reason: '应用内导航：入口校验已由 go() 的 resolveNavigation 完成'
    }
  }

  if (r.deepLink === 'deny') {
    return { ok: false, action: 'blocked', code: 'deep-link-denied', path: p, reason: p + ' 声明为不可深链' }
  }

  // 「缺少上次选择的组织」**可以进入选择流程**，不视为拒绝深链（HO 第三章）
  //
  // ⚠️ 本分支当前**没有任何页面会命中**，而且这是**有意**的：唯一声明了
  //    `keyContext: ['org']` 的页面是 `pages/entrust/workbench/workbench`，
  //    它本身就是组织选择流程（自带 onPickOrg 与「需要选择服务经营主体」
  //    「还没有加入服务经营主体」两态），所以它**不声明** `requiresOrg` ——
  //    否则会把「进工作台来选组织」判成「先去别处选组织」，形成循环。
  //    将来出现「必须已有组织、自己不选」的页面时再声明 requiresOrg。
  const contexts = r.keyContext || []
  if (contexts.indexOf('org') !== -1 && r.requiresOrg && !options.orgId) {
    return {
      ok: true,
      action: 'redirect-org',
      code: 'need-org',
      path: p,
      reason: '缺少上次选择的组织：进入组织选择流程（不是拒绝该页深链）'
    }
  }

  // 前端拦截只是体验层；授权与归属仍以服务端为准（HO 第三章末）
  if (options.hasPermission === false) {
    return { ok: false, action: 'blocked', code: 'forbidden', path: p, reason: '无访问权限（服务端为准，此处仅提前拦截）' }
  }
  if (options.belongs === false) {
    return { ok: false, action: 'blocked', code: 'not-belong', path: p, reason: '目标成果/任务/会话不属于所给委托' }
  }

  return { ok: true, action: 'continue', code: 'ok', path: p, reason: '' }
}

/** 按业务域统计页面数（CI 报告用） */
function countByDomain() {
  const out = {}
  for (const p of Object.keys(ROUTES)) {
    const d = ROUTES[p].domain || 'unknown'
    out[d] = (out[d] || 0) + 1
  }
  return out
}

module.exports = {
  MAX_STACK,
  STACK_BUDGET,
  STRATEGY,
  STRATEGY_VALUES,
  DEPTH_WEIGHT,
  STRATEGY_TARGET_TAB,
  STRATEGY_TARGET_TAB_OR_ROOT,
  NAV_EDGES,
  FALLBACK,
  ROUTES,
  TAB_BAR_PAGES,
  MIGRATED_PAGES,
  normalize,
  queryOf,
  parseQuery,
  routeOf,
  edgeOf,
  isTabBarPage,
  chainDepth,
  currentDepth,
  stackSnapshot,
  keyParamsOf,
  pageKey,
  validateParamValue,
  validateParams,
  requiredParamsOf,
  resolveNavigation,
  go,
  performPlan,
  canDeepLink,
  guardEntry,
  countByDomain
}
