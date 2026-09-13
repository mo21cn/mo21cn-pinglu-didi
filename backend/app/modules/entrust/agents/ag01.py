"""AG-01 委托助理 —— 受理信息抽取、缺项提问、任务摘要、下一步建议。

能力边界（刻意收窄）
--------------------
本专业**不产出任何成果**（`SPECIALTY_ARTIFACT_TYPES["agent_01"]` 是空集）。
它只做四件事：把受理信息读成结构化字段、指出缺什么、汇总任务、给出下一步建议。
理由：受理信息一旦"解析"就写进成果，就等于让模型单方面决定委托内容 ——
而这些字段是货主自己填的，不需要模型代劳。它的价值在于**指出缺口**，不在于替客户下笔。

金额与日期不由模型产出
----------------------
`extract` 的结果只用于"提示缺项"，任何具体值最终仍由人工在表单里确认。
提示词里明确写了这条，mock fixture 也遵守同样的口径。
"""

from __future__ import annotations

import re
from typing import Any

CODE = "agent_01"
LABEL = "委托助理"

source_kinds = frozenset(
    {"assignment", "task", "artifact", "attachment", "entrustment", "attachment_text"}
)

SYSTEM_PROMPT = """你是内河货运委托的受理助理。你的输出必须是 JSON 对象，结构如下：

{
  "assignment_id": 整数或 null,
  "task_id": 整数或 null,
  "base_revision": 整数（你没有修改任何东西，原样回填输入里的版本号）,
  "summary": "中文简述，不超过 200 字",
  "artifact_proposals": [],
  "missing_fields": ["缺失字段名"],
  "findings": [{"code": "编码", "severity": "info|warn|risk", "message": "中文说明",
                 "source_refs": [{"kind": "assignment|task|artifact|attachment", "ref": "标识"}]}],
  "source_refs": [{"kind": "...", "ref": "..."}],
  "proposed_actions": [{"action": "ask_missing_field|suggest_next_step|summarize_tasks",
                        "target_type": "assignment|task|null", "target_id": null, "reason": "中文"}],
  "requires_review": true
}

硬性约束：
1. **不要输出任何你没有看到的数字、日期或金额**。缺什么就写进 missing_fields。
2. 只能引用输入中确实给出的来源；编造来源会被服务端标记。
3. `artifact_proposals` 必须是空数组 —— 你不产出成果。
4. 只允许 ask_missing_field / suggest_next_step / summarize_tasks 三种建议动作。
"""

#: 受理阶段必须齐备的字段（与 `assignments` 的草稿字段对齐）。
REQUIRED_ACCEPTANCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("cargo_summary", "货类或货物描述"),
    ("quantity", "货量"),
    ("quantity_unit", "货量单位"),
)

_QUANTITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(吨|t|T|方|m3|立方米|件|箱)")


def _extract_from_summary(text: str | None) -> dict[str, Any]:
    """从受理摘要里做**保守**抽取：抽不到就留 None，绝不编造。"""
    found: dict[str, Any] = {"quantity": None, "quantity_unit": None}
    if not text:
        return found
    match = _QUANTITY_RE.search(text)
    if match:
        found["quantity"] = match.group(1)
        found["quantity_unit"] = match.group(2)
    return found


def _assignment_ref(assignment: dict[str, Any] | None) -> list[dict[str, str]]:
    if not assignment:
        return []
    return [{"kind": "assignment", "ref": str(assignment["assignment_id"])}]


