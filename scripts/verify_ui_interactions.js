/**
 * UI 交互契约校验（不需要微信开发者工具）——覆盖 2026-09-11 UI 打磨轮的 4 项改动，
 * 以及一类会静默失效的写法（wx.showActionSheet 传超过 6 项）。
 *
 * 为什么需要它：`wx.showActionSheet` 的 itemList 上限是 6，超过会直接 fail，
 * 而页面通常没有 fail 兜底 → 表现为「点了没反应 / 没有下拉选择」。
 * 这类缺陷静态脚本（verify_miniapp.js）抓不到，真机走查又要 IDE，
 * 所以用「加载页面 JS + 桩掉 wx/require + 驱动真实方法」的方式在 CI 里守住。
 *
 * 校验内容：
 *   ① port-picker 组件（13 项港口弹层）的 props/事件契约
 *   ② 货主页「查看匹配船源」的选择闭环（缺港口 → 拉起面板 → 选完自动继续）
 *   ③ 发布货物 / 发布空船 / 船东页的港口选择是否全部改走组件
 *   ④ 我的页「退出」出口、货主页与船东页切角色弹窗不含「港口方」
 *   ⑤ 静态防线：showActionSheet 长列表、组件注册、底栏按钮居中、页面底部留白
 *   ⑥ 身份选择（仅货主/船东）· 登录链路顺序（login→bindRole→switchRole）·
 *      订单页/支付页：订单自带 cargo/ship 摘要时零富化请求，缺失时才按角色
 *      回退单侧（防跨角色 403）
 *   ⑦ 失败可定位（describeError 分类 · 失败环节点名）· 开发期身份稳定（不依赖 wx.login）·
 *      /healthz 连通性预检
 *
 * 用法：node scripts/verify_ui_interactions.js     （退出码 0=全绿，1=有失败）
 * 方法论见 skill：miniapp-page-logic-verification
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniapp')

let N_OK = 0
const FAILS = []
const ok = (label) => { N_OK++; console.log('  [ok] ' + label) }
const fail = (label, extra) => {
  FAILS.push(label + (extra ? ' → ' + extra : ''))
  console.log('  [FAIL] ' + label + (extra ? '  → ' + extra : ''))
}
const check = (label, cond, extra) => (cond ? ok(label) : fail(label, extra))
const section = (t) => console.log('\n=== ' + t + ' ===')

const read = (p) => fs.readFileSync(path.join(MP, p), 'utf8')

// ---------------------------------------------------------------- 运行时桩
function makeWx() {
  const calls = { actionSheet: [], modal: [], toast: [], switchTab: [], reLaunch: [], navigateTo: [] }
  const wx = {
    __calls: calls,
    showActionSheet: (o) => { calls.actionSheet.push(o); if (o && o.success) o.success({ tapIndex: 0 }) },
    showModal: (o) => {
      calls.modal.push(o)
      if (o && o.success) o.success({ confirm: o.__confirm === false ? false : true, cancel: false })
    },
    showToast: (o) => calls.toast.push(o),
    showLoading() {}, hideLoading() {}, stopPullDownRefresh() {}, setNavigationBarTitle() {},
    switchTab: (o) => calls.switchTab.push(o),
    reLaunch: (o) => calls.reLaunch.push(o),
    navigateTo: (o) => calls.navigateTo.push(o),
    redirectTo() {}, setClipboardData() {}, pageScrollTo() {},
    getWindowInfo: () => ({ statusBarHeight: 44, windowWidth: 375, windowHeight: 812 }),
    getSystemInfoSync: () => ({ statusBarHeight: 44, windowWidth: 375 }),
    getStorageSync: () => '', setStorageSync() {}, removeStorageSync() {},
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: () => ({ exec: (cb) => cb && cb([{}]) }) }), exec() {} }),
    nextTick: (fn) => fn(),
  }
  return wx
}

function makeRequire(wx, reqLog, authState) {
  const realPorts = require(path.join(MP, 'utils', 'ports.js'))
  // 纯常量/纯函数模块直接加载真实实现（P3-4 收敛后页面从这两个文件取数）
  const realConstants = require(path.join(MP, 'utils', 'constants.js'))
  const realDates = require(path.join(MP, 'utils', 'dates.js'))
  const AUTH = authState || { loggedIn: true, user: { user_id: 1, current_role: 'shipper', roles: ['shipper'] } }
  return (p) => {
    const s = String(p)
    if (s.indexOf('utils/ports') !== -1) return realPorts
    if (s.indexOf('utils/constants') !== -1) return realConstants
    if (s.indexOf('utils/dates') !== -1) return realDates
    if (s.indexOf('auth') !== -1) {
      const A = {
        getUser: () => JSON.parse(JSON.stringify(AUTH.user)),
        isLoggedIn: () => AUTH.loggedIn,
        clearUser: () => reqLog.push('clearUser'),
        // 开发期「角色 → 演示账号」映射（真实实现见 utils/auth.js 的 DEV_ROLE_CODE）。
        // 本桩恒返回 false（= 没换账号），故不进 reqLog、不影响下面的链路顺序断言；
        // 映射本身的断言在 ⑥ 末尾用真实 auth.js 直接验。
        ensureDevAccount: () => false,
        switchRole: (r) => {
          reqLog.push('switchRole:' + r)
          if (AUTH.failSwitch) return Promise.reject(new Error('switch fail'))
          AUTH.user.current_role = r
          return Promise.resolve({ current_role: r })
        },
        login: () => {
          reqLog.push('login')
          if (AUTH.failLogin) return Promise.reject(new Error('login fail'))
          AUTH.loggedIn = true
          return Promise.resolve({})
        },
        bindRole: (r) => {
          reqLog.push('bindRole:' + r)
          if (AUTH.failBind) return Promise.reject(new Error('bind fail'))
          if ((AUTH.user.roles || []).indexOf(r) === -1) AUTH.user.roles = (AUTH.user.roles || []).concat([r])
          return Promise.resolve({ roles: AUTH.user.roles })
        },
        // 与 utils/auth.js 的 enterRole 同构：镜像分支、调用顺序、以及
        // 失败时的 err.stage 标记（index.js 靠它点名「哪一步失败」，缺了就会
        // 一律显示「进入工作台」）。真实实现的端到端行为由 verify_login_flow.js 验证。
        enterRole: (r) => {
          const mark = (name, p) =>
            p.catch((err) => {
              if (err && !err.stage) err.stage = name
              throw err
            })
          if (!AUTH.loggedIn) {
            return mark('login', A.login())
              .then(() => mark('bind', A.bindRole(r)))
              .then(() => mark('switch', A.switchRole(r)))
          }
          if ((AUTH.user.roles || []).indexOf(r) === -1) {
            return mark('bind', A.bindRole(r)).then(() => mark('switch', A.switchRole(r)))
          }
          if (AUTH.user.current_role !== r) return mark('switch', A.switchRole(r))
          return Promise.resolve(null)
        },
        ROLE_LABELS: { shipper: '货主', owner: '船东', port: '港口方' },
      }
      return A
    }
    if (s.indexOf('tabbar') !== -1) return { syncTabBar() {} }
    // 统一智能入口（F19/F20）：最小同构桩 —— 记录调用。
    // 「openSmartEntry 打开智能搜索页」与四类意图的渲染分支由 ⑧ 章在真实页面上断言。
    if (s.indexOf('agent-entry') !== -1) {
      return {
        openSmartEntry: (o) => {
          reqLog.push('openSmartEntry')
          wx.navigateTo({ url: '/pages/assistant/assistant?mode=search', fail: () => {} })
        },
        runComplianceCheck: (url, body) => {
          reqLog.push('compliance ' + url + ' ' + JSON.stringify(body || {}))
          if (AUTH.compliancePayload) {
            wx.showModal({ title: '合规预检', content: AUTH.compliancePayload.summary || '' })
          }
        },
        showComplianceModal: (r) => wx.showModal({ title: '合规预检', content: (r && r.summary) || '' }),
      }
    }
    if (s.indexOf('request') !== -1) {
      return {
        request: (o) => {
          reqLog.push((o.method || 'GET') + ' ' + o.url)
          if (AUTH.failRequest) return Promise.reject(new Error('request:fail fail to connect'))
          // 订单端点可注入载荷：用于区分「订单自带 cargo/ship 摘要 → 零富化」
          // 与「摘要缺失 → 回退单侧」两条路径。
          if (AUTH.orderPayload && /\/order\/orders(\/|$|\?)/.test(o.url)) {
            return Promise.resolve(JSON.parse(JSON.stringify(AUTH.orderPayload)))
          }
          // 货源解析端点可注入载荷（F14 解析态断言用）
          if (AUTH.parsePayload && /\/agent\/cargo-parse$/.test(o.url)) {
            return Promise.resolve(JSON.parse(JSON.stringify(AUTH.parsePayload)))
          }
          // 统一入口端点可注入载荷（F20 搜索态四类意图断言用）
          if (AUTH.routePayload && /\/agent\/route$/.test(o.url)) {
            return Promise.resolve(JSON.parse(JSON.stringify(AUTH.routePayload)))
          }
          return Promise.resolve({ id: 99, total: 0, items: [] })
        },
        getToken: () => 't', setToken() {}, clearToken() {}, BASE_URL: 'http://127.0.0.1:8000',
        // 与 utils/request.js 的真实实现同构：把原始信息翻译成 cause/hint
        describeError: (err) => ({ cause: (err && (err.errMsg || err.message)) || '未知错误', hint: '' }),
      }
    }
    return {}
  }
}

/** 加载页面/组件 JS，返回其配置对象 */
function loadConfig(file, kind, wx, reqLog, authState) {
  const src = fs.readFileSync(file, 'utf8')
  let cfg = null
  const req = makeRequire(wx, reqLog, authState)
  const getApp = () => ({ globalData: {}, routeByRole: () => '/pages/index/index' })
  if (kind === 'component') {
    new Function('require', 'Component', 'wx', 'getApp', src)(req, (c) => { cfg = c }, wx, getApp)
  } else {
    new Function('require', 'Page', 'wx', 'getApp', src)(req, (c) => { cfg = c }, wx, getApp)
  }
  return cfg
}

