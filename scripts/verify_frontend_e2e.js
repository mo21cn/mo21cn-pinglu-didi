/**
 * 前端端到端走查（不需要微信开发者工具）——在真实后端上验证小程序的**取数逻辑与模板契约**。
 *
 * 做法：先把页面会发的每个请求在真实接口上跑一遍拿到真载荷，再用这些载荷驱动页面自身的
 * 取数/装饰逻辑（`new Function` 桩掉 require/Page/wx），最后核对：
 *   ① 取数是否抛异常            ② 结束后是否卡在 loading（页面卡"加载中"）
 *   ③ 错误态是否被误触发        ④ 关键列表是否有数据（演示路径依赖）
 *   ⑤ 模板里读的每个字段是否真的被产出（防渲染空白）
 *   ⑥ 派生数值是否越界（甘特 left/width、评分百分比、金额锁定值、预检口径）
 *
 * 用法：
 *   node scripts/verify_frontend_e2e.js                     # 默认 http://127.0.0.1:8000
 *   node scripts/verify_frontend_e2e.js --base=http://x:8000
 *
 * 前置：后端已启动（推荐 LLM_MOCK=true WECHAT_MOCK=true），且演示数据已铺
 *   （backend/scripts/seed_demo.py）。退出码 0=全绿，1=有断言失败，2=初始化失败。
 *
 * 方法论见 skill：miniapp-page-logic-verification。
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const BASE = (process.argv.find((a) => a.indexOf('--base=') === 0) || '').split('=')[1] || 'http://127.0.0.1:8000'

let N_OK = 0
const FAILS = []
const NOTES = []
const ok = () => { N_OK++ }
const fail = (label, extra) => {
  FAILS.push(label + (extra ? '  → ' + extra : ''))
  console.log('  [FAIL] ' + label + (extra ? '  → ' + extra : ''))
}
const note = (label) => { NOTES.push(label); console.log('  [note] ' + label) }

// ---------------------------------------------------------------- 实时拉取真实载荷
const D = {}          // 列表/详情载荷
const schedules = {}  // berthId → 档期
const payments = {}   // orderId → 支付单（无单则不存在）
const contracts = {}  // orderId → 合同（不可生成则不存在）
const CONTRACT_ERR = {} // orderId → 服务端拒答原文
let refCargoId = 0
let refShipId = 0

// 委托支线（ENT-022）：这些载荷的可见性口径与前 13 页**不同** —— 它走
// 「组织成员 + 委托授权」的叠加层，与 `current_role` 无关（DR-0008）。
const entrustDetail = {}        // assignmentId → 委托详情
const entrustWorkbench = {}     // assignmentId → 七槽位工作台摘要
const entrustQueueByStatus = {} // status → 组织队列（同一筛选条件下的**真载荷**）

const qs = (p) => (p ? '?' + new URLSearchParams(p).toString() : '')

async function api(method, p, { token, body } = {}) {
  const res = await fetch(BASE + '/api/v1' + p, {
    method,
    headers: Object.assign(
      { 'Content-Type': 'application/json' },
      token ? { Authorization: 'Bearer ' + token } : {}
    ),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch (e) { data = null }
  return { status: res.status, data }
}

async function bootstrap() {
  const tok = {}
  for (const r of ['shipper', 'owner', 'port']) {
    const res = await api('POST', '/auth/login', { body: { code: 'seed-' + r } })
    if (res.status !== 200) throw new Error('登录失败 seed-' + r + ': ' + res.status + ' ' + JSON.stringify(res.data))
    // ⚠️ login 签发的 token 带的是**库里持久化的 current_role**，不是请求参数。
    // 其它脚本/探针一旦改过该账号的 current_role，这里就会拿到「角色不对的 token」→
    // 后续接口 403。故登录后显式切到目标角色，让本脚本与运行顺序无关。
    const sw = await api('POST', '/auth/switch-role', { body: { role: r }, token: res.data.access_token })
    if (sw.status !== 200) throw new Error('切换角色失败 seed-' + r + ' → ' + r + ': ' + sw.status + ' ' + JSON.stringify(sw.data))
    tok[r] = sw.data.access_token
  }
  console.log('三角色登录成功：seed-shipper / seed-owner / seed-port')

  const get = async (p, role, params) => {
    const res = await api('GET', p + qs(params), { token: tok[role] })
    if (res.status !== 200) throw new Error('GET ' + p + ' → ' + res.status + ' ' + JSON.stringify(res.data).slice(0, 160))
    return res.data
  }

  D.cargoList = await get('/cargo/shipments', 'shipper', { size: 50 })
  D.ships = await get('/ship/registry', 'owner', { size: 50 })
  D.berths = await get('/port/berths', 'port', { size: 50 })
  D.appts = await get('/port/appts-review', 'port', { status: '', size: 100 })
  D.orders = await get('/order/orders', 'shipper', { size: 100 })

  for (const b of D.berths.items || []) {
    const res = await api('GET', '/port/berths/' + b.id + '/schedule', { token: tok.port })
    if (res.status === 200) schedules[b.id] = res.data
  }
  for (const o of D.orders.items || []) {
    const res = await api('GET', '/payment/payments/order/' + o.id, { token: tok.shipper })
    if (res.status === 200) payments[o.id] = res.data
  }
  // 合同：按订单状态各取一个代表（同状态条款/风险一致，避免重复打模型）
  const repByStatus = {}
  for (const o of D.orders.items || []) if (!repByStatus[o.status]) repByStatus[o.status] = o
  for (const o of Object.values(repByStatus)) {
    const res = await api('POST', '/agent/contract/generate', { token: tok.shipper, body: { order_id: o.id } })
    if (res.status === 200) contracts[o.id] = res.data
    else CONTRACT_ERR[o.id] = (res.data && res.data.detail) || '合同生成失败'
  }
  D.contract = Object.values(contracts)[0] || null

  // 撮合：货主侧取第一个已发布货源；船东侧优先取候选最多的已认证船
  const published = (D.cargoList.items || []).filter((c) => c.status === 'published')
  if (published.length) {
    refCargoId = published[0].id
    const res = await api('POST', '/match/cargos/' + refCargoId + '/ships', { token: tok.shipper })
    if (res.status === 200) D.matchCargo = res.data
  }
  const verified = (D.ships.items || []).filter((s) => s.status === 'verified')
  let best = null
  for (const s of verified) {
    const res = await api('POST', '/match/ships/' + s.id + '/cargos', { token: tok.owner })
    if (res.status === 200 && (!best || (res.data.total || 0) > (best.data.total || 0))) {
      best = { shipId: s.id, data: res.data }
    }
  }
  if (best) { refShipId = best.shipId; D.matchShip = best.data }

  // 走查用例：从真实数据里挑代表，避免硬编码 ID
  D.berthCases = (D.berths.items || []).map((b) => {
    const sc = schedules[b.id]
    if (!sc) return null
    const cap = Number(sc.berth.concurrent_capacity) || 0
    const n = (sc.confirmed || []).length
    return [b.id, n === 0 ? '空档' : n >= cap ? '满档' : '混合']
  }).filter(Boolean)

  const contractTag = { completed: '已完成 · 期望 0 风险', matched: '已撮合 · 期望有风险', cancelled: '已撤销 · 期望错误态' }
  D.contractCases = Object.values(repByStatus)
    .filter((o) => contractTag[o.status])
    .map((o) => [o.id, contractTag[o.status], o.status === 'cancelled'])

  const payLabel = { pending: '待支付', paid: '已支付', refunded: '已退款', closed: '已关闭' }
  const byPayStatus = {}
  for (const [oid, p] of Object.entries(payments)) if (!byPayStatus[p.status]) byPayStatus[p.status] = Number(oid)
  D.payCases = Object.entries(byPayStatus).map(([st, oid]) => [oid, payLabel[st] || st, null])
  // 「无支付单」要按订单可支付性分成两类断言——不可支付（面议/已撤单/已完成）时
  // 页面本就不该给发起入口。旧版只有一个宽松兜底候选，种子为待支付锚点预建支付单后
  // 会退到面议单上，把正确行为判成失败。
  const payableNoPay = (o) => !payments[o.id] && o.status === 'matched' && o.freight_price != null
  const noPayYes = (D.orders.items || []).find(payableNoPay)
  const noPayNo = (D.orders.items || []).find((o) => !payments[o.id] && !payableNoPay(o))
  if (noPayYes) D.payCases.push([noPayYes.id, '无支付单·可支付', true])
  else note('S2 支付 · 库内无「已撮合且运费已定、尚未发起支付」的订单，正向发起入口用例由 seed 待支付锚点预建的支付单覆盖')
  if (noPayNo) D.payCases.push([noPayNo.id, '无支付单·不可支付', false])

  // ── 委托支线载荷（ENT-022）──────────────────────────────────────────────
  // 委托页面取数走**叠加层**身份（组织成员 / 委托授权），不是 `current_role`。
  // 所以这里刻意复用两个**既有** token：
  //   · seed-shipper —— 委托的创建人（详情页可见性来自"我是创建人"）；
  //   · seed-owner   —— 演示组织的经理（队列页的组织视角）。
  // 把两个账号放到正确成员位置上的是 `backend/scripts/seed_entrust_demo.py`；
  // 本脚本不新增登录身份，也就不需要 `dev_login_code` 之外的任何约定。
  D.entrustMine = await get('/entrust/assignments', 'shipper', { view: 'owner', size: 50 })
  D.entrustOrgs = await get('/entrust/my-orgs', 'owner')
  const entrustOrgId = (D.entrustOrgs.items || []).length ? D.entrustOrgs.items[0].org_id : null
  if (entrustOrgId) {
    D.entrustQueue = await get('/entrust/assignments', 'owner',
      { view: 'org', org_id: entrustOrgId, size: 50 })
    // 筛选条件下的真载荷：不给真载荷的话，"点筛选后列表变了没"只能证明
    // 页面把数组换掉了，证明不了它换对了 —— 那正是筛选最容易错的地方。
    for (const st of ['draft', 'submitted', 'claimed', 'cancelled']) {
      entrustQueueByStatus[st] = await get('/entrust/assignments', 'owner',
        { view: 'org', org_id: entrustOrgId, status: st, size: 50 })
    }
  } else {
    D.entrustQueue = { total: 0, page: 1, size: 50, items: [] }
  }

  for (const a of D.entrustMine.items || []) {
    const d = await api('GET', '/entrust/assignments/' + a.assignment_id, { token: tok.shipper })
    if (d.status === 200) entrustDetail[a.assignment_id] = d.data
    const w = await api('GET', '/entrust/assignments/' + a.assignment_id + '/workbench',
      { token: tok.shipper })
    if (w.status === 200) entrustWorkbench[a.assignment_id] = w.data
  }
  D.entrustCases = (D.entrustMine.items || []).map((a) => [
    a.assignment_id,
    '#' + a.assignment_id + ' ' + a.status,
    a.status === 'submitted'
  ])

  console.log('载荷就绪：货 %d · 船 %d · 泊位 %d · 预约 %d · 订单 %d · 支付单 %d · 合同 %d',
    (D.cargoList.items || []).length, (D.ships.items || []).length, (D.berths.items || []).length,
    (D.appts.items || []).length, (D.orders.items || []).length,
    Object.keys(payments).length, Object.keys(contracts).length)
  console.log('委托载荷：我的委托 %d · 组织队列 %d · 组织 %d 个 · 工作台 %d 张',
    (D.entrustMine.items || []).length, (D.entrustQueue.items || []).length,
    (D.entrustOrgs.items || []).length, Object.keys(entrustWorkbench).length)
  if (!(D.entrustMine.items || []).length) {
    note('委托支线无载荷 —— 请确认已铺 backend/scripts/seed_entrust_demo.py')
  }
}

/** url → 载荷（与页面真实请求一一对应） */
function route(url, body) {
  const u = String(url).replace(/^\/api\/v1/, '')
  let m
  if ((m = u.match(/^\/payment\/payments\/order\/(\d+)$/))) {
    const p = payments[Number(m[1])]
    return p ? { ok: p } : { err: '支付单不存在' }
  }
  if ((m = u.match(/^\/order\/orders\/(\d+)$/))) {
    const o = (D.orders.items || []).find((x) => x.id === Number(m[1]))
    return o ? { ok: o } : { err: '订单不存在' }
  }
  if ((m = u.match(/^\/port\/berths\/(\d+)\/schedule$/))) {
    const s = schedules[Number(m[1])]
    return s ? { ok: s } : { err: '泊位不存在' }
  }
  if ((m = u.match(/^\/match\/cargos\/\d+\/ships$/))) return { ok: D.matchCargo }
  if ((m = u.match(/^\/match\/ships\/\d+\/cargos$/))) return { ok: D.matchShip }
  if (u.indexOf('/agent/contract/generate') === 0) {
    const oid = Number((body && body.order_id) || 0)
    const c = contracts[oid]
    return c ? { ok: c } : { err: CONTRACT_ERR[oid] || '合同生成失败' }
  }
  if (u.indexOf('/port/appts-review') === 0) return { ok: D.appts }
  if (u.indexOf('/port/berths') === 0) return { ok: D.berths }
  if (u.indexOf('/ship/registry') === 0) return { ok: D.ships }
  if (u.indexOf('/cargo/shipments') === 0) return { ok: D.cargoList }
  if (u.indexOf('/order/orders') === 0) return { ok: D.orders }

  // ── 委托支线（ENT-022）──────────────────────────────────────────────
  // 顺序要紧：`/workbench` 与 `/{id}` 都用**精确正则**，必须排在「列表」之前 ——
  // 否则 `fetchWorkbench` 会被列表分支接走，页面把一个分页对象当成工作台用。
  // 那种错不会抛异常，只会让七个槽位全部空掉（看起来像"这单还没数据"）。
  if ((m = u.match(/^\/entrust\/assignments\/(\d+)\/workbench$/))) {
    const w = entrustWorkbench[Number(m[1])]
    return w ? { ok: w } : { err: '工作台不存在：' + m[1] }
  }
  if ((m = u.match(/^\/entrust\/assignments\/(\d+)$/))) {
    const d = entrustDetail[Number(m[1])]
    return d ? { ok: d } : { err: '委托不存在：' + m[1] }
  }
  if (u === '/entrust/my-orgs') return { ok: D.entrustOrgs }
  if (u === '/entrust/assignments') {
    const q = body || {}
    if ((q.view || 'owner') === 'org') {
      if (q.status) {
        const r = entrustQueueByStatus[q.status]
        return r ? { ok: r } : { err: '未拉取状态 ' + q.status + ' 的队列载荷' }
      }
      return { ok: D.entrustQueue }
    }
    return { ok: D.entrustMine }
  }
  // 写端点（记录任务 / 受理委托）**有意不登记**：本脚本只驱动取数链路，
  // 页面若在取数时误发写请求，应在这里显式失败而不是被静默吞掉。
  if (u.indexOf('/entrust/') === 0) return { err: '未登记的委托接口 ' + u }

  if (u === '/healthz') return { ok: { status: 'ok' } }   // 首页连通性预检
  return { err: '未登记的接口 ' + u }
}

