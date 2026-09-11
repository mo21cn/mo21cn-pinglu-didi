// 登录态管理：wx.login → 后端 /auth/login → 本地缓存用户信息
const { request, setToken, clearToken, getToken } = require('./request')

const USER_KEY = 'user_info'

/**
 * 开发期登录策略（⚠️ 接入真实 AppID 后必须改为 false）
 *
 * 背景：本仓库 project.config.json 的 appid 仍是占位值（wxYOUR_APPID_HERE），
 * 开发者工具拿不到真实微信身份；而后端 WECHAT_MOCK=true 时
 * openid = mock-openid-{code}，等于说 **code 本身就是身份**。
 * 若走 wx.login：每次调用返回的 code 都不同 → 每次登录都注册出一个新用户
 * →「我的货源 / 我的订单」永远是空的；更糟的是微信登录服务一旦不可达
 * （无 APPID / 代理拦截），整条进入链路会直接卡在「登录失败」。
 * 故开发期固定使用一个本地 code：身份稳定，且不依赖 wx.login。
 */
const DEV_STABLE_IDENTITY = true
const DEV_CODE_KEY = 'dev_device_code'
const DEV_DEFAULT_CODE = 'devtools-local'

const ROLE_LABELS = {
  shipper: '货主',
  owner: '船东'
}

function getUser() {
  const raw = wx.getStorageSync(USER_KEY)
  return raw ? JSON.parse(raw) : null
}

function setUser(user) {
  wx.setStorageSync(USER_KEY, JSON.stringify(user))
}

/**
 * 退出登录：清空登录态。
 * `dev_login_code`（联调用的固定身份 code）一并清除 —— 否则退出后重新点身份时，
 * 仍会用上一次的 code 登回旧账号（可能是另一个角色），导致「刚退出就进错身份」。
 */
function clearUser() {
  wx.removeStorageSync(USER_KEY)
  wx.removeStorageSync('dev_login_code')
  clearToken()
}

function isLoggedIn() {
  return !!getToken() && !!getUser()
}

/**
 * 微信一键登录
 * 流程：wx.login 获取临时 code → POST /api/v1/auth/login（后端 Mock 或真实 code2session）
 * 新用户自动注册，默认角色为货主（shipper）。
 *
 * 身份优先级：
 *   ① Storage 里的 `dev_login_code`（联调指定身份，如 seed-shipper / seed-owner）
 *   ② 开发期固定身份 `dev_device_code`（见 DEV_STABLE_IDENTITY 说明）
 *   ③ 生产：真实 wx.login
 *
 * @param {object} [opts] {silent} silent=true 时不弹错误提示，由调用方统一处理
 */
function login(opts) {
  const silent = !!(opts && opts.silent)
  const override = wx.getStorageSync('dev_login_code')
  if (override) return _loginWithCode(override, silent)
  if (DEV_STABLE_IDENTITY) return _loginWithCode(_devCode(), silent)
  return _wxLoginCode().then((code) => _loginWithCode(code, silent))
}

/** 取 wx.login 的临时 code（仅生产路径使用） */
function _wxLoginCode() {
  return new Promise((resolve, reject) => {
    wx.login({
      success(res) {
        if (res && res.code) {
          resolve(res.code)
        } else {
          const e = new Error('wx.login 未返回 code')
          e.errMsg = 'wx.login:fail 未返回 code'
          reject(e)
        }
      },
      fail(err) {
        const e = new Error((err && err.errMsg) || 'wx.login 失败')
        e.errMsg = (err && err.errMsg) || 'wx.login:fail'
        reject(e)
      }
    })
  })
}

/**
 * 开发期固定身份 code：首次取用时落盘并长期复用。
 * 刻意不放进 clearUser —— 否则「退出 → 再进入」会换成一个全新账号，
 * 刚发布的货源、订单全部看不见。
 */
function _devCode() {
  let code = wx.getStorageSync(DEV_CODE_KEY)
  if (!code) {
    code = DEV_DEFAULT_CODE
    wx.setStorageSync(DEV_CODE_KEY, code)
  }
  return code
}

/** 用指定 code 完成登录（token + 用户信息落地） */
function _loginWithCode(code, silent) {
  return request({
    url: '/api/v1/auth/login',
    method: 'POST',
    auth: false,
    silent: !!silent,
    data: { code }
  }).then((data) => {
    setToken(data.access_token)
    setUser({
      user_id: data.user_id,
      openid: data.openid,
      nickname: data.nickname,
      roles: data.roles,
      current_role: data.current_role,
      role_label: ROLE_LABELS[data.current_role] || data.current_role
    })
    return data
  })
}

/** 绑定新角色（如货主升级为货主+船东） */
function bindRole(role, opts) {
  return request({
    url: '/api/v1/auth/bind-role',
    method: 'POST',
    silent: !!(opts && opts.silent),
    data: { role }
  }).then((data) => {
    const user = getUser() || {}
    setUser({ ...user, roles: data.roles })
    return data
  })
}

/** 切换当前角色（后端重签 token） */
function switchRole(role, opts) {
  return request({
    url: '/api/v1/auth/switch-role',
    method: 'POST',
    silent: !!(opts && opts.silent),
    data: { role }
  }).then((data) => {
    setToken(data.access_token)
    const user = getUser() || {}
    setUser({
      ...user,
      current_role: data.current_role,
      role_label: ROLE_LABELS[data.current_role] || data.current_role
    })
    return data
  })
}

module.exports = { login, bindRole, switchRole, isLoggedIn, getUser, clearUser, ROLE_LABELS }
