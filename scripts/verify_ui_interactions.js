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
 *      订单页/支付页富化只打当前角色那一侧接口（防跨角色 403）
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
  const AUTH = authState || { loggedIn: true, user: { user_id: 1, current_role: 'shipper', roles: ['shipper'] } }
  return (p) => {
    const s = String(p)
    if (s.indexOf('utils/ports') !== -1) return realPorts
    if (s.indexOf('auth') !== -1) {
      return {
        getUser: () => JSON.parse(JSON.stringify(AUTH.user)),
        isLoggedIn: () => AUTH.loggedIn,
        clearUser: () => reqLog.push('clearUser'),
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
        ROLE_LABELS: { shipper: '货主', owner: '船东' },
      }
    }
    if (s.indexOf('tabbar') !== -1) return { syncTabBar() {} }
    if (s.indexOf('request') !== -1) {
      return {
        request: (o) => {
          reqLog.push((o.method || 'GET') + ' ' + o.url)
          return Promise.resolve({ id: 99, total: 0, items: [] })
        },
        getToken: () => 't', setToken() {}, clearToken() {}, BASE_URL: 'http://127.0.0.1:8000',
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

  // showActionSheet 长列表（微信上限 6 项，超过静默失败）
  const overLimit = []
  for (const f of files.filter((x) => x.endsWith('.js'))) {
    const src = fs.readFileSync(f, 'utf8')
    const re = /showActionSheet\(\s*\{[\s\S]{0,600}?itemList:\s*([^\n]+)/g
    let m
    while ((m = re.exec(src))) {
      const expr = m[1]
      if (/PORTS/.test(expr)) overLimit.push(`${path.relative(MP, f)}: ${expr.trim()}`)
      const lit = expr.match(/\[([^\]]*)\]/)
      if (lit) {
        const n = lit[1].split(',').filter((s) => s.trim()).length
        if (n > 6) overLimit.push(`${path.relative(MP, f)}: 字面量 ${n} 项`)
      }
    }
  }
  check('全站无 showActionSheet 承载超过 6 项（港口 13 项必须走组件）', overLimit.length === 0, overLimit.join(' | '))

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
  check('auth.js ROLE_LABELS 不含港口方', !!roleLabels && !/port:/.test(roleLabels[1]))
  const labelOf = read('custom-tab-bar/index.js').match(/const LABEL_OF = \{([\s\S]*?)\n\}/)
  check('自定义 tabBar LABEL_OF 不含港口方', !!labelOf && !/port:/.test(labelOf[1]))
  check('退出登录会清 dev_login_code（否则登回旧身份）', /removeStorageSync\(['"]dev_login_code['"]\)/.test(read('utils/auth.js')))
  check('港口页「业务办理」按身份门控（非港口方不发请求）', /role !== 'port'/.test(read('pages/port/port.js')))

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

  // —— 失败路径不得把 loading 卡死（遮罩会吞掉所有点击 = 「点了没反应」）——
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: false, failLogin: true, user: { user_id: 1, current_role: '', roles: [] } })
    const self = instantiate(cfg)
    cfg.onPickRole.call(self, { currentTarget: { dataset: { role: 'shipper' } } })
    await tick()
    check('登录失败：复位 loading 并提示', self.data.logging === false && self.data.pendingRole === '' &&
      wx.__calls.toast.some((o) => /登录失败/.test(o.title)), JSON.stringify(wx.__calls.toast.map((o) => o.title)))
  }
  {
    const wx = makeWx()
    const reqLog = []
    const cfg = loadIdx(wx, reqLog, { loggedIn: true, user: { user_id: 1, current_role: 'shipper', roles: ['shipper'] } })
    const self = instantiate(cfg)
    cfg.onPickRole.call(self, { currentTarget: { dataset: { role: 'shipper' } } })
    await tick()
    check('已就位时直接调 switchTab', wx.__calls.switchTab.length === 1)
    wx.__calls.switchTab[0].fail({ errMsg: 'switchTab:fail' })
    check('switchTab 失败：复位并提示（不再静默）', self.data.logging === false && self.data.pendingRole === '' &&
      wx.__calls.toast.some((o) => /进入工作台失败/.test(o.title)), JSON.stringify(wx.__calls.toast.map((o) => o.title)))
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

  // —— 订单页 / 支付页：富化只打当前角色那一侧（此前两侧都打 → 必有一个 403）——
  {
    const cases = [
      ['pages/trade/orders/orders.js', 'fetchAll', 'shipper', '/api/v1/cargo/shipments', '/api/v1/ship/registry'],
      ['pages/trade/orders/orders.js', 'fetchAll', 'owner', '/api/v1/ship/registry', '/api/v1/cargo/shipments'],
      ['pages/trade/payment/payment.js', 'fetch', 'shipper', '/api/v1/cargo/shipments', '/api/v1/ship/registry'],
      ['pages/trade/payment/payment.js', 'fetch', 'owner', '/api/v1/ship/registry', '/api/v1/cargo/shipments'],
    ]
    for (const [file, method, role, want, forbid] of cases) {
      const wx = makeWx()
      const reqLog = []
      const cfg = loadConfig(path.join(MP, file), 'page', wx, reqLog, { loggedIn: true, user: { current_role: role, roles: [role] } })
      const self = instantiate(cfg, { role, orderId: 1 })
      if (typeof cfg[method] !== 'function') { fail(`${path.basename(file)}.${method} 不存在`); continue }
      cfg[method].call(self)
      await tick()
      check(`${path.basename(file)}(${role}) 只打 ${want}，不打 ${forbid}`,
        reqLog.some((r) => r.indexOf(want) !== -1) && !reqLog.some((r) => r.indexOf(forbid) !== -1), JSON.stringify(reqLog))
    }
  }

  // ---------------------------------------------------------------- 汇总
  console.log('\n' + '='.repeat(72))
  console.log(`UI 交互契约校验：OK ${N_OK} · FAIL ${FAILS.length}`)
  FAILS.forEach((f) => console.log('  FAIL · ' + f))
  console.log('='.repeat(72))
  process.exit(FAILS.length === 0 ? 0 : 1)
})()
