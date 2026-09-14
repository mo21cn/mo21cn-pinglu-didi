#!/usr/bin/env node
/**
 * tabBar 目标行为校验（独立于 verify_routes / verify_routes_behavior）
 *
 * ── 为什么必须有这一层 ────────────────────────────────────────────────
 * `verify_routes.js` 只能解析**字面量**目标。凡目标由变量算出 ——
 * `ROLE_META[role].page`、`map[role] || ...`、`conf[role] || conf.all` ——
 * 它只能记一条 `[变量目标]` 备注就放行。
 *
 * 于是「动态 switchTab 调用点」长期处在**静态门禁全绿、但没人验过**的状态：
 * `wx.switchTab` 打到一个不在 `app.json` `tabBar.list` 里的页面**不会抛错**
 * （静默失败），用户只看到"点了没反应 / 回不去"，而 CI 是绿的。
 *
 * ── 做法 ─────────────────────────────────────────────────────────────
 * 不给静态解析器加变量求值（那是无底洞），而是**驱动真实页面 / 组件逻辑**：
 * 桩掉 `wx` / `Page` / `Component` / `getCurrentPages`，加载真实 JS，
 * 按真实入口调用（`onLoad` / `onPickRole` / `onTap` / `onSwitchRole`），
 * 捕获 `wx.switchTab` 的 url，再与 `app.json` 的 `tabBar.list` 比对。
 *
 * ── 覆盖面（HO 项 5 第 4 条点名）─────────────────────────────────────
 *   · pages/index/index.js     —— 已有登录态（onLoad 直接进工作台）× 两种角色
 *   · pages/index/index.js     —— 选身份卡（onPickRole）× 两种角色
 *   · custom-tab-bar/index.js  —— 位置 1/2/4/5（switchTab）× 两种角色
 *   · custom-tab-bar/index.js  —— 位置 3「发布」（凸起钮，navigateTo）× 两种角色
 *   · pages/mine/mine.js       —— 账号区切换身份（onSwitchRole）× 两种角色
 *
 * ── 与 verify_routes.js 的分工是**刻意的**，不是遗漏 ──────────────────
 * `verify_routes.js` 仍**保留**「[变量目标]」备注：它是对所有动态调用的**中立记录**，
 * 目的是"让人看得见有哪些动态调用"，不是判错。
 * 本脚本只对**已登记的调用点**做取值域封闭断言；将来有人新增动态 switchTab
 * 却没纳入本脚本时，那条静态备注仍在（不会因为"本脚本存在"就消失）。
 * ⇒ 不把所有动态调用一律判错，只把**能算出取值域的**那几处钉死。
 *
 * 用法：node scripts/verify_tabbar_targets.js
 * 退出码：0 通过 / 1 有 FAIL
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniapp')

const appJson = JSON.parse(fs.readFileSync(path.join(MP, 'app.json'), 'utf8'))
const APP_PAGES = appJson.pages || []
const TAB_BAR_URLS = (((appJson.tabBar || {}).list) || []).map((t) => '/' + t.pagePath)
const REGISTERED_URLS = APP_PAGES.map((p) => '/' + p)

let N_OK = 0
const FAILS = []
const ok = (label) => { N_OK++; console.log('  [ok] ' + label) }
const fail = (label, extra) => {
  FAILS.push(label + (extra ? ' → ' + extra : ''))
  console.log('  [FAIL] ' + label + (extra ? '  → ' + extra : ''))
}
const check = (label, cond, extra) => (cond ? ok(label) : fail(label, extra))
const section = (t) => console.log('\n=== ' + t + ' ===')

// ---------------------------------------------------------------- 运行时桩
function makeWx() {
  const calls = {
    switchTab: [], navigateTo: [], reLaunch: [], redirectTo: [], navigateBack: [],
    toast: [], modal: [], loading: 0
  }
  const wx = {
    __calls: calls,
    switchTab: (o) => {
      calls.switchTab.push(o || {})
      // 真实 wx 的 success/fail 必须被调用到：index.js 的 `enter()` 把 resolve/reject
      // 挂在它们上面，不回调会让那层 Promise 永远 pending（断言变成空转）。
      if (o && o.success) o.success({ errMsg: 'switchTab:ok' })
    },
    navigateTo: (o) => {
      calls.navigateTo.push(o || {})
      if (o && o.success) o.success({ errMsg: 'navigateTo:ok' })
    },
    reLaunch: (o) => {
      calls.reLaunch.push(o || {})
      if (o && o.success) o.success({ errMsg: 'reLaunch:ok' })
    },
    redirectTo: (o) => calls.redirectTo.push(o || {}),
    navigateBack: (o) => calls.navigateBack.push(o || {}),
    showToast: (o) => calls.toast.push(o),
    showModal: (o) => {
      calls.modal.push(o)
      if (o && o.success) o.success({ confirm: true, cancel: false })
    },
    showLoading: () => { calls.loading++ },
    hideLoading() {},
    stopPullDownRefresh() {},
    setNavigationBarTitle() {},
    getStorageSync: () => '',
    setStorageSync() {},
    removeStorageSync() {},
    getWindowInfo: () => ({ statusBarHeight: 44, windowWidth: 375, windowHeight: 812 }),
    getSystemInfoSync: () => ({ statusBarHeight: 44, windowWidth: 375 }),
    createSelectorQuery: () => ({
      select: () => ({ boundingClientRect: () => ({ exec: (cb) => cb && cb([{}]) }) }),
      exec() {}
    }),
    nextTick: (fn) => fn()
  }
  return wx
}

/**
 * 加载页面 / 组件 JS，返回它交给 `Page()` / `Component()` 的配置对象。
 *
 * 不 require 真实依赖：小程序模块路径（`../../utils/auth`）在 Node 下解析不到。
 * 用 `requireMap` 按**子串**映射到桩，键顺序即优先级。
 */
