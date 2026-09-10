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
        if (!silent) wx.showToast({ title: '网络异常，请稍后重试', icon: 'none' })
        reject(err)
      }
    })
  })
}

module.exports = { request, getToken, setToken, clearToken, BASE_URL }
