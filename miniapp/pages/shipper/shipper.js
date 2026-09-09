// 货主工作台：发布货源 + 我的货源列表
const { request } = require('../../utils/request')
const { switchRole } = require('../../utils/auth')

const CARGO_TYPES = [
  { key: 'bulk', label: '散货' },
  { key: 'general', label: '件杂货' },
  { key: 'container', label: '集装箱' },
  { key: 'tanker', label: '液货' },
  { key: 'other', label: '其他' }
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

const STATUS_LABELS = {
  draft: '草稿',
  published: '已发布',
  matched: '已撮合',
  shipped: '运输中',
  completed: '已完成',
  cancelled: '已取消'
}

Page({
  data: {
    tab: 'list',            // list=我的货源 form=发布货源
    // 表单
    form: {
      cargo_name: '',
      cargo_type: 'bulk',
      weight_t: '',
      origin_port: 'NNG',
      dest_port: 'GGU',
      expect_date: '',
      offer_price: '',
      remark: ''
    },
    cargoTypes: CARGO_TYPES,
    ports: PORTS,
    originIndex: 0,
    destIndex: 1,
    typeIndex: 0,
    today: '',
    // 列表
    list: [],
    total: 0,
    statusLabels: STATUS_LABELS,
    submitting: false,
    loading: false
  },

  onLoad() {
    const now = new Date()
    const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000)
    const iso = tomorrow.toISOString().slice(0, 10)
    this.setData({ today: iso, 'form.expect_date': iso })
    this.fetchList()
  },

  onShow() {
    if (this.data.tab === 'list') this.fetchList()
  },

  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
    if (this.data.tab === 'list') this.fetchList()
  },

  /** 拉取我的货源列表 */
  fetchList() {
    this.setData({ loading: true })
    request({ url: '/api/v1/cargo/shipments', data: { size: 50 } })
      .then((res) => {
        this.setData({ list: res.items || [], total: res.total || 0 })
      })
      .catch(() => {})
      .finally(() => this.setData({ loading: false }))
  },

  // ---- 表单事件 ----
  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value })
  },
  onTypePick(e) {
    const idx = Number(e.detail.value)
    this.setData({ typeIndex: idx, 'form.cargo_type': CARGO_TYPES[idx].key })
  },
  onOriginPick(e) {
    const idx = Number(e.detail.value)
    this.setData({ originIndex: idx, 'form.origin_port': PORTS[idx].key })
  },
  onDestPick(e) {
    const idx = Number(e.detail.value)
    this.setData({ destIndex: idx, 'form.dest_port': PORTS[idx].key })
  },
  onDatePick(e) {
    this.setData({ 'form.expect_date': e.detail.value })
  },

  /** 提交货源（默认直接发布进入撮合池） */
  submitForm() {
    const f = this.data.form
    if (!f.cargo_name) return wx.showToast({ title: '请填写货物名称', icon: 'none' })
    if (!f.weight_t || Number(f.weight_t) <= 0) return wx.showToast({ title: '请填写有效吨位', icon: 'none' })
    if (f.origin_port === f.dest_port) return wx.showToast({ title: '起讫港不能相同', icon: 'none' })

    if (this.data.submitting) return
    this.setData({ submitting: true })
    request({
      url: '/api/v1/cargo/shipments',
      method: 'POST',
      data: {
        cargo_name: f.cargo_name,
        cargo_type: f.cargo_type,
        weight_t: Number(f.weight_t),
        origin_port: f.origin_port,
        dest_port: f.dest_port,
        expect_date: f.expect_date,
        offer_price: f.offer_price ? Number(f.offer_price) : null,
        remark: f.remark,
        publish_now: true
      }
    })
      .then(() => {
        wx.showToast({ title: '货源已发布', icon: 'success' })
        this.setData({ tab: 'list' })
        this.fetchList()
      })
      .catch(() => {})
      .finally(() => this.setData({ submitting: false }))
  },

  /** 草稿发布 / 取消 */
  onAction(e) {
    const { id, action } = e.currentTarget.dataset
    request({ url: `/api/v1/cargo/shipments/${id}/${action}`, method: 'POST' })
      .then(() => {
        wx.showToast({ title: action === 'publish' ? '已发布' : '已取消', icon: 'success' })
        this.fetchList()
      })
      .catch(() => {})
  },

  /** 已发布货源 → 跳转撮合页找候选船（可下单） */
  onMatch(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: `/pages/trade/match/match?mode=cargo&refId=${id}` })
  },

  /** 查看我的订单（支付/签收/撤单） */
  goOrders() {
    wx.navigateTo({ url: '/pages/trade/orders/orders' })
  },

  /** 角色不符时引导切换 */
  ensureRole() {
    const auth = require('../../utils/auth')
    const user = auth.getUser()
    if (user && user.current_role !== 'shipper') {
      return switchRole('shipper')
    }
    return Promise.resolve()
  }
})
