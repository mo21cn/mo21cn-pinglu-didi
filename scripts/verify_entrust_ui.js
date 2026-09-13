#!/usr/bin/env node
/**
 * 委托发货前端契约校验（S2 前端切片 / ENT-012 · AC-02 入口隔离 / AC-04 工作台 / AC-21 五态）
 *
 * 为什么单独一个脚本
 * ------------------
 * 页面里的 `onLoad` / `onShow` 只能在微信开发者工具里跑，CI 跑不了。而这一片里
 * 最容易出错、又最难被发现的两件事恰好都在纯逻辑里：
 *   · 入口该不该显示 —— 判错就是"用户点进去必然 403"，或者"该看到的人看不到"；
 *   · 当前该渲染哪一种状态 —— 判错就是把"失败"渲染成"没有数据"，没人会报这种 bug。
 * 所以这两件事被抽进 miniapp/utils/entrust.js 的纯函数（不接触 wx），由本脚本在
 * Node 里用真实分支直接驱动。
 *
 * 本脚本不是"再抄一遍期望值"，它钉的是**跨端不变式**：
 *   1. 状态取值域与后端 `assignments.py` 的 `STATUS_*` 一致 —— 后端加状态，这里就红；
 *   2. 入口可见性只由服务端探测结果决定，401/403/404/断网一律隐藏；
 *   3. 五态判定顺序不可调换，且**错误优先于空**（403 且 total=0 必须是 denied）；
 *   4. 界面用到的每个 CSS 类都真实存在 —— 含 `hover-class`：它漏声明不会报错，
 *      只表现为"点了没有反馈"，靠肉眼几乎发现不了；
 *   5. 业务投影不泄漏内部字段，未知值不臆造成一个具体值。
 *
 * 用法：node scripts/verify_entrust_ui.js
 * 退出码：0 通过 / 1 有问题
 */
const fs = require('fs')
const path = require('path')

const REPO = path.resolve(__dirname, '..')
const MINI = path.join(REPO, 'miniapp')

const errors = []
let checked = 0

function check(name, cond, detail) {
  checked++
  if (!cond) errors.push(name + (detail ? ' —— ' + detail : ''))
}

function read(abs) {
  try {
    return fs.readFileSync(abs, 'utf8')
  } catch (e) {
    errors.push(`[文件] 读取失败 ${path.relative(REPO, abs)}: ${e.message}`)
    return ''
  }
}

/** 从 CSS 源码里抽出所有类名（`.` 后必须是字母/下划线，避免把 `0.5`、`rgba(...,.10)` 当类名） */
function cssClasses(src) {
  const set = new Set()
  const re = /\.(-?[a-zA-Z_][a-zA-Z0-9_-]*)/g
  let m
  while ((m = re.exec(src))) set.add(m[1])
  return set
}

/**
 * 抽出 wxml 里用到的类名：`class` 与 `hover-class` 两个属性。
 * 「静态 token」直接取；「动态表达式里的字符串字面量」（如 `{{x ? 'filter-pill-active' : ''}}`）
 * 也一并取出 —— 否则那半个类名永远没人校验。`{{item.statusClass}}` 这种间接引用
 * 抓不到，改由 statusClass() 的输出单独校验（见下）。
 */
function wxmlClassTokens(src) {
  const tokens = []
  const attrRe = /\b(?:hover-)?class\s*=\s*"([^"]*)"/g
  let m
  while ((m = attrRe.exec(src))) {
    const raw = m[1]
    raw
      .replace(/\{\{[^}]*\}\}/g, ' ')
      .split(/\s+/)
      .forEach((t) => {
        if (t) tokens.push(t)
      })
    const dyn = raw.match(/\{\{[^}]*\}\}/g) || []
    dyn.forEach((seg) => {
      const lits = seg.match(/'[a-zA-Z][a-zA-Z0-9_-]*'/g) || []
      lits.forEach((l) => tokens.push(l.slice(1, -1)))
    })
  }
  return tokens
}

// ---- 载入被测模块 ----
let E = null
try {
  E = require(path.join(MINI, 'utils', 'entrust.js'))
} catch (e) {
  errors.push(`[模块] 无法载入 miniapp/utils/entrust.js: ${e.message}`)
}

if (!E) {
  console.log(`检查完成：${checked} 项（模块载入失败，跳过其余断言）`)
  console.log(`\n发现 ${errors.length} 个问题：`)
  errors.forEach((x) => console.log('  - ' + x))
  process.exit(1)
}

const NUM_RE = /^\d+$/

