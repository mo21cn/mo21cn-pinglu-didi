#!/usr/bin/env node
/**
 * 路由模块行为测试（ENT-018 / DR-0011 §5.1 第二层）
 *
 * 与 `verify_routes.js` 的分工：
 *   verify_routes.js        —— **静态**：声明 ⇄ app.json ⇄ 代码三方一致
 *   本脚本                   —— **行为**：纯函数在真实输入下的语义（含"必须拒绝"的用例）
 *
 * 为什么必须有这一层：静态校验只能证明「声明与代码一致」，证明不了「声明本身是对的」。
 * HO 实测的 4 个问题（空参通过、非法枚举通过、双向来路算成 Infinity、缺参仍 navigateTo）
 * 全都是**语义**问题——它们能在静态门禁全绿的情况下存在。
 *
 * 覆盖：
 *   A. 参数契约      —— 缺参 / 空参 / 非法类型 / 非法枚举 / 越界 全部被拒
 *   B. 深链强度      —— require-params 严于 allow；`deepLinkOnly` 只对深链生效
 *   C. 上下文复用    —— 同路径不同委托/组织**不得**复用；相同则可复用（A4）
 *   D. 策略链        —— 复用 → 声明式替换 → 压栈 → 未保存编辑 → 明确失败（A7）
 *   E. 环与链深      —— push 环 ⇒ Infinity（**注入必红 / 移除必绿**在进程内完成）
 *   F. 入口守卫      —— 冷启动缺参/deny/越权/不归属被拦；应用内导航放行
 *   G. 非小程序环境  —— go() 不抛异常（CI 里没有 wx）
 *   H. 非线性切换    —— 工作台↔会话循环 20 次，栈深保持受控（A3 的 Node 侧）
 *   I. reset 与接入  —— 声明为 reset 的边必须真的重置栈（ENT-019 补的运行期缺口）
 *
 * 用法：node scripts/verify_routes_behavior.js
 * 退出码：0 通过 / 1 有条目失败
 */
const assert = require('assert')
const path = require('path')

const MINI = path.resolve(__dirname, '..', 'miniapp')
const R = require(path.join(MINI, 'utils', 'routes.js'))

let pass = 0
const failures = []

function t(name, fn) {
  try {
    fn()
    pass++
    console.log('  ✓ ' + name)
  } catch (e) {
    failures.push({ name, message: e.message })
    console.log('  ✗ ' + name + ' —— ' + e.message)
  }
}

function group(title) {
  console.log('\n' + title)
}

/** 造一个页面栈（栈底在前） */
function stackOf(entries) {
  return entries.map((e) => (typeof e === 'string' ? { route: e, options: {} } : e))
}

const DETAIL = 'pages/entrust/detail/detail'
const WORKBENCH = 'pages/entrust/workbench/workbench'
const ASSISTANT = 'pages/assistant/assistant'
const MATCH = 'pages/trade/match/match'
const CARGO = 'pages/publish/cargo/cargo'
const MINE = 'pages/mine/mine'

// ─────────────────────────────────────────────────────────────
group('A. 参数契约（HO 实测 ②③ 的回归）')

t('缺参：resolveNavigation 拒绝未带 assignment_id 的详情页（HO ①）', () => {
  const p = R.resolveNavigation('/' + DETAIL)
  assert.strictEqual(p.ok, false)
  assert.strictEqual(p.action, 'blocked')
  assert.strictEqual(p.code, 'bad-params')
  assert.ok(p.errors.some((e) => e.key === 'assignment_id'))
})

t('空参：`assignment_id=` 不再通过（HO ②）', () => {
  const v = R.validateParams(DETAIL, '/' + DETAIL + '?assignment_id=', { deepLink: true })
  assert.strictEqual(v.ok, false)
  assert.deepStrictEqual(v.missing, ['assignment_id'])
  assert.strictEqual(R.canDeepLink('/' + DETAIL + '?assignment_id=').ok, false)
})

t('非法枚举：`mode=乱填` 被拒（HO ③）', () => {
  const v = R.validateParams(MATCH, '/' + MATCH + '?mode=乱填&refId=3', { deepLink: true })
  assert.strictEqual(v.ok, false)
  assert.deepStrictEqual(v.invalid, ['mode'])
})

