"""「变更复核检查点」夹具 —— `DEMO-1-runbook.md` §7 三种夹具状态的第 2 行。

用法（backend 目录下）：

    python scripts/seed_entrust_revalidation.py

前置（不满足就报错退出，**不自己造**）
--------------------------------------
必须先跑 `seed_entrust_demo.py`。本脚本**复用**它铺出的三样东西：

* 组织 `演示经营主体·工作台`；
* 货主 `seed-shipper`（委托的创建人）；
* 经理 `seed-owner`（组织成员，案件的登记人 / 决定人）；
* 以及货主 → 组织那条**生效中**的委托授权（成果必须挂在授权上）。

为什么不自造这四样：runbook §2 的组织表是**按名字**辨认"这份数据属于哪份种子"的。
自己现造一套会让共享库里出现第二个工作台组织，从此"某条队列是谁铺的"再也说不清。

为什么**不**纳入 `reset_demo_env.py` 的 `SEED_ORDER`
--------------------------------------------------
复位要的起点是「一致、可复制的**干净**起点」（§6.1），而夹具是**有状态的数据** ——
把它塞进复位基线，会让 §6.3 那张基线表（`ent_exception`=2、`ent_revalidation`=0 …）
逐格失效，也让"复位后环境是干净的"这句话不再成立。
⇒ 与 `seed_contract_cases.py` 同属**按需追加**（§5 第 ④ 行），不进基线。

要什么（§7 的定义）
-------------------
> **变更复核检查点**：需要一张处于"变更待复核"（`revalidation` open）的单。

本脚本铺的是这个终局的**完整链路**，而不是往 `ent_revalidation` 直接插几行：

    建委托草稿 → 提交 → 受理
      → 挂 4 个成果（1 个登记为受影响项，另 3 个只存在、**刻意不登记**）
      → 登记变更请求（`change_request`，类别 `origin_destination_mode`）
      → in_review → approved（携带**依据版本**与**批准的修改内容**）
      → apply（应用变更 ＋ 变更传播，同一事务）

为什么不直接插行
----------------
`revalidation.py` 的核心不变式：某成果需要复核 ⇔ 存在一行 `status='open'` 的行。
手插几行只能得到"看起来像夹具的几行数据"，因为：

* **复核任务**（`ent_workflow_task`）不会连带产生 ⇒ 界面上"这批复核做完了没有"永远空白；
* `target_revision_id` 只能自己编 —— 它本该是**标记那一瞬间该成果生效的版本**；
* **批准快照**不会存在 ⇒ `apply` 的「旧批准不得直接应用到新内容」无从演示；
* 类别 → 复核范围的映射不会被执行到 ⇒ 夹具可能与 DR-0016 §3 的映射表悄悄不一致。

⇒ 夹具走**与线上同一条服务层链路**，与用户点出来的是同构的。

铺出的形状（与脚本末尾的实测打印逐项对应）
----------------------------------------
| 项 | 值 |
| --- | --- |
| 委托 | `演示委托·变更复核样本` → `claimed` |
| 案件 | `change_request` · `review-required` · `applied` |
| 变更类别 | `origin_destination_mode`（DR-0016 §3 第 2 行） |
| 受影响项 | **1 条**（`customer_quote`）—— 刻意只登记一个 |
| 复核项 | **4 条 `open`**：1 条挂在成果上 ＋ 3 条"无对象但有区域"（§4.3） |
| 范围不足 | **3 类**候选成果存在却未登记 ⇒ 进 `unconfirmed` 交经理人确认（§4.1） |

那 3 类"存在却未登记"的成果是**故意**的：它是 DR-0016 §4.1 唯一的可演示入口 ——
"映射点名了这类成果、它在委托里确实存在、却没被登记为受影响项"。
不铺它们，`unconfirmed` 永远是空数组，而空数组与"这个机制没实现"在界面上长得一样。

⚠️ 本脚本**不声称**夹具等于"变更复核"的验收通过：它只是让 §7 那一行从
「**缺失**」变成「**有对象可演示**」。演示结论仍由走查 / 验收给出。

一个读表时的注意点（既有实现的行为，本脚本**未做任何改动**）
----------------------------------------------------------
`ent_revalidation.target_revision_id` 指向的是**应用之后**生效的那一版 —— 变更传播与
"应用"在同一个事务里、且排在**追加新版本之后**（`exceptions.apply_case`）。
读这张夹具时不要把它当成"被替换掉的那一版"。
"""

