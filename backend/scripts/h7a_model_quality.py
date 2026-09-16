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
from app.modules.agent.service import (  # noqa: E402
    build_cargo_prompt,
    coerce_confidence,
)

HERE = Path(__file__).resolve().parent
#: **冻结基线集**（跨轮可比，未经 HO 同意不得增删）
SAMPLES_PATH = HERE / "h7a_samples.json"
#: **挑战集**（HO 0917 批准新增 ≥5 条）。⚠️ 与主集**分别报告**，不混合、不改变主集判定。
CHALLENGE_PATH = HERE / "h7a_challenge_samples.json"
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


#: 置信度的四种状态。**必须区分**：HO 0917 指出「把缺失/非法置信度补成 0」会混淆
#: 「模型没有报告置信度」与「模型报告正确概率为零」—— 后者是合法且重要的信号
#: （模型明说"我完全不确定"，却和"它没说"被记成同一个数）。
CONF_OK = "ok"  # 合法数值，在 [0,1]
CONF_MISSING = "missing"  # 该字段根本没出现在 field_confidence 里
CONF_INVALID = "invalid"  # 非数值 / 布尔 / 无法转 float
CONF_RANGE = "range"  # 能转 float 但越界（<0 或 >1 或 NaN）


def _read_confidence(conf_raw: Any, field: str) -> tuple[float | None, str]:
    """读单个字段的置信度；返回 ``(值 or None, 状态)``。

    ⚠️ 不要写成 ``conf_raw.get(field) or 0.0`` —— 那会把合法的 ``0`` 换成默认值，
    也会把"缺失"记成"置信度 0"。三种情况必须分开（见 ``CONF_*``）。
    """
    if not isinstance(conf_raw, dict):
        return None, CONF_MISSING
    if field not in conf_raw:
        return None, CONF_MISSING
    v = conf_raw[field]
    # ⭐ 归一逻辑**复用产品侧同一个函数**（HO 0917「对齐产品与评测的置信度语义」）：
    # "什么算一个合法置信度"只应有一处定义，否则两边各写一遍必然悄悄分叉。
    f = coerce_confidence(v)
    if f is not None:
        return round(f, 3), CONF_OK
    # 归一失败 ⇒ 细分类（评测侧要能区分是哪种"没拿到"）。产品侧不需要这么细。
    if v is None or isinstance(v, bool):
        return None, CONF_INVALID
    try:
        fv = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, CONF_INVALID
    if fv != fv or fv < 0.0 or fv > 1.0:  # NaN 或越界
        return None, CONF_RANGE
    return None, CONF_INVALID


def _ece(rows: list[dict[str, Any]], bins: int = 10) -> dict[str, Any]:
    """期望校准误差（ECE），**仅作记录项**（HO 0917：不替换旧 calibration 主判据）。

    定义：把 [0,1] 均分成 ``bins`` 箱，按箱内样本数加权，
    ``ECE = Σ (n_b / N) · |acc_b − conf_b|``。

    ⚠️ 它和 ``calibration_gap``（答对/答错两组均值之差）**衡量的不是同一件事**：
    前者是"置信度与实测正确率是否一致"，后者是"置信度能否区分对错"。
    二者阈值都是 0.10 纯属巧合，**不得互相替代或互相折算**。
    """
    usable = [r for r in rows if r["confidence_state"] == CONF_OK and r["confidence"] is not None]
    if not usable:
        return {"value": None, "bins": bins, "n": 0, "detail": []}
    acc_buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for r in usable:
        idx = min(bins - 1, int(float(r["confidence"]) * bins))
        acc_buckets[idx].append((float(r["confidence"]), 1 if r["ok"] else 0))
    n = len(usable)
    total = 0.0
    detail: list[dict[str, Any]] = []
    for i, b in enumerate(acc_buckets):
        if not b:
            continue
        conf = sum(x[0] for x in b) / len(b)
        acc = sum(x[1] for x in b) / len(b)
        total += (len(b) / n) * abs(acc - conf)
        detail.append(
            {
                "bin": f"[{i / bins:.1f},{(i + 1) / bins:.1f})",
                "n": len(b),
                "mean_confidence": round(conf, 4),
                "accuracy": round(acc, 4),
            }
        )
    return {"value": round(total, 4), "bins": bins, "n": n, "detail": detail}


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

        conf, conf_state = _read_confidence(conf_raw, field)
        rows.append(
            {
                "field": field,
                "ok": bool(ok),
                "got": got,
                "want": want,
                "confidence": conf,
                "confidence_state": conf_state,
            }
        )

    return {
        "id": sample["id"],
        "note": sample.get("note", ""),
        "rows": rows,
        "hallucinations": hallucinations,
        "correct": sum(1 for r in rows if r["ok"]),
        "total": len(rows),
    }


