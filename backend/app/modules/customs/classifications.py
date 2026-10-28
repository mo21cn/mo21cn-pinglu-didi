"""智慧关务（关域）D2 服务层 —— 商品归类辅助（模型建议 + 人工定稿）。

## 这一层要解决的真问题不是"调模型"，是"模型不在的时候怎么办"

归类建议依赖大模型，而模型会不可用：无 Key、超时、限流、余额不足、返回解析不了。
若界面只有一个"候选列表"出口，模型一挂就只剩空列表 —— 演示现场分不清是
"没有匹配项"还是"服务坏了"，事后也查不出当时发生了什么。

所以本层把**降级**做成一等公民：

1. `status` 多一档 `manual_required`：它不是错误态，是"模型没帮上忙，请人来定"的
   正常分支。⛔ 不把降级包装成 200 + 空候选；
2. `degraded.kind` 机器可读（白名单，与 `agent.llm.LLMError.kind` 同源），
   `degraded.reason` 是**给用户看的一句话**，由白名单映射生成。
   ⛔ 绝不回显 `str(exc)`：里面可能带 base_url、模型名、凭证片段；
3. "模型给了 0 条候选"也记降级（`empty_result`）—— 那和"调用失败"是两件事，
   但对用户是同一句话："这次没给出建议，请手工填写"。

## 三条硬纪律

* **模型永不落结论**：模型的输出只进候选表（`source='model'`），
  `cus_classification.hs_code` 只有人工确认才写。把结论直接交给模型，
  等于把归类责任转移给一个不可审计的东西；
* **候选只追加**：人工结果写 `source='manual'` 的新行，⛔ 不覆盖模型行；
  否则事后无从回答"这个编码到底是谁定的"；
* **人工通路不依赖模型可用性**（AC-08 同族）：`submit_manual` 不碰 LLM，
  降级状态下照样能出结论。演示时这就是"断源仍可作业"的第二处实证。

## 可见性

与 D1 同一口径：本人，或**记录所属组织**的成员；不可见一律 404（不泄漏存在性）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.agent.llm import LLMError, chat_json

TS_FMT = "%Y-%m-%d %H:%M:%S"

#: 归类记录状态（`manual_required` 不是错误态，见模块头）
STATUSES = ("pending", "suggested", "manual_required", "confirmed")

#: 候选项来源。⛔ 两者不可互相覆盖
CANDIDATE_SOURCES = ("model", "manual")

#: 一次最多展示几条候选（超出只会让演示现场念不完，且排名靠后的置信度本就低）
MAX_CANDIDATES = 5

#: 降级类别 → 给用户看的一句话。
#:
#: 白名单的作用有两个：① 用户看到的是可复述的中文，不是第三方报错；
#: ② 新增的异常类别会落到 `unknown`，**不会**被顺手把原文透出去。
DEGRADED_REASON: dict[str, str] = {
    "timeout": "归类模型响应超时，本次没有给出建议",
    "network": "归类模型暂时不可达（网络或服务端异常），本次没有给出建议",
    "auth": "归类模型凭证无效，本次没有给出建议",
    "rate_limit": "归类模型触发限流，本次没有给出建议",
    "quota": "归类模型账户余额或配额不足，本次没有给出建议",
    "bad_response": "归类模型返回的内容无法解析，本次没有给出建议",
    "empty_result": "归类模型本次没有给出任何候选编码",
    "unknown": "归类模型本次未给出可用结果",
}

#: 人工通路的固定提示语（页面直接展示，不在前端拼字符串）
MANUAL_HINT_OPEN = "模型没有给出建议，可直接手工填写税则号列并确认结论"
MANUAL_HINT_SUGGESTED = "也可手工填写或改判；人工结论记为独立候选，不覆盖模型建议"
MANUAL_HINT_DONE = "结论已确认。候选表只追加，如需更正请新建一条归类记录"


class ClassificationError(Exception):
    """归类领域异常基类（router 负责翻成 HTTP）。"""


class ClassificationNotFoundError(ClassificationError):
    """记录不存在，**或**对当前用户不可见（两者不区分）。"""


class ClassificationForbiddenError(ClassificationError):
    """越权写（指定的 org 不是操作者所在组织）。"""


class ClassificationStateError(ClassificationError):
    """状态不允许该操作（如已确认后重复确认）。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 `ent_*` / `cus_*` 表的时间列口径一致）。"""
    return datetime.now(tz=None).replace(microsecond=0)


def _ts(value: datetime | None = None) -> str:
    return (value or utcnow_naive()).strftime(TS_FMT)


def _norm_time(value: Any) -> str | None:
    """把 sqlite 的 TEXT / MySQL 的 DATETIME 统一成 `YYYY-MM-DD HH:MM:SS`。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime(TS_FMT)
    return str(value)[:19]