t('越界：`refId=0` 被拒（页面会取不到候选，属配置错误级）', () => {
  const v = R.validateParams(MATCH, '/' + MATCH + '?mode=cargo&refId=0', { deepLink: true })
  assert.strictEqual(v.ok, false)
  assert.deepStrictEqual(v.invalid, ['refId'])
})

t('类型错：`refId=abc` 被拒；`refId=12` 通过', () => {
  assert.strictEqual(R.validateParams(MATCH, 'mode=cargo&refId=abc', { deepLink: true }).ok, false)
  assert.strictEqual(R.validateParams(MATCH, 'mode=cargo&refId=12', { deepLink: true }).ok, true)
})

t('合法深链通过（mode=cargo&refId=12）', () => {
  const r = R.canDeepLink('/' + MATCH + '?mode=cargo&refId=12')
  assert.strictEqual(r.ok, true, JSON.stringify(r))
})

t('未登记路由：resolveNavigation / canDeepLink / chainDepth 三者一致拒绝', () => {
  assert.strictEqual(R.resolveNavigation('/pages/nope/nope').code, 'unregistered')
  assert.strictEqual(R.canDeepLink('/pages/nope/nope').ok, false)
  assert.strictEqual(R.chainDepth('pages/nope/nope'), Infinity)
})

// ─────────────────────────────────────────────────────────────
group('B. 深链强度与 `deepLinkOnly`')

t('require-params 严于 allow：assistant 深链缺 mode 被拒', () => {
  assert.strictEqual(R.canDeepLink('/' + ASSISTANT).ok, false)
  assert.strictEqual(R.canDeepLink('/' + ASSISTANT + '?mode=search').ok, true)
})

t('`deepLinkOnly` 只对深链生效：应用内 go() 不带 mode 不被拦', () => {
  // shipper.goAssistant() 就是不带的；若把它拦掉就是回归
  const p = R.resolveNavigation('/' + ASSISTANT, { stack: stackOf([MINE]) })
  assert.strictEqual(p.ok, true)
  assert.strictEqual(p.action, 'push')
})

t('workbench 由 deny 改为 allow：带 org_id 与不带都不拦（页面自带选择态）', () => {
  assert.strictEqual(R.ROUTES[WORKBENCH].deepLink, 'allow')
  assert.strictEqual(R.canDeepLink('/' + WORKBENCH).ok, true)
  assert.strictEqual(R.canDeepLink('/' + WORKBENCH + '?org_id=7').ok, true)
  assert.strictEqual(R.canDeepLink('/' + WORKBENCH + '?org_id=').ok, false, '带了但为空 → 格式错')
})

t('tabBar 页仍为 deny：深链无意义', () => {
  assert.strictEqual(R.ROUTES[MINE].deepLink, 'deny')
  assert.strictEqual(R.canDeepLink('/' + MINE).ok, false)
})

// ─────────────────────────────────────────────────────────────
group('C. 上下文复用键（A4）')

t('同路径不同委托 → 不复用，走压栈', () => {
  const stack = stackOf([MINE, { route: DETAIL, options: { assignment_id: '1' } }])
  const p = R.resolveNavigation('/' + DETAIL + '?assignment_id=2', {
    stack,
    from: WORKBENCH,
    strategy: R.STRATEGY.PUSH
  })
  assert.strictEqual(p.action, 'push')
})

t('同路径同委托 → 复用（返回栈中那一层并刷新）', () => {
  const stack = stackOf([
    MINE,
    { route: DETAIL, options: { assignment_id: '1' } },
    { route: ASSISTANT, options: { mode: 'search' } }
  ])
  const p = R.resolveNavigation('/' + DETAIL + '?assignment_id=1', {
    stack,
    from: ASSISTANT,
    strategy: R.STRATEGY.BACK
  })
  assert.strictEqual(p.action, 'reuse')
  assert.strictEqual(p.delta, 1)
})

