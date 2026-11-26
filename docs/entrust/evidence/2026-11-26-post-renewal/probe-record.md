# 2026-11-26 续费后「仍不能登录」只读复现记录

> 触发：HO 反馈「云托管续费已经完成」，并提供手机截图。
> 截图读数：弹窗「**进入货主工作台失败**」／「失败环节：登录（/auth/login）」／
> 「原因：**云托管调用失败**」＋三条固定检查项（envId 一致、服务已部署且在线、基础库 ≥ 2.23.0）。
> 结论先说：**续费解决了环境付费层（402），但没有恢复服务 / 默认域名（404）**，
> 所以 `wx.cloud.callContainer` 仍然失败 ⇒ 登录链路断在第一步。
> ⛔ 本轮全程只读；未改 `miniapp/**`、未改 `env.js`、未写云端、未触发部署。

## 1. 时间点与动作

| 时间（GMT+8） | 动作 |
| --- | --- |
| 11-08 09:3x | 记录 `402 ENV_ISOLATED`（证据 `2026-11-08-env-isolated/`），判定唯一解法为 HO 续费 |
| 11-26 12:56 | HO 反馈续费完成 + 手机报错截图 |
| 11-26 12:5x–13:0x | 只读复测 5 个路径、负控 host、DNS、官网释义、`check_demo_readiness.py` |
| 11-26 13:0x | 打开云托管控制台代办通道，发现登录态过期 ⇒ 启动扫码等待 |

## 2. 决定性读数（网关层）

Base：`https://pinglu-backend-318466-5-1494250212.sh.run.tcloudbase.com`
全部响应头 `server: cbrgw` ⇒ CloudBase Run 网关层，**请求未到达容器**。

| 路径 | 11-08 | 11-26 |
| --- | --- | --- |
| `GET /healthz` | `402 ENV_ISOLATED` | **`404 INVALID_HOST`** |
| `GET /openapi.json` | 402 | 404 |
| `GET /api/v1/customs/sources` | 402 | 404 |
| `GET /` / `GET /docs` | 402 | 404 |
| `POST /api/v1/auth/login`（`{"code":"probe-only"}`） | 402 | 404 |

11-26 统一 body：

```json
{"code":"INVALID_HOST","message":"Invalid host. For more information, please refer to https://docs.cloudbase.net/error-code/service/INVALID_HOST","requestId":"<uuid>"}
```

⭐ **负控**（排除「只是 DNS 没生效 / 402 残留」）：

| Host | HTTP | body.code |
| --- | --- | --- |
| `prod-d0ga9bxi6e4226222.sh.run.tcloudbase.com` | 404 | `INVALID_HOST` |
| `pinglu-backend.sh.run.tcloudbase.com` | 404 | `INVALID_HOST` |
| `definitely-not-a-real-service-xyz.sh.run.tcloudbase.com` | 404 | `INVALID_HOST` |

旧域名 DNS **仍正常**解析到 `124.223.145.112` / `124.223.146.85`，
且显式改写 `Host: prod-d0ga9bxi6e4226222` 只得到 `421 Misdirected Request`
⇒ 不是 DNS 问题，是**该默认域名已从网关的合法域名表注销**。

官方释义（`https://docs.cloudbase.net/error-code/service/INVALID_HOST`）：
> 「访问域名或 Host 未正确填写；请求的域名既不是 CloudBase 合法的默认域名，
> 也不是绑定的自定义域名；自定义域名未配置正确 DNS 解析 / 解析未生效。」

## 3. 前端报错链路（为什么弹这三条）

- `miniapp/app.js::onLaunch` → `wx.cloud.init({ env: CLOUD_ENV_ID })`；
- `miniapp/utils/request.js::cloudRequest` → `wx.cloud.callContainer({
  config: { env: 'prod-d0ga9bxi6e4226222' }, path: '/api/v1/auth/login',
  header: { 'X-WX-SERVICE': 'pinglu-backend', ... } })`；
- 目标服务 / 域名不存在 ⇒ `fail(err)`，`errMsg` 含 `callContainer`；
- `describeError()` 命中 `/callContainer/i` ⇒ 返回固定的
  `cause: '云托管调用失败'` + 三条通用检查项，由 `pages/index/index.js` 弹 modal。