function loadConfig(file, kind, wx, opts) {
  const src = fs.readFileSync(file, 'utf8')
  const requireMap = (opts && opts.requireMap) || {}
  const pages = (opts && opts.pages) || []
  let cfg = null
  const reg = (c) => { cfg = c }
  const req = (p) => {
    const s = String(p)
    for (const key of Object.keys(requireMap)) {
      if (s.indexOf(key) !== -1) return requireMap[key]
    }
    return {}
  }
  const getApp = () => ({ globalData: {}, routeByRole: () => '/pages/index/index' })
  const getCurrentPages = () => pages
  if (kind === 'component') {
    new Function('require', 'Component', 'wx', 'getApp', 'getCurrentPages', src)(
      req, reg, wx, getApp, getCurrentPages
    )
  } else {
    new Function('require', 'Page', 'wx', 'getApp', 'getCurrentPages', src)(
      req, reg, wx, getApp, getCurrentPages
    )
  }
  return cfg
}

/** 造一个实例：页面方法在顶层，组件方法在 `methods` 里（这里都摊平到实例上） */
function instantiate(cfg, extra) {
  const self = Object.assign({}, cfg)
  if (cfg.methods) Object.assign(self, cfg.methods)
  self.data = Object.assign(JSON.parse(JSON.stringify(cfg.data || {})), extra || {})
  self.setData = function (patch, cb) {
    Object.keys(patch).forEach((k) => {
      if (k.indexOf('.') !== -1) {
        const parts = k.split('.')
        let cur = self.data
        for (let i = 0; i < parts.length - 1; i++) {
          if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {}
          cur = cur[parts[i]]
        }
        cur[parts[parts.length - 1]] = patch[k]
      } else {
        self.data[k] = patch[k]
      }
    })
    if (cb) cb()
  }
  self.events = []
  self.triggerEvent = function (name, detail) { self.events.push({ name, detail }) }
  return self
}

const flush = () => new Promise((r) => setImmediate(r))

// ---------------------------------------------------------------- 校验器
/**
 * 校验一组 switchTab 目标是否都落在 `app.json` 的 `tabBar.list` 内。
 *
 * 抽成独立函数的目的：能对**合成输入**跑一遍（见 §3「校验器自检」）。
 * 只拿真实调用跑，无法证明校验器本身不是空转的 —— 而"空转的门禁"比没有门禁更糟，
 * 因为它会让人以为这块被验过了。
 */
function auditSwitchTabTargets(calls, allowed) {
  const bad = []
  for (const c of calls) {
    if (!c.url) {
      bad.push(c.site + ': switchTab 未给出 url')
      continue
    }
    if (allowed.indexOf(c.url) === -1) {
      bad.push(c.site + ': switchTab -> ' + c.url + '（不在 app.json tabBar.list）')
    }
  }
  return bad
}

