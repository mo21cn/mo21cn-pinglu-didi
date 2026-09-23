"""把演示数据的归属账号切换为真实登录账号（一次性运维动作）。

为什么是「改 openid」而不是「迁数据」
----------------------------------
所有 `ent_*` 数据（组织 / 委托 / 任务 / 成果 / 案件 / 合同）都通过 **`user_id`** 关联。
把**演示账号**的 `openid` 改成用户的**真实 openid**，用户登录即命中全部演示数据，
**不需要逐表迁移**。

对照：现成的 `bind_demo_to_openid.py` 只覆盖 `cargo` / `ships` / `orders` 四个字段，
对 `ent_*` 无效 —— 所以委托支线的数据它搬不动。

模式
----
    --mode list     只列出候选账号，**不落库**（建议先看再决定）
    --mode auto     自动绑定到「最近创建的非演示账号」
    --mode <openid> 显式绑定到指定 openid

安全设计
--------
- **默认不动数据库**：必须显式给 `--mode`，没有隐式行为；
- 归档用户的空账号时**保留可追溯的映射**（改名为 `archived-<id>`，不是删除）；
- 全程一个事务，异常自动回滚；
- ⛔ 面向演示/开发环境，别在真实生产数据上跑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从任意目录执行（脚本不在包内）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402

#: 委托支线演示数据的归属账号（货主，也是 `ent_assignment.owner_user_id` 的来源）
DEMO_OPENID = "mock-openid-seed-shipper"

#: 演示账号的 openid 前缀（用来把"真实登录账号"筛出来）
DEMO_PREFIX = "mock-openid-"


def _rows(db, sql: str, params: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
    return db.execute(text(sql), params or {}).fetchall()


def list_candidates(db) -> int:  # type: ignore[no-untyped-def]
    """打印候选，不改任何数据。"""
    print("── 非演示账号（真实登录产生的）──")
    rows = _rows(
        db,
        "SELECT id, openid, nickname, current_role, created_at FROM users "
        "WHERE openid NOT LIKE :p ORDER BY id DESC LIMIT 10",
        {"p": DEMO_PREFIX + "%"},
    )
    if not rows:
        print("  （无）⇒ 请先在小程序里完成一次登录，再来绑定")
    for r in rows:
        print(f"  id={r[0]}  openid={r[1]}  nickname={r[2]!r}  role={r[3]}  created={r[4]}")

    print("── 演示账号（委托数据的归属）──")
    d = _rows(db, "SELECT id, openid, nickname FROM users WHERE openid = :o", {"o": DEMO_OPENID})
    if d:
        print(f"  id={d[0][0]}  openid={d[0][1]}  nickname={d[0][2]!r}")
    else:
        print(f"  !! 未找到 {DEMO_OPENID} —— 种子还没铺？")
    return 0


def bind(db, openid: str) -> int:  # type: ignore[no-untyped-def]
    """把 `openid` 过继给演示账号（原账号归档）。"""
    target = _rows(db, "SELECT id, openid FROM users WHERE openid = :o", {"o": openid})
    if not target:
        print(f"!! 目标 openid 不存在：{openid}")
        return 1
    target_id = int(target[0][0])

    demo = _rows(db, "SELECT id FROM users WHERE openid = :o", {"o": DEMO_OPENID})
    if not demo:
        print(f"!! 演示账号不存在：{DEMO_OPENID}（种子没铺？）")
        return 1
    demo_id = int(demo[0][0])

    if target_id == demo_id:
        print("⇒ 该 openid 已经在演示账号上，无需绑定")
        return 0

    archived = f"archived-{target_id}"
    print(f"[bind] 1/2 归档空账号 id={target_id} → openid={archived}")
    db.execute(
        text("UPDATE users SET openid = :n WHERE id = :i"), {"n": archived, "i": target_id}
    )
    print(f"[bind] 2/2 演示账号 id={demo_id} 接管 openid={openid}")
    db.execute(text("UPDATE users SET openid = :o WHERE id = :i"), {"o": openid, "i": demo_id})
    db.commit()

    print("[bind] 完成 ✓")
    print(f"[bind] 回滚：UPDATE users SET openid='{archived}' WHERE id={demo_id};")
    print(f"[bind]       UPDATE users SET openid='{openid}' WHERE id={target_id};")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把委托演示数据的归属切到真实登录账号")
    ap.add_argument("--mode", required=True, help="list | auto | <openid>")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        mode = args.mode.strip()
        if mode == "list":
            return list_candidates(db)
        if mode == "auto":
            rows = _rows(
                db,
                "SELECT openid FROM users WHERE openid NOT LIKE :p ORDER BY id DESC LIMIT 1",
                {"p": DEMO_PREFIX + "%"},
            )
            if not rows:
                print("!! 无可用的非演示账号 ⇒ 请先在小程序里登录一次")
                return 1
            print(f"[bind] auto 选中 openid={rows[0][0]}")
            return bind(db, str(rows[0][0]))
        return bind(db, mode)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
