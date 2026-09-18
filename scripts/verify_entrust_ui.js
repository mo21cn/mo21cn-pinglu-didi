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
 *
 * ⚠️ 动态表达式的字面量**必须先剥掉比较运算的右侧**再取。
 * 反例：`{{queue === 'assignment' ? 'queue-pill-active' : ''}}` —— 这里 `'assignment'`
 * 是**比较对象**（队列名），不是类名。不剥的话会报一条「类 .assignment 未定义」的
 * 假错，而修假错的"办法"五花八门（把队列名写进 CSS、把判断挪进 JS），
 * 每一条都在削弱这个检查真正要守的东西。剥掉算子右侧后，真正的类名
 * （`'queue-pill-active'`）仍然照查。
 */
function stripComparisonOperands(expr) {
  return expr
    .replace(/(?:===|!==|==|!=)\s*'[^']*'/g, ' ')
    .replace(/'[^']*'\s*(?:===|!==|==|!=)/g, ' ')
}

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
      // 一个字面量里可能是**一串**类名（`{{x ? 'a b' : ''}}`），按空白拆开逐个查：
      // 只取单 token 的话，带空格的那种会被静默漏检（反向验证时正是这样漏掉的）。
      const lits = stripComparisonOperands(seg).match(/'[^']*'/g) || []
      lits.forEach((l) => {
        l
          .slice(1, -1)
          .split(/\s+/)
          .forEach((t) => {
            if (/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(t)) tokens.push(t)
          })
      })
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

/** 本切片新增/改动的页面：不允许任何未定义类名 */
const ENTRUST_PAGES = [
  'pages/entrust/workbench/workbench',
  'pages/entrust/detail/detail',
  'pages/entrust/artifact/artifact',
  'pages/entrust/case/case',
  // 切片四之六的登记页。列进来会顺带拿到三项现成检查：解构导入的每个函数都被导出、
  // 已在 app.json 注册、模板里的类名都有定义 —— 这三件漏掉都不报错，只是静默不生效。
  'pages/entrust/case-create/case-create',
  // DR-0015 / ENT-033：会话屏（UI-03 的成果卡一半）
  'pages/entrust/session/session',
  // S1 / DEMO-1 §3.3：客户受理屏（UI-07）。它从 utils/entrust 解构导入 10 个成员，
  // 列进来才会被下面那三项现成检查扫到：解构的每个成员都被导出、已在 app.json 注册、
  // 模板里的类名都有定义 —— 这三件漏掉都不报错，只是静默不生效。
  'pages/entrust/intake/intake',
  // S1 工作项 5：「我的委托」（货主侧状态屏）。它从 utils/entrust 解构导入 5 个成员，
  // 列进来才会被下面那三项现成检查扫到：解构的每个成员都被导出、已在 app.json 注册、
  // 模板里的类名都有定义 —— 这三件漏掉都不报错，只是静默不生效。
  'pages/entrust/assignments/assignments'
]

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
  { path: 'pages/entrust/artifact/artifact', knownEmpty: [] },
  { path: 'pages/entrust/case/case', knownEmpty: [] },
  { path: 'pages/entrust/case-create/case-create', knownEmpty: [] },
  { path: 'pages/entrust/session/session', knownEmpty: [] },
  { path: 'pages/entrust/intake/intake', knownEmpty: [] },
  { path: 'pages/entrust/assignments/assignments', knownEmpty: [] },
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

const artWxml = read(path.join(MINI, 'pages/entrust/artifact/artifact.wxml'))
;['loading', 'expired', 'denied', 'error'].forEach(function (st) {
  check(`[模板] 成果页有 ${st} 分支`, new RegExp("view === '" + st + "'").test(artWxml))
})
check('[模板] 成果页有 ready（兜底）分支', /wx:else/.test(artWxml))

const caseWxmlTop = read(path.join(MINI, 'pages/entrust/case/case.wxml'))
;['loading', 'expired', 'denied', 'error'].forEach(function (st) {
  check(`[模板] 案件页有 ${st} 分支`, new RegExp("view === '" + st + "'").test(caseWxmlTop))
})
check('[模板] 案件页有 ready（兜底）分支', /wx:else/.test(caseWxmlTop))

const sessionWxml = read(path.join(MINI, 'pages/entrust/session/session.wxml'))
;['loading', 'expired', 'denied', 'error', 'empty'].forEach(function (st) {
  check(`[模板] 会话页有 ${st} 分支`, new RegExp("view === '" + st + "'").test(sessionWxml))
})
check('[模板] 会话页有 ready（兜底）分支', /wx:else/.test(sessionWxml))
// 成果卡必须显示**精确版本**：AC-05 的"same object/version"就落在这一行字上，
// 只显示成果名不显示 vN，等于把"是不是同一版"交给人去猜。
check('[模板] 会话页成果卡显示 v{{版本号}}', /v\{\{item\.currentRevisionNo\}\}/.test(sessionWxml))

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

// ─────────────────────────────────────────────────────────────
// 9. 组织选择器（ENT-012 第二切片）：候选来自服务端，标签必须与后端逐字对齐
// ─────────────────────────────────────────────────────────────
const accessSrc = read(path.join(REPO, 'backend/app/modules/entrust/access.py'))

// 后端权限常量：源码里是 `PERM_XXX = "entrust:xxx"`
const backendPerms = []
accessSrc.replace(/^PERM_[A-Z_]+\s*=\s*"([^"]+)"/gm, function (_, code) {
  backendPerms.push(code)
  return ''
})
check(
  '[组织] 从 access.py 抽到了权限常量（抽取失败会让下面的覆盖断言变成空转）',
  backendPerms.length >= 8,
  '实际抽到 ' + backendPerms.length + ' 个'
)

const frontPermKeys = Object.keys(E.ORG_PERMISSION_LABELS || {})
const missingPerm = backendPerms.filter(function (p) {
  return frontPermKeys.indexOf(p) === -1
})
check(
  '[组织] 前端权限标签覆盖后端全部权限常量（缺一个就会把原始权限码显示给用户）',
  missingPerm.length === 0,
  '缺标签：' + JSON.stringify(missingPerm)
)
const extraPerm = frontPermKeys.filter(function (p) {
  return backendPerms.indexOf(p) === -1
})
check(
  '[组织] 前端没有后端不存在的权限标签（防拼错与僵尸标签）',
  extraPerm.length === 0,
  '多余标签：' + JSON.stringify(extraPerm)
)

// 后端角色取值域：ORG_ROLE_PERMISSIONS 字典的键
const roleBlock = accessSrc.match(/ORG_ROLE_PERMISSIONS[^=]*=\s*\{([\s\S]*?)\n\}/)
const backendRoles = []
if (roleBlock) {
  roleBlock[1].replace(/^\s*"([a-z]+)"\s*:/gm, function (_, r) {
    backendRoles.push(r)
    return ''
  })
}
check('[组织] 从 access.py 抽到了角色取值域', backendRoles.length >= 4, '实际 ' + backendRoles.length)
const missingRole = backendRoles.filter(function (r) {
  return !Object.prototype.hasOwnProperty.call(E.ORG_ROLE_LABELS || {}, r)
})
check('[组织] 前端角色标签覆盖后端全部角色', missingRole.length === 0, '缺标签：' + JSON.stringify(missingRole))

// ── 「选哪个组织」的决策规则：不猜，也不用已经失效的旧选择
const orgList = [
  { orgId: '1', name: '甲' },
  { orgId: '2', name: '乙' }
]
check('[组织] 清单为空 → none（是"没有组织身份"，不是"没有数据"）', E.pickOrg([], '1').reason === 'none')
check(
  '[组织] 只有一个组织 → 直接用，不要求用户做没有余地的选择',
  E.pickOrg([orgList[0]], '').orgId === '1' && E.pickOrg([orgList[0]], '').reason === 'only'
)
check(
  '[组织] 多组织且无记录 → ambiguous（**不猜**，猜错会让人看另一个组织的数据）',
  E.pickOrg(orgList, '').reason === 'ambiguous' && E.pickOrg(orgList, '').orgId === ''
)
check(
  '[组织] 上次选择仍在清单里 → 沿用',
  E.pickOrg(orgList, '2').orgId === '2' && E.pickOrg(orgList, '2').reason === 'saved'
)
check(
  '[组织] 上次选择已不在清单里 → 不放行旧值（否则会停在一个已不属于的组织上）',
  E.pickOrg(orgList, '9').orgId === '' && E.pickOrg(orgList, '9').reason === 'ambiguous'
)

// ── 组织投影：译成中文，且不把内部字段交给界面
const orgProj = E.decorateOrg({
  org_id: 7,
  name: '平陆航运',
  member_role: 'manager',
  permissions: ['entrust:view', 'entrust:assignment:claim'],
  internal_cost_rate: 0.12
})
check('[组织] org_id 投影为字符串（避免与数字比较时类型不一致）', orgProj.orgId === '7', '实际 ' + orgProj.orgId)
check(
  '[组织] 角色译成中文，而不是把 member_role 原样显示',
  orgProj.roleLabel === '经理人',
  '实际 ' + orgProj.roleLabel
)
check('[组织] 权限码译成中文', orgProj.permissionText.indexOf('受理委托') !== -1, '实际 ' + orgProj.permissionText)
check('[组织] 不把内部字段带进投影', !('internal_cost_rate' in orgProj))
check(
  '[组织] 组织名为空时给中性默认值（不臆造）',
  E.decorateOrg({ org_id: 1 }).name === '未命名组织'
)

// ── 页面接线：候选来自服务端，失败与队列取数同一裁决
check('[组织] 工作台有组织选择器容器', /class="org-bar"/.test(wbWxml))
check('[组织] 选择器只在多组织时出现', /wx:if="\{\{orgs\.length > 1\}\}"/.test(wbWxml))
check('[组织] 选择器绑定 onPickOrg', /bindtap="onPickOrg"/.test(wbWxml))
check('[组织] 工作台实现了 onPickOrg', /onPickOrg\s*\(/.test(wbJs))
check('[组织] 工作台调用服务端清单接口（不自行枚举组织）', /fetchMyOrgs\(/.test(wbJs))
check(
  '[组织] 工作台把选择存进 Storage 仅作下次默认值',
  /setStorageSync\(\s*STORAGE_ORG_KEY/.test(wbJs)
)
check(
  '[组织] none / ambiguous 都被显式处理（组织问题不被渲染成"没有委托"）',
  /reason === 'none'/.test(wbJs) && /reason === 'ambiguous'/.test(wbJs)
)

// ─────────────────────────────────────────────────────────────
// 10. 工作台七槽位（UI-05 / ENT-021）：前端配置表 ⇄ 后端声明逐字一致
//     DR-0010 验证 #1。两侧都有断言，就没人能单方面改顺序或改名 ——
//     "槽位是页面骨架"，顺序错位是最难在界面上发现、又最容易发生的一类漂移。
// ─────────────────────────────────────────────────────────────

const dtJs = read(path.join(MINI, 'pages/entrust/detail/detail.js'))
const wbPy = read(path.join(REPO, 'backend/app/modules/entrust/workbench.py'))
const tasksPy = read(path.join(REPO, 'backend/app/modules/entrust/tasks.py'))

/** 从 `... = (` 起切出**配对括号**内的块（正则跨不了嵌套） */
function sliceParenBlock(src, headRe) {
  const m = src.match(headRe)
  if (!m) return ''
  const start = m.index + m[0].length - 1
  let depth = 0
  for (let i = start; i < src.length; i++) {
    if (src[i] === '(') depth++
    else if (src[i] === ')') {
      depth--
      if (depth === 0) return src.slice(start, i + 1)
    }
  }
  return ''
}

const slotSpecBlock = sliceParenBlock(wbPy, /SLOT_SPECS[^=]*=\s*\(/)

/** 「本期未开放」标记的开关（DR-0013 §7.3）。**必须读它的值**，不能按"匹配不到
 *  `open=False` 就算开放"处理：常量翻成 `True` 的那天，那种写法会继续绿着 ——
 *  那时它就成了一个骗人的门禁。锚在行首并把 `[^=]` 限制在行内，避免跨行扫到别的 `=`。 */
const slotOpenMatch = /^EXCEPTIONS_SLOT_OPEN[^=\n]*=\s*(True|False)\s*$/m.exec(wbPy)
const exceptionsSlotOpen = slotOpenMatch ? slotOpenMatch[1] === 'True' : null

function slotIsOpen(chunk) {
  if (/open=False/.test(chunk)) return false
  if (/open=EXCEPTIONS_SLOT_OPEN/.test(chunk)) return exceptionsSlotOpen === true
  return true
}

const backendSlots = slotSpecBlock
  .split(/SlotSpec\(/)
  .slice(1)
  .map(function (chunk) {
    return {
      key: (chunk.match(/key="([^"]+)"/) || [])[1] || '',
      title: (chunk.match(/title="([^"]+)"/) || [])[1] || '',
      open: slotIsOpen(chunk)
    }
  })

const frontSlots = E.WORKBENCH_SLOTS || []

check('[槽位] 前端配置表 7 条', frontSlots.length === 7, '实际 ' + frontSlots.length)
check('[槽位] 后端 SLOT_SPECS 解析出 7 条', backendSlots.length === 7, '实际 ' + backendSlots.length)
check(
  '[槽位] 能解析出后端 EXCEPTIONS_SLOT_OPEN 的值（解析不到就无从判断谁未开放）',
  exceptionsSlotOpen !== null,
  '实际 ' + String(exceptionsSlotOpen)
)
check(
  '[槽位] key 与顺序前后端逐字一致（DR-0010 §3.1：不得重排）',
  frontSlots.map((s) => s.key).join(',') === backendSlots.map((s) => s.key).join(','),
  '前端 [' +
    frontSlots.map((s) => s.key).join(',') +
    '] / 后端 [' +
    backendSlots.map((s) => s.key).join(',') +
    ']'
)
check(
  '[槽位] 标题前后端逐字一致',
  frontSlots.map((s) => s.title).join('|') === backendSlots.map((s) => s.title).join('|'),
  '前端 [' +
    frontSlots.map((s) => s.title).join('|') +
    '] / 后端 [' +
    backendSlots.map((s) => s.title).join('|') +
    ']'
)
check(
  '[槽位] 撤下后**不再有**任何未开放槽位（多一个都说明有人悄悄降级了能力）',
  backendSlots
    .filter((s) => !s.open)
    .map((s) => s.key)
    .join(',') === '',
  '实际 [' + backendSlots.filter((s) => !s.open).map((s) => s.key).join(',') + ']'
)

// 任务类型取值域：前端标签表 / 顺序表都必须与后端同集合
const backendTaskTypes = Array.from(
  new Set(
    (tasksPy.match(/TASK_TYPE_[A-Z_]+ = "([^"]+)"/g) || []).map(function (line) {
      return line.match(/"([^"]+)"/)[1]
    })
  )
).sort()
const frontTaskTypes = Object.keys(E.TASK_TYPE_LABELS || {}).sort()
check(
  '[任务] 前端类型标签覆盖后端全部取值域（少一个界面就会显示原始英文码）',
  frontTaskTypes.join(',') === backendTaskTypes.join(','),
  '前端 [' + frontTaskTypes.join(',') + '] / 后端 [' + backendTaskTypes.join(',') + ']'
)
check(
  '[任务] 顺序表与标签表同集合',
  (E.TASK_TYPE_ORDER || []).slice().sort().join(',') === frontTaskTypes.join(','),
  '实际 [' + (E.TASK_TYPE_ORDER || []).join(',') + ']'
)
frontSlots.forEach(function (slot) {
  if (!slot.taskType) return
  check(
    `[槽位] ${slot.key} 的动作任务类型 '${slot.taskType}' 在取值域内`,
    backendTaskTypes.indexOf(slot.taskType) !== -1
  )
})
check(
  '[槽位] 四个派生字段的标签与顺序固定（DR-0010 §3.5）',
  JSON.stringify(E.SLOT_FIELD_LABELS) ===
    JSON.stringify(['当前成果', '未决问题', '下一责任方', '最后更新']),
  '实际 ' + JSON.stringify(E.SLOT_FIELD_LABELS)
)

// ── 空值四态文案：四句话必须不同，且都要有（DR-0010 §3.6）
// 「一律显示待补充」正是四态要消灭的东西，所以这里断言的是**互不相同**而不是"都有值"。
const emptyText = E.SLOT_EMPTY_TEXT || {}
const emptyValues = Object.keys(emptyText).map(function (k) {
  return emptyText[k]
})
check(
  '[四态] 文案齐备（暂无记录 / 尚未分配 / 不适用 / 信息缺失 / 本期未开放）',
  ['noRecord', 'unassigned', 'notApplicable', 'missingInfo', 'notOpen'].every(function (k) {
    return !!emptyText[k]
  }),
  '实际 ' + JSON.stringify(emptyText)
)
check(
  '[四态] 五种文案互不相同（禁止一律「待补充」）',
  new Set(emptyValues).size === emptyValues.length,
  '实际 ' + JSON.stringify(emptyValues)
)

// ── 载荷字段名与状态取值域：前后端**跨语言**核对 ────────────────────────
// 上面钉的是「槽位骨架」，这一段钉的是「骨架上的字段」。不钉这里就是本片最大的静默失效面：
// 后端把 `next_owner` 改名、或把 `missing_info` 改成 `missing`，前端读不到就落进各自的
// 兜底分支 —— 界面显示的是「不适用」「无未决问题」，**一句错误的业务结论**，而不是一次报错。
// 原先本节的投影断言全部由手写 fixture 驱动，后端怎么改都不会红。
const schPy = read(path.join(REPO, 'backend/app/modules/entrust/schemas.py'))
// 复用第 7 节已读入的 entrust.js 源码（const，不能重复声明）

/** 取一个 Pydantic 响应模型声明的字段名（先剔除三引号文本，再按类体缩进取值） */
function schemaFields(src, cls) {
  const parts = src.split(new RegExp('^class ' + cls + '\\(BaseModel\\):', 'm'))
  if (parts.length < 2) return []
  const rest = parts[1]
  const stop = rest.search(/^class /m)
  const block = (stop === -1 ? rest : rest.slice(0, stop)).replace(/"""[\s\S]*?"""/g, '')
  const names = []
  block.split('\n').forEach(function (line) {
    const m = line.match(/^ {4}([a-z_][a-z0-9_]*)\s*:/)
    if (m) names.push(m[1])
  })
  return names
}

const wbSchemaClasses = (schPy.match(/^class (Workbench[A-Za-z]*)\(BaseModel\):/gm) || []).map(
  function (l) {
    return l.replace(/^class /, '').replace(/\(BaseModel\):$/, '')
  }
)
check(
  '[载荷] 工作台响应模型全部纳入核对（新增一个模型就必须在此有交待）',
  wbSchemaClasses.length === 8 && wbSchemaClasses.indexOf('WorkbenchOut') !== -1,
  '实际 ' + wbSchemaClasses.join(', ')
)

// 前端**有意不读**载荷字段的清单。豁免必须给理由 —— 「声明了但界面从没用过」本身
// 就是个该被看见的事实，不能靠默默忽略让它消失。
const WIRE_EXEMPT = {
  'WorkbenchSlotOut.title':
    '标题取自前端配置表 WORKBENCH_SLOTS（顺序与标题由「标题逐字一致」断言钉住两侧相等）',
  'WorkbenchCountsOut.unassigned_tasks':
    '同一事实已由 issues 的 ISSUE_UNASSIGNED_TASK 类别单独表达，不在两处重复显示；计数保留给后续批量指派'
}
const unseenFields = []
wbSchemaClasses.forEach(function (cls) {
  schemaFields(schPy, cls).forEach(function (f) {
    const id = cls + '.' + f
    if (WIRE_EXEMPT[id]) return
    if (!new RegExp('\\.' + f + '\\b').test(entrustJs)) unseenFields.push(id)
  })
})
check(
  '[载荷] 后端声明的每个字段前端都真的读过（改了字段名这里就红，不会静默显示成兜底文案）',
  unseenFields.length === 0,
  '前端未读取：' + unseenFields.join(', ')
)
check(
  '[载荷] 豁免清单不为空壳（每人都有理由，防止用豁免把真问题盖住）',
  Object.keys(WIRE_EXEMPT).every(function (k) {
    return WIRE_EXEMPT[k].length > 10 && k.indexOf('.') > 0
  })
)

// 后端 workbench.py 里的三族 state 常量（前端所有字面量比较都要落在这些取值内）
const wbStateConst = {}
;(wbPy.match(/^((?:CURRENT|ISSUES|OWNER|ISSUE)_[A-Z_]+)\s*=\s*"([^"]+)"/gm) || []).forEach(function (l) {
  const m = l.match(/^([A-Z_]+)\s*=\s*"([^"]+)"/)
  wbStateConst[m[1]] = m[2]
})

// 未决问题的**类别**取值域：前端标签表必须覆盖后端 ISSUE_* 全部取值。
// 与任务类型同理 —— 少一个键，该类别就只剩一句描述文本，用户得读懂整句话才知道
// 该不该动手（"缺项"要去补数据，"阻断"是这条路走不通，两者动作完全不同）。
const backendIssueKinds = Object.keys(wbStateConst)
  .filter(function (k) {
    return k.indexOf('ISSUE_') === 0
  })
  .map(function (k) {
    return wbStateConst[k]
  })
  .sort()
const frontIssueKinds = Object.keys(E.ISSUE_KIND_LABELS || {}).sort()
check(
  '[未决] 前端类别标签覆盖后端 ISSUE_* 全部取值（少一个界面就只剩描述文本）',
  frontIssueKinds.join(',') === backendIssueKinds.join(','),
  '前端 [' + frontIssueKinds.join(',') + '] / 后端 [' + backendIssueKinds.join(',') + ']'
)

