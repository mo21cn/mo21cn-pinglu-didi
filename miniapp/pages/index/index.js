// pages/index/index.js —— 首页：角色路由入口（三角色合一）
const app = getApp()

Page({
  data: {
    role: null,
    roleText: '',
    entries: [
      { key: 'shipper', text: '货主工作台', desc: '发布货源 · 比价下单 · 全程跟踪' },
      { key: 'owner', text: '船东工作台', desc: '挂载运力 · 接单 · 过闸 · 结算' },
      { key: 'port', text: '港口工作台', desc: '泊位维护 · 预约审批 · 装卸排期' }
    ]
  },

  onShow() {
    const role = app.globalData.role
    this.setData({ role })
  },

  // 进入指定角色工作台
  enterRole(e) {
    const key = e.currentTarget.dataset.key
    wx.navigateTo({ url: app.routeByRole(key) })
  }
})
