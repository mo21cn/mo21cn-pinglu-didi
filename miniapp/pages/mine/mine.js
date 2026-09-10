// 我的工作台（v1 · 占位骨架）
const { request } = require('../../utils/request')
const { getUser, clearUser, switchRole } = require('../../utils/auth')

const ROLE_LIST = [
  { key: 'shipper', icon: '🚢', label: '货主' },
  { key: 'owner',   icon: '⚓', label: '船东' },
  { key: 'port',    icon: '🏗', label: '港口方' }
]

const ENTRY_LIST = [
  { key: 'verify',  icon: '✅', label: '实名认证', dev: true },
  { key: 'wallet',  icon: '💰', label: '我的钱包', dev: true },
  { key: 'order',   icon: '📋', label: '我的订单', dev: false },
  { key: 'ship',    icon: '⚓', label: '我的船队', dev: false },
  { key: 'cargo',   icon: '📦', label: '我的货源', dev: false },
  { key: 'invoice', icon: '🧾', label: '发票管理', dev: true },
  { key: 'msg',     icon: '🔔', label: '消息中心', dev: true },
  { key: 'cs',      icon: '💬', label: '联系客服', dev: false }
]

Page({
  data: {
    userName: '船友',
    userInitials: '客',
    roleLabel: '货主',
    currentRole: 'shipper',
    roleList: ROLE_LIST,
    entryList: ENTRY_LIST,
    stats: { cargo: 0, ship: 0, order: 0, done: 0 }
  },

  onShow() {
    const user = getUser()
    if (user) {
      const initials = this.getInitials(user.nickname || '船友')
      this.setData({
        userName: user.nickname || '船友',
        userInitials: initials,
        currentRole: user.current_role || 'shipper',
        roleLabel: this.roleLabelText(user.current_role)
      })
    }
    this.fetchStats()
  },

  onPullDownRefresh() {
    this.fetchStats()
    wx.stopPullDownRefresh()
  },

  fetchStats() {
    const fetchOne = (url) => request({ url, data: { size: 1 } }).then((res) => res.total || 0).catch(() => 0)
    Promise.all([
      fetchOne('/api/v1/cargo/shipments'),
      fetchOne('/api/v1/ship/registry'),
      fetchOne('/api/v1/order/orders'),
      fetchOne('/api/v1/order/orders?status=completed')
    ]).then(([cargo, ship, order, done]) => {
      this.setData({ stats: { cargo, ship, order, done } })
    })
  },

  getInitials(name) {
    if (!name) return '客'
    return String(name).trim().charAt(0).toUpperCase()
  },

  roleLabelText(role) {
    return { shipper: '货主', owner: '船东', port: '港口方' }[role] || '用户'
  },

  onEditProfile() {
    wx.showToast({ title: '编辑资料功能开发中', icon: 'none' })
  },

  onSwitchRole(e) {
    const role = e.currentTarget.dataset.role
    if (role === this.data.currentRole) return
    switchRole(role)
      .then(() => {
        wx.showToast({ title: '已切换为' + this.roleLabelText(role), icon: 'success' })
        const map = {
          shipper: '/pages/shipper/shipper',
          owner: '/pages/owner/owner',
          port: '/pages/port/port'
        }
        wx.switchTab({ url: map[role] || '/pages/index/index' })
      })
      .catch(() => wx.showToast({ title: '切换失败', icon: 'none' }))
  },

  onEntry(e) {
    const key = e.currentTarget.dataset.key
    const handlers = {
      verify: () => this.todo('实名认证'),
      wallet: () => this.todo('我的钱包'),
      invoice: () => this.todo('发票管理'),
      msg: () => this.todo('消息中心'),
      order: () => wx.switchTab({ url: '/pages/trade/orders/orders' }),
      ship: () => wx.switchTab({ url: '/pages/owner/owner' }),
      cargo: () => wx.switchTab({ url: '/pages/shipper/shipper' }),
      cs: () => wx.navigateTo({ url: '/pages/assistant/assistant' })
    }
    const fn = handlers[key]
    if (fn) fn()
    else this.todo('该功能')
  },

  todo(label) {
    wx.showToast({ title: `${label} 开发中`, icon: 'none', duration: 1800 })
  },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '退出后需重新登录，是否继续？',
      success: (res) => {
        if (!res.confirm) return
        clearUser()
        wx.reLaunch({ url: '/pages/index/index' })
      }
    })
  }
})
