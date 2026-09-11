// 07 港口服务（底栏二级页）
// 服务网格为原型占位；「港口服务 → 业务办理」进入平台运营台（泊位预约审核 / 泊位管理 / 新建泊位，F4 已开发）
const { request } = require('../../utils/request')
const { getUser } = require('../../utils/auth')
const { syncTabBar } = require('../../utils/tabbar')
// 13 个港口的全站唯一来源（第三方审计 P2-5：原本地只列 10 项，桂林/贺州/玉林无法建泊位）
const { PORTS } = require('../../utils/ports')

const SERVICE_GROUPS = [
  {
    key: 'ganghang', title: '港航服务', tag: '功能预览', real: true, open: true,
    tip: '服务入口已预留，后续开放',
    items: [
      { key: 'gh1', icon: '🔔', label: '预约报闸' },
      { key: 'gh2', icon: '💰', label: '过闸费用' },
      { key: 'gh3', icon: '⏳', label: '待闸队列' }
    ]
  },
  {
    key: 'chuanzha', title: '船闸服务', tag: '即将开放', open: true,
    items: [
      { key: 'cz1', icon: '🔔', label: '预约报闸' },
      { key: 'cz2', icon: '🔍', label: '待闸查询' },
      { key: 'cz3', icon: '⏳', label: '待闸队列' },
      { key: 'cz4', icon: '📏', label: '船闸水位' },
      { key: 'cz5', icon: '💰', label: '过闸费用' },
      { key: 'cz6', icon: '🏛', label: '船闸信息' },
      { key: 'cz7', icon: '📋', label: '过闸记录' },
      { key: 'cz8', icon: '🧭', label: '运行方案' }
    ]
  },
  {
    key: 'gangkou', title: '港口服务', tag: '即将开放', open: true,
    items: [
      { key: 'ops', icon: '🏢', label: '业务办理', real: true },
      { key: 'gk2', icon: 'ℹ️', label: '港口信息' },
      { key: 'gk3', icon: '📅', label: '船期表查询' }
    ]
  },
  {
    key: 'hangdao', title: '航道服务', tag: '即将开放', open: true,
    items: [
      { key: 'hd1', icon: '🌤', label: '水情气象' },
      { key: 'hd2', icon: '⚓', label: '拖船租赁' },
      { key: 'hd3', icon: '⛽', label: '燃料补给' },
      { key: 'hd4', icon: '🔧', label: '船舶维修' }
    ]
  }
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
    view: 'service',       // service=服务网格（原型） ops=平台运营台（已开发）
    groups: SERVICE_GROUPS,
    ports: PORTS,
    tab: 'appts',
    apptFilter: 'pending',
    apptFilters: [
      { key: 'pending', label: '待确认' },
      { key: 'confirmed', label: '已锁定' },
      { key: '', label: '全部' }
    ],
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
    loading: false,
    error: ''
  },

  onShow() {
    syncTabBar(this)
    if (this.data.view === 'ops') this.reloadOps()
  },

  onPullDownRefresh() {
    if (this.data.view === 'ops') this.reloadOps()
    wx.stopPullDownRefresh()
  },

  toggleGroup(e) {
    const key = e.currentTarget.dataset.key
    const groups = this.data.groups.map((g) => (g.key === key ? { ...g, open: !g.open } : g))
    this.setData({ groups })
  },

  onServiceTap(e) {
    const item = e.currentTarget.dataset.item
    if (item && item.key === 'ops') {
      // 业务办理（泊位管理 / 预约审核）是港口方运营台，端点仅港口方角色可用。
      // 港口方身份已从 C 端下线 → 非港口方身份直接给出说明，不再发无效请求（否则整页 403）。
      const role = (getUser() || {}).current_role
      if (role !== 'port') {
        wx.showToast({ title: '运营台为港口方内部功能，暂未对外开放', icon: 'none', duration: 2200 })
        return
      }
      this.setData({ view: 'ops', tab: 'appts' })
      this.fetchAppts()
      return
    }
    wx.showToast({ title: `${item.label} · 功能预览，后续开放`, icon: 'none', duration: 2000 })
  },

  onLocate() {
    wx.showToast({ title: '地图定位能力接入中', icon: 'none' })
  },

  backToService() {
    this.setData({ view: 'service' })
  },

  // ---- 三级页：泊位档期 / 预约审核详情 ----
  /** 泊位卡 → 档期详情（只读甘特 + 硬约束校验链） */
  goBerthDetail(e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: '/pages/port/berth/berth?id=' + id })
  },

  /** 预约卡 → 审核详情；记录体走 Storage 缓存，免去单条查询端点 */
  goApptDetail(e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    const record = (this.data.apptList || []).filter((a) => a.id === id)[0]
    if (record) wx.setStorageSync('port_appt_detail', { id, record })
    wx.navigateTo({ url: '/pages/port/appt/appt?id=' + id })
  },

  // ---- 运营台：预约审核 / 泊位管理 / 新建泊位 ----
  reloadOps() {
    if (this.data.tab === 'appts') this.fetchAppts()
    if (this.data.tab === 'list') this.fetchBerths()
  },

  switchTab(e) {
    const tab = e.currentTarget.dataset.tab
    this.setData({ tab })
    this.reloadOps()
  },

  fetchBerths() {
    this.setData({ loading: true, error: '' })
    request({ url: '/api/v1/port/berths', data: { size: 50 } })
      .then((res) => this.setData({ berthList: res.items || [] }))
      .catch((err) => this.setData({ error: (err && err.message) || '泊位列表加载失败' }))
      .finally(() => this.setData({ loading: false }))
  },

  fetchAppts() {
    this.setData({ loading: true, error: '' })
    request({
      url: '/api/v1/port/appts-review',
      data: { status: this.data.apptFilter, size: 50 }
    })
      .then((res) => this.setData({ apptList: res.items || [], apptTotal: res.total || 0 }))
      .catch((err) => this.setData({ error: (err && err.message) || '预约列表加载失败' }))
      .finally(() => this.setData({ loading: false }))
  },

  /** 审核列表状态筛选（status 空串 = 全量，服务端未传即不过滤） */
  pickApptFilter(e) {
    const key = e.currentTarget.dataset.key
    if (key === this.data.apptFilter) return
    this.setData({ apptFilter: key })
    this.fetchAppts()
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: e.detail.value })
  },

  onPortPick(e) {
    const idx = Number(e.detail.value)
    this.setData({ portIndex: idx, 'form.port_code': PORTS[idx].key })
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
    // 与详情页（appt.js）统一口径：驳回须填原因，可编辑弹窗承载（第三方审计 P3-8）
    wx.showModal({
      title: '驳回预约',
      editable: true,
      placeholderText: '请填写驳回原因（如：档期安排冲突）',
      success: (res) => {
        if (!res.confirm) return
        const reason = (res.content || '').trim() || '档期安排冲突'
        request({
          url: `/api/v1/port/appts/${id}/reject`,
          method: 'POST',
          data: { reason }
        })
          .then(() => {
            wx.showToast({ title: '已驳回', icon: 'success' })
            this.fetchAppts()
          })
          .catch((err) => wx.showToast({ title: err.message || '操作失败', icon: 'none' }))
      }
    })
  },

  submitForm() {
    // 提交守卫（第三方审计 P2-6：原实现连点会重复创建泊位）
    if (this.data.submitting) return
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
          submitting: false,
          form: { port_code: f.port_code, berth_no: '', berth_name: '', max_dwt: '', max_draft: '', concurrent_capacity: '1' },
          tab: 'list'
        })
        this.fetchBerths()
      })
      .catch((err) => {
        this.setData({ submitting: false })
        wx.showToast({ title: err.message || '创建失败', icon: 'none' })
      })
  }
})
