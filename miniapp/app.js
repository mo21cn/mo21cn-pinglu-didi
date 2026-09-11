// app.js —— 全局逻辑
// （第三方审计 P3-1：原文件还定义了 routeByRole / workspacePages / publishPages，
//  但页面各自维护 ROLE_META、tabBar 由 custom-tab-bar 的 PAGE_OF/PUBLISH_OF 解析，
//  三者全站无引用 → 删除死代码；路由映射的单一事实来源在各页面与 tabbar.js。）
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
  }
})
