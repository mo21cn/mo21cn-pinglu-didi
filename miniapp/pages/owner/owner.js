// 船东端首页（03）· 找货
// 货源大厅（演示数据 + 前端筛选排序）/ 我的船队 / 船舶备案
const { request } = require('../../utils/request')
const auth = require('../../utils/auth')
const { syncTabBar } = require('../../utils/tabbar')
// 全量港口（完整展示名，如「南宁 · 平塘港」）· 顶栏常用港用
const { PORTS: PORTS_FULL } = require('../../utils/ports')
// 统一智能入口（F20 路由 / F19 全局入口）
const { openSmartEntry } = require('../../utils/agent-entry')

const SHIP_TYPES = [
  { key: 'bulk',      label: '散货船' },
  { key: 'general',   label: '件杂货船' },
  { key: 'container', label: '集装箱船' },
  { key: 'tanker',    label: '液货船' }
]

const SHIP_STATUS_LABELS = {
  pending_verify: '待审核',
  verified: '已通过',
  rejected: '已驳回'
}

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
  { key: 'QNZ', label: '钦州' },
  { key: 'FCG', label: '防城港' },
  { key: 'BHZ', label: '北海' }
]

// 货源大厅演示数据
// 注：后端暂未开放「公开货源大厅」接口（/cargo/shipments 为货主本人的发货单），
//     本列表为交互原型演示数据；撮合与出单仍走真实接口（货主发起）。
const DEMO_CARGO = [
  { id: 'd1', origin_port: 'NNG', dest_port: 'GGU', cargo_name: '水泥熟料', weight_t: 1200, price_label: '运费面议', owner_label: '陈某',   time_ago: '1小时前', distance: 4,  cargo_type: 'bulk' },
  { id: 'd2', origin_port: 'GGU', dest_port: 'WUZ', cargo_name: '钢材',     weight_t: 800,  price_label: '运费面议', owner_label: '李老板', time_ago: '3小时前', distance: 12, cargo_type: 'general' },
  { id: 'd3', origin_port: 'QNZ', dest_port: 'NNG', cargo_name: '设备',     weight_t: 200,  price_label: '运费面议', owner_label: '王先生', time_ago: '5小时前', distance: 26, cargo_type: 'general' },
  { id: 'd4', origin_port: 'NNG', dest_port: 'QNZ', cargo_name: '粮食',     weight_t: 1500, price_label: '¥ 32000',  owner_label: '赵经理', time_ago: '8小时前', distance: 18, cargo_type: 'bulk' },
  { id: 'd5', origin_port: 'BSZ', dest_port: 'GGU', cargo_name: '煤炭',     weight_t: 2000, price_label: '运费面议', owner_label: '钱老板', time_ago: '昨天',    distance: 45, cargo_type: 'bulk' }
]

