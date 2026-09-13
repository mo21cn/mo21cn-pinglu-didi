// 路由注册表与页面栈预算（R1 / ENT-015）
//
// ── 为什么需要这个模块 ────────────────────────────────────────────────
// 小程序 `wx.navigateTo` 的页面栈上限是 **10 层**，超限时 `navigateTo` 直接失败，
// 而失败形态是「点了没反应」而不是报错（`utils/agent-entry.js` 里那句 fail 兜底
// 就是为它写的）。委托支线要在现有 17 个页面上继续加页面（UI-01～UI-05），
// 如果「新增页面」不需要声明任何东西就能进 `app.json`，栈深只会在某天突然撞上限，
// 而且很难反推出是哪条链叠到第 10 层的。
//
// 所以把「页面类型 / 上级页面 / 可否深链 / 深链必需参数」变成**必须声明**的注册表，
// 由 `scripts/verify_routes.js` 在 CI 上双向核对（`app.json` ⇄ 本表）。
//
// ── 三条 CI 强制约束 ──────────────────────────────────────────────────
//   1. `app.json` 的每个页面都必须登记，反之注册表里也不得有多余项（禁止偷偷加页）
//   2. 任何页面的声明链深 `chainDepth()` 不得超过 `STACK_BUDGET`
//   3. 实际导航边必须已被声明：`navigateTo` / `redirectTo` 的**源页面**必须在
//      目标页面的 `parents` 里（否则链深会被低估）
//
// ── 本模块是纯数据 + 纯函数 ───────────────────────────────────────────
// 可在 Node 里直接 `require`（CI 静态校验要用），**模块加载期不触碰 `wx`**；
// 只有 `go()` / `currentDepth()` 会用到运行时 API，且都带存在性判断。

// 页面栈硬上限（小程序平台固定值，不要改）
const MAX_STACK = 10

// 声明链深预算：留 2 层给「临时页」（如 preview 预览、assistant 浮层再叠一层），
// 这样即使运行期多叠了两层，也不会撞到 10 层硬上限。
const STACK_BUDGET = 8

