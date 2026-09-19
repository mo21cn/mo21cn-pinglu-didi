# DEMO-1 证据索引（Evidence index）

> 合同 §12 要求的第 8 份仓库内交付物。本合同条款允许**大型录制品放在 Git 之外**，
> 但必须给出**稳定、可授权的引用**；文本日志与关键截图**直接入库**。
> ⛔ 本文件不含口令、访问令牌、模型 Key，也不含与本任务无关的客户数据
> （入库前已对 `sk-` / `Bearer` / `token=` / `Authorization` / `api_key` / `SECRET`
> 逐项扫描，仅有两处**假阳性**：`task-5` 含 `sk-`、`token=已切到 owner` 是状态说明）。

## 0. 怎么读这张索引

每条证据给四样东西：**候选版本**、**命令**、**环境与模型模式**、**可打开的原始文件**。
「档」用与 `DEMO-1-readiness.md` 相同的四档：已实现／已自动化验证／**已设备运行**／仍待验收。

⛔ **一条纪律**：本索引只登记**实际发生过**的运行。开发者报告（口头或聊天里的读数）
一律标注「开发者报告，未经 HO 核验」。

---

## 1. E-2026-09-19-A — 全量 `--section all` 首跑（**全量分章回归**，不是主链）

| 项 | 值 |
| --- | --- |
| **档** | **已设备运行**（47 章 / 787 断言） |
| **性质（⛔ 不得误读）** | 这是**全量分章回归首跑**，**不是**「同一张新委托走完 13 步」。各章各自挑委托（部分受 `--anchor` 偏好影响）⇒ 其结果**不能拼成**主链证据。发包方 2026-09-19 评估已明确此定性 |
| **候选版本** | `dc3e711`（`#174` squash；本轮使用的走查脚本内容与它**同一棵树** —— 运行开始时这些改动尚未提交，事后以 `#174` 合入） |
| **命令** | `python scripts/run_walkthrough_devtools.py --section all --pay --extra-seeds seed_entrust_canonical.py,seed_entrust_contract_flow.py` |
| **时间** | 2026-09-19 `17:34:45` 起（产物目录名）～ 约 `18:46` 止 |
| **环境** | Windows；仓库 `E:\pinglu-didi`；`.venv`（CPython 3.13）；模拟器＝微信开发者工具（wechatide 工具链）；数据库＝**runner 自建临时 SQLite**（非生产库） |
| **模型模式** | ⚠️ **未设 `LLM_MOCK`**（`run_walkthrough_devtools.py` 里 `LLM_MOCK` 出现 **0** 次），且本机**无 `backend/.env`、无 `LLM_API_KEY`** ⇒ 按 `backend/app/agent/llm.py` 的**显式降级**走 mock 规则模板。⇒ 属 **fixture 级证据**，⛔ 不得据此声称"跑过一次真实模型会话" |
| **逐步结果** | 见下方「原始文件」中的 `walk-all-stdout.txt`（每步一行，含判据与读数） |
| **汇总** | `PASS=750` / `FAIL=11` / `NOT_RUN=9` / `LIMITATION=17` / `ENV_BLOCKED=0` / `REVIEW_REQUIRED=0`；运行期 console error **0** |
| **覆盖边界** | ⛔ 本章循环当时**不含** `53` / `54`（`DEFAULT_ORDER` 静默漏项，已修）⇒ **第 12 步与十三步产物链不在本轮内**；产物里 `53`/`54` 各出现 **0** 次 |

### 原始文件（已入库）

| 文件 | 大小 | sha256(前 16) | 说明 |
| --- | --- | --- | --- |
| [`evidence/2026-09-19-walk-all/walk-all-stdout.txt`](evidence/2026-09-19-walk-all/walk-all-stdout.txt) | 141,738 B | `a60e1d3a46b8ab03` | 走查**完整 stdout**（含每步 `PASS/FAIL/NOT_RUN/LIMITATION` 与读数、汇总节、截图目录、重跑关联） |
| [`evidence/2026-09-19-walk-all/backend-access.log`](evidence/2026-09-19-walk-all/backend-access.log) | 1,105,018 B | `cbb03f60aca21631` | 后端**访问日志**（uvicorn access log，按 `===== HH:MM:SS 起后端 =====` 分段）。⭐ 它是 `㊸` 上传链定位的第一手材料：该段（`assignments/38` 的 `submit` → `claim` → `session-context`）**没有** `POST /entrust/attachments` |

### 关键截图（已入库，取自 `walk-20260919-173445/`）

