// 自定义 tabBar（UI V2）
// 原生 tabBar 无法按角色切换「找船/找货」与「发布货物/发布空船」，
// 故启用 custom tabBar：5 个位置由本组件按当前角色动态渲染。
//   位置1  找船 / 找货      （角色决定，switchTab）
//   位置2  订单              （switchTab）
//   位置3  发布货物/发布空船 （凸起圆钮，navigateTo 二级页）
//   位置4  港口              （switchTab）
//   位置5  我的              （switchTab）
const auth = require('../utils/auth')

// 每个位置对应的页面路由（按角色）
const PAGE_OF = {
  find: { shipper: '/pages/shipper/shipper', owner: '/pages/owner/owner' },
  order: { all: '/pages/trade/orders/orders' },
  port: { all: '/pages/port/port' },
  mine: { all: '/pages/mine/mine' }
}

// 中间凸起按钮：按角色进入「发布货物」或「发布空船」
const PUBLISH_OF = {
  shipper: '/pages/publish/cargo/cargo',
  owner: '/pages/publish/ship/ship'
}

const LABEL_OF = {
  shipper: { find: '找船',  publish: '发布货物' },
  owner:   { find: '找货',  publish: '发布空船' }
}

Component({
  data: {
    role: 'shipper',
    selected: 0,
    list: []
  },

  lifetimes: {
    attached() {
      this.refresh()
    }
  },

  methods: {
    /** 刷新：读取当前角色 + 当前路由，重算渲染数据与高亮位 */
    refresh() {
      const user = auth.getUser()
      const role = (user && user.current_role) || 'shipper'
      const pages = getCurrentPages()
      const current = pages.length ? pages[pages.length - 1].route : ''
      this.setData({
        role,
        list: this.buildList(role),
        selected: this.calcSelected('/' + current)
      })
    },

    buildList(role) {
      const labels = LABEL_OF[role] || LABEL_OF.shipper
      const isOwner = role === 'owner'
      return [
        { key: 'find',    icon: isOwner ? '📦' : '🚢', label: labels.find },
        { key: 'order',   icon: '📋', label: '订单' },
        { key: 'publish', icon: '＋', label: labels.publish, center: true },
        { key: 'port',    icon: '⚓', label: '港口' },
        { key: 'mine',    icon: '👤', label: '我的' }
      ]
    },

    calcSelected(path) {
      if (path.indexOf('/pages/shipper/') >= 0 || path.indexOf('/pages/owner/') >= 0) return 0
      if (path.indexOf('/pages/trade/orders/') >= 0) return 1
      if (path.indexOf('/pages/publish/') >= 0) return 2
      if (path.indexOf('/pages/port/') >= 0) return 3
      if (path.indexOf('/pages/mine/') >= 0) return 4
      return -1
    },

    onTap(e) {
      const key = e.currentTarget.dataset.key
      const role = this.data.role

      // 中间凸起：发布货物 / 发布空船（二级页，navigateTo）
      if (key === 'publish') {
        const url = PUBLISH_OF[role] || PUBLISH_OF.shipper
        wx.navigateTo({ url })
        return
      }

      const conf = PAGE_OF[key]
      if (!conf) return
      const url = conf[role] || conf.all
      if (!url) return
      wx.switchTab({ url })
    }
  }
})
