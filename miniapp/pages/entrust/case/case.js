// 委托发货 · 案件详情（UI-08 / ENT-030 切片四之四「只读」+ 切片四之六「处置」）
//
// 本页是**异常 / 变更案件的详情与处置**：PRD §4.1「UI-08」（第 155 行）要求的六要素
// —— 原因 / 受影响记录 / 拟解决方案 / 决定与审批 / 执行证据 / 结案 —— 外加一条
// append-only 的处理记录。六要素的顺序与"为空时怎么说"来自 `utils/entrust.js` 的
// `CASE_ELEMENTS`，本页**不自己判断"该不该显示某一块"**：少一块就是详情缺内容，
// 而页面看起来只会像"这一块没数据"。
//
// ── 只读首片（四之四）与处置（四之六）的分界 ──────────────────────────
// 四之四**有意**只读：那次要先把"能不能看清这宗案件"做对，四个带幂等键与乐观锁的
// 写端点塞进来会让这件事被表单实现细节淹没（当时的判断写在旧注释里，保留在案）。
// 四之六把处置补上，位置就选在本页 —— 四个写命令（登记受影响项 / 移除 / 记录决定 /
// 关闭 / 重开）需要的 `revision_no`、`capabilities`、受影响项清单，本页**本来就都拿着**；
// 搬到一个新页去反而要重新取一遍，还要处理"详情页刚看完、处置页状态已经变了"。
// 登记案件仍然独立成页（`case-create`）—— 那是"新建"，与"改一宗已有案件"不是一件事。
//
// ⚠️ 界面按钮**不是**权限控制：`capabilities` 由服务端给出，据此显隐按钮只是体验层；
//    写端独立复核权限、证据、幂等与版本，两者不一致时**以写端为准** ——
//    收到 403/409 必须提示，不能静默（`caseWriteError` 负责把它翻成能照做的结论）。
//
// ENT-019 起新增委托页面必须从首个切片接入运行期导航治理（`utils/routes.js`）：
//   · `onLoad` 的入口守卫与 `go()` 用**同一份** `paramSchema` 判定参数合法性；
//   · 返回 / 回首页经 `go()`（`case → index` 声明为 `reset`，执行 `reLaunch`）；
//   · 本页登记在 `MIGRATED_PAGES` 里，CI 会核对不得再出现裸 `wx.navigateTo /
//     redirectTo / reLaunch`。
//
// ⚠️ 参数名是 `case_id`，接口路径却是 `/exceptions/{exception_id}`（DR-0014 §7）：
//    同一个值、两个名字。**不要为了"统一"改掉任一侧** —— 路径那侧已经发布。
//
// ⚠️ 404 是**刻意含混**的：服务端对「开关关闭」「案件不存在」「非参与方」都返回 404
//    （不泄漏存在性）。共享的 `viewState` 因此给的是「功能未开放」而不是「案件不存在」
//    —— 后者在开关关闭时就是一句错误的业务结论。这条映射三个委托页面共用，要改一起改。
const {
  VIEW,
  CASE_TARGET_LABELS,
  addCaseLink,
  caseClosureOptions,
  caseDecideAvailable,
  caseDecisionOptions,
  caseWriteError,
  closeCase,
  decideCase,
  decorateCase,
  decorateCaseLinkTargets,
  fetchArtifactCandidates,
  fetchArtifactTypes,
  fetchCase,
  fetchTaskCandidates,
  newIdempotencyKey,
  removeCaseLink,
  reopenCase,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）；`go()` 需要知道"从哪来"才能查到导航边 */
const SELF = 'pages/entrust/case/case'

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    caseId: '',
    detail: null,
    fields: [],
    blocks: [],
    events: [],
    actionHint: '',
    /** 候选面板用的案件字段（写命令的参数都取这一份，不从模板反推） */
    assignmentId: '',
    kind: '',
    status: '',
    revisionNo: 1,
    /** 受影响项行的可点版本（比 `blocks` 里那份多一个 `linkId`） */
    affected: [],
    // ── 处置能力（服务端 `capabilities` + 取值域镜像的共同结论）──────────
    canAddLink: false,
    canRemoveLink: false,
    canDecide: false,
    canClose: false,
    canReopen: false,
    decisionOptions: [],
    closureOptions: [],
    /**
     * 处置区的**页内表单**状态。
     *
     * 用页内表单而不是 `wx.showModal`：原生弹层不在渲染树里 ⇒ 走查工具点不到它的
     * 确认键 ⇒ 这两条人工落点在真机上无法验证（详见 `onPickDecision` 上方注释）。
     * 每次取数（`applyState`）都把三份表单清空 —— 写成功后会 `load()`，
     * 表单不该带着上一次的内容留在屏幕上。
     */
    decideForm: { to: '', note: '', basis: '' },
    decideHint: '',
    closeForm: { disp: '', evidence: '', resolution: '' },
    closeHint: '',
    reopenForm: { open: false, reason: '' },
    reopenHint: '',
    /** 受影响项候选面板 */
    linkPickOpen: false,
    candLoaded: false,
    candHint: '',
    candidates: []
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
    // 冷启动 / 外部深链（消息、分享、扫码）必须在本页自检：`go()` 管不到这条路径。
    // 判据只有一份 —— `case_id` 的必需性与字符集写在 `utils/routes.js` 的
    // `paramSchema` 里，`go()` 与 `onLoad` 用同一套规则。页面自己只查 `!id` 的话，
    // `?case_id=../../x` 这类"看着有值"的会被放过去，一路带到接口。
    const rawId = query && query.case_id != null ? String(query.case_id) : ''
    const gate = R.guardEntry(SELF + (rawId ? '?case_id=' + encodeURIComponent(rawId) : ''), {
      coldStart: R.currentDepth() <= 1
    })

    if (!gate.ok) {
      // 缺参/格式非法都是**编程或传播错误**（正常入口都会带上），按错误态显示而不是
      // 静默空白。这里必须直接给 error 态，**不能**借 viewState 的兜底推断：
      // `status=0 且 netError=false` 会被它判成"空"，于是编程错误被伪装成一个正常的
      // 业务状态（这正是 verify_entrust_ui.js 要守住的"错误优先于空"）。
      const errs = gate.errors || []
      const miss = errs.filter(function (e) {
        return String(e.reason).indexOf('缺少') === 0
      })
      this.applyState(
        {
          state: VIEW.ERROR,
          title: miss.length ? '缺少案件编号' : '案件编号不合法',
          hint: miss.length ? '请从委托工作台或会话进入' : gate.reason
        },
        null
      )
      return
    }

    this.setData({ caseId: rawId })
    this.load()
  },

  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchCase(this.data.caseId)
      .then(function (res) {
        self.applyState(viewState({ status: 200, total: 1 }), decorateCase(res))
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.applyState(
          viewState({ status: status, netError: !status, detail: err && err.detail }),
          null
        )
      })
  },

  applyState(state, detail) {
    const caps = (detail && detail.capabilities) || {}
    const kind = (detail && detail.kind) || ''
    const status = (detail && detail.status) || ''
    const closureOptions = detail ? caseClosureOptions(kind, status) : []
    const decisionOptions = detail ? caseDecisionOptions(kind, status) : []
    const canDecide = caseDecideAvailable(caps, kind, status)
    const canClose = !!caps.can_close && closureOptions.length > 0
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      detail: detail,
      fields: detail ? detail.fields : [],
      blocks: detail ? detail.blocks : [],
      events: detail ? detail.events : [],
      actionHint: detail ? detail.actionHint : '',
      assignmentId: detail ? detail.assignmentId : '',
      kind: kind,
      status: status,
      revisionNo: detail ? detail.revisionNo || 1 : 1,
      affected: detail ? this.affectedRows(detail) : [],
      // 渲染条件 = 服务端能力位 **且** 取值域镜像里真有可选项。
      // 只看 `can_decide` 会渲染出一个空的选择条（后端对 `change_request/rejected`
      // 与 `exception/applied` 给 `can_decide=true`，而它们唯一的出边 `closed`
      // 归 close 所有）—— 理由写在 `caseDecideAvailable` 的注释里。
      canAddLink: !!caps.can_add_link,
      canRemoveLink: !!caps.can_remove_link,
      canDecide: canDecide,
      canClose: canClose,
      canReopen: !!caps.can_reopen,
      decisionOptions: decisionOptions,
      closureOptions: closureOptions,
      // 取数即清空处置表单（见 data 里的说明）
      decideForm: { to: '', note: '', basis: '' },
      decideHint: '',
      closeForm: { disp: '', evidence: '', resolution: '' },
      closeHint: '',
      reopenForm: { open: false, reason: '' },
      reopenHint: '',
      // 处置区整体是否要出现。单独算一个布尔而不是在模板里写四段 `||`：
      // 模板里写布尔表达式，改一处漏一处不会有任何东西报错。
      canAnyAction: !!caps.can_add_link || canDecide || canClose || !!caps.can_reopen
    })
  },

  /**
   * 受影响项行（比 `blocks` 里那份多一个 `linkId` —— 移除时要它）。
   *
   * 从 `detail.blocks` 里取而不是另存一份 `affected` 投影：受影响记录是**六要素
   * ②** 的同一份数据，另存一份就会出现"要素②说两条、处置区说一条"。
   * 取不到一律返回空数组（六要素的顺序由 `CASE_ELEMENTS` 决定，本函数不假定位置）。
   */
  affectedRows(detail) {
    const blocks = (detail && detail.blocks) || []
    for (let i = 0; i < blocks.length; i++) {
      if (blocks[i].key === 'affected') return blocks[i].items || []
    }
    return []
  },

  // ── 处置（切片四之六）────────────────────────────────────────────────

  /**
   * 一次处置提交的公共骨架：加载提示 → 写 → 成功刷新 / 失败翻译。
   *
   * 五个写命令的**失败处理完全一致**（`caseWriteError` 分流 + 409 必须重新取数），
   * 所以走同一条路径；各自"怎么收集输入、发什么 body"留在 `on*` 里 ——
   * 那是它们唯一不同的地方，也是唯一该由调用方决定的事。
   */
  submit(label, run) {
    const self = this
    wx.showLoading({ title: label, mask: true })
    return run()
      .then(function () {
        wx.hideLoading()
        wx.showToast({ title: '已记录', icon: 'success' })
        // 写成功必须**重新取数**：状态、`revision_no`、事件链、受影响项都会变。
        // 用返回值就地改本地状态等于在界面里维护第二份真相，漏一个字段就开始漂移。
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        const w = caseWriteError(err)
        wx.showModal({ title: w.title, content: w.hint, showCancel: false })
        // 409 = 本地版本已过期。必须把最新状态取回来 —— 否则用户照提示重试
        // 仍然拿 409（`expected_revision` 还是旧的那个），会以为"重试永远失败"。
        if (w.conflict) self.load()
      })
  },

  /** 展开 / 收起候选面板；第一次展开时懒加载本单的任务与成果 */
  onToggleLinkPick() {
    const open = !this.data.linkPickOpen
    this.setData({ linkPickOpen: open })
    if (open && !this.data.candLoaded) this.loadCandidates()
  },

  loadCandidates() {
    const self = this
    const id = this.data.assignmentId
    if (!id) {
      // 详情里连 `assignment_id` 都没有：这宗案件的数据本身不完整，
      // 不能静默当成"本单没有候选" —— 那是两件不同的事。
      this.setData({ candLoaded: true, candidates: [], candHint: '这宗案件没有关联到委托单，无法列出候选' })
      return Promise.resolve()
    }
    this.setData({ candHint: '正在读取本单的任务与成果…' })
    // 类型注册表失败只影响成果那一行的副标题，不让它拖垮整个候选面板
    return Promise.all([
      fetchTaskCandidates(id),
      fetchArtifactCandidates(id),
      fetchArtifactTypes().catch(function () {
        return []
      })
    ])
      .then(function (res) {
        const rows = decorateCaseLinkTargets(res[0], res[1], res[2])
        self.setData({
          candLoaded: true,
          candidates: rows,
          candHint: rows.length ? '' : '这张委托还没有任务或成果可以关联'
        })
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        // 失败也置 `candLoaded`：否则每次展开都会重发一次注定失败的请求
        self.setData({
          candLoaded: true,
          candidates: [],
          candHint: status
            ? '候选读取失败（接口返回 ' + status + '）'
            : '候选读取失败：请确认后端已启动'
        })
      })
  },

  onAddLinkTarget(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const kind = ds.kind || ''
    const id = ds.id != null ? String(ds.id) : ''
    if (!CASE_TARGET_LABELS[kind] || !id) return
    const self = this
    wx.showModal({
      title: '登记受影响项',
      content: '把「' + CASE_TARGET_LABELS[kind] + ' #' + id + '」登记为这宗案件的受影响项？',
      success: function (res) {
        if (!res.confirm) return
        self.submit('登记中', function () {
          return addCaseLink(
            self.data.caseId,
            {
              expected_revision: self.data.revisionNo,
              target_kind: kind,
              target_id: Number(id)
            },
            newIdempotencyKey('case-link')
          )
        })
      }
    })
  },

  onRemoveLink(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const linkId = Number(ds.link || 0)
    if (!linkId) return
    const self = this
    wx.showModal({
      title: '移除受影响项',
      content: '移除后这宗案件不再关联它。若这是阻断类案件的最后一条，移除后必须补一条，否则案件不合法。',
      confirmText: '移除',
      success: function (res) {
        if (!res.confirm) return
        self.submit('移除中', function () {
          return removeCaseLink(
            self.data.caseId,
            linkId,
            self.data.revisionNo,
            newIdempotencyKey('case-unlink')
          )
        })
      }
    })
  },

  // ── 记录决定（页内表单）─────────────────────────────────────────────
  //
  // ⚠️ 从 `wx.showModal({editable:true})` 改成页内表单的依据（2026-09-14 真机实测）：
  //    原生 modal 的弹层**不在渲染树里** —— `.weui-dialog*` 选择器全部命中 0 个元素、
  //    `page` 的 outerWXML 读出来是空串、tap 确认键失败。于是「记录决定 / 关闭」
  //    这两条人工落点**在真机上根本没法被验证**，而 DR-0013 §7.3 条件 4 要求的
  //    恰恰是"人工落点经界面走通"。
  //    同一取向此前用过一次：7 项的任务类型选择从 `showActionSheet` 改成页内展开条
  //    （见 detail.js 的 TASK_TYPE_OPTIONS 注释）—— 弹层承担关键输入，既难走查也难操作。

  onPickDecision(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const to = ds.status || ''
    if (!to) return
    // 换目标状态就清掉依据版本（它只对「已批准」有意义）与上一次的提示
    this.setData({
      decideForm: { to: to, note: (this.data.decideForm || {}).note || '', basis: '' },
      decideHint: ''
    })
  },

  onDecideInput(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const field = ds.df || ''
    if (field !== 'note' && field !== 'basis') return
    const patch = { decideHint: '' }
    patch['decideForm.' + field] = (e && e.detail && e.detail.value) || ''
    this.setData(patch)
  },

  onSubmitDecision() {
    const f = this.data.decideForm || {}
    const to = f.to || ''
    if (!to) {
      this.setData({ decideHint: '请先选一个目标状态' })
      return
    }
    // `approved` 必须指向它所依据的**精确**成果版本（§3.1.1 服务端强制）。
    // 在页内就说清，别让用户填完提交后拿一个 400。
    if (to === 'approved' && !/^\d+$/.test(String(f.basis || '').trim())) {
      this.setData({
        decideHint: '批准必须给出依据成果版本 id（数字；成果页「版本历史」每条都标着「版本 id」）'
      })
      return
    }
    const self = this
    const body = { expected_revision: this.data.revisionNo, to_status: to }
    const note = String(f.note || '').trim()
    if (note) body.decision_note = note
    if (to === 'approved') body.basis_revision_id = Number(String(f.basis).trim())
    return this.submit('提交中', function () {
      return decideCase(self.data.caseId, body, newIdempotencyKey('case-decide'))
    })
  },

  // ── 关闭（页内表单：处置 + 证据 + 结案说明）──────────────────────────
  // 一并把"关一次要连点两个弹层"消掉：选处置 → 填证据（必填）→ 填结案说明 → 提交。

  onPickDisposition(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const disp = ds.disp || ''
    if (!disp) return
    this.setData({ 'closeForm.disp': disp, closeHint: '' })
  },

  onCloseInput(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const field = ds.df || ''
    if (field !== 'evidence' && field !== 'resolution') return
    const patch = { closeHint: '' }
    patch['closeForm.' + field] = (e && e.detail && e.detail.value) || ''
    this.setData(patch)
  },

  onSubmitClose() {
    const f = this.data.closeForm || {}
    const disp = f.disp || ''
    const evidence = String(f.evidence || '').trim()
    if (!disp) {
      this.setData({ closeHint: '请先选一种处置方式' })
      return
    }
    // 没有一键关闭（PRD 第 255 行 `not a generic skip`）：证据是关闭的**前置**，
    // 不能靠"留空"绕过 —— 页内直接拦住，而不是让服务端报 400 再说一遍。
    if (!evidence) {
      this.setData({ closeHint: '证据引用不能为空' })
      return
    }
    const self = this
    const body = {
      expected_revision: this.data.revisionNo,
      closure_disposition: disp,
      evidence_ref: evidence
    }
    const note = String(f.resolution || '').trim()
    if (note) body.resolution_note = note
    return this.submit('关闭中', function () {
      return closeCase(self.data.caseId, body, newIdempotencyKey('case-close'))
    })
  },

  // ── 重开（页内表单，理由同"记录决定"）──────────────────────────────

  onOpenReopen() {
    this.setData({ reopenForm: { open: true, reason: '' }, reopenHint: '' })
  },

  onReopenInput(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    if ((ds.df || '') !== 'reason') return
    this.setData({
      'reopenForm.reason': (e && e.detail && e.detail.value) || '',
      reopenHint: ''
    })
  },

  onSubmitReopen() {
    const reason = String((this.data.reopenForm || {}).reason || '').trim()
    if (!reason) {
      this.setData({ reopenHint: '重开必须说明原因' })
      return
    }
    const self = this
    return this.submit('重开中', function () {
      return reopenCase(
        self.data.caseId,
        { expected_revision: self.data.revisionNo, reason: reason },
        newIdempotencyKey('case-reopen')
      )
    })
  },

  onRetry() {
    this.load()
  },

  onBack() {
    // 冷启动 / 消息深链进入时栈里只有本页，`navigateBack` 会**静默失败**
    // （「点了返回没反应」）。回入口重走身份链路：case → index 声明为 `reset`。
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