t('同一工作台但组织不同 → 不复用（否则 A/B 组织串单）', () => {
  const stack = stackOf([MINE, { route: WORKBENCH, options: { org_id: 'A' } }])
  const p = R.resolveNavigation('/' + WORKBENCH, {
    stack,
    from: WORKBENCH,
    ctx: { orgId: 'B' }
  })
  assert.notStrictEqual(p.action, 'reuse')
})

t('同一工作台且组织相同 → 复用（原地刷新）', () => {
  const stack = stackOf([MINE, { route: WORKBENCH, options: { org_id: 'A' } }])
  const p = R.resolveNavigation('/' + WORKBENCH, {
    stack,
    from: WORKBENCH,
    ctx: { orgId: 'A' }
  })
  assert.strictEqual(p.action, 'reuse')
  assert.strictEqual(p.delta, 0)
})

t('pageKey 组成：路径 + 上下文参数 + 组织（org_id 不入键，避免自相矛盾）', () => {
  assert.strictEqual(
    R.pageKey(DETAIL, 'assignment_id=9', {}),
    DETAIL + '|assignment_id=9'
  )
  assert.strictEqual(R.pageKey(WORKBENCH, '', { orgId: 'A' }), WORKBENCH + '|org=A')
  assert.strictEqual(R.pageKey(WORKBENCH, 'org_id=A', { orgId: 'A' }), WORKBENCH + '|org=A')
})

// ─────────────────────────────────────────────────────────────
group('D. 策略链（DR-0011 §3.4 / A7）')

const deepStack = stackOf([MINE, MINE, MINE, MINE, MINE, MINE, MINE, MINE]) // 8 层 = 预算

t('预算内正常压栈', () => {
  const p = R.resolveNavigation('/' + WORKBENCH, { stack: stackOf([MINE]) })
  assert.strictEqual(p.action, 'push')
})

t('预算触顶 + 未声明替换 + 无未保存编辑 → 明确失败并给兜底去处', () => {
  const p = R.resolveNavigation('/' + DETAIL + '?assignment_id=5', { stack: deepStack })
  assert.strictEqual(p.ok, false)
  assert.strictEqual(p.action, 'blocked')
  assert.strictEqual(p.code, 'stack-budget')
  assert.strictEqual(p.fallback.url, '/pages/entrust/workbench/workbench')
})

t('预算触顶 + 有未保存编辑 → 先提示，不静默卸载', () => {
  const p = R.resolveNavigation('/' + DETAIL + '?assignment_id=5', {
    stack: deepStack,
    hasUnsaved: true
  })
  assert.strictEqual(p.action, 'confirm-unsaved')
  assert.strictEqual(p.code, 'unsaved-edit')
})

t('声明为 replace 的边：预算内也用替换，不偷偷变成压栈', () => {
  const p = R.resolveNavigation('/' + MATCH + '?mode=cargo&refId=3', {
    stack: stackOf([MINE]),
    from: CARGO
  })
  assert.strictEqual(p.action, 'replace')
  assert.strictEqual(p.strategy, R.STRATEGY.REPLACE)
})

t('声明为 replace 的边 + 有未保存编辑 → 确认态', () => {
  const p = R.resolveNavigation('/' + MATCH + '?mode=cargo&refId=3', {
    stack: stackOf([MINE]),
    from: CARGO,
    hasUnsaved: true
  })
  assert.strictEqual(p.action, 'confirm-unsaved')
})

t('tabBar 目标：switchTab 且剥掉查询串', () => {
  const p = R.resolveNavigation('/' + MINE + '?x=1')
  assert.strictEqual(p.action, 'switchTab')
  assert.strictEqual(p.url, '/' + MINE)
})

t('resolveNavigation **不是默认 redirectTo**：预算内是 push，不是 replace', () => {
  const p = R.resolveNavigation('/' + DETAIL + '?assignment_id=5', { stack: stackOf([MINE]) })
  assert.strictEqual(p.action, 'push')
})

// ─────────────────────────────────────────────────────────────
group('E. 环与链深（注入必红 → 移除必绿）')

