# DEMO-1 交付包（可评审）

> 用途：HO 终验的**单一入口**。本文件只做**汇总与索引**，⛔ 不产生新结论：
> 每一行都能指到证据或对应的里程碑文件。
> 判定口径与逐行结论以 `DEMO-1-acceptance.md` §2 为准；环境与夹具以 `DEMO-1-runbook.md` 为准。

---

## 1. 候选与 CI（可复跑）

| 项 | 值 |
| --- | --- |
| **固定候选 SHA** | `9addfe8`（`develop`，PR #184 squash）＋ 本轮收尾提交（见 §7 变更记录） |
| **CI** | 候选上 **6/6 job SUCCESS**：后端 lint+test／迁移 SQLite／迁移 MySQL 8.0／并发集成 MySQL／前端静态契约／前端端到端 |
| **本机门禁** | `python -X utf8 scripts/verify_local_gates.py` ⇒ `{"total": 16, "failed": 0}` |
| **后端单测** | pytest **1015** 项（通过 998／跳过 17／失败 0／错误 0） |
| **证据入口** | `docs/entrust/evidence/`（`* -text` 逐字节入库；「工作区 sha256 == 索引 blob」已复核） |

## 2. 启动（照抄即可跑）

```bash
# 一次性
bash scripts/setup-dev.sh

# 后端（端口 8000 是约定，不是随意值：前端 BASE / 两份种子 / 走查脚本都写死它）
cd backend && uvicorn app.main:app --reload

# 小程序
微信开发者工具打开 miniapp/
```

环境变量与「关闭 `ENTRUST_ENABLED` 必须 404 而不是降级」的口径见 `DEMO-1-runbook.md` §3。

## 3. 复位（D1-17：一次真实复位演练）

```bash
cd backend
python -X utf8 scripts/reset_demo_env.py --db <隔离库绝对路径>.db --selftest --report <报告>.json
```

* `--selftest` 跑的正是 DR-0018 §6 那条判据：**基线 → 改变状态 → 复位 → 再取基线**，两轮比对。
* 安全闸（`--db` 缺失／非 SQLite／指向开发库／在仓库工作树内／`APP_ENV=production` ⇒ 退出码 2）见 runbook §6.3。
* ⚠️ **附件一致性与客户端身份缓存**两面的结论是 `LIMITATION` / `NOT_APPLICABLE`（不是 PASS）——
  原文在 runbook §6.3，⛔ 不在这里美化。
* ⭐ **「新建主链」与「预置检查点」来源可区分**：见 §5.3 的读数。

## 4. 13 步现场脚本（主链）

```bash
# 起干净实例（一次性；之后一律 --skip-ide 复用，⛔ 不要另起实例）
python -X utf8 scripts/run_walkthrough_devtools.py --prepare-ide --kill-all-ide --keepalive 10800

# 主链：第 1–13 步落在**同一张新委托**上（顺序不可打乱）
python -X utf8 scripts/run_walkthrough_devtools.py --skip-ide --pay --chain \
  --min-keepalive-left 3600 \
  --section 43,chain4,45,44,49,50,chain9,55,52,chain11,53,chain12 \
  --extra-seeds seed_entrust_canonical.py,seed_entrust_contract_flow.py,seed_entrust_completion_ready.py
```

⚠️ **`--extra-seeds` 不是可选项**：`49`（合同派生）、`50`（货量变更的 900 吨候选基线）、
`53` 的「齐备⇒结案成功」都按标题找**夹具单**。2026-09-20 实测：漏掉它会连带触发两处
脚本缺陷（已修，见 §7），并且 49/50 找不到载体。

**为什么必须是这个顺序**：每一步都是下一步的**业务**前提（表见 `DEMO-1-runbook.md` §8.9）。
`chain9`／`chain11`／`chain12` ⛔ 不能单独跑或提前跑（要求本单已完成前序步骤）。

