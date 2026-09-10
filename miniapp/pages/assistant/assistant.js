// pages/assistant/assistant.js
// F13 底部客服对话入口：调用 /api/v1/agent/assistant（F9/F10 后端：只读问答 + AgentCall 审计）
// 历史消息持久化到 Storage（key: chat_history_v1，最多 50 条）
const request = require('../../utils/request.js')
const auth = require('../../utils/auth.js')

const STORAGE_KEY = 'chat_history_v1'
const MAX_HISTORY = 50
// 上送后端用于多轮上下文的历史条数（assistant 仅取最近 6 条拼到 user 消息）
const CTX_TURNS = 6

Page({
  data: {
    messages: [],     // { role: 'user'|'assistant', text, ts, mocked?, latency? }
    input: '',
    sending: false,
    scrollTop: 0,
    hasMore: true,    // 是否还能往上滚（暂未做分页）
    emptyHint: true   // 是否显示空状态提示
  },

  onLoad() {
    this.loadHistory()
  },

  onShow() {
    // 切回页面时滚到最新
    this.scrollToBottom()
  },

  onPullDownRefresh() {
    // 暂未接分页，仅刷新历史（重新拉 Storage）+ 停止动画
    this.loadHistory()
    wx.stopPullDownRefresh()
  },

  loadHistory() {
    try {
      const raw = wx.getStorageSync(STORAGE_KEY)
      const list = Array.isArray(raw) ? raw : []
      this.setData({
        messages: list,
        emptyHint: list.length === 0
      }, () => this.scrollToBottom())
    } catch (e) {
      // Storage 异常不阻塞
    }
  },

  saveHistory() {
    try {
      const trimmed = this.data.messages.slice(-MAX_HISTORY)
      wx.setStorageSync(STORAGE_KEY, trimmed)
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

    // 组装多轮上下文（只取最近 CTX_TURNS 条 user/assistant 对，按时间升序）
    const historyForApi = this.data.messages
      .filter(m => !m.pending && (m.role === 'user' || m.role === 'assistant'))
      .slice(-(CTX_TURNS * 2))
      .map(m => ({ role: m.role, content: m.text }))

    try {
      const resp = await request.request({
        url: '/api/v1/agent/assistant',
        method: 'POST',
        data: { question: text, history: historyForApi }
      })
      const answer = (resp && resp.answer) ? String(resp.answer) : '抱歉，暂未获得回答。'
      // 替换末尾占位
      const list = this.data.messages.slice()
      const idx = list.findIndex(m => m.pending)
      if (idx >= 0) {
        list[idx] = {
          role: 'assistant',
          text: answer,
          ts: Date.now(),
          mocked: !!resp.mocked,
          latency: resp.latency_ms || 0
        }
      }
      this.setData({ messages: list, sending: false }, () => {
        this.scrollToBottom()
        this.saveHistory()
      })
    } catch (e) {
      // request.js 已弹过 toast；这里把占位改为错误态
      const list = this.data.messages.slice()
      const idx = list.findIndex(m => m.pending)
      if (idx >= 0) {
        list[idx] = { role: 'assistant', text: '（未获得回答，可重试或换个问法）', ts: Date.now(), error: true }
      }
      this.setData({ messages: list, sending: false }, () => {
        this.scrollToBottom()
        this.saveHistory()
      })
    }
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
          try { wx.removeStorageSync(STORAGE_KEY) } catch (e) {}
        }
      }
    })
  }
})
