// 委托发货 · 专业会话屏（UI-03 / DR-0015 / ENT-033 / **S2 首片 + 第二片 + 第三片**）
//
// 本页三件事，都得是真的：
//
// 1. **AC-05 的聊天侧**：成果卡与工作台、成果页**同源同版本** —— 字段来自
//    `utils/entrust.js` 的 `decorateArtifact`，本页不复写任何字段名映射。
// 2. **S2 首片 + 第二片**（HO 0917-2 定为首要任务）：
//    已认领委托 → 进入专属会话 → 发送报价文本 / 引用附件 → 创建并执行**持久化作业** →
//    展示结果或明确失败 → 提案**人工采纳**为成果 → 离开后重新进入仍可恢复。
// 3. **S2 第三片**：上传样报价单 → 提取文本 → 引用它让 AG-02 解析
//    （BP-02 的 attachment selection，也就是主演示脚本第 2 步）。
//
// 关键是"真"在哪：
//   * 会话、消息、作业、附件全部**落库**，由后端 `ent_session` / `ent_session_message` /
//     `ent_agent_job` / `ent_attachment` 承载；**不在页面里保存一份消息数组**当作聊天记录
//     （那叫假聊天：刷新就没，换个设备也没有，审计更无从谈起）。
//   * 发消息与跑作业是**两个动作**：`submitJob` 只建作业，`runJob` 才执行。
//     合成一步会让人以为"点了就跑完了"，而作业其实可能还在排队。
//   * 作业成功只产出**提案（envelope）**，不是成果（AC-09）。卡上必须写"提案"，
//     写成"解析结果"会让人以为已经落库。
//   * `mocked=true` 时后端走的是本地规则模板（fixture），结构与真实模式同构，
//     但**不是真实模型输出** —— 界面必须显示模式，不得拿它充当模型质量证据。
//   * ⚠️ **「已上传」≠「Agent 读得到」**：只有提取完成（`extract_status='done'`）的
//     附件才有文本进 Agent 的来源目录。上传后必须立刻提取，并把结果如实报出来 ——
//     否则用户以为"传上去就完事了"，而 Agent 那边只看到一个文件名。
//
// 运行期导航治理与其它委托页同口径：`onLoad` 走 `guardEntry()`，跳转走 `go()`，
// 本页登记在 `MIGRATED_PAGES`（CI 会核对不得出现裸 `wx.navigateTo` 等）。
const {
  SAMPLE_QUOTE_FILENAME,
  SAMPLE_QUOTE_TEXT,
  SAMPLE_SCAN_FILENAME,
  SAMPLE_SCAN_BASE64,
  VIEW,
  decorateArtifact,
  decorateJob,
  decorateMessage,
  decorateSession,
  decorateAttachment,
  fetchArtifactCandidates,
  fetchArtifactTypes,
  fetchEntrustmentAttachments,
  fetchJobs,
  fetchSession,
  fetchSessionContext,
  fetchSessions,
  adoptJobProposal,
  appendMessage,
  createSession,
  extractAttachment,
  extractStatusLabel,
  jobFailureText,
  newIdempotencyKey,
  runJob,
  submitJob,
  transcribeAttachment,
  uploadAttachment,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

const SELF = 'pages/entrust/session/session'

/** 成果卡上预览几个字段：卡是"引用"，不是表单（表单在成果页） */
const PREVIEW_FIELDS = 4

/**
 * 报价会话用的专业槽位：AG-02（agent_02＝报价 / 比价 / 组客户报价）。
 *
 * ⚠️ 抄的是后端 `envelope.SPECIALTY_AG02` 的**值**，不是自己起的名。
 * 专业码写错时建会话会被 400 拒，但这个错误在界面上会显示成"会话打不开"，
 * 与真正的原因差得很远 —— 所以这里把常量与出处一起写清楚。
 */
const SPECIALTY_QUOTE = 'agent_02'

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    assignmentId: '',
    sessionId: '',
    entrustmentId: '',
    sessionTitle: '',
    sessionStatusLabel: '',
    cards: [],
    messages: [],
    jobs: [],
    attachments: [],
    attachmentText: '',
    draft: '',
    /**
     * **对客报价金额**（元，可选）—— WP-1，2026-09-21。
     *
     * ⚠️ 此前这个入参只有接口能给（`miniapp` 全仓无 `amount`）⇒ "对客报价"这条
     *    业务事实在界面上**造不出来**，只能由开发者代填。
     * 留空 ＝ 只解析、不报价（后端 `ag02` 只在 `amount` 非空时才产出 `customer_quote`
     * 提案）；⛔ 不发明金额，⛔ 也不是"模型调用费"。
     */
    jobAmount: '',
    sending: false,
    /** 上传报价单：进行中标记（上传 + 提取是一串动作，全程只能有一个在跑） */
    uploading: false,
    attachNotice: '',
    /** 附件写动作（重抽 / 人工转录）的进行中标记 */
    attachBusy: false,
    /** 重抽：待确认覆盖的附件（人工转录会被覆盖，必须先问一次） */
    reextractId: '',
    /** 人工转录：正在录入的附件 + 草稿 */
    transcribeId: '',
    transcribeDraft: '',
    /** 引用附件跑作业时的进行中标记（与「发消息」区分开） */
    referencing: false,
    /** 采纳：待确认的作业与提案类型（页内确认条的两个键） */
    adoptingJobId: '',
    adoptingType: '',
    adopting: false,
    adoptNotice: '',
    /** fixture 模式提示（后端 mocked=true 时置真） */
    mockedNote: ''
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
    //    再编码一次就是二次编码。
    const rawId = R.decodeParam(query && query.assignment_id)
    const gate = R.guardEntry(
      SELF + (rawId ? '?assignment_id=' + encodeURIComponent(rawId) : ''),
      { coldStart: R.currentDepth() <= 1 }
    )

    if (!gate.ok) {
      // 缺参 / 格式非法是**编程或传播错误**：按错误态显示，而不是留一片空白
      const errs = gate.errors || []
      const miss = errs.filter(function (e) {
        return String(e.reason).indexOf('缺少') === 0
      })
      this.applyState({
        state: VIEW.ERROR,
        title: miss.length ? '缺少委托编号' : '委托编号不合法',
        hint: miss.length ? '请从委托工作台进入会话' : gate.reason
      })
      return
    }

    this.setData({ assignmentId: rawId })
    this.load()
  },

  /**
   * 取数四路并行：本单会话、本单作业、成果卡、成果类型注册表。
   *
   * ⚠️ 会话**可能还不存在**（第一次进）⇒ 先查、查不到再建。
   * 这正是后端给 `GET /sessions` 加 `assignment_id` 过滤的原因：
   * 页面从工作台进来时只有委托单号，没有会话号。
   */
  load() {
    const self = this
    const assignmentId = this.data.assignmentId
    return Promise.all([
      fetchSessions({ view: 'mine', assignmentId: assignmentId }).catch(function () {
        return { items: [] }
      }),
      fetchJobs({ assignmentId: assignmentId }).catch(function () {
        return { items: [] }
      }),
      fetchArtifactCandidates(assignmentId).catch(function () {
        return { items: [] }
      }),
      fetchArtifactTypes().catch(function () {
        return { items: [] }
      })
    ])
      .then(function (res) {
        const sessions = (res[0] && res[0].items) || []
        const jobs = (res[1] && res[1].items) || []
        const artifacts = (res[2] && res[2].items) || []
        // ⚠️ `fetchArtifactTypes()` 返回**接口载荷** `{items:[...]}`，不是数组 ——
        // 这里曾经写成 `res[1] || []` 再 `.forEach`，真机上一进本页就抛错并被吞成
        // 与真正原因无关的错误态（ENT-040）。
        const specs = (res[3] && res[3].items) || []
        const byCode = {}
        specs.forEach(function (s) {
          byCode[s.code] = s
        })
        const cards = artifacts.map(function (item) {
          const one = decorateArtifact(item, byCode[item.artifact_type] || {})
          return {
            artifactId: one.artifactId,
            typeLabel: one.typeLabel,
            statusLabel: one.statusLabel,
            currentRevisionNo: one.currentRevisionNo,
            preview: (one.fields || []).slice(0, PREVIEW_FIELDS).map(function (f) {
              return { name: f.name, label: f.label, value: f.value }
            })
          }
        })

        const decorated = jobs.map(decorateJob)
        const mocked = decorated.some(function (j) {
          return j.mocked
        })

        if (sessions.length) {
          // 已有会话 ⇒ 直接进（"离开后重新进入仍可恢复"就落在这里）
          const one = decorateSession(sessions[0])
          self.setData({
            sessionId: one.sessionId,
            entrustmentId: one.entrustmentId,
            sessionTitle: one.title,
            sessionStatusLabel: one.statusLabel,
            cards: cards,
            jobs: decorated,
            mockedNote: mocked ? '本页含 fixture 结果（LLM_MOCK），不是真实模型输出' : ''
          })
          return self.loadRest(one.sessionId)
        }
        // 本单还没有会话 ⇒ 先问服务端要授权上下文，再建。
        return self.ensureSession().then(function (sid) {
          self.setData({ cards: cards, jobs: decorated })
          // ⚠️ 建会话没成功时**直接返回**，不要再走 `loadRest('')`：
          // 那会用"空态"把上面刚给出的错误态盖掉，用户看到的就变成
          // "这单没有消息"，而真正的原因（定位不到授权 / 无权限）被吞掉。
          if (!sid) return undefined
          return self.loadRest(sid)
        })
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.applyState({
          state: VIEW.ERROR,
          title: '会话内容读取失败',
          hint: status ? '接口返回 ' + status : '请确认后端已启动'
        })
      })
  },

  /**
   * 第一次进入本单：先问服务端「该用哪条委托授权」，再建一个 AG-02 会话。
   *
   * ⚠️ 授权 id **只能来自 `session-context`**，不许在页面里从别的清单推。
   *
   * 这里曾经写成 `pickEntrustment(entrustments)` 再取 `one.entrustmentId` ——
   * 那是个**取错函数**的缺陷：`pickEntrustment` 是组织选择器用的，返回
   * `{orgId, needPick}`，于是 `entrustmentId` 恒为 `undefined`，页面**必然**
   * 掉进"没有可用的委托授权"这一支；而真原因是取错了函数，页面上完全看不出来。
   * 更不能改用的两条路：`/my-orgs` 只回成员身份（没有授权 id），
   * `/my-entrustments` 只回**货主自己授权出去**的授权（经理拿不到）。
   * 拿它们顶上就是"猜一个 id 去撞权限"，撞不中的表现是"能进页面但建不了会话"。
   */
  ensureSession() {
    const self = this
    return fetchSessionContext(this.data.assignmentId)
      .then(function (ctx) {
        const c = ctx || {}
        if (!c.entrustment_id) {
          // 服务端说"定位不到唯一一条授权"⇒ 如实照说（它已经给出是哪一种），
          // 不要退化成空态，也不要自己编一个 id 去试。
          self.applyState({
            state: VIEW.ERROR,
            title: '无法定位本单的委托授权',
            hint:
              c.note ||
              '请确认该委托已指定服务经营主体，且货主已把权限授权给该组织'
          })
          return ''
        }
        return createSession(
          c.entrustment_id,
          {
            assignment_id: Number(self.data.assignmentId),
            agent_specialty: SPECIALTY_QUOTE,
            title: '委托 #' + self.data.assignmentId + ' 报价会话'
          },
          newIdempotencyKey('sess')
        )
          .then(function (row) {
            const s = decorateSession(row)
            self.setData({
              sessionId: s.sessionId,
              // 附件挂在委托授权下 —— 不记下它，`loadRest` 就会拿 0 去请求而永远取不到
              entrustmentId: s.entrustmentId || String(c.entrustment_id),
              sessionTitle: s.title,
              sessionStatusLabel: s.statusLabel
            })
            return s.sessionId
          })
          .catch(function (err) {
            // 建会话失败（多半是缺 agent 作业权限）⇒ 如实报错，不假装"没有消息"。
            // 带上服务端状态码：403 与 500 是两件完全不同的事。
            const status = (err && err.httpStatus) || 0
            self.applyState({
              state: VIEW.ERROR,
              title: '会话创建失败',
              hint: status
                ? '接口返回 ' + status + '（建会话需要 entrust:agent:job 权限）'
                : '需要 entrust:agent:job 权限；只读成员不能让 Agent 干活'
            })
            return ''
          })
      })
      .catch(function (err) {
        // 上下文本身取不到：非参与方是 404，后端没起是网络错 —— 分开说。
        const status = (err && err.httpStatus) || 0
        self.applyState({
          state: VIEW.ERROR,
          title: '读取会话上下文失败',
          hint: status === 404 ? '本单对当前身份不可见' : status ? '接口返回 ' + status : '请确认后端已启动'
        })
        return ''
      })
  },

  /** 拉消息与附件（会话已确定时） */
  loadRest(sessionId) {
    const self = this
    if (!sessionId) {
      self.applyState(viewState({ status: 200, total: self.data.cards.length }))
      return Promise.resolve()
    }
    return Promise.all([
      fetchSession(sessionId).catch(function () {
        return null
      }),
      fetchEntrustmentAttachments(self.data.entrustmentId || 0).catch(function () {
        return { items: [] }
      })
    ]).then(function (res) {
      const detail = res[0]
      const atts = (res[1] && res[1].items) || []
      const messages = ((detail && detail.messages) || []).map(decorateMessage)
      const attachments = atts.map(decorateAttachment)
      // ⚠️ 摘要必须把「有几份」与「有几份 Agent 真读得到」分开：
      //    未提取的附件对 Agent 而言只是文件名，把它们算进"已提供给 Agent"
      //    是拿一件没发生的事当事实说。
      const done = attachments.filter(function (a) {
        return a.hasText
      }).length
      let attachmentText
      if (!attachments.length) {
        attachmentText = '本单暂无附件'
      } else if (done === attachments.length) {
        attachmentText = '本单附件 ' + attachments.length + ' 份，文本均已提取（随上下文提供给 Agent）'
      } else {
        attachmentText =
          '本单附件 ' +
          attachments.length +
          ' 份，其中已提取文本 ' +
          done +
          ' 份（未提取的附件对 Agent 只是文件名）'
      }
      self.setData({
        messages: messages,
        attachments: attachments,
        attachmentText: attachmentText
      })
      // ⚠️ 会话已存在时**不**让 total=0 落进"空状态"：空状态会把对话区整块藏掉，
      // 于是"还没有消息。粘贴一段报价文本…"这句引导也一起消失 ——
      // 新会话刚建好时正是最需要它的时候。有会话即按"就绪"渲染。
      const total = self.data.cards.length + messages.length + self.data.jobs.length
      self.applyState(viewState({ status: 200, total: Math.max(1, total) }))
    })
  },

  applyState(state) {
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint
    })
  },

  onDraft(e) {
    this.setData({ draft: (e && e.detail && e.detail.value) || '' })
  },

  /** 作业金额输入（页内；随「发送并解析」或「让 Agent 解析这份报价单」一起提交） */
  onJobAmountInput(e) {
    this.setData({ jobAmount: (e && e.detail && e.detail.value) || '' })
  },

  /**
   * 作业输入里的可选字段 —— **只在真的填了金额时才带**（WP-1）。
   *
   * ⛔ 字段缺席 ≠ 传 `null`：前者是"这次没报价"，后者会变成"我确认金额为空"。
   * ⛔ 币种不猜：与 `ag02` 的缺省口径一致（`currency` 缺省 `CNY`）。
   */
  jobAmountInput() {
    const amount = String(this.data.jobAmount == null ? '' : this.data.jobAmount).trim()
    if (!amount) return {}
    return { amount: amount, currency: 'CNY' }
  },

  /**
   * 发一条报价文本并让 Agent 解析。
   *
   * 三步分开，每步都可能失败、失败都要能说清楚：
   *   ① 追加消息（落库，作为事实）；② 提交作业（建 `queued` 行）；③ 推进一次（真执行）。
   * ⚠️ 不合成一步：合成之后"消息发出去了但作业没跑"这个状态就无从表达。
   */
  onSend() {
    const self = this
    const text = (this.data.draft || '').trim()
    const sessionId = this.data.sessionId
    if (!text || this.data.sending || !sessionId) return
    this.setData({ sending: true, draft: '' })
    appendMessage(sessionId, text, newIdempotencyKey('msg'))
      .then(function () {
        return submitJob(
          sessionId,
          { input: Object.assign({ quote_text: text }, self.jobAmountInput()) },
          newIdempotencyKey('job')
        )
      })
      .then(function (job) {
        const jid = job && (job.job_id || job.jobId)
        if (!jid) throw new Error('作业提交未返回 job_id')
        return runJob(jid).then(function () {
          return jid
        })
      })
      .then(function () {
        self.setData({ sending: false })
        return self.refresh()
      })
      .catch(function (err) {
        self.setData({ sending: false, draft: text })
        const status = (err && err.httpStatus) || 0
        wx.showToast({
          icon: 'none',
          title: status === 409 ? '作业已在执行中' : '发送失败' + (status ? '（' + status + '）' : '')
        })
      })
  },

  /** 刷新（发送后 / 手动下拉重进） */
  refresh() {
    const self = this
    const assignmentId = this.data.assignmentId
    return Promise.all([
      this.data.sessionId ? fetchSession(this.data.sessionId).catch(function () { return null }) : Promise.resolve(null),
      fetchJobs({ assignmentId: assignmentId }).catch(function () {
        return { items: [] }
      })
    ]).then(function (res) {
      const messages = ((res[0] && res[0].messages) || []).map(decorateMessage)
      const jobs = ((res[1] && res[1].items) || []).map(decorateJob)
      const mocked = jobs.some(function (j) {
        return j.mocked
      })
      self.setData({
        messages: messages,
        jobs: jobs,
        mockedNote: mocked ? '本页含 fixture 结果（LLM_MOCK），不是真实模型输出' : ''
      })
    })
  },

  onPullDownRefresh() {
    const self = this
    this.refresh().then(function () {
      wx.stopPullDownRefresh()
    }, function () {
      wx.stopPullDownRefresh()
    })
  },

  /** 作业失败原因（失败必须**明确**，不能只写"失败"） */
  jobFailure(job) {
    return jobFailureText(job)
  },

  /**
   * 采纳一份提案为成果（BP-02 的核心：**提案 → 人工采纳 → 同一份成果**）。
   *
   * 为什么分两步（先问、再确认）：采纳会**创建成果**，是本页唯一的写业务动作。
   * 而确认用**页内确认条**而不是 `wx.showModal`：原生弹层不在渲染树里
   * （选择器命中 0，工具点不到它的确认键）⇒ 关键路径永远拿不到设备证据。
   * 这与本项目「受理」入口已经是同一条处置。
   */
  onAdoptAsk(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const jobId = ds.job_id != null ? String(ds.job_id) : ''
    const want = ds.artifact_type ? String(ds.artifact_type) : ''
    if (!jobId || !want) return
    this.setData({ adoptingJobId: jobId, adoptingType: want, adoptNotice: '' })
  },

  onAdoptCancel() {
    this.setData({ adoptingJobId: '', adoptingType: '' })
  },

  onAdoptConfirm() {
    const self = this
    const jobId = this.data.adoptingJobId
    const want = this.data.adoptingType
    if (!jobId || !want || this.data.adopting) return
    const job = (this.data.jobs || []).filter(function (j) {
      return String(j.jobId) === jobId
    })[0]
    const prop = ((job && job.proposals) || []).filter(function (p) {
      return p.artifactType === want
    })[0]
    if (!prop) {
      // 找不到对应提案 ⇒ 页面状态与按钮不同步。如实报错，不要盲发一个空载荷
      // （那会在服务端产生一份内容为空的成果）。
      wx.showToast({ icon: 'none', title: '该提案已不在本页，请下拉刷新' })
      return
    }
    this.setData({ adopting: true })
    adoptJobProposal(
      jobId,
      // `payload` 原样提交：本页没有编辑态，就不伪造"已人工修改过的内容"。
      // 真正的字段级修正发生在成果页（那里有编辑与确认链路）。
      { artifact_type: want, payload: prop.payload, note: '人工确认后采纳' },
      newIdempotencyKey('adopt')
    )
      .then(function (row) {
        const aid = row && row.artifact_id != null ? String(row.artifact_id) : ''
        self.setData({ adopting: false, adoptingJobId: '', adoptingType: '' })
        // 重新走一遍取数：成果卡才会把**刚采纳的这一份**列出来
        // （`refresh()` 只刷消息与作业，不刷成果卡）。
        return self.load().then(function () {
          self.setData({
            adoptNotice: aid
              ? '已采纳为成果 #' + aid + '（与工作台、成果页是同一份）'
              : '已采纳为成果'
          })
        })
      })
      .catch(function (err) {
        self.setData({ adopting: false })
        const status = (err && err.httpStatus) || 0
        wx.showToast({
          icon: 'none',
          title:
            status === 400
              ? '该提案类型与该作业不匹配'
              : status === 409
                ? '作业尚未成功，暂不能采纳'
                : '采纳失败' + (status ? '（' + status + '）' : '')
        })
      })
  },

  /**
   * 选一份本地文件作为"样报价单"（BP-02 的 attachment selection / 演示第 2 步）。
   *
   * 用 `chooseMessageFile` 而不是 `chooseMedia`：报价单是**文档**不是图片。
   * 选完立刻走 `uploadQuote`，两者分开是为了让"取消选择"不产生任何请求。
   */
  onPickQuote() {
    const self = this
    if (this.data.uploading) return
    if (!this.data.sessionId) {
      // 附件要挂在**委托授权**下（参与方协作），而授权 id 来自会话上下文 ——
      // 会话还没就绪就上传，只会得到一个注定 400 的请求。
      wx.showToast({ icon: 'none', title: '会话尚未就绪，请稍后重试' })
      return
    }
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      success(res) {
        const f = (res.tempFiles || [])[0]
        if (!f || !f.path) return
        self.uploadQuote(f)
      },
      fail() {
        // 用户取消选择：不是错误，不提示、不报错
      }
    })
  },

  /**
   * 用**内置示例报价单**走同一套上传 / 提取链（不经过原生文件选择器）。
   *
   * 两条独立理由，任一成立都足够：
   *   1. **可走查**：`wx.chooseMessageFile` 打开的是 OS 级原生弹层，不在小程序渲染树里
   *      ⇒ 自动走查够不着它的选择项，"演示第 2 步"就永远拿不到设备证据
   *      （技能 `miniapp-device-walkthrough` 的负面清单把这条写成了**界面要求**）。
   *   2. **可演示**：演示者不必先把文件塞进模拟器才能演这一步。
   *
   * 刻意复用 `uploadQuote`：另写一份上传逻辑的话，"示例通路能过、真实通路没过"
   * 这种分叉会在演示当天才暴露。
   */
  onUseSampleQuote() {
    const self = this
    if (this.data.uploading) return
    if (!this.data.sessionId) {
      wx.showToast({ icon: 'none', title: '会话尚未就绪，请稍后重试' })
      return
    }
    const name = SAMPLE_QUOTE_FILENAME
    const path = wx.env.USER_DATA_PATH + '/' + name
    try {
      // 先落成真实文件，再走与"上传真文件"完全同一条链 —— 这样拿到的设备证据
      // 覆盖的是真实上传路径，而不是一条只为走查准备的旁路。
      wx.getFileSystemManager().writeFileSync(path, SAMPLE_QUOTE_TEXT, 'utf8')
    } catch (e) {
      wx.showToast({ icon: 'none', title: '写入示例文件失败：' + ((e && e.errMsg) || '') })
      return
    }
    return this.uploadQuote({ path: path, name: name })
  },

  /**
   * **拍照上传（调 OS 摄像头）** —— 扫描件/纸质报价单的入口。
   *
   * ⚠️ 范围到此为止：只调 `wx.chooseMedia({sourceType:['camera']})` 拿到一张图，
   * 然后走**与上传文件完全同一条链**。⛔ 不做 OCR、不做图像增强/裁剪/多页、
   * 不做"扫描件 → 可机读文本"的任何自动转换 —— 拿不到文本时的正确出路是
   * **人工转录**（页面已有的那一条），不是在这里偷偷塞一段识别。
   *
   * 为什么它有价值：图片没有机读文本层 ⇒ 后端如实判 `needs_transcription`
   * ⇒ 「人工转录」入口按状态出现。这就是 D1-05 附件分支的**真机通路**。
   *
   * ⚠️ 诚实边界：摄像头是 **OS 级弹层**，自动走查够不着它（与该页的
   * `chooseMessageFile` 同族）⇒ 该格只能记 `LIMITATION`，取证走下面的"内置扫描件样本"。
   */
  onCaptureScan() {
    const self = this
    if (this.data.uploading) return
    if (!this.data.sessionId) {
      wx.showToast({ icon: 'none', title: '会话尚未就绪，请稍后重试' })
      return
    }
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera'],
      camera: 'back',
      success(res) {
        const f = (res.tempFiles || [])[0]
        if (!f || !f.tempFilePath) return
        self.uploadQuote({ path: f.tempFilePath, name: '扫描件-' + Date.now() + '.jpg' })
      },
      fail() {
        // 用户取消拍照：不是错误，不提示、不报错
      }
    })
  },

  /**
   * 用**内置扫描件样本**（一张真 PNG 字节流）走同一套上传/提取链。
   *
   * 与"内置示例报价单"是**同一族的第二条通路**，理由也一样：真机通路（摄像头、
   * 系统文件选择器）都在小程序渲染树之外，走查够不着 ⇒ 不给页内通路就永远拿不到
   * 设备证据。两条通路**分开记**，⛔ 互不冒充（真机那一格记 `LIMITATION`）。
   *
   * ⚠️ 不是"骗过嗅探"：后端**魔数优先**（AC-18，不听信客户端声明），真 PNG
   * 本来就该被判成图片 ⇒ `needs_transcription`。这是产品的如实行为。
   */
  onUseSampleScan() {
    const self = this
    if (this.data.uploading) return
    if (!this.data.sessionId) {
      wx.showToast({ icon: 'none', title: '会话尚未就绪，请稍后重试' })
      return
    }
    const name = SAMPLE_SCAN_FILENAME
    const path = wx.env.USER_DATA_PATH + '/' + name
    try {
      // `wx.base64ToArrayBuffer` 而不是 `writeFileSync(..., 'base64')`：
      // 后者依赖各端对 encoding 参数的支持差异，前者是**字节级**的、可预期。
      const buf = wx.base64ToArrayBuffer(SAMPLE_SCAN_BASE64)
      wx.getFileSystemManager().writeFileSync(path, buf)
    } catch (e) {
      wx.showToast({ icon: 'none', title: '写入扫描件样本失败：' + ((e && e.errMsg) || '') })
      return
    }
    return this.uploadQuote({ path: path, name: name })
  },

  /**
   * 上传 → 立刻提取。两步**不能省第二步**。
   *
   * 只上传不提取的话，附件在 Agent 眼里只是"一个文件名"
   * （后端 `agentjobs._attach_text_excerpts` 只收 `extract_status='done'` 的文本），
   * 而用户会以为"传上去就完事了"。所以这里把提取并进来，并把**结果**如实报出来：
   * 提取成功说"Agent 已能读到"，提取没成功就说清楚是哪一档（需人工转录 / 格式不支持 / 提取失败）。
   */
  uploadQuote(file) {
    const self = this
    this.setData({ uploading: true, attachNotice: '' })
    const opts = this.data.entrustmentId
      ? { entrustmentId: this.data.entrustmentId }
      : { assignmentId: this.data.assignmentId }
    // ⚠️ 返回整条链的 promise：不返回的话，`await this.uploadQuote(f)` 等到的
    //    是 `undefined` —— 调用方（含 e2e 的页面驱动）会立刻往下走，
    //    读到的是"上传完、提取还没回来"的中间态，表现成"上传了但没提取"。
    return uploadAttachment(file.path, opts, newIdempotencyKey('att'))
      .then(function (row) {
        const one = decorateAttachment(row)
        return extractAttachment(one.attachmentId, newIdempotencyKey('ext')).then(function (out) {
          return { one: one, out: out || {} }
        })
      })
      .then(function (r) {
        self.setData({ uploading: false })
        const status = String((r.out && r.out.extract_status) || '')
        const chars = (r.out && r.out.extracted_chars) || 0
        const name = r.one.name
        let notice
        if (status === 'done') {
          notice = '已上传「' + name + '」并提取 ' + chars + ' 字 —— Agent 已能读到这份报价单'
        } else {
          // ⚠️「上传成功」与「Agent 读得到」是两件事，分开说：
          //    合成一句"上传成功"会让人以为下一步一定解析得出来。
          notice =
            '已上传「' +
            name +
            '」，但' +
            extractStatusLabel(status) +
            '。' +
            ((r.out && r.out.detail) || '') +
            '（Agent 暂时只能看到文件名）'
        }
        self.setData({ attachNotice: notice })
        return self.loadRest(self.data.sessionId)
      })
      .catch(function (err) {
        self.setData({ uploading: false })
        const status = (err && err.httpStatus) || 0
        const detail = (err && err.detail) || ''
        wx.showToast({
          icon: 'none',
          title:
            status === 401
              ? '登录已过期，请重新登录'
              : status
                ? '上传失败（' + status + '）' + (detail ? '：' + String(detail).slice(0, 30) : '')
                : '上传失败：请确认后端已启动'
        })
      })
  },

  /**
   * 重抽（重新提取）——**人工转录之后必须先问一次**（HO 0917-3 裁定四）。
   *
   * 为什么不能直接抽：转录内容**不可再生成**（扫描件重抽只会得到"需要转录"），
   * 而后端在提取失败的分支还会删掉旧文本 ⇒ 一次失败的重抽足以让人的劳动
   * 凭空消失，界面上却只看到"提取失败"。所以这里用**页内确认条**先问
   * （不用 `wx.showModal`：原生弹层不在渲染树里，关键路径拿不到设备证据）。
   *
   * 文本来自**机读提取**时不必问 —— 那本来就能由文件再生成一次。
   */
  onReextractAsk(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const id = ds.attachment_id != null ? String(ds.attachment_id) : ''
    if (!id || this.data.attachBusy) return
    const one = this.pickAttachment(id)
    if (!one) {
      wx.showToast({ icon: 'none', title: '该附件已不在本页，请下拉刷新' })
      return
    }
    if (one.overwritesManualText) {
      this.setData({ reextractId: id, attachNotice: '' })
      return
    }
    return this.runReextract(id, false)
  },

  onReextractCancel() {
    this.setData({ reextractId: '' })
  },

  onReextractConfirm() {
    const id = this.data.reextractId
    if (!id) return
    this.setData({ reextractId: '' })
    return this.runReextract(id, true)
  },

  /** 真重抽。`acknowledge` 只在"确认覆盖人工转录"时为真。 */
  runReextract(id, acknowledge) {
    const self = this
    this.setData({ attachBusy: true, attachNotice: '' })
    // 返回整条链：调用方（含 e2e）await 它才等于"这件事做完了"
    return extractAttachment(id, newIdempotencyKey('rex'), {
      acknowledgeTranscriptionOverwrite: acknowledge
    })
      .then(function (out) {
        self.setData({ attachBusy: false })
        const status = String((out && out.extract_status) || '')
        const prev = String((out && out.previous_text_source) || '')
        const chars = (out && out.extracted_chars) || 0
        self.setData({
          attachNotice:
            status === 'done'
              ? '已重新提取 ' + chars + ' 字' + (prev ? '（覆盖了原先的' + (prev === 'manual_transcription' ? '人工转录' : '机读提取') + '文本）' : '')
              : '重新提取未成功：' + extractStatusLabel(status) + '。' + ((out && out.detail) || '')
        })
        return self.loadRest(self.data.sessionId)
      })
      .catch(function (err) {
        self.setData({ attachBusy: false })
        const status = (err && err.httpStatus) || 0
        // 409 是"有人工转录、需先确认"——这正是这条守卫存在的意义，
        // 不能提示成一个笼统的"失败"，否则用户永远不知道要先确认。
        if (status === 409) {
          self.setData({ reextractId: id })
          return
        }
        wx.showToast({
          icon: 'none',
          title: status ? '重抽失败（' + status + '）' : '重抽失败：请确认后端已启动'
        })
      })
  },

  /** 人工转录入口：图片/扫描件拿不到机读文本时的降级通道 */
  onTranscribeOpen(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const id = ds.attachment_id != null ? String(ds.attachment_id) : ''
    if (!id || this.data.attachBusy) return
    this.setData({ transcribeId: id, transcribeDraft: '', attachNotice: '' })
  },

  onTranscribeInput(e) {
    this.setData({ transcribeDraft: (e && e.detail && e.detail.value) || '' })
  },

  onTranscribeCancel() {
    this.setData({ transcribeId: '', transcribeDraft: '' })
  },

  onTranscribeSubmit() {
    const self = this
    const id = this.data.transcribeId
    const text = (this.data.transcribeDraft || '').trim()
    if (!id || this.data.attachBusy) return
    if (!text) {
      wx.showToast({ icon: 'none', title: '请先输入转录内容（提交空内容会被拒）' })
      return
    }
    this.setData({ attachBusy: true })
    return transcribeAttachment(id, text, newIdempotencyKey('tr'))
      .then(function (out) {
        self.setData({ attachBusy: false, transcribeId: '', transcribeDraft: '' })
        const chars = (out && out.char_count) || 0
        self.setData({
          attachNotice:
            '已人工转录 ' + chars + ' 字并标记来源为「人工转录」—— Agent 已能读到，' +
            '但重抽会先来问你一次（转录内容不可再生成）'
        })
        return self.loadRest(self.data.sessionId)
      })
      .catch(function (err) {
        self.setData({ attachBusy: false })
        const status = (err && err.httpStatus) || 0
        wx.showToast({
          icon: 'none',
          title: status ? '转录失败（' + status + '）' : '转录失败：请确认后端已启动'
        })
      })
  },

  /** 按附件编号取本页那一行（找不到返回 undefined） */
  pickAttachment(id) {
    return (this.data.attachments || []).filter(function (a) {
      return String(a.attachmentId) === String(id)
    })[0]
  },

  /**
   * 用已上传的附件让 Agent 执行一次解析（**引用**这条链的界面入口）。
   *
   * ⚠️ 这里**只传 `attachment_id`，绝不传 `quote_text`**：
   * 后端 `ag02._quote_source` 的优先序是「操作者粘贴文本 > 附件已提取文本」，
   * 只要带上了 `quote_text`，附件就会被**静默忽略** —— 页面上看起来一切正常，
   * 但 Agent 读的根本不是那份附件。这也让 `source_refs` 里
   * `attachment_text` 这个来源标记失去意义。
   */
  onUseAttachment(e) {
    const self = this
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const id = ds.attachment_id != null ? String(ds.attachment_id) : ''
    if (!id || this.data.referencing || !this.data.sessionId) return
    const one = (this.data.attachments || []).filter(function (a) {
      return String(a.attachmentId) === id
    })[0]
    if (!one) {
      wx.showToast({ icon: 'none', title: '该附件已不在本页，请下拉刷新' })
      return
    }
    // 双保险：按钮在未提取时是禁用的，但这条判断不能只靠"按钮藏起来了"。
    // 未提取的附件没有文本，引用它只会让 Agent 把它当成"一个文件名"。
    if (!one.canReference) {
      wx.showToast({ icon: 'none', title: one.referenceHint })
      return
    }
    this.setData({ referencing: true, attachNotice: '' })
    appendMessage(
      this.data.sessionId,
      '【引用附件 #' + id + '】请解析这份报价单',
      newIdempotencyKey('msg')
    )
      .then(function () {
        return submitJob(
          self.data.sessionId,
          { input: Object.assign({ attachment_id: Number(id) }, self.jobAmountInput()) },
          newIdempotencyKey('job')
        )
      })
      .then(function (job) {
        const jid = job && (job.job_id || job.jobId)
        if (!jid) throw new Error('作业提交未返回 job_id')
        return runJob(jid)
      })
      .then(function () {
        self.setData({ referencing: false })
        return self.refresh()
      })
      .catch(function (err) {
        self.setData({ referencing: false })
        const status = (err && err.httpStatus) || 0
        wx.showToast({
          icon: 'none',
          title: status === 409 ? '作业已在执行中' : '解析失败' + (status ? '（' + status + '）' : '')
        })
      })
  },

  onOpenArtifact(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const artifactId = ds.artifact_id != null ? String(ds.artifact_id) : ''
    if (!artifactId) return
    // PRD 第 150 行 "link to exact workbench artifact"：落到**精确**那一份
    R.go(
      '/pages/entrust/artifact/artifact?artifact_id=' + encodeURIComponent(artifactId),
      { from: SELF }
    )
  },

  onBack() {
    R.go('/pages/entrust/workbench/workbench', { from: SELF })
  }
})
