// 预约审核详情（L3 三级页）· 港域（F4）
// 入口：平台运营台 → 预约审核 → 点击预约卡
// 跳转参数：id=预约ID（记录体经 Storage 缓存传入，缺失时用审核列表兜底）
//
// 页面主线：一条预约从「待确认」到「档期锁定」的完整留痕，并把
// 「确认这一刻会发生什么」摊开——行锁 + 重叠计数 + 容量判定，超容量即 409。
const { request } = require('../../../utils/request')
const { getUser } = require('../../../utils/auth')

const CACHE_KEY = 'port_appt_detail'

const STATUS_META = {
  pending: { label: '待确认', chip: 'chip-warn' },
  confirmed: { label: '已锁定', chip: 'chip-success' },
  rejected: { label: '已驳回', chip: 'chip-danger' },
  cancelled: { label: '已撤销', chip: 'chip-muted' },
  completed: { label: '已完成', chip: 'chip-purple' }
}

/** 手写解析 ISO 时间串，统一按 UTC 求差值 */
function parseTs(s) {
  if (!s) return 0
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!m) return 0
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0))
}

function pad(n) {
  return n < 10 ? '0' + n : String(n)
}

function fmtClock(s) {
  const ts = parseTs(s)
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

Page({
  data: {
    apptId: 0,
    role: 'shipper',
    loading: true,
    error: '',
    // 预约
    appt: null,
    statusLabel: '—',
    statusChip: 'chip-muted',
    planStartText: '—',
    planEndText: '—',
    durationText: '—',
    createdText: '—',
    // 泊位容量上下文
    berthTitle: '—',
    capacity: 0,
    capacityText: '—',
    overlapNow: 0,
    afterConfirm: 0,
    hasCapacityInfo: false,
    isPending: false,
    showCalc: false,
    conflict: false,
    checkText: '—',
    checkChip: 'chip-muted',
    checkNote: '',
    // 状态机
    timeline: [],
    // 操作态
    canConfirm: false,
    canReject: false,
    canComplete: false,
    barNote: ''
  },

  onLoad(options) {
    const apptId = Number((options && options.id) || 0)
    const user = getUser()
    this.setData({ apptId, role: (user && user.current_role) || 'shipper' })
    wx.setNavigationBarTitle({ title: '预约审核' })
    if (!apptId) {
      this.setData({ loading: false, error: '缺少预约参数，无法加载详情' })
      return
    }
    if (this.data.role !== 'port') {
      this.setData({
        loading: false,
        error: '预约审核仅港口方角色可查看，请在「我的」页切换为港口方身份后重试'
      })
      return
    }
    this.fetch()
  },

  onPullDownRefresh() {
    if (this.data.apptId && this.data.role === 'port') this.fetch()
    wx.stopPullDownRefresh()
  },

  /** 取数：优先命中跳转前缓存 → 未命中则拉全量审核列表按 id 找回 */
  fetch() {
    this.setData({ loading: true, error: '' })
    const cached = this.readCache()
    const resolver = cached
      ? Promise.resolve(cached)
      : request({ url: '/api/v1/port/appts-review', data: { status: '', size: 100 } }).then(
          (res) => (res.items || []).find((a) => a.id === this.data.apptId) || null
        )

    resolver
      .then((appt) => {
        if (!appt) throw new Error('预约不存在或已不可查看')
        return this.loadCapacity(appt)
      })
      .catch((err) => this.setData({ error: (err && err.message) || '加载失败，请稍后重试' }))
      .finally(() => this.setData({ loading: false }))
  },

  readCache() {
    const bag = wx.getStorageSync(CACHE_KEY)
    if (!bag || !bag.record) return null
    return bag.id === this.data.apptId ? bag.record : null
  },

  /** 取泊位容量上下文：用于把「确认后会冲突吗」提前算给审核人看 */
  loadCapacity(appt) {
    return request({
      url: '/api/v1/port/berths/' + appt.berth_id + '/schedule',
      silent: true
    })
      .catch(() => null)
      .then((res) => this.apply(appt, res))
  },

  apply(appt, schedule) {
    const meta = STATUS_META[appt.status] || { label: appt.status, chip: 'chip-muted' }
    const start = parseTs(appt.plan_start)
    const end = parseTs(appt.plan_end)
    const isPending = appt.status === 'pending'

    // 与本次窗口重叠的已确认预约数（左闭右开，与服务端 count_confirmed_overlap 同口径）；
    // 排除自身——语义是「本单之外，窗口内已确认的占用数」，否则已确认单会自己和自己冲突。
    let overlapNow = 0
    let capacity = 0
    let berthTitle = '—'
    if (schedule && schedule.berth) {
      capacity = Number(schedule.berth.concurrent_capacity) || 0
      berthTitle = (schedule.berth.port_code || '—') + ' · ' + (schedule.berth.berth_no || '—')
      overlapNow = (schedule.confirmed || []).filter((c) => {
        if (c.appt_id === appt.id) return false
        const cs = parseTs(c.plan_start)
        const ce = parseTs(c.plan_end)
        return cs < end && ce > start
      }).length
    }
    const hasCapacityInfo = !!capacity
    const afterConfirm = overlapNow + 1
    const conflict = isPending && hasCapacityInfo && afterConfirm > capacity
    const showCalc = isPending && hasCapacityInfo

    let checkText = '容量信息暂不可用'
    let checkChip = 'chip-muted'
    let checkNote = ''
    if (isPending) {
      if (hasCapacityInfo) {
        checkText = '重叠 ' + overlapNow + ' / 容量 ' + capacity
        checkChip = conflict ? 'chip-danger' : 'chip-success'
      } else {
        checkNote = '泊位档期暂时取不到，确认时仍由服务端加锁二次校验容量。'
      }
    } else if (appt.status === 'confirmed') {
      checkText = '已占用档期'
      checkChip = 'chip-success'
      checkNote = '该预约已确认为有效占用；同窗口另有 ' + overlapNow +
        ' 条已确认预约（容量 ' + (capacity || '—') + '）。'
    } else if (appt.status === 'completed') {
      checkText = '占用已释放'
      checkChip = 'chip-purple'
      checkNote = '靠泊已完成核销，该时间窗不再计入档期占用。'
    } else {
      checkText = '未产生占用'
      checkChip = 'chip-muted'
      checkNote = appt.status === 'rejected'
        ? '申请被驳回，从未写入档期。'
        : '申请已撤销，从未写入档期。'
    }

    const canConfirm = isPending && !conflict

    this.setData({
      appt,
      statusLabel: meta.label,
      statusChip: meta.chip,
      planStartText: fmtClock(appt.plan_start),
      planEndText: fmtClock(appt.plan_end),
      durationText: fmtSpan(end - start),
      createdText: fmtClock(appt.created_at),
      berthTitle,
      capacity,
      capacityText: capacity ? capacity + ' 船位' : '—',
      overlapNow,
      afterConfirm,
      hasCapacityInfo,
      isPending,
      showCalc,
      conflict,
      checkText,
      checkChip,
      checkNote,
      timeline: this.buildTimeline(appt),
      canConfirm,
      canReject: isPending,
      canComplete: appt.status === 'confirmed',
      barNote: this.buildBarNote(appt, canConfirm, conflict, capacity)
    })
  },

  buildBarNote(appt, canConfirm, conflict, capacity) {
    if (appt.status === 'pending') {
      if (conflict) {
        return '当前窗口已占满 ' + capacity + ' 船位，确认将被拒绝（409 档期冲突），建议驳回或协调改期'
      }
      return canConfirm ? '确认后该窗口档期即锁定，其他船舶无法再占用' : '容量信息暂不可用，确认时由服务端二次校验'
    }
    if (appt.status === 'confirmed') return '该预约已锁定档期，船舶完成靠泊后可由港口方核销'
    if (appt.status === 'rejected') return appt.reject_reason ? '驳回原因：' + appt.reject_reason : '该申请已被驳回'
    if (appt.status === 'cancelled') return '船东已撤销申请，档期未产生占用'
    if (appt.status === 'completed') return '该预约已核销完成，全程留痕已闭合'
    return ''
  },

  /** 状态机留痕：已发生 = 实心高亮，待发生 = 灰色预告 */
  buildTimeline(a) {
    const items = [{
      key: 'created',
      title: '船东提交预约申请',
      time: fmtClock(a.created_at),
      note: '申请阶段为「待确认」，不占用档期',
      done: true
    }]

    if (a.status === 'pending') {
      items.push({
        key: 'review',
        title: '等待港口方审核',
        time: '',
        note: '确认即锁定档期；驳回则注明原因退回',
        done: false
      })
    }

    if (a.status === 'rejected') {
      items.push({
        key: 'rejected',
        title: '港口方驳回',
        time: '',
        note: a.reject_reason || '未通过港口方审核',
        done: true
      })
    }

    if (a.status === 'cancelled') {
      items.push({
        key: 'cancelled',
        title: '船东撤销申请',
        time: '',
        note: 'pending / confirmed 均可撤销；撤销即刻释放档期',
        done: true
      })
    }

    if (a.status === 'confirmed' || a.status === 'completed') {
      items.push({
        key: 'confirmed',
        title: '港口方确认 · 档期锁定',
        time: '',
        note: '确认时校验重叠预约数 < 并发容量',
        done: true
      })
    }

    if (a.status === 'confirmed') {
      items.push({
        key: 'complete',
        title: '靠泊完成 · 港口方核销',
        time: '',
        note: '核销后档期占用释放',
        done: false
      })
    }

    if (a.status === 'completed') {
      items.push({
        key: 'completed',
        title: '靠泊完成 · 已核销',
        time: '',
        note: '该泊位档期占用已释放',
        done: true
      })
    }

    return items
  },

  // ---- 操作 ----
  onConfirm() {
    request({ url: '/api/v1/port/appts/' + this.data.apptId + '/confirm', method: 'POST' })
      .then(() => {
        wx.showToast({ title: '已确认，档期锁定', icon: 'success' })
        this.clearCache()
        this.fetch()
      })
      .catch(() => {})
  },

  onReject() {
    wx.showModal({
      title: '驳回预约',
      editable: true,
      placeholderText: '请填写驳回原因（可留空）',
      success: (r) => {
        if (!r.confirm) return
        request({
          url: '/api/v1/port/appts/' + this.data.apptId + '/reject',
          method: 'POST',
          data: { reason: r.content || '档期安排冲突' }
        })
          .then(() => {
            wx.showToast({ title: '已驳回', icon: 'success' })
            this.clearCache()
            this.fetch()
          })
          .catch(() => {})
      }
    })
  },

  onComplete() {
    request({ url: '/api/v1/port/appts/' + this.data.apptId + '/complete', method: 'POST' })
      .then(() => {
        wx.showToast({ title: '已核销完成', icon: 'success' })
        this.clearCache()
        this.fetch()
      })
      .catch(() => {})
  },

  clearCache() {
    wx.removeStorageSync(CACHE_KEY)
  },

  onPlaceholder(e) {
    const name = (e.currentTarget && e.currentTarget.dataset.name) || '该能力'
    wx.showToast({ title: name + '即将开放', icon: 'none' })
  },

  goBerth() {
    if (!this.data.appt) return
    wx.navigateTo({ url: '/pages/port/berth/berth?id=' + this.data.appt.berth_id })
  },

  goBack() {
    wx.navigateBack({ delta: 1 })
  }
})
