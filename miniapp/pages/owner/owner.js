// 船东端首页（找货 · v1）
const { request } = require('../../utils/request')

const SHIP_TYPES = [
  { key: 'bulk',      label: '散货船' },
  { key: 'general',   label: '件杂货船' },
  { key: 'container', label: '集装箱船' },
  { key: 'tanker',    label: '液货船' }
]

const CARGO_TYPE_LABELS = {
  bulk: '散货',
  general: '件杂货',
  container: '集装箱',
  tanker: '液货',
  other: '其他'
}

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
  { key: 'QNZ', label: '钦州（海港）' },
  { key: 'FCG', label: '防城港（海港）' },
  { key: 'BHZ', label: '北海（海港）' }
]

const MOCK_CARGO = [
  { id: 'm1', cargo_type: 'bulk',      cargo_name: '散装水泥',  weight_t: 800,  origin_port: 'NNG', dest_port: 'GGU', expect_date: '2026-09-12', offer_price: 25000, owner_label: '张老板', distance_label: '1.5 km', time_ago: '1分钟前' },
  { id: 'm2', cargo_type: 'container', cargo_name: '集装箱货',  weight_t: 1200, origin_port: 'QNZ', dest_port: 'FCG', expect_date: '2026-09-13', offer_price: null,  owner_label: '王经理', distance_label: '3.2 km', time_ago: '5分钟前' },
  { id: 'm3', cargo_type: 'general',   cargo_name: '钢材配件',  weight_t: 600,  origin_port: 'LZH', dest_port: 'WUZ', expect_date: '2026-09-15', offer_price: 18000, owner_label: '李总',   distance_label: '8.6 km', time_ago: '10分钟前' },
  { id: 'm4', cargo_type: 'tanker',    cargo_name: '食用油',    weight_t: 1500, origin_port: 'BHZ', dest_port: 'FCG', expect_date: '2026-09-14', offer_price: 42000, owner_label: '陈先生', distance_label: '12 km',  time_ago: '15分钟前' },
  { id: 'm5', cargo_type: 'bulk',      cargo_name: '煤炭',      weight_t: 2000, origin_port: 'BSZ', dest_port: 'GGU', expect_date: '2026-09-16', offer_price: null,  owner_label: '赵老板', distance_label: '20 km',  time_ago: '20分钟前' },
  { id: 'm6', cargo_type: 'other',     cargo_name: '工程设备',  weight_t: 300,  origin_port: 'YUL', dest_port: 'QNZ', expect_date: '2026-09-13', offer_price: 9500,  owner_label: '钱经理', distance_label: '25 km',  time_ago: '30分钟前' }
]

