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
  decorateManagerRelease,
  decorateRevisions,
  fetchArtifact,
  fetchArtifactTypes,
  fetchEntrustmentOfferReleases,
  fetchRevisions,
  fieldDrafts,
  isArtifactDirty,
  newIdempotencyKey,
  releaseOffer,
  releasedRevisionFor,
  viewState,
  withdrawOffer
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
    /**
     * 页内确认条当前展开的版本号（0＝都收起）。
     *
     * 「设为生效版本」是一次不可含糊的确认，但**不能用 `wx.showModal`**：
     * 原生弹层的确认键不在渲染树里 ⇒ 走查工具点不到 ⇒ 这条关键路径拿不到
     * 设备证据（技能 `miniapp-device-walkthrough` 的负面清单实测过）。
     */
    confirmRevNo: 0,
    confirmText: '',
    revisions: [],
    /** 编辑态：字段值放 `formFields[].text`，模板不拼动态键名 */
    editing: false,
    formFields: [],
    dirty: false,
    saveError: '',
    /** 保存成功后的提示（**必须说清生效版本有没有变**） */
    saveNotice: '',

    // ── 对客发布（经理侧；S3 纵向切片 / BP-03 第 5/8/10 条）────────────────
    /**
     * 该成果所属的**委托授权** id。
     *
     * 发布走的是 `/entrustments/{eid}/offer-releases`，路径参数是**授权**而不是成果 ——
     * 这不是设计失误，而是权限的作用域本来就是"某货主在某组织下的授权"。
     * 取值来自成果详情的 `entrustment_id`（服务端返回），不由调用方声明。
     */
    entrustmentId: '',
    /** 该成果的发布记录（经理视角：含客户快照、来源门槛与响应） */
    releases: [],
    /**
     * 页内确认条当前展开的**待发布版本号**（0＝都收起）。
     *
     * 用页内确认条而不用 `wx.showModal`：发布是"客户将看到并据以决定"的动作，
     * 且发布后旧版本会被**显式取代**（不可回退地改变客户手上那份的可响应状态）。
     * 原生弹层的确认键不在渲染树里 ⇒ 工具点不到 ⇒ 这条主演示路径拿不到设备证据。
     */
    releaseRevNo: 0,
    releaseText: '',
    /** 本次发布**明确授权**给客户的附件 id（逗号分隔；留空＝不授权任何附件） */
    releaseAtts: '',
    releaseHint: '',
    releasing: false,
    /**
     * 撤回确认条展开在哪一条发布上（`''` ＝都收起）。
     * 存**字符串**：装饰后的 `releaseId` 由 `_sid()` 统一成字符串，
     * 用数字与它比较会永远不等（0 !== '0'）—— 表现为"点了撤回没反应"。
     */
    withdrawReleaseId: '',
    withdrawReason: '',
    withdrawHint: ''
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
        // 发布记录与成果**分开取、失败自己消化**：两者的可见性口径不同
        // （发布列表要 `entrust:view`，成果详情只要在授权链上）。取不到发布记录
        // 不能让人连成果都看不了 —— 那是把"少一块信息"说成"页面坏了"。
        const eid = raw.entrustment_id
        const rel = eid
          ? fetchEntrustmentOfferReleases(eid).catch(function () {
              return null
            })
          : Promise.resolve(null)
        return rel.then(function (relRes) {
          const releases = ((relRes && relRes.items) || []).map(decorateManagerRelease)
          self.applyState(viewState({ status: 200, total: 1 }), artifact, revisions, releases, eid)
        })
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

  applyState(state, artifact, revisions, releases, entrustmentId) {
    const list = releases || []
    // 把"这一版发过没有"算在**版本行**上：发布是按**精确版本**做的，
    // 不给出版本级的结论，用户就只能猜"我发的到底是哪一版"。
    //
    // ⚠️ 必须用 `releasedRevisionFor(release, **本成果**, 版本号)` ——
    //    `list` 是**整条授权**的发布（可能含别的成果），而版本号只在成果**内部**唯一。
    //    只比版本号 ⇒ 别的成果发过 v1 就把本成果的 v1 也标成已发布 ⇒
    //    **不给发布入口**、用户发不出去，而页面上看不出为什么（静默）。
    //    2026-09-18 设备走查抓到（见 `utils/entrust.js` 的 `releasedRevisionFor`）。
    const rows = (revisions || []).map(function (r) {
      const rel = releasedRevisionFor(list, artifact && artifact.artifactId, r.revisionNo)
      return Object.assign({}, r, {
        releaseId: rel ? rel.releaseId : '',
        releaseStatusLabel: rel ? rel.statusLabel : '',
        releaseStatusClass: rel ? rel.statusClass : '',
        releaseDecisionLabel: rel ? rel.decisionLabel : '',
        releaseResponseNote: rel ? rel.responseNote : '',
        // 已经发出去、还在等客户确认 ⇒ 不再给"发布这一版"按钮：
        // 重复发布同一版只会把客户手上那份取代掉，没有任何收益。
        published: !!rel && rel.status === 'released',
        canWithdraw: !!rel && rel.canWithdraw
      })
    })
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      artifact: artifact,
      revisions: rows,
      releases: list,
      entrustmentId:
        entrustmentId === null || entrustmentId === undefined ? '' : String(entrustmentId),
      editing: false,
      formFields: [],
      dirty: false,
      saveError: '',
      releaseRevNo: 0,
      releaseText: '',
      releaseAtts: '',
      releaseHint: '',
      withdrawReleaseId: '',
      withdrawReason: '',
      withdrawHint: ''
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
    const card = confirmCard(this.data.artifact || {}, revisionNo)
    // **页内二次确认**（2026-09-17 由 `wx.showModal` 改过来）。
    //
    // 原生弹层的确认键不在小程序渲染树里 ⇒ 走查工具点不到它（技能
    // `miniapp-device-walkthrough` 的负面清单实测过），而合同主演示第 3 步
    // 「更正一个字段 → 从工作台打开同一份成果」要先让更正的版本**生效**
    // —— 用弹层的话这条路径永远拿不到设备证据。
    // 受理 / 应用变更 / 记录任务三处已在更早的切片改过，这里是同一取向的第四处。
    //
    // 文案仍由 `confirmCard()` 生成（静态脚本断言它必须带成果 ID 与精确版本）。
    this.setData({
      confirmRevNo: revisionNo,
      confirmText: card.title + '：' + card.target + '。' + card.body
    })
  },

  /** 取消页内确认：只收起，不改任何状态（确认动作本身没发生过） */
  onConfirmCancel() {
    this.setData({ confirmRevNo: 0, confirmText: '' })
  },

  /** 确认切换生效版本（前一动作已在页内问过，这里不再开任何弹层） */
  onConfirmSubmit() {
    const revisionNo = Number(this.data.confirmRevNo)
    if (!revisionNo) return
    this.setData({ confirmRevNo: 0, confirmText: '' })
    return this.submitConfirm(revisionNo)
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

  // ── 对客发布（S3 / BP-03 第 5/8/10 条）──────────────────────────────
  //
  // 三条口径在本页的落点：
  // ① **发布的是"精确的那一版"**：入口挂在版本行上，带的是那一行的 `revision_no`。
  //    后端也没有"不传版本就发最新"的分支，前端若按"最新版"发起，两边就会分叉。
  // ② **授权附件是发布的一部分**：在这里明确写下客户**可以下载哪几份** ——
  //    留空就等于"不授权任何附件"，而不是"自动全给"（默认必须是最小权限）。
  // ③ **发布的结果要在页面上留下痕迹**：发完重取，版本行上直接显示"待客户确认／
  //    客户已接受"，而不是只弹一个"发布成功"（后者让人无从确认客户到底收到了什么）。

  /** 展开「发布这一版」的页内确认条。`revisionNo` 来自被点的那一行。 */
  onRelease(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const revisionNo = Number(ds.no)
    if (!revisionNo || !this.data.entrustmentId) return
    this.setData({
      releaseRevNo: revisionNo,
      releaseText:
        '发布 v' +
        revisionNo +
        ' 给客户 —— 客户将看到这一版的「客户白名单投影」，并据此决定接受或拒绝。' +
        '同一成果此前仍待确认的发布会被「显式取代」；已被客户响应过的版本不受影响。',
      releaseAtts: '',
      releaseHint: ''
    })
  },

  onReleaseAttsInput(e) {
    this.setData({ releaseAtts: (e && e.detail && e.detail.value) || '', releaseHint: '' })
  },

  onReleaseCancel() {
    this.setData({ releaseRevNo: 0, releaseText: '', releaseAtts: '', releaseHint: '' })
  },

  /** 解析"授权附件"输入：逗号/空格/中文逗号分隔的整数 id。非法即**拒发**并说清。 */
  parseAttachmentIds(text) {
    const raw = String(text || '').trim()
    if (!raw) return { ok: true, ids: [] }
    const parts = raw.split(/[,，\s]+/).filter(function (x) {
      return x !== ''
    })
    const ids = []
    for (let i = 0; i < parts.length; i += 1) {
      const n = Number(parts[i])
      if (!Number.isInteger(n) || n <= 0) {
        return { ok: false, ids: [], bad: parts[i] }
      }
      if (ids.indexOf(n) === -1) ids.push(n)
    }
    return { ok: true, ids: ids }
  },

  onReleaseSubmit() {
    const revisionNo = Number(this.data.releaseRevNo)
    if (!revisionNo) return Promise.resolve()
    const parsed = this.parseAttachmentIds(this.data.releaseAtts)
    if (!parsed.ok) {
      this.setData({ releaseHint: '授权附件只能是正整数 id，无法识别：' + parsed.bad })
      return Promise.resolve()
    }
    return this.submitRelease(revisionNo, parsed.ids)
  },

  submitRelease(revisionNo, attachmentIds) {
    const self = this
    if (this.data.releasing) return Promise.resolve()
    this.setData({ releasing: true, releaseHint: '' })
    wx.showLoading({ title: '发布中', mask: true })
    return releaseOffer(
      this.data.entrustmentId,
      {
        artifact_id: Number(this.data.artifactId),
        revision_no: revisionNo,
        // `null` 与 `[]` 在这里**同义**（都不授权任何附件）；显式给 null
        // 避免后端把空数组当成"清单是空的但存在"这种含糊状态。
        authorized_attachment_ids: attachmentIds.length ? attachmentIds : null
      },
      newIdempotencyKey('offer-release')
    )
      .then(function (res) {
        wx.hideLoading()
        const superseded = (res && res.superseded_release_ids) || []
        self.setData({ releasing: false, releaseRevNo: 0, releaseText: '', releaseAtts: '' })
        wx.showToast({ title: 'v' + revisionNo + ' 已发布', icon: 'success' })
        return self.load().then(function () {
          // ⚠️ `load()` 会把提示清掉，所以在重载**之后**放回来：这句话里
          //    "取代了哪几条"是本次操作的真实后果，用户需要看到。
          self.setData({
            saveNotice:
              'v' + revisionNo + ' 已发布给客户' +
              (superseded.length ? '，同时取代了此前的发布 #' + superseded.join('、#') : '')
          })
        })
      })
      .catch(function (err) {
        wx.hideLoading()
        const status = (err && err.httpStatus) || 0
        const detail = (err && err.detail) || ''
        // 400 里最常见的是**来源门槛**（未核验的提案不得作为已发布依据）。
        // 服务端会带回待核验清单；这里把清单也显示出来 —— 只说"不能发布"是没法干活的。
        let hint = '发布未提交'
        if (status === 400 && detail && typeof detail === 'object') {
          const pend = (detail.pending_sources || []).map(function (p) {
            return p.kind + ':' + p.ref
          })
          const rej = (detail.rejected_sources || []).map(function (p) {
            return p.kind + ':' + p.ref
          })
          hint =
            (detail.message || '来源门槛未过') +
            (pend.length ? '｜待核验：' + pend.join('、') : '') +
            (rej.length ? '｜判定不可用：' + rej.join('、') : '')
        } else if (status === 409) {
          hint = '这一版当前不能发布（可能是已被客户响应过的旧版本）'
        } else if (status) {
          hint = '发布未提交（服务端返回 ' + status + (detail ? '：' + detail : '') + '）'
        } else {
          hint = '发布未提交：网络异常（可重试）'
        }
        self.setData({ releasing: false, releaseHint: hint })
        return null
      })
  },

  // ── 撤回发布 ────────────────────────────────────────────────────────
  //
  // 只有**未被客户响应**的发布能撤回（后端 409 兜住"已接受不能被抹掉"）。
  // 这里是显式撤回的入口：不撤回就只能靠"再发一版"来改变客户手上的内容，
  // 而"再发一版"会在客户侧留下两份可响应记录 —— 那正是要避免的歧义。

  onWithdraw(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const releaseId = ds.id === null || ds.id === undefined ? '' : String(ds.id)
    if (!releaseId) return
    this.setData({ withdrawReleaseId: releaseId, withdrawReason: '', withdrawHint: '' })
  },

  onWithdrawReasonInput(e) {
    this.setData({ withdrawReason: (e && e.detail && e.detail.value) || '', withdrawHint: '' })
  },

  onWithdrawCancel() {
    this.setData({ withdrawReleaseId: 0, withdrawReason: '', withdrawHint: '' })
  },

  onWithdrawSubmit() {
    const releaseId = String(this.data.withdrawReleaseId || '')
    const reason = String(this.data.withdrawReason || '').trim()
    if (!releaseId) return Promise.resolve()
    if (!reason) {
      // 理由必填不是形式主义：客户侧会看到"这份报价已被撤回"，
      // 而"为什么"只能来自这一句；不填就等于让客户自己去猜。
      this.setData({ withdrawHint: '撤回必须写明理由（客户会看到这条说明）' })
      return Promise.resolve()
    }
    return this.submitWithdraw(releaseId, reason)
  },

  submitWithdraw(releaseId, reason) {
    const self = this
    this.setData({ releasing: true, withdrawHint: '' })
    wx.showLoading({ title: '撤回中', mask: true })
    return withdrawOffer(releaseId, { reason: reason }, newIdempotencyKey('offer-withdraw'))
      .then(function () {
        wx.hideLoading()
        self.setData({ releasing: false, withdrawReleaseId: '', withdrawReason: '' })
        wx.showToast({ title: '已撤回', icon: 'success' })
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        const status = (err && err.httpStatus) || 0
        self.setData({
          releasing: false,
          withdrawHint:
            status === 409
              ? '这条发布已被客户响应，不能撤回（接受事实永久保留；要改只能发新版本）'
              : status
                ? '撤回未成功（服务端返回 ' + status + '）'
                : '撤回未成功：网络异常'
        })
        return null
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
