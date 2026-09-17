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

source_kinds = frozenset(
    {"assignment", "task", "artifact", "attachment", "entrustment", "attachment_text"}
)

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
- quote_parsed：必填 carrier、rate；可选 cargo_name、quantity、quantity_unit、route、
  valid_until、currency、rate_unit、includes、excludes
- supplier_compare：必填 candidates（数组）
- customer_quote：必填 amount、currency、includes

硬性约束：
1. 抽取不到的字段**留空或省略**，绝不猜测；金额用十进制字符串（如 "38.00"）。
2. **rate 必须与 rate_unit 成对**（`"元/吨"` 写 `rate_unit="吨"`，`"元/柜"` 写
   `rate_unit="柜"`）。只给一个金额、不说这一价是每吨还是每柜，费用就无法确定，
   下游也没法算总额。同样地：金额要带 `currency`；数量与计价单位是**两件事**，
   别把 `rate_unit` 当成 `quantity_unit`。
3. 只能引用输入里【可引用的来源目录】列出的来源，且 **kind 与 ref 都要原样复制**。
   写成文件名、加 `#` 前缀、把 `attachment_text` 写成 `attachment`、或引用目录里
   没有的编号，都会被服务端判为**编造来源**并标记给经理人核对。
4. 所有产出都是提案，`requires_review` 必须为 true。
5. 只允许 parse_quote / compare_suppliers / assemble_customer_quote 三种建议动作。
"""

#: 承运人。**两条通路，带标签的那条优先**：`报价方：西江航运有限公司` 是人写单子时的
#: 显式字段；无标签形式是兜底，只在**整个公司名被分隔符/行首包住**时才算数。
#:
#: ⚠️ 旧写法把公司后缀 `(?:有限公司|公司)` 写成**可选**、且允许在**句中**匹配，
#: 于是夹具首部那句「…也不是真实航运报价」里的**「也不是真实航运」被抽成了承运人**
#: （2026-09-17 设备走查实测：`carrier="也不是真实航运"`）。规则模板在 fixture 通路上
#: 替模型做抽取，**抽错比抽不到更坏**：它会变成一个看起来正常的字段，一路流到客户看的
#: 报价上；而"抽不到"至少会走人工补填分支（`carrier_unparsed` 告警）。
#: ⇒ 兜底那条**必须**带公司后缀，且左右都不能紧挨着汉字（不许从句子中间切一段出来）。
_CARRIER_RE = re.compile(
    r"(?:承运人|船东|报价方|承运方|公司)\s*[:：]\s*([^\n，,；;]{2,40})"
    r"|(?<![一-龥])([\u4e00-\u9fa5]{2,20}(?:物流|航运|船务|海运|货运|运输)"
    r"(?:有限公司|有限责任公司|公司))(?![一-龥])"
)
_RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:/|每)?\s*(吨|柜|箱|方|立方米|m3|公里|km)?")
_DATE_RE = re.compile(r"(?:有效期(?:至)?|截止|至)\s*[:：]?\s*(\d{4}-\d{2}-\d{2})")
#: 航线。**优先带标签的那条**（`航线：南宁 → 贵港`）—— 它是报价单里的声明字段。
#:
#: ⚠️ 旧写法只有一条无标签规则，于是「运输方案（公路 — 内河 — 公路）」这种**括号里的
#: 并列短语**会被当成航线（实测：`route="公路→内河"`，而真值是 `南宁→贵港`）。
#: 无标签形式保留，但只认「**整行就是一个箭头短语**」的情形，且只认 `→` / `->`：
#: `至` / `到` / `—` 在中文里出现得太滥，放在无标签形式里等于在猜。
_ROUTE_LABEL_RE = re.compile(
    r"(?:航线|路线|航段|行程)\s*[:：]\s*([\u4e00-\u9fa5]{2,8})\s*(?:→|->|—|至|到)\s*"
    r"([\u4e00-\u9fa5]{2,8})"
)
_ROUTE_BARE_RE = re.compile(
    r"(?m)^\s*([\u4e00-\u9fa5]{2,8})\s*(?:→|->)\s*([\u4e00-\u9fa5]{2,8})\s*$"
)
#: **数量**（与单价的分母是两件事，别混）。`数量：1200 吨` → quantity + quantity_unit
_QUANTITY_RE = re.compile(
    r"(?:数量|货量|总量|托运量)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(吨|柜|箱|方|立方米|m3)"
)
#: 币种。**金额不带币种等于半个事实**（CNY 与 USD 差一个数量级）
_CURRENCY_ALIASES = {"CNY": "CNY", "RMB": "CNY", "人民币": "CNY", "USD": "USD", "美元": "USD"}
_CURRENCY_RE = re.compile(r"CNY|RMB|人民币|USD|美元")


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
            # ⚠️ 这是**计价单位**（单价的分母），不是数量单位。旧写法把它记成
            # `quantity_unit`，等于让"每吨 45 元"冒充"数量是吨"：两者恰好同名时
            # 看不出问题，换成"每柜 3000 元"就会凭空造出一个不存在的数量事实
            # （HO 0917-3 裁定二：rate=45.00 本身不足以确定费用）。
            payload["rate_unit"] = match.group(2)

    match = _QUANTITY_RE.search(text)
    if match:
        payload["quantity"] = _to_decimal_text(match.group(1))
        if match.group(2):
            payload["quantity_unit"] = match.group(2)

    match = _CURRENCY_RE.search(text)
    if match:
        payload["currency"] = _CURRENCY_ALIASES.get(match.group(0), match.group(0))

    match = _DATE_RE.search(text)
    if match:
        payload["valid_until"] = match.group(1)

    match = _ROUTE_LABEL_RE.search(text) or _ROUTE_BARE_RE.search(text)
    if match:
        payload["route"] = f"{match.group(1)}→{match.group(2)}"

    return payload


def _quote_source(context: dict[str, Any], job_input: dict[str, Any]) -> tuple[str, str | None]:
    """确定"要解析的报价文本"从哪来 → (文本, 来源标记)。

    优先序刻意固定：

    1. **操作者当面粘贴的文本**（`quote_text`）优先。它是人当场给出的输入，
       最新、最明确 —— 附件里的旧报价可能已被新报价取代；
    2. 其次是**附件已提取的文本**（ENT-013）：上传报价单 PDF → 提取 →
       直接解析，操作者不必再手打一遍；
    3. 都没有 → 返回空，让上层走"解析不到任何字段"的分支（不猜）。

    来源标记告诉调用方该引用 `operator_input` 还是 `attachment_text` ——
    两者的可信度不同，随口引错会让"模型编造来源"的检查失去意义。
    """
    text = str(job_input.get("quote_text") or "").strip()
    if text:
        return text, "operator_input"

    attachment_id = job_input.get("attachment_id")
    if attachment_id is None:
        return "", None
    ref = str(attachment_id)
    for item in context.get("attachments") or []:
        if str(item.get("attachment_id")) != ref:
            continue
        excerpt = item.get("text_excerpt")
        if excerpt:
            return str(excerpt), "attachment_text"
        return "", None
    return "", None


def _refs(
    context: dict[str, Any], job_input: dict[str, Any], *, text_source: str | None = None
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    attachment_id = job_input.get("attachment_id")
    if attachment_id is not None:
        refs.append({"kind": "attachment", "ref": str(attachment_id)})
    assignment = context.get("assignment") or {}
    if assignment.get("assignment_id") is not None:
        refs.append({"kind": "assignment", "ref": str(assignment["assignment_id"])})
    if text_source == "operator_input":
        # 操作者当面提交的文本本身就是可信输入来源（不是模型编的）
        refs.append({"kind": "operator_input", "ref": "job_input"})
    elif text_source == "attachment_text" and attachment_id is not None:
        # 文本来自附件提取层：引用要写成 attachment_text，才能对上服务端枚举的
        # 来源目录（提取没成功时它根本不在目录里，引用它会被标为编造）。
        refs.append({"kind": "attachment_text", "ref": str(attachment_id)})
    return refs


def mock_content(context: dict[str, Any], job_input: dict[str, Any]) -> dict[str, Any]:
    """确定性 fixture：解析操作者提供的报价文本 + 可选候选清单。"""
    assignment = context.get("assignment") or {}
    assignment_id = assignment.get("assignment_id")
    base_revision = int(assignment.get("revision") or 0)
    quote_text, text_source = _quote_source(context, job_input)
    refs = _refs(context, job_input, text_source=text_source)
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


def build_user_prompt(
    context: dict[str, Any],
    job_input: dict[str, Any],
    source_catalog: frozenset[tuple[str, str]] | None = None,
) -> str:
    """把只读事实与操作者输入渲染成提示词（真实模式使用）。

    `source_catalog` 是本次作业**允许引用**的 `(kind, ref)` 集合（由
    `runner.build_source_catalog` 从服务端已读出的数据库事实枚举）。必须传进来：
    不给目录，模型只能"猜一个像样的 ref"，而它猜出来的东西在服务端核不上 ——
    2026-09-17 的 live 实测就是这样（它照着文件名写了描述串）。
    """
    assignment = context.get("assignment") or {}
    quote_text, text_source = _quote_source(context, job_input)
    source_label = {
        "operator_input": "操作者提交的文本",
        "attachment_text": "附件提取文本",
    }.get(text_source or "", "无")
    lines = [
        "【受理单】",
        f"ID：{assignment.get('assignment_id')}  版本：{assignment.get('revision')}",
        f"标题：{assignment.get('title')}",
        f"货类/货物：{assignment.get('cargo_summary') or '（未填写）'}",
        "",
        f"【待解析的报价文本】来源：{source_label}",
        quote_text or "（无）",
    ]

    attachments = context.get("attachments") or []
    if attachments:
        lines += ["", "【附件】"]
        for item in attachments:
            state = item.get("extract_status")
            detail = "已提取文本" if state == "done" else f"未提取（{state}）"
            truncated = "；文本已截断，非全文" if item.get("text_truncated") else ""
            lines.append(
                f"- #{item.get('attachment_id')} {item.get('filename')}"
                f"（{item.get('content_type')}）：{detail}{truncated}"
            )
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
    lines += _render_source_catalog(source_catalog, text_source=text_source, job_input=job_input)
    return "\n".join(lines)


def _render_source_catalog(
    source_catalog: frozenset[tuple[str, str]] | None,
    *,
    text_source: str | None,
    job_input: dict[str, Any],
) -> list[str]:
    """把来源目录渲染成**可原样复制**的清单（HO 0917-3 裁定二的第一条）。

    "只能引用存在的来源"这句话本身没有可操作性 —— 模型不知道 ref 长什么样，
    于是它写一个看起来合理的描述串（实测就是 `#1 文件名（text/plain）`）。
    给它逐行列出来的 `kind=... ref=...`，它就能照抄。
    """
    if not source_catalog:
        return ["", "【可引用的来源目录】", "（空）—— 因此 source_refs 请一律留空。"]
    out = ["", "【可引用的来源目录（kind 与 ref 必须原样复制，不得改写）】"]
    for kind, ref in sorted(source_catalog):
        out.append(f"- kind={kind} ref={ref}")
    if text_source == "attachment_text" and job_input.get("attachment_id") is not None:
        out.append(
            "本次报价文本来自附件提取 ⇒ 引用里**必须**出现 "
            f"`kind=attachment_text ref={job_input['attachment_id']}` 这一行"
            "（只有它才说明文本被读进来了；只写 `attachment` 只表示附件存在）。"
        )
    return out


__all__ = [
    "CODE",
    "LABEL",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "mock_content",
    "source_kinds",
]
