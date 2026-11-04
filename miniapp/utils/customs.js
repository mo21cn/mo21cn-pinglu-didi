// 智慧关务 · 取数与口径（D1 前端两页的唯一数据入口）
//
// 为什么单独一个 util 而不是写在页面里：这两页要读的是**同一份口径**——
// 「官方源不可用」「状态来自人工登记」「每条事件带来源与获取时间」。
// 口径散落在两个页面的 setData 里，就会出现"列表说暂不可用、详情说已接入"
// 这类自相矛盾，而那正是 AC-02 想防的事。
//
// ⛔ 本文件**不得**在没有数据时编造一条"看起来像最新"的状态：
//    拿不到就渲染"暂无"，拿不到官方源就渲染"暂不可用"，绝不拿旧值凑。
const { request } = require('./request')

const BASE = '/api/v1/customs'

//: 状态机的中文标签。键与后端 `STATUSES` 一一对应；
//: 未收录的值一律回落"未知"——**未知保持未知**，不猜。
const STATUS_LABEL = {
  unknown: '未知',
  manifest_filed: '已报舱单',
  declared: '已申报',
  inspection: '查验中',
  released: '已放行',
  held: '暂扣',
  rejected: '退单'
}

//: 终态：到了这里就不再随时间推进（与后端 `TERMINAL_STATUSES` 同口径）
const TERMINAL = { released: true }

/** 数据源状态（`GET /sources`）—— 页面顶部的状态条唯一数据源 */
function fetchSources() {
  return request({ url: BASE + '/sources', method: 'GET' })
}

/**
 * 人工登记记录列表（`GET /declarations`）。
 *
 * `no` 给了就是按单号精确查；**查不到返回空列表**（不是 404，也不伪造一条空记录）。
 */
function fetchDeclarations(options) {
  const opts = options || {}
  const data = {}
  if (opts.no) data.no = String(opts.no).trim()
  return request({ url: BASE + '/declarations', method: 'GET', data: data })
}

/** 详情 + 时间线 + 新鲜度（`GET /declarations/{id}`） */
function fetchDeclaration(id) {
  return request({
    url: BASE + '/declarations/' + encodeURIComponent(String(id)),
    method: 'GET'
  })
}

// ---------------------------------------------------------------------------
// D2：商品归类辅助（模型建议 → 人工定稿）
//
// 三条铁律（与后端 `classifications.py` 同一口径，前端只负责**照实呈现**）：
//   1. 模型候选**不是结论** —— `hs_code` 只有人工定稿后才有值；
//   2. 模型不可用时**不是失败页** —— 接口仍 201，`status=manual_required` +
//      `degraded.reason` 给一句中文原因，`manual_entry.available=true` 给出口；
//   3. 人工结论**不覆盖**模型候选 —— 候选表只追加。
// ⛔ 因此前端**不得**用"候选为空"来判断"模型挂了"：那两件事在界面上必须分开显示。
// ---------------------------------------------------------------------------

/** 幂等键：写端点必须带 `Idempotency-Key`（与委托支线同口径） */
function newIdemKey(prefix) {
  return (prefix || 'cus') + '-' + Date.now() + '-' + Math.floor(Math.random() * 1e6)
}

/** 发起一次归类建议（`POST /classifications`，模型不可用时仍 201） */
function createClassification(payload) {
  return request({
    url: BASE + '/classifications',
    method: 'POST',
    data: payload || {},
    headers: { 'Idempotency-Key': newIdemKey('cus-cls') }
  })
}

/** 归类记录列表（`GET /classifications`；可按 `declaration_id` / `status` 过滤） */
function fetchClassifications(options) {
  const opts = options || {}
  const data = {}
  if (opts.declaration_id) data.declaration_id = Number(opts.declaration_id)
  if (opts.status) data.status = String(opts.status)
  return request({ url: BASE + '/classifications', method: 'GET', data: data })
}

/** 归类详情（`GET /classifications/{id}`） */
function fetchClassification(id) {
  return request({
    url: BASE + '/classifications/' + encodeURIComponent(String(id)),
    method: 'GET'
  })
}

/** 人工填写并确认结论（`POST /classifications/{id}/manual`，不依赖模型） */
function submitManualClassification(id, payload) {
  return request({
    url: BASE + '/classifications/' + encodeURIComponent(String(id)) + '/manual',
    method: 'POST',
    data: payload || {},
    headers: { 'Idempotency-Key': newIdemKey('cus-man') }
  })
}

