// 委托发货 · 财务与结算（§10.1 第 10–11 步的界面入口）
//
// 本页是**第 10–11 步在现有产品界面上的落点**（裁定 Q4=A：第 10–12 步经现有界面演示，
// 不新增专用演示页面）。它把三片后端此前**都只能在接口层演示**的能力接了出来：
//
//   · 费用行（S7-1）：清单与合计（按「币种 × 收付方向」分开）、登记、确认、
//     提争议、处置（**必须显式给「是否计入」** —— 裁定 Q2=B）；
//   · 缺件与补录（S7-2）：缺什么证据（派生）、在**原任务**上补录一条
//     （`occurred_at` 是**业务发生时间**，与记录时间分开）；
//   · 结算与收付（S7-3）：生成**版本**、内部确认、**客户确认该精确版本**、
//     记收付依据、预览**对客投影**、看财务结案状态与未结成因。
//
// 三条通道的可见性**不同**，本页不做二次判断，只把服务端的结论照实渲染：
//   · 费用与结算的**内部**读数（版本链 / 财务状态）**没有货主面** —— 行上带对手方与
//     内部成本。因此取数**之前**先按本地组织权限（`ORG_PERM_VIEW`）判一次，
//     不该看的一次请求都不发（与运力那一组同口径）。隐藏不等于放行，服务端仍独立判定。
//   · **对客投影**（`customer-view`）走委托授权链，**货主本人可见**；
//   · **客户确认**只有货主本人能做 —— 经理点了会拿到 403。那一格**照实显示服务端的文案**
//     （不自己编），因为"客户确认不得由经理代做"这条口径的正文在后端，抄一份必然漂移。
//
// 交互形态：关键路径一律走**页内 DOM**（`data.open*` 系列），**不用 `wx.showModal`** ——
// 原生弹层不在渲染树里，走查工具点不到它的确认键（判据：弹层承担的关键路径 = 不可验证的路径）。
//
// 导航：本页登记在 `utils/routes.js` 的 `ROUTES` / `NAV_EDGES` / `MIGRATED_PAGES`，
// `onLoad` 的入口守卫与 `go()` 用**同一份** `paramSchema`。

