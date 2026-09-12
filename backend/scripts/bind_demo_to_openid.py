"""把演示数据绑定到真实微信 openid（接入真实 AppID 后的收尾步骤）。

背景
----
`seed_demo.py` 铺出的演示数据（货源 / 船舶 / 订单 / 支付 / 泊位预约）全部挂在
`mock-openid-seed-*` 三个演示账号名下。接入真实 AppID 后，手机走真实 wx.login，
拿到的是**真实 openid** —— 在库里是一个全新空账号，于是「我的货源 / 我的订单」
全空、看起来和功能故障一模一样。

本脚本把演示资产整体迁移到该真实账号，并补上三角色权限，
使真机既能验证真实登录链路，又能看到完整演示数据。

用法（backend 目录下，后端未启动时也可执行）
--------------------------------------------
    python scripts/bind_demo_to_openid.py                    # 自动取最近创建的非演示账号
    python scripts/bind_demo_to_openid.py --openid oXXXXXX   # 显式指定
    python scripts/bind_demo_to_openid.py --dry-run          # 只预览、不落库

流程
----
1) 先在开发者工具或手机上完成一次真实登录（这会创建真实账号）；
2) 执行本脚本；
3) 在小程序里下拉刷新 / 重进页面即可看到演示数据。

注意
----
- 迁移后 orders.shipper_id 与 owner_id 会指向同一账号（同一微信号既当货主又当船东），
  这是为了让单一账号能看全两个视角；撮合时可能推荐到自己的船，属预期行为。
- 本脚本面向本机开发库。生产环境不要执行。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从任意目录执行：脚本不在包内，需把 backend 根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import bindparam, text  # noqa: E402

from app.core.database import engine  # noqa: E402

# 演示账号（seed_demo.py 使用的固定 code → mock openid）
DEMO_OPENIDS = (
    "mock-openid-seed-shipper",
    "mock-openid-seed-owner",
    "mock-openid-seed-port",
)

# 需要把归属关系从演示账号迁到目标账号的「表.列」
FK_COLUMNS = (
    ("cargo", "shipper_id"),
    ("ships", "owner_id"),
    ("orders", "shipper_id"),
    ("orders", "owner_id"),
    ("payments", "payer_id"),
    ("payments", "payee_id"),
    ("berth_appts", "applier_id"),
)

TARGET_ROLES = '["shipper", "owner", "port"]'


def resolve_demo_ids(conn) -> list[int]:
    """取三个演示账号的 id；一个都没有说明还没铺演示数据。"""
    rows = conn.execute(
        text("select id, openid from users where openid in :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": list(DEMO_OPENIDS)},
    ).all()
    return [r[0] for r in rows]


def resolve_target(conn, explicit: str | None) -> tuple[int, str]:
    """确定目标账号：显式传入优先，否则取最近创建的非演示账号。"""
    if explicit:
        row = conn.execute(
            text("select id, openid from users where openid = :o"), {"o": explicit}
        ).first()
        if row is None:
            raise SystemExit(
                f"× 库里找不到 openid = {explicit} 的账号。\n"
                "  请先在开发者工具或手机上用真实微信登录一次，再执行本脚本。"
            )
        return row[0], row[1]

    row = conn.execute(
        text(
            "select id, openid, nickname from users "
            "where openid not like 'mock-openid-seed-%' "
            "order by id desc limit 1"
        )
    ).first()
    if row is None:
        raise SystemExit(
            "× 没有找到非演示账号。\n"
            "  请先在开发者工具或手机上用真实微信登录一次，再执行本脚本。"
        )
    print(f"  自动选中最近创建的账号：id={row[0]} openid={row[1]} nickname={row[2] or '(空)'}")
    print("  （若不对，用 --openid <真实openid> 显式指定）")
    return row[0], row[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="把演示数据绑定到真实微信 openid")
    parser.add_argument("--openid", help="目标真实 openid（默认取最近创建的非演示账号）")
    parser.add_argument("--dry-run", action="store_true", help="只预览将要迁移的记录数，不落库")
    args = parser.parse_args()

    print("=" * 68)
    print("滴滴打船 · 把演示数据绑定到真实 openid")
    print("=" * 68)

    with engine.begin() as conn:
        demo_ids = resolve_demo_ids(conn)
        if not demo_ids:
            raise SystemExit(
                "\n× 库里没有演示账号 —— 请先运行 python scripts/seed_demo.py 铺演示数据。"
            )

        target_id, target_openid = resolve_target(conn, args.openid)
        row = conn.execute(
            text("select roles, current_role from users where id = :i"), {"i": target_id}
        ).first()
        print(f"\n目标账号：id={target_id} openid={target_openid}")
        print(f"  现有角色：{row[0]} / 当前 {row[1]}")
        print(f"演示账号：{', '.join(f'#{i}' for i in demo_ids)}")

        if target_id in demo_ids:
            print("\n目标账号本身就是演示账号，无需迁移。")
            return

        print("\n将要迁移的记录数：")
        total = 0
        for table, col in FK_COLUMNS:
            n = conn.execute(
                text(f"select count(*) from {table} where {col} in :ids").bindparams(  # noqa: S608
                    bindparam("ids", expanding=True)
                ),
                {"ids": demo_ids},
            ).scalar_one()
            total += n
            print(f"  {table}.{col}: {n}")
        print(f"  合计：{total}")

        if args.dry_run:
            print("\n[dry-run] 未做任何改动。去掉 --dry-run 即真正执行。")
            return

        for table, col in FK_COLUMNS:
            conn.execute(
                text(f"update {table} set {col} = :t where {col} in :ids").bindparams(  # noqa: S608
                    bindparam("ids", expanding=True)
                ),
                {"t": target_id, "ids": demo_ids},
            )

        conn.execute(
            text(
                "update users set roles = :r, current_role = 'shipper' where id = :t"
            ),
            {"r": TARGET_ROLES, "t": target_id},
        )

    print(f"\n完成：{total} 条记录已归属到账号 #{target_id}，角色 = 货主 + 船东 + 港口方。")
    print("现在重新进入小程序（或下拉刷新）即可看到演示数据。")


if __name__ == "__main__":
    main()