from __future__ import annotations

import json
import os
import sys

# 以 `python scripts/xxx.py`（cwd=backend）执行时 `app` 不在 sys.path 上，
# 需显式补上 backend/。这是本文件唯一需要的 sys.path 操作。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.entrust import artifacts as artifacts_svc  # noqa: E402
from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust import exceptions as exceptions_svc  # noqa: E402
from app.modules.entrust import revalidation as reval_svc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

#: 与 `seed_entrust_demo.py` **同名**：本脚本是它的追加项，不是第二份工作台种子。
ORG_NAME = "演示经营主体·工作台"
SHIPPER_CODE = "seed-shipper"
MANAGER_CODE = "seed-owner"

ASSIGNMENT_TITLE = "演示委托·变更复核样本"
CASE_TITLE = "演示变更·改港（起讫地由贵港改为梧州）"

#: DR-0016 §3 第 2 行：起讫地 / 运输方式变化。**不写字面量** —— 抄常量，
#: 决策记录改名时这里会跟着变，而字面量不会（那正是最贵的静默失配）。
CHANGE_CATEGORY = reval_svc.CHANGE_ROUTE

#: 登记为**受影响项**的那一个成果：`customer_quote` 在 `CHANGE_ROUTE` 的候选类型内，
#: 且能落到 `quote` 复核区域「报价范围」⇒ 会产生**带目标**的复核项。
LINKED_ARTIFACT_TYPE = "customer_quote"

#: 只存在、**刻意不登记**的成果：制造 `unconfirmed`（§4.1 的可演示入口）。
#: 三者都在 `CHANGE_ROUTE.artifact_types` 内 —— 不在候选里就永远算不上"范围不足"。
UNLINKED_ARTIFACT_TYPES: tuple[str, ...] = (
    "procurement_confirm",
    "contract_review",
    "quote_parsed",
)

ARTIFACT_SPECS: dict[str, dict[str, object]] = {
    LINKED_ARTIFACT_TYPE: {
        "amount": "48000.00",
        "currency": "CNY",
        "includes": "运费、装卸费、保险费（南宁 → 贵港）",
        "valid_until": "2026-11-15",
    },
    "procurement_confirm": {
        "supplier": "演示承运方（非真实客户）",
        "agreed_scope": "南宁→贵港 1200 吨钢材，含装卸",
        "agreed_amount": "41000.00",
        "currency": "CNY",
    },
    "contract_review": {
        "parties": "演示货主 / 演示承运方",
        "clauses": "违约条款、装卸时限、货损赔付",
        "effective_date": "2026-09-25",
    },
    "quote_parsed": {
        "carrier": "贵港航运 1024（演示）",
        "rate": "46500.00",
        "cargo_name": "钢材",
        "quantity": "1200",
        "quantity_unit": "吨",
        "route": "南宁 → 贵港",
    },
}

#: 经批准的修改内容（= 应用后 `customer_quote` 的新载荷）。
#: **只改与改港有关的两个字段** —— 把整份载荷重写一遍会让"这次变更到底改了什么"
#: 在版本历史里看不出来（`note` 会写"由案件 #N 的批准变更应用"，但改了哪几个字段
#: 只能靠逐字段比对）。
APPROVED_CHANGE: dict[str, object] = {
    "amount": "51500.00",
    "currency": "CNY",
    "includes": "运费、装卸费、保险费（南宁 → 梧州）",
    "valid_until": "2026-11-15",
}