const {
  FINANCIAL_BLOCKER_LABELS,
  FINANCIAL_STATUS_LABELS,
  ORG_PERM_VIEW,
  VIEW,
  approveSettlement,
  confirmCharge,
  confirmSettlement,
  createSettlement,
  customerSettlementHint,
  decorateCharge,
  decorateChargeTotal,
  decorateEvidenceGap,
  decorateSettlement,
  disputeCharge,
  fetchCharges,
  fetchCustomerSettlement,
  fetchEvidenceGaps,
  fetchFinancialStatus,
  fetchMyOrgs,
  fetchSettlements,
  fetchWorkbench,
  newIdempotencyKey,
  recordCharge,
  recordSettlementPayment,
  recordTaskEvidence,
  resolveCharge
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）；`go()` 需要知道「从哪来」才能查到导航边 */
const SELF = 'pages/entrust/finance/finance'

//: 证据类型（与后端 `tasks.EVIDENCE_KINDS` 同一取值域，顺序是给人读的）。
const EVIDENCE_KIND_CHOICES = [
  { code: 'document', label: '单证' },
  { code: 'photo', label: '照片' },
  { code: 'receipt', label: '回执' },
  { code: 'confirmation', label: '确认函' },
  { code: 'email', label: '邮件' },
  { code: 'contract', label: '合同' },
  { code: 'payment', label: '支付凭据' }
]

//: 收付方向（与费用行同一口径：应收＝对客户，应付＝对供应商）。
const DIRECTION_CHOICES = [
  { code: 'receivable', label: '应收' },
  { code: 'payable', label: '应付' }
]

//: 处置结果（裁定 Q2=B 的三种**金额结果**）。
const OUTCOME_CHOICES = [
  { code: 'accepted', label: '认可' },
  { code: 'adjusted', label: '调减' },
  { code: 'rejected', label: '拒绝' }
]

//: 默认币种（DEMO-1 仅 CNY；跨币种不相加是后端明确不做的）。
const DEFAULT_CURRENCY = 'CNY'

Page({
  data: {
    statusBarHeight: 20,
    assignmentId: '',
    loading: true,
    errorTitle: '',
    errorHint: '',
    denied: false,

    // 组织权限（本地投影：不该看的一次请求都不发）
    canViewInternal: false,
    // 对客通道（货主侧）：内部读数对它一律 404 ⇒ 走 workbench 给的「该确认哪一版」
    customerMode: false,
    customerSettlementId: '',
    customerVersionText: '',
    awaitingCustomer: false,
    customerHint: '',
    customerLines: [],

    // 费用
    charges: [],
    chargeTotals: [],
    chargeCounted: 0,
    chargeExcluded: 0,
    chargeOpen: false,
    chargeForm: { direction: 'receivable', chargeKind: '', amount: '', basis: '', counterparty: '' },
    chargeHint: '',
    disputeOpenKey: '',
    disputeForm: { reason: '' },
    resolveOpenKey: '',
    resolveForm: { outcome: 'accepted', method: '', countsInTotal: 'yes', finalAmount: '' },

    // 缺件与交接
    gaps: [],
    gapMissingTotal: 0,
    gapTaskCount: 0,
    handoverPresent: false,
    handoverMissingText: '',
    evidenceOpenKey: '',
    evidenceForm: { kind: 'document', ref: '', occurredAt: '' },
    evidenceHint: '',

    // 结算与收付
    settlements: [],
    applicableId: '',
    financialStatus: '',
    financialStatusText: '',
    blockers: [],
    balances: [],
    settlementHint: '',
    paymentOpenKey: '',
    paymentForm: { direction: 'receivable', amount: '', ref: '' },
    customerView: null,
    customerViewHint: '',

    evidenceKindChoices: EVIDENCE_KIND_CHOICES,
    directionChoices: DIRECTION_CHOICES,
    outcomeChoices: OUTCOME_CHOICES,
    currency: DEFAULT_CURRENCY
  },

  onLoad(query) {
    const rawId = R.decodeParam(query && query.assignment_id)
    const gate = R.guardEntry(SELF + (rawId ? '?assignment_id=' + encodeURIComponent(rawId) : ''), {
      coldStart: R.currentDepth() <= 1
    })
    if (!gate.ok) {
      this.setData({
        loading: false,
        errorTitle: '打不开这一页',
        errorHint: (gate.errors || []).map(function (e) {
          return String(e.reason || '')
        }).join('；') || '委托标识缺失或格式不对'
      })
      return
    }
    try {
      const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
      this.setData({ statusBarHeight: info.statusBarHeight || 20 })
    } catch (e) {
      // 拿不到状态栏高度就用默认值（不影响功能）
    }
    this.setData({ assignmentId: rawId })
  },

  onShow() {
    this.load()
  },

  load() {
    const id = this.data.assignmentId
    if (!id) return
    this.setData({ loading: true, errorTitle: '', errorHint: '', denied: false })

    // 先判本地组织权限，再决定取哪些（内部读数**没有货主面**）。
    fetchMyOrgs()
      .then((res) => {
        const orgs = (res && res.items) || []
        const canInternal = orgs.some(function (o) {
          return ((o && o.permissions) || []).indexOf(ORG_PERM_VIEW) >= 0
        })
        this.setData({ canViewInternal: canInternal })
        return this.fetchAll(canInternal)
      })
      .catch((err) => {
        this.applyError(err)
      })
  },

  /** 取数：内部三块只在有组织权限时取；**货主侧走对客通道**。 */
  fetchAll(canInternal) {
    const id = this.data.assignmentId
    if (!canInternal) {
      // ⚠️ 货主侧**一条内部请求都不发**（费用 / 版本链 / 财务状态对它都是 404）。
      //    ⛔ 也不能就此渲染"空区块"：那会显示成「还没有计入合计的费用行」＋
      //    「未开始」，把"你没有这个视角"伪装成一个正常的空状态 ——
      //    正是本项目反复拦的「错误被显示成空」。
      return fetchWorkbench(id)
        .then((res) => {
          const hint = customerSettlementHint(res)
          if (!hint) {
            this.setData({
              loading: false,
              canViewInternal: false,
              customerMode: true,
              customerSettlementId: '',
              customerHint: '这张委托还没有结算版本 —— 等经理出结算后，这里会出现待你确认的那一版',
              customerLines: []
            })
            return null
          }
          return fetchCustomerSettlement(hint.settlementId)
            .then((view) => {
              this.setData({
                loading: false,
                canViewInternal: false,
                customerMode: true,
                customerSettlementId: hint.settlementId,
                customerVersionText: hint.versionText,
                awaitingCustomer: hint.awaitingCustomer,
                customerHint: '',
                customerLines: this.customerLineRows(view)
              })
              return null
            })
            .catch((err) => this.applyError(err))
        })
        .catch((err) => this.applyError(err))
    }
    return Promise.all([
      fetchCharges(id),
      fetchEvidenceGaps(id),
      fetchSettlements(id),
      fetchFinancialStatus(id)
    ]).then((results) => {
      this.applyFinance(results[0] || {}, results[1] || {}, results[2] || {}, results[3] || {})
      return null
    })
  },

  /** 对客投影 → 模板可直接渲染的行（字段名与服务端白名单一致，不自己挑）。 */
  customerLineRows(view) {
    return ((view && view.lines) || []).map(function (line) {
      const l = line || {}
      return {
        key: String(l.charge_id == null ? '' : l.charge_id),
        chargeKind: l.charge_kind || '',
        amountText: l.amount === null || l.amount === undefined ? '未知' : String(l.amount),
        currency: l.currency || '',
        basis: l.basis || ''
      }
    })
  },

  applyFinance(chargePayload, gapPayload, settlementPayload, financial) {
    const charges = (chargePayload.items || []).map(decorateCharge)
    const totals = (chargePayload.groups || []).map(decorateChargeTotal)
    const gaps = (gapPayload.items || []).map(decorateEvidenceGap)
    const handover = gapPayload.handover || {}
    const settlements = (settlementPayload.items || []).map(decorateSettlement)
    const blockers = (financial.blockers || []).map(function (b) {
      const code = (b && b.code) || ''
      return {
        key: code,
        code: code,
        text: FINANCIAL_BLOCKER_LABELS[code] || (b && b.message) || code,
        message: (b && b.message) || ''
      }
    })
    this.setData({
      loading: false,
      charges: charges,
      chargeTotals: totals,
      chargeCounted: Number(chargePayload.counted_lines || 0),
      chargeExcluded: Number(chargePayload.excluded_lines || 0),
      gaps: gaps,
      gapMissingTotal: Number(gapPayload.missing_total || 0),
      gapTaskCount: Number(gapPayload.tasks_total || 0),
      handoverPresent: handover.present === true,
      handoverMissingText: (handover.missing || []).join('、'),
      settlements: settlements,
      applicableId: settlementPayload.applicable_settlement_id
        ? String(settlementPayload.applicable_settlement_id)
        : '',
      financialStatus: financial.financial_status || '',
      financialStatusText: FINANCIAL_STATUS_LABELS[financial.financial_status] || financial.financial_status || '未知',
      blockers: blockers,
      balances: (financial.balances || []).map(function (b) {
        const row = b || {}
        return {
          key: String(row.direction || '') + '#' + String(row.currency || ''),
          directionText: row.direction === 'receivable' ? '应收' : row.direction === 'payable' ? '应付' : (row.direction || '未知'),
          currency: row.currency || '',
          totalText: row.total === null || row.total === undefined ? '未知' : String(row.total),
          settledText: row.settled === null || row.settled === undefined ? '未知' : String(row.settled),
          outstandingText: row.outstanding === null || row.outstanding === undefined ? '未知' : String(row.outstanding)
        }
      })
    })
  },

  applyError(err) {
    const status = (err && err.httpStatus) || 0
    if (status === 403 || status === 404) {
      this.setData({
        loading: false,
        denied: true,
        errorTitle: '无查看权限',
        errorHint: '费用与结算的内部读数不设货主面（行上带对手方与内部成本）'
      })
      return
    }
    this.setData({
      loading: false,
      errorTitle: '加载失败',
      errorHint: status ? '接口返回 ' + status : '无法连接后端，请确认服务已启动'
    })
  },

  // ── 费用：登记 / 确认 / 提争议 / 处置 ────────────────────────────────

  onToggleChargeForm() {
    this.setData({ chargeOpen: !this.data.chargeOpen, chargeHint: '' })
  },

  onChargeInput(e) {
    const key = (e.currentTarget.dataset && e.currentTarget.dataset.key) || ''
    if (!key) return
    const form = Object.assign({}, this.data.chargeForm)
    form[key] = e.detail.value
    this.setData({ chargeForm: form })
  },

  onPickChargeDirection(e) {
    const code = (e.currentTarget.dataset && e.currentTarget.dataset.code) || ''
    if (!code) return
    this.setData({ chargeForm: Object.assign({}, this.data.chargeForm, { direction: code }) })
  },

  onSubmitCharge() {
    const f = this.data.chargeForm
    if (!f.chargeKind || !f.amount || !f.basis) {
      this.setData({ chargeHint: '类别、金额与计费依据都要填：没有依据的费用行不可核对' })
      return
    }
    const body = {
      direction: f.direction,
      charge_kind: f.chargeKind,
      amount: f.amount,
      currency: DEFAULT_CURRENCY,
      basis: f.basis,
      counterparty: f.counterparty || null
    }
    recordCharge(this.data.assignmentId, body, newIdempotencyKey('charge'))
      .then(() => {
        this.setData({
          chargeOpen: false,
          chargeHint: '已登记为草稿 —— 草稿不进合计，确认后才算数',
          chargeForm: { direction: 'receivable', chargeKind: '', amount: '', basis: '', counterparty: '' }
        })
        this.load()
      })
      .catch((err) => this.setData({ chargeHint: this.describe(err) }))
  },

  onConfirmCharge(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    if (!id) return
    confirmCharge(id, {}, newIdempotencyKey('charge-confirm'))
      .then(() => this.load())
      .catch((err) => this.setData({ chargeHint: this.describe(err) }))
  },

  onOpenDispute(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    this.setData({ disputeOpenKey: this.data.disputeOpenKey === id ? '' : id, chargeHint: '' })
  },

  onDisputeInput(e) {
    this.setData({ disputeForm: { reason: e.detail.value } })
  },

  onSubmitDispute(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    const reason = (this.data.disputeForm.reason || '').trim()
    if (!reason) {
      this.setData({ chargeHint: '争议必须写理由：只标「有争议」而不说为什么，合计的差异无从复核' })
      return
    }
    disputeCharge(id, { reason: reason }, newIdempotencyKey('charge-dispute'))
      .then(() => {
        this.setData({ disputeOpenKey: '', disputeForm: { reason: '' }, chargeHint: '已提争议 —— 争议行退出合计' })
        this.load()
      })
      .catch((err) => this.setData({ chargeHint: this.describe(err) }))
  },

  onOpenResolve(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    this.setData({ resolveOpenKey: this.data.resolveOpenKey === id ? '' : id, chargeHint: '' })
  },

  onResolveInput(e) {
    const key = (e.currentTarget.dataset && e.currentTarget.dataset.key) || ''
    if (!key) return
    const form = Object.assign({}, this.data.resolveForm)
    form[key] = e.detail.value
    this.setData({ resolveForm: form })
  },

  onPickOutcome(e) {
    const code = (e.currentTarget.dataset && e.currentTarget.dataset.code) || ''
    if (!code) return
    this.setData({ resolveForm: Object.assign({}, this.data.resolveForm, { outcome: code }) })
  },

  onPickCounts(e) {
    const code = (e.currentTarget.dataset && e.currentTarget.dataset.code) || ''
    if (!code) return
    this.setData({ resolveForm: Object.assign({}, this.data.resolveForm, { countsInTotal: code }) })
  },

  onSubmitResolve(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    const f = this.data.resolveForm
    if (!f.method) {
      this.setData({ chargeHint: '处置依据必填 —— 无依据的处置等于没处置' })
      return
    }
    const counts = f.countsInTotal === 'yes'
    if (counts && !f.finalAmount) {
      this.setData({ chargeHint: '选择「计入合计」时必须给出最终金额' })
      return
    }
    const body = {
      outcome: f.outcome,
      method: f.method,
      counts_in_total: counts,
      final_amount: counts ? f.finalAmount : null
    }
    resolveCharge(id, body, newIdempotencyKey('charge-resolve'))
      .then(() => {
        this.setData({
          resolveOpenKey: '',
          resolveForm: { outcome: 'accepted', method: '', countsInTotal: 'yes', finalAmount: '' },
          chargeHint: '已处置'
        })
        this.load()
      })
      .catch((err) => this.setData({ chargeHint: this.describe(err) }))
  },

  // ── 缺件：在原任务补录 ──────────────────────────────────────────────

  onOpenEvidence(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    this.setData({ evidenceOpenKey: this.data.evidenceOpenKey === id ? '' : id, evidenceHint: '' })
  },

  onEvidenceInput(e) {
    const key = (e.currentTarget.dataset && e.currentTarget.dataset.key) || ''
    if (!key) return
    const form = Object.assign({}, this.data.evidenceForm)
    form[key] = e.detail.value
    this.setData({ evidenceForm: form })
  },

  onPickEvidenceKind(e) {
    const code = (e.currentTarget.dataset && e.currentTarget.dataset.code) || ''
    if (!code) return
    this.setData({ evidenceForm: Object.assign({}, this.data.evidenceForm, { kind: code }) })
  },

  onSubmitEvidence(e) {
    const taskId = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    const f = this.data.evidenceForm
    if (!f.ref) {
      this.setData({ evidenceHint: '证据必须有来源（附件键 / 文件说明 / 外部凭据编号）' })
      return
    }
    if (!f.occurredAt) {
      // 业务发生时间**必填**：补录的语义就是"事后登记一件已经发生的事"，
      // 说不出它何时发生的登记没有把事情说清（后端也按必填校验）。
      this.setData({ evidenceHint: '业务发生时间必填（这件事实际发生的时间，不是现在）' })
      return
    }
    recordTaskEvidence(
      taskId,
      { kind: f.kind, ref: f.ref, occurred_at: f.occurredAt, source: 'manual' },
      newIdempotencyKey('evidence')
    )
      .then((res) => {
        const gap = (res && res.gap) || {}
        const missing = (gap.missing || []).join('、')
        this.setData({
          evidenceOpenKey: '',
          evidenceForm: { kind: 'document', ref: '', occurredAt: '' },
          evidenceHint: missing ? '已补录；还缺：' + missing : '已补录 —— 这个任务的必需证据齐了'
        })
        this.load()
      })
      .catch((err) => this.setData({ evidenceHint: this.describe(err) }))
  },

  // ── 结算：生成版本 / 内部确认 / 客户确认 / 收付依据 / 对客投影 ──────

  onCreateSettlement() {
    createSettlement(this.data.assignmentId, newIdempotencyKey('settlement'))
      .then((res) => {
        this.setData({ settlementHint: '已生成 ' + String((res && res.version_no) || '') + ' 版（待内部确认）' })
        this.load()
      })
      .catch((err) => this.setData({ settlementHint: this.describe(err) }))
  },

  onApproveSettlement(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    if (!id) return
    approveSettlement(id, {}, newIdempotencyKey('settlement-approve'))
      .then(() => {
        this.setData({ settlementHint: '已内部确认 —— 下一步等客户确认这一版' })
        this.load()
      })
      .catch((err) => this.setData({ settlementHint: this.describe(err) }))
  },

  onConfirmSettlement(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    if (!id) return
    confirmSettlement(id, { decision: 'accepted' }, newIdempotencyKey('settlement-confirm'))
      .then(() => {
        this.setData({ settlementHint: '客户已接受这一版' })
        this.load()
      })
      .catch((err) => this.setData({ settlementHint: this.describe(err) }))
  },

  onOpenPayment(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    this.setData({ paymentOpenKey: this.data.paymentOpenKey === id ? '' : id, settlementHint: '' })
  },

  onPaymentInput(e) {
    const key = (e.currentTarget.dataset && e.currentTarget.dataset.key) || ''
    if (!key) return
    const form = Object.assign({}, this.data.paymentForm)
    form[key] = e.detail.value
    this.setData({ paymentForm: form })
  },

  onPickPaymentDirection(e) {
    const code = (e.currentTarget.dataset && e.currentTarget.dataset.code) || ''
    if (!code) return
    this.setData({ paymentForm: Object.assign({}, this.data.paymentForm, { direction: code }) })
  },

  onSubmitPayment(e) {
    const settlementId = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    const f = this.data.paymentForm
    if (!f.amount || !f.ref) {
      this.setData({ settlementHint: '金额与凭据引用都要填 —— 没有凭据的收付不可核对' })
      return
    }
    recordSettlementPayment(
      settlementId,
      { direction: f.direction, amount: f.amount, ref: f.ref },
      newIdempotencyKey('payment')
    )
      .then((res) => {
        this.setData({
          paymentOpenKey: '',
          paymentForm: { direction: 'receivable', amount: '', ref: '' },
          settlementHint: '已记一条收付依据（' + String((res && res.mode) || '') + '：合成样本，不代表真实到账）'
        })
        this.load()
      })
      .catch((err) => this.setData({ settlementHint: this.describe(err) }))
  },

  onPreviewCustomerView(e) {
    const id = (e.currentTarget.dataset && e.currentTarget.dataset.id) || ''
    if (!id) return
    fetchCustomerSettlement(id)
      .then((res) => {
        const view = res || {}
        this.setData({
          customerView: {
            versionText: 'v' + String(view.version_no == null ? '' : view.version_no),
            totalText: view.total === null || view.total === undefined ? '未知' : String(view.total),
            currency: view.currency || '',
            isApplicable: view.is_applicable === true,
            decisionText: view.customer_decision === 'accepted'
              ? '客户已接受'
              : view.customer_decision === 'rejected'
                ? '客户不接受'
                : '客户未确认',
            lines: this.customerLineRows(view)
          },
          customerViewHint: '这是客户看到的内容：只有对客（应收）费用，没有内部成本'
        })
      })
      .catch((err) => this.setData({ customerView: null, customerViewHint: this.describe(err) }))
  },

  /** 领域错误 → 给用户读的一句话。**用服务端给的 detail**，不自己编。 */
  describe(err) {
    const status = (err && err.httpStatus) || 0
    const detail = (err && err.detail) || ''
    if (status === 403) return detail || '当前身份没有这个权限'
    if (status === 409) return detail || '当前状态不允许这个操作（刷新后重看）'
    if (status === 400) return detail || '入参不合法'
    if (status === 404) return detail || '对象不存在或无权查看'
    return detail || '操作失败，请稍后重试'
  },

  onBack() {
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
