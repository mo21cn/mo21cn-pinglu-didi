"""给演示账号补**组织成员资格**（一次性运维动作）。

为什么需要
----------
云端演示**只有一个微信号**可用（一个 openid ⇒ 一个 `users` 行），而种子把"货主"与
"组织经理"拆成**两个**账号（`mock-openid-seed-shipper` / `mock-openid-seed-owner`）。
`bind_demo_identity.py` 的过继只能把 openid 给其中一个 ⇒ **另一个视角必然为空**：

以货主身份进「委托发货 · 经理工作台」时，`workbench.js` 先 `fetchMyOrgs()`，
`pickOrg([])` 返回 `reason='none'` ⇒ 页面停在 **「还没有加入服务经营主体」**
并**提前 return，连队列都不查**（这也是"服务端日志里看不到请求"的原因）。

本脚本把演示账号**同时**放进演示组织（`ent_org_member`，角色默认 `manager`），
于是一个微信号即可走完整条链：**发布委托 → 组织受理/认领 → 任务与成果 → 合同/结算**。

**不迁任何业务数据**：`ent_*` 全部按 `user_id` / `org_id` 关联，成员资格只是**一行**。

角色选择
--------
`manager`（`access.py::ORG_ROLE_PERMISSIONS`）= `entrust:view` ＋ 认领 / 结案 / 受控重开 /
报价制作与发布 / 派单 / 结算生成 / 发起 Agent 作业 —— 正好覆盖演示需要的动作面，
但**不含**成员管理与授权管理（那两项属 `owner`/`admin`，演示不需要）。

安全设计
--------
- **默认不动库**：必须显式给 `--mode`；先 `list` 看现状再 `grant`；
- **幂等**：已是 `active` 成员 ⇒ 不重复插入，只打印现状（容器每次冷启动都会跑一次）；
- **打印可执行回滚 SQL**（`DELETE` 该成员行），并回读确认；
- ⛔ 仅面向**演示/开发**环境；生产应走正常准入（组织邀请 / 审批），不是改表。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402

#: 演示组织（由 `seed_entrust_demo.py` 创建）
DEMO_ORG_ID = 1

#: 演示默认角色（见模块 docstring 的角色选择说明）
DEMO_MEMBER_ROLE = "manager"

#: 允许的角色（与 `access.ORG_ROLE_PERMISSIONS` 的键一致）
VALID_ROLES = ("owner", "admin", "manager", "member")

ARCHIVED_PREFIX = "archived-"


def _rows(db, sql: str, params: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
    return db.execute(text(sql), params or {}).fetchall()


def find_user(db, *, openid: str | None = None, user_id: int | None = None):  # type: ignore[no-untyped-def]
    if openid is not None:
        rows = _rows(
            db, "SELECT id, openid, nickname, status FROM users WHERE openid = :o", {"o": openid}
        )
    elif user_id is not None:
        rows = _rows(
            db, "SELECT id, openid, nickname, status FROM users WHERE id = :i", {"i": user_id}
        )
    else:
        return None
    return rows[0] if rows else None


def cmd_list(db) -> int:  # type: ignore[no-untyped-def]
    """只读打印：组织、成员、以及委托授权现状（grant 之前先看这个）。"""
    print("[grant] ── 组织 ──")
    orgs = _rows(db, "SELECT id, name, status FROM ent_organization ORDER BY id")
    if not orgs:
        print("[grant]   （无）⇒ 种子还没铺？先跑 boot_seed（SEED_ON_BOOT）")
    for r in orgs:
        print(f"[grant]   id={r[0]}  name={r[1]!r}  status={r[2]}")

    print("[grant] ── 组织成员 ──")
    members = _rows(
        db,
        "SELECT m.org_id, m.user_id, m.member_role, m.status, u.openid "
        "FROM ent_org_member m LEFT JOIN users u ON u.id = m.user_id ORDER BY m.org_id, m.user_id",
    )
    if not members:
        print("[grant]   （无）⇒ 这正是受理台为空的原因")
    for r in members:
        print(f"[grant]   org={r[0]} user={r[1]} role={r[2]!r} status={r[3]} openid={r[4]}")

    print("[grant] ── 委托授权（谁把委托授权给了哪个组织）──")
    ents = _rows(
        db,
        "SELECT id, org_id, entrust_user_id, status FROM ent_entrustment ORDER BY id",
    )
    for r in ents:
        print(f"[grant]   entrustment={r[0]} org={r[1]} 委托方 user={r[2]} status={r[3]}")
    print("[grant] 用法：--mode grant --openid <演示账号的 openid> [--org-id 1] [--role manager]")
    return 0


def cmd_grant(  # type: ignore[no-untyped-def]
    db,
    *,
    openid: str | None,
    user_id: int | None,
    org_id: int,
    role: str,
) -> int:
    if role not in VALID_ROLES:
        print(f"[grant] !! 角色非法：{role}（可用：{'/'.join(VALID_ROLES)}）")
        return 2

    user = find_user(db, openid=openid, user_id=user_id)
    if not user:
        which = f"openid={openid}" if openid else f"user_id={user_id}"
        print(f"[grant] !! 账号不存在：{which}")
        return 1
    uid, uopenid, unick, ustatus = int(user[0]), str(user[1]), str(user[2] or ""), str(user[3])
    if ustatus != "active":
        print(f"[grant] !! 账号状态不是 active（{ustatus}）⇒ 拒绝")
        return 1
    if uopenid.startswith(ARCHIVED_PREFIX):
        print(f"[grant] ⛔ 目标是被归档的账号（{uopenid}）⇒ 这不是可用的演示身份")
        return 1

    org = _rows(db, "SELECT id, name, status FROM ent_organization WHERE id = :i", {"i": org_id})
    if not org:
        print(f"[grant] !! 组织不存在：id={org_id}（先跑 --mode list 看清现状）")
        return 1
    print(f"[grant] 目标账号 id={uid} openid={uopenid} nickname={unick!r}")
    print(f"[grant] 目标组织 id={org_id} name={org[0][1]!r}")

    existing = _rows(
        db,
        "SELECT member_role, status FROM ent_org_member WHERE org_id = :o AND user_id = :u",
        {"o": org_id, "u": uid},
    )
    if existing and str(existing[0][1]) == "active":
        print(
            f"[grant] ⇒ 已是该组织的 active 成员（role={existing[0][0]!r}），无需重复插入（幂等）"
        )
        return 0

    if existing:
        # 有历史行但非 active（suspended/removed）：就地复活，避免留下重复行
        print(
            f"[grant] 复用既有成员行：status={existing[0][1]!r} → active，role={existing[0][0]!r} → {role!r}"
        )
        db.execute(
            text(
                "UPDATE ent_org_member SET status = 'active', member_role = :r "
                "WHERE org_id = :o AND user_id = :u"
            ),
            {"r": role, "o": org_id, "u": uid},
        )
    else:
        db.execute(
            text(
                "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
                "VALUES (:o, :u, :r, 'active', :ts)"
            ),
            {"o": org_id, "u": uid, "r": role, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        )
    db.commit()

    # 回读确认（⛔ 别只信"已提交"）
    check = _rows(
        db,
        "SELECT member_role, status FROM ent_org_member WHERE org_id = :o AND user_id = :u",
        {"o": org_id, "u": uid},
    )
    if not check or str(check[0][1]) != "active":
        print(f"[grant] !! 回读失败：{check}")
        return 1
    print(f"[grant] 完成 ✓ 回读：role={check[0][0]!r} status={check[0][1]!r}")
    print("[grant] 期望效果：`GET /api/v1/entrust/my-orgs` 出现该组织；")
    print("[grant]           `GET /api/v1/entrust/assignments?view=org` 出现委托队列；")
    print("[grant]           小程序「委托发货 · 经理工作台」不再停在「还没有加入服务经营主体」")
    print("[grant] 回滚（可执行）：")
    if existing:
        print(
            f"[grant]   UPDATE ent_org_member SET status='{existing[0][1]}', "
            f"member_role='{existing[0][0]}' WHERE org_id={org_id} AND user_id={uid};"
        )
    else:
        print(f"[grant]   DELETE FROM ent_org_member WHERE org_id={org_id} AND user_id={uid};")
    print("[grant] 注意：回滚后请把云侧 `DEMO_GRANT_ORG_MEMBER` 清空，避免每次冷启动重跑")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="给演示账号补组织成员资格（一次性运维）")
    ap.add_argument("--mode", required=True, help="list | grant")
    ap.add_argument("--openid", default=None, help="grant 的目标 openid（与 --user-id 二选一）")
    ap.add_argument(
        "--user-id", dest="user_id", type=int, default=None, help="grant 的目标 users.id"
    )
    ap.add_argument("--org-id", dest="org_id", type=int, default=DEMO_ORG_ID, help="目标组织 id")
    ap.add_argument("--role", default=DEMO_MEMBER_ROLE, help=f"成员角色（{'/'.join(VALID_ROLES)}）")
    args = ap.parse_args()

    mode = (args.mode or "").strip()
    if mode in {"auto", "true", "1", "yes"}:
        print("[grant] ⛔ 不接受 auto/true：必须**显式**指定 --openid 或 --user-id")
        print(
            "[grant]    （与 bind_demo_identity 同一纪律：自动挑「最近创建的账号」无法证明它是谁）"
        )
        return 2
    if mode == "grant" and not (args.openid or args.user_id):
        print("[grant] ⛔ grant 需要显式目标：--openid <o> 或 --user-id <n>")
        return 2

    db = SessionLocal()
    try:
        if mode == "list":
            return cmd_list(db)
        if mode == "grant":
            return cmd_grant(
                db,
                openid=args.openid,
                user_id=args.user_id,
                org_id=args.org_id,
                role=args.role,
            )
        print(f"[grant] !! 未知模式：{mode}（可用：list / grant）")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
