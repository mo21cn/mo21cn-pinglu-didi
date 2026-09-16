// 委托发货 · 成果详情（查看 / 编辑 / 确认）（UI-05 第二片 / ENT-023）
//
// 本页是**人与确认的落点**。首片（ENT-021）把七槽位与「当前成果 · vN」显示出来了，
// 但成果只能看；§3.8 要求"范围内槽位必须有可用人工操作（编辑、记录、确认）"，
// 成果这一侧缺的正是后两项。所以本页不新造机制 —— 后端
// `POST /artifacts/{id}/revisions`（编辑＝追加版本）与 `/confirm`（绑定精确版本）
// 早已存在且测试扎实，缺的是入口。
//
// 三条语义必须**在界面上说出来**，不能只靠后端保证：
//
// ① **编辑不改变生效版本**。保存成功后提示必须是
//    「已保存为 vN，生效版本仍是 vM」——只显示"保存成功"会让用户以为客户
//    看到的已经是新内容。后端 `append_revision` 的返回里
//    `superseding_current: false` 就是这个事实，本页如实转述。
// ② **确认绑定精确版本**。确认卡上带 `成果 #N · vK`（PRD 第 187/188 行：
//    对话与工作台引用同一 artifact ID 与版本）。卡文案由契约层的 `confirmCard()`
//    生成并在静态脚本里断言。
// ③ 历史版本**可以**被重新确认为生效版本（后端如此设计，"确认不必是最新"）。
//    本页不禁止它 —— 前端禁止就与后端语义分叉，而分叉的那一侧迟早不同步。
//
// ENT-019 起的运行期导航治理同样适用于本页：`onLoad` 走 `guardEntry()`，
// 返回 / 回首页走 `go()`，且本页登记在 `MIGRATED_PAGES` 中（CI 会核对不得再出现
// 裸 `wx.navigateTo / redirectTo / reLaunch`）。
const {
  VIEW,
  appendRevision,
  buildPayload,
  confirmArtifact,
  confirmCard,
  decorateArtifact,
  decorateRevisions,
  fetchArtifact,
  fetchArtifactTypes,
  fetchRevisions,
  fieldDrafts,
  isArtifactDirty,
  newIdempotencyKey,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

const SELF = 'pages/entrust/artifact/artifact'

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    artifactId: '',
    artifact: null,
    revisions: [],
    /** 编辑态：字段值放 `formFields[].text`，模板不拼动态键名 */
    editing: false,
    formFields: [],
    dirty: false,
    saveError: '',
    /** 保存成功后的提示（**必须说清生效版本有没有变**） */
    saveNotice: ''
  },

  onLoad(query) {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })

    // ⚠️ 归一化后再重建 url（见 `routes.decodeParam`）：`onLoad` 拿到的是未解码串，
    //    再编码一次就是二次编码。本页是 ASCII id，行为不变；统一写法防将来带中文时重演。
    const rawId = R.decodeParam(query && query.artifact_id)
    const gate = R.guardEntry(
      SELF + (rawId ? '?artifact_id=' + encodeURIComponent(rawId) : ''),
      { coldStart: R.currentDepth() <= 1 }
    )

    if (!gate.ok) {
      // 缺参 / 格式非法是**编程或传播错误**：按错误态显示，而不是留一片空白。
      // 也不能借 viewState 兜底 —— `status=0 且 netError=false` 会被它判成"空列表"，
      // 于是"链接是错的"被显示成"这个成果没有内容"，正好掩盖了真正的问题。
      const errs = gate.errors || []
      const miss = errs.filter(function (e) {
        return String(e.reason).indexOf('缺少') === 0
      })
      this.applyState(
        {
          state: VIEW.ERROR,
          title: miss.length ? '缺少成果编号' : '成果编号不合法',
          hint: miss.length ? '请从委托工作台的槽位进入' : gate.reason
        },
        null,
        null
      )
      return
    }

    this.setData({ artifactId: rawId })
    this.load()
  },

  /**
   * 取数：注册表 + 成果详情 + 版本历史。
   *
   * 三个请求一起发、任一失败即整体失败。为什么**不需要**注册表也要一起等：
   * 字段中文标签与必填/选填分组都来自注册表，缺了它就只能显示英文键 ——
   * 「显示半个成果」（比如只有原始键名）比直接说加载失败更让人困惑。
   */
  load() {
    const self = this
    const id = this.data.artifactId
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return Promise.all([fetchArtifactTypes(), fetchArtifact(id), fetchRevisions(id)])
      .then(function (res) {
        const types = (res[0] && res[0].items) || []
        const raw = res[1] || {}
        const spec = types.filter(function (t) {
          return t && t.code === raw.artifact_type
        })[0] || null
        const artifact = decorateArtifact(raw, spec)
        const revisions = decorateRevisions((res[2] && res[2].items) || [], artifact.currentRevisionId)
        // 编辑的起点是**当前生效版本**：这是"改这份内容"的语义，
        // 而不是"照表单重建一份"（后者会静默丢掉未声明的历史字段）。
        self._basePayload = (raw.current_revision && raw.current_revision.payload) || {}
        self._fields = artifact.fields
        self._initial = fieldDrafts(artifact.fields)
        self.applyState(viewState({ status: 200, total: 1 }), artifact, revisions)
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

  applyState(state, artifact, revisions) {
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      artifact: artifact,
      revisions: revisions || [],
      editing: false,
      formFields: [],
      dirty: false,
      saveError: ''
    })
  },

  // ── 编辑 ─────────────────────────────────────────────────────────────

  onEdit() {
    // 进入编辑态：把当前值填进表单。用**平坦数组 + text** 而不是 `drafts[name]`，
    // 因为 WXML 的动态键取值 `{{obj[key]}}` 在各基础库版本上表现不一致，
    // 而"输入框拿到的是 undefined"只会表现为一片空白，很难归因。
    // 白名单必须**显式带上** `declared` 与 `kindHint`：模板要按 `item.kindHint`
    // 显示"列表（JSON 数组）"。少传一个键不会报错，只会让那行提示**永远不渲染** ——
    // 而缺值的列表字段与单行文本框长得一模一样，提示不显示就等于这个缺口没修。
    const fields = (this._fields || []).map(function (f) {
      return {
        name: f.name,
        label: f.label,
        required: f.required,
        internal: f.internal,
        unknown: f.unknown,
        kind: f.kind,
        declared: f.declared,
        kindHint: f.kindHint,
        text: f.value
      }
    })
    this.setData({ editing: true, formFields: fields, dirty: false, saveError: '', saveNotice: '' })
  },

  onCancelEdit() {
    this.setData({ editing: false, formFields: [], dirty: false, saveError: '' })
  },

  onFieldInput(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const idx = Number(ds.idx)
    const value = (e && e.detail && e.detail.value) || ''
    const patch = {}
    patch['formFields[' + idx + '].text'] = value
    this.setData(patch)
    this.setData({ dirty: this.computeDirty(), saveError: '' })
  },

  computeDirty() {
    const drafts = {}
    ;(this.data.formFields || []).forEach(function (f) {
      drafts[f.name] = f.text
    })
    return isArtifactDirty(this._fields || [], drafts, this._initial || {})
  },

  onSave() {
    const self = this
    const drafts = {}
    ;(this.data.formFields || []).forEach(function (f) {
      drafts[f.name] = f.text
    })
    const built = buildPayload(this._basePayload || {}, this._fields || [], drafts)
    if (!built.ok) {
      // JSON 解析失败：**不提交**，也不把用户输入清掉 —— 让人改到能过为止
      this.setData({ saveError: built.errorHint })
      return
    }
    const prevNo = this.data.artifact ? this.data.artifact.currentRevisionNo : null
    wx.showLoading({ title: '保存中', mask: true })
    const key = newIdempotencyKey('art-rev')
    return appendRevision(this.data.artifactId, { payload: built.payload }, key)
      .then(function (res) {
        wx.hideLoading()
        const no = res && res.revision_no ? res.revision_no : ''
        // 后半句是重点：生效版本**没有**因为编辑而改变。少说这一句，
        // 用户会以为客户看到的已经换成新内容了。
        const notice = prevNo === null || prevNo === undefined
          ? '已保存为 v' + no + '（当前还没有生效版本，确认后才会生效）'
          : '已保存为 v' + no + '，生效版本仍是 v' + prevNo + '（需确认才会切换）'
        self.setData({ editing: false, formFields: [], dirty: false, saveNotice: notice })
        wx.showToast({ title: '已保存 v' + no, icon: 'success' })
        return self.load().then(function () {
          // load() 会把提示清掉，这里在重载之后再放回来（否则用户根本看不到）
          self.setData({ saveNotice: notice })
        })
      })
      .catch(function (err) {
        wx.hideLoading()
        // 服务端已给出可定位的原因（无权限 / 已作废 / 未知类型 / 校验），
        // 请求层会提示；这里只兜网络层，避免静默失败。
        if (!(err && err.httpStatus)) {
          wx.showToast({ title: '保存失败：网络异常', icon: 'none' })
        }
      })
  },

  // ── 确认（绑定精确版本）──────────────────────────────────────────────

  /**
   * 把某个版本确认为生效版本。`revisionNo` 来自被点的那一行的 dataset ——
   * 即用户**看到并点中**的那个版本号，不是"再取一次最新"。
   */
  onConfirm(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const revisionNo = Number(ds.no)
    if (!revisionNo) return
    const self = this
    const card = confirmCard(this.data.artifact || {}, revisionNo)
    // 用弹层而不是页内卡片：这是一次**不可含糊的确认动作**，需要一个明确的
    // 是/否出口；页内卡片容易被误当成又一屏内容。文案里的 `成果 #N · vK`
    // 由 confirmCard() 生成（静态脚本断言它必须带 ID 与精确版本）。
    wx.showModal({
      title: card.title,
      content: card.target + '\n\n' + card.body,
      success: function (res) {
        if (!res.confirm) return
        self.submitConfirm(revisionNo)
      }
    })
  },

  submitConfirm(revisionNo) {
    const self = this
    wx.showLoading({ title: '确认中', mask: true })
    return confirmArtifact(this.data.artifactId, revisionNo, newIdempotencyKey('art-confirm'))
      .then(function () {
        wx.hideLoading()
        wx.showToast({ title: 'v' + revisionNo + ' 已生效', icon: 'success' })
        return self.load().then(function () {
          self.setData({ saveNotice: '生效版本已切换为 v' + revisionNo })
        })
      })
      .catch(function (err) {
        wx.hideLoading()
        if (!(err && err.httpStatus)) {
          wx.showToast({ title: '确认失败：网络异常', icon: 'none' })
        }
      })
  },

  onRetry() {
    this.load()
  },

  /** 返回：编辑中且有改动时先确认（不静默丢掉用户刚敲的内容） */
  onBack() {
    const self = this
    const leave = function () {
      if (R.currentDepth() <= 1) {
        R.go('/pages/index/index', { from: SELF })
        return
      }
      wx.navigateBack({ delta: 1 })
    }
    if (!this.data.editing || !this.data.dirty) {
      leave()
      return
    }
    wx.showModal({
      title: '放弃未保存的编辑？',
      content: '该成果的这一版改动还没有保存，返回后不会保留。',
      success: function (res) {
        if (res.confirm) leave()
      }
    })
  },

  onRelogin() {
    R.go('/pages/index/index', { from: SELF })
  }
})