/** 截取从 `(` 起**括号配对**的实参文本（跳过字符串，避免其中的括号带偏计数） */
function parenArgs(src, openIdx) {
  let depth = 0
  let i = openIdx
  while (i < src.length) {
    const ch = src[i]
    if (ch === "'" || ch === '"' || ch === '`') {
      const q = ch
      i++
      while (i < src.length && src[i] !== q) {
        if (src[i] === '\\') i++
        i++
      }
      i++
      continue
    }
    if (ch === '(') depth++
    else if (ch === ')') {
      depth--
      if (depth === 0) return src.slice(openIdx, i + 1)
    }
    i++
  }
  return src.slice(openIdx)
}

/**
 * 静态审计：`wx.switchTab(...)` 实参里出现的**每个路由字面量**都必须是 tabBar 页。
 *
 * 这条抓的是"兜底分支打歪"这一整类 bug —— 例如
 * `wx.switchTab({ url: map[role] || '/pages/index/index' })`：
 * 正常路径解析不出问题，但一旦哪个角色漏了映射，就会静默切到一个非 tabBar 页。
 * 只检查 `'/pages/` 开头的字面量；其余字符串（属性名等）不参与。
 */
function auditSwitchTabLiterals(src, allowed) {
  const bad = []
  const CALL = /wx\s*\.\s*switchTab\s*\(/g
  let m
  while ((m = CALL.exec(src)) !== null) {
    const args = parenArgs(src, m.index + m[0].length - 1)
    const LIT = /'([^'\n]*)'|"([^"\n]*)"/g
    let l
    while ((l = LIT.exec(args)) !== null) {
      const v = l[1] !== undefined ? l[1] : l[2]
      if (v.indexOf('/pages/') !== 0) continue
      if (allowed.indexOf(v) === -1) {
        bad.push('switchTab 实参里的字面量 ' + v + ' 不是 tabBar 页（静默失败风险）')
      }
    }
  }
  return bad
}

/** 列出所有含 `wx.switchTab` 的小程序 JS 文件（相对 miniapp 的 posix 路径） */
function filesWithSwitchTab() {
  const out = []
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      if (name === 'node_modules' || name === 'miniprogram_npm' || name === '.git') continue
      const p = path.join(dir, name)
      if (fs.statSync(p).isDirectory()) walk(p)
      else if (name.endsWith('.js')) {
        if (fs.readFileSync(p, 'utf8').indexOf('wx.switchTab') !== -1) {
          out.push(path.relative(MP, p).replace(/\\/g, '/'))
        }
      }
    }
  }
  walk(MP)
  return out.sort()
}

// ---------------------------------------------------------------- 驱动
const ROLES = ['shipper', 'owner']
const IDX = path.join(MP, 'pages', 'index', 'index.js')
const MINE = path.join(MP, 'pages', 'mine', 'mine.js')
const TABBAR = path.join(MP, 'custom-tab-bar', 'index.js')

function idxRequireMap(role, loggedIn) {
  return {
    auth: {
      isLoggedIn: () => loggedIn,
      getUser: () => ({ user_id: role === 'owner' ? 2 : 1, current_role: role, roles: [role] }),
      // 恒 false（= 没换账号）→ onLoad 会继续走到 switchTab；换账号那条路另有断言覆盖
      ensureDevAccount: () => false,
      enterRole: () => Promise.resolve(null)
    },
    request: {
      request: () => Promise.resolve({}),
      describeError: () => ({ cause: '', hint: '' })
    }
  }
}

function mineRequireMap(role) {
  return {
    auth: {
      getUser: () => ({ user_id: role === 'owner' ? 2 : 1, current_role: role }),
      clearUser: () => {},
      enterRole: () => Promise.resolve(null)
    },
    tabbar: { syncTabBar() {} },
    entrust: { probeEntry: () => Promise.resolve({ visible: false, hint: '' }) }
  }
}

function tabBarRequireMap(role) {
  return {
    auth: { getUser: () => ({ user_id: role === 'owner' ? 2 : 1, current_role: role }) }
  }
}

/** 已有登录态：onLoad 里直接进上次的工作台 */
function driveIndexOnLoad(role) {
  const wx = makeWx()
  const cfg = loadConfig(IDX, 'page', wx, { requireMap: idxRequireMap(role, true) })
  const pg = instantiate(cfg)
  pg.onLoad()
  return {
    site: 'pages/index/index.js: onLoad（已有登录态，role=' + role + '）',
    calls: wx.__calls.switchTab.map((o) => ({ url: o.url }))
  }
}

