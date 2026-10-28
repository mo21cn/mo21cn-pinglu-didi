# 环境与出处（⑮ 章设备证据 · 2026-10-26）

本目录**逐字节**保存 2026-10-26 一轮真机走查的产物。目的不是"证明功能能跑"，
而是让**别人能复核**：说清哪个读数、来自哪条命令、在什么环境、哪些是自证、哪些是转述。

## 1. 命令（以产物自证为准）

`summary.json` 的 `argv` 字段（机器写入、不可事后编辑）为：

```
scripts\verify_miniapp_devtools.py --shots E:\pinglu-didi\miniapp-device-artifacts\walk-20261026-075640 --section 15
```

⛔ **外层入口没有被写进任何产物**。产出这批文件的那次调用据称是

```
python scripts/run_walkthrough_devtools.py --section 15 --skip-ide
```

—— 这是**转述**，本目录内的文件无法自证。能自证的只有上面那条内层 argv，以及
`_walk_075640_out.txt` 里"后端 8000 应答正常"（说明当时后端在跑；`verify_miniapp_devtools.py`
自己**不会**起后端，起后端的是外层 runner）。复核时请以 argv 为准。

## 2. 运行时（这一格是本品最大的短板）

| 项 | 值 | 自证性 |
| --- | --- | --- |
| 运行期 `wx.getSystemInfoSync().SDKVersion` | 据称 **3.17.2** | ⛔ **产物内无记录** |
| 项目 `project.private.config.json` → `libVersion` | `3.17.2`（mtime 07:55，早于本轮 07:57） | 旁证 |
| IDE `WeappVendor/cfg.json` → `notGrayList` 含 `3.17.2` | 是 | 旁证（cfg mtime 07:57:04，恰在本轮） |
| `RUNTIME_PROFILE` | `dev` | ⛔ 无记录；仓库当前为 `demo`（已还原） |
| `--llm-mock` | 未使用（argv 内无此参数） | ✅ argv 自证 |

⚠️ **"3.17.2 稳定版"这条结论目前只有旁证，没有产物自证**：`summary.json` / stdout / 截图
里都**没有**任何一处写着基础库版本。本条已通过改代码关闭（`verify_miniapp_devtools.py`
新增 `capture_runtime()`，此后 `summary.json` 自带 `runtime.SDKVersion` 与 `runtime.libVersionGray`），
但**对已产出的这一批无效**。复核者有理由要求重跑一轮带版本自证的。

## 3. 被复核推翻的两条转述

| 转述 | 实测 | 影响 |
| --- | --- | --- |
| "3.17.3 不在 `notGrayList` ⇒ 退到 3.17.3 仍是灰度" | 按 10-21 的旧 `cfg.json` 成立（该快照 `list` 仅 8 条）；按 10-26 生效那份**不成立**（`list` 48 条，3.17.3 与 3.17.4 都在表内） | 结论（3.17.2 是稳定版）不变；但"3.17.2 是**最新**稳定版、使用率最高"不成立 —— 当前清单里 `usageRateList[0]` 是 3.17.4（84.38%），3.17.2 的 85.3% 出自 10-21 旧快照 |
| "安全中心的拦截已不再生效，无需改设置" | **在本会话仍生效**（见 §4） | 见 §4：这不是"要不要改"的问题，而是"改了才能由本会话复跑" |

## 4. 本会话无法复跑的原因（如实记录）

同一台机器、同一个 IDE 在跑，本会话执行 ⑮ 章时被拦在其入口：

```
Blocked by Security Center rules:
  read  D:\Program Files (x86)\Tencent\微信web开发者工具\icudtl.dat
        (rule: D:\Program Files (x86)\Tencent\微信web开发者工具\icudtl.dat)
  write D:\Program Files (x86)\Tencent\微信web开发者工具\debug.log
        (rule: D:\Program Files (x86)\Tencent\微信web开发者工具\debug.log)
```

⇒ 是**两条文件级规则**（不是整个安装目录被拒）命中，命中点正好是：
`icudtl.dat` 是 Electron 启动必需，`debug.log` 是 IDE 写入所需。
`verify_miniapp_devtools.py` 的驱动层用 IDE 自带 Electron 当 Node 运行时
（`ELECTRON_RUN_AS_NODE=1`），所以**换 Node 直跑 CLI 也绕不开 `icudtl.dat`**。

失败轮次留在 `miniapp-device-artifacts/walk-20261026-080857/`（未入本目录：它不是证据，是阻塞记录）。

## 5. 哈希口径

`docs/entrust/evidence/.gitattributes` 里是 `* -text` ⇒ 本目录文件**不做任何行尾/编码转换**，
所以下表 sha256 既是磁盘字节哈希、也是**入库 blob 字节哈希**，两者一致。

（`summary.json` 内没有内嵌哈希，本文件是唯一的哈希登记处。）
