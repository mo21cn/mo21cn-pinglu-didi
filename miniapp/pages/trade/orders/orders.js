// 06 我的订单（底栏二级页）
// 统计行（全部/待承运/运输中/已完成/已撤单）+ 待处理事项 + 订单列表
// 合同双入口：卡片「查看合同」→ 三级页 pages/trade/contract（完整版）；长按订单卡 → 弹层快速预览
// 注：订单接口仅返回 cargo_id/ship_id → 用「我的货源 / 我的船队」列表做 enrich 展示路线。
//     若拿不到货源详情（如船东视角），降级显示「货源 #id」。
const { request } = require('../../../utils/request')
const { getUser } = require('../../../utils/auth')
const { syncTabBar } = require('../../../utils/tabbar')

const ORDER_STATUS_LABELS = {
  matched: '待承运',
  shipped: '运输中',
  completed: '已完成',
  cancelled: '已撤单'
}

const PORT_LABELS = {
  NNG: '南宁', GGU: '贵港', WUZ: '梧州', BIN: '来宾', LZH: '柳州',
  BSZ: '百色', CHZ: '崇左', GXL: '桂林', HEZ: '贺州', YUL: '玉林',
  QNZ: '钦州', FCG: '防城港', BHZ: '北海'
}

const TODO_BY_ROLE = {
  shipper: [
    { key: 'pay',     icon: '💰', title: '待支付订单', status: 'matched' },
    { key: 'receive', icon: '✅', title: '待确认收货', status: 'shipped' }
  ],
  owner: [
    { key: 'ship',    icon: '🚢', title: '待启运订单', status: 'matched' },
    { key: 'transit', icon: '📍', title: '运输中订单', status: 'shipped' }
  ],
  port: []
}

