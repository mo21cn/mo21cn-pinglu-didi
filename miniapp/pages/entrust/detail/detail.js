// 委托发货 · 委托详情（ENT-012 / AC-04 第一个切片的只读半边）
//
// 本页只做**只读字段展示**：受理、任务、成果、Agent 提交等写动作属于后续批次。
// 不做写动作也就不需要提前把内部字段（受理价 / 成本口径 / 内部比价）拉出来 ——
// 客户数据白名单投影（`project_for_customer`）的完整分叉在 S3 处理。
//
// ENT-019：本页已接入运行期导航治理（`utils/routes.js`）——
//   · `onLoad` 的入口守卫与 `go()` 用**同一份** `paramSchema` 判定参数合法性；
//   · 返回 / 回首页经 `go()`（`workbench → index` 声明为 `reset`，执行 `reLaunch`）；
//   · 本页登记在 `MIGRATED_PAGES` 里，CI 会核对不得再出现裸 `wx.navigateTo /
//     redirectTo / reLaunch`。
const { VIEW, decorateDetail, fetchAssignment, viewState } = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）；`go()` 需要知道"从哪来"才能查到导航边 */
const SELF = 'pages/entrust/detail/detail'

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

    // ── 入口守卫（ENT-019）────────────────────────────────────────────
    // 冷启动 / 外部深链（消息、分享、扫码）时必须在本页自检：`go()` 管不到这条路径。
    //
    // 把「缺参」从"页面自己判断"改成"守卫的统一结论"，收益不是少写几行，而是**判据
    // 只有一份**：`assignment_id` 的必需性与字符集写在 `utils/routes.js` 的
    // `paramSchema` 里，`go()` 与 `onLoad` 用同一套规则。初版页面只查 `!id`，
    // 于是 `?assignment_id=../../x`、`?assignment_id=a b` 这类"看着有值"的会被放过去，
    // 一路带到接口。
    const rawId = query && query.assignment_id != null ? String(query.assignment_id) : ''
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
  },

  /** 明确告知后续能力未开放，而不是给一个点了没反应的按钮 */
  onNextBatch() {
    wx.showToast({ title: '受理 / 任务 / 成果 / Agent 在下一批开放', icon: 'none', duration: 2000 })
  }
})
