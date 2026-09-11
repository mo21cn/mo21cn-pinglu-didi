// 撮合结果（L3 三级页）· 撮合引擎（F5）
// 确定性内核：硬约束过滤（CSP）+ 多目标加权评分，纯函数、不经 LLM、结果可复现
// 跳转参数：mode=cargo&refId=<货源id>（货主找船，可选船下单）
//           mode=ship&refId=<船舶id>（船东找货，只读——出单须由货主发起）
// 入口：①货主端「我的货源」「查看匹配船源」②发布货源成功后自动跳转 ③船东端「我的船队」找货
const { request } = require('../../../utils/request')

// 业务常量唯一来源（第三方审计 P3-4：原为页面本地复制）
const { SHIP_TYPE_LABELS, PORT_LABELS } = require('../../../utils/constants')

const CARGO_TYPE_LABELS = {
  bulk: '散货',
  general: '件杂货',
  container: '集装箱',
  tanker: '液货'
}

// 评分维度（与 engine.py 的 W_* 权重一一对应，满分合计 100）
const DIMS = [
  { key: 'load_utilization', name: '载重利用率', max: 40 },
  { key: 'type_fit', name: '船型适配', max: 20 },
  { key: 'home_port', name: '船籍港就近', max: 20 },
  { key: 'cert_margin', name: '证书余量', max: 20 }
]

const SORTS = [
  { key: 'score', label: '综合评分' },
  { key: 'load_utilization', label: '载重利用率' },
  { key: 'cert_margin', label: '证书余量' },
  { key: 'home_port', label: '船籍港就近' }
]

// 硬约束拦截原因（filter_stats 键 → 中文）
const STAT_LABELS = {
  ship_not_verified: '船舶未过审',
  cert_expired: '证书未覆盖装货日',
  deadweight_insufficient: '载重不足',
  type_incompatible: '船型货类不兼容',
  cargo_not_published: '货源未发布',
  draft_insufficient: '吃水不足'
}

Page({
  data: {
    mode: 'cargo', // cargo=货主找船 ship=船东找货
    refId: 0,
    title: '智能撮合',
    loading: true,
    error: '',
    items: [],
    total: 0,
    filterStats: [],
    sorts: SORTS,
    sortKey: 'score',
    sortLabel: '综合评分',
    // 下单弹窗
    orderModal: false,
    target: null,
    freightPrice: ''
  },

  onLoad(options) {
    const mode = options && options.mode === 'ship' ? 'ship' : 'cargo'
    const refId = Number((options && options.refId) || 0)
    this.setData({ mode, refId, title: mode === 'cargo' ? '为货源找船' : '为船找货源' })
    wx.setNavigationBarTitle({ title: '智能撮合' })
    if (!refId) {
      this.setData({ loading: false, error: '缺少撮合对象参数，无法计算候选' })
      return
    }
    this.fetch()
  },

  onPullDownRefresh() {
    if (this.data.refId) this.fetch()
    wx.stopPullDownRefresh()
  },

  /** 调撮合引擎（硬约束过滤 + 四项加权评分） */
  fetch() {
    this.setData({ loading: true, error: '' })
    const url = this.data.mode === 'cargo'
      ? '/api/v1/match/cargos/' + this.data.refId + '/ships'
      : '/api/v1/match/ships/' + this.data.refId + '/cargos'
    request({ url, method: 'POST' })
      .then((res) => {
        this._raw = (res.items || []).map((it) => this.decorate(it))
        const stats = res.filter_stats || {}
        const filterStats = Object.keys(stats)
          .map((k) => ({ key: k, label: STAT_LABELS[k] || k, count: stats[k] }))
          .sort((a, b) => b.count - a.count)
        this.setData({ total: res.total || 0, filterStats }, () => this.applySort())
      })
      .catch((err) => {
        this.setData({ error: (err && err.message) || '撮合计算失败，请稍后重试' })
      })
      .finally(() => this.setData({ loading: false }))
  },

  /** 渲染字段装饰：四项得分拆解 → 进度条模型 */
  decorate(it) {
    const b = it.breakdown || {}
    const dims = DIMS.map((d) => {
      const v = Number(b[d.key] || 0)
      const ratio = d.max ? Math.min(v / d.max, 1) : 0
      return {
        key: d.key,
        name: d.name,
        value: v.toFixed(1),
        max: d.max,
        pct: Math.round(ratio * 100)
      }
    })
    const base = { ...it, score_text: Number(it.score).toFixed(1), dims }
    if (this.data.mode === 'cargo') {
      return {
        ...base,
        ship_type_label: SHIP_TYPE_LABELS[it.ship_type] || it.ship_type,
        home_port_label: PORT_LABELS[it.home_port] || it.home_port || '—'
      }
    }
    return {
      ...base,
      cargo_type_label: CARGO_TYPE_LABELS[it.cargo_type] || it.cargo_type,
      origin_label: PORT_LABELS[it.origin_port] || it.origin_port,
      dest_label: PORT_LABELS[it.dest_port] || it.dest_port
    }
  },

  switchSort(e) {
    const key = e.currentTarget.dataset.key
    if (!key || key === this.data.sortKey) return
    const def = SORTS.find((s) => s.key === key)
    this.setData({ sortKey: key, sortLabel: def ? def.label : key }, () => this.applySort())
  },

  /** 排序：综合分直接比 score；单维度排序用该维度得分，同分回退综合分 */
  applySort() {
    const key = this.data.sortKey
    const items = (this._raw || []).slice()
    if (key === 'score') {
      items.sort((a, b) => b.score - a.score)
    } else {
      items.sort((a, b) => {
        const diff = Number((b.breakdown || {})[key] || 0) - Number((a.breakdown || {})[key] || 0)
        return diff !== 0 ? diff : b.score - a.score
      })
    }
    this.setData({ items })
  },

  onRetry() {
    this.fetch()
  },

  // ---- 货主下单流程 ----

  /** 点候选船「选船下单」→ 弹运费确认 */
  onTapCandidate(e) {
    if (this.data.mode !== 'cargo') return
    const idx = Number(e.currentTarget.dataset.index)
    const target = this.data.items[idx]
    if (!target) return
    this.setData({ orderModal: true, target, freightPrice: '' })
  },

  onFreightInput(e) {
    this.setData({ freightPrice: e.detail.value })
  },

  closeModal() {
    this.setData({ orderModal: false })
  },

  /** 确认下单：POST /order/orders（出单前置复用撮合硬约束，单一事实来源） */
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
          wx.switchTab({ url: '/pages/trade/orders/orders' })
        }, 800)
      })
      .catch((e) => console.warn('[swallowed]', (e && e.message) || e))
  }
})
