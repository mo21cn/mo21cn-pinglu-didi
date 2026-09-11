/**
 * 登录链路真机复现校验（需要后端在 127.0.0.1:8000 运行）。
 *
 * 为什么单独一个脚本：identity 进入失败这类缺陷，静态脚本与「桩掉 request」的
 * 契约校验都看不见 —— 它们只能用假的 request 猜顺序。本脚本加载**真实的**
 * utils/auth.js、utils/request.js、pages/index/index.js，只桩掉 wx 的 UI 部分，
 * 让 wx.request 走真实 HTTP 打后端，因此链路顺序、鉴权头、错误翻译、
 * token 里的 role 全是生产代码本身的行为。
 *
 * 覆盖的回归点（都曾在真实环境踩到）：
 *   ① 首登链路必须 login → bindRole → switchRole，否则带旧角色 token 进工作台 → 全线 403
 *   ② wx.login 不可用（AppID 是占位值）时仍能进入，不被微信登录服务卡死
 *   ③ 身份稳定：重复登录是同一个账号，不会每次新建用户（否则「我的货源」永远为空）
 *   ④ 后端不可达时，弹窗必须点名失败环节并给出真实原因，而不是笼统「登录失败」
 *   ⑤ 任何失败都不得把 loading 遮罩留在页面上（会吞掉全部点击）
 *
 * 用法：node scripts/verify_login_flow.js     （退出码 0=全绿，1=有断言失败，2=后端未就绪）
 */
const path = require('path')
const fs = require('fs')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniapp')
const PAGE = path.join(MP, 'pages', 'index', 'index.js')
const BASE = 'http://127.0.0.1:8000'

let PASS = 0
const FAILS = []
function check(label, cond, extra) {
  if (cond) { PASS++; console.log('  [ok] ' + label) } else {
    FAILS.push(label)
    console.log('  [FAIL] ' + label + (extra ? '  → ' + extra : ''))
  }
}

/** 解出 JWT payload（用于断言 token 里的角色确实是目标角色） */
function jwtPayload(token) {
  try {
    const t = String(token).split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(Buffer.from(t, 'base64').toString('utf8'))
  } catch (e) { return {} }
}

/**
 * 造一个「真 HTTP + 假 UI」的小程序运行时。
 * @param {object} o {storage, wxLoginFails, offline, switchTabFails}
 */
function makeHarness(o) {
  const opt = o || {}
  const storage = Object.assign({}, opt.storage || {})
  const log = []
  const cache = {}

  const wx = {
    getStorageSync: (k) => (k in storage ? storage[k] : ''),
    setStorageSync: (k, v) => { storage[k] = v },
    removeStorageSync: (k) => { delete storage[k] },
    login: (a) => {
      log.push('wx.login')
      if (opt.wxLoginFails) a.fail({ errMsg: 'wx.login:fail auth deny' })
      else a.success({ code: 'wxcode-' + Date.now() })
    },
    showToast: (a) => log.push('TOAST「' + a.title + '」'),
    showModal: (a) => log.push('MODAL「' + a.title + '」\n      ' + String(a.content).replace(/\n/g, '\n      ')),
    switchTab: (a) => {
      log.push('switchTab ' + a.url)
      if (opt.switchTabFails) { if (a.fail) a.fail({ errMsg: 'switchTab:fail can not switch' }) }
      else if (a.success) a.success({})
    },
    request: (a) => {
      if (opt.offline) {
        log.push('HTTP (后端不可达，跳过实测) ' + a.url)
        a.fail({ errMsg: 'request:fail fail to connect' })
        return
      }
      fetch(a.url, {
        method: a.method || 'GET',
        headers: a.header || {},
        body: a.method && a.method !== 'GET' ? JSON.stringify(a.data || {}) : undefined,
      })
        .then(async (r) => {
          const text = await r.text()
          let data
          try { data = JSON.parse(text) } catch (e) { data = text }
          log.push(`HTTP ${a.method || 'GET'} ${a.url} → ${r.status}`)
          a.success({ statusCode: r.status, data })
        })
        .catch((e) => {
          log.push('HTTP-FAIL ' + a.url + ' ' + e.message)
          a.fail({ errMsg: 'request:fail ' + e.message })
        })
    },
  }

  const req = (p) => {
    const s = String(p)
    let file = null
    if (s === './request' || s.indexOf('utils/request') !== -1) file = path.join(MP, 'utils', 'request.js')
    else if (s === './auth' || s.indexOf('utils/auth') !== -1) file = path.join(MP, 'utils', 'auth.js')
    else if (s.indexOf('tabbar') !== -1) return { syncTabBar() {} }
    else return {}
    if (cache[file]) return cache[file]
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', 'wx', fs.readFileSync(file, 'utf8'))(req, mod, mod.exports, wx)
    cache[file] = mod.exports
    return mod.exports
  }

  let cfg = null
  new Function('require', 'Page', 'wx', 'getApp', fs.readFileSync(PAGE, 'utf8'))(req, (c) => { cfg = c }, wx, () => ({}))
  const self = Object.assign({}, cfg)
  self.data = JSON.parse(JSON.stringify(cfg.data || {}))
  self.setData = function (patch, cb) { Object.assign(self.data, patch); if (cb) cb() }

  return {
    wx, log, storage, self, cfg,
    token: () => storage.access_token || '',
    user: () => { try { return JSON.parse(storage.user_info || 'null') } catch (e) { return null } },
    enter: (role) => cfg.onPickRole.call(self, { currentTarget: { dataset: { role } } }),
  }
}