async def _call(prompt: str, text: str) -> tuple[dict[str, Any] | None, str | None, str, int, bool]:
    """调真实模型一次；返回 (content, error_kind, error_message, latency_ms, mocked)。

    ``prompt`` 由调用方用 ``build_cargo_prompt(today)`` 渲染好传进来 ——
    **必须是生产同一个渲染入口**，否则"验证"验的是另一套提示词。
    参考答案的日期也按同一个 ``today`` 展开，两侧同基准。

    ⚠️ `error_message` 必须一起返回：2026-09-16 重跑时 S10 两次 `bad_response`，
    结果文件里只有 `{"kind": "bad_response"}` —— 光看这个查不出原因，
    只能另写探针复现才看到"答案是 markdown 围栏里的 JSON"。
    **错误分类用来路由，错误原文用来定位**，两者缺一不可。

    ⚠️ `mocked` 必须一起返回（见 ``run()`` 里的逐条断言）：HO 的要求是
    「正式质量测试必须确认实际响应 ``mocked=false``」—— **配置层的检查不够**。
    ``main()`` 只在入口看 ``LLM_MOCK`` 与 ``LLM_API_KEY``，那证明的是"配置声称要发真实请求"；
    真正要证明的是"这一条响应确实不是桩"。两者不是一回事：
    桩可能由别的开关、别的注入路径产生，入口检查看不见。
    """
    try:
        res = await llm_gateway.chat_json(system=prompt, user=text)
    except LLMError as exc:
        return None, exc.kind, str(exc), 0, False
    return res.content, None, "", res.latency_ms, bool(res.mocked)


def _load_set(name: str) -> list[dict[str, Any]]:
    """按名字取样本集：``orig`` 是**跨轮可比的冻结基线**，``challenge`` 是挑战集。"""
    path = CHALLENGE_PATH if name == "challenge" else SAMPLES_PATH
    return json.loads(path.read_text(encoding="utf-8"))["samples"]


async def run(runs: int, which: str = "orig") -> dict[str, Any]:
    """跑一个或两个样本集；**每个集合单独统计、单独报告**（HO 0917 裁定）。

    ⚠️ 为什么必须分开：挑战集是刻意挑的易错输入，混进基线会同时毁掉两件事 ——
    ① 基线的**跨轮可比性**（准确率被稀释，看不出真实变化）；
    ② 挑战集本身的**诊断价值**（混在一起就分不清掉点出在哪一类输入上）。

    ⚠️ **判定只按主集合（orig）**：挑战集是"观察项"，不得反向改变基线的达标结论
    —— 否则"补挑战样本"就会被当成一条让指标变好看的捷径。
    """
    today = date.today()
    # 与生产同一个渲染入口：提示词里带上了"今天"，模型才有基准推算相对日期
    prompt = build_cargo_prompt(today)
    settings = get_settings()
    names = ["orig", "challenge"] if which == "both" else [which]
    calls = sum(len(_load_set(n)) for n in names) * runs

    print(f"供应商   : {settings.LLM_PROVIDER}  模型: {settings.LLM_MODEL}")
    print(f"端点     : {settings.LLM_BASE_URL}")
    print(f"样本集   : {', '.join(names)}  ⇒ 共 {calls} 次调用")
    print(f"今天     : {today.isoformat()}（相对日期按它推算）")
    print(f"提示词   : 携带今天={today.isoformat() in prompt}（{len(prompt)} 字符）\n")

    sets: dict[str, Any] = {}
    for n in names:
        samples = _load_set(n)
        print(f"---- 样本集 {n}：{len(samples)} 条 × {runs} 轮 ----")
        sets[n] = await _run_one_set(n, samples, runs, prompt, today)
        print("")

    primary = "orig" if "orig" in sets else names[0]
    result: dict[str, Any] = {
        "provider": settings.LLM_PROVIDER,
        "model": settings.LLM_MODEL,
        "base_url": settings.LLM_BASE_URL,
        "runs": runs,
        "today": today.isoformat(),
        "prompt_includes_today": today.isoformat() in prompt,
        "prompt_chars": len(prompt),
        "sample_sets": names,
        "primary_set": primary,
        "sets": sets,
    }
    # 主集合的字段平铺到顶层（兼容既有读数脚本）；**新分析请用 sets[...]**
    result.update(sets[primary])
    result["verdict"] = _verdict(sets[primary])
    return result


