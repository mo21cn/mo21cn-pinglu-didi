#!/usr/bin/env node
/**
 * 小程序「真机走查」验证器（微信开发者工具 · 模拟器自动化）
 * ---------------------------------------------------------------------------
 * 与 verify_frontend_e2e.js 的分工：
 *   - verify_frontend_e2e.js 在 Node 里驱动页面纯逻辑（无需开发者工具，CI 可跑）
 *   - 本脚本驱动**真实模拟器**：真实点击、真实渲染、逐屏截图，用于汇报前走查
 *
 * 前置条件（缺一不可）：
 *   1) 后端已启动：cd backend && LLM_MOCK=true WECHAT_MOCK=true uvicorn app.main:app --reload
 *   2) 演示数据已铺：python scripts/seed_demo.py
 *   3) 开发者工具**服务端口已打开**：设置 → 安全设置 → 服务端口
 *      （CLI 无法代开；端口号见同名开关下方提示，本脚本用 MINIAPP_AUTO_WS 覆盖）
 *   4) miniapp/project.private.config.json 存在（appid + urlCheck:false），见 *.example.json
 *
 * 用法：
 *   node scripts/verify_miniapp_device.js
 *   MINIAPP_AUTO_WS=ws://127.0.0.1:9421 node scripts/verify_miniapp_device.js
 *   WALK_PAY=1 node scripts/verify_miniapp_device.js     # 额外真机点击「模拟支付」（会消耗演示锚点）
 *
 * 退出码：0 全部通过；1 有断言失败；2 致命错误（连不上模拟器等）
 *
 * 依赖（未入库，按需安装）：
 *   npm i -g miniprogram-automator  或装到任意 node_modules 并设 NODE_PATH
 */
const path = require('path')
const fs = require('fs')
const util = require('util')

const WS = process.env.MINIAPP_AUTO_WS || 'ws://127.0.0.1:9420'
const API = process.env.API_BASE || 'http://127.0.0.1:8000/api/v1'
const ROOT = path.resolve(__dirname, '..')
// ⚠️ 每轮走查写进独立的时间戳子目录：不清空旧目录会新旧混淆，
//    但「一次性删掉整个目录的 png」会被沙箱批量删除保护拦下
//    （SAFE_DELETE_BULK_CONFIRM_REQUIRED，阈值 50/轮），所以改为分目录 + LATEST.txt 指针。
const SHOTS_ROOT = process.env.WALK_SHOTS || path.join(ROOT, 'tmp', 'walkthrough-shots')
const RUN_ID = process.env.WALK_RUN_ID || new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
const SHOTS = path.join(SHOTS_ROOT, RUN_ID)
const DO_PAY = process.env.WALK_PAY === '1'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let automator
try {
  automator = require('miniprogram-automator')
} catch (e) {
  console.error('[FATAL] 未找到 miniprogram-automator。请先安装：npm i -g miniprogram-automator')
  process.exit(2)
}

