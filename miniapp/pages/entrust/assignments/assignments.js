// 委托发货 · 我的委托（S1 工作项 5 / D1-01）
//
// 数据源：`GET /api/v1/entrust/assignments?view=owner`（货主视角）。
// 本页回答货主的两个问题：「我提交的委托现在**真实**状态是什么」、
// 「**被谁**接了」—— 后者是本页存在的核心理由，此前界面上根本没有这个答案。
//
// 为什么与经理工作台（`pages/entrust/workbench`）分开，而不是给它加一个视角开关：
// 工作台的每一格都建立在一个**已定位的组织**之上（组织选择器、`org_id` 作用域、
// 受理权限投影），而货主**没有组织身份**。塞进同一页就得出一个"同一模板里同时
// 判我是哪种身份"的分支，而那正是最容易渲染出错误数据形状的做法
// （拿不到 org_id 时把空组织当成"没有委托"）。
//
// ENT-019：本页从落地起接入运行期导航治理 ——
//   · 跳转一律经 `go()`（不裸调 `wx.navigateTo`），页面栈预算与上下文复用才真的生效；
//   · `onLoad` 执行 `guardEntry()` 入口守卫（冷启动 / 消息深链进入时必须自检）；
//   · 本页登记在 `utils/routes.js` 的 `MIGRATED_PAGES` 里，CI 会核对
//     "名单内不得再出现裸 `wx.navigateTo / redirectTo / reLaunch`"。
const { VIEW, decorateList, fetchMine, pageHint, viewState } = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）；`go()` 需要知道"从哪来"才能查到导航边 */
const SELF = 'pages/entrust/assignments/assignments'

