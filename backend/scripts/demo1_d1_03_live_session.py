"""D1-03 live 证据（**经真实会话／作业入口**，不是直接调 `run_agent`）。

为什么另起一份，而不是扩 `demo1_live_ag02_evidence.py`
------------------------------------------------------
两者回答的**不是同一个问题**：

* `demo1_live_ag02_evidence.py` 验的是「模型对这份报价单能否产出类型化、有来源的
  提案」—— 它自己造上下文、不读库；因此它证明不了「**在真实会话里**跑一次会怎样」；
* 本脚本验的是 HO 0917-3 待裁决清单第 2 行：**从会话／作业入口切入跑一次 D1-03**，
  把「作业 id / 成果 id 与版本 / 提取结果 / 模型模式 / 来源核对结果」一并记下来。
  走的是 HTTP API → `sessions.create_session` → `agentjobs.submit_job` →
  `agentjobs.run_job` 这条**生产入口**，库里会真的留下行。

输入是谁
--------
`backend/scripts/fixtures/DEMO1-canonical-sample-quotation.txt`（合同 §3.1 canonical
夹具；与主演示第 2 步、CI 端到端用的是同一份）。**走真实 multipart 上传 → 提取**，
不把文本塞进 `quote_text` —— 塞进去就不是"附件文本"这条链了。

判据（fail-closed，且**不重跑到碰巧通过**）
------------------------------------------
1. 作业行的 `mocked` 必须为 **false**（真模型调用）。它取自**作业行**，
   不是"我以为我配了 live"（本机 `backend/.env.local` 会把 `LLM_MOCK` 顶掉）；
2. `unverified_sources` 必须为**空**。非空 ⇒ 记 **FAIL**、照样落盘证据、非 0 退出；
3. 枚举不出「这次的提取版本」（`extracted_chars` / `text_source`）⇒ 不出证据。

为什么"非空就退出非 0"而不是打个警告
------------------------------------
D1-03 的原文是 `a typed, **sourced** proposal` —— 来源核对不上，这一半就不成立。
把它降级成警告，等于用"作业成功"冒充"sourced"，而作业成功在不带输入时也会发生
（`quote_unparsed` 那种"空成功"）。

它**不是**什么（合同 §3.2）
---------------------------
* 不是模型质量结论（H7a 的 `calibration` 仍记未达标；本脚本不产生任何准确率）；
* 不是 R1 验收，不能替代任何一条 D1；
* 不是 D1-05（离线/人工可完成）的证据 —— live 与 offline **互不替代**；
* 不宣称真实航线、真实运力或真实成交（输入是标注为合成的夹具）。

前置
----
后端已在 `127.0.0.1:8000` 运行，库已 `migrate.py` 且跑过演示种子
（`seed_demo.py` → `seed_entrust_demo.py`）。脚本会**真写**一张委托
（创建→提交→受理）并上传一份附件 —— 因此**不能**对着只读环境跑。

用法（**在 `backend` 目录下**）：

    python scripts/demo1_d1_03_live_session.py
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core.config import get_settings  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FIXTURE = HERE / "fixtures" / "DEMO1-canonical-sample-quotation.txt"
OUT_DIR = REPO / "docs" / "entrust" / "milestones" / "evidence"

BASE = "http://127.0.0.1:8000"
API = BASE + "/api/v1"

#: 演示种子里的固定身份（与 `utils/auth.js`、设备走查同一套 code）
CODE_SHIPPER = "seed-shipper"
CODE_OWNER = "seed-owner"
ORG_WORKBENCH = "演示经营主体·工作台"

#: ⚠️ 本机 `http_proxy` 会接管对 `127.0.0.1` 的请求并回 502 ⇒ 走空代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    idem: str | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 300.0,
) -> tuple[int, Any]:
    """一次 HTTP 调用 → `(status, data)`；**4xx/5xx 不抛异常**，把状态码交回调用方。

    负例与失败要能被**分清**（`-1` 才是"没发出去"），所以这里单独捕 `HTTPError`。
    """
    data = body if body is not None else None
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if idem:
        headers["Idempotency-Key"] = idem
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return int(resp.status), (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return int(exc.code), json.loads(raw)
        except Exception:  # noqa: BLE001
            return int(exc.code), raw[:400]
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def _must(step: str, status: int, data: Any) -> Any:
    """把"这一步必须是 200"写成一条判据，失败时把**服务端原话**带出来。"""
    if status != 200:
        raise SystemExit(f"[{step}] 失败：HTTP {status} {str(data)[:400]}")
    return data


def _health() -> bool:
    """后端可达性（`/healthz` 在 **API 前缀之外**，所以不走 `_call`）。"""
    try:
        with _OPENER.open(BASE + "/healthz", timeout=5.0) as resp:
            return int(resp.status) == 200
    except Exception:  # noqa: BLE001
        return False


def _login(code: str) -> str:
    status, data = _call("POST", "/auth/login", payload={"code": code})
    if status != 200 or not isinstance(data, dict):
        raise SystemExit(f"登录失败（code={code}）：HTTP {status} {str(data)[:300]}")
    return str(data.get("access_token") or "")


def _multipart(
    field: str, filename: str, content: bytes, extra: dict[str, str]
) -> tuple[bytes, str]:
    boundary = "----demo1d103" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in extra.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
        f'filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'.encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _target_org(shipper_token: str) -> tuple[int, str]:
    """货主侧「我授权出去的组织」里取演示经营主体（**不写死 id**：种子重铺会变）。"""
    data = _must(
        "GET /my-entrustments", *_call("GET", "/entrust/my-entrustments", token=shipper_token)
    )
    for row in data.get("items") or []:
        if str((row or {}).get("org_name") or "") == ORG_WORKBENCH:
            return int(row["org_id"]), ORG_WORKBENCH
    raise SystemExit(
        f"货主名下没有指向「{ORG_WORKBENCH}」的生效授权 ⇒ 先跑 seed_entrust_demo.py。"
        f"实际：{[(r or {}).get('org_name') for r in (data.get('items') or [])]}"
    )


def run() -> dict[str, Any]:
    started = _now()
    settings = get_settings()
    fixture_text = FIXTURE.read_text(encoding="utf-8")

    # ── 0. 后端必须在（不在就是**环境错误**，不是业务结论）──────────────────
    if not _health():
        raise SystemExit(
            f"后端不可达（{BASE}/healthz）⇒ 环境错误。"
            "本脚本不负责起后端：起后端的进程一结束，uvicorn 就会被回收。"
        )

    steps: list[dict[str, Any]] = []

    def note(step: str, detail: str) -> None:
        steps.append({"step": step, "detail": detail})

    # ── 1. 第 1 步：客户提交 → A1 受理（真实入口）─────────────────────────
    shipper = _login(CODE_SHIPPER)
    owner = _login(CODE_OWNER)
    org_id, org_name = _target_org(shipper)

    created = _must(
        "POST /assignments",
        *_call(
            "POST",
            "/entrust/assignments",
            token=shipper,
            payload={
                "title": "D1-03 live 证据 · 报价单解析（200 吨演示货）",
                "cargo_summary": "钢材 · 南宁 → 贵港（演示货物，非真实客户材料）",
                "quantity": 800,
                "quantity_unit": "吨",
            },
            idem=uuid.uuid4().hex,
        ),
    )
    aid = int(created["assignment_id"])
    note("第1步 客户建单", f"assignment_id={aid} revision={created.get('revision')!r}")

    submitted = _must(
        "POST /assignments/{id}/submit",
        *_call(
            "POST",
            f"/entrust/assignments/{aid}/submit",
            token=shipper,
            payload={"org_id": org_id, "expected_revision": created.get("revision")},
            idem=uuid.uuid4().hex,
        ),
    )
    note("第1步 客户提交", f"status={submitted.get('status')!r} org={org_name}({org_id})")

    claimed = _must(
        "POST /assignments/{id}/claim",
        *_call(
            "POST",
            f"/entrust/assignments/{aid}/claim",
            token=owner,
            payload={},
            idem=uuid.uuid4().hex,
        ),
    )
    note(
        "第1步 A1 受理",
        f"status={claimed.get('status')!r} claimed_by={claimed.get('claimed_by')!r}",
    )

    ctx = _must(
        "GET /assignments/{id}/session-context",
        *_call("GET", f"/entrust/assignments/{aid}/session-context", token=owner),
    )
    eid = int(ctx["entrustment_id"])
    note("第1步 取授权上下文", f"entrustment_id={eid} note={str(ctx.get('note'))[:80]!r}")

    # ── 2. 第 2 步：上传 canonical 夹具 → 提取 → 引用 → 真实会话里调 AG-02 ──
    raw, ctype = _multipart(
        "file", FIXTURE.name, fixture_text.encode("utf-8"), {"entrustment_id": str(eid)}
    )
    uploaded = _must(
        "POST /attachments",
        *_call(
            "POST",
            "/entrust/attachments",
            token=owner,
            body=raw,
            content_type=ctype,
            idem=uuid.uuid4().hex,
        ),
    )
    att_id = int(uploaded["attachment_id"])
    note(
        "第2步 上传",
        f"attachment_id={att_id} filename={uploaded['filename']} bytes={uploaded['size_bytes']} "
        f"extract_status={uploaded['extract_status']!r}",
    )

    extracted = _must(
        "POST /attachments/{id}/extract",
        *_call(
            "POST",
            f"/entrust/attachments/{att_id}/extract",
            token=owner,
            payload={},
            idem=uuid.uuid4().hex,
        ),
    )
    if extracted.get("extract_status") != "done" or not extracted.get("extracted_chars"):
        raise SystemExit(f"提取未完成 ⇒ 没有可引用的文本，D1-03 不成立：{extracted}")
    note(
        "第2步 提取完成",
        f"extract_status='done' extracted_chars={extracted['extracted_chars']} "
        f"text_source={extracted.get('text_source')!r}",
    )

    sess = _must(
        "POST /entrustments/{id}/sessions",
        *_call(
            "POST",
            f"/entrust/entrustments/{eid}/sessions",
            token=owner,
            payload={
                "assignment_id": aid,
                "agent_specialty": "agent_02",
                "title": "D1-03 · 报价单解析会话",
            },
            idem=uuid.uuid4().hex,
        ),
    )
    sid = int(sess["session_id"])
    note("第2步 开专属会话", f"session_id={sid} specialty=agent_02")

    job = _must(
        "POST /sessions/{id}/jobs",
        *_call(
            "POST",
            f"/entrust/sessions/{sid}/jobs",
            token=owner,
            payload={"base_revision": 1, "input": {"attachment_id": att_id}},
            idem=uuid.uuid4().hex,
        ),
    )
    jid = int(job["job_id"])
    note("第2步 提交作业", f"job_id={jid} status={job.get('status')!r}（提交与执行分离）")

    t0 = time.monotonic()
    run_res = _call(
        "POST", f"/entrust/agent/jobs/{jid}/run", token=owner, payload={}, idem=uuid.uuid4().hex
    )
    if run_res[0] != 200:
        raise SystemExit(
            f"[POST /agent/jobs/{jid}/run] 失败：HTTP {run_res[0]} {str(run_res[1])[:400]}"
        )
    run_seconds = round(time.monotonic() - t0, 1)

    detail = _must(
        "GET /agent/jobs/{id}",
        *_call("GET", f"/entrust/agent/jobs/{jid}", token=owner),
    )
    jrow = detail.get("job") or {}
    envelope = jrow.get("envelope") or {}
    attempts = detail.get("attempts") or []

    proposal_types = [
        str(p.get("artifact_type")) for p in (envelope.get("artifact_proposals") or [])
    ]
    kinds = [str((r or {}).get("kind")) for r in (envelope.get("source_refs") or [])]
    unverified = envelope.get("unverified_sources") or []
    missing = envelope.get("proposal_missing_fields") or envelope.get("missing_fields") or []

    note(
        "第2步 作业终态",
        f"status={jrow.get('status')!r} mocked={jrow.get('mocked')!r} "
        f"proposals={proposal_types} source_kinds={kinds} "
        f"unverified={len(unverified)} missing={missing}",
    )

    # ── 3. 采纳为成果（D1-03 要的 "typed proposal" 落到 artifact/revision 上）──
    artifact: dict[str, Any] = {}
    if jrow.get("status") == "succeeded" and proposal_types:
        parsed = next(
            (
                p
                for p in envelope.get("artifact_proposals") or []
                if p.get("artifact_type") == "quote_parsed"
            ),
            None,
        )
        if parsed is not None:
            adopted = _must(
                "POST /agent/jobs/{id}/adopt",
                *_call(
                    "POST",
                    f"/entrust/agent/jobs/{jid}/adopt",
                    token=owner,
                    payload={
                        "artifact_type": "quote_parsed",
                        "payload": parsed.get("payload") or {},
                        "note": "D1-03 证据：人工确认后采纳（提案 → 成果）",
                    },
                    idem=uuid.uuid4().hex,
                ),
            )
            art_id = int(adopted["artifact_id"])
            revs = _must(
                "GET /artifacts/{id}/revisions",
                *_call("GET", f"/entrust/artifacts/{art_id}/revisions", token=owner),
            )
            artifact = {
                "artifact_id": art_id,
                "artifact_type": "quote_parsed",
                "payload": adopted.get("payload") or parsed.get("payload"),
                "revision_no": (revs.get("items") or [{}])[-1].get("revision_no")
                if revs.get("items")
                else None,
                "revision_count": len(revs.get("items") or []),
            }
            note(
                "第3步 采纳为成果",
                f"artifact_id={art_id} revision_no={artifact['revision_no']!r} "
                f"payload={json.dumps(artifact['payload'], ensure_ascii=False)[:160]}",
            )

    is_live = jrow.get("mocked") is False
    source_ok = len(unverified) == 0 and "attachment_text" in kinds
    verdict = "PASS" if (is_live and source_ok and artifact) else "FAIL"

    host = settings.LLM_BASE_URL.split("//")[-1].split("/")[0]
    return {
        "evidence_label": "D1-03 live invocation evidence（**经真实会话／作业入口**）",
        "contract_ref": "DEMO-1-contract-v1.0 §9 D1-03 / §3.1 Canonical fixture / §10.1 第 2 步",
        "recorded_at": started.isoformat(),
        "verdict": verdict,
        "verdict_reason": {
            "live（作业行 mocked=false）": is_live,
            "来源可核对（unverified_sources 为空）": len(unverified) == 0,
            "来源含 attachment_text（读的是附件文本）": "attachment_text" in kinds,
            "产出类型化提案并采纳为成果": bool(artifact),
        },
        "ids": {
            "assignment_id": aid,
            "entrustment_id": eid,
            "session_id": sid,
            "job_id": jid,
            "attachment_id": att_id,
            "artifact_id": artifact.get("artifact_id"),
            "artifact_revision_no": artifact.get("revision_no"),
        },
        "invocation": {
            "provider": settings.LLM_PROVIDER,
            "model": settings.LLM_MODEL,
            "endpoint_host": host,  # 只记主机名，不含任何凭据
            "mocked_from_job_row": jrow.get("mocked"),  # 判据取自**作业行**
            "run_seconds": run_seconds,
            "agent": "AG-02（agent_02 / 方案与采购）",
            "attempts": [
                {
                    "attempt_no": a.get("attempt_no"),
                    "status": a.get("status"),
                    "latency_ms": a.get("latency_ms"),
                    "mocked": a.get("mocked"),
                    "error_kind": a.get("error_kind"),
                }
                for a in attempts
            ],
        },
        "input": {
            "fixture_path": str(FIXTURE.relative_to(REPO)).replace("\\", "/"),
            "fixture_sha256": _digest(fixture_text),
            "fixture_data_label": "合成 / 人工录入（Synthetic — 不是真实客户材料）",
            "source_channel": "attachment_text（附件提取文本）",
            "upload_route": "POST /entrust/attachments（真实 multipart）→ POST /extract",
            "extracted_chars": extracted.get("extracted_chars"),
            "text_source": extracted.get("text_source"),
        },
        "result": {
            "job_status": jrow.get("status"),
            "summary": envelope.get("summary"),
            "proposal_types": proposal_types,
            "source_kinds": kinds,
            "unverified_sources": unverified,
            "proposal_missing_fields": missing,
            "requires_review": envelope.get("requires_review"),
            "artifact": artifact,
        },
        "steps": steps,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "app_env": settings.APP_ENV,
            "llm_mock_in_caller_process": settings.LLM_MOCK,
            "note": (
                "`llm_mock_in_caller_process` 只是**调用方进程**读到的配置，**不是判据**："
                "本机 backend/.env.local 会把 `LLM_MOCK` 顶掉，而调用方与后端可以是"
                "两套 env（第一次实测就是这样：字段为 true、作业行 mocked=false）。"
                "**唯一的判据是作业行的 `mocked`**（见 `invocation.mocked_from_job_row`）——"
                "「我以为配了 live」不能当 live 证据。"
            ),
        },
        "not_evidence_of": [
            "不是模型质量结论（H7a 的 calibration 仍记未达标；本脚本不产生准确率）",
            "不是 R1 验收，不能替代任何一条 D1",
            "不是 D1-05（离线/人工可完成）的证据 —— live 与 offline 互不替代",
            "不是真实航线、真实运力或真实成交的证据（输入是标注为合成的夹具）",
        ],
    }


def main() -> int:
    evidence = run()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = evidence["recorded_at"].replace(":", "").replace("-", "")[:15]
    out = OUT_DIR / f"D1-03-live-session-{stamp}.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    ids = evidence["ids"]
    res = evidence["result"]
    inv = evidence["invocation"]
    print("D1-03 live 证据（真实会话／作业入口）已落盘：")
    print(f"  {out.relative_to(REPO)}")
    print(
        f"  IDs       : assignment={ids['assignment_id']} entrustment={ids['entrustment_id']} "
        f"session={ids['session_id']} job={ids['job_id']} attachment={ids['attachment_id']} "
        f"artifact={ids['artifact_id']} rev={ids['artifact_revision_no']}"
    )
    print(
        f"  模型      : {inv['provider']} / {inv['model']}（mocked={inv['mocked_from_job_row']}）"
        f"  单次推进 {inv['run_seconds']}s"
    )
    print(f"  提案      : {res['proposal_types']}")
    print(f"  来源      : {res['source_kinds']}")
    print(f"  未核验来源: {res['unverified_sources'] or '无'}")
    print(
        f"  提取      : {evidence['input']['extracted_chars']} 字（{evidence['input']['text_source']}）"
    )
    print(f"  结论      : {evidence['verdict']}")
    for k, v in (evidence["verdict_reason"] or {}).items():
        print(f"    - {k}: {v}")

    if evidence["verdict"] != "PASS":
        print("\n⚠️ 来源核对未通过 ⇒ 按 **FAIL** 记录（证据已落盘，不靠重跑掩盖）。")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