def _stamp() -> str:
    return utcnow_naive().strftime(_TS)


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
    """货主 → 组织的**生效中**授权。成果必须挂在授权上（`ent_artifact.entrustment_id` 非空）。"""
    row = db.execute(
        text(
            "SELECT id FROM ent_entrustment "
            "WHERE org_id = :o AND entrust_user_id = :u AND status = 'active'"
        ),
        {"o": org_id, "u": owner_id},
    ).first()
    if row is None:
        raise RuntimeError(
            f"货主 {owner_id} 对组织 {org_id} 没有生效中的委托授权"
            " —— 请先跑 `python scripts/seed_entrust_demo.py`"
        )
    return int(row[0])


def _ensure_assignment(
    db: Session, *, owner_id: int, org_id: int, claim_by: int
) -> tuple[int, str]:
    """确保存在一张**已受理**的夹具委托，返回 `(assignment_id, status)`。

    与 `seed_entrust_demo.py` 同一条处置：幂等按**状态**推进，不是"行存在就跳过" ——
    上一次中途失败留下的 `draft` 若被直接复用，`raise_case` 会以
    「委托状态为 draft」拒绝，而报错看起来像夹具脚本坏了。
    """
    row = db.execute(
        text(
            "SELECT id, status, revision FROM ent_assignment "
            "WHERE owner_user_id = :u AND title = :t"
        ),
        {"u": owner_id, "t": ASSIGNMENT_TITLE},
    ).first()
    if row is None:
        created = svc.create_assignment(
            db,
            owner_user_id=owner_id,
            title=ASSIGNMENT_TITLE,
            cargo_summary="钢材 · 南宁 → 贵港（演示货物，非真实客户材料）",
            quantity="1200.000",
            quantity_unit="吨",
        )
        assignment_id = int(created["assignment_id"])
        status = str(created["status"])
        revision = int(created["revision"])
    else:
        assignment_id, status, revision = int(row[0]), str(row[1]), int(row[2])

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


def _ensure_artifact(
    db: Session, *, entrustment_id: int, artifact_type: str, created_by: int, assignment_id: int
) -> int:
    """按 `(归属委托, 成果类型)` 复用成果；不存在则走服务层创建（首版即生效）。"""
    row = db.execute(
        text("SELECT id FROM ent_artifact WHERE artifact_type = :t AND assignment_id = :a"),
        {"t": artifact_type, "a": assignment_id},
    ).first()
    if row:
        return int(row[0])
    created = artifacts_svc.create_artifact(
        db,
        entrustment_id=entrustment_id,
        artifact_type=artifact_type,
        payload=dict(ARTIFACT_SPECS[artifact_type]),
        created_by=created_by,
        assignment_id=assignment_id,
    )
    return int(created["artifact_id"])


def _current_revision(db: Session, artifact_id: int) -> int | None:
    """成果**当前生效**的版本 id —— 批准快照里那个"基础版本"就是它。

    刻意不用"最新版本"：最新可能是草稿，而 `apply` 的过期判据比的是**生效**版本
    （`exceptions._artifact_current_revision`）。两处取同一个定义，夹具才不会造出
    一个"批准时就已经过期"的案件。
    """
    row = db.execute(
        text("SELECT current_revision_id FROM ent_artifact WHERE id = :a"), {"a": artifact_id}
    ).first()
    return None if row is None or row[0] is None else int(row[0])


def _case_row(db: Session, *, assignment_id: int, title: str) -> tuple[int, str, int] | None:
    row = db.execute(
        text(
            "SELECT id, status, revision_no FROM ent_exception "
            "WHERE assignment_id = :a AND title = :t"
        ),
        {"a": assignment_id, "t": title},
    ).first()
    if row is None:
        return None
    return int(row[0]), str(row[1]), int(row[2])