const PAGE_SIZE = 20

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    items: [],
    total: 0,
    /** 已加载到第几页（触底时 +1）。 */
    page: 1,
    /** 是否还有更早的一页。判据是「已显示 < 总数」，见 `applyState`。 */
    hasMore: false,
    loadingMore: false,
    /**
     * `onReachBottom` 被调用过几次。**不参与渲染**，只服务于 DR-0018 的 A.P-2 探针：
     * 触底后列表若没变长，必须能分清"工具压根没造出滚动"与"造出了滚动但加载失败" ——
     * 没有这个计数，两种完全不同的原因会长成同一个失败。
     */
    reachCount: 0,
    pageHint: ''
  },

  onLoad() {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })

    // ── 入口守卫（ENT-019）────────────────────────────────────────────
    // 本页**没有任何参数**（`paramSchema` 为空）：它列的是"我自己的委托"，
    // 范围由服务端按登录身份决定，不接受调用方指定。所以守卫在这里能拦的
    // 只有两件：路由未登记、以及页面栈超出预算。
    //
    // 仍然必须跑：`go()` 只覆盖应用内导航，管不到"别人发了个链接给你"这条路径；
    // 而且守卫要放在取数**之前** —— 一个进不来的页面不该先发一次请求，
    // 再拿一次与入口无关的报错回来。
    const gate = R.guardEntry(SELF, { coldStart: R.currentDepth() <= 1 })
    this.entryOk = gate.ok
    if (!gate.ok) {
      this.applyState({ state: VIEW.ERROR, title: '入口不合法', hint: gate.reason }, [], 0)
    }
  },

  /**
   * 每次显示都重取。
   *
   * 为什么放在 `onShow` 而不是只放在 `onLoad`：从详情页返回时 `onLoad` **不会**重跑，
   * 而状态恰恰可能已经变了（经理刚受理了这一单）。本页显示的是"现在是什么状态"，
   * 不是"进来那一刻的状态" —— 拿一个过期状态当当前状态展示，是这类页面最容易
   * 被用户当成"系统没同步"的问题（而且它不会报错）。
   *
   * 首次进入时 `onShow` 紧随 `onLoad`，所以只发一次请求。
   */
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
   * 触底加载更早一页（第 2 页起）。
   *
   * 为什么必须有：`load()` 只取第 1 页（`PAGE_SIZE = 20`）。没有本方法时，
   * 货主若提了 25 张单，**第 21 张之后永远看不到** —— 更糟的是界面会诚实地
   * 显示「第 1/2 页」，让这个缺陷看起来像"分页提示"。**分页提示不是分页能力。**
   */
  onReachBottom() {
    const self = this
    this.setData({ reachCount: this.data.reachCount + 1 })
    if (this.data.view !== VIEW.READY || this.data.loadingMore || !this.data.hasMore) return
    const next = this.data.page + 1
    this.setData({ loadingMore: true })
    fetchMine({ page: next, size: PAGE_SIZE })
      .then(function (res) {
        // ⚠️ **追加**而不是替换：替换会把用户已经看到的 20 张清掉，
        //    而"下一页"的语义是接着看，不是从头看。
        const merged = self.data.items.concat(decorateList(res && res.items))
        const total = (res && res.total) || 0
        self.setData({
          items: merged,
          total: total,
          page: next,
          hasMore: merged.length < total,
          loadingMore: false,
          pageHint: pageHint(total, next, PAGE_SIZE)
        })
      })
      .catch(function () {
        // ⚠️ 第 2 页失败**不清空已加载的**：用户已经看到 20 张，
        //    把列表清空比"这一页没加载上"严重得多。
        self.setData({ loadingMore: false })
        wx.showToast({ title: '加载更多失败', icon: 'none' })
      })
  },

  /** 取数：一切结果（含失败）都进 `viewState` 裁决，**不 catch 成空列表**。 */
  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchMine({ page: 1, size: PAGE_SIZE })
      .then(function (res) {
        const total = (res && res.total) || 0
        // ⚠️ 不传权限投影（第二个参数）：`canClaim` 只对**经理**有意义，
        //    货主没有受理入口。不传 ⇒ 该字段恒为 false —— 这里不需要它，
        //    也就不给它造一个"看着像权限判定"的值（见 `decorateAssignment` 的说明）。
        const items = decorateList(res && res.items)
        self.applyState(
          viewState({
            status: 200,
            total: total,
            // 空态文案与其他队列不同：委托队列空着说的是"还不该有单"，
            // 这里说的是"你还没提过" —— 复用同一句会让货主以为筛错了。
            // 判定顺序仍由 `viewState` 独占（错误优先于空），这里只覆写措辞。
            emptyTitle: '还没有委托',
            emptyHint: '在「发布货源」里选「委托发货」提交一单后，这里会显示真实状态与承接组织'
          }),
          items,
          total
        )
      })
      .catch(function (err) {
        self.applyFailure(err)
      })
  },

  /** 取数失败统一进 viewState（与工作台同一份判定顺序，不另写一套）。 */
  applyFailure(err) {
    const status = (err && err.httpStatus) || 0
    this.applyState(
      viewState({ status: status, netError: !status, detail: err && err.detail }),
      [],
      0
    )
  },

  applyState(state, items, total, page) {
    const p = page || 1
    const shown = (items || []).length
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      items: items,
      total: total,
      page: p,
      // ⚠️ `hasMore` 判的是「已显示 < 总数」，**不是**「本页满 20 张」：
      //    总数恰好 = 页长时本页是满的，但下一页是空的 —— 用页长判断会白请求一次。
      hasMore: shown < (total || 0),
      pageHint: pageHint(total, p, PAGE_SIZE)
    })
  },

  onRetry() {
    this.load()
  },

  /** 打开某张委托的详情。复用键含 `assignment_id` ⇒ 同一张会被复用而不是再压一层。 */
  onOpen(e) {
    const id = e.currentTarget.dataset.mineId
    if (!id) return
    R.go('/pages/entrust/detail/detail?assignment_id=' + encodeURIComponent(String(id)), {
      from: SELF,
      fail: function () {
        wx.showToast({ title: '打开详情失败', icon: 'none' })
      }
    })
  },

  onBack() {
    // 冷启动 / 消息深链进入时栈里只有本页，`navigateBack` 会**静默失败**
    // （用户点了"返回"却什么都没发生），所以栈深为 1 时改走 `go()` 回首页。
    if (R.currentDepth() <= 1) {
      R.go('/pages/index/index', { from: SELF })
      return
    }
    wx.navigateBack({ delta: 1 })
  },

  onRelogin() {
    R.go('/pages/index/index', { from: SELF })
  }
})
