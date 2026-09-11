// pages/assistant/assistant.js
// 三种模式（同一个页面，靠路由 mode 区分；UI 完全复用「智能客服」的对话式外壳）：
//   · 默认        —— F13 客服导购：调用 /api/v1/agent/assistant（只读问答 + AgentCall 审计）
//   · mode=parse  —— F14 货源解析：一句话描述 → 结构化货源草稿（可用则带去发布页）
//   · mode=search —— F20 统一入口「智能搜索」：/api/v1/agent/route 判意图后派发，
//                    结果按意图渲染（货源卡 / 合规卡 / 回答气泡），不再用可编辑弹窗
// 历史消息持久化到 Storage（三模式分库，最多 50 条）
//
// 注意：本页 onLoad 必须接收 options —— 早期版本写成无参，导致 /pages/assistant?mode=parse
// 的 query 被整体丢弃，「✨Ai」与「客服」进的是同一页、同一行为（货源解析前端入口从未接上）。
const request = require('../../utils/request.js')
const auth = require('../../utils/auth.js')
const { portLabel } = require('../../utils/ports.js')

const STORAGE_KEY = 'chat_history_v1'
const PARSE_STORAGE_KEY = 'parse_history_v1'
const SEARCH_STORAGE_KEY = 'search_history_v1'
const MAX_HISTORY = 50
// 上送后端的多轮上下文条数上限：必须 ≤ 后端 AssistantRequest.history 的 max_length=10。
// 早期写成 CTX_TURNS*2=12 条超限 → 第 6 轮发送起携带 11+ 条历史被 FastAPI 422 拒收，
// 前端只显示「未获得回答」无法定位（第三方审计 P1-1）。
const MAX_CTX_HISTORY = 10
// 解析结果转交发布页的会话键（发布页 onLoad 读取后即清除，避免重复回填）
const DRAFT_KEY = 'cargo_draft_v1'

// 消息自增 id（wx:key 用）：ts 在同毫秒会插入 user+pending 两条导致键重复（第三方审计 P3-6）
let msgSeq = 0
function nextMsgId() { return ++msgSeq }

const FIELD_LABELS = {
  cargo_name: '货物名称',
  cargo_type: '货物类别',
  weight_t: '重量',
  origin_port: '起运港',
  dest_port: '目的港',
  expect_date: '期望装货日期',
  offer_price: '运费出价'
}

const CARGO_TYPE_LABELS = {
  bulk: '散货',
  general: '件杂货',
  container: '集装箱',
  tanker: '液货',
  other: '其他'
}

/** 统一入口的意图 → 中文标签（F20 路由结果要让人看得见） */
const INTENT_LABELS = {
  cargo_parse: '货源解析',
  compliance: '合规预检',
  contract: '智能合同',
  assistant: '智能客服'
}
const LEVEL_LABELS = { pass: '通过', warn: '有提示', block: '未通过' }
const SEVERITY_LABELS = { block: '阻断', warn: '提示' }
const COMP_TITLES = {
  pass: '合规预检通过',
  warn: '合规预检有提示',
  block: '合规预检未通过'
}

/**
 * 三种模式的全部文案与示例。集中一处，避免 WXML 里层层嵌套三元表达式
 * （此前只有两模式，加第三种时最易漏分支）。
 */
const MODE_META = {
  chat: {
    navTitle: '智能客服',
    bannerTitle: '平台智能客服',
    bannerSub: '咨询发货/找船/支付/航线/船型等问题，纯只读，AI 不代替您执行任何写操作',
    inputPlaceholder: '描述您的问题，按回车发送',
    sendLabel: '发送',
    busyLabel: '发送中',
    botBadge: '客',
    emptyIcon: '💬',
    emptyTitle: '有什么可以帮您？',
    emptySub: '试试这样问：',
    chipWide: false,
    chips: ['怎么发布货源？', '钦州港能走什么货？', '液货用什么船拉？', '撤单了运费退吗？', '智能撮合怎么评分？']
  },
  parse: {
    navTitle: '智能货源解析',
    bannerTitle: '智能填写货源',
    bannerSub: '用一句话描述货物与航线，AI 解析成货源草稿；核对后带去发布页，不会直接替您发布',
    inputPlaceholder: '例：800吨散装水泥，下周三从南宁运到贵港',
    sendLabel: '解析',
    busyLabel: '解析中',
    botBadge: '解',
    emptyIcon: '✨',
    emptyTitle: '描述一下这票货',
    emptySub: '试试这样说：',
    chipWide: true,
    chips: [
      '我有800吨散装水泥，下周三从南宁运到贵港，运费2万5',
      '500吨钢材从柳州发到梧州，一周内装货',
      '甲醇500吨，钦州到贵港，运费面议'
    ]
  },
  search: {
    navTitle: '智能搜索',
    bannerTitle: '平台智能搜索',
    bannerSub: '一句话搜货源/船源，也能直接问用法——系统自动判断意图并路由到对应能力（找货 · 合规 · 合同 · 客服），全程只读',
    inputPlaceholder: '搜货/搜船/问用法——用一句话描述',
    sendLabel: '搜索',
    busyLabel: '识别中',
    botBadge: '搜',
    emptyIcon: '🔍',
    emptyTitle: '想搜什么？',
    emptySub: '一句话就行，例如：',
    chipWide: true,
    chips: [
      '我要发800吨散装水泥，南宁到贵港',
      '甲醇能不能运？需要什么资质',
      '滞期费一般怎么约定',
      '怎么发布货源？',
      '钦州港能走什么货？'
    ]
  }
}

