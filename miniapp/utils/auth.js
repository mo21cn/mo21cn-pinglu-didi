// 登录态管理：wx.login → 后端 /auth/login → 本地缓存用户信息
const { request, setToken, clearToken, getToken } = require('./request')

const USER_KEY = 'user_info'

/**
 * 开发期登录策略 —— 是否使用「固定身份」由**是否配了真实 AppID**自动决定。
 *
 * 背景：后端 WECHAT_MOCK=true 时 openid = mock-openid-{code}，等于说 **code 本身就是身份**。
 * 若在没有真实 AppID 的情况下走 wx.login：每次调用返回的 code 都不同 →
 * 每次登录都注册出一个新用户 →「我的货源 / 我的订单」永远是空的；更糟的是微信登录服务
 * 一旦不可达（无 APPID / 代理拦截），整条进入链路会直接卡在「登录失败」。
 *
 * 所以分两种情况：
 *   ① 无真实 AppID（占位值 / 游客态 touristappid / Node 校验环境）
 *      → 用固定 code 登录：身份稳定，且不依赖 wx.login，clone 后开箱即用。
 *   ② 有真实 AppID（开发者工具已填真实值 / 真机预览）
 *      → 走真实 wx.login：真实 openid 由微信号决定、天然稳定，
 *        不需要固定身份机制，也不该再让假 code 去请求 code2session。
 *
 * 因此「接入真实 AppID 后必须改为 false」这条要求现在是**自动满足**的，
 * 不需要人工改代码。需要人工干预时，用 Storage 的 dev_stable_identity 强制覆盖。
 *
 * 情况 ② 还有一个断层：**真实 AppID 填上了，后端却可能还没有微信凭据**
 * （WX_APP_SECRET 未配，或 WECHAT_MOCK 仍为 true）。此时 wx.login 拿到的真实 code
 * 每次都不一样，后端若拿它当身份 → 每次登录都是全新空账号，看起来和功能故障一模一样。
 * 故走 wx.login 时会**附带一个预览回退身份 dev_code**（该角色对应的演示账号 code）：
 * 后端有真实凭据就用真实 openid，没有就用 dev_code 落回演示账号。
 * 凭据配齐后这层回退自动失效，客户端无需再改。
 */
const STABLE_IDENTITY_FALLBACK = true
const DEV_CODE_KEY = 'dev_device_code'
const DEV_DEFAULT_CODE = 'seed-shipper'

/** 判断当前是否运行在「有真实 AppID」的环境（真实 appid = wx + 16 位字母数字） */
function _hasRealAppId() {
  try {
    const info = typeof wx.getAccountInfoSync === 'function' ? wx.getAccountInfoSync() : null
    const id = (info && info.miniProgram && info.miniProgram.appId) || ''
    return /^wx[a-z0-9]{16}$/i.test(id)
  } catch (e) {
    // 非小程序环境（Node 校验脚本）没有该 API → 按「无真实 AppID」处理
    return false
  }
}

/**
 * 是否启用固定身份。
 * 优先级：Storage 显式指定 > 按是否配了真实 AppID 自动判定。
 */
function _stableIdentityEnabled() {
  try {
    const forced = wx.getStorageSync('dev_stable_identity')
    if (forced === true || forced === 'true') return true
    if (forced === false || forced === 'false') return false
  } catch (e) { /* Storage 不可用时忽略，走自动判定 */ }
  return _hasRealAppId() ? false : STABLE_IDENTITY_FALLBACK
}

/**
 * 开发期「角色 → 演示账号」映射。
 * 取值与 backend/scripts/seed_demo.py 的 SHIPPER_CODE / OWNER_CODE 严格一致。
 *
 * 为什么必须映射：演示数据（货源 / 船舶 / 订单 / 泊位 / 预约）全部挂在两个演示账号
 * 名下。后端 WECHAT_MOCK=true 时 `openid = mock-openid-{code}`，用别的 code 登录会
 * **当场注册出一个全新空账号** —— 订单页不报错、走「暂无订单」空态，「我的货源」也全空，
 * 表现和功能故障一模一样（2026-09-11 用户报「订单页面仍然是空白」即此因）。
 * 故开发期点哪个身份，就直接登录该身份对应的演示账号。
 */
const DEV_ROLE_CODE = { shipper: 'seed-shipper', owner: 'seed-owner' }

const ROLE_LABELS = {
  shipper: '货主',
  owner: '船东',
  port: '港口方'
}

