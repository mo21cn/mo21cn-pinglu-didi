// 04 发布货物（货主端二级页）
// 对接 POST /api/v1/cargo/shipments（publish_now=true 确认发布 / false 保存草稿）
const { request } = require('../../../utils/request')
// 13 个港口的全站唯一来源（showActionSheet 的 itemList 上限 6，港口选择走 port-picker 组件）
const { PORTS, portLabel } = require('../../../utils/ports')
// 合规预检结论展示（F17 → 与统一入口共用同一套渲染）
const { runComplianceCheck } = require('../../../utils/agent-entry')

// 货源解析草稿的会话键（解析页 / 统一入口写入，本页读取后即清除）
const DRAFT_KEY = 'cargo_draft_v1'

const CARGO_TYPES = [
  { key: 'bulk',      label: '散货' },
  { key: 'general',   label: '件杂货' },
  { key: 'container', label: '集装箱' },
  { key: 'tanker',    label: '液货' },
  { key: 'other',     label: '其他' }
]

const CARGO_TYPE_LABEL = {}
CARGO_TYPES.forEach((t) => { CARGO_TYPE_LABEL[t.key] = t.label })

// 解析结果字段 → 表单高亮键（与 needs_review 的字段名对齐）
const REVIEW_LABELS = {
  cargo_name: '货物名称',
  cargo_type: '货物类别',
  weight_t: '重量',
  origin_port: '装货港',
  dest_port: '卸货港',
  expect_date: '装货日期',
  offer_price: '运费'
}

const PACKS = ['散装', '袋装', '托盘', '裸装', '罐装']

const SHIP_TYPES = [
  { key: '',          label: '按货物条件匹配' },
  { key: 'bulk',      label: '散货船' },
  { key: 'general',   label: '件杂货船' },
  { key: 'container', label: '集装箱船' },
  { key: 'tanker',    label: '液货船' }
]

