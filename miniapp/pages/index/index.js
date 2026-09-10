// 首屏：角色选择浮窗（v1）
const auth = require('../../utils/auth')

const ROLE_META = {
  shipper: { label: '货主', page: '/pages/shipper/shipper', desc: '发货找船' },
  owner:   { label: '船东', page: '/pages/owner/owner',     desc: '接单找货' },
  port:    { label: '港口方', page: '/pages/port/port',     desc: '港作协同' }
}

Page({
  data: {
    pendingRole: '',
    logging: false
  },

  onLoad() {
    if (auth.isLoggedIn()) {
      const user = auth.getUser()
      const role = user.current_role
      if (ROLE_META[role]) {
        wx.switchTab({ url: ROLE_META[role].page })
        return
      }
    }
  },

  onShow() {
    this.setData({ pendingRole: '' })
  },

  onPickRole(e) {
    const role = e.currentTarget.dataset.role
    if (!ROLE_META[role] || this.data.logging) return
    this.setData({ pendingRole: role, logging: true })

    const doEnter = () => {
      auth.switchRole(role)
        .then(() => wx.switchTab({ url: ROLE_META[role].page }))
        .catch(() => {
          this.setData({ logging: false, pendingRole: '' })
          wx.showToast({ title: '切换角色失败，请重试', icon: 'none' })
        })
    }

    if (!auth.isLoggedIn()) {
      auth.login()
        .then(() => auth.bindRole(role))
        .then(doEnter)
        .catch(() => {
          this.setData({ logging: false, pendingRole: '' })
          wx.showToast({ title: '登录失败，请重试', icon: 'none' })
        })
    } else {
      const user = auth.getUser()
      if ((user.roles || []).includes(role)) {
        doEnter()
      } else {
        auth.bindRole(role)
          .then(doEnter)
          .catch(() => {
            this.setData({ logging: false, pendingRole: '' })
            wx.showToast({ title: '绑定身份失败，请重试', icon: 'none' })
          })
      }
    }
  },

  onOneClick() {
    if (this.data.logging) return
    this.setData({ logging: true })
    auth.login()
      .then(() => {
        this.setData({ logging: false })
        wx.showToast({ title: '登录成功，请选择身份', icon: 'success' })
      })
      .catch(() => {
        this.setData({ logging: false })
        wx.showToast({ title: '登录失败，请重试', icon: 'none' })
      })
  },

  onWechatLogin() {
    this.onOneClick()
  }
})
