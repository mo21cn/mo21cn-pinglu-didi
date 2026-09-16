// 委托发货 · 专业会话屏（UI-03 / DR-0015 / ENT-033 / **S2 首片**）
//
// 本页两件事，都得是真的：
//
// 1. **AC-05 的聊天侧**：成果卡与工作台、成果页**同源同版本** —— 字段来自
//    `utils/entrust.js` 的 `decorateArtifact`，本页不复写任何字段名映射。
// 2. **S2 首片的真实纵向增量**（HO 0917-2 定为首要任务）：
//    已认领委托 → 进入专属会话 → 发送报价文本 → 创建并执行**持久化作业** →
//    展示结果或明确失败 → 离开后重新进入仍可恢复。
//
// 关键是"真"在哪：
//   * 会话、消息、作业全部**落库**，由后端 `ent_session` / `ent_session_message` /
//     `ent_agent_job` 承载；**不在页面里保存一份消息数组**当作聊天记录
//     （那叫假聊天：刷新就没，换个设备也没有，审计更无从谈起）。
//   * 发消息与跑作业是**两个动作**：`submitJob` 只建作业，`runJob` 才执行。
//     合成一步会让人以为"点了就跑完了"，而作业其实可能还在排队。
//   * 作业成功只产出**提案（envelope）**，不是成果（AC-09）。卡上必须写"提案"，
//     写成"解析结果"会让人以为已经落库。
//   * `mocked=true` 时后端走的是本地规则模板（fixture），结构与真实模式同构，
//     但**不是真实模型输出** —— 界面必须显示模式，不得拿它充当模型质量证据。
//
// 运行期导航治理与其它委托页同口径：`onLoad` 走 `guardEntry()`，跳转走 `go()`，
// 本页登记在 `MIGRATED_PAGES`（CI 会核对不得出现裸 `wx.navigateTo` 等）。
const {
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
  appendMessage,
  createSession,
  jobFailureText,
  newIdempotencyKey,
  runJob,
  submitJob,
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
    sending: false,
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
      const done = attachments.filter(function (a) {
        return a.extractStatus === 'done'
      }).length
      self.setData({
        messages: messages,
        attachments: attachments,
        attachmentText: attachments.length
          ? '本单附件 ' + attachments.length + ' 份，其中已提取文本 ' + done + ' 份（随上下文提供给 Agent）'
          : '本单暂无附件'
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
          { input: { quote_text: text } },
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
