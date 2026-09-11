// 首位屏（01）：身份选择浮窗
// 不设主页——进入即弹出身份选择，点击卡片直接进入对应工作台
const auth = require('../../utils/auth')
const { request, describeError } = require('../../utils/request')

// 可选身份：仅货主 / 船东（港口方身份已下线，港航服务改由「港口」页承载）
const ROLE_META = {
  shipper: { label: '货主', page: '/pages/shipper/shipper' },
  owner:   { label: '船东', page: '/pages/owner/owner' }
}

/**
 * 失败环节的中文名。
 * 原先整条链路共用一个 `.catch(() => '登录失败')`，于是任何环节出问题
 * （wx.login 不可用 / 后端没起 / 绑定失败 / 页面编译错）都只显示「登录失败」，
 * 既定位不到原因，也判断不出该修哪里。
 */
const STAGE_LABEL = {
  login: '登录（/auth/login）',
  bind: '绑定身份（/auth/bind-role）',
  switch: '切换身份（/auth/switch-role）',
  enter: '进入工作台（switchTab）'
}

/** 给链路的每个阶段打标记，失败时才能说清是哪一步 */
function stage(name, p) {
  return p.catch((err) => {
    if (err && !err.stage) err.stage = name
    throw err
  })
}

Page({
  data: {
    pendingRole: '',
    logging: false,
    // 进页面先探一次后端；不可用时提前告知，而不是等点完身份再报「登录失败」
    backendDown: false
  },

  onLoad() {
    this.probeBackend()
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
    this.probeBackend()
  },

  /** 后端连通性预检：失败只提示、不阻塞（进入链路的报错仍会兜底） */
  probeBackend() {
    request({ url: '/healthz', auth: false, silent: true })
      .then(() => this.setData({ backendDown: false }))
      .catch(() => this.setData({ backendDown: true }))
  },

  /** 失败统一出口：撤掉遮罩 + 说清「哪一步 / 什么原因 / 怎么办」 */
  reportFail(role, err) {
    const d = describeError(err)
    const s = STAGE_LABEL[(err && err.stage) || 'login'] || '登录'
    const label = (ROLE_META[role] || {}).label || ''
    this.setData({
      logging: false,
      pendingRole: '',
      backendDown: /无法连接后端|请求超时|网络异常/.test(d.cause)
    })
    // 完整错误同时打到 Console，便于对着调试器排查
    console.error('[进入工作台失败]', role, err)
    wx.showModal({
      title: '进入' + label + '工作台失败',
      content: '失败环节：' + s + '\n原因：' + d.cause + (d.hint ? '\n\n' + d.hint : ''),
      showCancel: false,
      confirmText: '知道了'
    })
  },

  onPickRole(e) {
    const role = e.currentTarget.dataset.role
    if (!ROLE_META[role] || this.data.logging) return
    this.setData({ pendingRole: role, logging: true })

    const fail = (err) => this.reportFail(role, err)
    // 各阶段都传 silent：统一由 reportFail 弹一个说清原因的弹窗，
    // 避免 request.js 的 toast 与弹窗各说一遍、互相覆盖
    const silent = { silent: true }

    const enter = () => new Promise((resolve, reject) => {
      // 跳转前先撤掉遮罩：否则「跳转成功」这条路径会把 loading 标记留在页面上，
      // 一旦页面没被销毁就会变成盖住全屏、吞掉点击的死遮罩（此前踩过同样的坑）。
      this.setData({ logging: false, pendingRole: '' })
      wx.switchTab({ url: ROLE_META[role].page, success: resolve, fail: reject })
    })

    if (!auth.isLoggedIn()) {
      // 首次进入：登录 → 绑定该角色 → 切换为当前角色 → 进入工作台
      //
      // ⚠️ `bindRole` 只把角色写进 roles 列表，服务端 current_role 仍是登录时的
      //    默认角色（shipper），token payload 里的 role 也不会变。缺少 `switchRole`
      //    会带着「货主身份的 token」进船东工作台，后端逐端点角色校验 → 全线 403。
      stage('login', auth.login(silent))
        .then(() => stage('bind', auth.bindRole(role, silent)))
        .then(() => stage('switch', auth.switchRole(role, silent)))
        .then(() => stage('enter', enter()))
        .catch(fail)
      return
    }

    const user = auth.getUser() || {}
    if ((user.roles || []).indexOf(role) === -1) {
      // 已登录但尚未绑定该身份：先绑定再切换
      stage('bind', auth.bindRole(role, silent))
        .then(() => stage('switch', auth.switchRole(role, silent)))
        .then(() => stage('enter', enter()))
        .catch(fail)
    } else if (user.current_role !== role) {
      stage('switch', auth.switchRole(role, silent))
        .then(() => stage('enter', enter()))
        .catch(fail)
    } else {
      stage('enter', enter()).catch(fail)
    }
  }
})
