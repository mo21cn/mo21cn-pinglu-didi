// 网络请求封装：统一鉴权头 / 错误处理 / 401 清除本地登录态并提示重新登录
// （第三方审计 P2-7：原注释写「401 自动重新登录」但实现只是 clearToken+toast，注释与行为不符 → 改注释对齐现状。
//   自动重登需重放整个 enterRole 链路且可能切错角色，不在请求层做；登录态过期由用户在首页重新进入。）
const BASE_URL = 'http://127.0.0.1:8000' // 开发环境后端地址
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
    return {
      cause: '请求域名未通过校验',
      hint: '开发者工具「详情 → 本地设置」勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」，再重新编译'
    }
  }
  if (/timeout/i.test(msg)) {
    return {
      cause: '请求超时',
      hint: '确认后端已启动（127.0.0.1:8000）；若开了系统代理，注意别让代理拦截本机回环地址'
    }
  }
  if (/fail( to)? connect|unable to connect/i.test(msg)) {
    return {
      cause: '无法连接后端 127.0.0.1:8000',
      hint: '后端未启动，或代理/防火墙拦截了本机请求'
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