// ---------------------------------------------------------------- 模板核对基底
const pageUniverse = {}   // page → Set(键)
const pageMeta = {}       // page → { jsFile }
const BUILTIN = new Set(['length', 'index', 'item', 'toString', 'slice', 'toFixed',
  'includes', 'indexOf', 'join', 'split', 'map', 'filter', 'concat', 'replace', 'trim'])

function keyUniverse(v, out, depth) {
  out = out || new Set()
  depth = depth || 0
  if (depth > 5 || v === null || typeof v !== 'object') return out
  if (Array.isArray(v)) { v.slice(0, 10).forEach((x) => keyUniverse(x, out, depth + 1)); return out }
  for (const k of Object.keys(v)) { out.add(k); keyUniverse(v[k], out, depth + 1) }
  return out
}

function jsKeyUniverse(file) {
  const src = fs.readFileSync(file, 'utf8')
  const out = new Set()
  let m
  const re = /(?:^|[{,(\[;])\s*([A-Za-z_$][\w$]*)\s*:/gm
  while ((m = re.exec(src))) out.add(m[1])
  const re2 = /["']([A-Za-z_$][\w$]*)["']\s*:/g
  while ((m = re2.exec(src))) out.add(m[1])
  return out
}

function collect(page, jsFile, data) {
  if (!pageUniverse[page]) {
    pageUniverse[page] = keyUniverse(data)
    pageMeta[page] = { jsFile }
  } else {
    for (const k of keyUniverse(data)) pageUniverse[page].add(k)
  }
}

function wxmlRefs(page) {
  const p = path.join(ROOT, 'miniapp', page + '.wxml')
  if (!fs.existsSync(p)) return null
  const src = fs.readFileSync(p, 'utf8')
  const roots = {}
  const refs = src.match(/\{\{[^}]*?\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)/g) || []
  for (const r of refs) {
    const m = r.match(/\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)/)
    if (!m || BUILTIN.has(m[2])) continue
    roots[m[1]] = roots[m[1]] || new Set()
    roots[m[1]].add(m[2])
  }
  return roots
}

/** 全部页面跑完后统一核对模板字段 */
function auditTemplates() {
  console.log('\n' + '='.repeat(78))
  console.log('模板字段核对（同页跨状态取并集；含页面内静态配置键）')
  console.log('='.repeat(78))
  for (const [page, set] of Object.entries(pageUniverse)) {
    const roots = wxmlRefs(page)
    if (!roots) { note(page + ' · 无 .wxml'); continue }
    const jsKeys = jsKeyUniverse(pageMeta[page].jsFile)
    const have = new Set([...set, ...jsKeys])
    const missing = []
    for (const [root, fields] of Object.entries(roots)) {
      for (const f of fields) if (!have.has(f)) missing.push(root + '.' + f)
    }
    if (missing.length) fail(page + ' · 模板读取但数据与静态配置均未产出', missing.join(', '))
    else ok()
  }
}

// ---------------------------------------------------------------- 页面装载
function loadPage(file, ctx) {
  const src = fs.readFileSync(file, 'utf8')
  let cfg = null
  const requireStub = (p) => {
    const s = String(p)
    // 纯常量/纯函数模块直接加载真实实现（P3-4 常量收敛 + P2-3 日期工具后页面依赖它们）
    const U = (name) => require(path.join(ROOT, 'miniapp', 'utils', name))
    if (s.indexOf('utils/ports') !== -1) return U('ports.js')
    if (s.indexOf('utils/constants') !== -1) return U('constants.js')
    if (s.indexOf('utils/dates') !== -1) return U('dates.js')
    // 路由注册表：纯数据 + 纯函数（加载期不碰 wx），直接加载真实实现。
    // 页面用 R.go()/R.canDeepLink() 时必须走真实表，否则「未登记路由」这类
    // 问题会被桩掩盖掉（与 routes 在 CI 里承担的门禁职责一致）。
    if (s.indexOf('utils/routes') !== -1) return U('routes.js')
    // 委托发货入口契约模块：纯函数部分加载真实实现，只把「入口探测」桩成
    // 「无权限」—— 本脚本关注的是已登录状态下各页面拿真数据渲染；入口可见性
    // 的四种分支由 scripts/verify_entrust_ui.js 用真实输入逐一覆盖。
    // ⚠️ 新增 utils/*.js 时必须在此登记：未登记的模块会落到末尾的 `return {}`，
    // 表现为页面调用时 "xxx is not a function"（本模块首次接入时就这样红过一次）。
    // 委托发货（ENT-022）：**常量与投影用真实实现** —— 槽位配置表、四态文案、
    // 「未开放 ≠ 空」的分支全在 utils/entrust.js 里，桩掉它等于把被测对象换成
    // 我自己写的假货。只把**取数**接到本脚本的 route() 上：载荷仍是 bootstrap
    // 用真实接口拉回来的，页面拿到的形状与真机一致（这就是 route() 的意义）。
    //
    // ⚠️ 新增一个取数函数就必须在此登记 —— 漏登记的会落到真实 request.js，
    //    在 Node 里（无 wx.request）直接抛错，表现为"页面取数失败"，
    //    与真实的接口问题长得一样，很难一眼分辨。
    if (s.indexOf('utils/entrust') !== -1) {
      const real = U('entrust.js')
      const fetchVia = (url, data) => {
        const r = route(url, data)
        return r.err ? Promise.reject(new Error(r.err)) : Promise.resolve(r.ok)
      }
      return Object.assign({}, real, {
        probeEntry: () => Promise.resolve({ visible: false, reason: 'denied', hint: '' }),
        fetchMyOrgs: () => fetchVia('/entrust/my-orgs'),
        fetchQueue: (opts) => {
          const o = opts || {}
          const data = { view: 'org', page: o.page || 1, size: o.size || 20 }
          if (o.orgId) data.org_id = o.orgId
          if (o.status) data.status = o.status
          return fetchVia('/entrust/assignments', data)
        },
        fetchAssignment: (id) => fetchVia('/entrust/assignments/' + id),
        fetchWorkbench: (id) => fetchVia('/entrust/assignments/' + id + '/workbench'),
        createTask: () => Promise.resolve({}),
        claimAssignment: () => Promise.resolve({})
      })
    }
    if (s.indexOf('auth') !== -1) {
      return {
        getUser: () => ({ current_role: ctx.role, user_id: ctx.uid || 1 }),
        isLoggedIn: () => true, clearUser() {}, login: () => Promise.resolve({}),
        switchRole: () => Promise.resolve({}), bindRole: () => Promise.resolve({}),
        // 开发期身份映射 + 统一进入链路（真实实现见 utils/auth.js）。
        // 本脚本关注「已登录状态下各页面拿真数据渲染」，故桩成直通。
        ensureDevAccount: () => false, enterRole: () => Promise.resolve({}),
        ROLE_LABELS: { shipper: '货主', owner: '船东', port: '港口方' },
      }
    }
    if (s.indexOf('tabbar') !== -1) return { syncTabBar() {} }
    if (s.indexOf('request') !== -1) {
      return {
        request: (opts) => {
          const r = route(opts.url, opts.data)
          return r.err ? Promise.reject(new Error(r.err)) : Promise.resolve(r.ok)
        },
        getToken: () => 'fake-token', setToken() {}, clearToken() {},
        BASE_URL: 'http://127.0.0.1:8000',
      }
    }
    return {}
  }
  const wxStub = {
    setNavigationBarTitle() {}, showToast() {}, showModal() {}, showLoading() {},
    hideLoading() {}, switchTab() {}, navigateTo() {}, redirectTo() {}, reLaunch() {},
    stopPullDownRefresh() {}, showActionSheet() {}, setClipboardData() {},
    getWindowInfo: () => ({ statusBarHeight: 44, windowWidth: 375, windowHeight: 812 }),
    getSystemInfoSync: () => ({ statusBarHeight: 44, windowWidth: 375 }),
    getStorageSync: (k) => (k === 'port_appt_detail' ? ctx.storageAppt : ''),
    setStorageSync() {}, removeStorageSync() {},
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: () => ({ exec: (cb) => cb && cb([{}]) }) }), exec() {} }),
    nextTick: (fn) => fn(), pageScrollTo() {}, setData() {},
  }
  new Function('require', 'Page', 'wx', 'getApp', src)(
    requireStub, (c) => { cfg = c }, wxStub,
    () => ({ globalData: { role: ctx.role }, routeByRole: () => '/pages/index/index', workspacePages: {}, publishPages: {} })
  )
  return cfg
}

