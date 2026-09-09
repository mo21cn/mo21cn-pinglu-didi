// 登录态管理：wx.login → 后端 /auth/login → 本地缓存用户信息
const { request, setToken, clearToken, getToken } = require('./request')

const USER_KEY = 'user_info'

const ROLE_LABELS = {
  shipper: '货主',
  owner: '船东',
  port: '港口方'
}

function getUser() {
  const raw = wx.getStorageSync(USER_KEY)
  return raw ? JSON.parse(raw) : null
}

function setUser(user) {
  wx.setStorageSync(USER_KEY, JSON.stringify(user))
}

function clearUser() {
  wx.removeStorageSync(USER_KEY)
  clearToken()
}

function isLoggedIn() {
  return !!getToken() && !!getUser()
}

/**
 * 微信一键登录
 * 流程：wx.login 获取临时 code → POST /api/v1/auth/login（后端 Mock 或真实 code2session）
 * 新用户自动注册，默认角色为货主（shipper）。
 */
function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success(loginRes) {
        if (!loginRes.code) {
          reject(new Error('wx.login 未获取到 code'))
          return
        }
        request({
          url: '/api/v1/auth/login',
          method: 'POST',
          auth: false,
          data: { code: loginRes.code }
        })
          .then((data) => {
            setToken(data.access_token)
            setUser({
              user_id: data.user_id,
              openid: data.openid,
              nickname: data.nickname,
              roles: data.roles,
              current_role: data.current_role,
              role_label: ROLE_LABELS[data.current_role] || data.current_role
            })
            resolve(data)
          })
          .catch(reject)
      },
      fail: reject
    })
  })
}

/** 绑定新角色（如货主升级为货主+船东） */
function bindRole(role) {
  return request({
    url: '/api/v1/auth/bind-role',
    method: 'POST',
    data: { role }
  }).then((data) => {
    const user = getUser() || {}
    setUser({ ...user, roles: data.roles })
    return data
  })
}

/** 切换当前角色（后端重签 token） */
function switchRole(role) {
  return request({
    url: '/api/v1/auth/switch-role',
    method: 'POST',
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
