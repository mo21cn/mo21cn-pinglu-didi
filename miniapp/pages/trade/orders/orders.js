// 订单二级页（v1 · 用户 banner + 状态 Tab + 待办卡 + 订单列表 + 合同弹层）
const { request } = require('../../../utils/request')
const { getUser } = require('../../../utils/auth')

const ORDER_STATUS_LABELS = {
  matched: '待承运',
  shipped: '运输中',
  completed: '已完成',
  cancelled: '已撤单'
}

const STATUS_TABS_BASE = [
  { key: '',          label: '全部' },
  { key: 'matched',   label: '待承运' },
  { key: 'shipped',   label: '运输中' },
  { key: 'completed', label: '已完成' },
  { key: 'cancelled', label: '已撤单' }
]

const TODO_BY_ROLE = {
  shipper: [
    { key: 'verify',  icon: '✅', title: '完成货主认证', sub: '认证后可发布货源、查看船东联系方式', urgent: true,  action: 'verify' },
    { key: 'invoice', icon: '📋', title: '上传营业执照', sub: '企业货主必传 · 个人货主可跳过',       urgent: false, action: 'invoice' },
    { key: 'pay',     icon: '💰', title: '关注平台支付优惠', sub: '运费支付返现 · 限时活动进行中', urgent: false, action: 'pay-promo' },
    { key: 'feedback',icon: '💬', title: '评价已完成订单', sub: '您的评价帮助平台优化匹配',         urgent: false, action: 'feedback' }
  ],
  owner: [
    { key: 'gps',    icon: '📍', title: '开启定位权限',  sub: '避免轨迹丢失，影响接单信誉',     urgent: true,  action: 'gps' },
    { key: 'empty',  icon: '🚢', title: '发布空船信息',  sub: '让货主主动找我订船 · 提高曝光', urgent: false, action: 'empty-ship' },
    { key: 'follow', icon: '🔔', title: '订阅货源推荐',  sub: '接收货源推送 · 运费到账实时通知', urgent: false, action: 'follow' },
    { key: 'archive',icon: '📑', title: '完善船舶档案',  sub: '解锁精准货源推荐 · 提升成单率', urgent: false, action: 'archive' }
  ],
  port: [
    { key: 'appt',    icon: '📅', title: '审核泊位预约',  sub: '今日待确认预约 · 及时锁定档期', urgent: true,  action: 'appt' },
    { key: 'berth',   icon: '⚓', title: '完善泊位信息',  sub: '维护吃水/载重限制 · 避免误派单', urgent: false, action: 'berth' },
    { key: 'report',  icon: '📊', title: '查看周度报表',  sub: '泊位利用率 · 同比环比',         urgent: false, action: 'report' },
    { key: 'safety',  icon: '🛟', title: '安全合规自查',  sub: '港口安全检查清单 · 月度更新',   urgent: false, action: 'safety' }
  ]
}

