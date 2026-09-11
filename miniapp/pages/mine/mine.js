// 08 我的（底栏二级页）
// 用户卡 / 会员卡 / 资产行 / 常用功能网格（多数原型占位）+ 账号（角色切换 / 退出）
const { getUser, clearUser, enterRole } = require('../../utils/auth')
const { syncTabBar } = require('../../utils/tabbar')

// 账号区可选身份：仅货主 / 船东（港口方身份已下线）
const ROLE_LIST = [
  { key: 'shipper', icon: '🚢', label: '货主' },
  { key: 'owner',   icon: '⚓', label: '船东' }
]

// 常用功能（hasRoute 的为真实可进入）
const FUNCTIONS = [
  { key: 'emptyShip', icon: '🚢', label: '我的空船', badge: '新' },
  { key: 'points',    icon: '🎁', label: '积分' },
  { key: 'recent',    icon: '👥', label: '最近联系' },
  { key: 'verify',    icon: '✅', label: '认证中心' },
  { key: 'qrcode',    icon: '🔳', label: '我的二维码' },
  { key: 'settings',  icon: '⚙️', label: '设置' },
  { key: 'cs',        icon: '🎧', label: '人工客服' },
  { key: 'prize',     icon: '🏆', label: '我的奖品' },
  { key: 'feedback',  icon: '💬', label: '意见反馈' },
  { key: 'home',      icon: '🏠', label: '我的主页' },
  { key: 'follow',    icon: '⭐', label: '我的关注' },
  // 已开发能力出口：退出登录 → 回首页重新选身份
  { key: 'logout',    icon: '⏏️', label: '退出', warn: true }
]

Page({
  data: {
    statusBarHeight: 20,
    userName: '用户',
    userInitials: '客',
    phoneText: '未绑定手机号',
    roleLabel: '货主',
    currentRole: 'shipper',
    roleList: ROLE_LIST,
    functions: FUNCTIONS
  },

  onLoad() {
    let info = {}
    try {
      info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = {}
    }
    this.setData({ statusBarHeight: info.statusBarHeight || 20 })
  },

  onShow() {
    syncTabBar(this)
    const user = getUser()
    if (user) {
      const name = '用户' + (user.user_id || '')
      this.setData({
        userName: name,
        userInitials: this.getInitials(name),
        phoneText: user.phone || '未绑定手机号',
        currentRole: user.current_role || 'shipper',
        roleLabel: this.roleLabelText(user.current_role)
      })
    }
  },

  getInitials(name) {
    if (!name) return '客'
    return String(name).trim().charAt(0).toUpperCase()
  },

  roleLabelText(role) {
    return { shipper: '货主', owner: '船东' }[role] || '用户'
  },

  onPreviewTip() {
    wx.showToast({ title: '以上为原型占位，后续分批开放', icon: 'none', duration: 2000 })
  },

  onBell() {
    wx.showToast({ title: '消息中心开发中', icon: 'none' })
  },

  onEditProfile() {
    wx.showToast({ title: '资料编辑开发中', icon: 'none' })
  },

  onUpgrade() {
    wx.showToast({ title: '会员体系 · 即将开放', icon: 'none' })
  },

  onAssetTap(e) {
    const map = { balance: '账户余额', fuel: '优惠油电', bank: '银行卡' }
    wx.showToast({ title: `${map[e.currentTarget.dataset.key]} · 后续开放`, icon: 'none' })
  },

  onFunction(e) {
    const key = e.currentTarget.dataset.key
    if (key === 'emptyShip') {
      wx.navigateTo({ url: '/pages/publish/ship/ship' })
      return
    }
    if (key === 'cs') {
      wx.navigateTo({ url: '/pages/assistant/assistant' })
      return
    }
    if (key === 'logout') {
      // 退出登录 → 回首页重新选择身份
      this.onLogout()
      return
    }
    const item = FUNCTIONS.find((f) => f.key === key)
    wx.showToast({ title: `${item ? item.label : '该功能'} · 原型占位，后续开放`, icon: 'none', duration: 1800 })
  },

  onSwitchRole(e) {
    const role = e.currentTarget.dataset.role
    if (role === this.data.currentRole) return
    if (!ROLE_LIST.some((r) => r.key === role)) return
    // 统一走 auth.enterRole：开发期会同时把身份切到该角色的演示账号。
    // 否则切换过去看到的是空账号（页面无报错、列表全空，极易误判成功能故障）。
    wx.showLoading({ title: '切换中...', mask: true })
    enterRole(role)
      .then(() => {
        wx.hideLoading()
        wx.showToast({ title: '已切换为' + this.roleLabelText(role), icon: 'success' })
        const map = {
          shipper: '/pages/shipper/shipper',
          owner: '/pages/owner/owner'
        }
        wx.switchTab({ url: map[role] || '/pages/index/index' })
      })
      .catch(() => {
        wx.hideLoading()
        wx.showToast({ title: '切换失败', icon: 'none' })
      })
  },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '将退出当前身份并回到首页重新选择，是否继续？',
      confirmText: '退出',
      success: (res) => {
        if (!res.confirm) return
        clearUser()
        wx.reLaunch({ url: '/pages/index/index' })
      }
    })
  }
})
