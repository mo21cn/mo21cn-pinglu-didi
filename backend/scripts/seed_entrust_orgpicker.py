"""组织选择器走查的演示数据（可重复执行 · 幂等）。

用法（backend 目录下，需先启动后端）：

    python scripts/seed_entrust_orgpicker.py

为什么单独一份种子
------------------
`seed_demo.py` 只铺既有的自主发货 / 船东 / 港口演示数据，**完全不碰 `ent_*` 表**：
委托支线的组织成员与委托授权没有任何种子。而「组织选择器」只有在**多组织身份**
下才可见 —— 手工点几下造不出来，所以单独一份，并且必须**幂等**（走查会反复重跑）。

造出的三种身份，正好覆盖前端 `pickOrg` 的三条分支
--------------------------------------------------
| 登录码 | 组织身份 | pickOrg 分支 | 界面应有表现 |
|---|---|---|---|
| `seed-mgr-multi` | A 组织经理 + B 组织成员 | `ambiguous` | 顶栏出现选择器，且**不预选** |
| `seed-mgr-single` | 仅 A 组织经理 | `only` | 顶栏**不出现**选择器 |
| `seed-mgr-only-b` | 仅 B 组织经理 | `only` | 顶栏不出现选择器，且队列里**只有 B 的委托** |
| `seed-mgr-none` | 无任何组织身份 | `none` | 提示「还没有加入经营主体」 |

⚠️ **为什么必须有 `seed-mgr-only-b`**（2026-09-16 补，S1 出口判据 ④）：
`GET /assignments/{id}` 的可见性判据是「**该委托的组织 ∈ 调用者的任一组织**」
（`ctx.org_ids`，不区分"当前在看哪个组织"）。所以 `seed-mgr-multi`
（A+B 双身份）**无论如何都看得到 A 的委托** —— 拿它验
「unrelated organization B 不可见」会**验不出来**，得到的会是假绿。
要证这条必须有一个**不属于 A** 的组织身份，即本行的 `seed-mgr-only-b`。
它与 `seed-mgr-none` 的区别：后者是"没有组织"，验不出"组织 B 的成员看不到组织 A 的委托"
这个**跨组织**结论。

另有一个货主身份 `seed-shipper-orgpicker`：给 A、B 两个组织**各留一张标题不同的
已提交委托**。这是「切换组织后队列真的变了」变得**可断言**的前提 —— 否则两个组织
都显示同样的（空）列表，选择器看起来"点了没反应"，走查会得出错误结论。

实现在哪一层
------------
`ent_organization` / `ent_org_member` / `ent_entrustment` **没有 HTTP 接口**
（它们是权限叠加层的数据，只能由组织管理动作写入），故这部分直接落库；
委托单仍走**真实服务层**（`assignments.create_assignment` + `submit_assignment`），
这样种子产生的委托与线上走的是同一套状态机与校验，不是手工拼出来的假数据。

演示用途：组织名与委托标题一律带「演示」前缀，便于在共享库里辨认与清理。
"""

from __future__ import annotations

import json
import os
import sys

# 以 `python scripts/xxx.py`（cwd=backend）执行时，`app` 不在 sys.path 上，
# 需显式补上 backend/ —— 这也是本文件唯一需要 sys.path 操作的原因。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.auth import service as auth_service  # noqa: E402
from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"

# 组织：刻意让 A、B 两个组织的**名称不同**，这样界面上"选中了哪个"一眼可辨，
# 走查截图也能直接当证据。
ORG_A = "演示经营主体·甲"
ORG_B = "演示经营主体·乙"

MULTI_CODE = "seed-mgr-multi"
SINGLE_CODE = "seed-mgr-single"
NONE_CODE = "seed-mgr-none"
#: 仅 B 组织经理。存在的唯一理由是出口判据 ④ 需要**一个不属于 A 的组织身份**
#: —— `seed-mgr-multi` 是 A+B 双身份，看不到"跨组织不可见"这条（见模块 docstring）。
ONLY_B_CODE = "seed-mgr-only-b"
SHIPPER_CODE = "seed-shipper-orgpicker"

# 同一货主在两个组织下的委托，**标题必须不同**（见模块 docstring 的说明）。
ASSIGNMENT_A = "演示委托·甲组织队列样本"
ASSIGNMENT_B = "演示委托·乙组织队列样本"


def _stamp() -> str:
    return utcnow_naive().strftime(_TS)