t('push 环 ⇒ 链深 Infinity；移除注入后恢复有限', () => {
  const before = R.chainDepth(WORKBENCH)
  assert.ok(isFinite(before), '注入前应是有限值')
  const injected = [
    { from: ASSISTANT, to: WORKBENCH, strategy: 'push' }, // 反向也变成 push ⇒ 成环
    { from: WORKBENCH, to: ASSISTANT, strategy: 'push', pending: '测试注入' }
  ]
  // 原本 workbench→assistant 已存在（pending, push），先把重复的那条去掉再加，避免重复声明
  const orig = R.NAV_EDGES.slice()
  try {
    for (let i = R.NAV_EDGES.length - 1; i >= 0; i--) {
      const e = R.NAV_EDGES[i]
      if ((e.from === WORKBENCH && e.to === ASSISTANT) || (e.from === ASSISTANT && e.to === WORKBENCH)) {
        R.NAV_EDGES.splice(i, 1)
      }
    }
    for (const e of injected) R.NAV_EDGES.push(e)
    assert.strictEqual(R.chainDepth(WORKBENCH), Infinity)
    assert.strictEqual(R.chainDepth(ASSISTANT), Infinity)
  } finally {
    R.NAV_EDGES.length = 0
    for (const e of orig) R.NAV_EDGES.push(e)
  }
  assert.strictEqual(R.chainDepth(WORKBENCH), before, '移除后应恢复')
})

t('工作台 ↔ 会话双向切换**不算环**（reuse 边不参与链深）', () => {
  assert.ok(isFinite(R.chainDepth(WORKBENCH)))
  assert.ok(isFinite(R.chainDepth(ASSISTANT)))
})

t('所有页面链深有限且不超预算', () => {
  for (const p of Object.keys(R.ROUTES)) {
    const d = R.chainDepth(p)
    assert.ok(isFinite(d), p + ' 链深应为有限值')
    assert.ok(d <= R.STACK_BUDGET, p + ' 链深 ' + d + ' 超预算')
  }
})

t('parents 由 push 边派生：match 的 parents 不含替换来源 cargo', () => {
  assert.deepStrictEqual(
    R.ROUTES[MATCH].parents.slice().sort(),
    ['pages/owner/owner', 'pages/shipper/shipper']
  )
  // 但链深仍要算上替换边（否则会低估真实栈）
  //
  // ⚠️ 4 → 3（DR-0015 / ENT-033）：委托支线的会话从公共域 `assistant` 收回
  // 自己的 `pages/entrust/session/session`（`workbench → assistant` 那条 push 边
  // 改指向 `workbench → session`）。于是 `assistant` 不再被委托工作台 push 进入，
  // 链深 3 → 2，连带 `cargo` 与 `match` 各降 1。
  // **这是拓扑真的变短了**，不是预算被放宽：改的是"委托会话属于哪个页面"，
  // 断言数字必须跟着事实走 —— 写死旧数字会把合法改动读成回归。
  assert.strictEqual(R.chainDepth(MATCH), 3)
})

// ─────────────────────────────────────────────────────────────
group('F. 目标页入口守卫（HO 第 4 条：深链校验必须落在目标页）')

t('应用内导航放行（入口校验已由 go() 完成）', () => {
  const g = R.guardEntry('/' + DETAIL + '?assignment_id=1', { coldStart: false })
  assert.strictEqual(g.ok, true)
  assert.strictEqual(g.code, 'internal')
})

t('冷启动缺参被拦（编程错误不伪装成业务态）', () => {
  const g = R.guardEntry('/' + DETAIL, { coldStart: true })
  assert.strictEqual(g.ok, false)
  assert.strictEqual(g.code, 'bad-params')
})

t('冷启动空参被拦', () => {
  const g = R.guardEntry('/' + DETAIL + '?assignment_id=', { coldStart: true })
  assert.strictEqual(g.ok, false)
  assert.strictEqual(g.code, 'bad-params')
})

t('deny 路由冷启动被拦（深链无意义）', () => {
  const g = R.guardEntry('/' + MINE, { coldStart: true })
  assert.strictEqual(g.ok, false)
  assert.strictEqual(g.code, 'deep-link-denied')
})