// 页面类型：
//   tab    —— tabBar 页，只能用 switchTab 进入，进入即**重置**页面栈
//   root   —— 可作为「全新栈」首页（reLaunch / 冷启动），进入即重置页面栈
//   detail —— 需要被 push 进入的页，必须声明 parents
//
// 深链策略：
//   allow          —— 允许被外部深链打开
//   require-params —— 允许深链，但必须带齐 params，否则页面会进错误态
//   deny           —— 依赖不可靠的本地上下文（当前身份 / 上一次选择），深链无意义且会误导
const ROUTES = {
  // ── 入口域 ─────────────────────────────────────────────────────────
  'pages/index/index': {
    kind: 'root',
    parents: [],
    deepLink: 'allow',
    params: [],
    domain: 'entry',
    note: '身份选择入口，冷启动与 reLaunch 的落点'
  },

  // ── tabBar（5 项） ──────────────────────────────────────────────────
  'pages/shipper/shipper': {
    kind: 'tab', parents: [], deepLink: 'deny', params: [], domain: 'workspace',
    note: '货主工作台'
  },
  'pages/owner/owner': {
    kind: 'tab', parents: [], deepLink: 'deny', params: [], domain: 'workspace',
    note: '船东工作台'
  },
  'pages/trade/orders/orders': {
    kind: 'tab', parents: [], deepLink: 'deny', params: [], domain: 'trade',
    note: '订单列表'
  },
  'pages/port/port': {
    kind: 'tab', parents: [], deepLink: 'deny', params: [], domain: 'port',
    note: '港口工作台'
  },
  'pages/mine/mine': {
    kind: 'tab', parents: [], deepLink: 'deny', params: [], domain: 'mine',
    note: '我的；委托发货入口挂在这里'
  },

  // ── 发布域 ─────────────────────────────────────────────────────────
  'pages/publish/cargo/cargo': {
    kind: 'detail',
    parents: ['pages/assistant/assistant'],
    deepLink: 'allow',
    params: [],
    domain: 'publish',
    note: '发布货源'
  },
  'pages/publish/ship/ship': {
    kind: 'detail',
    parents: ['pages/mine/mine', 'pages/owner/owner'],
    deepLink: 'allow',
    params: [],
    domain: 'publish',
    note: '发布空船'
  },

  // ── 交易域 ─────────────────────────────────────────────────────────
  'pages/trade/match/match': {
    kind: 'detail',
    parents: ['pages/publish/cargo/cargo', 'pages/owner/owner', 'pages/shipper/shipper'],
    deepLink: 'require-params',
    params: ['mode', 'refId'],
    domain: 'trade',
    note: '撮合结果；缺 mode/refId 会取不到候选'
  },
  'pages/trade/contract/contract': {
    kind: 'detail',
    parents: ['pages/trade/orders/orders'],
    deepLink: 'require-params',
    params: ['order_id'],
    domain: 'trade',
    note: '合同预览'
  },
  'pages/trade/payment/payment': {
    kind: 'detail',
    parents: ['pages/trade/orders/orders'],
    deepLink: 'require-params',
    params: ['order_id'],
    domain: 'trade',
    note: '支付详情'
  },

  // ── 港口域 ─────────────────────────────────────────────────────────
  'pages/port/appt/appt': {
    kind: 'detail',
    parents: ['pages/port/port'],
    deepLink: 'require-params',
    params: ['id'],
    domain: 'port',
    note: '泊位预约'
  },
  'pages/port/berth/berth': {
    kind: 'detail',
    parents: ['pages/port/port', 'pages/port/appt/appt'],
    deepLink: 'require-params',
    params: ['id'],
    domain: 'port',
    note: '泊位详情'
  },

  // ── 公共域 ─────────────────────────────────────────────────────────
  'pages/assistant/assistant': {
    kind: 'detail',
    parents: ['pages/mine/mine', 'pages/owner/owner', 'pages/shipper/shipper'],
    deepLink: 'require-params',
    params: ['mode'],
    domain: 'shared',
    // ⚠️ 已知静态盲区：`utils/agent-entry.js` 的 openSmartEntry() 也会跳到这里，
    //    但它的**调用方页面**无法静态解析（工具函数），故此处只声明可确认的页面来源。
    //    新增页面若调用 openSmartEntry，请把该页面补进 parents。
    note: '智能客服 / 统一入口（mode=parse|search）'
  },
  'pages/preview/preview': {
    kind: 'detail',
    parents: ['pages/publish/cargo/cargo'],
    deepLink: 'allow',
    params: [],
    domain: 'shared',
    note: '占位预览页'
  },

  // ── 委托发货域（ENT-xxx） ───────────────────────────────────────────
  'pages/entrust/workbench/workbench': {
    kind: 'detail',
    parents: ['pages/mine/mine'],
    deepLink: 'deny',
    params: [],
    domain: 'entrust',
    // 深链 deny 的理由：工作台首屏要判定「组织身份」，依赖服务端探测 + 上次选择
    // 的 Storage（`entrust_active_org`），深链进来必然经过选择态，没有意义。
    note: '委托工作台（由「我的」入口进入）'
  },
  'pages/entrust/detail/detail': {
    kind: 'detail',
    parents: ['pages/entrust/workbench/workbench'],
    deepLink: 'require-params',
    params: ['assignment_id'],
    domain: 'entrust',
    note: '委托详情'
  }
}

// tabBar 页集合（供运行期与 CI 共用，避免各页各写一份）
const TAB_BAR_PAGES = Object.keys(ROUTES).filter((p) => ROUTES[p].kind === 'tab')

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

/** 查一条路由；未登记返回 null */
function routeOf(url) {
  const p = normalize(url)
  return Object.prototype.hasOwnProperty.call(ROUTES, p) ? ROUTES[p] : null
}

/** 是否 tabBar 页（用 switchTab 还是 navigateTo 的判据） */
function isTabBarPage(url) {
  return TAB_BAR_PAGES.indexOf(normalize(url)) !== -1
}

/**
 * 声明链深：从「全新栈的首个页面」到该页面最少要叠几层。
 * tab / root 进入即重置栈，故为 1；detail 为 1 + 所有 parents 的最大链深。
 * 环或未登记 parent 返回 Infinity（CI 会单独报出来，不要靠它兜底）。
 */
