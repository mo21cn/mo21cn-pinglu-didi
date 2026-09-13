"""成果版本机制的契约测试（ENT-004）。

覆盖规范 3.4 第一条的四条语义：
1. 编辑产生新 revision（append-only，历史不可变）；
2. 确认绑定精确版本（current 只能显式确认改变）；
3. 人工接管优先（manual 生效时 agent 不得替换）；
4. 失效成果不可确认/不可追加。
"""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust.artifacts import (  # noqa: E402
    SOURCE_AGENT,
    SOURCE_MANUAL,
    ArtifactNotFoundError,
    ArtifactVoidError,
    ManualTakeoverError,
    append_revision,
    confirm_revision,
    create_artifact,
    get_artifact,
    list_revisions,
    void_artifact,
)


@pytest.fixture()
def artifacts():
    """独立 SQLite 内存库会话，`ent_` 表由迁移创建（与 test_entrust_access 同款）。"""
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_create_binds_revision_one(artifacts):
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"freight": 12000, "currency": "CNY"},
        created_by=7,
        source=SOURCE_MANUAL,
    )
    assert art["current_revision_id"] is not None
    cur = art["current_revision"]
    assert cur is not None
    assert cur["revision_no"] == 1
    assert cur["payload"] == {"freight": 12000, "currency": "CNY"}
    assert cur["source"] == SOURCE_MANUAL
    assert art["status"] == "active"


def test_append_does_not_move_current_and_history_immutable(artifacts):
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"v": 1},
        created_by=7,
    )
    first_rev_id = art["current_revision_id"]

    rev2 = append_revision(
        artifacts,
        artifact_id=art["artifact_id"],
        payload={"v": 2},
        actor_id=7,
        source=SOURCE_MANUAL,
        note="修正运价",
    )
    assert rev2["revision_no"] == 2

    # 未确认前，current 仍指向 revision 1
    after = get_artifact(artifacts, art["artifact_id"])
    assert after["current_revision_id"] == first_rev_id
    assert after["current_revision"]["payload"] == {"v": 1}

    # append-only：历史版本原样保留，且共两个版本
    revs = list_revisions(artifacts, art["artifact_id"])
    assert [r["revision_no"] for r in revs] == [1, 2]
    assert revs[0]["payload"] == {"v": 1}
    assert revs[1]["payload"] == {"v": 2}


def test_confirm_binds_exact_revision(artifacts):
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="customer_quote",
        payload={"v": 1},
        created_by=7,
    )
    append_revision(
        artifacts,
        artifact_id=art["artifact_id"],
        payload={"v": 2},
        actor_id=7,
        source=SOURCE_MANUAL,
    )
    confirmed = confirm_revision(
        artifacts, artifact_id=art["artifact_id"], revision_no=2, actor_id=7
    )
    assert confirmed["current_revision"] is not None
    assert confirmed["current_revision"]["revision_no"] == 2
    assert confirmed["current_revision"]["payload"] == {"v": 2}

    # 客户按更早版本确认也是合法的：精确绑定，不强制"最新"
    back = confirm_revision(artifacts, artifact_id=art["artifact_id"], revision_no=1, actor_id=7)
    assert back["current_revision"]["revision_no"] == 1


def test_agent_cannot_replace_manual_current(artifacts):
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"by": "human"},
        created_by=7,
        source=SOURCE_MANUAL,
    )
    # Agent 产出新版本（允许追加，但不生效）
    append_revision(
        artifacts,
        artifact_id=art["artifact_id"],
        payload={"by": "agent"},
        actor_id=99,
        source=SOURCE_AGENT,
    )
    with pytest.raises(ManualTakeoverError):
        confirm_revision(
            artifacts,
            artifact_id=art["artifact_id"],
            revision_no=2,
            actor_id=99,
            as_source=SOURCE_AGENT,
        )
    # 人工确认可以替换
    ok = confirm_revision(
        artifacts,
        artifact_id=art["artifact_id"],
        revision_no=2,
        actor_id=7,
        as_source=SOURCE_MANUAL,
    )
    assert ok["current_revision"]["payload"] == {"by": "agent"}


def test_agent_confirm_ok_when_no_manual_takeover(artifacts):
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"by": "agent"},
        created_by=99,
        source=SOURCE_AGENT,
    )
    append_revision(
        artifacts,
        artifact_id=art["artifact_id"],
        payload={"by": "agent2"},
        actor_id=99,
        source=SOURCE_AGENT,
    )
    ok = confirm_revision(
        artifacts,
        artifact_id=art["artifact_id"],
        revision_no=2,
        actor_id=99,
        as_source=SOURCE_AGENT,
    )
    assert ok["current_revision"]["revision_no"] == 2


def test_void_blocks_confirm_and_append(artifacts):
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="contract_review",
        payload={"v": 1},
        created_by=7,
    )
    aid = art["artifact_id"]
    voided = void_artifact(artifacts, artifact_id=aid, actor_id=7, reason="委托撤销")
    assert voided["status"] == "void"

    with pytest.raises(ArtifactVoidError):
        confirm_revision(artifacts, artifact_id=aid, revision_no=1, actor_id=7)
    with pytest.raises(ArtifactVoidError):
        append_revision(
            artifacts,
            artifact_id=aid,
            payload={"v": 2},
            actor_id=7,
            source=SOURCE_MANUAL,
        )
    # 历史仍可读（审计）
    assert len(list_revisions(artifacts, aid)) == 1


def test_not_found_errors(artifacts):
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(artifacts, 99999)
    with pytest.raises(ArtifactNotFoundError):
        confirm_revision(artifacts, artifact_id=99999, revision_no=1, actor_id=7)
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"v": 1},
        created_by=7,
    )
    with pytest.raises(ArtifactNotFoundError):
        confirm_revision(artifacts, artifact_id=art["artifact_id"], revision_no=42, actor_id=7)
    with pytest.raises(ArtifactNotFoundError):
        append_revision(
            artifacts,
            artifact_id=99999,
            payload={},
            actor_id=7,
            source=SOURCE_MANUAL,
        )


def test_revision_numbers_increment(artifacts):
    art = create_artifact(
        artifacts,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"v": 0},
        created_by=7,
    )
    aid = art["artifact_id"]
    for i in range(1, 4):
        r = append_revision(
            artifacts,
            artifact_id=aid,
            payload={"v": i},
            actor_id=7,
            source=SOURCE_MANUAL,
        )
        assert r["revision_no"] == i + 1
    assert [r["revision_no"] for r in list_revisions(artifacts, aid)] == [1, 2, 3, 4]
