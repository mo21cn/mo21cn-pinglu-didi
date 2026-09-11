// pages/assistant/assistant.js
// 两种模式（同一个页面，靠路由 mode 区分）：
//   · mode=parse —— F14 货源解析：一句话描述 → 结构化货源草稿（可用则带去发布页）
//   · 默认       —— F13 客服导购：调用 /api/v1/agent/assistant（只读问答 + AgentCall 审计）
// 历史消息持久化到 Storage（chat 与 parse 分库，最多 50 条）
//
// 注意：本页 onLoad 必须接收 options —— 早期版本写成无参，导致 /pages/assistant?mode=parse
// 的 query 被整体丢弃，「✨Ai」与「客服」进的是同一页、同一行为（货源解析前端入口从未接上）。
const request = require('../../utils/request.js')
const auth = require('../../utils/auth.js')
const { portLabel } = require('../../utils/ports.js')

const STORAGE_KEY = 'chat_history_v1'
const PARSE_STORAGE_KEY = 'parse_history_v1'
const MAX_HISTORY = 50
// 上送后端用于多轮上下文的历史条数（assistant 仅取最近 6 条拼到 user 消息）
const CTX_TURNS = 6
// 解析结果转交发布页的会话键（发布页 onLoad 读取后即清除，避免重复回填）
const DRAFT_KEY = 'cargo_draft_v1'

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

Page({
  data: {
    mode: 'chat',        // 'chat' 客服导购 | 'parse' 货源解析
    roleBlocked: false,  // 解析态下角色不是货主 → 引导切换
    messages: [],        // { role, text, ts, pending?, error?, kind?, ... }
    input: '',
    sending: false,
    scrollTop: 0,
    hasMore: true,
    emptyHint: true,
    parseChips: [
      '我有800吨散装水泥，下周三从南宁运到贵港，运费2万5',
      '500吨钢材从柳州发到梧州，一周内装货',
      '甲醇500吨，钦州到贵港，运费面议'
    ]
  },

  onLoad(options) {
    const opts = options || {}
    const isParse = opts.mode === 'parse'
    const user = auth.getUser() || {}
    // 解析态只对货主开放。以 current_role 为准；老 Storage 残留可能没有该字段，
    // 此时退回 roles 列表判断——不能让「字段缺失」把真货主挡在门外。
    const roles = user.roles || []
    const isShipper = user.current_role
      ? user.current_role === 'shipper'
      : roles.indexOf('shipper') >= 0
    this.setData({
      mode: isParse ? 'parse' : 'chat',
      roleBlocked: isParse && !isShipper
    })
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
    return this.data.mode === 'parse' ? PARSE_STORAGE_KEY : STORAGE_KEY
  },

  loadHistory() {
    try {
      const raw = wx.getStorageSync(this.storageKey())
      const list = Array.isArray(raw) ? raw : []
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
    wx.showLoading({ title: '切换身份…', mask: true })
    auth.enterRole('shipper')
      .then(() => {
        wx.hideLoading()
        this.setData({ roleBlocked: false })
        wx.showToast({ title: '已切换为货主', icon: 'success' })
      })
      .catch((err) => {
        wx.hideLoading()
        wx.showModal({
          title: '切换失败',
          content: ((err && err.stage) ? '失败环节：' + err.stage + '\n' : '')
            + ((err && err.message) || '请稍后重试'),
          showCancel: false
        })
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

    const userMsg = { role: 'user', text, ts: Date.now() }
    const pendingMsg = { role: 'assistant', text: '正在思考…', ts: Date.now(), pending: true }
    this.setData({
      messages: [...this.data.messages, userMsg, pendingMsg],
      input: '',
      sending: true,
      emptyHint: false
    }, () => {
      this.scrollToBottom()
      this.saveHistory()
    })

    // 组装多轮上下文（只取最近 CTX_TURNS 对，按时间升序）
    const historyForApi = this.data.messages
      .filter((m) => !m.pending && (m.role === 'user' || m.role === 'assistant'))
      .slice(-(CTX_TURNS * 2))
      .map((m) => ({ role: m.role, content: m.text }))

    const isParse = this.data.mode === 'parse'
    try {
      if (isParse) {
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
      this.replacePending({
        role: 'assistant',
        text: isParse ? '（未能解析，请换个说法或手动填写）' : '（未获得回答，可重试或换个问法）',
        ts: Date.now(),
        error: true
      }, () => this.saveHistory())
    }
  },

  /** 用最终消息替换末尾「正在思考」占位 */
  replacePending(msg, after) {
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
        from: 'parse',
        ts: Date.now()
      })
    } catch (err) {
      wx.showToast({ title: '草稿保存失败', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/publish/cargo/cargo?from=parse' })
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
