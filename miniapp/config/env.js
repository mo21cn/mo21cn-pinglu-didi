// ---------------------------------------------------------------------------
// 运行档位与部署目标 —— 全小程序**唯一**的环境配置入口（WP-0 E-0.4 的"三档配置"）
//
// 为什么收拢在这一个文件：`request.js`（传输层）与 `entrust.js`（上传）都要按档位
// 分流，若各自判环境就会漂移。切换档位 = 改 `PROFILE` 这**一行**，其余代码不感知。
//
// dev     —— 联调档（现状）：开发者工具走回环、真机走 LAN IP；允许 Storage 覆盖。
//           CI（scripts/verify_*.js）与本机走查的基线就是这一档，⛔ 勿动其语义。
// demo    —— 体验版档：与 release 同走云托管 `callContainer`（私有协议，免域名/免备案）。
//           当前与 release 共用同一环境；将来体验版要隔离时只改本文件的常量。
// release —— 发布档：`wx.cloud.callContainer` 调云托管容器。
//           ⛔ **不读 LAN IP、不读 Storage 覆盖**（工作单 WP-4 的硬要求：
//           "不用 LAN 兜底与本地存储覆盖"）——防止有人用 Storage 把发布版指到开发机。
//
// ⚠️ 三个常量都会随 `require` 进入 Node 校验环境（verify_frontend_e2e.js 直接
//     require request.js）⇒ 本文件**禁止**在任何模块顶层触碰 `wx`。
// ---------------------------------------------------------------------------

// 当前档位：上线前把这一行改成 'release'（或出体验版时改 'demo'）
const PROFILE = 'dev'

// 微信云托管环境 ID（envId）—— 2026-09-23 HO 开通，与 AppID wx4a57f29bc38ca11d 同主体
const CLOUD_ENV_ID = 'prod-d0ga9bxi6e4226222'

// 后端在云托管里的服务名（小程序调用时经 `X-WX-SERVICE` 头路由）
// ⚠️ 必须与云托管控制台里创建的服务名一致；服务尚未创建前 release/demo 档不可用
const CLOUD_SERVICE = 'pinglu-backend'

module.exports = { PROFILE, CLOUD_ENV_ID, CLOUD_SERVICE }
