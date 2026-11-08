# 2026-11-08 云托管环境隔离（ENV_ISOLATED）实测记录

> 执行人：Agent（只读探测）｜时间：2026-11-08 09:29–09:38（GMT+8）
> 关联：PR #211 合并（`develop=6669bfa1a284099e0df2af759a79477d81bc4a0d`，该提交 CI **6/6 success**）
> ⛔ 全程未写云端、未改 IDE 登录态、未改 `miniapp/config/env.js`、未触碰 HO 的 Chrome 进程。
> 索引：`DEMO-1-evidence-index.md` **§3.21**；交接单：`DEMO-1-live-ui-work-order.md` **E-0.11「2026-11-08 实测」节**。

## 0. HO 本轮 5 条指令与处置（如实登记）

| # | 指令 | 处置 |
| --- | --- | --- |
| 1 | 授权合并 PR #211 | ✅ **已完成**：`state=MERGED` / `mergedAt=2026-11-08T01:30:57Z`；develop 尖端 `6669bfa1`，CI **6/6 success** |
| 2 | `0.6.0` 版本已通过审核 | 事实登记：**审核通过 ≠ 已发布 ≠ 已上线**（§1）。正式发布属 HO 拍板项 |
| 3 | 云托管设置你可代办 | 授权留痕；**当前不可执行**（§6）：环境隔离 + 无凭据 / 无浏览器通道 + 续费是账号支付动作。⛔ 未代设、未重部署、不虚报 |
| 4 | 同意执行「下一步 4：B 完成后重跑 §15」 | **本轮不跑**：402 下只会得到错误页，且 B 未落地 ⇒ 无效证据。待续费 + B 完成后重跑，并给 `viewHint==''` 新守卫取正控 / 负控 |
| 5 | 同意执行「下一步 5：HO 签署后收口台账」 | **本轮不收口**：HO 评审单 §0 / §1.3 / §5 签署栏仍留白，且新增「环境隔离」硬阻塞档 |

## 1. 0.6.0 代码发布审核结果（HO 提供的截图读数）

| 字段 | 读数 |
| --- | --- |
| 页面 | 代码发布审核结果 |
| 小程序 | 智丘科技 |
| 发布审核结果 | 你的小程序代码发布**已通过审核** |
| 提交时间 | 2026-11-07 18:35:05 |
| 提交版本 | 0.6.0 |
| 审核时间 | 2026-11-08 09:22:04 |
| 底部入口 | 小程序运营专区 / **前往发布** |

⛔ 「前往发布」入口仍在 ⇒ **发布动作尚未发生**；审核通过不等于已发布、不等于已上线。
体验版「选为体验版 + 保存新码」是另一条路，不因审核通过自动完成。
提交时间 18:35:05 与 11-07 17:40 由 IDE 原生 MCP `upload` 上传开发版 `0.6.0` 的时间线吻合。

## 2. 外部只读探测：所有路径 HTTP 402 ENV_ISOLATED

命令（无凭据、只发 GET）：

```bash
python -X utf8 -c "<urllib 直连 base + 5 个路径，ProxyHandler({}) 免代理>"
```

Base：`https://pinglu-backend-318466-5-1494250212.sh.run.tcloudbase.com`

| 路径 | HTTP | body 前 120 字符 |
| --- | --- | --- |
| `/healthz` | 402 | `{"code":"ENV_ISOLATED","message":"Environment prod-d0ga9bxi6e4226222 is isolated. ...` |
| `/openapi.json` | 402 | 同上 |
| `/api/v1/customs/sources` | 402 | 同上 |
| `/` | 402 | 同上 |
| `/docs` | 402 | 同上 |

统一响应体（231 字节）与响应头：

```json
{"code":"ENV_ISOLATED","message":"Environment prod-d0ga9bxi6e4226222 is isolated. For more information, please refer to https://docs.cloudbase.net/error-code/service/ENV_ISOLATED","requestId":"<uuid>"}
```