Page({
  data: {
    role: 'shipper',
    userName: '船友',
    userInitials: '客',
    roleLabel: '货主',
    greeting: '您好',
    stats: { pending: 0 },
    statusLabels: ORDER_STATUS_LABELS,
    statusTabs: STATUS_TABS_BASE.map((t) => ({ ...t, count: 0 })),
    activeStatus: '',
    list: [],
    loading: false,
    todoList: [],
    contract: { show: false, orderId: 0, text: '', risks: [] }
  },

  onShow() {
    const user = getUser()
    const role = (user && user.current_role) || 'shipper'
    const userName = (user && user.nickname) || '船友'
    const initials = this.getInitials(userName)
    const todoList = TODO_BY_ROLE[role] || []
    const greeting = this.timeBasedGreeting()
    this.setData({ role, userName, userInitials: initials, todoList, greeting, roleLabel: this.roleLabelText(role) })
    this.fetchList()
  },

  onPullDownRefresh() {
    this.fetchList()
    wx.stopPullDownRefresh()
  },

  getInitials(name) {
    if (!name) return '客'
    return String(name).trim().charAt(0).toUpperCase()
  },

  timeBasedGreeting() {
    const h = new Date().getHours()
    if (h < 6) return '夜深了'
    if (h < 12) return '早上好'
    if (h < 14) return '中午好'
    if (h < 18) return '下午好'
    return '晚上好'
  },

  switchStatus(e) {
    this.setData({ activeStatus: e.currentTarget.dataset.key })
    this.fetchList()
  },

  fetchList() {
    this.setData({ loading: true })
    const data = { size: 50 }
    if (this.data.activeStatus) data.status = this.data.activeStatus
    request({ url: '/api/v1/order/orders', data })
      .then((res) => {
        const items = res.items || []
        const counts = {
          '': items.length,
          matched: items.filter((o) => o.status === 'matched').length,
          shipped: items.filter((o) => o.status === 'shipped').length,
          completed: items.filter((o) => o.status === 'completed').length,
          cancelled: items.filter((o) => o.status === 'cancelled').length
        }
        const tabs = this.data.statusTabs.map((t) => ({ ...t, count: counts[t.key] || 0 }))
        const pending = items.filter((o) => o.status === 'matched').length
        this.setData({ list: items, statusTabs: tabs, stats: { pending } })
      })
      .catch(() => {})
      .finally(() => this.setData({ loading: false }))
  },

  roleLabelText(role) {
    return { shipper: '货主', owner: '船东', port: '港口方' }[role] || '用户'
  },

  onTodoTap(e) {
    const action = e.currentTarget.dataset.action
    const handlers = {
      verify: () => this.todoTip('前往"我的"完成认证'),
      invoice: () => this.todoTip('上传营业执照'),
      'pay-promo': () => this.todoTip('运费支付优惠详情'),
      feedback: () => this.todoTip('评价订单'),
      gps: () => (wx.openSetting && wx.openSetting()),
      'empty-ship': () => this.goShipperTab('发布货源'),
      follow: () => this.todoTip('货源订阅已开启'),
      archive: () => wx.switchTab({ url: '/pages/owner/owner' }),
      appt: () => wx.showToast({ title: '切到港口工作台审核', icon: 'none' }),
      berth: () => wx.switchTab({ url: '/pages/port/port' }),
      report: () => this.todoTip('周度报表开发中'),
      safety: () => this.todoTip('安全自查清单')
    }
    const fn = handlers[action]
    if (fn) fn()
    else this.todoTip('该功能开发中')
  },

  goShipperTab() {
    wx.switchTab({ url: '/pages/shipper/shipper' })
  },

  todoTip(msg) {
    wx.showToast({ title: msg, icon: 'none', duration: 1800 })
  },

  onPay(e) {
    const id = Number(e.currentTarget.dataset.id)
    const order = this.data.list.find((o) => o.id === id)
    if (!order) return

    request({ url: `/api/v1/payment/payments/order/${id}`, silent: true })
      .catch((err) => {
        if (String(err.message).indexOf('支付单') !== -1 || String(err.message).indexOf('订单') !== -1) {
          return request({
            url: '/api/v1/payment/payments',
            method: 'POST',
            data: { order_id: id, channel: 'mock' }
          })
        }
        throw err
      })
      .then((pay) => {
        if (pay.status === 'paid') {
          return wx.showToast({ title: '该订单已支付', icon: 'none' })
        }
        if (pay.status !== 'pending') {
          return wx.showToast({ title: '支付单已' + (pay.status === 'refunded' ? '退款' : '关闭'), icon: 'none' })
        }
        wx.showModal({
          title: '确认支付运费',
          content: `支付金额：¥ ${pay.amount}`,
          confirmText: '支付',
          success: (r) => {
            if (!r.confirm) return
            request({
              url: `/api/v1/payment/payments/${pay.id}/mock-pay`,
              method: 'POST',
              data: {}
            })
              .then(() => {
                wx.showToast({ title: '支付成功', icon: 'success' })
                this.fetchList()
              })
              .catch(() => {})
          }
        })
      })
      .catch(() => {})
  },

  onShip(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '确认启运',
      content: '启运后进入履约期，订单不可撤单',
      success: (r) => {
        if (!r.confirm) return
        request({ url: `/api/v1/order/orders/${id}/ship`, method: 'POST' })
          .then(() => {
            wx.showToast({ title: '已启运', icon: 'success' })
            this.fetchList()
          })
          .catch(() => {})
      }
    })
  },

  onComplete(e) {
    const id = e.currentTarget.dataset.id
    request({ url: `/api/v1/order/orders/${id}/complete`, method: 'POST' })
      .then(() => {
        wx.showToast({ title: '已签收，订单完成', icon: 'success' })
        this.fetchList()
      })
      .catch(() => {})
  },

  onCancel(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '确认撤单',
      content: '已支付运费将自动原路退款，货源释放回撮合池',
      editable: false,
      success: (r) => {
        if (!r.confirm) return
        request({
          url: `/api/v1/order/orders/${id}/cancel`,
          method: 'POST',
          data: { reason: '小程序端撤单' }
        })
          .then(() => {
            wx.showToast({ title: '已撤单', icon: 'success' })
            this.fetchList()
          })
          .catch(() => {})
      }
    })
  },

  onContract(e) {
    const id = e.currentTarget.dataset.id
    wx.showLoading({ title: '生成中...', mask: true })
    request({ url: '/api/v1/agent/contract/generate', method: 'POST', data: { order_id: id } })
      .then((res) => {
        wx.hideLoading()
        this.setData({
          contract: { show: true, orderId: id, text: res.contract_text || '', risks: res.risks || [] }
        })
      })
      .catch(() => wx.hideLoading())
  },

  closeContract() {
    this.setData({ 'contract.show': false })
  }
})
