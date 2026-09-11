// 智能入口工具（F19 全局入口 + F20 统一入口）
//
//   openSmartEntry()      → 打开「智能搜索」页（pages/assistant?mode=search）
//   runComplianceCheck()  → 直接对某个合规端点做预检并展示结论
//   showComplianceModal() → 展示合规初筛结论（发布页/备案页共用）
//
// 为什么「智能搜索」不是一个弹窗：
//   F20 的路由结果有四种形态（货源草稿 / 合规结论 / 客服回答 / 合同引导），
//   其中货源草稿与合规结论是**结构化卡片**（7 个字段 / 逐条依据与建议）。
//   wx.showModal 只能塞一段纯文本，长文案还会被截断成两行挤在一起——所以统一入口
//   直接复用「智能客服」的对话式页面外壳，由页面按意图渲染卡片，样式与客服完全一致。
//
// 设计取向：路由失败/未派发都不弹系统错误，一律给「下一步能做什么」的引导语。
const { request } = require('./request')

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

/** 弹出合规初筛结论（发布页与备案页共用；结果只读，用弹窗比跳页更轻） */
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
    .catch((e) => console.warn('[swallowed]', (e && e.message) || e))
}

/**
 * 打开统一入口。
 * @param {object} [opts] 保留 title/placeholder 以兼容旧调用方（页面内已有各自文案）
 */
function openSmartEntry(opts) {
  const options = opts || {}
  wx.navigateTo({
    url: '/pages/assistant/assistant?mode=search',
    fail: () => {
      // 极少见：页面栈已满（10 层）时 navigateTo 会失败，退回首页提示而不是静默无反应
      wx.showToast({ title: options.title || '请返回首页后重试', icon: 'none' })
    }
  })
}

module.exports = {
  openSmartEntry,
  runComplianceCheck,
  showComplianceModal
}
