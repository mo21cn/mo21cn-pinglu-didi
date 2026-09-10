// 首位屏（01）：身份选择浮窗
// 不设主页——进入即弹出身份选择，点击卡片直接进入对应工作台
const auth = require('../../utils/auth')

const ROLE_META = {
  shipper: { label: '货主', page: '/pages/shipper/shipper' },
  owner:   { label: '船东', page: '/pages/owner/owner' },
  port:    { label: '港口方', page: '/pages/port/port' }
}

Page({
  data: {
    pendingRole: '',
    logging: false
  },

  onLoad() {
    // 已登录且已有角色 → 直接进上次的工作台（无需再次选择）
    if (auth.isLoggedIn()) {
      const user = auth.getUser()
      const role = user && user.current_role
      if (role && ROLE_META[role]) {
        wx.switchTab({ url: ROLE_META[role].page })
      }
    }
  },

  onShow() {
    this.setData({ pendingRole: '', logging: false })
  },

  onPickRole(e) {
    const role = e.currentTarget.dataset.role
    if (!ROLE_META[role] || this.data.logging) return
    this.setData({ pendingRole: role, logging: true })

    const enter = () => {
      wx.switchTab({
        url: ROLE_META[role].page,
        fail: () => this.setData({ logging: false, pendingRole: '' })
      })
    }

    if (!auth.isLoggedIn()) {
      // 首次进入：登录 → 绑定该角色 → 进入工作台
      auth.login()
        .then(() => auth.bindRole(role))
        .then(enter)
        .catch(() => {
          this.setData({ logging: false, pendingRole: '' })
          wx.showToast({ title: '登录失败，请重试', icon: 'none' })
        })
      return
    }

    const user = auth.getUser() || {}
    if ((user.roles || []).includes(role)) {
      // 已有该角色：切过去即可
      auth.switchRole(role).then(enter).catch(() => {
        this.setData({ logging: false, pendingRole: '' })
        wx.showToast({ title: '切换角色失败，请重试', icon: 'none' })
      })
    } else {
      auth.bindRole(role)
        .then(() => auth.switchRole(role))
        .then(enter)
        .catch(() => {
          this.setData({ logging: false, pendingRole: '' })
          wx.showToast({ title: '绑定身份失败，请重试', icon: 'none' })
        })
    }
  }
})
