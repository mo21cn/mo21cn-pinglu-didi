// 支付详情（L3 三级页）· 支付域（F7）
// 确定性内核：状态机 pending → paid / closed；paid → refunded（撤单联动）
// 入口：订单卡「去支付」（待支付）· 订单卡「支付详情」（已支付/已退款/已关闭）
// 跳转参数：order_id=xx
//
// 页面主线：一张单据的完整资金留痕（创建锁价 → 支付 → 退款/关闭），
// 用来展示「资金流与订单流的一致性由确定性内核驱动」——退款不开放独立端点，
// 只由撤单联动，避免两条状态机各说各话。
const { request } = require('../../../utils/request')
const { getUser } = require('../../../utils/auth')

const CHANNEL_LABELS = {
  mock: '模拟支付（联调期）',
  wechat_mp: '微信支付'
}

const PAY_STATUS = {
  pending: { label: '待支付', chip: 'chip-warn' },
  paid: { label: '已支付', chip: 'chip-success' },
  refunded: { label: '已退款', chip: 'chip-purple' },
  closed: { label: '已关闭', chip: 'chip-muted' }
}

const ORDER_STATUS = {
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

function fmtTime(t) {
  if (!t) return ''
  return String(t).slice(0, 19).replace('T', ' ')
}

Page({
  data: {
    orderId: 0,
    role: 'shipper',
    loading: true,
    error: '',
    // 订单摘要
    order: null,
    orderStatusLabel: '',
    cargoText: '—',
    shipText: '—',
    // 支付单
    paid: null,
    amountPrefix: '¥',
    amountText: '0.00',
    statusLabel: '未发起',
    statusChip: 'chip-muted',
    channelLabel: '—',
    timeline: [],
    // 操作态
    canPay: false,
    canCreate: false,
    barNote: ''
  },

  onLoad(options) {
    const orderId = Number((options && options.order_id) || 0)
    const user = getUser()
    this.setData({ orderId, role: (user && user.current_role) || 'shipper' })
    wx.setNavigationBarTitle({ title: '支付详情' })
    if (!orderId) {
      this.setData({ loading: false, error: '缺少订单参数，无法加载支付详情' })
      return
    }
    this.fetch()
  },

  onPullDownRefresh() {
    if (this.data.orderId) this.fetch()
    wx.stopPullDownRefresh()
  },

  /** 三路取数：订单（必需）· 支付单（可无）· 货源/船队（用于线路富化，拿不到则降级） */
  fetch() {
    this.setData({ loading: true, error: '' })
    const safe = (url) => request({ url, data: { size: 100 } }).catch(() => ({ items: [] }))
    Promise.all([
      request({ url: '/api/v1/order/orders/' + this.data.orderId }),
      request({ url: '/api/v1/payment/payments/order/' + this.data.orderId, silent: true }).catch(() => null),
      safe('/api/v1/cargo/shipments'),
      safe('/api/v1/ship/registry')
    ])
      .then((res) => {
        const order = res[0]
        const payment = res[1]
        const cargoMap = {}
        ;(res[2].items || []).forEach((c) => { cargoMap[c.id] = c })
        const shipMap = {}
        ;(res[3].items || []).forEach((s) => { shipMap[s.id] = s })
        this.apply(order, payment, cargoMap, shipMap)
      })
      .catch((err) => {
        this.setData({ error: (err && err.message) || '加载失败，请稍后重试' })
      })
      .finally(() => this.setData({ loading: false }))
  },

  apply(order, payment, cargoMap, shipMap) {
    const role = this.data.role
    const c = cargoMap[order.cargo_id]
    const s = shipMap[order.ship_id]

    const cargoText = c
      ? c.cargo_name + ' · ' + c.weight_t + ' 吨 · ' +
        (PORT_LABELS[c.origin_port] || c.origin_port) + ' → ' + (PORT_LABELS[c.dest_port] || c.dest_port)
      : '货源 #' + order.cargo_id
    const shipText = s ? s.ship_name + ' · 载重 ' + s.deadweight_t + ' 吨' : '船舶 #' + order.ship_id

    const meta = payment ? PAY_STATUS[payment.status] || { label: payment.status, chip: 'chip-muted' } : null
    // 尚未发起支付：仅货主 + 订单待承运 + 运费已议定 → 可发起
    const canCreate = !payment && role === 'shipper' && order.status === 'matched' && order.freight_price !== null

    let amount = '面议'
    let amountPrefix = ''
    if (payment) {
      amount = Number(payment.amount).toFixed(2)
      amountPrefix = '¥'
    } else if (order.freight_price !== null && order.freight_price !== undefined) {
      amount = Number(order.freight_price).toFixed(2)
      amountPrefix = '¥'
    }

    this.setData({
      order,
      orderStatusLabel: ORDER_STATUS[order.status] || order.status,
      cargoText,
      shipText,
      paid: payment,
      amountText: amount,
      amountPrefix,
      statusLabel: meta ? meta.label : '未发起',
      statusChip: meta ? meta.chip : 'chip-muted',
      channelLabel: payment ? CHANNEL_LABELS[payment.channel] || payment.channel : '—',
      timeline: this.buildTimeline(payment),
      canPay: !!(payment && payment.status === 'pending' && role === 'shipper'),
      canCreate,
      barNote: this.buildBarNote(payment, order, canCreate, role)
    })
  },

  buildBarNote(payment, order, canCreate, role) {
    if (payment) {
      if (payment.status === 'pending') {
        return role === 'shipper'
          ? '联调期以模拟回调替代微信支付，点击后资金状态置为已支付'
          : '等待货主完成支付，运费支付成功后方可启运'
      }
      if (payment.status === 'paid') return '如需退款，请在订单页发起撤单，系统将自动原路全额退款'
      if (payment.status === 'refunded') return '该支付单已全额退款，资金流已闭环'
      return '该支付单已随撤单关闭，未产生资金流'
    }
    if (canCreate) return '发起支付后生成待支付单据，运费金额即被锁定'
    if (role !== 'shipper') return '仅货主（付款方）可发起支付'
    if (order.status === 'cancelled') return '订单已撤单，不可发起支付'
    if (order.status === 'shipped') return '订单已启运，当前状态不可发起支付'
    if (order.status === 'completed') return '订单已完成，无需发起支付'
    if (order.freight_price === null) return '订单运费为面议，须先与船东议定运价再支付'
    return '当前状态不可发起支付'
  },

  /** 资金状态留痕时间轴：已发生=实心高亮，待发生=灰色预告 */
  buildTimeline(p) {
    if (!p) return []
    const items = [{
      key: 'created',
      title: '支付单创建 · 锁定运费',
      time: fmtTime(p.created_at),
      note: '渠道 ' + (CHANNEL_LABELS[p.channel] || p.channel),
      done: true
    }]

    if (p.status === 'paid' || p.status === 'refunded') {
      items.push({
        key: 'paid',
        title: '支付成功 · 运费进入平台待结算',
        time: fmtTime(p.paid_at),
        note: p.transaction_no ? '流水号 ' + p.transaction_no : '',
        done: true
      })
    } else if (p.status === 'pending') {
      items.push({
        key: 'paid',
        title: '等待支付成功',
        time: '',
        note: '收到模拟回调 / 微信支付通知后置为已支付',
        done: false
      })
    }

    if (p.status === 'refunded') {
      items.push({
        key: 'refunded',
        title: '全额退款 · 已原路退回',
        time: fmtTime(p.refunded_at),
        note: p.refund_no ? '退款单号 ' + p.refund_no : '',
        done: true
      })
    } else if (p.status === 'closed') {
      items.push({
        key: 'closed',
        title: '支付单关闭 · 未产生资金流',
        time: fmtTime(p.closed_at),
        note: '由撤单联动关闭',
        done: true
      })
    } else if (p.status === 'paid') {
      items.push({
        key: 'settle',
        title: '结算分账 / 船东提现',
        time: '',
        note: '后续版本能力，当前非阻断',
        done: false
      })
    } else if (p.status === 'pending') {
      items.push({
        key: 'close',
        title: '超时未付自动关单',
        time: '',
        note: '超时关单定时任务为后续版本能力',
        done: false
      })
    }

    return items
  },

  // ---- 操作 ----
  /** 发起支付：POST /payment/payments（仅 matched 订单、运费已议定） */
  onCreate() {
    wx.showLoading({ title: '创建中...', mask: true })
    request({
      url: '/api/v1/payment/payments',
      method: 'POST',
      data: { order_id: this.data.orderId, channel: 'mock' }
    })
      .then(() => {
        wx.hideLoading()
        wx.showToast({ title: '支付单已创建', icon: 'success' })
        this.fetch()
      })
      .catch(() => wx.hideLoading())
  },

  /** 模拟支付成功：POST /payment/payments/{id}/mock-pay（幂等，重复回调不重复记账） */
  onMockPay() {
    const p = this.data.paid
    if (!p) return
    wx.showModal({
      title: '确认支付运费',
      content: '支付金额：¥ ' + Number(p.amount).toFixed(2) + '\n联调期模拟回调，不产生真实资金流',
      confirmText: '确认支付',
      success: (r) => {
        if (!r.confirm) return
        request({
          url: '/api/v1/payment/payments/' + p.id + '/mock-pay',
          method: 'POST',
          data: {}
        })
          .then(() => {
            wx.showToast({ title: '支付成功', icon: 'success' })
            this.fetch()
          })
          .catch(() => {})
      }
    })
  },

  onPlaceholder(e) {
    const name = (e.currentTarget && e.currentTarget.dataset.name) || '该能力'
    wx.showToast({ title: name + '即将开放', icon: 'none' })
  },

  /** 回订单页（tabBar 页须用 switchTab） */
  goOrder() {
    wx.switchTab({ url: '/pages/trade/orders/orders' })
  }
})
