// 网络请求封装：统一鉴权头 / 错误处理 / 401 清除本地登录态并提示重新登录
// （第三方审计 P2-7：原注释写「401 自动重新登录」但实现只是 clearToken+toast，注释与行为不符 → 改注释对齐现状。
//   自动重登需重放整个 enterRole 链路且可能切错角色，不在请求层做；登录态过期由用户在首页重新进入。）
// ---- 后端地址解析 ----
// 开发者工具 / Node 校验脚本走回环地址：这是 CI（scripts/verify_*.js）与本机自动化的基线，勿改。
const DEVTOOLS_BASE_URL = 'http://127.0.0.1:8000'
// 真机（预览 / 体验版 / 正式版）走本机局域网地址：手机上的 127.0.0.1 指向手机自己，
// 必须换成运行后端的电脑的局域网 IP（手机需与该电脑连同一个 WiFi）。
// 换网络或换机器后此地址会变：届时在开发者工具 Storage 面板设 dev_base_url 覆盖，
// 或只改这一行即可，无需动其它代码。
const LAN_BASE_URL = 'http://192.168.0.178:8000'

/**
 * 解析后端地址：按运行平台自动选择，并允许 Storage 显式覆盖。
 *
 * 判定依据是 platform —— 真机为 ios/android，开发者工具为 devtools。
 * 取不到 platform 时（Node 校验环境没有这些 API）按开发者工具处理，
 * 保证 scripts/verify_*.js 的断言基线不被扰动。
 */
/**
 * 是否运行在真机（iOS / Android）上。
 *
 * 与 resolveBaseUrl 共用同一判定口径。取不到 platform 时（Node 校验环境
 * 没有这些 API）按开发者工具处理，保证 scripts/verify_*.js 的断言基线不被扰动。
 */
function isRealDevice() {
  let platform = ''
  try {
    if (typeof wx.getDeviceInfo === 'function') {
      platform = (wx.getDeviceInfo() || {}).platform || ''
    } else if (typeof wx.getSystemInfoSync === 'function') {
      platform = (wx.getSystemInfoSync() || {}).platform || ''
    }
  } catch (e) { /* 非小程序环境无此 API，按开发者工具处理 */ }
  return platform === 'ios' || platform === 'android'
}

function resolveBaseUrl() {
  try {
    const override = wx.getStorageSync('dev_base_url')
    if (override) return override
  } catch (e) { /* Storage 不可用（如 Node 校验环境）时静默降级 */ }
  return isRealDevice() ? LAN_BASE_URL : DEVTOOLS_BASE_URL
}

const BASE_URL = resolveBaseUrl()
const BASE_HOST = BASE_URL.replace(/^https?:\/\//, '') // 供错误提示拼接，避免文案与真实地址脱节
const TOKEN_KEY = 'access_token'

/** 构造带诊断信息的 HTTP 错误（错误对象上带 httpStatus/detail，供 describeError 翻译） */
function httpError(httpStatus, detail) {
  const e = new Error(detail)
  e.httpStatus = httpStatus
  e.detail = detail
  return e
}

/**
 * 把错误翻译成「哪一步坏了 + 怎么修」。
 * 调用方（如首页身份进入链路）据此给出可执行的提示，
 * 而不是笼统的「登录失败 / 网络异常」——后者无法定位问题。
 * @returns {{cause: string, hint: string}}
 */
function describeError(err) {
  const msg = (err && (err.errMsg || err.message)) || ''
  if (/url not in domain list/i.test(msg)) {
    // ⚠️ 这条提示必须区分运行环境：开发者工具里那个「不校验合法域名」**只对电脑模拟器生效**，
    // 真机上照做无效 —— 用户会对着「设置明明勾了却还是被拦」的界面无从下手（真实踩坑）。
    if (isRealDevice()) {
      return {
        cause: '请求域名未通过校验',
        hint: '真机放行方式：点右上角「···」→「开发调试」（部分版本显示为「打开调试」），'
          + '小程序会自动重启，之后即可直连局域网后端。'
          + '开发者工具里的「不校验合法域名」只对电脑模拟器生效，真机无效。'
      }
    }
    return {
      cause: '请求域名未通过校验',
      hint: '开发者工具「详情 → 本地设置」勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」，再重新编译'
    }
  }
  if (/timeout/i.test(msg)) {
    return {
      cause: '请求超时',
      hint: '确认后端已启动（' + BASE_HOST + '）；若开了系统代理，注意别让代理拦截该地址'
    }
  }
  if (/fail( to)? connect|unable to connect/i.test(msg)) {
    return {
      cause: '无法连接后端 ' + BASE_HOST,
      hint: '后端未启动，或代理/防火墙拦截了对 ' + BASE_HOST + ' 的请求'
    }
  }
  if (/switchTab/i.test(msg)) {
    return {
      cause: '无法跳转到工作台页面',
      hint: '目标页不在 app.json 的 tabBar 列表中，或该页面编译报错（看 Console 面板）'
    }
  }
  if (/wx\.login|login:fail/i.test(msg)) {
    return {
      cause: 'wx.login 不可用',
      hint: 'AppID 未配置或微信登录服务不可达；开发期可用 Storage 里的 dev_login_code 指定固定身份'
    }
  }
  if (err && err.httpStatus) {
    return {
      cause: '接口返回 ' + err.httpStatus + (err.detail ? '：' + err.detail : ''),
      hint: '看后端终端（uvicorn --reload 窗口）的日志定位'
    }
  }
  // 兜底：原始 errMsg 放进 hint，界面上仍给一句人话
  return { cause: '网络异常，请稍后重试', hint: msg }
}

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || ''
}

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token)
}

function clearToken() {
  wx.removeStorageSync(TOKEN_KEY)
}

/**
 * 通用请求
 * @param {object} opts {url, method, data, auth, silent} auth=true 时自动附带 Bearer token；
 *   silent=true 时不自动弹错误 toast（供调用方自行处理预期内失败，如 404 探测）
 */
function request(opts) {
  const { url, method = 'GET', data = {}, auth = true, silent = false } = opts
  return new Promise((resolve, reject) => {
    const header = { 'Content-Type': 'application/json' }
    if (auth && getToken()) {
      header['Authorization'] = 'Bearer ' + getToken()
    }
    wx.request({
      url: BASE_URL + url,
      method,
      data,
      header,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else if (res.statusCode === 401) {
          clearToken()
          if (!silent) wx.showToast({ title: '登录已过期，请重新登录', icon: 'none' })
          reject(httpError(401, '登录已过期，请重新登录'))
        } else {
          const detail = (res.data && res.data.detail) || '请求失败'
          if (!silent) wx.showToast({ title: String(detail), icon: 'none' })
          reject(httpError(res.statusCode, String(detail)))
        }
      },
      fail(err) {
        if (silent) {
          reject(err)
          return
        }
        // 网络层错误分级提示：区分「域名未配置 / 后端未启动 / 真断网」，
        // 避免所有失败都笼统提示"网络异常"而无法定位问题（联调期高频场景）。
        const d = describeError(err)
        if (/url not in domain list/i.test((err && err.errMsg) || '')) {
          wx.showModal({ title: d.cause, content: d.hint, showCancel: false })
        } else {
          wx.showToast({ title: d.cause, icon: 'none' })
        }
        reject(err)
      }
    })
  })
}

module.exports = { request, getToken, setToken, clearToken, describeError, BASE_URL }