| 文件 | sha256(前 16) | 对应 |
| --- | --- | --- |
| [`shots/43-5-专属会话屏.jpg`](evidence/2026-09-19-walk-all/shots/43-5-专属会话屏.jpg) | `dfb374ee36fb4400` | 步 1–3 · 进入会话屏（上传前） |
| [`shots/43-6-附件已上传并提取.jpg`](evidence/2026-09-19-walk-all/shots/43-6-附件已上传并提取.jpg) | `650f4f8e2f5a920f` | ⭐ **`㊸` 失败现场**：与 43-5 画面几乎无差异（`25253` vs `25260` B）⇒ 点击后页面**没有进入上传态** |
| [`shots/15-委托发货-功能预览.jpg`](evidence/2026-09-19-walk-all/shots/15-委托发货-功能预览.jpg) | `cf8c9fd91baac060` | ⭐ `⑮` 失败现场：真机落点＝**受理屏**（断言当时仍写「功能预览」占位页 ⇒ 过期断言） |
| [`shots/25-A-成果详情-查看态.jpg`](evidence/2026-09-19-walk-all/shots/25-A-成果详情-查看态.jpg) | `c6915133e814cc2e` | `㉕B` 查看态（编辑前的界面） |
| [`shots/25-B-成果详情-编辑态.jpg`](evidence/2026-09-19-walk-all/shots/25-B-成果详情-编辑态.jpg) | `fc274cec1b294149` | ⭐ `㉕B/C/D` 失败现场：`.btn-primary` 点到「发布」提交键 ⇒ **未进入编辑态** |
| [`shots/45-5-确认成功.jpg`](evidence/2026-09-19-walk-all/shots/45-5-确认成功.jpg) | `9badc58c5f596b04` | **第 5 步**（运力确认闭环）通过 |
| [`shots/52-财务与结算.jpg`](evidence/2026-09-19-walk-all/shots/52-财务与结算.jpg) | `0afc2567e0f4d506` | **第 10–11 步**（费用→缺件→结算→客户确认→结清）通过 |

### Git 之外的大件（附引用方式）

| 资产 | 体量 | 位置与获取 |
| --- | --- | --- |
| 本轮**全部截图** | 146 个文件 / 4.9 MB | 开发机 `miniapp-device-artifacts/walk-20260919-173445/`（`.gitignore:67` 排除）。**入库的 7 张是关键子集**；需要全集时由开发者打包提供（⛔ 该路径本身**不构成**交付） |
| 本轮临时数据库 | — | runner 每次新建、跑完即弃 ⇒ **不可复现取证**，请以 `walk-all-stdout.txt` 的 API 直证读数为准 |

---

## 2. E-2026-09-19-B — `--section 53,54`（第 12 步 ＋ 十三步产物链）

| 项 | 值 |
| --- | --- |
| **档** | **已设备运行**（章 53 `×24` 全 PASS；章 54 读侧 `PASS=9 / NOT_RUN=1`） |
| **命令** | `python scripts/run_walkthrough_devtools.py --skip-ide --section 53,54 --extra-seeds seed_entrust_canonical.py,seed_entrust_contract_flow.py,seed_entrust_completion_ready.py` |
| **产物** | `_walk_090034_out.txt`（章节 `53,54`，34 断言）＋ `_walk_124045_out.txt`（章节 `50,54`，27 断言，`54 ⑤` 转 PASS 那一次） |
| **⚠️ 登记口径** | 这两份**尚未入库**（当时未按 §12 收口）⇒ 现按**开发者报告**登记，**未经 HO 核验**。补齐方式见本文件末尾「待补」 |

---

## 3. E-2026-09-19-C — 自动化与静态门禁（可复跑）

| 项 | 值 |
| --- | --- |
| **档** | **已自动化验证** |
| **本机门禁** | `python -X utf8 scripts/verify_local_gates.py` ⇒ `{"total":16,"failed":0}`；后端 pytest **1015** 项（通过 998 / 跳过 17 / 失败 0 / 错误 0） |
| **CI** | PR `#176`：6/6 job **SUCCESS**（后端 lint+test / 迁移 SQLite / 迁移 MySQL / 前端静态契约 / 前端端到端 / 并发集成 MySQL） |
| **命令与环境** | 见 `DEMO-1-runbook.md`；CI 定义以 `.github/workflows/ci.yml` 为权威 |

---

## 4. 待补（本索引自己的缺口，如实登记）

| # | 缺口 | 处置 |
| --- | --- | --- |
| 1 | **E-B 的两份原始产物未入库** | 下一轮随主链 PR 一并入库（或按附条件引用） |
| 2 | **本轮的"全量"不含 53/54** | 已修 `DEFAULT_ORDER`；**主链跑通后**做一次真正的全量回归（发包方裁定 P2） |
| 3 | **合同 §12 另有两份交付物尚未建立** | `DEMO-1-walkthrough.md`（主脚本 / 角色 / 预期结果 / 诚实回退）与 `DEMO-1-acceptance.md`（D1 逐行结果 ＋ 候选 SHA ＋ CI ＋ 限制 ＋ HO 终裁）⇒ 待主链证据成形后一次写全，避免先写一份再推翻 |
| 4 | **同一张新委托的 13 步主链** | ⛔ **尚无任何证据**。这是当前主任务，见 `DEMO-1-readiness.md` §10.2 的「本轮执行结果」栏 |