- ⚠️ **诊断缺口**：hint 里**没有原始 `errMsg`，也没有网关 `code`**，
  现场看不出是 `ENV_ISOLATED` 还是 `INVALID_HOST`；且若网关以 `success` 返回
  402/404，`errorFromResponse()` 只取 `data.detail`（网关 body 无该字段）⇒ 只显示「请求失败」。

⛔ **本轮不改前端**：改 `miniapp/**` 会使已上传的 `0.6.0` 代码包失效、要重新上传，
在云端服务未恢复前是无效动作；该缺口服务恢复后单独排期。

## 4. 分档结论

| 层 | 状态 | 依据 |
| --- | --- | --- |
| 环境付费 / 隔离 | ✅ 已恢复 | `402 ENV_ISOLATED` 消失（HO 续费生效） |
| 服务 / 默认域名 | ⛔ 未恢复 | 旧域名全路径 `404 INVALID_HOST`，与「不存在的 host」负控同形 |
| 小程序 `callContainer` | ⛔ 失败 | HO 手机截图「云托管调用失败」；找不到 `envId + X-WX-SERVICE` 目标 |
| 登录 `/auth/login` | ⛔ 失败 | 请求根本没到后端 |

⛔ **不是代码 bug、改仓库代码修不好**；也⛔ **不是「代码没部署」**——
网关层拦截，容器未收到请求，不要据此重复合并 / 部署代码。

## 5. 能力边界与「可代办」状态

- 11-08 判定「无浏览器自动化通道」**只覆盖 Python 侧**；本轮复核发现
  09-23 的历史通道仍在：Node `playwright-core@1.60.0-alpha`（位于
  `C:/Users/Administrator/AppData/Roaming/npm/node_modules/@playwright/cli/node_modules/`）
  ＋ 持久化 profile `E:\PL-Ship-Broker\.workbuddy\abprofile`
  ＋ `tmp/cr_login.js` / `cloudrun_probe.js` / `cr_env.js` / `cr_envvars.js`
  ＋ `C:\Program Files\Google\Chrome\Application\chrome.exe`。
- `node tmp/cloudrun_probe.js` 实测：页面跳回 `https://cloud.weixin.qq.com/cloudrun`
  营销首页，`NEED_LOGIN=true` / `IS_CONSOLE=false` ⇒ **控制台登录态已过期**。
- 已启动 `node tmp/cr_login.js`（后台）等待 HO 扫码，每 120 秒刷新二维码截图
  `tmp/cr_qr.png`；扫码成功后脚本会 dump 服务列表并截 `tmp/cr_service_list.png`。
- ⛔ **扫码前不宣称已代办**：未读服务列表、未改环境变量、未触发重部署。
  续费本身是账号支付动作，只能 HO 完成。

## 6. 待控制台确认（决定要不要改代码）

1. 环境 `prod-d0ga9bxi6e4226222` 是否仍存在、**ID 是否变化**；
2. 服务 `pinglu-backend` 是否还在、实例数、流水线（`develop` / `backend` / `8000`）绑定；
3. 新的默认域名；
4. 若服务被回收，`DATABASE_URL` / `JWT_SECRET_KEY` / `APP_ENV` 等环境变量是否还在
   —— 09-23 部署记录对口令值只留占位符 `***`，真实值仅存在于控制台。

⇒ 若仅服务被回收：重建 / 重新部署即可，**代码不用改**；
⇒ 若环境 ID 变化：还需同步 `miniapp/config/env.js::CLOUD_ENV_ID` 并**重新上传代码包**。

## 7. 本轮判据升级（已入 PR）

`backend/scripts/check_demo_readiness.py` 新增 `gateway_diagnosis`：
`402 ENV_ISOLATED` → 续费；`404 INVALID_HOST` → 重建 / 重部署服务或更新 `CLOUD_ENV_ID`；
并打印「⛔ 这不是代码没部署，不要据此重复合并/部署代码」。
`backend/tests/test_demo_readiness.py` **9 → 13 条**（含正控、负控、未知 code 不冒充已知诊断），
本机 pytest 13/13 PASS、ruff check / format --check 按 CI 口径通过。

## 8. 复现命令

```bash
python -X utf8 backend/scripts/check_demo_readiness.py
python -X utf8 backend/scripts/check_demo_readiness.py --base-url https://<新域名> --json
```
