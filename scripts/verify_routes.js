#!/usr/bin/env node
/**
 * 路由注册表静态校验（R1 / ENT-015 → ENT-018）
 *
 * `scripts/verify_miniapp.js` 已经覆盖「路由可达」与「tabBar 页该用 switchTab」；
 * 本脚本补的是它**没覆盖**的事，也是委托支线继续加页面时真正的风险面：
 *
 *   1. 注册表与 app.json 双向一致 —— 新增页面必须登记（禁止"偷偷加页"）
 *   2. 声明链深 chainDepth() ≤ STACK_BUDGET —— 页面栈不会某天突然撞 10 层上限
 *   3. 导航边契约（ENT-018 重点）
 *      a. 代码里真实存在的每条导航边，都必须在 NAV_EDGES 里声明过
 *      b. 声明的 push / replace 边**必须有代码证据**，没有就必须标 `pending` 并写明理由
 *         —— 这条是为了让「声明」与「事实」不再混为一谈（HO 第 4 条）
 *      c. 声明的策略必须与真实调用的 API 一致（navigateTo↔push / redirectTo↔replace…）
 *      d. `reuse` / `back` 边的目标必须有 push 或 replace 入边，否则永远进不去
 *      e. push+replace 图里不得成环（成环 ⇒ 链深不可判定）
 *   4. 深链参数契约 —— `paramSchema` 必须覆盖 `deepLink` 的强度：
 *      require-params ⇒ 至少一个 required；deny ⇒ 不得声明参数；其余 ⇒ 只能是可选参数
 *   5. 运行期治理接入（ENT-019）
 *      a. `MIGRATED_PAGES` 名单内的页面**不得**再出现裸 `wx.navigateTo / redirectTo /
 *         switchTab / reLaunch` —— 跳转必须经 `go()`，否则页面栈预算与深链契约重新
 *         退化成"只在 CI 里成立的声明"
 *      b. 名单内的页面必须真的出现 `go(` 调用（防"名单写了、代码没接"）
 *      c. `go(<字面量 url>)` 与 `wx.*` 一样计入**导航边证据**，但它**不携带策略**：
 *         策略由 `NAV_EDGES` 的声明决定（这正是 ENT-018 分离层级与导航边之后
 *         「边是唯一真相」的含义），所以对 `go` 证据不做 3.c 的策略比对
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
const PARAM_TYPES = new Set(['id', 'int', 'enum', 'string'])
const STRATEGIES = new Set(routes.STRATEGY_VALUES)
// 真实调用 API ↔ 声明策略
const API_STRATEGY = {
  navigateTo: routes.STRATEGY.PUSH,
  redirectTo: routes.STRATEGY.REPLACE,
  switchTab: routes.STRATEGY.SWITCH_TAB,
  reLaunch: routes.STRATEGY.RESET
}

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

// ---------------------------------------------------------------- 3. 字段与参数契约
/**
 * 参数契约三态：
 *   require-params ⇒ 至少一个 required 参数
 *   deny           ⇒ 不得声明任何参数（深链一律拒，声明参数只会误导）
 *   allow          ⇒ 只允许可选参数（required 参数必须显式标 deepLinkOnly）
 */
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
    if (!parents.length) {
      errors.push(`[字段] ${p}: kind=detail 必须至少有一条 push 入边（见 NAV_EDGES）`)
    }
  } else if (parents.length) {
    errors.push(`[字段] ${p}: kind=${r.kind} 不应有 push 入边（进入即重置页面栈）`)
  }
  for (const parent of parents) {
    if (!regSet.has(parent)) errors.push(`[字段] ${p}: push 来源 ${parent} 未登记`)
    if (!pageSet.has(parent)) errors.push(`[字段] ${p}: push 来源 ${parent} 不在 app.json 里`)
  }

  const schema = r.paramSchema || {}
  const hasRequired = Object.keys(schema).some((k) => schema[k].required)
  for (const k of Object.keys(schema)) {
    const spec = schema[k] || {}
    if (!PARAM_TYPES.has(spec.type)) {
      errors.push(`[字段] ${p}: 参数 ${k} 的 type=${spec.type} 非法（应为 ${[...PARAM_TYPES].join('/')}）`)
    }
    if (spec.type === 'enum' && !(spec.values || []).length) {
      errors.push(`[字段] ${p}: 参数 ${k} 声明为 enum 但没有 values`)
    }
    if (spec.type !== 'enum' && (spec.values || []).length) {
      errors.push(`[字段] ${p}: 参数 ${k} 只有 enum 才能声明 values`)
    }
    if (spec.required !== true && spec.required !== false) {
      errors.push(`[字段] ${p}: 参数 ${k} 的 required 必须是布尔值（显式写清，别靠默认）`)
    }
  }
  for (const k of r.keyParams || []) {
    if (!Object.prototype.hasOwnProperty.call(schema, k)) {
      errors.push(`[字段] ${p}: keyParams 里的 ${k} 不在 paramSchema 中`)
    }
  }
  for (const c of r.keyContext || []) {
    if (c !== 'org') errors.push(`[字段] ${p}: keyContext 只支持 'org'，收到 ${c}`)
  }
  if (r.requiresOrg && (r.keyContext || []).indexOf('org') === -1) {
    errors.push(`[字段] ${p}: requiresOrg=true 但 keyContext 未声明 'org'（两处口径必须一致）`)
  }

  if (r.deepLink === 'require-params') {
    if (!hasRequired) errors.push(`[深链契约] ${p}: deepLink=require-params 必须至少有一个 required 参数`)
  } else if (r.deepLink === 'deny') {
    if (Object.keys(schema).length) errors.push(`[深链契约] ${p}: deepLink=deny 不得声明参数`)
  } else if (hasRequired) {
    const notDeepLinkOnly = Object.keys(schema).filter((k) => schema[k].required && !schema[k].deepLinkOnly)
    if (notDeepLinkOnly.length) {
      errors.push(
        `[深链契约] ${p}: deepLink=allow 时 required 参数必须标 deepLinkOnly（否则会把应用内导航一并拦掉）：` +
        notDeepLinkOnly.join(', ')
      )
    }
  }
}

