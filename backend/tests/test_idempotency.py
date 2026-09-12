"""幂等服务契约测试。

覆盖：首次执行 / 成功重放 / 同键不同体冲突 / 进行中并发 / 失败释放 / 过期重跑 /
清理过期 / 请求头校验 / 指纹规范化。
"""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.idempotency import (  # noqa: E402
    IdempotencyConflictError,
    IdempotencyInProgressError,
    MissingIdempotencyKeyError,
    begin,
    fail,
    fingerprint_of,
    idempotent,
    purge_expired,
    require_idempotency_key,
    succeed,
    utcnow_naive,
)

SCOPE = "entrust:quote:publish"


@pytest.fixture()
def session():
    """独立的 SQLite 内存库会话，`ent_` 表由迁移创建（与 CI 路径一致）。"""
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _insert(
    db, *, key="k1", fingerprint="fp", status="in_progress", snapshot=None, expires_at=None
):
    db.execute(
        text(
            "INSERT INTO ent_idempotency "
            "(scope, idempotency_key, actor_user_id, request_fingerprint, status,"
            " response_snapshot, created_at, expires_at) "
            "VALUES (:s, :k, 1, :f, :st, :snap, :c, :e)"
        ),
        {
            "s": SCOPE,
            "k": key,
            "f": fingerprint,
            "st": status,
            "snap": snapshot,
            "c": utcnow_naive().strftime("%Y-%m-%d %H:%M:%S"),
            "e": expires_at,
        },
    )
    db.commit()


# ─────────────────────────────────────────────── 基本语义


def test_first_call_returns_none_and_replays_after_success(session):
    """首次执行返回 None；成功后同一键重放快照，业务不再执行。"""
    assert begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp") is None

    succeed(session, scope=SCOPE, key="k1", status_code=201, body={"quote_id": 7})

    replay = begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp")
    assert replay is not None
    assert replay.status_code == 201
    assert replay.body == {"quote_id": 7}


def test_different_scope_does_not_interfere(session):
    """不同 scope 下的相同键互不影响。"""
    assert begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp") is None
    assert (
        begin(session, scope="entrust:task:dispatch", key="k1", actor_user_id=1, fingerprint="fp")
        is None
    )


def test_same_key_different_payload_is_conflict(session):
    """同键不同请求体 = 客户端用错键，必须判冲突而不是重放。"""
    begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp-a")
    with pytest.raises(IdempotencyConflictError):
        begin(session, scope=SCOPE, key="k1", actor_user_id=2, fingerprint="fp-b")


def test_in_progress_is_reported_not_replayed(session):
    """上一次仍在处理中时，不得放行第二次写。"""
    _insert(session, key="k1", fingerprint="fp", status="in_progress")
    with pytest.raises(IdempotencyInProgressError):
        begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp")


def test_failed_record_is_released_for_retry(session):
    """失败记录被删除，客户端可用同一键立即重试。"""
    _insert(session, key="k1", fingerprint="fp", status="failed")
    assert begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp") is None


def test_fail_never_deletes_successful_snapshot(session):
    """已成功的快照必须保留到过期，否则重试会真的再执行一次。"""
    begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp")
    succeed(session, scope=SCOPE, key="k1", body={"ok": True})
    fail(session, scope=SCOPE, key="k1")

    replay = begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp")
    assert replay is not None and replay.body == {"ok": True}


# ─────────────────────────────────────────────── 过期与清理


def test_expired_record_is_not_replayed(session):
    """过期快照不再重放，本次当作新操作执行。"""
    past = (utcnow_naive() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert(
        session,
        key="k1",
        fingerprint="fp",
        status="succeeded",
        snapshot='{"status": 200, "body": {"old": 1}}',
        expires_at=past,
    )

    assert begin(session, scope=SCOPE, key="k1", actor_user_id=1, fingerprint="fp") is None


def test_purge_expired_only_removes_expired(session):
    """清理只删过期行；未过期的重放窗口必须保留。"""
    past = (utcnow_naive() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    future = (utcnow_naive() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert(session, key="old", fingerprint="fp", status="succeeded", expires_at=past)
    _insert(session, key="new", fingerprint="fp", status="succeeded", expires_at=future)
    _insert(session, key="forever", fingerprint="fp", status="succeeded", expires_at=None)

    assert purge_expired(session) == 1

    remaining = (
        session.execute(
            text("SELECT idempotency_key FROM ent_idempotency ORDER BY idempotency_key")
        )
        .scalars()
        .all()
    )
    assert remaining == ["forever", "new"]


# ─────────────────────────────────────────────── guard 上下文


def test_guard_replays_on_second_call(session):
    """第二次进入 guard 直接拿到 replay，业务代码不再执行。"""
    payload = {"quote": {"price": 100}, "note": "首次"}
    calls: list[str] = []

    for _ in range(2):
        with idempotent(session, scope=SCOPE, key="k1", actor_user_id=1, payload=payload) as guard:
            if guard.replay is not None:
                calls.append("replay")
            else:
                calls.append("work")
                guard.succeed(200, {"quote_id": 1})

    assert calls == ["work", "replay"]


def test_guard_releases_key_when_business_raises(session):
    """业务抛异常 → 释放键，客户端可用同一键重试（异常继续向外抛）。"""
    payload = {"x": 1}
    with (
        pytest.raises(RuntimeError),
        idempotent(session, scope=SCOPE, key="k1", actor_user_id=1, payload=payload),
    ):
        raise RuntimeError("下游失败")

    # 同一键可以重新开始
    with idempotent(session, scope=SCOPE, key="k1", actor_user_id=1, payload=payload) as guard:
        assert guard.replay is None
        guard.succeed(200, {"ok": True})


def test_guard_releases_key_when_succeed_not_called(session):
    """进入 guard 但既没成功也没抛异常，同样释放键（防止误判为成功）。"""
    with idempotent(session, scope=SCOPE, key="k1", actor_user_id=1, payload={"x": 1}):
        pass

    with idempotent(session, scope=SCOPE, key="k1", actor_user_id=1, payload={"x": 1}) as guard:
        assert guard.replay is None


# ─────────────────────────────────────────────── 指纹与请求头


def test_fingerprint_ignores_key_order_and_unicode_escaping():
    """字段顺序不同、Unicode 写法不同，只要内容一致就是同一操作。"""
    assert fingerprint_of({"a": 1, "b": {"c": 2}}) == fingerprint_of({"b": {"c": 2}, "a": 1})
    assert fingerprint_of({"name": "平陆"}) == fingerprint_of({"name": "平陆"})
    assert fingerprint_of({"a": 1}) != fingerprint_of({"a": 2})


def test_require_idempotency_key():
    """缺头 / 空值必须报错；正常值去空白。"""
    assert require_idempotency_key({"Idempotency-Key": "  abc  "}) == "abc"
    for headers in ({}, {"Idempotency-Key": "   "}):
        with pytest.raises(MissingIdempotencyKeyError):
            require_idempotency_key(headers)
