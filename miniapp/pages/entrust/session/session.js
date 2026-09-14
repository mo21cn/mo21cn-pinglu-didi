// 委托发货 · 专业会话屏（UI-03 的成果卡一半 / DR-0015 / ENT-033）
//
// AC-05（PRD 第 475 行）要求 "Chat card and workbench show the same
// object/version"。工作台与成果页早已通真载荷，**聊天侧一件都没有** —— 本页补的
// 正是这一半，而且只补这一半：
//
// * **同源同版本**：字段来自 `utils/entrust.js` 的 `decorateArtifact`，
//   与成果页读**同一份**投影。页面里再写一套字段名映射，就是第二份真相 ——
//   改了后端列名，一边跟着变一边不变，AC-05 会以一种很难复现的方式失败。
// * **不做** Agent 作业与人工接管：二者依赖尚不存在的执行器（DR-0015 §4.2）。
//
// 运行期导航治理与其它委托页同口径：`onLoad` 走 `guardEntry()`，跳转走 `go()`，
// 本页登记在 `MIGRATED_PAGES`（CI 会核对不得出现裸 `wx.navigateTo` 等）。
const {
  VIEW,
  decorateArtifact,
  fetchArtifactCandidates,
  fetchArtifactTypes,
  viewState
} = require('../../../utils/entrust')

const R = require('../../../utils/routes')

const SELF = 'pages/entrust/session/session'

/** 成果卡上预览几个字段：卡是"引用"，不是表单（表单在成果页） */
const PREVIEW_FIELDS = 4

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    assignmentId: '',
    cards: []
  },

  onLoad(query) {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })

    const rawId = query && query.assignment_id != null ? String(query.assignment_id) : ''
    const gate = R.guardEntry(
      SELF + (rawId ? '?assignment_id=' + encodeURIComponent(rawId) : ''),
      { coldStart: R.currentDepth() <= 1 }
    )

    if (!gate.ok) {
      // 缺参 / 格式非法是**编程或传播错误**：按错误态显示，而不是留一片空白
      // （与成果页同口径：空白会被读成"这单没有成果"）。
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
   * 取数：类型注册表 + 本单成果清单。
   *
   * 注册表失败**不**拖垮整页（没有它只是类型名显示原始代码），
   * 清单失败**必须**进错误态 —— 那才是"这个会话读不到内容"的真正原因。
   */
  load() {
    const self = this
    return Promise.all([
      fetchArtifactCandidates(this.data.assignmentId),
      fetchArtifactTypes().catch(function () {
        return []
      })
    ])
      .then(function (res) {
        const list = (res[0] && res[0].items) || []
        const specs = res[1] || []
        const byCode = {}
        specs.forEach(function (s) {
          byCode[s.code] = s
        })
        const cards = list.map(function (item) {
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
        self.applyState(viewState({ status: 200, total: cards.length }), cards)
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

  applyState(state, cards) {
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      cards: cards || []
    })
  },

  onOpenArtifact(e) {
    const ds = (e && e.currentTarget && e.currentTarget.dataset) || {}
    const artifactId = ds.artifact_id != null ? String(ds.artifact_id) : ''
    if (!artifactId) return
    // PRD 第 150 行 "link to exact workbench artifact"：落到**精确**那一份，
    // 不是"最新的一份" —— 后者会漂移（与 routes.js 里 artifact_id 必填同理）。
    R.go(
      '/pages/entrust/artifact/artifact?artifact_id=' + encodeURIComponent(artifactId),
      { from: SELF }
    )
  },

  onBack() {
    R.go('/pages/entrust/workbench/workbench', { from: SELF })
  }
})