// ---------------------------------------------------------------- 4. 导航边（声明侧）
const edgeKey = (from, to) => `${from} -> ${to}`
const declared = new Map()
for (const e of routes.NAV_EDGES) {
  const label = `[边] ${edgeKey(e.from, e.to)}`
  if (!regSet.has(e.from)) errors.push(`${label}: from 未登记`)
  if (!regSet.has(e.to)) errors.push(`${label}: to 未登记`)
  if (e.from === e.to) errors.push(`${label}: 自环`)
  if (!STRATEGIES.has(e.strategy)) errors.push(`${label}: strategy=${e.strategy} 非法`)
  const k = edgeKey(e.from, e.to)
  if (declared.has(k)) errors.push(`${label}: 重复声明`)
  declared.set(k, e)
  // 只有 replace / back / reuse 需要解释"为什么不进下一级"；
  // switchTab / reset 本身即自解释（重置栈），不再要求写 reason。
  const NEEDS_REASON = [routes.STRATEGY.REPLACE, routes.STRATEGY.BACK, routes.STRATEGY.REUSE]
  if (NEEDS_REASON.indexOf(e.strategy) !== -1 && !e.reason) {
    errors.push(`${label}: strategy=${e.strategy} 必须写 reason（说明为什么不算"进下一级"）`)
  }
  // 策略 ↔ 目标类型
  if (STRATEGY_TARGET_TAB_INCLUDES(e.strategy) && routes.ROUTES[e.to] && routes.ROUTES[e.to].kind !== 'tab') {
    errors.push(`${label}: strategy=${e.strategy} 的目标必须是 tabBar 页，但 ${e.to} 的 kind=${routes.ROUTES[e.to].kind}`)
  }
  if (STRATEGY_TARGET_RESET_INCLUDES(e.strategy) && routes.ROUTES[e.to] && !['tab', 'root'].includes(routes.ROUTES[e.to].kind)) {
    errors.push(`${label}: strategy=${e.strategy} 的目标必须是 tab/root，但 ${e.to} 的 kind=${routes.ROUTES[e.to].kind}`)
  }
}
function STRATEGY_TARGET_TAB_INCLUDES(s) {
  return routes.STRATEGY_TARGET_TAB.indexOf(s) !== -1
}
function STRATEGY_TARGET_RESET_INCLUDES(s) {
  return routes.STRATEGY_TARGET_TAB_OR_ROOT.indexOf(s) !== -1
}