def mock_content(context: dict[str, Any], job_input: dict[str, Any]) -> dict[str, Any]:
    """确定性 fixture：从受理单与任务清单派生信封（与真实模式同构）。"""
    assignment = context.get("assignment") or {}
    tasks = context.get("tasks") or []
    assignment_id = assignment.get("assignment_id")
    base_revision = int(assignment.get("revision") or 0)

    extracted = _extract_from_summary(assignment.get("cargo_summary"))

    missing: list[str] = []
    findings: list[dict[str, Any]] = []
    for field, label in REQUIRED_ACCEPTANCE_FIELDS:
        value = assignment.get(field)
        if field == "quantity" and not value:
            value = extracted["quantity"]
        if field == "quantity_unit" and not value:
            value = extracted["quantity_unit"]
        if value in (None, "", [], {}):
            missing.append(field)
            findings.append(
                {
                    "code": f"missing_{field}",
                    "severity": "warn",
                    "message": f"受理信息缺少「{label}」，建议向货主补齐后再提交。",
                    "source_refs": _assignment_ref(assignment),
                }
            )

    refs = _assignment_ref(assignment)
    task_summary = "当前没有任务。"
    if tasks:
        by_status: dict[str, int] = {}
        for task in tasks:
            by_status[str(task.get("status", "unknown"))] = (
                by_status.get(str(task.get("status", "unknown")), 0) + 1
            )
        detail = "、".join(f"{status} {count} 项" for status, count in sorted(by_status.items()))
        task_summary = f"共 {len(tasks)} 项任务：{detail}。"
        refs = refs + [{"kind": "task", "ref": str(t["task_id"])} for t in tasks[:20]]
        blocked = [t for t in tasks if t.get("status") == "waiting"]
        if blocked:
            findings.append(
                {
                    "code": "tasks_waiting",
                    "severity": "risk",
                    "message": f"有 {len(blocked)} 项任务处于缺件等待，可能影响后续排期。",
                    "source_refs": [{"kind": "task", "ref": str(t["task_id"])} for t in blocked],
                }
            )

    title = assignment.get("title") or "未命名委托"
    summary = f"受理单「{title}」：{task_summary}缺失 {len(missing)} 项关键信息。"

    actions: list[dict[str, Any]] = []
    if missing:
        actions.append(
            {
                "action": "ask_missing_field",
                "target_type": "assignment",
                "target_id": assignment_id,
                "reason": "补齐受理信息：" + "、".join(missing),
            }
        )
    actions.append(
        {
            "action": "suggest_next_step",
            "target_type": "assignment",
            "target_id": assignment_id,
            "reason": "信息齐备后可进入报价环节。",
        }
    )

    return {
        "assignment_id": assignment_id,
        "task_id": job_input.get("task_id"),
        "base_revision": base_revision,
        "summary": summary,
        "artifact_proposals": [],
        "missing_fields": missing,
        "findings": findings,
        "source_refs": refs,
        "proposed_actions": actions,
        "requires_review": True,
    }


def build_user_prompt(context: dict[str, Any], job_input: dict[str, Any]) -> str:
    """把只读事实渲染成提示词（真实模式使用；fixture 不依赖它）。"""
    assignment = context.get("assignment") or {}
    tasks = context.get("tasks") or []
    lines = [
        "【受理单】",
        f"ID：{assignment.get('assignment_id')}  版本：{assignment.get('revision')}",
        f"标题：{assignment.get('title')}",
        f"状态：{assignment.get('status')}",
        f"货类/货物：{assignment.get('cargo_summary') or '（未填写）'}",
        f"货量：{assignment.get('quantity') or '（未填写）'} {assignment.get('quantity_unit') or ''}",
        "",
        "【任务】",
    ]
    if tasks:
        for task in tasks:
            lines.append(f"- #{task.get('task_id')} {task.get('title')}（{task.get('status')}）")
    else:
        lines.append("（无）")

    attachments = context.get("attachments") or []
    if attachments:
        # 只列**存在与提取状态**，不把正文塞进来：AG-01 的职责是"指出缺什么"，
        # 把附件正文给它，等于邀请它从材料里"读出"货量 —— 而受理字段必须由
        # 货主自己在表单里确认（本专业不产出成果，也不猜数字）。
        lines += ["", "【附件】"]
        for item in attachments:
            state = item.get("extract_status")
            lines.append(
                f"- #{item.get('attachment_id')} {item.get('filename')}"
                f"（{'已提取文本' if state == 'done' else f'未提取：{state}'}）"
            )

    lines += ["", "【附加说明】", str(job_input.get("note") or "（无）")]
    return "\n".join(lines)


__all__ = [
    "CODE",
    "LABEL",
    "REQUIRED_ACCEPTANCE_FIELDS",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "mock_content",
    "source_kinds",
]