/** 把解析草稿转成可直接渲染的行（未识别/待确认的标红） */
function buildRows(draft, needsReview) {
  const nr = Array.isArray(needsReview) ? needsReview : []
  return Object.keys(FIELD_LABELS).map((key) => {
    let value = draft ? draft[key] : null
    if (key === 'cargo_type' && value) value = CARGO_TYPE_LABELS[value] || value
    if ((key === 'origin_port' || key === 'dest_port') && value) value = portLabel(value)
    if (key === 'weight_t' && value) value = value + ' 吨'
    if (key === 'offer_price' && value !== null && value !== undefined) value = value + ' 元'
    const missing = nr.indexOf(key) >= 0 || value === null || value === undefined || value === ''
    return { key, label: FIELD_LABELS[key], value: missing ? '未识别' : String(value), missing }
  })
}

/** needs_review 字段名 → 中文（供提示文案） */
function reviewLabels(needsReview) {
  return (Array.isArray(needsReview) ? needsReview : [])
    .map((k) => FIELD_LABELS[k] || k)
    .join('、')
}

/**
 * 统一入口响应 → 可直接渲染的消息对象。
 *
 * 三条呈现原则：
 *   1. 能结构化的就结构化（货源草稿 → 解析卡；合规结论 → 合规卡），不塞进一段文字；
 *   2. 未派发不算错误：给「下一步能做什么」的引导语 + 可点的动作（如切货主后重试）；
 *   3. 每条都标出识别到的意图与置信度——路由是产品能力，要让人看得见。
 */
function routeMessage(res, text) {
  const resp = res || {}
  const intent = resp.intent || 'assistant'
  const base = {
    role: 'assistant',
    ts: Date.now(),
    intent,
    intentLabel: INTENT_LABELS[intent] || intent,
    confidence: Math.round((resp.confidence || 0) * 100)
  }

  if (intent === 'cargo_parse') {
    if (!resp.dispatched) {
      // 典型：当前身份不是货主 → 给引导语 + 一键切角色重试（复用 auth 唯一入口）
      return Object.assign(base, {
        text: resp.message || '货源解析仅货主角色可用。',
        needRole: true,
        retryText: text
      })
    }
    const result = resp.result || {}
    const review = result.needs_review || []
    const draft = result.draft || null
    if (!draft) {
      return Object.assign(base, { text: '没解析出货物内容，换个说法试试（例：800吨散装水泥，南宁到贵港）。' })
    }
    return Object.assign(base, {
      kind: 'parse',
      draft,
      rows: buildRows(draft, review),
      missingText: reviewLabels(review),
      hasMissing: review.length > 0
    })
  }

  if (intent === 'compliance') {
    const r = resp.result || {}
    const level = r.level || 'pass'
    return Object.assign(base, {
      kind: 'compliance',
      level,
      levelLabel: LEVEL_LABELS[level] || level,
      compTitle: COMP_TITLES[level] || COMP_TITLES.pass,
      summary: r.summary || resp.message || '',
      findings: (r.findings || []).map((f) => Object.assign({}, f, {
        severityLabel: SEVERITY_LABELS[f.severity] || f.severity
      }))
    })
  }

  if (intent === 'contract') {
    return Object.assign(base, {
      text: resp.message || '合同风控需要具体订单：请到「订单」页打开该订单，再点「查看合同」。'
    })
  }

  // assistant（含后端兜底）：直接展示回答
  const answer = (resp.result && resp.result.answer) || resp.message || '未获得回答'
  return Object.assign(base, {
    text: answer,
    mocked: !!(resp.result && resp.result.mocked),
    latency: (resp.result && resp.result.latency_ms) || 0
  })
}