def _advance_case(
    db: Session,
    *,
    case_id: int,
    status: str,
    revision: int,
    actor_id: int,
    artifact_id: int,
) -> tuple[str, int, list[str]]:
    """把案件推进到 `applied`（已到则原地不动），返回 `(状态, 版本, 走过的步骤)`。

    每一步都**重新读**当前生效版本再决定 —— 决定依据必须是"此刻的版本"，
    复用循环外读到的那个值会在中途失败重跑时拿到过期依据（`apply` 会以 stale 拒绝）。
    """
    steps: list[str] = []
    for _ in range(5):  # 状态机最长路径 open→in_review→approved→applied，5 是护栏
        if status == exceptions_svc.STATUS_OPEN:
            exceptions_svc.decide(
                db,
                exception_id=case_id,
                actor_id=actor_id,
                to_status=exceptions_svc.STATUS_IN_REVIEW,
                expected_revision=revision,
                decision_note="复核夹具：进入复核",
                change_category=CHANGE_CATEGORY,
            )
            status, revision = exceptions_svc.STATUS_IN_REVIEW, revision + 1
            steps.append("open→in_review")
            continue
        if status == exceptions_svc.STATUS_IN_REVIEW:
            basis = _current_revision(db, artifact_id)
            if basis is None:  # pragma: no cover - 成果刚建好就没有生效版本，属真异常
                raise RuntimeError(f"成果 #{artifact_id} 没有生效版本，无法作为批准依据")
            exceptions_svc.decide(
                db,
                exception_id=case_id,
                actor_id=actor_id,
                to_status=exceptions_svc.STATUS_APPROVED,
                expected_revision=revision,
                decision_note="复核夹具：批准改港（依据当前生效版本）",
                basis_revision_id=basis,
                approved_changes={f"artifact#{artifact_id}": dict(APPROVED_CHANGE)},
            )
            status, revision = exceptions_svc.STATUS_APPROVED, revision + 1
            steps.append(f"in_review→approved（basis=revision #{basis}）")
            continue
        if status == exceptions_svc.STATUS_APPROVED:
            exceptions_svc.apply_case(
                db,
                exception_id=case_id,
                actor_id=actor_id,
                expected_revision=revision,
            )
            status, revision = exceptions_svc.STATUS_APPLIED, revision + 1
            steps.append("approved→applied（应用 + 变更传播，同一事务）")
            continue
        break
    return status, revision, steps


def _review_items(db: Session, case_id: int) -> list[dict[str, object]]:
    return [
        {
            "review_key": str(r["review_key"]),
            "area": str(r["area"]),
            "task_type": str(r["task_type"]),
            "review_task_id": int(r["review_task_id"]),
            "target_id": r["target_id"],
            "target_revision_id": r["target_revision_id"],
            "status": str(r["status"]),
        }
        for r in reval_svc.list_for_case(db, case_id)
    ]