def _visible_clause(org_ids: frozenset[int] | set[int]) -> tuple[str, dict[str, Any]]:
    """可见性谓词：本人 OR 所属组织成员。组织集合为空时就只剩"本人"。"""
    ids = sorted(int(x) for x in org_ids)
    if not ids:
        return "c.owner_user_id = :uid", {}
    placeholders = ", ".join(f":org{i}" for i in range(len(ids)))
    params = {f"org{i}": oid for i, oid in enumerate(ids)}
    return (
        f"(c.owner_user_id = :uid OR (c.org_id IS NOT NULL AND c.org_id IN ({placeholders})))",
        params,
    )


def _record(session: Session, classification_id: int) -> Any:
    row = (
        session.execute(
            text(
                "SELECT id, declaration_id, owner_user_id, org_id, product_name, "
                "product_desc, status, hs_code, degraded_kind, degraded_reason, "
                "model_name, model_mocked, decided_by_user_id, decided_at, "
                "created_at, updated_at "
                "FROM cus_classification WHERE id = :id"
            ),
            {"id": classification_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ClassificationNotFoundError("归类记录不存在或不可见")
    return row


def _candidates_of(session: Session, classification_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT id, hs_code, label, confidence, source, rank_no, rationale, "
                "actor_user_id, created_at "
                "FROM cus_classification_candidate "
                "WHERE classification_id = :cid "
                "ORDER BY source ASC, rank_no ASC, id ASC"
            ),
            {"cid": classification_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "id": int(r["id"]),
            "hs_code": str(r["hs_code"]),
            "label": r["label"],
            "confidence": (None if r["confidence"] is None else float(r["confidence"])),
            "source": str(r["source"]),
            "rank": int(r["rank_no"]),
            "rationale": r["rationale"],
            "actor_user_id": r["actor_user_id"],
            "created_at": _norm_time(r["created_at"]),
        }
        for r in rows
    ]


def _degraded_of(row: Any) -> dict[str, str] | None:
    kind = row["degraded_kind"]
    if not kind:
        return None
    return {"kind": str(kind), "reason": str(row["degraded_reason"] or DEGRADED_REASON["unknown"])}


def _manual_entry_of(status: str) -> dict[str, Any]:
    """人工通路的可用性 —— 由**服务端**判定，⛔ 不让前端自己按状态猜。"""
    if status == "confirmed":
        return {"available": False, "hint": MANUAL_HINT_DONE}
    if status == "suggested":
        return {"available": True, "hint": MANUAL_HINT_SUGGESTED}
    return {"available": True, "hint": MANUAL_HINT_OPEN}


def _out(session: Session, row: Any) -> dict[str, Any]:
    status = str(row["status"])
    return {
        "id": int(row["id"]),
        "declaration_id": row["declaration_id"],
        "product_name": str(row["product_name"]),
        "product_desc": row["product_desc"],
        "status": status,
        "hs_code": row["hs_code"],
        "degraded": _degraded_of(row),
        "manual_entry": _manual_entry_of(status),
        "candidates": _candidates_of(session, int(row["id"])),
        "model": {
            "name": row["model_name"],
            "mocked": bool(row["model_mocked"]),
        },
        "decided_by_user_id": row["decided_by_user_id"],
        "decided_at": _norm_time(row["decided_at"]),
        "created_at": _norm_time(row["created_at"]),
        "updated_at": _norm_time(row["updated_at"]),
    }


# ---------------------------------------------------------------------------
# 模型调用（唯一一处依赖 LLM 的地方）
# ---------------------------------------------------------------------------

_SYSTEM = (
    "你是中国海关进出口商品归类助理。根据商品名称与描述，给出最多 5 个候选税则号列"
    "（HS 编码），按可能性从高到低排列。只输出 JSON："
    '{"candidates":[{"hs_code":"10位数字或8位数字","label":"商品名称","'
    'confidence":0到1之间的小数,"rationale":"一句话依据"}]}。'
    "不确定时宁可少给，不要编造。"
)


def _mock_classify(prompt: str) -> dict[str, Any]:
    """`LLM_MOCK=true` 时的规则模板（CI / 无 Key 开发用，不出网）。

    刻意只覆盖"能认出来"的少数品类：认不出来就返回空列表 —— 那会走 `empty_result`
    降级分支，正好让 CI 也覆盖到"模型没建议"这条路径，而不是靠人工构造。
    """
    text_ = prompt.lower()
    if "钢" in prompt or "steel" in text_:
        return {
            "candidates": [
                {
                    "hs_code": "7308900000",
                    "label": "钢铁结构体及其部件",
                    "confidence": 0.72,
                    "rationale": "按材质（钢铁）与用途（结构件）归入 73 章",
                }
            ]
        }
    if "塑料" in prompt or "plastic" in text_:
        return {
            "candidates": [
                {
                    "hs_code": "3926909090",
                    "label": "其他塑料制品",
                    "confidence": 0.61,
                    "rationale": "未列名塑料制品归入 3926",
                }
            ]
        }
    return {"candidates": []}


def _clean_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把模型输出收敛成可入库的候选：去空、去重、排序、截断。

    ⛔ 这里不做"猜"：`hs_code` 不是 8/10 位数字的、重复的、缺 `hs_code` 的，一律丢弃。
    宁可少一条候选，也不让一个格式不对的编码出现在页面上被当成结论。
    """
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("hs_code") or "").strip()
        if not code.isdigit() or len(code) not in (8, 10) or code in seen:
            continue
        seen.add(code)
        conf = item.get("confidence")
        confidence: float | None = None
        if isinstance(conf, (int, float)) and 0 <= float(conf) <= 1:
            confidence = round(float(conf), 4)
        label = item.get("label")
        rationale = item.get("rationale")
        cleaned.append(
            {
                "hs_code": code,
                "label": (str(label)[:256] if label else None),
                "confidence": confidence,
                "rationale": (str(rationale)[:1024] if rationale else None),
            }
        )
    # 置信度缺失的排最后（未知不该排在已知前面），同分保持模型给的顺序
    cleaned.sort(key=lambda c: (c["confidence"] is None, -(c["confidence"] or 0.0)))
    return cleaned[:MAX_CANDIDATES]


def _degraded_kind(exc: LLMError) -> str:
    """把第三方错误类别收敛到本模块的白名单（未知一律 unknown）。"""
    return exc.kind if exc.kind in DEGRADED_REASON else "unknown"


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------


async def propose(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    product_name: str,
    product_desc: str | None = None,
    declaration_id: int | None = None,
    org_id: int | None = None,
) -> dict[str, Any]:
    """发起一次归类建议。

    **先落记录再调模型**：模型慢/挂都不影响"这次发起过"这件事被记住。
    反过来的顺序（调完再落库）会在超时时留下一个查不到原因的空白。
    """
    if org_id is not None and int(org_id) not in {int(x) for x in org_ids}:
        raise ClassificationForbiddenError("只能在自己所在的组织下发起归类")

    now = _ts()
    result = session.execute(
        text(
            "INSERT INTO cus_classification "
            "(declaration_id, owner_user_id, org_id, product_name, product_desc, status, "
            " model_mocked, revision, created_at, updated_at) "
            "VALUES (:did, :uid, :oid, :name, :desc, 'pending', 0, 1, :now, :now)"
        ),
        {
            "did": declaration_id,
            "uid": user_id,
            "oid": org_id,
            "name": product_name[:128],
            "desc": (product_desc[:1024] if product_desc else None),
            "now": now,
        },
    )
    # `text()` 插入拿主键只能读驱动的 `lastrowid`（SQLite/MySQL 都支持），
    # 但 SQLAlchemy 的 `Result` 类型上没有这个属性 ⇒ mypy 需要一处显式豁免（与 D1 同）。
    new_id = int(result.lastrowid or 0)  # type: ignore[attr-defined]
    if not new_id:
        raise ClassificationError("发起归类失败：未能取得记录 id")
    # 立刻提交：模型调用可能等十几秒，不让一个待插入的事务横跨这段时间
    # （SQLite 会因此把库锁住，MySQL 则是长事务）。这也让"发起过"这件事先落定。
    session.commit()

    prompt = f"商品名称：{product_name}"
    if product_desc:
        prompt += f"\n补充描述：{product_desc}"

    status = "suggested"
    degraded_kind: str | None = None
    degraded_reason: str | None = None
    model_name: str | None = None
    model_mocked = 0
    candidates: list[dict[str, Any]] = []

    try:
        llm = await chat_json(system=_SYSTEM, user=prompt, mock_content=_mock_classify)
    except LLMError as exc:
        # 降级：不写候选、不写结论，把"为什么没有建议"记下来
        status = "manual_required"
        degraded_kind = _degraded_kind(exc)
        degraded_reason = DEGRADED_REASON[degraded_kind]
    else:
        candidates = _clean_candidates(llm.content)
        model_mocked = 1 if llm.mocked else 0
        if not llm.mocked:
            from app.core.config import get_settings

            model_name = get_settings().LLM_MODEL
        if not candidates:
            # 调用成功但没给出可用候选 —— 同样是降级，且单独区别于"调用失败"
            status = "manual_required"
            degraded_kind = "empty_result"
            degraded_reason = DEGRADED_REASON["empty_result"]

    for idx, cand in enumerate(candidates, start=1):
        session.execute(
            text(
                "INSERT INTO cus_classification_candidate "
                "(classification_id, hs_code, label, confidence, source, rank_no, "
                " rationale, actor_user_id, created_at) "
                "VALUES (:cid, :code, :label, :conf, 'model', :rank, :why, NULL, :now)"
            ),
            {
                "cid": new_id,
                "code": cand["hs_code"],
                "label": cand["label"],
                "conf": cand["confidence"],
                "rank": idx,
                "why": cand["rationale"],
                "now": now,
            },
        )

    session.execute(
        text(
            "UPDATE cus_classification SET status = :s, degraded_kind = :dk, "
            "degraded_reason = :dr, model_name = :mn, model_mocked = :mm, updated_at = :u "
            "WHERE id = :id"
        ),
        {
            "s": status,
            "dk": degraded_kind,
            "dr": degraded_reason,
            "mn": model_name,
            "mm": model_mocked,
            "u": _ts(),
            "id": new_id,
        },
    )
    session.commit()
    return _out(session, _record(session, new_id))


def submit_manual(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    classification_id: int,
    hs_code: str,
    label: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    """人工填写并确认结论 —— 不碰 LLM，模型不可用时照样能出结果。

    写两处：候选表追加一条 `source='manual'`（⛔ 不覆盖模型行），
    记录表写 `hs_code` / `decided_by_user_id` / `decided_at` 并置 `confirmed`。
    """
    params: dict[str, Any] = {"uid": int(user_id), "id": int(classification_id)}
    clause, extra = _visible_clause(org_ids)
    params.update(extra)
    row = (
        session.execute(
            text(f"SELECT c.* FROM cus_classification c WHERE c.id = :id AND {clause}"),
            params,
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ClassificationNotFoundError("归类记录不存在或不可见")
    if str(row["status"]) == "confirmed":
        raise ClassificationStateError("结论已确认；候选表只追加，如需更正请新建一条归类记录")

    code = (hs_code or "").strip()
    if not code.isdigit() or len(code) not in (8, 10):
        raise ClassificationStateError("税则号列必须是 8 位或 10 位数字")

    now = _ts()
    next_rank = int(
        session.execute(
            text(
                "SELECT COALESCE(MAX(rank_no), 0) AS m FROM cus_classification_candidate "
                "WHERE classification_id = :cid AND source = 'manual'"
            ),
            {"cid": classification_id},
        ).scalar_one()
    )
    session.execute(
        text(
            "INSERT INTO cus_classification_candidate "
            "(classification_id, hs_code, label, confidence, source, rank_no, rationale, "
            " actor_user_id, created_at) "
            "VALUES (:cid, :code, :label, NULL, 'manual', :rank, :why, :uid, :now)"
        ),
        {
            "cid": classification_id,
            "code": code,
            "label": (label[:256] if label else None),
            "rank": next_rank + 1,
            "why": (rationale[:1024] if rationale else None),
            "uid": int(user_id),
            "now": now,
        },
    )
    session.execute(
        text(
            "UPDATE cus_classification SET status = 'confirmed', hs_code = :code, "
            "decided_by_user_id = :uid, decided_at = :now, updated_at = :now, "
            "revision = revision + 1 WHERE id = :id"
        ),
        {"code": code, "uid": int(user_id), "now": now, "id": classification_id},
    )
    session.commit()
    return _out(session, _record(session, classification_id))


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def list_classifications(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    declaration_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clause, extra = _visible_clause(org_ids)
    params: dict[str, Any] = {"uid": int(user_id)}
    params.update(extra)
    where = [clause]
    if declaration_id is not None:
        where.append("c.declaration_id = :did")
        params["did"] = int(declaration_id)
    if status:
        where.append("c.status = :st")
        params["st"] = status
    # ⚠️ 用 `.mappings()` 而不是 `.scalars()`：后者在部分 SQLAlchemy 存根版本上
    # 会推断不出元素类型，`mypy --strict` 报 `var-annotated`（"Need type annotation"）。
    # 本机旧 venv 不报、CI 装最新依赖就报 —— 2026-10-26 实际因此红过一次，
    # 靠"按 requirements.txt 新建干净 venv 复现"才定位。D1 全程用 `.mappings()`，同源。
    rows = (
        session.execute(
            text(
                "SELECT c.id FROM cus_classification c "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY c.created_at DESC, c.id DESC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    ids = [int(r["id"]) for r in rows]
    return [_out(session, _record(session, rid)) for rid in ids]


def get_detail(
    session: Session,
    *,
    user_id: int,
    org_ids: frozenset[int] | set[int],
    classification_id: int,
) -> dict[str, Any]:
    clause, extra = _visible_clause(org_ids)
    params: dict[str, Any] = {"uid": int(user_id), "id": int(classification_id)}
    params.update(extra)
    row = (
        session.execute(
            text(f"SELECT c.* FROM cus_classification c WHERE c.id = :id AND {clause}"),
            params,
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ClassificationNotFoundError("归类记录不存在或不可见")
    return _out(session, row)
