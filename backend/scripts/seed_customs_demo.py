"""智慧关务 D1 的演示数据（可重复执行 · 幂等）。

用法（backend 目录下）：

    python scripts/seed_customs_demo.py

为什么必须有这份种子
--------------------
D1 交付的是「人工登记 → 状态时间线」这一条**只读**链路：页面上能看到的每一行，
都必须先有一条**真实登记过的**记录。没有种子的话，关务首页永远是空态 ——
空态本身是诚实的，但它证明不了这个切片做成了，演示时也没东西可看。

本种子只造**两条**记录，就够演示三件事：

| 单号 | 事件序列 | 演示什么 |
|---|---|---|
| `PL-CUS-DEMO-0001` | 已申报 → 查验中 | 时间线**有多条**、以及状态会往前走 |
| `PL-CUS-DEMO-0002` | 已放行 | 终态（`released`）在界面上是「已完成」而不是「进行中」 |

⭐ 两个时间**故意拉开**：`occurred_at` 定在几天前，`fetched_at` 是**服务端写入的当前时刻**。
这不是凑数 —— D1 详情页的核心断言就是「发生时间」与「取得时间」分两列显示；
两者相等的时候，"页面把它们合并成一列"这个缺陷**看不出来**（判据必须能失败）。

纪律
----
* 一律走**真实服务层**（`declarations.create_declaration` / `append_event`），
  不手写 INSERT —— 这样种子造出来的记录与线上走的是同一套状态机与校验；
* 单号、来源都带「演示」字样，便于在共享库里辨认与清理；
* **幂等**：重复执行只会复用已有记录，不会因为"单号已登记"（409）而炸；
* `source` 一律写成人话（"报关行回执"），因为 D1 的界面要**照原样**显示来源 ——
  写个 `demo` 上去，界面就会显示一个没人看得懂的东西。
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

# 以 `python scripts/xxx.py`（cwd=backend）执行时，`app` 不在 sys.path 上。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.modules.auth import service as auth_service  # noqa: E402
from app.modules.customs import declarations as svc  # noqa: E402

#: 允许铺演示种子的环境（**白名单**，不是黑名单）。
#:
#: 为什么必须挡：本脚本会写入 `PL-CUS-DEMO-*` 记录。在**生产库**上跑一次，
#: 那些记录就成了真实客户库里的假数据 —— 既是数据事故，也直接违反 Q-06
#: （"演示数据全部虚构、可公开、可录屏"只对演示库成立）。
#: 先前没有这道守卫，而 `DEMO-0-演示台词.md` §7 的检查单又要求"重跑一次" ——
#: 只要有人把生产库的 `DATABASE_URL` 配进 `.env.production` 再照单执行，就会中招。
DEMO_ENVS = frozenset({"development", "dev", "local", "test"})

#: 本机地址：给"看着不像生产库"一个可读的提示，但**不作判据**
#: （开发库可以在内网另一台机器上，用地址做判据会误伤正常联调）。
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _assert_demo_env() -> None:
    """拒绝在非演示环境执行。⛔ 不提供 `--force`：有开关就等于没有守卫。"""
    settings = get_settings()
    env = str(getattr(settings, "APP_ENV", "") or "").strip().lower()
    if env == "production":
        raise SystemExit(
            "拒绝执行：APP_ENV=production。\n"
            "本脚本会写入 PL-CUS-DEMO-* 演示记录，只允许在演示档后端执行（Q-06 守卫）。\n"
            "演示环境请用 APP_ENV=development（本机）或在演示库上单独执行；\n"
            "⛔ 生产库不做例外，也不接受任何绕过参数。"
        )
    if env not in DEMO_ENVS:
        raise SystemExit(
            f"拒绝执行：APP_ENV={env!r} 不在演示环境白名单 {sorted(DEMO_ENVS)} 内（Q-06 守卫）。"
        )
    url = str(getattr(settings, "DATABASE_URL", "") or "")
    if url and not any(h in url for h in _LOOPBACK_HOSTS):
        # 只提示、不拒绝：开发库在内网别处是合法的
        print(f"⚠️ 目标库不是本机地址（{url.split('@')[-1]}）；请确认这是演示库而不是生产库。")


#: 演示账号。与 `seed_entrust_demo.py` 用的是同一个货主身份 ——
#: 走查 ⑮ 章登录的就是它，两边不一致会导致"种子里有、页面上没有"。
SHIPPER_CODE = "seed-shipper"

NO_MAIN = "PL-CUS-DEMO-0001"
NO_RELEASED = "PL-CUS-DEMO-0002"


def _stamp(delta: timedelta | None = None):
    base = svc.utcnow_naive()
    return base + (delta or timedelta(0))


def _user_id(db: Session, code: str, nickname: str) -> int:
    """按登录码取用户；不存在则创建。

    `openid` 必须与后端在 `WECHAT_MOCK` 下的构造**逐字一致**（`mock-openid-<code>`），
    否则小程序用同一个 `dev_login_code` 登录时会命中另一个用户，种子等于白铺。
    """
    openid = f"mock-openid-{code}"
    row = db.execute(text("SELECT id FROM users WHERE openid = :o"), {"o": openid}).first()
    if row:
        return int(row[0])
    user = auth_service.upsert_user(db, openid, "", nickname, role="shipper")
    db.commit()
    return int(user.id)


def _exists(db: Session, user_id: int, no: str) -> int:
    row = db.execute(
        text(
            "SELECT id FROM cus_declaration "
            "WHERE declaration_no = :no AND owner_user_id = :uid AND org_id IS NULL"
        ),
        {"no": no, "uid": user_id},
    ).first()
    return int(row[0]) if row else 0


def _ensure(
    db: Session, user_id: int, no: str, status: str, source: str, occurred: object, note: str
) -> int:
    """登记一条记录；已存在则直接返回它的 id（幂等）。"""
    found = _exists(db, user_id, no)
    if found:
        return found
    body = svc.create_declaration(
        db,
        user_id=user_id,
        org_ids=frozenset(),
        declaration_no=no,
        status=status,
        source=source,
        kind="export",
        occurred_at=occurred,  # type: ignore[arg-type]
        note=note,
        org_id=None,
        assignment_id=None,
    )
    return int(body["declaration"]["id"])


def main() -> int:
    _assert_demo_env()
    db = SessionLocal()
    try:
        user_id = _user_id(db, SHIPPER_CODE, "演示货主")

        main_id = _ensure(
            db,
            user_id,
            NO_MAIN,
            "declared",
            "报关行回执（演示）",
            _stamp(timedelta(days=-3)),
            "演示：首条登记（已申报）",
        )
        released_id = _ensure(
            db,
            user_id,
            NO_RELEASED,
            "released",
            "报关行回执（演示）",
            _stamp(timedelta(days=-5)),
            "演示：一次登记到放行的记录",
        )

        # 给主线再追加一条"查验中"。只追加、不改历史（AC-06）；
        # `fetched_at` 由服务端写当前时刻 ⇒ 与 `occurred_at`（两天前）自然拉开，
        # 详情页「发生 / 取得」两列的差异是**造出来的**，不是碰巧。
        events = db.execute(
            text("SELECT status FROM cus_status_event WHERE object_type = :t AND object_id = :oid"),
            {"t": svc.OBJECT_TYPE, "oid": main_id},
        ).fetchall()
        seen = {str(r[0]) for r in events}
        if "inspection" not in seen:
            svc.append_event(
                db,
                user_id=user_id,
                org_ids=frozenset(),
                declaration_id=main_id,
                status="inspection",
                source="报关行电话告知（演示）",
                occurred_at=_stamp(timedelta(days=-2)),  # type: ignore[arg-type]
                note="演示：第二条状态（查验中），用于验证时间线有多条",
                corrects_event_id=None,
            )

        rows = db.execute(
            text(
                "SELECT d.id, d.declaration_no, d.status, "
                "(SELECT COUNT(*) FROM cus_status_event e "
                "  WHERE e.object_type = :t AND e.object_id = d.id) AS n "
                "FROM cus_declaration d WHERE d.owner_user_id = :uid ORDER BY d.id"
            ),
            {"uid": user_id, "t": svc.OBJECT_TYPE},
        ).fetchall()
        print(f"关务演示数据就绪（{SHIPPER_CODE} user_id={user_id}）：")
        for r in rows:
            print(f"  #{r[0]} {r[1]} → {r[2]}（事件 {r[3]} 条）")
        print(f"  主线 {NO_MAIN} 的事件：已申报（3 天前）→ 查验中（2 天前）")
        print(f"  另一条 {NO_RELEASED}：已放行（5 天前）= 终态")
        print(f"  released 记录 id={released_id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
