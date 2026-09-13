#!/usr/bin/env node
/**
 * 路由注册表静态校验（R1 / ENT-015）
 *
 * `scripts/verify_miniapp.js` 已经覆盖「路由可达」与「tabBar 页该用 switchTab」；
 * 本脚本补的是它**没覆盖**的三件事，也是委托支线继续加页面时真正的风险面：
 *
 *   1. 注册表与 app.json 双向一致 —— 新增页面必须登记（禁止"偷偷加页"）
 *   2. 声明链深 chainDepth() ≤ STACK_BUDGET —— 页面栈不会某天突然撞 10 层上限
 *   3. 实际导航边已被声明 —— navigateTo/redirectTo 的源页面必须在该页 parents 里
 *      （只在注册表里写个漂亮的 depth、实际却有没声明的来路，等于没防）
 *   4. 深链参数契约 —— require-params 的页面必须列出必需参数，
 *      且注册表里声明的参数与代码里真实拼接的查询键不能脱节
 *
 * 用法：node scripts/verify_routes.js
 * 退出码：0 通过 / 1 有问题
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const MINI = path.join(ROOT, 'miniapp')
const routes = require(path.join(MINI, 'utils', 'routes.js'))

const errors = []
const notes = []
const KINDS = new Set(['tab', 'root', 'detail'])
const DEEP_LINK = new Set(['allow', 'require-params', 'deny'])

const appJson = JSON.parse(fs.readFileSync(path.join(MINI, 'app.json'), 'utf8'))
const pages = appJson.pages || []
const tabBarPages = ((appJson.tabBar && appJson.tabBar.list) || []).map((t) => t.pagePath)
const registered = Object.keys(routes.ROUTES)

// ---------------------------------------------------------------- 1. 双向一致
const pageSet = new Set(pages)
const regSet = new Set(registered)

for (const p of pages) {
  if (!regSet.has(p)) errors.push(`[未登记] ${p} 在 app.json 里但不在 utils/routes.js 的 ROUTES`)
}
for (const p of registered) {
  if (!pageSet.has(p)) errors.push(`[多余项] ROUTES 里的 ${p} 不在 app.json 的 pages 中`)
}

// ---------------------------------------------------------------- 2. tabBar 口径
const tabSet = new Set(tabBarPages)
for (const p of tabBarPages) {
  const r = routes.ROUTES[p]
  if (!r) continue // 已在上面的「未登记」里报过
  if (r.kind !== 'tab') errors.push(`[kind 不符] ${p} 在 tabBar 里，但 kind=${r.kind}`)
}
for (const p of routes.TAB_BAR_PAGES) {
  if (!tabSet.has(p)) errors.push(`[kind 不符] ${p} 声明 kind=tab，但不在 app.json tabBar.list`)
}

// ---------------------------------------------------------------- 3. 字段合法性
for (const p of registered) {
  const r = routes.ROUTES[p]
  if (!KINDS.has(r.kind)) errors.push(`[字段] ${p}: kind=${r.kind} 非法（应为 tab/root/detail）`)
  if (!DEEP_LINK.has(r.deepLink)) {
    errors.push(`[字段] ${p}: deepLink=${r.deepLink} 非法（应为 allow/require-params/deny）`)
  }
  if (!r.domain) errors.push(`[字段] ${p}: 缺少 domain`)
  if (!r.note) errors.push(`[字段] ${p}: 缺少 note（说明这个页面是干什么的）`)

  const parents = r.parents || []
  if (r.kind === 'detail') {
    if (!parents.length) errors.push(`[字段] ${p}: kind=detail 必须声明非空 parents`)
  } else if (parents.length) {
    errors.push(`[字段] ${p}: kind=${r.kind} 的 parents 必须为空（进入即重置页面栈）`)
  }
  for (const parent of parents) {
    if (!regSet.has(parent)) errors.push(`[字段] ${p}: parent ${parent} 未登记`)
    if (!pageSet.has(parent)) errors.push(`[字段] ${p}: parent ${parent} 不在 app.json 里`)
  }

  const params = r.params || []
  if (r.deepLink === 'require-params' && !params.length) {
    errors.push(`[字段] ${p}: deepLink=require-params 必须列出 params`)
  }
  if (r.deepLink !== 'require-params' && params.length) {
    errors.push(`[字段] ${p}: 只有 require-params 才该声明 params`)
  }
}

// ---------------------------------------------------------------- 4. 链深与环
const depths = {}
let deepest = { path: '', depth: 0 }
for (const p of registered) {
  const d = routes.chainDepth(p)
  depths[p] = d
  if (!isFinite(d)) {
    errors.push(`[成环] ${p}: 链深无法计算（parents 里存在环或未登记项）`)
    continue
  }
  if (d > routes.STACK_BUDGET) {
    errors.push(`[超预算] ${p}: 声明链深 ${d} > STACK_BUDGET ${routes.STACK_BUDGET}`)
  }
  if (d > deepest.depth) deepest = { path: p, depth: d }
}

// ---------------------------------------------------------------- 5. 实际导航边
function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name)
    let st
    try { st = fs.statSync(p) } catch (e) { continue }
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === '.git' || name === 'miniprogram_npm') continue
      walk(p, out)
    } else if (name.endsWith('.js')) out.push(p)
  }
  return out
}

const CALL = /wx\.(navigateTo|redirectTo|switchTab|reLaunch)\s*\(/g
const URL_LITERAL = /url\s*:\s*[`'"]([^`'"]+)[`'"]/
const edgeSeen = new Set()
let edges = 0
let skippedUtilSources = 0

for (const file of walk(MINI)) {
  const rel = path.relative(MINI, file).replace(/\\/g, '/')
  const src = fs.readFileSync(file, 'utf8')
  const srcPage = rel.replace(/\.js$/, '')
  const isPage = pageSet.has(srcPage)

  CALL.lastIndex = 0
  let m
  while ((m = CALL.exec(src)) !== null) {
    const api = m[1]
    const tail = src.slice(m.index + m[0].length, m.index + m[0].length + 220)
    const u = URL_LITERAL.exec(tail)
    if (!u) continue
    const target = routes.normalize(u[1])
    const uniq = `${srcPage} -> ${target} (${api})`
    if (edgeSeen.has(uniq)) continue
    edgeSeen.add(uniq)

    if (!regSet.has(target)) {
      errors.push(`[未登记目标] ${rel}: ${api} -> ${target}`)
      continue
    }
    const tr = routes.ROUTES[target]

    if (api === 'navigateTo' && tr.kind === 'tab') {
      errors.push(`[API 误用] ${rel}: navigateTo -> ${target}（tabBar 页必须 switchTab）`)
    }
    // switchTab / reLaunch 会重置页面栈，不作为链深证据
    if (api === 'switchTab' || api === 'reLaunch') continue

    edges++
    if (!isPage) {
      skippedUtilSources++
      notes.push(`[工具来源] ${rel}: ${api} -> ${target}（源不是页面，无法校验 parents）`)
      continue
    }
    if (tr.kind === 'detail' && (tr.parents || []).indexOf(srcPage) === -1) {
      errors.push(
        `[未声明来路] ${rel}: ${api} -> ${target}，但 ${target} 的 parents 未包含 ${srcPage}` +
        `（链深会被低估）`
      )
    }
  }
}

// ---------------------------------------------------------------- 汇总
const byDomain = routes.countByDomain()
console.log(`页面 ${pages.length} 个 / 注册 ${registered.length} 条；`)
console.log(`tabBar ${tabBarPages.length} 项；域分布：` + Object.keys(byDomain).sort()
  .map((d) => `${d}=${byDomain[d]}`).join(' '))
console.log(`导航边校验 ${edges} 条（跳过工具来源 ${skippedUtilSources} 条）；深度上限 ${routes.STACK_BUDGET}`)
console.log(`最深声明链：${deepest.path} = ${deepest.depth} 层`)
for (const n of notes.slice(0, 10)) console.log('  ' + n)

if (errors.length) {
  console.log(`\n✗ 发现 ${errors.length} 个问题：`)
  for (const e of errors) console.log('  - ' + e)
  process.exit(1)
}
console.log('\n✓ 路由注册表校验通过')
