// 订单列表页：角色视角的交易操作中枢（下单→支付→启运→签收/撤单）
const { request } = require('../../../utils/request')
const { getUser } = require('../../../utils/auth')

const ORDER_STATUS = {
  matched: '已撮合',
  shipped: '运输中',
  completed: '已完成',
  cancelled: '已撤单'
}

Page({
  data: {
    role: 'shipper',
    list: [],
    total: 0,
    statusLabels: ORDER_STATUS,
    statusFilter: [
      { key: '', label: '全部' },
      { key: 'matched', label: '待承运' },
      { key: 'shipped', label: '运输中' },
      { key: 'completed', label: '已完成' },
      { key: 'cancelled', label: '已撤单' }
    ],
    activeStatus: '',
    loading: false,
    // 合同预览弹层
    contract: { show: false, orderId: 0, text: '', risks: [] }
  },

  onShow() {
    const user = getUser()
    this.setData({ role: (user && user.current_role) || 'shipper' })
    this.fetchList()
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
        this.setData({ list: res.items || [], total: res.total || 0 })
      })
      .catch(() => {})
      .finally(() => this.setData({ loading: false }))
  },

  // ---- 货主：支付（查找或创建支付单 → 模拟支付成功）----

  onPay(e) {
    const id = Number(e.currentTarget.dataset.id)
    const order = this.data.list.find((o) => o.id === id)
    if (!order) return

    // 先查支付单（404 静默），无则创建（一订单一支付单，防重复由后端保证）
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
        // 确认支付（模拟渠道，真实微信支付接入点）
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

  // ---- 订单操作 ----

  onShip(e) {
    // 船东启运：matched → shipped
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
    // 货主签收：shipped → completed
    const id = e.currentTarget.dataset.id
    request({ url: `/api/v1/order/orders/${id}/complete`, method: 'POST' })
      .then(() => {
        wx.showToast({ title: '已签收，订单完成', icon: 'success' })
        this.fetchList()
      })
      .catch(() => {})
  },

  onCancel(e) {
    // 撤单：matched → cancelled（已支付自动退款，待支付自动关闭）
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

  // ---- 智能合同：草稿生成 + 风险提示（草稿不具法律效力） ----
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