// `back` / `reuse` 的目标必须有 push 或 replace 入边，否则永远进不去
for (const e of routes.NAV_EDGES) {
  if ([routes.STRATEGY.BACK, routes.STRATEGY.REUSE].indexOf(e.strategy) === -1) continue
  const canEnter = routes.NAV_EDGES.some(
    (x) => x.to === e.to && [routes.STRATEGY.PUSH, routes.STRATEGY.REPLACE].indexOf(x.strategy) !== -1
  )
  if (!canEnter) {
    errors.push(`[边] ${edgeKey(e.from, e.to)}: strategy=${e.strategy} 但 ${e.to} 没有任何 push/replace 入边，永远无法进入`)
  }
}

// ---------------------------------------------------------------- 5. 链深与环
const depths = {}
let deepest = { path: '', depth: 0 }
for (const p of registered) {
  const d = routes.chainDepth(p)
  depths[p] = d
  if (!isFinite(d)) {
    errors.push(`[成环] ${p}: 链深无法计算（push/replace 边里存在环或未登记来源）`)
    continue
  }
  if (d > routes.STACK_BUDGET) {
    errors.push(`[超预算] ${p}: 声明链深 ${d} > STACK_BUDGET ${routes.STACK_BUDGET}`)
  }
  if (d > deepest.depth) deepest = { path: p, depth: d }
}

// ---------------------------------------------------------------- 6. 实际导航边（代码侧）
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

/**
 * 从 `(` 开始截取**括号配对**的实参列表。
 * 初版只往后取固定 220 字符，既会把后面无关的 `url:` 误当成这次调用的目标，
 * 也可能漏掉写得更长的调用——两个方向都不可靠。
 */