function instantiate(cfg, ctx) {
  const self = Object.assign({}, cfg)
  self.data = Object.assign(JSON.parse(JSON.stringify(cfg.data || {})), ctx.data || {})
  self._set = null
  self.setData = function (patch, cb) {
    self._set = Object.assign({}, self._set, patch)
    Object.assign(self.data, patch) // 页面会回读 this.data，必须同步写入
    if (cb) cb()
  }
  self._final = () => Object.assign({}, self.data, self._set || {})
  return self
}

const tick = (ms) => new Promise((r) => setTimeout(r, ms || 60))

/** 走一个页面：可选先跑 onLoad/onShow，再调取数方法，最后断言基础不变量 */
async function walk(label, page, entry, ctx, pre) {
  console.log('\n--- ' + label + ' ---')
  const file = path.join(ROOT, 'miniapp', page + '.js')
  let cfg
  try { cfg = loadPage(file, ctx) } catch (e) { fail(label + ' · 页面 JS 装载失败', e.message); return null }
  if (!cfg) { fail(label + ' · 未捕获到 Page 配置'); return null }
  const self = instantiate(cfg, ctx)
  let thrown = null
  try {
    for (const m of pre || []) if (cfg[m]) cfg[m].call(self, ctx.arg || {})
    if (entry) {
      if (!cfg[entry]) { fail(label + ' · 方法不存在: ' + entry); return null }
      cfg[entry].call(self)
    }
    await tick(ctx.wait || 90)
  } catch (e) { thrown = e }
  if (thrown) { fail(label + ' · ' + (entry || pre.join(',')) + ' 抛异常', thrown.message); return self }
  ok()
  const d = self._final()
  if (d.loading === true) fail(label + ' · 取数结束后仍 loading=true（页面会卡在"加载中"）')
  else ok()
  if (d.error) {
    if (ctx.allowError) ok()
    else fail(label + ' · 错误态被触发: ' + d.error)
  } else if (ctx.allowError) {
    fail(label + ' · 期望错误态但未触发')
  } else ok()
  collect(page, file, d)
  return self
}

