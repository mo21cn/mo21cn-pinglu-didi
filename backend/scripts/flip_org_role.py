"""把一个演示身份在某个组织里的成员角色改成另一个角色（走查用的**撤权 / 复权**通道）。

用法（backend 目录下）：

    python scripts/flip_org_role.py --code seed-mgr-only-b --org 演示经营主体·乙 --role member
    python scripts/flip_org_role.py --code seed-mgr-only-b --org 演示经营主体·乙 --role manager

`--role` 只接受 `manager` / `member`（理由见 `ROLES` 的说明）。

为什么需要它
------------
`ent_organization` / `ent_org_member` / `ent_entrustment` **没有 HTTP 接口**（权限叠加层的
数据只能由组织管理动作写入，见 `seed_entrust_orgpicker.py` 的同段说明），而 D-4 裁定的
第四条边界要求证明「受理入口**已经展示之后**，权限被撤销 ⇒ 服务端拒绝 + 界面刷新」。
要造这个场景必须在**运行中**改成员角色 —— 只能落库，故单独一个脚本，与 `seed_*.py`
同一族（都是本机走查 / 联调的数据工具，不是产品代码）。

⚠️ 它**有副作用**，用完必须还原
------------------------------
`seed_entrust_orgpicker.py` 的 `_member()` 是「有则跳过」，**不会**把角色改回来。
所以：角色不改回去，下一次走查的 ㊱ 章就会红（那一章的**对照组**明确要求
`seed-mgr-only-b` 在**乙组织**是 `manager`）。调用方必须把本脚本包在 `try / finally` 里
—— 走查脚本 `verify_miniapp_devtools.py` 的 ㊲ 章就是这么用的。

退出码：`0` = 改成功；`2` = 找不到用户或成员关系（前置没铺种子）。
"""

from __future__ import annotations

import argparse
import os
import sys

# 以 `python scripts/xxx.py`（cwd=backend）执行时，`app` 不在 sys.path 上 —— 与其它脚本同法。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402

#: 只允许写这两个角色：它们正好是 `ORG_ROLE_PERMISSIONS` 里"有认领权限 / 无认领权限"的分界
#: （`manager` 含 `entrust:assignment:claim`，`member` 只有 `entrust:view`）。
#: 不接受任意字符串 —— 拼错一个不存在的角色会**静默变成无权限**，那是最难查的那类失败。
ROLES = ("manager", "member")


def _user_id(db, code: str) -> int | None:
    """按登录码取用户 id。openid 构造必须与 `auth.service.code2session` 在 mock 下逐字一致。"""
    row = db.execute(
        text("SELECT id FROM users WHERE openid = :o"), {"o": f"mock-openid-{code}"}
    ).first()
    return int(row[0]) if row else None


def _org_id(db, name: str) -> int | None:
    row = db.execute(text("SELECT id FROM ent_organization WHERE name = :n"), {"n": name}).first()
    return int(row[0]) if row else None


def main() -> int:
    ap = argparse.ArgumentParser(description="改演示身份在某组织的成员角色（走查撤权 / 复权通道）")
    ap.add_argument("--code", required=True, help="登录码（dev_login_code），如 seed-mgr-multi")
    ap.add_argument("--org", required=True, help="组织名（种子里的常量），如 演示经营主体·甲")
    ap.add_argument("--role", required=True, choices=ROLES, help="目标角色")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        user_id = _user_id(db, args.code)
        if user_id is None:
            print(f"找不到用户：{args.code}（openid=mock-openid-{args.code}）", file=sys.stderr)
            return 2
        org_id = _org_id(db, args.org)
        if org_id is None:
            print(f"找不到组织：{args.org}", file=sys.stderr)
            return 2
        row = db.execute(
            text("SELECT id, member_role FROM ent_org_member WHERE org_id = :o AND user_id = :u"),
            {"o": org_id, "u": user_id},
        ).first()
        if row is None:
            print(
                f"找不到成员关系：{args.code}(user_id={user_id}) @ {args.org}(org_id={org_id})"
                " —— 先跑 seed_entrust_orgpicker.py",
                file=sys.stderr,
            )
            return 2
        before = str(row[1])
        if before == args.role:
            # 幂等：已经是目标角色就什么都不做（走查会反复重跑）。**仍然打印**，
            # 免得调用方把"本来就没变"读成"改失败"。
            print(f"无需改动：user_id={user_id} @ {args.org} 已是 {before}")
            return 0
        db.execute(
            text("UPDATE ent_org_member SET member_role = :r WHERE id = :i"),
            {"r": args.role, "i": int(row[0])},
        )
        db.commit()
        print(f"已改：user_id={user_id} @ {args.org}(org_id={org_id})：{before} -> {args.role}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
