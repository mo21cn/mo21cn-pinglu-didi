"""把演示数据的归属账号切换为真实登录账号（一次性运维动作）。

为什么是「改 openid」而不是「迁数据」
----------------------------------
所有 `ent_*` 数据（组织 / 委托 / 任务 / 成果 / 案件 / 合同）都通过 **`user_id`** 关联。
把**演示账号**的 `openid` 改成用户的**真实 openid**，用户登录即命中全部演示数据，
**不需要逐表迁移**。

对照：现成的 `bind_demo_to_openid.py` 只覆盖 `cargo` / `ships` / `orders` 四个字段，
对 `ent_*` 无效 —— 所以委托支线的数据它搬不动。

⛔ 为什么**没有** `auto` 模式（2026-09-23 评审后移除）
--------------------------------------------------
原 `--mode auto` 取「最近创建的非演示账号」。这条规则**无法证明那台账号就是操作者
自己的微信号** —— 库里可能有多台真实账号（换过微信号、多个开发者、走查脚本注入过
身份），"最近"只是时间顺序。绑错的后果是把演示数据挂到一个不属于你的账号上。

⇒ 现在的流程刻意是两步：**先 `list` 读候选，再由人显式指定** openid 或 user_id。
容器内无法交互确认，所以"显式"必须体现在命令行参数上，不能靠"自动挑一个"。

安全设计
--------
- **默认不动数据库**：必须显式给 `--mode`，没有隐式行为；
- **体检目标账号**：目标账号若已有业务数据（任意表里以它为归属的行），默认**拒绝**绑定
  （加 `--force` 可强行）—— 因为过继后那些行会挂在一个被归档的账号下；
- **体检演示账号状态**：演示账号必须还原样存在（`openid = mock-openid-seed-shipper`），
  否则说明之前已经绑过 / 种子没铺，一律拒绝；
- 归档用户的空账号时**保留可追溯的映射**（改名为 `archived-<id>`，不是删除）；
- **打印可直接执行的回滚 SQL**（顺序经过唯一性约束校验，见 `rollback_sql`）；
- 全程一个事务，异常自动回滚。

⚠️ 治理提醒：这是**演示环境的一次性准备动作**，不是上线路径。
线上用户的身份与权限应走**正常准入 + 组织授权**（`ent_org_member` / `ent_entrustment`），
**不应长期依赖"改 openid 把种子数据分给某个人"**。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从任意目录执行（脚本不在包内）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402

#: 委托支线演示数据的归属账号（货主，也是 `ent_assignment.owner_user_id` 的来源）
DEMO_OPENID = "mock-openid-seed-shipper"

#: 演示账号的 openid 前缀
DEMO_PREFIX = "mock-openid-"

#: 归档账号的 openid 前缀（`archived-<原 user_id>`）
ARCHIVED_PREFIX = "archived-"

#: 视作「用户归属列」的额外列名（不以 `user_id` 结尾的那几个）
USER_COLUMN_EXTRA = frozenset({"created_by", "claimed_by"})


def _rows(db, sql: str, params: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
    return db.execute(text(sql), params or {}).fetchall()


def _quote(db, name: str) -> str:  # type: ignore[no-untyped-def]
    """按方言引用标识符（MySQL 用反引号、SQLite 用双引号）。

    ⛔ 不能写死双引号：云端是 MySQL，`SELECT ... FROM "t"` 只在 ANSI_QUOTES 模式下才合法。
    """
    return db.get_bind().dialect.identifier_preparer.quote(name)


def user_attribution_columns(db) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    """枚举「哪张表的哪一列指向 users.id」。

    刻意**动态探测**而不是写死表清单：新增一张带 `*_user_id` 的表会自动被体检覆盖，
    不会因为"忘了往清单里加"而漏查。
    """
    out: list[tuple[str, str]] = []
    insp = inspect(db.get_bind())
    for table in sorted(insp.get_table_names()):
        for col in insp.get_columns(table):
            name = str(col["name"])
            if name.endswith("user_id") or name in USER_COLUMN_EXTRA:
                out.append((table, name))
    return out


def business_data_counts(db, user_id: int) -> tuple[list[tuple[str, str, int]], list[str]]:  # type: ignore[no-untyped-def]
    """统计以 `user_id` 为归属的行数。

    Returns:
        (非零统计, 查询失败的说明)；失败不抛异常 —— 体检是"尽量看清"，不是断言。
    """
    hits: list[tuple[str, str, int]] = []
    errors: list[str] = []
    for table, col in user_attribution_columns(db):
        sql = f"SELECT COUNT(*) FROM {_quote(db, table)} WHERE {_quote(db, col)} = :u"
        try:
            n = db.execute(text(sql), {"u": user_id}).scalar() or 0
        except Exception as exc:  # noqa: BLE001 — 缺表/方言差异都不该中断体检
            errors.append(f"{table}.{col}: {type(exc).__name__}")
            continue
        if n:
            hits.append((table, col, int(n)))
    hits.sort(key=lambda x: -x[2])
    return hits, errors


def find_user(db, *, openid: str | None = None, user_id: int | None = None):  # type: ignore[no-untyped-def]
    if openid is not None:
        rows = _rows(
            db,
            "SELECT id, openid, nickname, created_at FROM users WHERE openid = :o",
            {"o": openid},
        )
    elif user_id is not None:
        rows = _rows(
            db, "SELECT id, openid, nickname, created_at FROM users WHERE id = :i", {"i": user_id}
        )
    else:
        return None
    return rows[0] if rows else None


def demo_state(db) -> str:  # type: ignore[no-untyped-def]
    """演示账号当前状态。

    - ``seeded``：演示账号原样存在（`openid == DEMO_OPENID`）⇒ 可绑定；
    - ``bound`` ：演示账号不在，但**委托数据存在** ⇒ 一定已经过继过（或被人改过）；
    - ``fresh`` ：演示账号不在，且委托数据为空 ⇒ 从未铺种子；
    - ``unknown``：连"委托数据是否存在"都查不出来（缺表 / 库不可达）。
    """
    if find_user(db, openid=DEMO_OPENID):
        return "seeded"
    try:
        n = db.execute(text(f"SELECT COUNT(*) FROM {_quote(db, 'ent_organization')}")).scalar() or 0
    except Exception:  # noqa: BLE001
        return "unknown"
    return "bound" if n else "fresh"


def rollback_sql(demo_id: int, demo_openid: str, target_id: int, target_openid: str) -> list[str]:
    """生成**可直接按序执行**的回滚 SQL。

    顺序是关键：`users.openid` 有唯一约束，必须**先释放**真实 openid，再还原目标账号。
    反过来写（先给目标账号设回真实 openid，而演示账号此时还占着它）会撞唯一性冲突。
    """
    return [
        f"UPDATE users SET openid='{demo_openid}' WHERE id={demo_id};",
        f"UPDATE users SET openid='{target_openid}' WHERE id={target_id};",
    ]


def cmd_list(db) -> int:  # type: ignore[no-untyped-def]
    """打印候选，不改任何数据（绑定前的第一步）。"""
    print(f"[list] 演示账号状态 = {demo_state(db)}")
    d = find_user(db, openid=DEMO_OPENID)
    if d:
        print(f"[list] 演示账号（委托数据归属）id={d[0]}  openid={d[1]}  nickname={d[2]!r}")
    else:
        print(f"[list] !! 未找到 {DEMO_OPENID}")

    print("[list] ── 非演示账号（真实登录产生的）──")
    rows = _rows(
        db,
        "SELECT id, openid, nickname, current_role, created_at FROM users "
        "WHERE openid NOT LIKE :p ORDER BY id DESC LIMIT 10",
        {"p": DEMO_PREFIX + "%"},
    )
    if not rows:
        print("[list]   （无）⇒ 请先在小程序里完成一次登录，再来绑定")
    for r in rows:
        hits, _errs = business_data_counts(db, int(r[0]))
        summary = "、".join(f"{t}.{c}×{n}" for t, c, n in hits[:4]) or "无业务数据"
        print(f"[list]   id={r[0]}  openid={r[1]}  nickname={r[2]!r}  role={r[3]}  created={r[4]}")
        print(f"[list]       业务数据：{summary}{' …' if len(hits) > 4 else ''}")

    print("[list] ⛔ 没有 auto 模式：请核对上面每一台的 openid 后**显式**指定")
    print("[list]    例：--mode bind --openid <你确认的那条>")
    return 0


def cmd_bind(db, *, openid: str | None, user_id: int | None, force: bool) -> int:  # type: ignore[no-untyped-def]
    target = find_user(db, openid=openid, user_id=user_id)
    if not target:
        which = f"openid={openid}" if openid else f"user_id={user_id}"
        print(f"[bind] !! 目标账号不存在：{which}")
        return 1
    target_id, target_openid = int(target[0]), str(target[1])

    if target_openid.startswith(DEMO_PREFIX):
        print(f"[bind] ⛔ 目标是演示账号（{target_openid}）⇒ 无需绑定")
        return 1
    if target_openid.startswith(ARCHIVED_PREFIX):
        print(f"[bind] ⛔ 目标是已归档账号（{target_openid}）⇒ 这不是可用的登录身份")
        return 1

    state = demo_state(db)
    if state != "seeded":
        print(f"[bind] ⛔ 演示账号不可绑定：状态={state}")
        print(
            "[bind]    seeded=种子已铺且演示账号原样；bound=已过继过；fresh=从未铺种子；unknown=查不出来"
        )
        print("[bind]    请先跑 --mode list 看清现状，必要时先重新铺种子（SEED_ON_BOOT）")
        return 1

    hits, errs = business_data_counts(db, target_id)
    if errs:
        print(f"[bind] ⚠️ 部分表体检失败（{len(errs)}）：{errs[:3]}")
    if hits and not force:
        print(f"[bind] ⛔ 目标账号 id={target_id} **已有业务数据**，拒绝绑定：")
        for t, c, n in hits:
            print(f"[bind]      {t}.{c} = {n}")
        print("[bind]    过继后这些行会挂在被归档的账号下（数据不会丢，但归属会变成别人）")
        print("[bind]    确认可接受再加 --force")
        return 1
    if hits:
        print(f"[bind] ⚠️ --force：目标账号已有 {len(hits)} 处业务数据，继续（见上一条 list 输出）")

    demo = find_user(db, openid=DEMO_OPENID)
    assert demo is not None  # demo_state == "seeded" 已保证
    demo_id = int(demo[0])
    archived = f"{ARCHIVED_PREFIX}{target_id}"

    print(f"[bind] 目标：id={target_id} openid={target_openid}")
    print(f"[bind] 演示：id={demo_id} openid={DEMO_OPENID}（委托数据在这里）")
    print(f"[bind] 1/2 归档目标账号 id={target_id} → openid={archived}")
    db.execute(text("UPDATE users SET openid = :n WHERE id = :i"), {"n": archived, "i": target_id})
    print(f"[bind] 2/2 演示账号 id={demo_id} 接管 openid={target_openid}")
    db.execute(
        text("UPDATE users SET openid = :o WHERE id = :i"), {"o": target_openid, "i": demo_id}
    )
    db.commit()

    print("[bind] 完成 ✓")
    print("[bind] 回滚（按此顺序执行，唯一约束不会冲突）：")
    for sql in rollback_sql(demo_id, DEMO_OPENID, target_id, target_openid):
        print(f"[bind]   {sql}")
    print("[bind] 注意：回滚后请把云侧 `BIND_DEMO_IDENTITY` 清空，并重新登录小程序")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把委托演示数据的归属切到真实登录账号（一次性运维）")
    ap.add_argument("--mode", required=True, help="list | bind")
    ap.add_argument("--openid", default=None, help="bind 的目标 openid（与 --user-id 二选一）")
    ap.add_argument(
        "--user-id", dest="user_id", type=int, default=None, help="bind 的目标 users.id"
    )
    ap.add_argument("--force", action="store_true", help="目标账号已有业务数据时仍继续（需先看清）")
    args = ap.parse_args()

    mode = (args.mode or "").strip()
    if mode in {"auto", "true", "1", "yes"}:
        print("[bind] ⛔ auto 模式已移除：它取「最近创建的非演示账号」，")
        print(
            "[bind]    无法证明那就是你的微信号 ⇒ 请先 --mode list，再显式指定 --openid/--user-id"
        )
        return 2
    if mode == "bind" and not (args.openid or args.user_id):
        print("[bind] ⛔ bind 需要显式目标：--openid <o> 或 --user-id <n>")
        return 2

    db = SessionLocal()
    try:
        if mode == "list":
            return cmd_list(db)
        if mode == "bind":
            return cmd_bind(db, openid=args.openid, user_id=args.user_id, force=args.force)
        print(f"[bind] !! 未知模式：{mode}（可用：list / bind）")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
