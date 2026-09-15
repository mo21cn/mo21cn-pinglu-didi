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

// 成果的编辑与确认（ENT-023）。这几份载荷都来自**真接口**，且 ⑮ 段会**真的写库** ——
// 「编辑不改变生效版本」「确认绑定精确版本」这两条语义必须由后端事实证明，
// 只靠前端自己的返回值构造是自证。
let entrustArtifactTypes = { items: [] }
let entrustArtifact = null
let entrustArtifactRevisions = { items: [] }
const entrustAssignmentArtifacts = {}   // assignmentId → 单委托成果清单
const entrustTasksByAssignment = {}     // assignmentId → 单委托任务清单（受影响项候选）
// ⑯ 段案件详情走查用的真载荷。**按 case_id 存一份**，不是只留最后一个：
// 队列里的两宗刻意覆盖"有受影响项"与"无受影响项"两种形状，⑯ 段要逐宗都走 ——
// 只留一宗会让另一宗拿到"载荷对不上"的桩错误，于是"页面缺陷"与"脚本没准备数据"
// 混在一起（本轮就踩到：案件 #1 被判成 error 态）。
const entrustCaseDetails = {}
const entrustWriteProof = {}            // ⑮ 段真写前后的读数对比（后端事实）

// ── 页面驱动的写通道 ──────────────────────────────────────────────────────
// 写请求**只在 ⑮ 段的显式用户动作里放行**（点"保存新版本"、点"设为生效版本"）：
// `load()` 期间任何写请求都被拒 —— 与 route() 里"写端点有意不登记"是同一条纪律。
// 但写动作必须真的发到后端，否则页面拿不到真响应，断言又退化成自证。
let WRITE_ENABLED = false
let ownerToken = ''                     // seed-owner（演示组织经理）：⑮ 段的真写与写后复读
const pageWrites = []                   // 真实发出的写请求，供"该不该写"的断言使用
let lastWx = null                       // 最近一次页面装载用的 wx 桩（读取弹层文案）

async function pageWrite(method, p, body, key) {
  if (!WRITE_ENABLED) {
    return { rejected: true, reason: '取数阶段不得发写请求：' + method + ' ' + p }
  }
  const res = await api(method, p, {
    token: ownerToken,
    body: body,
    headers: { 'Idempotency-Key': key }
  })
  // 连**响应**一起记下来：⑮ 段要拿后端返回的 `revision_no` 做断言，
  // 而不是从页面的 setData 里反推（那又变成自证）。
  pageWrites.push({ method: method, path: p, body: body, status: res.status, data: res.data })
  return res
}

/**
 * 写通道的响应 → 页面期待的 Promise 语义：非 200 抛**带 `httpStatus`** 的错误
 * （与 utils/request.js 的拒绝形状一致），页面据此走"服务端已给出原因、
 * 自己不再弹 toast"那条分支；错误形状不对会把正确的页面行为判成缺陷。
 */
function rejectIfNotOk(res) {
  if (res && res.rejected) throw new Error(res.reason)
  if (res.status !== 200) {
    const err = new Error('写请求被拒 ' + res.status)
    err.httpStatus = res.status
    err.detail = res.data && res.data.detail
    throw err
  }
  return res.data
}

/**
 * 写之后把回放快照换成**真事实**。
 *
 * route() 回放的是 bootstrap 那一份静态快照 —— 页面自己写完之后再 `load()`，
 * 拿到的是**写之前**的库。若不复读，后面"生效版本变没变"的断言会假绿
 * （页面显示的是旧快照，我却拿它当"页面反映了后端"）。
 */
async function refreshArtifactReplay(artifactId) {
  const a = await api('GET', '/entrust/artifacts/' + artifactId, { token: ownerToken })
  if (a.status === 200) entrustArtifact = a.data
  const r = await api('GET', '/entrust/artifacts/' + artifactId + '/revisions', { token: ownerToken })
  if (r.status === 200) entrustArtifactRevisions = r.data
}

const qs = (p) => (p ? '?' + new URLSearchParams(p).toString() : '')