```text
content-type: application/json; charset=utf-8
server: cbrgw
x-cloudbase-request-id: <uuid>
x-request-id: <uuid>
```

`server: cbrgw` ⇒ **CloudBase Run 网关层**返回，⛔ 不是应用代码返回的错误，也不是鉴权 401。
逐路径原文见 `cloudhost-402-responses.txt`。

## 3. `check_demo_readiness.py` 由 4/4 PASS 降级

命令：`python backend/scripts/check_demo_readiness.py`（只读 4 项，无凭据），`rc=1`。

```text
[FAIL] healthz: HTTP 402 body={"code":"ENV_ISOLATED",...}
[FAIL] customs_routes: HTTP 402 paths=0 missing=[7 条 /api/v1/customs/* 路径]
[FAIL] customs_auth: HTTP 402 body={"code":"ENV_ISOLATED",...}（期望 401 未提供访问令牌）
[PASS] runtime_profile: RUNTIME_PROFILE='demo' ...\miniapp\config\env.js
代码层已部署：否 ／ 种子层已证：否 ／ 设备层已证：否
```

⇒ 11-04 首测、11-07 复跑的 **4/4 PASS 已不是现状**；唯一 PASS 是**本地文件**读数，
与云端可用性无关。原始输出见 `check-demo-readiness-20261108.txt`。

## 4. 设备侧决定性对照：`wx.cloud.callContainer` 同样 402

⭐ 这一条排除了「只是公网直连被挡 / 只是探测脚本参数不对」的可能。

在 IDE 小程序页面上下文（`automation_evaluate`）按 `miniapp/utils/request.js::cloudRequest`
的**真实参数**直发：

```js
wx.cloud.callContainer({
  config: { env: 'prod-d0ga9bxi6e4226222' },
  path: '/api/v1/customs/sources',
  method: 'GET',
  data: {},
  header: { 'Content-Type': 'application/json', 'X-WX-SERVICE': 'pinglu-backend' },
  success: ..., fail: ...
})
```

返回：

```json
{"ok": true, "statusCode": 402,
 "body": "{\"code\":\"ENV_ISOLATED\",\"message\":\"Environment prod-d0ga9bxi6e4226222 is isolated. ...\",\"requestId\":\"fbee861b-db14-11f1-9dad-52540026ca1e\"}"}
```

⇒ **整个云托管环境不可用，`demo` 档演示手机当前连不上后端**。

⚠️ **歧义排除（避免误判）**：同期 IDE `simulator_refresh()` 后页面仍读到
`view='ready'` / `viewHint=''` / `sources=3` / `list=0` / `checkedAt='2026-11-07 18:01'`。
这是**热重载保留了旧 data**（`refresh` 不重跑 `onShow` / `home.js::load()`），
⛔ **不能**当作「云端还通」的证据。
另一次强制调 `load()` 的尝试返回 `NO_LOAD`（页面实例未暴露该方法），故改用上面的直发对照定性。

IDE 通道本身正常：`status → ok=True` / `loginExpired=false` / 登录用户「中象」，
`page_stack` 含 `pages/customs/home/home`，端口 35531 `LISTENING`。

## 5. 官方错误码释义

来源：`https://docs.cloudbase.net/error-code/ENV_ISOLATED`（本轮 WebFetch 原文）

> **错误原因**：环境已到期隔离，无法正常访问服务。可能的原因包括：
> 1. 环境套餐到期，未及时续费；2. 环境欠费。
>
> **解决方案**：1. 前往云开发控制台 `https://tcb.cloud.tencent.com/dev` 查看环境状态与到期时间，
> 确认是否为到期隔离；2. 对环境进行续费，环境恢复后再重新发起请求。

⇒ 唯一解除动作是 **HO 续费**；这是账号支付动作，Agent 不能代办。

## 6.「云托管设置你可代办」的实测能力边界

历史依据（HO 授权并非凭空）：`E:\PL-Ship-Broker\云端部署执行记录-20260923.md` 开头记