def _unconfirmed(db: Session, case_id: int) -> list[str]:
    """取**最后一次** `revalidation_planned` 事件里记下的 `unconfirmed`。

    从事件读而不是重算：范围不足是"当时交给经理人确认的那份清单"，
    重算会拿到"按今天的库算出来"的另一份，两者不等价（事件是审计事实）。
    """
    row = db.execute(
        text(
            "SELECT payload_json FROM ent_exception_event "
            "WHERE exception_id = :c AND event_kind = :k ORDER BY seq DESC"
        ),
        {"c": case_id, "k": exceptions_svc.EVENT_REVALIDATION_PLANNED},
    ).first()
    if row is None or not row[0]:
        return []
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError):  # pragma: no cover - 载荷损坏属真异常
        return []
    raw = payload.get("unconfirmed") or []
    return [str(x) for x in raw]


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

        artifact_ids: dict[str, int] = {}
        for artifact_type in (LINKED_ARTIFACT_TYPE, *UNLINKED_ARTIFACT_TYPES):
            artifact_ids[artifact_type] = _ensure_artifact(
                db,
                entrustment_id=entrustment_id,
                artifact_type=artifact_type,
                created_by=manager,
                assignment_id=assignment_id,
            )
        linked_id = artifact_ids[LINKED_ARTIFACT_TYPE]

        existing = _case_row(db, assignment_id=assignment_id, title=CASE_TITLE)
        if existing is None:
            created = exceptions_svc.raise_case(
                db,
                assignment_id=assignment_id,
                actor_id=manager,
                kind=exceptions_svc.KIND_CHANGE_REQUEST,
                title=CASE_TITLE,
                severity="medium",
                impact_kind=exceptions_svc.IMPACT_REVIEW_REQUIRED,
                cause="货主口头通知改港、待书面确认（演示）",
                change_category=CHANGE_CATEGORY,
                due_at=None,
                links=[{"target_kind": exceptions_svc.TARGET_ARTIFACT, "target_id": linked_id}],
            )
            case_id, case_status, revision = int(created["id"]), str(created["status"]), 1
            created_now = True
        else:
            case_id, case_status, revision = existing
            created_now = False

        case_status, revision, steps = _advance_case(
            db,
            case_id=case_id,
            status=case_status,
            revision=revision,
            actor_id=manager,
            artifact_id=linked_id,
        )

        items = _review_items(db, case_id)
        open_items = [i for i in items if i["status"] == "open"]
        unconfirmed = _unconfirmed(db, case_id)

        if case_status != exceptions_svc.STATUS_APPLIED:  # pragma: no cover - 状态机护栏
            raise RuntimeError(f"案件 #{case_id} 未能推进到 applied，当前 {case_status}")
        if not open_items:
            raise RuntimeError(
                f"案件 #{case_id} 已 applied，但没有任何 open 的复核项 —— "
                "夹具的定义就是「一张处于变更待复核状态的单」，没有复核项等于没铺出来。"
                "（若此前被人为完成/取消过复核任务，请换一个 CASE_TITLE 重铺，"
                "不要手改 ent_revalidation 去凑数。）"
            )

        print("「变更复核检查点」夹具就绪：")
        print(f"  组织：{ORG_NAME} = id {org_id}")
        print(f"  授权：entrustment_id={entrustment_id}")
        print(f"  {SHIPPER_CODE} user_id={shipper} 货主 ／ {MANAGER_CODE} user_id={manager} 经理")
        print(f"  委托 #{assignment_id} 「{ASSIGNMENT_TITLE}」 → {assignment_status}")
        print(f"  成果：{', '.join(f'{t}#{i}' for t, i in artifact_ids.items())}")
        print(f"      （受影响项只登记 {LINKED_ARTIFACT_TYPE}#{linked_id}，另 3 个刻意不登记）")
        print(
            f"  案件 #{case_id} 「{CASE_TITLE}」 → {case_status}（{'新建' if created_now else '复用'}）"
        )
        print(f"      类别 {CHANGE_CATEGORY}｜案件版本 revision_no={revision}")
        print("      推进：" + (" → ".join(steps) if steps else "（早已 applied，本轮未再写）"))
        print(f"  复核项 {len(open_items)} 条 open：")
        for i in open_items:
            target = i["target_id"] if i["target_id"] is not None else "（无对象）"
            print(
                f"      {i['review_key']}｜{i['area']}｜任务 {i['task_type']}"
                f"#{i['review_task_id']}｜目标 {target}"
            )
        print(
            f"  范围不足（unconfirmed，交经理人确认）{len(unconfirmed)} 类："
            + (", ".join(unconfirmed) if unconfirmed else "（无）")
        )
        print("      → 这 3 类成果在委托里存在却没被登记为受影响项，是 DR-0016 §4.1 的可演示入口")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
