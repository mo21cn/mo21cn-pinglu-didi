// 港口工作台（v1 · 蓝紫渐变头 + 原核心功能保留）
const { request } = require('../../utils/request')

const PORTS = [
  { code: 'NNG', label: '南宁' },
  { code: 'GGU', label: '贵港' },
  { code: 'WUZ', label: '梧州' },
  { code: 'LBZ', label: '来宾' },
  { code: 'LZZ', label: '柳州' },
  { code: 'BSE', label: '百色' },
  { code: 'CHZ', label: '崇左' },
  { code: 'QNZ', label: '钦州' },
  { code: 'FCG', label: '防城港' },
  { code: 'BHZ', label: '北海' }
]

const STATUS_LABELS = {
  pending: '待确认',
  confirmed: '已锁定',
  rejected: '已驳回',
  cancelled: '已撤销',
  completed: '已完成'
}

Page({
  data: {
    tab: 'appts',
    ports: PORTS,
    form: {
      port_code: 'NNG',
      berth_no: '',
      berth_name: '',
      max_dwt: '',
      max_draft: '',
      concurrent_capacity: '1'
    },
    portIndex: 0,
    berthList: [],
    apptList: [],
    apptTotal: 0,
    statusLabels: STATUS_LABELS,
    submitting: false,
    loading: false
  },

  onLoad() { this.fetchAppts() },

  onShow() {
    if (this.data.tab === 'appts') this.fetchAppts()
    if (this.data.tab === 'list') this.fetchBerths()
  },

  onPullDownRefresh() {
    if (this.data.tab === 'appts') this.fetchAppts()
    if (this.data.tab === 'list') this.fetchBerths()
    wx.stopPullDownRefresh()
  },

  switchTab(e) {
    const tab = e.currentTarget.dataset.tab
    this.setData({ tab })
    if (tab === 'list') this.fetchBerths()
    if (tab === 'appts') this.fetchAppts()
  },

  fetchBerths() {
    this.setData({ loading: true })
    request({ url: '/api/v1/port/berths', data: { size: 50 } })
      .then((res) => this.setData({ berthList: res.items || [] }))
      .catch(() => {})
      .finally(() => this.setData({ loading: false }))
  },

  fetchAppts() {
    this.setData({ loading: true })
    request({ url: '/api/v1/port/appts-review', data: { status: 'pending', size: 50 } })
      .then((res) => this.setData({ apptList: res.items || [], apptTotal: res.total || 0 }))
      .catch(() => {})
      .finally(() => this.setData({ loading: false }))
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value })
  },

  onPortPick(e) {
    const idx = Number(e.detail.value)
    this.setData({ portIndex: idx, 'form.port_code': PORTS[idx].code })
  },

  submitForm() {
    const f = this.data.form
    if (!f.berth_no) return wx.showToast({ title: '请填写泊位号', icon: 'none' })
    if (!f.max_dwt || Number(f.max_dwt) <= 0) return wx.showToast({ title: '请填写最大载重吨', icon: 'none' })
    if (!f.max_draft || Number(f.max_draft) <= 0) return wx.showToast({ title: '请填写允许吃水', icon: 'none' })
    this.setData({ submitting: true })
    request({
      url: '/api/v1/port/berths',
      method: 'POST',
      data: {
        port_code: f.port_code,
        berth_no: f.berth_no,
        berth_name: f.berth_name,
        max_dwt: Number(f.max_dwt),
        max_draft: Number(f.max_draft),
        allowed_ship_types: ['bulk', 'general'],
        concurrent_capacity: Number(f.concurrent_capacity) || 1
      }
    })
      .then(() => {
        wx.showToast({ title: '泊位已创建', icon: 'success' })
        this.setData({
          form: { port_code: f.port_code, berth_no: '', berth_name: '', max_dwt: '', max_draft: '', concurrent_capacity: '1' },
          tab: 'list'
        })
        this.fetchBerths()
      })
      .catch((err) => wx.showToast({ title: err.message || '创建失败', icon: 'none' }))
      .finally(() => this.setData({ submitting: false }))
  },

  confirmAppt(e) {
    const id = e.currentTarget.dataset.id
    request({ url: `/api/v1/port/appts/${id}/confirm`, method: 'POST' })
      .then(() => {
        wx.showToast({ title: '已确认，档期锁定', icon: 'success' })
        this.fetchAppts()
      })
      .catch((err) => wx.showToast({ title: err.message || '确认失败（档期冲突）', icon: 'none' }))
  },

  rejectAppt(e) {
    const id = e.currentTarget.dataset.id
    request({
      url: `/api/v1/port/appts/${id}/reject`,
      method: 'POST',
      data: { reason: '档期安排冲突' }
    })
      .then(() => {
        wx.showToast({ title: '已驳回', icon: 'success' })
        this.fetchAppts()
      })
      .catch((err) => wx.showToast({ title: err.message || '操作失败', icon: 'none' }))
  },

  goBack() { wx.navigateBack() }
})
