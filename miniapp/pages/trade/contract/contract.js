// 合同预览（L3 三级页）· 智能合同 Agent（F11）成果展示
//
// 方案 A（零后端改动）：复用 POST /api/v1/agent/contract/generate，**进页即生成**，草稿不落库。
// 入口：订单页卡片「查看合同」→ navigateTo 本页；订单页内弹层保留为「快速预览」（长按订单卡）。
//
// 架构亮点（页面上要讲清的三件事）：
//   1. 核心条款零 LLM——甲乙方/货物/航线/运费/船舶由订单数据模板渲染（contract.render_contract）
//   2. 风险点零 LLM——规则引擎 R1-R5 扫描订单事实（contract.check_risks）
//   3. LLM 只在补充条款写标准文字，prompt 禁止输出任何数字与日期
const { request } = require('../../../utils/request')

const SEV_ORDER = { high: 0, medium: 1, low: 2 }
const SEV_LABELS = { high: '高', medium: '中', low: '低' }

/** 转义 HTML 特殊字符 */
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** 行内语法：**加粗** / *斜体*（免责声明行用） */
function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong style="font-weight:500;color:#0F2540">$1</strong>')
    .replace(/\*(.+?)\*/g, '<span style="color:#98A6B8">$1</span>')
}

/**
 * 合同 Markdown → rich-text 节点串（仅覆盖内核实际产出的语法）
 * 支持：# / ## / > 引用 / - 列表 / **加粗**
 */
function mdToHtml(text) {
  if (!text) return ''
  // 预处理：① 合并连续引用行（合同头"不具法律效力"声明是两行 "> ..."）
  //         ② 合并软换行——合同正文长段落被硬换行，逐行成段会把一句话拆成两段
  const isPlain = (s) =>
    !!s.trim() &&
    s.indexOf('#') !== 0 &&
    s.indexOf('> ') !== 0 &&
    s.indexOf('- ') !== 0 &&
    !/^-{3,}$/.test(s.trim())
  const lines = []
  for (const ln of String(text).split('\n')) {
    const prev = lines[lines.length - 1]
    if (ln.indexOf('> ') === 0 && prev !== undefined && prev.indexOf('> ') === 0) {
      lines[lines.length - 1] = prev + ln.slice(2)
    } else if (isPlain(ln) && prev !== undefined && isPlain(prev)) {
      lines[lines.length - 1] = prev + ln
    } else {
      lines.push(ln)
    }
  }
  const out = []
  let inList = false

  const closeList = () => {
    if (inList) {
      out.push('</ul>')
      inList = false
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/\s+$/, '')
    if (!line.trim()) {
      closeList()
      continue
    }
    if (/^-{3,}$/.test(line.trim())) {
      closeList()
      out.push('<div style="height:1px;background:#EEF1F6;margin:18px 0"></div>')
      continue
    }
    if (line.indexOf('### ') === 0) {
      closeList()
      out.push(
        '<div style="font-size:13px;font-weight:500;color:#3B5BDB;margin:14px 0 6px">' +
          inline(line.slice(4)) +
          '</div>'
      )
    } else if (line.indexOf('## ') === 0) {
      closeList()
      out.push(
        '<div style="font-size:14px;font-weight:500;color:#3B5BDB;margin:20px 0 8px;padding-left:10px;border-left:3px solid #5C7CFA">' +
          inline(line.slice(3)) +
          '</div>'
      )
    } else if (line.indexOf('# ') === 0) {
      closeList()
      out.push(
        '<div style="font-size:17px;font-weight:500;color:#0F2540;margin:0 0 10px">' +
          inline(line.slice(2)) +
          '</div>'
      )
    } else if (line.indexOf('> ') === 0) {
      closeList()
      out.push(
        '<div style="font-size:12px;color:#8A94A6;background:#F7F9FC;border-radius:6px;padding:8px 10px;margin:0 0 12px;line-height:1.6">' +
          inline(line.slice(2)) +
          '</div>'
      )
    } else if (line.indexOf('- ') === 0) {
      if (!inList) {
        out.push('<ul style="margin:0 0 10px;padding-left:18px">')
        inList = true
      }
      out.push(
        '<li style="font-size:13px;color:#3A4256;line-height:1.8">' +
          inline(line.slice(2)) +
          '</li>'
      )
    } else {
      closeList()
      out.push(
        '<div style="font-size:13px;color:#3A4256;line-height:1.8;margin:0 0 8px">' +
          inline(line) +
          '</div>'
      )
    }
  }
  closeList()
  return out.join('')
}

Page({
  data: {
    orderId: 0,
    loading: true,
    error: '',
    mocked: false,
    degraded: false,
    degradedReason: '',
    latencyMs: 0,
    risks: [],
    riskCount: 0,
    highCount: 0,
    contractHtml: '',
    rawText: ''
  },

  onLoad(query) {
    const orderId = Number((query && query.order_id) || 0)
    if (!orderId) {
      this.setData({ loading: false, error: '缺少订单参数，请从订单页进入' })
      return
    }
    this.setData({ orderId })
    wx.setNavigationBarTitle({ title: '合同预览 · 订单 #' + orderId })
    this.generate()
  },

  onPullDownRefresh() {
    this.generate()
    wx.stopPullDownRefresh()
  },

  /** 方案 A：进页即生成（无 GET 快照端点，故每次进入都会重新生成） */
  generate() {
    if (!this.data.orderId) return
    this.setData({ loading: true, error: '' })
    request({
      url: '/api/v1/agent/contract/generate',
      method: 'POST',
      data: { order_id: this.data.orderId },
      // 错误交给页面兜底态展示，不再叠加 toast
      silent: true
    })
      .then((res) => {
        const risks = (res.risks || [])
          .slice()
          .sort((a, b) => {
            const ia = SEV_ORDER[a.severity]
            const ib = SEV_ORDER[b.severity]
            return (ia === undefined ? 9 : ia) - (ib === undefined ? 9 : ib)
          })
          .map((r) => ({
            severity: r.severity,
            sev_label: SEV_LABELS[r.severity] || r.severity,
            title: r.title,
            detail: r.detail,
            suggestion: r.suggestion
          }))
        const rawText = res.contract_text || ''
        this.setData({
          loading: false,
          mocked: !!res.mocked,
          degraded: !!res.degraded,
          degradedReason: res.degraded_reason || '',
          latencyMs: res.latency_ms || 0,
          risks,
          riskCount: risks.length,
          highCount: risks.filter((r) => r.severity === 'high').length,
          rawText,
          contractHtml: mdToHtml(rawText)
        })
      })
      .catch((err) => {
        this.setData({
          loading: false,
          error: String((err && err.message) || '生成失败，请稍后重试')
        })
      })
  },

  onCopy() {
    if (!this.data.rawText) return
    wx.setClipboardData({
      data: this.data.rawText,
      success: () => wx.showToast({ title: '合同全文已复制', icon: 'success' })
    })
  },

  /** 占位能力（签署 / PDF 导出未开发，见 README 迭代需求） */
  onPlaceholder(e) {
    const name = (e.currentTarget.dataset.name || '该功能') + '即将开放'
    wx.showToast({ title: name, icon: 'none' })
  }
})
