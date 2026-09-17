# D1-03 live 证据 · 备注（本次运行的读法）

| 字段 | 内容 |
| --- | --- |
| 证据文件 | `D1-03-live-ag02-20260917T084834.json`（同目录） |
| 生成方式 | `backend/scripts/demo1_live_ag02_evidence.py`（**一次**受控真实调用，fail-closed：fixture 模式下拒绝产出） |
| 输入 | `backend/scripts/fixtures/DEMO1-SYNTHETIC-sample-quotation.txt`（标注为合成；与演示第 2 步、CI 端到端用**同一份**） |
| 日期 | 2026-09-17 |

## 1. 这次运行**成立**的部分

- 走的是**生产同一条代码路径**：`runner.run_agent` → `chat_json`（提示词由 `ag02.build_user_prompt` 生成），
  输出再经 `validate_outcome` 校验 ⇒ 信封是**被服务端接受**的，不是我手写的样例。
- **真实调用**：`minimax / MiniMax-M2.7`，`mocked=false`，延迟 13 266 ms。
- **类型化提案**：产出 `quote_parsed` 一份，`proposal_missing_fields` 为空
  （即 `carrier` / `rate` 两个必填都拿到了）⇒ 符合 D1-03 的 "typed proposal"。
- 输入来源是 **`attachment_text`（附件提取文本）** —— 与演示第 2 步"上传 → 提取 → 引用"是同一条链。

## 2. ⚠️ 这次运行**暴露的缺陷**（如实记，不掩盖）

`validation.unverified_sources` **非空**：

```
[{"kind": "attachment", "ref": "#1 DEMO1-SYNTHETIC-sample-quotation.txt（text/plain）"}]
```

- 服务端的来源目录里，附件的合法 ref 是**纯 id**（`"1"`，见 `runner.build_source_catalog`）；
- 而真实模型写出来的是**给人看的描述串**（带 `#`、文件名、MIME）；
- ⇒ 这条来源**核对不上**，被标记为"查不到的来源"。

影响与不影响，分开说：

| 面向 | 影响 |
| --- | --- |
| 作业是否失败 | **不失败**。未核对的来源是**标记**，不阻断（`validate_envelope` 的口径） |
| 演示能否照演 | **能**。页面按提案的 `payload` 渲染，不显示 `source_refs` |
| D1-03 的 "sourced proposal" | ⚠️ **打折**。合同要求的是**可核对的来源**，而当前 live 路径下这条核对不上 |
| fixture 路径 | **不受影响**。`ag02.mock_content` 写的是合法 ref，这是"live 与 fixture 会分叉"的又一例 |

## 3. 处置建议（**本轮不修**，理由见下）

修法很小：在 `ag02.SYSTEM_PROMPT` 的来源契约里写明 **`ref` 必须是服务端目录里的原样 id**
（附件给 `"1"` 而不是 `"#1 文件名"`），并补一条确定性回归把"模型给了描述串 ⇒ 被标记"钉住。

**本轮不改它**的原因：

1. 它改的是**面向真实模型的提示词契约**，会直接影响 live 输出形态 ⇒ 属于独立切片（改完要重跑本脚本留新证据）；
2. 本轮的授权范围是"上传报价单 → 提取 → 引用进 AG-02"，把提示词一起改会让"这一片改了什么"说不清；
3. fixture 路径（CI 与演示默认）**不因此受损**，不构成阻塞。

## 4. 这份证据**不是**什么（合同 §3.2 / §9）

- **不是模型质量结论**：H7a 的 `calibration` 仍记**未达标**，本脚本不产生任何准确率；
- **不是 R1 验收**，不能替代任何一条 D1；
- **不是 D1-05（离线/人工可完成）的证据** —— 合同明文：live 与 offline **互不替代**；
- **不是真实航线、真实运力或真实成交**的证据：输入是标注为合成的夹具；
- **一次运行**已包含随机性，重跑结果可能不同；本文件不把单次结果当作稳定性证据。
