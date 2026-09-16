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
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
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
    const taskType = ds.task || ''
    if (taskType) {
      this.promptTaskTitle(taskType)
      return
    }
    // 再点一次同一槽位即收起（不要留下一个"必须点别处才能取消"的面板）
    this.setData({ pickKey: this.data.pickKey === ds.key ? '' : ds.key || '' })
  },

  /** 选中任务类型 → 收起选择条并进入标题输入 */
  onPickTaskType(e) {
    const picked = (e && e.currentTarget && e.currentTarget.dataset.type) || ''
    this.setData({ pickKey: '' })
    if (picked) this.promptTaskTitle(picked)
  },

  promptTaskTitle(taskType) {
    const self = this
    const label = TASK_TYPE_LABELS[taskType] || taskType
    wx.showModal({
      title: '记录任务 · ' + label,
      editable: true,
      placeholderText: '任务标题（1-128 字）',
      success: function (res) {
        if (!res.confirm) return
        const title = (res.content || '').trim()
        if (!title) {
          wx.showToast({ title: '任务标题不能为空', icon: 'none' })
          return
        }
        self.submitTask(taskType, title)
      }
    })
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
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        // 服务端失败原因（无权限 / 委托未受理 / 校验不通过）已由请求层提示；
        // 这里只兜底网络层（没有 httpStatus 的那类），避免静默失败。
        if (!(err && err.httpStatus)) {
          wx.showToast({ title: '任务未记录：网络异常', icon: 'none' })
        }
      })
  },

  // ── 人工落点：受理委托（待受理时）────────────────────────────────────

  onClaim() {
    const self = this
    wx.showModal({
      title: '受理委托',
      content: '受理后该委托进入组织队列，可以派发任务。确认受理？',
      success: function (res) {
        if (!res.confirm) return
        wx.showLoading({ title: '受理中', mask: true })
        claimAssignment(self.data.assignmentId, newIdempotencyKey('claim'))
          .then(function () {
            wx.hideLoading()
            wx.showToast({ title: '已受理', icon: 'success' })
            return self.load()
          })
          .catch(function (err) {
            wx.hideLoading()
            // D-4 裁定 §5：入口展示之后**权限被撤销（403）或委托已被他人认领（409）**，
            // 一律「以后端结果为准」—— 提示已由请求层按服务端 `detail` 如实发出，
            // 这里必须把页面刷成最新状态（重取权限投影 + 委托本体），
            // 否则用户会对着一个已经不可能成功的按钮反复点。
            // ⚠️ 其余错误（400 / 网络层）**不**刷新：状态没变，刷新只会让用户丢掉
            //    当前位置感，还可能把真正该看的那条提示顶掉。
            const status = (err && err.httpStatus) || 0
            if (status === 403 || status === 409) {
              return self.load()
            }
            return null
          })
      }
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