t('合法冷启动深链放行', () => {
  const g = R.guardEntry('/' + DETAIL + '?assignment_id=1', { coldStart: true })
  assert.strictEqual(g.ok, true)
  assert.strictEqual(g.code, 'ok')
})

t('无权限 / 不归属：已知结论即拦（服务端仍为权威）', () => {
  const g1 = R.guardEntry('/' + DETAIL + '?assignment_id=1', { coldStart: true, hasPermission: false })
  assert.strictEqual(g1.code, 'forbidden')
  const g2 = R.guardEntry('/' + DETAIL + '?assignment_id=1', { coldStart: true, belongs: false })
  assert.strictEqual(g2.code, 'not-belong')
})

t('requiresOrg 分支：缺组织 → 进选择流程，**不是**拒绝深链', () => {
  const r = R.ROUTES[DETAIL]
  const keep = { requiresOrg: r.requiresOrg, keyContext: r.keyContext }
  try {
    r.requiresOrg = true
    r.keyContext = ['org']
    const g = R.guardEntry('/' + DETAIL + '?assignment_id=1', { coldStart: true })
    assert.strictEqual(g.ok, true)
    assert.strictEqual(g.action, 'redirect-org')
    const g2 = R.guardEntry('/' + DETAIL + '?assignment_id=1', { coldStart: true, orgId: 'A' })
    assert.strictEqual(g2.action, 'continue')
  } finally {
    r.requiresOrg = keep.requiresOrg
    r.keyContext = keep.keyContext
  }
})

// ─────────────────────────────────────────────────────────────
group('G. 非小程序环境（CI 里没有 wx）')

t('go() 不抛异常，返回计划且 performed=false', () => {
  assert.strictEqual(typeof wx, 'undefined', '本脚本应在无 wx 的环境下运行')
  const p = R.go('/' + DETAIL + '?assignment_id=1', { stack: stackOf([MINE]) })
  assert.strictEqual(p.action, 'push')
  assert.strictEqual(p.performed, false)
})

t('currentDepth() 在无运行时返回 0', () => {
  assert.strictEqual(R.currentDepth(), 0)
  assert.deepStrictEqual(R.stackSnapshot(), [])
})

t('go() 拒绝非法参数时也不抛异常', () => {
  const p = R.go('/' + MATCH + '?mode=cargo&refId=0')
  assert.strictEqual(p.action, 'blocked')
  assert.strictEqual(p.performed, false)
})

// ─────────────────────────────────────────────────────────────
group('H. 非线性切换：工作台↔会话循环 20 次栈深受控（A3 的 Node 侧）')

t('循环 20 次后栈深不增长，上下文不串单', () => {
  let stack = stackOf([{ route: MINE, options: {} }, { route: WORKBENCH, options: { org_id: 'A' } }])
  const ctx = { orgId: 'A' }
  let maxDepth = stack.length
  for (let i = 0; i < 20; i++) {
    const toAssistant = R.resolveNavigation('/' + ASSISTANT, {
      stack,
      ctx,
      from: stack.length ? stack[stack.length - 1].route : ''
    })
    assert.ok(toAssistant.ok, '第 ' + i + ' 轮进会话被拦：' + toAssistant.reason)
    if (toAssistant.action === 'push') {
      stack = stack.concat([{ route: ASSISTANT, options: { mode: 'search' } }])
    } else if (toAssistant.action === 'reuse') {
      stack = stack.slice(0, stack.length - toAssistant.delta)
    }
    const backWorkbench = R.resolveNavigation('/' + WORKBENCH, {
      stack,
      ctx,
      from: stack.length ? stack[stack.length - 1].route : ''
    })
    assert.ok(backWorkbench.ok, '第 ' + i + ' 轮回工作台被拦：' + backWorkbench.reason)
    if (backWorkbench.action === 'push') {
      stack = stack.concat([{ route: WORKBENCH, options: { org_id: 'A' } }])
    } else if (backWorkbench.action === 'reuse') {
      stack = stack.slice(0, stack.length - backWorkbench.delta)
    }
    maxDepth = Math.max(maxDepth, stack.length)
  }
  assert.ok(maxDepth <= 3, '20 轮循环后最深栈 ' + maxDepth + ' 层（期望 ≤3）')
  assert.ok(stack.length <= 3, '结束时栈深 ' + stack.length)
})