function getUser() {
  const raw = wx.getStorageSync(USER_KEY)
  if (!raw) return null
  // 容错（第三方审计 P3-2）：损坏的 JSON 不应把整页登录态判断炸掉
  try {
    return JSON.parse(raw)
  } catch (e) {
    console.warn('[auth] user_info 损坏，已清除', (e && e.message) || e)
    try { wx.removeStorageSync(USER_KEY) } catch (e2) { /* 清理失败不阻塞 */ }
    return null
  }
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
 *   ② 无真实 AppID 时的固定身份 `dev_device_code`（见 _stableIdentityEnabled 说明）
 *   ③ 有真实 AppID：真实 wx.login（openid 由微信号决定，天然稳定）
 *
 * @param {object} [opts] {silent, role}
 *   silent=true 时不弹错误提示，由调用方统一处理；
 *   role 用于给出「预览回退身份」（见 DEV_ROLE_CODE），缺省按货主演示账号。
 */
function login(opts) {
  const silent = !!(opts && opts.silent)
  const role = opts && opts.role
  const override = wx.getStorageSync('dev_login_code')
  if (override) return _loginWithCode(override, silent)
  if (_stableIdentityEnabled()) return _loginWithCode(_devCode(), silent)
  return _wxLoginCode().then((code) => _loginWithCode(code, silent, _previewCode(role)))
}

/**
 * 预览回退身份：该角色对应的演示账号 code。
 * 后端尚无微信凭据（或仍 WECHAT_MOCK=true）时由后端启用，保证真机也命中演示数据。
 */
function _previewCode(role) {
  return DEV_ROLE_CODE[role] || DEV_DEFAULT_CODE
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
function _loginWithCode(code, silent, previewCode) {
  const data = { code }
  // 仅在真实 wx.login 路径附带：后端有真实凭据时不会读它
  if (previewCode) data.dev_code = previewCode
  return request({
    url: '/api/v1/auth/login',
    method: 'POST',
    auth: false,
    silent: !!silent,
    data
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

/**
 * 开发期：把身份切到该角色对应的演示账号。
 * 仅在「无真实 AppID」时生效 —— 配了真实 AppID 后身份由微信号决定，
 * 切角色不再需要换账号（同一个 user 绑多角色即可）。
 *
 * @returns {boolean} true = 换了账号。此时旧 token / user_info 已被清掉，
 *   调用方**必须**按「未登录」重新走 login → bindRole → switchRole，
 *   否则会带着上一个账号的 token 去打新账号的接口（订单列表按 user_id 过滤 → 全空）。
 *
 * 手工指定的 `dev_login_code` 优先级更高（走查脚本要靠它注入 seed-port 测港口身份），
 * 存在时不干预，免得把联调用的固定身份冲掉。
 */
function ensureDevAccount(role) {
  if (!_stableIdentityEnabled()) return false
  if (wx.getStorageSync('dev_login_code')) return false
  const code = DEV_ROLE_CODE[role]
  if (!code) return false
  if (wx.getStorageSync(DEV_CODE_KEY) === code) return false
  wx.setStorageSync(DEV_CODE_KEY, code)
  wx.removeStorageSync(USER_KEY)
  clearToken()
  return true
}

/** 给链路的某一阶段打标记，失败时才能说清是「哪一步」失败 */
function _stage(name, p) {
  return p.catch((err) => {
    if (err && !err.stage) err.stage = name
    throw err
  })
}

/**
 * 进入某角色 —— 首页身份卡、「我的」页身份切换、货主⇄船东互切**共用同一入口**。
 *
 *   ① 开发期先确保登录的是该角色对应的演示账号（换账号则清掉旧登录态）
 *   ② 未登录            → login → bindRole → switchRole
 *   ③ 已登录但未绑该角色 → bindRole → switchRole
 *   ④ 已是当前角色       → 直接返回
 *
 * 失败时给 err.stage 打 login / bind / switch 标记，供页面点名失败环节。
 */
function enterRole(role, opts) {
  const silent = { silent: !!(opts && opts.silent) }
  return Promise.resolve()
    .then(() => {
      ensureDevAccount(role)
    })
    .then(() => {
      if (!isLoggedIn()) {
        return _stage('login', login({ silent: silent.silent, role }))
          .then(() => _stage('bind', bindRole(role, silent)))
          .then(() => _stage('switch', switchRole(role, silent)))
      }
      const user = getUser() || {}
      if ((user.roles || []).indexOf(role) === -1) {
        return _stage('bind', bindRole(role, silent)).then(() =>
          _stage('switch', switchRole(role, silent))
        )
      }
      if (user.current_role !== role) return _stage('switch', switchRole(role, silent))
      return null
    })
}

module.exports = {
  login,
  bindRole,
  switchRole,
  enterRole,
  ensureDevAccount,
  isLoggedIn,
  getUser,
  clearUser,
  ROLE_LABELS
}