async function api(method, p, { token, body, headers } = {}) {
  const res = await fetch(BASE + '/api/v1' + p, {
    method,
    headers: Object.assign(
      { 'Content-Type': 'application/json' },
      token ? { Authorization: 'Bearer ' + token } : {},
      // 委托支线的写端点要求 `Idempotency-Key`（缺了直接 400），
      // 故 api() 需要能把额外头传进来 —— 只靠 token/body 两个参数写不出真写用例。
      headers || {}
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
  // 组织经理 token 在这里就交到模块级（原来只在"挑中成果"时才赋）：
  // 后面凡是要以经理身份**读**组织侧载荷的段（案件候选、案件详情）都要用它，
  // 与"有没有挑中成果"无关 —— 挂在挑中分支里会让不挑成果的那次运行拿不到 token，
  // 表现为相关的段整段 401，看起来像"接口坏了"。
  ownerToken = tok.owner

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

  // ⑬b 异常 / 变更队列（UI-04 / ENT-030 切片四之六）：与委托队列**同口径** ——
  // 组织级真载荷，页面不拿脚本编的数据渲染。演示种子目前不造案件，所以这两条载荷
  // 多半是 `total:0`；正因为它是真载荷，「空」才是可以断言的业务事实，
  // 而不是「脚本忘了给数据」。（有案件的走查在真机走查脚本里做，那里会真登记一宗。）
  const caseQueueParams = entrustOrgId
    ? { view: 'org', org_id: entrustOrgId, size: 50 }
    : null
  if (caseQueueParams) {
    D.entrustCaseQueue = await get('/entrust/exceptions', 'owner',
      Object.assign({ scope: 'unclosed' }, caseQueueParams))
    // 范围筛选的对照载荷：与上面状态筛选同理 —— 只证明"数组换了"是没意义的。
    D.entrustCaseQueueAll = await get('/entrust/exceptions', 'owner',
      Object.assign({ scope: 'all' }, caseQueueParams))
  } else {
    D.entrustCaseQueue = { total: 0, page: 1, size: 50, org_id: 0, items: [] }
    D.entrustCaseQueueAll = { total: 0, page: 1, size: 50, org_id: 0, items: [] }
  }
  // 案件**详情**（UI-08 + 处置区）。队列里每一宗都取一次 —— 不是"随便挑一个编号"，
  // 而是**队列自己报出来的**那些，于是"队列能列出来"与"详情能打开"用的是同一条事实。
  // 队列为空时一张都不取：⑯ 段据此把详情走查整段标成 note，而不是判失败
  // （"库里没有案件"是数据状态，不是页面缺陷）。
  for (const row of D.entrustCaseQueue.items || []) {
    const c = await api('GET', '/entrust/exceptions/' + row.case_id, { token: tok.owner })
    if (c.status === 200) entrustCaseDetails[String(row.case_id)] = c.data
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

  // ── 成果页载荷（ENT-023）：**只读**取一次 ───────────────────────────────
  //
  // 这里刻意不写库。写动作改由 ⑮ 段**从页面**驱动（点"保存新版本"、点"设为生效版本"），
  // 走的是真实用户路径：页面自己组 payload、自己带幂等键、自己读返回值。
  //
  // 反过来，若在 bootstrap 里先写一遍再让 ⑮ 段比对，就会退化 ——
  // 例如先确认 v4、页面再保存一次，页面看到的生效版本已经是 v4，
  // 于是"编辑不改变生效版本"被断言成「已保存为 v4，生效版本仍是 v4」：
  // 看起来通过，其实什么都没证明。
  await (async function loadArtifactPayload() {
    const firstAid = (D.entrustMine.items || [])[0] && D.entrustMine.items[0].assignment_id
    if (!firstAid) {
      note('成果走查 · 无委托载荷，跳过（先确认 seed_entrust_demo.py 已生效）')
      return
    }
    try {
      entrustArtifactTypes = await get('/entrust/artifact-types', 'owner')
      // 类型注册表：type code → spec（含 `field_types`）
      const typeSpecs = {}
      ;(entrustArtifactTypes.items || []).forEach(function (s) { typeSpecs[s.code] = s })

      // 挑**确实有成果**的那张委托。不能用 `items[0]`：组织队列里"待受理"的委托单
      // 天然没有成果，选到它会让整段走查静默跳过 —— 本轮就这样空跑过一次
      // （OK 259 / FAIL 0 全绿，但成果页一个字都没验）。
      //
      // 再往上一步（ENT-025）：**优先挑带「缺值列表字段」的成果**。那正是本片修的
      // 缺口现场 —— 类型声明为 `list`、值还不存在（键都没有）。只有挑到它，
      // 才可能在**真载荷**上证明"缺值也让列表字段走 JSON 编辑形态"，
      // 而不是只在静态 fixture 上证明。评分：2 = 有缺值列表字段；1 = 有列表字段；
      // 0 = 其它成果（仍会被选中，保证走查不会因为没挑到而整段跳过）。
      let artId = 0
      let pickedAid = null
      let best = -1
      for (const a of D.entrustMine.items || []) {
        const list = await get('/entrust/assignments/' + a.assignment_id + '/artifacts', 'owner', { size: 50 })
        entrustAssignmentArtifacts[a.assignment_id] = list
        // 同一张委托的任务清单一起拉：登记案件时"选受影响项"要它。
        // 放在打分循环**之前** —— 一旦挑中（`best === 2`）就会 break，
        // 那时再拉就来不及了（被挑中的那张委托恰恰是最需要它的）。
        entrustTasksByAssignment[a.assignment_id] = await get(
          '/entrust/tasks', 'owner', { assignment_id: a.assignment_id, size: 50 }
        )
        for (const it of list.items || []) {
          const spec = typeSpecs[it.artifact_type] || {}
          const ft = spec.field_types || {}
          const listFields = Object.keys(ft).filter(function (n) { return ft[n] === 'list' })
          let score = 0
          let emptyOnes = []
          if (listFields.length) {
            score = 1
            const detail = await get('/entrust/artifacts/' + it.artifact_id, 'owner')
            const payload = (detail.current_revision || {}).payload || {}
            emptyOnes = listFields.filter(function (n) {
              const v = payload[n]
              return v === undefined || v === null || (Array.isArray(v) && v.length === 0)
            })
            if (emptyOnes.length) score = 2
          }
          if (score > best) {
            best = score
            artId = it.artifact_id
            pickedAid = a.assignment_id
            entrustWriteProof.listFields = listFields
            entrustWriteProof.emptyListFields = emptyOnes
          }
          if (best === 2) break
        }
        if (best === 2) break
      }
      entrustWriteProof.artifactPickScore = best
      if (!artId) {
        note('成果走查 · 我的委托下都没有成果，跳过编辑/确认走查')
        return
      }
      entrustWriteProof.artifactId = artId
      entrustWriteProof.assignmentId = pickedAid
      entrustArtifact = await get('/entrust/artifacts/' + artId, 'owner')
      entrustArtifactRevisions = await get('/entrust/artifacts/' + artId + '/revisions', 'owner')
      entrustWriteProof.beforeCurrentNo = (entrustArtifact.current_revision || {}).revision_no
      entrustWriteProof.beforeRevisionCount = (entrustArtifactRevisions.items || []).length
      // 写端点要求组织侧 `entrust:quote:create`；把 seed-owner 放到经理位置上的是
      // seed_entrust_demo.py（与队列页用的是同一个身份，不新增登录约定）。
      ownerToken = tok.owner
    } catch (e) {
      note('成果走查 · 取数失败，跳过：' + (e && e.message))
    }
  })()

  console.log('载荷就绪：货 %d · 船 %d · 泊位 %d · 预约 %d · 订单 %d · 支付单 %d · 合同 %d',
    (D.cargoList.items || []).length, (D.ships.items || []).length, (D.berths.items || []).length,
    (D.appts.items || []).length, (D.orders.items || []).length,
    Object.keys(payments).length, Object.keys(contracts).length)
  console.log('委托载荷：我的委托 %d · 组织队列 %d · 组织 %d 个 · 工作台 %d 张',
    (D.entrustMine.items || []).length, (D.entrustQueue.items || []).length,
    (D.entrustOrgs.items || []).length, Object.keys(entrustWorkbench).length)
  if (entrustArtifact) {
    console.log('成果载荷：注册表 %d 类 · 成果 #%s（%s，生效 v%s）· 版本 %d 条',
      (entrustArtifactTypes.items || []).length, String(entrustArtifact.artifact_id),
      String(entrustArtifact.artifact_type), String(entrustWriteProof.beforeCurrentNo),
      (entrustArtifactRevisions.items || []).length)
  }
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
  if ((m = u.match(/^\/entrust\/assignments\/(\d+)\/artifacts$/))) {
    const l = entrustAssignmentArtifacts[Number(m[1])]
    return l ? { ok: l } : { err: '未拉取委托 ' + m[1] + ' 的成果清单' }
  }
  if ((m = u.match(/^\/entrust\/assignments\/(\d+)$/))) {
    const d = entrustDetail[Number(m[1])]
    return d ? { ok: d } : { err: '委托不存在：' + m[1] }
  }
  if (u === '/entrust/my-orgs') return { ok: D.entrustOrgs }
  // 单委托任务清单（登记案件 / 处置里选受影响项用）。**精确匹配**：它是
  // `GET /entrust/tasks?assignment_id=`，若落到别的分支，页面会把一份委托载荷
  // 当成任务列出来（形状不对，表现为"受影响项里全是委托标题"这种静默错值）。
  if (u === '/entrust/tasks') {
    const q = body || {}
    const rows = entrustTasksByAssignment[Number(q.assignment_id)]
    return rows ? { ok: rows } : { err: '未拉取委托 ' + q.assignment_id + ' 的任务清单' }
  }
  // 组织级案件清单（UI-04）。**精确匹配** `/entrust/exceptions`：它没有子路径，
  // 但也绝不能落到下面的 `/entrust/assignments` 分支里 —— 那会让队列页拿到一份
  // 委托载荷渲染案件行（形状不对，表现为"案件标题是委托标题"这种静默错值）。
  if (u === '/entrust/exceptions') {
    const q = body || {}
    if ((q.view || '') === 'org') {
      return q.scope === 'all' ? { ok: D.entrustCaseQueueAll } : { ok: D.entrustCaseQueue }
    }
    return { err: '本脚本未拉取单委托视图的案件载荷（缺 assignment_id 视图）' }
  }
  // 案件**详情**（UI-08 + 处置区）。按编号回放 bootstrap 那一份真载荷。
  // 编号必须对得上：页面若问了别的案件，回放另一份会让它"看起来正常"，
  // 而处置区显示的 `revision_no` / 受影响项就全错了 —— 那才是最坏的假绿。
  if ((m = u.match(/^\/entrust\/exceptions\/(\d+)$/))) {
    const c = entrustCaseDetails[m[1]]
    return c ? { ok: c } : { err: '未拉取案件 ' + m[1] + ' 的详情' }
  }
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
  // 成果详情 / 版本历史 / 注册表（ENT-023）。三者都是**精确匹配**，且
  // `/artifacts/{id}/revisions` 必须排在裸 `/artifacts/{id}` 之前。
  // 回放的是 bootstrap 那一份只读快照；⑮ 段写完库后用 refreshArtifactReplay()
  // 把它换成真事实，再让页面重载 —— 否则页面看到的是写之前的库。
  if ((m = u.match(/^\/entrust\/artifacts\/(\d+)\/revisions$/))) {
    if (!entrustArtifact) return { err: '成果载荷未就绪：' + m[1] }
    return { ok: entrustArtifactRevisions }
  }
  if ((m = u.match(/^\/entrust\/artifacts\/(\d+)$/))) {
    if (!entrustArtifact) return { err: '成果载荷未就绪：' + m[1] }
    // 编号对不上就直接失败：页面若问了别的成果，回放同一份快照会让它"看起来正常"
    if (String(entrustArtifact.artifact_id) !== m[1]) {
      return { err: '页面请求成果 ' + m[1] + '，但载荷是 ' + entrustArtifact.artifact_id }
    }
    return { ok: entrustArtifact }
  }
  if (u === '/entrust/artifact-types') return { ok: entrustArtifactTypes }
  // 写端点（`POST /artifacts/{id}/revisions` 与 `/confirm`）**依然不在这里登记**：
  // 它们由 requireStub 里的写通道接管（带"取数阶段不得写"的闸门），
  // 任何绕过该通道的写请求都会落到下面这行兜底里显式失败。
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
        // 组织级案件清单（UI-04）。查询参数**不在这里重拼**：直接用真实模块的
        // `caseOrgListQuery`，否则这个桩会变成第二份实现 —— 它拼错了，
        // 走查照样绿，因为两边都是它自己。
        fetchCaseOrgList: (opts) => fetchVia('/entrust/exceptions', real.caseOrgListQuery(opts)),
        fetchCase: (id) => fetchVia('/entrust/exceptions/' + id),
        // 受影响项候选（登记案件 / 处置时选目标）。两个端点都是**既有接口**，
        // 本脚本只把它们接到 route() 的回放载荷上 —— 不在桩里另写一份筛选逻辑。
        fetchTaskCandidates: (id, size) =>
          fetchVia('/entrust/tasks', { assignment_id: id, page: 1, size: size || 50 }),
        fetchArtifactCandidates: (id, size) =>
          fetchVia('/entrust/assignments/' + id + '/artifacts', { page: 1, size: size || 50 }),
        // 成果页（ENT-023）三条取数：与其它页同口径 —— 载荷来自 bootstrap 的真接口，
        // 只是经 route() 回放（写之后由 refreshArtifactReplay() 换成真事实）。
        fetchArtifactTypes: () => fetchVia('/entrust/artifact-types'),
        fetchArtifact: (id) => fetchVia('/entrust/artifacts/' + id),
        fetchRevisions: (id) => fetchVia('/entrust/artifacts/' + id + '/revisions'),
        // 写端点：**只放行显式用户动作**（见 WRITE_ENABLED）。走真网络，
        // 失败时抛带 `httpStatus` 的错误 —— 与 utils/request.js 的拒绝形状一致，
        // 页面据此走"服务端已给出原因、不重复弹 toast"那条分支。
        appendRevision: (id, body, key) =>
          pageWrite('POST', '/entrust/artifacts/' + id + '/revisions', body, key).then(rejectIfNotOk),
        confirmArtifact: (id, no, key) =>
          pageWrite('POST', '/entrust/artifacts/' + id + '/confirm', { revision_no: no }, key)
            .then(rejectIfNotOk),
        createTask: (id, body, key) =>
          pageWrite('POST', '/entrust/assignments/' + id + '/tasks', body, key).then(rejectIfNotOk),
        claimAssignment: (id, key) =>
          pageWrite('POST', '/entrust/assignments/' + id + '/claim', {}, key).then(rejectIfNotOk),
        // 案件六个写命令（切片四之六）。与上面三条同一条通道：**只在 WRITE_ENABLED
        // 的段里**才真的发出去，取数阶段一律被拒 —— 登记案件会改库（新案件会进
        // 组织队列），在"读"的段里发生它会让后续断言拿到一个被自己污染的世界。
        createCase: (id, body, key) =>
          pageWrite('POST', '/entrust/assignments/' + id + '/exceptions', body, key)
            .then(rejectIfNotOk),
        addCaseLink: (id, body, key) =>
          pageWrite('POST', '/entrust/exceptions/' + id + '/links', body, key).then(rejectIfNotOk),
        removeCaseLink: (id, linkId, rev, key) =>
          pageWrite(
            'DELETE',
            '/entrust/exceptions/' + id + '/links/' + linkId + '?expected_revision=' + rev,
            {},
            key
          ).then(rejectIfNotOk),
        decideCase: (id, body, key) =>
          pageWrite('POST', '/entrust/exceptions/' + id + '/decision', body, key).then(rejectIfNotOk),
        closeCase: (id, body, key) =>
          pageWrite('POST', '/entrust/exceptions/' + id + '/close', body, key).then(rejectIfNotOk),
        reopenCase: (id, body, key) =>
          pageWrite('POST', '/entrust/exceptions/' + id + '/reopen', body, key).then(rejectIfNotOk)
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
    _modals: [],
    setNavigationBarTitle() {}, showToast() {}, showLoading() {},
    // showModal 只**记录**、不自动点确认：确认卡上的「成果 #N · vK」就是在这里被
    // 断言的（PRD 第 187 行），而真正的提交由 ⑮ 段显式驱动 submitConfirm()。
    // 让桩自动确认会把"文案对不对"与"写没写"两件事缠在一起，失败时分不清是哪一侧。
    showModal(o) { wxStub._modals.push(o || {}) },
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
  lastWx = wxStub
  return cfg
}

/**
 * 按小程序的**路径键**语义写值（`formFields[0].text` / `a.b`）。
 *
 * 为什么必须有：成果编辑页用 `setData({'formFields[idx].text': v})` 往数组元素里写值 ——
 * 真机上这是 setData 的标准用法。桩若不认路径键，会把它当成一个**字面键**记下来，
 * `this.data.formFields[i].text` 读到的还是旧值，页面就被误判成"输入没生效"。
 * 桩不真实会把**正确的**代码判成缺陷，比漏测更难查。
 *
 * @returns {boolean} true = 已按路径写入；false = 交给调用方按普通键处理
 */
function setByPath(target, key, value) {
  const m = String(key).match(/^([A-Za-z_$][\w$]*)((?:\[\d+\]|\.[A-Za-z_$][\w$]*)+)$/)
  if (!m) return false
  const parts = m[2].match(/\[\d+\]|\.[A-Za-z_$][\w$]*/g) || []
  let cur = target[m[1]]
  if (cur === null || typeof cur !== 'object') return false
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i]
    const k = p.charAt(0) === '[' ? Number(p.slice(1, -1)) : p.slice(1)
    if (i === parts.length - 1) { cur[k] = value; return true }
    const nxt = cur[k]
    if (nxt === null || typeof nxt !== 'object') return false
    cur = nxt
  }
  return false
}

function instantiate(cfg, ctx) {
  const self = Object.assign({}, cfg)
  self.data = Object.assign(JSON.parse(JSON.stringify(cfg.data || {})), ctx.data || {})
  self._set = null
  self.setData = function (patch, cb) {
    const plain = {}
    for (const k of Object.keys(patch || {})) {
      if (setByPath(self.data, k, patch[k])) continue
      plain[k] = patch[k]
      self.data[k] = patch[k] // 页面会回读 this.data，必须同步写入
    }
    self._set = Object.assign({}, self._set, plain)
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
    // 委托端点整组 500 的**首要**原因不是种子、而是缺表：`ent_` 前缀的表由
    // `backend/migrations/` 管理，`app/main.py` 里的 create_all 明确排除了它们（DR-0001）。
    // 少跑迁移时 /api/v1/entrust/* 会全部 500，而 500 在日志里读起来很像"委托没数据" ——
    // 2026-09-14 CI 上「前端端到端」job 就是这么红的，故把迁移写进提示。
    console.error('        ③ 迁移已应用（cd backend && python migrate.py）—— ent_ 表不在 create_all 范围内')
    console.error('        ④ 委托开关已开、委托种子已铺（ENTRUST_ENABLED=true；backend/scripts/seed_entrust_demo.py）')
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

      // ── ⑬b 异常与变更队列（UI-04 / 切片四之六）──
      //
      // 这一节钉三件在真载荷下才成立的事：
      //   ① 切队列真的换了**行形状**（`caseId` 而不是 `assignmentId`）；
      //   ② 案件队列的空态说的是案件队列的话（沿用"还没有委托"就说明两个队列共用了一句）；
      //   ③ 范围筛选切到「全部」后拿的是**另一份真载荷**（只断言"数组换了"没有意义）。
      let qThrown = null
      try {
        s.onSwitchQueue({ currentTarget: { dataset: { queue: 'case' } } })
      } catch (e) {
        qThrown = e
      }
      await tick(90)
      if (qThrown) {
        fail('09b 案件队列 · 切队列抛异常', qThrown.message)
      } else {
        const dq = s._final()
        const wantCase = D.entrustCaseQueue
        if (dq.queue !== 'case') fail('09b 案件队列 · 未切到案件队列', String(dq.queue))
        else ok()
        if (wantCase.total === 0) {
          if (dq.view !== 'empty') fail('09b 案件队列 · 空载荷未落到 empty 态', String(dq.view))
          else if (String(dq.viewTitle).indexOf('还没有委托') !== -1) {
            fail('09b 案件队列 · 空态沿用了委托队列的文案', String(dq.viewTitle))
          } else ok()
          if ((dq.items || []).length !== 0) fail('09b 案件队列 · 空载荷却有行', String((dq.items || []).length))
          else ok()
        } else {
          const gotCase = (dq.items || []).map((x) => String(x.caseId)).sort().join(',')
          const expCase = (wantCase.items || []).map((x) => String(x.case_id)).sort().join(',')
          if (dq.view !== 'ready') fail('09b 案件队列 · 有载荷却不是 ready', String(dq.view))
          else if (gotCase !== expCase) fail('09b 案件队列 · 案件条目与载荷不符', gotCase + ' vs ' + expCase)
          else if (dq.total !== wantCase.total) {
            fail('09b 案件队列 · total 与载荷不符', dq.total + ' vs ' + wantCase.total)
          } else ok()
        }

        let scThrown = null
        try {
          s.onCaseScope({ currentTarget: { dataset: { caseScope: 'all' } } })
        } catch (e) {
          scThrown = e
        }
        await tick(90)
        if (scThrown) fail('09b 案件队列 · 切换范围抛异常', scThrown.message)
        else {
          const dAll = s._final()
          if (dAll.activeCaseScope !== 'all') fail('09b 案件队列 · 范围未切到 all', String(dAll.activeCaseScope))
          else if (dAll.total !== D.entrustCaseQueueAll.total) {
            fail('09b 案件队列 · 范围 all 的 total 与载荷不符', dAll.total + ' vs ' + D.entrustCaseQueueAll.total)
          } else ok()
        }
        collect('pages/entrust/workbench/workbench',
          path.join(ROOT, 'miniapp/pages/entrust/workbench/workbench.js'), s._final())
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
    // `exceptions` 的「本期未开放」标记已于 2026-09-14 撤下（DR-0013 §7.3 五条满足），
    // 于是这条从"未开放槽位只能是 exceptions"翻成"**一个都不能有**"。
    // ⚠️ 不能只把它删掉或改成"不报错"：`closedKeys` 恒空时任何形状的断言都会绿，
    //    那就是空转。这里**显式**要求为空 —— 有人再收回某块能力，这条要红。
    if (closedKeys.length !== 0) {
      fail(
        '09 详情 #' + aid + ' · 出现未开放槽位（标记已撤下，多一个都说明能力被悄悄降级）',
        closedKeys.join(',')
      )
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

  // 缺值列表字段在真写里填的 JSON 数组文本。前后端断言共用同一份字符串，
  // 避免「前端存对了、断言比错了」这种自欺。
  const E2E_LIST_FILL = '["e2e 新增项"]'

  // ══════════════════ 成果的编辑与确认（ENT-023）：真写走查 ══════════════════
  //
  // 与前面各段有一处**性质差别**：这一段会真的写库。理由 ——
  // 「编辑不改变生效版本」「确认绑定精确版本」这两条语义若只在只读载荷上核对，
  // 核对的是我构造的数据。这里让**页面**把改动提交给后端（点"保存新版本"、
  // 点"设为生效版本"），再用后端事实判定：两次 `GET` 出的 `current_revision_no`
  // 才是结论，页面的 setData 只是被检查的对象。
  //
  // 写闸门（WRITE_ENABLED）只在显式驱动的那一刻打开、`finally` 里立刻关掉：
  // 闸门开着时若有别的页面在取数并误发写请求，也会被当成"该写"放行。
  if (!entrustArtifact) {
    // 有委托载荷却一份成果都挑不出来 = 种子或归属出了问题。这里**显式失败**：
    // 本 job 的 CI 定义里明确串了 seed_entrust_demo.py，静默跳过等于整段没验 ——
    // 而"全绿但什么都没验"比红更难发现（本轮已经这样空跑过一次）。
    fail('10 成果详情 · 取不到任何成果（seed_entrust_demo.py 未生效？）',
      '我的委托 ' + (D.entrustMine.items || []).length + ' 张 · '
      + '已查成果清单的委托 ' + Object.keys(entrustAssignmentArtifacts).length + ' 张')
  } else {
    const proof = entrustWriteProof
    const artId = entrustArtifact.artifact_id
    const bePayload = (entrustArtifact.current_revision || {}).payload || {}
    const spec = (entrustArtifactTypes.items || []).filter(function (t) {
      return t && t.code === entrustArtifact.artifact_type
    })[0] || null
    let writesDriven = 0

    const self = await walk('10 成果详情 · #' + artId, 'pages/entrust/artifact/artifact', null,
      { role: 'owner', arg: { artifact_id: String(artId) } }, ['onLoad'])

    if (self) {
      const P = '10 成果详情 #' + artId + ' · '
      const d = self._final()
      const art = d.artifact || {}
      const beCurrentNo = (entrustArtifact.current_revision || {}).revision_no

      // ① 页面把后端事实如实摆出来
      if (d.view !== E.VIEW.READY) fail(P + '正常载荷下未进入 ready', d.view + ' ' + d.viewHint)
      else ok()
      if (String(d.artifactId) !== String(artId) || String(art.artifactId) !== String(artId)) {
        fail(P + '页面持有的成果编号不对', String(d.artifactId) + '/' + String(art.artifactId))
      } else ok()
      if (art.currentRevisionNo !== beCurrentNo) {
        fail(P + '生效版本与后端不一致', String(art.currentRevisionNo) + ' vs ' + String(beCurrentNo))
      } else ok()
      if (art.canEdit !== true || art.canConfirm !== true) {
        fail(P + '有效且已登记的类型却不可编辑/确认',
          'canEdit=' + art.canEdit + ' canConfirm=' + art.canConfirm)
      } else ok()

      // ② 注册表 → 表单：行数、顺序、标签、必填标记四处都要与注册表逐字对齐
      const reqs = (spec && spec.required_fields) || []
      const opts = (spec && spec.optional_fields) || []
      const unk = entrustArtifact.unknown_fields || []
      const wantRows = reqs.length
        + opts.filter(function (n) { return reqs.indexOf(n) === -1 }).length + unk.length
      if (!spec) {
        fail(P + '成果类型不在注册表中', String(entrustArtifact.artifact_type))
      } else if (art.registryKnown !== true) {
        fail(P + '类型已登记但页面判为未登记')
      } else if ((art.fields || []).length !== wantRows) {
        fail(P + '字段行数与注册表不符', (art.fields || []).length + ' vs ' + wantRows)
      } else if (art.typeLabel !== spec.label) {
        fail(P + '类型中文名未取注册表', String(art.typeLabel))
      } else ok()
      const labelValues = Object.keys(E.ARTIFACT_FIELD_LABELS).map(function (k) {
        return E.ARTIFACT_FIELD_LABELS[k]
      })
      let labelBad = null
      for (const f of art.fields || []) {
        if (labelBad) break
        // 已知字段必须译成中文；未知字段回退成原始键名（回退成空串才会没人发现）
        if (f.unknown) { if (f.label !== f.name) labelBad = f.name + ' 应为原始键名' } else if (
          labelValues.indexOf(f.label) === -1) labelBad = f.name + ' → ' + f.label
      }
      if (labelBad) fail(P + '字段标签不在标签表内', labelBad)
      else ok()
      let reqBad = null
      for (const f of art.fields || []) {
        if (reqBad) break
        // 必填标错会让人去补不必补的字段，或漏补真正必需的字段
        if (!f.unknown && !!f.required !== (reqs.indexOf(f.name) !== -1)) {
          reqBad = f.name + ' required=' + f.required
        }
      }
      if (reqBad) fail(P + '必填标记与注册表不符', reqBad)
      else ok()

      // ③ 缺项与未声明字段：有就说、没有就不许凭空提示
      const beMissing = entrustArtifact.missing_fields || []
      if (beMissing.length && String(art.missingHint || '').indexOf(String(beMissing.length)) === -1) {
        fail(P + '缺项提示未如实报数', String(art.missingHint) + ' vs missing=' + beMissing.length)
      } else if (!beMissing.length && art.missingHint) {
        fail(P + '无缺项却给了缺项提示', String(art.missingHint))
      } else ok()
      if (!unk.length && art.unknownHint) fail(P + '无未声明字段却给了提示', String(art.unknownHint))
      else if (unk.length && !art.unknownHint) fail(P + '有未声明字段却没有任何提示')
      else ok()

      // ④ 版本历史：「生效 / 历史」派生自后端事实，且**恰好一条**生效
      const beRevs = entrustArtifactRevisions.items || []
      if ((d.revisions || []).length !== beRevs.length) {
        fail(P + '版本条数与后端不符', (d.revisions || []).length + ' vs ' + beRevs.length)
      } else ok()
      const currents = (d.revisions || []).filter(function (r) { return r.isCurrent })
      if (currents.length !== 1) {
        fail(P + '生效版本条数不是 1', String(currents.length))
      } else if (currents[0].revisionNo !== beCurrentNo) {
        fail(P + '「生效版本」标在了别的版本上', currents[0].revisionNo + ' vs ' + beCurrentNo)
      } else if (currents[0].roleLabel !== E.REVISION_ROLE.current) {
        fail(P + '生效版本角色文案不对', String(currents[0].roleLabel))
      } else ok()
      const byNo = function (a, b) { return a - b }
      const wantNos = beRevs.map(function (r) { return r.revision_no }).sort(byNo)
      const gotNos = (d.revisions || []).map(function (r) { return r.revisionNo }).sort(byNo)
      if (wantNos.join(',') !== gotNos.join(',')) {
        fail(P + '版本号与后端不符', gotNos.join(',') + ' vs ' + wantNos.join(','))
      } else ok()
      let tlBad = null
      for (const r of d.revisions || []) {
        if (tlBad) break
        // 标题行缺来源看着像"这一版不知道是谁产生的"；缺摘要则时间轴只剩版本号
        if (!r.title || String(r.title).indexOf('v' + r.revisionNo) !== 0) tlBad = 'title=' + r.title
        else if (!r.summary) tlBad = 'v' + r.revisionNo + ' 无摘要'
        else if (!r.roleClass) tlBad = 'v' + r.revisionNo + ' 无角色样式'
      }
      if (tlBad) fail(P + '版本时间轴行信息不全', tlBad)
      else ok()

      // ⑤ 编辑态：初值 = 当前生效版本的内容，未改动即不脏
      self.onEdit()
      await tick(30)
      const d2 = self._final()
      const fields = (d2.artifact || {}).fields || []
      if (d2.editing !== true) fail(P + '进入编辑态失败')
      else if ((d2.formFields || []).length !== fields.length) {
        fail(P + '编辑表单行数与字段数不符', (d2.formFields || []).length + ' vs ' + fields.length)
      } else if (d2.dirty !== false) fail(P + '刚进入编辑态就判定为脏')
      else ok()
      let draftBad = null
      ;(d2.formFields || []).forEach(function (f, i) {
        if (draftBad) return
        if (f.name !== fields[i].name) draftBad = '第 ' + i + ' 行是 ' + f.name + '，字段是 ' + fields[i].name
        else if (f.text !== fields[i].value) {
          draftBad = f.name + ' 初值 ' + JSON.stringify(f.text) + ' ≠ ' + JSON.stringify(fields[i].value)
        }
      })
      if (draftBad) fail(P + '编辑表单初值不是当前生效版本', draftBad)
      else ok()

      // ⑤b 声明为 list 的字段（尤其是**缺值**的那些）：形态、类型、初始文本都要
      //     来自**契约**。缺值字段没有值可看，按值推断必然判错 —— 本片修的缺口。
      const declaredListNames = proof.listFields || []
      const emptyListName = (proof.emptyListFields || []).filter(function (n) {
        return (d2.formFields || []).some(function (f) { return f.name === n })
      })[0] || null
      if (!declaredListNames.length) {
        note(P + '该成果类型没有声明为 list 的字段，缺值列表字段走查未覆盖')
      } else {
        let listBad = null
        ;(d2.formFields || []).forEach(function (f) {
          if (listBad || declaredListNames.indexOf(f.name) === -1) return
          if (f.declared !== 'list') listBad = f.name + ' declared=' + f.declared
          else if (f.kind !== 'json') listBad = f.name + ' kind=' + f.kind
          else if (String(f.kindHint || '').indexOf('JSON') === -1) {
            listBad = f.name + ' kindHint=' + String(f.kindHint)
          }
        })
        if (listBad) fail(P + '列表字段没有按契约走 JSON 编辑形态', listBad)
        else ok()
        if (!emptyListName) {
          note(P + '挑中的成果没有缺值列表字段（选型评分 ' + proof.artifactPickScore
            + '），该子用例（缺值也不退化）未覆盖')
        } else {
          const lf = (d2.formFields || []).filter(function (f) {
            return f.name === emptyListName
          })[0]
          if (lf.text !== '' || typeof lf.text !== 'string') {
            fail(P + '缺值的列表字段初始文本不是空串（undefined 会被 setData 丢掉，'
              + 'null 字符串会让人以为已经填了东西）',
            emptyListName + ' text=' + JSON.stringify(lf.text))
          } else ok()
        }
      }

      // ⑥ 改动 → 脏；改回 → 不脏（"动过就算脏"会让用户被无谓地拦在返回确认里）
      //
      // 挑字段有讲究：必须是**当前生效版本里已经有值**的标量字段。
      // 改一个"本来就缺"的字段会让 payload 新增一个键 —— 那是**正确**行为，
      // 却会被 ⑦ 里"键集必须与当前版本一致"的断言判成丢字段。
      // 本轮就是这样误报过一次：挑中的是刻意缺必填的 settlement_draft，
      // 而 index 0 正是它缺的那个 receivable_lines。
      const scalarIdx = (d2.formFields || []).findIndex(function (f) {
        return !f.unknown && f.kind !== 'json'
          && f.text !== '' && f.text !== undefined && bePayload[f.name] !== undefined
      })
      const editable = scalarIdx >= 0
      if (!editable) {
        note(P + '该类型没有"已有值且可编辑"的标量字段，脏判定与保存的真写分支未覆盖')
      } else {
        const orig = d2.formFields[scalarIdx].text
        self.onFieldInput({
          currentTarget: { dataset: { idx: scalarIdx } }, detail: { value: orig + '（e2e 改动）' }
        })
        await tick(20)
        if (self._final().dirty !== true) fail(P + '字段被改动后未标记为脏')
        else ok()
        self.onFieldInput({ currentTarget: { dataset: { idx: scalarIdx } }, detail: { value: orig } })
        await tick(20)
        if (self._final().dirty !== false) fail(P + '改回原值后仍判定为脏')
        else ok()
      }

      // ⑦ 真写 ①：保存 → 追加新版本。**生效版本必须没变** —— 这句话既要在后端成立，
      //    也要被界面说出来（少说一句，用户会以为客户已经看到新内容）。
      if (!editable) {
        note(P + '跳过保存走查（没有"已有值且可编辑"的标量字段）')
      } else {
        const prevNo = (self._final().artifact || {}).currentRevisionNo
        const baseKeys = Object.keys(bePayload).sort()
        const idx = scalarIdx
        const editedName = d2.formFields[idx].name
        const typed = String(d2.formFields[idx].text) + '（e2e 真写）'
        self.onFieldInput({ currentTarget: { dataset: { idx: idx } }, detail: { value: typed } })
        await tick(20)
        // 缺值的列表字段一并补成合法 JSON 数组。两个目的：
        //   · 证明"缺值也让列表字段能按数组提交"（本片修的缺口）；
        //   · 把"补上缺值字段会**新增**一个 payload 键"这个**正确行为**变成被断言
        //     的事实，而不是"要绕开的误报"。
        const listIdx = emptyListName
          ? (d2.formFields || []).map(function (f) { return f.name }).indexOf(emptyListName)
          : -1
        if (listIdx >= 0) {
          self.onFieldInput({
            currentTarget: { dataset: { idx: listIdx } },
            detail: { value: E2E_LIST_FILL }
          })
          await tick(20)
        }
        const before = pageWrites.length
        writesDriven += 1
        WRITE_ENABLED = true
        let saveThrown = null
        try {
          await self.onSave()
          await tick(60)
        } catch (e) { saveThrown = e } finally { WRITE_ENABLED = false }
        const d3 = self._final()
        const w1 = pageWrites[pageWrites.length - 1]
        if (saveThrown) fail(P + '保存抛异常', saveThrown.message)
        else if (pageWrites.length !== before + 1 || !w1) fail(P + '保存没有发出写请求')
        else if (w1.status !== 200) {
          note(P + '追加版本被拒（' + w1.status + '）：' + JSON.stringify(w1.data || {}).slice(0, 160))
        } else {
          proof.appendedNo = w1.data.revision_no
          proof.appendSupersedingCurrent = w1.data.superseding_current
          const sent = (w1.body || {}).payload || {}
          // 页面发出去的 payload 必须**从当前生效版本增量改**：键集变了就是有字段被
          // 静默丢弃（"按表单重建 payload"的典型症状，正文里有专门注释）
          const sentKeys = Object.keys(sent).sort()
          // 允许新增的键**恰好**是本次编辑的缺值列表字段（把缺的补上就是对的）；
          // 丢键一律算"字段被静默丢弃"，多出别的键算"凭空造字段"。
          const allowedNew = (listIdx >= 0 ? [emptyListName] : []).slice().sort()
          const lostKeys = baseKeys.filter(function (k) { return sentKeys.indexOf(k) === -1 }).sort()
          const addedKeys = sentKeys.filter(function (k) { return baseKeys.indexOf(k) === -1 }).sort()
          if (lostKeys.length) {
            fail(P + '保存的 payload 丢了字段（有字段被静默丢弃）', lostKeys.join(','))
          } else if (addedKeys.join(',') !== allowedNew.join(',')) {
            fail(P + '保存的 payload 新增了预期外的字段',
              addedKeys.join(',') + ' vs ' + allowedNew.join(','))
          } else ok()
          if (listIdx >= 0) {
            if (!Array.isArray(sent[emptyListName])) {
              fail(P + '缺值的列表字段被存成了非数组（内容对、类型错）',
                emptyListName + ' = ' + JSON.stringify(sent[emptyListName]))
            } else if (sent[emptyListName][0] !== 'e2e 新增项') {
              fail(P + '缺值的列表字段值不是用户输入的内容',
                JSON.stringify(sent[emptyListName]))
            } else ok()
          }
          if (String(sent[editedName]) !== typed) {
            fail(P + '保存的字段值不是用户输入的内容',
              JSON.stringify(sent[editedName]) + ' vs ' + JSON.stringify(typed))
          } else ok()
          // "键集没变"还不等于"值没被悄悄改掉"：数值/布尔被表单 string 化、
          // 内部字段被重排都会在这里露头（逐值比对，跳过被编辑的那一个）
          let drift = null
          for (const k of baseKeys) {
            if (drift || k === editedName || k === emptyListName) continue
            if (JSON.stringify(sent[k]) !== JSON.stringify(bePayload[k])) {
              drift = k + '：' + JSON.stringify(bePayload[k]) + ' → ' + JSON.stringify(sent[k])
            }
          }
          if (drift) fail(P + '保存顺带改动了未编辑的字段', drift)
          else ok()
          if (proof.appendedNo === prevNo) {
            fail(P + '追加出的版本号与生效版本相同，断言退化', String(proof.appendedNo))
          } else if (String(d3.saveNotice || '').indexOf('已保存为 v' + proof.appendedNo) === -1) {
            fail(P + '保存提示未说清新版本号', String(d3.saveNotice))
          } else if (String(d3.saveNotice).indexOf('生效版本仍是 v' + prevNo) === -1) {
            fail(P + '保存提示没说清生效版本未变', String(d3.saveNotice))
          } else ok()
          if (d3.editing !== false) fail(P + '保存成功后未退出编辑态')
          else ok()

          // 后端事实：追加后生效版本**没变**，版本数恰好 +1（是追加不是覆盖）
          await refreshArtifactReplay(artId)
          if (listIdx >= 0) {
            // 真载荷复核：后端**如实存下来的是数组**，不是字符串。这一条才是"类型对了"
            // 的终点 —— 前面查的都是页面**发出去**什么，这里看**存下来**什么。
            const apRow = (entrustArtifactRevisions.items || []).filter(function (r) {
              return r.revision_no === proof.appendedNo
            })[0]
            const stored = apRow && apRow.payload ? apRow.payload[emptyListName] : undefined
            if (!Array.isArray(stored)) {
              fail(P + '后端存的缺值列表字段不是数组（前端解析没生效）',
                emptyListName + ' = ' + JSON.stringify(stored))
            } else if (stored[0] !== 'e2e 新增项') {
              fail(P + '后端存的列表内容与用户输入不符', JSON.stringify(stored))
            } else ok()
          }
          proof.afterAppendCurrentNo = (entrustArtifact.current_revision || {}).revision_no
          if (proof.afterAppendCurrentNo !== prevNo) {
            fail(P + '编辑改变了生效版本（后端事实）', proof.afterAppendCurrentNo + ' vs ' + prevNo)
          } else ok()
          const afterCount = (entrustArtifactRevisions.items || []).length
          if (afterCount !== proof.beforeRevisionCount + 1) {
            fail(P + '版本数没有恰好 +1（不是追加？）',
              afterCount + ' vs ' + (proof.beforeRevisionCount + 1))
          } else ok()
          if (proof.appendSupersedingCurrent !== false) {
            fail(P + '追加响应的 superseding_current 不是 false', String(proof.appendSupersedingCurrent))
          } else ok()

          // 页面重载后要把这些事实显示出来：新版本进历史、生效版本仍是旧的
          self.onRetry()
          await tick(90)
          const d4 = self._final()
          const newRow = (d4.revisions || []).filter(function (r) {
            return r.revisionNo === proof.appendedNo
          })[0]
          if (!newRow) fail(P + '重载后版本历史里没有刚追加的 v' + proof.appendedNo)
          else if (newRow.isCurrent) fail(P + '刚追加的版本被显示为生效版本')
          else ok()
          if ((d4.artifact || {}).currentRevisionNo !== prevNo) {
            fail(P + '重载后生效版本显示不对', String((d4.artifact || {}).currentRevisionNo))
          } else ok()

          // ⑧ 真写 ②：点"设为生效版本" → 确认卡必须点名**成果 ID + 精确版本**
          //    （PRD 第 187 行：对话与工作台引用同一 artifact ID 与版本）
          if (!newRow) {
            note(P + '没有刚追加的版本行，确认走查跳过')
          } else {
            lastWx._modals = []
            self.onConfirm({ currentTarget: { dataset: { no: newRow.revisionNo } } })
            await tick(20)
            const modal = (lastWx._modals || [])[0]
            const want = '成果 #' + artId + ' · v' + newRow.revisionNo
            if (!modal) fail(P + '确认卡没有弹出')
            else if (String(modal.content || '').indexOf(want) === -1) {
              fail(P + '确认卡未点名成果 ID 与精确版本', String(modal.content))
            } else ok()
            if (!modal) {
              note(P + '确认走查跳过')
            } else {
              const before2 = pageWrites.length
              writesDriven += 1
              WRITE_ENABLED = true
              let confThrown = null
              try {
                await self.submitConfirm(newRow.revisionNo)
                await tick(60)
              } catch (e) { confThrown = e } finally { WRITE_ENABLED = false }
              const w2 = pageWrites[pageWrites.length - 1]
              if (confThrown) fail(P + '确认抛异常', confThrown.message)
              else if (pageWrites.length !== before2 + 1 || !w2) fail(P + '确认没有发出写请求')
              else if (w2.status !== 200) {
                note(P + '确认被拒（' + w2.status + '）：' + JSON.stringify(w2.data || {}).slice(0, 160))
              } else {
                await refreshArtifactReplay(artId)
                proof.afterConfirmNo = (entrustArtifact.current_revision || {}).revision_no
                if (proof.afterConfirmNo !== newRow.revisionNo) {
                  // 后端事实：确认绑定的是**被点中的那个版本**，不是"再取一次最新"
                  fail(P + '确认未绑定到所点的精确版本（后端事实）',
                    proof.afterConfirmNo + ' vs ' + newRow.revisionNo)
                } else ok()
                // `submitConfirm` 内部只重载过一次，而那时回放快照还是**写之前**的
                // （快照要等我这边刷新）。所以必须再重载一次，读到的才是写之后的库 ——
                // 否则会把"桩的快照旧"误判成"页面标记错了"（本轮误报过一次）。
                self.onRetry()
                await tick(90)
                const d5 = self._final()
                if (String(d5.saveNotice || '').indexOf('生效版本已切换为 v' + newRow.revisionNo) === -1) {
                  fail(P + '确认后未告知生效版本已切换', String(d5.saveNotice))
                } else ok()
                const cur5 = (d5.revisions || []).filter(function (r) { return r.isCurrent })
                if (cur5.length !== 1 || cur5[0].revisionNo !== newRow.revisionNo) {
                  fail(P + '确认后生效版本的界面标记不对',
                    cur5.map(function (r) { return r.revisionNo }).join(','))
                } else ok()
                note('10 成果详情 · 真写链路：生效 v' + proof.beforeCurrentNo + ' →(编辑) v'
                  + proof.appendedNo + '（生效未变）→(确认) 生效 v' + proof.afterConfirmNo
                  + '；版本数 ' + proof.beforeRevisionCount + '→' + (entrustArtifactRevisions.items || []).length)
              }
            }
          }
        }
      }

      // ⑨ 取数阶段不得发写请求：全程写请求数必须**恰好等于**显式驱动的次数。
      //    页面若在 load() 里顺手写点什么（比如自动确认最新版本），这条立刻红 ——
      //    而那种缺陷在只读走查里是看不见的。
      if (pageWrites.length !== writesDriven) {
        fail(P + '写请求数与显式驱动的次数不符', pageWrites.length + ' vs ' + writesDriven)
      } else ok()
    }
  }

  // ── 11 会话屏 ·「对话与工作台引用同一成果 ID 与版本」（AC-05 的核心判据）──────
  // 为什么必须**真载荷**：AC-05 要的是"同一"，不是"两页都能显示一个版本号"。
  // 若会话页自己另读一份投影（或读"最新版本"而不是"生效版本"），
  // 两边的数字会在一些时刻偶然相等 —— 只有拿后端事实逐条比对才判得出来。
  {
    const aid = entrustWriteProof.assignmentId
    if (!aid) {
      note('11 会话屏 · 无委托锚点（成果段没挑到成果），跳过')
    } else {
      const beRes = await api('GET',
        '/entrust/assignments/' + aid + '/artifacts?page=1&size=50', { token: ownerToken })
      // ⚠️ 这里必须用**模块级**的 `api` / `ownerToken`：`get` 与 `tok` 都是
      // bootstrap 内的局部闭包，在这一层不可见（写成 `get(...)` / `tok.owner`
      // 都会在运行期直接 ReferenceError 崩掉，而崩点在最后一段、前面的断言全绿，
      // 很容易被读成"新增那段只是没跑"）。
      if (beRes.status !== 200) {
        fail('11 会话屏 · 取后端成果清单失败', 'GET /assignments/' + aid + '/artifacts → '
          + beRes.status)
      }
      const beItems = ((beRes.data || {}).items) || []
      // 灌进桩的快照：本 harness 的既有做法就是"先用真接口拉、再把真载荷喂给页面"。
      // 不灌的话页面只会拿到 `{err: '未拉取委托 N 的成果清单'}` ⇒ 卡片为空，
      // 而错误信息会被读成"会话页取不到数据"（页面其实没错）。
      if (beRes.status === 200) entrustAssignmentArtifacts[aid] = beRes.data
      const s = await walk('11 会话屏 · 委托 #' + aid, 'pages/entrust/session/session', null,
        { role: 'owner', arg: { assignment_id: String(aid) } }, ['onLoad'])

      if (!beItems.length) {
        // 与成果段同一取向：有锚点却读不到成果 = 种子/归属出了问题，**显式失败**，
        // 不静默跳过（静默跳过等于整段没验，而"全绿但什么都没验"比红更难发现）。
        fail('11 会话屏 · 后端该委托下没有任何成果（seed_entrust_demo.py 未生效？）',
          'assignment #' + aid)
      } else if (s) {
        const P = '11 会话屏 #' + aid + ' · '
        const d = s._final()
        const cards = d.cards || []
        if (cards.length !== beItems.length) {
          // 失败信息里带上面页状态：卡片为空时，"view=denied / error" 与"取到了空清单"
          // 是两件完全不同的事，而只看条数分不出来。
          fail(P + '成果卡条数与后端不符', cards.length + ' vs ' + beItems.length
            + '（view=' + String(d.view) + '；hint=' + String(d.viewHint || '') + '）')
        } else ok()
        const beBy = {}
        beItems.forEach(function (x) { beBy[String(x.artifact_id)] = x })
        let bad = null
        for (const c of cards) {
          const b = beBy[String(c.artifactId)]
          if (!b) { bad = '后端没有成果 #' + c.artifactId; break }
          // ⚠️ 比对基准取**清单端点的扁平字段**（`current_revision_no`），
          // 而不是 `current_revision.revision_no` —— 后者只有**详情**端点才有，
          // 用它当基准会让"两边都是空"被当成一致（假绿），而 AC-05 要的正是版本号。
          const no = b.current_revision_no
          if (c.currentRevisionNo !== no) {
            bad = '#' + c.artifactId + ' 会话显示 v' + c.currentRevisionNo + '，清单端点 v' + no
            break
          }
        }
        if (bad) fail(P + '会话卡的版本不是后端事实', bad)
        else ok()

        // **三处同源**：会话卡 / 工作台槽位（同一 artifacts 清单）/ 成果页所读的那一份，
        // 必须是同一个 artifact_id 且同一个 revision_no。
        if (entrustArtifact) {
          const want = String(entrustArtifact.artifact_id)
          const wantNo = (entrustArtifact.current_revision || {}).revision_no
          const hit = cards.filter(function (c) { return String(c.artifactId) === want })
          if (!hit.length) {
            fail(P + '成果 #' + want + ' 在会话卡里不存在（对话看不到工作台的那一份）',
              'cards=' + cards.map(function (c) { return c.artifactId }).join(','))
          } else if (hit[0].currentRevisionNo !== wantNo) {
            fail(P + '同一成果在两处的版本不同（AC-05 的直接反例）',
              '会话 v' + hit[0].currentRevisionNo + ' vs 成果页/工作台 v' + wantNo)
          } else ok()
        } else {
          note('11 会话屏 · 成果段未取到参照成果，只做了"与后端一致"的比对')
        }
      }
    }
  }

  // ⑯ 案件登记与处置（切片四之六 / ENT-030）────────────────────────────
  //
  // 这一节在**真载荷**下钉三件：
  //   ① 登记页拿得到委托摘要、算得出候选、守得住非法入参；
  //   ② 案件页的处置区由 `capabilities` **与**取值域镜像**共同**决定 ——
  //      只看 `can_decide` 会在 `change_request/rejected`、`exception/applied` 上
  //      渲染出一个空的选择条（后端对这两个状态给 `can_decide=true`，
  //      而它们唯一的出边 `closed` 归 close 所有）；
  //   ③ 受影响项行带得出 `link_id`（移除要它；模板里拿不到就等于移除按钮永远无效）。
  //
  // ⚠️ 本节**不写库**：登记 / 决定 / 关闭三步的真流程由真机走查完成
  //    （DR-0013 §7.3 条件 4 要求"经界面操作走通"，那要在真模拟器里点）。
  //    这里只保证"页面能拿真载荷把该显示的都显示出来"，并核对写请求数没变。
  {
    const caseRows = D.entrustCaseQueue.items || []
    // 候选（任务 / 成果）只对**案件所属的那张委托**有意义。bootstrap 只为
    // "我的委托"拉过一轮，可能没覆盖到（它会挑中即 break）—— 这里按需补一次。
    // 不补的话页面会拿到"未拉取"的桩错误，把**数据准备问题**误判成页面缺陷。
    const aids = new Set(caseRows.map(function (x) { return Number(x.assignment_id) }))
    for (const aid of aids) {
      if (!entrustTasksByAssignment[aid]) {
        const r = await api('GET', '/entrust/tasks?assignment_id=' + aid + '&size=50', { token: ownerToken })
        if (r.status === 200) entrustTasksByAssignment[aid] = r.data
      }
      if (!entrustAssignmentArtifacts[aid]) {
        const r = await api('GET', '/entrust/assignments/' + aid + '/artifacts?size=50', { token: ownerToken })
        if (r.status === 200) entrustAssignmentArtifacts[aid] = r.data
      }
    }

    // ⑯-a 登记案件页：非法入参被守卫拦下（且**不发起取数**）
    for (const [tag, arg] of [['缺参', {}], ['非法编号', { assignment_id: '../../x' }]]) {
      const s = await walk('⑯ 登记案件 · ' + tag, 'pages/entrust/case-create/case-create', null,
        { role: 'owner', arg }, ['onLoad'])
      if (!s) continue
      const d = s._final()
      if (d.view !== 'error') fail('⑯ 登记案件 ' + tag + ' · 未落 error 态（会被当成正常业务状态）', String(d.view))
      else if (!d.viewTitle) fail('⑯ 登记案件 ' + tag + ' · error 态没有标题')
      else ok()
      if (d.detail) fail('⑯ 登记案件 ' + tag + ' · 被拦下却还是取了数', String(d.detail.assignmentId))
      else ok()
    }

    // ⑯-b 登记案件页：合法入参进 ready，且候选 / 受影响项走得通
    const firstAid = caseRows.length
      ? String(caseRows[0].assignment_id)
      : String(((D.entrustMine.items || [])[0] || {}).assignment_id || '')
    if (!firstAid || firstAid === 'undefined') {
      note('⑯ 登记案件 · 没有可用的委托编号，登记页走查跳过')
    } else {
      const writesBefore = pageWrites.length
      const s = await walk('⑯ 登记案件 · #' + firstAid, 'pages/entrust/case-create/case-create', null,
        { role: 'owner', arg: { assignment_id: firstAid } }, ['onLoad'])
      if (s) {
        const d = s._final()
        if (d.view !== 'ready') fail('⑯ 登记案件 · 未落 ready 态', String(d.view))
        else if (String(d.assignmentId) !== firstAid) {
          fail('⑯ 登记案件 · 持有的委托编号与入参不符', String(d.assignmentId))
        } else if (!d.detail) fail('⑯ 登记案件 · 没有委托摘要（头卡会是空的）')
        else ok()
        // 三个选择条必须与契约取值域一致（页面不自己 Object.keys 标签表）
        const kindOk = (d.kinds || []).length === Object.keys(E.CASE_KIND_LABELS).length
        const sevOk = (d.severities || []).length === Object.keys(E.CASE_SEVERITY_LABELS).length
        const impOk = (d.impacts || []).length === Object.keys(E.CASE_IMPACT_LABELS).length
        if (!kindOk || !sevOk || !impOk) {
          fail('⑯ 登记案件 · 选择条取值域与契约不符',
            [kindOk, sevOk, impOk].join(','))
        } else ok()
        // 默认影响类型**不得**是阻断（阻断有后果，不能是"没改过就是这个"）
        if (d.form.impact_kind === 'execution-blocking') {
          fail('⑯ 登记案件 · 默认影响类型是"阻断执行"（默认值不该有流程后果）')
        } else ok()

        // 候选：展开面板 → 懒加载 → 拿到本单的任务 / 成果
        let pickThrown = null
        try { s.onToggleLinks() } catch (e) { pickThrown = e }
        await tick(90)
        if (pickThrown) fail('⑯ 登记案件 · 展开候选抛异常', pickThrown.message)
        else {
          const dc = s._final()
          if (dc.candLoaded !== true) fail('⑯ 登记案件 · 候选没有加载完成（candLoaded 仍为 false）')
          else if ((dc.candidates || []).length && dc.candHint) {
            fail('⑯ 登记案件 · 有候选却还带着失败提示', String(dc.candHint))
          } else ok()
          const cand = (dc.candidates || [])[0]
          if (!cand) {
            note('⑯ 登记案件 · 本单没有任务 / 成果，选受影响项一步跳过')
          } else {
            let addThrown = null
            try {
              s.onAddLink({ currentTarget: { dataset: { kind: cand.target_kind, id: cand.target_id } } })
            } catch (e) { addThrown = e }
            await tick(20)
            const dl = s._final()
            if (addThrown) fail('⑯ 登记案件 · 选受影响项抛异常', addThrown.message)
            else if ((dl.links || []).length !== 1) {
              fail('⑯ 登记案件 · 选中的受影响项没有进列表', String((dl.links || []).length))
            } else if (!dl.links[0].key || String(dl.links[0].target_id) !== String(cand.target_id)) {
              fail('⑯ 登记案件 · 受影响项行缺 key 或编号不对', JSON.stringify(dl.links[0]))
            } else ok()
            // 去掉一条也要能生效（移除按钮靠 dataset 里的行标识，不是靠数组下标）
            // ⚠️ 键名是 `rmKey` 而**不是** `key`：模板里该按钮用 `data-rm-key`
            //    （`data-key` 已被筛选 pill 占用，共用会让走查选择器歧义）。
            //    本轮把模板改成 `data-rm-key` 却漏改了这里的驱动，CI 立刻报
            //    「移除后列表仍有条目」—— 这条断言的价值就在这：驱动与模板的键名
            //    必须同步，改了任一侧而另一侧没跟上就会红。
            let rmThrown = null
            try { s.onRemoveLink({ currentTarget: { dataset: { rmKey: dl.links[0].key } } }) }
            catch (e) { rmThrown = e }
            await tick(20)
            const dr = s._final()
            if (rmThrown) fail('⑯ 登记案件 · 移除受影响项抛异常', rmThrown.message)
            else if ((dr.links || []).length !== 0) {
              fail('⑯ 登记案件 · 移除后列表仍有条目', String((dr.links || []).length))
            } else ok()
          }
        }
        collect('pages/entrust/case-create/case-create',
          path.join(ROOT, 'miniapp/pages/entrust/case-create/case-create.js'), s._final())
      }
      // 取数阶段不得写：登记页的 onLoad 只读不写
      if (pageWrites.length !== writesBefore) {
        fail('⑯ 登记案件 · 取数阶段发出了写请求', String(pageWrites.length - writesBefore))
      } else ok()
    }

    // ⑯-c 案件页（含处置区）。**逐宗都走**：队列里的两宗刻意覆盖"有受影响项"与
    //      "无受影响项"两种形状，只走第一宗会让另一种形状的模板字段永远拿不到运行时值
    //      （模板核对会因此报"模板读取但数据与生产均未产出"——那是模板核对在替我们数形状）。
    if (!caseRows.length) {
      note('⑯ 案件页 · 组织队列里没有案件，处置区走查跳过')
    }
    for (const row of caseRows) {
      const cid = String(row.case_id)
      const s = await walk('⑯ 案件详情 · #' + cid, 'pages/entrust/case/case', null,
        { role: 'owner', arg: { case_id: cid } }, ['onLoad'])
      if (!s) continue
      const d = s._final()
      if (d.view !== 'ready') {
        fail('⑯ 案件详情 #' + cid + ' · 未落 ready 态', String(d.view))
        continue
      }
      if (String(d.caseId) !== cid) fail('⑯ 案件详情 #' + cid + ' · 持有的案件编号不对', String(d.caseId))
      else ok()
      if (!d.detail) { fail('⑯ 案件详情 #' + cid + ' · 没有投影（页面会是空的）'); continue }

      // 处置区：能力位与取值域镜像**共同**决定渲染条件
      const caps = (entrustCaseDetails[cid] || {}).capabilities || null
      if (!caps) {
        note('⑯ 案件详情 #' + cid + ' · 无 bootstrap 载荷可对照能力位')
      } else {
        const wantDecide = E.caseDecideAvailable(caps, d.kind, d.status)
        const wantClose = !!caps.can_close && E.caseClosureOptions(d.kind, d.status).length > 0
        if (d.canAddLink !== !!caps.can_add_link || d.canDecide !== wantDecide || d.canClose !== wantClose) {
          fail('⑯ 案件详情 #' + cid + ' · 处置能力位与契约结论不符',
            JSON.stringify({ canAddLink: d.canAddLink, canDecide: d.canDecide, canClose: d.canClose }) +
            ' vs ' + JSON.stringify({ canAddLink: !!caps.can_add_link, canDecide: wantDecide, canClose: wantClose }))
        } else ok()
        if (d.canAnyAction !== (!!caps.can_add_link || wantDecide || wantClose || !!caps.can_reopen)) {
          fail('⑯ 案件详情 #' + cid + ' · canAnyAction 与各分项不自洽', String(d.canAnyAction))
        } else ok()
      }
      // 处置选项必须与契约镜像逐项相等（差一项就是用户能看到一个必然被拒的选项）
      const wantD = E.caseDecisionOptions(d.kind, d.status).map((x) => x.key).join(',')
      const gotD = (d.decisionOptions || []).map((x) => x.key).join(',')
      const wantC = E.caseClosureOptions(d.kind, d.status).map((x) => x.key).join(',')
      const gotC = (d.closureOptions || []).map((x) => x.key).join(',')
      if (wantD !== gotD) fail('⑯ 案件详情 #' + cid + ' · 决定选项与契约不符', gotD + ' vs ' + wantD)
      else if (wantC !== gotC) fail('⑯ 案件详情 #' + cid + ' · 关闭处置与契约不符', gotC + ' vs ' + wantC)
      else ok()
      // 受影响项行必须带得出 link_id（移除按钮靠它；拿不到就等于永远移除不掉）。
      // 对照的是**后端原始载荷**（`entrustCaseDetails[cid].case.affected`），
      // 不是 `d.detail` 的同名字段 —— 后者本来就没有 `affected` 这一项
      // （受影响项在投影里是**六要素②的 items**，`decorateCase` 不另给顶层字段）。
      // 拿不存在的字段当基准会得到 `0 vs 1` 这种看起来像页面错的假失败。
      const rawAffected = ((entrustCaseDetails[cid] || {}).case || {}).affected || []
      if ((d.affected || []).length !== rawAffected.length) {
        fail('⑯ 案件详情 #' + cid + ' · 处置区的受影响项条数与后端载荷不符',
          (d.affected || []).length + ' vs ' + rawAffected.length)
      } else if (rawAffected.length && (d.affected || []).some(function (x) { return !x.linkId })) {
        fail('⑯ 案件详情 #' + cid + ' · 受影响项行没有 link_id（移除按钮会拿不到参数）')
      } else ok()

      // 处置区的候选：仅在有 can_add_link 时展开（否则那个面板根本不渲染）
      if (d.canAddLink) {
        let thrown = null
        try { s.onToggleLinkPick() } catch (e) { thrown = e }
        await tick(90)
        const dc = s._final()
        if (thrown) fail('⑯ 案件详情 #' + cid + ' · 展开候选抛异常', thrown.message)
        else if (dc.candLoaded !== true) fail('⑯ 案件详情 #' + cid + ' · 候选没有加载完成')
        else ok()
      }
      collect('pages/entrust/case/case', path.join(ROOT, 'miniapp/pages/entrust/case/case.js'), s._final())
    }
  }

  auditTemplates()

  console.log('\n' + '='.repeat(78))
  console.log('OK ' + N_OK + ' · FAIL ' + FAILS.length + ' · note ' + NOTES.length)
  // 报出**实际**覆盖的页面数：这个数字会被 ci.yml 的 job 注释与计划文档引用，
  // 与其在别处写一个会过期的数，不如每次由脚本自己说出来（本轮就因为注释里的
  // 旧数字与新增页面不一致而需要额外核对一次）。
  console.log('覆盖页面 ' + Object.keys(pageUniverse).length + ' 个 / 断言 ' + N_OK)
  console.log('='.repeat(78))
  if (FAILS.length) { console.log('\n需处理：'); FAILS.forEach((f) => console.log('  - ' + f)) }
  if (NOTES.length) { console.log('\n备注：'); NOTES.forEach((n) => console.log('  - ' + n)) }
  process.exit(FAILS.length ? 1 : 0)
})()
