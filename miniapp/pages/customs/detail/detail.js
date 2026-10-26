// 智慧关务 · 详情（D1 前端第 2 页）
//
// 这页要回答的问题只有一个：**这条状态是谁在什么时候告诉我们的**。
// 所以时间线里「事情发生的时间」与「我们取到它的时间」必须并排显示 ——
// 合成一列之后，一条三天前补录的人工状态会显示成"刚刚更新"，
// 那是在伪装新鲜度（AC-09 明确禁止）。
//
// 顶部那条 `freshness` 来自服务端（`freshness()`），页面**不自己算**：
// 页面自己算一份就会出现"列表说暂不可用、详情说已接入"这种自相矛盾（AC-02 的坑）。
const R = require('../../../utils/routes')
const { fetchDeclaration, declarationView, freshnessView, eventView } = require('../../../utils/customs')

const SELF = 'pages/customs/detail/detail'

Page({
  data: {
    statusBarHeight: 20,
    view: 'loading', // loading | error | ready
    id: 0,
    rec: null,
    timeline: [],
    freshness: null,
    officialUnavailable: false,
    errTitle: '',
    errHint: ''
  },

  onLoad(options) {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    const id = Number((options && options.id) || 0)
    this.setData({ statusBarHeight: info.statusBarHeight || 20, id: id })

    // 本页**有参数**：`paramSchema` 里 `id` 是必填正整数，
    // 所以守卫在这里能拦三种：路由未登记、缺参/类型错、栈超预算。
    // 缺参是**编程错误**（调用方没带 id），不该伪装成"记录不存在"。
    //
    // ⚠️ **必须把参数拼进 url 再交给守卫**：`guardEntry(url, ctx)` 是从 **url** 里
    //    解析参数的（见 `routes.js`），只传页面路径等于告诉它"这页没有参数"——
    //    于是每次进入都被判成「id 缺少必填参数」。2026-10-26 真机首跑就是这么炸的
    //    （截图上是「入口不合法 · 参数校验未通过：id 缺少必填参数」，而列表跳转是对的）。
    const gate = R.guardEntry(SELF + (id ? '?id=' + encodeURIComponent(String(id)) : ''), {
      coldStart: R.currentDepth() <= 1
    })
    this.entryOk = gate.ok
    if (!gate.ok) {
      this.setData({ view: 'error', errTitle: '入口不合法', errHint: gate.reason })
      return
    }
    if (!id) {
      this.setData({ view: 'error', errTitle: '缺少记录 id', errHint: '请从列表进入' })
      this.entryOk = false
    }
  },

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

  load() {
    const self = this
    this.setData({ view: 'loading' })
    fetchDeclaration(this.data.id)
      .then(function (res) {
        const body = res || {}
        const rec = declarationView(body.declaration)
        const events = Array.isArray(body.timeline) ? body.timeline : []
        const fresh = body.freshness ? freshnessView(body.freshness) : null
        // 时间线按 `occurred_at` 升序（后端 `_events_of` 的 ORDER BY）⇒ **最后一条才是最新的**。
        // 高亮标记挂在"最新"而不是"第一条"，否则图片上标红的是最早那条状态机事件。
        const timeline = events.map(eventView)
        if (timeline.length) timeline[timeline.length - 1].latest = true
        self.setData({
          view: 'ready',
          rec: rec,
          timeline: timeline,
          freshness: fresh,
          officialUnavailable: !!(fresh && fresh.officialState === 'unavailable')
        })
      })
      .catch(function (err) {
        // 404 与"无权限"后端**故意同一种响应**（不泄漏存在性），页面也照此措辞，
        // ⛔ 不要写成"无权查看"——那等于告诉别人"这条存在，只是你看不到"。
        const status = (err && err.statusCode) || 0
        self.setData({
          view: 'error',
          errTitle: status === 404 ? '记录不存在或不可见' : '取不到这条记录',
          errHint: status === 404 ? '请核对单号；非参与方看不到他人的登记记录' : (err && err.message) || '请稍后重试'
        })
      })
  },

  onRetry() {
    this.load()
  },

  onBack() {
    if (R.currentDepth() <= 1) {
      R.go('/pages/customs/home/home', { from: SELF })
      return
    }
    wx.navigateBack({ delta: 1 })
  }
})
