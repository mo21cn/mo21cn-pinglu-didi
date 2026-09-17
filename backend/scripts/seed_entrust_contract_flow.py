"""DEMO-1 §10.1 第 7 步的**前置夹具**：一条「客户已接受的对客报价发布」。

要什么
------
§10.1 第 7 步：`Create the contract and record labeled sample signature evidence.`
合同派生（`contracts.derive_contract`）的前置是**已接受事实**：一条对客报价发布，
且客户对它的响应是 `accept`。全仓此前**没有任何种子**造这两个对象 ——
派生只在 `backend/tests/` 里被覆盖过，界面与真机走查拿不到一个可派生的对象。

本脚本把这段事实铺出来：canonical 委托 → 对客报价成果 → 发布 → 客户接受。

⚠️ 本脚本**不做派生**（重要）
----------------------------
派生是第 7 步要被**界面/走查证明的那个动作**。种子替它做了，界面上就只剩一个
"显示已有合同"的读路径 —— 而"经界面点一下能派生出合同"这条恰恰是第 7 步的判据。
与 `seed_entrust_canonical.py`「只登记候选、不做任何确认」同一条纪律：
**种子负责摆好事实，不负责替业务做动作**。

⚠️ 也不**记签署证据**（同理）。

`--derive`：给 **e2e** 用的第二形态（默认关闭）
--------------------------------------------
e2e（`scripts/verify_frontend_e2e.js`）在页面加载**之后**做「模板字段核对」：
它要求 wxml 里读到的每个字段都在**页面数据域 ∪ 页面自己的 .js** 里出现过。
合同卡那一块的键（`contract.*` / `sig.*` / 来源行与证据行的 `item.*`）**只来自投影层**
（`utils/entrust.js`）且**只在 `contract` / `sig` 非空时才进数据域** ⇒ 夹具里没有
"已派生的合同"，核对就会报「模板读取但数据与静态配置均未产出」。

⚠️ **正解是让夹具真的有数据，不是给页面 `.js` 补键**（补键＝造一个假落点，
模板真写错字段名时就再也查不出来）。这与 ci.yml 里 canonical 种子那段注释同型：
航段行当年也是靠"把带航段的夹具加进 e2e 配方"才达标，不是靠改页面。

⇒ `--derive` 就是那份"真的派生出来 + 真的记一条证据"的夹具形态：
* **走查（㊾ 章）不带这个开关** —— 派生动作留给界面去做，那才是第 7 步的判据；
* **e2e 带** —— 它要的是"页面在真载荷下把该显示的显示出来"。

为什么挂在 canonical 那张委托上
--------------------------------
`derive_contract` 的一条条款 `route_scope` 由**航段**构成；canonical 是唯一
带航段的委托（三条：公路—内河—公路）。挂在没航段的委托上，派生出的合同会退化成
"运输范围以委托单 #N 的已受理范围为准" —— 那句话对演示第 4 步（公—水—公路线）
没有证明力。

为什么**不**纳入 `reset_demo_env.py` 的 `SEED_ORDER`
----------------------------------------------------
与 `seed_entrust_canonical.py` 同型：这是**有状态夹具**（一条已接受的发布），
塞进复位基线会让 runbook §6.3 的基线表逐格失效。⇒ 按需追加，不进基线。

⚠️ 本脚本**不声称**任何验收通过：它只让第 7 步从「没有对象」变成「有对象可演示」。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# 以 `python scripts/xxx.py`（cwd=backend）执行时 `app` 不在 sys.path 上，需显式补上。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.entrust import artifacts as art  # noqa: E402
from app.modules.entrust import contracts as ctr  # noqa: E402
from app.modules.entrust import offers as offers_svc  # noqa: E402

#: 与 `seed_entrust_canonical.py` 的 `ASSIGNMENT_TITLE` **逐字一致**：
#: 本脚本是它的追加项，不是第二份委托。改了一处另一处会静默找不到对象。
ASSIGNMENT_TITLE = "DEMO-1 canonical · 钢材 800 吨 南宁 → 贵港"

#: 对客报价内容。**金额必须是真的数** —— `derive_contract` 只在
#: `_numeric(amount)` 判得出来时才写价款条款，否则记进"合同少写了什么"的缺失清单。
QUOTE_PAYLOAD: dict[str, Any] = {
    "amount": 36000,
    "currency": "CNY",
    "includes": ["船舶运输", "装船", "卸船"],
    "excludes": ["港口建设费", "滞期费"],
    "valid_until": "2026-12-31",
    "note": "本报价为演示样件（非真实商业承诺）",
}

QUOTE_NOTE = "DEMO-1 第 7 步夹具：对客报价样件"

DERIVE_NOTE = "DEMO-1 第 7 步夹具（--derive）：合同核对稿"

EVIDENCE_NOTE = "DEMO-1 第 7 步夹具（--derive）：客户签回的样件扫描件"


def _require_assignment(db: Session) -> tuple[int, int, int]:
    """canonical 委托 ⇒ `(assignment_id, owner_user_id, org_id)`。

    `org_id` 为空即报错：发布与派生都要求委托已归属组织（未归属 ⇒ 端点 404，
    而那个 404 看起来像"接口坏了"）。
    """
    row = (
        db.execute(
            text("SELECT id, owner_user_id, org_id, status FROM ent_assignment WHERE title = :t"),
            {"t": ASSIGNMENT_TITLE},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RuntimeError(
            f"找不到委托「{ASSIGNMENT_TITLE}」—— 请先跑 `python scripts/seed_entrust_canonical.py`"
        )
    if row["org_id"] is None:
        raise RuntimeError(
            f"委托 #{int(row['id'])} 还没有归属组织（status={row['status']}）—— "
            "发布与派生都要求 org_id 非空；请先跑 canonical 种子把它推到已受理"
        )
    return int(row["id"]), int(row["owner_user_id"]), int(row["org_id"])


def _require_entrustment(db: Session, *, org_id: int, owner_id: int) -> int:
    """货主 → 组织的**生效中**授权（`offers` 与 `contracts` 都靠它定位客户）。"""
    rows = db.execute(
        text(
            "SELECT id FROM ent_entrustment "
            "WHERE org_id = :o AND entrust_user_id = :u AND status = 'active'"
        ),
        {"o": org_id, "u": owner_id},
    ).fetchall()
    if not rows:
        raise RuntimeError(
            f"货主 {owner_id} 对组织 {org_id} 没有生效中的委托授权 —— 请先跑 seed_entrust_demo.py"
        )
    if len(rows) > 1:
        raise RuntimeError(
            f"货主 {owner_id} 与组织 {org_id} 之间有 {len(rows)} 条生效授权 —— "
            "发布/派生要求能唯一确定授权，请先收敛"
        )
    return int(rows[0][0])


def _require_manager(db: Session, *, org_id: int) -> int:
    """组织内的 active 成员（发布人 / 派生人）。取 id 最小的那个，不挑角色。"""
    row = (
        db.execute(
            text(
                "SELECT user_id FROM ent_org_member WHERE org_id = :o AND status = 'active' "
                "ORDER BY user_id"
            ),
            {"o": org_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RuntimeError(f"组织 {org_id} 没有 active 成员 —— 请先跑 seed_entrust_demo.py")
    return int(row["user_id"])


def _ensure_quote_artifact(db: Session, *, aid: int, eid: int, by: int) -> int:
    """确保有一份对客报价成果（`customer_quote`），返回 artifact_id。"""
    row = (
        db.execute(
            text(
                "SELECT id FROM ent_artifact WHERE assignment_id = :a "
                "AND artifact_type = 'customer_quote' AND status = 'active' ORDER BY id"
            ),
            {"a": aid},
        )
        .mappings()
        .first()
    )
    if row is not None:
        return int(row["id"])
    created = art.create_artifact(
        db,
        entrustment_id=eid,
        assignment_id=aid,
        artifact_type="customer_quote",
        payload=dict(QUOTE_PAYLOAD),
        created_by=by,
        source=art.SOURCE_MANUAL,
        note=QUOTE_NOTE,
    )
    return int(created["artifact_id"])


def _ensure_accepted_release(
    db: Session, *, aid: int, artifact_id: int, by: int, owner: int
) -> int:
    """确保存在一条**客户已接受的**发布，返回 release_id。

    幂等按「有没有已响应的发布」判，不是"行存在就跳过"：
    `ent_offer_response` 有 `UNIQUE(release_id)`，重复响应会以 409 失败，
    而那个 409 看起来像夹具坏了。
    """
    releases = offers_svc.list_releases(db, assignment_id=aid)
    for rel in releases:
        if str(rel.get("artifact_type") or rel.get("snapshot", {}).get("artifact_type")) != (
            "customer_quote"
        ):
            continue
        resp = offers_svc.response_of(db, release_id=int(rel["release_id"]))
        if resp is not None and str(resp["decision"]) == offers_svc.DECISION_ACCEPT:
            return int(rel["release_id"])
    # 没有 ⇒ 新发布一条并让货主接受
    rel = offers_svc.release_offer(
        db, artifact_id=artifact_id, revision_no=1, actor_user_id=by, note=QUOTE_NOTE
    )
    offers_svc.respond_to_offer(
        db,
        release_id=int(rel["release_id"]),
        decision=offers_svc.DECISION_ACCEPT,
        note="客户接受（DEMO-1 第 7 步夹具）",
        actor_user_id=owner,
    )
    return int(rel["release_id"])


def _ensure_derivation(db: Session, *, release_id: int, by: int) -> dict[str, Any]:
    """确保这条已接受发布**已派生**出合同；已派生则复用。

    复用而不是"再来一份"：`ent_contract_derivation` 有 `UNIQUE(release_id)`，
    重复派生会以 409 失败，而那个 409 看起来像夹具坏了。
    """
    existing = ctr.get_derivation_by_release(db, release_id=release_id)
    if existing is not None:
        return existing
    return ctr.derive_contract(db, release_id=release_id, actor_user_id=by, note=DERIVE_NOTE)


def _ensure_signature_evidence(
    db: Session, *, contract_artifact_id: int, by: int
) -> dict[str, Any]:
    """确保该合同的**当前版本**上有一条「样件扫描件」证据；已记过则复用。

    幂等按**业务唯一约束**判（同一版同一形态只一条）：重跑时服务层会抛
    `SignatureEvidenceStateError`（HTTP 层是 409）—— 那是"已经记过了"，
    不是失败 ⇒ 读回那条即可，不要把它当异常抛出去（否则 `set -e` 下 e2e 配方会红）。
    """
    try:
        return ctr.record_signature_evidence(
            db,
            contract_artifact_id=contract_artifact_id,
            evidence_kind=ctr.KIND_SAMPLE_SCAN,
            actor_user_id=by,
            note=EVIDENCE_NOTE,
        )
    except ctr.SignatureEvidenceStateError:
        for row in ctr.list_signature_evidence(db, contract_artifact_id=contract_artifact_id):
            if str(row["evidence_kind"]) == ctr.KIND_SAMPLE_SCAN:
                return row
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DEMO-1 §10.1 第 7 步的前置夹具")
    ap.add_argument(
        "--derive",
        action="store_true",
        help="额外**真派生**一份合同并**真记**一条样件证据（e2e 配方用；走查不带）",
    )
    args = ap.parse_args(argv)

    db = SessionLocal()
    try:
        aid, owner_id, org_id = _require_assignment(db)
        eid = _require_entrustment(db, org_id=org_id, owner_id=owner_id)
        manager_id = _require_manager(db, org_id=org_id)
        artifact_id = _ensure_quote_artifact(db, aid=aid, eid=eid, by=manager_id)
        release_id = _ensure_accepted_release(
            db, aid=aid, artifact_id=artifact_id, by=manager_id, owner=owner_id
        )
        if args.derive:
            _ensure_derivation(db, release_id=release_id, by=manager_id)
        derivation = ctr.get_derivation_by_release(db, release_id=release_id)
        evidence = None
        if args.derive and derivation is not None:
            evidence = _ensure_signature_evidence(
                db,
                contract_artifact_id=int(derivation["contract_artifact_id"]),
                by=manager_id,
            )
        legs = (
            db.execute(
                text("SELECT COUNT(*) AS n FROM ent_leg WHERE assignment_id = :a"), {"a": aid}
            )
            .mappings()
            .first()
        )
        print("DEMO-1 §10.1 第 7 步夹具（已接受的对客报价发布）")
        print(f"  委托单        : #{aid}  {ASSIGNMENT_TITLE}")
        print(f"  货主 / 组织   : user #{owner_id} / org #{org_id}")
        print(f"  委托授权      : #{eid}（生效中，唯一）")
        print(f"  经理（发布人）: user #{manager_id}")
        print(f"  对客报价成果  : #{artifact_id}（customer_quote）")
        print(f"  已接受发布    : #{release_id}")
        print(f"  该委托航段数  : {int((legs or {'n': 0})['n'])}（派生出的 route_scope 由它构成）")
        if derivation is None:
            print("  派生状态      : 尚未派生 —— 第 7 步的派生动作留给界面 / 走查去做")
        else:
            print(
                f"  派生状态      : 已派生 合同成果 #{derivation['contract_artifact_id']}"
                f"（第 {derivation['contract_revision_no']} 版）"
            )
        if evidence is not None:
            print(
                f"  签署证据      : #{evidence['evidence_id']} "
                f"{evidence['contract_revision_no']} 版 · {evidence['evidence_kind']} · "
                f"{evidence['mode']}（e2e 用；走查侧不应由夹具代做）"
            )
        print("OK seed_entrust_contract_flow")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