// ─────────────────────────────────────────────────────────────
group('I. reset 策略与页面接入（ENT-019：声明层与运行期必须真的接上）')

const INDEX = 'pages/index/index'

t('reset 边解析为 action=reset（不是 push）', () => {
  const p = R.resolveNavigation('/' + INDEX, { from: WORKBENCH, stack: stackOf([WORKBENCH]) })
  assert.strictEqual(p.ok, true, JSON.stringify(p))
  assert.strictEqual(p.action, 'reset')
  assert.strictEqual(p.strategy, 'reset')
  assert.strictEqual(p.url, '/' + INDEX)
})

t('reset **优先于复用**：目标在栈底也不降级成 navigateBack', () => {
  // 冷启动深链进工作台的真实栈形：index 在栈底（同组织、键相同，复用分支本该命中）
  const stack = stackOf([
    { route: INDEX, options: {} },
    { route: MINE, options: {} },
    { route: WORKBENCH, options: { org_id: 'A' } }
  ])
  const p = R.resolveNavigation('/' + INDEX, { from: WORKBENCH, stack, ctx: { orgId: 'A' } })
  assert.strictEqual(p.action, 'reset', '被复用分支降级了：' + JSON.stringify(p))
  assert.strictEqual(p.delta, undefined, 'reset 不该带 delta')
})

t('reset 优先于预算判定：栈已到预算仍要重置，而不是 blocked', () => {
  const stack = stackOf([
    { route: INDEX, options: {} },
    MINE,
    CARGO,
    MATCH,
    WORKBENCH,
    ASSISTANT,
    DETAIL,
    WORKBENCH
  ])
  assert.strictEqual(stack.length, R.STACK_BUDGET, '前置条件：栈已达预算')
  const p = R.resolveNavigation('/' + INDEX, { from: WORKBENCH, stack, ctx: { orgId: 'A' } })
  assert.strictEqual(p.ok, true, JSON.stringify(p))
  assert.strictEqual(p.action, 'reset')
})

t('两个委托页 → 首页 的边都声明为 reset，且走 go() 得到 reset', () => {
  for (const from of [WORKBENCH, DETAIL]) {
    const e = R.edgeOf(from, INDEX)
    assert.ok(e, from + ' → index 缺少导航边声明')
    assert.strictEqual(e.strategy, 'reset')
    const p = R.go('/' + INDEX, { from, stack: stackOf([from]) })
    assert.strictEqual(p.action, 'reset')
    assert.strictEqual(p.performed, false, 'CI 里没有 wx，不该真的跳')
  }
})

t('performPlan() 在无 wx 环境不抛异常，且对未通过的计划不动作', () => {
  const bad = R.resolveNavigation('/' + MATCH + '?mode=cargo&refId=0')
  assert.strictEqual(bad.ok, false)
  const out = R.performPlan(bad, {})
  assert.strictEqual(out.performed, false)
  const good = R.resolveNavigation('/' + DETAIL + '?assignment_id=1', { stack: stackOf([WORKBENCH]) })
  assert.strictEqual(R.performPlan(good, {}).performed, false)
})

t('工作台可被冷启动深链进入（deepLink=allow 之后**不能**被误拦）', () => {
  const g1 = R.guardEntry('/' + WORKBENCH, { coldStart: true })
  assert.strictEqual(g1.ok, true, JSON.stringify(g1))
  assert.strictEqual(g1.action, 'continue')
  const g2 = R.guardEntry('/' + WORKBENCH + '?org_id=ORG-A', { coldStart: true, orgId: 'ORG-A' })
  assert.strictEqual(g2.ok, true)
})

t('工作台非法/空 org_id 深链被拦（格式先于取数）', () => {
  const bad = R.guardEntry('/' + WORKBENCH + '?org_id=' + encodeURIComponent('../etc'), {
    coldStart: true
  })
  assert.strictEqual(bad.ok, false)
  assert.strictEqual(bad.code, 'bad-params')
  const empty = R.guardEntry('/' + WORKBENCH + '?org_id=', { coldStart: true })
  assert.strictEqual(empty.ok, false)
  assert.strictEqual(empty.code, 'bad-params')
})

