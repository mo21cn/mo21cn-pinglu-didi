// 委托发货 · 单张委托工作台（UI-05 首片 / ENT-021）
//
// 本页是**单张委托的工作台**：委托本体摘要 + 七个业务槽位（DR-0010 §3.1）。
// 七槽位的顺序与 key 来自 `utils/entrust.js` 的 `WORKBENCH_SLOTS`，数据来自
// `GET /assignments/{id}/workbench`（一条只读聚合查询，返回每槽四字段 + 计数）。
//
// 空值四态（DR-0010 §3.6）：投影层已经把「暂无记录 / 尚未分配 / 不适用 / 信息缺失」
// 分别算好并给了各自的文案，本页**不做二次判断** —— 页面里再判一次就会与后端
// 漂移，而且"错误被显示成空"这类问题正是页面自己判断时最容易发生的。
//
// 首片（ENT-021）的人工落点：**记录任务**（每个开放槽位）+ **受理委托**（待受理时）。
// 第二片（ENT-023）补上第三个人工落点：**进入成果页**（编辑 / 确认）——
// 槽位里的成果引用可点，点了带着这一条的 `artifact_id` 进成果页。
// 切片四之五（ENT-030）把同一个落点扩到**案件页**：`exceptions` 槽下发的是
// `CaseRef`（`case_id`，无版本），与成果引用**不同形状**，由投影层按槽位配置分流，
// 本页只按 dataset 里的 `kind` 转交（见 `onOpenRef`）。
// 切片四之六（ENT-030）再补一个人工落点：**登记案件**（`onCreateCase`）——
// 入口只在已受理（`claimed`）时出现，因为受理前没有责任主体，`raise_case` 会 409。
// 本页自己**不**做成果的编辑与确认：那需要版本历史与字段表单，
// 塞进这张卡里会把七槽位总览变成半个编辑器；且成果页需要独立入口核对"生效版本是哪个"。
//
// ENT-019：本页已接入运行期导航治理（`utils/routes.js`）——
//   · `onLoad` 的入口守卫与 `go()` 用**同一份** `paramSchema` 判定参数合法性；
//   · 返回 / 回首页经 `go()`（`workbench → index` 声明为 `reset`，执行 `reLaunch`）；
//   · 本页登记在 `MIGRATED_PAGES` 里，CI 会核对不得再出现裸 `wx.navigateTo /
//     redirectTo / reLaunch`。
//
// 交互形态（2026-09-16 统一）：本页的**两个关键路径**都走**页内 DOM**，一律不用
// `wx.showModal` ——
//   · 「受理委托」= 页内确认条（`data.claimOpen`）；
//   · 「记录任务」= 页内标题输入条（`data.taskOpenKey` / `data.taskForm`）。
// 理由只有一条：**原生弹层不在渲染树里**（`.weui-dialog*` 命中 0、`page` 的 outerWXML
// 读出来是空），走查工具**点不到它的确认键** ⇒ 由弹层承担的关键路径永远拿不到设备证据
// （⑧b / ㉕D 的 `LIMITATION` 就是这么来的）。判据：**弹层承担的关键路径 = 不可验证的路径**。
// 静态防线见 `scripts/verify_ui_interactions.js` ⑪ 章（断言这两条路都不出现 `showModal`）。
const {
  VIEW,
  TASK_TYPE_LABELS,
  TASK_TYPE_ORDER,
  ORG_PERM_CLAIM,
  canClaimAssignment,
  claimAssignment,
  createTask,
  decorateDetail,
  decorateWorkbench,
  fetchAssignment,
  fetchMyOrgs,
  fetchWorkbench,
  newIdempotencyKey,
  permittedOrgIds,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）；`go()` 需要知道"从哪来"才能查到导航边 */
const SELF = 'pages/entrust/detail/detail'

/**
 * 任务类型选项（`plan_tasks` 的内联选择器用）。
 *
 * 用**页内展开的选择条**而不是 `wx.showActionSheet`：后者超过 6 项在真机上会滚动、
 * 底部项容易被切断，项目约定是长列表走 `port-picker` 这类组件；而任务类型只有 7 项、
 * 又已经在同一张卡里，直接展开比弹一层更少上下文切换。
 * （`scripts/verify_ui_interactions.js` 的静态防线会拦下"showActionSheet + 动态列表"，
 *  这条拦截是对的 —— 它拦住的正是我这个初版实现。）
 */
const TASK_TYPE_OPTIONS = TASK_TYPE_ORDER.map(function (t) {
  return { key: t, label: TASK_TYPE_LABELS[t] || t }
})

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    assignmentId: '',
    detail: null,
    fields: [],
    slots: [],
    /**
     * 「受理委托」入口 —— 两个条件**同时**满足才为真（D-4 裁定 §1）：
     * 委托处于可认领状态（`submitted`），且当前操作者在**该委托所属组织**内
     * 有 `entrust:assignment:claim`。
     *
     * 判据由 `canClaimAssignment()` 给出（与队列卡片**同一份实现**，裁定 §4）。
     * 初值 `false` 是保守缺省：权限投影取到之前不得提前展示可执行按钮（裁定 §4）。
     * 隐藏按钮**不等于**放行 —— 写端 `claim_assignment` 仍按同一个 `org_id`
     * 独立校验成员资格与权限（裁定 §3）。
     */
    canClaim: false,
    /**
     * 已受理（status=claimed）时给「登记异常 / 变更」入口。
     *
     * 只看**委托状态**，不看权限 —— 与服务端同一条判据：`raise_case` 要求委托
     * 已受理（否则 409），权限则由 `_assert_can_write` 单独判定。
     * 前端在这里假装知道有没有权限，只会在无权限时给出一个必然 403 的按钮。
     */
    canCreateCase: false,
    /** 归属机制上线前的历史成果计数提示（0 时为空串） */
    unassignedHint: '',
    /** 已展开类型选择的槽位 key（空串＝都收起） */
    pickKey: '',
    /**
     * 「记录任务」的**页内输入条**展开在哪个槽位（空串＝都收起）。
     *
     * 为什么**不用** `wx.showModal({ editable: true })`：原生弹层**不在渲染树里**
     * （`.weui-dialog*` 全部命中 0、`page` 的 outerWXML 读出来是空），走查工具
     * **点不到它的确认键** ⇒ 这条人工落点就永远拿不到设备证据。而"记录任务"
     * 恰恰是本页承载的**关键输入**。判据：**弹层承担的关键路径 = 不可验证的路径**。
     *
     * 同一条理由此前已用过两次：本页 7 项任务类型选择从 `showActionSheet` 改成
     * 页内展开条；案件页的「记录决定 / 关闭 / 重开」从可编辑弹层改成页内表单
     * （见 `case.wxml` 的 `act-input` 注释）。这里是第三处，形态与案件页一致。
     */
    taskOpenKey: '',
    /** 输入条内的任务类型与标题（标题由 `bindinput` 按**路径**写回，不整对象替换） */
    taskForm: { type: '', typeLabel: '', title: '' },
    /** 输入条的校验提示（在**页内**说清，不用 toast —— toast 会消失，而这句话要一直看得见） */
    taskHint: '',
    /**
     * 「受理委托」的**页内确认条**（`true` ＝已展开）。
     *
     * 与队列卡片的确认条同一条理由：受理是本页唯一会**改变业务状态**、且**不可回退**
     * 的动作（`submitted → claimed` 没有反向边），它必须可被真机验证。原生弹层的
     * 确认键工具点不到 ⇒ 只能记 `LIMITATION`（⑧b / ㉕D 就是这么来的）。
     */
    claimOpen: false,
    /** 受理请求在飞（防同一页重复点击；跨用户并发仍由服务端 409 兜住） */
    claiming: false,
    taskTypes: TASK_TYPE_OPTIONS
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
    // 冷启动 / 外部深链（消息、分享、扫码）时必须在本页自检：`go()` 管不到这条路径。
    //
    // 把「缺参」从"页面自己判断"改成"守卫的统一结论"，收益不是少写几行，而是**判据
    // 只有一份**：`assignment_id` 的必需性与字符集写在 `utils/routes.js` 的
    // `paramSchema` 里，`go()` 与 `onLoad` 用同一套规则。初版页面只查 `!id`，
    // 于是 `?assignment_id=../../x`、`?assignment_id=a b` 这类"看着有值"的会被放过去，
    // 一路带到接口。
    // ⚠️ 归一化后再重建 url（见 `routes.decodeParam`）：`onLoad` 拿到的是未解码串，
    //    再编码一次就是二次编码。本页是 ASCII id，行为不变；统一写法防将来带中文时重演。
    const rawId = R.decodeParam(query && query.assignment_id)
    const gate = R.guardEntry(SELF + (rawId ? '?assignment_id=' + encodeURIComponent(rawId) : ''), {
      coldStart: R.currentDepth() <= 1
    })

    if (!gate.ok) {
      // 缺参/格式非法都是**编程或传播错误**（正常入口都会带上），按错误态显示而不是
      // 静默空白。这里必须直接给 error 态，**不能**借 viewState 的兜底推断：`status=0
      // 且 netError=false` 的输入会被它判为「空列表」，页面显示「还没有委托」，
      // 于是编程错误被伪装成一个正常的业务状态（这正是 verify_entrust_ui.js
      // 要守住的"错误优先于空"）。
      const errs = gate.errors || []
      const miss = errs.filter(function (e) {
        return String(e.reason).indexOf('缺少') === 0
      })
      this.applyState(
        {
          state: VIEW.ERROR,
          title: miss.length ? '缺少委托编号' : '委托编号不合法',
          hint: miss.length ? '请从委托列表进入详情' : gate.reason
        },
        null,
        null
      )
      return
    }

    this.setData({ assignmentId: rawId })
    // 组织权限投影（D-4 裁定 §2）。取数**之前**置空：空表 ⇒ 不显示受理入口 ——
    // 这是裁定 §4「权限尚未加载或加载失败时，不提前展示可执行按钮」的代码形态。
    this.permittedOrgIds = {}
    this.load()
  },

  load() {
    const self = this
    const id = this.data.assignmentId
    // 重取时把两处页内交互面**复位**：整页刷新之后，展开着的确认条 / 输入条都已经
    // 失去了它当初的判据（权限与委托状态都可能变了）⇒ 让它们回到"未展开"，
    // 而不是留一个点了必然失败的按钮。复位只放在这里一处，页面别处不再各收一次。
    this.setData({
      view: VIEW.LOADING,
      viewTitle: '加载中',
      viewHint: '',
      claimOpen: false,
      claiming: false,
      pickKey: '',
      taskOpenKey: '',
      taskHint: ''
    })
    // 前两个请求是**页面内容**：工作台是主内容，委托本体是它的头卡。任一失败都按失败
    // 处理 ——「显示半个工作台」会让用户以为槽位就是这些，比直接说加载失败更糟。
    //
    // 第三个是**权限投影**（D-4 裁定 §2），决定受理入口显不显示。它与前两个
    // **命运不同**：自己消化失败、降级成"空投影"（⇒ 不显示入口）。依据是裁定 §4
    // 「仍可查看其有权读取的内容」—— 拿不到权限结论时用户仍有权读这张委托，
    // 把整页打成错误态是**过度反应**，而且会说错话（委托明明读到了，页面却说失败）。
    // 保守方向也在这里：拿不到 ⇒ 不显示，而不是猜"有权限"。
    return Promise.all([
      fetchAssignment(id),
      fetchWorkbench(id),
      fetchMyOrgs().catch(function () {
        return null
      })
    ])
      .then(function (res) {
        self.permittedOrgIds = permittedOrgIds((res[2] && res[2].items) || [], ORG_PERM_CLAIM)
        const detail = decorateDetail(res[0], self.permittedOrgIds)
        const board = decorateWorkbench(res[1])
        self.applyState(viewState({ status: 200, total: 1 }), detail, board)
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.applyState(
          viewState({ status: status, netError: !status, detail: err && err.detail }),
          null,
          null
        )
      })
  },

  applyState(state, detail, board) {
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      detail: detail,
      fields: detail ? this.buildFields(detail) : [],
      slots: board ? board.slots : [],
      // 受理入口 = 可认领状态 ∧ **该委托所属组织**内的认领权限 ——
      // 与队列卡片**同一份实现**（`canClaimAssignment`，D-4 裁定 §4）。
      // 组织取 `detail.orgId`：受理用的工作台载荷里没有 `org_id`。
      // 无权限时服务端仍会拒绝（403），请求层如实提示 —— 这里只决定显不显示。
      canClaim: canClaimAssignment(
        board && board.status,
        detail && detail.orgId,
        this.permittedOrgIds
      ),
      canCreateCase: !!(board && board.status === 'claimed'),
      unassignedHint: board ? board.unassignedHint : ''
    })
  },

  /** 头卡字段行（模板只做 wx:for，不做表达式）。货类/货量已进 `overview` 槽位，此处不重复。 */
  buildFields(detail) {
    return [
      { label: '委托编号', value: '#' + detail.assignmentId },
      { label: '归属', value: detail.orgText },
      { label: '提交时间', value: detail.createdAt || '—' },
      { label: '数据版本', value: 'r' + (detail.revision || 1) }
    ]
  },

  // ── 人工落点：记录任务（每个开放槽位）────────────────────────────────

  /**
   * 记录一项任务。任务类型由槽位决定；`plan_tasks` 是**全单任务总览**，
   * 不替用户假定类型，所以在卡内展开选择条让用户自己挑。
   */
  onRecordTask(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const key = String(ds.key || '')
    if (!key) return
    const taskType = ds.task || ''
    if (taskType) {
      // 槽位自带固定类型（例如"记录异常"这类写死类型的槽）⇒ 直接进输入条，
      // 不必先让用户在只有一项的选择条里点一下。
      this.openTaskForm(key, taskType)
      return
    }
    // 再点一次同一槽位即收起（不要留下一个"必须点别处才能取消"的面板）；
    // 同时收起**另一条**输入条 —— 同一时刻只允许一个编辑面展开。
    this.setData({ pickKey: this.data.pickKey === key ? '' : key, taskOpenKey: '' })
  },

  /** 选中任务类型 → 收起选择条并进入**页内**标题输入条 */
  onPickTaskType(e) {
    const picked = (e && e.currentTarget && e.currentTarget.dataset.type) || ''
    // ⚠️ 槽位 key 取自**当前展开的选择条**（`pickKey`），必须在清空它**之前**读出来 ——
    //    选择条是渲染在槽位卡内部的，选完之后"是哪一个槽位"只剩这一个来源。
    const key = String(this.data.pickKey || '')
    if (!picked || !key) {
      this.setData({ pickKey: '' })
      return
    }
    this.openTaskForm(key, picked)
  },

  /**
   * 打开某个槽位的页内输入条。
   *
   * **同一时刻只允许一条**：并排两条长得一样的输入条时，用户填错那一条也是一次
   * 真实的任务记录（写端每次提交都是新意图、没有幂等键可救）。切换槽位时标题**清空**，
   * 不留上一条的残余 —— 残留的标题会被当成"已经填好了"直接提交。
   */
  openTaskForm(key, taskType) {
    this.setData({
      pickKey: '',
      taskOpenKey: key,
      taskForm: { type: taskType, typeLabel: TASK_TYPE_LABELS[taskType] || taskType, title: '' },
      taskHint: ''
    })
  },

  /** 标题输入：按**路径**写回（不整对象替换，避免输入法组字被打断） */
  onTaskInput(e) {
    this.setData({
      'taskForm.title': (e && e.detail && e.detail.value) || '',
      taskHint: ''
    })
  },

  /** 收起输入条（取消）。用户打过的标题**不保留** —— 取消就是取消，不做"半保存"。 */
  onCancelTask() {
    this.setData({ taskOpenKey: '', taskForm: { type: '', typeLabel: '', title: '' }, taskHint: '' })
  },

  /**
   * 提交任务。空标题在**页内**说清。
   *
   * 不用 `wx.showToast`：toast 几秒后消失，而「为什么没提交」正是用户此刻需要
   * 一直看到的那句话；且它同样不可被走查断言（不在渲染树里的东西都一样）。
   */
  onSubmitTask() {
    const form = this.data.taskForm || {}
    const title = String(form.title || '').trim()
    if (!title) {
      this.setData({ taskHint: '任务标题不能为空（1–128 字）' })
      return Promise.resolve()
    }
    return this.submitTask(form.type, title)
  },

  submitTask(taskType, title) {
    const self = this
    wx.showLoading({ title: '提交中', mask: true })
    // 幂等键每次提交新生成：重试同一次提交时会复用同一个键（此处是单次用户动作，
    // 用户重新点击就是一次新的意图，应当新键）。
    return createTask(
      this.data.assignmentId,
      { task_type: taskType, title: title },
      newIdempotencyKey('task')
    )
      .then(function () {
        wx.hideLoading()
        wx.showToast({ title: '任务已记录', icon: 'success' })
        // 成功走整页 `load()`，输入条由 `load()` 统一复位（不在两处各收一次，
        // 否则"哪一处负责收"会变成一个要靠记忆维持的约定）。
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        // 服务端失败原因（无权限 / 委托未受理 / 校验不通过）已由请求层提示；
        // 这里只兜底网络层（没有 httpStatus 的那类），避免静默失败。
        // ⚠️ 失败时**不**调 `load()` ⇒ 输入条与用户打好的标题都留着，重试不用重打。
        if (!(err && err.httpStatus)) {
          self.setData({ taskHint: '任务未记录：网络异常（标题已保留，可重试）' })
        }
      })
  },

  // ── 人工落点：受理委托（待受理时）────────────────────────────────────

  /**
   * 展开 / 收起页内确认条。**不弹原生层**（理由见 `data.claimOpen` 的说明）。
   *
   * 与队列卡片的 `onToggleClaim` 同一形态，区别只是本页只有一张委托 ——
   * 所以不需要 `claimOpenId` 那种"展开在哪一张"的记账。
   */
  onToggleClaim() {
    if (this.data.claiming) return
    this.setData({ claimOpen: !this.data.claimOpen })
  },

  /**
   * 确认受理（页内确认条上的那一下）。
   *
   * 失败按状态码决定要不要刷新。D-4 裁定 §5：入口展示之后**权限被撤销（403）或
   * 委托已被他人认领（409）**，一律「以后端结果为准」—— 提示已由请求层按服务端
   * `detail` 如实发出，这里必须把页面刷成最新状态（重取**权限投影** + 委托本体），
   * 否则用户会对着一个已经不可能成功的按钮反复点。
   * ⚠️ 其余错误（400 / 网络层）**不**刷新：状态没变，刷新只会让用户丢掉当前位置感，
   *    还可能把真正该看的那条提示顶掉。
   */
  onSubmitClaim() {
    const self = this
    if (this.data.claiming) return
    this.setData({ claimOpen: false, claiming: true })
    wx.showLoading({ title: '受理中', mask: true })
    // 幂等键每次新生成：用户重新点击＝一次新的意图。同一次网络重试复用同键是请求层的事。
    return claimAssignment(this.data.assignmentId, newIdempotencyKey('claim'))
      .then(function () {
        wx.hideLoading()
        self.setData({ claiming: false })
        wx.showToast({ title: '已受理', icon: 'success' })
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        self.setData({ claiming: false })
        const status = (err && err.httpStatus) || 0
        if (status === 403 || status === 409) {
          return self.load()
        }
        return null
      })
  },

  // ── 人工落点：登记案件（已受理时）────────────────────────────────────

  /**
   * 进入登记案件页。**带的是本页的 `assignment_id`**（写端的路径参数），
   * 而不是"到登记页再去猜是哪张委托" —— 猜出来的委托可能已不是当前这张。
   */
  onCreateCase() {
    R.go(
      '/pages/entrust/case-create/case-create?assignment_id=' +
        encodeURIComponent(String(this.data.assignmentId)),
      { from: SELF }
    )
  },

  // ── 人工落点：进入成果页（编辑 / 确认）──────────────────────────────

  /**
   * 打开槽位里被点的那一条引用。**带的是被点那一条的 ID 与形状**
   * （`id` / `kind` 都来自投影层写进 dataset 的值），不是"到列表里再找一次"：
   * 工作台显示的就是精确 ID 与版本。
   *
   * `kind` 分流到两个页面：成果页要 `artifact_id`、案件页要 `case_id`
   * （DR-0014 §7 有意同名值不同名）。**未知 `kind` 一律不跳** —— 静默退回
   * 成果页会把一个案件 ID 当成果 ID 去读，而"读不到"与"这条引用过期了"
   * 在界面上长得一模一样。
   */
  onOpenRef(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const id = ds.id
    if (!id) return
    if (ds.kind === 'case') {
      R.go('/pages/entrust/case/case?case_id=' + encodeURIComponent(String(id)), { from: SELF })
      return
    }
    if (ds.kind === 'artifact') {
      R.go('/pages/entrust/artifact/artifact?artifact_id=' + encodeURIComponent(String(id)), {
        from: SELF
      })
    }
  },

  onRetry() {
    this.load()
  },

  onBack() {
    // 冷启动 / 消息深链进入时栈里只有本页，`navigateBack` 会**静默失败**
    // （「点了返回没反应」）。回入口重走身份链路：detail → index 声明为 `reset`。
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
