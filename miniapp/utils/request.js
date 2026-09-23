// 网络请求封装：统一鉴权头 / 错误处理 / 401 清除本地登录态并提示重新登录
// （第三方审计 P2-7：原注释写「401 自动重新登录」但实现只是 clearToken+toast，注释与行为不符 → 改注释对齐现状。
//   自动重登需重放整个 enterRole 链路且可能切错角色，不在请求层做；登录态过期由用户在首页重新进入。）
// ---- 运行档位（唯一入口：config/env.js）----
// dev 档走下面的直连地址；demo/release 档走 `wx.cloud.callContainer`（云托管私有协议）。
const { PROFILE, CLOUD_ENV_ID, CLOUD_SERVICE } = require('../config/env')
const USE_CLOUD = PROFILE !== 'dev'

// ---- 后端地址解析（仅 dev 档使用）----
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
  // ⛔ Storage 覆盖是**联调便利**，只在 dev 档生效 —— release/demo 档严禁被
  // Storage 把流量指到别处（工作单 WP-4："不用 LAN 兜底与本地存储覆盖"）。
  if (!USE_CLOUD) {
    try {
      const override = wx.getStorageSync('dev_base_url')
      if (override) return override
    } catch (e) { /* Storage 不可用（如 Node 校验环境）时静默降级 */ }
  }
  return isRealDevice() ? LAN_BASE_URL : DEVTOOLS_BASE_URL
}

const BASE_URL = resolveBaseUrl()
const BASE_HOST = BASE_URL.replace(/^https?:\/\//, '') // 供错误提示拼接，避免文案与真实地址脱节
const TOKEN_KEY = 'access_token'

/**
 * 把后端的 `detail` 说成**一句人话**。
 *
 * ⚠️ 传输层**不替调用方决定** `detail` 的类型：后端的 `detail` 有两类 ——
 *    纯字符串（400/403 那类"一句话拒绝"）和**结构体**（409 的两种分流：
 *    规则不过带 `rule_checks` 逐条判定、状态冲突带 `existing_confirmation_id`）。
 *    早先这里统一 `String(detail)` ⇒ 结构体在到达页面之前就被压成 `'[object Object]'`，
 *    于是页面上那两条分支（`isObj ? capacityRuleRows(det)` / `det.existing_confirmation_id`）
 *    **全是死代码**，用户只看到「确认未提交（服务端返回 409）：[object Object]」。
 *    2026-09-17 设备走查 ㊺ 章实测到这一版：服务端响应体里四条判定都在，界面判定行=0。
 * ⇒ 规则：`err.detail` **原样保留结构**；需要字符串的地方显式调本函数。
 */
function detailText(detail) {
  if (detail == null) return ''
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object') {
    // 结构体里那句人话按后端约定在 `message`；没有就返回空串让调用方走自己的兜底文案，
    // **绝不**退回 `'[object Object]'`（那等于把"没话可说"伪装成一句话）
    return typeof detail.message === 'string' ? detail.message : ''
  }
  return String(detail)
}

/** 构造带诊断信息的 HTTP 错误（错误对象上带 httpStatus/detail，供 describeError 翻译） */
function httpError(httpStatus, detail) {
  const e = new Error(detailText(detail))
  e.httpStatus = httpStatus
  // ⚠️ 原样挂结构，不 `String()`：压成字符串会让 409 的两种分流在页面侧不可分辨
  e.detail = detail
  return e
}

/**
 * 从 HTTP 响应体构造错误 —— **传输层与校验脚本共用同一口径**。
 *
 * 为什么要有它：`detail` 的兜底（空 ⇒ `'请求失败'`）原先只写在 `request()` 的
 * `wx.request` 回调里，于是校验脚本想"造一个与真机同形的错误"就只能**再写一遍** ——
 * 而两份实现会各自演化，这正是「注释里说形状一致、实际不一致」的来源
 * （2026-09-17 实测：e2e 的写通道把 detail 压成了字符串，与真机不同形，而注释写着"一致"）。
 * 抽出来之后，`scripts/verify_frontend_e2e.js` 的写通道与本函数**是同一段代码**：
 * 形状一致由机器保证，不靠人记得。
 */
function errorFromResponse(httpStatus, data) {
  const raw = data && data.detail
  const detail = raw === undefined || raw === null || raw === '' ? '请求失败' : raw
  return httpError(httpStatus, detail)
}

/**
 * 把错误翻译成「哪一步坏了 + 怎么修」。
 * 调用方（如首页身份进入链路）据此给出可执行的提示，
 * 而不是笼统的「登录失败 / 网络异常」——后者无法定位问题。
 * @returns {{cause: string, hint: string}}
 */
