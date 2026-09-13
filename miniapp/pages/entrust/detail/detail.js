// 委托发货 · 委托详情（ENT-012 / AC-04 第一个切片的只读半边）
//
// 本页只做**只读字段展示**：受理、任务、成果、Agent 提交等写动作属于后续批次。
// 不做写动作也就不需要提前把内部字段（受理价 / 成本口径 / 内部比价）拉出来 ——
// 客户数据白名单投影（`project_for_customer`）的完整分叉在 S3 处理。
const { VIEW, decorateDetail, fetchAssignment, viewState } = require('../../../utils/entrust')

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    assignmentId: '',
    detail: null,
    fields: []
  },

  onLoad(query) {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })

    const id = query && query.assignment_id
    if (!id) {
      // 缺参数是**编程错误**（正常入口都会带上），按错误态显示而不是静默空白。
      // 这里必须直接给 error 态，**不能**借 viewState 的兜底推断：`status=0 且
      // netError=false` 的输入会被它判为「空列表」，页面显示「还没有委托」，
      // 于是编程错误被伪装成一个正常的业务状态（这正是 verify_entrust_ui.js
      // 要守住的"错误优先于空"）。
      this.applyState(
        { state: VIEW.ERROR, title: '缺少委托编号', hint: '请从委托列表进入详情' },
        null
      )
      return
    }
    this.setData({ assignmentId: String(id) })
    this.load()
  },

  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchAssignment(this.data.assignmentId)
      .then(function (res) {
        const detail = decorateDetail(res)
        self.applyState(viewState({ status: 200, total: 1 }), detail)
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
      detail: detail,
      fields: detail ? this.buildFields(detail) : []
    })
  },

  /** 详情字段行（模板只做 wx:for，不做表达式） */
  buildFields(detail) {
    return [
      { label: '委托编号', value: '#' + detail.assignmentId },
      { label: '货类 / 货物', value: detail.cargoSummary },
      { label: '货量', value: detail.quantityText },
      { label: '归属', value: detail.orgText },
      { label: '提交时间', value: detail.createdAt || '—' },
      { label: '数据版本', value: 'r' + (detail.revision || 1) }
    ]
  },

  onRetry() {
    this.load()
  },

  onBack() {
    wx.navigateBack({ delta: 1 })
  },

  onRelogin() {
    wx.reLaunch({ url: '/pages/index/index' })
  },

  /** 明确告知后续能力未开放，而不是给一个点了没反应的按钮 */
  onNextBatch() {
    wx.showToast({ title: '受理 / 任务 / 成果 / Agent 在下一批开放', icon: 'none', duration: 2000 })
  }
})