function statusLabel(status) {
  return STATUS_LABEL[status] || '未知'
}

/**
 * 服务端时间 → 本地时间（`YYYY-MM-DD HH:mm`）。
 *
 * ⚠️ 这件事**必须做**，否则界面会显示一个比用户的表慢 8 小时的时间：
 * 服务端所有时间列都是 **UTC 且无时区标记**（`utcnow_naive()` / `datetime.now(UTC)
 * .replace(tzinfo=None)`，格式 `YYYY-MM-DD HH:MM:SS`）。直接渲染出来，"核对于
 * 2026-10-26 03:21" 会在一个明明是 11:21 的上午出现在屏幕上 —— 而且它看起来
 * 完全正常，没有人会怀疑。AC-09 要的是"来源 + 获取时间"都可信，
 * 一个差 8 小时的时间戳不算可信。
 *
 * 解析失败就**原样返回**：显示一个可疑但真实的值，好过显示 "Invalid Date"，
 * 也好过悄悄换成"现在"（那就是伪造新鲜度）。
 */
function toLocalTime(text) {
  const raw = String(text == null ? '' : text).trim()
  if (!raw) return ''
  // 已经是带时区的 ISO 串就直接交给 Date；否则补 `T` 与 `Z`（声明它是 UTC）。
  const iso = /[zZ]|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : raw.replace(' ', 'T') + 'Z'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return raw
  const p = function (n) {
    return (n < 10 ? '0' : '') + n
  }
  return (
    d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
    ' ' + p(d.getHours()) + ':' + p(d.getMinutes())
  )
}

function isTerminal(status) {
  return !!TERMINAL[status]
}

/**
 * 数据源状态条的展示模型。
 *
 * 返回 `{ notice, officialUnavailable, rows }`：
 * - `officialUnavailable` 为真时，页面**不渲染**"查官方状态"入口
 *   （PRD §3 第 4 条：不要放一个点进去必然失败的入口）；
 * - `rows` 逐条给 label / state / reason，模板只做映射、不做判断。
 */
function sourcesView(data) {
  const body = data || {}
  const list = Array.isArray(body.sources) ? body.sources : []
  const rows = list.map(function (s) {
    return {
      key: s.key,
      label: s.label,
      kind: s.kind,
      state: s.state,
      stateLabel: s.state_label || (s.state === 'available' ? '可用' : '暂不可用'),
      available: s.state === 'available',
      reason: s.reason || ''
    }
  })
  const manual = rows.filter(function (r) {
    return r.kind === 'manual'
  })[0]
  return {
    notice: body.notice || '',
    checkedAt: toLocalTime(body.checked_at),
    officialUnavailable: body.official_available === false,
    manualAvailable: !!(manual && manual.available),
    rows: rows
  }
}

/**
 * 单条记录的展示模型。
 *
 * `tail` 只在**非终态**时出现：终态还给"下一步会怎样"就是在暗示一个不会发生的变化。
 */
function declarationView(d) {
  const row = d || {}
  return {
    id: row.id,
    no: row.declaration_no || '（无单号）',
    kind: row.kind || 'export',
    status: row.status || 'unknown',
    statusLabel: statusLabel(row.status),
    terminal: isTerminal(row.status),
    note: row.note || '',
    revision: row.revision || 1,
    createdAt: toLocalTime(row.created_at),
    updatedAt: toLocalTime(row.updated_at)
  }
}

/**
 * 新鲜度卡的展示模型。
 *
 * 为什么要过一道：`freshness.last_fetched_at` 也是**服务端 UTC 原值**。
 * 直接渲染就会出现"同一屏上时间线写着 11:30、上面的最后一次获取写着 03:30"
 * —— 用户没法判断是数据错还是时区差，等于把可信度自己毁掉。
 * 所以**一切时间字段都从本函数出**，模板里不再直接读 `freshness.*_at`。
 */
function freshnessView(f) {
  const row = f || {}
  return {
    officialState: row.official_state || 'unavailable',
    officialLabel: row.official_state_label || '暂不可用',
    lastFetchedAt: toLocalTime(row.last_fetched_at),
    lastSource: row.last_source || '',
    sourceKind: row.source_kind || '',
    message: row.message || ''
  }
}

/**
 * 时间线的一条事件。
 *
 * ⚠️ `fetchedAt` 与 `occurredAt` **不能合并成一个时间**：
 * 前者是"我们什么时候知道的"，后者是"事情什么时候发生的"。
 * 合成一个字段后，一条三天前的人工补录会显示成"刚刚更新"——那是在伪装新鲜度（AC-09）。
 */
