// 网络请求封装：统一鉴权头 / 错误处理 / 401 自动重新登录
const BASE_URL = 'http://127.0.0.1:8000' // 开发环境后端地址
const TOKEN_KEY = 'access_token'

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
          wx.showToast({ title: '登录已过期，请重新登录', icon: 'none' })
          reject(new Error('unauthorized'))
        } else {
          const detail = (res.data && res.data.detail) || '请求失败'
          if (!silent) wx.showToast({ title: String(detail), icon: 'none' })
          reject(new Error(String(detail)))
        }
      },
      fail(err) {
        if (silent) {
          reject(err)
          return
        }
        // 网络层错误分级提示：区分「域名未配置 / 后端未启动 / 真断网」，
        // 避免所有失败都笼统提示"网络异常"而无法定位问题（联调期高频场景）。
        const msg = (err && err.errMsg) || ''
        if (/url not in domain list/i.test(msg)) {
          wx.showModal({
            title: '域名未配置',
            content: '请在开发者工具「详情 → 本地设置」勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」后重新编译。',
            showCancel: false,
          })
        } else if (/timeout/i.test(msg)) {
          wx.showToast({ title: '请求超时，请检查后端是否启动', icon: 'none' })
        } else if (/fail( to)? connect|unable to connect/i.test(msg)) {
          wx.showToast({ title: '无法连接后端（127.0.0.1:8000）', icon: 'none' })
        } else {
          wx.showToast({ title: '网络异常，请稍后重试', icon: 'none' })
        }
        reject(err)
      }
    })
  })
}

module.exports = { request, getToken, setToken, clearToken, BASE_URL }