function callArgs(src, openIdx) {
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

const CALL = /wx\.(navigateTo|redirectTo|switchTab|reLaunch)\s*\(/g
const URL_LITERAL = /url\s*:\s*[`'"]([^`'"]+)[`'"]/

/**
 * `go(<url>)` / `R.go(<url>)` —— ENT-019 起页面统一走的导航入口。
 *
 * 与 `wx.*` 的调用不同，`go()` 的第一个参数是**裸 url 字符串**而不是 `{url: …}`，
 * 所以不能用 URL_LITERAL 取目标；也不能要求它是纯字面量：实际写法是
 * `go('/pages/entrust/detail/detail?assignment_id=' + encodeURIComponent(id))`。
 * 这里取**开头那段字面量**（截到 `?` 之前）作为目标；若拼出来的路径不在注册表里
 * 且不是纯字面量，就按"变量目标"记备注，不误报。
 */
const GO_CALL = /(?:^|[^\w$])go\s*\(/g
const PURE_LITERAL = /^[`'"]([^`'"]+)[`'"]$/
const LITERAL_PREFIX = /^[`'"](\/pages\/[^`'"?]*)/

/**
 * 把注释替换成**等长空白**（保持字符下标不变，便于继续按 index 取源码）。
 *
 * 为什么必须做：`go()` 是"文档里也会被提到的东西"——注释里写 `` `go()` ``
 * 是很自然的写法。不剥注释就会把文档当调用：实测首版把 11 处注释里的 `go()`
 * 记成了"变量目标"备注，把真正的调用淹在里面，而备注本是给人看异常用的。
 *
 * 字符串与正则字面量里的 `//`、`/*` 不能被当成注释，所以走状态机；
 * 正则起始用「前一个有意义字符」启发式判断（`( , = : [ { ; ! & | ?` 之后）。
 */
function stripComments(src) {
  const out = src.split('')
  const RE_START_AFTER = '([{,;:=!&|?'
  let i = 0
  let prev = ''
  while (i < src.length) {
    const ch = src[i]
    const next = src[i + 1]
    if (ch === "'" || ch === '"' || ch === '`') {
      i++
      while (i < src.length && src[i] !== ch) {
        if (src[i] === '\\') i++
        i++
      }
      i++
      prev = ch
      continue
    }
    if (ch === '/' && (next === '/' || next === '*')) {
      const line = next === '/'
      out[i] = ' '
      out[i + 1] = ' '
      i += 2
      while (i < src.length) {
        if (line && src[i] === '\n') break
        if (!line && src[i] === '*' && src[i + 1] === '/') {
          out[i] = ' '
          out[i + 1] = ' '
          i += 2
          break
        }
        if (src[i] !== '\n') out[i] = ' '
        i++
      }
      continue
    }
    if (ch === '/' && (prev === '' || RE_START_AFTER.indexOf(prev) !== -1)) {
      // 正则字面量：整体当代码保留，只把内容跳过（避免其中的 `/` 被误判成注释）
      i++
      let inClass = false
      while (i < src.length) {
        const c = src[i]
        if (c === '\\') {
          i += 2
          continue
        }
        if (c === '\n') break
        if (c === '[') inClass = true
        else if (c === ']') inClass = false
        else if (c === '/' && !inClass) {
          i++
          break
        }
        i++
      }
      prev = '/'
      continue
    }
    if (ch !== ' ' && ch !== '\t' && ch !== '\n' && ch !== '\r') prev = ch
    i++
  }
  return out.join('')
}

/** 取实参列表里的**第 1 个顶层实参**（入参是含最外层括号的 callArgs 结果） */
function firstArg(argsText) {
  const inner = argsText.slice(1, -1)
  let depth = 0
  let i = 0
  for (; i < inner.length; i++) {
    const ch = inner[i]
    if (ch === "'" || ch === '"' || ch === '`') {
      const q = ch
      i++
      while (i < inner.length && inner[i] !== q) {
        if (inner[i] === '\\') i++
        i++
      }
      continue
    }
    if (ch === '(' || ch === '[' || ch === '{') depth++
    else if (ch === ')' || ch === ']' || ch === '}') depth--
    else if (ch === ',' && depth === 0) break
  }
  return inner.slice(0, i).trim()
}

const evidence = new Map() // `${from} -> ${to}` -> Set(api)
const dynamicTargets = []
const utilSources = []
const goTargets = []
let scannedCalls = 0

for (const file of walk(MINI)) {
  const rel = path.relative(MINI, file).replace(/\\/g, '/')
  const raw = fs.readFileSync(file, 'utf8')
  // 扫描用「剥掉注释的同长副本」：注释里提到 go() / wx.navigateTo 是自然写法，
  // 不剥就会把文档当调用（下标与原文一致，故后续仍可回原文取内容）。
  const src = stripComments(raw)
  const srcPage = rel.replace(/\.js$/, '')
  const isPage = pageSet.has(srcPage)

  CALL.lastIndex = 0
  let m
  while ((m = CALL.exec(src)) !== null) {
    const api = m[1]
    scannedCalls++
    const args = callArgs(src, m.index + m[0].length - 1)
    const u = URL_LITERAL.exec(args)
    if (!u) {
      // 目标由变量决定（如 ROLE_META[role].page）——静态解析不了，记为备注
      if (isPage) dynamicTargets.push(`${rel}: ${api} -> (变量目标)`)
      continue
    }
    const target = routes.normalize(u[1])
    if (!regSet.has(target)) {
      errors.push(`[未登记目标] ${rel}: ${api} -> ${target}`)
      continue
    }
    const tr = routes.ROUTES[target]

    if (api === 'navigateTo' && tr.kind === 'tab') {
      errors.push(`[API 误用] ${rel}: navigateTo -> ${target}（tabBar 页必须 switchTab）`)
    }

    if (!isPage) {
      utilSources.push(`${rel}: ${api} -> ${target}`)
      continue
    }
    const k = edgeKey(srcPage, target)
    if (!evidence.has(k)) evidence.set(k, new Set())
    evidence.get(k).add(api)
    // switchTab / reLaunch 会重置栈，同样要在 NAV_EDGES 里声明（便于看"谁能到这个页面"）
    const want = API_STRATEGY[api]
    const dec = declared.get(k)
    if (!dec) {
      errors.push(
        `[未声明边] ${rel}: ${api} -> ${target}；请在 NAV_EDGES 补 ` +
        `{ from: '${srcPage}', to: '${target}', strategy: '${want}' }`
      )
    } else if (dec.strategy !== want) {
      errors.push(
        `[策略不符] ${edgeKey(srcPage, target)}: 代码用 ${api}（应为 ${want}），` +
        `但 NAV_EDGES 声明为 ${dec.strategy}`
      )
    }
  }

  // ── go() 调用（ENT-019）：同样算导航边证据，但**不携带策略** ──
  GO_CALL.lastIndex = 0
  while ((m = GO_CALL.exec(src)) !== null) {
    // 跳过函数定义本身（`function go(url, opts) {`）
    if (/function\s*$/.test(src.slice(Math.max(0, m.index - 24), m.index))) continue
    const a1 = firstArg(callArgs(src, m.index + m[0].length - 1))
    // 空实参的 `go()` 不可能是真实调用（注释已剥）。若真调用取不到参数，
    // 证据会缺失 → 上面的「无证据边」检查会红，所以这里静默跳过是安全的。
    if (!a1) continue
    const prefix = LITERAL_PREFIX.exec(a1)
    if (!prefix) {
      if (isPage) goTargets.push(`${rel}: go(${a1.slice(0, 48)})`)
      continue
    }
    const target = routes.normalize(prefix[1])
    const pure = PURE_LITERAL.exec(a1)
    if (!regSet.has(target)) {
      // 半动态拼接（`'/pages/x/y?id=' + encodeURIComponent(id)`）解析不出已登记路由时
      // 按变量目标记备注；纯字面量则是真错。
      if (pure) errors.push(`[未登记目标] ${rel}: go -> ${target}`)
      else if (isPage) goTargets.push(`${rel}: go(${a1.slice(0, 48)})`)
      continue
    }
    if (!isPage) {
      utilSources.push(`${rel}: go -> ${target}`)
      continue
    }
    if (routes.ROUTES[target].kind === 'tab') {
      errors.push(`[API 误用] ${rel}: go -> ${target}（tabBar 页必须经 go() 的 switchTab 分支，不能直传）`)
    }
    const k = edgeKey(srcPage, target)
    if (!evidence.has(k)) evidence.set(k, new Set())
    evidence.get(k).add('go')
    if (!declared.get(k)) {
      errors.push(
        `[未声明边] ${rel}: go -> ${target}；请在 NAV_EDGES 补 ` +
        `{ from: '${srcPage}', to: '${target}', strategy: '<按语义选 push/replace/…>' }`
      )
    }
    // 注意：这里**刻意不比对策略** —— go() 的目标只说明"要去哪"，用哪个导航 API
    // 由 NAV_EDGES 的声明决定（ENT-018「边是唯一真相」）。比对留给上面的 wx.* 分支。
  }
}

// 声明的 push / replace 边必须有代码证据；没有就得标 pending
for (const e of routes.NAV_EDGES) {
  const k = edgeKey(e.from, e.to)
  const ev = evidence.get(k)
  const needsEvidence = [routes.STRATEGY.PUSH, routes.STRATEGY.REPLACE].indexOf(e.strategy) !== -1
  if (needsEvidence) {
    if (!ev && !e.pending) {
      errors.push(
        `[无证据边] ${k}: 声明为 ${e.strategy} 但代码里找不到对应调用。` +
        `若是有意先行声明，请补 pending 字段写明何时生效；否则删掉这条边。`
      )
    }
    if (ev && e.pending) {
      errors.push(`[过期 pending] ${k}: 已有代码证据（${[...ev].join(',')}），pending 标记应当移除`)
    }
  } else if (!ev && !e.pending) {
    notes.push(`[无代码证据] ${k}: strategy=${e.strategy}（该策略本就无法由静态扫描证明）`)
  }
}

// ---------------------------------------------------------------- 7. 已接入运行期治理
// ENT-019：`MIGRATED_PAGES` 是「哪些页面真的走 go()」的可核对名单。
// 没有这一节，名单只是一行注释 —— 有人把页面的 go() 改回 wx.navigateTo 也不会有人发现。
//
// 允许 `wx.navigateBack`：「返回上一页」的目标是**动态的**（谁把我推进来的就还给谁），
// 注册表里没有也不该有这样一条边，所以它不参与治理。
const BARE_NAV = /wx\s*\.\s*(navigateTo|redirectTo|switchTab|reLaunch)\s*\(/g
const HAS_GO = /(?:^|[^\w$])go\s*\(/
for (const p of routes.MIGRATED_PAGES) {
  if (!regSet.has(p)) {
    errors.push(`[已接入] ${p}: 在 MIGRATED_PAGES 里但未登记进 ROUTES`)
    continue
  }
  const file = path.join(MINI, p + '.js')
  if (!fs.existsSync(file)) {
    errors.push(`[已接入] ${p}: 声明已接入运行期治理，但 ${p}.js 不存在`)
    continue
  }
  const src = stripComments(fs.readFileSync(file, 'utf8'))
  BARE_NAV.lastIndex = 0
  let b
  while ((b = BARE_NAV.exec(src)) !== null) {
    errors.push(
      `[已接入] ${p}: 仍有裸 wx.${b[1]}( —— 名单内的页面跳转必须经 go()` +
      `（否则页面栈预算与深链契约在运行期重新失效）`
    )
  }
  if (!HAS_GO.test(src)) {
    errors.push(
      `[已接入] ${p}: 名单里声明已接入运行期治理，但代码里找不到 go( 调用` +
      `（要么真的没接，要么名单写早了）`
    )
  }
  if (!routes.ROUTES[p].paramSchema && routes.ROUTES[p].deepLink !== 'allow') {
    errors.push(`[已接入] ${p}: 已接入守卫但未声明 paramSchema（deepLink=${routes.ROUTES[p].deepLink}）`)
  }
}

// ---------------------------------------------------------------- 汇总
const byDomain = routes.countByDomain()
const pendings = routes.NAV_EDGES.filter((e) => e.pending)
console.log(`页面 ${pages.length} 个 / 注册 ${registered.length} 条；`)
console.log(`tabBar ${tabBarPages.length} 项；域分布：` + Object.keys(byDomain).sort()
  .map((d) => `${d}=${byDomain[d]}`).join(' '))
console.log(
  `导航边：声明 ${routes.NAV_EDGES.length} 条 / 代码证据 ${evidence.size} 条 / ` +
  `待生效 ${pendings.length} 条；扫描调用 ${scannedCalls} 次`
)
console.log(
  `运行期治理：已接入 ${routes.MIGRATED_PAGES.length}/${registered.length} 页 ` +
  `（${routes.MIGRATED_PAGES.join(' / ')}；未接入的页面按 HO 第 4 条保持现状）`
)
const hist = {}
for (const e of routes.NAV_EDGES) hist[e.strategy] = (hist[e.strategy] || 0) + 1
console.log('  策略分布：' + Object.keys(hist).sort().map((s) => `${s}=${hist[s]}`).join(' '))
console.log(`深度上限 ${routes.STACK_BUDGET}（平台硬限 ${routes.MAX_STACK}）`)
console.log(`最深声明链：${deepest.path} = ${deepest.depth} 层`)
console.log(`非 push 的最深页：` + Object.keys(depths)
  .filter((p) => isFinite(depths[p]) && routes.ROUTES[p].kind === 'detail')
  .sort((a, b) => depths[b] - depths[a]).slice(0, 3)
  .map((p) => `${p}=${depths[p]}`).join(' / '))
for (const e of pendings) console.log(`  [待生效] ${edgeKey(e.from, e.to)} (${e.strategy}) —— ${e.pending}`)
for (const n of notes.slice(0, 10)) console.log('  ' + n)
for (const d of dynamicTargets) console.log('  [变量目标] ' + d)
// 「变量目标」是**中立备注**（记录有哪些动态调用），不是判错。
// 其中 `switchTab` 的取值域由 `verify_tabbar_targets.js` 驱动真实页面逻辑验：
// 捕获的每个目标都必须落在 app.json 的 tabBar.list 内（非 tabBar 页会静默失败）。
if (dynamicTargets.length) {
  console.log('  （变量目标不是错误；switchTab 的取值域见 verify_tabbar_targets.js）')
}
for (const g of goTargets) console.log('  [go 变量目标] ' + g)
for (const u of utilSources) console.log('  [工具来源] ' + u)

if (errors.length) {
  console.log(`\n✗ 发现 ${errors.length} 个问题：`)
  for (const e of errors) console.log('  - ' + e)
  process.exit(1)
}
console.log('\n✓ 路由注册表校验通过')