function eventView(e) {
  const row = e || {}
  return {
    id: row.id,
    status: row.status || 'unknown',
    statusLabel: statusLabel(row.status),
    occurredAt: toLocalTime(row.occurred_at),
    fetchedAt: toLocalTime(row.fetched_at),
    source: row.source || '',
    sourceKind: row.source_kind || 'manual',
    official: row.source_kind === 'official',
    note: row.note || '',
    correctsId: row.corrects_event_id || 0
  }
}

/**
 * 归类记录状态的中文标签。键与后端 `STATUSES` 一一对应；
 * 未收录的值回落"未知"——未知保持未知，不猜。
 *
 * ⚠️ `manual_required` **不是错误态**，是"模型没给建议、请人工定稿"的正常状态。
 * 给它写"失败"会让界面看起来像坏了，而我们恰恰要把"模型不可用"做成可演示的亮点。
 */
const CLS_STATUS_LABEL = {
  pending: '待建议',
  suggested: '已给出候选',
  manual_required: '需人工填写',
  confirmed: '已确认结论'
}

const CLS_SOURCE_LABEL = {
  model: '模型候选',
  manual: '人工定稿'
}

/**
 * 归类记录的展示模型。
 *
 * 三条不合并的边界（合并任何一条，界面就会开始撒谎）：
 * 1. `status === 'confirmed'` 之前 `conclusion` **恒为空** —— 模型候选永远不进这个字段；
 * 2. `degraded` 与 `candidates` **各自独立** —— 不能因为"候选空"就说"模型挂了"，
 *    也不能因为"有降级原因"就以为"一条候选都没有"；
 * 3. `manual.available` 由**服务端**给（`manual_entry.available`），⛔ 前端不按状态自己猜。
 */
function classificationView(c) {
  const row = c || {}
  const status = row.status || 'pending'
  const rawCands = Array.isArray(row.candidates) ? row.candidates : []
  const candidates = rawCands.map(function (x) {
    const conf = x.confidence == null ? null : Number(x.confidence)
    return {
      id: x.id,
      hsCode: x.hs_code || '',
      label: x.label || '',
      rank: x.rank || 0,
      source: x.source || 'model',
      sourceLabel: CLS_SOURCE_LABEL[x.source] || '未知来源',
      isModel: x.source === 'model',
      // 置信度只在有值时显示：显示"0%"会让一条低分候选看起来像"模型放弃了"
      confidenceText: conf == null || isNaN(conf) ? '' : Math.round(conf * 100) + '%',
      rationale: x.rationale || '',
      createdAt: toLocalTime(x.created_at)
    }
  })
  const deg = row.degraded && row.degraded.kind
    ? { kind: row.degraded.kind, reason: row.degraded.reason || '本次未给出可用结果' }
    : null
  const manual = row.manual_entry || {}
  return {
    id: row.id,
    status: status,
    statusLabel: CLS_STATUS_LABEL[status] || '未知',
    productName: row.product_name || '',
    productDesc: row.product_desc || '',
    declarationId: row.declaration_id || 0,
    // 结论：只有人工定稿才有。⛔ 不是 `candidates[0].hs_code`
    conclusion: row.hs_code || '',
    hasConclusion: !!(row.hs_code && String(row.hs_code).length),
    candidates: candidates,
    hasCandidates: candidates.length > 0,
    degraded: deg,
    hasDegraded: !!deg,
    // 「候选区或降级区至少有一块可看」—— 这一条是页面级的可失败判据：
    // 两块都空＝用户只看到一个空列表，那正是要消灭的形态
    hasOutcome: candidates.length > 0 || !!deg,
    manual: {
      available: manual.available !== false,
      hint: manual.hint || ''
    },
    manualVisible: manual.available === true,
    model: {
      name: (row.model && row.model.name) || '',
      mocked: !!(row.model && row.model.mocked)
    },
    decidedAt: toLocalTime(row.decided_at),
    createdAt: toLocalTime(row.created_at),
    updatedAt: toLocalTime(row.updated_at)
  }
}

module.exports = {
  BASE,
  STATUS_LABEL,
  fetchSources,
  fetchDeclarations,
  fetchDeclaration,
  createClassification,
  fetchClassifications,
  fetchClassification,
  submitManualClassification,
  statusLabel,
  isTerminal,
  toLocalTime,
  sourcesView,
  declarationView,
  freshnessView,
  eventView,
  classificationView
}
