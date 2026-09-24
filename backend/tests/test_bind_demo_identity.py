"""`scripts/bind_demo_identity.py` ＋ `boot_seed` 重复铺种子防护的用例。

针对 2026-09-23 评审指出的四处风险各留一条可失败断言：

1. **`auto` 模式必须被拒** —— 它取「最近创建的非演示账号」，
   无法证明那就是操作者本人的微信号（换过微信号 / 多个开发者 / 走查注入过身份都会踩）；
2. **绑定前必须体检目标账号是否已有业务数据** —— 否则那些行会挂到被归档的账号下；
3. **打印的回滚 SQL 必须可执行** —— `users.openid` 有唯一约束，
   顺序错了会撞冲突（原实现正是错的）；
4. **过继之后重开 `SEED_ON_BOOT` 不得铺出第二套样本** —— 种子按 openid 找不到演示账号
   会新建一个，`user_id` 与既有 `ent_*` 数据不同 ⇒ 判定为"没铺过" ⇒ 重复铺。
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import boot_seed
from app.models import Base
from app.modules.auth import service as auth_service
from scripts.bind_demo_identity import (
    DEMO_OPENID,
    business_data_counts,
    cmd_bind,
    demo_state,
    main,
    rollback_sql,
    user_attribution_columns,
)


@pytest.fixture(autouse=True)
def _clean_boot_env(monkeypatch):
    """清掉启动期开关，避免本机环境变量污染用例（一次性动作现已扩到两项）。"""
    for name in ("SEED_ON_BOOT", "BIND_DEMO_IDENTITY", "DEMO_GRANT_ORG_MEMBER"):
        monkeypatch.delenv(name, raising=False)


def _make_session(*, with_migrations: bool = True):  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    if with_migrations:
        from migrate import apply_pending

        apply_pending(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


@pytest.fixture()
def db():  # type: ignore[no-untyped-def]
    session = _make_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def db_without_migrations():  # type: ignore[no-untyped-def]
    """没有应用迁移的库：`ent_*` 表不存在（用来验证 "unknown" 分支）。"""
    session = _make_session(with_migrations=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded(db):  # type: ignore[no-untyped-def]
    """铺好演示账号 + 一条委托归属数据（`ent_org_member`）。"""
    demo_id = auth_service.upsert_user(db, DEMO_OPENID, "", "演示货主", role="shipper").id
    db.execute(
        text(
            "INSERT INTO ent_organization (name, status, created_at) VALUES ('演示组织', 'active', '2026-09-23 00:00:00')"
        )
    )
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (1, :u, 'manager', 'active', '2026-09-23 00:00:00')"
        ),
        {"u": demo_id},
    )
    db.commit()
    return db


# ── 状态判定 ────────────────────────────────────────────────────────────────


def test_demo_state_fresh_when_never_seeded(db):  # type: ignore[no-untyped-def]
    """演示账号不在 + 委托数据为空 ⇒ fresh（可以铺种子）。"""
    assert demo_state(db) == "fresh"
    assert boot_seed._seed_skip_reason("fresh") is None


def test_demo_state_seeded_when_demo_account_intact(seeded):  # type: ignore[no-untyped-def]
    assert demo_state(seeded) == "seeded"
    assert boot_seed._seed_skip_reason("seeded") is None


def test_demo_state_bound_when_demo_account_gone_but_data_remains(seeded):  # type: ignore[no-untyped-def]
    """过继之后：演示账号的 openid 被换走，但委托数据仍在 ⇒ bound。"""
    seeded.execute(
        text("UPDATE users SET openid = :o WHERE openid = :d"),
        {"o": "archived-1", "d": DEMO_OPENID},
    )
    seeded.commit()
    assert demo_state(seeded) == "bound"


def test_demo_state_unknown_when_schema_missing(db_without_migrations):  # type: ignore[no-untyped-def]
    """连"委托数据是否存在"都查不出来 ⇒ unknown（调用方必须跳过铺种子）。"""
    assert demo_state(db_without_migrations) == "unknown"


# ── 体检 ────────────────────────────────────────────────────────────────────


def test_user_attribution_columns_covers_ent_and_platform_tables(db):  # type: ignore[no-untyped-def]
    cols = set(user_attribution_columns(db))
    assert ("ent_org_member", "user_id") in cols
    assert ("ent_assignment", "owner_user_id") in cols
    assert ("ent_workflow_task", "assignee_user_id") in cols
    assert ("ent_entrustment", "entrust_user_id") in cols


def test_business_data_counts_finds_owned_rows(seeded):  # type: ignore[no-untyped-def]
    demo_id = int(
        seeded.execute(text("SELECT id FROM users WHERE openid = :o"), {"o": DEMO_OPENID}).scalar()
    )
    hits, errors = business_data_counts(seeded, demo_id)
    assert errors == []
    assert ("ent_org_member", "user_id", 1) in hits


def test_business_data_counts_empty_for_clean_account(seeded):  # type: ignore[no-untyped-def]
    uid = auth_service.upsert_user(
        seeded, "oxREALopenid000000000000000000", "", "真号", role="shipper"
    ).id
    seeded.commit()
    hits, _errors = business_data_counts(seeded, uid)
    assert hits == []


# ── 绑定：拒绝路径 ──────────────────────────────────────────────────────────


def test_bind_rejects_missing_target(seeded, capsys):  # type: ignore[no-untyped-def]
    assert cmd_bind(seeded, openid="oxNOTexist0000000000000000000", user_id=None, force=False) == 1
    assert "目标账号不存在" in capsys.readouterr().out


def test_bind_refuses_demo_account_as_target(seeded, capsys):  # type: ignore[no-untyped-def]
    assert cmd_bind(seeded, openid=DEMO_OPENID, user_id=None, force=False) == 1
    assert "无需绑定" in capsys.readouterr().out


def test_bind_refuses_archived_target(seeded, capsys):  # type: ignore[no-untyped-def]
    auth_service.upsert_user(seeded, "archived-9", "", "旧号", role="shipper")
    seeded.commit()
    assert cmd_bind(seeded, openid="archived-9", user_id=None, force=False) == 1
    assert "已归档账号" in capsys.readouterr().out


def test_bind_refuses_when_already_bound(seeded, capsys):  # type: ignore[no-untyped-def]
    """演示账号不在（已过继过）⇒ 拒绝再次绑定，避免把数据来回搬。"""
    real = auth_service.upsert_user(
        seeded, "oxREALopenid000000000000000000", "", "真号", role="shipper"
    )
    seeded.commit()
    seeded.execute(
        text("UPDATE users SET openid = :o WHERE openid = :d"),
        {"o": "archived-1", "d": DEMO_OPENID},
    )
    seeded.commit()
    rc = cmd_bind(seeded, openid="oxREALopenid000000000000000000", user_id=real.id, force=False)
    assert rc == 1
    assert "状态=bound" in capsys.readouterr().out


def test_bind_refuses_target_with_business_data(seeded, capsys):  # type: ignore[no-untyped-def]
    """目标账号已有业务数据时必须拒绝 —— 否则过继后那些行会挂到被归档的账号下。"""
    real = auth_service.upsert_user(
        seeded, "oxREALopenid000000000000000000", "", "真号", role="shipper"
    )
    seeded.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (1, :u, 'manager', 'active', '2026-09-23 00:00:00')"
        ),
        {"u": real.id},
    )
    seeded.commit()
    assert cmd_bind(seeded, openid=None, user_id=real.id, force=False) == 1
    out = capsys.readouterr().out
    assert "已有业务数据" in out
    assert "ent_org_member.user_id = 1" in out


def test_bind_force_overrides_business_data_check(seeded, capsys):  # type: ignore[no-untyped-def]
    real = auth_service.upsert_user(
        seeded, "oxREALopenid000000000000000000", "", "真号", role="shipper"
    )
    seeded.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (1, :u, 'manager', 'active', '2026-09-23 00:00:00')"
        ),
        {"u": real.id},
    )
    seeded.commit()
    assert cmd_bind(seeded, openid=None, user_id=real.id, force=True) == 0
    assert "--force" in capsys.readouterr().out


# ── 绑定：成功路径与可执行回滚 ──────────────────────────────────────────────


def test_bind_success_swaps_openids_and_rollback_is_executable(seeded, capsys):  # type: ignore[no-untyped-def]
    real_openid = "oxREALopenid000000000000000000"
    real = auth_service.upsert_user(seeded, real_openid, "", "真号", role="shipper")
    seeded.commit()
    demo_id = int(
        seeded.execute(text("SELECT id FROM users WHERE openid = :o"), {"o": DEMO_OPENID}).scalar()
    )

    assert cmd_bind(seeded, openid=real_openid, user_id=None, force=False) == 0
    out = capsys.readouterr().out

    # 过继完成：真实 openid 落在演示账号（＝委托数据的归属）上
    assert (
        seeded.execute(text("SELECT id FROM users WHERE openid = :o"), {"o": real_openid}).scalar()
        == demo_id
    )
    assert (
        seeded.execute(text("SELECT openid FROM users WHERE id = :i"), {"i": real.id}).scalar()
        == f"archived-{real.id}"
    )

    # 回滚 SQL：**先释放真实 openid，再还原目标**（反过来必撞唯一性冲突）
    stmts = rollback_sql(demo_id, DEMO_OPENID, real.id, real_openid)
    assert stmts[0].endswith(f"WHERE id={demo_id};") and DEMO_OPENID in stmts[0]
    assert stmts[1].endswith(f"WHERE id={real.id};") and real_openid in stmts[1]
    for sql in stmts:
        assert sql in out

    # 真的按这个顺序执行一遍，不应抛异常，且状态复原
    for sql in stmts:
        seeded.execute(text(sql))
    seeded.commit()
    assert (
        seeded.execute(text("SELECT openid FROM users WHERE id = :i"), {"i": demo_id}).scalar()
        == DEMO_OPENID
    )
    assert (
        seeded.execute(text("SELECT openid FROM users WHERE id = :i"), {"i": real.id}).scalar()
        == real_openid
    )
    assert demo_state(seeded) == "seeded"


# ── CLI ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["auto", "true", "1", "yes"])
def test_cli_rejects_auto_like_modes(monkeypatch, capsys, bad):  # type: ignore[no-untyped-def]
    """`auto` / `true` 必须被拒 —— 它们无法证明目标就是操作者的微信号。"""
    monkeypatch.setattr(sys, "argv", ["bind_demo_identity.py", "--mode", bad])
    assert main() == 2
    assert "auto 模式已移除" in capsys.readouterr().out


def test_cli_bind_requires_explicit_target(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sys, "argv", ["bind_demo_identity.py", "--mode", "bind"])
    assert main() == 2
    assert "需要显式目标" in capsys.readouterr().out


# ── boot_seed：重复铺种子防护 ───────────────────────────────────────────────


def test_seed_skip_reason_bound_is_explained():
    reason = boot_seed._seed_skip_reason("bound")
    assert reason is not None
    assert "避免把同一批样本再铺一遍" in reason


def test_seed_skip_reason_unknown_refuses_to_seed():
    reason = boot_seed._seed_skip_reason("unknown")
    assert reason is not None
    assert "宁可少铺" in reason


def test_boot_seed_skips_seeding_when_bound(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """过继之后重开 SEED_ON_BOOT → 必须跳过，且一个种子脚本都不许跑。"""
    monkeypatch.setenv("SEED_ON_BOOT", "true")
    monkeypatch.setattr(boot_seed, "_wait_backend", lambda *a, **k: True)
    monkeypatch.setattr(boot_seed, "_demo_state", lambda: "bound")
    called: list[str] = []
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: called.append(name) or True
    )

    assert boot_seed.main() == 0
    out = capsys.readouterr().out
    assert called == []
    assert "演示账号状态 = bound" in out
    assert "跳过铺种子" in out


def test_boot_seed_seeds_when_fresh(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SEED_ON_BOOT", "true")
    monkeypatch.setattr(boot_seed, "_wait_backend", lambda *a, **k: True)
    monkeypatch.setattr(boot_seed, "_demo_state", lambda: "fresh")
    called: list[str] = []
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: called.append(name) or True
    )
    monkeypatch.delenv("BIND_DEMO_IDENTITY", raising=False)

    assert boot_seed.main() == 0
    assert called == [name for name, _ in boot_seed.SEED_STEPS]


def test_boot_seed_rejects_auto_bind_target(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """`BIND_DEMO_IDENTITY=auto` 必须被拒（且明确告知怎么改）。"""
    monkeypatch.setenv("SEED_ON_BOOT", "true")
    monkeypatch.setenv("BIND_DEMO_IDENTITY", "auto")
    monkeypatch.setattr(boot_seed, "_wait_backend", lambda *a, **k: True)
    monkeypatch.setattr(boot_seed, "_demo_state", lambda: "seeded")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        boot_seed,
        "_run_script",
        lambda name, extra=(), **k: calls.append((name, tuple(extra))) or True,
    )

    assert boot_seed.main() == 0
    out = capsys.readouterr().out
    assert "不接受" in out
    assert "BIND_DEMO_IDENTITY=<openid>" in out
    assert all(name != "bind_demo_identity.py" for name, _ in calls)


def test_boot_seed_passes_explicit_bind_target(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SEED_ON_BOOT", "true")
    monkeypatch.setenv("BIND_DEMO_IDENTITY", "user:7")
    monkeypatch.setattr(boot_seed, "_wait_backend", lambda *a, **k: True)
    monkeypatch.setattr(boot_seed, "_demo_state", lambda: "seeded")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        boot_seed,
        "_run_script",
        lambda name, extra=(), **k: calls.append((name, tuple(extra))) or True,
    )

    assert boot_seed.main() == 0
    assert ("bind_demo_identity.py", ("--mode", "bind", "--user-id", "7")) in calls