t('详情页非法 id 字符被拦（初版只查 `!id`，这类会被放过去）', () => {
  for (const v of ['a b', '../etc', 'x?y', '1/2', '%2e%2e']) {
    const g = R.guardEntry('/' + DETAIL + '?assignment_id=' + v, { coldStart: true })
    assert.strictEqual(g.ok, false, '未拦住 assignment_id=' + v)
    assert.strictEqual(g.code, 'bad-params')
  }
  assert.strictEqual(R.guardEntry('/' + DETAIL + '?assignment_id=A-1_b', { coldStart: true }).ok, true)
})

t('不传 coldStart 时按「页面栈 ≤1 层」推断（无运行时 ⇒ 视为冷启动）', () => {
  assert.strictEqual(R.currentDepth(), 0)
  const g = R.guardEntry('/' + MINE)
  assert.strictEqual(g.code, 'deep-link-denied', '没按冷启动处理：' + JSON.stringify(g))
})

t('MIGRATED_PAGES 与 ROUTES 一致（名单不得指向未登记页面）', () => {
  assert.ok(R.MIGRATED_PAGES.length > 0, '名单为空：运行期治理没有任何页面在管')
  for (const p of R.MIGRATED_PAGES) {
    assert.ok(R.ROUTES[p], p + ' 在 MIGRATED_PAGES 里但未登记进 ROUTES')
  }
})

t('未保存编辑：**压栈不卸载当前页** ⇒ 不打扰用户（不该弹窗）', () => {
  const p = R.resolveNavigation('/' + DETAIL + '?assignment_id=1', {
    from: WORKBENCH,
    stack: stackOf([WORKBENCH]),
    hasUnsaved: true
  })
  assert.strictEqual(p.ok, true, '压栈被未保存编辑拦下了：' + JSON.stringify(p))
  assert.strictEqual(p.action, 'push')
})

t('未保存编辑：replace 边的 afterConfirm 是**可执行**计划（放弃后能进）', () => {
  const p = R.resolveNavigation('/' + MATCH + '?mode=cargo&refId=3', {
    from: CARGO,
    stack: stackOf([CARGO]),
    hasUnsaved: true
  })
  assert.strictEqual(p.action, 'confirm-unsaved')
  assert.strictEqual(p.strategy, 'replace')
  assert.ok(p.afterConfirm, '缺 afterConfirm：页面确认放弃后无从执行')
  assert.strictEqual(p.afterConfirm.ok, true)
  assert.strictEqual(p.afterConfirm.action, 'replace')
  assert.strictEqual(R.performPlan(p.afterConfirm, {}).performed, false, 'CI 里没有 wx，不该真的跳')
})

t('未保存编辑：预算耗尽时 afterConfirm 是 blocked（放弃编辑也进不去，必须如实说）', () => {
  const stack = stackOf([
    'pages/index/index', MINE, CARGO, MATCH, ASSISTANT,
    'pages/preview/preview', MINE, WORKBENCH
  ])
  assert.strictEqual(stack.length, R.STACK_BUDGET)
  const p = R.resolveNavigation('/' + DETAIL + '?assignment_id=1', {
    from: WORKBENCH, stack, hasUnsaved: true
  })
  assert.strictEqual(p.action, 'confirm-unsaved')
  assert.ok(p.afterConfirm, '缺 afterConfirm')
  assert.strictEqual(p.afterConfirm.ok, false)
  assert.strictEqual(p.afterConfirm.code, 'stack-budget')
  assert.ok(p.afterConfirm.fallback, 'blocked 计划必须带兜底去处')
})

// ─────────────────────────────────────────────────────────────
console.log('\n' + '─'.repeat(60))
console.log(`行为用例：通过 ${pass}，失败 ${failures.length}`)
if (failures.length) {
  for (const f of failures) console.log(`  ✗ ${f.name}\n      ${f.message}`)
  process.exit(1)
}
console.log('✓ 路由模块行为测试全部通过')
