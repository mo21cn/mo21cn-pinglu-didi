// app.js —— 全局逻辑
App({
  globalData: {
    // 当前登录角色：shipper(货主) | owner(船东) | port(港口方) | null(未登录)
    role: null,
    userInfo: null,
    // 运行环境：development | test | production（与后端 APP_ENV 对齐）
    env: 'development'
  },

  onLaunch() {
    // TODO(MVP F1): 微信 code2session 登录，换取 openid/unionid，确定角色
    // 依据后端返回的角色信息设置 this.globalData.role，并缓存登录态
  },

  // 按角色路由到对应工作台（三角色合一的核心）
  routeByRole(role) {
    const map = {
      shipper: '/pages/shipper/shipper',
      owner: '/pages/owner/owner',
      port: '/pages/port/port'
    }
    if (!map[role]) return '/pages/index/index'
    return map[role]
  }
})