Page({
  data: {
    mode: 'chat',        // 'chat' 客服导购 | 'parse' 货源解析 | 'search' 智能搜索
    roleBlocked: false,  // 解析态下角色不是货主 → 引导切换
    messages: [],        // { role, text, ts, pending?, error?, kind?, intent?, ... }
    input: '',
    sending: false,
    scrollTop: 0,
    // 以下文案由 MODE_META 在 onLoad 注入（WXML 不再写三目嵌套）
    bannerTitle: MODE_META.chat.bannerTitle,
    bannerSub: MODE_META.chat.bannerSub,
    inputPlaceholder: MODE_META.chat.inputPlaceholder,
    sendLabel: MODE_META.chat.sendLabel,
    busyLabel: MODE_META.chat.busyLabel,
    botBadge: MODE_META.chat.botBadge,
    emptyIcon: MODE_META.chat.emptyIcon,
    emptyTitle: MODE_META.chat.emptyTitle,
    emptySub: MODE_META.chat.emptySub,
    chipWide: MODE_META.chat.chipWide,
    chips: MODE_META.chat.chips,
    emptyHint: true
  },

  onLoad(options) {
    const opts = options || {}
    const mode = opts.mode === 'parse' ? 'parse' : (opts.mode === 'search' ? 'search' : 'chat')
    const meta = MODE_META[mode]
    const user = auth.getUser() || {}
    // 解析态只对货主开放。以 current_role 为准；老 Storage 残留可能没有该字段，
    // 此时退回 roles 列表判断——不能让「字段缺失」把真货主挡在门外。
    // 搜索态不做门控：路由会自己判角色并给引导语，比「一律拦下」更有用。
    const roles = user.roles || []
    const isShipper = user.current_role
      ? user.current_role === 'shipper'
      : roles.indexOf('shipper') >= 0
    this.setData({
      mode,
      roleBlocked: mode === 'parse' && !isShipper,
      bannerTitle: meta.bannerTitle,
      bannerSub: meta.bannerSub,
      inputPlaceholder: meta.inputPlaceholder,
      sendLabel: meta.sendLabel,
      busyLabel: meta.busyLabel,
      botBadge: meta.botBadge,
      emptyIcon: meta.emptyIcon,
      emptyTitle: meta.emptyTitle,
      emptySub: meta.emptySub,
      chips: meta.chips
    })
    // 三种模式共用一个页面，标题随模式切换（json 里的默认值是「智能客服」）
    wx.setNavigationBarTitle({ title: meta.navTitle })
    this.loadHistory()
  },

  onShow() {
    this.scrollToBottom()
  },

  onPullDownRefresh() {
    this.loadHistory()
    wx.stopPullDownRefresh()
  },

  storageKey() {
    if (this.data.mode === 'parse') return PARSE_STORAGE_KEY
    if (this.data.mode === 'search') return SEARCH_STORAGE_KEY
    return STORAGE_KEY
  },

  loadHistory() {
    try {
      const raw = wx.getStorageSync(this.storageKey())
      // 旧版本存储的消息没有 id（用 ts 当 key），恢复时统一补上自增 id
      const list = (Array.isArray(raw) ? raw : []).map((m) => (m && !m.id ? { ...m, id: nextMsgId() } : m))
      this.setData({ messages: list, emptyHint: list.length === 0 }, () => this.scrollToBottom())
    } catch (e) {
      // Storage 异常不阻塞
    }
  },

  saveHistory() {
    try {
      wx.setStorageSync(this.storageKey(), this.data.messages.slice(-MAX_HISTORY))
    } catch (e) {
      // 容量超限不阻塞
    }
  },

  onInput(e) {
    this.setData({ input: e.detail.value })
  },

  onQuickAsk(e) {
    const q = (e.currentTarget.dataset.q || '').trim()
    if (!q) return
    this.setData({ input: q })
  },

  /** 解析态下非货主：直接切到货主身份（复用 auth 的唯一入口） */
  onSwitchToShipper() {
    this.switchToShipper().then(() => {
      this.setData({ roleBlocked: false })
      wx.showToast({ title: '已切换为货主', icon: 'success' })
    })
  },

  /** 搜索态下遇到「需要货主身份」的引导：切角色后自动用原句重试 */
  onSwitchForRetry(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    const msg = this.data.messages[idx]
    const text = (msg && msg.retryText) || ''
    if (!text) return
    this.switchToShipper().then(() => {
      this.setData({ input: text })
      this.onSend()
    })
  },

  /** 切换为货主（带环节标记的失败提示），供门控与重试两处共用 */
  switchToShipper() {
    wx.showLoading({ title: '切换身份…', mask: true })
    return auth.enterRole('shipper')
      .then(() => {
        wx.hideLoading()
      })
      .catch((err) => {
        wx.hideLoading()
        wx.showModal({
          title: '切换失败',
          content: ((err && err.stage) ? '失败环节：' + err.stage + '\n' : '')
            + ((err && err.message) || '请稍后重试'),
          showCancel: false
        })
        throw err
      })
  },

  scrollToBottom() {
    this.setData({ scrollTop: this.data.scrollTop + 9999 })
  },

  async onSend() {
    const text = (this.data.input || '').trim()
    if (!text || this.data.sending) return

    if (!auth.isLoggedIn()) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }
    if (this.data.mode === 'parse' && this.data.roleBlocked) {
      wx.showToast({ title: '请先切换为货主身份', icon: 'none' })
      return
    }

    const userMsg = { id: nextMsgId(), role: 'user', text, ts: Date.now() }
    const pendingMsg = { id: nextMsgId(), role: 'assistant', text: '正在思考…', ts: Date.now(), pending: true }
    this.setData({
      messages: [...this.data.messages, userMsg, pendingMsg],
      input: '',
      sending: true,
      emptyHint: false
    }, () => {
      this.scrollToBottom()
      this.saveHistory()
    })

    // 组装多轮上下文（按时间升序；条数对齐后端 max_length=10，多轮不再 422）
    const historyForApi = this.data.messages
      .filter((m) => !m.pending && (m.role === 'user' || m.role === 'assistant'))
      .slice(-MAX_CTX_HISTORY)
      .map((m) => ({ role: m.role, content: m.text }))

    const mode = this.data.mode
    try {
      if (mode === 'parse') {
        const resp = await request.request({
          url: '/api/v1/agent/cargo-parse',
          method: 'POST',
          data: { text }
        })
        const review = (resp && resp.needs_review) || []
        const done = {
          role: 'assistant',
          kind: 'parse',
          ts: Date.now(),
          draft: (resp && resp.draft) || null,
          rows: buildRows((resp && resp.draft) || null, review),
          missingText: reviewLabels(review),
          hasMissing: review.length > 0,
          confidence: Math.round(((resp && resp.confidence) || 0) * 100),
          mocked: !!(resp && resp.mocked)
        }
        this.replacePending(done, () => this.saveHistory())
      } else if (mode === 'search') {
        // 统一入口：后端判意图 → 按意图回不同结构的 result，这里统一转成可渲染消息
        const resp = await request.request({
          url: '/api/v1/agent/route',
          method: 'POST',
          data: { text }
        })
        this.replacePending(routeMessage(resp, text), () => this.saveHistory())
      } else {
        const resp = await request.request({
          url: '/api/v1/agent/assistant',
          method: 'POST',
          data: { question: text, history: historyForApi }
        })
        const answer = (resp && resp.answer) ? String(resp.answer) : '抱歉，暂未获得回答。'
        this.replacePending({
          role: 'assistant',
          text: answer,
          ts: Date.now(),
          mocked: !!(resp && resp.mocked),
          latency: (resp && resp.latency_ms) || 0
        }, () => this.saveHistory())
      }
    } catch (e) {
      // request.js 已弹过 toast；这里把占位改为错误态
      const fallback = mode === 'parse'
        ? '（未能解析，请换个说法或手动填写）'
        : (mode === 'search' ? '（未能识别，换个说法或直接进「客服」提问）' : '（未获得回答，可重试或换个问法）')
      this.replacePending({
        role: 'assistant',
        text: fallback,
        ts: Date.now(),
        error: true
      }, () => this.saveHistory())
    }
  },

  /** 用最终消息替换末尾「正在思考」占位（统一在此补 id，兼容各分支产物与历史遗留数据） */
  replacePending(msg, after) {
    if (!msg.id) msg.id = nextMsgId()
    const list = this.data.messages.slice()
    const idx = list.findIndex((m) => m.pending)
    if (idx >= 0) list[idx] = msg
    this.setData({ messages: list, sending: false }, () => {
      this.scrollToBottom()
      if (after) after()
    })
  },

  /** 解析结果 → 带去发布货源页（草稿经 Storage 传递，发布页 onLoad 回填） */
  onUseDraft(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    const msg = this.data.messages[idx]
    if (!msg || !msg.draft) return
    try {
      wx.setStorageSync(DRAFT_KEY, {
        draft: msg.draft,
        needs_review: (msg.rows || []).filter((r) => r.missing).map((r) => r.key),
        from: this.data.mode,
        ts: Date.now()
      })
    } catch (err) {
      wx.showToast({ title: '草稿保存失败', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/publish/cargo/cargo?from=' + this.data.mode })
  },

  onClear() {
    if (this.data.messages.length === 0) return
    wx.showModal({
      title: '清空对话',
      content: '将清空当前会话历史（仅清除本机缓存），确认？',
      confirmText: '清空',
      confirmColor: '#c0392b',
      success: (res) => {
        if (res.confirm) {
          this.setData({ messages: [], emptyHint: true })
          try { wx.removeStorageSync(this.storageKey()) } catch (e) {}
        }
      }
    })
  }
})
