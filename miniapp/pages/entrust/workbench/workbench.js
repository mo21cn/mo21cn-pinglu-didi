// 委托发货 · 经理工作台（S2 前端切片 / ENT-012 / AC-04 · AC-21）
//
// 数据源：
//   · 委托队列 `GET /api/v1/entrust/assignments?view=org`
//   · 异常 / 变更队列 `GET /api/v1/entrust/exceptions?view=org&org_id=…`（UI-04，切片四之六）
// 写动作（一个）：
//   · **受理委托** `POST /api/v1/entrust/assignments/{id}/claim`（S1 工作项 4 / D1-02）
//     入口挂在委托卡片上、只在**待受理**状态时出现，且**页内二次确认**
//     （见 `data.claimOpenId` 的说明）。权限由服务端判定，前端不假装知道。
// 两个队列的可见性都由服务端按「组织成员 + 该货主授权 + 权限码」判定；
// 前端不做任何"我是不是经理"的本地判断（那只能靠可篡改的本地角色字段），
// 也不自行拼组织范围（缺 `org_id` 后端直接 422，不提供无范围查询）。
//
// ENT-019：本页已接入运行期导航治理（`utils/routes.js`）——
//   · 跳转一律经 `go()`（不再裸调 `wx.navigateTo`），页面栈预算与上下文复用才真的生效；
//   · `onLoad` 执行 `guardEntry()` 入口守卫（冷启动 / 消息深链进入时必须自检）；
//   · 本页登记在 `utils/routes.js` 的 `MIGRATED_PAGES` 里，CI 会核对"名单内不得再出现
//     裸 `wx.navigateTo / redirectTo / reLaunch`"。
const {
  CASE_KIND_LABELS,
  CASE_KIND_ORDER,
  CASE_ORG_SCOPE_LABELS,
  CASE_ORG_SCOPE_ORDER,
  ORG_PERM_CLAIM,
  STATUS_META,
  STATUS_ORDER,
  VIEW,
  claimAssignment,
  decorateCaseList,
  decorateList,
  decorateOrgs,
  fetchCaseOrgList,
  fetchMyOrgs,
  fetchQueue,
  newIdempotencyKey,
  pageHint,
  permittedOrgIds,
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

/**
 * 本页承载**两个队列**（DR-0010 §3.8 / DR-0014 §3.1–3.2）：
 *
 * - `assignment` —— 委托队列（`GET /assignments?view=org`）。本页原有内容，
 *   仍是默认队列：经理人进工作台的第一件事通常是看"有哪些单要处理"。
 * - `case` —— 异常 / 变更队列（`GET /exceptions?view=org&org_id=…`，UI-04）。
 *   它是**跨委托**的组合队列，与委托队列共用同一个组织定位与同一套五态裁决，
 *   但筛选维度不同（开闭范围 + 案件类型，而不是委托状态）。
 *
 * 为什么做成本页的队列切换、而不是另开一个页面：两个队列的前提完全一样
 * （同一个组织上下文、同一次组织选择、同一套五态），另开页面就得把这套
 * 定位逻辑复制一份，或者把用户再赶回去选一次组织。同样它也**不该**被做成
 * 两个 tabBar 页 —— 那是两个入口，不是两个视图。
 */
const QUEUE = { ASSIGNMENT: 'assignment', CASE: 'case' }

// 筛选条：全部 + 后端取值域（不从页面里硬编码状态字面量，改后端只需改 utils）
const FILTERS = [{ key: '', label: '全部' }].concat(
  STATUS_ORDER.map(function (key) {
    return { key: key, label: STATUS_META[key].label }
  })
)

/**
 * 案件队列的开闭范围。**不是状态筛选** ——
 * 六个案件状态摊成筛选条，「未关闭」这一档就得靠多选表达，漏选就是漏看。
 */
const CASE_SCOPES = CASE_ORG_SCOPE_ORDER.map(function (key) {
  return { key: key, label: CASE_ORG_SCOPE_LABELS[key] }
})

/** 案件类型筛选。合到一行会与范围筛选混成一条，故另起一行、各带自己的默认项。 */
const CASE_KINDS = [{ key: '', label: '全部类型' }].concat(
  CASE_KIND_ORDER.map(function (key) {
    return { key: key, label: CASE_KIND_LABELS[key] }
  })
)

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    // 当前队列（两个队列共用 items / total / pageHint 一格渲染位：
    // 它们是"当前这个队列的数据"，不是"两个队列各自的数据"——后者会让模板
    // 需要同时判断"哪一格是活的"，而这正是最容易渲染出错数据的形状）
    queue: QUEUE.ASSIGNMENT,
    filters: FILTERS,
    activeStatus: '',
    caseScopes: CASE_SCOPES,
    caseKinds: CASE_KINDS,
    activeCaseScope: CASE_ORG_SCOPE_ORDER[0],
    activeCaseKind: '',
    items: [],
    total: 0,
    pageHint: '',
    orgs: [],
    activeOrgId: '',
    orgReason: '',
    /**
     * 队列卡片上「受理」的**页内二次确认**当前展开在哪一张上（空串＝都收起）。
     *
     * 为什么不用 `wx.showModal`：原生弹层**不在渲染树里**，走查工具点不到它的
     * 确认键 ⇒ "确认之后"那一步永远拿不到设备证据（与 ENT-041「应用变更」把
     * 确认条改成页内 DOM 同一个原因，也是 ㉞ 章原生弹层只能记 `LIMITATION` 的原因）。
     * 受理是本页**唯一会改变业务状态**的动作，它必须可被真机验证。
     *
     * 同一时刻只允许一张展开：并排两条长得一样的小字确认条，用户按错的那条
     * 也是一个真实的受理动作，而受理不可回退（`submitted → claimed` 没有反向边）。
     */
    claimOpenId: '',
    /** 受理请求在飞的委托 id（空串＝没有在飞）。防同一张卡重复点击；跨用户并发仍由服务端 409 兜住 */
    claimingId: ''
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
    // ⚠️ 过一遍 `R.decodeParam`：`onLoad` 拿到的是**未解码**的百分号串（见该函数注释）。
    //    本页带的是纯数字 `org_id`，`encodeURIComponent` 本是恒等变换、接上它行为不变；
    //    统一这么写是为了**同一类缺陷不留第二处** —— 受理屏（带中文货名）已经因此
    //    翻过车，将来这个参数换成中文就会重演。
    const rawOrg = R.decodeParam(query && query.org_id)
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
    // 组织权限投影（D-4 裁定 §2）—— 在取数**之前**先置空：
    // 空表 ⇒ 任何卡片都不显示「受理」入口。裁定 §4 要求"权限尚未加载或加载失败时，
    // 不提前展示可执行按钮"，显式置空就是这句话的代码形态 ——
    // 而不是依赖"恰好还没取到"这种偶然。
    this.permittedOrgIds = {}
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
        const rawOrgs = (res && res.items) || []
        const orgs = decorateOrgs(rawOrgs)
        // 权限投影（D-4 裁定 §2）：必须用**原始载荷**算 —— `decorateOrgs()` 的产出
        // 已把权限码翻成中文标签（`decorateOrg`），拿它查权限码永远查不到，
        // 而且是**静默**的（表现为"按钮从来不出现"，看起来像权限不足）。
        // 存实例属性而不是 `data`：它是判据、不是渲染数据；进 `data` 既白跑一次
        // setData，又会诱导模板绕过 `canClaim` 直接消费它。
        self.permittedOrgIds = permittedOrgIds(rawOrgs, ORG_PERM_CLAIM)
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
   * 取数入口：按当前队列分流。
   *
   * 分流放在这里、而不是让两个队列各自在 `catch` 里 `setData`，是为了让
   * 「失败不得被渲染成没有数据」这条只有一份实现（见下方两个取数函数共用的
   * `applyFailure`）。两个队列共用同一个组织定位结果与同一套五态裁决，
   * 差别只在取哪个接口、空态怎么说。
   */
  loadQueue() {
    return this.data.queue === QUEUE.CASE ? this.loadCaseQueue() : this.loadAssignmentQueue()
  },

  /**
   * 委托队列。**不 catch 后 setData 成空列表** —— 那会把"失败"渲染成"没有委托"，
   * 是这类页面最常见也最难被发现的问题。一切进入 viewState 统一裁决。
   */
  loadAssignmentQueue() {
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
        // 传权限投影：卡片上「受理」入口 = 可认领状态 ∧ **该卡所属组织**的认领权限
        // （`canClaimAssignment`，D-4 裁定 §4）。不传 ⇒ 该卡一律不显示受理入口。
        const items = decorateList(res && res.items, self.permittedOrgIds)
        self.applyState(viewState({ status: 200, total: total }), items, total)
      })
      .catch(function (err) {
        self.applyFailure(err)
      })
  },

  /**
   * 异常 / 变更队列（UI-04，**跨委托**）。
   *
   * 空态文案必须与委托队列不同：两个队列空的时候说的不是一件事
   * （"还没有委托" vs "还没有异常或变更"），复用同一句会让用户以为筛错了队列。
   * 但判定顺序仍由 `viewState` 独占 —— 这里只覆写措辞，不覆写"错误优先于空"。
   */
  loadCaseQueue() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    if (!this.data.activeOrgId) {
      // 组织未定位就发请求，后端会以 422 回应（缺范围）。那是**编程错误**而不是业务状态，
      // 所以按 error 态点名，而不是让它退化成一句笼统的"加载失败"。
      this.applyState(
        { state: VIEW.ERROR, title: '尚未确定要查看的组织', hint: '请先在上方选择服务经营主体' },
        [],
        0
      )
      return Promise.resolve()
    }
    return fetchCaseOrgList({
      orgId: this.data.activeOrgId,
      scope: this.data.activeCaseScope,
      kind: this.data.activeCaseKind,
      page: 1,
      size: PAGE_SIZE
    })
      .then(function (res) {
        const total = (res && res.total) || 0
        const items = decorateCaseList(res && res.items)
        self.applyState(
          viewState({
            status: 200,
            total: total,
            emptyTitle: '还没有异常或变更',
            emptyHint: '案件由经理人登记后出现在这里'
          }),
          items,
          total
        )
      })
      .catch(function (err) {
        self.applyFailure(err)
      })
  },

  /** 取数失败统一进 viewState（两个队列共用一份，避免各写一套判定顺序） */
  applyFailure(err) {
    const status = (err && err.httpStatus) || 0
    this.applyState(
      viewState({ status: status, netError: !status, detail: err && err.detail }),
      [],
      0
    )
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

  /**
   * 切换队列（委托 / 异常与变更）。**先清空再取**。
   *
   * 两个队列的行形状不同（`assignmentId` vs `caseId`），把上一个队列的行留在 `items`
   * 里、只换 `queue`，模板就会拿另一个形状的字段去渲染 —— 那会是一屏"看着像这个队列
   * 的数据"的错值，而不是明显的空白。清空之后，"还没有数据"与"数据还没回来"
   * 仍然只由 `view` 一个字段表达（`loadQueue` 立刻置 loading）。
   */
  onSwitchQueue(e) {
    const key = (e.currentTarget.dataset.queue || '').toString()
    if (!key || key === this.data.queue) return
    // 只认注册过的两个队列：未知值静默忽略，避免以后加队列时把某个拼错的 key
    // 变成"切过去了但什么都没发生"。
    if (key !== QUEUE.ASSIGNMENT && key !== QUEUE.CASE) return
    this.setData({ queue: key, items: [], total: 0, pageHint: '' })
    this.loadQueue()
  },

  /** 切换案件开闭范围（未关闭 / 全部）。组织已定位，只重取案件队列。 */
  onCaseScope(e) {
    const key = (e.currentTarget.dataset.caseScope || '').toString()
    if (!key || key === this.data.activeCaseScope) return
    this.setData({ activeCaseScope: key })
    this.loadCaseQueue()
  },

  /** 切换案件类型筛选（全部类型 / 异常 / 变更请求）。 */
  onCaseKind(e) {
    const key = (e.currentTarget.dataset.caseKind || '').toString()
    if (key === this.data.activeCaseKind) return
    this.setData({ activeCaseKind: key })
    this.loadCaseQueue()
  },

  /** 打开案件详情（UI-08）。与 `onOpen` 同一套 `go()` 用法，只是目标是案件页。 */
  onOpenCase(e) {
    const id = e.currentTarget.dataset.caseId
    if (!id) return
    const self = this
    R.go('/pages/entrust/case/case?case_id=' + encodeURIComponent(String(id)), {
      from: SELF,
      ctx: { orgId: this.data.activeOrgId },
      hasUnsaved: this.hasUnsaved(),
      onUnsaved: function (plan) {
        self.confirmLeave(plan)
      },
      fail: function () {
        wx.showToast({ title: '打开案件失败', icon: 'none' })
      }
    })
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

  // ── 队列里的受理入口（S1 工作项 4 / D1-02）──────────────────────────

  /**
   * 展开 / 收起某张卡的受理确认条。
   *
   * 两个锚点共用本方法（`data-act-claim` 展开、`data-act-claim-cancel` 收起），
   * 与案件页 `onToggleApply()` 同一形态；但队列是**列表**，所以必须带 id ——
   * 不带 id 就分不清"要展开哪一张"。
   */
  onToggleClaim(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    // 显式 `String()` 归一：投影层的 `assignmentId` 是数字，而这个 id 要拿到模板里
    // 与 `claimOpenId` 做**严格**比较 —— 两边不同型会让确认条永远不显示
    // （表现是"点了受理没反应"）。归一放在这一侧，模板那边补 `+ ''`。
    const id = String(ds.actClaim || ds.actClaimCancel || '')
    if (!id) return
    // 再点一次同一张即收起（不要留下"必须点别处才能取消"的面板）
    this.setData({ claimOpenId: this.data.claimOpenId === id ? '' : id })
  },

  /** 确认受理（页内确认条上的那一下）。在飞期间对同一张卡不再受理第二次。 */
  onSubmitClaim(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const id = String(ds.actClaimSubmit || '')
    if (!id || this.data.claimingId === id) return
    this.submitClaim(id)
  },

  /**
   * 受理委托。失败时按状态码决定要不要刷新 —— 最常见的失败是 409「已被别人受理」，
   * 其次是 403「权限被撤销」；两者都意味着本地这份列表（乃至本地那份**权限投影**）
   * 已经过期。不刷新的话用户看到的还是「待受理」，会以为"点了没反应"，然后再点一次。
   *
   * 刷新走 `load()`（整页）而不是只重取队列：`load()` 会把组织清单与**权限投影**
   * 一并重取。只刷队列的话，按钮会留在页面上继续骗人去点（D-4 裁定 §5）。
   *
   * 不在这里弹失败原因：请求层已按服务端 `detail` / 网络分级如实提示过，
   * 页面再弹一遍就是两句话描述同一件事（`detail.js` 的 catch 也是这个取向）。
   */
  submitClaim(assignmentId) {
    const self = this
    this.setData({ claimingId: assignmentId, claimOpenId: '' })
    wx.showLoading({ title: '受理中', mask: true })
    // 幂等键每次新生成：用户重新点击＝一次新的意图。同一次网络重试复用同键是
    // 请求层的事（`claimAssignment` 只发一次），这里不复用键是对的 ——
    // 复用反而会让"点两次"被服务端当成同一次。
    return claimAssignment(assignmentId, newIdempotencyKey('claim'))
      .then(function () {
        wx.hideLoading()
        self.setData({ claimingId: '' })
        wx.showToast({ title: '已受理', icon: 'success' })
        return self.loadQueue()
      })
      .catch(function (err) {
        wx.hideLoading()
        self.setData({ claimingId: '' })
        // D-4 裁定 §5：权限被撤销（403）或已被他人抢先认领（409）⇒ 以后端结果为准。
        // 其余错误（400 / 网络层）不刷新：状态没变，刷新只会让用户丢掉当前位置感。
        const status = (err && err.httpStatus) || 0
        if (status === 403 || status === 409) {
          return self.load()
        }
        return null
      })
  },

  /**
   * 打开该委托的**专业会话屏**（DR-0015 / AC-05 的聊天侧入口）。
   *
   * 为什么落在**每张委托卡片**上而不是页面级一个入口：会话的语义是
   * 「某张委托的会话」——`session` 页的 `assignment_id` 是必填参数，
   * 没有"全局会话"这个对象。放在卡片上，`assignment_id` 天然带着，不必再让用户选一次。
   *
   * ⚠️ 必须用 `catchtap` 绑定：卡片整体已有 `bindtap="onOpen"`，
   * 用 `bindtap` 会**同时**触发两者（跳详情 + 跳会话），表现为"点一次跳了两页"。
   */
  onOpenSession(e) {
    const id = e.currentTarget.dataset.actSession
    if (!id) return
    R.go('/pages/entrust/session/session?assignment_id=' + encodeURIComponent(String(id)), {
      from: SELF,
      fail: function () {
        wx.showToast({ title: '打开会话失败', icon: 'none' })
      }
    })
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
   *    不存在"改了没提交"）、队列切换与筛选条（都是纯视图状态，丢了不算数据丢失），
   *    以及受理（点「确认受理」就是一次服务端写，展开确认条本身不承载任何待提交内容
   *    —— 它随时可点「取消」收起，收起不丢东西）。
   *    第一个**真正的**编辑面（成果编辑 / 任务派发表单）出现时，**必须**改这里，
   *    否则 `go()` 的 confirm-unsaved 分支永远走不到。登记案件表单因此**没有**做在本页
   *    的弹层里，而是独立的 `pages/entrust/case-create/case-create`：本页有两个队列、
   *    两套筛选与两套行形状，"再来一个带未保存状态的表单"会让这一页的三件事互相纠缠。
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