Page({
  data: {
    tabs: [
      { key: 'find',     label: '找货',   count: 0 },
      { key: 'fleet',    label: '我的船队', count: 0 },
      { key: 'registry', label: '船舶备案', count: 0 }
    ],
    currentTab: 'find',
    filterRegionLabel: '全国',
    filterTimeLabel: '时间排序',
    cargoList: [],
    loading: false,
    shipList: [],
    shipLoading: false,
    regForm: {
      ship_name: '',
      ship_type: 'bulk',
      deadweight_t: '',
      length_m: '',
      width_m: '',
      draft_m: '',
      cert_no: '',
      cert_expiry: ''
    },
    regShipTypeLabel: '散货船',
    regTypeIndex: 0,
    regSubmitting: false
  },

  onLoad() { this.fetchCargoList() },

  onShow() {
    if (this.data.currentTab === 'find') this.fetchCargoList()
    if (this.data.currentTab === 'fleet') this.fetchShipList()
  },

  onPullDownRefresh() {
    if (this.data.currentTab === 'find') this.fetchCargoList()
    if (this.data.currentTab === 'fleet') this.fetchShipList()
    wx.stopPullDownRefresh()
  },

  goSearch() { wx.showToast({ title: '搜索功能开发中', icon: 'none' }) },
  goAI() { wx.navigateTo({ url: '/pages/assistant/assistant?topic=cargo' }) },
  goAssistant() { wx.navigateTo({ url: '/pages/assistant/assistant' }) },

  switchTab(e) {
    const key = e.currentTarget.dataset.key
    this.setData({ currentTab: key })
    if (key === 'find') this.fetchCargoList()
    if (key === 'fleet') this.fetchShipList()
  },

  onFilterRegion() {
    wx.showActionSheet({
      itemList: ['全国', '西江干线', '北部湾', '广西内河'],
      success: (res) => {
        const labels = ['全国', '西江干线', '北部湾', '广西内河']
        this.setData({ filterRegionLabel: labels[res.tapIndex] })
      }
    })
  },
  onFilterTime() {
    wx.showActionSheet({
      itemList: ['时间倒序', '时间正序', '距离最近', '出价最高'],
      success: (res) => {
        const labels = ['时间排序', '时间倒序', '时间正序', '距离最近', '出价最高']
        this.setData({ filterTimeLabel: labels[res.tapIndex] || '时间排序' })
      }
    })
  },
  onFilterMore() {
    wx.showToast({ title: '筛选面板开发中', icon: 'none' })
  },

  fetchCargoList() {
    this.setData({ loading: true })
    setTimeout(() => {
      this.setData({ cargoList: MOCK_CARGO, loading: false })
    }, 300)
  },

  onCargoTap(e) {
    const item = e.currentTarget.dataset.item
    wx.showModal({
      title: '货源详情',
      content: `${item.cargo_name}\n${item.weight_t} 吨\n${item.origin_port} → ${item.dest_port}\n${item.expect_date} 装\n${item.offer_price ? '¥ ' + item.offer_price : '面议'}`,
      confirmText: '立即抢单',
      cancelText: '关闭',
      success: (res) => {
        if (res.confirm) this.grabCargo(item.id)
      }
    })
  },

  onGrab(e) {
    const id = e.currentTarget.dataset.id
    this.grabCargo(id)
  },

  grabCargo(id) {
    wx.showToast({ title: '抢单接口开发中 · 敬请期待', icon: 'none', duration: 2000 })
  },

  fetchShipList() {
    this.setData({ shipLoading: true })
    request({ url: '/api/v1/ship/registry', data: { size: 50 } })
      .then((res) => {
        const items = res.items || []
        this.setData({
          shipList: items,
          shipCount: items.length,
          'tabs[1].count': items.filter((s) => s.status === 'verified').length
        })
      })
      .catch(() => {})
      .finally(() => this.setData({ shipLoading: false }))
  },

  onShipMatch(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: `/pages/trade/match/match?mode=ship&refId=${id}` })
  },

  goAddShip() {
    this.setData({ currentTab: 'registry' })
  },

  onRegInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`regForm.${field}`]: e.detail.value })
  },

  pickShipType() {
    wx.showActionSheet({
      itemList: SHIP_TYPES.map((t) => t.label),
      success: (res) => {
        const idx = res.tapIndex
        const item = SHIP_TYPES[idx]
        this.setData({
          regTypeIndex: idx,
          regShipTypeLabel: item.label,
          'regForm.ship_type': item.key
        })
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
        length_m: Number(f.length_m || 0),
        width_m: Number(f.width_m || 0),
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
            length_m: '', width_m: '', draft_m: '',
            cert_no: '', cert_expiry: ''
          }
        })
        this.fetchShipList()
        this.setData({ currentTab: 'fleet' })
      })
      .catch(() => {})
      .finally(() => this.setData({ regSubmitting: false }))
  },

  cargoTypeLabel(t) { return CARGO_TYPE_LABELS[t] || t },
  shipStatusLabel(s) { return SHIP_STATUS_LABELS[s] || s },
  portLabel(key) {
    const p = PORTS.find((x) => x.key === key)
    return p ? p.label : key
  },

  onPromoTap() {
    wx.showToast({ title: '活动详情开发中', icon: 'none' })
  }
})
