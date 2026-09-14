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

/** 本切片新增/改动的三个页面：不允许任何未定义类名 */
const ENTRUST_PAGES = [
  'pages/entrust/workbench/workbench',
  'pages/entrust/detail/detail',
  'pages/entrust/artifact/artifact'
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
const backendSlots = slotSpecBlock
  .split(/SlotSpec\(/)
  .slice(1)
  .map(function (chunk) {
    return {
      key: (chunk.match(/key="([^"]+)"/) || [])[1] || '',
      title: (chunk.match(/title="([^"]+)"/) || [])[1] || '',
      open: !/open=False/.test(chunk)
    }
  })

const frontSlots = E.WORKBENCH_SLOTS || []

check('[槽位] 前端配置表 7 条', frontSlots.length === 7, '实际 ' + frontSlots.length)
check('[槽位] 后端 SLOT_SPECS 解析出 7 条', backendSlots.length === 7, '实际 ' + backendSlots.length)
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
  '[槽位] 只有 exceptions 是「本期未开放」（多一个都说明有人悄悄降级了能力）',
  backendSlots
    .filter((s) => !s.open)
    .map((s) => s.key)
    .join(',') === 'exceptions',
  '实际 ' + backendSlots.filter((s) => !s.open).map((s) => s.key).join(',')
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
  slots: [{ key: 'exceptions', title: '异常与变更', available: false, unavailable_reason: '本期未开放：尚无数据模型' }]
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
  '[模板] 成果精确版本（artifact_id + 版本）在槽位里显示出来',
  /rf\.artifactId/.test(dtWxml) && /rf\.text/.test(dtWxml)
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

// ── 投影行为 ──────────────────────────────────────────────────────────
const SPEC_QUOTE = {
  code: 'quote_parsed',
  label: '报价解析稿',
  required_fields: ['carrier', 'rate'],
  optional_fields: ['cargo_name', 'valid_until'],
  internal_fields: [],
  editable: true
}
const SPEC_PROC = {
  code: 'procurement_confirm',
  label: '采购确认',
  required_fields: ['supplier', 'agreed_scope'],
  optional_fields: ['agreed_amount'],
  internal_fields: ['agreed_amount'],
  editable: true
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
  '[接线] 详情页槽位可点进成果页，且带的是这一条的 artifact_id',
  /onOpenArtifact\(/.test(dtJs) && /onOpenArtifact/.test(dtWxml) &&
    /data-id="\{\{rf\.artifactId\}\}"/.test(dtWxml) &&
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

// ---- 输出 ----
console.log(`检查完成：${checked} 项断言 / 覆盖 ${PAGE_CSS_CHECKS.length} 个页面 + 1 个契约模块`)
if (errors.length) {
  console.log(`\n发现 ${errors.length} 个问题：`)
  errors.forEach((x) => console.log('  - ' + x))
  process.exit(1)
}
console.log('全部通过 ✓')
