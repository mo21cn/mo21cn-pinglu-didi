// 08 我的（底栏二级页）
// 用户卡 / 会员卡 / 资产行 / 常用功能网格（多数原型占位）+ 账号（角色切换 / 退出）
const { getUser, clearUser, enterRole } = require('../../utils/auth')
const { syncTabBar } = require('../../utils/tabbar')
// 委托发货入口的可见性判定（AC-02 入口隔离）——判定逻辑在 utils/entrust.js，
// 那里是不接触 wx 的纯函数，可被 CI 的 scripts/verify_entrust_ui.js 直接驱动。
const entrust = require('../../utils/entrust')

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
    userName: '未登录',
    userInitials: '客',
    phoneText: '未绑定手机号',
    roleLabel: '登录后使用',
    currentRole: '',
    roleList: ROLE_LIST,
    functions: FUNCTIONS,
    // 委托发货入口：默认隐藏，仅当服务端静默探测放行才显示（AC-02）
    showEntrust: false,
    entrustHint: '',
    // 「我的委托」入口（S1 工作项 5）：**独立**探测、独立门控，与上面的经理入口
    // 不共用一个开关 —— 理由见 probeMineEntrust()。
    showMineEntrust: false
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
    } else {
      // 未登录给中性占位（第三方审计 P3-10：原默认「货主」会误导身份判断）
      this.setData({
        userName: '未登录',
        userInitials: '客',
        phoneText: '未绑定手机号',
        currentRole: '',
        roleLabel: '登录后使用'
      })
    }
    this.probeEntrust()
    this.probeMineEntrust()
  },

  /**
   * 静默探测「委托发货」入口的可见性（AC-02 入口隔离）。
   *
   * 判定依据是**服务端**：组织成员资格 + 该货主的委托授权 + `entrust:view` 权限，
   * 三者都满足才放行。绝不用本地的 `current_role` 判断 —— 它存在 Storage 里，
   * 改一下就能造出一个"看起来是经理"的界面，而真正的权限只在服务端。
   *
   * 探测失败（401/403/404/断网）一律保持隐藏：显示一个"点进去必然失败"的入口
   * 比不显示更糟，用户会以为功能坏了，而不是"这个身份没有这项能力"。
   */
  probeEntrust() {
    const self = this
    entrust.probeEntry().then(function (decision) {
      self.setData({
        showEntrust: !!decision.visible,
        entrustHint: decision.hint || ''
      })
    })
  },

  onEntrust() {
    wx.navigateTo({ url: '/pages/entrust/workbench/workbench' })
  },

  /**
   * 静默探测「我的委托」入口的可见性（S1 工作项 5，AC-02 的**货主侧**）。
   *
   * ⚠️ 它与 `probeEntrust()` **各探一次、互不代替**，这是本函数存在的全部理由。
   * 二者问的不是同一个问题：
   *   · `probeEntrust()`  → `view=org`：「我在某个组织里能**受理**委托吗」；
   *   · `probeMineEntrust()` → `view=owner`：「**委托这个能力**对我开不开放」。
   * 一个货主账号通常**只**满足后者（他不是任何组织的经理）—— 若两个入口共用
   * 一个开关，货主就永远看不到自己的委托列表，而这类"入口从不出现"的缺陷
   * 不会报错、也不会被任何后端用例发现（它们只测接口）。
   *
   * 判据由 `utils/entrust.probeOwnerEntry()` 独占：它按 HTTP 状态判定
   * （404 = 功能未启用、401 = 登录过期 ⇒ 隐藏），而**空列表算可见** ——
   * "你还没提过委托"是正常起点，不是权限问题。
   *
   * 探测失败一律保持隐藏：显示一个点进去必然失败的入口比不显示更糟。
   */
  probeMineEntrust() {
    const self = this
    entrust.probeOwnerEntry().then(function (decision) {
      self.setData({ showMineEntrust: !!decision.visible })
    })
  },

  onMineEntrust() {
    wx.navigateTo({ url: '/pages/entrust/assignments/assignments' })
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
        const target = map[role]
        if (!target) {
          // 兜底：ROLE_LIST 里若加了新角色而这里漏了映射，绝不能把非 tabBar 页交给
          // wx.switchTab —— 它打不到时会**静默失败**，用户只看到"点了没反应"。
          // 用 reLaunch 回首页（index 是 root 页）让人重新选身份，失败也是可见的。
          wx.reLaunch({ url: '/pages/index/index' })
          return
        }
        wx.switchTab({ url: target })
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