> 执行人：AI（代 HO 侧控制台操作）｜证据来源：控制台页面读数（**脚本驱动 Chrome**）＋ 外部 HTTP 探测 ＋ DMS 只读 SQL

2026-11-08 本机实测：

| 能力项 | 实测结果 |
| --- | --- |
| `cloudbase` / `tcb` CLI | **MISSING**（`shutil.which` 四种拼写均无） |
| 腾讯云 API 凭据环境变量 | **无**（只匹配到 `TENCENT_DOCS_LOCAL_MCP` / `TENCENT_DOCS_LOCAL_SERVER`，与本任务无关） |
| `~/.cloudbase` / `~/.config/cloudbase` / `~/.tcbrc` / `.cloudbaserc.json` | **均不存在**（无 CLI 登录态） |
| `tencentcloud` Python SDK | **未安装**（`ModuleNotFoundError`） |
| Chrome | 已装（x64），14 个进程属 HO 日常窗口；**9222 调试端口未监听** |
| `playwright` / `selenium` | **均未安装** |
| 云托管 OpenAPI 中的 admin / env / seed 端点 | 无法枚举（本身即 402） |

⇒ 09-23 那条「脚本驱动 Chrome」代办通道**当前不可用**；且环境隔离期间
改环境变量 / 触发重部署都不会生效（网关直接 402）。
⇒ 本轮**未代设变量、未触发重部署、未启动或改动 HO 的 Chrome**，⛔ 不虚报「已代办」。

环境恢复后，若 HO 提供腾讯云 API 密钥或开启可驱动的浏览器通道，Agent 可据此直接代办
「设两条变量 + 触发重部署」，本轮授权无需再请示一次。

## 7. 结论与边界（不合并成「上线完成」）

| 档 | 11-08 结论 |
| --- | --- |
| 代码已合 | ✅ PR #207–#211 全进主干，`develop=6669bfa1`，CI 6/6 |
| ⛔ 环境可用 | **环境隔离（402 ENV_ISOLATED），演示手机连不上后端** ← 最高优先阻塞 |
| 云端代码已部署 | ⚠️ 历史 4/4 PASS 失效，本轮「代码层已部署：否」 |
| 关务种子已铺 | ⛔ 11-07 已观测到空；本轮连重部署都不可做 |
| 设备已验 | ⚠️ 11-07 demo 档 37 项 `PASS 36 / NOT_RUN 1`，`verdict=NOT_RUN`；本轮不补跑 |
| 分发可用 | ⏳ 0.6.0 审核已过（未发布）+ 代码包已上传；体验版新码 / 正式发布仍待 HO |

⛔ 不把「审核通过」写成「已上线」；不把「授权代办」写成「已代办」；
不把 `NOT_RUN` 刷绿；不代填 HO 评审单签署栏；不在环境 402 时跑无效 §15。

## 8. HO 复跑清单（环境恢复后按顺序）

1. `https://tcb.cloud.tencent.com/dev` → 查 `prod-d0ga9bxi6e4226222` → **续费 / 解除隔离**；
2. `python backend/scripts/check_demo_readiness.py` 应回到 **4/4 PASS** 且 `/healthz` 200；
3. 云托管服务 `pinglu-backend` 环境变量新增
   `DEMO_DATA_STORE=true` 与 `SEED_CUSTOMS_OWNER=oxLE-3R72-BKYUGJ-hL7dNfmX9C0`
   （11-07 `GET /api/v1/auth/me` 实测 `user_id=2`；⛔ 仍以 HO 控制台核对为准）→ **触发重部署**；
4. 回传容器日志两行 `SEED_CUSTOMS_OWNER 已显式设置` / `关务演示数据就绪`；
5. 之后交回 Agent 重跑 `python scripts/run_walkthrough_devtools.py --section 15 --skip-ide`，
   补详情 / 归类分支证据与 `viewHint` 守卫的设备正控 / 负控。
