"""`scripts/grant_demo_membership.py` ＋ 启动期「一次性动作与种子解耦」的用例。

覆盖两条关键纪律：

1. **一次性动作显式化**：`DEMO_GRANT_ORG_MEMBER` 只接受 `<openid>` 或 `user:<id>`，
   `auto`/`true` 一律拒绝（无法证明那条账号就是操作者本人）；
2. **一次性动作独立于播种**：过继之后播种守卫会跳过铺种子，若把一次性动作写在守卫
   之后并由守卫早退，它会被一起吞掉 —— 2026-09-24 实测的设计陷阱，必须有用例钉住。
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
from scripts.grant_demo_membership import cmd_grant, cmd_list, main

ORG_ID = 1
OPENID = "oxDEMOopenid00000000000000000"


def _make_session():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    from migrate import apply_pending

    apply_pending(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


@pytest.fixture(autouse=True)
def _clean_boot_env(monkeypatch):
    """清掉启动期开关，避免本机环境变量污染用例。"""
    for name in ("SEED_ON_BOOT", "BIND_DEMO_IDENTITY", "DEMO_GRANT_ORG_MEMBER"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db():  # type: ignore[no-untyped-def]
    session = _make_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded(db):  # type: ignore[no-untyped-def]
    """组织 1 + 一个演示账号（**不含**成员行，用来验 grant）。"""
    db.execute(
        text(
            "INSERT INTO ent_organization (name, status, created_at) "
            "VALUES ('演示经营主体·工作台', 'active', '2026-09-24 00:00:00')"
        )
    )
    user = auth_service.upsert_user(db, OPENID, "", "演示货主", role="shipper")
    db.commit()
    return db, int(user.id)


def _members(db):  # type: ignore[no-untyped-def]
    return db.execute(
        text("SELECT org_id, user_id, member_role, status FROM ent_org_member ORDER BY id")
    ).fetchall()


# ── 只读模式 ────────────────────────────────────────────────────────────────


def test_list_is_read_only(seeded, capsys):  # type: ignore[no-untyped-def]
    db, uid = seeded
    assert cmd_list(db) == 0
    out = capsys.readouterr().out
    assert "演示经营主体·工作台" in out
    assert "（无）⇒ 这正是受理台为空的原因" in out
    assert _members(db) == []
    assert uid > 0


# ── 授权路径 ────────────────────────────────────────────────────────────────


def test_grant_inserts_manager_and_prints_rollback(seeded, capsys):  # type: ignore[no-untyped-def]
    db, uid = seeded
    assert cmd_grant(db, openid=OPENID, user_id=None, org_id=ORG_ID, role="manager") == 0
    out = capsys.readouterr().out

    rows = _members(db)
    assert len(rows) == 1
    assert (int(rows[0][0]), int(rows[0][1]), str(rows[0][2]), str(rows[0][3])) == (
        ORG_ID,
        uid,
        "manager",
        "active",
    )
    assert f"DELETE FROM ent_org_member WHERE org_id={ORG_ID} AND user_id={uid};" in out
    assert "完成 ✓" in out
    # 期望效果必须写出来，否则运维不知道去验什么
    assert "/api/v1/entrust/my-orgs" in out


def test_grant_is_idempotent(seeded, capsys):  # type: ignore[no-untyped-def]
    db, uid = seeded
    assert cmd_grant(db, openid=None, user_id=uid, org_id=ORG_ID, role="manager") == 0
    capsys.readouterr()
    assert cmd_grant(db, openid=None, user_id=uid, org_id=ORG_ID, role="manager") == 0
    out = capsys.readouterr().out
    assert "无需重复插入（幂等）" in out
    assert len(_members(db)) == 1


def test_grant_reuses_inactive_row(seeded, capsys):  # type: ignore[no-untyped-def]
    """有历史行但非 active ⇒ 就地复活，不产生重复行。"""
    db, uid = seeded
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'member', 'suspended', '2026-09-24 00:00:00')"
        ),
        {"o": ORG_ID, "u": uid},
    )
    db.commit()
    assert cmd_grant(db, openid=None, user_id=uid, org_id=ORG_ID, role="manager") == 0
    out = capsys.readouterr().out
    assert "复用既有成员行" in out
    rows = _members(db)
    assert len(rows) == 1
    assert str(rows[0][2]) == "manager" and str(rows[0][3]) == "active"
    assert "UPDATE ent_org_member SET status='suspended'" in out


def test_grant_rollback_is_executable(seeded, capsys):  # type: ignore[no-untyped-def]
    db, uid = seeded
    assert cmd_grant(db, openid=None, user_id=uid, org_id=ORG_ID, role="manager") == 0
    out = capsys.readouterr().out
    # 打印行带 `[grant]   ` 前缀，取子串时从 SQL 本身开始
    stmt = next(
        line[line.index("DELETE FROM") :].strip()
        for line in out.splitlines()
        if "DELETE FROM" in line
    )
    db.execute(text(stmt))
    db.commit()
    assert _members(db) == []


# ── 拒绝路径 ────────────────────────────────────────────────────────────────


def test_grant_refuses_unknown_user(seeded, capsys):  # type: ignore[no-untyped-def]
    db, _uid = seeded
    assert (
        cmd_grant(
            db, openid="oxNOSUCH000000000000000000000", user_id=None, org_id=ORG_ID, role="manager"
        )
        == 1
    )
    assert "账号不存在" in capsys.readouterr().out


def test_grant_refuses_missing_org(seeded, capsys):  # type: ignore[no-untyped-def]
    db, uid = seeded
    assert cmd_grant(db, openid=None, user_id=uid, org_id=999, role="manager") == 1
    assert "组织不存在" in capsys.readouterr().out
    assert _members(db) == []


def test_grant_refuses_archived_user(seeded, capsys):  # type: ignore[no-untyped-def]
    db, _uid = seeded
    auth_service.upsert_user(db, "archived-1", "", "旧号", role="shipper")
    db.commit()
    assert cmd_grant(db, openid="archived-1", user_id=None, org_id=ORG_ID, role="manager") == 1
    assert "被归档的账号" in capsys.readouterr().out


def test_grant_rejects_invalid_role(seeded, capsys):  # type: ignore[no-untyped-def]
    db, uid = seeded
    assert cmd_grant(db, openid=None, user_id=uid, org_id=ORG_ID, role="superuser") == 2
    assert "角色非法" in capsys.readouterr().out
    assert _members(db) == []


@pytest.mark.parametrize("bad", ["auto", "true", "1", "yes"])
def test_cli_grant_rejects_auto_like_modes(monkeypatch, capsys, bad):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sys, "argv", ["grant_demo_membership.py", "--mode", bad])
    assert main() == 2
    assert "不接受 auto/true" in capsys.readouterr().out


def test_cli_grant_requires_explicit_target(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sys, "argv", ["grant_demo_membership.py", "--mode", "grant"])
    assert main() == 2
    assert "需要显式目标" in capsys.readouterr().out


# ── 启动期：一次性动作与种子解耦 ────────────────────────────────────────────


def test_oneshots_run_when_seeding_disabled(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """SEED_ON_BOOT 未开时，一次性动作仍必须执行。"""
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "user:9")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        boot_seed,
        "_run_script",
        lambda name, extra=(), **k: calls.append((name, tuple(extra))) or True,
    )

    assert boot_seed.main() == 0
    out = capsys.readouterr().out
    assert "SEED_ON_BOOT 未开启，跳过铺种子" in out
    assert calls == [("grant_demo_membership.py", ("--mode", "grant", "--user-id", "9"))]


def test_oneshots_run_even_when_seed_guard_skips(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """播种守卫早退**不得**吞掉一次性动作（2026-09-24 的陷阱）。"""
    monkeypatch.setenv("SEED_ON_BOOT", "true")
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", OPENID)
    monkeypatch.setattr(boot_seed, "_wait_backend", lambda *a, **k: True)
    monkeypatch.setattr(boot_seed, "_demo_state", lambda: "bound")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        boot_seed,
        "_run_script",
        lambda name, extra=(), **k: calls.append((name, tuple(extra))) or True,
    )

    assert boot_seed.main() == 0
    out = capsys.readouterr().out
    assert "跳过铺种子" in out
    assert calls == [("grant_demo_membership.py", ("--mode", "grant", "--openid", OPENID))]


def test_boot_seed_runs_both_oneshots(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BIND_DEMO_IDENTITY", "user:2")
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "user:2")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        boot_seed,
        "_run_script",
        lambda name, extra=(), **k: calls.append((name, tuple(extra))) or True,
    )

    assert boot_seed.main() == 0
    assert calls == [
        ("bind_demo_identity.py", ("--mode", "bind", "--user-id", "2")),
        ("grant_demo_membership.py", ("--mode", "grant", "--user-id", "2")),
    ]


def test_boot_seed_rejects_auto_grant_target(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "auto")
    calls: list[str] = []
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: calls.append(name) or True
    )

    assert boot_seed.main() == 0
    out = capsys.readouterr().out
    assert "不接受" in out
    assert "DEMO_GRANT_ORG_MEMBER=<openid>" in out
    assert calls == []


# ── 多目标（2026-09-24：两位测试者一次授予） ────────────────────────────────


def test_grant_env_accepts_multiple_targets(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """体验成员是多个人 ⇒ 一次性动作必须能一次授予多条，避免反复改环境变量＋反复冷启动。"""
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "user:4,user:5")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        boot_seed,
        "_run_script",
        lambda name, extra=(), **k: calls.append((name, tuple(extra))) or True,
    )

    assert boot_seed.main() == 0
    out = capsys.readouterr().out
    assert calls == [
        ("grant_demo_membership.py", ("--mode", "grant", "--user-id", "4")),
        ("grant_demo_membership.py", ("--mode", "grant", "--user-id", "5")),
    ]
    assert "共 2 个目标" in out


@pytest.mark.parametrize("raw", ["user:4 user:5", "user:4, user:5", " user:4 ,user:5 "])
def test_grant_env_tolerates_separators_and_spaces(monkeypatch, raw):  # type: ignore[no-untyped-def]
    """逗号 / 空白 / 混合，以及多余空格都必须容忍 —— 运维手抄时最常见的噪声。"""
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", raw)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: calls.append(tuple(extra)) or True
    )

    assert boot_seed.main() == 0
    assert calls == [
        ("--mode", "grant", "--user-id", "4"),
        ("--mode", "grant", "--user-id", "5"),
    ]


def test_grant_env_supports_mixed_openid_and_user_id(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "oxREAL0000000000000000000000,user:5")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: calls.append(tuple(extra)) or True
    )

    assert boot_seed.main() == 0
    assert calls == [
        ("--mode", "grant", "--openid", "oxREAL0000000000000000000000"),
        ("--mode", "grant", "--user-id", "5"),
    ]


def test_grant_env_rejects_whole_list_when_any_target_invalid(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """列表里有一项非法 ⇒ **整段拒绝**（不静默跳过），否则"以为加了两条、实际一条"会变成下一个故障。"""
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "user:4,auto")
    calls: list[str] = []
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: calls.append(name) or True
    )

    assert boot_seed.main() == 0
    assert calls == []
    assert "不接受" in capsys.readouterr().out


def test_grant_env_rejects_malformed_user_id(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "user:abc")
    calls: list[str] = []
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: calls.append(name) or True
    )

    assert boot_seed.main() == 0
    assert calls == []
    assert "不接受" in capsys.readouterr().out


def test_oneshot_failure_is_reported_with_target(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """失败项要**点名到目标**：只报脚本名会让人不知道是哪一条没加上。"""
    monkeypatch.setenv("DEMO_GRANT_ORG_MEMBER", "user:4,user:5")
    # 4 成功、5 失败（回显最后一位参数即 user-id）
    monkeypatch.setattr(
        boot_seed, "_run_script", lambda name, extra=(), **k: tuple(extra)[-1] == "4"
    )

    assert boot_seed.main() == 0
    out = capsys.readouterr().out
    assert "以下步骤失败" in out
    assert "grant_demo_membership.py(user:5)" in out
    assert "grant_demo_membership.py(user:4)" not in out
