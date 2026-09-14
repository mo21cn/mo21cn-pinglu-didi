// 委托发货 · 登记案件（ENT-030 切片四之六 / DR-0013 §7.1「登记案件」人工落点）
//
// 本页只做一件事：**把一宗异常 / 变更案件登记进系统**。处置（受影响项 / 决定 /
// 关闭 / 重开）在案件详情页 —— 那里已经有案件本体、`capabilities` 与 `revision_no`，
// 正是四个写命令各自需要的东西；塞进本页会让「填完表单」与「改一宗已有案件」
// 挤在同一个页面状态里，两件事都会变难。
//
// 为什么登记案件要单独成页，而不是工作台里的一个弹层
// --------------------------------------------------
// 组织工作台本身已经有两个队列、两套筛选、两套行形状（见 workbench.js）。再塞进
// 一个带**未保存状态**的表单，三件事会互相纠缠：切换队列要不要警告放弃编辑？
// 筛选变了要不要重置表单？把表单独立出去，这些问题一个都不存在。
//
// 受影响项为什么要能**随案件一起**提交
// ------------------------------------
// 不是图省事：C2 规定 `impact_kind='execution-blocking'` 必须至少有一条受影响项。
// 若只能"先建案件、再补 link"，那个中间态本身就违反 C2 —— 要么放行一个非法状态，
// 要么让用户看着一个必然失败的按钮。后端 `raise_case` 因此接受 `links` 并**同事务**
// 写入，本页照它的形状来。
//
// 候选（任务 / 成果）为什么是**懒加载**的
// --------------------------------------
// 只在用户第一次展开受影响项面板时才去取。理由有两层：① 不选受影响项是完全正常的
// 用法（`review-required` 就不需要），为此多打两个请求、并让它们的失败把整页判成
// 加载失败，是把边角需求当主线；② 候选清单的取数失败只影响这一个面板，
// 页面其余部分照常可用。
//
// ENT-019 起新增委托页面必须从首个切片接入运行期导航治理（`utils/routes.js`）：
//   · `onLoad` 的入口守卫与 `go()` 用**同一份** `paramSchema` 判定参数合法性；
//   · 登记成功后用 `go()` 走 `replace` 到案件详情（**不**用 push —— 返回键不该
//     把用户带回一张已经提交过的表单）；
//   · 本页登记在 `MIGRATED_PAGES` 里，CI 会核对不得出现裸 `wx.navigateTo /
//     redirectTo / reLaunch`。
const {
  VIEW,
  caseCreateBody,
  caseImpactOptions,
  caseKindOptions,
  caseSeverityOptions,
  caseWriteError,
  createCase,
  decorateCaseLinkTargets,
  decorateDetail,
  fetchArtifactCandidates,
  fetchArtifactTypes,
  fetchAssignment,
  fetchTaskCandidates,
  newIdempotencyKey,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）；`go()` 需要知道"从哪来"才能查到导航边 */
const SELF = 'pages/entrust/case-create/case-create'

/** 表单初值。`impact_kind` 默认取「需要复核」而不是「阻断执行」：
 *  阻断是有后果的选择（相关任务立即做不了），不该是"没改过就是这个"。 */
const BLANK_FORM = {
  kind: 'exception',
  title: '',
  severity: 'medium',
  impact_kind: 'review-required',
  cause: '',
  proposed_action: '',
  due_at: ''
}

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    assignmentId: '',
    detail: null,
    form: Object.assign({}, BLANK_FORM),
    kinds: caseKindOptions(),
    severities: caseSeverityOptions(),
    impacts: caseImpactOptions(),
    /** 已登记的受影响项（随案件一起提交） */
    links: [],
    /** 受影响项候选面板是否展开 */
    pickOpen: false,
    /** 候选是否已取过（区分"还没取"与"取了是空的"） */
    candLoaded: false,
    candidates: [],
    candHint: '',
    /** 提交中的禁用标记（防重复点击生成两个幂等键） */
    submitting: false,
    /** 上一次提交用的幂等键：**失败重试时复用**，改动内容后作废（见 onInput） */
    lastKey: ''
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
    // 冷启动 / 外部深链必须在本页自检：`go()` 管不到这条路径。判据只有一份 ——
    // `assignment_id` 的必需性与字符集写在 `routes.js` 的 `paramSchema` 里。
    const rawId = query && query.assignment_id != null ? String(query.assignment_id) : ''
    const gate = R.guardEntry(
      SELF + (rawId ? '?assignment_id=' + encodeURIComponent(rawId) : ''),
      { coldStart: R.currentDepth() <= 1 }
    )

    if (!gate.ok) {
      // 缺参 / 格式非法都是**编程或传播错误**，必须直接给 error 态，不能借
      // `viewState` 的兜底推断 —— 那会把编程错误伪装成一个正常业务状态。
      const errs = gate.errors || []
      const miss = errs.filter(function (e) {
        return String(e.reason).indexOf('缺少') === 0
      })
      this.applyState(
        {
          state: VIEW.ERROR,
          title: miss.length ? '缺少委托编号' : '委托编号不合法',
          hint: miss.length ? '请从委托详情页的「登记异常 / 变更」进入' : gate.reason
        },
        null
      )
      return
    }

    this.setData({ assignmentId: rawId })
    this.load()
  },

  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchAssignment(this.data.assignmentId)
      .then(function (res) {
        self.applyState(viewState({ status: 200, total: 1 }), decorateDetail(res))
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
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      detail: detail
    })
  },

  // ── 表单 ────────────────────────────────────────────────────────────

  /**
   * 字段输入。**同时作废上一次提交的幂等键** —— 用户改了内容就是一次新的意图；
   * 沿用旧键会让服务端把这次提交当成上一次的重放，用户看到的是"提交成功但内容没变"。
   */
  onInput(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const field = ds.field || ''
    if (!field) return
    const patch = { lastKey: '' }
    patch['form.' + field] = (e && e.detail && e.detail.value) || ''
    this.setData(patch)
  },

  /** 选择条（案件类型 / 严重度 / 影响类型 / 截止日期都是"选中一个值"，同一入口） */
  onPick(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const field = ds.field || ''
    const value = ds.key != null ? String(ds.key) : ''
    if (!field || !value) return
    const patch = { lastKey: '' }
    patch['form.' + field] = value
    this.setData(patch)
  },

  onDueChange(e) {
    this.setData({ 'form.due_at': (e && e.detail && e.detail.value) || '', lastKey: '' })
  },

  onClearDue() {
    this.setData({ 'form.due_at': '', lastKey: '' })
  },

  // ── 受影响项 ────────────────────────────────────────────────────────

  /** 展开 / 收起候选面板；第一次展开时懒加载候选 */
  onToggleLinks() {
    const open = !this.data.pickOpen
    this.setData({ pickOpen: open })
    if (open && !this.data.candLoaded) this.loadCandidates()
  },

  loadCandidates() {
    const self = this
    const id = this.data.assignmentId
    this.setData({ candHint: '正在读取本单的任务与成果…' })
    // 类型注册表失败不该让候选整体不可用 —— 它只影响成果那一行的副标题，
    // 所以它自己 catch 成空表，不参与 Promise.all 的失败判定。
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
        // 失败也置 candLoaded：否则每次展开都会重发一次注定失败的请求。
        self.setData({
          candLoaded: true,
          candidates: [],
          candHint: status
            ? '候选读取失败（接口返回 ' + status + '）'
            : '候选读取失败：请确认后端已启动'
        })
      })
  },

  onAddLink(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const kind = ds.kind || ''
    const id = ds.id != null ? String(ds.id) : ''
    if (!kind || !id) return
    // 去重：同一目标登记两次在写端是 409（唯一约束），在界面上则只会让人困惑
    const dup = this.data.links.some(function (it) {
      return it.target_kind === kind && String(it.target_id) === id
    })
    if (dup) {
      wx.showToast({ title: '这一条已经加过了', icon: 'none' })
      return
    }
    // `text` / `sub` 一并存下来：它们是**用户点的那一行**显示的说法，
    // 提交时只发 `target_kind` / `target_id`（`caseCreateBody` 会过滤）。
    // 不在这里回头去查候选表：候选面板关了以后那份清单就不该再被依赖。
    const picked = (this.data.candidates || []).filter(function (it) {
      return it.target_kind === kind && String(it.target_id) === id
    })[0] || {}
    const links = this.data.links.concat([
      {
        key: kind + '-' + id,
        target_kind: kind,
        target_id: id,
        text: picked.text || ('#' + id),
        sub: picked.sub || ''
      }
    ])
    this.setData({ links: links, lastKey: '' })
  },

  onRemoveLink(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const key = ds.key || ''
    const links = this.data.links.filter(function (it) {
      return it.key !== key
    })
    this.setData({ links: links, lastKey: '' })
  },

  // ── 提交 ────────────────────────────────────────────────────────────

  onSubmit() {
    const self = this
    if (this.data.submitting) return
    const check = caseCreateBody(
      Object.assign({}, this.data.form, { links: this.data.links })
    )
    if (!check.ok) {
      // 前置检查的结论要**一次说完**：只报第一条会让用户改一条、再来一次。
      wx.showModal({
        title: '还不能提交',
        content: check.errors.join('\n'),
        showCancel: false
      })
      return
    }

    // 幂等键：同一次提交的重试复用，改动内容后由 onInput/onPick 置空重新生成。
    const key = this.data.lastKey || newIdempotencyKey('case')
    this.setData({ submitting: true, lastKey: key })
    wx.showLoading({ title: '登记中', mask: true })
    return createCase(this.data.assignmentId, check.body, key)
      .then(function (res) {
        wx.hideLoading()
        self.setData({ submitting: false, lastKey: '' })
        const newId = res && res.case_id != null ? String(res.case_id) : ''
        if (!newId) {
          // 建成了但拿不到编号：**不能假装成功**，否则用户不知道去哪找这宗案件。
          wx.showModal({
            title: '已提交',
            content: '案件已登记成功，但没有返回案件编号。请回到委托详情页的「异常与变更」槽查看。',
            showCancel: false
          })
          return
        }
        wx.showToast({ title: '已登记案件 #' + newId, icon: 'success' })
        // replace（不是 push）：返回键不该把用户带回一张已经提交过的表单。
        // 这条边在 routes.js 里声明为 `replace`，go() 据此走 redirectTo。
        R.go('/pages/entrust/case/case?case_id=' + encodeURIComponent(newId), {
          from: SELF,
          ctx: { orgId: self.data.detail && self.data.detail.orgId }
        })
      })
      .catch(function (err) {
        wx.hideLoading()
        // 保留 lastKey：下一次点提交是同一件事的重试，必须复用同一个键。
        self.setData({ submitting: false })
        const w = caseWriteError(err)
        wx.showModal({ title: w.title, content: w.hint, showCancel: false })
      })
  },

  // ── 导航 ────────────────────────────────────────────────────────────

  /** 有未保存内容吗（路由层据此决定要不要拦下"替换/重置栈"这类会丢内容的动作） */
  hasUnsaved() {
    const f = this.data.form || {}
    return !!(
      String(f.title || '').trim() ||
      String(f.cause || '').trim() ||
      String(f.proposed_action || '').trim() ||
      String(f.due_at || '').trim() ||
      (this.data.links || []).length
    )
  },

  onBack() {
    const self = this
    const leave = function () {
      // 冷启动 / 消息深链进入时栈里只有本页，`navigateBack` 会**静默失败**
      // （「点了返回没反应」）。回入口重走身份链路。
      if (R.currentDepth() <= 1) {
        R.go('/pages/index/index', { from: SELF })
        return
      }
      wx.navigateBack({ delta: 1 })
    }
    if (!this.hasUnsaved()) {
      leave()
      return
    }
    wx.showModal({
      title: '放弃已填内容？',
      content: '这一页的登记内容还没有提交，返回后会丢失。',
      confirmText: '放弃',
      cancelText: '继续填写',
      success: function (res) {
        if (res.confirm) {
          // 用户已明确放弃，先清掉再走 —— 否则 leave() 内部的判断路径会再拦一次
          self.setData({ form: Object.assign({}, BLANK_FORM), links: [], lastKey: '' })
          leave()
        }
      }
    })
  },

  onRetry() {
    this.load()
  },

  onRelogin() {
    R.go('/pages/index/index', { from: SELF })
  }
})
