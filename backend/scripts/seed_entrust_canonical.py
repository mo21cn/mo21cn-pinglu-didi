"""DEMO-1 canonical 运力候选夹具 —— 钢材 800 吨 / 公—水—公 / 三条候选。

用法（backend 目录下）：

    python scripts/seed_entrust_canonical.py

前置（不满足就报错退出，**不自己造**）
--------------------------------------
必须先跑 `seed_entrust_demo.py`。本脚本**复用**它铺出的四样东西：

* 组织 `演示经营主体·工作台`；
* 货主 `seed-shipper`（委托的创建人）；
* 经理 `seed-owner`（组织成员，认领人）；
* 货主 → 组织那条**生效中**的委托授权（候选运力与采购确认都要挂在明确的授权上）。

为什么不自造这四样：runbook §2 的组织表是**按名字**辨认"这份数据属于哪份种子"的。
自己现造一套会让共享库里出现第二个工作台组织，从此"某条队列是谁铺的"再也说不清。

为什么**不**纳入 `reset_demo_env.py` 的 `SEED_ORDER`
--------------------------------------------------
复位要的起点是「一致、可复制的**干净**起点」（runbook §6.1），而夹具是**有状态的数据** ——
把它塞进复位基线，§6.3 那张基线表（`ent_exception`=2、`ent_revalidation`=0 …）会逐格失效。
⇒ 与 `seed_contract_cases.py`、`seed_entrust_revalidation.py` 同属**按需追加**（§5 第 ④/⑤ 行），
不进基线。

⚠️ 为什么不改 `seed_entrust_demo.py` 里的那两张委托
--------------------------------------------------
`demo1_canonical.json` 的 `does_not_replace` 逐字写着：旧种子的 1200 吨主单与「收货港变更」
样本**继续作为旧回归数据存在**（HO 0917-3 明令不要全局替换，会让既有测试基线漂移）。
⇒ 本脚本只**新增**第三张委托，一个既有行都不改。

要什么（合同 §10.1 第 5 步 / §10.2 负例 / D1-06 / §3.1）
---------------------------------------------------------
| 合同要求 | 本脚本铺出的对象 |
| --- | --- |
| BP-03 第 2 条「两家可比」（运力/价格口径/数量单位/有效期/证据可见） | 同一张委托上 **3 条**候选，事实字段齐备 |
| §3.1 元素表 `Constraint example`：900 吨候选在变更后不再适用 | **C-900**：900.000 吨、单船、不拆批 ⇒ 变更到 950 吨后 `capacity` 不过 |
| `demo1_canonical.json`：变更后业务仍需能完成 | **C-1200**：1200.000 吨 ⇒ 950 吨仍适用 |
| §10.2 `unsuitable/expired resource confirmation` | **C-EXPIRED**：`valid_until` 是**绝对过去日期** ⇒ 仅 `validity` 不过 |

三个必须写死的口径（否则下次还要争）
------------------------------------
① **容量口径落数据字段**：`vessel_count` / `allows_partial_load` 是**数据**，不是文档。
   canonical 夹具的 `why_the_basis_matters` 已说明：只写「900」不够 ——
   若允许拆批或多船承运，950 吨**未必**装不下，那条判据就不成立。
② **过期用绝对日期**：C-900 / C-1200 写 `2026-12-31`，C-EXPIRED 写 `2026-01-31`。
   **不用"今天减 N 天"** —— 相对日期会让"过期"随运行日漂移，历史证据无法复算（事后回看
   这一行，没人能说出"它当时到底过没过期"）。
③ **证据必须指得着**：三条候选都带 `evidence_kind` + `evidence_ref`，后者指向
   `backend/scripts/fixtures/` 下的**具体文件**，不留空。⇒ C-EXPIRED 只差 `validity` 一条
   （若它连证据也缺，被拒时就说不清"到底因为什么"，§10.2 的负例也就废了）。

⚠️ 时效性（必须知道的一件事实）
------------------------------
① 的两组日期都是**绝对**的 ⇒ 进入 **2027 年**后 C-900 / C-1200 会一起"过期"，
canonical 的「变更后仍适用」那一半就演示不出来。届时处置是**重新生成**夹具日期
（允许改绝对值，**不允许**改成相对日期），且夹具正文与候选行的 `valid_until` **两处必须同改**。
本脚本会在**真实基准日**下重跑一遍规则，与固定演示基准日结论不一致时**显式告警**。

⚠️ 本脚本**不声称**任何验收通过
------------------------------
它只让 §10.1 第 5 步从「**没有对象**」变成「**有对象可演示**」。
演示结论仍由走查 / 验收给出。⚠️ 特别地：本脚本**只登记候选，不做任何确认** ——
"只登记候选"那一刻的痕迹正是 BP-03 Exit evidence
`A chosen quotation alone does not create confirmed capacity.` 的正面证据。

⚠️ 一个已知缺口（本脚本不掩盖它）
--------------------------------
canonical 夹具用 `candidate_id`（`C-900` / `C-1200`）标识候选，但
`ent_capacity_candidate` **没有业务编号列** ⇒ 该编号在库里**没有落点**。
本脚本把它保留为 `CANDIDATE_SPECS` 的 `label`，**只用于打印与对账**，
不塞进任何列（塞进 `vessel_name` 之类会造出一个语义错误的字段）。
若接口与表将来要承载它，需另开切片 —— 本文件如实登记这个缺口，不做近似替代。
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, cast

# 以 `python scripts/xxx.py`（cwd=backend）执行时 `app` 不在 sys.path 上，
# 需显式补上 backend/。这是本文件唯一需要的 sys.path 操作。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import CursorResult  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust import capacity as capacity_svc  # noqa: E402
from app.modules.entrust import registry as reg  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

#: 与 `seed_entrust_demo.py` **同名**：本脚本是它的追加项，不是第二份工作台种子。
ORG_NAME = "演示经营主体·工作台"
SHIPPER_CODE = "seed-shipper"
MANAGER_CODE = "seed-owner"

#: 与 `demo1_canonical.json` 的 `assignment.*` 逐字对应。
ASSIGNMENT_TITLE = "DEMO-1 canonical · 钢材 800 吨 南宁 → 贵港"
CARGO_SUMMARY = "钢材 · 南宁 → 贵港（合成场景，非真实客户材料）"
QUANTITY = "800.000"
QUANTITY_UNIT = "吨"
CHANGED_QUANTITY = "950.000"

#: 三段：公路—内河—公路。`ent_leg` 是本支线唯一的"最小结构化航段对象"
#: （`ent_commitment.py` id=1），**没有服务层建段函数** ⇒ 本脚本直接写。
LEG_SPECS: tuple[tuple[int, str, str, str], ...] = (
    (1, "road", "厂区", "南宁港"),
    (2, "water", "南宁港", "贵港港"),
    (3, "road", "贵港港", "卸货地"),
)
#: 候选运力挂到**水运段**：容量问题出在这一段（判据本身取整单货量，见 `_assignment_demand`）。
WATER_LEG_SEQ = 2

FIXTURE_DIR = "backend/scripts/fixtures"

#: 固定**演示基准日**（判定用的 `as_of`）。
#: 与夹具里的绝对日期同源 ⇒ 结论可复算。⚠️ 不要改成 `today()`：
#: 那会让"这份夹具铺出来时到底能不能演示"变成"哪天跑的"的函数。
AS_OF = date(2026, 9, 17)

_DOC_C900 = FIXTURE_DIR + "/DEMO1-canonical-sample-quotation.txt"
_DOC_C1200 = FIXTURE_DIR + "/DEMO1-canonical-candidate-C1200-quotation.txt"
_DOC_EXPIRED = FIXTURE_DIR + "/DEMO1-canonical-candidate-EXPIRED-quotation.txt"

#: 三条候选。`label` 只是 canonical 的业务编号（库里没有落点，见模块文档的"已知缺口"）。
CANDIDATE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "label": "C-900",
        "carrier": "桂平航 6688",
        "vessel_name": "桂平航 6688",
        "capacity_tonnes": "900.000",
        "vessel_count": 1,
        "allows_partial_load": False,
        "rate": "45.00",
        "rate_unit": "吨",
        "currency": "CNY",
        "valid_until": "2026-12-31",
        "evidence_ref": _DOC_C900,
        #: 变更前（800 吨）应当**四条全过**。
        "expect_before": (),
        #: 变更后（950 吨）应当**只有 capacity 不过** ⇒ §3.1「900 吨候选在变更后不适用」。
        "expect_after": (capacity_svc.RULE_CAPACITY,),
        "purpose": "变更后不适用的那一家（900 吨单船不拆批，950 装不下）",
    },
    {
        "label": "C-1200",
        "carrier": "横州集运 101",
        "vessel_name": "横州集运 101",
        "capacity_tonnes": "1200.000",
        "vessel_count": 1,
        "allows_partial_load": False,
        "rate": "47.50",
        "rate_unit": "吨",
        "currency": "CNY",
        "valid_until": "2026-12-31",
        "evidence_ref": _DOC_C1200,
        "expect_before": (),
        "expect_after": (),
        "purpose": "变更后仍适用的那一家（1200 ≥ 950 ⇒ 业务可以继续完成）",
    },
    {
        "label": "C-EXPIRED",
        "carrier": "平南航运 303",
        "vessel_name": "平南航运 303",
        #: 容量**充足**：它被拒的理由只有一条（过期）。若容量也不够，
        #: §10.2 的 `expired` 负例与 `unsuitable` 负例就分不开了。
        "capacity_tonnes": "950.000",
        "vessel_count": 1,
        "allows_partial_load": False,
        "rate": "43.00",
        "rate_unit": "吨",
        "currency": "CNY",
        "valid_until": "2026-01-31",
        "evidence_ref": _DOC_EXPIRED,
        "expect_before": (capacity_svc.RULE_VALIDITY,),
        "expect_after": (capacity_svc.RULE_VALIDITY,),
        "purpose": "§10.2「过期资源不得确认」的载体（容量够、证据齐，只差有效期）",
    },
)


def _stamp() -> str:
    return utcnow_naive().strftime(_TS)


def _same_decimal(a: Any, b: Any) -> bool:
    """按值比两个"定点文本"。`45.00` 与 `45.0000` **相等** —— 差的是表示不是事实。

    直接比字符串会把"单价 4 位小数存储、夹具写 2 位"误判成口径不一致。
    """
    try:
        return Decimal(str(a).strip()) == Decimal(str(b).strip())
    except (InvalidOperation, ValueError):
        return str(a).strip() == str(b).strip()


def _require_user(db: Session, code: str) -> int:
    """按登录码查既有用户；**不存在就报错**（本脚本是追加项，不负责建账号）。"""
    row = db.execute(
        text("SELECT id FROM users WHERE openid = :o"), {"o": f"mock-openid-{code}"}
    ).first()
    if row is None:
        raise RuntimeError(
            f"找不到演示账号 {code} —— 请先跑 `python scripts/seed_entrust_demo.py`"
            "（本脚本是它的追加项，刻意不自己建账号）"
        )
    return int(row[0])


def _require_org(db: Session) -> int:
    row = db.execute(
        text("SELECT id FROM ent_organization WHERE name = :n"), {"n": ORG_NAME}
    ).first()
    if row is None:
        raise RuntimeError(
            f"找不到组织「{ORG_NAME}」—— 请先跑 `python scripts/seed_entrust_demo.py`"
        )
    return int(row[0])


def _require_entrustment(db: Session, *, org_id: int, owner_id: int) -> int:
    """货主 → 组织的**生效中**授权（`capacity._write_scope` 要求它**恰好一条**）。"""
    rows = db.execute(
        text(
            "SELECT id FROM ent_entrustment "
            "WHERE org_id = :o AND entrust_user_id = :u AND status = 'active'"
        ),
        {"o": org_id, "u": owner_id},
    ).fetchall()
    if not rows:
        raise RuntimeError(
            f"货主 {owner_id} 对组织 {org_id} 没有生效中的委托授权"
            " —— 请先跑 `python scripts/seed_entrust_demo.py`"
        )
    if len(rows) > 1:
        # 运力端点会以 409 拒绝（"存在多条生效授权，需先收敛为一条"）。
        # 种子在这里就停下，比让演示当场撞 409 好 —— 后者看起来像界面坏了。
        raise RuntimeError(
            f"货主 {owner_id} 与组织 {org_id} 之间有 {len(rows)} 条生效授权，"
            "运力端点要求**恰好一条**（否则 409）—— 请先收敛，再跑本脚本"
        )
    return int(rows[0][0])


def _ensure_assignment(
    db: Session, *, owner_id: int, org_id: int, claim_by: int
) -> tuple[int, str]:
    """确保存在一张**已受理**的 canonical 委托，返回 `(assignment_id, status)`。

    幂等按**状态**推进，不是"行存在就跳过"：上一次中途失败留下的 `draft` 若被直接复用，
    `submit_assignment` 会以「状态为 draft」拒绝，而报错看起来像夹具脚本坏了。

    ⚠️ `create_assignment` 把 `org_id` 写死为 NULL（草稿允许不完整），
    组织是**提交时**由 `submit_assignment(org_id=...)` 落上去的 —— 所以这张单必须走到
    至少 `submitted`：运力端点的 `_load_org_scope` 要求 `org_id` 非空，否则 404。
    """
    row = (
        db.execute(
            text(
                "SELECT id, status, revision, org_id, quantity, quantity_unit "
                "FROM ent_assignment WHERE owner_user_id = :u AND title = :t"
            ),
            {"u": owner_id, "t": ASSIGNMENT_TITLE},
        )
        .mappings()
        .first()
    )

    if row is None:
        created = svc.create_assignment(
            db,
            owner_user_id=owner_id,
            title=ASSIGNMENT_TITLE,
            cargo_summary=CARGO_SUMMARY,
            quantity=QUANTITY,
            quantity_unit=QUANTITY_UNIT,
        )
        assignment_id = int(created["assignment_id"])
        status = str(created["status"])
        revision = int(created["revision"])
    else:
        assignment_id = int(row["id"])
        status = str(row["status"])
        revision = int(row["revision"])
        # 复用时核对货物口径：`_assignment_demand` 取的是这一行的 quantity，
        # 它被改过则"900 ≥ 800"这类判据全部失据，而界面上看不出来。
        if (
            not _same_decimal(row["quantity"], QUANTITY)
            or str(row["quantity_unit"]) != QUANTITY_UNIT
        ):
            raise RuntimeError(
                f"委托 #{assignment_id}「{ASSIGNMENT_TITLE}」的货量口径与 canonical 不符："
                f"库里是 {row['quantity']!r} {row['quantity_unit']!r}，"
                f"canonical 是 {QUANTITY!r} {QUANTITY_UNIT!r}。"
                "—— 判据全靠这一行，口径不对则三条候选的结论全部失据。"
                "请改名重铺，或把那行改回 canonical 口径（不要在本脚本里静默沿用）。"
            )

    if status == svc.STATUS_DRAFT:
        svc.submit_assignment(
            db,
            assignment_id=assignment_id,
            actor_id=owner_id,
            org_id=org_id,
            expected_revision=revision,
        )
        status = svc.STATUS_SUBMITTED
    if status == svc.STATUS_SUBMITTED:
        svc.claim_assignment(db, assignment_id=assignment_id, actor_id=claim_by)
        status = "claimed"
    return assignment_id, status


def _ensure_legs(db: Session, *, assignment_id: int) -> dict[int, int]:
    """确保三段航段存在，返回 `{seq: leg_id}`。

    直接 INSERT：`ent_leg` 是"最小结构化航段对象"，**目前没有服务层建段函数**
    （既有建段只出现在用例与迁移里）。幂等按 `(assignment_id, seq)`（表上有唯一键）。

    复用时不核对 `mode`/起点/终点：那些是**方案内容**，被合法修改过（例如改港）
    不该让种子报错 —— 但**要看得见**，所以下面打印实际值而不是脚本里的期望值。
    """
    out: dict[int, int] = {}
    ts = _stamp()
    for seq, mode, from_name, to_name in LEG_SPECS:
        row = db.execute(
            text("SELECT id FROM ent_leg WHERE assignment_id = :a AND seq = :s"),
            {"a": assignment_id, "s": seq},
        ).first()
        if row is not None:
            out[seq] = int(row[0])
            continue
        res = cast(
            "CursorResult[Any]",
            db.execute(
                text(
                    "INSERT INTO ent_leg "
                    "(assignment_id, seq, mode, from_name, to_name, created_at, updated_at) "
                    "VALUES (:a, :s, :m, :f, :t, :ts, :ts)"
                ),
                {
                    "a": assignment_id,
                    "s": seq,
                    "m": mode,
                    "f": from_name,
                    "t": to_name,
                    "ts": ts,
                },
            ),
        )
        db.commit()
        out[seq] = int(res.lastrowid or 0)
    return out


def _fact_mismatches(row: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """复用既有候选行时，逐项核对**事实字段**。

    ⚠️ 刻意**不核对 `status`**：`candidate → confirmed` 是合法业务动作
    （演示时经理点了确认），拿它当"口径不一致"会让种子的第二次运行无端失败。
    核对的是事实：吨位/船数/拆批/单价口径/有效期/证据。
    """
    diffs: list[str] = []
    if not _same_decimal(row.get("capacity_tonnes"), spec["capacity_tonnes"]):
        diffs.append(
            f"capacity_tonnes：库 {row.get('capacity_tonnes')!r} ≠ 夹具 {spec['capacity_tonnes']!r}"
        )
    if int(row.get("vessel_count") or 0) != int(spec["vessel_count"]):
        diffs.append(
            f"vessel_count：库 {row.get('vessel_count')!r} ≠ 夹具 {spec['vessel_count']!r}"
        )
    if bool(row.get("allows_partial_load")) != bool(spec["allows_partial_load"]):
        diffs.append(
            f"allows_partial_load：库 {row.get('allows_partial_load')!r} ≠ 夹具 {spec['allows_partial_load']!r}"
        )
    if not _same_decimal(row.get("rate"), spec["rate"]):
        diffs.append(f"rate：库 {row.get('rate')!r} ≠ 夹具 {spec['rate']!r}")
    if str(row.get("rate_unit") or "") != str(spec["rate_unit"]):
        diffs.append(f"rate_unit：库 {row.get('rate_unit')!r} ≠ 夹具 {spec['rate_unit']!r}")
    if str(row.get("valid_until") or "") != str(spec["valid_until"]):
        diffs.append(f"valid_until：库 {row.get('valid_until')!r} ≠ 夹具 {spec['valid_until']!r}")
    if str(row.get("evidence_kind") or "") != str(reg.EVIDENCE_DOCUMENT):
        diffs.append(
            f"evidence_kind：库 {row.get('evidence_kind')!r} ≠ 夹具 {reg.EVIDENCE_DOCUMENT!r}"
        )
    if str(row.get("evidence_ref") or "") != str(spec["evidence_ref"]):
        diffs.append(
            f"evidence_ref：库 {row.get('evidence_ref')!r} ≠ 夹具 {spec['evidence_ref']!r}"
        )
    return diffs


def _ensure_candidate(
    db: Session, *, assignment_id: int, leg_id: int | None, spec: dict[str, Any]
) -> dict[str, Any]:
    """按 `(assignment_id, carrier)` 复用候选；不存在则走服务层命令登记。

    ⚠️ `record_candidate` **没有幂等**（它每次调用都 INSERT）—— 幂等必须在调用方做，
    否则第二跑会给同一家承运人再加一行，界面上出现两条一模一样的候选。
    `(assignment_id, carrier)` 在本夹具里唯一：三家承运人互不相同。

    ⚠️ 复用分支上做**事实核对**，不一致就抛错（不是静默沿用）：本脚本声明的口径
    是 canonical 的唯一来源，库里若有一份不同的夹具，必须由人决定重铺 ——
    静默沿用会让"演示为什么给出这个结论"永远说不清。
    """
    for row in capacity_svc.list_candidates(db, assignment_id=assignment_id):
        if str(row["carrier"]) != str(spec["carrier"]):
            continue
        diffs = _fact_mismatches(row, spec)
        if diffs:
            raise RuntimeError(
                f"候选「{spec['label']}」（{spec['carrier']}）在库里与夹具口径不一致：\n   - "
                + "\n   - ".join(diffs)
                + "\n请改名重铺，或把这些字段改回 canonical 口径 ——"
                "不要在本脚本里静默沿用（判据全靠这些字段）。"
            )
        return row

    created = capacity_svc.record_candidate(
        db,
        assignment_id=assignment_id,
        actor_user_id=0,  # 登记人不落库（`record_candidate` 自述：候选运力是**事实**）
        carrier=spec["carrier"],
        capacity_tonnes=spec["capacity_tonnes"],
        vessel_count=spec["vessel_count"],
        allows_partial_load=spec["allows_partial_load"],
        leg_id=leg_id,
        vessel_name=spec["vessel_name"],
        rate=spec["rate"],
        rate_unit=spec["rate_unit"],
        currency=spec["currency"],
        valid_until=spec["valid_until"],
        evidence_kind=reg.EVIDENCE_DOCUMENT,
        evidence_ref=spec["evidence_ref"],
    )
    return created


def _run_rules(
    row: dict[str, Any], *, demand: str, assignment_id: int, as_of: date
) -> dict[str, Any]:
    """把候选行喂给**真实规则函数**，返回 `{rule_code: outcome}`。

    判据不是"我读了一遍行、觉得没问题"，而是**同一个 `evaluate`**（服务层写库前用的那个）。
    自己另写一份判断，就得到了第二份"适用性"定义 —— 两个定义必然有一天结论不一致。
    """
    results = capacity_svc.evaluate(
        demand_tonnes=demand,
        demand_unit=QUANTITY_UNIT,
        demand_ref=f"assignment:{assignment_id}.quantity",
        capacity_tonnes=row["capacity_tonnes"],
        vessel_count=row["vessel_count"],
        allows_partial_load=row["allows_partial_load"],
        valid_until=row["valid_until"],
        evidence_kind=row["evidence_kind"],
        evidence_ref=row["evidence_ref"],
        as_of=as_of,
    )
    return {r.rule_code: r.outcome for r in results}


def _failed(run: dict[str, Any]) -> list[str]:
    return sorted(k for k, v in run.items() if v != capacity_svc.OUTCOME_PASS)


def main() -> int:
    db = SessionLocal()
    try:
        org_id = _require_org(db)
        shipper = _require_user(db, SHIPPER_CODE)
        manager = _require_user(db, MANAGER_CODE)
        entrustment_id = _require_entrustment(db, org_id=org_id, owner_id=shipper)

        assignment_id, assignment_status = _ensure_assignment(
            db, owner_id=shipper, org_id=org_id, claim_by=manager
        )
        legs = _ensure_legs(db, assignment_id=assignment_id)
        water_leg = legs.get(WATER_LEG_SEQ)

        rows = [
            _ensure_candidate(db, assignment_id=assignment_id, leg_id=water_leg, spec=spec)
            for spec in CANDIDATE_SPECS
        ]

        # ── 形状自检（不通过就抛，不打印一份"看起来就绪"的清单）──
        if len(rows) != len(CANDIDATE_SPECS):
            raise RuntimeError(f"候选数 {len(rows)} ≠ 夹具声明 {len(CANDIDATE_SPECS)}")
        for spec, row in zip(CANDIDATE_SPECS, rows, strict=True):
            if str(row["status"]) != capacity_svc.CANDIDATE_STATUS_CANDIDATE:
                # "只登记 ≠ 确认"是本夹具的核心；已确认的行意味着这份夹具
                # 已经被用过一次确认动作，演示第 5 步的"正面证据"就没了。
                print(
                    f"  ⚠️ {spec['label']} 的 status={row['status']!r}（不是 candidate）——"
                    "本夹具的定义是「只登记、未确认」；若要重演这条正面证据，请换标题重铺。"
                )
            for field in ("evidence_kind", "evidence_ref"):
                if not str(row.get(field) or "").strip():
                    raise RuntimeError(
                        f"{spec['label']} 的 {field} 为空 —— BP-03 第 3 条的 "
                        "`identified evidence` 要求**类别 + 引用**都在，缺一即不构成证据"
                    )

        # ── 规则预演：夹具不只是"行存在"，它必须能驱动出那四条结论 ──
        before = [
            _run_rules(r, demand=QUANTITY, assignment_id=assignment_id, as_of=AS_OF) for r in rows
        ]
        after = [
            _run_rules(r, demand=CHANGED_QUANTITY, assignment_id=assignment_id, as_of=AS_OF)
            for r in rows
        ]
        for spec, b, a in zip(CANDIDATE_SPECS, before, after, strict=True):
            got_before, got_after = tuple(_failed(b)), tuple(_failed(a))
            if got_before != tuple(spec["expect_before"]):
                raise RuntimeError(
                    f"{spec['label']} 在变更前（{QUANTITY} 吨）的判定与夹具声明不符："
                    f"实际不过 {got_before}，声明 {tuple(spec['expect_before'])}"
                )
            if got_after != tuple(spec["expect_after"]):
                raise RuntimeError(
                    f"{spec['label']} 在变更后（{CHANGED_QUANTITY} 吨）的判定与夹具声明不符："
                    f"实际不过 {got_after}，声明 {tuple(spec['expect_after'])}"
                )

        # ── 时效性告警（绝对日期会随运行日漂移，**不静默**）──
        today = capacity_svc.today_utc()
        drift: list[str] = []
        if today != AS_OF:
            for spec, r in zip(CANDIDATE_SPECS, rows, strict=True):
                got = tuple(
                    _failed(
                        _run_rules(r, demand=QUANTITY, assignment_id=assignment_id, as_of=today)
                    )
                )
                if got != tuple(spec["expect_before"]):
                    drift.append(f"{spec['label']}（今天 {today.isoformat()} 下不过 {got}）")

        # ── 打印形状 ──
        print("「canonical 运力候选」夹具就绪：")
        print(f"  组织：{ORG_NAME} = id {org_id}")
        print(f"  授权：entrustment_id={entrustment_id}（运力端点要求**恰好一条**有效授权）")
        print(f"  {SHIPPER_CODE} user_id={shipper} 货主 ／ {MANAGER_CODE} user_id={manager} 经理")
        print(
            f"  委托 #{assignment_id}「{ASSIGNMENT_TITLE}」 → {assignment_status}"
            f"（货量 {QUANTITY} {QUANTITY_UNIT}；变更后 {CHANGED_QUANTITY}）"
        )
        print(f"  航段 {len(legs)} 段：" + " → ".join(f"{s}:{legs[s]}" for s in sorted(legs)))
        print(f"  候选 {len(rows)} 条（**全部只登记、未确认**）：")
        for spec, row in zip(CANDIDATE_SPECS, rows, strict=True):
            partial = "允许拆批" if row["allows_partial_load"] else "不拆批"
            print(
                f"      {spec['label']}｜{row['carrier']}｜{row['capacity_tonnes']} 吨"
                f" × {row['vessel_count']} 船｜{partial}"
                f"｜{row['rate']} 元/{row['rate_unit']}｜有效至 {row['valid_until']}"
                f"｜证据 {row['evidence_kind']}／{row['evidence_ref']}"
            )
            print(f"          status={row['status']}（腿 {row['leg_id']}）—— {spec['purpose']}")
        print(f"  规则预演（基准日 {AS_OF.isoformat()}，四条规则全跑）：")
        for spec, b, a in zip(CANDIDATE_SPECS, before, after, strict=True):
            fb, fa = _failed(b), _failed(a)
            print(
                f"      {spec['label']}：变更前 "
                + ("四条全过" if not fb else "不过 " + ", ".join(fb))
                + " ⇒ 变更后 "
                + ("四条全过" if not fa else "不过 " + ", ".join(fa))
            )
        print("  由此可演示的三条结论（**本脚本只铺数据，不声称结论已验收**）：")
        print("      ① C-900 变更后仅 capacity 不过 ⇒ §3.1「900 吨候选在变更后不再适用」（D1-09）")
        print("      ② C-1200 变更后仍全过 ⇒ 变更后业务**可以继续完成**（夹具为此而备）")
        print("      ③ C-EXPIRED 仅 validity 不过 ⇒ §10.2「expired resource 不得确认」")
        print("  未做（刻意的）：**没有任何确认**。三张确认相关表都是空的 ——")
        print("      这正是 BP-03 Exit evidence `A chosen quotation alone does not create")
        print("      confirmed capacity.` 的正面证据，演示第 5 步的第 1 个动作就是它。")
        if drift:
            print("  ⚠️ 时效性告警：这些候选的**绝对**有效期已随运行日漂移 ——")
            for d in drift:
                print(f"      {d}")
            print("      ⇒ 处置：重新生成夹具日期（**不要**改成相对日期），")
            print("         且夹具正文的「有效期至」与候选行的 valid_until **两处必须同改**。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