Page({
  data: {
    form: {
      cargo_name: '',
      cargo_type: 'bulk',
      weight_t: '',
      origin_port: '',
      origin_label: '',
      dest_port: '',
      dest_label: '',
      expect_date: '',
      offer_price: '',
      remark: ''
    },
    cargoTypeLabel: '散货',
    packLabel: '',
    shipTypeLabel: '',
    priceMode: 'negotiable',
    remarkLen: 0,
    confirmed: false,
    submitting: false,

    // 智能填写 / 合规预检
    parsing: false,
    smartTip: '',
    smartMissing: [],   // needs_review 字段名（渲染提示文案用）
    missTip: {},        // 字段名 → 是否高亮（WXML 不支持 indexOf，须预计算）

    // 港口选择弹层（port-picker 组件）
    ppVisible: false,
    ppTitle: '选择港口',
    ppTip: '',
    ppCurrent: '',
    ppPorts: PORTS,
    ppAction: ''
  },

  onLoad() {
    const d = new Date(Date.now() + 24 * 3600 * 1000)
    this.setData({ 'form.expect_date': d.toISOString().slice(0, 10) })
    // 来自「✨Ai 解析页」或「统一入口」的草稿 → 回填表单
    this.applySmartDraft()
  },

  // ---- 智能填写：一句话 → 货源解析 Agent → 回填表单（Agent 无直写，仍需人工确认后发布） ----
  applySmartDraft() {
    let raw = null
    try { raw = wx.getStorageSync(DRAFT_KEY) } catch (e) { return }
    if (!raw || !raw.draft) return
    try { wx.removeStorageSync(DRAFT_KEY) } catch (e) { /* 清理失败不阻塞 */ }
    this.fillFromDraft(raw)
  },

  fillFromDraft(raw) {
    const d = (raw && raw.draft) || {}
    const nr = (raw && raw.needs_review) || []
    const patch = {}
    if (d.cargo_name) patch['form.cargo_name'] = d.cargo_name
    if (d.cargo_type) {
      patch['form.cargo_type'] = d.cargo_type
      patch.cargoTypeLabel = CARGO_TYPE_LABEL[d.cargo_type] || d.cargo_type
    }
    if (d.weight_t !== null && d.weight_t !== undefined) patch['form.weight_t'] = String(d.weight_t)
    if (d.origin_port) {
      patch['form.origin_port'] = d.origin_port
      patch['form.origin_label'] = portLabel(d.origin_port)
    }
    if (d.dest_port) {
      patch['form.dest_port'] = d.dest_port
      patch['form.dest_label'] = portLabel(d.dest_port)
    }
    if (d.expect_date) patch['form.expect_date'] = d.expect_date
    if (d.offer_price !== null && d.offer_price !== undefined) {
      patch['form.offer_price'] = String(d.offer_price)
      patch.priceMode = 'fixed'
    }
    // 高亮待确认字段（WXML 不能做 indexOf，故预计算成对象）
    const missTip = {}
    Object.keys(REVIEW_LABELS).forEach((k) => { missTip[k] = nr.indexOf(k) >= 0 })
    patch.missTip = missTip
    patch.smartMissing = nr
    patch.smartTip = nr.length
      ? 'AI 已回填，请确认：' + nr.map((k) => REVIEW_LABELS[k] || k).join('、')
      : 'AI 已按描述回填，请核对后发布（发布前仍由您确认）'
    this.setData(patch)
  },

  onSmartFill() {
    if (this.data.parsing) return
    wx.showModal({
      title: '一句话发货',
      editable: true,
      placeholderText: '例：我有800吨散装水泥，下周三从南宁运到贵港，运费2万5',
      confirmText: '智能解析',
      success: (res) => {
        if (!res.confirm) return
        const text = String(res.content || '').trim()
        if (text.length < 4) {
          wx.showToast({ title: '请至少输入 4 个字', icon: 'none' })
          return
        }
        this.parseText(text)
      }
    })
  },

  parseText(text) {
    this.setData({ parsing: true })
    wx.showLoading({ title: '正在解析…', mask: true })
    request({ url: '/api/v1/agent/cargo-parse', method: 'POST', data: { text } })
      .then((resp) => this.fillFromDraft(resp || {}))
      .catch(() => { /* request 已提示，保留原表单 */ })
      .finally(() => {
        wx.hideLoading()
        this.setData({ parsing: false })
      })
  },

  // ---- 合规预检（F17）：发布前即时预检，只提示不阻断 ----
  onComplianceCheck() {
    const f = this.data.form
    if (!f.origin_port || !f.dest_port || !f.weight_t || !f.expect_date) {
      wx.showToast({ title: '请先填完港口 / 重量 / 日期', icon: 'none' })
      return
    }
    const parts = []
    if (this.data.packLabel) parts.push('包装：' + this.data.packLabel)
    if (f.remark) parts.push(f.remark)
    runComplianceCheck('/api/v1/agent/compliance/cargo', {
      cargo_name: f.cargo_name || (this.data.cargoTypeLabel + '货'),
      cargo_type: f.cargo_type,
      weight_t: Number(f.weight_t),
      origin_port: f.origin_port,
      dest_port: f.dest_port,
      expect_date: f.expect_date,
      remark: parts.join(' · ').slice(0, 255)
    })
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field
    const patch = { [`form.${field}`]: e.detail.value }
    if (field === 'remark') patch.remarkLen = String(e.detail.value || '').length
    // 补全后撤掉解析回填留下的「待确认」高亮
    if (this.data.missTip && this.data.missTip[field]) patch[`missTip.${field}`] = false
    this.setData(patch)
  },

  // ---- 港口选择：13 项超过 wx.showActionSheet 的 itemList 上限（6），走 port-picker 组件 ----
  pickOrigin() {
    this.openPortPicker({ title: '选择装货港', current: this.data.form.origin_port, action: 'origin' })
  },

  pickDest() {
    this.openPortPicker({ title: '选择卸货港', current: this.data.form.dest_port, action: 'dest' })
  },

  openPortPicker(opts) {
    this.setData({
      ppTitle: opts.title || '选择港口',
      ppTip: opts.tip || '',
      ppCurrent: opts.current || '',
      ppPorts: opts.ports || PORTS,
      ppAction: opts.action || '',
      ppVisible: true
    })
  },

  onPortPicked(e) {
    const { key, label } = e.detail
    const patch = { ppVisible: false }
    if (this.data.ppAction === 'origin') {
      patch['form.origin_port'] = key
      patch['form.origin_label'] = label
      if (this.data.missTip && this.data.missTip.origin_port) patch['missTip.origin_port'] = false
    } else {
      patch['form.dest_port'] = key
      patch['form.dest_label'] = label
      if (this.data.missTip && this.data.missTip.dest_port) patch['missTip.dest_port'] = false
    }
    this.setData(patch)
  },

  onPortPickerClose() {
    this.setData({ ppVisible: false })
  },

  pickCargoType() {
    wx.showActionSheet({
      itemList: CARGO_TYPES.map((t) => t.label),
      success: (res) => {
        const t = CARGO_TYPES[res.tapIndex]
        const patch = { cargoTypeLabel: t.label, 'form.cargo_type': t.key }
        if (this.data.missTip && this.data.missTip.cargo_type) patch['missTip.cargo_type'] = false
        this.setData(patch)
      }
    })
  },

  pickPack() {
    wx.showActionSheet({
      itemList: PACKS,
      success: (res) => this.setData({ packLabel: PACKS[res.tapIndex] })
    })
  },

  pickShipType() {
    wx.showActionSheet({
      itemList: SHIP_TYPES.map((t) => t.label),
      success: (res) => {
        const t = SHIP_TYPES[res.tapIndex]
        this.setData({ shipTypeLabel: t.key ? t.label : '' })
      }
    })
  },

  pickDate() {
    wx.showActionSheet({
      itemList: ['今天', '明天', '后天', '一周内'],
      success: (res) => {
        const offsets = [0, 1, 2, 7]
        const d = new Date(Date.now() + offsets[res.tapIndex] * 86400000)
        const dateStr = d.toISOString().slice(0, 10)
        const patch = { 'form.expect_date': dateStr }
        if (this.data.missTip && this.data.missTip.expect_date) patch['missTip.expect_date'] = false
        this.setData(patch)
      }
    })
  },

  pickPriceMode(e) {
    const mode = e.currentTarget.dataset.mode
    const patch = { priceMode: mode }
    if (mode === 'negotiable') patch['form.offer_price'] = ''
    this.setData(patch)
  },

  toggleConfirm() {
    this.setData({ confirmed: !this.data.confirmed })
  },

  // ---- 组装请求体 ----
  buildPayload(publishNow) {
    const f = this.data.form
    const parts = []
    if (this.data.packLabel) parts.push('包装：' + this.data.packLabel)
    if (this.data.shipTypeLabel) parts.push('所需船型：' + this.data.shipTypeLabel)
    if (f.remark) parts.push(f.remark)
    return {
      cargo_name: f.cargo_name || (this.data.cargoTypeLabel + '货'),
      cargo_type: f.cargo_type,
      weight_t: Number(f.weight_t),
      origin_port: f.origin_port,
      dest_port: f.dest_port,
      expect_date: f.expect_date,
      offer_price: f.offer_price ? Number(f.offer_price) : null,
      remark: parts.join(' · ').slice(0, 255),
      publish_now: !!publishNow
    }
  },

  validate(publishNow) {
    const f = this.data.form
    if (!f.origin_port) { wx.showToast({ title: '请选择装货港', icon: 'none' }); return false }
    if (!f.dest_port) { wx.showToast({ title: '请选择卸货港', icon: 'none' }); return false }
    if (f.origin_port === f.dest_port) { wx.showToast({ title: '起讫港不能相同', icon: 'none' }); return false }
    if (!f.weight_t || Number(f.weight_t) <= 0) { wx.showToast({ title: '请填写有效重量', icon: 'none' }); return false }
    if (!f.expect_date) { wx.showToast({ title: '请选择期望装货日期', icon: 'none' }); return false }
    if (publishNow && !this.data.confirmed) {
      wx.showToast({ title: '请先勾选信息真实性确认', icon: 'none' })
      return false
    }
    return true
  },

  saveDraft() {
    if (this.data.submitting) return
    if (!this.validate(false)) return
    this.submit(false)
  },

  publish() {
    if (this.data.submitting) return
    if (!this.validate(true)) return
    this.submit(true)
  },

  submit(publishNow) {
    this.setData({ submitting: true })
    request({
      url: '/api/v1/cargo/shipments',
      method: 'POST',
      data: this.buildPayload(publishNow)
    })
      .then((cargo) => {
        if (publishNow) {
          wx.showToast({ title: '货源已发布', icon: 'success' })
        } else {
          wx.showToast({ title: '草稿已保存', icon: 'success' })
        }
        this.setData({ submitting: false })
        if (publishNow) {
          setTimeout(() => wx.redirectTo({ url: `/pages/trade/match/match?mode=cargo&refId=${cargo.id}` }), 700)
        } else {
          setTimeout(() => wx.navigateBack(), 700)
        }
      })
      .catch(() => this.setData({ submitting: false }))
  }
})