/** 选身份卡：onPickRole → auth.enterRole().then(enter) → switchTab */
async function driveIndexOnPickRole(role) {
  const wx = makeWx()
  const cfg = loadConfig(IDX, 'page', wx, { requireMap: idxRequireMap(role, true) })
  const pg = instantiate(cfg)
  pg.onPickRole({ currentTarget: { dataset: { role } } })
  await flush()
  return {
    site: 'pages/index/index.js: onPickRole（选身份卡，role=' + role + '）',
    calls: wx.__calls.switchTab.map((o) => ({ url: o.url }))
  }
}

/** 自定义 tabBar：refresh() 后按 key 点击 */
function driveTabBarOnTap(role, key) {
  const wx = makeWx()
  const cfg = loadConfig(TABBAR, 'component', wx, {
    requireMap: tabBarRequireMap(role),
    pages: [{ route: 'pages/shipper/shipper' }]
  })
  const bar = instantiate(cfg)
  bar.refresh()
  bar.onTap({ currentTarget: { dataset: { key } } })
  return {
    site: 'custom-tab-bar/index.js: onTap(key=' + key + ', role=' + role + ')',
    list: bar.data.list,
    switchCalls: wx.__calls.switchTab.map((o) => ({ url: o.url })),
    navCalls: wx.__calls.navigateTo.map((o) => ({ url: o.url }))
  }
}

/** 我的页：账号区切换身份 */
async function driveMineOnSwitchRole(role) {
  const wx = makeWx()
  const cfg = loadConfig(MINE, 'page', wx, { requireMap: mineRequireMap(role) })
  const pg = instantiate(cfg, { currentRole: role === 'owner' ? 'shipper' : 'owner' })
  pg.onSwitchRole({ currentTarget: { dataset: { role } } })
  await flush()
  return {
    site: 'pages/mine/mine.js: onSwitchRole（role=' + role + '）',
    calls: wx.__calls.switchTab.map((o) => ({ url: o.url }))
  }
}

