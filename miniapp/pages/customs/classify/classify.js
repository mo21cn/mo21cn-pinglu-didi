// 智慧关务 · 归类辅助（D2 前端第 3 页）
//
// 这一页要解决的**不是**"能不能归类"，而是"模型给的东西算不算结论"。
// 所以界面上有三块必须物理分开、且互不替代：
//   ① 候选：模型给的建议（带来源与置信度），**不是结论**；
//   ② 降级：模型这次没给建议的原因（一句中文），⛔ 不是错误码；
//   ③ 人工定稿：不含任何模型字段的通路 —— 模型全线不可用时，这一块必须还能用。
//
// ⛔ 四条不做的事：
//   1. 不在候选为空时显示"失败/无数据"——候选空是 `degraded` 的事，两者分开呈现；
//   2. 不把 `candidates[0].hs_code` 当成结论（结论只来自 `hs_code` 字段＝人工定稿）；
//   3. 不在降级时把人工入口藏起来（那正是本次要消灭的"失败页里没有出口"）；
//   4. 不裸调 wx.navigateTo —— 本页登记在 `MIGRATED_PAGES`，跳转一律经 `R.go()`。
const R = require('../../../utils/routes')
const {
  fetchClassifications,
  fetchClassification,
  createClassification,
  submitManualClassification,
  classificationView
} = require('../../../utils/customs')

const SELF = 'pages/customs/classify/classify'

