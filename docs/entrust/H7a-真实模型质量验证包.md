# H7a：真实模型质量验证包

| 字段 | 内容 |
| --- | --- |
| 编号 | H7a（DR-0005 未覆盖项 ①） |
| 状态 | **验证包已就绪；首次真实验证 NOT_RUN —— 阻塞在「这把凭证挂不到可用资源」，既非模型质量、也非"没买订阅"（见 §5.1）** |
| 日期 | 2026-09-16 |
| HO 裁决 | 「准备验证包，暂不宣称通过；先完成样本、参考答案、评分规则、模型与费用预算；资源就绪后独立验证。fixture 继续服务 CI」 |

## 1. 它验证什么、不验证什么

`LLM_MOCK=true` 的规则模板只保证**输出结构与真实模式同构**，它证明不了真实模型
**好不好用**。CI 继续由 fixture 覆盖（无 Key、无网络依赖），本验证包是**CI 之外**
的独立质量测量。

- ✅ 验证：货源解析（`agent/service.py::parse_cargo`）在真实模型上的**逐字段准确率**、
  **是否编造**、**置信度有没有区分度**、**延迟**与**多轮一致性**。
- ❌ 不验证：撮合／支付／状态机等确定性内核（架构红线：它们不经 LLM）；
  也不验证 Agent 作业（ag01/ag02）的信封质量 —— 那需要 DB 夹具，列为下一步。
- ❌ **不代表 PRD 验收通过**：本包产出的是"测量结果"，不是"合格证"。

## 2. 位置与用法

```
backend/scripts/llm_key_doctor.py        # Key 体检（只读）：分清「账户有资源」与「这把 Key 能花到那笔资源」
backend/scripts/h7a_samples.json         # 样本 + 参考答案（机器判分，不需要人工逐条看）
backend/scripts/h7a_model_quality.py     # 跑分脚本（真发请求、会产生费用）
```

Key 走 `backend/.env.local`（`.gitignore` 已排除，**禁止入库**），
因为 config 的 `env_file` 把 `.env.local` 放在最高优先级。

```bash
cd backend
# 0) 先体检：不花调用费，只读三件事（Key 是否被站点认可 / 套餐剩多少 / 能不能真调通）
.venv/Scripts/python.exe scripts/llm_key_doctor.py
# 1) 再看会花多少
.venv/Scripts/python.exe scripts/h7a_model_quality.py --dry-run
# 2) 真跑
.venv/Scripts/python.exe scripts/h7a_model_quality.py --runs 2 --out ../artifacts/h7a.json
```

**第 0 步不是可选的礼貌步骤**：2026-09-16 首次真跑 24 次全被 402 挡下，根因查了很久
（见 §5.1）—— 先花两秒体检，能省掉整轮排查。

退出码三态：`0` = 达标 / `1` = 未达标 / `2` = **没跑成（NOT_RUN）**。
`2` 与 `1` 必须分开 —— "没验成"不能倒推成"不达标"，也不能被读成"跑过了"。

## 3. 样本（12 条）

`h7a_samples.json` 的覆盖点，每条都对应一类真实风险：

| 样本 | 考察点 |
| --- | --- |
| S01 | 完整信息（基线） |
| S02 | 缺目的港 + 缺价格 → **必须置 null** |
| S03 | 缺重量 |
| S04 | 集装箱 + 万元口语（"两万五" → 25000） |
| S05 | 液货枚举（tanker） |
| S06 | 中文大写数字（"八十吨" → 80） |
| S07 | **相对日期（"下周三"）** —— 见 §6 发现项 |
| S08 | 完全无关输入 → 全 null（**不编造**） |
| S09 | 信息不足（"我想发点货"）→ 全 null |
| S10 | 域外港口（上海/广州不在 13 个枚举内）→ 正确行为是置 null，**不得自创代码** |
| S11 | 中文数字（"三百五十吨" / "四万二"） |
| S12 | 长句 + 干扰（先提"贵港→南宁"的老线路，真实线路是"崇左→贺州"） |

## 4. 评分规则与阈值（**跑之前定死，跑完不许改**）

