// 委托发货 · 客户委托草稿 / 提交（UI-07 · S1 / DEMO-1 §3.3）
//
// 本页只做一件事：**把货主的委托交出去**。
//   ① 让货主看到「可以委托给谁」—— 来自服务端 `GET /my-entrustments`
//      （读 `ent_entrustment`，不是 `ent_org_member`；DR-0012 归属 ≠ 权限边界）；
//   ② 收三个字段（一句话说明 / 货物说明 / 数量与单位）；
//   ③ 两步写命令：`POST /assignments`（建草稿）→ `POST /assignments/{id}/submit`（提交）。
//
// 为什么入口在「发布货源」页，而不是「我的」
// ----------------------------------------
// 货主的心智是"我有一票货要发，怎么发"。「自主发货」和「委托发货」是同一个决策的
// 两个分支，放在同一处才不会让用户在两个 tab 之间来回找。所以本页由
// `pages/publish/cargo/cargo` 的 `pickEntrustDelivery()` 经 `go()` 进入，并把已填的
// 货名 / 数量 / 单位带过来当**草稿初值**（不代替用户说话：标题仍要用户自己填）。
//
// 为什么"创建"和"提交"不是两个按钮
// --------------------------------
// 货主的心智是"交出去"，中间那个纯草稿态对他没有意义。所以界面只有一个"提交"，
// 但**实现上**它确实是两条写命令 —— 这就带来一个必须处理的中间态：
// 「草稿建成了、提交没成」。处理方式见下面的 `locked` / `createdId`。
//
// ── 三条不可含糊的语义（写在代码里，不靠注释口头保证）────────────────────
//
// 1. **草稿建立后锁定内容字段**。否则用户改完再点提交，会**再建一张草稿**，
//    而服务端那张没人认领的草稿既不会出现在任何列表里（货主侧"我的委托"本切片没做），
//    也不会有人发现。锁定比"悄悄多一张"好。提交目标（交给谁）**不锁** ——
//    它不是草稿内容（草稿的 `org_id` 在提交时才写入），换目标是一个合法的新意图。
//
// 2. **重试必须原样复用两把幂等键与 `expected_revision`**。
//    提交端点的幂等重放要求 `(scope, key, actor, payload)` 全部一致：键相同而
//    `expected_revision` 变了，服务端会判成"同键异体"回 409，用户看到的是
//    "状态已变化" —— 而实际上只是他自己重试了一次。所以建草稿返回的 `revision`
//    必须与键一起留着，重试时**不重新取数**。
//
// 3. **`hasUnsaved()` 把"已建草稿未提交"也算作未保存**。服务端那张草稿确实还在，
//    但本页返回后货主**在界面上再也找不到它的提交入口**（同上，货主侧列表本切片没做）。
//    "服务端有、界面没有"对用户来说等于丢了，所以必须拦一次并说清楚。
//
// ── 在途状态跨页面实例续接（本切片补上，此前是已知余量）──────────────────
//
// 原登记的问题：幂等键只活在页面实例里，于是"提交响应丢失后**杀掉小程序再重进**"
// 会让两把键随之消失，结果是**再建一张草稿并提交** —— 重复提交。
//
// 现在把在途状态（两把键 + 草稿编号与版本 + 表单内容）落 storage 并续接。
// 三个必须守住的点：
//   · **按用户隔离**：持久化的内容含货名等**客户数据**，用一个全局键会让下一个
//     登录的账号读到上一个人的货名 —— 那比重复提交严重得多。键里带 `user_id`；
//     **拿不到 `user_id` 就不持久化**（退化成页面实例内：宁可少修复一点也不泄漏）。
//   · **不设过期**：服务端那张草稿真实存在且可提交，静默丢弃会让人以为单没了。
//     所以续接时在界面上**明确告知**，并给一个「重新填写」断开关联。
//   · **断开关联 ≠ 撤回**：`onRestart` / `onBack` 的放弃只清**本地**在途状态，
//     **不**调用撤回端点 —— 服务端那张草稿仍在（可在委托工作台看到）。
//     单方面替用户删掉服务端数据是更坏的选择。
//
// ENT-019 起新增委托页面必须从首个切片接入运行期导航治理（`utils/routes.js`）：
//   · `onLoad` 的入口守卫与 `go()` 用**同一份** `paramSchema` 判定参数合法性；
//   · 提交成功后用 `go()` 走 `replace` 到委托详情（**不**用 push —— 返回键不该
//     把用户带回一张已经提交过的表单）；
//   · 本页登记在 `MIGRATED_PAGES` 里，CI 会核对不得出现裸 `wx.navigateTo /
//     redirectTo / reLaunch`。
const {
  VIEW,
  assignmentDraftBody,
  assignmentWriteError,
  createAssignment,
  decorateEntrustments,
  fetchMyEntrustments,
  newIdempotencyKey,
  pickEntrustment,
  submitAssignment,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

// 取当前登录用户只为**给持久化键加隔离**（见文件头）。它不是权限依据 ——
// 能委托给谁始终由服务端 `GET /my-entrustments` 决定。
const { getUser } = require('../../../utils/auth')

/** 本页路径（注册表口径）；`go()` 需要知道"从哪来"才能查到导航边 */
const SELF = 'pages/entrust/intake/intake'

/** 从「发布货源」带过来的初值字段（与 `routes.js` 的 `paramSchema` 必须同名） */
const CARRY_KEYS = ['cargo_name', 'quantity', 'quantity_unit']

const BLANK_FORM = {
  title: '',
  cargo_summary: '',
  quantity: '',
  quantity_unit: ''
}

/**
 * 按 `onLoad` 收到的 query **重建**本页 url，供入口守卫判定。
 *
 * 只重建 `paramSchema` 声明过的键：把 query 原样拼回去会把未知参数一起带进
 * 校验（它们没有声明、不参与判定），重建可以保证"校验的对象"与"声明的契约"一致。
 *
 * ⚠️ 每个值都要先过 `R.decodeParam`：小程序交给 `onLoad` 的是**未解码**的
 *    百分号串，这里是"重建 url"而不是"照搬 url"，直接 `encodeURIComponent`
 *    就成了**第二次编码** —— 中文货名会膨胀 3 倍（`走查货物·铁矿石` 8 字 → 69
 *    字符），撞上 `paramSchema` 的长度上限后，本页会把自己的入口判成
 *    「参数不合法」，用户看到一个连表单都没有的错误页。2026-09-16 真机走查 ㉞
 *    第一次带中文参数就把这个缺陷打了出来（此前六个页面只带 ASCII id，
 *    `encodeURIComponent` 是恒等变换，所以一直没暴露）。
 */
function urlOf(query) {
  const q = query || {}
  const parts = []
  CARRY_KEYS.forEach(function (k) {
    const v = R.decodeParam(q[k])
    if (v) parts.push(k + '=' + encodeURIComponent(v))
  })
  return SELF + (parts.length ? '?' + parts.join('&') : '')
}

// ── 在途草稿的持久化（跨页面实例续接）───────────────────────────────────

/** storage 键前缀。真键 = 前缀 + `user_id`（按用户隔离，见文件头）。 */
const DRAFT_STORE_PREFIX = 'entrust_intake_draft_'

/** 载荷版本。形状变了就改它 —— 旧版本一律当作"没有"，不做兼容猜测。 */
const DRAFT_STORE_VERSION = 1

/**
 * 当前用户的持久化键；**拿不到 `user_id` 返回空串**，调用方据此**不持久化**。
 *
 * 为什么失败要退化、而不是用全局键兜底：全局键会让下一个登录的账号读到
 * 上一个人的货名。少修复一条"重复提交"换来的是不泄漏客户数据 —— 这个交换必须做。
 */
function draftStoreKey() {
  let user = null
  try {
    user = getUser()
  } catch (e) {
    user = null
  }
  const uid = user && user.user_id != null ? String(user.user_id) : ''
  return uid ? DRAFT_STORE_PREFIX + uid : ''
}

function blankForm() {
  return Object.assign({}, BLANK_FORM)
}

/**
 * 读回在途草稿；不存在或形状不对一律返回 `null`。
 *
 * 形状校验是**必须的**：storage 里的东西可能来自旧版本、也可能被截断或改坏，
 * 直接 `setData` 进去会让页面带着 `undefined` 渲染 —— 那看起来像"草稿是空的"，
 * 而用户明明填过，于是他会重填一遍（又建一张草稿）。宁可当作没有草稿。
 */
function loadDraftState() {
  const key = draftStoreKey()
  if (!key) return null
  let raw = null
  try {
    raw = wx.getStorageSync(key)
  } catch (e) {
    return null
  }
  if (!raw || typeof raw !== 'object') return null
  if (raw.v !== DRAFT_STORE_VERSION) return null
  const form = raw.form && typeof raw.form === 'object' ? raw.form : null
  if (!form) return null
  return {
    createKey: typeof raw.createKey === 'string' ? raw.createKey : '',
    submitKey: typeof raw.submitKey === 'string' ? raw.submitKey : '',
    createdId: raw.createdId == null ? '' : String(raw.createdId),
    draftRevision: Number(raw.draftRevision) || 0,
    form: {
      title: String(form.title || ''),
      cargo_summary: String(form.cargo_summary || ''),
      quantity: String(form.quantity || ''),
      quantity_unit: String(form.quantity_unit || '')
    }
  }
}

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',

    /** 可选提交目标（服务端只给生效中的委托授权） */
    targets: [],
    /** 当前选中的目标组织；空 = 还没选 */
    orgId: '',
    /** 选中目标的组织名（模板不做事，避免在 wxml 里遍历 targets） */
    orgName: '',
    /** 选中目标上"我在这个组织能做什么"（来自该条授权的 permissions） */
    orgPermText: '',
    /** 多个目标时必须显式选（唯一目标直接选中 —— 不猜） */
    needPick: false,

    form: Object.assign({}, BLANK_FORM),
    /** 从「发布货源」带过来的初值说明（带过来才显示） */
    carryHint: '',

    submitting: false,
    /** 内容已锁定（草稿已建立）：改内容会多建一张草稿，见文件头第 1 条 */
    locked: false,
    /** 已建草稿的编号 */
    createdId: '',
    /** 已建草稿的版本。与编号**一起**留着：重试时不能重新取数（见文件头第 2 条） */
    draftRevision: 0,
    /** 创建阶段用过的幂等键（失败重试复用；内容改动后作废） */
    createKey: '',
    /** 提交阶段用过的幂等键（失败重试复用；换目标后作废） */
    submitKey: ''
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
    // 参数的类型与长度写在 `routes.js` 的 `paramSchema` 里。
    const gate = R.guardEntry(urlOf(query), { coldStart: R.currentDepth() <= 1 })
    if (!gate.ok) {
      // 参数非法是**传播错误**，必须直接给 error 态：借 `viewState` 的兜底推断
      // 会把它伪装成一个正常业务状态（例如"加载失败"），排错时看不出真正的原因。
      this.applyState({
        state: VIEW.ERROR,
        title: '入口参数不合法',
        hint: gate.reason
      })
      return
    }

    // 续接优先于 carry：续接的是一张**已经建在服务端**的草稿（用户真实填过），
    // 而 carry 只是"源页面能提供的默认值"。两者同时存在时，前者才是事实。
    if (!this.restoreDraft()) this.applyCarry(query)
    this.load()
  },

  /**
   * 续接上一次没提交完的草稿（跨页面实例）。返回是否真的续接了。
   *
   * 它同时恢复**两把幂等键与草稿版本** —— 这正是修掉"响应丢失后重进会重复提交"
   * 的关键：重试必须复用同一个 `(scope, key, payload)`，重新生成键等于发起新意图。
   */
  restoreDraft() {
    const saved = loadDraftState()
    if (!saved) return false
    const patch = {
      form: Object.assign(blankForm(), saved.form),
      createKey: saved.createKey,
      submitKey: saved.submitKey,
      createdId: saved.createdId,
      draftRevision: saved.draftRevision,
      // 有编号就说明草稿已在服务端 ⇒ 内容锁定（否则改完重提会建出第二张，见文件头第 1 条）
      locked: !!saved.createdId
    }
    // 提示**只讲"这是续接来的"**：草稿编号与"内容已锁定"由模板里的 `draft-note`
    // 统一表达，两处都写会变成同一句话在界面上出现两次。
    if (saved.createdId) patch.carryHint = '已续接上次未提交的内容。'
    this.setData(patch)
    return true
  },

  /**
   * 回填从「发布货源」带过来的初值。
   *
   * 只填**货物说明 / 数量 / 单位**，不填标题：`title` 是"要办什么"，
   * 源页面里根本没有这个信息。用货名冒充标题等于替用户写了一句他没说过的话。
   */
  applyCarry(query) {
    const q = query || {}
    // 同 `urlOf`：框架把 query 原样交过来（未解码），不归一化的话草稿初值会是
    // 一串 `%E8%B5%B0…` —— 用户看到的是乱码，而不是他从上一页带过来的货名。
    const name = R.decodeParam(q.cargo_name)
    const qty = R.decodeParam(q.quantity)
    const unit = R.decodeParam(q.quantity_unit)
    const patch = {}
    if (name) patch['form.cargo_summary'] = name
    if (qty) patch['form.quantity'] = qty
    if (unit) patch['form.quantity_unit'] = unit
    const shown = []
    if (name) shown.push(name)
    if (qty) shown.push(qty + (unit ? ' ' + unit : ''))
    if (shown.length) {
      patch.carryHint = '已从「发布货源」带过来：' + shown.join(' · ') + '。请补一句「要办什么」再提交。'
    }
    if (Object.keys(patch).length) this.setData(patch)
  },

  /**
   * 读「我授权出去的组织」。
   *
   * ⚠️ 空清单**不是错误**（服务端只返回生效中的授权）：它是"你还没有把委托授权给
   * 任何组织"，下一步动作是去找服务主体完成授权 —— 与"接口坏了"完全不同，
   * 所以单独一个 `empty` 态，且**错误优先于空**（先判 HTTP 状态，再判条数）。
   */
  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchMyEntrustments()
      .then(function (res) {
        const rows = decorateEntrustments((res && res.items) || [])
        if (!rows.length) {
          self.setData({ targets: [], orgId: '', orgName: '', orgPermText: '', needPick: false })
          const base =
            '委托发货是把货交给服务经营主体（货代公司）去办。你现在还没有把委托授权给任何组织 —— ' +
            '请先由对方把你加入它的组织并完成委托授权，再回到这里提交。'
          const stuck = self.data.createdId
            ? '已保存的草稿 #' + self.data.createdId + ' 仍然在服务端，重新获得授权后回来重试即可。'
            : ''
          self.applyState({
            state: VIEW.EMPTY,
            title: '还没有可委托的服务主体',
            hint: stuck ? base + ' ' + stuck : base
          })
          return
        }
        const pick = pickEntrustment(rows)
        self.setData({ targets: rows, needPick: pick.needPick })
        if (pick.orgId) self.applyOrg(rows, pick.orgId)
        self.applyState({ state: VIEW.READY, title: '', hint: '' })
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.applyState(
          viewState({ status: status, netError: !status, detail: err && err.detail })
        )
      })
  },

  applyState(state) {
    this.setData({ view: state.state, viewTitle: state.title, viewHint: state.hint })
  },

  /** 把选中的目标投影成模板直接可用的三个字段（模板不做事） */
  applyOrg(rows, orgId) {
    const hit =
      (rows || []).filter(function (t) {
        return String(t.orgId) === String(orgId)
      })[0] || null
    this.setData({
      orgId: hit ? String(hit.orgId) : '',
      orgName: hit ? hit.orgName : '',
      orgPermText: hit ? hit.permissionText : ''
    })
  },

  // ── 表单 ────────────────────────────────────────────────────────────

  /**
   * 字段输入。**同时作废创建用的幂等键** —— 内容改了就是新的草稿意图；
   * 沿用旧键会让服务端把这次创建当成上一次的重放，用户建了一张内容不同的草稿
   * 却拿到上一次的响应，界面显示成功而服务端还是旧内容。
   */
  onInput(e) {
    // 草稿已建立：内容锁定（模板层也用 `disabled` 挡，这里再挡一次防漏）
    if (this.data.locked) return
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const field = ds.field || ''
    if (!field) return
    const patch = { createKey: '' }
    patch['form.' + field] = (e && e.detail && e.detail.value) || ''
    this.setData(patch)
    // 每次输入都落盘：否则"填到一半被杀掉"会丢内容，而内容丢了幂等键也失去意义
    // （键是为这份内容准备的）。
    this.persistDraft()
  },

  /**
   * 选择提交目标。
   *
   * 与内容字段相反，**草稿建立后仍然可改**：草稿的 `org_id` 是在提交时才写入的，
   * 换目标不会产生第二张草稿。但它是**新的提交意图**，所以作废提交用的幂等键。
   */
  onPickOrg(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const orgId = ds.orgId != null ? String(ds.orgId) : ''
    if (!orgId) return
    this.applyOrg(this.data.targets, orgId)
    this.setData({ submitKey: '' })
    this.persistDraft()
  },

  /** 把当前在途状态写进 storage。拿不到用户标识就**不写**（见文件头）。 */
  persistDraft() {
    const key = draftStoreKey()
    if (!key) return
    try {
      wx.setStorageSync(key, {
        v: DRAFT_STORE_VERSION,
        createKey: this.data.createKey,
        submitKey: this.data.submitKey,
        createdId: this.data.createdId,
        draftRevision: this.data.draftRevision,
        form: this.data.form
      })
    } catch (e) {
      // 写不进去只影响"下次能不能续接"，不影响本次提交 —— 不该让交互失败。
      // ⚠️ 但也不要在这里 toast：storage 满/不可用在真机上极少见，
      //    弹一个用户无法处理的错误只会干扰主流程。
    }
  },

  /** 清掉本地在途状态（**不**动服务端那张草稿）。 */
  clearPersistedDraft() {
    const key = draftStoreKey()
    if (!key) return
    try {
      wx.removeStorageSync(key)
    } catch (e) {
      // 同上：清不掉不会让主流程失败，最坏是下次又续接一次
    }
  },

  // ── 提交 ────────────────────────────────────────────────────────────

  onSubmit() {
    const self = this
    if (this.data.submitting) return

    const check = assignmentDraftBody(this.data.form)
    if (!check.ok) {
      // 前置检查的结论要**一次说完**：只报第一条会让用户改一条、再来一次。
      wx.showModal({ title: '还不能提交', content: check.errors.join('\n'), showCancel: false })
      return
    }
    if (!this.data.orgId) {
      wx.showModal({
        title: '还不能提交',
        content: '请选择这一单委托给谁 —— 平台不替你猜。',
        showCancel: false
      })
      return
    }

    this.setData({ submitting: true })
    wx.showLoading({ title: '提交中', mask: true })

    return this.ensureDraft(check.body)
      .then(function (draft) {
        return self.sendSubmit(draft.assignmentId, draft.revision)
      })
      .then(function () {
        wx.hideLoading()
        const id = self.data.createdId
        // 提交成功 = 在途状态结束，清掉本地续接载荷。不清的话下次进来会续接一张
        // **已经提交过**的草稿：界面显示"内容已锁定"、而提交会被服务端以状态冲突拒绝，
        // 用户面对的是一个他无法理解也无法脱身的假状态。
        self.clearPersistedDraft()
        self.setData({ submitting: false, createKey: '', submitKey: '' })
        wx.showToast({ title: '已提交，等待受理', icon: 'success' })
        // replace（不是 push）：返回键不该把用户带回一张已经提交过的表单。
        R.go('/pages/entrust/detail/detail?assignment_id=' + encodeURIComponent(id), {
          from: SELF,
          ctx: { orgId: self.data.orgId }
        })
      })
      .catch(function (err) {
        wx.hideLoading()
        // 两把键都**保留**：下一次点提交是同一件事的重试，必须复用（见文件头第 2 条）。
        self.setData({ submitting: false })
        const w = assignmentWriteError(err)
        // 403 = 对该组织没有生效授权。刷新**能**改变结论（授权可能刚被撤回或续上），
        // 所以重新取一次清单，让"重新选择服务主体"真的有可选项。
        if (w.kind === 'denied') self.load()
        wx.showModal({ title: w.title, content: w.hint, showCancel: false })
      })
  },

  /**
   * 确保手上有一张草稿，返回 `{assignmentId, revision}`。
   *
   * 已建过就**原样复用编号与版本**，不重新取数：提交端点的幂等重放要求
   * `payload`（含 `expected_revision`）完全一致，重新取一次版本会让"重试"
   * 变成"同键异体" 409（见文件头第 2 条）。
   */
  ensureDraft(body) {
    const self = this
    if (this.data.createdId && this.data.draftRevision) {
      return Promise.resolve({
        assignmentId: this.data.createdId,
        revision: this.data.draftRevision
      })
    }
    const key = this.data.createKey || newIdempotencyKey('asg')
    this.setData({ createKey: key })
    return createAssignment(body, key).then(function (res) {
      const id = res && res.assignment_id != null ? String(res.assignment_id) : ''
      const rev = res && res.revision != null ? Number(res.revision) : 0
      if (!id || !rev) {
        // 建成了却拿不到编号/版本：**不能假装成功**，否则用户不知道这张委托在哪，
        // 也无法重试提交。说清楚并把这次失败交回上层。
        throw new Error('创建委托成功但没有返回编号或版本，请到委托工作台确认后再试')
      }
      // `locked` 与 createdId 同时置上：从这一刻起内容字段不可再改，
      // 否则改完重提会建出第二张草稿（见文件头第 1 条）。
      self.setData({ createdId: id, draftRevision: rev, locked: true })
      // 建草稿成功是**必须落盘**的一刻：在这里丢掉编号/版本，重进后既不知道
      // 已有草稿、也没留下那把键，只能从头再发起一次创建 —— 就是重复提交。
      self.persistDraft()
      return { assignmentId: id, revision: rev }
    })
  },

  sendSubmit(assignmentId, revision) {
    const key = this.data.submitKey || newIdempotencyKey('asgsub')
    this.setData({ submitKey: key })
    // 键要在**发请求之前**落盘。若等响应回来才写，而进程在响应到达前被杀，
    // 那把键就没留下 —— 重进后会生成新键重发，服务端看到的是两次不同的提交意图
    // （幂等保护失效）。先写后发，最坏情况只是"键留下了但没发出去"，
    // 下次重试复用同一个键，服务端会正确识别为同一件事。
    this.persistDraft()
    return submitAssignment(assignmentId, this.data.orgId, revision, key)
  },

  // ── 导航 ────────────────────────────────────────────────────────────

  /**
   * 有未提交的内容吗（`onBack` 据此决定要不要拦）。
   *
   * 「已建草稿但没提交」也算未保存 —— 见文件头第 3 条。
   */
  hasUnsaved() {
    if (this.data.createdId) return true
    const f = this.data.form || {}
    return !!(
      String(f.title || '').trim() ||
      String(f.cargo_summary || '').trim() ||
      String(f.quantity || '').trim() ||
      String(f.quantity_unit || '').trim()
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
      content: this.data.createdId
        ? '委托草稿 #' +
          this.data.createdId +
          ' 已经保存在服务端，但本页返回后就没有它的提交入口了。'
        : '这一页填的内容还没有提交，返回后会丢失。',
      confirmText: '放弃',
      cancelText: '继续填写',
      success: function (res) {
        if (res.confirm) {
          // 用户已明确放弃：先清干净再走，否则 leave() 内部的判断路径会再拦一次。
          // 同时清掉持久化载荷 —— 用户说了放弃，下次进来不该再冒出这张草稿。
          // （只清**本地**关联；服务端那张草稿仍在，撤回要走 cancel 端点，本页不做。）
          self.clearPersistedDraft()
          self.setData({
            form: blankForm(),
            createKey: '',
            submitKey: '',
            createdId: '',
            draftRevision: 0,
            locked: false
          })
          leave()
        }
      }
    })
  },

  /**
   * 断开关联，从头填一张新的。
   *
   * 存在的理由：续接来的草稿**内容已锁定**（改动会多建一张草稿，见文件头第 1 条）。
   * 没有这个入口，一个想换货名的用户只能"先提交再撤回"—— 那会留下一张真实的废单。
   *
   * 它只清**本地**在途状态，**不**调用撤回端点：单方面替用户删掉服务端数据更坏。
   */
  onRestart() {
    const self = this
    wx.showModal({
      title: '重新填写？',
      content: this.data.createdId
        ? '会清空本页内容，并断开与草稿 #' +
          this.data.createdId +
          ' 的关联。服务端那张草稿仍会保留（可在委托工作台看到），本页不再替你提交它。'
        : '会清空本页已填的内容。',
      confirmText: '重新填写',
      cancelText: '继续编辑',
      success: function (res) {
        if (!res.confirm) return
        self.clearPersistedDraft()
        self.setData({
          form: blankForm(),
          createKey: '',
          submitKey: '',
          createdId: '',
          draftRevision: 0,
          locked: false,
          carryHint: ''
        })
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
