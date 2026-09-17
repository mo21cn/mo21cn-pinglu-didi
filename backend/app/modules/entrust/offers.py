"""S3 对客发布与客户响应（BP-03 第 4/5/6/7/10 条；D1-07 / D1-11）。

本模块把 HO 0917-3 裁定三的四条口径落成**可判定的行为**：

| 口径（裁定三原文要点） | 落在这里的实现 |
| --- | --- |
| 发布 = 独立记录，绑定**精确成果 revision**，保存**当时的客户白名单快照** | `release_offer()`：按 `revision_no` **点名**版本（拿不到就拒，**不取最新**）；快照用 `registry.project_for_customer()` **在发布那一刻**算完落库 |
| 新建内部草稿**不等于**自动撤销旧报价；只有显式重新发布／撤回才改变旧报价的可响应状态 | `release_offer()` 只把**同一成果**的旧 `released` 记录标为 `superseded`（显式动作）；`withdraw_release()` 是另一条显式动作。⛔ 没有任何"有更新的记录 ⇒ 旧的就是失效"的推断 |
| **已接受的旧版本永久保留** | 有客户响应的发布记录**不参与取代、也不能撤回**（`_assert_not_responded`）⇒ 接受事实不可被覆盖 |
| 客户 = 当前委托的**货主本人**；组织经理权限**不能**代替客户确认权限 | `respond_to_offer()` 只认 `customer_user_id`（发布时取自 `assignment.owner_user_id`）；经理调用得到 **403**，不是"也行" |
| 客户下载**只**限该发布记录明确授权的文件 | `authorized_attachment_ids` 随发布冻结；客户侧投影只回这一份清单 |
| **未核验提案不得被当作已核实依据** | `source_gate()`：声明过的来源**逐条**必须有核验记录（对象 + 记录，不是一个勾选），否则发布被拒并列出待核验清单 |

为什么不复用 `artifacts.confirm_revision`
----------------------------------------
"设为生效版本"改的是**内部**的当前版本指针（成果页 / 工作台都在读它）；"发布"改的是
**对客可响应状态**。两者是不同的业务事实：内部确认一个新版本**不得**悄悄撤销客户手上
那份报价（裁定三冻结细节①）。合在一起实现，迟早会出现"编辑一下，客户那边就点不动了"。

为什么客户响应必须靠唯一约束而不是"先查后写"
--------------------------------------------
`ent_offer_response` 上 `UNIQUE (release_id)` 是**数据库层面的事实判据**。应用层
"先查有没有响应、没有再插"在并发下两个请求可以同时通过检查 —— 于是同一次发布被接受
两次，而两次都"看起来"合法。本模块**捕获 `IntegrityError` 并转成 409**，把并发冲突
变成一句可读的业务结论（`OfferAlreadyRespondedError`）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.entrust import artifacts as art
from app.modules.entrust import registry as reg
from app.modules.entrust.authz import load_assignment

# ── 发布记录状态（与迁移注释同一取值域）────────────────────────────────────
STATUS_RELEASED = "released"
STATUS_WITHDRAWN = "withdrawn"
STATUS_SUPERSEDED = "superseded"
ALL_RELEASE_STATUSES = frozenset({STATUS_RELEASED, STATUS_WITHDRAWN, STATUS_SUPERSEDED})

#: 只有这一个状态**可以**被客户响应。其它两个都必须失败（合同 §10.2 的负例）。
RESPONDABLE_STATUSES = frozenset({STATUS_RELEASED})

DECISION_ACCEPT = "accept"
DECISION_REJECT = "reject"
ALL_DECISIONS = frozenset({DECISION_ACCEPT, DECISION_REJECT})

# ── 来源核验台账状态 ────────────────────────────────────────────────────────
CHECK_DECLARED = "declared"
CHECK_VERIFIED = "verified"
CHECK_REJECTED = "rejected"
ALL_CHECK_STATES = frozenset({CHECK_DECLARED, CHECK_VERIFIED, CHECK_REJECTED})


class OfferError(RuntimeError):
    """业务错误 → 400（请求合法但当前事实不允许这么做）。"""


class OfferNotFoundError(OfferError):
    """不存在或对调用方不可见 → 404（两者**不区分**，避免泄漏存在性）。"""


class OfferStateError(OfferError):
    """与当前状态冲突 → 409（已响应／已撤回／已被取代）。"""


class OfferForbiddenError(RuntimeError):
    """身份不对 → 403（此处只用于"经理冒充客户"这一类：他知道它存在，但无权替客户决定）。"""


class OfferSourceGateError(OfferError):
    """来源门槛未过 → 400，且**带清单**：只说"未核验"等于没给出可执行的下一步。"""

    def __init__(
        self, message: str, *, pending: list[dict[str, str]], rejected: list[dict[str, str]]
    ):
        super().__init__(message)
        self.pending = pending
        self.rejected = rejected


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fmt(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    # `dt` 声明成 Any（同一函数要接受 datetime 与已格式化的字符串），
    # 显式 `str(...)` 兜住 mypy 的 no-any-return。
    return str(dt.strftime("%Y-%m-%d %H:%M:%S"))


def _load_list(raw: Any) -> list[int]:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw]
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return [int(x) for x in parsed] if isinstance(parsed, list) else []


# ── 发布记录 ────────────────────────────────────────────────────────────────

_RELEASE_COLS = (
    "id, assignment_id, entrustment_id, artifact_id, revision_id, revision_no, "
    "customer_user_id, snapshot_json, authorized_attachment_ids, status, released_by, "
    "released_at, closed_by, closed_at, close_reason, created_at, updated_at"
)


def _row_to_release(row: Any) -> dict[str, Any]:
    return {
        "release_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "entrustment_id": (
            int(row["entrustment_id"]) if row["entrustment_id"] is not None else None
        ),
        "artifact_id": int(row["artifact_id"]),
        "revision_id": int(row["revision_id"]),
        "revision_no": int(row["revision_no"]),
        "customer_user_id": int(row["customer_user_id"]),
        "snapshot": json.loads(str(row["snapshot_json"]) or "{}"),
        "authorized_attachment_ids": _load_list(row["authorized_attachment_ids"]),
        "status": str(row["status"]),
        "released_by": int(row["released_by"]),
        "released_at": _fmt(row["released_at"]),
        "closed_by": int(row["closed_by"]) if row["closed_by"] is not None else None,
        "closed_at": _fmt(row["closed_at"]) or None,
        "close_reason": row["close_reason"],
        "created_at": _fmt(row["created_at"]),
        "updated_at": _fmt(row["updated_at"]),
    }


def get_release(session: Session, *, release_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {_RELEASE_COLS} FROM ent_offer_release WHERE id = :rid"),
            {"rid": release_id},
        )
        .mappings()
        .first()
    )
    return _row_to_release(row) if row is not None else None


def list_releases(
    session: Session,
    *,
    assignment_id: int | None = None,
    artifact_id: int | None = None,
    customer_user_id: int | None = None,
    entrustment_id: int | None = None,
    statuses: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """按维度列出发布记录（**必填至少一个维度**，避免全表扫）。"""
    where: list[str] = []
    params: dict[str, Any] = {}
    if assignment_id is not None:
        where.append("assignment_id = :aid")
        params["aid"] = assignment_id
    if artifact_id is not None:
        where.append("artifact_id = :art")
        params["art"] = artifact_id
    if customer_user_id is not None:
        where.append("customer_user_id = :cid")
        params["cid"] = customer_user_id
    if entrustment_id is not None:
        where.append("entrustment_id = :eid")
        params["eid"] = entrustment_id
    if statuses:
        names = sorted(statuses)
        keys = [f"s{i}" for i in range(len(names))]
        where.append("status IN (" + ", ".join(f":{k}" for k in keys) + ")")
        params.update(dict(zip(keys, names, strict=True)))
    if not where:
        raise OfferError("列出发布记录至少要给一个维度（委托单 / 成果 / 客户 / 授权）")
    sql = (
        f"SELECT {_RELEASE_COLS} FROM ent_offer_release WHERE "
        + " AND ".join(where)
        + " ORDER BY released_at DESC, id DESC"
    )
    rows = session.execute(text(sql), params).mappings()
    return [_row_to_release(r) for r in rows]


# ── 客户响应 ────────────────────────────────────────────────────────────────

_RESPONSE_COLS = (
    "id, release_id, assignment_id, artifact_id, responded_revision_id, decision, "
    "customer_user_id, note, responded_at, created_at, updated_at"
)


def _row_to_response(row: Any) -> dict[str, Any]:
    return {
        "response_id": int(row["id"]),
        "release_id": int(row["release_id"]),
        "assignment_id": int(row["assignment_id"]),
        "artifact_id": int(row["artifact_id"]),
        "responded_revision_id": int(row["responded_revision_id"]),
        "decision": str(row["decision"]),
        "customer_user_id": int(row["customer_user_id"]),
        "note": row["note"],
        "responded_at": _fmt(row["responded_at"]),
    }


def response_of(session: Session, *, release_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {_RESPONSE_COLS} FROM ent_offer_response WHERE release_id = :rid"),
            {"rid": release_id},
        )
        .mappings()
        .first()
    )
    return _row_to_response(row) if row is not None else None


def list_responses(session: Session, *, assignment_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            f"SELECT {_RESPONSE_COLS} FROM ent_offer_response WHERE assignment_id = :aid "
            "ORDER BY responded_at DESC, id DESC"
        ),
        {"aid": assignment_id},
    ).mappings()
    return [_row_to_response(r) for r in rows]


# ── 来源核验台账 ────────────────────────────────────────────────────────────

_CHECK_COLS = (
    "id, artifact_id, revision_no, source_kind, source_ref, state, method, "
    "checked_by, checked_at, note, created_at, updated_at"
)


def _row_to_check(row: Any) -> dict[str, Any]:
    return {
        "check_id": int(row["id"]),
        "artifact_id": int(row["artifact_id"]),
        "revision_no": int(row["revision_no"]),
        "source_kind": str(row["source_kind"]),
        "source_ref": str(row["source_ref"]),
        "state": str(row["state"]),
        "method": row["method"],
        "checked_by": int(row["checked_by"]) if row["checked_by"] is not None else None,
        "checked_at": _fmt(row["checked_at"]) or None,
        "note": row["note"],
    }


def list_source_checks(
    session: Session, *, artifact_id: int, revision_no: int | None = None
) -> list[dict[str, Any]]:
    sql = f"SELECT {_CHECK_COLS} FROM ent_offer_source_check WHERE artifact_id = :aid"
    params: dict[str, Any] = {"aid": artifact_id}
    if revision_no is not None:
        sql += " AND revision_no = :rev"
        params["rev"] = revision_no
    sql += " ORDER BY revision_no, source_kind, source_ref, state"
    return [_row_to_check(r) for r in session.execute(text(sql), params).mappings()]


def record_declared_sources(
    session: Session,
    *,
    artifact_id: int,
    revision_no: int,
    sources: list[dict[str, str]],
) -> int:
    """把**作业信封里声明过的来源**记成待核验项（采纳提案时由服务端调用）。

    为什么由服务端写：核验对象必须是"模型当初声明了什么"，而不是人后来手填一份 ——
    手填的对象可以被写成任何东西，那这条门槛就只剩形式。同一条声明重复写入是幂等的
    （唯一键 `(artifact, revision, kind, ref, state)`）。
    """
    now = _fmt(utcnow_naive())
    written = 0
    for item in sources:
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        if not kind or not ref:
            continue
        exists = session.execute(
            text(
                "SELECT id FROM ent_offer_source_check WHERE artifact_id = :aid AND "
                "revision_no = :rev AND source_kind = :k AND source_ref = :r AND state = :s"
            ),
            {"aid": artifact_id, "rev": revision_no, "k": kind, "r": ref, "s": CHECK_DECLARED},
        ).first()
        if exists is not None:
            continue
        session.execute(
            text(
                "INSERT INTO ent_offer_source_check "
                "(artifact_id, revision_no, source_kind, source_ref, state, method, "
                " checked_by, checked_at, note, created_at, updated_at) "
                "VALUES (:aid, :rev, :k, :r, :s, NULL, NULL, NULL, NULL, :now, :now)"
            ),
            {
                "aid": artifact_id,
                "rev": revision_no,
                "k": kind,
                "r": ref,
                "s": CHECK_DECLARED,
                "now": now,
            },
        )
        written += 1
    return written


def source_gate(
    session: Session,
    *,
    artifact_id: int,
    revision_no: int,
    revision_source: str | None = None,
) -> dict[str, Any]:
    """发布前来源门槛的**唯一实现**（谁要判"能不能发布"都必须调它）。

    判定：
    * 每条 `declared` 来源都要有一条对应的 `verified` 行 —— 否则进 `pending`；
    * 任何 `rejected` 行 ⇒ 进 `rejected`（"核了，判定不可用"，与"还没核"分开说）；
    * 没有声明来源的版本（人工直写/修改）⇒ 无待核验项 ⇒ `ok=True`：它本来就不是
      "模型提案"，把人工产出也算成待核验会让门槛变成走过场。

    ⚠️ 一处**必须有**的例外：`revision_source='agent'` 却**一条声明都没有** ⇒ 判**不通过**
    （`missing_declaration=True`）。为什么不能放行：声明是在采纳时由服务端写台账的，
    而 `artifacts.create_artifact` 内部自己 commit —— 两者不在一个事务里。若把
    "没有声明"当成"没有待核验项"，一次中途失败就会让**模型产出**的成果变成"无来源可核、
    直接可发布"，而这件事在数据上完全看不出来。宁可拒发（并说清原因），也不留这条缝。
    """
    rows = list_source_checks(session, artifact_id=artifact_id, revision_no=revision_no)
    declared = [r for r in rows if r["state"] == CHECK_DECLARED]
    verified = [r for r in rows if r["state"] == CHECK_VERIFIED]
    rejected = [r for r in rows if r["state"] == CHECK_REJECTED]
    verified_keys = {(r["source_kind"], r["source_ref"]) for r in verified}
    rejected_keys = {(r["source_kind"], r["source_ref"]) for r in rejected}
    pending = [
        {"kind": r["source_kind"], "ref": r["source_ref"]}
        for r in declared
        if (r["source_kind"], r["source_ref"]) not in verified_keys
        and (r["source_kind"], r["source_ref"]) not in rejected_keys
    ]
    rejected_out = [
        {"kind": r["source_kind"], "ref": r["source_ref"], "method": r["method"] or ""}
        for r in rejected
    ]
    missing_declaration = bool(revision_source == art.SOURCE_AGENT and not rows)
    return {
        "artifact_id": artifact_id,
        "revision_no": revision_no,
        "declared": [{"kind": r["source_kind"], "ref": r["source_ref"]} for r in declared],
        "verified": [{"kind": r["source_kind"], "ref": r["source_ref"]} for r in verified],
        "pending": pending,
        "rejected": rejected_out,
        "missing_declaration": missing_declaration,
        "ok": not pending and not rejected_out and not missing_declaration,
    }


def record_source_check(
    session: Session,
    *,
    artifact_id: int,
    revision_no: int,
    source_kind: str,
    source_ref: str,
    state: str,
    method: str,
    note: str | None,
    actor_user_id: int,
) -> dict[str, Any]:
    """人工核验留痕：**必须**给出核验依据（`method`），否则不算核验。

    `state` 只接受 `verified` / `rejected`：`declared` 由服务端在采纳时写入，
    不允许人手工伪造"声明"（那等于自己给自己发一张待核验清单）。
    """
    if state not in (CHECK_VERIFIED, CHECK_REJECTED):
        raise OfferError("核验状态只能是 verified（已核验）或 rejected（判定不可用）")
    if not str(method or "").strip():
        raise OfferError("核验必须写明依据（method）：无依据的核验等于没核")
    revisions = art.list_revisions(session, artifact_id)
    if not any(r["revision_no"] == revision_no for r in revisions):
        raise OfferError(f"成果 {artifact_id} 没有版本 v{revision_no}")
    now = utcnow_naive()
    try:
        session.execute(
            text(
                "INSERT INTO ent_offer_source_check "
                "(artifact_id, revision_no, source_kind, source_ref, state, method, "
                " checked_by, checked_at, note, created_at, updated_at) "
                "VALUES (:aid, :rev, :k, :r, :s, :m, :by, :now, :note, :now, :now)"
            ),
            {
                "aid": artifact_id,
                "rev": revision_no,
                "k": source_kind,
                "r": source_ref,
                "s": state,
                "m": str(method).strip()[:255],
                "by": actor_user_id,
                "note": (note[:255] if note else None),
                "now": _fmt(now),
            },
        )
    except IntegrityError as exc:  # 同一个 (来源, 状态) 已有记录 ⇒ 幂等，不重复写
        session.rollback()
        raise OfferStateError("这条来源已经有过相同状态的核验记录") from exc
    session.commit()
    rows = list_source_checks(session, artifact_id=artifact_id, revision_no=revision_no)
    for r in rows:
        if (
            r["state"] == state
            and r["source_kind"] == source_kind
            and r["source_ref"] == source_ref
        ):
            return r
    raise OfferError("核验记录写入后读不到（不该发生）")


# ── 命令：发布 ──────────────────────────────────────────────────────────────


def _assert_not_responded(session: Session, release: dict[str, Any]) -> None:
    """有客户响应的发布**不可撤销、不可取代** —— 接受事实不能事后被抹掉。"""
    if response_of(session, release_id=release["release_id"]) is not None:
        raise OfferStateError(
            f"发布 {release['release_id']} 已被客户响应，不能撤回或取代"
            "（已接受的版本永久保留，后续变更要新的客户确认）"
        )


def release_offer(
    session: Session,
    *,
    artifact_id: int,
    revision_no: int,
    authorized_attachment_ids: list[int] | None = None,
    note: str | None = None,
    actor_user_id: int,
) -> dict[str, Any]:
    """把**指定的那一个成果版本**发布给客户（幂等由调用方的 `run_write` 负责）。

    ⛔ 不接受"发布最新版本"：拿不到指定 `revision_no` 就拒绝 —— D1-07 要证明客户
    接受的是**那一个**版本，而"取最新"在并发编辑下会让证据变成"反正是某一个版本"。
    """
    artifact = art.get_artifact(session, artifact_id)  # 不存在 → ArtifactNotFoundError(404)
    if str(artifact["status"]) == "void":
        raise OfferStateError(f"成果 {artifact_id} 已作废，不能发布")
    assignment_id = artifact["assignment_id"]
    if assignment_id is None:
        raise OfferError("该成果没有归属委托单，不能发布（发布必须有明确的客户与委托上下文）")
    assignment = load_assignment(session, int(assignment_id))
    if assignment is None:
        raise OfferNotFoundError("委托单不存在")

    target = next(
        (r for r in art.list_revisions(session, artifact_id) if r["revision_no"] == revision_no),
        None,
    )
    if target is None:
        raise OfferError(f"成果 {artifact_id} 没有版本 v{revision_no}（不取最新版本代替）")

    artifact_type = str(artifact["artifact_type"])
    projected = reg.project_for_customer(artifact_type, target["payload"])
    if not projected:
        raise OfferError(f"成果类型 {artifact_type} 不在客户白名单投影内（投影为空），不能对客发布")

    gate = source_gate(
        session, artifact_id=artifact_id, revision_no=revision_no, revision_source=target["source"]
    )
    if gate["missing_declaration"]:
        raise OfferSourceGateError(
            f"版本 v{revision_no} 是模型产出，但**没有任何来源声明记录** ⇒ 无从核对，不得发布。"
            "（来源声明由服务端在采纳提案时写入；缺了它说明采纳那一步没走完，"
            "请重新采纳或改走人工录入版本）",
            pending=[],
            rejected=[],
        )
    if gate["rejected"]:
        refs = ", ".join(f"{r['kind']}:{r['ref']}" for r in gate["rejected"])
        raise OfferSourceGateError(
            f"版本 v{revision_no} 有来源被核验判定为不可用（{refs}），不能发布",
            pending=gate["pending"],
            rejected=gate["rejected"],
        )
    if gate["pending"]:
        refs = ", ".join(f"{p['kind']}:{p['ref']}" for p in gate["pending"])
        raise OfferSourceGateError(
            f"版本 v{revision_no} 还有未核验的来源（{refs}）—— "
            "未核验的提案不得作为已发布依据；请先逐条留下核验记录",
            pending=gate["pending"],
            rejected=gate["rejected"],
        )

    now = utcnow_naive()
    stamp = _fmt(now)

    # 显式取代：**同一成果**此前可响应的发布记录（裁定三冻结细节①）
    previous = list_releases(
        session, artifact_id=artifact_id, statuses=frozenset({STATUS_RELEASED})
    )
    superseded: list[int] = []
    for old in previous:
        _assert_not_responded(session, old)  # 已响应的旧版本：永久保留，不许动
        session.execute(
            text(
                "UPDATE ent_offer_release SET status = :st, closed_by = :by, closed_at = :now, "
                "close_reason = :why, updated_at = :now WHERE id = :rid"
            ),
            {
                "st": STATUS_SUPERSEDED,
                "by": actor_user_id,
                "now": stamp,
                "why": f"被同一成果的新发布取代（v{revision_no}）",
                "rid": old["release_id"],
            },
        )
        superseded.append(old["release_id"])

    snapshot = {
        "artifact_type": artifact_type,
        "revision_no": int(revision_no),
        # ⚠️ 这份 payload 是**发布那一刻**的客户白名单投影结果。发布后按"现在的投影规则"
        #    重算会悄悄改变客户已看到的内容 —— 而记录上还写着"同一个版本"。
        "payload": projected,
        "content_source": target["source"],
        "snapshot_taken_at": stamp,
        "snapshot_excludes_internal": True,
    }
    inserted = cast(
        "CursorResult[Any]",
        session.execute(
            text(
                "INSERT INTO ent_offer_release "
                "(assignment_id, entrustment_id, artifact_id, revision_id, revision_no, "
                " customer_user_id, snapshot_json, authorized_attachment_ids, status, released_by, "
                " released_at, closed_by, closed_at, close_reason, created_at, updated_at) "
                "VALUES (:aid, :eid, :art, :revid, :rev, :cid, :snap, :atts, :st, :by, "
                " :now, NULL, NULL, :note, :now, :now)"
            ),
            {
                "aid": int(assignment_id),
                "eid": artifact["entrustment_id"],
                "art": artifact_id,
                "revid": target["revision_id"],
                "rev": int(revision_no),
                "cid": int(assignment["owner_user_id"]),
                "snap": json.dumps(snapshot, ensure_ascii=False),
                "atts": json.dumps(sorted({int(x) for x in (authorized_attachment_ids or [])}))
                if authorized_attachment_ids
                else None,
                "st": STATUS_RELEASED,
                "by": actor_user_id,
                "now": stamp,
                "note": (note[:255] if note else None),
            },
        ),
    )
    session.commit()
    # ⚠️ 取主键用 `lastrowid`（MySQL / SQLite 的 DBAPI 都给），**不要**用 `MAX(id)`
    #    或"按条件回查"：并发下会取到别人的那一行，而发布记录被写错是审计性缺陷。
    new_id = int(inserted.lastrowid or 0)
    created = get_release(session, release_id=new_id)
    if created is None:
        raise OfferError("发布记录写入后读不到（不该发生）")
    if not created["snapshot"].get("payload"):
        # 快照是这次发布的**全部价值**（客户照着它做决定）。写进去了却读成空，
        # 说明键名/列名有一处对不上 —— 宁可在写入路径上就炸，也不要让一条
        # "已发布、但客户看到的是空内容"的记录流到下游。
        raise OfferError(
            "发布记录的快照为空（写读键名不一致）—— 已中止，不产生'已发布但内容为空'的记录"
        )
    created["superseded_release_ids"] = superseded
    return created


def withdraw_release(
    session: Session, *, release_id: int, reason: str, actor_user_id: int
) -> dict[str, Any]:
    """显式撤回（裁定三：只有显式动作才改变旧报价的可响应状态）。"""
    release = get_release(session, release_id=release_id)
    if release is None:
        raise OfferNotFoundError("发布记录不存在")
    if release["status"] != STATUS_RELEASED:
        raise OfferStateError(f"发布记录当前是 {release['status']}，不能撤回")
    _assert_not_responded(session, release)
    if not str(reason or "").strip():
        raise OfferError("撤回必须写明理由（留痕，不靠猜）")
    now = _fmt(utcnow_naive())
    session.execute(
        text(
            "UPDATE ent_offer_release SET status = :st, closed_by = :by, closed_at = :now, "
            "close_reason = :why, updated_at = :now WHERE id = :rid"
        ),
        {
            "st": STATUS_WITHDRAWN,
            "by": actor_user_id,
            "now": now,
            "why": str(reason).strip()[:255],
            "rid": release_id,
        },
    )
    session.commit()
    updated = get_release(session, release_id=release_id)
    if updated is None:
        raise OfferError("撤回后读不到该发布记录（不该发生）")
    return updated


def respond_to_offer(
    session: Session, *, release_id: int, decision: str, note: str | None, actor_user_id: int
) -> dict[str, Any]:
    """客户接受／拒绝**该已发布版本**。

    三重判定，缺一不可：
    1. **身份**：只有 `customer_user_id`（＝该委托货主本人）能响应；
       经理即使有全部组织权限，也只会得到 403（裁定三原话：组织经理权限本身不能代替客户确认）；
    2. **可响应状态**：`status` 必须是 `released` —— 未发布／已撤回／已被取代都必须失败；
    3. **只能响应一次**：靠 `UNIQUE (release_id)`；并发冲突转成 409，而不是抛 500。
    """
    if decision not in ALL_DECISIONS:
        raise OfferError(f"响应只能是 {sorted(ALL_DECISIONS)} 之一")
    release = get_release(session, release_id=release_id)
    if release is None:
        raise OfferNotFoundError("发布记录不存在")
    if int(actor_user_id) != int(release["customer_user_id"]):
        raise OfferForbiddenError(
            "只有该委托的货主本人可以接受／拒绝这份发布 —— 组织（经理）权限不能代替客户确认"
        )
    if release["status"] not in RESPONDABLE_STATUSES:
        raise OfferStateError(
            f"该发布当前是 {release['status']}，不能响应"
            "（未发布 / 已撤回 / 已被取代的版本都必须失败）"
        )
    now = utcnow_naive()
    stamp = _fmt(now)
    try:
        session.execute(
            text(
                "INSERT INTO ent_offer_response "
                "(release_id, assignment_id, artifact_id, responded_revision_id, decision, "
                " customer_user_id, note, responded_at, created_at, updated_at) "
                "VALUES (:rid, :aid, :art, :revid, :dec, :cid, :note, :now, :now, :now)"
            ),
            {
                "rid": release_id,
                "aid": release["assignment_id"],
                "art": release["artifact_id"],
                "revid": release["revision_id"],
                "dec": decision,
                "cid": int(actor_user_id),
                "note": (note[:500] if note else None),
                "now": stamp,
            },
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # 并发或重复：唯一约束是判据。**不吞**成"成功"，也不让它变成 500。
        raise OfferStateError("这份发布已经被响应过了（同一次发布只能被响应一次）") from exc
    created = response_of(session, release_id=release_id)
    if created is None:
        raise OfferError("响应写入后读不到（不该发生）")
    return created


# ── 投影：两条通道，服务端各自算好 ──────────────────────────────────────────


def project_release_for_manager(
    session: Session, release: dict[str, Any], *, response: dict[str, Any] | None = None
) -> dict[str, Any]:
    """经理视角：含发布控制信息、**客户看到的快照**与来源门槛状态。"""
    # 用发布时冻结进快照的 `content_source`，而不是再查一次版本表：
    # ① 少一次 N+1；② 与发布那一刻的判定**同源**（口径不会因为版本被后续编辑而漂移）。
    gate = source_gate(
        session,
        artifact_id=release["artifact_id"],
        revision_no=release["revision_no"],
        revision_source=release["snapshot"].get("content_source"),
    )
    return {
        "release_id": release["release_id"],
        "assignment_id": release["assignment_id"],
        "entrustment_id": release["entrustment_id"],
        "artifact_id": release["artifact_id"],
        "revision_id": release["revision_id"],
        "revision_no": release["revision_no"],
        "customer_user_id": release["customer_user_id"],
        "status": release["status"],
        "released_by": release["released_by"],
        "released_at": release["released_at"],
        "closed_by": release["closed_by"],
        "closed_at": release["closed_at"],
        "close_reason": release["close_reason"],
        # 经理要能回答"客户当初看到的到底是哪一份内容" ⇒ 快照原样给出
        "customer_snapshot": release["snapshot"],
        "authorized_attachment_ids": release["authorized_attachment_ids"],
        "source_gate": gate,
        "response": response,
    }


def project_release_for_customer(
    release: dict[str, Any], *, response: dict[str, Any] | None = None
) -> dict[str, Any]:
    """客户视角：**只**回发布时冻结的那份投影（服务端算好，不是让前端藏）。

    刻意**不**回 `released_by` / `closed_by` / `artifact_id` 的内部控制信息与
    来源台账 —— 客户不该看到内部是谁发布的、有哪些内部来源待核验。
    """
    return {
        "release_id": release["release_id"],
        "assignment_id": release["assignment_id"],
        "revision_no": release["revision_no"],
        "status": release["status"],
        "released_at": release["released_at"],
        "content": release["snapshot"].get("payload") or {},
        "artifact_type": release["snapshot"].get("artifact_type"),
        "content_source": release["snapshot"].get("content_source"),
        "authorized_attachment_ids": release["authorized_attachment_ids"],
        "can_respond": release["status"] in RESPONDABLE_STATUSES and response is None,
        "response": response,
    }


__all__ = [
    "ALL_CHECK_STATES",
    "ALL_DECISIONS",
    "ALL_RELEASE_STATUSES",
    "CHECK_DECLARED",
    "CHECK_REJECTED",
    "CHECK_VERIFIED",
    "DECISION_ACCEPT",
    "DECISION_REJECT",
    "OfferError",
    "OfferForbiddenError",
    "OfferNotFoundError",
    "OfferSourceGateError",
    "OfferStateError",
    "RESPONDABLE_STATUSES",
    "STATUS_RELEASED",
    "STATUS_SUPERSEDED",
    "STATUS_WITHDRAWN",
    "get_release",
    "list_releases",
    "list_responses",
    "list_source_checks",
    "project_release_for_customer",
    "project_release_for_manager",
    "record_declared_sources",
    "record_source_check",
    "release_offer",
    "respond_to_offer",
    "response_of",
    "source_gate",
    "utcnow_naive",
    "withdraw_release",
]
