# ⑮ 章设备证据（MCP 通道首轮）

| 项 | 值 |
| --- | --- |
| 采集时间 | 2026-10-26 09:28:10 +0800 |
| 产物目录 | `miniapp-device-artifacts/walk-20261026-092810`（本目录为逐字节副本） |
| 通道 | **mcp**（`summary.json` → `channel`） |
| 判定 | PASS=30 / FAIL=0 / ENV_BLOCKED=0 / REVIEW_REQUIRED=0 / NOT_RUN=0 |
| 运行期 console error | 本轮增量**为空** |
| 断言范围 | ⑮ 发货及关务方式选择：三卡文案与顺序、几何对齐、单选圈、点「智慧关务」只弹说明层不导航、说明层法务口径、委托发货→受理屏（UI-07）与带过去货名/货量、返回二级页栈 |

## 环境自证（不是转述）

`summary.json` 的 `runtime` 是本轮**实测**值（来自 `automation_runtime_info --action systemInfo`）：

```json
{"SDKVersion": "3.17.2", "model": "iPhone 12/13 (Pro)", "platform": "devtools",
 "system": "iOS 10.0.1", "language": "zh_CN", "profile": "dev",
 "libVersionCfg": {"sharedPresent": true, "shared": "3.17.2",
                   "privatePresent": true, "private": "3.17.2"},
 "libVersionGray": {"verdict": "stable", "cfgLibCount": 48,
                    "source": "...\\User Data\\bf938c...\\WeappVendor\\cfg.json"}}
```

- `SDKVersion` = **3.17.2**，与 `project.config.json` 钉的版本一致；灰度判定 `stable`
  （依据当前生效的 `cfg.json`，48 个版本，`notGrayList` 含 3.17.2 / 3.17.3 / 3.17.4）。
- `profile` = `dev`：本机走查必须用 `dev` 档（`demo` 会让小程序走云托管生产库，
  种子在本机 sqlite ⇒ 个人维度页面全空）。跑完已改回 `demo` 并复核 `git status`。

## 怎么跑的

```
python scripts/run_walkthrough_devtools.py --section 15 --skip-ide
```

- runner 负责起后端并播种；`--skip-ide` 表示复用已在运行的 IDE 实例。
- **通道是 MCP**：驱动层 `scripts/wechatide_mcp.py` 直接 `POST http://127.0.0.1:<.ide>/mcp`
  + `Authorization: Bearer <token>`，**不启动任何 exe、不读 IDE 安装目录**。
  因此本轮与安全中心的文件规则（`icudtl.dat` / `debug.log` / 各 `.exe`）**完全不发生关系**。

## 与 09:20 那一轮的关系

09:20 那轮（`walk-20261026-092050`）同样 PASS=30，但 `summary.json` **没有** `channel` 字段
（该字段是本轮才加的），只能间接判断通道 ⇒ 本目录这一轮取代它作为正式证据；
09:20 那轮留作"同章节第 4 次 PASS"的重跑关联记录，不删除。

## 复核方法

```
python backend/scripts/check_evidence_integrity.py    # 哈希与索引登记一致性
```

截图与 `summary.json`、`walk-15-stdout.txt` 一起构成证据三元组：**产物 + 原文 + 自证环境**。
缺 `runtime` 的旧批次只能算旁证（见 `2026-10-26-section-15-lib-3.17.2/env-provenance.md` 的分档说明）。
