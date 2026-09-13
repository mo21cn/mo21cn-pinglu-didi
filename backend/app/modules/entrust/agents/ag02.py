"""AG-02 方案与采购 —— 报价解析与归一条款、比价、成本与约束。

能力边界
--------
产出三类**提案**（全部需人工复核，绝不自动落库）：

* `quote_parsed`：把船东报价文本解析成结构化字段（承运人、单价、有效期、航线）；
* `supplier_compare`：多家报价的横向对比（内部口径 —— 该类型的字段全部是内部字段，
  客户投影会整体剔除）；
* `customer_quote`：对客报价草稿（需人工组装与定版，见计划 §4 UI-06）。

金额与日期从哪来
----------------
本专业**必须**从输入的报价文本里抽取金额与日期 —— 这是它的职责。但抽取结果
只是**提案**，且 `rate` 用**十进制字符串**承载（不用浮点），与项目"金额定点精度"
的约定一致。抽取不到就留空，让它进 `missing_fields`，不猜。

比价是内部信息
--------------
`supplier_compare` 在注册表里 `internal_fields` 覆盖了全部业务字段，
`project_for_customer` 会把它投影成空对象 —— 客户侧永远看不到供应商比价与成本口径。
"""

from __future__ import annotations

import re
from typing import Any

CODE = "agent_02"
LABEL = "方案与采购"

source_kinds = frozenset({"assignment", "task", "artifact", "attachment", "entrustment"})

SYSTEM_PROMPT = """你是内河货运的方案与采购助理。输出必须是 JSON 对象，结构与要求：

{
  "assignment_id": 整数或 null,
  "task_id": 整数或 null,
  "base_revision": 整数,
  "summary": "中文简述",
  "artifact_proposals": [{"artifact_type": "quote_parsed|supplier_compare|customer_quote",
                           "payload": {...}, "note": "中文"}],
  "missing_fields": [],
  "findings": [{"code": "编码", "severity": "info|warn|risk", "message": "中文",
                 "source_refs": [{"kind": "...", "ref": "..."}]}],
  "source_refs": [{"kind": "...", "ref": "..."}],
  "proposed_actions": [{"action": "parse_quote|compare_suppliers|assemble_customer_quote",
                        "target_type": "artifact|assignment|null", "target_id": null, "reason": "中文"}],
  "requires_review": true
}

字段契约（服务端会按注册表校验，缺必填会记入 missing_fields）：
- quote_parsed：必填 carrier、rate；可选 cargo_name、quantity、quantity_unit、route、valid_until
- supplier_compare：必填 candidates（数组）
- customer_quote：必填 amount、currency、includes

硬性约束：
1. 抽取不到的字段**留空或省略**，绝不猜测；金额用十进制字符串（如 "38.00"）。
2. 只能引用输入中确实给出的来源（附件/任务/成果/受理单）；编造来源会被服务端标记。
3. 所有产出都是提案，`requires_review` 必须为 true。
4. 只允许 parse_quote / compare_suppliers / assemble_customer_quote 三种建议动作。
"""

_CARRIER_RE = re.compile(
    r"(?:承运人|船东|报价方|承运方|公司)\s*[:：]\s*([^\n，,；;]{2,40})"
    r"|([\u4e00-\u9fa5]{2,20}(?:物流|航运|船务|海运|货运|运输)(?:有限公司|公司)?)"
)
_RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:/|每)?\s*(吨|柜|箱|方|立方米|m3|公里|km)?")
_DATE_RE = re.compile(r"(?:有效期(?:至)?|截止|至)\s*[:：]?\s*(\d{4}-\d{2}-\d{2})")
_ROUTE_RE = re.compile(r"([\u4e00-\u9fa5]{2,8})\s*(?:→|->|—|至|到)\s*([\u4e00-\u9fa5]{2,8})")


def _to_decimal_text(raw: str) -> str:
    """转成两位小数的十进制字符串（不用浮点，对齐金额精度约定）。"""
    whole, _, frac = raw.partition(".")
    frac = (frac + "00")[:2]
    return f"{whole}.{frac}"


def _parse_quote_text(text: str) -> dict[str, Any]:
    """从报价文本做**保守**解析；解析不到的键不出现在结果里。"""
    payload: dict[str, Any] = {}
    if not text:
        return payload

    match = _CARRIER_RE.search(text)
    if match:
        payload["carrier"] = (match.group(1) or match.group(2) or "").strip()

    match = _RATE_RE.search(text)
    if match:
        payload["rate"] = _to_decimal_text(match.group(1))
        if match.group(2):
            payload["quantity_unit"] = match.group(2)

    match = _DATE_RE.search(text)
    if match:
        payload["valid_until"] = match.group(1)

    match = _ROUTE_RE.search(text)
    if match:
        payload["route"] = f"{match.group(1)}→{match.group(2)}"

    return payload


