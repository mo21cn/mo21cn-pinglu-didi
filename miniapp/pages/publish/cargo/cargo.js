// 04 发布货物（货主端二级页）
// 对接 POST /api/v1/cargo/shipments（publish_now=true 确认发布 / false 保存草稿）
const { request } = require('../../../utils/request')

const CARGO_TYPES = [
  { key: 'bulk',      label: '散货' },
  { key: 'general',   label: '件杂货' },
  { key: 'container', label: '集装箱' },
  { key: 'tanker',    label: '液货' },
  { key: 'other',     label: '其他' }
]

const PACKS = ['散装', '袋装', '托盘', '裸装', '罐装']

const SHIP_TYPES = [
  { key: '',          label: '按货物条件匹配' },
  { key: 'bulk',      label: '散货船' },
  { key: 'general',   label: '件杂货船' },
  { key: 'container', label: '集装箱船' },
  { key: 'tanker',    label: '液货船' }
]

const PORTS = [
  { key: 'NNG', label: '南宁 · 平塘港' },
  { key: 'GGU', label: '贵港' },
  { key: 'WUZ', label: '梧州' },
  { key: 'BIN', label: '来宾' },
  { key: 'LZH', label: '柳州' },
  { key: 'BSZ', label: '百色' },
  { key: 'CHZ', label: '崇左' },
  { key: 'GXL', label: '桂林' },
  { key: 'HEZ', label: '贺州' },
  { key: 'YUL', label: '玉林' },
  { key: 'QNZ', label: '钦州' },
  { key: 'FCG', label: '防城港' },
  { key: 'BHZ', label: '北海' }
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
    submitting: false
  },

  onLoad() {
    const d = new Date(Date.now() + 24 * 3600 * 1000)
    this.setData({ 'form.expect_date': d.toISOString().slice(0, 10) })
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field
    const patch = { [`form.${field}`]: e.detail.value }
    if (field === 'remark') patch.remarkLen = String(e.detail.value || '').length
    this.setData(patch)
  },

  pickOrigin() {
    wx.showActionSheet({
      itemList: PORTS.map((p) => p.label),
      success: (res) => {
        const p = PORTS[res.tapIndex]
        this.setData({ 'form.origin_port': p.key, 'form.origin_label': p.label })
      }
    })
  },

  pickDest() {
    wx.showActionSheet({
      itemList: PORTS.map((p) => p.label),
      success: (res) => {
        const p = PORTS[res.tapIndex]
        this.setData({ 'form.dest_port': p.key, 'form.dest_label': p.label })
      }
    })
  },

  pickCargoType() {
    wx.showActionSheet({
      itemList: CARGO_TYPES.map((t) => t.label),
      success: (res) => {
        const t = CARGO_TYPES[res.tapIndex]
        this.setData({ cargoTypeLabel: t.label, 'form.cargo_type': t.key })
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
        this.setData({ 'form.expect_date': d.toISOString().slice(0, 10) })
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