// ─────────────────────────────────────────────────────────────
// 1. 状态取值域：前端 ⇄ 后端必须逐字对齐
// ─────────────────────────────────────────────────────────────
const backendSrc = read(path.join(REPO, 'backend/app/modules/entrust/assignments.py'))
const backendStatuses = []
{
  const re = /STATUS_[A-Z_]+\s*=\s*"([a-z_]+)"/g
  let m
  while ((m = re.exec(backendSrc))) {
    if (backendStatuses.indexOf(m[1]) === -1) backendStatuses.push(m[1])
  }
}
backendStatuses.sort()
const frontStatuses = (E.STATUS_ORDER || []).slice().sort()

check(
  '[取值域] STATUS_ORDER 与后端 assignments.STATUS_* 完全一致',
  backendStatuses.length > 0 && JSON.stringify(frontStatuses) === JSON.stringify(backendStatuses),
  `前端 ${JSON.stringify(frontStatuses)} vs 后端 ${JSON.stringify(backendStatuses)}（后端新增状态时必须同步前端三处：META / ORDER / HINT）`
)
check(
  '[取值域] STATUS_META 覆盖全部状态',
  JSON.stringify(Object.keys(E.STATUS_META).sort()) === JSON.stringify(backendStatuses),
  '缺一个状态 → 界面会把它显示成「未知状态」'
)
check(
  '[取值域] STATUS_HINT 覆盖全部状态',
  JSON.stringify(Object.keys(E.STATUS_HINT).sort()) === JSON.stringify(backendStatuses),
  '缺一个状态 → 详情页少一句下一步动作说明'
)

// ─────────────────────────────────────────────────────────────
// 2. 入口可见性（AC-02）：只有服务端明确放行才显示
// ─────────────────────────────────────────────────────────────
const entryCases = [
  [{ status: 200 }, true, 'ok', '服务端放行'],
  [{ status: 400 }, true, 'need_org', '属于多组织、需先选组织，不是权限问题 → 仍可见'],
  [{ status: 401 }, false, 'expired', '登录过期'],
  [{ status: 403 }, false, 'denied', '不在组织内 / 缺权限'],
  [{ status: 404 }, false, 'disabled', '功能未启用'],
  [{ status: 500 }, false, 'unknown', '未知失败'],
  [{ netError: true }, false, 'offline', '断网（无 httpStatus）']
]
entryCases.forEach(function (c) {
  const d = E.entryDecision(c[0])
  check(
    `[入口] ${JSON.stringify(c[0])} → visible=${c[1]}（${c[3]}）`,
    d.visible === c[1] && d.reason === c[2],
    '实际 ' + JSON.stringify(d)
  )
})

// ─────────────────────────────────────────────────────────────
// 3. 五态推导（AC-21）：顺序不可调换，且错误优先于空
// ─────────────────────────────────────────────────────────────
const viewCases = [
  [{ loading: true }, 'loading', '加载中优先于一切'],
  [{ loading: true, status: 401 }, 'loading', '加载中不看其它字段（否则会闪一下错误态）'],
  [{ status: 401 }, 'expired', '登录过期要引导重新登录，不能显示成"没有委托"'],
  [{ status: 403 }, 'denied', '无权限'],
  [{ status: 403, total: 0 }, 'denied', '**错误优先于空**：403 时 total=0 不代表没有数据'],
  [{ status: 400 }, 'denied', '多组织未指定 → 引导选组织'],
  [{ status: 404 }, 'denied', '功能未开放'],
  [{ status: 500 }, 'error', '其它失败'],
  [{ netError: true }, 'error', '断网'],
  [{ status: 200, total: 0 }, 'empty', '真空'],
  [{ status: 200, total: 3 }, 'ready', '有数据']
]
viewCases.forEach(function (c) {
  const s = E.viewState(c[0])
  check(`[五态] ${JSON.stringify(c[0])} → ${c[1]}（${c[2]}）`, s.state === c[1], '实际 ' + JSON.stringify(s))
})