判分写在 `h7a_model_quality.py::THRESHOLDS`：

| 指标 | 阈值 | 为什么是这个数 |
| --- | --- | --- |
| 逐字段准确率 | ≥ 85% | 12 样本 × 7 字段 = 84 次判定；低于此值解析结果需要人工逐单复核，产品上不划算 |
| 编造次数 | = 0 | 系统提示词写死「信息缺失时置 null，不要编造」，属硬要求，不折算成分数 |
| 置信度校准 | > 0.10 | 答对字段的平均置信度 − 答错字段的；差值为负说明置信度没有区分度，`needs_review` 就是摆设 |
| 调用失败 | = 0 | 输出不可解析 = 该样本完全不可用 |

字段级判定口径：港口/货类**精确匹配**（枚举内）；重量/价格**0.5% 相对容差**；
货名用**关键词包含**（避免"散装水泥"/"水泥"的差异造成假失败）；
**null 与非 null 不一致即判错**，期望为 null 却给出值额外记一次「编造」。

## 5. 当前状态：NOT_RUN（**不得宣称通过**）

2026-09-16 用授权 Key 实跑，结果：

- Key **有效**：`GET /v1/models` 返 **200**，供应商实测确认为 **MiniMax**
  （可用模型 8 个：`MiniMax-M3`、`MiniMax-M2.7`、`MiniMax-M2.7-highspeed`、
  `MiniMax-M2.5`…；在 DeepSeek / 硅基流动 / Moonshot / 智谱 / 百川 等端点均返 401，
  可排除误配）。站点为**国内站**：同一把 Key 打 `api.minimax.io` 返 `2049 invalid api key`。
- 但 `POST /chat/completions` 返 **HTTP 402 `insufficient_balance_error`（`insufficient balance (1008)`）**，
  24 次调用全部被拒 ⇒ **一条可评分的结果都没产生**。
- ⇒ H7a 仍是「验证包已备好、真实验证未执行」。**不因"验证包做完了"就记通过**；
  也不因 402 就记"模型不达标" —— 这是资源侧阻塞，与模型质量无关。

### 5.1 402 的根因：不是「没买订阅」，而是「这把 Key 挂不到那笔资源」

首版把这行记成「账户余额不足 ⇒ **充值后重跑**」。**方向是错的** —— 账户本来就有
**Token Plan Plus 月度订阅**。三行实查证据（`scripts/llm_key_doctor.py`，只读）：

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `GET /models` | **200**，8 个模型 | Key 被站点认可，不是无效 Key |
| `GET /token_plan/remains` | **200**，`general` **周窗口剩余 99%** | **账号的套餐额度几乎是满的** |
| `POST /chat/completions` | **402 / 1008** | 可真调用说"资源不足" |

三者并存只有一个解释。按 MiniMax 官方（Token Plan 概要 / 常见问题）：

> 订阅 Key 用于 Token Plan 订阅套餐和已购积分；**订阅 Key 与普通按量计费 API Key
> 相互独立，不能混用**。每位用户在所属的每个团队中都有一把专属订阅 Key。

即平台是**两套凭证、两个钱包**：

| 凭证 | 花的是 | 从哪取 |
| --- | --- | --- |
| **订阅 Key** | Token Plan 套餐额度 / 已购积分 | 账户管理 / Token Plan |
| **按量计费 API Key** | 开放平台**账户余额**（wallet） | 接口密钥页 |

手上这把是 **`sk-api-` 形态（按量计费）**，调 `/chat/completions` 就从**账户余额**扣；
余额为 0 ⇒ 402，**套餐里剩的 99% 与它无关**。
⇒ 一句话：**「账户有钱」与「这把 Key 能花到那笔钱」是两件事**。
把 402 读成"该充值"会让人去充一笔不需要充的钱。

> **形态判读的依据级别**：官方明确规定"两套 Key 互不通用"，但**未公布前缀对照表**；
> `sk-api-` / `sk-cp-` 的对应关系来自社区实践，属**旁证**。
> 最终判据是控制台上「这把 Key 是从哪一页创建的」。
> 另：`GET /v1/account/balance` 返 **403 `api key is not an admin key`**，
> 说明这把是普通（非 admin）Key —— 与"按量计费 Key"的画像一致。

