// 委托发货 · 经理工作台（S2 前端切片 / ENT-012 / AC-04 · AC-21）
//
// 数据源：GET /api/v1/entrust/assignments?view=org
// 服务端按「组织成员 + 该货主授权 + entrust:view 权限」判定可见性；
// 前端不做任何"我是不是经理"的本地判断（那只能靠可篡改的本地角色字段）。
//
// ENT-019：本页已接入运行期导航治理（`utils/routes.js`）——
//   · 跳转一律经 `go()`（不再裸调 `wx.navigateTo`），页面栈预算与上下文复用才真的生效；
//   · `onLoad` 执行 `guardEntry()` 入口守卫（冷启动 / 消息深链进入时必须自检）；
//   · 本页登记在 `utils/routes.js` 的 `MIGRATED_PAGES` 里，CI 会核对"名单内不得再出现
//     裸 `wx.navigateTo / redirectTo / reLaunch`"。
const {
  STATUS_META,
  STATUS_ORDER,
  VIEW,
  decorateList,
  decorateOrgs,
  fetchMyOrgs,
  fetchQueue,
  pageHint,
  pickOrg,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）。显式写出而不是从 getCurrentPages 反推：
 *  `go()` 需要知道"从哪来"才能查到对应的导航边，页面自己是最可靠的来源。 */
const SELF = 'pages/entrust/workbench/workbench'

const PAGE_SIZE = 20

/** 记住"上次选的组织"。存本地只是**默认值**，不是权限依据 —— 权限始终由服务端判定。 */
const STORAGE_ORG_KEY = 'entrust_active_org'

// 筛选条：全部 + 后端取值域（不从页面里硬编码状态字面量，改后端只需改 utils）
const FILTERS = [{ key: '', label: '全部' }].concat(
  STATUS_ORDER.map(function (key) {
    return { key: key, label: STATUS_META[key].label }
  })
)

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    filters: FILTERS,
    activeStatus: '',
    items: [],
    total: 0,
    pageHint: '',
    orgs: [],
    activeOrgId: '',
    orgReason: ''
  },

  onLoad(query) {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })

    // ── 入口守卫（ENT-019）────────────────────────────────────────────
    // 冷启动 / 外部深链（消息、分享、扫码）时**必须在本页自检**：`go()` 只覆盖应用内
    // 导航，管不到"别人给你发了个链接"这条路径。
    //
    // 守卫必须放在取数**之前**：把格式非法的 `org_id` 原样送进接口，等于把一次
    // 「传播/手改错误」渲染成「这个组织没有委托」—— 后者是会被当成业务事实汇报出去的。
    const rawOrg = query && query.org_id != null ? String(query.org_id) : ''
    const gate = R.guardEntry(SELF + (rawOrg ? '?org_id=' + encodeURIComponent(rawOrg) : ''), {
      coldStart: R.currentDepth() <= 1,
      orgId: rawOrg
    })

    if (!gate.ok) {
      // 参数非法 / 未登记路由：这是**编程或传播错误**，不是业务状态，
      // 所以走 error 态明确点名（与 detail 页"错误优先于空"同一条原则）。
      this.applyState(
        { state: VIEW.ERROR, title: '入口参数不合法', hint: gate.reason },
        [],
        0
      )
      return
    }
    // `redirect-org` 在本页**等价于 continue**：本页自己就是组织选择流程
    // （自带 onPickOrg 与「需要选择/还没有加入服务经营主体」两态），
    // 再跳一次组织选择会成环 —— 见 utils/routes.js guardEntry 里的说明。

    // 组织来源优先级：**深链参数 > 上次选择 > 由服务端清单推导**。
    // 深链带 org_id 是有意的（例如从消息直接进入某个组织的队列）；
    // 但两者都只是"默认值"，最终仍要由服务端清单确认它**仍然有效**（见 pickOrg）。
    const fromQuery = rawOrg
    this.savedOrgId = fromQuery || wx.getStorageSync(STORAGE_ORG_KEY) || ''
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
   * 取数：**先定位组织，再取队列**，最后统一落到五态上。
   *
   * 组织清单的失败**不能**被当成"没有组织"（那会把一次网络故障说成
   * "还没有加入服务经营主体"这种业务事实），所以它与队列取数走**同一个**
   * viewState 裁决 —— 这也正是"不 catch 成空"的同一原则。
   */
  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchMyOrgs()
      .then(function (res) {
        const orgs = decorateOrgs(res && res.items)
        const picked = pickOrg(orgs, self.savedOrgId)
        self.savedOrgId = picked.orgId
        self.setData({ orgs: orgs, activeOrgId: picked.orgId, orgReason: picked.reason })

        if (picked.reason === 'none') {
          // 没有任何组织身份。这不是"没有数据"，而是这个账号还没有办理能力 ——
          // 两者的下一步动作完全不同，所以用 denied 态 + 可行动的文案。
          self.setData({
            view: VIEW.DENIED,
            viewTitle: '还没有加入服务经营主体',
            viewHint: '请联系服务经营主体把你加入组织；加入后这里会出现委托队列',
            items: [],
            total: 0,
            pageHint: ''
          })
          return null
        }
        if (picked.reason === 'ambiguous') {
          // 多组织且未选择：**不猜**（见 utils/entrust.js 的 pickOrg 说明）。
          self.setData({
            view: VIEW.DENIED,
            viewTitle: '需要选择服务经营主体',
            viewHint: '你属于多个组织，请在上方选择要查看的组织',
            items: [],
            total: 0,
            pageHint: ''
          })
          return null
        }
        return self.loadQueue()
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.applyState(
          viewState({ status: status, netError: !status, detail: err && err.detail }),
          [],
          0
        )
      })
  },

  /**
   * 取队列。**不 catch 后 setData 成空列表** —— 那会把"失败"渲染成"没有委托"，
   * 是这类页面最常见也最难被发现的问题。一切进入 viewState 统一裁决。
   */
  loadQueue() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchQueue({
      orgId: this.data.activeOrgId,
      status: this.data.activeStatus,
      page: 1,
      size: PAGE_SIZE
    })
      .then(function (res) {
        const total = (res && res.total) || 0
        const items = decorateList(res && res.items)
        self.applyState(viewState({ status: 200, total: total }), items, total)
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.applyState(
          viewState({ status: status, netError: !status, detail: err && err.detail }),
          [],
          0
        )
      })
  },

  applyState(state, items, total) {
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      items: items,
      total: total,
      pageHint: pageHint(total, 1, PAGE_SIZE)
    })
  },

  onFilter(e) {
    const key = e.currentTarget.dataset.key || ''
    if (key === this.data.activeStatus) return
    this.setData({ activeStatus: key })
    // 组织已定位，只重取队列（不必再问一次组织清单）
    this.loadQueue()
  },

  /** 切换组织：记住选择（下次进来不用再选），并立刻重取队列。 */
  onPickOrg(e) {
    const orgId = (e.currentTarget.dataset.org || '').toString()
    if (!orgId || orgId === this.data.activeOrgId) return
    this.savedOrgId = orgId
    try {
      wx.setStorageSync(STORAGE_ORG_KEY, orgId)
    } catch (err) {
      // 存不进去只影响"下次的默认值"，不影响本次选择 —— 不该让交互失败
    }
    this.setData({ activeOrgId: orgId, orgReason: 'picked' })
    this.loadQueue()
  },

  onRetry() {
    // 重试从**组织定位**开始：失败可能就发生在那一步（它现在也是取数链路的一环）
    this.load()
  },

  onBack() {
    // 冷启动 / 消息深链进入时栈里只有本页，`navigateBack` 会**静默失败**
    // （表现为「点了返回没反应」）。这种情况应当回到入口重走身份链路 ——
    // 注册表里 workbench → index 声明为 `reset`，故经 `go()` 会执行 `reLaunch`。
    if (R.currentDepth() <= 1) {
      R.go('/pages/index/index', { from: SELF })
      return
    }
    // 热路径下"返回上一页"的目标是**动态的**（谁把我推进来的就还给谁），
    // 注册表里没有也不需要这样一条边，所以直接用原生语义。
    wx.navigateBack({ delta: 1 })
  },

  /** 登录过期：回首页重走身份链路（本页已无可用的鉴权信息） */
  onRelogin() {
    R.go('/pages/index/index', { from: SELF })
  },

  onOpen(e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    const self = this
    // 经 `go()` 而不是裸 `wx.navigateTo`：栈里若已有同一张委托的详情页，
    // 复用分支会「返回并刷新」而不是再压一层（同路径不同委托**不复用**，
    // 复用键含 assignment_id 与当前组织，见 routes.pageKey）。
    R.go('/pages/entrust/detail/detail?assignment_id=' + encodeURIComponent(String(id)), {
      from: SELF,
      ctx: { orgId: this.data.activeOrgId },
      hasUnsaved: this.hasUnsaved(),
      onUnsaved: function (plan) {
        self.confirmLeave(plan)
      },
      fail: function () {
        wx.showToast({ title: '打开详情失败', icon: 'none' })
      }
    })
  },

  /**
   * 本页是否有未保存的编辑。
   *
   * ⚠️ 当前恒为 `false`，而且**这是事实而不是遗漏**：本页只有组织选择（选中即落本地，
   *    不存在"改了没提交"）与筛选条（纯视图状态，丢了不算数据丢失）。第一个真正的
   *    编辑面（成果编辑 / 任务派发）在 UI-05 之后出现，届时**必须**改这里，
   *    否则 `go()` 的 confirm-unsaved 分支永远走不到。
   */
  hasUnsaved() {
    return false
  },

  /**
   * 未保存编辑保护：明确提示，**不静默卸载**（DR-0011 §3.4 第 4 步）。
   *
   * 用户确认放弃后执行 `plan.afterConfirm`（"放弃编辑后该执行的那个计划"），
   * 而不是重新 `resolveNavigation()`：重算会让 `hasUnsaved` 从 true 变 false 而落到
   * 另一条分支上，提示与结果自相矛盾。两种情形都必须如实处理：
   *   · replace 边 → afterConfirm 是可执行计划（redirectTo）；
   *   · 预算耗尽 → afterConfirm 是 blocked（**放弃编辑也进不去**），这时要
   *     直接说清楚，不能让用户以为"存下来就能进"。
   */
  confirmLeave(plan) {
    const self = this
    if (!plan) return
    if (!wx.showModal) return
    wx.showModal({
      title: '还有未保存的内容',
      content: '继续将放弃当前未保存的编辑，确定继续吗？',
      success: function (res) {
        if (!res || !res.confirm) return
        const next = plan.afterConfirm
        if (!next || !next.ok) {
          wx.showToast({
            title: (next && next.reason) || '无法继续（请先保存）',
            icon: 'none',
            duration: 2500
          })
          return
        }
        R.performPlan(next, {
          from: SELF,
          ctx: { orgId: self.data.activeOrgId },
          fail: function () {
            wx.showToast({ title: '打开详情失败', icon: 'none' })
          }
        })
      }
    })
  }
})