const expectList = (label, arr, key, { nonEmpty } = {}) => {
  if (!Array.isArray(arr)) return fail(label + ' · ' + key + ' 结构异常', typeof arr)
  if (nonEmpty && arr.length === 0) return fail(label + ' · ' + key + ' 为空（演示会看到空列表）')
  ok()
}

// ================================================================ 逐页走查
;(async () => {
  try {
    await bootstrap()
  } catch (e) {
    console.error('\n[FATAL] 初始化失败：' + e.message)
    console.error('请确认：① 后端已启动（cd backend && LLM_MOCK=true WECHAT_MOCK=true uvicorn app.main:app）')
    console.error('        ② 演示数据已铺（backend/scripts/seed_demo.py）')
    process.exit(2)
  }

  // ① 首页身份选择
  {
    const s = await walk('01 首页身份选择', 'pages/index/index', null, { role: 'shipper' }, ['onLoad'])
    // onLoad 里的 /healthz 预检：后端可用时不得置灰成「未连接后端」
    if (s) {
      const d = s._final()
      if (d.backendDown === true) fail('01 · /healthz 预检误判为后端不可用', String(d.backendDown))
      else ok()
    }
  }

  // ② 货主找船
  {
    const s = await walk('02 货主找船', 'pages/shipper/shipper', 'fetchMyCargoCount', { role: 'shipper' }, ['onLoad'])
    if (s) {
      const d = s._final()
      if (typeof d.myCargoTotal !== 'number') fail('02 · myCargoTotal 不是数字', String(d.myCargoTotal))
      else ok()
    }
  }

  // ③ 船东找货（货源大厅为页面内演示数据）
  {
    const s = await walk('03 船东找货 · 货源大厅', 'pages/owner/owner', 'applyFilter', { role: 'owner' }, [])
    if (s) expectList('03 船东找货 · 货源大厅', s._final().list, 'list', { nonEmpty: true })
    const s2 = await walk('03 船东找货 · 我的船队', 'pages/owner/owner', 'fetchShipList', { role: 'owner' }, [])
    if (s2) {
      const d = s2._final()
      expectList('03 · 船队', d.shipList, 'shipList', { nonEmpty: true })
      if (d.shipCount !== (d.shipList || []).length) fail('03 · shipCount 与 shipList 长度不一致')
      else ok()
      const verified = (d.shipList || []).filter((x) => x.status === 'verified')
      if (verified.length === 0) fail('03 · 无已审核船舶 → "智能找货"按钮全部不可见')
      else ok()
    }
  }

  // ④ 港口服务（泊位 + 预约）
  {
    const s = await walk('07 港口服务 · 泊位列表', 'pages/port/port', 'fetchBerths', { role: 'port' }, [])
    if (s) expectList('07 · 泊位列表', s._final().berthList, 'berthList', { nonEmpty: true })
    const s2 = await walk('07 港口服务 · 预约审核', 'pages/port/port', 'fetchAppts', { role: 'port' }, [])
    if (s2) {
      const d = s2._final()
      expectList('07 · 预约审核', d.apptList, 'apptList', { nonEmpty: true })
      if (typeof d.apptTotal !== 'number') fail('07 · apptTotal 不是数字')
      else ok()
    }
  }

  // ⑤ 泊位档期详情（满档 / 空档）
  for (const [bid, tag] of D.berthCases) {
    const s = await walk('S3 泊位档期详情 · ' + tag + ' #' + bid, 'pages/port/berth/berth', null,
      { role: 'port', arg: { id: bid } }, ['onLoad'])
    if (s) {
      const d = s._final()
      const bars = d.bars || []
      const n = (schedules[bid].confirmed || []).length
      if (bars.length !== n) fail('S3 泊位 · 甘特条数与会话数不符', 'bars=' + bars.length + ' confirmed=' + n)
      else ok()
      for (const bar of bars) {
        const L = parseFloat(bar.left), W = parseFloat(bar.width)
        if (!(L >= 0 && L <= 100)) fail('S3 泊位 · 甘特 left 越界', String(L))
        else if (!(W > 0 && W <= 100)) fail('S3 泊位 · 甘特 width 越界', String(W))
        else ok()
      }
      if (bars.length && Math.abs(parseFloat(bars[0].left)) > 0.5) fail('S3 泊位 · 首条未贴左边', String(bars[0].left))
      else ok()
      const lastRight = bars.length ? parseFloat(bars[bars.length - 1].left) + parseFloat(bars[bars.length - 1].width) : 0
      if (bars.length && lastRight > 100.5) fail('S3 泊位 · 末条右端越界', String(lastRight))
      else ok()
    }
  }

  // ⑥ 预约审核详情（待确认 / 已锁定）
  const apptItems = D.appts.items || []
  for (const [tag, sample] of [
    ['待确认', apptItems.find((x) => x.status === 'pending')],
    ['已锁定', apptItems.find((x) => x.status === 'confirmed')],
  ]) {
    if (!sample) { note('S3 预约 · 缺少 "' + tag + '" 样本，跳过'); continue }
    const s = await walk('S3 预约审核详情 · ' + tag + ' #' + sample.id, 'pages/port/appt/appt', null,
      { role: 'port', arg: { id: sample.id }, storageAppt: sample }, ['onLoad'])
    if (s) {
      const d = s._final()
      // 口径一致性：canConfirm 只应在「待确认 且 未超容量」时为真
      const wantConfirm = sample.status === 'pending' && !d.conflict
      if (d.canConfirm !== wantConfirm) fail('S3 预约 · canConfirm 与状态/预检不一致',
        'canConfirm=' + d.canConfirm + ' 期望=' + wantConfirm)
      else ok()
      // 预检必须排除自身：已确认单若把自己算进去，会永远判冲突
      if (sample.status === 'confirmed' && d.conflict === true) fail('S3 预约 · 已锁定单被判为冲突（自比缺陷）')
      else ok()
      if (typeof d.checkText !== 'string' || !d.checkText.length) fail('S3 预约 · 缺少容量预检文案')
      else ok()
      if (sample.status === 'pending') {
        const sc = schedules[sample.berth_id]
        const cap = sc ? Number(sc.berth.concurrent_capacity) : 0
        const s0 = new Date(sample.plan_start).getTime()
        const e0 = new Date(sample.plan_end).getTime()
        const overlap = sc ? (sc.confirmed || []).filter((c) => {
          const cs = new Date(c.plan_start).getTime()
          const ce = new Date(c.plan_end).getTime()
          return cs < e0 && ce > s0 // 左闭右开，与服务端同口径
        }).length : 0
        const wantConflict = overlap + 1 > cap
        if (d.conflict !== wantConflict) {
          fail('S3 预约 · 预检口径与服务端不一致',
            'conflict=' + d.conflict + ' 期望=' + wantConflict + '（重叠' + overlap + '/容量' + cap + '）')
        } else ok()
        note('S3 预约 · 待确认单 #' + sample.id + ' 窗口重叠 ' + overlap + '/' + cap +
          ' → conflict=' + d.conflict + '（true 即现场确认会被 409 拦）')
      }
      expectList('S3 预约 · 时间轴', d.timeline, 'timeline', { nonEmpty: true })
    }
  }

  // ⑦ 订单页（三角色）
  for (const role of ['shipper', 'owner', 'port']) {
    const s = await walk('06 我的订单 · ' + role, 'pages/trade/orders/orders',
      'fetchAll', { role }, ['onLoad', 'onShow'])
    if (s) {
      const d = s._final()
      // onShow 内已 fetchAll 一次，这里再跑一次以确保终态
      expectList('06 · 订单列表(' + role + ')', d.list, 'list', { nonEmpty: role === 'shipper' })
      if (role === 'shipper') {
        const total = (d.list || []).length
        if (total === 0) fail('06 · 货主订单为空')
        else ok()
        // 合同弹层（长按预览）：severity/detail 只在这条路径上产生
        if ((d.rawList || []).length) {
          const pick = (d.rawList || []).find((o) => o.status === 'matched' && contracts[o.id]) || d.rawList[0]
          const oid = pick.id
          try { s.onContract({ currentTarget: { dataset: { id: oid } } }) } catch (e) {
            fail('06 · 合同弹层抛异常', e.message)
          }
          await tick(90)
          const d2 = s._final()
          const risks = (d2.contract && d2.contract.risks) || []
          if (!d2.contract || d2.contract.show !== true) fail('06 · 合同弹层未打开')
          else ok()
          if (!Array.isArray(risks)) fail('06 · 合同弹层 risks 结构异常')
          else ok()
          collect('pages/trade/orders/orders', path.join(ROOT, 'miniapp/pages/trade/orders/orders.js'), d2)
          note('06 · 合同弹层（订单 #' + oid + '）风险条目 ' + risks.length + ' 项')
        }
      }
    }
  }

  // ⑧ 撮合页（货主找船 / 船东找货）
  for (const [mode, refId] of [['cargo', refCargoId], ['ship', refShipId]]) {
    const s = await walk('05 撮合页 · ' + (mode === 'cargo' ? '货主找船' : '船东找货'),
      'pages/trade/match/match', null,
      { role: mode === 'cargo' ? 'shipper' : 'owner', arg: { mode, refId } }, ['onLoad'])
    if (s) {
      const d = s._final()
      const expect = mode === 'cargo' ? (D.matchCargo.items || []).length : (D.matchShip.items || []).length
      if ((d.items || []).length !== expect) fail('05 撮合 · 候选数与载荷不符', 'items=' + (d.items || []).length + ' 期望=' + expect)
      else ok()
      if (d.total !== expect) fail('05 撮合 · total 与候选数不符', 'total=' + d.total + ' 期望=' + expect)
      else ok()
      let prev = Infinity, desc = true
      for (const it of d.items || []) { if (Number(it.score) > prev + 1e-6) desc = false; prev = Number(it.score) }
      if (!desc) fail('05 撮合 · 默认未按综合评分降序')
      else ok()
      for (const it of d.items || []) {
        const dims = it.dims || []
        if (dims.length !== 4) fail('05 撮合 · 评分维度不是 4 项', String(dims.length))
        else if (dims.some((x) => !(x.pct >= 0 && x.pct <= 100))) fail('05 撮合 · 进度条百分比越界')
        else ok()
      }
      expectList('05 撮合 · 未入局原因', d.filterStats, 'filterStats')
    }
  }

  // ⑨ 合同三级页：已完成(0 风险) / 已撮合(2 项高风险) / 已撤销(负例)
  for (const [oid, tag, allowErr] of D.contractCases) {
    const s = await walk('S1 合同三级页 · ' + tag, 'pages/trade/contract/contract', null,
      { role: 'shipper', arg: { order_id: oid }, allowError: allowErr }, ['onLoad'])
    if (!s) continue
    const d = s._final()
    if (allowErr) {
      if (String(d.error).indexOf('撤销') === -1) fail('S1 合同 · 已撤销订单的错误文案不符合服务端口径', d.error)
      else ok()
      continue
    }
    if (!d.contractHtml || String(d.contractHtml).indexOf('<') === -1) fail('S1 合同 · rich-text HTML 未生成')
    else ok()
    const want = (contracts[oid] && contracts[oid].risks) || []
    if ((d.risks || []).length !== want.length) fail('S1 合同 · 风险条目数与载荷不符', (d.risks || []).length + ' vs ' + want.length)
    else ok()
    if (d.riskCount !== (d.risks || []).length) fail('S1 合同 · riskCount 与 risks 长度不符')
    else ok()
    const sev = (d.risks || []).map((r) => r.severity)
    if (want.length && sev.every((x) => x === 'high')) note('S1 合同 · ' + tag + ' 风险全为 high，排序断言退化（已用 2 项验证非空）')
    const rank = { high: 0, medium: 1, low: 2 }
    let sorted = true
    for (let i = 1; i < sev.length; i++) {
      const a = rank[sev[i]] === undefined ? 9 : rank[sev[i]]
      const b = rank[sev[i - 1]] === undefined ? 9 : rank[sev[i - 1]]
      if (a < b) sorted = false
    }
    if (!sorted) fail('S1 合同 · 风险未按 high→medium→low 排序', sev.join(','))
    else ok()
    for (const r of d.risks || []) {
      if (!r.sev_label || !r.title) fail('S1 合同 · 风险条目缺 sev_label/title')
      else ok()
    }
  }

  // ⑩ 支付详情三级页（待支付 / 已支付 / 已退款 / 无支付单）
  for (const [oid, tag, expectCreate] of D.payCases) {
    const s = await walk('S2 支付详情 · ' + tag + ' #' + oid, 'pages/trade/payment/payment', null,
      { role: 'shipper', arg: { order_id: oid } }, ['onLoad'])
    if (s) {
      const d = s._final()
      if (payments[oid]) {
        expectList('S2 支付 · 时间轴(' + tag + ')', d.timeline, 'timeline', { nonEmpty: true })
        const locked = Number(payments[oid].amount)
        if (String(d.amountText) !== locked.toFixed(2)) {
          fail('S2 支付 · 金额未取支付单锁定值', d.amountText + ' vs ' + locked.toFixed(2))
        } else ok()
        if (d.amountPrefix !== '¥') fail('S2 支付 · 金额前缀不是 ¥', String(d.amountPrefix))
        else ok()
      } else {
        const want = expectCreate === undefined || expectCreate === null ? true : expectCreate
        if (d.canCreate !== want) {
          fail(`S2 支付 · 无支付单（${tag}）发起入口 canCreate 应为 ${want}`, String(d.canCreate))
        } else ok()
      }
    }
  }

  // ⑪ 发布空船
  {
    const s = await walk('05 发布空船', 'pages/publish/ship/ship', 'fetchShips', { role: 'owner' }, ['onLoad'])
    if (s) {
      const d = s._final()
      expectList('05 发布空船 · 可选船舶', d.ships, 'ships', { nonEmpty: false })
      if ((d.ships || []).length === 0) note('05 发布空船 · 无已认证船舶可选（seed-owner 应有 2 艘 verified）')
    }
  }

  // ⑫ 发布货物 / 我的
  await walk('04 发布货物', 'pages/publish/cargo/cargo', null, { role: 'shipper' }, ['onLoad'])
  await walk('08 我的', 'pages/mine/mine', null, { role: 'shipper' }, ['onLoad', 'onShow'])

  // ══════════════════ 委托支线（ENT-022）：两页真载荷走查 ══════════════════
  //
  // 为什么值得单独一段：委托支线此前**没有**任何真载荷走查 —— 前端投影只被
  // 「同形 fixture + 静态交叉断言」覆盖，后端把 `state` 语义改掉时静态断言不会红，
  // 界面会静默显示「不适用」「无未决问题」这类**错误的业务结论**。
  // 这一段把载荷换成真接口返回的，专门盯「后端说什么 ↔ 界面显示什么」。
  const E = require(path.join(ROOT, 'miniapp', 'utils', 'entrust.js'))

  // ⑬ 委托队列（经理工作台 · 组织视角）
  //
  // 本页取数链是「先定位组织，再取队列」。两步都要断言：组织清单的失败**不能**
  // 被当成"没有组织"（那是把网络故障说成"还没加入经营主体"这种业务事实）。
  {
    const eOrg = (D.entrustOrgs.items || [])[0]
    const s = await walk('09 委托队列 · 经理工作台', 'pages/entrust/workbench/workbench',
      null, { role: 'owner' }, ['onLoad'])
    if (s && !eOrg) {
      note('09 委托队列 · seed-owner 无组织身份，队列断言跳过（需先铺 seed_entrust_demo.py）')
    } else if (s) {
      const d = s._final()
      // 只有一个组织 → 必须走 `only`：不该让用户为唯一选项做一次选择
      if (String(d.activeOrgId) !== String(eOrg.org_id)) {
        fail('09 队列 · 组织未定位到唯一组织', 'activeOrgId=' + d.activeOrgId + ' 期望=' + eOrg.org_id)
      } else ok()
      if (d.orgReason !== 'only') fail('09 队列 · 单组织未走 only 分支', String(d.orgReason))
      else ok()
      expectList('09 队列 · 委托列表', d.items, 'items', { nonEmpty: true })
      if (d.total !== D.entrustQueue.total) {
        fail('09 队列 · total 与载荷不符', d.total + ' vs ' + D.entrustQueue.total)
      } else ok()
      const wantIds = (D.entrustQueue.items || []).map((x) => String(x.assignment_id))
      const gotIds = (d.items || []).map((x) => String(x.assignmentId))
      if (gotIds.join(',') !== wantIds.join(',')) {
        fail('09 队列 · 条目与载荷不一致', gotIds.join(',') + ' vs ' + wantIds.join(','))
      } else ok()

      // 筛选：只断言"数组换了"没有意义（换错了也一样通过），要与该筛选条件下的
      // **真载荷**对照 —— 这正是筛选最容易错的地方。
      for (const st of ['claimed', 'submitted']) {
        const want = entrustQueueByStatus[st]
        if (!want) continue
        let thrown = null
        try { s.onFilter({ currentTarget: { dataset: { key: st } } }) } catch (e) { thrown = e }
        await tick(90)
        if (thrown) { fail('09 队列 · 筛选 ' + st + ' 抛异常', thrown.message); continue }
        const d2 = s._final()
        const got = (d2.items || []).map((x) => String(x.assignmentId)).sort().join(',')
        const exp = (want.items || []).map((x) => String(x.assignment_id)).sort().join(',')
        if (got !== exp) fail('09 队列 · 筛选 ' + st + ' 条目与载荷不符', got + ' vs ' + exp)
        else if (d2.total !== want.total) fail('09 队列 · 筛选 ' + st + ' total 不符', d2.total + ' vs ' + want.total)
        else ok()
        collect('pages/entrust/workbench/workbench',
          path.join(ROOT, 'miniapp/pages/entrust/workbench/workbench.js'), d2)
      }
    }
  }

  // ⑭ 委托详情（单张委托工作台 · 七槽位）
  //
  // 三层断言，越往下越能抓到"看错地方"的错：
  //   ① 结构：七个槽位都在、顺序与前端配置表一致；
  //   ② 逐槽与**后端返回**对照：四态是后端定的，界面只是译成文案 —— 译错就是
  //      「该去补数据」被说成「这一槽本来就没有内容」；
  //   ③ 语义：未开放槽不得落进四态；历史成果计数如实。
  for (const [aid, tag, wantClaim] of D.entrustCases) {
    const s = await walk('09 委托详情 · ' + tag, 'pages/entrust/detail/detail', null,
      { role: 'shipper', arg: { assignment_id: String(aid) } }, ['onLoad'])
    if (!s) continue
    const d = s._final()
    const wb = entrustWorkbench[aid]
    const slots = d.slots || []

    if (String(d.assignmentId) !== String(aid)) {
      fail('09 详情 #' + aid + ' · 页面持有的委托编号不对', String(d.assignmentId))
    } else ok()
    if (slots.length !== E.WORKBENCH_SLOTS.length) {
      fail('09 详情 #' + aid + ' · 槽位数与配置表不符',
        slots.length + ' vs ' + E.WORKBENCH_SLOTS.length)
      continue
    }
    const gotKeys = slots.map((x) => x.key).join(',')
    const wantKeys = E.WORKBENCH_SLOTS.map((x) => x.key).join(',')
    if (gotKeys !== wantKeys) fail('09 详情 #' + aid + ' · 槽位顺序与配置表不符', gotKeys)
    else ok()

    if (!wb) { note('09 详情 #' + aid + ' · 无工作台载荷，逐槽核对跳过'); continue }

    const raw = {}
    ;(wb.slots || []).forEach((x) => { raw[x.key] = x })
    let opened = 0
    let closedKeys = []
    for (const view of slots) {
      const back = raw[view.key]
      const pre = '09 详情 #' + aid + ' · ' + view.key + ' '
      if (!back) { fail(pre + '在载荷里不存在'); continue }

      // ① 未开放：走独立通道，**不得**套用四态（否则"能力没做"被说成"这单没有"）
      if (back.available === false) {
        closedKeys.push(view.key)
        if (view.available !== false) fail(pre + '后端标未开放，前端按开放渲染')
        else if ((view.fields || []).length) fail(pre + '未开放却给了字段行', String(view.fields.length))
        else if (!view.note) fail(pre + '未开放却没给理由')
        else if (view.tag !== E.SLOT_EMPTY_TEXT.notOpen) fail(pre + '未开放标签不对', String(view.tag))
        else ok()
        continue
      }
      opened += 1

      // ② 四个派生字段一个都不能少（少一个就是"这一栏整块不见了"）
      if ((view.fields || []).length !== 4) {
        fail(pre + '派生字段不是 4 行', String((view.fields || []).length))
        continue
      }
      const labels = view.fields.map((f) => f.label).join('/')
      if (labels !== E.SLOT_FIELD_LABELS.join('/')) fail(pre + '字段标签或顺序不对', labels)
      else ok()

      const cur = back.current || {}
      const iss = back.issues || {}
      const own = back.next_owner || {}
      const f0 = view.fields[0]
      const f1 = view.fields[1]
      const f2 = view.fields[2]

      // 「当前成果」：present ⟺ 有内容。空态必须是「暂无记录」，不能是别的四态文案
      if (cur.state === 'present') {
        if (f0.empty) fail(pre + '后端 present，界面却显示空', String(f0.value))
        else if (f0.value !== (cur.text || '')) fail(pre + '当前成果文案与载荷不符', f0.value + ' vs ' + cur.text)
        else ok()
      } else if (!f0.empty || f0.value !== E.SLOT_EMPTY_TEXT.noRecord) {
        fail(pre + '当前成果应为「' + E.SLOT_EMPTY_TEXT.noRecord + '」', f0.value)
      } else ok()

      // 「未决问题」：信息缺失是**有内容的**状态，不是空
      if (iss.state === 'missing_info') {
        if (f1.empty || String(f1.value).indexOf(E.SLOT_EMPTY_TEXT.missingInfo) === -1) {
          fail(pre + '信息缺失未如实显示', f1.value)
        } else ok()
      } else if (iss.state === 'present') {
        if (f1.empty || String(f1.value).indexOf('未决') === -1) fail(pre + '未决问题未如实显示', f1.value)
        else ok()
      } else if (f1.empty !== true) {
        fail(pre + '无未决问题时应为空态', String(f1.value))
      } else ok()
      if (f1.label !== E.SLOT_FIELD_LABELS[1]) fail(pre + '未决问题字段标签不对', f1.label)
      else ok()

      // 「下一责任方」：assigned / unassigned / not_applicable 三句必须分开
      if (own.state === 'assigned') {
        if (f2.empty || !f2.value) fail(pre + '已指派却显示空态', String(f2.value))
        else ok()
      } else if (own.state === 'unassigned') {
        if (f2.value !== E.SLOT_EMPTY_TEXT.unassigned) fail(pre + '应显示「尚未分配」', String(f2.value))
        else ok()
      } else if (f2.value !== E.SLOT_EMPTY_TEXT.notApplicable) {
        fail(pre + '应显示「不适用」', String(f2.value))
      } else ok()

      // 计数摘要：只写有内容的项，且数字要与载荷一致（写错数字比不写更糟）
      const counts = back.counts || {}
      const ct = String(view.countsText || '')
      if (counts.tasks && ct.indexOf('任务 ' + counts.tasks) === -1) {
        fail(pre + '计数未含任务数', ct + ' vs tasks=' + counts.tasks)
      } else if (counts.artifacts && ct.indexOf('成果 ' + counts.artifacts) === -1) {
        fail(pre + '计数未含成果数', ct + ' vs artifacts=' + counts.artifacts)
      } else ok()
    }

    // 反向风险：七个槽位**全部**走未开放分支同样会通过上面每一条 ——
    // 那种"全空且理由齐全"的界面其实什么都没显示。所以要有正向计数。
    if (opened === 0) fail('09 详情 #' + aid + ' · 七个槽位全部未开放，页面等于什么都没显示')
    else ok()
    if (closedKeys.length && closedKeys.join(',') !== 'exceptions') {
      fail('09 详情 #' + aid + ' · 未开放槽位不止 exceptions', closedKeys.join(','))
    } else ok()

    // ③ 历史成果（归属机制上线前的存量）必须如实报数
    const wantUnassigned = Number(wb.unassigned_artifact_total || 0)
    const hint = String(d.unassignedHint || '')
    if (wantUnassigned) {
      if (hint.indexOf(String(wantUnassigned)) === -1) {
        fail('09 详情 #' + aid + ' · 历史成果未如实报数', 'hint=' + hint + ' 期望含 ' + wantUnassigned)
      } else ok()
    } else if (hint) {
      fail('09 详情 #' + aid + ' · 无历史成果却给了提示', hint)
    } else ok()

    // 受理入口只看**委托状态**（有没有权限由服务端判定，前端不猜）
    if (!!d.canClaim !== !!wantClaim) {
      fail('09 详情 #' + aid + ' · 受理入口与委托状态不符',
        'canClaim=' + d.canClaim + ' 状态=' + wb.status)
    } else ok()
  }

  // 没有委托载荷时**显式失败**而不是静默：本 job 的 CI 定义里明确串了
  // `seed_entrust_demo.py`，跑不到数据只有两种可能 —— 种子失效，或开关没打开。
  // 两种情况都会让委托两页退化成"看 404 分支"（全绿且什么都没验），
  // 静默通过比失败危险得多。
  if (!D.entrustCases.length) {
    fail('09 委托 · 无委托载荷（seed_entrust_demo.py 未生效 / ENTRUST_ENABLED 未开）',
      '组织 ' + (D.entrustOrgs.items || []).length + ' 个 · 我的委托 0 张')
  }

  auditTemplates()

  console.log('\n' + '='.repeat(78))
  console.log('OK ' + N_OK + ' · FAIL ' + FAILS.length + ' · note ' + NOTES.length)
  console.log('='.repeat(78))
  if (FAILS.length) { console.log('\n需处理：'); FAILS.forEach((f) => console.log('  - ' + f)) }
  if (NOTES.length) { console.log('\n备注：'); NOTES.forEach((n) => console.log('  - ' + n)) }
  process.exit(FAILS.length ? 1 : 0)
})()
