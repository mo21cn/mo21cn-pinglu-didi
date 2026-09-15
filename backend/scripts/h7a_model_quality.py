"""H7a —— 真实模型质量验证包（**不是 CI 门禁，也不代表 PRD 验收通过**）。

为什么要这个脚本
----------------
HO 对 H7a 的裁决是「准备验证包，暂不宣称通过；资源就绪后独立验证，fixture 继续服务 CI」。
背景：``LLM_MOCK=true`` 的规则模板只保证**输出结构与真实模式同构**，
它证明不了真实模型**好不好用** —— 而"合同/解析类质量"此前没有任何判据。

本脚本就是那个「资源就绪后的独立验证」：

- **真发请求**（走 ``app.modules.agent.llm.chat_json``，与线上同一条代码路径，
  含 system prompt、`response_format`、错误分类）；
- **机器判分**（样本 + 参考答案在 ``h7a_samples.json``，不需要人工逐条看）；
- **阈值先定死、再跑**，不允许事后调（见 ``THRESHOLDS``）；
- **产出落到文件**，便于跨模型/跨时间对比。

它**不参与 CI**（CI 无 Key、也不该有网络依赖）。本仓库的 CI 继续由 fixture 覆盖。

用法（cwd=backend，需 ``.env.local`` 里配好 ``LLM_API_KEY`` 且 ``LLM_MOCK=false``）::

    .venv/Scripts/python.exe scripts/h7a_model_quality.py
    .venv/Scripts/python.exe scripts/h7a_model_quality.py --runs 3 --out ../artifacts/h7a.json

⚠️ **会花钱**：每次调用都真实计费。默认 12 样本 × 2 轮 = 24 次；
   改样本或加大 ``--runs`` 前先看 ``--dry-run`` 打印的调用次数与字符量估算。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# 以 `python scripts/h7a_model_quality.py`（cwd=backend）执行时，`app` 不在 sys.path 上，
# 需显式补上 backend/ —— 这是本文件唯一需要的 sys.path 操作。
# （2026-09-16 补：此前缺这段，文档里那条运行命令实际跑不起来，报 ModuleNotFoundError）
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core.config import get_settings  # noqa: E402
from app.modules.agent import llm as llm_gateway  # noqa: E402
from app.modules.agent.llm import LLMError  # noqa: E402
from app.modules.agent.service import build_cargo_prompt  # noqa: E402

HERE = Path(__file__).resolve().parent
SAMPLES_PATH = HERE / "h7a_samples.json"
FIELDS = (
    "cargo_name",
    "cargo_type",
    "weight_t",
    "origin_port",
    "dest_port",
    "expect_date",
    "offer_price",
)

#: **先定死的判据**（跑之前就写在这里；跑完不许改数字去凑结果）
#: 理由：阈值若可以事后调整，"验证"就退化成"给自己发合格证"。
THRESHOLDS: dict[str, Any] = {
    "field_accuracy_min": 0.85,  # 逐字段准确率下限（84 次判定）
    "hallucination_max": 0,  # 「不得编造」是 prompt 硬要求 ⇒ 一次都不许有
    "calibration_gap_min": 0.10,  # 答对字段的平均置信度 − 答错字段的，须 > 0.1
    "parse_failure_max": 0,  # 输出不可解析 = 该样本完全不可用
}


def _expected_date(spec: Any, today: date) -> str | None:
    """把参考答案里的日期规格展开成 ``YYYY-MM-DD``。

    ``{"ymd": "10-08"}`` → 今年 10-08；``{"next_weekday": 2}`` → 下周三（周三=2）。
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        if "ymd" in spec:
            return f"{today.year}-{spec['ymd']}"
        if "next_weekday" in spec:
            target = int(spec["next_weekday"])
            delta = (target - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
    raise ValueError(f"无法解析的日期规格: {spec!r}")


def _num_ok(got: Any, want: Any) -> bool:
    """数值判定：null 必须一致；非 null 用 0.5% 相对容差。"""
    if want is None:
        return got is None
    if got is None or isinstance(got, bool):
        return False
    try:
        g, w = float(got), float(want)
    except (TypeError, ValueError):
        return False
    return abs(g - w) <= abs(w) * 0.005 + 1e-9


def _name_ok(got: Any, want: Any) -> bool:
    """货名判定：参考答案给关键词集合，任一命中即可（避免"散装水泥"/"水泥"的差异）。"""
    if want is None:
        return got is None
    if got is None:
        return False
    kws = want.get("keywords") if isinstance(want, dict) else None
    if not kws:
        return False
    text = str(got).replace(" ", "").lower()
    return any(str(k).replace(" ", "").lower() in text for k in kws)


def _grade_sample(sample: dict[str, Any], content: dict[str, Any], today: date) -> dict[str, Any]:
    """对单条样本逐字段判分，返回明细（含编造计数与置信度对）。"""
    expect = sample["expect"]
    conf_raw = content.get("field_confidence") or {}
    rows: list[dict[str, Any]] = []
    hallucinations = 0

    for field in FIELDS:
        got = content.get(field)
        want_raw = expect.get(field)
        want = _expected_date(want_raw, today) if field == "expect_date" else want_raw

        if field == "cargo_name":
            ok = _name_ok(got, want)
        elif field in ("weight_t", "offer_price"):
            ok = _num_ok(got, want)
        else:
            ok = got == want

        if want is None and got is not None:
            hallucinations += 1

        try:
            conf = float(conf_raw.get(field, 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        rows.append(
            {"field": field, "ok": bool(ok), "got": got, "want": want, "confidence": round(conf, 3)}
        )

    return {
        "id": sample["id"],
        "note": sample.get("note", ""),
        "rows": rows,
        "hallucinations": hallucinations,
        "correct": sum(1 for r in rows if r["ok"]),
        "total": len(rows),
    }


async def _call(prompt: str, text: str) -> tuple[dict[str, Any] | None, str | None, str, int]:
    """调真实模型一次；返回 (content, error_kind, error_message, latency_ms)。

    ``prompt`` 由调用方用 ``build_cargo_prompt(today)`` 渲染好传进来 ——
    **必须是生产同一个渲染入口**，否则"验证"验的是另一套提示词。
    参考答案的日期也按同一个 ``today`` 展开，两侧同基准。

    ⚠️ `error_message` 必须一起返回：2026-09-16 重跑时 S10 两次 `bad_response`，
    结果文件里只有 `{"kind": "bad_response"}` —— 光看这个查不出原因，
    只能另写探针复现才看到"答案是 markdown 围栏里的 JSON"。
    **错误分类用来路由，错误原文用来定位**，两者缺一不可。
    """
    try:
        res = await llm_gateway.chat_json(system=prompt, user=text)
    except LLMError as exc:
        return None, exc.kind, str(exc), 0
    return res.content, None, "", res.latency_ms


async def run(runs: int) -> dict[str, Any]:
    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))["samples"]
    today = date.today()
    # 与生产同一个渲染入口：提示词里带上了"今天"，模型才有基准推算相对日期
    prompt = build_cargo_prompt(today)
    settings = get_settings()

    print(f"供应商   : {settings.LLM_PROVIDER}  模型: {settings.LLM_MODEL}")
    print(f"端点     : {settings.LLM_BASE_URL}")
    print(f"样本/轮次: {len(samples)} × {runs} = {len(samples) * runs} 次调用")
    print(f"今天     : {today.isoformat()}（相对日期按它推算）")
    print(f"提示词   : 携带今天={today.isoformat() in prompt}（{len(prompt)} 字符）\n")

    per_run: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    latencies: list[int] = []
    stable_ids: list[str] = []

    for run_no in range(1, runs + 1):
        outcomes: dict[str, Any] = {}
        for s in samples:
            content, kind, err_msg, latency = await _call(prompt, s["text"])
            if content is None:
                errors.append({"id": s["id"], "run": run_no, "kind": kind, "message": err_msg})
                print(f"  ✗ {s['id']} 调用失败: {kind}")
                print(f"      {err_msg[:300]}")
                continue
            latencies.append(latency)
            outcomes[s["id"]] = content
        graded = [
            _grade_sample(s, outcomes[s["id"]], today) for s in samples if s["id"] in outcomes
        ]
        per_run.append({"run": run_no, "graded": graded, "raw": outcomes})
        print(
            f"  第 {run_no} 轮：{sum(g['correct'] for g in graded)}/{sum(g['total'] for g in graded)}"
        )

    # 稳定性：同一样本多轮输出是否完全一致（**只在 runs>1 时才有意义**）
    if runs > 1:
        for s in samples:
            vals = [
                json.dumps(r["raw"].get(s["id"]), ensure_ascii=False, sort_keys=True)
                for r in per_run
            ]
            if len(set(vals)) == 1:
                stable_ids.append(s["id"])

    all_rows = [row for r in per_run for g in r["graded"] for row in g["rows"]]
    total = len(all_rows)
    correct = sum(1 for r in all_rows if r["ok"])
    hallucination = sum(g["hallucinations"] for r in per_run for g in r["graded"])
    ok_conf = [r["confidence"] for r in all_rows if r["ok"]]
    bad_conf = [r["confidence"] for r in all_rows if not r["ok"]]
    gap = (statistics.fmean(ok_conf) - statistics.fmean(bad_conf)) if ok_conf and bad_conf else 0.0

    by_field = {
        f: {
            "correct": sum(1 for r in all_rows if r["field"] == f and r["ok"]),
            "total": sum(1 for r in all_rows if r["field"] == f),
        }
        for f in FIELDS
    }

    result: dict[str, Any] = {
        "provider": settings.LLM_PROVIDER,
        "model": settings.LLM_MODEL,
        "base_url": settings.LLM_BASE_URL,
        "runs": runs,
        "samples": len(samples),
        "today": today.isoformat(),
        # 自证：结果文件本身写明这轮的提示词是否携带了基准日期。
        # 否则"评分基准假定模型知道年份"这件事只能靠记忆，事后无从复核。
        "prompt_includes_today": today.isoformat() in prompt,
        "prompt_chars": len(prompt),
        "thresholds": THRESHOLDS,
        "field_accuracy": round(correct / total, 4) if total else 0.0,
        "correct": correct,
        "total": total,
        "hallucinations": hallucination,
        "parse_failures": len(errors),
        "errors": errors,
        "calibration_gap": round(gap, 4),
        "mean_confidence_correct": round(statistics.fmean(ok_conf), 4) if ok_conf else None,
        "mean_confidence_wrong": round(statistics.fmean(bad_conf), 4) if bad_conf else None,
        "latency_ms": {
            "mean": int(statistics.fmean(latencies)) if latencies else None,
            "p95": int(sorted(latencies)[int(len(latencies) * 0.95) - 1]) if latencies else None,
        },
        "by_field": by_field,
        "stable_sample_ids": stable_ids if runs > 1 else None,
        "per_sample": [
            {
                "id": g["id"],
                "note": g["note"],
                "correct": g["correct"],
                "total": g["total"],
                "hallucinations": g["hallucinations"],
                "rows": g["rows"],
            }
            for g in per_run[-1]["graded"]
        ],
    }
    result["verdict"] = _verdict(result)
    return result


def _verdict(r: dict[str, Any]) -> dict[str, Any]:
    """按**跑之前定死的**阈值逐条判定；返回每条的达标情况与总评。"""
    checks = {
        "field_accuracy": r["field_accuracy"] >= THRESHOLDS["field_accuracy_min"],
        "hallucination": r["hallucinations"] <= THRESHOLDS["hallucination_max"],
        "calibration": r["calibration_gap"] > THRESHOLDS["calibration_gap_min"],
        "parse_failure": r["parse_failures"] <= THRESHOLDS["parse_failure_max"],
    }
    return {"checks": checks, "all_met": all(checks.values())}


def _report(r: dict[str, Any]) -> None:
    print("\n================ H7a 真实模型质量 ================")
    print(
        f"字段准确率 : {r['field_accuracy']:.2%}  ({r['correct']}/{r['total']})"
        f"  阈值 ≥ {THRESHOLDS['field_accuracy_min']:.0%}"
    )
    print(f"编造次数   : {r['hallucinations']}  阈值 ≤ {THRESHOLDS['hallucination_max']}")
    print(f"置信度校准 : +{r['calibration_gap']:.3f}  阈值 > {THRESHOLDS['calibration_gap_min']}")
    print(f"调用失败   : {r['parse_failures']}  阈值 ≤ {THRESHOLDS['parse_failure_max']}")
    print(f"延迟       : mean {r['latency_ms']['mean']}ms / p95 {r['latency_ms']['p95']}ms")
    if r["stable_sample_ids"] is not None:
        print(f"多轮一致   : {len(r['stable_sample_ids'])}/{r['samples']} 条样本输出完全一致")
    print("\n-- 逐字段 --")
    if r["total"] == 0:
        # 一次都没成功（例如账户余额不足 402）⇒ 必须说"没验成"，不能显示 0/0 假装跑过
        print("  （无成功调用，逐字段统计无意义 —— 见上方「调用失败」与 errors 明细）")
    for f, d in r["by_field"].items():
        if d["total"]:
            print(f"  {f:<13} {d['correct']:>3}/{d['total']:<3} {d['correct'] / d['total']:.0%}")
    print("\n-- 未达标明细（最后一轮）--")
    any_bad = False
    for s in r["per_sample"]:
        bad = [x for x in s["rows"] if not x["ok"]]
        if bad:
            any_bad = True
            print(f"  {s['id']} {s['note']}")
            for x in bad:
                print(f"      {x['field']:<13} got={x['got']!r}  want={x['want']!r}")
    if not any_bad:
        print("  （无）")
    if r["total"] == 0:
        print(
            "\n⚠️ **本次没有产生任何可评分的调用** —— 这是「未执行（NOT_RUN）」，"
            "\n   不是「模型不达标」，也不得倒推成任何质量结论。按 `errors` 里的 kind 对号入座："
            "\n   `quota`（402，Key 与计费方式不匹配或余额不足，用 llm_key_doctor.py 定位）"
            "\n   / `rate_limit`（429）/ `bad_response`（响应体解析不了，看错误里的 content 开头）"
            "\n   / `network` / `timeout`。",
        )
        return

    print("\n-- 判定 --")
    for k, v in r["verdict"]["checks"].items():
        print(f"  {'✅' if v else '❌'} {k}")
    print(f"\n总评: {'全部达标' if r['verdict']['all_met'] else '未全部达标（如实记录，不折算）'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H7a 真实模型质量验证（非 CI，真发请求、会产生费用）")
    ap.add_argument("--runs", type=int, default=2, help="每条样本跑几轮（>1 时额外统计多轮一致性）")
    ap.add_argument("--out", default="", help="结果 JSON 落点")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发生的调用量与费用估算")
    args = ap.parse_args(argv)

    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))["samples"]
    calls = len(samples) * args.runs
    chars = sum(len(s["text"]) for s in samples) * args.runs + len(build_cargo_prompt()) * calls
    print(
        f"样本 {len(samples)} 条 × {args.runs} 轮 = {calls} 次调用；"
        f"提示词约 {chars} 字符（≈{chars // 3} tokens，仅输入侧）"
    )
    if args.dry_run:
        print("（dry-run：未发起任何请求）")
        return 0

    settings = get_settings()
    if settings.LLM_MOCK or not settings.LLM_API_KEY:
        print(
            "未配置真实模型（LLM_MOCK=true 或 LLM_API_KEY 为空）⇒ 本脚本无意义，退出",
            file=sys.stderr,
        )
        return 2

    result = asyncio.run(run(args.runs))
    _report(result)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入: {out}")
    if result["total"] == 0:
        # 退出码区分三态：0=达标 / 1=未达标 / 2=**没跑成**（NOT_RUN，不是失败）
        return 2
    return 0 if result["verdict"]["all_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
