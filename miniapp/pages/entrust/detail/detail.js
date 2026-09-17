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
// 第 4 条（S3 收口 · 合同 §10.1 第 5 步 / BP-03 第 2、3 条 / D1-06）：
// 本页新增**运力确认与有效期**这一块（经理侧）。它承载四件事：
//   · 候选运力清单 —— 第 2 条要的"两家可比"（吨位 / 装载口径 / 单价口径 / 有效期 / 证据）；
//   · 登记一条候选（**不是确认**：BP-03 第 3 条明确要求把"选中"与"确认"分开）；
//   · 确认 —— 跑规则闸门，通过才产出采购确认成果；不通过时把**逐条**判定（含通过项）照实渲染；
//   · 只读复算 —— "这条确认现在还成立吗"（D1-09 那句「900 吨候选在变更后不再适用」的可见形态）。
// ⚠️ 六条端点**整组没有客户面**（后端 `capacity_api` 逐条走 `assert_can_view_org` /
//    `assert_can_write_entrustment`，**不设货主旁路**）：候选行带承运人与供应商单价、
//    确认行带需求量与缺口吨数、逐规则判定里还有比较过程。
//    ⇒ 取数**之前**先按本地组织权限投影（`ORG_PERM_VIEW`）判一次，不该看的一次请求都不发；
//    隐藏不等于放行，服务端仍按授权独立判定（与 `canClaim` / `canAssembleQuote` 同一口径）。
const {
  CAPACITY_EVIDENCE_LABELS,
  CAPACITY_EVIDENCE_ORDER,
  ORG_PERM_CLAIM,
  ORG_PERM_QUOTE_CREATE,
  ORG_PERM_VIEW,
  TASK_TYPE_LABELS,
  TASK_TYPE_ORDER,
  VIEW,
  canClaimAssignment,
  capacityRuleRows,
  claimAssignment,
  confirmCapacity,
  createArtifact,
  createTask,
  decorateCapacityCandidate,
  decorateCapacityConfirmation,
  decorateCapacityRecheck,
  decorateCustomerOffer,
  decorateDetail,
  decorateWorkbench,
  downloadOfferAttachment,
  fetchAssignment,
  fetchCapacityCandidates,
  fetchCapacityConfirmations,
  fetchMyOfferReleases,
  fetchMyOrgs,
  fetchSessionContext,
  fetchWorkbench,
  isPermittedOrg,
  newIdempotencyKey,
  permittedOrgIds,
  pickOfferForAssignment,
  respondOffer,
  recordCapacityCandidate,
  recheckCapacityConfirmation,
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

/**
 * 证据类别选项（登记候选运力用）。
 *
 * 与 `TASK_TYPE_OPTIONS` 同一形态与理由：做成**页内选择条**而不是自由输入 ——
 * 自由输入能填出必然被后端拒（`evidence_kind` 不在取值域）的值，而「类别填错了」
 * 与「随便填了个类别」在库里的形状一样；BP-03 第 3 条要的是「类别 + 引用」两件齐全。
 */
const CAPACITY_EVIDENCE_OPTIONS = CAPACITY_EVIDENCE_ORDER.map(function (k) {
  return { key: k, label: CAPACITY_EVIDENCE_LABELS[k] || k }
})

/**
 * 登记表单的空白草稿。
 *
 * 做成**函数**而不是共享对象字面量：把同一个对象引用放进 `setData`/`data`，
 * 一处取消就会把别处正在编辑的表单一起清空 —— 而那种错只在"同时开了两处"时出现。
 * 键名与模板里 `data-df` 的 kebab 写法一一对应；到后端字段名的转换**只在
 * `buildCapBody()` 一处**发生（散着写就会出现"某个字段改了名但没人发现"）。
 */
function emptyCapForm() {
  return {
    carrier: '',
    vesselName: '',
    capacityTonnes: '',
    vesselCount: '1',
    allowsPartialLoad: false,
    rate: '',
    rateUnit: '吨',
    currency: 'CNY',
    validUntil: '',
    evidenceKind: '',
    evidenceRef: ''
  }
}

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
    /**
     * 「组装对客报价」入口（S3：合同 §10.1 第 6 步 `Release the offer` 的前置动作）。
     *
     * 为什么这一步必须存在：能被客户看到的成果类型只有 `customer_quote` /
     * `contract_review`（`registry.CUSTOMER_VISIBLE_TYPES`），而主演示链路上
     * AG-02 产出的是 `quote_parsed`（**船东侧报价**，服务端在信封里明令
     * "供应商侧报价…不得互相替代"）⇒ 拿它去发布会被 400 拒（拒得对）。
     * 计划 §4 UI-06 与 §5.2 的 S3 DoD 都要求"对客报价**可人工组装**"，
     * 这一片补的就是这一步。
     *
     * 两个条件同时满足才显示（与 `canClaim` 同一套口径）：
     *   1. 服务端能定位到**唯一一条**生效授权（`GET /assignments/{id}/session-context`
     *      返回 `entrustment_id`）—— 多条授权时该端点**不猜**，返回 `null` 并说明原因，
     *      此时这里也不显示（宁可不显示，也不替用户在两条授权之间选边）；
     *   2. 我在**该委托所属组织**内有 `entrust:quote:create`。
     *
     * 隐藏按钮不等于放行：写端 `POST /entrustments/{eid}/artifacts` 仍独立校验。
     */
    canAssembleQuote: false,
    /** 组装表单是否展开（页内，不用原生弹层 —— 理由同「受理委托」） */
    quoteOpen: false,
    /** 组装表单的草稿：金额/币种/包含项/有效期（`includes` 在页内按逗号分隔录入） */
    quoteForm: { amount: '', currency: 'CNY', includes: '', validUntil: '' },
    /** 组装表单的校验/失败提示（在**页内**说清，不用 toast） */
    quoteHint: '',
    /** 提交在飞（防重复点击；重复提交另有幂等键兜底） */
    quoteSubmitting: false,
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
    taskTypes: TASK_TYPE_OPTIONS,

    // ── 对客报价（客户侧；S3 纵向切片 / BP-03 第 6/7/9/10 条）──────────────
    /**
     * 本单的**对客报价**（客户视角）。`null` ＝ 这一单没有发给我的发布。
     *
     * 说明为什么它不会在经理的屏幕上出现：数据来自 `GET /my-offer-releases`，
     * 服务端只回 `customer_user_id == 登录用户` 的那些 ⇒ 经理拿到的永远是空列表。
     * 前端**不**靠角色判断来隐藏这张卡 —— 那是"先拿到再隐藏"，而合同要求
     * 客户数据必须由**服务端投影**决定谁能看到。
     */
    offer: null,
    /**
     * 响应表单展开成哪一种（`''` ＝ 未展开）。
     *
     * 用页内展开条而**不是** `wx.showModal`：接受／拒绝是本屏唯一会**改变业务事实**
     * 且**不可回退**的动作（后端 `UNIQUE(release_id)`，响应过一次就永远不能再响应），
     * 它必须能被真机验证。原生弹层的确认键不在渲染树里，工具点不到 ⇒ 只能记
     * `LIMITATION`。本页的受理确认条与任务输入条已按同一条理由改造过两次。
     */
    offerForm: '',
    /** 客户填的响应说明（拒绝时尤其需要一句话，否则经理只看到一个"拒绝"） */
    offerNote: '',
    /** 响应表单内的校验/失败提示（页内常驻，不用会消失的 toast） */
    offerHint: '',
    /** 响应请求在飞 */
    offerSubmitting: false,
    /** 附件下载的**页内**状态文案（下载是异步的，且失败要能一直看得见） */
    downloadHint: '',

    // ── 运力确认与有效期（经理侧；第 4 条）──────────────────────────────
    /**
     * 该不该**发这次取数**：我在**这张委托所属组织**内有 `entrust:view`。
     * 判据在 `applyState`（本地组织权限投影），`loadCapacity` 只读它。
     * ⚠️ 用本地投影**不是为了省一次请求**：客户发出这个请求必然 403，
     *    而 403 被 `.catch` 吞掉的表现与"这一单没有候选运力"一模一样 ——
     *    那就是一次静默降级（本页注释反复在防的那类缺陷）。
     */
    canViewCapacity: false,
    /**
     * 该不该**显示入口**：授权能被唯一定位（`ctx.entrustment_id`）∧ 我在该组织内有
     * `entrust:quote:create`。与 `canAssembleQuote` 同一判据、同一理由 ——
     * 隐藏不等于放行，写端 `_write_scope` 仍独立判定（成员资格 → 恰好一条授权 → 写权限）。
     */
    canRecordCapacity: false,
    /** 候选运力（已装饰：吨位/装载口径/单价/有效期/证据三态/状态标签） */
    capCandidates: [],
    /** 已作出的运力确认（**冻结副本** + 逐规则判定 + 成果引用） */
    capConfirmations: [],
    /** 登记表单展开（页内；理由同「受理委托」—— 原生弹层不在渲染树里、工具点不到确认键） */
    capOpen: false,
    /** 登记表单草稿（键名见 `emptyCapForm`） */
    capForm: emptyCapForm(),
    /** 登记表单的校验提示（页内常驻，不用会消失的 toast） */
    capHint: '',
    /** 登记提交在飞（防重复点击；重复提交另有幂等键兜底） */
    capSubmitting: false,
    /** 确认条展开在哪一条候选上（`''` ＝ 都收起；两侧都是字符串，见模板注释） */
    capConfirmKey: '',
    /** 「本次确认覆盖的范围」—— 后端必填（它是一条**采购范围判断**，数据里没有出处） */
    capScope: '',
    /** 确认备注（选填） */
    capNote: '',
    /** 确认提交在飞 */
    capConfirming: false,
    /** 最近一次被拒的**逐条**判定（含通过项）：标题写清它是哪一条候选的 */
    capRuleTitle: '',
    capRuleRows: [],
    /** 复算结果显示在哪一条确认上（`''` ＝ 不显示） */
    capRecheckId: '',
    capRecheck: null,
    /** 复算的失败提示（页内常驻） */
    capRecheckHint: '',
    /** 证据类别选项（页内选择条） */
    capEvidenceOptions: CAPACITY_EVIDENCE_OPTIONS
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
    // 组织权限投影**取数之前置空**（D-4 裁定 §2/§4 的代码形态）：空投影 ⇒ 不显示入口，
    // 也不发运力那两次取数 ——「权限尚未加载或加载失败时不提前展示可执行按钮」。
    // ⚠️ 三个投影一起清：只清一个的话，上一轮的结论会在这轮的第一帧里被继续使用，
    //    而那正好是"权限与页面所说不一致"的窗口期。
    this.permittedOrgIds = {}
    this.permittedQuoteOrgs = {}
    this.permittedViewOrgs = {}
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
      taskHint: '',
      offerForm: '',
      offerNote: '',
      offerHint: '',
      downloadHint: '',
      capOpen: false,
      capForm: emptyCapForm(),
      capHint: '',
      capSubmitting: false,
      capConfirmKey: '',
      capScope: '',
      capNote: '',
      capConfirming: false,
      capRuleTitle: '',
      capRuleRows: [],
      capRecheckId: '',
      capRecheck: null,
      capRecheckHint: ''
    })
    // 前两个请求是**页面内容**：工作台是主内容，委托本体是它的头卡。任一失败都按失败
    // 处理 ——「显示半个工作台」会让用户以为槽位就是这些，比直接说加载失败更糟。
    //
    // 第三个是**权限投影**（D-4 裁定 §2），决定受理入口显不显示。它与前两个
    // **命运不同**：自己消化失败、降级成"空投影"（⇒ 不显示入口）。依据是裁定 §4
    // 「仍可查看其有权读取的内容」—— 拿不到权限结论时用户仍有权读这张委托，
    // 把整页打成错误态是**过度反应**，而且会说错话（委托明明读到了，页面却说失败）。
    // 保守方向也在这里：拿不到 ⇒ 不显示，而不是猜"有权限"。
    //
    // 第四个是对客报价（S3）。它与权限投影同一命运、同一理由：客户可能这单根本没有
    // 收到过发布（正常状态），而经理**永远**会拿到空列表（服务端只回"发给我的"）。
    // 让它失败时把整页打成错误态，会让经理看不了这张委托 —— 那是把"没有报价"
    // 说成"页面坏了"。
    // 第五个是**授权定位**（`session-context`）：它回答"这单该用哪一条委托授权"，
    // 是多条授权时服务端唯一不猜的答案。与上面两个投影同一命运、同一理由：
    // 取不到只是不显示「组装对客报价」入口，不该把整页打成错误态。
    // ⚠️ 不自己按 (org, owner) 去推授权 id：`artifacts.count_unassigned` 的注释写得很清楚
    //    —— 同一 (org, owner) 可能有多条授权，挑一条就是猜测，数据边界会随之改变。
    return Promise.all([
      fetchAssignment(id),
      fetchWorkbench(id),
      fetchMyOrgs().catch(function () {
        return null
      }),
      fetchMyOfferReleases().catch(function () {
        return null
      }),
      fetchSessionContext(id).catch(function () {
        return null
      })
    ])
      .then(function (res) {
        const orgItems = (res[2] && res[2].items) || []
        self.permittedOrgIds = permittedOrgIds(orgItems, ORG_PERM_CLAIM)
        self.permittedQuoteOrgs = permittedOrgIds(orgItems, ORG_PERM_QUOTE_CREATE)
        // 运力块只读的那道判据（`entrust:view`）。与上面两个各管一段：
        // 认领 / 组装报价 / 看运力成本口径，是**三种**不同的组织侧能力。
        self.permittedViewOrgs = permittedOrgIds(orgItems, ORG_PERM_VIEW)
        const detail = decorateDetail(res[0], self.permittedOrgIds)
        const board = decorateWorkbench(res[1])
        // 只挑**本单**的那条：`/my-offer-releases` 是"我收到的全部发布"，
        // 混着别的委托单。挑错会把 A 单的报价显示在 B 单上，而两者都"看起来正常"。
        const mine = pickOfferForAssignment((res[3] && res[3].items) || [], id)
        self.applyState(viewState({ status: 200, total: 1 }), detail, board, mine, res[4])
        // 运力那一块**第二轮**取：它的判据（本地组织权限投影）要等 `fetchMyOrgs` 回来才知道，
        // 而"该不该发这次请求"必须在请求**之前**判（理由见 `data.canViewCapacity`）。
        // 两轮都是同一次 `load()` 的一部分：失败由 `loadCapacity` 自己消化（不把整页打成错误态，
        // 与权限投影、对客报价同一命运 —— 委托本体明明读到了，页面不该说失败）。
        return self.loadCapacity()
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

  applyState(state, detail, board, offer, ctx) {
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
      // 「组装对客报价」= 授权能被**唯一**定位 ∧ 我在该组织内有制作报价权限。
      // `ctx.entrustment_id` 为 null ⇒ 服务端刻意不猜（未指定组织 / 无生效授权 /
      // 多条匹配需显式指定），此时同样不显示入口 —— 理由在 `ctx.note` 里，
      // 不要把它误报成"没权限"。
      canAssembleQuote:
        !!(ctx && ctx.entrustment_id) &&
        isPermittedOrg(this.permittedQuoteOrgs, detail && detail.orgId),
      entrustmentId: ctx && ctx.entrustment_id ? String(ctx.entrustment_id) : '',
      quoteOpen: false,
      quoteForm: { amount: '', currency: 'CNY', includes: '', validUntil: '' },
      quoteHint: '',
      quoteSubmitting: false,
      canCreateCase: !!(board && board.status === 'claimed'),
      unassignedHint: board ? board.unassignedHint : '',
      offer: offer ? decorateCustomerOffer(offer) : null,
      offerForm: '',
      offerNote: '',
      offerHint: '',
      downloadHint: '',
      // ── 运力块的三个判据（见 data 上的逐条说明）──
      // 读：我在该委托所属组织内有 `entrust:view`（决定**发不发**那两次取数）。
      canViewCapacity: isPermittedOrg(this.permittedViewOrgs, detail && detail.orgId),
      // 写：授权可唯一定位 ∧ 我在该组织内有 `entrust:quote:create`。
      canRecordCapacity:
        !!(ctx && ctx.entrustment_id) &&
        isPermittedOrg(this.permittedQuoteOrgs, detail && detail.orgId),
      // 候选与确认**不在这里清**：紧接着的 `loadCapacity()` 会重取。
      // （在这一步清掉会让"取数未回来"的那一帧显示成"没有候选运力"，与真事实同形。）
      capOpen: false,
      capForm: emptyCapForm(),
      capHint: '',
      capSubmitting: false,
      capConfirmKey: '',
      capScope: '',
      capNote: '',
      capConfirming: false,
      capRuleTitle: '',
      capRuleRows: [],
      capRecheckId: '',
      capRecheck: null,
      capRecheckHint: ''
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

  // ── 人工落点：组装对客报价（S3；合同 §10.1 第 6 步的前置动作）──────────
  //
  // 为什么这一步是本片的主角：能被客户看到的成果类型只有 `customer_quote` /
  // `contract_review`（`registry.CUSTOMER_VISIBLE_TYPES`），而主演示链路上 AG-02
  // 产出的是 `quote_parsed`（**船东侧报价**，服务端在信封里写着"供应商侧报价…
  // 不得互相替代"）⇒ 拿它去发布会被 400 拒，**拒得对**。计划 §4 UI-06 与 §5.2
  // 的 S3 DoD 都要求"对客报价**可人工组装**"，本组就是那一步。

  /**
   * 展开 / 收起组装表单。**页内**，不用原生弹层 —— 理由同「受理委托」：
   * 弹层不在渲染树里，它的确认键在真机上点不到 ⇒ 这条关键输入永远拿不到设备证据。
   */
  onToggleQuote() {
    if (this.data.quoteSubmitting) return
    this.setData({ quoteOpen: !this.data.quoteOpen, quoteHint: '' })
  },

  /**
   * 表单输入：按 `data-df` 决定写回哪个字段。
   *
   * 按**字段名**而不是"第几个输入框"定位：位置索引会在模板调序时静默错位
   * （写回另一个字段，页面看起来还正常）。写法与案件页的 `act-input` 一致。
   */
  onQuoteInput(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const field = String(ds.df || '')
    if (!field) return
    this.setData({
      ['quoteForm.' + field]: (e && e.detail && e.detail.value) || '',
      quoteHint: ''
    })
  },

  /** 收起并清空（取消就是取消，不留半份草稿）。 */
  onCancelQuote() {
    this.setData({
      quoteOpen: false,
      quoteForm: { amount: '', currency: 'CNY', includes: '', validUntil: '' },
      quoteHint: ''
    })
  },

  /**
   * 提交组装：创建一份 `customer_quote` 成果（**人工直写**，来源 `manual`）。
   *
   * 校验在**页内**说清（同 `onSubmitTask` 的理由：toast 会消失，而"为什么没提交"
   * 正是此刻要一直看得见的那句话）。必填字段与后端注册表逐字一致：
   * `amount` / `currency` / `includes`。
   *
   * ⚠️ **不替用户补默认值**：金额空着就是空着。替它填一个数才是真正危险的 ——
   * 那会变成一条看起来完整、却没人确认过的对客报价。
   */
  onSubmitQuote() {
    const self = this
    if (this.data.quoteSubmitting) return Promise.resolve()
    const form = this.data.quoteForm || {}
    const amountText = String(form.amount || '').trim()
    const currency = String(form.currency || '').trim()
    const includes = String(form.includes || '')
      .split(/[,\uFF0C\u3001]/)
      .map(function (s) {
        return s.trim()
      })
      .filter(function (s) {
        return !!s
      })
    const eid = String(this.data.entrustmentId || '')
    if (!eid) {
      this.setData({ quoteHint: '定位不到唯一一条生效委托授权，暂时无法组装' })
      return Promise.resolve()
    }
    // 先判空再判数：`Number('')` 是 0，只判 `isFinite` 会让空金额静默变成 ¥0
    const amount = amountText ? Number(amountText) : NaN
    if (!amountText || !isFinite(amount)) {
      this.setData({ quoteHint: '请填写「金额」，且必须是数字（对客报价的必填项）' })
      return Promise.resolve()
    }
    if (!currency) {
      this.setData({ quoteHint: '请填写「币种」' })
      return Promise.resolve()
    }
    if (!includes.length) {
      this.setData({ quoteHint: '请至少填写一项「费用包含」' })
      return Promise.resolve()
    }
    const validUntil = String(form.validUntil || '').trim()
    const payload = { amount: amount, currency: currency, includes: includes }
    if (validUntil) payload.valid_until = validUntil
    this.setData({ quoteSubmitting: true, quoteHint: '' })
    wx.showLoading({ title: '组装中', mask: true })
    return createArtifact(
      eid,
      {
        artifact_type: 'customer_quote',
        payload: payload,
        // 带上这张委托单：成果才会归属到它，界面上才看得到（不带就是"历史未归属成果"）
        assignment_id: Number(this.data.assignmentId),
        note: '经理按已确认口径人工组装的对客报价'
      },
      newIdempotencyKey('quote-assemble')
    )
      .then(function () {
        wx.hideLoading()
        self.setData({
          quoteSubmitting: false,
          quoteOpen: false,
          quoteForm: { amount: '', currency: 'CNY', includes: '', validUntil: '' }
        })
        wx.showToast({ title: '已组装', icon: 'success' })
        // 重取整页：新成果要经**槽位投影**出现在「对客方案与合同」里，
        // 而不是由前端自己往列表里塞一行 —— 否则界面上会有两份"当前状态"。
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        const status = (err && err.httpStatus) || 0
        self.setData({
          quoteSubmitting: false,
          quoteHint: status
            ? '组装未提交（服务端返回 ' + status + '），请按提示核对字段口径'
            : '组装未提交：网络异常（表单已保留，可重试）'
        })
        return null
      })
  },

  // ── 人工落点：客户响应（对客报价；S3 / BP-03 第 6/7 条）──────────────
  //
  // 这一组是本屏**唯一**「身份即权限」的落点：只有该委托的货主本人能响应。
  // 经理看得到这张卡（他有权看这条委托的发布），但服务端对他的响应请求返回 **403**
  // —— 界面因此**不给他**按钮，而不是给一个必然失败的按钮。

  /** 展开响应表单（`accept` / `reject`）；再点同一次即收起。 */
  onOfferRespond(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const want = String(ds.decision || '')
    if (!want) return
    this.setData({
      offerForm: this.data.offerForm === want ? '' : want,
      offerNote: '',
      offerHint: ''
    })
  },

  /** 说明按**路径**写回（不整对象替换，避免输入法组字被打断） */
  onOfferNoteInput(e) {
    this.setData({ offerNote: (e && e.detail && e.detail.value) || '', offerHint: '' })
  },

  /** 收起响应表单（取消）。已输入的内容**不保留** —— 取消就是取消。 */
  onOfferCancel() {
    this.setData({ offerForm: '', offerNote: '', offerHint: '' })
  },

  /**
   * 提交响应。**这一步没有回头路**：后端 `UNIQUE(release_id)`，同一次发布只能被响应一次，
   * 而"接受"是客户对**那一版内容**的确认 —— 所以确认动作必须发生在用户明确选中的那一版上
   * （`offerForm` 由按钮的 `data-decision` 决定，不靠"再读一次状态"）。
   */
  onOfferSubmit() {
    const offer = this.data.offer
    const decision = String(this.data.offerForm || '')
    if (!offer || !decision) return Promise.resolve()
    const note = String(this.data.offerNote || '').trim()
    if (decision === 'reject' && !note) {
      // 拒绝必须给一句话：经理那边只看到"已拒绝"是没法决定下一步的
      this.setData({ offerHint: '拒绝时请写明原因（经理据此决定是改价还是撤回）' })
      return Promise.resolve()
    }
    return this.submitOfferResponse(decision, note)
  },

  submitOfferResponse(decision, note) {
    const self = this
    if (this.data.offerSubmitting) return Promise.resolve()
    this.setData({ offerSubmitting: true, offerHint: '' })
    wx.showLoading({ title: '提交中', mask: true })
    return respondOffer(
      this.data.offer.releaseId,
      { decision: decision, note: note || null },
      newIdempotencyKey('offer-resp')
    )
      .then(function () {
        wx.hideLoading()
        self.setData({ offerSubmitting: false, offerForm: '', offerNote: '' })
        wx.showToast({ title: decision === 'accept' ? '已接受' : '已拒绝', icon: 'success' })
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        const status = (err && err.httpStatus) || 0
        if (status === 409) {
          // 已被响应过 / 已撤回 / 已被取代：页面上那个按钮已经没有意义了。
          // **必须刷新**，否则用户会对着一个不可能成功的按钮反复点。
          self.setData({ offerSubmitting: false })
          return self.load()
        }
        self.setData({
          offerSubmitting: false,
          offerHint: status
            ? '响应未提交（服务端返回 ' + status + '），请按提示处理'
            : '响应未提交：网络异常（说明已保留，可重试）'
        })
        return null
      })
  },

  /**
   * 下载发布清单里的一份授权附件。
   *
   * 判据全在服务端（清单外的附件返回 404）—— 前端在这里**不做**任何"这份能不能下"的判断，
   * 因为前端看到的清单和发布时冻结的清单可能已经被后来的操作改变，自己判会给出
   * 一个"看着能点、点了 404"的按钮。
   */
  onDownloadOfferAttachment(e) {
    const self = this
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const attId = ds.id
    if (!attId || !this.data.offer) return Promise.resolve()
    this.setData({ downloadHint: '正在下载附件 #' + attId + '…' })
    return downloadOfferAttachment(this.data.offer.releaseId, attId)
      .then(function (res) {
        self.setData({ downloadHint: '附件 #' + attId + ' 已下载，正在打开…' })
        wx.openDocument({
          filePath: res.filePath,
          showMenu: true,
          fail() {
            // 打开失败**不影响**"下载成功"这个事实：开发者工具对部分文件类型不提供预览。
            // 两件事分开说，否则会被读成"下载坏了"。
            self.setData({
              downloadHint: '附件 #' + attId + ' 已下载到临时文件，但当前环境不支持直接打开'
            })
          }
        })
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        let hint = '附件下载失败：网络异常'
        if (status === 404) {
          // ⚠️ 这一格的 404 有确定含义（不在发布冻结的授权清单里），不是网络抖动。
          // 说成"失败，请重试"会让人一直重试一件永远不会成功的事。
          hint = '附件 #' + attId + ' 不在本次发布的授权清单内（服务端 404）'
        } else if (status) {
          hint = '附件下载失败（服务端返回 ' + status + '）'
        }
        self.setData({ downloadHint: hint })
        return null
      })
  },

  // ── 运力确认与有效期（经理侧；BP-03 第 2、3 条 / D1-06 / 合同 §10.1 第 5 步）──
  //
  // 判据分三层，越往下越"以服务端为准"：
  //   ① 该不该**发这次取数** —— 本地组织权限投影里有 `entrust:view`（`loadCapacity`）；
  //   ② 该不该**显示入口** —— 授权能被唯一定位 ∧ 我在该组织内有 `entrust:quote:create`；
  //   ③ 该不该**放行** —— 完全由服务端判定（403 / 409 + 逐条判定）。
  // 前端一律不猜第 ③ 层：本页给出的每一个「不通过」都必须是后端说的原话。

  /**
   * 取运力数据（候选清单 + 已作出的确认）。
   *
   * ⚠️ 客户侧**一次请求都不发**（理由见 `data.canViewCapacity`）。取不到时也不把整页
   * 打成错误态：这与权限投影、对客报价同一命运 —— 拿不到运力结论时用户仍有权读这张委托。
   * 但**不静默**：有 `entrust:view` 却被拒是一次真失败，必须在页内说出来。
   */
  loadCapacity() {
    const self = this
    if (!this.data.canViewCapacity) {
      this.setData({ capCandidates: [], capConfirmations: [] })
      return Promise.resolve()
    }
    const id = this.data.assignmentId
    return Promise.all([fetchCapacityCandidates(id), fetchCapacityConfirmations(id)])
      .then(function (res) {
        self.setData({
          capCandidates: (res[0] || []).map(decorateCapacityCandidate),
          capConfirmations: (res[1] || []).map(decorateCapacityConfirmation)
        })
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.setData({
          capCandidates: [],
          capConfirmations: [],
          capHint: status
            ? '运力数据未能读取（服务端返回 ' + status + '）—— 本地权限投影与授权结论不一致时也会这样'
            : '运力数据未能读取：网络异常'
        })
      })
  },

  /** 展开 / 收起登记表单。**页内**，不用原生弹层（理由见文件头的「交互形态」）。 */
  onToggleCap() {
    if (this.data.capSubmitting) return
    this.setData({ capOpen: !this.data.capOpen, capHint: '' })
  },

  /** 收起并清空（取消就是取消，不留半份草稿 —— 与「组装对客报价」同一取向）。 */
  onCancelCap() {
    this.setData({ capOpen: false, capForm: emptyCapForm(), capHint: '' })
  },

  /**
   * 表单输入：按 `data-df` 决定写回哪个字段。
   *
   * 按**字段名**而不是"第几个输入框"定位：位置索引会在模板调序时静默错位
   * （写回另一个字段，页面看起来还正常）。与 `onQuoteInput` / 案件页的 `act-input` 同形。
   */
  onCapInput(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const field = String(ds.df || '')
    if (!field) return
    // 表的值是 **setData 路径**，不是裸字段名：本块有两个输入框不在 `capForm` 里。
    const map = {
      'cap-carrier': 'capForm.carrier',
      'cap-vessel': 'capForm.vesselName',
      'cap-tonnes': 'capForm.capacityTonnes',
      'cap-vessels': 'capForm.vesselCount',
      'cap-rate': 'capForm.rate',
      'cap-rate-unit': 'capForm.rateUnit',
      'cap-currency': 'capForm.currency',
      'cap-valid': 'capForm.validUntil',
      'cap-evidence-ref': 'capForm.evidenceRef',
      // 范围与备注属于**这一次确认**，不属于候选草稿：它们随确认条的开关生灭
      // （`onOpenCapConfirm` / `onCancelCapConfirm` 会清掉），所以留在 `data` 根上。
      // 混进 `capForm` 会让"取消再打开"把上一次的范围带过来，看起来像已经填好了。
      //
      // ⚠️ 少了这两条时：`bindinput` 照常触发、`ds.df` 也照常读到，只是 map 查不到 ⇒
      //    **静默空转**，输入框一个字符都写不进去，而页面表现成"范围永远填不上、
      //    确认永远提示必填"。e2e 的 ⑰ 段就是靠这条红的（`capScope` 恒为空 ⇒
      //    `onSubmitCapConfirm` 在校验处就返回，四条逐条判定一条都没拿到）。
      'cap-scope': 'capScope',
      'cap-note': 'capNote'
    }
    const path = map[field]
    if (!path) return
    // 按**路径**写回（不整对象替换，避免输入法组字被打断）
    this.setData({ [path]: (e && e.detail && e.detail.value) || '', capHint: '' })
  },

  /**
   * 装载口径：**判容量时要用到**，所以必须显式选而不是默认。
   * 夹具层面已经证明：只写"900 吨"没有判据价值 —— 允许拆批或多船承运时，
   * 950 吨未必装不下。`data-act-partial` 给的是 `'0'` / `'1'`，这里转成布尔。
   */
  onCapPickPartial(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    this.setData({ 'capForm.allowsPartialLoad': String(ds.actCapPartial) === '1', capHint: '' })
  },

  /** 证据类别：选中 / 再点一次取消（不选就是"未登记"，**不替用户补默认值**）。 */
  onCapPickKind(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const kind = String(ds.actCapKind || '')
    if (!kind) return
    this.setData({
      'capForm.evidenceKind': this.data.capForm.evidenceKind === kind ? '' : kind,
      capHint: ''
    })
  },

  /**
   * 表单 → 请求体。**转换只在这一处**（模板的 kebab 名 → 后端字段名）。
   *
   * 校验在**页内**说清（同 `onSubmitTask` / `onSubmitQuote` 的理由：toast 会消失，
   * 而"为什么没提交"正是此刻要一直看得见的那句话）。前端这几条只挡"必然被后端拒"
   * 的输入（少一次往返），**服务端才是判据**。空字段**不发键** —— 发 `""` 会让
   * 服务端的报错与用户的操作对不上（与案件页登记表单同一条）。
   */
  buildCapBody() {
    const f = this.data.capForm || {}
    const carrier = String(f.carrier || '').trim()
    if (!carrier) return { hint: '请填写「承运人」（必填）' }
    const tonnesText = String(f.capacityTonnes || '').trim()
    // 先判空再判数：`Number('')` 是 0，只判 `isFinite` 会让空吨位静默变成 0 吨
    const tonnes = tonnesText ? Number(tonnesText) : NaN
    if (!tonnesText || !isFinite(tonnes)) return { hint: '请填写「运力吨位」，且必须是数字（必填）' }
    if (!(tonnes > 0)) return { hint: '「运力吨位」必须大于 0 —— 0 吨的候选无法通过容量判定' }
    const vesselsText = String(f.vesselCount || '').trim()
    const vessels = vesselsText ? Number(vesselsText) : 1
    if (!isFinite(vessels) || !(vessels >= 1) || Math.floor(vessels) !== vessels) {
      return { hint: '「船数」必须是不小于 1 的整数（留空按 1 条船）' }
    }
    const rateText = String(f.rate || '').trim()
    const rateUnit = String(f.rateUnit || '').trim()
    // 单价与计价单位**成对**：只有金额无法确定费用（后端同一条，注册表里也是成对的）
    if (!!rateText !== !!rateUnit) return { hint: '「单价」与「计价单位」必须一起给或一起留空' }
    if (rateText && (!isFinite(Number(rateText)) || !(Number(rateText) > 0))) {
      return { hint: '「单价」必须是正数' }
    }
    const validUntil = String(f.validUntil || '').trim()
    if (validUntil && !/^\d{4}-\d{2}-\d{2}$/.test(validUntil)) {
      return { hint: '「有效期至」要写成 2026-12-31 这样的日期' }
    }
    const body = { carrier: carrier, capacity_tonnes: tonnes, vessel_count: vessels }
    body.allows_partial_load = !!f.allowsPartialLoad
    const vesselName = String(f.vesselName || '').trim()
    if (vesselName) body.vessel_name = vesselName
    if (rateText) body.rate = Number(rateText)
    if (rateUnit) body.rate_unit = rateUnit
    const currency = String(f.currency || '').trim()
    if (currency) body.currency = currency
    if (validUntil) body.valid_until = validUntil
    const kind = String(f.evidenceKind || '').trim()
    const ref = String(f.evidenceRef || '').trim()
    if (kind) body.evidence_kind = kind
    if (ref) body.evidence_ref = ref
    return { body: body }
  },

  /**
   * 登记一条候选运力。
   *
   * ⚠️ 这一步**不确认**任何东西，注册表与合同 BP-03 第 3 条都把两者分开
   * （`A chosen quotation alone does not create confirmed capacity.`）。
   * 成功后走整页 `load()`：候选要经**投影**回到清单里，而不是前端自己往数组里塞一行。
   */
  onSubmitCap() {
    const self = this
    if (this.data.capSubmitting) return Promise.resolve()
    const built = this.buildCapBody()
    if (built.hint) {
      this.setData({ capHint: built.hint })
      return Promise.resolve()
    }
    this.setData({ capSubmitting: true, capHint: '' })
    wx.showLoading({ title: '登记中', mask: true })
    return recordCapacityCandidate(
      this.data.assignmentId,
      built.body,
      newIdempotencyKey('cap-cand')
    )
      .then(function () {
        wx.hideLoading()
        self.setData({ capSubmitting: false })
        wx.showToast({ title: '已登记候选', icon: 'success' })
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        const status = (err && err.httpStatus) || 0
        self.setData({
          capSubmitting: false,
          capHint: status
            ? '候选未登记（服务端返回 ' + status + '），请按提示核对字段口径'
            : '候选未登记：网络异常（表单已保留，可重试）'
        })
        return null
      })
  },

  /**
   * 展开某一条候选的**页内**确认条。再点同一条即收起（不留"必须点别处才能取消"的面板）。
   *
   * 为什么不是原生弹层：确认是本块唯一会**改变业务事实、且不可回退**的动作
   * （后端 `UNIQUE(candidate_id)`，一条候选只能被确认一次），它必须可被真机验证。
   * 弹层不在渲染树里、确认键点不到 ⇒ 只能记 `LIMITATION`（⑧b / ㉕D 的先例）。
   */
  onOpenCapConfirm(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    // ⚠️ 读的是 `data-act-cap-confirm-open` 对应的 **dataset 键** `actCapConfirmOpen`
    //    （小驼峰），不是 `ds.id` —— `data-act-*` 被属性名转写过了。
    //    写成 `ds.id` 会恒为 undefined ⇒ 按钮点了没反应，且不报错。
    const id = String(ds.actCapConfirmOpen == null ? '' : ds.actCapConfirmOpen)
    if (!id) return
    const same = String(this.data.capConfirmKey) === id
    this.setData({
      capConfirmKey: same ? '' : id,
      capScope: same ? '' : this.data.capScope,
      capNote: same ? '' : this.data.capNote,
      capHint: '',
      capRuleRows: [],
      capRuleTitle: ''
    })
  },

  onCancelCapConfirm() {
    this.setData({ capConfirmKey: '', capScope: '', capNote: '', capHint: '' })
  },

  /**
   * 提交确认。
   *
   * 请求体**只带** `candidate_id` 与 `agreed_scope`（+ 可选 note）—— 一切事实
   * （承运人、吨位、船数、是否拆批、单价、有效期、证据）都由后端**从候选行读**。
   * 前端把事实一起发过去，会造出"确认的内容"与"候选运力"可以不一致的状态，
   * 而那条不一致在界面上看不出来 ⇒「确认」就退化成一次自述。
   *
   * 失败分流（两种 409 的**处置完全不同**，混在一起就会把用户送进死循环）：
   *   · 带 `rule_checks` ⇒ **规则不过**：事实没变，把**逐条**判定照实显示出来，
   *     **不刷新**（刷新会把用户刚看到的那张判定表顶掉）；
   *   · 带 `existing_confirmation_id` ⇒ 状态冲突（已被确认过）：**必须刷新** ——
   *     页面上那个按钮已经没有意义了，不刷新用户会对着它反复点。
   */
  onSubmitCapConfirm(e) {
    const self = this
    if (this.data.capConfirming) return Promise.resolve()
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    // 同上：dataset 键是 `actCapConfirmSubmit`（不是 `id`）
    const candidateId = Number(ds.actCapConfirmSubmit)
    const scope = String(this.data.capScope || '').trim()
    if (!candidateId) return Promise.resolve()
    if (!scope) {
      // `agreed_scope` 是**必填**：它是一条采购范围判断，数据里没有它的出处
      this.setData({ capHint: '请填写「本次确认覆盖的范围」（必填）' })
      return Promise.resolve()
    }
    const note = String(this.data.capNote || '').trim()
    this.setData({ capConfirming: true, capHint: '', capRuleRows: [], capRuleTitle: '' })
    wx.showLoading({ title: '确认中', mask: true })
    return confirmCapacity(
      this.data.assignmentId,
      { candidate_id: candidateId, agreed_scope: scope, note: note || null },
      newIdempotencyKey('cap-confirm')
    )
      .then(function () {
        wx.hideLoading()
        self.setData({ capConfirming: false, capConfirmKey: '', capScope: '', capNote: '' })
        wx.showToast({ title: '运力已确认', icon: 'success' })
        return self.load()
      })
      .catch(function (err) {
        wx.hideLoading()
        const status = (err && err.httpStatus) || 0
        const det = (err && err.detail) || null
        const isObj = !!(det && typeof det === 'object')
        const rows = isObj ? capacityRuleRows(det) : []
        const msg = isObj ? String(det.message || '') : String(det || '')
        if (rows.length) {
          // 规则不过：把**全部**判定照实摆出来（含通过的 —— 后端特意全给）
          return Promise.resolve(
            self.setData({
              capConfirming: false,
              capRuleTitle: msg || '这一条候选不满足确认条件',
              capRuleRows: rows,
              capHint: ''
            })
          )
        }
        if (isObj && det.existing_confirmation_id) {
          self.setData({ capConfirming: false })
          return self.load()
        }
        if (status === 403 || (isObj && det.existing_artifact_id)) {
          self.setData({ capConfirming: false })
          return self.load()
        }
        self.setData({
          capConfirming: false,
          capHint: status
            ? '确认未提交（服务端返回 ' + status + '）' + (msg ? '：' + msg : '，请按提示处理')
            : '确认未提交：网络异常（填写内容已保留，可重试）'
        })
        return null
      })
  },

  /**
   * 只读复算这条确认（不改任何行）。
   *
   * 它存在的全部意义是让「900 吨候选在变更后不再适用」这句话**可以被看到**：
   * 同一套规则换一组当前事实之后不再通过了。所以页面上必须并排给出结论、
   * 变化了的判定输入、以及**不通过那几条的原话** —— 只给一个"已失效"等于让人去猜。
   */
  onCapRecheck(e) {
    const self = this
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    // 同上：dataset 键是 `actCapRecheck`
    const id = ds.actCapRecheck
    if (!id) return Promise.resolve()
    this.setData({ capRecheckHint: '' })
    return recheckCapacityConfirmation(id)
      .then(function (res) {
        self.setData({
          capRecheckId: String(id),
          capRecheck: decorateCapacityRecheck(res),
          capRecheckHint: ''
        })
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.setData({
          capRecheckId: String(id),
          capRecheck: null,
          capRecheckHint: status
            ? '复算未能进行（服务端返回 ' + status + '）'
            : '复算未能进行：网络异常'
        })
        return null
      })
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