Page({
  data: {
    view: 'hall',            // hall=货源大厅 fleet=我的船队 registry=船舶备案
    defaultPortLabel: '南宁 · 平塘港',
    userCode: '',            // 顶栏用户 ID（用户1024）——与「我的」页同一口径

    // 港口选择弹层（13 项超过 wx.showActionSheet 的 itemList 上限 6，改走 port-picker 组件）
    ppVisible: false, ppTitle: '选择港口', ppTip: '', ppCurrent: '', ppPorts: PORTS_FULL, ppAction: '',

    // 货源大厅筛选
    originKey: '', originLabel: '装货港',
    destKey: '',   destLabel: '卸货港',
    shipTypeKey: '', shipTypeLabel: '船型',
    sortKey: 'latest',
    list: [],

    // 船队
    shipList: [],
    shipLoading: false,
    shipError: '',
    shipCount: 0,
    verifiedCount: 0,

    // 备案表单
    regForm: {
      ship_name: '', ship_type: 'bulk', deadweight_t: '',
      length_m: '', width_m: '', draft_m: '', cert_no: '', cert_expiry: ''
    },
    regShipTypeLabel: '散货船',
    regSubmitting: false
  },

  onLoad() {
    this.applyFilter()
    this.fetchShipList()
  },

  onShow() {
    syncTabBar(this)
    this.fetchIdentity()
    if (this.data.view === 'fleet') this.fetchShipList()
  },

  onPullDownRefresh() {
    if (this.data.view === 'fleet') this.fetchShipList()
    this.applyFilter()
    wx.stopPullDownRefresh()
  },

  // ---- 顶栏 ----
  // 港口选择：13 项超过 wx.showActionSheet 的 itemList 上限（6），改走 port-picker 组件
  pickDefaultPort() {
    const cur = PORTS_FULL.find((p) => p.label === this.data.defaultPortLabel)
    this.openPortPicker({
      title: '常用港口',
      tip: '选中后作为默认出发港',
      current: cur ? cur.key : '',
      ports: PORTS_FULL,
      action: 'defaultPort'
    })
  },

  // ---- 港口选择弹层：统一入口 ----
  openPortPicker(opts) {
    this.setData({
      ppTitle: opts.title || '选择港口',
      ppTip: opts.tip || '',
      ppCurrent: opts.current || '',
      ppPorts: opts.ports || PORTS_FULL,
      ppAction: opts.action || '',
      ppVisible: true
    })
  },

  onPortPicked(e) {
    const { key, label } = e.detail
    const action = this.data.ppAction
    const patch = { ppVisible: false }
    if (action === 'defaultPort') {
      patch.defaultPortLabel = label
    } else if (action === 'originFilter') {
      patch.originKey = key
      patch.originLabel = key ? label : '装货港'
    } else if (action === 'destFilter') {
      patch.destKey = key
      patch.destLabel = key ? label : '卸货港'
    }
    this.setData(patch, () => {
      if (action === 'originFilter' || action === 'destFilter') this.applyFilter()
    })
  },

  onPortPickerClose() {
    this.setData({ ppVisible: false })
  },

  // 切角色：仅保留「船东 ⇄ 货主」互切（港口方入口已按 UI 评审下线）
  onSwitchRole() {
    wx.showModal({
      title: '切换身份',
      content: '切换到「货主（找船）」，进入货主工作台。',
      confirmText: '切换',
      success: (res) => {
        if (!res.confirm) return
        // 走 auth.enterRole（而非裸 switchRole）：开发期会同时换到该角色的演示账号，
        // 否则切过去是空账号，订单/货源列表全空
        auth.enterRole('shipper')
          .then(() => wx.switchTab({ url: '/pages/shipper/shipper' }))
          .catch(() => {})
      }
    })
  },

  // 统一智能入口（F20）：搜索框走意图路由，「✨Ai」直达货源解析态（非货主会引导切换）
  goSearch() { openSmartEntry({ title: '智能搜索', placeholder: '搜货/搜船/问用法——用一句话描述' }) },
  goAI() { wx.navigateTo({ url: '/pages/assistant/assistant?mode=parse' }) },
  goAssistant() { wx.navigateTo({ url: '/pages/assistant/assistant' }) },

  // ---- 货源大厅：筛选 / 排序 ----
  pickOriginFilter() {
    this.openPortPicker({
      title: '筛选装货港',
      tip: '选择后立即刷新货源列表',
      current: this.data.originKey,
      ports: [{ key: '', label: '装货港（全部）' }].concat(PORTS),
      action: 'originFilter'
    })
  },

  pickDestFilter() {
    this.openPortPicker({
      title: '筛选卸货港',
      tip: '选择后立即刷新货源列表',
      current: this.data.destKey,
      ports: [{ key: '', label: '卸货港（全部）' }].concat(PORTS),
      action: 'destFilter'
    })
  },

  pickShipTypeFilter() {
    const opts = [{ key: '', label: '船型（全部）' }].concat(SHIP_TYPES.map((t) => ({ key: t.key, label: t.label })))
    wx.showActionSheet({
      itemList: opts.map((o) => o.label),
      success: (res) => {
        const o = opts[res.tapIndex]
        this.setData({ shipTypeKey: o.key, shipTypeLabel: o.key ? o.label : '船型' }, () => this.applyFilter())
      }
    })
  },

  pickSort(e) {
    this.setData({ sortKey: e.currentTarget.dataset.sort }, () => this.applyFilter())
  },

  onFilterMore() {
    wx.showToast({ title: '高级筛选（吨位/日期/出价）开发中', icon: 'none', duration: 2000 })
  },

  applyFilter() {
    const { originKey, destKey, shipTypeKey, sortKey } = this.data
    // 注意：船型筛选在演示数据上按货类近似匹配（散货→散货船等）
    let list = DEMO_CARGO.filter((it) => {
      if (originKey && it.origin_port !== originKey) return false
      if (destKey && it.dest_port !== destKey) return false
      if (shipTypeKey && it.cargo_type !== shipTypeKey) return false
      return true
    })
    list = list.map((it) => ({
      ...it,
      origin_label: this.portLabel(it.origin_port),
      dest_label: this.portLabel(it.dest_port)
    }))
    if (sortKey === 'distance') list.sort((a, b) => a.distance - b.distance)
    this.setData({ list })
  },

  onViewCargo(e) {
    const item = e.currentTarget.dataset.item
    wx.showModal({
      title: `${item.origin_label} → ${item.dest_label}`,
      content: `${item.cargo_name} · ${item.weight_t} 吨\n运价：${item.price_label}\n货主：${item.owner_label}\n\n订单由货主发起。建议先发布空船信息提高曝光，货主可直接向您下单。`,
      confirmText: '发布空船',
      cancelText: '知道了',
      success: (res) => {
        if (res.confirm) wx.navigateTo({ url: '/pages/publish/ship/ship' })
      }
    })
  },

  // ---- 顶栏身份（用户头像 + 用户 ID + 常用港）----
  /** 与货主页同一实现：用户 ID 口径对齐「我的」页，缺失时给中性占位 */
  fetchIdentity() {
    const user = auth.getUser() || {}
    this.setData({ userCode: user.user_id ? '用户' + user.user_id : '未登录' })
  },

  /** 头像 → 「我的」（tabBar 页，必须 switchTab） */
  goMine() {
    wx.switchTab({ url: '/pages/mine/mine' })
  },

  // ---- 我的船队 / 备案（保留已开发能力） ----
  goFleet() {
    this.setData({ view: 'fleet' })
    this.fetchShipList()
  },

  backToHall() {
    this.setData({ view: 'hall' })
  },

  goRegistry() {
    this.setData({ view: 'registry' })
  },

  fetchShipList() {
    this.setData({ shipLoading: true, shipError: '' })
    request({ url: '/api/v1/ship/registry', data: { size: 50 } })
      .then((res) => {
        const items = res.items || []
        this.setData({
          shipList: items,
          shipCount: items.length,
          verifiedCount: items.filter((s) => s.status === 'verified').length
        })
      })
      .catch((err) => this.setData({ shipError: (err && err.message) || '船队加载失败' }))
      .finally(() => this.setData({ shipLoading: false }))
  },

  onShipMatch(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: `/pages/trade/match/match?mode=ship&refId=${id}` })
  },

  onRegInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`regForm.${field}`]: e.detail.value })
  },

  pickShipType() {
    wx.showActionSheet({
      itemList: SHIP_TYPES.map((t) => t.label),
      success: (res) => {
        const t = SHIP_TYPES[res.tapIndex]
        this.setData({ regShipTypeLabel: t.label, 'regForm.ship_type': t.key })
      }
    })
  },

  submitRegForm() {
    const f = this.data.regForm
    if (!f.ship_name) return wx.showToast({ title: '请填写船名', icon: 'none' })
    if (!f.deadweight_t || Number(f.deadweight_t) <= 0) return wx.showToast({ title: '请填写有效载重吨', icon: 'none' })
    if (!f.draft_m || Number(f.draft_m) <= 0) return wx.showToast({ title: '请填写满载吃水', icon: 'none' })
    if (!f.cert_no) return wx.showToast({ title: '请填写检验证书号', icon: 'none' })
    if (!f.cert_expiry) return wx.showToast({ title: '请填写证书有效期', icon: 'none' })
    if (this.data.regSubmitting) return

    this.setData({ regSubmitting: true })
    request({
      url: '/api/v1/ship/registry',
      method: 'POST',
      data: {
        ship_name: f.ship_name,
        ship_type: f.ship_type,
        deadweight_t: Number(f.deadweight_t),
        length_m: Number(f.length_m || 1),
        width_m: Number(f.width_m || 1),
        draft_m: Number(f.draft_m),
        cert_no: f.cert_no,
        cert_expiry: f.cert_expiry
      }
    })
      .then(() => {
        wx.showToast({ title: '备案已提交，待审核', icon: 'success' })
        this.setData({
          regForm: {
            ship_name: '', ship_type: 'bulk', deadweight_t: '',
            length_m: '', width_m: '', draft_m: '', cert_no: '', cert_expiry: ''
          }
        })
        this.fetchShipList()
        this.setData({ view: 'fleet' })
      })
      .catch(() => {})
      .finally(() => this.setData({ regSubmitting: false }))
  },

  // ---- 工具 ----
  portLabel(key) {
    const p = PORTS.find((x) => x.key === key)
    return p ? p.label : key
  },

  shipStatusLabel(s) { return SHIP_STATUS_LABELS[s] || s }
})
