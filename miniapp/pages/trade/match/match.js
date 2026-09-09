// 撮合页：货主为货源找候选船（可选船下单）/ 船东为船找候选货源（只读）
// 跳转参数：mode=cargo&refId=<货源id> 或 mode=ship&refId=<船舶id>
const { request } = require('../../../utils/request')

const SHIP_TYPE_LABELS = {
  bulk: '散货船',
  general: '件杂货船',
  container: '集装箱船',
  tanker: '液货船'
}

const PORT_LABELS = {
  NNG: '南宁', GGU: '贵港', WUZ: '梧州', BIN: '来宾', LZH: '柳州',
  BSZ: '百色', CHZ: '崇左', GXL: '桂林', HEZ: '贺州', YUL: '玉林',
  QNZ: '钦州', FCG: '防城港', BHZ: '北海'
}

Page({
  data: {
    mode: 'cargo',        // cargo=货主找船 ship=船东找货
    refId: 0,
    title: '智能撮合',
    items: [],
    filterStats: [],
    total: 0,
    loading: false,
    // 下单弹窗
    orderModal: false,
    target: null,         // 选中的候选船
    freightPrice: ''
  },

  onLoad(options) {
    const mode = options.mode === 'ship' ? 'ship' : 'cargo'
    const refId = Number(options.refId || 0)
    this.setData({
      mode,
      refId,
      title: mode === 'cargo' ? '为货源找船' : '为船找货源'
    })
    wx.setNavigationBarTitle({ title: this.data.title })
    this.fetch()
  },

  /** 调撮合引擎（确定性内核：硬约束过滤 + 多目标评分） */
  fetch() {
    if (!this.data.refId) return
    this.setData({ loading: true })
    const url = this.data.mode === 'cargo'
      ? `/api/v1/match/cargos/${this.data.refId}/ships`
      : `/api/v1/match/ships/${this.data.refId}/cargos`
    request({ url, method: 'POST' })
      .then((res) => {
        const items = (res.items || []).map((it) => this.decorate(it))
        const filterStats = Object.keys(res.filter_stats || {})
          .map((k) => `${this.statLabel(k)} ${res.filter_stats[k]}`)
        this.setData({ items, total: res.total || 0, filterStats })
      })
      .catch(() => {})
      .finally(() => this.setData({ loading: false }))
  },

  /** 渲染字段装饰 */
  decorate(it) {
    if (this.data.mode === 'cargo') {
      return {
        ...it,
        ship_type_label: SHIP_TYPE_LABELS[it.ship_type] || it.ship_type,
        score_text: it.score.toFixed(1)
      }
    }
    return {
      ...it,
      cargo_type_label: it.cargo_type,
      origin_label: PORT_LABELS[it.origin_port] || it.origin_port,
      dest_label: PORT_LABELS[it.dest_port] || it.dest_port,
      score_text: it.score.toFixed(1)
    }
  },

  statLabel(key) {
    const map = {
      ship_not_verified: '未过审',
      cert_expired: '证书过期',
      deadweight_insufficient: '载重不足',
      type_incompatible: '船型不符',
      cargo_not_published: '货未发布'
    }
    return map[key] || key
  },

  // ---- 货主下单流程 ----

  /** 点候选船「下单」→ 弹运费确认 */
  onTapCandidate(e) {
    if (this.data.mode !== 'cargo') return
    const idx = Number(e.currentTarget.dataset.index)
    const target = this.data.items[idx]
    this.setData({ orderModal: true, target, freightPrice: '' })
  },

  onFreightInput(e) {
    this.setData({ freightPrice: e.detail.value })
  },

  closeModal() {
    this.setData({ orderModal: false })
  },

  /** 确认下单：POST /order/orders（出单前置复用撮合硬约束） */
  confirmOrder() {
    const t = this.data.target
    const price = this.data.freightPrice
    if (!t) return
    if (!price || Number(price) <= 0) {
      return wx.showToast({ title: '请填写有效运费', icon: 'none' })
    }
    request({
      url: '/api/v1/order/orders',
      method: 'POST',
      data: {
        cargo_id: this.data.refId,
        ship_id: t.ship_id,
        freight_price: Number(price)
      }
    })
      .then(() => {
        this.setData({ orderModal: false })
        wx.showToast({ title: '下单成功', icon: 'success' })
        setTimeout(() => {
          wx.redirectTo({ url: '/pages/trade/orders/orders' })
        }, 800)
      })
      .catch(() => {})
  }
})
