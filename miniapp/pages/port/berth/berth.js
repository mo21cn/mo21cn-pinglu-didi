// 泊位档期详情（L3 三级页）· 港域（F4）
// 入口：平台运营台 → 泊位管理 → 点击泊位卡
// 跳转参数：id=泊位ID
//
// 页面主线：把「稀缺资源防超卖」这条纪律可视化——
//   pending 不占档期 → 确认时行锁 + 重叠计数 → 超过并发容量即 409。
// 展示性页面：只读档期 + 参数解读，不改泊位参数（编辑能力为占位）。
const { request } = require('../../../utils/request')
const { getUser } = require('../../../utils/auth')
// 业务常量唯一来源（第三方审计 P3-4：原本地复制 PORT_LABELS（未使用，死代码）与
// SHIP_TYPE_LABELS（tanker 写成「油船」，与其余 7 处不一致）→ 统一收敛）
const { SHIP_TYPE_LABELS } = require('../../../utils/constants')

// 硬约束校验链（与 service.create_appt 一一对应，纯展示）
const RULES = [
  { key: 'verified', title: '船舶须已通过备案审核', note: '待审核 / 已驳回的船舶不得预约' },
  { key: 'dwt', title: '船舶载重吨 ≤ 泊位上限', note: '吨位咬合，超限直接拒绝' },
  { key: 'draft', title: '船舶吃水 ≤ 泊位允许吃水', note: '防止搁浅，按证书吃水比对' },
  { key: 'type', title: '船型须在适靠船型内', note: '散货 / 件杂货 / 集装箱 / 油船 白名单' }
]

/** 手写解析 ISO 时间串（避免各端 Date 解析差异），统一按 UTC 求差值 */
function parseTs(s) {
  if (!s) return 0
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!m) return 0
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0))
}

function pad(n) {
  return n < 10 ? '0' + n : String(n)
}

function fmtClock(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + ' ' +
    pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes())
}

function fmtSpan(ms) {
  if (!ms || ms < 0) return '—'
  const h = ms / 3600000
  if (h < 24) return (Math.round(h * 10) / 10) + ' 小时'
  const d = Math.floor(h / 24)
  const rh = Math.round(h - d * 24)
  return rh ? d + ' 天 ' + rh + ' 小时' : d + ' 天'
}

/** 扫描线求峰值并发（左闭右开：首尾相接不算重叠） */
function peakConcurrency(intervals) {
  const events = []
  intervals.forEach((it) => {
    events.push({ t: it.start, delta: 1 })
    events.push({ t: it.end, delta: -1 })
  })
  events.sort((a, b) => (a.t === b.t ? a.delta - b.delta : a.t - b.t))
  let cur = 0
  let peak = 0
  events.forEach((e) => {
    cur += e.delta
    if (cur > peak) peak = cur
  })
  return peak
}

Page({
  data: {
    berthId: 0,
    role: 'shipper',
    loading: true,
    error: '',
    // 泊位
    berth: null,
    berthTitle: '—',
    berthSub: '—',
    statusLabel: '—',
    statusChip: 'chip-muted',
    dwtText: '—',
    draftText: '—',
    typeText: '—',
    capacityText: '—',
    rules: RULES,
    // 档期
    bars: [],
    hasSchedule: false,
    rangeText: '—',
    peakText: '0',
    peak: 0,
    capacity: 0,
    occupancyText: '—',
    occupancyChip: 'chip-muted'
  },

  onLoad(options) {
    const berthId = Number((options && options.id) || 0)
    const user = getUser()
    // API 仅港口方角色可用；非港口方进入时提前给出解释，避免一次无谓请求
    this.setData({ berthId, role: (user && user.current_role) || 'shipper' })
    wx.setNavigationBarTitle({ title: '泊位档期' })
    if (!berthId) {
      this.setData({ loading: false, error: '缺少泊位参数，无法加载档期' })
      return
    }
    if (this.data.role !== 'port') {
      this.setData({
        loading: false,
        error: '泊位档期仅港口方角色可查看，请在「我的」页切换为港口方身份后重试'
      })
      return
    }
    this.fetch()
  },

  onPullDownRefresh() {
    if (this.data.berthId && this.data.role === 'port') this.fetch()
    wx.stopPullDownRefresh()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request({ url: '/api/v1/port/berths/' + this.data.berthId + '/schedule' })
      .then((res) => this.apply(res))
      .catch((err) => this.setData({ error: (err && err.message) || '档期加载失败，请稍后重试' }))
      .finally(() => this.setData({ loading: false }))
  },

  apply(res) {
    const b = (res && res.berth) || {}
    const confirmed = (res && res.confirmed) || []

    // ---- 泊位基本信息 ----
    const types = (b.allowed_ship_types || []).map((t) => SHIP_TYPE_LABELS[t] || t)
    const params = {
      berth: b,
      berthTitle: (b.port_code || '—') + ' · ' + (b.berth_no || '—'),
      berthSub: b.berth_name || '未命名泊位',
      statusLabel: b.status === 'active' ? '可用' : '停用',
      statusChip: b.status === 'active' ? 'chip-success' : 'chip-muted',
      // 空值兜底（第三方审计 P3-9：null 拼接会显示「null 吨」）
      dwtText: (b.max_dwt != null && b.max_dwt !== '') ? b.max_dwt + ' 吨' : '—',
      draftText: (b.max_draft != null && b.max_draft !== '') ? b.max_draft + ' 米' : '—',
      typeText: types.length ? types.join(' / ') : '—',
      capacityText: (b.concurrent_capacity != null) ? b.concurrent_capacity + ' 船位' : '—',
      capacity: Number(b.concurrent_capacity) || 0
    }

    // ---- 档期甘特 ----
    const items = confirmed
      .map((a) => ({ id: a.appt_id, shipId: a.ship_id, start: parseTs(a.plan_start), end: parseTs(a.plan_end) }))
      .filter((a) => a.start && a.end && a.end > a.start)
      .sort((a, b2) => a.start - b2.start)

    let bars = []
    let rangeText = '—'
    if (items.length) {
      const min = items[0].start
      const max = items.reduce((acc, a) => (a.end > acc ? a.end : acc), items[0].end)
      const total = max - min || 1
      bars = items.map((a) => {
        const left = ((a.start - min) / total) * 100
        const width = Math.max(((a.end - a.start) / total) * 100, 4)
        return {
          key: 'a' + a.id,
          label: '预约 #' + a.id,
          ship: '船 #' + a.shipId,
          span: fmtClock(a.start) + ' → ' + fmtClock(a.end),
          duration: fmtSpan(a.end - a.start),
          left: left.toFixed(2) + '%',
          width: width.toFixed(2) + '%'
        }
      })
      rangeText = fmtClock(min) + ' ~ ' + fmtClock(max)
    }

    const peak = peakConcurrency(items)
    const cap = params.capacity
    let occupancyText = '暂无占用'
    let occupancyChip = 'chip-muted'
    if (items.length) {
      occupancyText = peak + ' / ' + cap + ' 船位'
      occupancyChip = peak >= cap ? 'chip-danger' : peak > 0 ? 'chip-warn' : 'chip-muted'
    }

    this.setData({
      ...params,
      bars,
      hasSchedule: items.length > 0,
      rangeText,
      peak,
      peakText: String(peak),
      occupancyText,
      occupancyChip
    })
  },

  onPlaceholder(e) {
    const name = (e.currentTarget && e.currentTarget.dataset.name) || '该能力'
    wx.showToast({ title: name + '即将开放', icon: 'none' })
  },

  goBack() {
    wx.navigateBack({ delta: 1 })
  }
})
