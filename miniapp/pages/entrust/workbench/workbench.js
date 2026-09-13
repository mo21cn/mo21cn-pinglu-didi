// 委托发货 · 经理工作台（S2 前端切片 / ENT-012 / AC-04 · AC-21）
//
// 数据源：GET /api/v1/entrust/assignments?view=org
// 服务端按「组织成员 + 该货主授权 + entrust:view 权限」判定可见性；
// 前端不做任何"我是不是经理"的本地判断（那只能靠可篡改的本地角色字段）。
const {
  STATUS_META,
  STATUS_ORDER,
  VIEW,
  decorateList,
  fetchQueue,
  pageHint,
  viewState
} = require('../../utils/entrust')

const PAGE_SIZE = 20

// 筛选条：全部 + 后端取值域（不从页面里硬编码状态字面量，改后端只需改 utils）
const FILTERS = [{ key: '', label: '全部' }].concat(
  STATUS_ORDER.map(function (key) {
    return { key: key, label: STATUS_META[key].label }
  })
)

Page({
  data: {
    statusBarHeight: 20,
    view: VIEW.LOADING,
    viewTitle: '加载中',
    viewHint: '',
    filters: FILTERS,
    activeStatus: '',
    items: [],
    total: 0,
    pageHint: '',
    orgId: ''
  },

  onLoad(query) {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    const patch = { statusBarHeight: info.statusBarHeight || 20 }
    if (query && query.org_id) patch.orgId = String(query.org_id)
    this.setData(patch)
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
   * 取数并落到五态上。
   *
   * 这里**不 catch 后 setData 成空列表** —— 那会把"失败"渲染成"没有委托"，
   * 是这类页面最常见也最难被发现的问题。一切进入 viewState 统一裁决。
   */
  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchQueue({
      orgId: this.data.orgId,
      status: this.data.activeStatus,
      page: 1,
      size: PAGE_SIZE
    })
      .then(function (res) {
        const total = (res && res.total) || 0
        const items = decorateList(res && res.items)
        self.applyState(viewState({ status: 200, total: total }), items, total)
      })
      .catch(function (err) {
        const status = (err && err.httpStatus) || 0
        self.applyState(
          viewState({ status: status, netError: !status, detail: err && err.detail }),
          [],
          0
        )
      })
  },

  applyState(state, items, total) {
    this.setData({
      view: state.state,
      viewTitle: state.title,
      viewHint: state.hint,
      items: items,
      total: total,
      pageHint: pageHint(total, 1, PAGE_SIZE)
    })
  },

  onFilter(e) {
    const key = e.currentTarget.dataset.key || ''
    if (key === this.data.activeStatus) return
    this.setData({ activeStatus: key })
    this.load()
  },

  onRetry() {
    this.load()
  },

  onBack() {
    wx.navigateBack({ delta: 1 })
  },

  /** 登录过期：回首页重走身份链路（本页已无可用的鉴权信息） */
  onRelogin() {
    wx.reLaunch({ url: '/pages/index/index' })
  },

  onOpen(e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: '/pages/entrust/detail/detail?assignment_id=' + id })
  }
})