// ---------------------------------------------------------------- 主流程
async function main() {
  console.log('tabBar 目标行为校验')
  console.log('app.json tabBar.list：' + TAB_BAR_URLS.join(' / '))

  const switchGroups = []
  const navGroups = []

  // ── §1 真实调用点（行为驱动）───────────────────────────────────────
  section('§1 动态 switchTab 调用点（驱动真实页面 / 组件逻辑）')

  for (const role of ROLES) {
    switchGroups.push(driveIndexOnLoad(role))
  }
  for (const role of ROLES) {
    switchGroups.push(await driveIndexOnPickRole(role))
  }
  for (const role of ROLES) {
    for (const key of ['find', 'order', 'port', 'mine']) {
      const r = driveTabBarOnTap(role, key)
      switchGroups.push({ site: r.site, calls: r.switchCalls })
    }
  }
  for (const role of ROLES) {
    switchGroups.push(await driveMineOnSwitchRole(role))
  }
  for (const role of ROLES) {
    const r = driveTabBarOnTap(role, 'publish')
    navGroups.push({ site: r.site, calls: r.navCalls })
  }

  // 每个调用点必须**真的**产生一次跳转：少了说明入口没被驱动到，
  // 断言会静默变成空转（这正是本脚本要避免的那类失效）。
  for (const g of switchGroups) {
    check('调用点真被驱动到：' + g.site, g.calls.length === 1,
      'captured=' + JSON.stringify(g.calls))
  }
  for (const g of navGroups) {
    check('发布按钮真被驱动到：' + g.site, g.calls.length === 1,
      'captured=' + JSON.stringify(g.calls))
  }

  // ── §2 取值域封闭断言 ─────────────────────────────────────────────
  section('§2 捕获到的目标必须落在 app.json 的 tabBar.list 内')
  const allSwitch = []
  for (const g of switchGroups) {
    for (const c of g.calls) allSwitch.push({ site: g.site, url: c.url })
  }
  const badSwitch = auditSwitchTabTargets(allSwitch, TAB_BAR_URLS)
  check('全部 ' + allSwitch.length + ' 条动态 switchTab 目标都是 tabBar 页', badSwitch.length === 0,
    badSwitch.join(' / '))

  const allNav = []
  for (const g of navGroups) {
    for (const c of g.calls) allNav.push({ site: g.site, url: c.url })
  }
  const badNav = allNav.filter((c) => REGISTERED_URLS.indexOf(c.url) === -1)
  check('发布按钮的 ' + allNav.length + ' 条 navigateTo 目标都已登记进 app.json',
    badNav.length === 0, badNav.map((c) => c.site + ' -> ' + c.url).join(' / '))
  const navToTab = allNav.filter((c) => TAB_BAR_URLS.indexOf(c.url) !== -1)
  check('发布按钮目标是二级页（tabBar 页不能用 navigateTo 进）', navToTab.length === 0,
    navToTab.map((c) => c.url).join(' / '))

  const covered = TAB_BAR_URLS.filter((u) => allSwitch.some((c) => c.url === u))
  check('5 个 tabBar 位都有动态调用点覆盖（' + covered.length + '/' + TAB_BAR_URLS.length + '）',
    covered.length === TAB_BAR_URLS.length,
    '未覆盖：' + TAB_BAR_URLS.filter((u) => covered.indexOf(u) === -1).join(' / '))

  // ── §3 静态：switchTab 实参里的字面量不得打歪 ──────────────────────
  section('§3 switchTab 实参里的路由字面量都必须是 tabBar 页（抓兜底分支打歪）')
  const scanFiles = filesWithSwitchTab()
  console.log('  扫描 ' + scanFiles.length + ' 个含 wx.switchTab 的文件')
  for (const rel of scanFiles) {
    const src = fs.readFileSync(path.join(MP, rel), 'utf8')
    const bad = auditSwitchTabLiterals(src, TAB_BAR_URLS)
    check(rel + ' 的 switchTab 字面量目标合法', bad.length === 0, bad.join(' / '))
  }

  // ── §4 校验器自检（非法目标失败用例）──────────────────────────────
  // 前两节全绿可能是"校验器根本不会红"。这里用合成输入证明它会红、也会绿。
  section('§4 校验器自检：非法目标必须被拒（注入必红 / 合法必绿）')
  const negBad = auditSwitchTabTargets(
    [{ site: 'synthetic', url: '/pages/index/index' }], TAB_BAR_URLS
  )
  check('注入非 tabBar 目标 → 校验器报错（注入必红）',
    negBad.length === 1 && /pages\/index\/index/.test(negBad[0]), negBad.join(' / '))
  const negMissing = auditSwitchTabTargets([{ site: 'synthetic', url: '' }], TAB_BAR_URLS)
  check('注入空目标 → 校验器报错（缺 url 也要红）', negMissing.length === 1, negMissing.join(' / '))
  const negOk = auditSwitchTabTargets(
    [{ site: 'synthetic', url: TAB_BAR_URLS[0] }], TAB_BAR_URLS
  )
  check('注入合法 tabBar 目标 → 校验器放行（合法必绿）', negOk.length === 0, negOk.join(' / '))

  const litBad = auditSwitchTabLiterals(
    "wx.switchTab({ url: map[role] || '/pages/index/index' })", TAB_BAR_URLS
  )
  check('字面量审计：兜底到非 tabBar 页 → 报错（注入必红）',
    litBad.length === 1 && /pages\/index\/index/.test(litBad[0]), litBad.join(' / '))
  const litOk = auditSwitchTabLiterals(
    "wx.switchTab({ url: '/pages/owner/owner' })", TAB_BAR_URLS
  )
  check('字面量审计：合法 tabBar 字面量 → 放行（合法必绿）', litOk.length === 0, litOk.join(' / '))
  const litVar = auditSwitchTabLiterals('wx.switchTab({ url })', TAB_BAR_URLS)
  check('字面量审计：纯变量目标 → 不判错（保留变量目标备注，不一律判错）',
    litVar.length === 0, litVar.join(' / '))

  // ── 汇总 ────────────────────────────────────────────────────────
  console.log('')
  console.log('动态调用点：switchTab ' + switchGroups.length + ' 处 / 发布按钮 ' + navGroups.length + ' 处')
  console.log('捕获 switchTab 目标 ' + allSwitch.length + ' 条 / navigateTo 目标 ' + allNav.length + ' 条')
  console.log('通过 ' + N_OK + ' 项')
  if (FAILS.length) {
    console.log('\n✗ 发现 ' + FAILS.length + ' 个问题：')
    for (const f of FAILS) console.log('  - ' + f)
    process.exit(1)
  }
  console.log('\n✓ tabBar 目标校验通过')
}

main().catch((e) => {
  console.error('\n✗ 脚本异常：' + ((e && e.stack) || e))
  process.exit(1)
})
