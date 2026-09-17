#!/usr/bin/env node
/**
 * 小程序静态校验（前端无自动化测试时的兜底）
 *
 * 检查项：
 *   1. 所有 .json 语法可解析
 *   2. app.json 注册的页面四件套（.js/.json/.wxml/.wxss）齐全
 *   3. 页面路径规范（pages/ 下、文件名与所在目录同名）＋ 无重复注册
 *   4. tabBar（含 custom）配置合法：≤5 项、页面已注册、custom 组件文件存在
 *   5. 路由引用可达：页面/wxml 中出现的 /pages/... 必须在 app.json 注册
 *   6. tabBar 页面用 switchTab、非 tabBar 页面用 navigateTo（错用会导致静默失败）
 *   7. wxml 事件绑定的方法在同名 .js 中已定义（抓事件名写错/漏定义）
 *   8. project.config.json 的 AppID 格式合法（wx + 16 位 = 18 位；抓手抄漏字符）
 *   9. 真机走查锚点契约：登记过的属性仍在预期元素上、值仍是模板表达式
 *      （走查工具没有 index 参数、不支持伪类，只能靠属性选择器；锚点失效是静默的）
 *
 * 用法：node scripts/verify_miniapp.js
 * 退出码：0 通过 / 1 有问题
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', 'miniapp')
let errors = []
const checked = { json: 0, js: 0, routes: 0 }

// 个人/本机私有文件：不入库，也不计入统计（否则本地与 CI 的数量对不上）
const IGNORE_FILES = new Set(['project.private.config.json'])

function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name)
    let st
    try { st = fs.statSync(p) } catch (e) { continue }
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === '.git') continue
      walk(p, out)
    } else if (!IGNORE_FILES.has(name)) out.push(p)
  }
  return out
}

const files = walk(ROOT)

// ---- 1. JSON 语法 ----
for (const f of files.filter((x) => x.endsWith('.json'))) {
  try {
    JSON.parse(fs.readFileSync(f, 'utf8'))
    checked.json++
  } catch (e) {
    errors.push(`[JSON] ${path.relative(ROOT, f)}: ${e.message}`)
  }
}

// ---- 2. app.json 注册 & tabBar ----
const appJson = JSON.parse(fs.readFileSync(path.join(ROOT, 'app.json'), 'utf8'))
const pages = new Set(appJson.pages || [])
const tabBarPages = new Set(((appJson.tabBar && appJson.tabBar.list) || []).map((t) => t.pagePath))

for (const p of pages) {
  for (const ext of ['.js', '.json', '.wxml', '.wxss']) {
    if (!fs.existsSync(path.join(ROOT, p + ext))) errors.push(`[PAGE] 缺少文件 ${p}${ext}`)
  }
}

// 页面路径规范：必须在 pages/ 下，且文件名与所在目录同名（pages/<域>/<页>/<页>）
for (const p of pages) {
  const base = path.basename(p)
  if (p.split('/')[0] !== 'pages') errors.push(`[CONV] 页面不在 pages/ 下: ${p}`)
  if (base !== path.basename(path.dirname(p))) {
    errors.push(`[CONV] 路径与文件名不一致: ${p}（应为 .../${base}/${base}）`)
  }
}

const dupPages = Array.from(new Set((appJson.pages || []).filter((p, i, arr) => arr.indexOf(p) !== i)))
if (dupPages.length) errors.push(`[PAGE] 重复注册: ${dupPages.join(', ')}`)

if (appJson.tabBar && appJson.tabBar.custom) {
  for (const ext of ['.js', '.json', '.wxml', '.wxss']) {
    if (!fs.existsSync(path.join(ROOT, 'custom-tab-bar', 'index' + ext))) {
      errors.push(`[TABBAR] 缺少 custom-tab-bar/index${ext}`)
    }
  }
}

if (tabBarPages.size > 5) errors.push(`[TABBAR] list 超过 5 项（${tabBarPages.size}）`)
for (const t of tabBarPages) {
  if (!pages.has(t)) errors.push(`[TABBAR] tabBar 页面未在 pages 注册: ${t}`)
}

// ---- 3. 路由可达性 ----
// 路由必须以字母数字结尾（避免把 indexOf('/pages/xxx/') 这类前缀判断误当路由）
const routeRe = /['"`]\/pages\/[A-Za-z0-9_\/-]*[A-Za-z0-9_-]/g
const switchTabRe = /switchTab\s*\(\s*\{\s*url:\s*['"`](\/pages\/[^'"`?]+)/g

for (const f of files.filter((x) => x.endsWith('.js') || x.endsWith('.wxml'))) {
  const src = fs.readFileSync(f, 'utf8')
  const rel = path.relative(ROOT, f)

  let m
  routeRe.lastIndex = 0
  while ((m = routeRe.exec(src))) {
    const before = src.slice(Math.max(0, m.index - 32), m.index)
    if (/indexOf\s*\(\s*$|startsWith\s*\(\s*$/.test(before)) continue
    const route = m[0].slice(1).replace(/^\//, '')
    checked.routes++
    if (!pages.has(route)) errors.push(`[ROUTE] ${rel} 引用了未注册页面: /${route}`)
  }

  let s
  switchTabRe.lastIndex = 0
  while ((s = switchTabRe.exec(src))) {
    const route = s[1].replace(/^\//, '')
    if (!tabBarPages.has(route)) {
      errors.push(`[ROUTE] ${rel} switchTab 到非 tabBar 页: ${s[1]}（应使用 navigateTo）`)
    }
  }
}

// ---- 4. wxml 事件处理函数存在性（抓「事件名写错/方法漏定义」这类静默失败） ----
const handlerRe = /\b(?:capture-)?(?:bind|catch)(?::)?([a-zA-Z]+)\s*=\s*"([^"]*)"/g
checked.handlers = 0

for (const f of files.filter((x) => x.endsWith('.wxml'))) {
  const jsPath = f.replace(/\.wxml$/, '.js')
  if (!fs.existsSync(jsPath)) continue
  const wxml = fs.readFileSync(f, 'utf8')
  const js = fs.readFileSync(jsPath, 'utf8')
  const rel = path.relative(ROOT, f)

  let h
  handlerRe.lastIndex = 0
  while ((h = handlerRe.exec(wxml))) {
    const name = (h[2] || '').trim()
    if (!name || name.indexOf('{{') !== -1) continue
    checked.handlers++
    const defined = new RegExp('(^|[^\\w.$])' + name + '\\s*[(:]').test(js)
    if (!defined) errors.push(`[EVENT] ${rel} 绑定了未定义的方法: ${name}`)
  }
}

// ---- 5. AppID 格式（抓「手抄漏字符」这类只在真机预览时才暴露的错误） ----
// 背景：AppID 少一位时工具不会报「格式错」，而是静默降级成游客模式 ——
// 表现是「点预览没反应 + 真机调试灰显」，极易被误判成账号权限问题。
const APPID_RE = /^wx[\da-z]{16}$/
// 本机开发可接受的占位值（无法生成预览码，但工具能正常打开项目）
const APPID_PLACEHOLDERS = ['', 'wxYOUR_APPID_HERE', 'touristappid']
const cfgPath = path.join(ROOT, 'project.config.json')
if (fs.existsSync(cfgPath)) {
  const appid = JSON.parse(fs.readFileSync(cfgPath, 'utf8')).appid || ''
  if (APPID_PLACEHOLDERS.indexOf(appid) === -1 && !APPID_RE.test(appid)) {
    errors.push(
      `[APPID] project.config.json 的 appid 非法: "${appid}"（${appid.length} 位）` +
        '—— 微信 AppID 固定为 wx + 16 位小写字母/数字，共 18 位。' +
        '请从公众平台「开发管理 → 开发设置」复制，勿手抄'
    )
  }
}

// ---- 6. 真机走查锚点契约（抓「属性被搬走/改成常量」这类静默失效） ----
// 背景：走查工具（`wechatide`）**没有 index 参数、不支持伪类**，只能靠属性选择器
// 定位元素；`--x/--y` 坐标触摸也会落到第一个匹配元素。所以「点第 i 个订单卡」这件事
// 只能写成 `[data-order-id="123"]`。以下三条失效都是**静默**的：
//   ① 锚点被挪到内层按钮上（同一属性出现在多个元素 ⇒ 选到谁看引擎实现）；
//   ② 值写成常量（每行同值 ⇒ 选择器退化成"第一个"）；
//   ③ 属性被删（走查脚本报「找不到元素」，但那要跑真机才发现）。
// 本表把「章节依赖哪个锚点」写进 CI 可校验的登记表：改坏了这里先红。
//
// 登记项 kind 语义：
//   row    列表行的身份锚点 —— 带该属性的标签必须含该类名，且值必须是模板表达式
//   act    行内动作锚点 —— 带该属性的标签必须绑定指定事件处理函数
//   static 枚举型锚点 —— 值本来就是常量（如 `data-mode="fixed"`），只查类名归属
const WALK_ANCHORS = [
  // ㉞ S1 / UI-07（客户委托草稿/提交屏）：真实入口与两处页内动作。
  // 「委托发货」与「自主发货」在 cargo.wxml 里是**同类元素**（都是 `.ch-opt`），
  // 而走查工具没有 index 参数 ⇒ 必须靠可区分属性才能点到第二项。
  { kind: 'act', file: 'pages/publish/cargo/cargo.wxml', class: 'ch-opt', attr: 'data-act-entrust', value: '1', handler: 'pickEntrustDelivery' },
  { kind: 'act', file: 'pages/entrust/intake/intake.wxml', attr: 'data-act-submit-intake', value: '1', handler: 'onSubmit' },
  { kind: 'act', file: 'pages/entrust/intake/intake.wxml', attr: 'data-act-intake-restart', value: '1', handler: 'onRestart' },
  // ⑦/⑦b/⑦c/⑧/⑨/⑨b 六章共用：订单卡与行内动作
  { kind: 'row', file: 'pages/trade/orders/orders.wxml', class: 'order-card', attr: 'data-order-id', value: '{{item.id}}' },
  { kind: 'act', file: 'pages/trade/orders/orders.wxml', attr: 'data-act-contract', handler: 'onContractPage', value: '{{item.id}}' },
  { kind: 'act', file: 'pages/trade/orders/orders.wxml', attr: 'data-act-pay', handler: 'onPayPage', value: '{{item.id}}' },
  // ENT-044：`data-act-pay` 曾在同一文件里被**两处复用**（「去支付」与「支付详情」）
  // ⇒ 走查按它取数时会同时命中两个入口（曾经据此得出过错误结论）。拆成两个属性：
  // 「去支付」= `data-act-pay`，「支付详情」= `data-act-payinfo`。
  { kind: 'act', file: 'pages/trade/orders/orders.wxml', attr: 'data-act-payinfo', handler: 'onPayPage', value: '{{item.id}}' },
  { kind: 'act', file: 'pages/trade/orders/orders.wxml', attr: 'data-act-cancel', handler: 'onCancel', value: '{{item.id}}' },
  { kind: 'act', file: 'pages/trade/orders/orders.wxml', attr: 'data-act-complete', handler: 'onComplete', value: '{{item.id}}' },
  { kind: 'act', file: 'pages/trade/orders/orders.wxml', attr: 'data-act-detail', handler: 'onDetail', value: '{{item.id}}' },
  { kind: 'act', file: 'pages/trade/orders/orders.wxml', attr: 'data-act-ship', handler: 'onShip', value: '{{item.id}}' },
  // ④b 撮合：两个方向的候选卡与「选船下单」
  { kind: 'row', file: 'pages/trade/match/match.wxml', class: 'cand-card', attr: 'data-ship-id', value: '{{item.ship_id}}' },
  { kind: 'row', file: 'pages/trade/match/match.wxml', class: 'cand-card', attr: 'data-cargo-id', value: '{{item.cargo_id}}' },
  { kind: 'act', file: 'pages/trade/match/match.wxml', attr: 'data-act-pick-ship', handler: 'onTapCandidate', value: '{{item.ship_id}}' },
  // ⑤ 发布空船前序：船东船队卡的「为船找货」（ENT-035 登记，供第 ⑨ 章用）
  { kind: 'act', file: 'pages/owner/owner.wxml', class: 'btn-secondary', attr: 'data-id', value: '{{item.id}}', handler: 'onShipMatch' },
  // ⑩ 港口：服务网格 / 预约卡 / 泊位卡（三个锚点都**不能**复用 `data-id` ——
  //    本文件里 `data-id` 已被 4 处占用（预约卡、驳回、确认、泊位卡），
  //    而登记表按 (file, attr) 去重 ⇒ 复用会让校验把别人的标签也算进来。
  //    这与 ENT-030 的 `data-rm-key` 同一取向：宁可新起一个属性名，也不要歧义。）
  { kind: 'act', file: 'pages/port/port.wxml', class: 'grid-item', attr: 'data-svc-key', value: '{{it.key}}', handler: 'onServiceTap' },
  { kind: 'row', file: 'pages/port/port.wxml', class: 'ops-card', attr: 'data-appt-id', value: '{{item.id}}' },
  { kind: 'row', file: 'pages/port/port.wxml', class: 'ops-card', attr: 'data-berth-id', value: '{{item.id}}' },
  // ⑩ 泊位档期、⑪ 预约审核：列表行
  { kind: 'row', file: 'pages/port/berth/berth.wxml', class: 'gt-row', attr: 'data-bar-key', value: '{{item.key}}' },
  { kind: 'row', file: 'pages/port/berth/berth.wxml', class: 'rule', attr: 'data-rule-key', value: '{{item.key}}' },
  { kind: 'row', file: 'pages/port/appt/appt.wxml', class: 'tl-item', attr: 'data-tl-key', value: '{{item.key}}' },
  // ⑤ 发布空船：计价方式分段控件（既有锚点，一并纳管）
  { kind: 'static', file: 'pages/publish/ship/ship.wxml', class: 'seg-item', attr: 'data-mode' },
  // ENT-030 切四之六：登记案件（第 ㉖ 章）与案件处置（第 ㉗ 章）依赖的锚点。
  // ⚠️ 这些不是"顺手加个属性"：`detail.wxml` 的 `.slot-btn` 同时属于 7 个「记录任务」
  //    按钮与 1 个「登记案件」按钮，而走查工具**没有 index 参数** ——
  //    没有锚点时 `tap('.slot-btn')` 会点到第一个「记录任务」，把断言建立在巧合上。
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-create-case',
    value: 'create-case', handler: 'onCreateCase' },
  // 2026-09-16（S1 收尾）：详情页的**两个关键路径**从原生弹层改成页内 DOM。
  // 受理确认条（三段式）与「记录任务」输入条各三个锚点，属性名两两不同 ——
  // `onToggleClaim` 被"展开"与"取消"共用，同名会让两者在断言与这张表里同形
  // （登记表按 file|attr 去重、每文件每属性只能登记一次）。
  // 为什么必须有锚点：本页有 7 个 `.slot-btn`（各槽位的「记录任务」）与 1 个受理按钮，
  // 走查工具**没有 index 参数** ⇒ 没有锚点就只能点到第一个，把断言建立在巧合上。
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-claim-open',
    value: '1', handler: 'onToggleClaim' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-claim-submit',
    value: '1', handler: 'onSubmitClaim' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-claim-cancel',
    value: '1', handler: 'onToggleClaim' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-task-submit',
    value: '{{item.key}}', handler: 'onSubmitTask' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-task-cancel',
    value: '{{item.key}}', handler: 'onCancelTask' },
  // 2026-09-17（S3 收口）：组装对客报价的三段式（展开 / 提交 / 取消）。
  // 与上面「受理确认条」同一条理由 —— 本页已有 7 个 `.slot-btn` 与 1 个受理按钮，
  // 走查工具没有 index 参数，没有专属锚点就只能点到第一个。
  // ⚠️ `onToggleQuote` 只被"展开"用；"取消"给的是 `onCancelQuote`（语义不同：
  //    取消会**清空**表单，展开/收起不会），所以两者必须各有一个锚点。
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-quote-open',
    value: '1', handler: 'onToggleQuote' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-quote-submit',
    value: '1', handler: 'onSubmitQuote' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-quote-cancel',
    value: '1', handler: 'onCancelQuote' },
  // 2026-09-17（第 4 条 · 运力确认与有效期）：详情页新增的经理侧三方块
  // （候选运力清单 / 登记候选 / 确认与复算）。与上面两组同一条理由 ——
  // 本页已有 7 个 `.slot-btn`（记录任务）+ 受理 + 组装报价，而走查工具**没有 index 参数**
  // ⇒ 没有专属锚点就只能点到第一个，断言建立在巧合上。
  // 先登记（第 ㊺ 章的设备走查还没写）：锚点登记表是"改模板时弄丢了会红"的那道闸，
  // 等章节写完再登记，中间那段时间是**无保护**的。
  // ⚠️ `data-act-cap-partial` **故意不写 `value`**：它两处取值本来就是 `'0'` / `'1'`
  //    （选择器靠值区分，要的不是"每行同值"那条保护）；而这里的校验是**所有命中**
  //    都必须等于声明的值 —— 写死一个会让另一处当场红。
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-open',
    value: '1', handler: 'onToggleCap' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-submit',
    value: '1', handler: 'onSubmitCap' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-cancel',
    value: '1', handler: 'onCancelCap' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-partial',
    handler: 'onCapPickPartial' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-kind',
    value: '{{option.key}}', handler: 'onCapPickKind' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-confirm-open',
    value: '{{item.candidateId}}', handler: 'onOpenCapConfirm' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-confirm-submit',
    value: '{{item.candidateId}}', handler: 'onSubmitCapConfirm' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-confirm-cancel',
    value: '{{item.candidateId}}', handler: 'onCancelCapConfirm' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-cap-recheck',
    value: '{{item.confirmationId}}', handler: 'onCapRecheck' },
  // 运输计划（三段航段）与必需任务前置（合同 §10.1 第 4 步；BP-03 第 1 条）。
  // 三个**行**锚点，判据都是"渲染树里数得出来的事实"：
  //   * 段数 = 段的行数（模板**不拼**"三段" —— 段数是数据的属性）；
  //   * 任务数 = 派单事实；前置文案是"必需任务前置"这一条要看的**判据本身**，
  //     且「无固定前置」与「前置：任务 #<id>」两种说法必须分得开（见 `decorateAssignmentPlan`）。
  // ⚠️ 本组是**先登记、走查章节还没写**（与 §7.16 条目 126 同一处境）：
  //    登记不等于取证 —— 计一档"已实现"，不计"已设备运行"。
  { kind: 'row', file: 'pages/entrust/detail/detail.wxml', class: 'plan-leg',
    attr: 'data-plan-leg', value: '{{item.seqText}}' },
  { kind: 'row', file: 'pages/entrust/detail/detail.wxml', class: 'plan-task',
    attr: 'data-plan-task', value: '{{item.taskIdText}}' },
  { kind: 'row', file: 'pages/entrust/detail/detail.wxml', class: 'plan-task-pre',
    attr: 'data-plan-task-pre', value: '{{item.preText}}' },
  // 航段命令（建段 / 改段留版本 / 版本历史）—— §10.1 第 4 步的**写侧**。
  // ⚠️ 与上面那组同一处境：**先登记、走查章节随后写**（㊼ 章）。
  // 判据是"渲染树里数得出来的事实"：段行 / 版本行 / 表单三个键。
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-leg-open',
    value: '1', handler: 'onOpenLeg' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-leg-edit',
    value: '{{item.legId}}', handler: 'onEditLeg' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-leg-hist',
    value: '{{item.legId}}', handler: 'onToggleLegHistory' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-leg-mode',
    value: '{{option.key}}', handler: 'onPickLegMode' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-leg-submit',
    value: '1', handler: 'onSubmitLeg' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', attr: 'data-act-leg-cancel',
    value: '1', handler: 'onCancelLeg' },
  { kind: 'row', file: 'pages/entrust/detail/detail.wxml', class: 'plan-rev',
    attr: 'data-plan-rev', value: '{{rev.revisionNoText}}' },
  // 页内输入框（文本靠 setData 注值，见走查脚本的声明），与案件页的 `act-input`+`data-df` 同形
  { kind: 'static', file: 'pages/entrust/detail/detail.wxml', class: 'slot-input', attr: 'data-df' },
  { kind: 'act', file: 'pages/entrust/case-create/case-create.wxml', attr: 'data-act-toggle-links',
    value: '1', handler: 'onToggleLinks' },
  { kind: 'act', file: 'pages/entrust/case-create/case-create.wxml', attr: 'data-act-submit-case',
    value: '1', handler: 'onSubmit' },
  // 移除已选受影响项。属性名是 `data-rm-key` 而**不是** `data-key`：
  // 本页的筛选 pill 已经占用了 `data-key`，共用会让选择器歧义（也登记不进这张表）。
  { kind: 'act', file: 'pages/entrust/case-create/case-create.wxml', class: 'link-remove',
    attr: 'data-rm-key', value: '{{item.key}}', handler: 'onRemoveLink' },
  // ENT-040：工作台委托卡片的「会话」入口（DR-0015 workflow → session 边的代码证据）。
  // ⚠️ 属性名用 `data-act-session` 而不是复用 `data-id`：卡片自身已用 `data-id` 作为
  //    "打开详情"的载荷，共用会让"点第 i 张卡的会话"与"点第 i 张卡"分不开 ——
  //    同一文件同一属性只能登记一次（登记表按 file|attr 去重）。
  { kind: 'act', file: 'pages/entrust/workbench/workbench.wxml', class: 'card-act',
    attr: 'data-act-session', value: '{{item.assignmentId}}', handler: 'onOpenSession' },
  // S1 工作项 4（第 ㉟ 章）：队列卡片上的**受理**三段式。
  // 与 ENT-041「应用变更」同形 —— 确认条是**页内 DOM**（`wx.showModal` 不在渲染树里、
  // 工具点不到它的确认键），三个属性名两两不同：`onToggleClaim` 被「受理」与「取消」
  // 共用，若都写 `data-act-claim`，两者在断言与这张表里就同形了（表按 file|attr 去重）。
  { kind: 'act', file: 'pages/entrust/workbench/workbench.wxml', class: 'card-act',
    attr: 'data-act-claim', value: '{{item.assignmentId}}', handler: 'onToggleClaim' },
  { kind: 'act', file: 'pages/entrust/workbench/workbench.wxml', class: 'card-act',
    attr: 'data-act-claim-submit', value: '{{item.assignmentId}}', handler: 'onSubmitClaim' },
  { kind: 'act', file: 'pages/entrust/workbench/workbench.wxml', class: 'card-act',
    attr: 'data-act-claim-cancel', value: '{{item.assignmentId}}', handler: 'onToggleClaim' },
  // 受影响项候选行（任务 / 成果共用一份候选表，靠 `target_kind` 区分）
  { kind: 'row', file: 'pages/entrust/case-create/case-create.wxml', class: 'cand-row',
    attr: 'data-id', value: '{{item.target_id}}' },
  // ENT-032（第 ㉙ 章）：UI-04 组织级队列依赖的锚点。
  // ⚠️ 同一页上有**四组**可点元素：两个队列 pill、两行筛选、案件卡。
  //    属性名必须两两不同 —— 否则 `[data-key="all"]` 这种选择器会落到
  //    另一个筛选条上（工具没有 index，选到谁是引擎实现细节）。
  { kind: 'act', file: 'pages/entrust/workbench/workbench.wxml', class: 'queue-pill',
    attr: 'data-queue', handler: 'onSwitchQueue' },
  { kind: 'act', file: 'pages/entrust/workbench/workbench.wxml', class: 'filter-pill',
    attr: 'data-case-scope', handler: 'onCaseScope' },
  { kind: 'act', file: 'pages/entrust/workbench/workbench.wxml', class: 'filter-pill',
    attr: 'data-case-kind', handler: 'onCaseKind' },
  { kind: 'row', file: 'pages/entrust/workbench/workbench.wxml', class: 'list-card',
    attr: 'data-case-id', value: '{{item.caseId}}' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-toggle-links',
    value: '1', handler: 'onToggleLinkPick' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-reopen',
    value: '1', handler: 'onOpenReopen' },
  // 处置区的三个**页内表单提交**锚点。它们取代了原来的 `wx.showModal` 输入：
  // 原生弹层不在渲染树里，走查点不到，条件 4 就永远只能记 not-run。
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-decide-submit',
    value: '1', handler: 'onSubmitDecision' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-close-submit',
    value: '1', handler: 'onSubmitClose' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-reopen-submit',
    value: '1', handler: 'onSubmitReopen' },
  // 页内表单的输入框（`data-df` 区分是哪一栏；文本靠 setData 注值，见走查脚本的声明）
  { kind: 'static', file: 'pages/entrust/case/case.wxml', class: 'act-input', attr: 'data-df' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', class: 'act-chip-x',
    attr: 'data-link', value: '{{item.linkId}}', handler: 'onRemoveLink' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', class: 'act-btn',
    attr: 'data-status', value: '{{item.key}}', handler: 'onPickDecision' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', class: 'act-btn',
    attr: 'data-disp', value: '{{item.key}}', handler: 'onPickDisposition' },
  { kind: 'row', file: 'pages/entrust/case/case.wxml', class: 'cand-row',
    attr: 'data-id', value: '{{item.target_id}}' },
  // ENT-041（第 ㉛ 章）：案件页「应用变更」的三段式。确认条必须是**页内 DOM**
  // （`wx.showModal` 不在渲染树里、工具点不到，这正是条件 4 原先只能记 not-run 的原因）。
  // 三个属性名两两不同：`onToggleApply` 被两个元素共用，若都写 `data-act-apply`
  // 就会让「展开」与「取消」在断言里同形（登记表也按 file|attr 去重，只能登记一个）。
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-apply',
    value: '1', handler: 'onToggleApply' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-apply-submit',
    value: '1', handler: 'onSubmitApply' },
  { kind: 'act', file: 'pages/entrust/case/case.wxml', attr: 'data-act-apply-cancel',
    value: '1', handler: 'onToggleApply' },
  // S1 工作项 5（第 ㊶ 章）：「我的委托」列表行的身份锚点。
  // 走查要断言的是"**我提交的那一单**重新进入后仍在列表里可见"，
  // 而列表里通常**不止一张**委托（种子 + 本轮新提交的都在）——
  // 没有锚点时 `tap('.mine-card')` 只能点到第一张，那条断言就建立在
  // "它恰好排在首位"这个巧合上（排序是 `updated_at DESC`，会随其它动作变化）。
  { kind: 'row', file: 'pages/entrust/assignments/assignments.wxml', class: 'mine-card',
    attr: 'data-mine-id', value: '{{item.assignmentId}}' },
  // 同上，「我的」页上的**货主侧**入口。⚠️ 本页有**两块** `.entrust-entry`
  // （经理入口 / 货主入口，由两个独立的服务端探测分别门控），
  // 走查要真实点击的是后者 —— 没有这个属性，`tap('.entrust-entry')` 会落到
  // 先渲染的那一块（经理入口）上，把断言建立在"它恰好先出现"这个巧合上。
  { kind: 'act', file: 'pages/mine/mine.wxml', class: 'entrust-entry',
    attr: 'data-act-mine-entrust', value: '1', handler: 'onMineEntrust' },
  // ㊸ 主演示第 1–3 步（合同 §10.1）：会话屏与成果页的**关键动作**锚点。
  // ⚠️ 这些不是"顺手加个属性"。㊸ 章要在这两页真实点击走完「上传 → 引用 → 采纳 →
  //    更正 → 从工作台打开同一份成果」，而走查工具**没有 index 参数**：
  //    同类元素（`.att-btn` 三个、`.btn-primary` 两个、`.confirm-ok` 多处）只能靠
  //    可区分属性才能点到指定那一个。若不登记，下一次改模板把它们弄丢时不会有任何提示
  //    —— 走查会在真机上退化成"点不到"，看起来像功能坏了。
  { kind: 'act', file: 'pages/entrust/session/session.wxml', class: 'att-upload',
    attr: 'data-act-pick-quote', value: '1', handler: 'onPickQuote' },
  { kind: 'act', file: 'pages/entrust/session/session.wxml', class: 'att-sample',
    attr: 'data-act-sample-quote', value: '1', handler: 'onUseSampleQuote' },
  { kind: 'act', file: 'pages/entrust/session/session.wxml', class: 'att-btn',
    attr: 'data-act-use-attachment', value: '1', handler: 'onUseAttachment' },
  { kind: 'act', file: 'pages/entrust/session/session.wxml', class: 'adopt-btn',
    attr: 'data-act-adopt', value: '1', handler: 'onAdoptAsk' },
  { kind: 'act', file: 'pages/entrust/session/session.wxml', class: 'confirm-ok',
    attr: 'data-act-adopt-confirm', value: '1', handler: 'onAdoptConfirm' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-primary',
    attr: 'data-act-edit', value: '1', handler: 'onEdit' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-primary',
    attr: 'data-act-save', value: '1', handler: 'onSave' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-ghost',
    attr: 'data-act-cancel-edit', value: '1', handler: 'onCancelEdit' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'slot-btn',
    attr: 'data-act-confirm-revision', value: '1', handler: 'onConfirm' },
  // 确认条本体（页内）：`wx.showModal` 的确认键工具点不到，故确认动作走页内。
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-primary',
    attr: 'data-act-confirm-revision-submit', value: '1', handler: 'onConfirmSubmit' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-ghost',
    attr: 'data-act-confirm-revision-cancel', value: '1', handler: 'onConfirmCancel' },
  // ㊹ S3 纵向切片：对客发布（经理侧，成果页）与客户响应（客户侧，委托详情页）。
  // 这两条链各有一个**唯一会改变业务事实**的动作，都必须可被真机点到：
  //   · 客户「接受/拒绝」—— 后端用 `UNIQUE(release_id)` 钉死"只能响应一次"，没有第二
  //     次机会；走 `wx.showModal` 就等于把唯一的落点放进渲染树外，工具够不着。
  //   · 经理「发布 / 撤回」—— 发布冻结的是**精确版本**的内容快照，撤回理由必填。
  // 三处细节值得留在表里：
  //   ① 客户侧的「接受」「拒绝」**共用同一个** `onOfferRespond`（靠 `data-decision` 分派），
  //      与既有 `onToggleClaim` 同一形态。所以属性名必须两两不同，否则
  //      `[data-act-offer-accept]` 与 `[data-act-offer-reject]` 在选择器上不可区分。
  //   ② `data-act-offer-origin` 是**纯展示**锚点（来源标注那一行）—— 它没有 `bindtap`，
  //      登记成 `static` 而不是 `act`：`act` 会去查事件绑定，查不到就报错，
  //      而它本来就不该有点击行为。
  //   ③ `data-act-offer-download` 的值是**附件 id**（每份授权附件一个按钮）：写成常量会让
  //      选择器退化成"第一个附件"，而"点第 2 份能不能下"正是白名单切片要证明的事。
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', class: 'slot-btn',
    attr: 'data-act-offer-accept', value: '1', handler: 'onOfferRespond' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', class: 'slot-btn',
    attr: 'data-act-offer-reject', value: '1', handler: 'onOfferRespond' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', class: 'slot-btn',
    attr: 'data-act-offer-submit', value: '1', handler: 'onOfferSubmit' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', class: 'slot-btn',
    attr: 'data-act-offer-cancel', value: '1', handler: 'onOfferCancel' },
  { kind: 'act', file: 'pages/entrust/detail/detail.wxml', class: 'slot-btn',
    attr: 'data-act-offer-download', value: '{{att.attachmentId}}',
    handler: 'onDownloadOfferAttachment' },
  { kind: 'static', file: 'pages/entrust/detail/detail.wxml', class: 'slot-note',
    attr: 'data-act-offer-origin' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'slot-btn',
    attr: 'data-act-release', value: '1', handler: 'onRelease' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'slot-btn',
    attr: 'data-act-withdraw-open', value: '1', handler: 'onWithdraw' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-primary',
    attr: 'data-act-release-submit', value: '1', handler: 'onReleaseSubmit' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-ghost',
    attr: 'data-act-release-cancel', value: '1', handler: 'onReleaseCancel' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-primary',
    attr: 'data-act-withdraw-submit', value: '1', handler: 'onWithdrawSubmit' },
  { kind: 'act', file: 'pages/entrust/artifact/artifact.wxml', class: 'btn-ghost',
    attr: 'data-act-withdraw-cancel', value: '1', handler: 'onWithdrawCancel' },
]

// 把 wxml 切成「标签」块：先剥掉注释（注释里的撇号会被当成引号，导致整个标签块
// 一路吞到下一个同样的引号 —— 实测会把 20 行卷进一个"标签"里，报出假歧义），
// 再按 `<` 起步、跳过属性值引号内的 `>`。属性值统一是双引号，
// 只跟踪 `"`（把 `'` 也当引号会让 `{{a ? 'x' : ''}}` 提前闭合）。
function tagsOf(src) {
  const body = src.replace(/<!--[\s\S]*?-->/g, '')
  const out = []
  let i = 0
  while (i < body.length) {
    const lt = body.indexOf('<', i)
    if (lt < 0) break
    let j = lt + 1
    let quote = null
    while (j < body.length) {
      const ch = body[j]
      if (quote) { if (ch === quote) quote = null } else if (ch === '"') quote = ch
      else if (ch === '>') break
      j++
    }
    out.push(body.slice(lt, j + 1))
    i = j + 1
  }
  return out
}

function attrValue(tag, name) {
  // 前置 (^|\s) 是必须的：`\bclass\s*=` 会命中 `hover-class=`（`-` 是非单词字符，
  // `\b` 在它和 `c` 之间成立），于是拿 hover 的值当 class 判，报出假的「锚点搬走」。
  const m = tag.match(new RegExp('(?:^|\\s)' + name + '\\s*=\\s*"([^"]*)"'))
  return m ? m[1] : null
}

const anchorSeen = new Set()
checked.anchors = 0
for (const a of WALK_ANCHORS) {
  const label = `[ANCHOR] ${a.file} · ${a.attr}`
  const key = a.file + '|' + a.attr
  if (anchorSeen.has(key)) errors.push(`${label}: 同一文件内重复登记`)
  anchorSeen.add(key)

  const abs = path.join(ROOT, a.file)
  if (!fs.existsSync(abs)) {
    errors.push(`${label}: 文件不存在（登记表指向了已删除的模板）`)
    continue
  }
  const tags = tagsOf(fs.readFileSync(abs, 'utf8'))
  const hits = tags.filter((t) => attrValue(t, a.attr) !== null)
  if (!hits.length) {
    errors.push(`${label}: 模板里找不到该属性（走查选择器会失效）`)
    continue
  }
  for (const t of hits) {
    checked.anchors++
    const cls = attrValue(t, 'class') || ''
    const val = attrValue(t, a.attr)
    if (a.class && cls.split(/\s+/).indexOf(a.class) === -1) {
      errors.push(`${label}: 该属性出现在不含 .${a.class} 的标签上 —— 锚点被搬走会造成选择歧义`)
    }
    if (a.value !== undefined && val !== a.value) {
      errors.push(`${label}: 值应为 ${a.value}，实际 ${JSON.stringify(val)}（值写成常量会让每行同值）`)
    }
    if (a.kind === 'row' && val.indexOf('{{') === -1) {
      errors.push(`${label}: 行锚点的值必须是模板表达式，实际 ${JSON.stringify(val)}`)
    }
    if (a.kind === 'act') {
      const bind = (t.match(/\b(?:catch|bind)tap\s*=\s*"([^"]*)"/) || [])[1]
      if (bind !== a.handler) {
        errors.push(`${label}: 应绑定 ${a.handler}，实际 ${JSON.stringify(bind || null)}`)
      }
    }
  }
}

// ---- 走查脚本里**禁止**用裸属性选择器数个数 ----
//
// 2026-09-17 实测（㊻ 章）：本工具链的 `querySelectorAll` **不认**裸属性选择器
// `[data-x]`，静默回 **0**（同一页同一语义：类名 → 1、`[data-x="值"]` → 1、裸属性 → 0）。
// 它比"报错"更坏，因为两种写法都会骗过审阅：
//   · `count('[data-x]') >= 1` ⇒ **假红**（看起来像模板没渲染）；
//   · `count('[data-x]') == 0`（断言"不该有"）⇒ **假绿**，真出问题也照样绿。
//
// 入口处已加运行期守卫（`scripts/wechatide_client.py` 的 `count()` 直接抛 `ValueError`），
// 这里再加一道**静态**检查：让它在"还没跑起来"的时候就红，而不是等一轮真机走查白跑。
// ⇒ 要按属性定位，写 `[data-x="值"]`；只按"有这个属性"筛，改用类名。
// ⚠️ 覆盖范围是 `scripts/*.py`（走查消费者），不是 miniapp —— 所以这里用独立的 REPO 根路径。
{
  const REPO = path.resolve(__dirname, '..')
  const bareCall = /\.count\(\s*f?(['"])\s*\[\s*[A-Za-z_][\w-]*\s*\]\s*\1/
  const pyDir = path.join(REPO, 'scripts')
  for (const f of fs.readdirSync(pyDir).filter((x) => x.endsWith('.py'))) {
    const abs = path.join(pyDir, f)
    fs.readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
      const t = line.trim()
      if (!t || t.startsWith('#')) return
      if (bareCall.test(line)) {
        errors.push(
          `[SELECTOR] scripts/${f}:${i + 1}: 用了裸属性选择器数个数（本工具链静默回 0）` +
            ` ⇒ 改用 [data-x="值"] 或类名`
        )
      }
    })
  }
}

// ---- 输出 ----
console.log(
  `检查完成：JSON ${checked.json} 个 / 页面 ${pages.size} 个 / 路由引用 ${checked.routes} 处 / ` +
    `事件绑定 ${checked.handlers} 处 / 走查锚点 ${checked.anchors} 处`
)
if (errors.length) {
  console.log(`\n发现 ${errors.length} 个问题：`)
  errors.forEach((e) => console.log('  - ' + e))
  process.exit(1)
}
console.log('全部通过 ✓')
