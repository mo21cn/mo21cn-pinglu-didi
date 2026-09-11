// 首位屏（01）：身份选择浮窗
// 不设主页——进入即弹出身份选择，点击卡片直接进入对应工作台
const auth = require('../../utils/auth')

// 可选身份：仅货主 / 船东（港口方身份已下线，港航服务改由「港口」页承载）
const ROLE_META = {
  shipper: { label: '货主', page: '/pages/shipper/shipper' },
  owner:   { label: '船东', page: '/pages/owner/owner' }
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

    // 统一复位：任何失败都要撤掉 loading，否则遮罩会一直盖住页面、后续点击全部失效
    const reset = (tip) => {
      this.setData({ logging: false, pendingRole: '' })
      if (tip) wx.showToast({ title: tip, icon: 'none' })
    }

    const enter = () => {
      wx.switchTab({
        url: ROLE_META[role].page,
        fail: () => reset('进入工作台失败，请重试')
      })
    }

    if (!auth.isLoggedIn()) {
      // 首次进入：登录 → 绑定该角色 → 切换为当前角色 → 进入工作台
      //
      // ⚠️ `bindRole` 只把角色写进 roles 列表，服务端 current_role 仍是登录时的
      //    默认角色（shipper），token payload 里的 role 也不会变。缺少 `switchRole`
      //    会带着「货主身份的 token」进船东工作台，后端逐端点角色校验 → 全线 403。
      auth.login()
        .then(() => auth.bindRole(role))
        .then(() => auth.switchRole(role))
        .then(enter)
        .catch(() => reset('登录失败，请重试'))
      return
    }

    const user = auth.getUser() || {}
    if ((user.roles || []).indexOf(role) === -1) {
      // 已登录但尚未绑定该身份：先绑定再切换
      auth.bindRole(role)
        .then(() => auth.switchRole(role))
        .then(enter)
        .catch(() => reset('绑定身份失败，请重试'))
    } else if (user.current_role !== role) {
      auth.switchRole(role).then(enter).catch(() => reset('切换角色失败，请重试'))
    } else {
      enter()
    }
  }
})