// ─────────────────────────────────────────────────────────────
// 4. 业务投影：不泄漏内部字段，未知值不臆造
// ─────────────────────────────────────────────────────────────
const leakyRow = {
  assignment_id: 12,
  title: '',
  cargo_summary: '',
  quantity: null,
  quantity_unit: '吨',
  status: 'submitted',
  revision: 3,
  created_at: '2026-09-13 10:00:00',
  org_id: 7,
  // 以下都不该出现在前端投影里
  storage_key: 'entrust/secret.pdf',
  cost_price: '100.00',
  quote_rate: '38.00',
  internal_note: '内部比价'
}
const proj = E.decorateAssignment(leakyRow)
const INTERNAL = ['storage_key', 'cost_price', 'quote_rate', 'internal_note']
INTERNAL.forEach(function (k) {
  check(`[投影] 不把内部字段 ${k} 交给前端`, !(k in proj))
})
check('[投影] 空标题给中性默认值（不臆造）', proj.title === '未命名委托', '实际 ' + proj.title)
check('[投影] 货量为空时说"未填写"而不是 0', proj.quantityText === '货量未填写', '实际 ' + proj.quantityText)
check(
  '[投影] 已知状态 → 中文标签 + 对应样式类',
  proj.statusLabel === '待受理' && proj.statusClass === 'chip chip-warn',
  `实际 ${proj.statusLabel} / ${proj.statusClass}`
)

const unknown = E.decorateAssignment({ status: 'nope', assignment_id: 1 })
check(
  '[投影] 未知状态显式标为「未知状态」而不是猜一个',
  unknown.statusLabel === '未知状态' && unknown.statusClass === 'chip chip-muted',
  `实际 ${unknown.statusLabel} / ${unknown.statusClass}`
)

const withQty = E.decorateAssignment({ assignment_id: 2, quantity: '1200.50', quantity_unit: '吨', status: 'claimed' })
check('[投影] 数量带单位', withQty.quantityText === '1200.50 吨', '实际 ' + withQty.quantityText)

const detailProj = E.decorateDetail(leakyRow)
check('[投影] 详情页每个状态都有下一步说明', !!detailProj.statusHint, '实际 ' + JSON.stringify(detailProj.statusHint))
check('[投影] 详情页给出归属组织文案', /组织/.test(detailProj.orgText || ''), '实际 ' + detailProj.orgText)

const ph = E.pageHint(37, 2, 20)
check('[投影] 分页提示与页数一致', ph.indexOf('2/2') !== -1 && ph.indexOf('37') !== -1, '实际 ' + ph)

// ─────────────────────────────────────────────────────────────
// 5. 样式类存在性：拼错类名不会报错，只会"没有样式"
// ─────────────────────────────────────────────────────────────
const appClasses = cssClasses(read(path.join(MINI, 'app.wxss')))

;(E.STATUS_ORDER || []).forEach(function (st) {
  E.statusClass(st)
    .split(/\s+/)
    .forEach(function (cls) {
      check(`[样式] 状态 ${st} 的类 .${cls} 存在于 app.wxss`, appClasses.has(cls))
    })
})

/** 本切片新增/改动的两个页面：不允许任何未定义类名 */
const ENTRUST_PAGES = ['pages/entrust/workbench/workbench', 'pages/entrust/detail/detail']

/**
 * 参与"类名存在性"检查的页面，及各自的已知空类名。
 *
 * `mine.wxml` 有三个历史遗留的空类名（写了但从未定义样式，渲染不受影响：
 * `.bell-icon` / `.role-chip-label` 的文字样式来自兄弟/父级类，`.nav` 的高度靠
 * `.nav-inner`、`padding-top` 是内联 style）。本次不改它们 —— 那会把与这一增量
 * 无关的历史文件拖进 diff，审查时更难判断"到底改了什么"。将来若要给它们加样式，
 * 从这里删名即可恢复拦截。
 */
const PAGE_CSS_CHECKS = [
  { path: 'pages/entrust/workbench/workbench', knownEmpty: [] },
  { path: 'pages/entrust/detail/detail', knownEmpty: [] },
  { path: 'pages/mine/mine', knownEmpty: ['nav', 'bell-icon', 'role-chip-label'] }
]

PAGE_CSS_CHECKS.forEach(function (page) {
  const wxml = read(path.join(MINI, page.path + '.wxml'))
  const pageCss = read(path.join(MINI, page.path + '.wxss'))
  const available = new Set([...appClasses, ...cssClasses(pageCss)])
  const known = new Set(page.knownEmpty)
  const tokens = Array.from(new Set(wxmlClassTokens(wxml)))
  tokens.forEach(function (t) {
    if (known.has(t)) return
    check(
      `[样式] ${page.path}.wxml 的类 .${t} 在全局或本页样式中已定义`,
      available.has(t),
      'hover-class 漏定义只会静默无反馈'
    )
  })
})

