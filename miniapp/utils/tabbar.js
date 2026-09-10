// 自定义 tabBar 同步工具
// 用法：在任意 tabBar 页面的 onShow 中调用 syncTabBar(this)
// 作用：按当前路由 + 当前角色刷新自定义 tabBar 的高亮位与文案
function syncTabBar(page) {
  if (!page || typeof page.getTabBar !== 'function') return
  const bar = page.getTabBar()
  if (bar && typeof bar.refresh === 'function') bar.refresh()
}

module.exports = { syncTabBar }
