// 06 我的订单（底栏二级页）
// 统计行（全部/待承运/运输中/已完成/已撤单）+ 待处理事项 + 订单列表
// 三级页入口：
//   合同：卡片「查看合同」→ pages/trade/contract（完整版）；长按订单卡 → 弹层快速预览
//   支付：卡片「去支付 / 支付详情」→ pages/trade/payment（单据状态机 + 资金留痕）
// 注：订单接口已内嵌 cargo/ship 摘要（后端 OrderOut.cargo/ship）→ 路线/货名/船名直接可用，
//     船东视角也不再有「货源 #id」降级；仅当摘要缺失时才回退到列表接口补齐。
const { request } = require('../../../utils/request')
const { getUser } = require('../../../utils/auth')
const { syncTabBar } = require('../../../utils/tabbar')

const ORDER_STATUS_LABELS = {
  matched: '待承运',
  shipped: '运输中',
  completed: '已完成',
  cancelled: '已撤单'
}

// 业务常量唯一来源（第三方审计 P3-4：原为页面本地复制）
const { PORT_LABELS } = require('../../../utils/constants')

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
    error: '',
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
  /**
   * 订单为主资源（失败即报错）。订单接口已内嵌 cargo/ship 摘要 → 常规路径**零富化请求**。
   *
   * 仅在摘要缺失时（旧后端 / 异常数据）才回退到「自己这一侧」的列表接口补齐：
   * ⚠️ `/cargo/shipments` 只允许货主、`/ship/registry` 只允许船东（后端逐端点校验
   *    current_role）。此前无条件并发两个接口，必然有一个 403 —— 控制台红错、
   *    且是纯粹的无效请求。故按角色二选一。
   */
  fetchAll() {
    this.setData({ loading: true, error: '' })
    request({ url: '/api/v1/order/orders', data: { size: 100 } })
      .then((orders) => {
        const items = orders.items || []
        // 常规路径：订单自带摘要 → 不发富化请求
        if (!items.some((o) => !o.cargo || !o.ship)) {
          this.setData({ rawList: items.map((o) => this.decorate(o)) })
          this.applyView()
          return null
        }
        const isOwner = this.data.role === 'owner'
        const enrichUrl = isOwner ? '/api/v1/ship/registry' : '/api/v1/cargo/shipments'
        return request({ url: enrichUrl, data: { size: 100 } })
          .catch(() => ({ items: [] }))
          .then((own) => {
            const cargoMap = {}
            const shipMap = {}
            ;((own || {}).items || []).forEach((it) => {
              if (isOwner) shipMap[it.id] = it
              else cargoMap[it.id] = it
            })
            this.setData({ rawList: items.map((o) => this.decorate(o, cargoMap, shipMap)) })
            this.applyView()
          })
      })
      .catch((err) => {
        this.setData({ error: (err && err.message) || '订单加载失败，请稍后重试' })
      })
      .finally(() => this.setData({ loading: false }))
  },

  /** 订单卡片展示字段装饰：订单内嵌摘要 > 联动列表 > 降级 #id */
  decorate(o, cargoMap, shipMap) {
    const c = o.cargo || ((cargoMap && cargoMap[o.cargo_id]) || null)
    const s = o.ship || ((shipMap && shipMap[o.ship_id]) || null)
    const originLabel = c ? (PORT_LABELS[c.origin_port] || c.origin_port) : '货源'
    const destLabel = c ? (PORT_LABELS[c.dest_port] || c.dest_port) : ('#' + o.cargo_id)
    let weightText = '—'
    if (c) weightText = c.weight_t + ' 吨'
    else if (s) weightText = '载重 ' + s.deadweight_t + ' 吨'
    let dateText = ''
    if (c) dateText = c.expect_date + ' 装货'
    else if (o.matched_at) dateText = String(o.matched_at).slice(0, 10) + ' 成交'
    // 摘要对象不再往下传（避免 setData 体积无谓翻倍）
    const rest = Object.assign({}, o)
    delete rest.cargo
    delete rest.ship
    return {
      ...rest,
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

  /** 「去支付 / 支付详情」→ 支付详情三级页（单据状态机 + 资金留痕时间轴） */
  onPayPage(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!id) return
    wx.navigateTo({ url: '/pages/trade/payment/payment?order_id=' + id })
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
          .catch((e) => console.warn('[swallowed]', (e && e.message) || e))
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
          .catch((e) => console.warn('[swallowed]', (e && e.message) || e))
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
          .catch((e) => console.warn('[swallowed]', (e && e.message) || e))
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
  }
})