function chainDepth(url, seen) {
  const p = normalize(url)
  const r = routeOf(p)
  if (!r) return Infinity
  if (r.kind === 'tab' || r.kind === 'root') return 1
  const visited = seen || []
  if (visited.indexOf(p) !== -1) return Infinity
  let max = 0
  for (const parent of r.parents || []) {
    const d = chainDepth(parent, visited.concat([p]))
    if (d > max) max = d
  }
  return max + 1
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
 * 决定「该用哪个导航 API、目标 url 长什么样」，不真正跳转。
 * 返回 { action, url, reason }；action 为 'blocked' 表示路由未登记。
 */
function resolveNavigation(url) {
  const p = normalize(url)
  const r = routeOf(p)
  if (!r) {
    return { action: 'blocked', path: p, url: p, reason: `未登记的路由：${p}` }
  }
  if (r.kind === 'tab') {
    // switchTab 不接受查询串：带上会被忽略甚至报错，这里主动剥掉并提示。
    return { action: 'switchTab', path: p, url: '/' + p, reason: '' }
  }
  const query = queryOf(url)
  return { action: 'navigateTo', path: p, url: '/' + p + (query ? '?' + query : ''), reason: '' }
}

/**
 * 统一导航入口：自动选 switchTab / navigateTo，并在栈快满时退化为 redirectTo。
 *
 * 为什么退化而不是直接失败：`navigateTo` 撞上限时用户只看到「点了没反应」。
 * redirectTo 会**替换当前页**——用户仍能看到目标页（代价是返回键少一层），
 * 这比无反馈好，且会在 console 留下可归因的警告。
 *
 * @param {string} url 目标（带 `/` 前缀与查询串，与 wx 原生写法一致）
 * @param {object} [opts] fail: 失败回调；redirectOnFull: 传 false 可禁用退化
 * @returns {boolean} 是否已发起导航
 */
function go(url, opts) {
  const options = opts || {}
  const plan = resolveNavigation(url)
  if (plan.action === 'blocked') {
    // 未登记就跳，等于绕过注册表；这里显式拦住并留下可搜索的日志。
    console.warn('[routes] ' + plan.reason)
    if (typeof wx !== 'undefined' && wx.showToast) {
      wx.showToast({ title: options.blockedTip || '页面暂不可用', icon: 'none' })
    }
    return false
  }
  if (typeof wx === 'undefined') return false

  if (plan.action === 'switchTab') {
    wx.switchTab({ url: plan.url, fail: options.fail })
    return true
  }

  const depth = currentDepth()
  if (depth >= MAX_STACK - 1 && options.redirectOnFull !== false) {
    console.warn(
      '[routes] 页面栈 ' + depth + '/' + MAX_STACK + '，' + plan.path + ' 退化为 redirectTo'
    )
    wx.redirectTo({ url: plan.url, fail: options.fail })
    return true
  }
  wx.navigateTo({ url: plan.url, fail: options.fail })
  return true
}

/**
 * 深链是否可接受（供页面 onLoad 自检 / 分享路径判断）。
 * @returns {{ok: boolean, reason: string}}
 */
function canDeepLink(url) {
  const p = normalize(url)
  const r = routeOf(p)
  if (!r) return { ok: false, reason: `未登记的路由：${p}` }
  if (r.deepLink === 'deny') return { ok: false, reason: `${p} 声明为不可深链` }
  if (r.deepLink === 'require-params') {
    const want = r.params || []
    const have = queryOf(url)
      .split('&')
      .map((kv) => kv.split('=')[0])
      .filter(Boolean)
    const missing = want.filter((k) => have.indexOf(k) === -1)
    if (missing.length) return { ok: false, reason: `缺少必需参数：${missing.join(', ')}` }
  }
  return { ok: true, reason: '' }
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
  ROUTES,
  TAB_BAR_PAGES,
  normalize,
  queryOf,
  routeOf,
  isTabBarPage,
  chainDepth,
  currentDepth,
  resolveNavigation,
  go,
  canDeepLink,
  countByDomain
}