/** 直接带 token 查后端 —— 用于断言「这个身份到底有没有数据」（订单页空白的本质） */
async function apiGet(path, token) {
  try {
    const r = await fetch(BASE + path, { headers: { Authorization: 'Bearer ' + token } })
    if (!r.ok) return { __status: r.status }
    return await r.json()
  } catch (e) {
    return { __error: e.message }
  }
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms || 1500))
const bullet = (log) => log.forEach((l) => console.log('   · ' + l))

;(async () => {
  try {
    const r = await fetch(BASE + '/healthz')
    if (!r.ok) throw new Error('HTTP ' + r.status)
  } catch (e) {
    console.error('[FATAL] 后端未就绪（' + e.message + '）')
    console.error('请先启动：cd backend && APP_ENV=development uvicorn app.main:app --reload --port 8000')
    process.exit(2)
  }

  console.log('='.repeat(74))
  console.log('① 清除缓存后点「船东」· 且 wx.login 不可用（AppID 为占位值时的真实环境）')
  console.log('='.repeat(74))
  {
    const h = makeHarness({ wxLoginFails: true })
    h.enter('owner')
    await wait(2500)
    bullet(h.log)
    check('全程未依赖 wx.login', h.log.indexOf('wx.login') === -1)
    check('身份已映射为船东演示账号（seed-owner）',
      h.storage.dev_device_code === 'seed-owner', JSON.stringify(h.storage.dev_device_code))
    check('已进入船东工作台', h.log.indexOf('switchTab /pages/owner/owner') !== -1)
    check('没有弹任何失败提示', !h.log.some((l) => /MODAL|TOAST/.test(l)),
      JSON.stringify(h.log.filter((l) => /MODAL|TOAST/.test(l))))
    check('token 里的角色已是 owner（不是登录默认的 shipper）',
      jwtPayload(h.token()).role === 'owner', String(jwtPayload(h.token()).role))
    check('loading 遮罩已复位（不会吞掉点击）', h.self.data.logging === false)
    check('链路顺序 login → bind-role → switch-role 全部命中',
      ['/auth/login', '/auth/bind-role', '/auth/switch-role'].every((u) => h.log.some((l) => l.indexOf(u) !== -1)))
    const od = await apiGet('/api/v1/order/orders?size=100', h.token())
    // 阈值恒为「非空」：本断言要证明的是「订单页不是永久空态」，与库内历史数据量无关。
    // 曾写 >= 10 —— 那是照本机累积数据（12 单）拍的数字，在「空库 + seed」的干净环境
    // （CI 首跑 / 甲方 clone）必然误报，且断言名本来就写着「非空」。
    check('★ 船东视角订单非空（订单页不再是「暂无订单」空态）',
      (od.items || []).length >= 1, 'items=' + (od.items || []).length + ' status=' + od.__status)
    const sr = await apiGet('/api/v1/ship/registry?size=100', h.token())
    check('★ 船东「我的船队」非空', (sr.items || []).length >= 3, 'items=' + (sr.items || []).length)
  }

  console.log('\n' + '='.repeat(74))
  console.log('② 同一台设备接着点「货主」')
  console.log('='.repeat(74))
  let uid = null
  {
    // 上一轮已登录为船东演示账号，这一轮改点货主 → 必须换到货主演示账号
    const h = makeHarness({ wxLoginFails: true, storage: { dev_device_code: 'seed-owner' } })
    h.enter('shipper')
    await wait(2500)
    bullet(h.log)
    check('已进入货主工作台', h.log.indexOf('switchTab /pages/shipper/shipper') !== -1)
    check('没有弹任何失败提示', !h.log.some((l) => /MODAL|TOAST/.test(l)))
    check('token 里的角色是 shipper', jwtPayload(h.token()).role === 'shipper', String(jwtPayload(h.token()).role))
    check('身份已换到货主演示账号（seed-shipper）',
      h.storage.dev_device_code === 'seed-shipper', JSON.stringify(h.storage.dev_device_code))
    const od = await apiGet('/api/v1/order/orders?size=100', h.token())
    check('★ 货主视角订单非空', (od.items || []).length >= 1, 'items=' + (od.items || []).length)
    const cg = await apiGet('/api/v1/cargo/shipments?size=100', h.token())
    // 「我的货源」非空才是身份稳定的证据（见脚本头部 ③：重复登录不得每次新建账号）。
    // 阈值不设高，避免与库内历史数据量耦合。
    check('★ 货主「我的货源」非空', (cg.items || []).length >= 1, 'items=' + (cg.items || []).length)
    uid = (h.user() || {}).user_id
  }

  console.log('\n' + '='.repeat(74))
  console.log('③ 身份稳定性：重新编译后重新登录，仍是同一个账号')
  console.log('='.repeat(74))
  {
    const h = makeHarness({ wxLoginFails: true })
    h.enter('shipper')
    await wait(2500)
    const uid2 = (h.user() || {}).user_id
    console.log('   首次 user_id=' + uid + ' / 重新登录 user_id=' + uid2)
    check('两次独立启动拿到同一个 user_id', !!uid2 && uid2 === uid, `${uid} vs ${uid2}`)
  }

  console.log('\n' + '='.repeat(74))
  console.log('④ 后端不可达时，报错必须能定位')
  console.log('='.repeat(74))
  {
    const h = makeHarness({ wxLoginFails: true, offline: true })
    h.enter('owner')
    await wait(600)
    bullet(h.log)
    const modal = h.log.find((l) => l.indexOf('MODAL') === 0) || ''
    check('弹窗点名失败环节（登录）', /失败环节：登录/.test(modal), modal)
    check('弹窗给出真实原因（不是笼统「登录失败」）', /无法连接后端/.test(modal), modal)
    check('loading 遮罩已复位', h.self.data.logging === false)
  }

  console.log('\n' + '='.repeat(74))
  console.log('⑤ 联调指定身份（dev_login_code）优先于开发固定身份')
  console.log('='.repeat(74))
  {
    const h = makeHarness({ wxLoginFails: true, storage: { dev_login_code: 'seed-owner' } })
    h.enter('owner')
    await wait(2500)
    bullet(h.log)
    check('使用 seed-owner 身份登录', /mock-openid-seed-owner/.test(JSON.stringify(jwtPayload(h.token()))),
      JSON.stringify(jwtPayload(h.token())))
    check('已进入船东工作台', h.log.indexOf('switchTab /pages/owner/owner') !== -1)
    check('手工身份不会被角色映射覆盖（走查脚本靠它注入 seed-port）',
      !h.storage.dev_device_code, JSON.stringify(h.storage.dev_device_code))
  }

  console.log('\n' + '='.repeat(74))
  console.log(`登录链路复现校验：OK ${PASS} · FAIL ${FAILS.length}`)
  FAILS.forEach((f) => console.log('  FAIL · ' + f))
  console.log('='.repeat(74))
  process.exit(FAILS.length ? 1 : 0)
})()
