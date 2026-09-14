// 委托发货 · 案件详情（UI-08 首片：只读 / ENT-030 切片四之四）
//
// 本页是**异常 / 变更案件的只读详情**：PRD §4.1「UI-08」（第 155 行）要求的六要素
// —— 原因 / 受影响记录 / 拟解决方案 / 决定与审批 / 执行证据 / 结案 —— 外加一条
// append-only 的处理记录。六要素的顺序与"为空时怎么说"来自 `utils/entrust.js` 的
// `CASE_ELEMENTS`，本页**不自己判断"该不该显示某一块"**：少一块就是详情缺内容，
// 而页面看起来只会像"这一块没数据"。
//
// ⛔ 本片**只读**：登记受影响项 / 决定 / 关闭 / 重开都不在这里。理由不是省事 ——
//    它们是带幂等键与乐观锁的写端点，界面必须处理 409（版本过期）与"重开后再次关闭"
//    的两轮历史；塞进首片会让"能不能看清这宗案件"这件正事被表单实现细节淹没。
//    `capabilities` 因此也只用来给一句**对"没权限"与"当前没有可执行动作"都成立**
//    的提示，不从它推断任何业务结论（理由见 `decorateCase`）。
//
// ENT-019 起新增委托页面必须从首个切片接入运行期导航治理（`utils/routes.js`）：
//   · `onLoad` 的入口守卫与 `go()` 用**同一份** `paramSchema` 判定参数合法性；
//   · 返回 / 回首页经 `go()`（`case → index` 声明为 `reset`，执行 `reLaunch`）；
//   · 本页登记在 `MIGRATED_PAGES` 里，CI 会核对不得再出现裸 `wx.navigateTo /
//     redirectTo / reLaunch`。
//
// ⚠️ 参数名是 `case_id`，接口路径却是 `/exceptions/{exception_id}`（DR-0014 §7）：
//    同一个值、两个名字。**不要为了"统一"改掉任一侧** —— 路径那侧已经发布。
//
// ⚠️ 404 是**刻意含混**的：服务端对「开关关闭」「案件不存在」「非参与方」都返回 404
//    （不泄漏存在性）。共享的 `viewState` 因此给的是「功能未开放」而不是「案件不存在」
//    —— 后者在开关关闭时就是一句错误的业务结论。这条映射三个委托页面共用，要改一起改。
const { VIEW, decorateCase, fetchCase, viewState } = require('../../../utils/entrust')

const R = require('../../../utils/routes')

/** 本页路径（注册表口径）；`go()` 需要知道"从哪来"才能查到导航边 */
const SELF = 'pages/entrust/case/case'

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    caseId: '',
    detail: null,
    fields: [],
    blocks: [],
    events: [],
    actionHint: ''
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
    // 冷启动 / 外部深链（消息、分享、扫码）必须在本页自检：`go()` 管不到这条路径。
    // 判据只有一份 —— `case_id` 的必需性与字符集写在 `utils/routes.js` 的
    // `paramSchema` 里，`go()` 与 `onLoad` 用同一套规则。页面自己只查 `!id` 的话，
    // `?case_id=../../x` 这类"看着有值"的会被放过去，一路带到接口。
    const rawId = query && query.case_id != null ? String(query.case_id) : ''
    const gate = R.guardEntry(SELF + (rawId ? '?case_id=' + encodeURIComponent(rawId) : ''), {
      coldStart: R.currentDepth() <= 1
    })

    if (!gate.ok) {
      // 缺参/格式非法都是**编程或传播错误**（正常入口都会带上），按错误态显示而不是
      // 静默空白。这里必须直接给 error 态，**不能**借 viewState 的兜底推断：
      // `status=0 且 netError=false` 会被它判成"空"，于是编程错误被伪装成一个正常的
      // 业务状态（这正是 verify_entrust_ui.js 要守住的"错误优先于空"）。
      const errs = gate.errors || []
      const miss = errs.filter(function (e) {
        return String(e.reason).indexOf('缺少') === 0
      })
      this.applyState(
        {
          state: VIEW.ERROR,
          title: miss.length ? '缺少案件编号' : '案件编号不合法',
          hint: miss.length ? '请从委托工作台或会话进入' : gate.reason
        },
        null
      )
      return
    }

    this.setData({ caseId: rawId })
    this.load()
  },

  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchCase(this.data.caseId)
      .then(function (res) {
        self.applyState(viewState({ status: 200, total: 1 }), decorateCase(res))
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
      fields: detail ? detail.fields : [],
      blocks: detail ? detail.blocks : [],
      events: detail ? detail.events : [],
      actionHint: detail ? detail.actionHint : ''
    })
  },

  onRetry() {
    this.load()
  },

  onBack() {
    // 冷启动 / 消息深链进入时栈里只有本页，`navigateBack` 会**静默失败**
    // （「点了返回没反应」）。回入口重走身份链路：case → index 声明为 `reset`。
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