async def _run_one_set(
    set_name: str, samples: list[dict[str, Any]], runs: int, prompt: str, today: date
) -> dict[str, Any]:
    settings = get_settings()
    per_run: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    mock_incidents: list[dict[str, Any]] = []
    latencies: list[int] = []
    stable_ids: list[str] = []

    for run_no in range(1, runs + 1):
        outcomes: dict[str, Any] = {}
        for s in samples:
            content, kind, err_msg, latency, mocked = await _call(prompt, s["text"])
            if content is None:
                errors.append({"id": s["id"], "run": run_no, "kind": kind, "message": err_msg})
                print(f"  ✗ {s['id']} 调用失败: {kind}")
                print(f"      {err_msg[:300]}")
                continue
            # ---- 逐条断言：必须是真实响应 ----
            # HO 口径：正式质量测试必须确认实际响应 mocked=false；
            # 请求真模型但缺凭据时应报资源阻塞，**不得**自动降级后继续产出"真模型"结论。
            # 因此这条桩响应既不计数、也不参与判分 —— 直接丢弃，并单独留痕。
            if mocked:
                mock_incidents.append({"id": s["id"], "run": run_no})
                print(f"  ⛔ {s['id']} 响应 mocked=true：非真实模型输出，已丢弃、不计分")
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
    # 校准统计**只收置信度合法的行**；缺失/非法/越界单独计数，不折算成 0 参与均值。
    # （HO 0917：先修测量语义，再谈指标；把"没报告"当成 0 会让 gap 的含义失真。）
    ok_conf = [r["confidence"] for r in all_rows if r["ok"] and r["confidence_state"] == CONF_OK]
    bad_conf = [
        r["confidence"] for r in all_rows if not r["ok"] and r["confidence_state"] == CONF_OK
    ]
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
        "set_name": set_name,
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
        # 逐条断言的汇总：应为 0。>0 表示拿到过桩响应 ⇒ 本轮**不构成**真实模型质量证据。
        "mocked_responses": len(mock_incidents),
        "mock_incidents": mock_incidents,
        "calibration_gap": round(gap, 4),
        "mean_confidence_correct": round(statistics.fmean(ok_conf), 4) if ok_conf else None,
        "mean_confidence_wrong": round(statistics.fmean(bad_conf), 4) if bad_conf else None,
        # 置信度质量账：这些行**没进** calibration_gap，必须可见，否则"少算了哪些"无从复核。
        "confidence_usable": len(ok_conf) + len(bad_conf),
        "confidence_missing": sum(1 for r in all_rows if r["confidence_state"] == CONF_MISSING),
        "confidence_invalid": sum(1 for r in all_rows if r["confidence_state"] == CONF_INVALID),
        "confidence_out_of_range": sum(1 for r in all_rows if r["confidence_state"] == CONF_RANGE),
        # ECE：**记录项**（HO 0917 裁定不替换主判据）。不进 verdict、不参与 all_met。
        "ece": _ece(all_rows),
        "latency_ms": {
            "mean": int(statistics.fmean(latencies)) if latencies else None,
            "p95": int(sorted(latencies)[int(len(latencies) * 0.95) - 1]) if latencies else None,
        },
        "by_field": by_field,
        "stable_sample_ids": stable_ids if runs > 1 else None,
        # ⚠️ 旧版只落最后一轮的明细 ⇒ 前面轮次的聚合结果**无法复算**（HO 0917 点名要修）。
        # 现在每一轮都落：既落判分明细，也落模型原始输出（raw），便于事后重算任何指标。
        "per_run_detail": [
            {
                "run": r["run"],
                "samples": [
                    {
                        "id": g["id"],
                        "note": g["note"],
                        "correct": g["correct"],
                        "total": g["total"],
                        "hallucinations": g["hallucinations"],
                        "rows": g["rows"],
                    }
                    for g in r["graded"]
                ],
                "raw": r["raw"],
            }
            for r in per_run
        ],
        # 保留旧字段（最后一轮）以免已有读数脚本失效；**新分析请用 per_run_detail**。
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
    """按**跑之前定死的**阈值逐条判定；返回每条的达标情况与总评。

    注意 ``real_response`` 与其余四条**性质不同**：它不衡量模型质量，
    而是衡量**这轮结果能不能算数**。桩响应出现一次，整轮就不能被引用为
    "真实模型质量"证据 —— 所以它必须进 ``all_met``，否则一条被丢弃的桩响应
    会让"数值看着还不错"的结论被当成真的。
    """
    checks = {
        "real_response": r["mocked_responses"] == 0,
        "field_accuracy": r["field_accuracy"] >= THRESHOLDS["field_accuracy_min"],
        "hallucination": r["hallucinations"] <= THRESHOLDS["hallucination_max"],
        "calibration": r["calibration_gap"] > THRESHOLDS["calibration_gap_min"],
        "parse_failure": r["parse_failures"] <= THRESHOLDS["parse_failure_max"],
    }
    return {"checks": checks, "all_met": all(checks.values())}


