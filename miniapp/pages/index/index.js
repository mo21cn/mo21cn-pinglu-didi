// 首页：登录态检查 + 微信一键登录 + 三角色工作台入口
const auth = require('../../utils/auth')

const ROLE_META = [
  { key: 'shipper', label: '货主', desc: '发货找船 · 货源管理', page: '/pages/shipper/shipper' },
  { key: 'owner', label: '船东', desc: '接单找货 · 船舶管理', page: '/pages/owner/owner' },
  { key: 'port', label: '港口方', desc: '泊位调度 · 港作协同', page: '/pages/port/port' }
]

Page({
  data: {
    isLoggedIn: false,
    user: null,
    roles: [],          // 已绑定角色的展示元数据
    currentRole: '',
    logging: false
  },

  onLoad() {
    this.refreshView()
  },

  onShow() {
    this.refreshView()
  },

  refreshView() {
    const user = auth.getUser()
    if (auth.isLoggedIn()) {
      const roles = ROLE_META.filter((r) => (user.roles || []).includes(r.key))
      this.setData({
        isLoggedIn: true,
        user,
        roles,
        currentRole: user.current_role
      })
    } else {
      this.setData({ isLoggedIn: false, user: null, roles: [], currentRole: '' })
    }
  },

  /** 微信一键登录 */
  handleLogin() {
    if (this.data.logging) return
    this.setData({ logging: true })
    auth.login()
      .then(() => {
        wx.showToast({ title: '登录成功', icon: 'success' })
        this.refreshView()
      })
      .catch(() => {
        wx.showToast({ title: '登录失败，请重试', icon: 'none' })
      })
      .finally(() => this.setData({ logging: false }))
  },

  /** 切换当前角色 */
  handleSwitchRole(e) {
    const role = e.currentTarget.dataset.role
    if (role === this.data.currentRole) return
    auth.switchRole(role)
      .then(() => {
        wx.showToast({ title: '已切换角色', icon: 'success' })
        this.refreshView()
      })
      .catch(() => {})
  },

  /** 进入对应角色工作台 */
  handleEnterWorkspace(e) {
    const page = e.currentTarget.dataset.page
    const role = e.currentTarget.dataset.role
    // 进入工作台前确保当前角色一致
    if (role !== this.data.currentRole) {
      auth.switchRole(role)
        .then(() => wx.navigateTo({ url: page }))
        .catch(() => {})
    } else {
      wx.navigateTo({ url: page })
    }
  },

  /** 退出登录 */
  handleLogout() {
    auth.clearUser()
    this.refreshView()
  }
})