// 三个 state 字段的取值域：前端字面量必须落在后端声明之内。
// 兜底分支（else）本身是合法的，但**它代表哪个取值**必须写清楚 —— 否则后端新增一个
// 状态，前端会默默走进 else 并给出一个错误的业务结论（"无未决问题"是最危险的一种）。
const STATE_FAMILY = [
  { field: 'current', prefix: 'CURRENT_', elseMeans: 'CURRENT_NO_RECORD' },
  { field: 'issues', prefix: 'ISSUES_', elseMeans: 'ISSUES_NONE' },
  { field: 'owner', prefix: 'OWNER_', elseMeans: 'OWNER_NOT_APPLICABLE' }
]
STATE_FAMILY.forEach(function (fam) {
  const declared = Object.keys(wbStateConst)
    .filter(function (k) {
      return k.indexOf(fam.prefix) === 0
    })
    .map(function (k) {
      return wbStateConst[k]
    })
  const used = []
  ;(entrustJs.match(new RegExp("\\b" + fam.field + "\\.state\\s*===\\s*'([^']+)'", 'g')) || []).forEach(
    function (s) {
      used.push(s.match(/'([^']+)'/)[1])
    }
  )
  check(
    `[取值域] ${fam.field}.state 前端用到的字面量都在后端声明内`,
    used.length > 0 &&
      used.every(function (u) {
        return declared.indexOf(u) !== -1
      }),
    '前端 [' + used.join(',') + '] / 后端 [' + declared.join(',') + ']'
  )
  const uncovered = declared.filter(function (d) {
    return used.indexOf(d) === -1 && d !== wbStateConst[fam.elseMeans]
  })
  check(
    `[取值域] ${fam.field}.state 后端每个取值都有落地（显式比较，或明确由 else 分支承担 = ${wbStateConst[fam.elseMeans]}）`,
    uncovered.length === 0,
    '无人处理：' + uncovered.join(', ')
  )
})

// ── 投影层行为：四态各自落到不同文案，且互不冒充
function slotOf(board, key) {
  return (board.slots || []).filter(function (s) {
    return s.key === key
  })[0]
}

function rawSlot(over) {
  return Object.assign(
    {
      key: 'procurement',
      title: '采购与报价',
      available: true,
      unavailable_reason: '',
      current: { state: 'no_record', text: '', refs: [] },
      issues: { state: 'none', count: 0, items: [] },
      next_owner: { state: 'not_applicable', user_id: null, text: '' },
      updated_at: null,
      counts: { artifacts: 0, tasks: 0, open_tasks: 0, unassigned_tasks: 0 }
    },
    over || {}
  )
}

const boardEmpty = E.decorateWorkbench({ assignment_id: 1, status: 'claimed', slots: [rawSlot()] })
const projEmpty = slotOf(boardEmpty, 'procurement')
check(
  '[投影] 渲染顺序由前端配置表决定（后端顺序错了界面仍正确）',
  boardEmpty.slots.map((s) => s.key).join(',') === frontSlots.map((s) => s.key).join(',')
)
check('[投影] 暂无记录 → 「暂无记录」，不是空白也不是 0', projEmpty.fields[0].value === '暂无记录', '实际 ' + projEmpty.fields[0].value)
check('[投影] 无未决问题 → 明确回答「无未决问题」', projEmpty.fields[1].value === '无未决问题', '实际 ' + projEmpty.fields[1].value)
check(
  '[投影] 没有待推进任务 → 「不适用」（不是「尚未分配」：前者没有下一步动作）',
  projEmpty.fields[2].value === '不适用',
  '实际 ' + projEmpty.fields[2].value
)
check('[投影] 最后更新为空 → 「暂无记录」，不编一个时间', projEmpty.fields[3].value === '暂无记录')

const boardUnassigned = E.decorateWorkbench({
  slots: [rawSlot({ next_owner: { state: 'unassigned', user_id: null, text: '' } })]
})
check(
  '[投影] 有活没人 → 「尚未分配」（与「不适用」是两句话）',
  slotOf(boardUnassigned, 'procurement').fields[2].value === '尚未分配',
  '实际 ' + slotOf(boardUnassigned, 'procurement').fields[2].value
)

const boardMissing = E.decorateWorkbench({
  slots: [
    rawSlot({
      issues: { state: 'missing_info', count: 2, items: [{ kind: 'missing_field', text: '缺 rate' }] }
    })
  ]
})
const projMissing = slotOf(boardMissing, 'procurement')
check(
  '[投影] 「信息缺失」是**有内容**的状态（带条数，且不算空）',
  projMissing.fields[1].value === '信息缺失 · 2 项' && projMissing.fields[1].empty === false,
  '实际 ' + projMissing.fields[1].value + ' / empty=' + projMissing.fields[1].empty
)
check(
  '[投影] 未决明细逐条给出，不只是个数',
  projMissing.issues.length === 1 && projMissing.issues[0].text.indexOf('rate') !== -1,
  '实际 ' + JSON.stringify(projMissing.issues)
)
check(
  '[投影] 未决明细带**类别**标签（"缺项"要去补数据、"阻断"是走不通，动作不同）',
  projMissing.issues[0].kind === 'missing_field' && projMissing.issues[0].kindLabel === '缺项',
  '实际 ' + JSON.stringify(projMissing.issues[0])
)
check(
  '[投影] 未知类别不猜文案（只给描述，不臆造一个类别名）',
  E.decorateWorkbench({
    slots: [rawSlot({ issues: { state: 'present', count: 1, items: [{ kind: 'brand_new', text: 'x' }] } })]
  }).slots.filter(function (s) {
    return s.key === 'procurement'
  })[0].issues[0].kindLabel === ''
)

// 「未开放」与「空」是两条路径 —— 本片最容易做错的一处
const boardClosed = E.decorateWorkbench({
  // 理由文案由后端给（本脚本只断言"有理由"）；这里用一句中性的合成文本，
  // 不抄后端的现行措辞 —— 抄了就成了一份会过期的副本，还容易被读成契约。
  slots: [{ key: 'exceptions', title: '异常与变更', available: false, unavailable_reason: '本期未开放' }]
})
const projClosed = slotOf(boardClosed, 'exceptions')
check(
  '[投影] 未开放槽位显示「本期未开放」并给出理由',
  projClosed.tag === '本期未开放' && !!projClosed.note,
  '实际 ' + projClosed.tag + ' / ' + projClosed.note
)
check(
  '[投影] 未开放槽位**不落进空值四态**（不给四字段，避免被读成「这单没有」）',
  projClosed.fields.length === 0
)

const boardNoSlot = E.decorateWorkbench({ slots: [] })
const projNoSlot = slotOf(boardNoSlot, 'overview')
check(
  '[投影] 接口未返回槽位时如实说「接口未返回该槽位」（不伪装成正常空态）',
  projNoSlot.available === false && /接口未返回/.test(projNoSlot.note),
  '实际 ' + projNoSlot.note
)
const boardExtra = E.decorateWorkbench({
  slots: [rawSlot({ key: 'not_declared' })]
})
check(
  '[投影] 接口多返回未声明的槽位 → 不渲染（两侧不同步时不该悄悄多出一块）',
  boardExtra.slots.length === 7
)

check(
  '[投影] 归属为空的历史成果如实报数，不因"不属于任何槽位"而消失',
  E.decorateWorkbench({ slots: [], unassigned_artifact_total: 3 }).unassignedTotal === 3 &&
    /3 份/.test(E.decorateWorkbench({ slots: [], unassigned_artifact_total: 3 }).unassignedHint)
)
check(
  '[投影] 没有历史成果时不给提示（不制造噪声）',
  boardNoSlot.unassignedHint === ''
)

