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
  decorateOrgs,
  fetchMyOrgs,
  fetchQueue,
  pageHint,
  pickOrg,
  viewState
} = require('../../utils/entrust')

const PAGE_SIZE = 20

/** 记住"上次选的组织"。存本地只是**默认值**，不是权限依据 —— 权限始终由服务端判定。 */
const STORAGE_ORG_KEY = 'entrust_active_org'

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
    orgs: [],
    activeOrgId: '',
    orgReason: ''
  },

  onLoad(query) {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    // 组织来源优先级：**深链参数 > 上次选择 > 由服务端清单推导**。
    // 深链带 org_id 是有意的（例如从消息直接进入某个组织的队列）；
    // 但两者都只是"默认值"，最终仍要由服务端清单确认它**仍然有效**（见 pickOrg）。
    const fromQuery = query && query.org_id ? String(query.org_id) : ''
    this.savedOrgId = fromQuery || wx.getStorageSync(STORAGE_ORG_KEY) || ''
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })
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
   * 取数：**先定位组织，再取队列**，最后统一落到五态上。
   *
   * 组织清单的失败**不能**被当成"没有组织"（那会把一次网络故障说成
   * "还没有加入服务经营主体"这种业务事实），所以它与队列取数走**同一个**
   * viewState 裁决 —— 这也正是"不 catch 成空"的同一原则。
   */
  load() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchMyOrgs()
      .then(function (res) {
        const orgs = decorateOrgs(res && res.items)
        const picked = pickOrg(orgs, self.savedOrgId)
        self.savedOrgId = picked.orgId
        self.setData({ orgs: orgs, activeOrgId: picked.orgId, orgReason: picked.reason })

        if (picked.reason === 'none') {
          // 没有任何组织身份。这不是"没有数据"，而是这个账号还没有办理能力 ——
          // 两者的下一步动作完全不同，所以用 denied 态 + 可行动的文案。
          self.setData({
            view: VIEW.DENIED,
            viewTitle: '还没有加入服务经营主体',
            viewHint: '请联系服务经营主体把你加入组织；加入后这里会出现委托队列',
            items: [],
            total: 0,
            pageHint: ''
          })
          return null
        }
        if (picked.reason === 'ambiguous') {
          // 多组织且未选择：**不猜**（见 utils/entrust.js 的 pickOrg 说明）。
          self.setData({
            view: VIEW.DENIED,
            viewTitle: '需要选择服务经营主体',
            viewHint: '你属于多个组织，请在上方选择要查看的组织',
            items: [],
            total: 0,
            pageHint: ''
          })
          return null
        }
        return self.loadQueue()
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

  /**
   * 取队列。**不 catch 后 setData 成空列表** —— 那会把"失败"渲染成"没有委托"，
   * 是这类页面最常见也最难被发现的问题。一切进入 viewState 统一裁决。
   */
  loadQueue() {
    const self = this
    this.setData({ view: VIEW.LOADING, viewTitle: '加载中', viewHint: '' })
    return fetchQueue({
      orgId: this.data.activeOrgId,
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
    // 组织已定位，只重取队列（不必再问一次组织清单）
    this.loadQueue()
  },

  /** 切换组织：记住选择（下次进来不用再选），并立刻重取队列。 */
  onPickOrg(e) {
    const orgId = (e.currentTarget.dataset.org || '').toString()
    if (!orgId || orgId === this.data.activeOrgId) return
    this.savedOrgId = orgId
    try {
      wx.setStorageSync(STORAGE_ORG_KEY, orgId)
    } catch (err) {
      // 存不进去只影响"下次的默认值"，不影响本次选择 —— 不该让交互失败
    }
    this.setData({ activeOrgId: orgId, orgReason: 'picked' })
    this.loadQueue()
  },

  onRetry() {
    // 重试从**组织定位**开始：失败可能就发生在那一步（它现在也是取数链路的一环）
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