def _refs(context: dict[str, Any], job_input: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    attachment_id = job_input.get("attachment_id")
    if attachment_id is not None:
        refs.append({"kind": "attachment", "ref": str(attachment_id)})
    assignment = context.get("assignment") or {}
    if assignment.get("assignment_id") is not None:
        refs.append({"kind": "assignment", "ref": str(assignment["assignment_id"])})
    if job_input.get("quote_text"):
        # 操作者当面提交的文本本身就是可信输入来源（不是模型编的）
        refs.append({"kind": "operator_input", "ref": "job_input"})
    return refs


def mock_content(context: dict[str, Any], job_input: dict[str, Any]) -> dict[str, Any]:
    """确定性 fixture：解析 operator 提供的报价文本 + 可选候选清单。"""
    assignment = context.get("assignment") or {}
    assignment_id = assignment.get("assignment_id")
    base_revision = int(assignment.get("revision") or 0)
    refs = _refs(context, job_input)

    quote_text = str(job_input.get("quote_text") or "")
    parsed = _parse_quote_text(quote_text)
    proposals: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    missing: list[str] = []

    if parsed:
        # 报价解析稿本来就是"规范化后的原文"，顺带把摘要作为货物描述提示
        payload = dict(parsed)
        if assignment.get("cargo_summary"):
            payload.setdefault("cargo_name", assignment["cargo_summary"])
        proposals.append(
            {
                "artifact_type": "quote_parsed",
                "payload": payload,
                "note": "由报价文本解析，需人工核对承运人与单价口径。",
            }
        )
        if "carrier" not in payload:
            missing.append("carrier")
            findings.append(
                {
                    "code": "carrier_unparsed",
                    "severity": "warn",
                    "message": "报价文本中未能识别承运人，请人工补填后再对外使用。",
                    "source_refs": refs,
                }
            )
        if "valid_until" not in payload:
            findings.append(
                {
                    "code": "validity_missing",
                    "severity": "risk",
                    "message": "报价未写明有效期；过期末确认的报价不得支撑新的确认采购。",
                    "source_refs": refs,
                }
            )
    else:
        missing.extend(["carrier", "rate"])
        findings.append(
            {
                "code": "quote_unparsed",
                "severity": "risk",
                "message": "未能从输入中解析出任何报价字段，请检查文本或改走人工录入。",
                "source_refs": refs,
            }
        )

    candidates = job_input.get("candidates") or []
    if candidates:
        normalized: list[dict[str, Any]] = []
        for item in candidates:
            carrier = str(item.get("carrier") or "").strip()
            raw_rate = item.get("rate")
            rate = _to_decimal_text(str(raw_rate)) if raw_rate not in (None, "") else None
            normalized.append(
                {
                    "carrier": carrier,
                    "rate": rate,
                    "currency": item.get("currency") or "CNY",
                    "valid_until": item.get("valid_until"),
                }
            )
        priced = [c for c in normalized if c["rate"]]
        selected = min(priced, key=lambda c: float(c["rate"]))["carrier"] if priced else None
        proposals.append(
            {
                "artifact_type": "supplier_compare",
                "payload": {
                    "candidates": normalized,
                    "selected_candidate": selected,
                    "comparison_note": (
                        f"共 {len(normalized)} 家候选，按单价升序取最低价"
                        f"{'（' + selected + '）' if selected else '（无可用单价）'}；"
                        "比价与成本口径属内部信息，不进客户投影。"
                    ),
                },
                "note": "内部比价，需人工确认资源可用性后再结论。",
            }
        )
        if len(priced) >= 2:
            ordered = sorted(priced, key=lambda c: float(c["rate"]))
            spread = float(ordered[1]["rate"]) - float(ordered[0]["rate"])
            if spread > 0:
                findings.append(
                    {
                        "code": "price_spread",
                        "severity": "info",
                        "message": f"最低价与次低价相差 {spread:.2f}，建议核对报价口径是否一致。",
                        "source_refs": refs,
                    }
                )

    amount = job_input.get("amount")
    if amount not in (None, ""):
        proposals.append(
            {
                "artifact_type": "customer_quote",
                "payload": {
                    "amount": _to_decimal_text(str(amount)),
                    "currency": str(job_input.get("currency") or "CNY"),
                    "includes": job_input.get("includes") or ["内河运费"],
                    "excludes": job_input.get("excludes"),
                    "valid_until": job_input.get("valid_until"),
                },
                "note": "对客报价草稿 —— 定版发布前必须人工组装并确认口径。",
            }
        )

    summary = (
        f"已解析报价文本（{len(parsed)} 个字段），生成 {len(proposals)} 份提案；"
        f"缺 {len(missing)} 项。"
    )

    actions: list[dict[str, Any]] = [
        {
            "action": "parse_quote",
            "target_type": "artifact",
            "target_id": None,
            "reason": "解析结果需人工核对承运人与单价口径后再确认。",
        }
    ]
    if candidates:
        actions.append(
            {
                "action": "compare_suppliers",
                "target_type": "artifact",
                "target_id": None,
                "reason": "比价结论需结合资源可用性判断，不能只看单价。",
            }
        )

    return {
        "assignment_id": assignment_id,
        "task_id": job_input.get("task_id"),
        "base_revision": base_revision,
        "summary": summary,
        "artifact_proposals": proposals,
        "missing_fields": missing,
        "findings": findings,
        "source_refs": refs,
        "proposed_actions": actions,
        "requires_review": True,
    }


def build_user_prompt(context: dict[str, Any], job_input: dict[str, Any]) -> str:
    """把只读事实与操作者输入渲染成提示词（真实模式使用）。"""
    assignment = context.get("assignment") or {}
    lines = [
        "【受理单】",
        f"ID：{assignment.get('assignment_id')}  版本：{assignment.get('revision')}",
        f"标题：{assignment.get('title')}",
        f"货类/货物：{assignment.get('cargo_summary') or '（未填写）'}",
        "",
        "【待解析的报价文本】",
        str(job_input.get("quote_text") or "（无）"),
    ]
    candidates = job_input.get("candidates") or []
    if candidates:
        lines += ["", "【候选报价】"]
        for item in candidates:
            lines.append(
                f"- {item.get('carrier')}: {item.get('rate')} {item.get('currency') or ''}"
            )
    if job_input.get("amount") not in (None, ""):
        lines += [
            "",
            f"【对客报价金额】{job_input.get('amount')} {job_input.get('currency') or ''}",
        ]
    return "\n".join(lines)


__all__ = [
    "CODE",
    "LABEL",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "mock_content",
    "source_kinds",
]