// ── 页面接线：取数、投影、人工落点、幂等
check('[接线] 工作台页拉取七槽位聚合接口', /fetchWorkbench\(/.test(dtJs))
check('[接线] 工作台页经 decorateWorkbench 投影（不在页面里二次判断四态）', /decorateWorkbench\(/.test(dtJs))
check(
  '[接线] 工作台页有人工落点：记录任务',
  /onRecordTask\s*\(/.test(dtJs) && /createTask\(/.test(dtJs)
)
check(
  '[接线] 记录任务带幂等键（否则一次重试会留下两条一样的任务）',
  /newIdempotencyKey\(/.test(dtJs)
)
check('[接线] 受理入口走既有认领端点', /claimAssignment\(/.test(dtJs))
check('[接线] 工作台页仍在 MIGRATED_PAGES 覆盖范围内（运行期导航治理）', /R\.guardEntry\(/.test(dtJs))

check('[模板] 七槽位按配置表渲染', /wx:for="\{\{slots\}\}"/.test(dtWxml))
check('[模板] 槽位键用于 wx:key（列表复用不错位）', /wx:key="key"/.test(dtWxml))
check(
  '[模板] 未开放槽位有独立分支（不与空态共用同一段渲染）',
  /wx:if="\{\{!item\.available\}\}"/.test(dtWxml)
)
check('[模板] 未开放槽位仍渲染理由文案', /class="slot-note"/.test(dtWxml))
check(
  '[模板] 每槽人工落点按钮绑定 onRecordTask，并经 dataset 带上任务类型',
  /bindtap="onRecordTask"/.test(dtWxml) && /data-task="\{\{item\.taskType\}\}"/.test(dtWxml)
)
check(
  // 「精确 ID + 版本」由投影层拼进 `text`（§11 按字符串逐字断言），
  // 模板侧只需渲染 `text` 并把 `id` 交给 dataset —— 两处各守一半。
  '[模板] 槽位引用渲染投影层给的一行文案与 id（模板不自己拼 "成果 #… · v…"）',
  /\{\{rf\.text\}\}/.test(dtWxml) && /data-id="\{\{rf\.id\}\}"/.test(dtWxml)
)
check(
  '[模板] 未决明细渲染类别徽标与描述（不只是把一句话贴上去）',
  /it\.kindLabel/.test(dtWxml) && /it\.text/.test(dtWxml)
)
check('[模板] 历史成果报数有专位呈现', /unassignedHint/.test(dtWxml))

// ─────────────────────────────────────────────────────────────
// 9. 成果的编辑与确认（UI-05 第二片 / ENT-023）
// ─────────────────────────────────────────────────────────────
const artJs = read(path.join(MINI, 'pages/entrust/artifact/artifact.js'))
const artPy = read(path.join(REPO, 'backend/app/modules/entrust/artifacts.py'))

/** 从 Python 源码里取 `PREFIX_X = "value"` 的 value 集合（跨语言取值域核对用） */
function pyConstKeys(src, prefix) {
  const out = []
  const re = new RegExp('^' + prefix + '[A-Z_]*\\s*=\\s*"([a-z_]+)"', 'gm')
  let m = re.exec(src)
  while (m !== null) {
    out.push(m[1])
    m = re.exec(src)
  }
  return out.sort()
}

const backendArtifactStatuses = pyConstKeys(artPy, 'STATUS_')
const frontArtifactStatuses = Object.keys(E.ARTIFACT_STATUS_LABELS || {}).sort()
check(
  '[成果] 成果状态标签覆盖后端 STATUS_* 全部取值',
  backendArtifactStatuses.join(',') === frontArtifactStatuses.join(','),
  '后端 ' + backendArtifactStatuses.join('/') + ' vs 前端 ' + frontArtifactStatuses.join('/')
)

const backendSources = pyConstKeys(artPy, 'SOURCE_')
const frontSources = Object.keys(E.REVISION_SOURCE_LABELS || {}).sort()
check(
  '[成果] 版本来源标签覆盖后端 SOURCE_* 全部取值',
  backendSources.join(',') === frontSources.join(','),
  '后端 ' + backendSources.join('/') + ' vs 前端 ' + frontSources.join('/')
)

// 状态类名必须真的存在于 app.wxss —— 模板里拼类名会让"类是否存在"变成运行期才知道，
// 写错只表现为"标签没颜色"，静态脚本抓不到。所以类名在契约层算好，这里核对它存在。
Object.keys(E.ARTIFACT_STATUS_LABELS || {}).forEach(function (st) {
  E.artifactStatusClass(st)
    .split(/\s+/)
    .forEach(function (cls) {
      check(`[成果] 状态 ${st} 的类 .${cls} 存在于 app.wxss`, appClasses.has(cls))
    })
})
check(
  '[成果] "有效"不用绿色成功态（绿色意味着"这件事成功了"，而它只是"还没被作废"）',
  E.artifactStatusClass('active').indexOf('success') === -1
)
check(
  '[接线] 成果页不硬编码成果状态字面量（只在契约层出现，页面里重复一份就会漂移）',
  Object.keys(E.ARTIFACT_STATUS_LABELS || {}).every(function (st) {
    return artJs.indexOf("'" + st + "'") === -1
  })
)

// ── 附件提取状态取值域：前端标签表 ⇄ 后端 `attachments.EXTRACT_*` 逐格对齐 ──
// 这一格曾经只有「done / 其它」两支，于是 `needs_transcription`（要找人转录）
// 与 `failed`（提取真的坏了）在界面上**长得一模一样**。这两件事该做的下一步
// 完全不同，少一档就是把它们混成一件。所以这里做**逐格**比对。
const attPy = read(path.join(REPO, 'backend/app/modules/entrust/attachments.py'))
const backendExtractStatuses = pyValueConsts(attPy, 'EXTRACT_')
const frontExtractStatuses = Object.keys(E.EXTRACT_STATUS_LABELS || {}).sort()
check(
  '[附件] 提取状态标签覆盖后端 EXTRACT_* 全部取值（七态逐格，少一个那一行就只剩原始英文码）',
  backendExtractStatuses.length > 0 &&
    backendExtractStatuses.join(',') === frontExtractStatuses.join(','),
  `后端 [${backendExtractStatuses.join('/')}] vs 前端 [${frontExtractStatuses.join('/')}]`
)
check(
  '[附件] 提取状态顺序表与标签键集合相等',
  (E.EXTRACT_STATUS_ORDER || [])
    .slice()
    .sort()
    .join(',') === frontExtractStatuses.join(','),
  `ORDER [${(E.EXTRACT_STATUS_ORDER || []).join('/')}] / LABELS [${frontExtractStatuses.join('/')}]`
)
check(
  '[附件] 「需人工转录」与「提取失败」必须是两句不同的话（旧写法把两者都显示成"未提取"）',
  E.extractStatusLabel('needs_transcription') !== E.extractStatusLabel('failed') &&
    E.extractStatusLabel('needs_transcription') !== E.extractStatusLabel('not_requested'),
  `${E.extractStatusLabel('needs_transcription')} / ${E.extractStatusLabel('failed')} / ` +
    `${E.extractStatusLabel('not_requested')}`
)
check(
  '[附件] 未知提取状态照实回显，不折成某个已知标签（折了会让人以为是"还没点提取"）',
  E.extractStatusLabel('brand_new_status').indexOf('brand_new_status') !== -1,
  E.extractStatusLabel('brand_new_status')
)
// ⚠️ 本片最要紧的一条：「**已上传**」≠「**Agent 读得到**」。
// 只有提取完成（done）的附件才有文本进 Agent 的来源目录。
;(function () {
  const done = E.decorateAttachment({
    attachment_id: 1,
    filename: 'q.txt',
    extract_status: 'done',
    size_bytes: 2048
  })
  const pendingText = E.decorateAttachment({
    attachment_id: 2,
    filename: 'scan.png',
    extract_status: 'needs_transcription',
    size_bytes: 1024
  })
  check(
    '[附件] 只有 done 才 canReference（未提取的附件对 Agent 只是"一个文件名"）',
    done.canReference === true && pendingText.canReference === false,
    `done=${done.canReference} / needs_transcription=${pendingText.canReference}`
  )
  check(
    '[附件] 引用提示要说明"为什么不能用"，且按状态给不同的下一步',
    pendingText.referenceHint.indexOf('转录') !== -1 &&
      E.decorateAttachment({ attachment_id: 3, extract_status: 'failed', extract_error: '编码错' })
        .referenceHint.indexOf('编码错') !== -1,
    pendingText.referenceHint
  )
})()

// ── 文本来源 + 引用来获取值域（HO 0917-3 裁定二第 3 条：页面必须显示来源） ──
// 两张表都是**后端取值域的镜像**。镜像的风险不是说错话，而是"少一档"：
// 少了 `manual_transcription`，界面就无法提示"重抽会覆盖人写的内容"。
const backendTextSources = pyValueConsts(attPy, 'TEXT_SOURCE_')
const frontTextSources = Object.keys(E.TEXT_SOURCE_LABELS || {}).sort()
check(
  '[附件] 文本来源标签覆盖后端 TEXT_SOURCE_* 全部取值',
  backendTextSources.length > 0 && backendTextSources.join(',') === frontTextSources.join(','),
  `后端 [${backendTextSources.join('/')}] vs 前端 [${frontTextSources.join('/')}]`
)
check(
  '[附件] 文本来源顺序表与标签键集合相等',
  (E.TEXT_SOURCE_ORDER || [])
    .slice()
    .sort()
    .join(',') === frontTextSources.join(','),
  `ORDER [${(E.TEXT_SOURCE_ORDER || []).join('/')}]`
)
check(
  '[附件] 未知文本来源照实回显，不折成某个已知来源',
  E.textSourceLabel('brand_new_source').indexOf('brand_new_source') !== -1,
  E.textSourceLabel('brand_new_source')
)
;(function () {
  // 重抽会不会覆盖"人写的"内容 —— 这条决定界面要不要先问一次
  const manual = E.decorateAttachment({
    attachment_id: 9,
    filename: 'scan.png',
    extract_status: 'done',
    text_source: 'manual_transcription'
  })
  const machine = E.decorateAttachment({
    attachment_id: 10,
    filename: 'q.txt',
    extract_status: 'done',
    text_source: 'extractor'
  })
  check(
    '[附件] 只有人工转录才需要在重抽前确认（机读提取可再生成，不必打扰用户）',
    manual.overwritesManualText === true && machine.overwritesManualText === false,
    `manual=${manual.overwritesManualText} / extractor=${machine.overwritesManualText}`
  )
  check(
    '[附件] 文本来源在界面上是两个不同的词（写成一个就分不出谁写的）',
    manual.textSourceLabel !== machine.textSourceLabel,
    `${manual.textSourceLabel} / ${machine.textSourceLabel}`
  )
  check(
    '[附件] 只有 needs_transcription / unsupported 才给人工转录入口（failed 是文件坏了，转也白转）',
    E.decorateAttachment({ attachment_id: 11, extract_status: 'needs_transcription' }).canTranscribe ===
      true &&
      E.decorateAttachment({ attachment_id: 12, extract_status: 'unsupported' }).canTranscribe ===
        true &&
      E.decorateAttachment({ attachment_id: 13, extract_status: 'failed' }).canTranscribe === false,
    'needs_transcription/unsupported/failed'
  )
})()

// ── 来源 kind：与后端 `runner.KIND_*` 逐字对齐 ──────────────────────────
// ⚠️ `attachment` 与 `attachment_text` **必须都在这张表里，且是不同的词**：
// 前者=附件存在，后者=文本被读进来了。界面把两者画成一个样子，
// 等于替 Agent 把"读了"说成"有"。
const runnerPy = read(path.join(REPO, 'backend/app/modules/entrust/agents/runner.py'))
const backendKinds = pyValueConsts(runnerPy, 'KIND_')
const frontKinds = Object.keys(E.SOURCE_KIND_LABELS || {}).sort()
check(
  '[来源] kind 标签覆盖后端 KIND_* 全部取值',
  backendKinds.length > 0 && backendKinds.join(',') === frontKinds.join(','),
  `后端 [${backendKinds.join('/')}] vs 前端 [${frontKinds.join('/')}]`
)
check(
  '[来源] 「附件」与「附件文本」在界面上必须是两个不同的词',
  E.sourceKindLabel('attachment') !== E.sourceKindLabel('attachment_text'),
  `${E.sourceKindLabel('attachment')} / ${E.sourceKindLabel('attachment_text')}`
)
check(
  '[来源] 未知 kind 照实回显，不折成"附件"',
  E.sourceKindLabel('brand_new_kind').indexOf('brand_new_kind') !== -1,
  E.sourceKindLabel('brand_new_kind')
)
;(function () {
  // 未核验来源必须**画出来**，否则"页面显示来源与未核验警告"这条等于没做
  const job = E.decorateJob({
    job_id: 1,
    status: 'succeeded',
    envelope: {
      artifact_proposals: [{ artifact_type: 'quote_parsed', payload: {}, note: '' }],
      source_refs: [
        { kind: 'attachment_text', ref: '7' },
        { kind: 'attachment', ref: '7' }
      ],
      unverified_sources: [{ kind: 'attachment', ref: '999', where: 'findings[0].source_refs' }]
    }
  })
  check(
    '[来源] 作业把源清单与未核验清单都装饰出来（含位置 where）',
    job.sources.length === 2 &&
      job.unverified.length === 1 &&
      job.unverified[0].where === 'findings[0].source_refs' &&
      job.unverified[0].text.indexOf('findings[0]') !== -1,
    JSON.stringify({ sources: job.sources.length, unverified: job.unverified })
  )
  const clean = E.decorateJob({
    job_id: 2,
    status: 'succeeded',
    envelope: { artifact_proposals: [], source_refs: [], unverified_sources: [] }
  })
  check(
    '[来源] 没有未核验来源时不无端告警（否则所有人都会学会忽略它）',
    clean.unverified.length === 0,
    JSON.stringify(clean.unverified)
  )
})()

/**
 * 字段标签必须覆盖注册表里出现的**每一个** required + optional 字段名。
 * 回退值是字段名本身（看得见但不该出现），少一个就意味着界面上会冒出英文键。
 * 反向也查：标签表多出注册表没有的名字，说明字段被删而标签没删，下次会误导。
 */
const regPy = read(path.join(REPO, 'backend/app/modules/entrust/registry.py'))
const specBlock = regPy.slice(regPy.indexOf('_SPECS: tuple[ArtifactTypeSpec, ...] = ('))
const declaredFields = []
const fieldRe = /(?:required_fields|optional_fields)=\(([^)]*)\)/g
let fm = fieldRe.exec(specBlock)
while (fm !== null) {
  const names = fm[1].match(/"[a-z_]+"/g) || []
  names.forEach(function (q) {
    declaredFields.push(q.replace(/"/g, ''))
  })
  fm = fieldRe.exec(specBlock)
}
const uniqueDeclared = Array.from(new Set(declaredFields)).sort()
const labelledFields = Object.keys(E.ARTIFACT_FIELD_LABELS || {}).sort()
const missingLabel = uniqueDeclared.filter(function (f) {
  return labelledFields.indexOf(f) === -1
})
const extraLabel = labelledFields.filter(function (f) {
  return uniqueDeclared.indexOf(f) === -1
})
check(
  `[成果] 字段中文标签覆盖注册表全部 required + optional 字段（${uniqueDeclared.length} 个）`,
  missingLabel.length === 0,
  '缺标签：' + missingLabel.join('、')
)
check(
  '[成果] 字段标签表没有注册表之外的多余项',
  extraLabel.length === 0,
  '多余：' + extraLabel.join('、')
)

// ── 字段**类型**声明（ENT-025）──────────────────────────────────────────
//
// 为什么要在静态层守住：前端的编辑形态原先只能**按运行时值的类型**推断，
// 于是**缺值**字段一律退化成单行输入框 —— 本该是列表的字段（如
// `settlement_draft.receivable_lines`）在刚创建、还没填的时候显示成文本框。
// 类型是**契约**，必须由注册表声明，且必须覆盖**全部**已知字段：
// 漏掉一个，那个字段在缺值时就会静静地退化回去。
const declaredKinds = {}
const kindPairRe = /\("([a-z_]+)",\s*(FIELD_[A-Z]+)\)/g
let km = kindPairRe.exec(specBlock)
while (km !== null) {
  declaredKinds[km[1]] = km[2]
  km = kindPairRe.exec(specBlock)
}
const kindMissing = uniqueDeclared.filter(function (f) {
  return !declaredKinds[f]
})
check(
  `[成果] 注册表为全部 ${uniqueDeclared.length} 个字段声明了类型（缺一个则该字段在缺值时退化成文本框）`,
  kindMissing.length === 0,
  '缺类型：' + kindMissing.join('、')
)

// 取值域跨语言核对：后端 `FIELD_*` 常量 ⇄ 前端 `ARTIFACT_FIELD_KINDS` 的键
const backendKindConsts = pyConstKeys(regPy, 'FIELD_')
const frontKindKeys = Object.keys(E.ARTIFACT_FIELD_KINDS || {}).sort()
check(
  '[成果] 字段类型取值域：前端 ARTIFACT_FIELD_KINDS ⇄ 后端 FIELD_* 逐字对齐',
  backendKindConsts.join(',') === frontKindKeys.join(','),
  '后端 ' + backendKindConsts.join('/') + ' vs 前端 ' + frontKindKeys.join('/')
)

// 声明的类型名必须都落在取值域内 —— 拼错类型名会被前端当作"没有契约"
const backendKindNames = {}
backendKindConsts.forEach(function (k) {
  backendKindNames['FIELD_' + k.toUpperCase()] = k
})
const badKindRefs = Object.keys(declaredKinds).filter(function (f) {
  return !backendKindNames[declaredKinds[f]]
})
check(
  '[成果] 注册表声明的类型名都在取值域内（拼错会被当成"没有契约"而回退到按值猜）',
  badKindRefs.length === 0,
  '异常：' + badKindRefs.map(function (f) { return f + '=' + declaredKinds[f] }).join('、')
)

// **行为**断言：缺值的结构化字段仍走 JSON 编辑形态 —— 这正是本片要修的那个缺口
const SPEC_SETTLE = {
  code: 'settlement_draft',
  label: '结算草稿',
  required_fields: ['receivable_lines'],
  optional_fields: ['note'],
  internal_fields: [],
  editable: true,
  field_types: { receivable_lines: 'list', note: 'text' }
}
const ART_EMPTY_LIST = {
  artifact_id: 11,
  entrustment_id: 3,
  assignment_id: 4,
  artifact_type: 'settlement_draft',
  status: 'active',
  current_revision_id: 30,
  updated_at: '2026-09-14 10:00:00',
  current_revision: {
    revision_id: 30,
    revision_no: 1,
    // `receivable_lines` **键都不存在** —— 缺值字段没有"值"可看，只能靠契约
    payload: { note: '只填了备注' },
    source: 'manual',
    note: '',
    created_at: '2026-09-14 10:00:00'
  },
  missing_fields: ['receivable_lines'],
  unknown_fields: []
}
const emptyListProj = E.decorateArtifact(ART_EMPTY_LIST, SPEC_SETTLE)
const listRow = emptyListProj.fields.filter(function (f) {
  return f.name === 'receivable_lines'
})[0]
check(
  '[成果] 缺值的列表字段仍按 JSON 编辑形态渲染（按值推断会判成标量 —— 就是原缺口）',
  !!listRow && listRow.kind === 'json' && listRow.declared === 'list',
  listRow ? listRow.kind + '/' + listRow.declared : '字段缺失'
)
check(
  '[成果] 缺值的结构化字段给出类型提示（用户无法从空白输入框看出该填 JSON 数组）',
  !!listRow && listRow.kindHint.indexOf('JSON') !== -1,
  listRow ? listRow.kindHint : '（无提示）'
)
check(
  '[成果] 未声明类型的字段仍回退到按值推断（未知字段没有契约可依，且只读）',
  emptyListProj.fields.filter(function (f) { return f.unknown }).every(function (f) {
    return f.declared === '' && f.kindHint === ''
  })
)

// 缺值时的两个**具体故障**（本片一并修）。它们与上面两条不同：上面验的是"判成什么
// 形态"，这里验的是"编辑框初始文本是什么" —— 形态对了、文本错了照样填不了。
//   · `JSON.stringify(undefined)` 返回 **JS undefined 本身**（不是字符串）⇒ setData
//     会把这个键丢掉，输入框拿到 undefined，表现为一片空白，看起来"没问题"；
//   · `JSON.stringify(null)` 返回字符串 `'null'` ⇒ 从没填过的字段看起来已经有内容。
check(
  '[成果] 缺值的结构化字段初始文本是空串（不是 JS undefined，也不是 "null"）',
  !!listRow && listRow.value === '' && typeof listRow.value === 'string',
  listRow ? JSON.stringify(listRow.value) : '字段缺失'
)
const withSettlePayload = function (payload) {
  return Object.assign({}, ART_EMPTY_LIST, {
    current_revision: Object.assign({}, ART_EMPTY_LIST.current_revision, { payload: payload })
  })
}
const nullListRow = E.decorateArtifact(
  withSettlePayload({ receivable_lines: null, note: '只填了备注' }),
  SPEC_SETTLE
).fields.filter(function (f) { return f.name === 'receivable_lines' })[0]
check(
  '[成果] 显式 null 的结构化字段同样显示为空（空就是空，不显示 "null"）',
  !!nullListRow && nullListRow.value === '' && nullListRow.empty === true,
  nullListRow ? JSON.stringify(nullListRow.value) : '字段缺失'
)
const filledListRow = E.decorateArtifact(
  withSettlePayload({ receivable_lines: ['运费 8000'], note: '只填了备注' }),
  SPEC_SETTLE
).fields.filter(function (f) { return f.name === 'receivable_lines' })[0]
check(
  '[成果] 有值的列表字段显示成缩进 JSON（多行可读，不挤成一行）',
  !!filledListRow &&
    filledListRow.value.indexOf('\n') !== -1 &&
    JSON.parse(filledListRow.value)[0] === '运费 8000',
  filledListRow ? JSON.stringify(filledListRow.value) : '字段缺失'
)

// 接线：模板里写了 `{{item.kindHint}}`，但数据层不传这个键就等于**永不渲染**。
// 这类"模板有、数据无"的失效不会报错，只能靠断言兜住。
const pageArtJs = read(path.join(MINI, 'pages/entrust/artifact/artifact.js'))
check(
  '[接线] 成果页编辑态把 declared / kindHint 一并投影进 formFields（漏传键只会让提示永不渲染）',
  pageArtJs.indexOf('declared: f.declared') !== -1 &&
    pageArtJs.indexOf('kindHint: f.kindHint') !== -1
)

// 编辑提交：结构化字段的三条硬语义（数组提交 / 清空＝移除键 / 非法 JSON 不提交）
const fillList = E.buildPayload({ note: '只填了备注' }, emptyListProj.fields, {
  receivable_lines: '["运费 8000"]',
  note: '只填了备注'
})
check(
  '[成果] 编辑后结构化字段以**数组**提交（不是字符串 "[...]" —— 内容对、类型错）',
  fillList.ok === true &&
    Array.isArray(fillList.payload.receivable_lines) &&
    fillList.payload.receivable_lines[0] === '运费 8000',
  JSON.stringify(fillList.payload.receivable_lines)
)
const clearList = E.buildPayload(
  { receivable_lines: ['旧'], note: '只填了备注' },
  emptyListProj.fields,
  { receivable_lines: '', note: '只填了备注' }
)
check(
  '[成果] 结构化字段被清空＝移除该键（不写成空数组/空对象 —— 它们在缺项判定里非空）',
  clearList.ok === true && !('receivable_lines' in clearList.payload)
)
const badJson = E.buildPayload({ note: '只填了备注' }, emptyListProj.fields, {
  receivable_lines: '[未闭合',
  note: '只填了备注'
})
check(
  '[成果] 非法 JSON 不提交并给出定位提示（不静默存成字符串）',
  badJson.ok === false && badJson.errorHint.indexOf('JSON') !== -1,
  badJson.errorHint
)
// 数字：原值**为空**（新填）时也必须还原成数字。只看原值类型时，同一个字段
// 第一次编辑存成字符串、第二次存成数字 —— 这种"两次编辑类型不同"最难查。
check(
  '[成果] 契约声明 number 时，原值为空也把新填的值还原成数字',
  E.coerceLike(null, '12', 'number') === 12 && typeof E.coerceLike(null, '12', 'number') === 'number'
)
check(
  '[成果] 声明 number 但填的不是数字时原样保留（不静默吞掉用户输入）',
  E.coerceLike(null, '一打', 'number') === '一打'
)
check(
  '[成果] 未声明类型的字段仍按原值类型还原',
  E.coerceLike(12, '13', '') === 13
)

const artWxmlForKinds = read(path.join(MINI, 'pages/entrust/artifact/artifact.wxml'))
const artWxssForKinds = read(path.join(MINI, 'pages/entrust/artifact/artifact.wxss'))
check(
  '[接线] 成果页编辑态渲染声明的类型提示（kindHint）',
  artWxmlForKinds.indexOf('item.kindHint') !== -1
)
check(
  '[接线] 类型提示类 .art-kind-hint 在本页样式中已定义（类名不存在不会报错，只会没样式）',
  cssClasses(artWxssForKinds).has('art-kind-hint')
)

// ── 投影行为 ──────────────────────────────────────────────────────────
const SPEC_QUOTE = {
  code: 'quote_parsed',
  label: '报价解析稿',
  required_fields: ['carrier', 'rate'],
  optional_fields: ['cargo_name', 'valid_until'],
  internal_fields: [],
  editable: true,
  field_types: { carrier: 'text', rate: 'number', cargo_name: 'text', valid_until: 'text' }
}
const SPEC_PROC = {
  code: 'procurement_confirm',
  label: '采购确认',
  required_fields: ['supplier', 'agreed_scope'],
  optional_fields: ['agreed_amount'],
  internal_fields: ['agreed_amount'],
  editable: true,
  field_types: { supplier: 'text', agreed_scope: 'text', agreed_amount: 'number' }
}
const ART_RAW = {
  artifact_id: 9,
  entrustment_id: 3,
  assignment_id: 4,
  artifact_type: 'quote_parsed',
  status: 'active',
  current_revision_id: 22,
  updated_at: '2026-09-14 10:00:00',
  current_revision: {
    revision_id: 22,
    revision_no: 2,
    payload: { carrier: '桂平船务', rate: 12, extra_note: 'x' },
    source: 'manual',
    note: '改价',
    created_at: '2026-09-14 10:00:00'
  },
  missing_fields: [],
  unknown_fields: ['extra_note']
}
const artProj = E.decorateArtifact(ART_RAW, SPEC_QUOTE)
check('[成果] 投影取的是生效版本的精确版本号', artProj.currentRevisionNo === 2)
check('[成果] 类型中文名来自注册表（前端不硬编码类型名）', artProj.typeLabel === '报价解析稿')
check(
  '[成果] 必填在前、选填在后（顺序由注册表决定，不由 payload 键序决定）',
  artProj.fields[0].name === 'carrier' &&
    artProj.fields[1].name === 'rate' &&
    artProj.fields[2].name === 'cargo_name',
  artProj.fields.map(function (f) { return f.name }).join(',')
)
const artLast = artProj.fields[artProj.fields.length - 1]
check(
  '[成果] 未声明的历史字段被识别出来并置于末尾（只读且会原样保留）',
  artLast.unknown === true && artLast.name === 'extra_note',
  artLast.name
)
check(
  '[成果] 数字字段在投影里仍是数字（不因表单而变成字符串）',
  artProj.fields[1].raw === 12 && artProj.fields[1].kind === 'scalar'
)
check(
  '[成果] 缺项提示点名到字段，而不是只说"信息不完整"',
  E.decorateArtifact(
    Object.assign({}, ART_RAW, { missing_fields: ['rate'] }),
    SPEC_QUOTE
  ).missingHint.indexOf('报价单价') !== -1
)
const procProj = E.decorateArtifact(
  Object.assign({}, ART_RAW, {
    artifact_type: 'procurement_confirm',
    current_revision: { revision_id: 1, revision_no: 1, payload: { supplier: 'A', agreed_amount: 5000 } }
  }),
  SPEC_PROC
)
check(
  '[成果] 内部字段被单独标注（经理能看到，但要能分辨"这栏不会到客户手里"）',
  procProj.fields.filter(function (f) { return f.internal }).length === 1 &&
    procProj.fields.filter(function (f) { return f.internal })[0].name === 'agreed_amount'
)
const voidProj = E.decorateArtifact(Object.assign({}, ART_RAW, { status: 'void' }), SPEC_QUOTE)
check(
  '[成果] 已作废：不给编辑/确认入口，且说清理由（不是把按钮藏起来让人猜）',
  voidProj.canEdit === false && voidProj.canConfirm === false && !!voidProj.statusHint
)
const noSpecProj = E.decorateArtifact(ART_RAW, null)
check(
  '[成果] 类型不在注册表中：字段契约无从校验 ⇒ 只读 + 说明理由',
  noSpecProj.registryKnown === false && noSpecProj.canEdit === false && !!noSpecProj.registryHint
)

const revProj = E.decorateRevisions(
  [
    { revision_id: 21, revision_no: 1, source: 'agent', note: '', created_at: '2026-09-13 09:00:00' },
    { revision_id: 22, revision_no: 2, source: 'manual', note: '改价', created_at: '2026-09-14 10:00:00' }
  ],
  22
)
check(
  '[成果] 「生效 / 历史」由 current_revision_id 派生（不是前端另记一份）',
  revProj[1].isCurrent === true && revProj[1].roleLabel === E.REVISION_ROLE.current &&
    revProj[0].isCurrent === false && revProj[0].roleLabel === E.REVISION_ROLE.superseded
)
check('[成果] 无备注时也给出可读文本（不留空行）', revProj[0].summary === '（无备注）')

// ── 编辑：payload 构建的三条硬语义 ────────────────────────────────────
const basePayload = { carrier: '桂平船务', rate: 12, extra_note: 'x' }
const draftsSame = { carrier: '桂平船务', rate: '12', cargo_name: '', valid_until: '', extra_note: 'x' }
const builtSame = E.buildPayload(basePayload, artProj.fields, draftsSame)
check(
  '[成果] 编辑不丢未声明的历史字段（这是"从当前 payload 增量改"而非"按表单重建"的理由）',
  builtSame.payload.extra_note === 'x'
)
check(
  '[成果] 用户没改动的数字字段类型不变（12 不会变成 "12"）',
  builtSame.payload.rate === 12
)
const builtChanged = E.buildPayload(basePayload, artProj.fields, Object.assign({}, draftsSame, { rate: '15.5' }))
check('[成果] 改动过的数字字段按数字存回', builtChanged.payload.rate === 15.5)
const builtClear = E.buildPayload(
  Object.assign({}, basePayload, { cargo_name: '玉米' }),
  artProj.fields,
  Object.assign({}, draftsSame, { cargo_name: '' })
)
check(
  '[成果] 清空标量＝移除该字段（不是留一个空字符串冒充已填）',
  !Object.prototype.hasOwnProperty.call(builtClear.payload, 'cargo_name')
)
const specJson = {
  code: 'supplier_compare',
  label: '供应报价对比',
  required_fields: ['candidates'],
  optional_fields: [],
  internal_fields: [],
  editable: true
}
const jsonProj = E.decorateArtifact(
  Object.assign({}, ART_RAW, {
    artifact_type: 'supplier_compare',
    current_revision: { revision_id: 1, revision_no: 1, payload: { candidates: [{ name: 'A' }] } }
  }),
  specJson
)
const builtJsonBad = E.buildPayload({ candidates: [{ name: 'A' }] }, jsonProj.fields, { candidates: '{oops' })
check(
  '[成果] 结构化字段 JSON 非法 ⇒ 明确报错且**不提交**（不静默吞掉用户输入）',
  builtJsonBad.ok === false && builtJsonBad.errors.length === 1 &&
    builtJsonBad.errors[0].name === 'candidates' && builtJsonBad.errorHint.indexOf('候选方案') !== -1
)
const builtJsonClear = E.buildPayload({ candidates: [{ name: 'A' }] }, jsonProj.fields, { candidates: '   ' })
check(
  '[成果] 清空结构化字段＝移除该键（不是留一个空对象，让下游以为这栏已办）',
  !Object.prototype.hasOwnProperty.call(builtJsonClear.payload, 'candidates')
)

const initialDrafts = E.fieldDrafts(artProj.fields)
check('[成果] 表单初值就是投影出来的文本（查看态与编辑态同一口径）',
  initialDrafts.rate === '12' && initialDrafts.extra_note === 'x')
check('[成果] 未改动 ⇒ 不脏', E.isArtifactDirty(artProj.fields, initialDrafts, initialDrafts) === false)
check('[成果] 改动可编辑字段 ⇒ 脏',
  E.isArtifactDirty(artProj.fields, Object.assign({}, initialDrafts, { carrier: '别家' }), initialDrafts) === true)
check('[成果] 未声明的只读字段不算改动（它根本不参与编辑）',
  E.isArtifactDirty(artProj.fields, Object.assign({}, initialDrafts, { extra_note: 'changed' }), initialDrafts) === false)

// ── 确认卡：必须点名 artifact ID 与**精确**版本 ───────────────────────
const card = E.confirmCard(artProj, 1)
check(
  '[成果] 确认卡带 target「成果 #N · vK」（PRD 187/188：对话与工作台引用同一 ID 与版本）',
  card.target.indexOf('#9') !== -1 && card.target.indexOf('v1') !== -1,
  card.target
)
check('[成果] 确认卡说明"生效版本会被绑定到该版本"，而不是笼统的"确认这一版"',
  card.body.indexOf('v1') !== -1)

// ── 页面接线：模板与脚本 ──────────────────────────────────────────────
check('[接线] 成果页同时拉取成果详情与版本历史', /fetchArtifact\(/.test(artJs) && /fetchRevisions\(/.test(artJs))
check(
  '[接线] 成果页拉注册表（字段标签与必填分组来自服务端契约，不在前端另写一套）',
  /fetchArtifactTypes\(/.test(artJs)
)
check(
  '[接线] 成果页用 buildPayload 构建提交内容（不自己拼 payload —— 那样会丢未声明字段）',
  /buildPayload\(/.test(artJs)
)
check('[接线] 成果页编辑走追加版本端点', /appendRevision\(/.test(artJs))
check('[接线] 成果页确认走 confirmArtifact', /confirmArtifact\(/.test(artJs))
check(
  '[接线] 确认卡由 confirmCard 生成（不在页面里手写文案，否则"带精确版本"这条没人守）',
  /confirmCard\(/.test(artJs)
)
// 「设为生效版本」这一次确认**必须**在页内 —— 原生弹层的确认键点不到。
// ⚠️ 断言只针对 `onConfirm` 那一段，**不是**"页面里没有 showModal"：本页别处仍有
//    弹层（如"未保存编辑时确认离开"），那是另一条路径、另一个切片的事。写成全局
//    `!/showModal/` 会立刻红，而红的原因与确认动作无关 —— 那种断言看似更严，
//    实际只是把两件事混成一件，反而更难看懂。
const artConfirmFn = (artJs.match(/onConfirm\(e\)\s*\{[\s\S]*?\n  \},/) || [''])[0]
check(
  '[接线] 「设为生效版本」走**页内确认条**（原生弹层的确认键工具点不到 ⇒' +
    '合同第 3 步"更正后跨视图可见"拿不到设备证据）',
  /onConfirmSubmit/.test(artJs) &&
    // ⚠️ 只看**调用**形态：函数注释里提到 `wx.showModal` 是在交代改动历史，
    //    不是还在用它。按纯文本匹配会把说明当成用法，于是断言永远红 ——
    //    这种"断言说得比事实多"的写法，比不加断言更糟（它会把真正的问题淹掉）。
    !/wx\.showModal\s*\(/.test(artConfirmFn) &&
    /data-act-confirm-revision-submit/.test(artWxml) &&
    /data-act-confirm-revision-cancel/.test(artWxml),
  `确认函数 ${artConfirmFn.length} 字符、其中 showModal ` +
    `${(artConfirmFn.match(/wx\.showModal\s*\(/g) || []).length} 处调用 / ` +
    `本页其它 showModal ${(artJs.match(/wx\.showModal/g) || []).length} 处（不属本条口径）`
)
check(
  '[接线] 编辑与确认各自带幂等键（网络抖动重试不会留下两条一样的版本/动作）',
  (artJs.match(/newIdempotencyKey\(/g) || []).length >= 2
)
check(
  '[接线] 保存后的提示必须说清"生效版本有没有变"',
  /生效版本仍是 v/.test(artJs) && /superseding|revision_no/.test(artJs)
)
check('[接线] 成果页入口守卫（深链/冷启动在本页自检）', /R\.guardEntry\(/.test(artJs))
check(
  '[接线] 有未保存编辑时返回要先确认（不静默丢掉用户刚敲的内容）',
  /dirty/.test(artJs) && /放弃未保存的编辑/.test(artJs)
)
check(
  '[接线] 详情页槽位引用可点，且带的是这一条的 ID 与形状（dataset 由投影层给）',
  /onOpenRef\(/.test(dtJs) && /onOpenRef/.test(dtWxml) &&
    /data-id="\{\{rf\.id\}\}"/.test(dtWxml) &&
    /data-kind="\{\{rf\.kind\}\}"/.test(dtWxml) &&
    /artifact_id=/.test(dtJs)
)

check(
  '[模板] 成果页显示生效版本号，并对"尚未确认"给出独立说法',
  /artifact\.currentRevisionNo/.test(artWxml) && /尚未确认任何版本/.test(artWxml)
)
check(
  '[模板] 确认按钮经 dataset 带上被点那一行的**精确版本号**',
  /bindtap="onConfirm"/.test(artWxml) && /data-no="\{\{item\.revisionNo\}\}"/.test(artWxml)
)
check(
  '[模板] 「设为生效版本」只在非生效版本上出现（已生效的那条不重复给入口）',
  /!item\.isCurrent/.test(artWxml)
)
check('[模板] 缺项提醒有专位呈现', /artifact\.missingHint/.test(artWxml))
check('[模板] 未声明字段的只读说明有专位呈现', /artifact\.unknownHint/.test(artWxml))
check(
  '[模板] 编辑态与查看态互斥（不会出现一边显示旧值一边可改）',
  /wx:if="\{\{!editing\}\}"/.test(artWxml) && /<view wx:else class="card">/.test(artWxml)
)
check(
  '[模板] 编辑表单的输入都绑到 onFieldInput 并带下标（动态键取值在各基础库上不稳）',
  /bindinput="onFieldInput"/.test(artWxml) && /data-idx="\{\{index\}\}"/.test(artWxml)
)
check(
  '[模板] 结构化字段走多行文本，标量走单行（不是一律文本框，让用户自己猜 JSON）',
  /item\.kind === 'json'/.test(artWxml) && /<textarea/.test(artWxml) && /<input/.test(artWxml)
)
check(
  '[模板] 必填 / 内部 / 未声明三种标记彼此可分（各带独立文字与样式）',
  /必填/.test(artWxml) && /内部/.test(artWxml) && /未声明|只读/.test(artWxml)
)

// ─────────────────────────────────────────────────────────────
// 10. 案件详情（UI-08 只读片 / ENT-030 切片四之四 · DR-0014）
// ─────────────────────────────────────────────────────────────
// 本片最容易静默失效的三件事，逐条钉住：
//   a. 案件取值域与后端 `exceptions.py` 脱节 —— 那一行只会显示原始英文值，
//      而界面看起来完全正常（和"槽位字段名对不上"是同一类失效）；
//   b. 六要素少一块 —— 页面看起来只是"这一块没数据"，没人会报这种 bug；
//   c. 拿能力位替用户下结论 —— 六个布尔值**分不出**「没授权」与「当前没有可执行
//      动作」，而说错这一句会让用户去找一个并不存在的问题。
const casePageJs = read(path.join(MINI, 'pages/entrust/case/case.js'))
const casePageWxml = read(path.join(MINI, 'pages/entrust/case/case.wxml'))
const excPy = read(path.join(REPO, 'backend/app/modules/entrust/exceptions.py'))

/**
 * 取 `PREFIX_X[: Final] = "value"` 的取值。
 *
 * 比第 9 节的 `pyConstKeys` 宽一点：案件常量带 PEP-484 标注（`X: Final = "…"`），
 * 且取值里带连字符（`review-required` / `execution-blocking`），
 * 用原函数会**一个都匹配不到** —— 那种情况下断言会拿空数组与前端比对，
 * 于是"两侧相等"在两侧都空时也成立。所以下面额外要求后端侧非空。
 */
function pyValueConsts(src, prefix) {
  const out = []
  const re = new RegExp('^' + prefix + '[A-Z0-9_]*\\s*(?::[^=\\n]+)?=\\s*"([a-z0-9_-]+)"', 'gm')
  let m = re.exec(src)
  while (m !== null) {
    if (out.indexOf(m[1]) === -1) out.push(m[1])
    m = re.exec(src)
  }
  return out.sort()
}

/** 取 `NAME [: Final] = frozenset({ "a", "b" })` 里的全部字面量 */
function pyFrozenSet(src, name) {
  const re = new RegExp('^' + name + '\\s*(?::[^=\\n]+)?=\\s*frozenset\\(([\\s\\S]*?)\\)', 'm')
  const m = re.exec(src)
  if (!m) return []
  return (m[1].match(/"([a-z0-9_-]+)"/g) || [])
    .map(function (s) {
      return s.slice(1, -1)
    })
    .sort()
}

// `KINDS` / `IMPACT_KINDS` / `STATUSES` 在后端是引用常量的 frozenset，取不到字面量，
// 故一律按**常量名前缀**取；`SEVERITIES` / `SOURCES` 是裸字面量 frozenset，只能按集合取。
const CASE_DOMAINS = [
  { note: '案件类型', front: 'CASE_KIND_LABELS', back: pyValueConsts(excPy, 'KIND_') },
  { note: '影响类型', front: 'CASE_IMPACT_LABELS', back: pyValueConsts(excPy, 'IMPACT_') },
  { note: '案件状态', front: 'CASE_STATUS_LABELS', back: pyValueConsts(excPy, 'STATUS_') },
  { note: '严重度', front: 'CASE_SEVERITY_LABELS', back: pyFrozenSet(excPy, 'SEVERITIES') },
  { note: '案件来源', front: 'CASE_SOURCE_LABELS', back: pyFrozenSet(excPy, 'SOURCES') },
  { note: '受影响项类型', front: 'CASE_TARGET_LABELS', back: pyValueConsts(excPy, 'TARGET_') },
  { note: '处置方式', front: 'CASE_DISPOSITION_LABELS', back: pyValueConsts(excPy, 'DISPOSITION_') },
  { note: '事件类型', front: 'CASE_EVENT_LABELS', back: pyValueConsts(excPy, 'EVENT_') },
  // 组织级队列（UI-04 / DR-0014 §3.1）：后端只认 scope=unclosed|all，并明确拒绝 status
  { note: '组织级队列范围', front: 'CASE_ORG_SCOPE_LABELS', back: pyFrozenSet(excPy, 'ORG_SCOPES') }
]

CASE_DOMAINS.forEach(function (d) {
  const front = Object.keys(E[d.front] || {}).sort()
  check(
    `[案件] ${d.note}标签与后端取值域逐字相等（少一个键那一行就只剩原始英文值）`,
    d.back.length > 0 && front.join(',') === d.back.join(','),
    `${d.front} 前端 [${front.join('/')}] / 后端 [${d.back.join('/')}]`
  )
})

// ─────────────────────────────────────────────────────────────
// 组织级队列（UI-04 / ENT-030 切片四之六）
//
// 取值域已在上表比对。这里钉的是**用起来**的那几件：默认范围从哪来、
// 查询参数是否恰好、以及空态措辞能不能覆写而**判定顺序不变**。
// ─────────────────────────────────────────────────────────────
check(
  '[案件] 组织级范围顺序表与标签键集合相等（顺序表里多一个值就会渲染成一个空 pill）',
  (E.CASE_ORG_SCOPE_ORDER || []).slice().sort().join(',') ===
    Object.keys(E.CASE_ORG_SCOPE_LABELS || {}).sort().join(','),
  `ORDER [${(E.CASE_ORG_SCOPE_ORDER || []).join('/')}] / LABELS [${Object.keys(
    E.CASE_ORG_SCOPE_LABELS || {}
  ).join('/')}]`
)
// 默认范围**必须**取自后端常量，不能在页面里另写一个「反正都用 unclosed」。
// 后端改默认值而界面照旧，表现是"用户以为筛的是全部，其实只有未关闭"——查不出来。
const pyDefaultScope = (excPy.match(/^ORG_SCOPE_UNCLOSED\s*(?::[^=\n]+)?=\s*"([a-z0-9_-]+)"/m) || [])[1] || ''
check(
  '[案件] 组织级默认范围等于后端 ORG_SCOPE_UNCLOSED（顺序表第一项即默认值）',
  pyDefaultScope !== '' && E.CASE_ORG_SCOPE_ORDER[0] === pyDefaultScope,
  `前端默认 ${E.CASE_ORG_SCOPE_ORDER[0]} / 后端 ${pyDefaultScope || '(未解析到)'}`
)

let orgQueryThrow = ''
try {
  E.caseOrgListQuery({})
} catch (e) {
  orgQueryThrow = String((e && e.message) || e)
}
check(
  '[案件] 组织级查询缺 orgId 时直接拒绝，不发一次注定 422 的请求',
  orgQueryThrow.length > 0,
  orgQueryThrow || '未抛出'
)

const orgQueryDefault = E.caseOrgListQuery({ orgId: 7 })
check(
  '[案件] 组织级查询恰好带 view=org / org_id / scope / page / size，且不猜 kind',
  orgQueryDefault.view === 'org' &&
    String(orgQueryDefault.org_id) === '7' &&
    orgQueryDefault.scope === E.CASE_ORG_SCOPE_ORDER[0] &&
    orgQueryDefault.page === 1 &&
    orgQueryDefault.size === 20 &&
    !Object.prototype.hasOwnProperty.call(orgQueryDefault, 'kind'),
  JSON.stringify(orgQueryDefault)
)
check(
  '[案件] 组织级查询不带 assignment_id / status（后端对视图参数混用一律 400）',
  !Object.prototype.hasOwnProperty.call(orgQueryDefault, 'assignment_id') &&
    !Object.prototype.hasOwnProperty.call(orgQueryDefault, 'status'),
  JSON.stringify(orgQueryDefault)
)
const orgQueryAll = E.caseOrgListQuery({
  orgId: 7,
  scope: 'all',
  kind: 'change_request',
  page: 3,
  size: 50
})
check(
  '[案件] 组织级查询按入参覆写 scope / kind / page / size',
  orgQueryAll.scope === 'all' &&
    orgQueryAll.kind === 'change_request' &&
    orgQueryAll.page === 3 &&
    orgQueryAll.size === 50,
  JSON.stringify(orgQueryAll)
)

const caseRow = E.decorateCaseRow({
  case_id: 5,
  assignment_id: 9,
  kind: 'exception',
  status: 'open',
  impact_kind: 'execution-blocking',
  blocking: true,
  affected_count: 2,
  updated_at: '2026-09-14 09:00:00'
})
const caseRowKeys = Object.keys(caseRow)
check(
  '[案件] 行投影**不含** severity（DR-0014 §3.2：清单里给了它，界面迟早拿它排序或加重）',
  caseRowKeys.indexOf('severity') === -1 && caseRowKeys.indexOf('severityLabel') === -1,
  caseRowKeys.join(',')
)
check(
  '[案件] 行投影区分「原值」与「展示值」：原值不带 # —— 带 # 拼进接口路径就是一条错路径',
  caseRow.caseId === '5' && caseRow.caseNo === '#5',
  `${caseRow.caseId} / ${caseRow.caseNo}`
)
check(
  '[案件] 行投影写明所属委托（UI-04 是跨委托队列，不写就只能点进去才知道是哪张单）',
  String(caseRow.assignmentText).indexOf('9') !== -1,
  caseRow.assignmentText
)
check(
  '[案件] 行投影：受影响项为 0 时如实说「未登记受影响项」，不显示 0 项',
  E.decorateCaseRow({ affected_count: 0 }).affectedText === '未登记受影响项',
  E.decorateCaseRow({ affected_count: 0 }).affectedText
)

// 「错误优先于空」这条只有一份实现：案件队列复用的是同一个 viewState，
// 只覆写空态**措辞**。下面两条一条验覆写生效、一条验覆写没有把顺序搞坏。
const vsCaseEmpty = E.viewState({ status: 200, total: 0, emptyTitle: '还没有异常或变更' })
check(
  '[案件] 空态措辞可覆写（两个队列空的时候说的不是一件事）',
  vsCaseEmpty.state === 'empty' && vsCaseEmpty.title === '还没有异常或变更',
  JSON.stringify(vsCaseEmpty)
)
check(
  '[案件] 覆写措辞后 404 仍优先于空（错误优先于空不得因措辞而失效）',
  E.viewState({ status: 404, total: 0, emptyTitle: '还没有异常或变更' }).state === 'denied'
)
check(
  '[案件] 只有一套五态裁决，没有给案件队列另写一套判定顺序',
  typeof E.caseListState === 'undefined' && typeof E.caseViewState === 'undefined'
)

const wbQueueJs = read(path.join(MINI, 'pages/entrust/workbench/workbench.js'))
check('[接线] 工作台案件队列的范围筛选由 CASE_ORG_SCOPE_ORDER 派生', /CASE_ORG_SCOPE_ORDER\.map/.test(wbQueueJs))
check('[接线] 工作台案件队列的类型筛选由 CASE_KIND_ORDER 派生', /CASE_KIND_ORDER\.map/.test(wbQueueJs))
;(E.CASE_ORG_SCOPE_ORDER || []).forEach(function (sc) {
  check(
    `[接线] 工作台页不硬编码范围字面量 '${sc}'`,
    wbQueueJs.indexOf("'" + sc + "'") === -1,
    '范围字面量只应出现在 utils/entrust.js，页面里重复一份就会漂移'
  )
})
check(
  '[接线] 工作台案件队列的空态文案与委托队列不同（复用同一句会让人以为切错了队列）',
  wbQueueJs.indexOf('还没有异常或变更') !== -1
)
check(
  '[接线] 工作台打开案件经 go()（不经裸 wx.navigateTo，页面栈预算才真的生效）',
  /go\(\s*'\/pages\/entrust\/case\/case\?case_id='/.test(wbQueueJs)
)

// 徽标类必须真实存在：类名在契约层算好、模板不拼 —— 拼错只表现为"标签没颜色"，
// 静态扫描抓不到（与第 9 节成果状态同一口径）。
;[E.CASE_STATUS_LABELS, E.CASE_IMPACT_LABELS].forEach(function (labels, i) {
  const classes = i === 0 ? E.CASE_STATUS_CLASS : E.CASE_IMPACT_CLASS
  Object.keys(labels).forEach(function (key) {
    const cls = classes[key]
    check(
      `[案件] ${i === 0 ? '状态' : '影响类型'} ${key} 有配好的徽标类`,
      typeof cls === 'string' && cls.length > 0
    )
    ;(cls || '').split(/\s+/).forEach(function (c) {
      if (c) check(`[案件] 徽标类 .${c} 存在于 app.wxss`, appClasses.has(c))
    })
  })
})

// ── 六要素：一份声明、一处产出
check(
  '[案件] 六要素的声明与产出逐项相等（少一块就是案件详情缺内容）',
  E.CASE_ELEMENTS.length === 6 &&
    E.decorateCase({})
      .blocks.map(function (b) {
        return b.key + ':' + b.mode
      })
      .join(',') ===
      E.CASE_ELEMENTS.map(function (el) {
        return el.key + ':' + el.mode
      }).join(','),
  '产出 ' +
    E.decorateCase({})
      .blocks.map(function (b) {
        return b.key
      })
      .join('/')
)

const emptyTexts = E.CASE_ELEMENTS.map(function (el) {
  return el.emptyText
})
check(
  '[案件] 六要素的"为空时怎么说"互不相同（禁止一律「暂无数据」—— 那等于没说）',
  new Set(emptyTexts).size === emptyTexts.length && emptyTexts.every(function (t) {
    return t && t.length > 4
  }),
  JSON.stringify(emptyTexts)
)

// ── 投影行为：真实载荷驱动
const CASE_FULL = {
  case: {
    case_id: 9,
    assignment_id: 3,
    org_id: 1,
    kind: 'change_request',
    title: '换船',
    cause: '原船主机故障',
    severity: 'high',
    impact_kind: 'execution-blocking',
    status: 'closed',
    owner_user_id: 7,
    raised_by_user_id: 5,
    source: 'customer',
    raised_at: '2026-09-01 10:00',
    due_at: '2026-09-05 18:00',
    proposed_action: '换用备选船舶',
    decision: { note: '同意换船', by: 2, at: '2026-09-02 09:00', basis_revision_id: 4 },
    resolution: { note: '已完成换船' },
    closure: { disposition: 'resolved', by: 2, at: '2026-09-03 12:00' },
    blocking: false,
    affected: [
      { link_id: 11, target_kind: 'task', target_id: 21, applied_revision_id: 4 },
      { link_id: 12, target_kind: 'artifact', target_id: 8, applied_revision_id: null }
    ],
    revision_no: 5,
    created_at: '2026-09-01 10:00',
    updated_at: '2026-09-03 12:00'
  },
  capabilities: {
    can_add_link: false,
    can_remove_link: false,
    can_decide: false,
    can_close: false,
    can_reopen: true,
    can_apply_change: false
  },
  events: [
    {
      seq: 1,
      event_kind: 'created',
      from_status: null,
      to_status: 'open',
      actor_user_id: 5,
      note: null,
      evidence_ref: null,
      basis_revision_id: null,
      payload: null,
      created_at: '2026-09-01 10:00'
    },
    {
      seq: 2,
      event_kind: 'closed',
      from_status: 'approved',
      to_status: 'closed',
      actor_user_id: 2,
      note: '已完成换船',
      evidence_ref: 'evid://signed-handover',
      basis_revision_id: null,
      payload: null,
      created_at: '2026-09-03 12:00'
    }
  ]
}

function caseBlock(detail, key) {
  return (detail.blocks || []).filter(function (b) {
    return b.key === key
  })[0]
}

const caseFull = E.decorateCase(CASE_FULL)
check('[案件] ① 原因取 `cause`', caseBlock(caseFull, 'cause').text === '原船主机故障')
check('[案件] ③ 拟解决方案取 `proposed_action`', caseBlock(caseFull, 'proposed_action').text === '换用备选船舶')
check(
  '[案件] ② 受影响记录逐条给出目标与"是否应用过版本"',
  caseBlock(caseFull, 'affected').items.length === 2 &&
    caseBlock(caseFull, 'affected').items[0].sub === '已应用版本 r4' &&
    caseBlock(caseFull, 'affected').items[1].sub === '未应用任何版本'
)
check(
  '[案件] ② 受影响项的目标类型译成中文（不把 task/artifact 原样交给界面）',
  caseBlock(caseFull, 'affected').items[0].text === '任务 #21' &&
    caseBlock(caseFull, 'affected').items[1].text === '成果 #8'
)
check('[案件] ④ 决定与审批给出说明/决定人/时间/依据版本', caseBlock(caseFull, 'decision').rows.length === 4)
check(
  '[案件] ④ 依据版本显示为**成果版本的 id**，不得写成 rN（那是另一个号）',
  caseBlock(caseFull, 'decision').rows[3].value === '版本 id 4'
)
check(
  '[案件] ⑤ 执行证据只取事件链的 evidence_ref（"应用过哪个版本"是另一件事）',
  caseBlock(caseFull, 'evidence').items.length === 1 &&
    caseBlock(caseFull, 'evidence').items[0].text === 'evid://signed-handover'
)
check(
  '[案件] ⑥ 结案给出处置方式的中文说法（不显示 resolved 这类原始值）',
  caseBlock(caseFull, 'closure').rows[0].value === '已解决'
)
check(
  '[案件] 处理记录逐条带时间 / 操作人 / 状态转移（缺项**不留悬空分隔符**）',
  caseFull.events[1].meta === '2026-09-03 12:00 · 操作人 用户 #2 · 已批准 → 已关闭' &&
    E.decorateCase({
      case: { case_id: 1 },
      events: [{ seq: 1, event_kind: 'created' }]
    }).events[0].meta === '',
  JSON.stringify(caseFull.events[1].meta)
)
check(
  '[案件] 缺案件号 / 委托号时给「—」而不是 `#undefined`（缺值不编造成一个值）',
  E.decorateCase({ case: {} })
    .fields.filter(function (f) {
      return f.key === 'no' || f.key === 'as'
    })
    .every(function (f) {
      return f.value === '—'
    })
)
const caseUnknown = E.decorateCase({
  case: { case_id: 1, assignment_id: 1, status: 'brand_new', kind: 'zzz', severity: 'zzz' }
})
check(
  '[案件] 未知取值保持未知（原样显示，不臆造成一个具体业务结论）',
  caseUnknown.statusLabel === 'brand_new' &&
    caseUnknown.kindLabel === 'zzz' &&
    caseUnknown.severityLabel === 'zzz' &&
    caseUnknown.statusClass === 'chip chip-muted',
  `实际 ${caseUnknown.statusLabel} / ${caseUnknown.kindLabel} / ${caseUnknown.statusClass}`
)

// 空载荷：六要素都要**说清"还没到那一步"**，而不是留白
const caseEmpty = E.decorateCase({ case: { case_id: 1, assignment_id: 1 } })
check(
  '[案件] 空载荷下六个要素都给出各自的"为空"说法（不是留白、也不是统一一句）',
  caseEmpty.blocks.every(function (b) {
    return b.emptyText && b.text === '' && b.rows.length === 0 && b.items.length === 0
  })
)
check(
  '[案件] 未指定责任人与未设置截止时间如实说"未指定/未设置"（不显示空）',
  caseEmpty.fields.filter(function (f) {
    return f.key === 'own'
  })[0].value === '未指定' &&
    caseEmpty.fields.filter(function (f) {
      return f.key === 'due'
    })[0].value === '未设置'
)

// 能力位：**不说原因**
const capsAllFalse = E.decorateCase({
  case: { case_id: 1, assignment_id: 1 },
  capabilities: {
    can_add_link: false,
    can_remove_link: false,
    can_decide: false,
    can_close: false,
    can_reopen: false,
    can_apply_change: false
  }
})
check(
  '[案件] 能力位全 false 时**不**断言"没有权限"（分不出"没授权"与"当前没有可执行动作"）',
  capsAllFalse.actionHint.indexOf('没有权限') === -1 &&
    capsAllFalse.actionHint.indexOf('无法处置') === -1,
  '实际：' + capsAllFalse.actionHint
)
check(
  '[案件] 有任一 A1 写能力时如实说"你有权限"（不是一句笼统的"按钮将出现"）',
  E.decorateCase({
    case: { case_id: 1, assignment_id: 1 },
    capabilities: { can_decide: true }
  }).actionHint === '你有处置这宗案件的权限'
)
check(
  '[案件] `can_apply_change` 参与界面结论（A2 五之四起它不再恒 false）—— ' +
    '继续排除会让「应用变更」区永远不出现，且看起来像前端没接线',
  E.decorateCase({
    case: { case_id: 1, assignment_id: 1 },
    capabilities: { can_apply_change: true }
  }).actionHint !== capsAllFalse.actionHint
)
check(
  '[案件] 只给 `can_apply_change`、没有批准快照 ⇒ `approval` 为 null（渲染条件必须两者都看）',
  E.decorateCase({
    case: { case_id: 1, assignment_id: 1 },
    capabilities: { can_apply_change: true }
  }).approval === null
)

// ── A2 五之四：复核传播 / 范围待确认 / 批准快照摘要的投影（ENT-041）─────────
const caseA2Payload = {
  case: { case_id: 9, assignment_id: 1, kind: 'change_request', status: 'approved' },
  capabilities: { can_apply_change: true },
  revalidation: [
    {
      review_key: 'artifact:12',
      area: '报价复核',
      task_type: 'quote',
      target_kind: 'artifact',
      target_id: 12,
      target_revision_id: 33,
      review_task_id: 77,
      status: 'open',
      note: null,
      created_at: '2026-09-15 10:00',
      resolved_at: null,
      resolved_by: null,
      task_title: '复核：报价复核（变更 #9）',
      task_status: 'pending'
    },
    {
      review_key: 'area:contract',
      area: '合同复核',
      task_type: 'contract',
      target_kind: null,
      target_id: null,
      target_revision_id: null,
      review_task_id: 78,
      status: 'cancelled',
      note: '复核任务被取消 ⇒ 本复核要求随之撤销（不等同于已核对）',
      created_at: '2026-09-15 10:00',
      resolved_at: '2026-09-15 11:00',
      resolved_by: 900,
      task_title: '复核：合同复核（变更 #9）',
      task_status: 'cancelled'
    }
  ],
  unconfirmed_types: ['procurement_confirm'],
  approval: {
    snapshot_version: 1,
    change_category: 'cargo_quantity_category',
    case_basis_revision_id: 33,
    targets: [
      {
        target_kind: 'artifact',
        target_id: 12,
        basis_revision_id: 33,
        change_fields: ['currency', 'amount']
      }
    ]
  }
}
const caseA2 = E.decorateCase(caseA2Payload, { procurement_confirm: '采购确认' })

check('[案件] 复核清单逐条投影（条数不得被截断）', caseA2.revalidation.length === 2)
check(
  '[案件] 复核项①：带目标与**精确版本 id**（不是 revision_no —— 两个是不同的数）',
  caseA2.revalidation[0].targetText === '成果 #12' &&
    caseA2.revalidation[0].revisionText === '版本 id 33'
)
check(
  '[案件] 复核项①状态：open ⇒ 「待复核」+ 警示色',
  caseA2.revalidation[0].statusLabel === '待复核' &&
    caseA2.revalidation[0].statusClass === 'chip chip-warn'
)
// ⚠️ 这一条是本组的重点：`cancelled` **不是** `resolved`。
// 合成一句「已处理」会让看的人以为复核已经做过了。
check(
  '[案件] 复核项②：`cancelled` 说「已撤销」而**不是**「已复核」（撤销 ≠ 核对过）',
  caseA2.revalidation[1].statusLabel === '已撤销' &&
    caseA2.revalidation[1].statusLabel !== '已复核'
)
check(
  '[案件] 无具体对象的复核项如实说「无具体对象 / 未绑定版本」（不编造一个目标）',
  caseA2.revalidation[1].targetText === '无具体对象' &&
    caseA2.revalidation[1].revisionText === '未绑定版本'
)
check(
  '[案件] 复核任务的状态要翻成中文并带标题（界面不能只拿到一个 #id）',
  caseA2.revalidation[0].taskStatusLabel === '待开始' &&
    caseA2.revalidation[0].taskTitle.indexOf('复核：') === 0
)
check(
  '[案件] 撤销原因要显示出来（否则那条 409 无人能解释）',
  caseA2.revalidation[1].note.indexOf('取消') !== -1
)
check(
  '[案件] 待确认范围用传入的类型表翻成中文（不在前端另存一份类型标签）',
  caseA2.unconfirmedTypes.length === 1 &&
    caseA2.unconfirmedTypes[0].label === '采购确认'
)
check(
  '[案件] 拿不到类型表时退回**原始 code**，不编造一个看起来对的中文名',
  E.decorateCase(caseA2Payload).unconfirmedTypes[0].label === 'procurement_confirm'
)
check(
  '[案件] 批准快照摘要：类别翻中文 + 逐目标列出**将改的字段**（apply 不接受"改成什么"，' +
    '看不到它就是盲点）',
  caseA2.approval.categoryLabel === '货物数量与品类' &&
    caseA2.approval.targets.length === 1 &&
    caseA2.approval.targets[0].fieldsText === '币种、报价金额' &&
    caseA2.approval.targets[0].basisText === '版本 id 33'
)
check(
  '[案件] 无批准快照时 `approval` 为 null（不是空摘要 —— 两者在界面上要说不同的话）',
  E.decorateCase({ case: { case_id: 1 }, capabilities: {} }).approval === null
)
check(
  '[案件] 变更类别表覆盖 DR-0016 五类（少一个键，那一类就只剩英文原值）',
  Object.keys(E.CHANGE_CATEGORY_LABELS).length === 5
)
// 任务状态标签表的键必须覆盖后端 `tasks.STATUS_*` 全部取值 —— 与成果/案件状态同一条纪律；
// 漏一个只会表现为"那一行是英文"，不会报错。
check(
  '[案件] 任务状态标签覆盖 5 个取值（pending/in_progress/waiting/done/cancelled）',
  Object.keys(E.TASK_STATUS_LABELS).length === 5 &&
    E.TASK_STATUS_LABELS.cancelled === '已取消'
)

// 渲染条件写在页面里，投影层断言不到 ⇒ 按源码文本守（与本脚本既有的做法一致）
const caseJs = read(path.join(MINI, 'pages/entrust/case/case.js'))
check(
  '[案件] 「应用变更」的渲染条件是**能力位 + 批准快照**两者都看 —— 只看能力位会渲染出' +
    '一个注定 400 的按钮',
  /canApply:\s*!!caps\.can_apply_change && !!detail && !!detail\.approval/.test(caseJs)
)
check(
  '[案件] apply 请求**只**带 `expected_revision`（修改内容只来自批准快照）',
  /applyCase\([\s\S]{0,200}expected_revision: self\.data\.revisionNo/.test(caseJs) &&
    !/applyCase\([\s\S]{0,300}payload/.test(caseJs)
)
const caseWxmlA2 = read(path.join(MINI, 'pages/entrust/case/case.wxml'))
check(
  '[案件] 模板有「应用变更」的页内确认条与锚点（不靠原生弹层：它不在渲染树里、走查点不到）',
  /data-act-apply="1"/.test(caseWxmlA2) &&
    /data-act-apply-submit="1"/.test(caseWxmlA2) &&
    /wx:if="\{\{canApply\}\}"/.test(caseWxmlA2)
)
check('[案件] 模板渲染复核传播清单与范围待确认提示', /revalidation\.length/.test(caseWxmlA2))
const artWxmlA2 = read(path.join(MINI, 'pages/entrust/artifact/artifact.wxml'))
check(
  '[成果] 模板按 `needsRevalidation` 渲染待复核徽标（并说明"依赖的事实变了"≠"内容错了"）',
  /wx:if="\{\{artifact\.needsRevalidation\}\}"/.test(artWxmlA2) &&
    /confirmBlockedHint/.test(artWxmlA2)
)

// ── 样式：投影层算出的类必须真的有定义（模板里 `class="{{it.cls}}"` 静态扫不到）
const caseClasses = new Set([
  ...appClasses,
  ...cssClasses(read(path.join(MINI, 'pages/entrust/case/case.wxss')))
])
caseFull.blocks.forEach(function (b) {
  ;(b.items || []).forEach(function (it) {
    ;(it.cls || '').split(/\s+/).forEach(function (c) {
      if (c) check(`[样式] 案件页投影产出的类 .${c} 有定义`, caseClasses.has(c))
    })
  })
})

// ── 页面接线
check('[接线] 案件页用 fetchCase 取详情（不在页面里拼 URL）', /fetchCase\(/.test(casePageJs))
check(
  '[接线] 案件页用 decorateCase 投影（六要素顺序与空态说法只有一份）',
  /decorateCase\(/.test(casePageJs)
)
check('[接线] 案件页入口守卫（深链/冷启动在本页自检）', /R\.guardEntry\(/.test(casePageJs))
check(
  '[接线] 案件页跳转统一走 go()（MIGRATED_PAGES 名单内页面不得裸调 wx 导航）',
  /R\.go\(/.test(casePageJs)
)
check(
  '[接线] 取详情走 `/exceptions/{id}`，参数名 `case_id` 与路径参数名不同名同值（DR-0014 §7）',
  /BASE \+ '\/exceptions\/'/.test(entrustJs) && /case_id/.test(casePageJs)
)
check(
  '[接线] 严重度只作为文本出现，**没有**配套的警示类名（着色会把"按严重度决定流程"请回来）',
  Object.keys(caseFull).indexOf('severityClass') === -1
)

check(
  '[模板] 案件页按 mode 三支渲染（text / rows / list），不按要素分叉',
  /item\.mode === 'text'/.test(casePageWxml) && /item\.mode === 'rows'/.test(casePageWxml)
)
check(
  '[模板] 案件页渲染顺序来自投影层（页面不自建六要素清单、只遍历 blocks）',
  // 判据是"页面自己没有那份清单"，不是"页面源码里不出现这个词" ——
  // 注释里说明"顺序来自 CASE_ELEMENTS"是**应该**出现的（初版断言正是被它误伤）。
  !/CASE_ELEMENTS\s*=\s*\[/.test(casePageJs) &&
    !/blocks\s*=\s*\[/.test(casePageJs) &&
    /wx:for="\{\{blocks\}\}"/.test(casePageWxml)
)
check('[模板] 空要素渲染 emptyText（不是留白）', /item\.emptyText/.test(casePageWxml))
check(
  '[模板] 处理记录（append-only）渲染在页面上 —— 只看当前状态会以为这宗案件从没被关过',
  /wx:for="\{\{events\}\}"/.test(casePageWxml)
)
check(
  '[模板] 阻断后果单独成行（徽标说"是什么"，这句说"会怎样"）',
  /detail\.blocking/.test(casePageWxml)
)

// ══════════════════════════════════════════════════════════════════════════════
// §11 槽位引用可点（UI-05）—— 成果与案件是**两个形状**，按槽位配置分流
// ══════════════════════════════════════════════════════════════════════════════
//
// 这一节来自一个**真会静默发生**的缺陷：`exceptions` 槽的 `refs` 是 `CaseRef`
// （键是 `case_id` / `title` / `kind` / `status` / `blocking`），而投影层早先只认
// `WorkbenchArtifactRef` 的 `artifact_id`。该槽一旦有案件就会渲染成 `undefined · v—`、
// `data-id` 为空、点了没反应 —— 而演示数据里**没有案件**，于是端到端一路绿。
// 所以这一节按**键名**断言，不按"看起来对"断言。

const slotRefKeys = Object.keys(E.REF_PROJECTORS || {})
const slotRefKinds = (E.WORKBENCH_SLOTS || []).map(function (s) {
  return s.key + '=' + s.refKind
})
check(
  '[槽位] 每个槽位都声明了 refKind，且取值都有对应的投影器（没声明=取不到引用）',
  slotRefKinds.length === 7 &&
    (E.WORKBENCH_SLOTS || []).every(function (s) {
      return slotRefKeys.indexOf(s.refKind) !== -1
    }),
  JSON.stringify(slotRefKinds)
)
check(
  '[槽位] `exceptions` 是唯一案件槽，其余槽位的引用形状是成果',
  (E.WORKBENCH_SLOTS || [])
    .filter(function (s) {
      return s.refKind === 'case'
    })
    .map(function (s) {
      return s.key
    })
    .join(',') === 'exceptions'
)

const REF_OVERVIEW_CFG = { key: 'overview', title: '委托概况', refKind: 'artifact' }
const REF_EXCEPTIONS_CFG = { key: 'exceptions', title: '异常与变更', refKind: 'case' }
const refSlot = function (cfg, refs) {
  return E.decorateSlot(cfg, { available: true, current: { refs: refs }, issues: {}, counts: {} })
}

const refArtSlot = refSlot(REF_OVERVIEW_CFG, [
  { artifact_id: 7, artifact_type: 'document', label: '报关单', revision_no: 3 }
])
check(
  '[引用] 成果槽读 `artifact_id` + `revision_no`，整行文案由投影层拼好',
  refArtSlot.refs.length === 1 &&
    refArtSlot.refs[0].kind === 'artifact' &&
    refArtSlot.refs[0].id === 7 &&
    refArtSlot.refs[0].text === '成果 #7 · 报关单 · v3',
  JSON.stringify(refArtSlot.refs)
)

const refCaseSlot = refSlot(REF_EXCEPTIONS_CFG, [
  { case_id: 12, kind: 'exception', title: '原船主机故障', status: 'open', blocking: true }
])
check(
  '[引用] 案件槽读 `case_id`（**不是** `artifact_id`），阻断与否写进文案',
  refCaseSlot.refs.length === 1 &&
    refCaseSlot.refs[0].kind === 'case' &&
    refCaseSlot.refs[0].id === 12 &&
    refCaseSlot.refs[0].text === '案件 #12 · 原船主机故障 · 异常 · 待处理 · 阻断执行',
  JSON.stringify(refCaseSlot.refs)
)
check(
  '[引用] 未阻断的案件不带"阻断执行"（这条说法不能成为固定后缀）',
  refSlot(REF_EXCEPTIONS_CFG, [{ case_id: 13, kind: 'change_request', status: 'open' }]).refs[0]
    .text === '案件 #13 · 变更请求 · 待处理'
)
check(
  '[引用] 两个形状不混装：案件形状喂成果槽不会产出"案件 #"，反之亦然',
  refSlot(REF_OVERVIEW_CFG, [{ case_id: 12, title: 'X' }]).refs[0].text.indexOf('案件 #') ===
    -1 &&
    refSlot(REF_EXCEPTIONS_CFG, [{ artifact_id: 7, label: '报关单' }]).refs[0].text.indexOf(
      '成果 #'
    ) === -1
)
check(
  '[引用] 缺 ID / 缺标题时**不产出** `#undefined`（未知不装扮成已知）',
  refSlot(REF_EXCEPTIONS_CFG, [{}]).refs[0].text.indexOf('undefined') === -1 &&
    refSlot(REF_OVERVIEW_CFG, [{}]).refs[0].text.indexOf('undefined') === -1,
  JSON.stringify(refSlot(REF_EXCEPTIONS_CFG, [{}]).refs)
)
check(
  '[引用] `refKind` 未知时给空数组，**不退回成果形状**' +
    '（退回就是把"两侧格式没对齐"说成"这个槽位没有引用"）',
  E.decorateSlot(
    { key: 'x', title: 'X', refKind: 'nope' },
    { available: true, current: { refs: [{ artifact_id: 1 }] }, issues: {}, counts: {} }
  ).refs.length === 0
)
check(
  '[引用] 槽位未开放时不给引用（"本期未开放"与"没有引用"是两件事，不叠在一张卡上）',
  E.decorateSlot(REF_EXCEPTIONS_CFG, { available: false, current: { refs: [{ case_id: 1 }] } })
    .refs.length === 0
)

// 样式：投影层算出的类必须真的有定义（模板里 `class="… {{rf.cls}}"` 静态扫不到）
const refClasses = new Set([
  ...appClasses,
  ...cssClasses(read(path.join(MINI, 'pages/entrust/detail/detail.wxss')))
])
;[refArtSlot, refCaseSlot].forEach(function (s) {
  ;(s.refs || []).forEach(function (rf) {
    ;(rf.cls || '')
      .split(/\s+/)
      .forEach(function (c) {
        if (c) check(`[样式] 槽位引用投影产出的类 .${c} 有定义`, refClasses.has(c))
      })
  })
})

check(
  '[模板] 引用行的文案只有一份（模板不拼"成果 #/案件 #"，只渲染投影层给的 text）',
  !/成果 #/.test(dtWxml) && !/案件 #/.test(dtWxml) && /\{\{rf\.text\}\}/.test(dtWxml)
)
check(
  '[接线] 详情页按 dataset 的 kind 分流：案件进案件页、成果进成果页',
  /if \(ds\.kind === 'case'\)/.test(dtJs) &&
    /case_id=/.test(dtJs) &&
    /if \(ds\.kind === 'artifact'\)/.test(dtJs)
)
check(
  '[接线] 两个分支各自提前返回，未知 kind 自然落空（不用 else 兜底）',
  /if \(ds\.kind === 'case'\)[\s\S]{0,260}?return[\s\S]{0,120}?if \(ds\.kind === 'artifact'\)/.test(
    dtJs
  )
)

// ─────────────────────────────────────────────────────────────
// 11. 案件写路径（切片四之六 / ENT-030 · DR-0013 §7.1）
// ─────────────────────────────────────────────────────────────
// 本片最危险的一件事：界面里放了一份**状态机镜像**与一份**关闭处置镜像**
// （`CASE_TRANSITIONS` / `CASE_CLOSURE_DISPOSITIONS`）。镜像的风险不是说错话，
// 而是**悄悄过期** —— 后端改了状态机、界面照旧列可选值，用户选一个必然 409 的项，
// 而界面上看不出任何异常。所以这里把两份表与后端**逐格**比对。
//
// 第二件：`caseWriteError` 的分流必须真能区分"刷新就能继续"与"再点也没用"。
// 把 409 说成"请重试"会把用户送进一个死循环，而日志里只有一串 409。
//
// 第三件：登记表单的前置校验（C1/C2）与"空值不发键"。两者都很容易写成"看起来对"：
// 前者漏一条就多一次往返，后者把 `""` 发出去会让服务端的报错与用户的操作对不上。
const caseCreateJs = read(path.join(MINI, 'pages/entrust/case-create/case-create.js'))
const caseCreateWxml = read(path.join(MINI, 'pages/entrust/case-create/case-create.wxml'))

/** 后端 `PREFIX_NAME [: Final] = "value"` → `{常量名: 值}` */
function pyConstMap(src, prefix) {
  const out = {}
  const re = new RegExp(
    '^(' + prefix + '[A-Z0-9_]*)\\s*(?::[^=\\n]+)?=\\s*"([a-z0-9_-]+)"',
    'gm'
  )
  let m = re.exec(src)
  while (m !== null) {
    out[m[1]] = m[2]
    m = re.exec(src)
  }
  return out
}

const pyNames = Object.assign(
  {},
  pyConstMap(excPy, 'KIND_'),
  pyConstMap(excPy, 'STATUS_'),
  pyConstMap(excPy, 'DISPOSITION_')
)
check(
  '[状态机] 后端常量名→值映射解析出来了（解析不到会让下面两侧都空而"相等"）',
  Object.keys(pyNames).length >= 13,
  '解析到 ' + Object.keys(pyNames).length + ' 个常量名'
)

/** 解析后端 `_STATUS_TRANSITIONS` → `{kind: {from: [to…]}}`（键是常量名，故先做映射） */
function pyTransitions(src) {
  const block = (src.match(/_STATUS_TRANSITIONS[\s\S]*?\n\}/) || [''])[0]
  const out = {}
  const kindRe = /(KIND_[A-Z_]+):\s*\{([\s\S]*?)\n    \}/g
  let km = kindRe.exec(block)
  while (km !== null) {
    const rows = {}
    const rowRe = /(STATUS_[A-Z_]+):\s*frozenset\(\{([^}]*)\}\)/g
    let rm = rowRe.exec(km[2])
    while (rm !== null) {
      rows[pyNames[rm[1]]] = (rm[2].match(/STATUS_[A-Z_]+/g) || [])
        .map(function (n) {
          return pyNames[n]
        })
        .sort()
      rm = rowRe.exec(km[2])
    }
    out[pyNames[km[1]]] = rows
    km = kindRe.exec(block)
  }
  return out
}

/** 解析后端 `_CLOSURE_DISPOSITIONS` → `{'kind/status': [disposition…]}` */
function pyClosure(src) {
  const block = (src.match(/_CLOSURE_DISPOSITIONS[\s\S]*?\n\}/) || [''])[0]
  // `_DISPOSITIONS_WITHOUT_APPLICATION` 是一个**共享常量**（三个 kind/状态组合引用它），
  // 先把它翻成**值**；引用它的那几行直接用这份值 —— 不能再把值当常量名去映射一遍
  // （那样得到的是 `undefined` 串，`.match()` 一无所获，于是"两侧都空"被当成"相等"）。
  const withoutRe =
    /^_DISPOSITIONS_WITHOUT_APPLICATION\s*(?::[^=\n]+)?=\s*frozenset\(([\s\S]*?)\)/m
  const wm = withoutRe.exec(src)
  const withoutApp = ((wm && wm[1]) || '')
    .match(/DISPOSITION_[A-Z_]+/g) || []
  const withoutAppValues = withoutApp
    .map(function (n) {
      return pyNames[n]
    })
    .sort()
  const out = {}
  const re = /\((KIND_[A-Z_]+),\s*(STATUS_[A-Z_]+)\):\s*(_DISPOSITIONS_WITHOUT_APPLICATION|frozenset\(([\s\S]*?)\))\s*,/g
  let m = re.exec(block)
  while (m !== null) {
    const values =
      m[3] === '_DISPOSITIONS_WITHOUT_APPLICATION'
        ? withoutAppValues
        : ((m[4] || '').match(/DISPOSITION_[A-Z_]+/g) || []).map(function (n) {
            return pyNames[n]
          })
    out[pyNames[m[1]] + '/' + pyNames[m[2]]] = values.slice().sort()
    m = re.exec(block)
  }
  return out
}

const backTransitions = pyTransitions(excPy)
const frontTransitions = E.CASE_TRANSITIONS || {}
check(
  '[状态机] 后端转移表解析出来了（种类数与前端一致）',
  Object.keys(backTransitions).length === Object.keys(frontTransitions).length &&
    Object.keys(backTransitions).length > 0,
  '后端 [' + Object.keys(backTransitions).join('/') + '] / 前端 [' +
    Object.keys(frontTransitions).join('/') + ']'
)

Object.keys(frontTransitions).forEach(function (kind) {
  const back = backTransitions[kind] || {}
  const front = frontTransitions[kind] || {}
  check(
    `[状态机] ${kind} 的**起始状态集合**与后端相等（多一个状态就会列出一个不存在的起点）`,
    Object.keys(back).sort().join(',') === Object.keys(front).sort().join(',') &&
      Object.keys(back).length > 0,
    '后端 [' + Object.keys(back).sort().join('/') + '] / 前端 [' +
      Object.keys(front).sort().join('/') + ']'
  )
  Object.keys(front).forEach(function (from) {
    const b = (back[from] || []).slice().sort().join(',')
    const f = (front[from] || []).slice().sort().join(',')
    check(
      `[状态机] ${kind} 从 ${from} 的出边与后端逐格相等（差一格＝界面给出一个必然 409 的选项）`,
      b === f,
      `后端 [${b}] / 前端 [${f}]`
    )
  })
})

const backClosure = pyClosure(excPy)
const frontClosure = E.CASE_CLOSURE_DISPOSITIONS || {}
check(
  '[关闭] 处置表的**键集合**（kind+状态）与后端相等（键多一个＝列出一个不可用的处置）',
  Object.keys(backClosure).sort().join(',') === Object.keys(frontClosure).sort().join(',') &&
    Object.keys(backClosure).length > 0,
  '后端 [' + Object.keys(backClosure).sort().join(' ') + '] / 前端 [' +
    Object.keys(frontClosure).sort().join(' ') + ']'
)
Object.keys(frontClosure).forEach(function (key) {
  const b = (backClosure[key] || []).slice().sort().join(',')
  const f = (frontClosure[key] || []).slice().sort().join(',')
  check(`[关闭] ${key} 的可选处置与后端相等`, b === f, `后端 [${b}] / 前端 [${f}]`)
})
// 「不允许通过驳回处置方案解除真实异常」（§3.5）—— 这条在界面上也必须成立，
// 否则用户能看到一个服务端必然拒绝的选项，而界面上没有任何线索
check(
  '[关闭] exception 在 rejected 状态**不能**以 resolved / accepted_residual 关闭（§3.5）',
  (E.caseClosureOptions('exception', 'rejected') || []).every(function (o) {
    return o.key !== 'resolved' && o.key !== 'accepted_residual'
  }),
  JSON.stringify(E.caseClosureOptions('exception', 'rejected'))
)
check(
  '[关闭] exception 在 applied 状态**可以**以 resolved 关闭（真实终结的那条路）',
  (E.caseClosureOptions('exception', 'applied') || []).some(function (o) {
    return o.key === 'resolved'
  })
)

// `decide` 不得用来关案件：后端对 `to_status=closed` 直接 409。
// 界面若不排除它，就会列出一个必然失败的选项（状态机里它确实是一条合法转移）。
check(
  '[决定] 后端 decide 对 to_status=closed 走 409 分支（界面排除 closed 的依据）',
  /to_status == STATUS_CLOSED[\s\S]{0,220}?ExceptionCaseConflictError/.test(excPy)
)
check(
  '[决定] 排除项恰好是 closed，且它真的被排除掉（改了状态机而忘了这里就会重新冒出来）',
  (E.CASE_DECIDE_EXCLUDED || []).join(',') === 'closed' &&
    Object.keys(frontTransitions).every(function (kind) {
      return Object.keys(frontTransitions[kind]).every(function (from) {
        return (E.caseDecisionOptions(kind, from) || []).every(function (o) {
          return o.key !== 'closed'
        })
      })
    })
)
check(
  '[决定] 已关闭案件不列任何决定选项（closed→open 是 reopen 的地盘，decide 走它必被拒）',
  (E.caseDecisionOptions('exception', 'closed') || []).length === 0 &&
    (E.caseDecisionOptions('change_request', 'closed') || []).length === 0
)
// `can_decide` 只看"有没有出边"，而 change_request/rejected 与 exception/applied
// 的唯一出边是 closed ⇒ 后端给 true、界面却无选项。这条断言把"必须两者同时成立"
// 钉住 —— 否则处置区会渲染出一个空的选择条。
check(
  '[决定] can_decide=true 但无选项时**不**渲染决定区（后端对它们只会给必然 409 的目标）',
  E.caseDecideAvailable({ can_decide: true }, 'change_request', 'rejected') === false &&
    E.caseDecideAvailable({ can_decide: true }, 'exception', 'applied') === false &&
    E.caseDecideAvailable({ can_decide: true }, 'exception', 'open') === true &&
    E.caseDecideAvailable({ can_decide: false, can_close: true }, 'exception', 'open') === false
)

// 选择条顺序表：与标签键集合相等（顺序表多一个值就会渲染出一个空 pill）
;[
  ['严重度', 'CASE_SEVERITY_ORDER', 'CASE_SEVERITY_LABELS'],
  ['影响类型', 'CASE_IMPACT_ORDER', 'CASE_IMPACT_LABELS']
].forEach(function (row) {
  const order = (E[row[1]] || []).slice().sort().join(',')
  const labels = Object.keys(E[row[2]] || {}).sort().join(',')
  check(`[案件] ${row[0]}顺序表与标签键集合相等`, order === labels && order !== '', `ORDER [${order}] / LABELS [${labels}]`)
})
;[
  ['案件类型', 'caseKindOptions', 'CASE_KIND_LABELS'],
  ['严重度', 'caseSeverityOptions', 'CASE_SEVERITY_LABELS'],
  ['影响类型', 'caseImpactOptions', 'CASE_IMPACT_LABELS']
].forEach(function (row) {
  const opts = E[row[1]]() || []
  check(
    `[案件] ${row[0]}选项条产出与标签表一致（页面不自己 Object.keys 标签表）`,
    opts.length > 0 &&
      opts.every(function (o) {
        return !!o.key && !!o.label
      }) &&
      opts.map(function (o) {
        return o.key
      }).sort().join(',') === Object.keys(E[row[2]] || {}).sort().join(',')
  )
})

// ── 登记表单的前置校验（C1 / C2）与"空值不发键" ────────────────────────
const okBody = E.caseCreateBody({
  kind: 'exception',
  title: '  主机故障  ',
  severity: 'high',
  impact_kind: 'execution-blocking',
  cause: '',
  proposed_action: '',
  due_at: '',
  links: [{ target_kind: 'task', target_id: '5' }]
})
check('[登记] 合法输入：摘要去空白、链接只留两个字段、空可选字段**不发键**', okBody.ok === true &&
  okBody.body.title === '主机故障' &&
  JSON.stringify(okBody.body.links) === JSON.stringify([{ target_kind: 'task', target_id: 5 }]) &&
  !Object.prototype.hasOwnProperty.call(okBody.body, 'cause') &&
  !Object.prototype.hasOwnProperty.call(okBody.body, 'proposed_action') &&
  !Object.prototype.hasOwnProperty.call(okBody.body, 'due_at'), JSON.stringify(okBody))
check('[登记] 来源固定 manual（界面登记不是 chat / Agent 建议，不能替它们记来源）',
  okBody.body.source === 'manual')
check('[登记] C1：严重度为 critical 而影响类型不是阻断 → 拦下并说明原因',
  E.caseCreateBody({ kind: 'exception', title: 'x', severity: 'critical', impact_kind: 'review-required' })
    .errors.join(' ').indexOf('阻断执行') !== -1)
check('[登记] C2：影响类型为阻断但一条受影响项都没有 → 拦下',
  E.caseCreateBody({ kind: 'exception', title: 'x', severity: 'high', impact_kind: 'execution-blocking' })
    .errors.join(' ').indexOf('受影响项') !== -1)
check('[登记] 空摘要 / 未知类型 / 未知严重度都拦下（前置检查不静默放行）',
  E.caseCreateBody({ kind: 'nope', title: '  ', severity: 'nope', impact_kind: 'nope' }).errors.length >= 3)
check('[登记] 受影响项编号非法（0 / 缺字段）拦下，不把 0 当合法编号发给服务端',
  E.caseCreateBody({
    kind: 'exception', title: 'x', severity: 'high', impact_kind: 'execution-blocking',
    links: [{ target_kind: 'task', target_id: 0 }]
  }).errors.join(' ').indexOf('受影响项') !== -1)

// ── 写失败的分流：409 必须与 403/400 分开 ──────────────────────────────
const w409 = E.caseWriteError({ httpStatus: 409, detail: '非法状态转移' })
const w403 = E.caseWriteError({ httpStatus: 403 })
const w400 = E.caseWriteError({ httpStatus: 400, detail: 'C2 违反' })
const w404 = E.caseWriteError({ httpStatus: 404 })
const wNet = E.caseWriteError(new Error('boom'))
check('[写失败] 409 → conflict=true（页面据此**重新取数**；说成"请重试"会把人送进死循环）',
  w409.conflict === true && w409.kind === 'conflict' && !!w409.hint)
check('[写失败] 403 / 400 / 404 都不是 conflict（重试改变不了结论，不能引导用户白点）',
  w403.conflict === false && w400.conflict === false && w404.conflict === false)
check('[写失败] 四类码各给不同的结论（合成一句话就等于没有分流）',
  new Set([w409.kind, w403.kind, w400.kind, w404.kind, wNet.kind]).size === 5)
check('[写失败] 400 带上服务端 detail（C1/C2 的具体说法只有服务端知道）',
  w400.hint.indexOf('C2 违反') !== -1)
check('[写失败] 404 只说"功能未开放或无权查看"，**不**替服务端断言"案件不存在"',
  w404.title.indexOf('不存在') === -1 && w404.title.indexOf('未开放') !== -1)
check('[写失败] 无 httpStatus → 网络层（且提示保留"可直接重试"）',
  wNet.kind === 'network' && wNet.hint.indexOf('重试') !== -1)

check('[处置] "无需实际应用即可终结"的三种处置单独判得出（与 resolved 的代价不同）',
  E.isDispositionWithoutApplication('cancelled') === true &&
    E.isDispositionWithoutApplication('duplicate') === true &&
    E.isDispositionWithoutApplication('resolved') === false &&
    E.isDispositionWithoutApplication('accepted_residual') === false)

// ── 候选清单归一化：两个端点两种行形状，副标题不能编造 ──────────────────
const candRows = E.decorateCaseLinkTargets(
  { items: [{ task_id: 5, task_type: 'execution', title: '安排装船' }] },
  { items: [{ artifact_id: 3, artifact_type: 'customer_quote' }] },
  [{ code: 'customer_quote', label: '客户报价' }]
)
check('[候选] 任务 / 成果归一成同一形状，且 `key` 唯一（wx:key 要用它）',
  candRows.length === 2 &&
    candRows[0].key === 'task-5' &&
    candRows[1].key === 'artifact-3' &&
    candRows[0].target_kind === 'task' &&
    candRows[1].target_kind === 'artifact')
check('[候选] 成果类型名取自**注册表**；注册表没给就显示原始代码（不编一个像样的中文名）',
  candRows[1].sub === '客户报价' &&
    E.decorateCaseLinkTargets(null, { items: [{ artifact_id: 3, artifact_type: 'customer_quote' }] })[0]
      .sub === 'customer_quote')
check('[候选] 载荷缺失时给空数组（不抛异常、也不造假行）',
  E.decorateCaseLinkTargets(null, null).length === 0)

// ── 页面接线：登记页 ───────────────────────────────────────────────────
check('[接线] 登记页经 go() / guardEntry 接入运行期治理',
  /R\.go\(/.test(caseCreateJs) && /R\.guardEntry\(/.test(caseCreateJs))
check('[接线] 登记页不再出现裸 wx.navigateTo / redirectTo / reLaunch',
  !/wx\.navigateTo\(|wx\.redirectTo\(|wx\.reLaunch\(/.test(caseCreateJs))
check('[接线] 登记页提交走 createCase + 幂等键（重试复用同一个键）',
  /createCase\(/.test(caseCreateJs) && /newIdempotencyKey\('case'\)/.test(caseCreateJs) &&
    /lastKey/.test(caseCreateJs))
check('[接线] 登记页成功后用 go() 走 replace 到案件详情（返回键不该回到已提交的表单）',
  /case_id=/.test(caseCreateJs) && /pages\/entrust\/case\/case/.test(caseCreateJs) &&
    !/wx\.redirectTo\(/.test(caseCreateJs))
check('[接线] 登记页的默认类型 / 严重度 / 影响类型都必须是**契约里存在的取值**（不是随手写的字符串）',
  ['kind', 'severity', 'impact_kind'].every(function (f) {
    const m = caseCreateJs.match(new RegExp(f + ":\\s*'([a-z_-]+)'"))
    if (!m) return false
    const src = f === 'kind' ? E.CASE_KIND_LABELS : f === 'severity' ? E.CASE_SEVERITY_LABELS : E.CASE_IMPACT_LABELS
    return Object.prototype.hasOwnProperty.call(src, m[1])
  })
)
check('[接线] 登记页模板四态齐备（缺一个分支就少一种表现，失败会被渲染成空白）',
  ['loading', 'expired', 'denied', 'error'].every(function (st) {
    return caseCreateWxml.indexOf("view === '" + st + "'") !== -1
  })
)
check('[接线] 登记页模板有选受影响项的锚点（工具没有 index 参数，靠 data-* 定位）',
  /data-kind="\{\{item\.target_kind\}\}"/.test(caseCreateWxml) &&
    /data-id="\{\{item\.target_id\}\}"/.test(caseCreateWxml))

// ── 页面接线：案件页的处置区 ───────────────────────────────────────────
check('[接线] 案件页按 canAnyAction 渲染处置区（能力位与取值域**共同**决定，不是只看 can_decide）',
  /canAnyAction/.test(casePageWxml) && /canDecide/.test(casePageJs) && /canClose/.test(casePageJs))
check('[接线] 案件页用 caseDecideAvailable 而不是裸 can_decide（后者对 change_request/rejected 给 true）',
  /caseDecideAvailable\(/.test(casePageJs))
check('[接线] 案件页五个写命令都走契约层（页面自己不拼接口路径）',
  ['addCaseLink', 'removeCaseLink', 'decideCase', 'closeCase', 'reopenCase'].every(function (fn) {
    return casePageJs.indexOf(fn + '(') !== -1
  }) && casePageJs.indexOf('/api/v1/entrust') === -1)
check('[接线] 案件页的移除按钮带 link_id 锚点（linkId 由投影层产出，不在模板里拼）',
  /data-link="\{\{item\.linkId\}\}"/.test(casePageWxml) && /linkId:\s*item\.link_id/.test(entrustJs))
check('[接线] 案件页 409 时会**重新取数**（只提示不刷新＝用户照提示重试仍是 409）',
  /if \(w\.conflict\) self\.load\(\)/.test(casePageJs))
check('[接线] 案件页关闭必须填证据引用（没有一键关闭，界面不放过空值）',
  /证据引用不能为空/.test(casePageJs))
check('[接线] 委托详情页有登记案件入口，且带 assignment_id',
  /onCreateCase\(/.test(dtJs) && /case-create\/case-create\?assignment_id=/.test(dtJs))
check('[接线] 登记入口只在已受理时出现（受理前 raise_case 必 409，不能摆一个必然失败的按钮）',
  /canCreateCase/.test(dtJs) && /status === 'claimed'/.test(dtJs))

// ---- ㊸ 主演示第 1–3 步：内置示例报价单必须与后端 canonical 夹具同源 ----
// 会话屏带了一份 canonical 样报价单的副本（"内置示例"入口要用）——因为
// `wx.chooseMessageFile` 是 OS 级原生弹层，自动走查够不着它的选择项，
// 附件入口必须另有一条页内通路（技能 miniapp-device-walkthrough 的界面要求）。
//
// 它是**生成**的不是手抄的，但生成也会过期：夹具改了、前端没重新生成，
// 于是"演示用一份、CI 用另一份" —— 两边都对不上，而且没人会立刻发现。
// 逐字节比对是唯一拦得住这种分叉的办法。
;(() => {
  const fx = path.join(REPO, 'backend/scripts/fixtures/DEMO1-canonical-sample-quotation.txt')
  if (!fs.existsSync(fx)) {
    errors.push('[夹具] 找不到后端 canonical 样报价单（前端副本没有可比对的参照物）')
    return
  }
  const want = fs.readFileSync(fx, 'utf8')
  const got = String(E.SAMPLE_QUOTE_TEXT || '')
  check(
    '[夹具] 前端内置示例报价单与后端 canonical 夹具**逐字节一致**' +
      '（两侧各写一份、还不比对，就一定会漂移）',
    got === want,
    `前端 ${got.length} 字符 / 后端 ${want.length} 字符`
  )
  check(
    '[夹具] 内置样本的文件名与磁盘上的夹具同名（上传后 name 字段才认得出是哪一份）',
    E.SAMPLE_QUOTE_FILENAME === path.basename(fx),
    `${E.SAMPLE_QUOTE_FILENAME} vs ${path.basename(fx)}`
  )
  check(
    '[夹具] 内置样本自带 Data label（合同 §3.1 要求合成件在文件里就能被认出是合成的）',
    got.indexOf('合成') !== -1 && got.indexOf('Data label') !== -1
  )
})()

// ---- ㊹ 界面文案里不得残留 Markdown 强调标记（`**x**`） ----
// 为什么值得一条检查：wxml 的**文本节点**与 js 的**字符串**都不是 Markdown 渲染器。
// 写在注释里是排版习惯（无害，本脚本会先把注释剥掉）；写进文案里就会原样显示成
// 「**样本签署**」——多了四个没人要的字符。而它是**静默**的：
// 结构 / 路由 / 类名 / 事件绑定这几道既有检查都不看文本内容，本地静态全绿、
// 真机走查也不会去挑"文案里多了两个星号"这种毛病 ⇒ 只有人肉盯屏幕才发现。
// 2026-09-17 一轮里连踩 3 处（客户已响应行、签署模式说明、发布确认条文案）⇒ 加这道闸。
// 唯一豁免：会话屏的内置样报价单 —— 它是后端夹具文件的**逐字节副本**，
// 星号属于夹具原文（上面已有逐字节比对守着），改它反而会让两侧不一致。
;(() => {
  const blank = (m) => m.replace(/[^\n]/g, ' ')
  /** 剥掉注释（保持行数不变，便于报出原始行号） */
  const strip = (src, kind) => {
    let s = src.replace(/\/\*[\s\S]*?\*\//g, blank)
    if (kind === 'js') s = s.replace(/^[ \t]*\/\/.*$/gm, '')
    if (kind === 'wxml') s = s.replace(/<!--[\s\S]*?-->/g, blank)
    return s
  }
  /** 把内置样报价单常量整段挖空（它是夹具副本，星号是原文） */
  const blankFixture = (src) => {
    const start = src.indexOf('const SAMPLE_QUOTE_TEXT')
    if (start === -1) return src
    const joinAt = src.indexOf('\n].join(', start)
    if (joinAt === -1) return src
    const lineEnd = src.indexOf('\n', joinAt + 1)
    const end = lineEnd === -1 ? src.length : lineEnd
    return src.slice(0, start) + blank(src.slice(start, end)) + src.slice(end)
  }

  const files = []
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const abs = path.join(dir, ent.name)
      if (ent.isDirectory()) walk(abs)
      else if (/\.(wxml|js|wxss)$/.test(ent.name)) files.push(abs)
    }
  }
  walk(MINI)

  const hits = []
  for (const abs of files) {
    const kind = abs.endsWith('.wxml') ? 'wxml' : abs.endsWith('.wxss') ? 'wxss' : 'js'
    let src = strip(fs.readFileSync(abs, 'utf8'), kind)
    if (kind === 'js') src = blankFixture(src)
    src.split('\n').forEach((line, i) => {
      if (line.indexOf('**') !== -1) {
        hits.push(`${path.relative(REPO, abs)}:${i + 1}  ${line.trim().slice(0, 90)}`)
      }
    })
  }
  check(
    '[文案] 界面文本里没有残留的 Markdown 星号（wxml 文本节点 / js 字符串；注释与夹具副本不计）',
    hits.length === 0,
    hits.length ? `共 ${hits.length} 处：\n      ` + hits.join('\n      ') : `扫描 ${files.length} 个文件`
  )
})()

// ─────────────────────────────────────────────────────────────
// 12. 「状态键 === 投影字段」：两侧类型必须先归一为字符串
//     来源：2026-09-17 设备走查 ㊺ 章实测到的两处真实缺陷（此前 CI 全绿）
// ─────────────────────────────────────────────────────────────
// 为什么单列一节：WXML 的 `===` 是**严格比较**，而这两侧的类型来自两个不同世界 ——
//   左：`dataset` 值 / 页面 `String(x)` 归一出来的状态键 ⇒ **恒为字符串**；
//   右：投影字段，一度原样透传 API 的 `int`（`candidate_id` / `confirmation_id`）。
// 两侧各自"看起来都对"，只有放在一起比才出错；而错法是**静默不渲染** ——
// 点下去什么都不出现、也不报错。静态门禁查得到"锚点写在模板里"，查不到运行时类型。
//
// ⚠️ 这里只登记**有故障证据**的两个配对，不做全仓模糊扫描：其余同类比较
//    （模板里还有若干 `*Id === item.<id>`）尚未逐条取证，按"未知保持未知"在案，
//    不在这里默认它们没问题 —— 但一旦取证，必须补进这张表。
const KEY_TYPE_PAIRS = [
  {
    what: '候选确认条（manager 侧运力块）',
    tpl: 'miniapp/pages/entrust/detail/detail.wxml',
    expr: 'capConfirmKey === item.candidateId',
    // 左侧**模拟 dataset**：`onOpenCapConfirm` 里是 `String(dataset 值)` ⇒ 字符串
    key: '2',
    project: () =>
      (E.decorateCapacityCandidate({ candidate_id: 2, status: 'candidate' }) || {}).candidateId,
    symptom:
      '点「确认这一条」后确认条不渲染：范围框 / 备注框 / 提交键在渲染树里都不存在',
  },
  {
    what: '只读复算的判定表',
    tpl: 'miniapp/pages/entrust/detail/detail.wxml',
    expr: 'capRecheckId === item.confirmationId',
    // 左侧**模拟页面归一**：`onCapRecheck` 里是 `String(id)`（id 来自 dataset）⇒ 字符串
    key: '3',
    project: () =>
      (E.decorateCapacityConfirmation({ confirmation_id: 3, candidate_id: 2 }) || {})
        .confirmationId,
    symptom:
      '点「复算这条确认」后判定表不出现（请求发了，页面像"复算了但没有结果"）',
  },
]
KEY_TYPE_PAIRS.forEach((p) => {
  const src = read(path.join(REPO, p.tpl))
  const found = src.indexOf(p.expr) !== -1
  check(
    `[键类型] ${p.what}：${p.tpl} 里确有判据 \`${p.expr}\``,
    found,
    found ? '' : '模板里找不到该判据原文 —— 改判据必须同步改这一节，否则本节就查了个寂寞'
  )
  const got = p.project()
  check(
    `[键类型] ${p.what}：投影字段必须是**字符串**（与恒为字符串的状态键严格比较才可能成立）`,
    typeof got === 'string',
    typeof got === 'string'
      ? `投影=${JSON.stringify(got)}（typeof ${typeof got}）`
      : `投影=${JSON.stringify(got)}（typeof ${typeof got}）而状态键是字符串 ⇒ ` +
        `\`${p.expr}\` 恒假，${p.symptom}`
  )
  check(
    `[键类型] ${p.what}：两侧在 \`===\` 下**确实相等**（不只是"都叫字符串"）`,
    got === p.key,
    got === p.key
      ? `${JSON.stringify(got)} === ${JSON.stringify(p.key)}`
      : `${JSON.stringify(got)} !== ${JSON.stringify(p.key)} ⇒ 页面静默不渲染`
  )
})

// ─────────────────────────────────────────────────────────────
// 13. 运输计划与必需任务前置（合同 §10.1 第 4 步 / BP-03 第 1 条）
//
// 本节钉的是**投影的三条取向**，不是"函数存在"：
//   · 只搬运不推导（段数 = 行数，服务端与投影都不拼"三段"这类结论）；
//   · 未知保持未知（未登记的 `mode` 原样显示，不兜底成"公路"）；
//   · 判不了的那一格必须自己说话（"没有前置"≠"前置指向谁不知道"，两种说法必须分得开）。
// 第 5 节已顺带覆盖"类名有定义 / 解构导入都有导出 / 页面已注册"，此处不重复。
// ─────────────────────────────────────────────────────────────
const planProj = E.decorateAssignmentPlan({
  assignment_id: 7,
  legs: [
    { leg_id: 1, seq: 1, mode: 'road', mode_label: '公路', from_name: '厂区', to_name: '南宁港' },
    { leg_id: 2, seq: 2, mode: 'air', mode_label: 'air', from_name: '南宁港', to_name: '贵港港' },
  ],
  task_prerequisites: [
    { task_id: 11, task_type: 'quote', title: '取两家报价', status: 'pending',
      precondition_task_id: null },
    { task_id: 12, task_type: 'purchase', title: '采购确认', status: 'done',
      precondition_task_id: 11 },
    { task_id: 13, task_type: 'contract', title: '出合同', status: 'pending',
      precondition_task_id: 99 },
  ],
})
const planEmpty = E.decorateAssignmentPlan({})
const planNull = E.decorateAssignmentPlan(null)

check('[计划] decorateAssignmentPlan 是纯函数（不碰 wx；`null` 入参也不抛）',
  typeof E.decorateAssignmentPlan === 'function' && !!planNull && planNull.legs.length === 0)

check('[计划] 每一段只拼**自己**的起终点（不是"整条链" —— 链是行间关系）',
  planProj.legs[0].routeText === '厂区 → 南宁港',
  '实际 ' + JSON.stringify(planProj.legs[0].routeText))

check('[计划] modeText **只搬运**服务端的 mode_label：未登记取值原样显示，不兜底成"公路"',
  planProj.legs[0].modeText === '公路' && planProj.legs[1].modeText === 'air',
  '实际 ' + JSON.stringify([planProj.legs[0].modeText, planProj.legs[1].modeText]))

check('[计划] 没有前置（服务端 null）⇒ 「无固定前置」',
  planProj.tasks[0].preText === '无固定前置',
  '实际 ' + JSON.stringify(planProj.tasks[0].preText))

check('[计划] 有前置 ⇒ 解析出**标题**（人读的是标题，不是 id）',
  planProj.tasks[1].preText === '前置：取两家报价',
  '实际 ' + JSON.stringify(planProj.tasks[1].preText))

check('[计划] ⭐ 前置指到本单清单之外 ⇒ 退回 `任务 #<id>`，**不编标题也不留空**',
  planProj.tasks[2].preText === '前置：任务 #99',
  '实际 ' + JSON.stringify(planProj.tasks[2].preText))

check('[计划] ⭐「无固定前置」与「前置：任务 #99」**两种说法不同**' +
  '（否则"没有前置"会被读成"前置不知道是谁"）',
  planProj.tasks[0].preText !== planProj.tasks[2].preText)

check('[计划] 每一行任务都必须有一句前置文案（判不了的那一格必须自己说话）',
  planProj.tasks.every((t) => !!t.preText),
  '实际 ' + JSON.stringify(planProj.tasks.map((t) => t.preText)))

check('[计划] 状态取 TASK_STATUS_LABELS；未登记状态原样回，不臆造成"待开始"',
  planProj.tasks[1].statusText === E.TASK_STATUS_LABELS.done &&
    E.decorateAssignmentPlan({ task_prerequisites: [{ task_id: 1, title: 'x', status: 'zzz' }] })
      .tasks[0].statusText === 'zzz')

check('[计划] 两种空态各自成态（`hasLegs` / `hasTasks` 分别判 —— ' +
  '"没有计划"要落方案、"没有任务"要派单，处置不同）',
  planEmpty.legs.length === 0 && planEmpty.tasks.length === 0 &&
    planEmpty.hasLegs === false && planEmpty.hasTasks === false,
  '实际 ' + JSON.stringify([planEmpty.legs.length, planEmpty.tasks.length,
    planEmpty.hasLegs, planEmpty.hasTasks]))

// 模板接线：三个行锚点必须真的在模板里，否则 WALK_ANCHORS 的登记会在
// `verify_miniapp.js` 里红（那是同一个事实的第二道检查，这里明写更好定位）。
const detailTpl = read(path.join(MINI, 'pages/entrust/detail/detail.wxml'))
;['data-plan-leg', 'data-plan-task', 'data-plan-task-pre'].forEach(function (attr) {
  check(`[计划] 模板 detail.wxml 里有行锚点 ${attr}`, detailTpl.indexOf(attr) !== -1)
})

// ── 截断可观测（开放项 O-9，2026-09-18 裁定）────────────────────
//
// `plan.task_prerequisites` 是**有界**读（`plan.TASK_LIMIT`）。被截时若响应里不留痕迹，
// "某条任务的前置指向没读回来的那一行"就显示成「前置：任务 #99」——
// 而上面刚好证明过：这句话与"本单真的没有前置"在投影里本来就长得不一样、
// 但在**没有 total 的响应**里它们无法被区分。所以这里钉的是：
// "被截"必须作为**上游给出的事实**进入投影，并且**能显示出来**。
const planTrunc = E.decorateAssignmentPlan({
  assignment_id: 8,
  legs: [],
  task_prerequisites: [
    { task_id: 21, task_type: 'quote', title: '报价', status: 'pending',
      precondition_task_id: null },
  ],
  task_prerequisites_total: 137,
  task_prerequisites_truncated: true,
})

check('[计划·O-9] 被截 ⇒ 投影里带截断事实与一句可显示的话（含"共多少条"）',
  planTrunc.tasksTruncated === true && /137/.test(planTrunc.tasksTruncatedText),
  '实际 ' + JSON.stringify([planTrunc.tasksTruncated, planTrunc.tasksTruncatedText]))

check('[计划·O-9] 没被截 ⇒ **不**说废话（文案是空串，不显示"共 N 条"这种噪音）',
  planProj.tasksTruncated === false && planProj.tasksTruncatedText === '',
  '实际 ' + JSON.stringify([planProj.tasksTruncated, planProj.tasksTruncatedText]))

check('[计划·O-9] 服务端没给 total 时退回"行数即总数"，且**非布尔真值不算被截**' +
  '（`' + "'yes'" + '` 这类真值若被当"有截断"，就等于把判据交给了取值形态）',
  E.decorateAssignmentPlan({
    task_prerequisites: [{ task_id: 1, title: 'x', status: 'pending', precondition_task_id: null }],
  }).tasksTruncated === false &&
    E.decorateAssignmentPlan({
      task_prerequisites: [{ task_id: 1, title: 'x', status: 'pending', precondition_task_id: null }],
      task_prerequisites_truncated: 'yes',
    }).tasksTruncated === false)

check('[计划·O-9] 模板 detail.wxml 里有这条提示的锚点与文案位',
  detailTpl.indexOf('data-plan-truncated') !== -1 &&
    detailTpl.indexOf('plan.tasksTruncatedText') !== -1)

// ─────────────────────────────────────────────────────────────
// 14. 航段命令的写侧（建段 / 改段留版本 / 版本历史 —— §10.1 第 4 步）
//
// 本节钉的是**"留版本"这件事在投影里没有被抹平**，以及**回填用的是原值不是文案**：
//   · 历史每一行是**当时**的取值快照（拿当前行去覆盖它，"2 版"照样显示、语义全错）；
//   · 改段表单回填 `modeRaw`（`air`），不是 `modeText`（标签）—— 回填标签会在库里
//     造出 `公路` 这个取值，而它与 `road` 在界面上长得一模一样；
//   · 说不了的那一格必须自己说话（没写改动说明 ≠ 说明未知 ≠ 显示空白）。
// ─────────────────────────────────────────────────────────────
const legRevProj = E.decorateLegRevisions([
  { revision_id: 91, leg_id: 8, seq: 1, mode: 'air', mode_label: 'air',
    from_name: '甲地', to_name: '乙地', revision_no: 1, change_kind: 'created',
    change_note: null, actor_user_id: 5, changed_at: '2026-09-18T00:01:02' },
  { revision_id: 92, leg_id: 8, seq: 1, mode: 'water', mode_label: '内河',
    from_name: '甲地', to_name: '丙地', revision_no: 2, change_kind: 'updated',
    change_note: '终点改到丙地', actor_user_id: 5, changed_at: '2026-09-18T00:03:04' },
])
const legRevNull = E.decorateLegRevisions(null)
const legRevEmpty = E.decorateLegRevisions([])
const legRevOdd = E.decorateLegRevisions([
  { revision_id: 93, leg_id: 8, seq: 2, mode: 'air', mode_label: 'air',
    from_name: '甲地', to_name: '乙地', revision_no: 3, change_kind: 'rewritten',
    change_note: '', changed_at: '' },
])

check('[航段历史] decorateLegRevisions 是纯函数（`null` / 空数组都不抛）',
  typeof E.decorateLegRevisions === 'function' && !!legRevNull && !!legRevEmpty &&
    legRevNull.items.length === 0)

check('[航段历史] ⭐ 每一版保留**当时**的取值（第 1 版仍是 air 与旧终点，' +
  '不是被当前值覆盖 —— 覆盖了也照样显示"2 版"）',
  legRevProj.items[0].modeText === 'air' && legRevProj.items[0].routeText === '甲地 → 乙地' &&
    legRevProj.items[1].modeText === '内河' && legRevProj.items[1].routeText === '甲地 → 丙地',
  '实际 ' + JSON.stringify(legRevProj.items.map((r) => [r.modeText, r.routeText])))

check('[航段历史] 改动类型走标签表（created→建段 / updated→改段）',
  legRevProj.items[0].kindText === '建段' && legRevProj.items[1].kindText === '改段',
  '实际 ' + JSON.stringify(legRevProj.items.map((r) => r.kindText)))

check('[航段历史] 未登记的改动类型**原样显示**（不臆造成"改段"，也不留空）',
  legRevOdd.items[0].kindText === 'rewritten',
  '实际 ' + JSON.stringify(legRevOdd.items[0].kindText))

check('[航段历史] `change_note` 为 null / 空串 ⇒ 「未写改动说明」' +
  '（既不留空白格，也不写成"说明未知"）',
  legRevProj.items[0].noteText === '未写改动说明' && legRevOdd.items[0].noteText === '未写改动说明',
  '实际 ' + JSON.stringify([legRevProj.items[0].noteText, legRevOdd.items[0].noteText]))

check('[航段历史] 版号拼成「第 N 版」（行首要有可读的版本号，不是裸数字）',
  legRevProj.items[0].revisionNoText === '第 1 版' &&
    legRevProj.items[1].revisionNoText === '第 2 版',
  '实际 ' + JSON.stringify(legRevProj.items.map((r) => r.revisionNoText)))

check('[航段历史] 时间**原样透出**（截成日期会让同一天的两版看起来一样 —— ' +
  '而"同一天改了两版还是同一版"正是这条通道要回答的问题）',
  legRevProj.items[0].changedAtText === '2026-09-18T00:01:02' &&
    legRevOdd.items[0].changedAtText === '')

check('[航段历史] 空列表与"有行"分得开（`hasItems`），且空列表时不编一个 legId',
  legRevProj.hasItems === true && legRevProj.legId === '8' &&
    legRevEmpty.hasItems === false && legRevEmpty.legId === '')

// ⭐ 回填判据：投影必须同时给出**原值**与显示文案。只给文案 ⇒ 改段表单只能拿
//   "公路"去填 `mode`，提交后库里就多出一个 `公路` 取值（与 `road` 长得一样）。
check('[计划] ⭐ 段行同时带**原值**与显示文案（改段回填要用原值，不是标签）',
  planProj.legs[0].modeRaw === 'road' && planProj.legs[0].modeText === '公路' &&
    planProj.legs[1].modeRaw === 'air' && planProj.legs[0].fromRaw === '厂区' &&
    planProj.legs[0].toRaw === '南宁港',
  '实际 ' + JSON.stringify([planProj.legs[0].modeRaw, planProj.legs[0].modeText,
    planProj.legs[1].modeRaw]))

check('[计划] 原值与显示文案确实**不同**（否则上面那条断言可能只是两个同名字段）',
  planProj.legs[0].modeRaw !== planProj.legs[0].modeText)

// 模板接线：写侧的锚点与 `data-df` 必须真的在模板里。
// ⚠️ 与 `verify_ui_interactions.js` 的分工：那边查"每个 data-df 都被 handler 认领"
//    （认领表在页面 .js 里），这里查"锚点存在"（走查脚本要用它定位）。
;['data-act-leg-open', 'data-act-leg-edit', 'data-act-leg-hist', 'data-plan-rev',
  'data-act-leg-submit', 'data-act-leg-cancel', 'data-act-leg-mode',
  'data-df="leg-seq"', 'data-df="leg-mode"', 'data-df="leg-from"',
  'data-df="leg-to"', 'data-df="leg-note"'].forEach(function (attr) {
  check(`[航段命令] 模板 detail.wxml 里有写侧锚点 ${attr}`, detailTpl.indexOf(attr) !== -1)
})

// ─────────────────────────────────────────────────────────────
// 合同派生与签署证据的投影（§10.1 第 7 步 / BP-03 第 8 条 / D1-08）
//
// 本节钉的是三件「看起来一样但其实不同」的事：
//   · 派生**绑版本**：报价版本与合同版本各自成串 —— 只写「已派生」，
//     等于把「这份合同是从哪一版来的」留在库里没人回答；
//   · 来源行**原值与文案两份**（界面显示「已接受报价」、比对用 `accepted_release`）；
//   · 证据行**必须带版本串**：「有证据」与「证据签在哪个版本上」是两个问题。
// ⚠️ 模板接线这一节只查锚点**存在**（存在 ≠ 走查跑过；取证在 ㊾ 章）。
// ─────────────────────────────────────────────────────────────
const contractProj = E.decorateContractDerivation({
  derivation_id: 7, assignment_id: 3, entrustment_id: 2, release_id: 12, response_id: 5,
  quote_artifact_id: 4, quote_revision_id: 9, quote_revision_no: 3,
  contract_artifact_id: 8, contract_revision_id: 11, contract_revision_no: 1,
  template_code: 'CN_TRANSPORT', template_version: 'v1',
  effective_date: '2026-09-20', derived_by: 6, derived_at: '2026-09-18T10:00:00',
  note: null,
  absent_quote_fields: ['excludes', 'note'],
  field_sources: [
    { field_path: 'clauses[1].text', value_text: '金额 36000 CNY',
      source_kind: 'accepted_release', source_kind_text: '已接受报价',
      source_ref: 'release:12@v3' },
    // 「运输范围」一条条款由**多个航段**构成 ⇒ 同一字段多行来源
    { field_path: 'route', value_text: '南宁仓 → 贵港码头',
      source_kind: 'leg', source_kind_text: '航段', source_ref: 'leg:4' },
    { field_path: 'route', value_text: '贵港码头 → 梧州码头',
      source_kind: 'leg', source_kind_text: '航段', source_ref: 'leg:5' }
  ]
})
const contractProjEmpty = E.decorateContractDerivation(null)

check('[合同] decorateContractDerivation 是纯函数（`null` 入参也不抛，且不编版本号）',
  typeof E.decorateContractDerivation === 'function' && !!contractProjEmpty &&
    contractProjEmpty.sources.length === 0 && contractProjEmpty.hasAbsent === false,
  '实际 ' + JSON.stringify(contractProjEmpty))

check('[合同] ⭐ 报价与合同的**精确版本各自成串**（D1-08 的 inspection 面 —— ' +
  '只写「已派生」就把来源留在库里没答）',
  contractProj.quoteRevisionNoText === '报价 v3' &&
    contractProj.contractRevisionNoText === '第 1 版',
  '实际 ' + JSON.stringify([contractProj.quoteRevisionNoText, contractProj.contractRevisionNoText]))

check('[合同] 来源行**同时给原值与文案**，且两者不同（界面显示文案、比对用原值）',
  contractProj.sources[0].sourceKind === 'accepted_release' &&
    contractProj.sources[0].sourceKindText === '已接受报价' &&
    contractProj.sources[0].sourceKind !== contractProj.sources[0].sourceKindText,
  '实际 ' + JSON.stringify(contractProj.sources[0]))

check('[合同] ⭐ 同一字段可以有多行来源，且每行的 `rowKey` 唯一 —— ' +
  '模板 `wx:key` 若用 `fieldPath`，重复的字段会被静默少渲染（"来源行数不对"正是要抓的那类问题）',
  contractProj.sources.length === 3 && contractProj.sourceCount === 3 &&
    new Set(contractProj.sources.map((s) => s.rowKey)).size === 3,
  '实际 ' + JSON.stringify(contractProj.sources.map((s) => s.rowKey)))

check('[合同] 缺失项**如实列出**（列出来而不是编默认值 —— 这是「派生」与「编造」的分界）',
  contractProj.hasAbsent === true && contractProj.absentText === 'excludes、note',
  '实际 ' + JSON.stringify([contractProj.hasAbsent, contractProj.absentText]))

check('[合同] 备注为空写「未写备注」（不是空白格、也不是「说明未知」）',
  contractProj.noteText === '未写备注', '实际 ' + JSON.stringify(contractProj.noteText))

check('[合同] 生效日缺失写「未提供」（缺值必须自己说话，不留空）',
  E.decorateContractDerivation({ effective_date: null }).effectiveDateText === '未提供')

const sigProj = E.decorateSignatureEvidence({
  contract_artifact_id: 8,
  has_items: true,
  disclaimer: '签署证据按样件标注（不构成实时电子签署）记录，不构成实时电子签署。',
  kind_options: [
    { value: 'sample_scan', label: '样件扫描件' },
    { value: 'written_confirmation', label: '书面确认' },
    { value: 'manual_record', label: '人工记录' }
  ],
  items: [
    { evidence_id: 21, contract_revision_no: 1, revision_no_text: '第 1 版',
      evidence_kind: 'sample_scan', evidence_kind_text: '样件扫描件',
      mode: 'labeled_sample', mode_text: '样件标注（不构成实时电子签署）',
      note: '', note_text: '未写说明', recorded_by: 6, recorded_at: '2026-09-18T10:05:00' }
  ]
})
const sigProjEmpty = E.decorateSignatureEvidence(null)

check('[签署证据] decorateSignatureEvidence 是纯函数（`null` 入参也不抛）',
  typeof E.decorateSignatureEvidence === 'function' && !!sigProjEmpty &&
    sigProjEmpty.items.length === 0 && sigProjEmpty.hasItems === false)

check('[签署证据] ⭐ 每一行都带**版本串** —— 「有证据」与「证据签在哪个版本上」是两个问题',
  sigProj.items[0].revisionNoText === '第 1 版',
  '实际 ' + JSON.stringify(sigProj.items[0].revisionNoText))

check('[签署证据] 形态给原值与文案两份（界面显示文案、比对用原值）',
  sigProj.items[0].kind === 'sample_scan' && sigProj.items[0].kindText === '样件扫描件' &&
    sigProj.items[0].kind !== sigProj.items[0].kindText)

check('[签署证据] ⭐ 常驻标注没被丢掉（「不构成实时电子签署」必须在文案里 —— ' +
  '页面上不写这句，「有证据」就会被读成「签过了」）',
  sigProj.items[0].modeText.indexOf('电子签署') !== -1 &&
    sigProj.disclaimer.indexOf('电子签署') !== -1,
  '实际 ' + JSON.stringify([sigProj.items[0].modeText, sigProj.disclaimer]))

check('[签署证据] 说明为空写「未写说明」（不留空白格）',
  sigProj.items[0].noteText === '未写说明')

check('[签署证据] 形态选项**来自服务端**（三项都非空；前端不另存一份取值域）',
  sigProj.kindOptions.length === 3 && sigProj.hasKindOptions === true &&
    sigProj.kindOptions.every(function (o) { return !!o.key && !!o.label && o.key !== o.label }),
  '实际 ' + JSON.stringify(sigProj.kindOptions))

// 模板接线：合同卡与证据表单的锚点必须真的在模板里（存在 ≠ 走查跑过）。
;['data-act-contract-derive', 'data-act-contract-sources', 'data-act-sig-open',
  'data-act-sig-kind', 'data-act-sig-submit', 'data-df="sig-note"',
  'sig.disclaimer', 'contract-card'].forEach(function (attr) {
  check(`[合同] 模板 detail.wxml 里有锚点 ${attr}`, detailTpl.indexOf(attr) !== -1)
})

// 页面 handler 的认领表：五条映射一条都不能少（少一条＝那一格静默空转）
const detailJs = read(path.join(MINI, 'pages/entrust/detail/detail.js'))
;["'leg-seq': 'legForm.seq'", "'leg-mode': 'legForm.mode'", "'leg-from': 'legForm.from'",
  "'leg-to': 'legForm.to'", "'leg-note': 'legForm.note'"].forEach(function (pair) {
  check(`[航段命令] 页面 data-df 认领表里有 ${pair}`, detailJs.indexOf(pair) !== -1)
})

// 空改动的本地闸门：页面必须先把"没改动"这件事说清（服务端也会 400），
// 而不是把注定失败的请求发出去、再把服务端的拒绝显示给用户。
check('[航段命令] 页面在本地就拦住"空改动"（改段一个字段都没变时不发请求）',
  detailJs.indexOf('完全相同') !== -1 && detailJs.indexOf('buildLegBody') !== -1)

// ─────────────────────────────────────────────────────────────
// 「读方向」也要有闸门：前端读的字段必须后端**真的声明过**
//
// 上面第「载荷字段名」那段钉的是**反方向**（后端声明了，前端有没有读）。
// 这里钉的是把本轮咬了两口的那一个方向：**前端读了 `x.foo`，而响应模型里没有 `foo`**。
//
// 为什么它必然是"静默"的：读一个不存在的键不抛错，值就是 `undefined`，
// 随后落进 `|| ''` / `|| null` / `|| []` 之类的兜底 ⇒ 症状是**某个集合恒为空**。
// 而人看到"空"的第一反应是查业务（"是不是还没有客户接受？"），不是查键名。
//
// 实证（2026-09-18，同一轮咬了两口）：第 7 步的派生前置判据读
// `release.artifact_type`，而 `OfferReleaseOut` 当时**没有**这个字段 ——
// 它只藏在 `customer_snapshot` 里（客户视角的 `OfferReleaseCustomerOut` 反倒给了
// 顶层同名字段）。⇒「客户已接受的对客报价」恒为空 ⇒ 前端不给派生入口、
// 走查 ㊾ 章整章 `NOT_RUN`，而夹具其实铺得好好的。
//
// 判据：对每一对 (函数, 响应模型)，函数体里所有 `alias.字段` 的读取都必须落在
// 那**份**模型的声明字段里 —— 或者落进该对的 `allow`（**必须给理由**）。
// ⚠️ 登记表是**白名单**：新写一个消费后端载荷的投影/挑选用函数，就要在这里登记；
// 没登记的函数不受保护（这一点与「新增取数函数必须登记 e2e 桩」同一性质）。
// ─────────────────────────────────────────────────────────────
const WIRE_READS = [
  // 挑「客户已接受的对客报价」——**就是咬到我的那一个**，登记它才有意义
  { fn: 'pickAcceptedQuoteRelease', model: 'OfferReleaseOut', alias: 'r', allow: {} },
  { fn: 'decorateManagerRelease', model: 'OfferReleaseOut', alias: 'd', allow: {} },
  { fn: 'decorateContractDerivation', model: 'ContractDerivationOut', alias: 'd', allow: {} },
  { fn: 'decorateSignatureEvidence', model: 'SignatureEvidenceListOut', alias: 'd', allow: {} }
]

/** 取一个 `function NAME(...) { … }` 的函数体源码（按大括号配对，不靠缩进猜测）。 */
function fnBody(src, name) {
  const at = src.indexOf('function ' + name + '(')
  if (at === -1) return ''
  const open = src.indexOf('{', at)
  if (open === -1) return ''
  let depth = 0
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++
    else if (src[i] === '}') {
      depth--
      if (depth === 0) return src.slice(open, i + 1)
    }
  }
  return src.slice(open)
}

const wireSummary = []
WIRE_READS.forEach(function (pair) {
  const body = fnBody(entrustJs, pair.fn)
  const declared = schemaFields(schPy, pair.model)
  check(
    `[读方向] 抽到了 ${pair.fn} 的函数体与 ${pair.model} 的声明字段` +
      '（抽不到会让下面两条断言变成空转）',
    body.length > 0 && declared.length > 0,
    '函数体 ' + body.length + ' 字符 / 字段 ' + declared.length + ' 个'
  )
  const reads = {}
  const re = new RegExp('\\b' + pair.alias + '\\.([a-zA-Z_][a-zA-Z0-9_]*)', 'g')
  let m
  while ((m = re.exec(body))) reads[m[1]] = true
  const keys = Object.keys(reads)
  const missing = keys.filter(function (k) {
    return declared.indexOf(k) === -1 && !pair.allow[k]
  })
  check(
    `[读方向] ${pair.fn} 读的字段都在 ${pair.model} 里声明过` +
      '（读了不存在的键不会报错，只会让某个集合恒为空）',
    keys.length > 0 && missing.length === 0,
    missing.length
      ? '未声明：' + missing.join(', ') + '｜已声明：' + declared.join(', ')
      : '读了 ' + keys.length + ' 个字段'
  )
  // 豁免必须带理由；豁免表里若出现**已经声明过**的键，说明那条豁免过期了（该删）
  const stale = Object.keys(pair.allow).filter(function (k) {
    return declared.indexOf(k) !== -1
  })
  check(`[读方向] ${pair.fn} 的豁免表没有过期条目（已声明的键不该再豁免）`,
    stale.length === 0, stale.join(', '))
  wireSummary.push(pair.fn + '→' + pair.model + '(' + keys.length + ')')
})
check('[读方向] 本节确实覆盖到了消费后端载荷的函数（否则上面的断言全是空转）',
  WIRE_READS.length >= 4, wireSummary.join(' / '))

// ---- 装饰层与消费层之间的**键名契约**（不是后端声明，是本层内部）----
//
// ⚠️ 为什么单独一节：上面那节的判据是"读的键在后端模型里声明过"，它抓不到
// **投影层改名后的**读法。2026-09-18 的真例：
//
//   `decorateManagerRelease()` 把发布行的版本号改名成 `revisionNo`（驼峰），
//   而消费者 `releasedRevisionOf(releases, no)` 读的是 `r.revision_no`（下划线）。
//   两处**各自都"有出处"**（一个来自 API 原文、一个来自本层改名）⇒ 读方向门禁
//   全绿；`Number(undefined)` 是 `NaN` ⇒ 恒挑不到 ⇒ 成果页版本行**恒显示未发布**，
//   并且**继续给**「发布这一版」的入口。静态门禁与 e2e 全绿，是 ㊹ 章**设备走查**
//   抓到的（它的夹具里从来没有"已发布"这一形态）。
//
// ⇒ 判据落**行为**：拿一个真实的 API 原文行走一遍装饰，再问消费者能不能挑到它。
//   这是纯函数，不接触 wx，能在 Node 里直接算。
const relRaw = {
  release_id: 9,
  assignment_id: 3,
  artifact_id: 7,
  revision_no: 2,
  status: 'released',
  authorized_attachment_ids: [],
  customer_snapshot: { payload: { amount: '1.00' } }
}
const relDec = E.decorateManagerRelease(relRaw)
check('[装饰契约] decorateManagerRelease 把 `revision_no` 改名为 `revisionNo`'
  + '（这是本层的事实，消费者必须按它读）',
  relDec.revisionNo === 2 && relDec.revision_no === undefined,
  'revNo=' + JSON.stringify(relDec.revisionNo) + ' revision_no=' + JSON.stringify(relDec.revision_no))
const hit = E.releasedRevisionOf([relDec], 2)
check('[装饰契约] `releasedRevisionOf` 能用**装饰后**的行挑到该版本'
  + '（挑不到 ⇒ 成果页恒显示"未发布"且继续给发布入口）',
  !!hit && hit.status === 'released',
  '命中的行=' + JSON.stringify(hit && hit.releaseId))
check('[装饰契约] 同一个函数也认 API 原文的 `revision_no`（它是导出的，调用方可能递原文）',
  !!E.releasedRevisionOf([relRaw], 2),
  '原文行命中=' + JSON.stringify(!!E.releasedRevisionOf([relRaw], 2)))
check('[装饰契约] 版本号不匹配时**返回 null**（不许退化成"总有发布"）',
  E.releasedRevisionOf([relDec], 3) === null,
  'no=3 ⇒ ' + JSON.stringify(E.releasedRevisionOf([relDec], 3)))

// ⭐⭐ **第二个缺陷 —— 修好"恒假"之后才暴露出来的那一半：跨成果误判。**
//
// 发布记录是按**授权**取的（`/entrustments/{eid}/offer-releases`），同一授权下可能有
// **多个成果**的发布；而版本号只在成果**内部**唯一（`v1` 每份成果都有）。
// 只比版本号 ⇒ "别的成果发过 v1"被读成"**本成果的 v1 发过了**" ⇒ 版本行显示已发布、
// **不给「发布这一版」入口** ⇒ 用户发不出去，且页面上看不出为什么（**静默**）。
//
// ⚠️ 顺序很重要：先把"恒假"（读错键名）修掉，这个"跨成果误判"才**第一次**显形 ——
//    在此之前每一次都是"恒未发布"，把误判严严实实盖住了。
//    ⇒ 教训写在这里：修掉一个"恒假"之后，必须回到**真实数据的形状**上再验一遍"恒真"的那一半。
const relOther = E.decorateManagerRelease({
  release_id: 11,
  assignment_id: 3,
  artifact_id: 99,
  revision_no: 1,
  status: 'released',
  authorized_attachment_ids: [],
  customer_snapshot: { payload: {} }
})
check('[装饰契约] 别的成果发过 v1 ⇒ **不能**把本成果的 v1 判成已发布'
  + '（跨成果误判会把发布入口藏掉，用户发不出去且看不出原因）',
  E.releasedRevisionFor([relOther], 7, 1) === null,
  'artifact 7 v1 命中=' + JSON.stringify(E.releasedRevisionFor([relOther], 7, 1)))
check('[装饰契约] `releasedRevisionFor` 在**本成果**的发布上仍能挑到它'
  + '（过滤没把该留的也滤掉）',
  !!E.releasedRevisionFor([relOther, relDec], 7, 2),
  'artifact 7 v2 命中=' + JSON.stringify(!!E.releasedRevisionFor([relOther, relDec], 7, 2)))
check('[装饰契约] 成果页的版本行必须**按本成果过滤**（`releasedRevisionFor`），'
  + '不许直接拿整条授权的发布列表去比版本号',
  /releasedRevisionFor\(list,\s*artifact/.test(
    fs.readFileSync(path.join(REPO, 'miniapp/pages/entrust/artifact/artifact.js'), 'utf8')),
  'artifact.js 里的调用形态')

// ---- 委托货量变更：投影行为（S6-1 / D1-09 / §10.1 第 8 步）----------------
//
// 这一节守的是**界面上的那条对照**：`800.000 吨 → 950.000 吨`。
// D1-09 要看的正是它 —— 而它有三个容易悄悄坏掉的地方：
//
//   ① 文案由前端自己拼（`数值 + ' ' + 单位`）⇒ 与别处漂移成两种写法；
//   ② 旧值未知时被当成 0 ⇒ 历史看起来像"从 0 涨到 950"（编出来的一个事实）；
//   ③ 「本委托货量」没进候选 / 没排在最前 ⇒ 选不到真正的变更落点。
//
// ⚠️ 判据落在**投影后的产物**上（`changeText` / `oldQuantityText` / 候选行的
//    `target_kind`），不落在"函数里有没有那一行代码"上 —— 后者一重构就漂。

// 一行**真实的 API 原文**（`GET /assignments/{id}/quantity-changes` 的 items[0]）
const qtyChangeRaw = {
  change_id: 4,
  assignment_id: 3,
  exception_id: 12,
  base_revision: 5,
  old_quantity: '800.000',
  old_quantity_unit: '吨',
  new_quantity: '950.000',
  new_quantity_unit: '吨',
  old_quantity_text: '800.000 吨',
  new_quantity_text: '950.000 吨',
  basis: '客户加货',
  applied_by: 900,
  applied_at: '2026-09-18 09:00:00'
}
const qtyDec = E.decorateQuantityChange(qtyChangeRaw)
check('[货量变更投影] 一行就是那条对照（`800.000 吨 → 950.000 吨`）',
  qtyDec.changeText === '800.000 吨 → 950.000 吨',
  'changeText=' + JSON.stringify(qtyDec.changeText))
check('[货量变更投影] 原值与文案**都给**（界面显示文案、程序比对用原值）',
  qtyDec.oldQuantity === '800.000' && qtyDec.newQuantity === '950.000' &&
    qtyDec.oldQuantityText === '800.000 吨',
  'raw=' + JSON.stringify([qtyDec.oldQuantity, qtyDec.newQuantity]))
check('[货量变更投影] 文案直接取**服务端**那一份（前端不拼 `数值 + 单位`）'
  + '（各拼一份必然漂移成"历史里 800 吨、别处 800.000吨"）',
  qtyDec.newQuantityText === qtyChangeRaw.new_quantity_text,
  'newQuantityText=' + JSON.stringify(qtyDec.newQuantityText))
check('[货量变更投影] 来源案件号必须带上（"这一改是谁批的"要能顺回去）',
  // ⚠️ `_sid` 一律返回**字符串**（与"dataset 过来的状态键恒字符串"同一口径），
  //    所以这里比的是 `'12'` 而不是 `12` —— 比错了会是一条**恒假**的断言。
  qtyDec.sourceText.indexOf('#12') !== -1 && String(qtyDec.exceptionId) === '12',
  'sourceText=' + JSON.stringify(qtyDec.sourceText) +
  ' exceptionId=' + JSON.stringify(qtyDec.exceptionId))

// ⭐ 旧值**未知保持未知**：从 NULL 改成确定值是合法变更，
//    把它当成 0 会让历史显示"从 0 涨到 950" —— 那是编出来的一个事实。
const qtyUnknown = E.decorateQuantityChange({
  change_id: 5,
  assignment_id: 3,
  exception_id: 13,
  base_revision: 6,
  old_quantity: null,
  old_quantity_unit: null,
  new_quantity: '950.000',
  new_quantity_unit: '吨',
  old_quantity_text: '未知',
  new_quantity_text: '950.000 吨',
  basis: '',
  applied_by: null,
  applied_at: ''
})
check('[货量变更投影] 旧值未知 ⇒ 显示「未知」，**不得**显示成 0',
  // ⚠️ 判据用**全等**而不是 `indexOf('0.000') === -1`：
  //    新值 `950.000` 本身就含 `0.000`，那种写法是一条**恒假**的断言
  //    （本轮实测踩到 —— 断言写错的表现是"改对了依然红"）。
  qtyUnknown.oldQuantityText === '未知' &&
    qtyUnknown.oldQuantity === '' &&
    qtyUnknown.changeText === '未知 → 950.000 吨',
  'oldQuantityText=' + JSON.stringify(qtyUnknown.oldQuantityText) +
  ' changeText=' + JSON.stringify(qtyUnknown.changeText))
check('[货量变更投影] 没写依据时说「未写依据」而不是留空（空行看起来像排版坏了）',
  qtyUnknown.basisText === '未写依据',
  'basisText=' + JSON.stringify(qtyUnknown.basisText))
check('[货量变更投影] 空清单 ⇒ 空数组（"这单没改过货量"与"读失败"由页面分开说）',
  Array.isArray(E.decorateQuantityChangeList([])) &&
    E.decorateQuantityChangeList([]).length === 0 &&
    E.decorateQuantityChangeList(null).length === 0,
  'empty ok')

// ⭐ 上一条**单靠取值比不出来**：服务端那份文案恰好等于"数值 + 空格 + 单位"时，
//    前端自己拼一遍也能通过（反向自证实测：注入拼接后门禁仍是绿的）。
//    ⇒ 再加一条**行为上可判定**的：有数、单位未知时，拼接会多出空格或 `null`，
//      而服务端给的是**剥掉空白**的那一份。这一格就是"直通 vs 自己拼"的分水岭。
const qtyNoUnit = E.decorateQuantityChange({
  change_id: 6,
  assignment_id: 3,
  exception_id: 14,
  base_revision: 7,
  old_quantity: '800.000',
  old_quantity_unit: null,
  new_quantity: '950.000',
  new_quantity_unit: '吨',
  old_quantity_text: '800.000',
  new_quantity_text: '950.000 吨',
  basis: '口径修正',
  applied_by: 900,
  applied_at: '2026-09-18 09:30:00'
})
check('[货量变更投影] 单位缺失（历史行可为 NULL）⇒ 文案取服务端那一份，'
  + '拼接会多出空格或 `null`',
  qtyNoUnit.oldQuantityText === '800.000' && qtyNoUnit.changeText === '800.000 → 950.000 吨',
  'oldQuantityText=' + JSON.stringify(qtyNoUnit.oldQuantityText) +
  ' changeText=' + JSON.stringify(qtyNoUnit.changeText))

// 另一条同样可判定的：**投影层不得再拼「数值 + 单位」**。
// 判据落在这两个文案字段的**赋值形态**上（是否为裸标识符）——
// 这是本仓对"同一事实只能有一份实现"已有的做法（见成果页那条 `releasedRevisionFor` 的形态检查）。
const entrustSrc = fs.readFileSync(path.join(REPO, 'miniapp/utils/entrust.js'), 'utf8')
const qtyProjBlock = entrustSrc.slice(
  entrustSrc.indexOf('function decorateQuantityChange('),
  entrustSrc.indexOf('function decorateQuantityChangeList(')
)
check('[货量变更投影] 两个文案字段**直通**服务端（投影层不得出现「数值 + 空格 + 单位」的拼接）',
  qtyProjBlock.length > 0 &&
    /newQuantityText:\s*\w+\s*,/.test(qtyProjBlock) &&
    !/\+\s*'\s'\s*\+/.test(qtyProjBlock),
  'block=' + qtyProjBlock.length + ' 有拼接=' + /\+\s*'\s'\s*\+/.test(qtyProjBlock))

// ── 受影响项候选：「本委托货量」必须进候选、且排在**最前** ──────────────
const candWithSelf = E.decorateCaseLinkTargets(
  { items: [{ task_id: 1, task_type: 'quote', title: '报价' }] },
  { items: [{ artifact_id: 2, artifact_type: 'customer_quote' }] },
  [],
  { assignment_id: 3, quantityText: '800.000 吨' }
)
check('[货量候选] 传了委托 ⇒ 候选里出现「本委托货量」，且 `target_kind` 是 `assignment`',
  candWithSelf.length === 3 && candWithSelf[0].target_kind === 'assignment' &&
    candWithSelf[0].target_id === '3',
  'rows=' + JSON.stringify(candWithSelf.map((r) => r.target_kind)))
check('[货量候选] 它排在**最前**（混在成果堆里会让人以为在改某一版报价）',
  candWithSelf[0].text.indexOf('本委托货量') === 0,
  'first=' + JSON.stringify(candWithSelf[0].text))
check('[货量候选] 副标题带上**服务端给的**当前货量（未知就说未知，不显示 0）',
  candWithSelf[0].sub.indexOf('800.000 吨') !== -1,
  'sub=' + JSON.stringify(candWithSelf[0].sub))
const candWithoutSelf = E.decorateCaseLinkTargets({ items: [] }, { items: [] }, [], null)
check('[货量候选] 没拿到委托详情时**不产出**这一行（给一个点不动的候选比不给更糟）',
  candWithoutSelf.length === 0,
  'rows=' + candWithoutSelf.length)

// ── 页面 data 与模板的接线 ───────────────────────────────────────────────
// ⚠️ 变量名带 `Qty` 后缀：本节上面已有一份 `caseJs`（另一个用途），
//    重名会直接 SyntaxError —— 顶层的 `const` 是同一个作用域。
const caseJsQty = fs.readFileSync(path.join(REPO, 'miniapp/pages/entrust/case/case.js'), 'utf8')
const caseTplQty = fs.readFileSync(path.join(REPO, 'miniapp/pages/entrust/case/case.wxml'), 'utf8')
check('[货量接线] 案件页取数时**登记**了货量变更历史（否则卡片恒空）',
  caseJsQty.indexOf('fetchQuantityChanges') !== -1 &&
    caseJsQty.indexOf('decorateQuantityChangeList') !== -1,
  'case.js 取数与投影')
check('[货量接线] 读失败**必须**与"空列表"分开说（空列表的语义是"从没改过"）',
  caseJsQty.indexOf('货量变更历史未能读取') !== -1,
  'case.js 失败分支')
check('[货量接线] 模板里有货量变更卡与三只输入框的锚点',
  ['qty-list', 'qty-main', 'data-df="qty"', 'data-df="qty-unit"', 'data-df="qty-basis"']
    .every(function (s) { return caseTplQty.indexOf(s) !== -1 }),
  'case.wxml 锚点')
check('[货量接线] 只在**已批准**分支里出现货量输入（驳回不该能改货量）',
  /showQuantityChange\s*&&\s*decideForm\.to\s*===\s*'approved'/.test(caseTplQty),
  '模板条件')
check('[货量接线] 提交决定时把变更内容并进 `approved_changes`，键是 `assignment#<id>`',
  /approvedChanges\['assignment#'\s*\+\s*targetId\]/.test(caseJsQty),
  'case.js 组装')
check('[货量接线] 应用确认里显示 `当前 → 将改为`（apply 不给人改写的机会，更不能盲点）',
  caseTplQty.indexOf('quantityChangeText') !== -1 &&
    caseTplQty.indexOf('quantityBasisText') !== -1,
  'case.wxml 应用区')

// ── 变更类别：「批准时补登」的唯一界面入口（A2 五之二）─────────────────────
//
// 应用变更**要求**类别已登记。而登记页此前没有类别选择器、本页也没有 ——
// ⇒ 变更请求在界面上**根本应用不了**（类别只能靠种子或接口写进去）。
// 这不是"少一个输入框"，是整条链路在界面上断掉。
check('[变更类别] `caseCategoryOptions` 只给变更请求选项（异常案件带类别会 400）',
  E.caseCategoryOptions('change_request').length === 5 &&
    E.caseCategoryOptions('exception').length === 0 &&
    E.caseCategoryOptions('').length === 0,
  'change_request=' + E.caseCategoryOptions('change_request').length +
  ' exception=' + E.caseCategoryOptions('exception').length)
check('[变更类别] 五个取值与服务端 `revalidation.CHANGE_CATEGORIES` 逐格对齐',
  (function () {
    const src = fs.readFileSync(
      path.join(REPO, 'backend/app/modules/entrust/revalidation.py'), 'utf8')
    const got = E.caseCategoryOptions('change_request').map(function (o) { return o.key }).sort()
    return got.length === 5 && got.every(function (k) { return src.indexOf('"' + k + '"') !== -1 })
  })(),
  JSON.stringify(E.caseCategoryOptions('change_request').map(function (o) { return o.key })))
check('[变更类别] 案件页模板里有选择条与它的 tap 锚点',
  caseTplQty.indexOf('data-cat=') !== -1 && caseTplQty.indexOf('onPickCategory') !== -1,
  'case.wxml 选择条')
check('[变更类别] 提交决定时把它并进 body（只在变更请求上，且只回传非空值）',
  /body\.change_category\s*=\s*cat/.test(caseJsQty) &&
    /this\.data\.kind\s*===\s*'change_request'/.test(caseJsQty),
  'case.js 组装')
check('[变更类别] 「货量输入框要不要出现」只有**一处**判据（`quantityVisibility`）'
  + '（三处各写一份 `&&`，迟早有一处漏改 ⇒ 选了类别但输入框不出现）',
  (caseJsQty.match(/quantityVisibility\(/g) || []).length >= 3 &&
    caseJsQty.indexOf('cargo_quantity_category') !== -1,
  'quantityVisibility 调用数=' + (caseJsQty.match(/quantityVisibility\(/g) || []).length)

// ---- 输出 ----
console.log(`检查完成：${checked} 项断言 / 覆盖 ${PAGE_CSS_CHECKS.length} 个页面 + 1 个契约模块`)
if (errors.length) {
  console.log(`\n发现 ${errors.length} 个问题：`)
  errors.forEach((x) => console.log('  - ' + x))
  process.exit(1)
}
console.log('全部通过 ✓')