Page({
  data: {
    statusBarHeight: 20,
    view: 'loading', // loading | error | ready
    viewHint: '',
    declarationId: 0,
    // 发起表单
    form: { name: '', desc: '' },
    submitting: false,
    // 当前这条归类记录（展示模型在 `classificationView` 里已拍平）
    rec: null,
    // 人工定稿表单
    manual: { hs: '', label: '', rationale: '' },
    manualSubmitting: false,
    manualError: '',
    // 本账号可见的归类记录
    list: [],
    errTitle: '',
    errHint: ''
  },

  onLoad(options) {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    const declared = Number((options && (options.declaration_id || options.id)) || 0)
    this.setData({ statusBarHeight: info.statusBarHeight || 20, declarationId: declared })

    // 本页 `paramSchema` 为空（归类可以不挂在某条报关记录上）⇒ 守卫只能拦
    // "路由未登记 / 栈超预算"，但**仍然要跑**：`go()` 管不到"别人发了个链接给你"。
    const gate = R.guardEntry(SELF, { coldStart: R.currentDepth() <= 1 })
    this.entryOk = gate.ok
    if (!gate.ok) {
      this.setData({ view: 'error', errTitle: '入口不合法', errHint: gate.reason })
    }
  },

  onShow() {
    if (!this.entryOk) return
    this.load()
  },

  onPullDownRefresh() {
    const self = this
    this.load().then(function () {
      wx.stopPullDownRefresh()
      self.setData({})
    })
  },

  /**
   * 读列表。**列表失败不把整页打红**：这一页的核心是"发起 + 人工定稿"，
   * 列表只是历史。列表挂了就显式说列表挂了，表单照常可用。
   */
  load() {
    const self = this
    const opts = {}
    if (this.data.declarationId) opts.declaration_id = this.data.declarationId
    this.setData({ view: this.data.rec ? 'ready' : 'loading', viewHint: '' })
    return fetchClassifications(opts)
      .then(function (res) {
        const raw = res && res.items ? res.items : res
        const arr = Array.isArray(raw) ? raw : []
        self.setData({
          view: 'ready',
          list: arr.map(classificationView)
        })
      })
      .catch(function (err) {
        if (self.data.rec) {
          self.setData({ view: 'ready', viewHint: '历史记录暂时取不到（表单不受影响）' })
          return
        }
        self.setData({
          view: 'error',
          errTitle: '归类记录取不到',
          errHint: (err && err.message) || '请稍后重试'
        })
      })
  },

  onNameInput(e) {
    this.setData({ 'form.name': e.detail.value })
  },

  onDescInput(e) {
    this.setData({ 'form.desc': e.detail.value })
  },

  onHsInput(e) {
    this.setData({ 'manual.hs': e.detail.value, manualError: '' })
  },

  onManualLabelInput(e) {
    this.setData({ 'manual.label': e.detail.value })
  },

  onRationaleInput(e) {
    this.setData({ 'manual.rationale': e.detail.value })
  },

  /**
   * 发起归类建议。
   *
   * ⚠️ 这个请求**模型挂掉也会 201**（`status=manual_required`）—— 所以这里的
   * catch 只处理真正的传输/校验失败。把降级当失败处理，就会回到"失败页 + 没有出口"。
   */
  onPropose() {
    const self = this
    const name = String(this.data.form.name || '').trim()
    if (!name) {
      wx.showToast({ title: '请先填商品名称', icon: 'none' })
      return
    }
    if (this.data.submitting) return
    this.setData({ submitting: true, manualError: '' })
    const payload = { product_name: name }
    const desc = String(this.data.form.desc || '').trim()
    if (desc) payload.product_desc = desc
    if (this.data.declarationId) payload.declaration_id = this.data.declarationId
    createClassification(payload)
      .then(function (res) {
        self.setData({
          submitting: false,
          rec: classificationView(res),
          view: 'ready',
          viewHint: ''
        })
        self.load()
      })
      .catch(function (err) {
        self.setData({ submitting: false })
        wx.showToast({ title: (err && err.message) || '发起失败', icon: 'none' })
      })
  },

  /**
   * 人工定稿。
   *
   * 只带 `hs_code / label / rationale` 三个字段 —— 请求体里**没有**任何模型字段，
   * 所以这条通路在模型全挂时仍然成立（这是 Q-10 的兑现方式）。
   */
  onSubmitManual() {
    const self = this
    const rec = this.data.rec
    if (!rec || !rec.id) {
      wx.showToast({ title: '请先生成一条归类记录', icon: 'none' })
      return
    }
    const hs = String(this.data.manual.hs || '').replace(/\s+/g, '')
    if (!hs) {
      this.setData({ manualError: '请填写税则号列（HS code）' })
      return
    }
    if (!/^\d{8,10}$/.test(hs)) {
      this.setData({ manualError: '税则号列应为 8–10 位数字（不含空格或字母）' })
      return
    }
    if (this.data.manualSubmitting) return
    this.setData({ manualSubmitting: true, manualError: '' })
    const payload = { hs_code: hs }
    const label = String(this.data.manual.label || '').trim()
    const rationale = String(this.data.manual.rationale || '').trim()
    if (label) payload.label = label
    if (rationale) payload.rationale = rationale
    submitManualClassification(rec.id, payload)
      .then(function (res) {
        self.setData({
          manualSubmitting: false,
          rec: classificationView(res),
          manual: { hs: '', label: '', rationale: '' }
        })
        wx.showToast({ title: '结论已确认', icon: 'none' })
        self.load()
      })
      .catch(function (err) {
        self.setData({
          manualSubmitting: false,
          manualError: (err && err.message) || '提交失败，请重试'
        })
      })
  },

  onOpenItem(e) {
    const self = this
    const id = e.currentTarget.dataset.id
    if (!id) return
    fetchClassification(id)
      .then(function (res) {
        self.setData({ rec: classificationView(res), view: 'ready', viewHint: '' })
      })
      .catch(function (err) {
        wx.showToast({ title: (err && err.message) || '取不到这条记录', icon: 'none' })
      })
  },

  onRetry() {
    this.load()
  },

  onBack() {
    if (R.currentDepth() <= 1) {
      R.go('/pages/customs/home/home', { from: SELF })
      return
    }
    wx.navigateBack({ delta: 1 })
  }
})