def _report(r: dict[str, Any]) -> None:
    print("\n================ H7a 真实模型质量 ================")
    n_mock = r["mocked_responses"]
    print(
        f"真实响应   : 桩响应 {n_mock} 条  {'✅ 全部 mocked=false' if n_mock == 0 else '⛔ 出现过 mocked=true'}"
    )
    print(
        f"字段准确率 : {r['field_accuracy']:.2%}  ({r['correct']}/{r['total']})"
        f"  阈值 ≥ {THRESHOLDS['field_accuracy_min']:.0%}"
    )
    print(f"编造次数   : {r['hallucinations']}  阈值 ≤ {THRESHOLDS['hallucination_max']}")
    # ⚠️ 符号必须用 `+.3f` 让格式化器自己给正号。旧版写死 `+{gap}` ⇒ 负值被拼成 `+-0.174`，
    # 终端上看着像 +0.174（HO 0917 点名要修）——**符号以结果文件为准，别拿终端拼接当符号**。
    print(f"置信度校准 : {r['calibration_gap']:+.3f}  阈值 > {THRESHOLDS['calibration_gap_min']}")
    print(
        f"  └ 参与样本: 合法 {r['confidence_usable']} / 缺失 {r['confidence_missing']}"
        f" / 非法 {r['confidence_invalid']} / 越界 {r['confidence_out_of_range']}"
        "  （后三类不参与 gap）"
    )
    ece = r.get("ece") or {}
    ece_v = ece.get("value")
    print(
        f"  └ ECE(记录项): {'n/a' if ece_v is None else f'{ece_v:.4f}'}"
        f"  n={ece.get('n', 0)}  ⚠️ 仅记录，不参与判定（与 gap 不是同一指标）"
    )
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

    _report_other_sets(r)

    print("\n-- 判定 --")
    print(f"  （判据来自样本集 **{r.get('primary_set', 'orig')}**；其余集合见上方，不参与判定）")
    for k, v in r["verdict"]["checks"].items():
        print(f"  {'✅' if v else '❌'} {k}")
    print(f"\n总评: {'全部达标' if r['verdict']['all_met'] else '未全部达标（如实记录，不折算）'}")


