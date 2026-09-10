// 货主端首页（找船 · v1）
// 入口：货源表单填写 + 一键发整车/零担 + 我的货源入口 + 热门航线
const { request } = require('../../utils/request')

const CARGO_TYPES = [
  { key: 'bulk',      label: '散货' },
  { key: 'general',   label: '件杂货' },
  { key: 'container', label: '集装箱' },
  { key: 'tanker',    label: '液货' },
  { key: 'other',     label: '其他' }
]

const PORTS = [
  { key: 'NNG', label: '南宁' },
  { key: 'GGU', label: '贵港' },
  { key: 'WUZ', label: '梧州' },
  { key: 'BIN', label: '来宾' },
  { key: 'LZH', label: '柳州' },
  { key: 'BSZ', label: '百色' },
  { key: 'CHZ', label: '崇左' },
  { key: 'GXL', label: '桂林' },
  { key: 'HEZ', label: '贺州' },
  { key: 'YUL', label: '玉林' },
  { key: 'QNZ', label: '钦州（海港）' },
  { key: 'FCG', label: '防城港（海港）' },
  { key: 'BHZ', label: '北海（海港）' }
]

// 热门航线（演示版静态数据，后续可走后端推荐）
const HOT_ROUTES = [
  { key: 'h1', from: '南宁',   to: '贵港',     price: 942,  dist: '约 160 km' },
  { key: 'h2', from: '贵港',   to: '梧州',     price: 1240, dist: '约 280 km' },
  { key: 'h3', from: '百色',   to: '南宁',     price: 1079, dist: '约 260 km' },
  { key: 'h4', from: '来宾',   to: '桂平',     price: 748,  dist: '约 130 km' },
  { key: 'h5', from: '钦州',   to: '防城港',   price: 639,  dist: '约 90 km'  },
  { key: 'h6', from: '北海',   to: '湛江',     price: 1180, dist: '约 360 km' }
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
    cargoTypes: CARGO_TYPES,
    ports: PORTS,
    cargoTypeIndex: 0,
    originIndex: -1,
    destIndex: -1,
    today: '',
    hotRoutes: HOT_ROUTES,
    myCargoTotal: 0,
    publishing: false
  },

  onLoad() {
    const now = new Date()
    const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000)
    const iso = tomorrow.toISOString().slice(0, 10)
    this.setData({ today: iso, 'form.expect_date': iso })
    this.fetchMyCargoCount()
  },

  onShow() {
    this.fetchMyCargoCount()
  },

  onPullDownRefresh() {
    this.fetchMyCargoCount()
    wx.stopPullDownRefresh()
  },

  // ---- 我的货源数量（用于快捷入口副标题） ----
  fetchMyCargoCount() {
    request({ url: '/api/v1/cargo/shipments', data: { size: 1 } })
      .then((res) => this.setData({ myCargoTotal: res.total || 0 }))
      .catch(() => {})
  },

  // ---- 顶栏入口 ----
  goHome() {},
  goSearch() { wx.showToast({ title: '搜索功能开发中', icon: 'none' }) },
  goAI() { wx.navigateTo({ url: '/pages/assistant/assistant?topic=cargo' }) },
  goAssistant() { wx.navigateTo({ url: '/pages/assistant/assistant' }) },

  // ---- 表单事件 ----
  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value })
  },

  pickCargoType() {
    wx.showActionSheet({
      itemList: CARGO_TYPES.map((t) => t.label),
      success: (res) => {
        const idx = res.tapIndex
        const item = CARGO_TYPES[idx]
        this.setData({ cargoTypeIndex: idx, cargoTypeLabel: item.label, 'form.cargo_type': item.key })
      }
    })
  },

  pickOrigin() {
    wx.showActionSheet({
      itemList: PORTS.map((p) => p.label),
      success: (res) => {
        const idx = res.tapIndex
        const item = PORTS[idx]
        this.setData({ originIndex: idx, 'form.origin_port': item.key, 'form.origin_label': item.label })
      }
    })
  },

  pickDest() {
    wx.showActionSheet({
      itemList: PORTS.map((p) => p.label),
      success: (res) => {
        const idx = res.tapIndex
        const item = PORTS[idx]
        this.setData({ destIndex: idx, 'form.dest_port': item.key, 'form.dest_label': item.label })
      }
    })
  },

  pickDate() {
    wx.showActionSheet({
      itemList: ['今天', '明天', '后天', '一周内', '自定义...'],
      success: (res) => {
        const now = new Date()
        const map = [
          new Date(now.getTime()),
          new Date(now.getTime() + 24 * 60 * 60 * 1000),
          new Date(now.getTime() + 2 * 24 * 60 * 60 * 1000),
          new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000)
        ]
        if (res.tapIndex <= 3) {
          this.setData({ 'form.expect_date': map[res.tapIndex].toISOString().slice(0, 10) })
        } else {
          wx.showToast({ title: '请使用表单日期控件', icon: 'none' })
        }
      }
    })
  },

  // ---- 双按钮发布 ----
  onPublish(e) {
    if (this.data.publishing) return
    const mode = e.currentTarget.dataset.mode
    const f = this.data.form
    if (!f.origin_port) return wx.showToast({ title: '请选择装货地', icon: 'none' })
    if (!f.dest_port) return wx.showToast({ title: '请选择卸货地', icon: 'none' })
    if (f.origin_port === f.dest_port) return wx.showToast({ title: '起讫港不能相同', icon: 'none' })
    if (!f.weight_t || Number(f.weight_t) <= 0) return wx.showToast({ title: '请填写有效吨位', icon: 'none' })
    if (!f.expect_date) return wx.showToast({ title: '请选择装货日期', icon: 'none' })

    this.setData({ publishing: true })
    request({
      url: '/api/v1/cargo/shipments',
      method: 'POST',
      data: {
        cargo_name: this.guessCargoName(f.cargo_type) || (mode === 'ltl' ? '零担货' : '整车货'),
        cargo_type: f.cargo_type,
        weight_t: Number(f.weight_t),
        origin_port: f.origin_port,
        dest_port: f.dest_port,
        expect_date: f.expect_date,
        offer_price: f.offer_price ? Number(f.offer_price) : null,
        remark: (mode === 'ltl' ? '[零担] ' : '[整车] ') + (f.remark || ''),
        publish_now: true
      }
    })
      .then(() => {
        wx.showToast({ title: '货源已发布', icon: 'success' })
        this.setData({
          'form.weight_t': '',
          'form.offer_price': '',
          'form.remark': ''
        })
        this.fetchMyCargoCount()
      })
      .catch(() => {})
      .finally(() => this.setData({ publishing: false }))
  },

  guessCargoName(type) {
    const map = {
      bulk: '散装货物', general: '件杂货', container: '集装箱货',
      tanker: '液体货物', other: '其他货物'
    }
    return map[type] || ''
  },

  goMyCargo() {
    wx.showLoading({ title: '加载我的货源...', mask: true })
    request({ url: '/api/v1/cargo/shipments', data: { size: 50 } })
      .then((res) => {
        wx.hideLoading()
        const items = res.items || []
        if (items.length === 0) {
          return wx.showToast({ title: '暂无货源', icon: 'none' })
        }
        const content = items
          .slice(0, 5)
          .map((it, i) => `${i + 1}. ${it.cargo_name} ${it.weight_t}吨 ${it.origin_port}→${it.dest_port}（${this.statusText(it.status)}）`)
          .join('\n')
        wx.showModal({
          title: `我的货源（${items.length}）`,
          content: content + (items.length > 5 ? '\n...' : ''),
          showCancel: false,
          confirmText: '知道了'
        })
      })
      .catch(() => wx.hideLoading())
  },

  statusText(status) {
    const map = {
      draft: '草稿', published: '已发布', matched: '已撮合',
      shipped: '运输中', completed: '已完成', cancelled: '已取消'
    }
    return map[status] || status
  },

  goAIPublish() {
    wx.navigateTo({ url: '/pages/assistant/assistant?topic=cargo&mode=parse' })
  },

  onRouteTap(e) {
    const item = e.currentTarget.dataset.item
    const findKey = (name) => {
      const clean = String(name).replace(/（[^）]*）/g, '')
      const hit = PORTS.find((p) => p.label.indexOf(clean) >= 0 || clean.indexOf(p.label) >= 0)
      return hit ? hit.key : ''
    }
    const originKey = findKey(item.from)
    const destKey = findKey(item.to)
    this.setData({
      'form.origin_port': originKey,
      'form.origin_label': item.from,
      'form.dest_port': destKey,
      'form.dest_label': item.to
    })
    wx.showToast({ title: `已填 ${item.from} → ${item.to}`, icon: 'success' })
  }
})