Page({
  data: {
    statusBarHeight: 20,
    role: 'shipper',
    activeKey: '',
    activeLabel: '',
    statusLabels: ORDER_STATUS_LABELS,
    stats: [],
    todoList: [],
    rawList: [],
    list: [],
    loading: false,
    contract: { show: false, orderId: 0, text: '', risks: [] }
  },

  onLoad() {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })
  },

  onShow() {
    syncTabBar(this)
    const user = getUser()
    const role = (user && user.current_role) || 'shipper'
    this.setData({ role })
    this.fetchAll()
  },

  onPullDownRefresh() {
    this.fetchAll()
    wx.stopPullDownRefresh()
  },

  // ---- 数据 ----
  fetchAll() {
    this.setData({ loading: true })
    const safe = (url) => request({ url, data: { size: 100 } }).catch(() => ({ items: [] }))
    Promise.all([
      safe('/api/v1/order/orders'),
      safe('/api/v1/cargo/shipments'),
      safe('/api/v1/ship/registry')
    ])
      .then(([orders, cargos, ships]) => {
        const cargoMap = {}
        ;(cargos.items || []).forEach((c) => { cargoMap[c.id] = c })
        const shipMap = {}
        ;(ships.items || []).forEach((s) => { shipMap[s.id] = s })

        const rawList = (orders.items || []).map((o) => this.decorate(o, cargoMap, shipMap))
        this.setData({ rawList })
        this.applyView()
      })
      .catch(() => {})
      .finally(() => this.setData({ loading: false }))
  },

  /** 订单卡片展示字段装饰（拿不到货源详情时降级） */
  decorate(o, cargoMap, shipMap) {
    const c = cargoMap[o.cargo_id]
    const s = shipMap[o.ship_id]
    const originLabel = c ? (PORT_LABELS[c.origin_port] || c.origin_port) : '货源'
    const destLabel = c ? (PORT_LABELS[c.dest_port] || c.dest_port) : ('#' + o.cargo_id)
    let weightText = '—'
    if (c) weightText = c.weight_t + ' 吨'
    else if (s) weightText = '载重 ' + s.deadweight_t + ' 吨'
    let dateText = ''
    if (c) dateText = c.expect_date + ' 装货'
    else if (o.matched_at) dateText = String(o.matched_at).slice(0, 10) + ' 成交'
    return {
      ...o,
      origin_label: originLabel,
      dest_label: destLabel,
      cargo_name: c ? c.cargo_name : ('货源 #' + o.cargo_id),
      ship_name: s ? s.ship_name : ('船舶 #' + o.ship_id),
      weight_text: weightText,
      date_text: dateText
    }
  },

  applyView() {
    const raw = this.data.rawList
    const stats = [
      { key: '',          label: '全部',   count: raw.length },
      { key: 'matched',   label: '待承运', count: raw.filter((o) => o.status === 'matched').length },
      { key: 'shipped',   label: '运输中', count: raw.filter((o) => o.status === 'shipped').length },
      { key: 'completed', label: '已完成', count: raw.filter((o) => o.status === 'completed').length },
      { key: 'cancelled', label: '已撤单', count: raw.filter((o) => o.status === 'cancelled').length }
    ]
    const defs = TODO_BY_ROLE[this.data.role] || []
    const todoList = defs
      .map((d) => ({ ...d, count: raw.filter((o) => o.status === d.status).length }))
      .filter((d) => d.count > 0)
    const activeKey = this.data.activeKey
    const list = activeKey ? raw.filter((o) => o.status === activeKey) : raw
    this.setData({
      stats,
      todoList,
      list,
      activeLabel: ORDER_STATUS_LABELS[activeKey] || ''
    })
  },

  switchStat(e) {
    const key = e.currentTarget.dataset.key
    this.setData({ activeKey: this.data.activeKey === key ? '' : key }, () => this.applyView())
  },

  resetFilter() {
    if (!this.data.activeKey) return
    this.setData({ activeKey: '' }, () => this.applyView())
  },

  onTodoTap(e) {
    const key = e.currentTarget.dataset.key
    const def = (TODO_BY_ROLE[this.data.role] || []).find((d) => d.key === key)
    if (!def) return
    this.setData({ activeKey: def.status }, () => this.applyView())
  },

  // ---- 订单操作 ----
  findOrder(id) {
    return this.data.rawList.find((o) => o.id === Number(id))
  },

  onPay(e) {
    const id = Number(e.currentTarget.dataset.id)
    const order = this.findOrder(id)
    if (!order) return

    request({ url: `/api/v1/payment/payments/order/${id}`, silent: true })
      .catch((err) => {
        const msg = String(err.message)
        if (msg.indexOf('支付单') !== -1 || msg.indexOf('订单') !== -1) {
          return request({
            url: '/api/v1/payment/payments',
            method: 'POST',
            data: { order_id: id, channel: 'mock' }
          })
        }
        throw err
      })
      .then((pay) => {
        if (pay.status === 'paid') return wx.showToast({ title: '该订单已支付', icon: 'none' })
        if (pay.status !== 'pending') return wx.showToast({ title: '支付单已关闭', icon: 'none' })
        wx.showModal({
          title: '确认支付运费',
          content: `支付金额：¥ ${pay.amount}`,
          confirmText: '支付',
          success: (r) => {
            if (!r.confirm) return
            request({ url: `/api/v1/payment/payments/${pay.id}/mock-pay`, method: 'POST', data: {} })
              .then(() => {
                wx.showToast({ title: '支付成功', icon: 'success' })
                this.fetchAll()
              })
              .catch(() => {})
          }
        })
      })
      .catch(() => {})
  },

  onShip(e) {
    const id = Number(e.currentTarget.dataset.id)
    wx.showModal({
      title: '确认启运',
      content: '启运后进入履约期，订单不可撤单',
      success: (r) => {
        if (!r.confirm) return
        request({ url: `/api/v1/order/orders/${id}/ship`, method: 'POST' })
          .then(() => {
            wx.showToast({ title: '已启运', icon: 'success' })
            this.fetchAll()
          })
          .catch(() => {})
      }
    })
  },

  onComplete(e) {
    const id = Number(e.currentTarget.dataset.id)
    wx.showModal({
      title: '确认收货',
      content: '确认后订单完成，运费结算给船东',
      success: (r) => {
        if (!r.confirm) return
        request({ url: `/api/v1/order/orders/${id}/complete`, method: 'POST' })
          .then(() => {
            wx.showToast({ title: '已确认收货', icon: 'success' })
            this.fetchAll()
          })
          .catch(() => {})
      }
    })
  },

  onCancel(e) {
    const id = Number(e.currentTarget.dataset.id)
    wx.showModal({
      title: '确认撤单',
      content: '已支付运费将自动原路退款，货源释放回撮合池',
      success: (r) => {
        if (!r.confirm) return
        request({
          url: `/api/v1/order/orders/${id}/cancel`,
          method: 'POST',
          data: { reason: '小程序端撤单' }
        })
          .then(() => {
            wx.showToast({ title: '已撤单', icon: 'success' })
            this.fetchAll()
          })
          .catch(() => {})
      }
    })
  },

  onDetail(e) {
    const id = Number(e.currentTarget.dataset.id)
    const o = this.findOrder(id)
    if (!o) return
    wx.showModal({
      title: `订单 #${o.id}`,
      content: `状态：${ORDER_STATUS_LABELS[o.status] || o.status}\n路线：${o.origin_label} → ${o.dest_label}\n货物：${o.cargo_name} · ${o.weight_text}\n船舶：${o.ship_name}\n运费：${o.freight_price ? '¥ ' + o.freight_price : '面议'}\n成交时间：${String(o.matched_at || '').slice(0, 19).replace('T', ' ')}`,
      showCancel: false,
      confirmText: '知道了'
    })
  },

  onContract(e) {
    const id = Number(e.currentTarget.dataset.id)
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

  /** 「查看合同」→ 合同三级页（L3 完整版：Agent 头卡 + 风险卡 + 正文 + 签署占位） */
  onContractPage(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!id) return
    wx.navigateTo({ url: '/pages/trade/contract/contract?order_id=' + id })
  },

  /** 长按订单卡 → 弹层快速预览（与三级页同源同接口，仅展示粒度更轻） */
  onPreviewContract(e) {
    this.onContract(e)
  },

  closeContract() {
    this.setData({ 'contract.show': false })
  },

  onBell() {
    wx.showToast({ title: '消息中心开发中', icon: 'none' })
  }
})