function describeError(err) {
  const msg = (err && (err.errMsg || err.message)) || ''
  // 云托管（callContainer）私有协议的失败：errMsg 形如
  // "cloud.callContainer:fail ..."（环境错/服务停机/基础库过旧都落这里）
  if (/callContainer/i.test(msg)) {
    return {
      cause: '云托管调用失败',
      hint: '依次确认：①小程序已开通云开发且 envId 与 config/env.js 一致；'
        + '②云托管服务「' + CLOUD_SERVICE + '」已部署且在线（控制台看实例数）；'
        + '③调试基础库 ≥ 2.23.0。'
    }
  }
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
      hint: USE_CLOUD
        ? '云托管服务可能已缩容到 0（首次调用会冷启动，稍等重试）或后端处理超时；看云托管控制台日志'
        : '确认后端已启动（' + BASE_HOST + '）；若开了系统代理，注意别让代理拦截该地址'
    }
  }
  if (/fail( to)? connect|unable to connect/i.test(msg)) {
    return {
      cause: USE_CLOUD ? '无法连接云托管服务' : '无法连接后端 ' + BASE_HOST,
      hint: USE_CLOUD
        ? '云托管服务未部署、已停机或环境配置错误；到云托管控制台核对服务状态'
        : '后端未启动，或代理/防火墙拦截了对 ' + BASE_HOST + ' 的请求'
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
    // ⚠️ 用 `detailText()` 而不是直接把 `err.detail` 拼进来：`detail` 可能是**结构体**
    //    （409 的两种分流），拼对象会得到「接口返回 409：[object Object]」——
    //    等于把"服务端说了原因"显示成"没原因"。结构体里那句人话在 `message`。
    const dt = detailText(err.detail)
    return {
      cause: '接口返回 ' + err.httpStatus + (dt ? '：' + dt : ''),
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
 * 成功回调的统一处理（wx.request 与 callContainer 的响应同形：statusCode + data）。
 * 抽出来是为了**一条纪律**：dev 与 release 两档的业务语义（2xx 放行／401 清态／
 * 错误构造）由**同一段代码**保证，不靠两份实现各自演化。
 */
function onResponse(res, silent, resolve, reject) {
  if (res.statusCode >= 200 && res.statusCode < 300) {
    resolve(res.data)
  } else if (res.statusCode === 401) {
    clearToken()
    if (!silent) wx.showToast({ title: '登录已过期，请重新登录', icon: 'none' })
    reject(httpError(401, '登录已过期，请重新登录'))
  } else {
    // 兜底与错误构造都交给 `errorFromResponse`：与校验脚本共用同一段代码，
    // 「e2e 里造出来的拒绝形状」与真机因此**不可能**各自演化。
    const err = errorFromResponse(res.statusCode, res.data)
    if (!silent) wx.showToast({ title: err.message || '请求失败', icon: 'none' })
    reject(err)
  }
}

/** 失败回调的统一处理（网络层错误分级提示，两档共用） */
function onFail(err, silent, reject) {
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

/** 组装公共请求头：鉴权与业务头合并，⛔ 不允许覆盖 Authorization */
function buildHeader(headers, withAuth) {
  const header = { 'Content-Type': 'application/json' }
  Object.keys(headers || {}).forEach(function (k) {
    if (k.toLowerCase() === 'authorization') return
    header[k] = headers[k]
  })
  if (withAuth && getToken()) {
    header['Authorization'] = 'Bearer ' + getToken()
  }
  return header
}

/**
 * dev 档传输：wx.request 直连后端（开发者工具回环 / 真机 LAN）。
 */
function directRequest(opts) {
  const { url, method = 'GET', data = {}, auth = true, silent = false, headers = {} } = opts
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + url,
      method,
      data,
      header: buildHeader(headers, auth),
      success(res) { onResponse(res, silent, resolve, reject) },
      fail(err) { onFail(err, silent, reject) }
    })
  })
}

/**
 * demo/release 档传输：`wx.cloud.callContainer` → 微信云托管（私有协议）。
 *
 * 为什么免域名：callContainer 不走 `wx.request` 的域名白名单，走微信侧私有通道
 * 直达同主体的云托管环境（工作单 E-0.8 的核验结论）。`X-WX-SERVICE` 头把请求
 * 路由到环境内的指定服务；`config.env` 指定环境（app.js onLaunch 已 init）。
 */
function cloudRequest(opts) {
  const { url, method = 'GET', data = {}, auth = true, silent = false, headers = {} } = opts
  return new Promise((resolve, reject) => {
    const header = buildHeader(headers, auth)
    header['X-WX-SERVICE'] = CLOUD_SERVICE
    wx.cloud.callContainer({
      config: { env: CLOUD_ENV_ID },
      path: url,
      method,
      data,
      header,
      success(res) { onResponse(res, silent, resolve, reject) },
      fail(err) { onFail(err, silent, reject) }
    })
  })
}

/**
 * 通用请求
 * @param {object} opts {url, method, data, auth, silent, headers} auth=true 时自动附带
 *   Bearer token；silent=true 时不自动弹错误 toast（供调用方自行处理预期内失败，如 404 探测）；
 *   headers 用于附加业务必需的请求头（如写端点要求的 `Idempotency-Key`）——
 *   与鉴权头合并，**不允许覆盖 Authorization**（那是请求层的职责，业务不该改写它）。
 */
function request(opts) {
  return USE_CLOUD ? cloudRequest(opts) : directRequest(opts)
}

module.exports = {
  request,
  getToken,
  setToken,
  clearToken,
  describeError,
  // 给静态门禁用的纯函数：`httpError` 与 `detailText` 都不碰 wx，
  // 可以在 Node 下直接调用 —— 「结构体 detail 不许被压成字符串」这条契约
  // 因此是**可执行地**被验证的，而不是只写在注释里
  httpError,
  detailText,
  // 同上，且 `scripts/verify_frontend_e2e.js` 的写通道也用它构造拒绝 ——
  // 校验脚本里"造出来的错误形状"与真机是同一段代码，不靠注释承诺一致
  errorFromResponse,
  BASE_URL,
  // 档位接线（供静态契约脚本断言"release 档必须走 callContainer"）：
  PROFILE,
  USE_CLOUD,
  CLOUD_ENV_ID,
  CLOUD_SERVICE
}
