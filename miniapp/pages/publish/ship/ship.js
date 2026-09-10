// 05 发布空船（船东端二级页）
// 后端暂无「空船发布」接口：表单完整可用，提交时占位提示 + 落本机草稿（Storage）
// 船只列表复用 GET /api/v1/ship/registry（仅取 verified）
const { request } = require('../../../utils/request')

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

const SHIP_TYPE_LABELS = {
  bulk: '散货船', general: '件杂货船', container: '集装箱船', tanker: '液货船'
}

const DRAFT_KEY = 'empty_ship_drafts'

Page({
  data: {
    ships: [],
    shipLoading: false,
    shipLabel: '',
    shipBrief: '',
    selectedShipId: 0,
    form: {
      current_port: '', current_port_label: '',
      available_t: '',
      available_date: '',
      origin_port: '', origin_label: '',
      dest_port: '', dest_label: '',
      offer_price: '',
      remark: ''
    },
    priceMode: 'negotiable',
    remarkLen: 0
  },

  onLoad() {
    const d = new Date(Date.now() + 3600 * 1000)
    this.setData({ 'form.available_date': d.toISOString().slice(0, 10) })
    this.fetchShips()
  },

  fetchShips() {
    this.setData({ shipLoading: true })
    request({ url: '/api/v1/ship/registry', data: { size: 50 } })
      .then((res) => {
        const ships = (res.items || []).filter((s) => s.status === 'verified')
        this.setData({ ships })
      })
      .catch(() => {})
      .finally(() => this.setData({ shipLoading: false }))
  },

  pickShip() {
    const ships = this.data.ships
    if (!ships.length) return
    wx.showActionSheet({
      itemList: ships.map((s) => `${s.ship_name}（${s.deadweight_t}吨）`),
      success: (res) => {
        const s = ships[res.tapIndex]
        this.setData({
          selectedShipId: s.id,
          shipLabel: s.ship_name,
          shipBrief: `${SHIP_TYPE_LABELS[s.ship_type] || s.ship_type} · 载重 ${s.deadweight_t} 吨 · 吃水 ${s.draft_m} 米`,
          'form.available_t': String(Math.round(s.deadweight_t))
        })
      }
    })
  },

  goRegistry() {
    wx.switchTab({
      url: '/pages/owner/owner',
      success: () => wx.showToast({ title: '请在「我的船队」中完成备案', icon: 'none', duration: 2500 })
    })
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field
    const patch = { [`form.${field}`]: e.detail.value }
    if (field === 'remark') patch.remarkLen = String(e.detail.value || '').length
    this.setData(patch)
  },

  pickCurrentPort() {
    const ports = PORTS.filter((p) => p.key !== 'NNG')
    const opts = [{ key: 'NNG', label: '南宁 · 平塘港' }].concat(ports)
    wx.showActionSheet({
      itemList: opts.map((p) => p.label),
      success: (res) => {
        const p = opts[res.tapIndex]
        this.setData({ 'form.current_port': p.key, 'form.current_port_label': p.label })
      }
    })
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

  setAnyPort() {
    this.setData({ 'form.dest_port': '', 'form.dest_label': '不同港' })
  },

  pickDate() {
    wx.showActionSheet({
      itemList: ['今天', '明天', '后天', '一周内'],
      success: (res) => {
        const offsets = [0, 1, 2, 7]
        const d = new Date(Date.now() + offsets[res.tapIndex] * 86400000)
        this.setData({ 'form.available_date': d.toISOString().slice(0, 10) })
      }
    })
  },

  pickPriceMode(e) {
    const mode = e.currentTarget.dataset.mode
    const patch = { priceMode: mode }
    if (mode === 'negotiable') patch['form.offer_price'] = ''
    this.setData(patch)
  },

  // ---- 草稿（本机） ----
  buildDraft() {
    return {
      ship_id: this.data.selectedShipId,
      ship_name: this.data.shipLabel,
      ...this.data.form,
      price_mode: this.data.priceMode,
      saved_at: new Date().toISOString()
    }
  },

  saveDraftToStorage(draft) {
    try {
      const raw = wx.getStorageSync(DRAFT_KEY)
      const list = Array.isArray(raw) ? raw : []
      list.unshift(draft)
      wx.setStorageSync(DRAFT_KEY, list.slice(0, 20))
    } catch (e) {
      // Storage 异常不阻塞
    }
  },

  validate() {
    const f = this.data.form
    if (!this.data.selectedShipId) { wx.showToast({ title: '请选择船舶', icon: 'none' }); return false }
    if (!f.current_port) { wx.showToast({ title: '请选择当前停靠港', icon: 'none' }); return false }
    if (!f.available_t || Number(f.available_t) <= 0) { wx.showToast({ title: '请填写可用载重', icon: 'none' }); return false }
    if (!f.available_date) { wx.showToast({ title: '请选择空船可用日期', icon: 'none' }); return false }
    if (!f.origin_port) { wx.showToast({ title: '请选择出发港', icon: 'none' }); return false }
    return true
  },

  saveDraft() {
    if (!this.validate()) return
    this.saveDraftToStorage(this.buildDraft())
    wx.showToast({ title: '草稿已保存到本机', icon: 'success' })
    setTimeout(() => wx.navigateBack(), 800)
  },

  // 后端暂无空船发布接口 → 占位：本机留档 + 明确提示
  publish() {
    if (!this.validate()) return
    this.saveDraftToStorage(this.buildDraft())
    wx.showModal({
      title: '功能开发中',
      content: '空船发布接口尚未上线。本次填写已保存为本机草稿，功能上线后可一键同步发布。',
      showCancel: true,
      confirmText: '知道了',
      cancelText: '继续修改',
      success: (res) => {
        if (res.confirm) wx.navigateBack()
      }
    })
  }
})