### 5.2 恢复条件

1. 到 **账户管理 / Token Plan**（`https://platform.minimaxi.com/console/plan`）
   复制**订阅 Key**，替换 `backend/.env.local` 的 `LLM_API_KEY`；
2. 重跑 `scripts/llm_key_doctor.py`，看到「端到端可调用」后再跑 §2 的 H7a 命令。
   **无需改代码**；**也不需要给按量计费钱包充值**（那是另一个动作，且不是当前要做的）。

⚠️ **未解疑点（如实留白，不硬解释）**：`token_plan/remains` 里
`current_interval_total_count` 为 **0**，而同一条记录的
`current_weekly_remaining_percent` 为 **99**。两者对"还有没有额度"的口径不一致，
官方文档未说明前者的语义 ⇒ 保持**未知**。换上订阅 Key 后复测可自然澄清。

## 6. 本轮顺带发现并已修的四个缺陷

它们不是 H7a 的目标，但是"第一次真发请求"才暴露出来的：

1. **HTTP 402 被归类成 `bad_request`**（`app/modules/agent/llm.py`）。
   报出来是"请求被拒绝"，掩盖了"该充值"这个**真正要做的动作**。
   ⇒ 新增 `quota` 分类，且**不可重试**（重试不会让账户有钱），
   见 `RETRYABLE_KINDS` 与 `tests/test_entrust_agent.py::test_quota_error_is_not_retryable`。
   **顺带**：402 的用户可见文案也改了 —— 原来只说"需充值"，但查清 §5.1 后可知
   402 有两种成因（账户余额为 0 / **Key 与计费方式不匹配**），文案须同时指向，
   否则会把人推去充一笔不需要充的钱。
2. **`.env.local` 会污染测试环境**（conftest 只防了微信，没防 LLM）。
   本机为调试真模型配上 `LLM_MOCK=false` 后，
   `test_claim_next_consumes_attempt_and_execute_succeeds` 由 `succeeded` 变 `failed`
   —— 真因是本机改了配置，不是代码坏了，但现象与"并发守卫写错"同形。
   ⇒ conftest 补 `force_llm_mock` autouse（与既有 `force_wechat_mock` 同一套约定）。
3. **`scripts/h7a_model_quality.py` 缺 sys.path 自举** ⇒ 本文件 §2 那条运行命令
   **实际跑不起来**（`ModuleNotFoundError: No module named 'app'`）。
   同目录 `seed_entrust_demo.py` / `seed_entrust_orgpicker.py` 早有同样的补法，本脚本漏了
   —— 教训：**"验证包写完了"与"验证包能按文档跑一遍"是两件事**，
   后者要在干净 shell 里真敲一次才知道。
   ⇒ 已在脚本头部补 `sys.path.insert` 并逐行标 `# noqa: E402`。
4. **代码注释与结论漂移**：`_report` 里仍写着"HTTP 402，本客户端当前归类为 bad_request"，
   而 402 早已经改成 `quota`；同时 `_report` 的"常见原因"仍只说"余额不足"。
   ⇒ 与 §5.1 同批订正。判据变了，读代码的人**总是后到** —— 注释必须跟着改。

## 7. 待办（不属于本包）

- 系统提示词**没有把"今天"告诉模型**，却要求"下周三"按今天推算（S07）。
  这是 prompt 侧缺陷，不是模型质量问题；等能真跑之后再确认，修法是把当天日期注入
  system prompt（改动会影响 mock 与审计，需单独一次提交）。
- Agent 作业（ag01/ag02）的信封质量：可机器判分的维度是「引用来源必须落在
  服务端枚举的来源目录内」，但要先解决 DB 夹具。
- 费用预算：单次完整跑（12 样本 × 2 轮 = 24 次调用）输入侧约 2.2 万字符
  （≈ 1.5 万 tokens，中文按 1.5 字符/token 估），输出侧约 5 千 tokens。
  **单价未在本轮查证，不臆造** —— 重跑前请按供应商当期定价核算。
