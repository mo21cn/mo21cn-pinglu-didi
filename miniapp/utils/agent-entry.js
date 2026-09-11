// 智能入口工具（F19 全局入口 + F20 统一入口）
//
// 一句话 → /api/v1/agent/route 意图路由 → 按意图派发：
//   cargo_parse → 草稿落 Storage，带用户去发布货源页回填
//   compliance  → 弹出合规初筛结论（阻断/提示逐条列出）
//   assistant   → 直接展示客服回答，可一键转完整对话页
//   contract    → 需要订单上下文，提示去订单页（本入口不传 order_id）
//
// 设计取向：路由失败/未派发都不弹系统错误，一律给「下一步能做什么」的引导语。
const { request } = require('./request')
const auth = require('./auth')

// 解析草稿转交发布页的会话键（与 pages/assistant 共用同一约定）
const DRAFT_KEY = 'cargo_draft_v1'

const SEV_LABEL = { block: '阻断', warn: '提示' }

/** 把合规初筛结果拼成可读文本 */
function complianceText(result) {
  const lines = [(result && result.summary) || '']
  const findings = (result && result.findings) || []
  findings.forEach((f) => {
    lines.push(`【${SEV_LABEL[f.severity] || f.severity}】${f.title}\n${f.detail}\n建议：${f.suggestion}`)
  })
  return lines.filter(Boolean).join('\n\n')
}

/** 弹出合规初筛结论（发布页与统一入口共用） */
function showComplianceModal(result) {
  const level = (result && result.level) || 'pass'
  const title = level === 'block' ? '合规预检未通过'
    : (level === 'warn' ? '合规预检有提示' : '合规预检通过')
  wx.showModal({
    title,
    content: complianceText(result),
    showCancel: false,
    confirmText: '知道了'
  })
}

/** 直接对某个合规端点做预检并展示结论 */
function runComplianceCheck(url, body) {
  request({ url, method: 'POST', data: body })
    .then((res) => showComplianceModal(res))
    .catch(() => {})
}

/** 打开统一入口（可编辑弹窗输入 → 路由 → 派发） */
function openSmartEntry(opts) {
  const options = opts || {}
  wx.showModal({
    title: options.title || '智能入口',
    editable: true,
    placeholderText: options.placeholder
      || '例：800吨散货从南宁到贵港 / 烟花爆竹能不能运 / 怎么发布货源',
    confirmText: '发送',
    success: (res) => {
      if (!res.confirm) return
      const text = String(res.content || '').trim()
      if (text.length < 2) {
        wx.showToast({ title: '请多输入几个字', icon: 'none' })
        return
      }
      dispatch(text)
    }
  })
}

function dispatch(text) {
  wx.showLoading({ title: '正在识别…', mask: true })
  request({ url: '/api/v1/agent/route', method: 'POST', data: { text } })
    .then((res) => {
      wx.hideLoading()
      handle(res, text)
    })
    .catch(() => wx.hideLoading())
}

function handle(res, text) {
  const intent = (res && res.intent) || 'assistant'

  if (intent === 'cargo_parse') {
    if (!res.dispatched) {
      // 典型：当前身份不是货主 → 引导切换（复用 auth 唯一入口，避免带旧角色 token）
      wx.showModal({
        title: '需要货主身份',
        content: res.message || '货源解析仅货主角色可用。',
        confirmText: '切换为货主',
        success: (r) => {
          if (!r.confirm) return
          wx.showLoading({ title: '切换身份…', mask: true })
          auth.enterRole('shipper')
            .then(() => { wx.hideLoading(); dispatch(text) })
            .catch(() => wx.hideLoading())
        }
      })
      return
    }
    const draft = (res.result && res.result.draft) || null
    if (!draft) {
      wx.showToast({ title: '未解析出内容，请换个说法', icon: 'none' })
      return
    }
    try {
      wx.setStorageSync(DRAFT_KEY, {
        draft,
        needs_review: (res.result && res.result.needs_review) || [],
        from: 'route',
        ts: Date.now()
      })
    } catch (e) {
      wx.showToast({ title: '草稿保存失败', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/publish/cargo/cargo?from=route' })
    return
  }

  if (intent === 'compliance') {
    if (res.result) showComplianceModal(res.result)
    else wx.showModal({ title: '合规初筛', content: res.message || '未获得结论', showCancel: false })
    return
  }

  if (intent === 'assistant') {
    const answer = (res.result && res.result.answer) || res.message || '未获得回答'
    wx.showModal({
      title: '智能客服',
      content: answer,
      confirmText: '继续追问',
      cancelText: '知道了',
      success: (r) => {
        if (r.confirm) wx.navigateTo({ url: '/pages/assistant/assistant' })
      }
    })
    return
  }

  if (intent === 'contract') {
    if (res.dispatched && res.result && res.result.order_id) {
      // 带订单上下文时直接去看合同（本入口不传 order_id，保留前向兼容）
      wx.navigateTo({ url: '/pages/trade/contract/contract?order_id=' + res.result.order_id })
      return
    }
    wx.showModal({
      title: '智能合同',
      content: res.message || '请先在订单页打开订单，再生成合同。',
      showCancel: false
    })
    return
  }

  // 兜底：意图未识别（后端目前不会走到这里，前端也不静默失败）
  wx.showModal({
    title: '智能入口',
    content: res.message || '暂未识别您的意图，请换个说法试试。',
    showCancel: false
  })
}

module.exports = {
  openSmartEntry,
  runComplianceCheck,
  showComplianceModal,
  DRAFT_KEY
}
