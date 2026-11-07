# 2026-11-07 18:01 · demo 档云端只读复测记录

> ⚠️ 本文件是**一次只读探测的读数抄录**，不是脚本自动产出。
> 目的：给 `walk-15-stdout-demo.txt` 里那条 `NOT_RUN` 补一个**独立对照** ——
> 区分「关务列表 0 条」究竟是「云端真的没有本账号可见的种子」，还是「列表请求失败被页面兜底成空态」。

## 环境与前置

| 项 | 读数 |
| --- | --- |
| 通道 | IDE 原生 MCP（`127.0.0.1:35531`），`WECHATIDE_PORT` + `WECHATIDE_TOKEN` 显式注入 |
| 档位 | `miniapp/config/env.js` → `RUNTIME_PROFILE='demo'` ⇒ `USE_CLOUD=true` ⇒ `wx.cloud.callContainer` |
| 基础库 | `SDKVersion=3.17.2` |
| 本地 8000 | **CLOSED**（走查 runner 已停止后端；`socket.connect` 超时） |
| IDE MCP 35531 | **LISTENING** |
| 登录态 | 已按走查前备份还原 HO 原值；`user_info` = `{"user_id":2,"openid":"oxLE-3R72-BKYUGJ-hL7dNfmX9C0","nickname":"演示货主","roles":["shipper","owner"],"current_role":"shipper","role_label":"货主"}` |

⛔ 本地 8000 关闭而页面仍能取到数据 ⇒ 排除「走了本地后端」这一解释。

## 设备侧读数（`navigateTo /pages/customs/home/home` 后 `page_data()`）

```
view                 = 'ready'
viewHint             = ''
officialUnavailable  = True
manualAvailable      = True
checkedAt            = '2026-11-07 18:01'
notice               = '本版本不接官方数据源：所有状态来自人工登记，以官方结果为准'
errTitle / errHint   = '' / ''
sources              = 3
list                 = 0
[data-act-cus-item="1"] 计数 = 0
```

`viewHint=''` 是关键：`miniapp/pages/customs/home/home.js::load()` 只在
`fetchDeclarations()` **抛错**时写入 `viewHint='登记记录暂时取不到（状态条不受影响）'`；
为空说明列表请求**成功返回 0 条**，不是请求失败。
`checkedAt` 是服务端 `checked_at` 经 `toLocalTime()` 转出的实时值，不是静态兜底。

## 数值层（用同一 IDE token 直连云托管）

| 请求 | 结果 |
| --- | --- |
| `GET /api/v1/auth/me` | 200 · `user_id=2` / `openid=oxLE-3R72-BKYUGJ-hL7dNfmX9C0` |
| `GET /api/v1/customs/sources` | 200 · 3 条数据源 |
| `GET /api/v1/customs/declarations` | 200 · `{"items":[],"total":0,"query":{"no":null}}` |
| 无 token 取上述三个关务端点 | 均 401 `{"detail":"未提供访问令牌"}` |

⇒ `sources` 和 `declarations` 都要求登录；`view='ready'` 反过来证明当次 token 在云端有效。

## 身份链路（为什么 `login_as("seed-shipper")` 没有把身份换掉）

`login_as()` 清 `access_token` / `user_info` 并写 `dev_login_code=seed-shipper`；
但 demo 档经 `wx.cloud.callContainer` 发请求时，微信平台注入 `x-wx-openid`，
后端 `backend/app/modules/auth/service.py::resolve_identity()` 在
`CLOUD_OPENID_TRUSTED=true` 且非 `WECHAT_MOCK` 时**优先采信平台注入身份**。
⇒ 重新登录仍落到同一真实演示账号（`user_info` 与走查前逐字一致），
`access_token` 换新只是同一用户重新签发（305→305 字节、内容不同，已还原 HO 原值）。
早先「`/auth/login` 带 `seed-shipper` 回 401」发生在**公网直连且无平台注入头**的探测里，
不是 demo 档链路。⇒ 走查里的空态读数**属于 HO 演示账号**，不是另一个没人登录的 user_id。

## 结论与边界

- B 段（云端关务种子）**当前确未铺**，这是设备侧 + 数值层双证，不是「待确认」。
- HO 需设 `DEMO_DATA_STORE=true` 与 `SEED_CUSTOMS_OWNER=oxLE-3R72-BKYUGJ-hL7dNfmX9C0`
  并重部署、回传容器日志两行；**Agent 不代设、不代触发**。
- 本记录**不构成**「设备已验」：它只是把 §15 那轮 `NOT_RUN` 的原因钉死。
- 本轮所有动作均为只读（导航 / 读 Storage / 读页面 data / GET），未写云端业务数据。
