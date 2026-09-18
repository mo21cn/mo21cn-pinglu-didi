"""「五维齐备」夹具 —— 铺一张**可以直接结案**的委托（DEMO-1 §10.1 第 12 步）。

为什么需要它
------------
第 12 步的判据是"按真实规则结案"，而结案要求**五个维度同时成立**：
任务处置完 / 必需证据齐 / 无阻断案件且无未关闭案件与未完成复核 / 结算已批准且
**客户已确认** / 余额按已记录的收付依据结清。已有种子（`seed_entrust_demo`）铺的
委托刻意停在"有阻断案件、财务未开始"那一步 —— 那是第 8–11 步要演示的**中间态**。

⇒ 本夹具铺**一张新的干净委托**并把它推到"齐备"。第 53 章 ⑧（齐备 ⇒ 结案成功）
与"同一张委托连续运行"的顺序链都以它为载体。

全部走**真实业务命令**（受理 → 任务 → 费用 → 结算 → 客户确认 → 收付），
⛔ 不手改状态 —— "可以结案"这件事本身就是这些命令产生的；手改出来的前提
不证明链路可用。

为什么**不**纳入 `reset_demo_env.py` 的 `SEED_ORDER`
----------------------------------------------------
与 canonical / contract_flow 同一条理由：它是**按需夹具**（有状态数据），
进基线会让 runbook §6.3 的基线表逐格失效。用 `--extra-seeds` 显式追加。

自证（本脚本的验收判据）
------------------------
末尾直接调 `closure.closure_readiness(...)`，**断言 `ready is True`**，否则**非 0 退出**
并打印逐条缺项 —— 夹具自己证明"它铺出来的东西真的齐备"，而不是"我以为齐备"。
（这条是刻意的 fail-closed：铺不齐就报错，⛔ 不留一个"看起来齐备"的库给走查用。）

幂等：同名委托已存在 ⇒ 复用（打印即可），不重复铺子对象；`--reset` 不做
（本机 safe-delete 守卫会拦删除，重建库请用 `mv`）。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust import charges as charge_svc  # noqa: E402
from app.modules.entrust import closure as closure_svc  # noqa: E402
from app.modules.entrust import settlement as settle_svc  # noqa: E402
from app.modules.entrust import tasks as task_svc  # noqa: E402

#: 与 `seed_entrust_demo.py` 的三个常量**逐字一致**。本脚本是它的**追加项**、
#: 不是第二份种子 ⇒ 这三个名字是演示身份的唯一来源，改名要两处一起改
#: （取不到就报错退出，不会静默铺到另一个组织上）。
ORG_NAME = "演示经营主体·工作台"
SHIPPER_CODE = "seed-shipper"
MANAGER_CODE = "seed-owner"

#: 带「演示」前缀，便于在共享库里辨认；结案之后它仍然可见（历史留痕）。
ASSIGNMENT_TITLE = "演示委托·五维齐备（可结案）"

#: 一笔应收 = 一笔收付 ⇒ 余额结清（"结清"由**已记录的收付依据**账面成立，不是口头）。
AMOUNT = "1800.00"
CURRENCY = "CNY"
BASIS = "演示费率表（五维齐备夹具）"
PAYMENT_REF = "水单-SAMPLE-READY-001"

#: 结案所需的权限（**不含**它就会被 403 —— 认领是接单、结案是对客户宣告做完，
#: 两件事不共用一个码）。列表里其余几项是这条链上每一步各自要的。
REQUIRED_PERMS = (
    "entrust:view",
    "entrust:assignment:claim",
    "entrust:assignment:complete",
    "entrust:task:dispatch",
    "entrust:settlement:create",
)


def _user(db: Session, code: str) -> int:
    """按登录码取演示用户 id（`openid = mock-openid-<code>`，与 `WECHAT_MOCK` 一致）。"""
    row = db.execute(
        text("SELECT id FROM users WHERE openid = :o"), {"o": f"mock-openid-{code}"}
    ).first()
    if row is None:
        raise SystemExit(f"!! 演示用户 {code} 不存在 —— 先跑 `python scripts/seed_demo.py`")
    return int(row[0])


def _org(db: Session, name: str) -> int:
    row = db.execute(text("SELECT id FROM ent_organization WHERE name = :n"), {"n": name}).first()
    if row is None:
        raise SystemExit(
            f"!! 演示组织「{name}」不存在 —— 先跑 `python scripts/seed_entrust_demo.py`"
        )
    return int(row[0])


def _ensure_complete_permission(db: Session, *, org_id: int, owner_id: int) -> list[str]:
    """确保（组织, 货主）这条生效授权里有结案权限；返回最终权限列表。

    为什么要**并集**而不是覆盖：覆盖会把其它切片要的权限抹掉，让它们在本脚本
    之后静默 403（那是"改一处、坏一片"的典型）。
    """
    import json

    row = db.execute(
        text(
            "SELECT id, permissions FROM ent_entrustment "
            "WHERE org_id = :o AND entrust_user_id = :u AND status = 'active'"
        ),
        {"o": org_id, "u": owner_id},
    ).first()
    if row is None:
        raise SystemExit("!! 该 (组织, 货主) 上没有生效授权 —— 先跑 seed_entrust_demo")
    current = json.loads(row[1] or "[]")
    merged = list(dict.fromkeys([*current, *REQUIRED_PERMS]))
    if merged != current:
        db.execute(
            text("UPDATE ent_entrustment SET permissions = :p WHERE id = :i"),
            {"p": json.dumps(merged), "i": int(row[0])},
        )
        db.commit()
    return merged


def _existing(db: Session) -> int | None:
    row = db.execute(
        text("SELECT id, status FROM ent_assignment WHERE title = :t ORDER BY id DESC"),
        {"t": ASSIGNMENT_TITLE},
    ).first()
    if row is None:
        return None
    if str(row[1]) != "claimed":
        raise SystemExit(
            f"!! 同名委托 #{int(row[0])} 的状态是 {row[1]!r}（不是 claimed）—— "
            "重建库请用 `mv` 换新库名，⛔ 别在本库里删"
        )
    return int(row[0])


def _build(db: Session, *, org_id: int, owner_id: int, manager_id: int) -> int:
    created = svc.create_assignment(db, owner_user_id=owner_id, title=ASSIGNMENT_TITLE)
    aid = int(created["assignment_id"])
    svc.submit_assignment(
        db,
        assignment_id=aid,
        actor_id=owner_id,
        org_id=org_id,
        expected_revision=int(created["revision"]),
    )
    svc.claim_assignment(db, assignment_id=aid, actor_id=manager_id)

    # ① 任务处置：建 → 开工 → 完成。**不要求证据**（`required_evidence` 留空）
    #    ⇒ "交付证据"这一维度由"没有未满足的证据要求"满足，而不是靠补录道具。
    task = task_svc.create_task(
        db,
        assignment_id=aid,
        actor_id=manager_id,
        task_type=task_svc.TASK_TYPE_HANDOVER,
        title="卸货交接（五维齐备夹具）",
    )
    task_svc.start_task(db, task_id=int(task["task_id"]), actor_id=manager_id)
    task_svc.complete_task(db, task_id=int(task["task_id"]), actor_id=manager_id)

    # ② 结算：草稿（不进合计）→ 确认 → 出**版本** → 内部确认 →
    #    ⭐ **客户确认**（只有货主本人能做）→ 收付依据。
    charge = charge_svc.record_charge(
        db,
        assignment_id=aid,
        direction="receivable",
        charge_kind="freight",
        amount=AMOUNT,
        currency=CURRENCY,
        basis=BASIS,
        actor_id=manager_id,
    )
    charge_svc.confirm_charge(db, charge_id=charge["charge_id"])
    version = settle_svc.create_settlement(db, assignment_id=aid, actor_id=manager_id)
    sid = int(version["settlement_id"])
    settle_svc.approve_settlement(db, settlement_id=sid, actor_id=manager_id)
    settle_svc.confirm_settlement(
        db, settlement_id=sid, customer_user_id=owner_id, decision="accepted"
    )
    settle_svc.record_payment(
        db,
        settlement_id=sid,
        actor_id=manager_id,
        direction="receivable",
        amount=AMOUNT,
        ref=PAYMENT_REF,
    )
    return aid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="铺一张五维度齐备（可直接结案）的委托")
    parser.add_argument("--quiet", action="store_true", help="只打印结论行")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        org_id = _org(db, ORG_NAME)
        owner_id = _user(db, SHIPPER_CODE)
        manager_id = _user(db, MANAGER_CODE)
        perms = _ensure_complete_permission(db, org_id=org_id, owner_id=owner_id)

        existing = _existing(db)
        reused = existing is not None
        aid = (
            existing
            if reused
            else _build(db, org_id=org_id, owner_id=owner_id, manager_id=manager_id)
        )

        readiness: dict[str, Any] = closure_svc.closure_readiness(
            db, assignment_id=aid, user_id=manager_id
        )
        if not args.quiet:
            print("「五维齐备」夹具：")
            print(f"  委托          : #{aid}  {ASSIGNMENT_TITLE}（{'复用' if reused else '新建'}）")
            print(f"  货主 / 组织   : user #{owner_id} / org #{org_id}")
            print(f"  经理          : user #{manager_id}（授权 {len(perms)} 项，含结案）")
            print(
                f"  齐备度        : ready={readiness['ready']}  缺项 {len(readiness['missing'])} 条"
            )
            for key, count in (readiness.get("missing_by_dimension") or {}).items():
                print(f"      {key}: {count}")
            print(f"  财务派生      : {readiness.get('financial_status')}")
    finally:
        db.close()

    if not readiness["ready"]:
        print("!! 夹具**没铺齐**（fail-closed，不留给走查一个假象）：", flush=True)
        for item in readiness["missing"]:
            print(f"   [{item['dimension']}] {item['code']}: {item['message']}", flush=True)
        return 1
    print(f"OK seed_entrust_completion_ready（aid={aid}，ready=True）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