def _report_other_sets(r: dict[str, Any]) -> None:
    """把非主集合（挑战集）**单独**打印一份。

    ⚠️ 它不参与上面的判定：挑战集是刻意挑的易错输入，用它去改达标结论
    等于让"补样本"变成一条把指标做好看的捷径（HO 0917 明确禁止）。
    """
    sets = r.get("sets") or {}
    if len(sets) <= 1:
        return
    primary = r.get("primary_set", "orig")
    for name, s in sets.items():
        if name == primary:
            continue
        print(f"\n-- 样本集 {name}（**观察项，不参与判定**）--")
        if not s.get("total"):
            print("   （无可评分调用 —— 见 errors 明细；不作任何质量结论）")
            continue
        print(
            f"  字段准确率 : {s['field_accuracy']:.2%}  ({s['correct']}/{s['total']})   "
            f"【仅观察，无阈值】"
        )
        print(f"  编造次数   : {s['hallucinations']}   【仅观察】")
        print(f"  置信度校准 : {s['calibration_gap']:+.3f}   【仅观察】")
        print(f"  调用失败   : {s['parse_failures']}")
        ece = s.get("ece") or {}
        ece_v = ece.get("value")
        ece_txt = "n/a" if ece_v is None else f"{ece_v:.4f}"
        print(f"  ECE(记录项): {ece_txt}  n={ece.get('n', 0)}")
        print(
            f"  置信度质量 : 合法 {s['confidence_usable']} / 缺失 {s['confidence_missing']}"
            f" / 非法 {s['confidence_invalid']} / 越界 {s['confidence_out_of_range']}"
        )
        bad = [x for g in s.get("per_sample", []) for x in g.get("rows", []) if not x.get("ok")]
        print(f"  错答字段   : {len(bad)} 个（本集**不**折算成达标/不达标）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H7a 真实模型质量验证（非 CI，真发请求、会产生费用）")
    ap.add_argument("--runs", type=int, default=2, help="每条样本跑几轮（>1 时额外统计多轮一致性）")
    ap.add_argument(
        "--set",
        choices=("orig", "challenge", "both"),
        default="orig",
        help="跑哪个样本集（默认 orig＝冻结基线；challenge 为挑战集；both 两个都跑、分别报告）",
    )
    ap.add_argument("--out", default="", help="结果 JSON 落点")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发生的调用量与费用估算")
    args = ap.parse_args(argv)

    names = ("orig", "challenge") if args.set == "both" else (args.set,)
    samples = [s for n in names for s in _load_set(n)]
    calls = len(samples) * args.runs
    chars = sum(len(s["text"]) for s in samples) * args.runs + len(build_cargo_prompt()) * calls
    print(
        f"样本集 {args.set}（{', '.join(names)}）共 {len(samples)} 条 × {args.runs} 轮 = {calls} 次调用；"
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

    result = asyncio.run(run(args.runs, args.set))
    _report(result)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入: {out}")
    if result["mocked_responses"] > 0:
        # 资源阻塞，不是"模型不达标"。与 402/缺 Key 同类：**没跑成**（NOT_RUN）。
        print(
            f"\n⛔ 本轮出现 {result['mocked_responses']} 条 `mocked=true` 的响应。"
            "\n   这意味着**至少有一条输出不是真模型给的**，本轮结果不得作为真实模型质量证据："
            '\n   · 该条已丢弃、未参与判分；但"其余条是真模型"这件事本身也没被证明。'
            "\n   · 按 HO 口径，此时应报**资源阻塞**（配置声称发真请求、实际拿到桩），"
            '\n     不得自动降级后继续产出"真模型"结论。'
            "\n   · 排查方向：`LLM_MOCK` 的实际取值来源、是否有别的注入路径覆盖了网关"
            "\n     （用 scripts/llm_key_doctor.py 先确认 Key 与计费方式）。",
            file=sys.stderr,
        )
        return 2
    if result["total"] == 0:
        # 退出码区分三态：0=达标 / 1=未达标 / 2=**没跑成**（NOT_RUN，不是失败）
        return 2
    return 0 if result["verdict"]["all_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
