// 船东工作台：我的船队 / 船舶备案
const { request } = require('../../utils/request')
const { switchRole } = require('../../utils/auth')

const SHIP_TYPES = [
  { key: 'bulk', label: '散货船' },
  { key: 'general', label: '件杂货船' },
  { key: 'container', label: '集装箱船' },
  { key: 'tanker', label: '液货船' }
]

const STATUS_LABELS = {
  pending_verify: '待审核',
  verified: '已通过',
  rejected: '已驳回'
}

Page({
  data: {
    tab: 'list',
    // 备案表单
    form: {
      ship_name: '',
      ship_type: 'bulk',
      deadweight_t: '',
      length_m: '',
      width_m: '',
      draft_m: '',
      cert_no: '',
      cert_expiry: ''
    },
    shipTypes: SHIP_TYPES,
    typeIndex: 0,
    // 船队
    list: [],
    total: 0,
    statusLabels: STATUS_LABELS,
    submitting: false,
    loading: false
  },

  onLoad() {
    this.fetchList()
  },

  onShow() {
    if (this.data.tab === 'list') this.fetchList()
  },

  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
    if (this.data.tab === 'list') this.fetchList()
  },

  fetchList() {
    this.setData({ loading: true })
    request({ url: '/api/v1/ship/registry', data: { size: 50 } })
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
    this.setData({ typeIndex: idx, 'form.ship_type': SHIP_TYPES[idx].key })
  },
  onExpiryPick(e) {
    this.setData({ 'form.cert_expiry': e.detail.value })
  },

  /** 提交备案 */
  submitForm() {
    const f = this.data.form
    if (!f.ship_name) return wx.showToast({ title: '请填写船名', icon: 'none' })
    if (!f.deadweight_t || Number(f.deadweight_t) <= 0) return wx.showToast({ title: '请填写有效载重吨', icon: 'none' })
    if (!f.draft_m || Number(f.draft_m) <= 0) return wx.showToast({ title: '请填写满载吃水', icon: 'none' })
    if (!f.cert_no) return wx.showToast({ title: '请填写船舶检验证书号', icon: 'none' })
    if (!f.cert_expiry) return wx.showToast({ title: '请选择证书有效期', icon: 'none' })

    if (this.data.submitting) return
    this.setData({ submitting: true })
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
        this.setData({ tab: 'list' })
        this.fetchList()
      })
      .catch(() => {})
      .finally(() => this.setData({ submitting: false }))
  },

  /** 已过审船舶 → 跳转撮合页找候选货源 */
  onMatch(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: `/pages/trade/match/match?mode=ship&refId=${id}` })
  },

  /** 我的订单（启运/撤单） */
  goOrders() {
    wx.navigateTo({ url: '/pages/trade/orders/orders' })
  },

  /** 角色不符时引导切换 */
  ensureRole() {
    const auth = require('../../utils/auth')
    const user = auth.getUser()
    if (user && user.current_role !== 'owner') {
      return switchRole('owner')
    }
    return Promise.resolve()
  }
})
