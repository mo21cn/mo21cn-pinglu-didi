// 货主端首页（02）· 找船
// 我要发货（港口/货物/用船需求/运输方式）→ 创建货源 → 跳撮合「查看匹配船源」
const { request } = require('../../utils/request')
const auth = require('../../utils/auth')
const { syncTabBar } = require('../../utils/tabbar')

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

// 推荐船源（演示数据：货主视角暂无撮合上下文时展示）
const HOT_SHIPS = [
  { name: '桂航008', type_label: '散货船',    deadweight_t: 2000, home_label: '南宁可装', certified: true },
  { name: '西江016', type_label: '散货船',    deadweight_t: 3000, home_label: '南宁可装', certified: true },
  { name: '平陆018', type_label: '集装箱船',  deadweight_t: 1500, home_label: '贵港可装', certified: true }
]

// 推荐航线（演示数据）
const HOT_ROUTES = [
  { key: 'r1', from: 'NNG', from_label: '南宁', to: 'GGU', to_label: '贵港' },
  { key: 'r2', from: 'NNG', from_label: '南宁', to: 'QNZ', to_label: '钦州' },
  { key: 'r3', from: 'GGU', from_label: '贵港', to: 'WUZ', to_label: '梧州' },
  { key: 'r4', from: 'NNG', from_label: '南宁', to: 'FCG', to_label: '防城港' }
]

