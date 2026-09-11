// 通用「功能预览」占位页（纯前端，不涉及后端）
// 入口：发布货物 → 委托发货。后续其它未开放功能可直接 navigateTo 复用本页。
Page({
  data: {
    title: '功能预览，即将开放'
  },

  onLoad(options) {
    // 可选：?title=xxx 覆写占位文案（不传即用设计稿文案）
    const t = options && options.title
    if (t) this.setData({ title: t })
  }
})