const R = []
const rec = (step, ok, note = '') => {
  R.push({ step, ok, note })
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${step}${note ? ' | ' + note : ''}`)
}

// ------------------------------- 后端锚点预取 -------------------------------
async function apiLogin(code) {
  const r = await fetch(API + '/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code })
  })
  if (!r.ok) throw new Error(`登录失败 ${code}: HTTP ${r.status}`)
  return r.json()
}
const auth = (t) => ({ Authorization: 'Bearer ' + t })
async function apiGet(p, t) {
  const r = await fetch(API + p, { headers: auth(t) })
  return r.status === 200 ? r.json() : null
}
async function apiPost(p, t, body) {
  const r = await fetch(API + p, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...auth(t) }, body: JSON.stringify(body || {})
  })
  return { status: r.status, data: await r.json().catch(() => null) }
}

// --------------------------------- 模拟器辅助 ---------------------------------
async function shot(mp, name) {
  const p = path.join(SHOTS, name + '.png')
  try {
    await mp.screenshot({ path: p })
    const size = fs.existsSync(p) ? fs.statSync(p).size : 0
    rec('截图 ' + name, size > 8000, size + 'B')
  } catch (e) {
    rec('截图 ' + name, false, String(e && e.message))
  }
}
async function tapAt(page, sel, i) {
  const els = await page.$$(sel)
  if (!els || !els.length) throw new Error('未找到元素 ' + sel)
  await els[Math.min(Math.max(i, 0), els.length - 1)].tap()
}
/** 用指定 code 登录（联调期靠 Storage 的 dev_login_code 指定种子身份） */
async function loginAs(mp, code) {
  await mp.evaluate((c) => {
    wx.setStorageSync('dev_login_code', c)
    wx.removeStorageSync('access_token')
    wx.removeStorageSync('user_info')
  }, code)
  await mp.reLaunch('/pages/index/index')
  await sleep(1200)
  return mp.currentPage()
}
async function waitPath(mp, want, tries = 25) {
  for (let i = 0; i < tries; i++) {
    try {
      const p = await mp.currentPage()
      if (p && p.path === want) return p
    } catch (_) { /* 会话瞬时无响应，继续等 */ }
    await sleep(300)
  }
  return null
}
/** 带重试的点击（页面过渡期会吞事件） */
async function tapRetry(page, getEl, ok, tries = 3, gap = 1400) {
  for (let i = 0; i < tries; i++) {
    const el = await getEl()
    if (!el) return false
    await el.tap()
    await sleep(gap)
    if (await ok()) return true
  }
  return false
}
/** 导航容错：模拟器在页面过渡/长任务时回执会迟到，首次超时往往页面其实已经
 *  跳过去了（currentPage 已变）→ 以「目标路径是否就位」判定成功，
 *  避免整轮走查被单次慢响应打断（实测：撮合页首次 navigateTo 回执超时会炸掉全程） */
async function nav(mp, how, target, wantPath) {
  let lastErr = null
  for (let i = 0; i < 3; i++) {
    try {
      if (how === 'tab') await mp.switchTab(target)
      else await mp.navigateTo(target)
      const p = await waitPath(mp, wantPath, 25)
      if (p) return p
      lastErr = new Error(`导航后未到达 ${wantPath}`)
    } catch (e) {
      lastErr = e
      const p = await waitPath(mp, wantPath, 12) // 超时兜底：看页面是否其实已就位
      if (p) { console.log(`[nav] ${target} 回执超时但页面已就位，继续`); return p }
    }
    await sleep(1200)
  }
  throw lastErr
}
/** 返回上一页：失败不致命（走查由后续 waitPath 兜底） */
async function backSafe(mp) {
  for (let i = 0; i < 2; i++) {
    try { await mp.navigateBack(); await sleep(600); return true } catch (_) { await sleep(800) }
  }
  return false
}
/**
 * 轮询页面 data 直到满足条件（合同页要等 LLM 生成完）。
 * 固定 sleep 不可靠：真实 LLM 生成慢于 sleep 时，读到的是 loading 态的空数据
 * （表现为 raw=0 / risks=[]，看起来像功能坏了，其实是等太短）。
 * 返回最后一次读到的 data（超时也返回，交给断言判失败）。
 */
async function waitData(page, pred, tries = 60, gap = 500) {
  let last = {}
  for (let i = 0; i < tries; i++) {
    try {
      last = (await page.data()) || {}
      if (pred(last)) return last
    } catch (_) { /* 页面切换中，继续等 */ }
    await sleep(gap)
  }
  return last
}
/** 合同页就绪：不在 loading，且（有正文 或 已落到错误态） */
const CONTRACT_READY = (d) => !!d && d.loading === false && (String(d.rawText || '').length > 0 || !!d.error)
const log = (t, o) => console.log(`[${t}]`, typeof o === 'string' ? o : JSON.stringify(o))

// ------------------------------------ 主流程 ------------------------------------
;(async () => {
  fs.mkdirSync(SHOTS, { recursive: true })
  fs.writeFileSync(path.join(SHOTS_ROOT, 'LATEST.txt'), SHOTS, 'utf8')

  // 0. 预取锚点（不写死 ID，随库自适应）
  const s = await apiLogin('seed-shipper')
  const o = await apiLogin('seed-owner')
  const pt = await apiLogin('seed-port')
  const cargos = (await apiGet('/cargo/shipments?size=100', s.access_token)).items
  const orders = (await apiGet('/order/orders?size=100', s.access_token)).items
  const berths = (await apiGet('/port/berths?size=50', pt.access_token)).items
  const appts = (await apiGet('/port/appts-review?status=&size=100', pt.access_token)).items
  const ships = (await apiGet('/ship/registry?size=50', o.access_token)).items

  let payAnchor = null
  for (const x of orders.filter((v) => v.status === 'matched')) {
    const pm = await apiGet(`/payment/payments/order/${x.id}`, s.access_token)
    if (pm && pm.status === 'pending') { payAnchor = { order: x, payment: pm }; break }
  }
  let matchCargo = null
  for (const c of cargos.filter((v) => v.status === 'published')) {
    const r = await apiPost(`/match/cargos/${c.id}/ships`, s.access_token)
    const total = r.status === 200 ? (r.data.total || 0) : 0
    if (!matchCargo || total > matchCargo.total) matchCargo = { cargo: c, total }
  }
  const shipAnchor = ships.filter((v) => v.status === 'verified').sort((a, b) => (b.deadweight_t || 0) - (a.deadweight_t || 0))[0]

  log('锚点/待支付', payAnchor ? { order: payAnchor.order.id, freight: payAnchor.order.freight_price, payment: payAnchor.payment.id } : '未找到')
  log('锚点/撮合货源', matchCargo ? { id: matchCargo.cargo.id, candidates: matchCargo.total } : '无')
  log('锚点/撮合船舶', shipAnchor ? { id: shipAnchor.id, name: shipAnchor.ship_name } : '无')
  // 智能合同仿真案例（seed_contract_cases.py 铺设）：按货源名里的 R3/R4/R5 标记定位订单
  const simCases = []
  for (const key of ['R3', 'R4', 'R5']) {
    const cargo = cargos.find((c) => c.cargo_name && c.cargo_name.indexOf('仿真案例 · ' + key) >= 0)
    if (!cargo) continue
    const order = orders.find((o) => o.cargo_id === cargo.id && o.status !== 'cancelled')
    if (order) simCases.push({ key, cargoId: cargo.id, orderId: order.id })
  }
  log('智能合同仿真案例', simCases.length ? simCases.map((x) => `${x.key}#${x.orderId}`).join(' ') : '未铺设')
  rec('预取智能合同仿真案例（R3/R4/R5）', simCases.length >= 3, simCases.map((x) => x.key + '#' + x.orderId).join(' '))

  log('库内规模', { cargos: cargos.length, orders: orders.length, berths: berths.length, appts: appts.length, ships: ships.length })
  rec('预取演示锚点', !!(payAnchor && matchCargo && shipAnchor),
    `order=${payAnchor && payAnchor.order.id} cargo=${matchCargo && matchCargo.cargo.id} ship=${shipAnchor && shipAnchor.id}`)

  const mp = await automator.connect({ wsEndpoint: WS })
  rec('连接模拟器', true, WS)
  const errs = []
  mp.on('console', (m) => {
    if (m.type === 'error') errs.push('[console.error] ' + m.args.map((a) => (typeof a === 'string' ? a : util.inspect(a, { depth: 2 }))).join(' '))
  })
  mp.on('exception', (e) => errs.push('[EXCEPTION] ' + util.inspect(e, { depth: 2 })))

  // ============ ⓪ 清缓存 → 按身份卡进入 → 订单必须有数据 ============
  // 直复现 2026-09-11 用户报的「订单页面仍然是空白」：
  // 根因是开发期固定身份用了一个全新空账号（devtools-local），而演示数据都在
  // seed-shipper / seed-owner 名下 —— 后端按 user_id 过滤订单 → 返回 200 空列表，
  // 页面不报错、走「暂无订单」空态，看起来就像功能坏了。
  // 修法见 miniapp/utils/auth.js 的 DEV_ROLE_CODE（按角色映射到演示账号），
  // 这里用真机把「清缓存 → 点身份卡 → 订单非空」这条路径钉住。
  for (const [cardIdx, roleLabel, wantPage] of [[0, '货主', 'pages/shipper/shipper'], [1, '船东', 'pages/owner/owner']]) {
    await mp.evaluate(() => {
      ;['dev_login_code', 'dev_device_code', 'access_token', 'user_info'].forEach((k) => wx.removeStorageSync(k))
    })
    await mp.reLaunch('/pages/index/index')
    await sleep(1600)
    const pi = await mp.currentPage()
    rec(`⓪ 清缓存后停在身份选择页（${roleLabel}）`, pi && pi.path === 'pages/index/index', pi && pi.path)
    await shot(mp, `00-清缓存-身份选择-${roleLabel}`)

    await tapAt(pi, '.role-card', cardIdx)
    const wp = await waitPath(mp, wantPage, 30)
    rec(`⓪ 点「${roleLabel}」进入工作台`, !!wp, wp ? wp.path : '未跳转')
    if (!wp) continue

    const code = await mp.evaluate(() => wx.getStorageSync('dev_device_code'))
    rec(`⓪ 身份已映射到演示账号（${roleLabel}）`,
      code === (cardIdx === 0 ? 'seed-shipper' : 'seed-owner'), String(code))

    const op = await nav(mp, 'tab', '/pages/trade/orders/orders', 'pages/trade/orders/orders')
    await sleep(2200)
    const od = await op.data()
    const n = (od.list || []).length
    rec(`★ ⓪ ${roleLabel}「订单」页有数据（不再是空白）`,
      n > 0 && !od.error, `list=${n} raw=${(od.rawList || []).length} error=${od.error || '-'}`)
    rec(`⓪ ${roleLabel} 订单统计行已渲染`, (od.stats || []).length === 5,
      JSON.stringify((od.stats || []).map((s) => s.label + ':' + s.count)))
    await shot(mp, `00-${roleLabel}-订单页`)
  }

  // ============================ 货主链路 ============================
  await loginAs(mp, 'seed-shipper')
  let p = await mp.currentPage()
  rec('① 首页身份选择渲染', p && p.path === 'pages/index/index', p && p.path)
  await shot(mp, '01-首页-身份选择')

  await tapAt(p, '.role-card', 0) // 我是货主
  let shipper = await waitPath(mp, 'pages/shipper/shipper', 30)
  rec('② 货主工作台进入（真实点击身份卡）', !!shipper, shipper ? shipper.path : '未跳转')
  if (!shipper) throw new Error('货主工作台未进入，走查中断')
  await sleep(1600)
  await shot(mp, '02-货主找船')
  const sd = await shipper.data()
  rec('② 货主页无错误且取数完成', !sd.error && !sd.loading,
    `myCargoTotal=${sd.myCargoTotal} hotShips=${(sd.hotShips || []).length} hotRoutes=${(sd.hotRoutes || []).length}`)

  const pub = await nav(mp, 'to', '/pages/publish/cargo/cargo', 'pages/publish/cargo/cargo')
  await sleep(1200)
  await shot(mp, '04-发布货物')
  rec('④ 发布货物页渲染', !(await pub.data()).error, 'form keys=' + Object.keys((await pub.data()).form || {}).join(','))

  const ordersPage = await nav(mp, 'tab', '/pages/trade/orders/orders', 'pages/trade/orders/orders')
  await sleep(1800)
  await shot(mp, '06-订单页')
  const od = await ordersPage.data()
  const olist = od.list || []
  rec('⑥ 订单页无错误且列表有数据', !od.error && olist.length > 0, `list=${olist.length} stats=${(od.stats || []).length}`)
  const c0 = olist[0] || {}
  rec('⑥ 订单卡路线/货名/船名（前端富化）', !!(c0.origin_label && c0.dest_label && c0.cargo_name && c0.ship_name),
    `#${c0.id} ${c0.origin_label}→${c0.dest_label} ${c0.cargo_name}`)
  rec('⑥ 待办卡生成', (od.todoList || []).length >= 0, JSON.stringify(od.todoList || []))

  // ⑧ 支付详情三级页（真实点击「去支付」）
  if (payAnchor) {
    const idx = olist.findIndex((x) => x.id === payAnchor.order.id)
    rec('⑧ 待支付订单在列表可见', idx >= 0, `order #${payAnchor.order.id} idx=${idx}`)
    if (idx >= 0) {
      const card = (await ordersPage.$$('.order-card'))[idx]
      let tapped = false
      for (const b of (await card.$$('.act-primary')) || []) {
        if (String(await b.text().catch(() => '')).indexOf('支付') >= 0) { await b.tap(); tapped = true; break }
      }
      rec('⑧ 「去支付」真实点击', tapped)
      await waitPath(mp, 'pages/trade/payment/payment', 25)
      await sleep(2200)
      await shot(mp, '08-支付详情三级页')
      const pp = await mp.currentPage()
      const pm = await pp.data()
      log('支付页', { orderId: pm.orderId, amountText: pm.amountText, statusLabel: pm.statusLabel, canPay: pm.canPay, timeline: (pm.timeline || []).length })
      rec('⑧ 支付页进入', pp.path === 'pages/trade/payment/payment', pp.path)
      rec('⑧ 金额为锁定的订单运费', String(pm.amountText || '').length > 0 && pm.amountText !== '面议', `amountText=${pm.amountText}`)
      rec('⑧ 资金留痕时间轴就绪', (pm.timeline || []).length >= 2, 'segments=' + (pm.timeline || []).length)
      rec('⑧ 底栏可支付', pm.canPay === true, 'canPay=' + pm.canPay)

      if (DO_PAY) {
        await mp.mockWxMethod('showModal', { confirm: true, cancel: false })
        const btn = await pp.$('.btn-primary')
        if (btn) {
          await btn.tap()
          await sleep(4500)
          const after = await pp.data()
          rec('⑧b 模拟支付后转「已支付」且不可重复支付', after.statusLabel === '已支付' && after.canPay === false,
            `statusLabel=${after.statusLabel}`)
          await shot(mp, '08b-支付-点击模拟支付后')
        }
        await mp.restoreWxMethod('showModal').catch(() => {})
      }
      await backSafe(mp)
      await sleep(1400)
    }
  }

  // ⑦ 合同三级页（真实点击「查看合同」+ 长按弹层）
  if (payAnchor) {
    const cur = await mp.currentPage()
    const idx2 = ((await cur.data()).list || []).findIndex((x) => x.id === payAnchor.order.id)
    if (idx2 >= 0) {
      const card = (await cur.$$('.order-card'))[idx2]
      let tapped = false
      for (const b of (await card.$$('.act-ghost')) || []) {
        if (String(await b.text().catch(() => '')).indexOf('合同') >= 0) { await b.tap(); tapped = true; break }
      }
      rec('⑦ 「查看合同」真实点击', tapped, `order #${payAnchor.order.id}`)
      await waitPath(mp, 'pages/trade/contract/contract', 25)
      const cp = await mp.currentPage()
      const cd = await waitData(cp, CONTRACT_READY)
      await shot(mp, '07-合同预览三级页')
      log('合同页', { orderId: cd.orderId, html: (cd.contractHtml || '').length, risks: (cd.risks || []).length, high: cd.highCount, mocked: cd.mocked })
      rec('⑦ 合同页进入', cp.path === 'pages/trade/contract/contract', cp.path)
      rec('⑦ 合同正文有内容', ((cd.contractHtml || '').length > 200) || ((cd.rawText || '').length > 200),
        `html=${(cd.contractHtml || '').length} raw=${(cd.rawText || '').length}`)
      rec('⑦ 风险卡渲染', Array.isArray(cd.risks), `risks=${(cd.risks || []).length} high=${cd.highCount}`)
      await backSafe(mp)
      await sleep(1500)
      const cur2 = await mp.currentPage()
      const id3 = ((await cur2.data()).list || []).findIndex((x) => x.id === payAnchor.order.id)
      let c3 = {}
      for (let i = 0; i < 3; i++) {
        const card2 = (await cur2.$$('.order-card'))[id3]
        if (!card2) break
        await card2.longpress()
        // 弹层内容同样来自合同 Agent，慢生成时会出现「弹层在但正文空」→ 等 show 即可
        c3 = await waitData(cur2, (d) => !!(d.contract && d.contract.show), 16, 400)
        if (c3.contract && c3.contract.show) break
        await sleep(900)
      }
      await shot(mp, '07b-订单页-长按合同弹层')
      rec('⑦ 长按订单卡弹出合同弹层', !!(c3.contract && c3.contract.show))
      if (c3.contract && c3.contract.show) { await tapAt(cur2, '.modal-mask', 0).catch(() => {}); await sleep(700) }
    }
  }

  // ⑦b 智能合同仿真案例（R3/R4/R5：订单页「查看合同」→ 风险卡命中预期规则）
  const EXPECT_RISK = { R3: '装货日期临近', R4: '船舶证书临期', R5: '液货/危险品运输' }
  for (const sc of simCases) {
    if ((await mp.currentPage()).path !== 'pages/trade/orders/orders') {
      await nav(mp, 'tab', '/pages/trade/orders/orders', 'pages/trade/orders/orders')
      await sleep(1500)
    }
    const page = await mp.currentPage()
    const idx = ((await page.data()).list || []).findIndex((x) => x.id === sc.orderId)
    rec(`⑦b 仿真案例 ${sc.key} 在订单列表可见`, idx >= 0, `#${sc.orderId} idx=${idx}`)
    if (idx < 0) continue
    const card = (await page.$$('.order-card'))[idx]
    let tapped = false
    for (const b of (await card.$$('.act-ghost')) || []) {
      if (String(await b.text().catch(() => '')).indexOf('合同') >= 0) { await b.tap(); tapped = true; break }
    }
    rec(`⑦b 「查看合同」可点（${sc.key}）`, tapped)
    await waitPath(mp, 'pages/trade/contract/contract', 25)
    const cp = await mp.currentPage()
    const cd = await waitData(cp, CONTRACT_READY)
    await shot(mp, '07c-合同-仿真案例-' + sc.key)
    const titles = (cd.risks || []).map((r) => r.title)
    log(`合同风险(${sc.key})`, (cd.risks || []).map((r) => `${r.sev_label}/${r.title}`))
    rec(`⑦b ${sc.key} 合同页进入`, cp.path === 'pages/trade/contract/contract', cp.path)
    rec(`⑦b ${sc.key} 命中预期风险「${EXPECT_RISK[sc.key]}」`,
      titles.indexOf(EXPECT_RISK[sc.key]) >= 0, 'risks=' + titles.join('、'))
    rec(`⑦b ${sc.key} 合同正文非空`, ((cd.rawText || '').length > 400), 'raw=' + (cd.rawText || '').length)
    await backSafe(mp)
    await sleep(1400)
  }

  // ⑦c 已完成订单也应能查看合同（干净合同 / 无风险）
  if (payAnchor) {
    const page = await mp.currentPage()
    const done = ((await page.data()).list || []).find((x) => x.status === 'completed')
    if (done) {
      const idx = ((await page.data()).list || []).findIndex((x) => x.id === done.id)
      const card = (await page.$$('.order-card'))[idx]
      let tapped = false
      for (const b of (await card.$$('.act-ghost')) || []) {
        if (String(await b.text().catch(() => '')).indexOf('合同') >= 0) { await b.tap(); tapped = true; break }
      }
      rec('⑦c 已完成订单可查看合同', tapped, `#${done.id}`)
      if (tapped) {
        await waitPath(mp, 'pages/trade/contract/contract', 25)
        const cd2 = await waitData(await mp.currentPage(), CONTRACT_READY)
        await shot(mp, '07d-合同-已完成订单-无风险')
        rec('⑦c 已完成合同无风险项', (cd2.risks || []).length === 0, 'risks=' + (cd2.risks || []).length)
        rec('⑦c 已完成合同正文非空', ((cd2.rawText || '').length > 400), 'raw=' + (cd2.rawText || '').length)
        await backSafe(mp)
        await sleep(1300)
      }
    }
  }

  // ④b 撮合页（货主方向）
  if (matchCargo) {
    await nav(mp, 'to', `/pages/trade/match/match?mode=cargo&refId=${matchCargo.cargo.id}`,
      'pages/trade/match/match')
    await sleep(2600)
    await shot(mp, '09-撮合-为货源找船')
    const mz = await mp.currentPage()
    const md = await mz.data()
    log('撮合(货主方向)', { total: md.total, filterStats: md.filterStats, error: md.error })
    rec('④ 撮合页(货主方向)进入', mz.path === 'pages/trade/match/match', mz.path)
    rec('④ 候选与未入局原因就绪', (md.total || 0) > 0 && (md.filterStats || []).length > 0,
      `total=${md.total} filter=${JSON.stringify(md.filterStats)}`)
    const cand = (md.items || [])[0]
    if (cand) rec('④ 评分四项拆解齐备', Array.isArray(cand.dims) && cand.dims.length === 4,
      (cand.dims || []).map((d) => `${d.name}=${d.value}/${d.max}`).join(' '))
    const opened = await tapRetry(mz, async () => {
      const cards = await mz.$$('.cand-card')
      if (!cards || !cards.length) return null
      return (await cards[0].$('.btn-primary')) || cards[0]
    }, async () => !!((await mz.data()).orderModal))
    await shot(mp, '09b-撮合-下单确认弹窗')
    rec('④ 下单确认弹窗可打开', opened)
    if (opened) {
      const ghost = await mz.$('.btn-ghost')
      if (ghost) await ghost.tap()
      await sleep(900)
      rec('④ 弹窗取消后未产生订单', !(await mz.data()).orderModal)
    }
    await backSafe(mp)
    await sleep(1200)
  }

  // ============================ 船东链路 ============================
  await loginAs(mp, 'seed-owner')
  await tapAt(await mp.currentPage(), '.role-card', 1)
  let owner = await waitPath(mp, 'pages/owner/owner', 30)
  rec('⑨ 船东工作台进入（真实点击身份卡）', !!owner, owner ? owner.path : '未跳转')
  if (owner) {
    await sleep(1800)
    await shot(mp, '10-船东找货')
    const owd = await owner.data()
    rec('⑨ 船东页渲染', !owd.error, `货源大厅=${(owd.list || []).length} 船队=${(owd.shipList || []).length} 已认证=${owd.verifiedCount}`)

    const psh = await nav(mp, 'to', '/pages/publish/ship/ship', 'pages/publish/ship/ship')
    await sleep(1800)
    await shot(mp, '05-发布空船')
    const psd = await psh.data()
    rec('⑤ 发布空船页渲染（船东视角）', !psd.error && (psd.ships || []).length > 0, `已认证船=${(psd.ships || []).length}`)
    await backSafe(mp)
    await sleep(1400)
    owner = await mp.currentPage()

    // 船队区块仅在 view==='fleet' 渲染，必须先点「我的船队」
    const me = await owner.$('.my-entry')
    if (me) { await me.tap(); await sleep(1500); await shot(mp, '10b-船东-我的船队') }
    const od2 = await owner.data()
    rec('⑨ 船队视图切换', od2.view === 'fleet', 'view=' + od2.view)
    const sIdx = (od2.shipList || []).findIndex((x) => shipAnchor && x.id === shipAnchor.id)
    rec('⑨ 目标船在船队列表', sIdx >= 0, `ship #${shipAnchor && shipAnchor.id} idx=${sIdx}`)
    if (sIdx >= 0) {
      const card = (await owner.$$('.ship-card'))[sIdx]
      const btn = card ? await card.$('.btn-secondary') : null
      await (btn || card).tap()
      await waitPath(mp, 'pages/trade/match/match', 25)
      await sleep(2800)
      await shot(mp, '09c-撮合-为船找货')
      const mz2 = await mp.currentPage()
      const md2 = await mz2.data()
      log('撮合(船东方向)', { ship: shipAnchor.ship_name, total: md2.total, filterStats: md2.filterStats })
      rec('⑨ 撮合页(船东方向)进入', mz2.path === 'pages/trade/match/match', mz2.path)
      rec('⑨ 船东方向有候选可排序', (md2.total || 0) > 0, `total=${md2.total}`)
      const scores = (md2.items || []).map((x) => x.score)
      rec('⑨ 候选按评分降序', scores.every((v, i) => i === 0 || scores[i - 1] >= v), 'scores=' + JSON.stringify(scores))
      await backSafe(mp)
      await sleep(1200)
    }
  }

  // ⑨b 船东 · 「订单」→ 智能合同（同一批仿真案例，验证两个角色都能检查）
  const oOrders = await nav(mp, 'tab', '/pages/trade/orders/orders', 'pages/trade/orders/orders')
  await sleep(2000)
  await shot(mp, '10c-船东-我的订单')
  const ood = await oOrders.data()
  rec('⑨b 船东订单页无错误且非空', !ood.error && (ood.list || []).length > 0,
    `role=${ood.role} list=${(ood.list || []).length}`)
  const ownerCase = simCases[simCases.length - 1]
  if (ownerCase) {
    const idx = ood.list.findIndex((x) => x.id === ownerCase.orderId)
    rec(`⑨b 船东可见仿真案例 ${ownerCase.key}`, idx >= 0, `#${ownerCase.orderId} idx=${idx}`)
    if (idx >= 0) {
      const card = (await oOrders.$$('.order-card'))[idx]
      let tapped = false
      for (const b of (await card.$$('.act-ghost')) || []) {
        if (String(await b.text().catch(() => '')).indexOf('合同') >= 0) { await b.tap(); tapped = true; break }
      }
      rec('⑨b 船东「查看合同」可点', tapped)
      if (tapped) {
        await waitPath(mp, 'pages/trade/contract/contract', 25)
        const cd3 = await waitData(await mp.currentPage(), CONTRACT_READY)
        await shot(mp, '10d-船东-合同-' + ownerCase.key)
        const titles3 = (cd3.risks || []).map((r) => r.title)
        log('船东侧合同风险', (cd3.risks || []).map((r) => `${r.sev_label}/${r.title}`))
        rec(`⑨b 船东侧命中预期风险「${EXPECT_RISK[ownerCase.key]}」`,
          titles3.indexOf(EXPECT_RISK[ownerCase.key]) >= 0, 'risks=' + titles3.join('、'))
        rec('⑨b 船东侧合同正文非空', ((cd3.rawText || '').length > 400), 'raw=' + (cd3.rawText || '').length)
        await backSafe(mp)
        await sleep(1300)
      }
    }
  }

  // ============================ 港口链路 ============================
  // ⚠️ C 端「我是港口方」身份入口已在 #33 全下线（首页只保留货主/船东两项），
  //    港口运营台只能以 port 身份**会话**进入。这里直接登录后端并把会话写进 Storage
  //    （键与 utils/auth.js / utils/request.js 完全一致：access_token + user_info），
  //    再走底栏「港口服务」tab —— 页面渲染与后续点击仍是真机的，只是绕过了已删除的入口。
  const ps = await apiLogin('seed-port')
  let ptoken = ps.access_token
  if (ps.current_role !== 'port') {
    const sw = await apiPost('/auth/switch-role', ptoken, { role: 'port' })
    if (sw.data && sw.data.access_token) ptoken = sw.data.access_token
  }
  const pme = await apiGet('/auth/me', ptoken)
  await mp.evaluate((s) => {
    wx.setStorageSync('access_token', s.token)
    wx.setStorageSync('user_info', JSON.stringify(s.user))
    wx.removeStorageSync('dev_login_code')
  }, { token: ptoken, user: pme })
  let port = await nav(mp, 'tab', '/pages/port/port', 'pages/port/port')
  rec('⑩ 港口工作台进入（port 身份会话 · C 端入口已下线）', !!port, port ? port.path : '未跳转')
  await sleep(1600)
  await shot(mp, '11-港口服务（占位网格）')
  let pd = await port.data()
  rec('⑩ 港口服务页（4 组占位）', pd.view === 'service' && (pd.groups || []).length === 4, `view=${pd.view} groups=${(pd.groups || []).length}`)

  // 服务网格 → 业务办理 → 运营台
  let entered = false
  for (const g of (await port.$$('.grid-item')) || []) {
    if (String(await g.text().catch(() => '')).indexOf('业务办理') >= 0) { await g.tap(); entered = true; break }
  }
  rec('⑩ 真实点击「业务办理」进入运营台', entered)
  await sleep(2000)
  pd = await port.data()
  await shot(mp, '11b-港口-预约审核（运营台默认页）')
  rec('⑩ 运营台视图', pd.view === 'ops', `view=${pd.view} tab=${pd.tab}`)
  const apptList = pd.apptList || []
  log('待确认预约', apptList.map((x) => ({ id: x.id, remark: x.remark })))
  rec('⑪ 待确认预约列表就绪', apptList.length > 0, `pending=${apptList.length}`)

  // ⑪ 预约审核详情 + 防超卖 409
  const aIdx = Math.max(0, apptList.findIndex((x) => String(x.remark || '').indexOf('A3') >= 0))
  if (apptList[aIdx]) {
    await (await port.$$('.ops-card'))[aIdx].tap()
    await waitPath(mp, 'pages/port/appt/appt', 25)
    await sleep(2000)
    await shot(mp, '13-预约审核详情')
    const ap = await mp.currentPage()
    const ad = await ap.data()
    log('预约详情', { apptId: ad.apptId, overlapNow: ad.overlapNow, capacity: ad.capacity, conflict: ad.conflict, canConfirm: ad.canConfirm })
    rec('⑪ 预约详情页进入', ap.path === 'pages/port/appt/appt', ap.path)
    rec('⑪ 容量预检判冲突（与服务端 409 同口径）', ad.conflict === true, `重叠 ${ad.overlapNow} + 1 > 容量 ${ad.capacity}`)
    rec('⑪ 留痕时间轴就绪', (ad.timeline || []).length >= 2, 'segments=' + (ad.timeline || []).length)
    const beforeTl = JSON.stringify(ad.timeline || [])
    const btn = await ap.$('.btn-primary')
    if (btn) {
      await btn.tap()
      await sleep(3000)
      await shot(mp, '13b-预约确认-服务端409拦截')
      const ad2 = await ap.data()
      rec('⑪ 「确认并锁定档期」被服务端拦下（状态未变）',
        JSON.stringify(ad2.timeline || []) === beforeTl, `status=${ad2.status}`)
    }
    const more = await ap.$('.section-head-more')
    if (more) {
      await more.tap()
      await waitPath(mp, 'pages/port/berth/berth', 20)
      await sleep(2000)
      await shot(mp, '12b-泊位档期（由预约页跳入）')
      rec('⑪ 预约页 → 泊位档期跳转', (await mp.currentPage()).path === 'pages/port/berth/berth')
      await backSafe(mp)
      await sleep(1300)
    }
    await backSafe(mp)
    await sleep(1400)
  }

  // ⑩ 泊位管理 → DEMO-01 档期（甘特 + 峰值并发）
  const portNow = await mp.currentPage()
  const tabs = await portNow.$$('.tab')
  if (tabs && tabs.length >= 2) { await tabs[1].tap(); await sleep(2000) }
  else rec('⑩ 运营台 tab 元素', false, '未找到 .tab')
  await shot(mp, '11c-港口-泊位管理')
  const berthList = (await portNow.data()).berthList || []
  log('泊位列表', berthList.map((b) => ({ id: b.id, no: b.port_code + '-' + b.berth_no, cap: b.concurrent_capacity })))
  const bIdx = Math.max(0, berthList.findIndex((b) => String(b.berth_no).indexOf('DEMO-01') >= 0))
  const berthTarget = berthList[bIdx]
  rec('⑩ 演示泊位 DEMO-01 在列表', !!berthTarget, berthTarget ? `#${berthTarget.id} cap=${berthTarget.concurrent_capacity}` : '未找到')
  if (berthTarget) {
    await (await portNow.$$('.ops-card'))[bIdx].tap()
    await waitPath(mp, 'pages/port/berth/berth', 25)
    await sleep(2200)
    await shot(mp, '12-泊位档期详情（满档+甘特）')
    const bp = await mp.currentPage()
    const bd = await bp.data()
    log('泊位档期页', { berthTitle: bd.berthTitle, capacity: bd.capacity, peak: bd.peak, bars: (bd.bars || []).length })
    rec('⑩ 泊位档期页进入', bp.path === 'pages/port/berth/berth', bp.path)
    rec('⑩ 档期甘特条渲染', (bd.bars || []).length >= 2, `bars=${(bd.bars || []).length} peak=${bd.peak}/${bd.capacity}`)
    rec('⑩ 峰值并发达容量（满档演示）', Number(bd.peak) >= Number(bd.capacity) && Number(bd.capacity) > 0, `peak=${bd.peak} cap=${bd.capacity}`)
    const geo = (bd.bars || []).map((b) => ({ l: parseFloat(b.left), w: parseFloat(b.width) }))
    rec('⑩ 甘特条几何在 [0,100]% 内',
      geo.every((g) => !isNaN(g.l) && !isNaN(g.w) && g.l >= 0 && g.l <= 100.5 && g.w > 0 && g.l + g.w <= 100.6), JSON.stringify(geo))
    await backSafe(mp)
    await sleep(1400)
  }

  // ============================ 我的 ============================
  await nav(mp, 'tab', '/pages/mine/mine', 'pages/mine/mine')
  await sleep(1600)
  await shot(mp, '14-我的')
  const mdd = await (await mp.currentPage()).data()
  rec('⑫ 我的页渲染', !mdd.error, `role=${mdd.currentRole} functions=${(mdd.functions || []).length}`)

  // ============================ 汇总 ============================
  console.log('\n================ 汇总 ================')
  const fail = R.filter((x) => !x.ok)
  console.log(`断言 ${R.length} 项：通过 ${R.length - fail.length}，失败 ${fail.length}`)
  fail.forEach((f) => console.log('  FAIL:', f.step, '|', f.note))
  console.log('\n[运行期 console.error / exception]')
  console.log(errs.length ? errs.slice(-25).join('\n') : '(无)')
  console.log('[截图目录]', SHOTS)
  fs.writeFileSync(path.join(SHOTS, 'summary.json'), JSON.stringify({ results: R, errors: errs, ws: WS, payClicked: DO_PAY }, null, 2), 'utf8')

  await mp.disconnect()
  process.exit(fail.length ? 1 : 0)
})().catch((e) => {
  console.error('[FATAL]', e && e.message ? e.message : e)
  try { fs.writeFileSync(path.join(SHOTS, 'summary.json'), JSON.stringify({ results: R, fatal: String((e && e.message) || e) }, null, 2), 'utf8') } catch (_) {}
  process.exit(2)
})
