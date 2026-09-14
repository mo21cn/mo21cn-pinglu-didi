"""委托工作台 / 详情页的演示数据（可重复执行 · 幂等）。

用法（backend 目录下）：

    python scripts/seed_entrust_demo.py

为什么要单独一份，而不是并进 `seed_entrust_orgpicker.py`
----------------------------------------------------
orgpicker 那份的职责是**组织选择器**：三个身份分别落在 `pickOrg` 的
`none` / `only` / `ambiguous` 三条分支上，委托人只是"切换组织后列表真的变了"
的配对样本，**不含任何任务与成果**。

而 UI-05 工作台的取数对象是**七个槽位**（DR-0010 §3.1）：若库里没有任务与成果，
七个槽位会全是「暂无记录」，e2e 只能验证"空态渲染对不对"，验证不了
"有数据时落对了槽位没有"。两种数据的形状与验收目标都不同；混在一起还会让
orgpicker 那三条分支的断言随工作台数据改动而变脆。

实现在哪一层
------------
* `ent_organization` / `ent_org_member` / `ent_entrustment` **没有 HTTP 接口**
  （它们是权限叠加层的数据，只能由组织管理动作写入）→ 直接落库；
* 委托、任务、成果**全部走真实服务层**（`assignments` / `tasks` / `artifacts`），
  与线上同一套状态机与校验。种子造出的东西与用户点出来的是同构的 ——
  否则 e2e 拿到的只是"看起来像真载荷的假载荷"。
* `ent_artifact.assignment_id` 的归属由服务层直接写入。HTTP 层的
  `artifacts_api._resolve_attribution` 会额外校验归属有效性，那属于**接口契约**
  的职责，已由 `tests/test_entrust_artifact_assignment.py` 覆盖；种子用自己刚造的
  委托，归属天然自洽，不必把 HTTP 层的校验搬进来。

铺出什么
--------
身份**复用既有演示账号**，因此 e2e 已有的三角色 token 开箱可用，不必新增登录：

* `seed-shipper` ── 货主（委托的 owner）；
* `seed-owner`   ── 演示组织的**经理**成员（组织队列视角的数据源）。

| 委托 | 状态 | 这份数据要验证什么 |
|---|---|---|
| `演示委托·工作台样本` | `claimed` | 六类成果 + 七类任务，七槽位里六个有真实记录 |
| `演示委托·工作台样本（待受理）` | `submitted` | 详情页给出「受理委托」入口；队列里与上一张并存 |

任务覆盖全部 7 个 `task_type`（`plan_tasks` 是**全单总览**，会读到全部）；
成果覆盖注册表 6 类中的 5 类，其中：

* `settlement_draft` **刻意缺必填字段** `receivable_lines` —— 它必须落在
  「信息缺失」而不是「暂无记录」（DR-0010 §3.6 的四态不是一句话）；
* 另造一条 **`assignment_id IS NULL` 的历史成果**，验证
  `unassigned_artifact_total` 如实报数（DR-0012 的历史存量出口）。

一条**未指派**任务（`handover`）也是有意留的：它让「下一责任方」落在
「尚未分配」而不是「不适用」—— 两句话的下一步动作完全不同。

演示用途：组织名、委托标题一律带「演示」前缀，便于在共享库里辨认与清理。
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
from app.modules.auth import service as auth_service  # noqa: E402
from app.modules.entrust import artifacts as artifacts_svc  # noqa: E402
from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust import tasks as tasks_svc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

#: 演示组织。名字带「演示」前缀，且与 orgpicker 的甲/乙两组织**刻意不同名** ——
#: 两个种子脚本的组织不重名，才能一眼看出"某条队列属于哪一份种子"。
ORG_NAME = "演示经营主体·工作台"

SHIPPER_CODE = "seed-shipper"
MANAGER_CODE = "seed-owner"

ASSIGNMENT_MAIN = "演示委托·工作台样本"
ASSIGNMENT_OPEN = "演示委托·工作台样本（待受理）"

#: 演示组织被授予的权限范围（货主 → 组织）。七槽位的人工落点都要用到它：
#: 认领（`claim`）、派单（`task:dispatch`）缺一个，种子里对应的动作就会 403。
ORG_PERMISSIONS = [
    "entrust:view",
    "entrust:assignment:claim",
    "entrust:quote:create",
    "entrust:quote:publish",
    "entrust:task:dispatch",
    "entrust:settlement:create",
]

#: 任务清单：`(task_type, title, 是否指派给经理)`。
#: 覆盖全部 7 个类型 —— `plan_tasks` 读全单任务，缺一个类型就少一种落点。
TASK_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("collect_documents", "收集随船单证与货物清单", True),
    ("quote", "向两家船东询价", True),
    ("purchase", "确认承运方与舱位", True),
    ("contract", "核对运输合同条款", True),
    ("execution", "安排装船与在途跟踪", True),
    ("handover", "到港交接与签收（待指派）", False),
    ("settlement", "出具结算草稿", True),
)

#: 成果清单：`(artifact_type, 载荷)`。字段必须落在注册表的
#: `required_fields` ∪ `optional_fields` 内，否则 `validate_payload` 直接抛错。
ARTIFACT_SPECS: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "quote_parsed",
        {
            "carrier": "桂平航 6688",
            "rate": "48000.00",
            "cargo_name": "钢材",
            "quantity": "1200",
            "quantity_unit": "吨",
            "route": "南宁 → 贵港",
        },
    ),
    (
        "procurement_confirm",
        {
            "supplier": "演示承运方（非真实客户）",
            "agreed_scope": "南宁→贵港 1200 吨钢材，含装卸",
            "agreed_amount": "41000.00",
            "currency": "CNY",
        },
    ),
    (
        "customer_quote",
        {
            "amount": "48000.00",
            "currency": "CNY",
            "includes": "运费、装卸费、保险费",
            "valid_until": "2026-10-31",
        },
    ),
    (
        "contract_review",
        {
            "parties": "演示货主 / 演示承运方",
            "clauses": "违约条款、装卸时限、货损赔付",
            "effective_date": "2026-09-20",
        },
    ),
    # 缺必填 `receivable_lines`：**这是有意的**。界面必须把它显示成
    # 「信息缺失（应有但缺失）」而不是「暂无记录」。四态若被压成一句话，
    # 用户就分不清"该去补数据"还是"这一槽本来就没有内容"。
    ("settlement_draft", {"note": "待补应收明细（演示四态用）"}),
)


def _stamp() -> str:
    return utcnow_naive().strftime(_TS)


def _user(db: Session, code: str, nickname: str) -> int:
    """按登录码取用户；不存在则创建。

    openid 的构造必须与后端 `auth.service.code2session` 在 `WECHAT_MOCK` 下的
    行为**逐字一致**（`mock-openid-<code>`）—— 否则小程序用同一个
    `dev_login_code` 登录时会命中另一个用户，种子等于白铺。
    """
    openid = f"mock-openid-{code}"
    row = db.execute(text("SELECT id FROM users WHERE openid = :o"), {"o": openid}).first()
    if row:
        return int(row[0])
    user = auth_service.upsert_user(db, openid, "", nickname, role="shipper")
    db.commit()
    return int(user.id)


def _org(db: Session, name: str) -> int:
    row = db.execute(text("SELECT id FROM ent_organization WHERE name = :n"), {"n": name}).first()
    if row:
        return int(row[0])
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": name, "c": _stamp()},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db: Session, org_id: int, user_id: int, role: str) -> None:
    row = db.execute(
        text("SELECT id FROM ent_org_member WHERE org_id = :o AND user_id = :u"),
        {"o": org_id, "u": user_id},
    ).first()
    if row:
        return
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": _stamp()},
    )
    db.commit()


def _entrust(db: Session, org_id: int, owner_id: int, permissions: list[str]) -> int:
    """生效的委托授权（`active`、无有效期上下限）。

    返回 `entrustment_id` —— 成果必须挂到某一条授权上（`ent_artifact.entrustment_id`
    非空），所以这里不能像 orgpicker 那样只做"存在即返回"。
    """
    row = db.execute(
        text(
            "SELECT id FROM ent_entrustment "
            "WHERE org_id = :o AND entrust_user_id = :u AND status = 'active'"
        ),
        {"o": org_id, "u": owner_id},
    ).first()
    if row:
        return int(row[0])
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " valid_from, valid_until, created_at)"
            " VALUES (:o, :u, :p, 'active', NULL, NULL, :c)"
        ),
        {"o": org_id, "u": owner_id, "p": json.dumps(permissions), "c": _stamp()},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _ensure_assignment(
    db: Session,
    *,
    owner_id: int,
    org_id: int,
    title: str,
    cargo_summary: str,
    quantity: str | None,
    quantity_unit: str | None,
    claim_by: int | None,
) -> tuple[int, str]:
    """确保存在一张（至少已提交的）委托，返回 `(assignment_id, status)`。

    幂等按**状态**推进而不是"行存在就跳过"：上一次中途失败可能留下一张 `draft`，
    若只按标题命中就直接返回，种子会"成功"而队列里什么都没有 —— 那种失败
    最像"页面没反应"，排查成本极高（orgpicker 已经踩过同一坑）。
    """
    row = db.execute(
        text(
            "SELECT id, status, revision FROM ent_assignment "
            "WHERE owner_user_id = :u AND title = :t"
        ),
        {"u": owner_id, "t": title},
    ).first()
    if row is None:
        created = svc.create_assignment(
            db,
            owner_user_id=owner_id,
            title=title,
            cargo_summary=cargo_summary,
            quantity=quantity,
            quantity_unit=quantity_unit,
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
    if claim_by is not None and status == svc.STATUS_SUBMITTED:
        svc.claim_assignment(db, assignment_id=assignment_id, actor_id=claim_by)
        status = "claimed"
    return assignment_id, status


def _ensure_task(
    db: Session,
    *,
    assignment_id: int,
    actor_id: int,
    task_type: str,
    title: str,
    assignee: int | None,
) -> tuple[int, str]:
    """按 `(委托, 类型, 标题)` 复用任务；不存在则走服务层创建。"""
    row = db.execute(
        text(
            "SELECT id, status FROM ent_workflow_task "
            "WHERE assignment_id = :a AND task_type = :t AND title = :ti"
        ),
        {"a": assignment_id, "t": task_type, "ti": title},
    ).first()
    if row:
        return int(row[0]), str(row[1])
    created = tasks_svc.create_task(
        db,
        assignment_id=assignment_id,
        actor_id=actor_id,
        task_type=task_type,
        title=title,
        assignee_user_id=assignee,
    )
    return int(created["task_id"]), str(created["status"])


def _ensure_artifact(
    db: Session,
    *,
    entrustment_id: int,
    artifact_type: str,
    payload: dict[str, object],
    created_by: int,
    assignment_id: int | None,
) -> int:
    """按 `(归属委托, 成果类型)` 复用成果；不存在则走服务层创建（首版即生效）。

    `assignment_id=None` 时按 `IS NULL` 匹配 —— 不能用 `=`，
    否则 NULL 比较恒为假，脚本每次都会再插一条"历史成果"。
    """
    if assignment_id is None:
        row = db.execute(
            text("SELECT id FROM ent_artifact WHERE artifact_type = :t AND assignment_id IS NULL"),
            {"t": artifact_type},
        ).first()
    else:
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
        payload=payload,
        created_by=created_by,
        assignment_id=assignment_id,
    )
    return int(created["artifact_id"])


def main() -> int:
    db = SessionLocal()
    try:
        org_id = _org(db, ORG_NAME)
        shipper = _user(db, SHIPPER_CODE, "演示货主")
        manager = _user(db, MANAGER_CODE, "演示经理")

        # 经理进组织（`manager` 角色 → 认领 / 派单 / 报价 / 结算都在角色权限内）；
        # 货主**不是**组织成员 —— 他对委托的可见性来自"我是创建人"，
        # 而不是成员关系。把他也加进组织会让"货主本人可见"与"组织成员可见"
        # 两条判据重叠，e2e 就分不清是哪一条在生效。
        _member(db, org_id, manager, "manager")
        entrustment_id = _entrust(db, org_id, shipper, ORG_PERMISSIONS)

        main_id, main_status = _ensure_assignment(
            db,
            owner_id=shipper,
            org_id=org_id,
            title=ASSIGNMENT_MAIN,
            cargo_summary="钢材 · 南宁 → 贵港（演示货物，非真实客户材料）",
            quantity="1200.000",
            quantity_unit="吨",
            claim_by=manager,
        )
        open_id, open_status = _ensure_assignment(
            db,
            owner_id=shipper,
            org_id=org_id,
            title=ASSIGNMENT_OPEN,
            cargo_summary="水泥熟料 · 贵港 → 梧州（演示货物，非真实客户材料）",
            quantity="800.000",
            quantity_unit="吨",
            claim_by=None,
        )

        task_rows: list[tuple[str, int, str]] = []
        for task_type, title, assigned in TASK_SPECS:
            task_id, task_status = _ensure_task(
                db,
                assignment_id=main_id,
                actor_id=manager,
                task_type=task_type,
                title=title,
                assignee=manager if assigned else None,
            )
            task_rows.append((task_type, task_id, task_status))

        artifact_rows: list[tuple[str, int]] = []
        for artifact_type, payload in ARTIFACT_SPECS:
            artifact_id = _ensure_artifact(
                db,
                entrustment_id=entrustment_id,
                artifact_type=artifact_type,
                payload=payload,
                created_by=manager,
                assignment_id=main_id,
            )
            artifact_rows.append((artifact_type, artifact_id))

        # 历史存量：归属机制上线前产生的成果没有 `assignment_id`，
        # 它不属于任何槽位，但必须在界面上如实报数（DR-0012）。
        orphan_id = _ensure_artifact(
            db,
            entrustment_id=entrustment_id,
            artifact_type="quote_parsed",
            payload={"carrier": "历史承运方（演示）", "rate": "39000.00"},
            created_by=manager,
            assignment_id=None,
        )

        print("委托工作台演示数据就绪：")
        print(f"  组织：{ORG_NAME} = id {org_id}")
        print(f"  授权：entrustment_id={entrustment_id}（{len(ORG_PERMISSIONS)} 项权限）")
        print(f"  {SHIPPER_CODE} user_id={shipper} 货主（非组织成员，靠创建人身份可见）")
        print(f"  {MANAGER_CODE} user_id={manager} 组织经理（队列视角）")
        print(f"  委托 #{main_id} 「{ASSIGNMENT_MAIN}」 → {main_status}")
        print(f"      任务 {len(task_rows)} 项：" + ", ".join(f"{t}#{i}" for t, i, _ in task_rows))
        print(
            "      成果 "
            + str(len(artifact_rows))
            + " 项："
            + ", ".join(f"{t}#{i}" for t, i in artifact_rows)
        )
        print("      （settlement_draft 刻意缺必填 receivable_lines → 期望「信息缺失」）")
        print(f"  委托 #{open_id} 「{ASSIGNMENT_OPEN}」 → {open_status}")
        print("      → 详情页应给出「受理委托」入口")
        print(f"  无归属历史成果 #{orphan_id} → unassigned_artifact_total 应 >= 1")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