function instantiate(cfg, extra) {
  const self = Object.assign({}, cfg)
  self.data = Object.assign(JSON.parse(JSON.stringify(cfg.data || {})), extra || {})
  self.setData = function (patch, cb) {
    Object.keys(patch).forEach((k) => {
      if (k.indexOf('.') !== -1) {
        // 'form.origin_port' 这类路径写法
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

// ---------------------------------------------------------------- ① 组件
section('① port-picker 组件契约')
{
  const PICKER = path.join(MP, 'components', 'port-picker', 'index.js')
  for (const ext of ['.js', '.json', '.wxml', '.wxss']) {
    check(`组件四件套存在 index${ext}`, fs.existsSync(PICKER.replace(/\.js$/, ext)))
  }
  const wx = makeWx()
  let cfg = null
  try { cfg = loadConfig(PICKER, 'component', wx, []) } catch (e) { fail('组件 JS 可加载', e.message) }
  check('组件 JS 可加载并捕获定义', !!cfg)
  if (cfg) {
    check('props 声明 visible/title/tip/current/ports', ['visible', 'title', 'tip', 'current', 'ports'].every((k) => cfg.properties && cfg.properties[k]))
    check('默认列表为 13 个港口', (cfg.data && cfg.data.list || []).length === 13, String((cfg.data && cfg.data.list || []).length))
    check('观察器监听 ports（筛选器可复用）', !!(cfg.observers && cfg.observers.ports))

    const self = instantiate(cfg)
    cfg.methods.onPick.call(self, { currentTarget: { dataset: { key: 'GGU' } } })
    const ev = self.events[0] || {}
    check('onPick 派发 select 事件', ev.name === 'select' && ev.detail && ev.detail.key === 'GGU' && ev.detail.label === '贵港', JSON.stringify(ev))
    cfg.methods.onMaskTap.call(self)
    cfg.methods.onCancelTap.call(self)
    check('遮罩/取消 均派发 close 事件', self.events.length === 3 && self.events[1].name === 'close' && self.events[2].name === 'close')

    self.setData({ list: [] })
    cfg.observers.ports.call(self, [{ key: '', label: '装货港（全部）' }].concat(cfg.data.list))
    check('observer 接受外部传入的 14 项列表', self.data.list.length === 14, String(self.data.list.length))
  }
}

// ---------------------------------------------------------------- ② 货主页闭环
section('② 货主页「查看匹配船源」选择闭环')
{
  const wx = makeWx()
  const reqLog = []
  const cfg = loadConfig(path.join(MP, 'pages/shipper/shipper.js'), 'page', wx, reqLog)
  check('shipper 页 JS 可加载', !!cfg)

  const baseForm = { cargo_name: '', cargo_type: 'bulk', weight_t: '', origin_port: 'NNG', origin_label: '南宁 · 平塘港', dest_port: '', dest_label: '', expect_date: '', offer_price: '' }

  // 缺卸货港 → 拉起面板（而不是只弹 toast）
  let self = instantiate(cfg, { form: Object.assign({}, baseForm) })
  wx.__calls.actionSheet.length = 0
  cfg.onFindShips.call(self)
  check('缺卸货港时拉起选择面板（ppAction=dest）', self.data.ppVisible === true && self.data.ppAction === 'dest', `visible=${self.data.ppVisible} action=${self.data.ppAction}`)
  check('港口选择不再走 showActionSheet', wx.__calls.actionSheet.length === 0, `calls=${wx.__calls.actionSheet.length}`)
  check('面板标题为「请选择卸货港」', self.data.ppTitle === '请选择卸货港', self.data.ppTitle)

  // 选完 → 写入 + 自动继续（缺吨数 → 展开货物资料并高亮）
  cfg.onPortPicked.call(self, { detail: { key: 'QNZ', label: '钦州' } })
  check('选中后写回卸货港', self.data.form.dest_port === 'QNZ' && self.data.form.dest_label === '钦州')
  check('选中后关闭面板', self.data.ppVisible === false)
  check('自动继续：展开「货物资料」并高亮缺失字段', self.data.panel === 'cargo' && self.data.miss === 'weight', `panel=${self.data.panel} miss=${self.data.miss}`)

  // 补全吨数 → 高亮清除 → 再点 → 走到底（真的发请求，不是死路）
  cfg.onInput.call(self, { currentTarget: { dataset: { field: 'weight_t' } }, detail: { value: '1200' } })
  check('补全吨数后清除高亮', self.data.miss === '', `miss=${self.data.miss}`)
  reqLog.length = 0
  self.data.form.expect_date = self.data.form.expect_date || '2026-09-12'
  cfg.onFindShips.call(self)
  check('字段齐全时真正发起创建货源请求（闭环走到底）', reqLog.some((r) => r === 'POST /api/v1/cargo/shipments'), JSON.stringify(reqLog))

  // 缺装货港 → action=origin
  self = instantiate(cfg, { form: Object.assign({}, baseForm, { origin_port: '', origin_label: '' }) })
  cfg.onFindShips.call(self)
  check('缺装货港时 ppAction=origin', self.data.ppAction === 'origin', self.data.ppAction)

  // 起讫港相同 → 高亮 dest
  self = instantiate(cfg, { form: Object.assign({}, baseForm, { dest_port: 'NNG', dest_label: '南宁 · 平塘港' }) })
  cfg.onFindShips.call(self)
  check('起讫港相同 → 高亮卸货港且不发请求', self.data.miss === 'dest', `miss=${self.data.miss}`)

  // 关闭面板
  self = instantiate(cfg)
  self.setData({ ppVisible: true })
  cfg.onPortPickerClose.call(self)
  check('onPortPickerClose 关闭面板', self.data.ppVisible === false)

  // 三个入口都改走组件
  for (const [m, title] of [['pickOrigin', '选择装货港'], ['pickDest', '选择卸货港'], ['pickDefaultPort', '常用港口']]) {
    const s2 = instantiate(cfg, { form: Object.assign({}, baseForm) })
    cfg[m].call(s2)
    check(`${m} 走组件（标题 ${title}）`, s2.data.ppVisible === true && s2.data.ppTitle === title, s2.data.ppTitle)
  }
  const s3 = instantiate(cfg, { form: Object.assign({}, baseForm) })
  cfg.onPortPicked.call(s3, { detail: { key: 'WUZ', label: '梧州' } })
  s3.setData({ ppAction: 'defaultPort' })
  cfg.onPortPicked.call(s3, { detail: { key: 'GGU', label: '贵港' } })
  check('常用港选中后同步装货港', s3.data.defaultPortKey === 'GGU' && s3.data.form.origin_port === 'GGU')
}

// ---------------------------------------------------------------- ③ 切角色 / 退出
section('③ 切角色弹窗与「我的」退出')
{
  for (const [file, pageName, other] of [['pages/shipper/shipper.js', '货主页', '船东'], ['pages/owner/owner.js', '船东页', '货主']]) {
    const wx = makeWx()
    const cfg = loadConfig(path.join(MP, file), 'page', wx, [])
    const self = instantiate(cfg)
    wx.__calls.actionSheet.length = 0
    cfg.onSwitchRole.call(self)
    check(`${pageName} 切角色用确认弹窗（非 actionSheet）`, wx.__calls.actionSheet.length === 0 && wx.__calls.modal.length === 1, `sheet=${wx.__calls.actionSheet.length} modal=${wx.__calls.modal.length}`)
    const content = (wx.__calls.modal[0] || {}).content || ''
    check(`${pageName} 弹窗不含「港口方」且指向${other}`, content.indexOf('港口方') === -1 && content.indexOf(other) !== -1, content)
  }

  const wx = makeWx()
  const reqLog = []
  const cfg = loadConfig(path.join(MP, 'pages/mine/mine.js'), 'page', wx, reqLog)
  const items = (cfg.data.functions || [])
  const logout = items.find((f) => f.key === 'logout')
  check('常用功能栏含「退出」', !!logout, `共 ${items.length} 项`)
  check('「退出」标记为已开发（warn 样式）', !!logout && logout.warn === true)
  check('常用功能栏共 12 项（网格补满三行）', items.length === 12, String(items.length))

  const self = instantiate(cfg)
  cfg.onFunction.call(self, { currentTarget: { dataset: { key: 'logout' } } })
  check('点「退出」弹确认框', wx.__calls.modal.length === 1, String(wx.__calls.modal.length))
  check('确认后清登录态并回首页', reqLog.indexOf('clearUser') !== -1 && wx.__calls.reLaunch.some((o) => o.url === '/pages/index/index'), JSON.stringify({ reqLog, rl: wx.__calls.reLaunch }))
  check('退出文案说明「重新选择」', /重新选择/.test((wx.__calls.modal[0] || {}).content || ''), (wx.__calls.modal[0] || {}).content)
}

// ---------------------------------------------------------------- ④ 其余页面港口选择
section('④ 发布货物 / 发布空船 / 船东页港口选择')
{
  const cases = [
    ['pages/publish/cargo/cargo.js', [['pickOrigin', 'origin'], ['pickDest', 'dest']]],
    ['pages/publish/ship/ship.js', [['pickCurrentPort', 'current'], ['pickOrigin', 'origin'], ['pickDest', 'dest']]],
    ['pages/owner/owner.js', [['pickDefaultPort', 'defaultPort'], ['pickOriginFilter', 'originFilter'], ['pickDestFilter', 'destFilter']]],
  ]
  for (const [file, methods] of cases) {
    const wx = makeWx()
    const cfg = loadConfig(path.join(MP, file), 'page', wx, [])
    for (const [m, action] of methods) {
      const self = instantiate(cfg)
      cfg[m].call(self)
      check(`${path.basename(file)} · ${m} → ppAction=${action}`, self.data.ppVisible === true && self.data.ppAction === action, `visible=${self.data.ppVisible} action=${self.data.ppAction}`)
      // 全量港口列表（不传 ports 的入口应为 13 项）
      if (action !== 'originFilter' && action !== 'destFilter') {
        check(`${path.basename(file)} · ${m} 面板承载 13 个港口`, (self.data.ppPorts || []).length === 13, String((self.data.ppPorts || []).length))
      }
    }
  }

  // 船东页筛选：含「全部」共 14 项；选「全部」时标签回退
  const wx = makeWx()
  const ownerCfg = loadConfig(path.join(MP, 'pages/owner/owner.js'), 'page', wx, [])
  let self = instantiate(ownerCfg)
  ownerCfg.pickOriginFilter.call(self)
  check('船东页装货港筛选 14 项（含「全部」）', self.data.ppPorts.length === 14, String(self.data.ppPorts.length))
  ownerCfg.onPortPicked.call(self, { detail: { key: '', label: '装货港（全部）' } })
  self.setData({ ppAction: 'originFilter' })
  ownerCfg.onPortPicked.call(self, { detail: { key: '', label: '装货港（全部）' } })
  check('选「全部」时筛选项标签回退为「装货港」', self.data.originLabel === '装货港' && self.data.originKey === '', `${self.data.originKey}/${self.data.originLabel}`)

  // 发布货物页写回
  const cargoCfg = loadConfig(path.join(MP, 'pages/publish/cargo/cargo.js'), 'page', makeWx(), [])
  const cargoSelf = instantiate(cargoCfg)
  cargoCfg.pickDest.call(cargoSelf)
  cargoCfg.onPortPicked.call(cargoSelf, { detail: { key: 'BHZ', label: '北海' } })
  check('发布货物页选中写回卸货港', cargoSelf.data.form.dest_port === 'BHZ' && cargoSelf.data.form.dest_label === '北海')

  // 发布空船页写回「当前停靠港」
  const shipCfg = loadConfig(path.join(MP, 'pages/publish/ship/ship.js'), 'page', makeWx(), [])
  const shipSelf = instantiate(shipCfg)
  shipCfg.pickCurrentPort.call(shipSelf)
  shipCfg.onPortPicked.call(shipSelf, { detail: { key: 'LZH', label: '柳州' } })
  check('发布空船页选中写回当前停靠港', shipSelf.data.form.current_port === 'LZH' && shipSelf.data.form.current_port_label === '柳州')
}

// ---------------------------------------------------------------- ⑤ 静态防线
section('⑤ 静态防线')
{
  const files = []
  const walk = (dir) => {
    for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, f.name)
      if (f.isDirectory()) walk(p)
      else files.push(p)
    }
  }
  walk(MP)

  // showActionSheet 长列表（微信上限 6 项，超过静默失败且无 fail 兜底）
  // 规则（第三方审计 §六.1 增强：原实现只认字面量与 PORTS，动态表达式漏检 ——
  //       「发布空船页选船」用 ships.map() 承载无上限数据源，船队 >6 艘即静默失败）：
  //   允许：① 字面量数组（≤6 项） ② PACKS（5 项常量）
  //         ③ 白名单常量的 .map()：CARGO_TYPES(5) / SHIP_TYPES(4) / SHIP_TYPES_ANY(5)
  //   其余表达式（动态数据源 / 未知变量）一律 FAIL —— 要承载长列表必须走 port-picker 组件
  const overLimit = []
  const SHEET_WHITELIST = new Set(['CARGO_TYPES', 'SHIP_TYPES', 'SHIP_TYPES_ANY'])
  for (const f of files.filter((x) => x.endsWith('.js'))) {
    const src = fs.readFileSync(f, 'utf8')
    const re = /showActionSheet\(\s*\{[\s\S]{0,600}?itemList:\s*([^\n]+)/g
    let m
    while ((m = re.exec(src))) {
      const expr = m[1].trim()
      const where = `${path.relative(MP, f)}: ${expr}`
      if (/PORTS/.test(expr)) { overLimit.push(`${where}（港口必须走 port-picker 组件）`) ; continue }
      const lit = expr.match(/\[([^\]]*)\]/)
      if (lit) {
        const n = lit[1].split(',').filter((s) => s.trim()).length
        if (n > 6) overLimit.push(`${where}（字面量 ${n} 项 > 6）`)
        continue
      }
      if (/^PACKS\b/.test(expr)) continue // 5 项常量
      const mapped = expr.match(/^([A-Za-z_$][\w$]*)\.map\(/)
      if (mapped) {
        if (SHEET_WHITELIST.has(mapped[1])) continue // 白名单常量（编译期固定项数 ≤6）
        overLimit.push(`${where}（动态表达式 .map() 非白名单——长列表必须走 port-picker）`)
        continue
      }
      overLimit.push(`${where}（未知表达式，请白名单化或改走 port-picker）`)
    }
  }
  check('全站无 showActionSheet 承载超过 6 项 / 动态列表（白名单外一律拦截）', overLimit.length === 0, overLimit.join(' | '))

  // 下拉刷新一致性（第三方审计 §六.2 / P2-2：JS 有 onPullDownRefresh 但 json 未开开关 → 手势静默无效）
  const pullMismatches = []
  for (const f of files.filter((x) => x.endsWith('.js') && x.indexOf(path.join('pages' + path.sep)) !== -1)) {
    const src = fs.readFileSync(f, 'utf8')
    if (!/onPullDownRefresh\s*\(\s*\)\s*\{/.test(src)) continue
    const json = f.replace(/\.js$/, '.json')
    let conf = null
    try { conf = JSON.parse(fs.readFileSync(json, 'utf8')) } catch (e) { pullMismatches.push(`${path.relative(MP, f)}: json 不可解析`) ; continue }
    if (conf.enablePullDownRefresh !== true) pullMismatches.push(`${path.relative(MP, f)}: 有 onPullDownRefresh 但 enablePullDownRefresh != true`)
  }
  check('有 onPullDownRefresh 的页面均已开启 enablePullDownRefresh', pullMismatches.length === 0, pullMismatches.join(' | '))

  // 业务日期禁用 toISOString().slice（第三方审计 §六.4 / P2-3：UTC 日期在东八区 0-8 点取到「昨天」）
  // 允许：utils/dates.js（本工具自身）；`new Date().toISOString()` 整串保存（时间戳语义，非业务日期）
  const isoDateHits = []
  for (const f of files.filter((x) => x.endsWith('.js'))) {
    if (f.endsWith(path.join('utils', 'dates.js'))) continue
    const src = fs.readFileSync(f, 'utf8')
    const re = /toISOString\s*\(\s*\)\s*\.slice/g
    if (re.test(src)) isoDateHits.push(path.relative(MP, f))
  }
  check('业务日期不走 toISOString().slice（UTC 偏移，改用 utils/dates.fmtDate）', isoDateHits.length === 0, isoDateHits.join(' | '))

  // 使用 port-picker 的页面必须注册组件
  const users = files.filter((f) => f.endsWith('.wxml') && fs.readFileSync(f, 'utf8').indexOf('<port-picker') !== -1)
  check('port-picker 至少被 4 个页面使用', users.length >= 4, `users=${users.length}`)
  const unregistered = []
  for (const f of users) {
    const json = f.replace(/\.wxml$/, '.json')
    let conf = {}
    try { conf = JSON.parse(fs.readFileSync(json, 'utf8')) } catch (e) { unregistered.push(`${path.relative(MP, f)}: json 不可解析`) ; continue }
    if (!conf.usingComponents || !conf.usingComponents['port-picker']) unregistered.push(path.relative(MP, f))
  }
  check('使用组件的页面均已在 usingComponents 注册', unregistered.length === 0, unregistered.join(' | '))

  // 底部固定条按钮对齐
  const appwxss = read('app.wxss')
  const m = appwxss.match(/\.bottom-bar \.btn-primary\s*\{([^}]*)\}/)
  check('.bottom-bar .btn-primary 声明 text-align: center', !!m && /text-align:\s*center/.test(m[1]), m ? m[1].replace(/\s+/g, ' ').trim() : 'not found')

  // 发布页底部留白不被简写覆盖
  for (const f of ['pages/publish/cargo/cargo.wxss', 'pages/publish/ship/ship.wxss']) {
    const css = read(f)
    const block = css.match(/\.page-sub\s*\{([^}]*)\}/)
    const body = block ? block[1] : ''
    const hasBottom = /padding-bottom:/.test(body) && /calc\(/.test(body)
    check(`${path.basename(f)} 为底部固定条预留 padding-bottom`, hasBottom, body.replace(/\s+/g, ' ').trim())
  }

  // 我的页退出样式
  check('mine.wxss 定义「退出」弱红样式', /\.fn-label-warn/.test(read('pages/mine/mine.wxss')))
  check('shipper.wxss 定义缺失字段高亮样式', /\.field-row-miss/.test(read('pages/shipper/shipper.wxss')))
}

// ---------------------------------------------------------------- ⑥ 身份 / 登录链路
;(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 25))
  const IDX = path.join(MP, 'pages/index/index.js')
  const loadIdx = (wx, reqLog, authState) => loadConfig(IDX, 'page', wx, reqLog, authState)

  section('⑥ 身份选择·登录链路·角色门控取数')

  // —— 港口方身份已下线（首页 / 我的 / 品牌底栏 / auth 文案均不得再出现）——
  const meta = read('pages/index/index.js').match(/const ROLE_META = \{([\s\S]*?)\n\}/)
  check('首页身份仅「货主 / 船东」', !!meta && /shipper/.test(meta[1]) && /owner/.test(meta[1]) && !/port/.test(meta[1]))
  check('首页 wxml 不再有「港口方」入口', read('pages/index/index.wxml').indexOf('港口方') === -1)
  check('首页 wxss 已清理 sheet-foot 样式', read('pages/index/index.wxss').indexOf('.sheet-foot') === -1)
  const mineList = read('pages/mine/mine.js').match(/const ROLE_LIST = \[([\s\S]*?)\n\]/)
  check('我的页账号区仅两个身份', !!mineList && !/port/.test(mineList[1]) && (mineList[1].match(/key:/g) || []).length === 2)
  const roleLabels = read('utils/auth.js').match(/const ROLE_LABELS = \{([\s\S]*?)\n\}/)
  // 港口方身份已下线：入口（切角色/账号区/tabBar）不得出现；但 ROLE_LABELS 保留
  // port 兜底（第三方审计 P3-3：万一后端返回 port 角色，role_label 不应回退为英文）
  check('auth.js ROLE_LABELS 含港口方兜底（入口下线 ≠ 标签缺失）', !!roleLabels && /port:\s*'港口方'/.test(roleLabels[1]))
  const labelOf = read('custom-tab-bar/index.js').match(/const LABEL_OF = \{([\s\S]*?)\n\}/)
  check('自定义 tabBar LABEL_OF 不含港口方', !!labelOf && !/port:/.test(labelOf[1]))
  check('退出登录会清 dev_login_code（否则登回旧身份）', /removeStorageSync\(['"]dev_login_code['"]\)/.test(read('utils/auth.js')))
  check('港口页「业务办理」按身份门控（非港口方不发请求）', /role !== 'port'/.test(read('pages/port/port.js')))

  // —— 进入身份的链路统一收敛到 auth.enterRole ——
  // 起因：开发期若用一个「全新 code」登录，后端会当场注册出空账号，
  // 订单页不报错、走「暂无订单」空态（2026-09-11 用户报「订单页面仍然空白」）。
  // 故 enterRole 里必须①先映射到该角色的演示账号，②再 login→bindRole→switchRole。
  {
    const authSrc = read('utils/auth.js')
    const idxSrc = read('pages/index/index.js')
    check('首页进入链路统一走 auth.enterRole（不再自行拼 login/bindRole/switchRole）',
      /auth\.enterRole\(/.test(idxSrc) && !/auth\.bindRole\(/.test(idxSrc) && !/auth\.switchRole\(/.test(idxSrc))
    check('auth.js 导出 enterRole / ensureDevAccount',
      /module\.exports[\s\S]*enterRole/.test(authSrc) && /ensureDevAccount/.test(authSrc))
    const ER = authSrc.match(/function enterRole\([\s\S]*?\n\}/)
    // 只断言「顺序」，不绑定调用参数写法（login 可带 role 以便回退预览身份）
    const erSrc = ER ? ER[0] : ''
    const iMap = erSrc.indexOf('ensureDevAccount(role)')
    const iLogin = erSrc.search(/login\(/)
    const iBind = erSrc.indexOf('bindRole(role')
    const iSwitch = erSrc.indexOf('switchRole(role')
    check('auth.js enterRole 内部顺序 映射账号 → login → bindRole → switchRole',
      iMap >= 0 && iMap < iLogin && iLogin < iBind && iBind < iSwitch,
      ER ? `map=${iMap} login=${iLogin} bind=${iBind} switch=${iSwitch}` : 'not found')
    check('DEV_ROLE_CODE 映射到演示账号（映射错=登录空账号=列表全空）',
      /DEV_ROLE_CODE\s*=\s*\{[^}]*shipper:\s*'seed-shipper'[^}]*owner:\s*'seed-owner'/.test(authSrc),
      (authSrc.match(/DEV_ROLE_CODE\s*=\s*\{[^}]*\}/) || ['?'])[0])
    check('首页 onLoad 换演示账号后不自动进工作台（留在身份选择页重选）',
      /if \(auth\.ensureDevAccount\(role\)\) return/.test(idxSrc))
    for (const f of ['pages/mine/mine.js', 'pages/owner/owner.js', 'pages/shipper/shipper.js']) {
      check(f + ' 里切换身份走 enterRole', /enterRole\(/.test(read(f)) && !/\.switchRole\(/.test(read(f)))
    }
  }

  // —— 登录链路：首次必须 switchRole，否则带着旧角色 token 进工作台 → 全线 403 ——
  {
    const wx = makeWx()
    const reqLog = []
    const authState = { loggedIn: false, user: { user_id: 1, current_role: '', roles: [] } }
    const cfg = loadIdx(wx, reqLog, authState)
    const self = instantiate(cfg)
    cfg.onPickRole.call(self, { currentTarget: { dataset: { role: 'owner' } } })
    await tick()
    check('首次登录链路顺序 login→bindRole→switchRole',
      reqLog.join(' > ') === 'login > bindRole:owner > switchRole:owner', reqLog.join(' > '))
    check('首次登录后 current_role 已切到目标身份', authState.user.current_role === 'owner')
    check('首次登录后进入船东工作台', wx.__calls.switchTab.length === 1 && wx.__calls.switchTab[0].url === '/pages/owner/owner',
      JSON.stringify(wx.__calls.switchTab.map((o) => o.url)))
    check('成功路径不弹提示', wx.__calls.toast.length === 0, JSON.stringify(wx.__calls.toast.map((o) => o.title)))
    check('成功路径不残留 loading 遮罩', self.data.logging === false || self.data.pendingRole === 'owner')
  }

  // —— 已登录三种分支 ——
  {
    const cases = [
      ['已是当前身份 → 直接进入，不发请求', 'owner', { current_role: 'owner', roles: ['shipper', 'owner'] }, ''],
      ['已绑但在别的身份 → 仅 switchRole', 'shipper', { current_role: 'owner', roles: ['shipper', 'owner'] }, 'switchRole:shipper'],
      ['未绑该身份 → bindRole + switchRole', 'owner', { current_role: 'shipper', roles: ['shipper'] }, 'bindRole:owner > switchRole:owner'],
    ]
    for (const [label, role, user, expect] of cases) {
      const wx = makeWx()
      const reqLog = []
      const cfg = loadIdx(wx, reqLog, { loggedIn: true, user: Object.assign({ user_id: 1 }, user) })
      const self = instantiate(cfg)
      cfg.onPickRole.call(self, { currentTarget: { dataset: { role } } })
      await tick()
      check(label, reqLog.join(' > ') === expect && wx.__calls.switchTab.length === 1, `${reqLog.join(' > ')} | tab=${wx.__calls.switchTab.length}`)
    }
  }

  // —— 失败路径不得把 loading 卡死（遮罩会吞掉所有点击 = 「点了没反应」），
  //    且必须说清「哪一步失败」——原先统一显示「登录失败」无法定位 ——
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: false, failLogin: true, user: { user_id: 1, current_role: '', roles: [] } })
    const self = instantiate(cfg)
    cfg.onPickRole.call(self, { currentTarget: { dataset: { role: 'shipper' } } })
    await tick()
    const modal = wx.__calls.modal[wx.__calls.modal.length - 1]
    check('登录失败：复位 loading', self.data.logging === false && self.data.pendingRole === '')
    check('登录失败：弹窗点名失败环节为「登录」', !!modal && /失败环节：登录/.test(modal.content), JSON.stringify(modal))
    check('登录失败：弹窗带上真实原因', !!modal && /原因：login fail/.test(modal.content), JSON.stringify(modal && modal.content))
    check('失败路径不再用会互相覆盖的 toast', wx.__calls.toast.length === 0, JSON.stringify(wx.__calls.toast.map((o) => o.title)))
  }
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: true, failBind: true, user: { user_id: 1, current_role: 'shipper', roles: ['shipper'] } })
    const self = instantiate(cfg)
    cfg.onPickRole.call(self, { currentTarget: { dataset: { role: 'owner' } } })
    await tick()
    const modal = wx.__calls.modal[wx.__calls.modal.length - 1]
    check('绑定失败：弹窗点名「绑定身份」而非笼统登录失败',
      !!modal && /失败环节：绑定身份/.test(modal.content), JSON.stringify(modal && modal.content))
  }
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: true, user: { user_id: 1, current_role: 'shipper', roles: ['shipper'] } })
    const self = instantiate(cfg)
    cfg.onPickRole.call(self, { currentTarget: { dataset: { role: 'shipper' } } })
    await tick()
    check('已就位时直接调 switchTab', wx.__calls.switchTab.length === 1)
    wx.__calls.switchTab[0].fail({ errMsg: 'switchTab:fail can not switch to no-tabBar page' })
    await tick()
    const modal = wx.__calls.modal[wx.__calls.modal.length - 1]
    check('switchTab 失败：复位并提示（不再静默）', self.data.logging === false && self.data.pendingRole === '')
    check('switchTab 失败：弹窗点名「进入工作台」', !!modal && /失败环节：进入工作台/.test(modal.content), JSON.stringify(modal && modal.content))
    check('switchTab 失败：错误对象被归类到 enter 阶段', !!modal, JSON.stringify(modal))
  }

  // —— 港口方身份点击无效（不再发请求 / 不跳转）——
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: false, user: { user_id: 1, current_role: '', roles: [] } })
    const self = instantiate(cfg)
    cfg.onPickRole.call(self, { currentTarget: { dataset: { role: 'port' } } })
    await tick()
    check('点击「港口方」不再有任何动作', reqLog.length === 0 && wx.__calls.switchTab.length === 0 && self.data.logging === false,
      JSON.stringify({ reqLog, tab: wx.__calls.switchTab.length }))
  }

  // —— 订单页 / 支付页（TODO-02）：订单自带 cargo/ship 摘要 → 零富化请求；
  //    摘要缺失（旧后端/异常）才回退，且只打当前角色那一侧（两侧都打 → 必有一个 403）——
  {
    const SUMMARY = {
      id: 7, cargo_id: 3, ship_id: 5, shipper_id: 1, owner_id: 2,
      freight_price: 12000, status: 'matched', cancel_reason: '',
      matched_at: '2026-09-10T10:00:00', shipped_at: null, completed_at: null,
      cancelled_at: null, created_at: '2026-09-10T10:00:00',
      cargo: { id: 3, cargo_name: '水泥熟料', cargo_type: 'bulk', weight_t: 1000, origin_port: 'NNG', dest_port: 'QNZ', expect_date: '2026-10-01' },
      ship: { id: 5, ship_name: '桂平顺发 88', ship_type: 'bulk', deadweight_t: 1500, home_port: 'GGU' },
    }
    const cases = [
      ['pages/trade/orders/orders.js', 'fetchAll', 'shipper', '/api/v1/cargo/shipments', '/api/v1/ship/registry', (o) => ({ total: 1, page: 1, size: 20, items: [o] })],
      ['pages/trade/orders/orders.js', 'fetchAll', 'owner', '/api/v1/ship/registry', '/api/v1/cargo/shipments', (o) => ({ total: 1, page: 1, size: 20, items: [o] })],
      ['pages/trade/payment/payment.js', 'fetch', 'shipper', '/api/v1/cargo/shipments', '/api/v1/ship/registry', (o) => o],
      ['pages/trade/payment/payment.js', 'fetch', 'owner', '/api/v1/ship/registry', '/api/v1/cargo/shipments', (o) => o],
    ]
    const settle = async () => { for (let i = 0; i < 6; i++) await tick() }
    for (const [file, method, role, want, forbid, wrap] of cases) {
      const base = path.basename(file)
      const build = (payload) => {
        const wx = makeWx()
        const reqLog = []
        const cfg = loadConfig(path.join(MP, file), 'page', wx, reqLog,
          { loggedIn: true, user: { current_role: role, roles: [role] }, orderPayload: payload })
        return { self: instantiate(cfg, { role, orderId: 7 }), reqLog, cfg }
      }

      // (1) 带内嵌摘要 → 不发任何富化请求，且卡片字段不降级
      {
        const { self, reqLog, cfg } = build(wrap(SUMMARY))
        if (typeof cfg[method] !== 'function') { fail(`${base}.${method} 不存在`); continue }
        cfg[method].call(self)
        await settle()
        check(`${base}(${role}) 订单自带摘要 → 零富化请求`,
          !reqLog.some((r) => r.indexOf(want) !== -1 || r.indexOf(forbid) !== -1), JSON.stringify(reqLog))
        const texts = file.indexOf('orders') !== -1
          ? (self.data.rawList || []).map((o) => [o.origin_label, o.dest_label, o.cargo_name, o.ship_name].join(' '))
          : [self.data.cargoText, self.data.shipText]
        const joined = texts.join(' | ')
        check(`${base}(${role}) 卡片字段取自内嵌摘要（无 #id 降级）`,
          texts.length > 0 && texts.every((t) => t && t.indexOf('#') === -1) && joined.indexOf('水泥熟料') !== -1 && joined.indexOf('桂平顺发 88') !== -1,
          joined)
      }

      // (2) 摘要缺失（旧后端/异常）→ 回退，且只打本角色那一侧
      {
        const stripped = Object.assign({}, SUMMARY)
        delete stripped.cargo
        delete stripped.ship
        const { self, reqLog, cfg } = build(wrap(stripped))
        cfg[method].call(self)
        await settle()
        check(`${base}(${role}) 摘要缺失 → 仅回退 ${want}，不打 ${forbid}`,
          reqLog.some((r) => r.indexOf(want) !== -1) && !reqLog.some((r) => r.indexOf(forbid) !== -1), JSON.stringify(reqLog))
      }
    }
  }

  // ---------------------------------------------------------------- ⑦ 失败可定位 · 开发期身份稳定
  section('⑦ 错误诊断分类 · 开发期身份稳定 · /healthz 预检')

  // —— describeError：把 errMsg 翻译成「哪一步坏了 + 怎么修」——
  // 直接 require 真实实现（utils/request.js 顶层不碰 wx，可在 Node 下加载）
  {
    const RQ = require(path.join(MP, 'utils', 'request.js'))
    check('utils/request 导出 describeError', typeof RQ.describeError === 'function')
    const cases = [
      ['域名未校验', { errMsg: 'request:fail url not in domain list' }, '请求域名未通过校验'],
      ['后端连不上', { errMsg: 'request:fail fail to connect' }, '无法连接后端 127.0.0.1:8000'],
      ['请求超时', { errMsg: 'request:fail timeout' }, '请求超时'],
      ['switchTab 失败', { errMsg: 'switchTab:fail can not switch to no-tabBar page' }, '无法跳转到工作台页面'],
      ['wx.login 不可用', { errMsg: 'wx.login:fail auth deny' }, 'wx.login 不可用'],
      ['HTTP 5xx 带 detail', { message: 'boom', httpStatus: 500, detail: 'boom' }, '接口返回 500：boom'],
    ]
    for (const [label, err, want] of cases) {
      const d = RQ.describeError(err)
      check('describeError · ' + label, d.cause === want, `${d.cause} ≠ ${want}`)
    }
    const d0 = RQ.describeError({ errMsg: 'request:fail 神秘错误' })
    check('describeError · 兜底给人话且保留原始 errMsg',
      d0.cause === '网络异常，请稍后重试' && /神秘错误/.test(d0.hint), JSON.stringify(d0))

    // —— 真机 / 模拟器的提示必须分流 ——
    // 开发者工具里的「不校验合法域名」只对电脑模拟器生效，真机照做无效。
    // 若提示不区分环境，真机用户会被指向一个永远修不好问题的开关（真实踩坑）。
    {
      const hadWx = Object.prototype.hasOwnProperty.call(global, 'wx')
      const savedWx = global.wx
      global.wx = { getDeviceInfo: () => ({ platform: 'android' }) }
      try {
        const dReal = RQ.describeError({ errMsg: 'request:fail url not in domain list' })
        check('describeError · 真机提示指向「开发调试」而非工具设置',
          dReal.cause === '请求域名未通过校验' && /开发调试/.test(dReal.hint),
          dReal.hint.slice(0, 50))
      } finally {
        if (hadWx) global.wx = savedWx
        else delete global.wx
      }
    }
  }

  // —— 开发期身份稳定：AppID 是占位值时 wx.login 拿不到真实身份，
  //    若走它则每次 code 都不同 → 每次登录都新建用户 →「我的货源/我的订单」永远为空 ——
  function loadAuth(storage, loginCalls, codes) {
    const src = read('utils/auth.js')
    const mod = { exports: {} }
    const req = (p) => {
      if (String(p).indexOf('request') !== -1) {
        return {
          request: (o) => {
            const code = (o.data && o.data.code) || ''
            codes.push(code)
            return Promise.resolve({
              access_token: 'tk', user_id: 1, openid: 'mock-openid-' + code,
              nickname: '', roles: ['shipper'], current_role: 'shipper'
            })
          },
          getToken: () => storage.access_token || '',
          setToken: (t) => { storage.access_token = t },
          clearToken: () => { delete storage.access_token },
          describeError: () => ({ cause: '', hint: '' }), BASE_URL: 'http://127.0.0.1:8000',
        }
      }
      return {}
    }
    const wx = {
      getStorageSync: (k) => (k in storage ? storage[k] : ''),
      setStorageSync: (k, v) => { storage[k] = v },
      removeStorageSync: (k) => { delete storage[k] },
      login: (o) => { loginCalls.push(o); if (o && o.fail) o.fail({ errMsg: 'wx.login:fail' }) },
    }
    new Function('require', 'module', 'exports', 'wx', src)(req, mod, mod.exports, wx)
    return mod.exports
  }
  {
    const storage = {}
    const loginCalls = []
    const codes = []
    const A = loadAuth(storage, loginCalls, codes)
    await A.login()
    check('开发期登录不调用 wx.login（不被微信登录服务卡死）', loginCalls.length === 0, 'calls=' + loginCalls.length)
    check('开发期登录使用固定 code', codes[0] === 'seed-shipper', JSON.stringify(codes))
    check('固定身份 code 已落盘（下次登录仍是同一账号）', storage.dev_device_code === 'seed-shipper', JSON.stringify(storage))
  }
  {
    // 角色 → 演示账号：点货主登 seed-shipper、点船东登 seed-owner
    for (const [role, code] of [['shipper', 'seed-shipper'], ['owner', 'seed-owner']]) {
      const storage = {}
      const codes = []
      const A = loadAuth(storage, [], codes)
      const changed = A.ensureDevAccount(role)
      check('ensureDevAccount(' + role + ') → 演示账号 ' + code,
        changed === true && storage.dev_device_code === code, JSON.stringify(storage))
      await A.login()
      check('  随后登录 code = ' + code, codes[0] === code, JSON.stringify(codes))
    }
  }
  {
    // 换账号必须清掉旧登录态，否则带着旧账号 token 去打新账号的接口 → 列表全空
    const storage = { dev_device_code: 'seed-owner', access_token: 'old-token', user_info: '{"user_id":2}' }
    const A = loadAuth(storage, [], [])
    const changed = A.ensureDevAccount('shipper')
    check('换演示账号时清掉旧 token / user_info',
      changed === true && !storage.access_token && !storage.user_info, JSON.stringify(storage))
  }
  {
    // 手工 dev_login_code 优先：走查脚本靠它注入 seed-port 测港口身份
    const storage = { dev_login_code: 'seed-port', dev_device_code: 'seed-shipper' }
    const A = loadAuth(storage, [], [])
    check('手工 dev_login_code 不被角色映射冲掉',
      A.ensureDevAccount('owner') === false && storage.dev_device_code === 'seed-shipper', JSON.stringify(storage))
  }
  {
    const storage = { dev_login_code: 'seed-owner' }
    const codes = []
    const A = loadAuth(storage, [], codes)
    await A.login()
    check('联调指定身份优先于开发固定身份', codes[0] === 'seed-owner', JSON.stringify(codes))
  }
  {
    const storage = {}
    const codes = []
    const A = loadAuth(storage, [], codes)
    await A.login()
    await A.login()
    check('重复登录 code 不变（不会每次登录都新建用户）', codes[0] === codes[1] && codes[0] === 'seed-shipper', JSON.stringify(codes))
    A.clearUser()
    check('退出清 dev_login_code、保留 dev_device_code（回首页换身份不丢账号）',
      !('dev_login_code' in storage) && storage.dev_device_code === 'seed-shipper', JSON.stringify(Object.keys(storage)))
  }
  check('auth.js 生产路径仍保留 wx.login（DEV_STABLE_IDENTITY=false 时回退）',
    /_wxLoginCode\(\)\.then/.test(read('utils/auth.js')) && /wx\.login\(\{/.test(read('utils/auth.js')))

  // —— /healthz 预检：进首页就把「后端没起」说清楚，而不是等点完身份再报登录失败 ——
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: false, user: null })
    const self = instantiate(cfg)
    cfg.probeBackend.call(self)
    await tick()
    check('/healthz 预检命中健康端点', reqLog.some((r) => /\/healthz/.test(r)), JSON.stringify(reqLog))
    check('后端可用 → 不显示未连接提示', self.data.backendDown === false, String(self.data.backendDown))
    check('首页模板有未连接提示位（可点重试）',
      /backendDown/.test(read('pages/index/index.wxml')) && /bindtap="probeBackend"/.test(read('pages/index/index.wxml')))
  }
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: false, user: null, failRequest: true })
    const self = instantiate(cfg)
    cfg.probeBackend.call(self)
    await tick()
    check('后端不可用 → 显示未连接提示', self.data.backendDown === true, String(self.data.backendDown))
  }

  // ------------------------------------------- ⑧ 解析入口 / 合规预检 / 统一入口
  section('⑧ 货源解析入口（F14）· 合规预检（F17）· 统一入口（F20）')
  {
    const wait = () => new Promise((r) => setTimeout(r, 25))
    const ASSIST = path.join(MP, 'pages', 'assistant', 'assistant.js')
    const CARGO = path.join(MP, 'pages', 'publish', 'cargo', 'cargo.js')
    const SHIP = path.join(MP, 'pages', 'publish', 'ship', 'ship.js')
    const SHIPPER = { loggedIn: true, user: { user_id: 1, current_role: 'shipper', roles: ['shipper'] } }
    const OWNER = { loggedIn: true, user: { user_id: 2, current_role: 'owner', roles: ['owner'] } }
    const PARSE_PAYLOAD = {
      draft: {
        cargo_name: '散装水泥', cargo_type: 'bulk', weight_t: 800,
        origin_port: 'NNG', dest_port: 'GGU', expect_date: '2026-09-20', offer_price: 25000
      },
      confidence: 0.86, needs_review: ['expect_date'], mocked: true, latency_ms: 12
    }

    // —— 历史缺陷防线：无参 onLoad 会把路由 query 整体丢掉 ——
    const assistSrc = read('pages/assistant/assistant.js')
    check('客服页 onLoad 接收 options（不再吞掉 mode=parse）', /onLoad\(\s*options\s*\)/.test(assistSrc) && !/onLoad\(\)\s*\{/.test(assistSrc))

    // —— 解析态：mode=parse 与角色门控 ——
    const wx1 = makeWx()
    const cfg1 = loadConfig(ASSIST, 'page', wx1, [], SHIPPER)
    const s1 = instantiate(cfg1)
    cfg1.onLoad.call(s1, { mode: 'parse' })
    check('mode=parse → 进入解析态', s1.data.mode === 'parse', String(s1.data.mode))
    check('货主进解析态 → 不触发角色门控', s1.data.roleBlocked === false)
    check('解析态示例是货源描述（含吨位）', (s1.data.chips || []).some((c) => /吨/.test(c)))
    const s1b = instantiate(cfg1)
    cfg1.onLoad.call(s1b, {})
    check('无 mode → 仍是客服模式（原行为不变）', s1b.data.mode === 'chat')

    const cfg1o = loadConfig(ASSIST, 'page', makeWx(), [], OWNER)
    const s1o = instantiate(cfg1o)
    cfg1o.onLoad.call(s1o, { mode: 'parse' })
    check('船东进解析态 → 门控开启（货源解析仅货主）', s1o.data.roleBlocked === true)
    check('解析态门控有「切换为货主」入口（走 auth.enterRole）',
      /onSwitchToShipper/.test(read('pages/assistant/assistant.wxml')) && /auth\.enterRole\('shipper'\)/.test(assistSrc))

    // —— 解析态发送：走 /agent/cargo-parse 并渲染结构化卡片 ——
    const wx2 = makeWx()
    const reqLog2 = []
    const cfg2 = loadConfig(ASSIST, 'page', wx2, reqLog2, Object.assign({}, SHIPPER, { parsePayload: PARSE_PAYLOAD }))
    const s2 = instantiate(cfg2)
    cfg2.onLoad.call(s2, { mode: 'parse' })
    s2.setData({ input: '800吨散装水泥，下周三从南宁运到贵港' })
    await cfg2.onSend.call(s2)
    check('解析态发送 → POST /agent/cargo-parse',
      reqLog2.some((r) => r === 'POST /api/v1/agent/cargo-parse'), JSON.stringify(reqLog2))
    const card = s2.data.messages[s2.data.messages.length - 1] || {}
    check('解析结果渲染为结构化卡片（7 个字段）', card.kind === 'parse' && (card.rows || []).length === 7, JSON.stringify(card.kind))
    check('待确认字段被标红并可读', card.hasMissing === true && (card.rows || []).some((r) => r.missing === true),
      String(card.missingText))
    check('港口回填为中文名（非裸代码）', (card.rows || []).some((r) => r.key === 'origin_port' && /南宁/.test(r.value)),
      JSON.stringify((card.rows || []).filter((r) => r.key === 'origin_port')))
    check('置信度以百分比展示', card.confidence === 86, String(card.confidence))
    cfg2.onUseDraft.call(s2, { currentTarget: { dataset: { idx: s2.data.messages.length - 1 } } })
    check('「带去发布页填写」跳发布货源页（Agent 无直写，仅带草稿）',
      wx2.__calls.navigateTo.some((n) => /pages\/publish\/cargo\/cargo/.test(n.url)),
      JSON.stringify(wx2.__calls.navigateTo))

    // —— 客服模式仍走 assistant（模式互不串台） ——
    const wx3 = makeWx()
    const reqLog3 = []
    const cfg3 = loadConfig(ASSIST, 'page', wx3, reqLog3, SHIPPER)
    const s3 = instantiate(cfg3)
    cfg3.onLoad.call(s3, {})
    s3.setData({ input: '怎么发布货源？' })
    await cfg3.onSend.call(s3)
    const lastMsg = s3.data.messages[s3.data.messages.length - 1] || {}
    check('客服模式发送 → POST /agent/assistant', reqLog3.some((r) => r === 'POST /api/v1/agent/assistant'), JSON.stringify(reqLog3))
    check('客服模式不落解析卡片', lastMsg.kind !== 'parse', String(lastMsg.kind))

    // —— 发布货源页：草稿回填与高亮 ——
    const cfg4 = loadConfig(CARGO, 'page', makeWx(), [], SHIPPER)
    const s4 = instantiate(cfg4)
    cfg4.fillFromDraft.call(s4, { draft: PARSE_PAYLOAD.draft, needs_review: ['weight_t', 'expect_date'] })
    check('解析草稿回填表单（含港口中文名与货类标签）',
      s4.data.form.origin_port === 'NNG' && s4.data.form.dest_port === 'GGU'
      && s4.data.form.weight_t === '800' && s4.data.cargoTypeLabel === '散货'
      && /南宁/.test(s4.data.form.origin_label),
      JSON.stringify(s4.data.form))
    check('needs_review 字段高亮（预计算 missTip，WXML 不能 indexOf）',
      s4.data.missTip.weight_t === true && s4.data.missTip.expect_date === true
      && s4.data.missTip.cargo_name === false, JSON.stringify(s4.data.missTip))
    check('回填后提示待确认字段', /AI 已回填/.test(s4.data.smartTip) && /重量/.test(s4.data.smartTip), s4.data.smartTip)
    cfg4.onInput.call(s4, { currentTarget: { dataset: { field: 'weight_t' } }, detail: { value: '900' } })
    check('人工补全后撤掉高亮', s4.data.missTip.weight_t === false && s4.data.form.weight_t === '900')

    // —— 发布货源页：一句话发货 + 合规预检 ——
    const wx5 = makeWx()
    const reqLog5 = []
    const cfg5 = loadConfig(CARGO, 'page', wx5, reqLog5, Object.assign({}, SHIPPER, { parsePayload: PARSE_PAYLOAD }))
    const s5 = instantiate(cfg5)
    cfg5.onSmartFill.call(s5)
    const modal = wx5.__calls.modal[0] || {}
    check('「一句话发货」弹可编辑输入框', modal.editable === true && !!modal.placeholderText)
    // 表单尚空 → 合规预检应先提示补全，不应发请求（顺序敏感：解析回填会填满表单）
    cfg5.onComplianceCheck.call(s5)
    check('表单不完整时合规预检只提示、不发请求', !reqLog5.some((r) => /compliance/.test(r)) && wx5.__calls.toast.length > 0,
      JSON.stringify(reqLog5))
    cfg5.parseText.call(s5, '800吨散装水泥南宁到贵港')
    await wait()
    check('一句话发货 → POST /agent/cargo-parse', reqLog5.some((r) => r === 'POST /api/v1/agent/cargo-parse'), JSON.stringify(reqLog5))
    check('一句话发货解析结果直接回填表单', s5.data.form.origin_port === 'NNG' && s5.data.form.weight_t === '800',
      JSON.stringify(s5.data.form))
    s5.setData({
      form: {
        cargo_name: '散装水泥', cargo_type: 'bulk', weight_t: '800', origin_port: 'NNG',
        dest_port: 'GGU', expect_date: '2026-09-20', origin_label: '南宁', dest_label: '贵港',
        offer_price: '', remark: '包装：散装'
      }
    })
    cfg5.onComplianceCheck.call(s5)
    const comp = reqLog5.find((r) => /compliance/.test(r)) || ''
    check('合规预检 → POST /agent/compliance/cargo', /compliance \/api\/v1\/agent\/compliance\/cargo/.test(comp), comp)
    check('合规预检带上起讫港与货类（结论可复核）',
      /"origin_port":"NNG"/.test(comp) && /"dest_port":"GGU"/.test(comp) && /"cargo_type":"bulk"/.test(comp), comp)

    // —— 发布空船页：船舶合规预检 ——
    const wx6 = makeWx()
    const reqLog6 = []
    const cfg6 = loadConfig(SHIP, 'page', wx6, reqLog6, OWNER)
    const s6 = instantiate(cfg6)
    cfg6.onComplianceCheck.call(s6)
    check('未选船舶时合规预检只提示', !reqLog6.some((r) => /compliance/.test(r)) && wx6.__calls.toast.length > 0)
    s6.setData({
      selectedShip: {
        ship_name: '平陆 001', ship_type: 'bulk', deadweight_t: 1500, length_m: 60, width_m: 12,
        draft_m: 3.5, home_port: 'GGU', cert_no: 'CERT-F17-001', cert_expiry: '2027-01-01'
      }
    })
    cfg6.onComplianceCheck.call(s6)
    const scomp = reqLog6.find((r) => /compliance/.test(r)) || ''
    check('船舶合规预检 → POST /agent/compliance/ship 且带证书字段',
      /compliance \/api\/v1\/agent\/compliance\/ship/.test(scomp) && /"cert_no":"CERT-F17-001"/.test(scomp), scomp)

    // —— 搜索态（F20 统一入口）：与「智能客服」同款页面，不再是可编辑弹窗 ——
    const ROUTE_CASES = [
      ['货源解析', {
        intent: 'cargo_parse', confidence: 0.92, dispatched: true, message: '',
        result: { draft: PARSE_PAYLOAD.draft, needs_review: PARSE_PAYLOAD.needs_review }
      }],
      ['合规预检', {
        intent: 'compliance', confidence: 0.8, dispatched: true, message: '',
        result: {
          target: 'text', level: 'block', summary: '命中 1 条阻断项',
          findings: [{
            code: 'C1', severity: 'block', title: '疑似禁运/管制货物',
            detail: '货物名称含「甲醇」', suggestion: '需危化品运输资质并单独申报'
          }],
          checked_rules: ['C1']
        }
      }],
      ['智能合同', { intent: 'contract', confidence: 0.75, dispatched: false, message: '合同风控需要具体订单。', result: null }],
      ['智能客服', {
        intent: 'assistant', confidence: 0.5, dispatched: true, message: '',
        result: { answer: '发布货源有两种方式…', mocked: true, latency_ms: 12 }
      }]
    ]

    const wxS = makeWx()
    const reqLogS = []
    const cfgS = loadConfig(ASSIST, 'page', wxS, reqLogS, OWNER)
    const sS = instantiate(cfgS)
    cfgS.onLoad.call(sS, { mode: 'search' })
    check('mode=search → 进入搜索态（与 chat/parse 三态并存）', sS.data.mode === 'search', String(sS.data.mode))
    check('搜索态不做角色门控（路由自行判角色并给引导）', sS.data.roleBlocked === false)
    check('搜索态文案按模式注入（WXML 不再三目嵌套）',
      sS.data.bannerTitle === '平台智能搜索' && /搜货|搜船/.test(sS.data.inputPlaceholder), sS.data.bannerTitle)
    check('三模式历史分库（chat / parse / search 各一份）',
      cfgS.storageKey.call(sS) === 'search_history_v1'
      && cfgS.storageKey.call(Object.assign({}, sS, { data: { mode: 'parse' } })) === 'parse_history_v1'
      && cfgS.storageKey.call(Object.assign({}, sS, { data: { mode: 'chat' } })) === 'chat_history_v1')

    sS.setData({ input: '我要发800吨散装水泥，南宁到贵港' })
    await cfgS.onSend.call(sS)
    check('搜索态发送 → POST /agent/route', reqLogS.some((r) => r === 'POST /api/v1/agent/route'), JSON.stringify(reqLogS))

    for (const [name, payload] of ROUTE_CASES) {
      const cfgR = loadConfig(ASSIST, 'page', makeWx(), [], Object.assign({}, OWNER, { routePayload: payload }))
      const sR = instantiate(cfgR)
      cfgR.onLoad.call(sR, { mode: 'search' })
      sR.setData({ input: '一句话描述' })
      await cfgR.onSend.call(sR)
      const m = sR.data.messages[sR.data.messages.length - 1] || {}
      if (payload.intent === 'cargo_parse') {
        check('搜索态·货源意图 → 渲染结构化解析卡（非纯文本）',
          m.kind === 'parse' && (m.rows || []).length === 7 && !!m.draft, String(m.kind))
        check('搜索态·货源意图标注识别结果与置信度', m.intentLabel === '货源解析' && m.confidence === 92, String(m.intentLabel))
      } else if (payload.intent === 'compliance') {
        check('搜索态·合规意图 → 渲染合规卡（三档结论 + 逐条依据与建议）',
          m.kind === 'compliance' && m.level === 'block' && m.levelLabel === '未通过'
          && (m.findings || []).length === 1 && m.findings[0].severityLabel === '阻断', String(m.kind))
      } else {
        check(`搜索态·${name}意图 → 文本气泡（不误落其它卡片）`,
          !m.kind && !!m.text && m.intentLabel === name, `${m.kind} / ${m.intentLabel}`)
      }
    }

    // 未派发（非货主描述货源）→ 引导语 + 一键切角色重试；不报错、不 500
    const wxN = makeWx()
    const reqLogN = []
    await (async () => {
      const cfgN = loadConfig(ASSIST, 'page', wxN, reqLogN, Object.assign({}, OWNER, {
        routePayload: {
          intent: 'cargo_parse', confidence: 0.7, dispatched: false,
          message: '货源解析仅货主角色可用，请先切换为货主。', result: null
        }
      }))
      const sN = instantiate(cfgN)
      cfgN.onLoad.call(sN, { mode: 'search' })
      sN.setData({ input: '我要发一批货' })
      await cfgN.onSend.call(sN)
      const mN = sN.data.messages[sN.data.messages.length - 1] || {}
      check('搜索态·未派发不算错误：给引导语 + 可点动作 + 原句重试',
        !mN.error && mN.needRole === true && /货主/.test(mN.text) && mN.retryText === '我要发一批货',
        JSON.stringify({ error: mN.error, needRole: mN.needRole, retryText: mN.retryText }))
      cfgN.onSwitchForRetry.call(sN, { currentTarget: { dataset: { idx: sN.data.messages.length - 1 } } })
      await wait()
      check('未派发 → 一键切货主走 auth.enterRole（bind + switch 全链）',
        reqLogN.some((r) => r === 'bindRole:shipper') && reqLogN.some((r) => r === 'switchRole:shipper'),
        JSON.stringify(reqLogN))
    })()

    // —— 统一入口：搜索框 / ✨Ai 的分工（静态防线）——
    for (const [who, file] of [['货主', 'pages/shipper/shipper.js'], ['船东', 'pages/owner/owner.js']]) {
      const src = read(file)
      check(`${who}页搜索框接统一入口（不再是「开发中」占位）`,
        /goSearch\(\)\s*\{\s*openSmartEntry\(/.test(src) && !/搜索功能开发中/.test(src))
      check(`${who}页「✨Ai」进解析态（mode=parse）`,
        /goAI\(\)\s*\{\s*wx\.navigateTo\(\{ url: '\/pages\/assistant\/assistant\?mode=parse' \}\)/.test(src))
    }
    const ae = read('utils/agent-entry.js')
    check('智能搜索不再用可编辑弹窗（改为智能客服同款页面）',
      !/editable\s*:\s*true/.test(ae) && /pages\/assistant\/assistant\?mode=search/.test(ae))
    check('搜索态调 /agent/route 且四类意图分支齐备',
      /\/api\/v1\/agent\/route/.test(assistSrc)
      && ['cargo_parse', 'compliance', 'contract', 'assistant'].every((k) => assistSrc.indexOf("'" + k + "'") >= 0))
    check('合规三档文案（通过 / 有提示 / 未通过）',
      /合规预检通过/.test(ae) && /合规预检有提示/.test(ae) && /合规预检未通过/.test(ae)
      && /合规预检通过/.test(assistSrc) && /合规预检未通过/.test(assistSrc))
    check('切断言而非报错：未派发时给引导语与重试动作', /dispatched/.test(assistSrc) && /needRole/.test(assistSrc))
    check('非货主描述货源 → 引导切换身份（走 auth.enterRole）', /enterRole\('shipper'\)/.test(assistSrc))
    check('草稿键与解析页/发布页一致（cargo_draft_v1）',
      /cargo_draft_v1/.test(assistSrc) && /cargo_draft_v1/.test(read('pages/publish/cargo/cargo.js')))
    check('发布货源页模板含智能填写卡与合规预检入口',
      /onSmartFill/.test(read('pages/publish/cargo/cargo.wxml')) && /onComplianceCheck/.test(read('pages/publish/cargo/cargo.wxml')))
    check('发布空船页模板含合规预检入口', /onComplianceCheck/.test(read('pages/publish/ship/ship.wxml')))

    // —— 顶栏身份：用户头像（矢量占位）+ 用户 ID + 常用港 ——
    for (const [who, dir] of [['货主', 'shipper'], ['船东', 'owner']]) {
      const wxml = read(`pages/${dir}/${dir}.wxml`)
      const js = read(`pages/${dir}/${dir}.js`)
      check(`${who}页顶栏是矢量人物头像（不再是 emoji 占位）`,
        /avatar-vec/.test(wxml) && /av-head/.test(wxml) && /av-body/.test(wxml) && !/top-avatar/.test(wxml))
      check(`${who}页顶栏显示用户 ID 与地理位置（常用港）`,
        /top-user-id/.test(wxml) && /\{\{userCode\}\}/.test(wxml) && /defaultPortLabel/.test(wxml))
      check(`${who}页用户 ID 与「我的」页同口径（用户+user_id）`, /'用户'\s*\+\s*user\.user_id/.test(js))
    }
    const avatarCss = read('app.wxss')
    check('矢量头像样式在 app.wxss（货主/船东共用，不逐页重复定义）',
      /\.avatar-vec/.test(avatarCss) && /\.av-head/.test(avatarCss) && /\.av-body/.test(avatarCss))

    // —— 订单页自绘导航（custom 导航缺顶部内边距会让统计行顶出页面框架）——
    const ordersWxml = read('pages/trade/orders/orders.wxml')
    check('订单页自绘导航存在（否则统计行顶到状态栏、被胶囊遮挡）',
      /class="nav"/.test(ordersWxml) && /statusBarHeight/.test(ordersWxml))
    check('订单页导航只有居中标题（右侧留空给胶囊，不做看不见的假入口）',
      /nav-title/.test(ordersWxml) && /我的订单/.test(ordersWxml) && !/nav-bell/.test(ordersWxml))
    check('订单页导航排在统计行之前（内容整体下移）',
      ordersWxml.indexOf('class="nav"') >= 0 && ordersWxml.indexOf('class="nav"') < ordersWxml.indexOf('class="stat-row"'))
    check('订单页导航样式齐备（nav / nav-inner）',
      /\.nav\s*\{/.test(read('pages/trade/orders/orders.wxss')) && /\.nav-inner/.test(read('pages/trade/orders/orders.wxss')))
    check('自绘导航的右侧控件避开微信胶囊（「我的」页「功能预览」曾整块被遮住）',
      /\.nav-preview\s*\{[^}]*right:\s*200rpx/.test(read('pages/mine/mine.wxss')))
  }

  // ------------------------------------------- ⑨ 发货方式选择（自主发货 / 委托发货）
  section('⑨ 发货方式选择弹窗')
  {
    const cargoWxml = read('pages/publish/cargo/cargo.wxml')
    const cargoJs = read('pages/publish/cargo/cargo.js')
    const cargoWxss = read('pages/publish/cargo/cargo.wxss')

    // —— 结构：弹窗 + 两张卡片 + 两枚单选 + 两段说明（对齐设计稿）——
    check('发布货物页有发货方式弹窗（进入本页即弹出）',
      /wx:if="\{\{showChannel\}\}"/.test(cargoWxml) && /class="ch-panel"/.test(cargoWxml))
    check('两张卡片文案为「自主发货 / 委托发货」，且自主在前',
      /自主发货/.test(cargoWxml) && /委托发货/.test(cargoWxml) &&
      cargoWxml.indexOf('自主发货') < cargoWxml.indexOf('委托发货'))
    check('两张卡片各带一枚单选圈 + 「默认」标记（设计稿 1:1）',
      (cargoWxml.match(/class="ch-ring"/g) || []).length === 2 &&
      (cargoWxml.match(/class="ch-radio-label">默认</g) || []).length === 2)
    check('两段说明文案齐全（平台名用品牌色强调）',
      (cargoWxml.match(/class="ch-brand"/g) || []).length === 2 &&
      /自行在/.test(cargoWxml) && /委托给/.test(cargoWxml) &&
      /找寻认证船主接单并完成运输/.test(cargoWxml) && /由平台组织运力完成运输/.test(cargoWxml))
    check('图标为纯 CSS 矢量（白圆 + 人形 / 手托盒），不新增图片资源',
      /\.ch-head\s*\{/.test(cargoWxss) && /\.ch-shoulder\s*\{/.test(cargoWxss) &&
      /\.ch-box-front\s*\{/.test(cargoWxss) && /\.ch-box-lid\s*\{/.test(cargoWxss) &&
      /\.ch-palm\s*\{/.test(cargoWxss) && !/\.ch-icon[^}]*url\(/.test(cargoWxss))
    check('卡片配色取自设计稿（橙 #E5A75A→#FECD91 / 蓝 #3584FD→#4A81FF）',
      /\.ch-card-self\s*\{[^}]*#E5A75A[^}]*#FECD91/.test(cargoWxss) &&
      /\.ch-card-entrust\s*\{[^}]*#3584FD[^}]*#4A81FF/.test(cargoWxss))

    // —— 行为 ——
    check('进入本页默认弹出（data 初值 true 且在 onLoad 复位）',
      /showChannel:\s*true/.test(cargoJs) &&
      /onLoad\(\)[\s\S]{0,300}showChannel:\s*true/.test(cargoJs))
    const selfBody = cargoJs.slice(cargoJs.indexOf('pickSelfDelivery'), cargoJs.indexOf('pickEntrustDelivery'))
    check('自主发货只关弹窗、留在本页（不跳转）',
      /showChannel:\s*false/.test(selfBody) && !/navigateTo/.test(selfBody) && !/redirectTo/.test(selfBody))
    const entBody = cargoJs.slice(cargoJs.indexOf('pickEntrustDelivery'), cargoJs.indexOf('closeChannelModal'))
    check('委托发货跳「功能预览」占位页', /navigateTo/.test(entBody) && /\/pages\/preview\/preview/.test(entBody))
    check('本功能纯前端：发货方式相关代码零接口调用',
      !/request\(/.test(selfBody) && !/request\(/.test(entBody))
    check('遮罩可关闭（再次点 tabBar 中间「+发货」可重新唤起）',
      /bindtap="closeChannelModal"/.test(cargoWxml) && /closeChannelModal\(\)/.test(cargoJs))

    // —— 占位页 ——
    const previewJs = read('pages/preview/preview.js')
    const previewWxml = read('pages/preview/preview.wxml')
    check('占位页文案为「功能预览，即将开放」',
      /功能预览，即将开放/.test(previewJs) && /\{\{title\}\}/.test(previewWxml))
    check('app.json 已注册占位页路由',
      JSON.parse(read('app.json')).pages.indexOf('pages/preview/preview') >= 0)
    check('占位页纯静态（无任何接口调用）',
      !/request\(/.test(previewJs) && !/wx\.request/.test(previewJs))

    // —— 版本号（当前发布版 v0.5.1；升版时同步此处与 pages/mine/mine.wxml）——
    check('版本号已升到 v0.5.1', /v0\.5\.1/.test(read('pages/mine/mine.wxml')))
  }

  // ---------------------------------------------------------------- 汇总
  console.log('\n' + '='.repeat(72))
  console.log(`UI 交互契约校验：OK ${N_OK} · FAIL ${FAILS.length}`)
  FAILS.forEach((f) => console.log('  FAIL · ' + f))
  console.log('='.repeat(72))
  process.exit(FAILS.length === 0 ? 0 : 1)
})()
