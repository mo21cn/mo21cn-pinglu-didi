# D1-03 live 证据（**真实会话／作业入口**）· 备注

| 字段 | 内容 |
| --- | --- |
| 证据文件 | `D1-03-live-session-20260917T115436.json`（同目录） |
| 生成方式 | `backend/scripts/demo1_d1_03_live_session.py` —— 经 **HTTP API 的真实会话／作业入口**，不是直接调 `run_agent` |
| 入口链 | `POST /assignments` → `submit` → `claim` → `POST /attachments`（真 multipart）→ `POST /extract` → `POST /entrustments/{eid}/sessions` → `POST /sessions/{sid}/jobs` → `POST /agent/jobs/{jid}/run` → `POST /agent/jobs/{jid}/adopt` |
| 输入 | `backend/scripts/fixtures/DEMO1-canonical-sample-quotation.txt`（canonical 夹具，合同 §3.1；与主演示第 2 步、CI 端到端**同一份**） |
| 运行环境 | 临时 SQLite 库（`_q23_live_*.db`，工作区，不入库）+ 演示种子；后端由同一进程 `Popen` 起、**跑完即收**（本机纪律：起后端的进程一结束，uvicorn 就被回收） |
| 日期 | 2026-09-17 |

> 与 `D1-03-live-ag02-notes.md`（08:48 那次）的分工：那次用的是**独立脚本**（自己造上下文、
> 不读库），这次走**真实会话／作业入口**（库里真留下会话、作业、成果、附件四行）。
> 两者都算 live，但回答的问题不同：一个问"模型对这份报价单行不行"，
> 一个问"**在真实会话里跑一次**行不行"。

## 1. 六个 id（D1-03 要的 `job/message/artifact IDs`）

| 对象 | id | 说明 |
| --- | --- | --- |
| `assignment_id` | **3** | 本次现建的委托（提交 → 受理，`claimed_by=2`） |
| `entrustment_id` | **1** | 由 `GET /assignments/{id}/session-context` 解析出的授权 |
| `session_id` | **1** | `agent_02` 专属会话（建会话时带 `assignment_id`） |
| `job_id` | **1** | 作业（"提交"与"执行"分离：先 `jobs`，再 `jobs/{id}/run`） |
| `attachment_id` | **1** | canonical 夹具，2674 bytes → 提取 **1228 字** |
| `artifact_id` / revision | **7 / v1** | 采纳后的成果（提案 ≠ 成果，过了人一遍） |

⚠️ id 取自**临时库**，重跑会变。证据的价值不在数值，而在"**同一次运行里这六个 id 对得上**"。

## 2. 这次运行**成立**的部分

- **真模型**：作业行 `mocked=false`，`minimax / MiniMax-M2.7`，尝试 `latency_ms=20515`（单次推进 20.7s）。
  判据取**作业行**，不取调用方 env（见 §4 的"弃用"一条）。
- **类型化提案**：`quote_parsed` 一份，`proposal_missing_fields` 空。
- ⭐ **来源可核对**：`source_kinds=['attachment_text']`、**`unverified_sources=[]`**
  ⇒ **PR #131 的提示词契约修正在真实会话里生效**。
  对照 08:48 那次（同一条链、同一份夹具）：模型把来源写成 `#1 文件名（text/plain）`，
  服务端目录里的合法 ref 是纯 id ⇒ 核对不上、被标记。**本次未复现。**
- **读的是附件文本**：来源 kind 是 `attachment_text`（只写 `attachment` 只表示"附件存在"）；
  上传后**先提取**才有文本，顺序在证据里可核对（`not_requested` → `done`）。
- **落成成果**：采纳 → `artifact_id=7`、`revision_no=1`。

## 3. 一处**观察**（不阻塞，但值得记）

同一次任务在 live 与 fixture 两条通路上的**输出形态不同**：

| 字段 | live（本次） | fixture 规则模板 |
| --- | --- | --- |
| `quantity` | `"800"` | `"800.00"` |
| `route` | `"南宁 → 贵港"`（带空格） | `"南宁→贵港"`（规范化） |
| `includes` / `excludes` | 模型归纳的整句 | 规则模板无此两项 |

⇒ 两条结论：① **live 与 fixture 互不替代**，不是口号 —— 连字段形态都不一样；
② 页面与投影对这两种形态都要能显示（不能假定"小数两位"或"箭头无空格"）。

## 4. 两次调用 / 一次弃用（如实记）

- 本次共发起 **2 次真实调用**（都经真实会话／作业入口），两次结果一致：
  `unverified_sources=[]`、`source_kinds=['attachment_text']`、`mocked=false`。
- 其中 **1 次的驱动脚本有 env 传递缺陷**（`DRY` 的覆盖写在本地 dict 上，而 `W4.start()`
  内部会再调一次 `build_env()` ⇒ 覆盖传不到后端；名义上是桩、实际跑了真模型），
  它的证据文件里 `llm_mock_in_this_process` 字段因此**与作业行自相矛盾** ⇒
  **该文件已删除**，脚本字段改名为 `llm_mock_in_caller_process` 并写明"**它不是判据**"。
  正式证据取修正后的那次。**不把自相矛盾的文件留在证据目录里。**

## 5. 这份证据**不是**什么（合同 §3.2 / §9）

- **不是 D1-03 通过**：D1-03 还要「可见的 job 状态 + 会话 UI」，那部分是 ㊸ 章在**设备上、
  fixture 模式**下取的证；两条证据**各自成立、互不替代**。
- **不是模型质量结论**：H7a 的 `calibration` 仍记**未达标**；本脚本不产生任何准确率。
- **不是 R1 验收**，不能替代任何一条 D1。
- **不是 D1-05（离线/人工可完成）的证据** —— live 与 offline 互不替代。
- **不是真实航线、真实运力或真实成交**的证据：输入是**标注为合成**的夹具。
- **不是稳定性证据**：2 次运行只说明"这两天这两次是这样"，不等于模型输出稳定。