## 5. 证据（按 D1-18 的 Required evidence）

### 5.1 完整主链（第 1–13 步同单）
见 §6 的 `D1-13` / `D1-15` 行与 §7 的读数；原始件在
`docs/entrust/evidence/2026-09-20-chain-1-13-fixed/`（stdout ＋ summary ＋ 截图）。

### 5.2 结案门槛（不齐备拒 / 齐备成 / 重开留痕）
**同一张单**两段证据：`chain11 ①b`（清理**之前**点结案 ⇒ 被拦 ＋ 缺项逐条可读 ＋ 状态未变）
＋ `53 ⑧/⑨`（齐备 ⇒ `completed` ＋ 留痕 ＋ 受控重开）。可组合既有 UI／服务／MySQL 证据。

### 5.3 无模型人工通路（D1-05）与复位（D1-17）
见 §6 对应行。**证据**：`docs/entrust/evidence/2026-09-20-manual-path/`（无模型轮）＋ `docs/entrust/evidence/2026-09-20-reset-drill/`（演练报告 ＋ 标签读数）；索引见 `DEMO-1-evidence-index.md` **§3.10**。

## 6. 演示边界（⛔ 明示，不藏）

| 边界 | 说明 |
| --- | --- |
| **前端静态契约** | CI 6/6 已含；本机 8 个脚本全绿 |
| **接口辅助** | 任务处置、案件关闭、费用行的 `quantity`、运力确认的前置：**经接口**（界面无入口或表单无该入参），读数里逐条标注 |
| **原生文件选择器** | 走查工具**够不到 OS 弹层** ⇒ 记为 `LIMITATION`，界面另有「内置样本」通路承担该步证据；⛔ 不为它扩建 OS 自动化 |
| **模拟签署 / 样本收付** | 合同「不构成实时电子签署」的常驻声明在页面上；收付依据标 `labeled_sample` |
| **模型模式** | 每轮必须写明真模型／fixture（`--llm-mock`）；两种模式的读数**不可互相冒充** |
| **上传失败（㊸）** | **保留待查**：`accessSync` 只证样本文件不存在，⛔ 不据"实例轮次"把 `FAIL` 改判 `LIMITATION` |

## 7. 变更记录（本轮）

| 提交 | 内容 |
| --- | --- |
| `337599d` | 剧本四项整改：`chain12` 纯读（＋`WRITE_LOG` 零写入正控）／D1-15 改运力复核／`case_revision` 取不到就停写／`㊿ ④a` 两通道并排＋正控／结案负例前置到 `chain11 ①b` |
| `1d81db4` | **D1-15 由 `PASS` 降回 `部分`**（撤回不实读数）⇒ 计数 **14／4 → 13／5**；补登 X3 漏记的第 9 条 FAIL；归档保活释放日志 |
| `ad60e81` | `.gitignore` 例外：证据目录里的 `.log` 不再被 `*.log` 静默挡住 |
| `e8b0f95` | 五处同类**裸下标**（`hit[0]`）——B1 轮实测 `49`/`50` 崩溃 ⇒ 连锁 19 条 FAIL；＋ 新增 `chain-manual` 章（D1-05 人工通路） |
| `7fa1939` | 给「人工转录」两个入口补 `data-act-*` 锚点（此前只有 `bindtap`，工具点不到） |
| **本文件所在提交** | `chain-manual` 按实测定型（判据改为看 job 的 `errorKind`）＋ 交付包 ＋ 证据归档 |

## 8. 待 HO 终验

* **D1-18**：本包（§12 全部文件）＋ **HO 验收记录** ← 后者**尚无**，本包目的就是让它可签。
* 当前结论：`partial preview`（**不是** `DEMO-1 已验收`）；D1 逐行 **达 PASS 16／未达 PASS 2**（未达：**05** 差「提取失败载体上的人工转录实测」；**18** 待 HO 签署）。
