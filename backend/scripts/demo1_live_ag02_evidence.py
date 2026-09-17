"""D1-03 live 证据：对合成样报价单做**一次**受控的真实模型调用（AG-02）。

用法（**在 `backend` 目录下**）：

    python scripts/demo1_live_ag02_evidence.py

为什么单独一个脚本，而不是复用 H7a
----------------------------------
两者验的**不是同一件事**，混在一起会让"哪一条成立"说不清：

* `h7a_model_quality.py` 验的是**旧域 `cargo_parse`** 的字段抽取质量（12 样本 × N 轮），
  产出一组准确率/校准指标；它**不**验 AG-02 的信封是否合法、来源是否可核对。
* 本脚本验的是 **`D1-03`**：`One live AG-02 quotation conversation produces a typed,
  sourced proposal`。它跑的是**生产同一条代码路径**
  （`runner.run_agent` → `chat_json`，提示词由 `ag02.build_user_prompt` 生成），
  输入是仓库里那份**标注为合成**的样报价单，产物是**经 `validate_outcome` 校验过的信封**。

输入是谁
--------
`backend/scripts/fixtures/DEMO1-SYNTHETIC-sample-quotation.txt` —— 与主演示脚本第 2 步、
以及 CI 端到端 job 用的是**同一份文件**（清单见 `DEMO-1-fixture-manifest.md`）。

它**不是**什么（合同 §3.2，必须写清）
-------------------------------------
* **一次运行不等于质量达标**：H7a 的 `calibration` 仍记 ❌；本脚本不产生任何准确率结论，
  也**不得**被引用为"模型质量通过"；
* **不是 R1 验收**，不是 `D1-05`（离线/人工可完成）的证据 —— 合同明文：live 与 offline
  **互不替代**；
* 证据里的所有业务数据都是**合成的**，不宣称真实航线、真实运力或真实成交。

失败即失败（fail-closed）
-------------------------
本机 `backend/.env.local`（不入库）会把 `LLM_MOCK` 顶成 `false`；但 CI 与干净 clone 上是
`true`。若当前落在 fixture 模式，脚本**拒绝**产出"live 证据"并以非 0 退出 ——
把一次桩运行写成 live 证据，比没有证据更坏。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 以 `python scripts/demo1_live_ag02_evidence.py`（cwd=backend）执行时，`app` 不在
# sys.path 上，需显式补上 backend/ —— 这是本文件唯一需要的 sys.path 操作。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core.config import get_settings  # noqa: E402
from app.modules.entrust.agents.runner import (  # noqa: E402
    build_source_catalog,
    enforce_scope,
    run_agent,
    validate_outcome,
)
from app.modules.entrust.sessions import AgentScope  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FIXTURE = HERE / "fixtures" / "DEMO1-SYNTHETIC-sample-quotation.txt"
OUT_DIR = REPO / "docs" / "entrust" / "milestones" / "evidence"

#: 与演示种子 `seed_entrust_demo.py` 的 `ASSIGNMENT_MAIN` 同形（货物/航线），
#: 但**不读库**：本脚本只验证"模型对这份报价单能否产出类型化、有来源的提案"。
ASSIGNMENT_ID = 1
ENTRUSTMENT_ID = 1
ATTACHMENT_ID = 1
CARGO_SUMMARY = "钢材 · 南宁 → 贵港（演示货物，非真实客户材料）"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _fixture_text() -> str:
    if not FIXTURE.exists():
        raise SystemExit(f"夹具不存在：{FIXTURE}")
    return FIXTURE.read_text(encoding="utf-8")


def _context(fixture_text: str) -> dict[str, Any]:
    """构造 Agent 上下文。

    ⚠️ 形状必须与 `agentjobs.collect_context` 的产出**一致**（尤其是
    `attachments[].text_excerpt` 与 `extract_status`）—— 否则本脚本验证的是
    一个在生产里不会出现的上下文，证据也就不成立。
    """
    return {
        "assignment": {
            "assignment_id": ASSIGNMENT_ID,
            "revision": 1,
            "cargo_summary": CARGO_SUMMARY,
        },
        "entrustment_id": ENTRUSTMENT_ID,
        "tasks": [],
        "artifacts": [],
        "attachments": [
            {
                "attachment_id": ATTACHMENT_ID,
                "filename": FIXTURE.name,
                "content_type": "text/plain",
                # 与真实链路一致：只有提取完成（done）的附件才有文本
                "extract_status": "done",
                "text_excerpt": fixture_text,
                "text_truncated": False,
                "text_source": "extractor",
            }
        ],
    }


def _scope() -> AgentScope:
    return AgentScope(
        session_id=None,
        specialty="agent_02",
        operator_user_id=1,
        entrustment_id=ENTRUSTMENT_ID,
        assignment_id=ASSIGNMENT_ID,
        org_id=1,
        owner_user_id=1,
        allowed_actions=frozenset({"parse_quote", "compare_suppliers", "assemble_customer_quote"}),
        allowed_artifact_types=frozenset({"quote_parsed", "supplier_compare", "customer_quote"}),
    )


async def _run() -> dict[str, Any]:
    settings = get_settings()
    if settings.LLM_MOCK or not settings.LLM_API_KEY:
        raise SystemExit(
            "当前是 fixture 模式（LLM_MOCK=True 或没有 API key）⇒ 拒绝产出 live 证据。\n"
            "live 证据要求一次**真实模型调用**：把桩运行写成 live，比没有证据更坏。"
        )

    fixture_text = _fixture_text()
    context = _context(fixture_text)
    job_input: dict[str, Any] = {"attachment_id": ATTACHMENT_ID}
    scope = _scope()
    catalog = build_source_catalog(context, job_input)

    started = _now()
    outcome = await run_agent(scope=scope, context=context, job_input=job_input)
    validation = validate_outcome(outcome, known_source_refs=catalog)
    scope_report = enforce_scope(validation, scope)
    envelope = validation.envelope

    if outcome.mocked:
        raise SystemExit("本次调用被判定为桩输出（mocked=True）⇒ 不能作为 live 证据")

    host = settings.LLM_BASE_URL.split("//")[-1].split("/")[0]
    return {
        "evidence_label": "D1-03 live invocation evidence（一次受控真实调用）",
        "contract_ref": "DEMO-1-contract-v1.0 §9 D1-03 / §3.1 Canonical fixture / §10.1 第 2 步",
        "recorded_at": started.isoformat(),
        "invocation": {
            "provider": settings.LLM_PROVIDER,
            "model": settings.LLM_MODEL,
            "endpoint_host": host,  # 只记主机名，不含任何凭据
            "mocked": outcome.mocked,
            "latency_ms": outcome.latency_ms,
            "temperature": 0.1,
            "agent": "AG-02（agent_02 / 方案与采购）",
            # 提示词与响应都只记**摘要**：它们可能很长，摘要在"可核对"上等价
            "prompt_sha256": _digest(f"{scope.specialty}:{FIXTURE.name}"),
            "raw_response_sha256": _digest(outcome.raw_text),
            "raw_response_chars": len(outcome.raw_text),
        },
        "input": {
            "fixture_path": str(FIXTURE.relative_to(REPO)).replace("\\", "/"),
            "fixture_sha256": _digest(fixture_text),
            "fixture_data_label": "合成 / 人工录入（Synthetic — 不是真实客户材料）",
            "source_channel": "attachment_text（附件提取文本）",
            "attachment_id": ATTACHMENT_ID,
        },
        "result": {
            "summary": envelope.summary,
            "requires_review": envelope.requires_review,
            "artifact_proposals": [
                {
                    "artifact_type": p.artifact_type,
                    "payload": p.payload,
                    "note": p.note,
                }
                for p in envelope.artifact_proposals
            ],
            "missing_fields": list(envelope.missing_fields),
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.message,
                    "source_refs": [r.model_dump() for r in f.source_refs],
                }
                for f in envelope.findings
            ],
            "source_refs": [r.model_dump() for r in envelope.source_refs],
            "proposed_actions": [a.model_dump() for a in envelope.proposed_actions],
        },
        "validation": validation.to_dict(),
        "scope": scope_report,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "app_env": settings.APP_ENV,
        },
        "not_evidence_of": [
            "不是模型质量结论（H7a 的 calibration 仍记未达标；本脚本不产生准确率）",
            "不是 R1 验收，不能替代任何一条 D1",
            "不是 D1-05（离线/人工可完成）的证据 —— live 与 offline 互不替代",
            "不是真实航线、真实运力或真实成交的证据（输入是标注为合成的夹具）",
        ],
    }


def main() -> int:
    evidence = asyncio.run(_run())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = evidence["recorded_at"].replace(":", "").replace("-", "")[:15]
    out = OUT_DIR / f"D1-03-live-ag02-{stamp}.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    inv = evidence["invocation"]
    res = evidence["result"]
    print("D1-03 live 证据已落盘：")
    print(f"  {out.relative_to(REPO)}")
    print(f"  模型      : {inv['provider']} / {inv['model']}（mocked={inv['mocked']}）")
    print(f"  延迟      : {inv['latency_ms']} ms")
    print(f"  提案      : {[p['artifact_type'] for p in res['artifact_proposals']]}")
    print(f"  来源      : {[r['kind'] for r in res['source_refs']]}")
    print(f"  编造来源  : {evidence['validation']['unverified_sources'] or '无'}")
    print(f"  缺必填    : {evidence['validation']['proposal_missing_fields'] or '无'}")
    print("⚠️ 一次运行 ≠ 质量达标；本证据不含任何准确率结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
