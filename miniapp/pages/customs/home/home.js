// 智慧关务 · 首页（D1 前端第 1 页）
//
// 这一页存在的唯一理由是**把"数据从哪来"摆到台面上**，而不是做一个小一号的查询框：
//   · 顶部「数据源状态条」逐条列出官方源 / 舱单 / 人工登记各自能不能用、为什么不能；
//   · 官方源不可用时，**不渲染"查官方状态"入口**（PRD §3 第 4 条：不要放一个
//     点进去必然失败的入口），改渲染一句显式说明；
//   · 人工登记永远可用 —— 这就是"断源仍可作业"（AC-08）在界面上的样子；
//   · 下面的列表**只列本平台自己登记过的记录**，标题里写明"非官方"，
//     不让任何一条看起来像海关回执。
//
// ⛔ 三条不做的事：
//   1. 不在接口失败时拿旧数据继续渲染（失败就显式失败，宁可空白）；
//   2. 不在官方源不可用时把人工登记的数据说成"最新状态"；
//   3. 不裸调 wx.navigateTo —— 本页登记在 `MIGRATED_PAGES`，跳转一律经 `R.go()`。
const R = require('../../../utils/routes')
const { fetchSources, fetchDeclarations, sourcesView, declarationView } = require('../../../utils/customs')

const SELF = 'pages/customs/home/home'

Page({
  data: {
    statusBarHeight: 20,
    view: 'loading', // loading | error | ready
    viewHint: '',
    notice: '',
    checkedAt: '',
    officialUnavailable: false,
    manualAvailable: false,
    sources: [],
    keyword: '',
    searched: false,
    list: [],
    errTitle: '',
    errHint: ''
  },

  onLoad() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })

    // 入口守卫：`paramSchema` 为空 ⇒ 这里能拦的只有"路由未登记 / 栈超预算"。
    // 仍然必须跑：`go()` 管不到"别人发了个链接给你"这条路径。
    const gate = R.guardEntry(SELF, { coldStart: R.currentDepth() <= 1 })
    this.entryOk = gate.ok
    if (!gate.ok) {
      this.setData({
        view: 'error',
        errTitle: '入口不合法',
        errHint: gate.reason
      })
    }
  },

  // 从详情页返回时要重取：`onLoad` 不会重跑，而状态可能已经变了。
  // 首次进入 `onShow` 紧随 `onLoad`，只发一轮请求。
  onShow() {
    if (!this.entryOk) return
    this.load()
  },

  onPullDownRefresh() {
    const self = this
    this.load().then(function () {
      wx.stopPullDownRefresh()
      self.setData({})
    })
  },

  /**
   * 并发取「数据源状态」与「最近登记」。
   *
   * 两个请求**互不替代**：状态条拿不到就说明整个模块不可用（渲染错误态），
   * 列表拿不到只该影响列表（状态条照样能证明"官方源不可用"这件事）。
   * 这里选择"任一失败即整体失败"是因为页面此时还没有可展示的确定信息 ——
   * 但只要 status 成功、list 失败，用户至少能看到状态条，因此 list 的失败
   * 记在 `listHint` 上，而不是把整页打红。
   */
  load() {
    const self = this
    const keyword = this.data.keyword
    this.setData({ view: this.data.view === 'ready' ? 'ready' : 'loading', viewHint: '' })
    return Promise.all([
      fetchSources().catch(function (err) {
        return { __error__: err }
      }),
      fetchDeclarations({ no: keyword }).catch(function (err) {
        return { __error__: err }
      })
    ]).then(function (results) {
      const srcRes = results[0]
      const listRes = results[1]
      if (srcRes && srcRes.__error__) {
        // 状态都拿不到：**不猜**、不沿用上一次的 sources
        self.setData({
          view: 'error',
          errTitle: '数据源状态取不到',
          errHint: (srcRes.__error__ && srcRes.__error__.message) || '请稍后重试'
        })
        return
      }
      const sv = sourcesView(srcRes)
      let rows = []
      let listHint = ''
      if (listRes && listRes.__error__) {
        listHint = '登记记录暂时取不到（状态条不受影响）'
      } else {
        const raw = listRes && listRes.items ? listRes.items : listRes
        const arr = Array.isArray(raw) ? raw : []
        rows = arr.map(declarationView)
      }
      self.setData({
        view: 'ready',
        notice: sv.notice,
        checkedAt: sv.checkedAt,
        officialUnavailable: sv.officialUnavailable,
        manualAvailable: sv.manualAvailable,
        sources: sv.rows,
        list: rows,
        searched: !!keyword,
        viewHint: listHint
      })
    })
  },

  onInput(e) {
    this.setData({ keyword: e.detail.value })
  },

  onSearch() {
    this.setData({ searched: !!this.data.keyword })
    this.load()
  },

  onClearSearch() {
    this.setData({ keyword: '', searched: false })
    this.load()
  },

  onOpenItem(e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    R.go('/pages/customs/detail/detail?id=' + encodeURIComponent(String(id)), { from: SELF })
  },

  onRetry() {
    this.load()
  },

  onBack() {
    if (R.currentDepth() <= 1) {
      // 冷启动直接落在本页（如分享链接）：没有可返回的上一页，回身份入口
      R.go('/pages/index/index', { from: SELF })
      return
    }
    wx.navigateBack({ delta: 1 })
  }
})