// ─────────────────────────────────────────────────────────────
// 6. 页面模板必须把六种状态都接上（少一个分支就少一种表现）
// ─────────────────────────────────────────────────────────────
const wbWxml = read(path.join(MINI, 'pages/entrust/workbench/workbench.wxml'))
const dtWxml = read(path.join(MINI, 'pages/entrust/detail/detail.wxml'))
;['loading', 'expired', 'denied', 'error', 'empty'].forEach(function (st) {
  check(`[模板] 工作台有 ${st} 分支`, new RegExp("view === '" + st + "'").test(wbWxml))
})
check('[模板] 工作台有 ready（兜底）分支', /wx:else/.test(wbWxml))
;['loading', 'expired', 'denied', 'error'].forEach(function (st) {
  check(`[模板] 详情页有 ${st} 分支`, new RegExp("view === '" + st + "'").test(dtWxml))
})
check('[模板] 详情页有 ready（兜底）分支', /wx:else/.test(dtWxml))

// ─────────────────────────────────────────────────────────────
// 7. 契约接线：谁都没权限"自己判断一遍"
// ─────────────────────────────────────────────────────────────
const entrustJs = read(path.join(MINI, 'utils', 'entrust.js'))
check('[接线] 入口探测是静默的（该项失败属正常情况，不应弹提示）', /silent:\s*true/.test(entrustJs))

const requestJs = read(path.join(MINI, 'utils', 'request.js'))
check(
  '[接线] request 的错误对象带 httpStatus（entrust.js 的 catch 依赖它区分 401/403/404）',
  /\.httpStatus\s*=/.test(requestJs)
)

const mineJs = read(path.join(MINI, 'pages/mine/mine.js'))
check('[接线] 我的页通过 probeEntry 决定入口可见性', /probeEntry\(/.test(mineJs))
check(
  '[接线] 我的页不用本地角色字段判断经理能力（Storage 可被改写）',
  mineJs.indexOf('manager') === -1 && !/current_role\s*===?\s*['"]/.test(mineJs)
)

const wbJs = read(path.join(MINI, 'pages/entrust/workbench/workbench.js'))
check('[接线] 工作台筛选条由 STATUS_ORDER 派生', /STATUS_ORDER\.map/.test(wbJs))
;(E.STATUS_ORDER || []).forEach(function (st) {
  check(
    `[接线] 工作台页不硬编码状态字面量 '${st}'`,
    wbJs.indexOf("'" + st + "'") === -1,
    '状态字面量只应出现在 utils/entrust.js，页面里重复一份就会漂移'
  )
})

// 页面从 utils/entrust 解构出来的每个函数都必须真的被导出（抓改名漏改）
ENTRUST_PAGES.forEach(function (p) {
  const src = read(path.join(MINI, p + '.js'))
  const m = src.match(/const\s*\{([^}]*)\}\s*=\s*require\(['"][^'"]*utils\/entrust['"]\)/)
  if (!m) {
    check(`[接线] ${p}.js 从 utils/entrust 解构导入`, false, '未找到 require 解构')
    return
  }
  m[1]
    .split(',')
    .map(function (s) {
      return s.trim()
    })
    .filter(Boolean)
    .forEach(function (key) {
      check(`[接线] ${p}.js 导入的 ${key} 确实被导出`, Object.prototype.hasOwnProperty.call(E, key))
    })
})

// ─────────────────────────────────────────────────────────────
// 8. 路由注册（非 tabBar 页，入口用 navigateTo 进入）
// ─────────────────────────────────────────────────────────────
let appJson = { pages: [] }
try {
  appJson = JSON.parse(read(path.join(MINI, 'app.json')))
} catch (e) {
  check('[路由] app.json 可解析', false, e.message)
}
const registered = new Set(appJson.pages || [])
ENTRUST_PAGES.forEach(function (p) {
  check(`[路由] ${p} 已在 app.json 注册`, registered.has(p))
})
check('[路由] 入口用 navigateTo（工作台不是 tabBar 页）', /navigateTo\(\{\s*url:\s*'\/pages\/entrust\/workbench\/workbench'/.test(mineJs))

// 数值常量一致性：分页大小必须是整数（防手改成字符串，后端会直接 422）
const pageSizeMatch = wbJs.match(/PAGE_SIZE\s*=\s*(\d+)/)
check('[接线] 工作台 PAGE_SIZE 是正整数', !!pageSizeMatch && NUM_RE.test(pageSizeMatch[1]))

// ---- 输出 ----
console.log(`检查完成：${checked} 项断言 / 覆盖 ${PAGE_CSS_CHECKS.length} 个页面 + 1 个契约模块`)
if (errors.length) {
  console.log(`\n发现 ${errors.length} 个问题：`)
  errors.forEach((x) => console.log('  - ' + x))
  process.exit(1)
}
console.log('全部通过 ✓')