Page({
  data: {
    roleLabel: '货主',
    defaultPortLabel: '南宁 · 平塘港',
    defaultPortKey: 'NNG',
    form: {
      cargo_name: '',
      cargo_type: 'bulk',
      weight_t: '',
      origin_port: 'NNG',
      origin_label: '南宁 · 平塘港',
      dest_port: '',
      dest_label: '',
      expect_date: '',
      offer_price: ''
    },
    panel: '',
    cargoTypeLabel: '散货',
    packLabel: '',
    shipTypeLabel: '',
    carryMode: 'ftl',
    cargoBrief: '品类 · 吨数',
    reqBrief: '船型 · 装货日期',
    hotShips: HOT_SHIPS,
    hotRoutes: HOT_ROUTES,
    myCargoTotal: 0,
    publishing: false
  },

  onLoad() {
    const d = new Date(Date.now() + 24 * 3600 * 1000)
    this.setData({ 'form.expect_date': d.toISOString().slice(0, 10) })
    this.refreshBrief()
    this.fetchMyCargoCount()
  },

  onShow() {
    syncTabBar(this)
    this.fetchMyCargoCount()
  },

  onPullDownRefresh() {
    this.fetchMyCargoCount()
    wx.stopPullDownRefresh()
  },

  // ---- 副标题摘要 ----
  refreshBrief() {
    const f = this.data.form
    const cargoBrief = (f.cargo_name || this.data.cargoTypeLabel) + (f.weight_t ? ' · ' + f.weight_t + ' 吨' : '')
    const reqBrief = (this.data.shipTypeLabel || '不限船型') + (f.expect_date ? ' · ' + f.expect_date.slice(5) + ' 装' : '')
    this.setData({ cargoBrief, reqBrief })
  },

  // ---- 顶栏 ----
  pickDefaultPort() {
    wx.showActionSheet({
      itemList: PORTS.map((p) => p.label),
      success: (res) => {
        const p = PORTS[res.tapIndex]
        this.setData({
          defaultPortLabel: p.label,
          defaultPortKey: p.key,
          'form.origin_port': p.key,
          'form.origin_label': p.label
        })
      }
    })
  },

  onSwitchRole() {
    wx.showActionSheet({
      itemList: ['切换到船东（找货）', '切换到港口方'],
      success: (res) => {
        const role = res.tapIndex === 0 ? 'owner' : 'port'
        const url = role === 'owner' ? '/pages/owner/owner' : '/pages/port/port'
        auth.switchRole(role)
          .then(() => wx.switchTab({ url }))
          .catch(() => {})
      }
    })
  },

  goSearch() { wx.showToast({ title: '搜索功能开发中', icon: 'none' }) },
  goAI() { wx.navigateTo({ url: '/pages/assistant/assistant?topic=cargo&mode=parse' }) },
  goAssistant() { wx.navigateTo({ url: '/pages/assistant/assistant' }) },

  // ---- 表单 ----
  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value }, () => this.refreshBrief())
  },

  togglePanel(e) {
    const panel = e.currentTarget.dataset.panel
    this.setData({ panel: this.data.panel === panel ? '' : panel })
  },

  pickCargoType() {
    wx.showActionSheet({
      itemList: CARGO_TYPES.map((t) => t.label),
      success: (res) => {
        const t = CARGO_TYPES[res.tapIndex]
        this.setData({ cargoTypeLabel: t.label, 'form.cargo_type': t.key }, () => this.refreshBrief())
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
        this.setData({ shipTypeLabel: t.key ? t.label : '' }, () => this.refreshBrief())
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

  pickDate() {
    const today = new Date().toISOString().slice(0, 10)
    wx.showActionSheet({
      itemList: ['今天', '明天', '后天', '一周内'],
      success: (res) => {
        const offsets = [0, 1, 2, 7]
        const d = new Date(Date.now() + offsets[res.tapIndex] * 86400000)
        this.setData({ 'form.expect_date': d.toISOString().slice(0, 10) }, () => this.refreshBrief())
      },
      fail: () => {
        // 用户取消：保留已有日期
        if (!this.data.form.expect_date) this.setData({ 'form.expect_date': today })
      }
    })
  },

  pickCarryMode(e) {
    this.setData({ carryMode: e.currentTarget.dataset.mode })
  },

  // ---- 查看匹配船源：创建货源（直接发布）→ 跳撮合页 ----
  onFindShips() {
    if (this.data.publishing) return
    const f = this.data.form
    if (!f.origin_port) return wx.showToast({ title: '请选择装货港', icon: 'none' })
    if (!f.dest_port) return wx.showToast({ title: '请选择卸货港', icon: 'none' })
    if (f.origin_port === f.dest_port) return wx.showToast({ title: '起讫港不能相同', icon: 'none' })
    if (!f.weight_t || Number(f.weight_t) <= 0) return wx.showToast({ title: '请在「货物资料」填写吨数', icon: 'none' })
    if (!f.expect_date) return wx.showToast({ title: '请在「用船需求」选择装货日期', icon: 'none' })

    const parts = []
    if (this.data.packLabel) parts.push('包装：' + this.data.packLabel)
    if (this.data.shipTypeLabel) parts.push('所需船型：' + this.data.shipTypeLabel)
    parts.push(this.data.carryMode === 'ftl' ? '整船运输' : '拼船运输')

    this.setData({ publishing: true })
    request({
      url: '/api/v1/cargo/shipments',
      method: 'POST',
      data: {
        cargo_name: f.cargo_name || (this.data.cargoTypeLabel + '货'),
        cargo_type: f.cargo_type,
        weight_t: Number(f.weight_t),
        origin_port: f.origin_port,
        dest_port: f.dest_port,
        expect_date: f.expect_date,
        offer_price: f.offer_price ? Number(f.offer_price) : null,
        remark: parts.join(' · '),
        publish_now: true
      }
    })
      .then((cargo) => {
        wx.showToast({ title: '货源已发布', icon: 'success' })
        this.fetchMyCargoCount()
        wx.navigateTo({ url: `/pages/trade/match/match?mode=cargo&refId=${cargo.id}` })
      })
      .catch(() => {})
      .finally(() => this.setData({ publishing: false }))
  },

  // ---- 推荐船源 / 航线 ----
  onShipTap() {
    wx.showToast({ title: '请先填写「我要发货」再查看匹配', icon: 'none', duration: 2000 })
  },

  onMoreShips() {
    wx.showToast({ title: '更多船源 · 发布货源后由撮合引擎推荐', icon: 'none', duration: 2000 })
  },

  onRouteTap(e) {
    const item = e.currentTarget.dataset.item
    this.setData({
      'form.origin_port': item.from,
      'form.origin_label': this.labelOf(item.from, item.from_label),
      'form.dest_port': item.to,
      'form.dest_label': this.labelOf(item.to, item.to_label)
    })
    wx.showToast({ title: `已预填 ${item.from_label} → ${item.to_label}`, icon: 'none' })
  },

  labelOf(key, fallback) {
    const p = PORTS.find((x) => x.key === key)
    return p ? p.label : fallback
  },

  onMoreRoutes() {
    wx.showToast({ title: '更多航线开发中', icon: 'none' })
  },

  // ---- 我的货源 ----
  fetchMyCargoCount() {
    request({ url: '/api/v1/cargo/shipments', data: { size: 1 } })
      .then((res) => this.setData({ myCargoTotal: res.total || 0 }))
      .catch(() => {})
  },

  goMyCargo() {
    wx.showLoading({ title: '加载中...', mask: true })
    request({ url: '/api/v1/cargo/shipments', data: { size: 50 } })
      .then((res) => {
        wx.hideLoading()
        const items = res.items || []
        if (items.length === 0) return wx.showToast({ title: '暂无货源', icon: 'none' })
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
  }
})