def _user(db: Session, code: str, nickname: str) -> int:
    """按登录码取用户；不存在则创建。

    openid 的构造必须与后端 `auth.service.code2session` 在 WECHAT_MOCK 下
    的行为**逐字一致**（`mock-openid-<code>`）—— 否则小程序用同一个
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


def _entrust(db: Session, org_id: int, owner_id: int, permissions: list[str]) -> None:
    """生效的委托授权（`active` 且无有效期上限）。提交委托时必须要它。"""
    row = db.execute(
        text(
            "SELECT id FROM ent_entrustment "
            "WHERE org_id = :o AND entrust_user_id = :u AND status = 'active'"
        ),
        {"o": org_id, "u": owner_id},
    ).first()
    if row:
        return
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " valid_from, valid_until, created_at)"
            " VALUES (:o, :u, :p, 'active', NULL, NULL, :c)"
        ),
        {"o": org_id, "u": owner_id, "p": json.dumps(permissions), "c": _stamp()},
    )
    db.commit()


def _submitted_assignment(db: Session, owner_id: int, org_id: int, title: str) -> int:
    """确保「该货主在该组织下有一张已提交的委托」，返回 assignment_id。

    幂等按**状态**判定而不是「行存在与否」：上一次中途失败可能留下一张 `draft`，
    若只按标题命中就直接返回，种子会"成功"但队列里什么都没有 —— 那种失败
    最像"选择器点了没反应"，排查成本很高。
    """
    row = db.execute(
        text(
            "SELECT id, status, revision FROM ent_assignment"
            " WHERE owner_user_id = :u AND title = :t"
        ),
        {"u": owner_id, "t": title},
    ).first()
    if row is not None:
        assignment_id = int(row[0])
        if str(row[1]) == svc.STATUS_SUBMITTED:
            return assignment_id
        svc.submit_assignment(
            db,
            assignment_id=assignment_id,
            actor_id=owner_id,
            org_id=org_id,
            expected_revision=int(row[2]),
        )
        return assignment_id

    created = svc.create_assignment(
        db,
        owner_user_id=owner_id,
        title=title,
        cargo_summary="演示货物（走查用，非真实客户材料）",
        quantity="120.000",
        quantity_unit="吨",
    )
    assignment_id = int(created["assignment_id"])
    svc.submit_assignment(
        db,
        assignment_id=assignment_id,
        actor_id=owner_id,
        org_id=org_id,
        expected_revision=int(created["revision"]),
    )
    return assignment_id


def main() -> int:
    db = SessionLocal()
    try:
        org_a = _org(db, ORG_A)
        org_b = _org(db, ORG_B)

        multi = _user(db, MULTI_CODE, "演示经理·多组织")
        single = _user(db, SINGLE_CODE, "演示经理·单组织")
        none = _user(db, NONE_CODE, "演示经理·无组织")
        only_b = _user(db, ONLY_B_CODE, "演示经理·仅乙组织")
        shipper = _user(db, SHIPPER_CODE, "演示货主·组织选择器")

        # multi：A 组织是**经理**、B 组织只是**成员** —— 这不是随意的取值，
        # 它同时是 DR-0008 的回归样本（跨组织权限不得并集）。
        _member(db, org_a, multi, "manager")
        _member(db, org_b, multi, "member")
        _member(db, org_a, single, "manager")
        # only_b：**只在 B**，是出口判据 ④（unrelated organization B 不可见）的样本 ——
        # 它既不属于 A，队列与详情都必须看不到 A 的委托。
        _member(db, org_b, only_b, "manager")
        # none 刻意不加任何成员关系。

        _entrust(db, org_a, shipper, ["entrust:view", "entrust:assignment:claim"])
        _entrust(db, org_b, shipper, ["entrust:view"])

        a_id = _submitted_assignment(db, shipper, org_a, ASSIGNMENT_A)
        b_id = _submitted_assignment(db, shipper, org_b, ASSIGNMENT_B)

        print("组织选择器演示数据就绪：")
        print(f"  组织：{ORG_A} = id {org_a} / {ORG_B} = id {org_b}")
        print(f"  {MULTI_CODE}  user_id={multi}  A=manager / B=member")
        print("      → 期望 pickOrg 分支 ambiguous（顶栏出现选择器且不预选）")
        print(f"  {SINGLE_CODE}  user_id={single}  A=manager")
        print("      → 期望 pickOrg 分支 only（顶栏不出现选择器）")
        print(f"  {ONLY_B_CODE}  user_id={only_b}  B=manager（**不属于 A**）")
        print("      → 出口判据 ④：队列里只有乙组织委托，且甲组织的委托详情 404")
        print(f"  {NONE_CODE}  user_id={none}  无组织")
        print("      → 期望 pickOrg 分支 none（提示还没有加入经营主体）")
        print(f"  {SHIPPER_CODE}  user_id={shipper}  A/B 各有授权")
        print(f"  委托：甲组织 id={a_id} / 乙组织 id={b_id}")
        print("      两张标题不同 —— 切换组织后队列条目应随之变化")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
