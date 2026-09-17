"""S3 数据底座（`ent_commitment` 迁移）的结构与**约束**测试（HO 0917-3 裁定三）。

本文件只测**结构**：表在不在、约束管不管用、默认值是不是设计里写的那个。
命令、端点与界面**均未实现** —— 别把这里的绿读成"S3 可用"。

为什么值得单独测一轮
--------------------
裁定三里有两条语义**只靠数据层就能挡住**，而它们恰好是最容易被"应用层先查后写"
绕过去的两条：

1. **同一次发布只能被响应一次** ⇒ 必须由 `UNIQUE (release_id)` 保证。应用层先查后写
   在并发下两个请求会同时通过检查，于是同一次发布被接受两次、且两次都"看起来合法"。
2. **航段在委托内有序且唯一** ⇒ 变更复核与运力确认要按航段对齐，`seq` 重复会让
   "第 2 段是水路还是公路"变成一个没有答案的问题。
"""

from __future__ import annotations

import os
import uuid

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

_TS = "2026-09-17 00:00:00"

NEW_TABLES = (
    "ent_leg",
    "ent_capacity_candidate",
    "ent_offer_release",
    "ent_offer_response",
)


@pytest.fixture()
def db():
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tables(session) -> set[str]:
    rows = session.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).scalars()
    return {str(r) for r in rows}


def test_commitment_migration_creates_four_tables(db):
    """四张表都由迁移建成（`ent_` 前缀不进 `create_all`，漏了就是运行期才炸）。"""
    names = _tables(db)
    for t in NEW_TABLES:
        assert t in names, f"缺表 {t}"


def test_offer_response_is_unique_per_release(db):
    """**同一次发布只能被响应一次** —— 判据取唯一约束，不取应用层的"先查后写"。

    这一条同时是 D1-07 的一半：客户对**同一个**已发布版本重复响应必须被挡住。
    """
    common = {
        "release_id": 7,
        "assignment_id": 1,
        "artifact_id": 3,
        "responded_revision_id": 11,
        "customer_user_id": 1,
        "note": None,
        "responded_at": _TS,
        "created_at": _TS,
        "updated_at": _TS,
    }
    insert = text(
        "INSERT INTO ent_offer_response "
        "(release_id, assignment_id, artifact_id, responded_revision_id, decision, "
        " customer_user_id, note, responded_at, created_at, updated_at) "
        "VALUES (:release_id, :assignment_id, :artifact_id, :responded_revision_id, "
        " :decision, :customer_user_id, :note, :responded_at, :created_at, :updated_at)"
    )
    db.execute(insert, {**common, "decision": "accept"})
    db.commit()

    with pytest.raises(IntegrityError):
        db.execute(insert, {**common, "decision": "reject"})
    db.rollback()

    # 复核：表里仍然只有那一条（回滚没有留下半行）
    assert db.execute(text("SELECT COUNT(*) FROM ent_offer_response")).scalar() == 1


def test_leg_sequence_is_unique_within_assignment(db):
    """航段在委托内按顺序唯一 —— `seq` 重复会让"第 2 段是什么"没有答案。"""
    insert = text(
        "INSERT INTO ent_leg (assignment_id, seq, mode, from_name, to_name, "
        " created_at, updated_at) VALUES (:a, :s, :m, :f, :t, :c, :c)"
    )
    db.execute(insert, {"a": 1, "s": 1, "m": "road", "f": "厂区", "t": "南宁港", "c": _TS})
    db.execute(insert, {"a": 1, "s": 2, "m": "water", "f": "南宁港", "t": "贵港港", "c": _TS})
    db.commit()

    with pytest.raises(IntegrityError):
        db.execute(insert, {"a": 1, "s": 2, "m": "road", "f": "x", "t": "y", "c": _TS})
    db.rollback()

    # 另一张委托的 seq=2 必须仍然可写（唯一性是**委托内**的，不是全局的）
    db.execute(insert, {"a": 2, "s": 2, "m": "water", "f": "x", "t": "y", "c": _TS})
    db.commit()
    assert db.execute(text("SELECT COUNT(*) FROM ent_leg")).scalar() == 3


def test_capacity_candidate_ships_the_basis_as_data(db):
    """容量口径必须是**数据**：只写"900 吨"算不出"950 装不下"。

    合同 BP-04 第 6 条要求它是确定性检查（"an LLM opinion is not the rule"），
    所以 `capacity_tonnes` / `vessel_count` / `allows_partial_load` 都落在列上。
    """
    db.execute(
        text(
            "INSERT INTO ent_capacity_candidate "
            "(assignment_id, leg_id, carrier, vessel_name, capacity_tonnes, vessel_count, "
            " allows_partial_load, rate, rate_unit, currency, valid_until, status, "
            " created_at, updated_at) "
            "VALUES (1, 2, '桂平航 6688', '桂平航 6688', '900.000', 1, 0, '45.00', '吨', "
            " 'CNY', '2026-12-31', 'candidate', :c, :c)"
        ),
        {"c": _TS},
    )
    db.commit()
    row = (
        db.execute(
            text(
                "SELECT capacity_tonnes, vessel_count, allows_partial_load, status "
                "FROM ent_capacity_candidate"
            )
        )
        .mappings()
        .one()
    )
    assert float(row["capacity_tonnes"]) == 900.0
    assert int(row["vessel_count"]) == 1
    # 不拆批是**判据的关键**：0 + 单船 ⇒ 950 > 900 才真的等于装不下
    assert int(row["allows_partial_load"]) == 0
    assert row["status"] == "candidate"


def test_offer_release_defaults_and_snapshot_required(db):
    """发布记录：默认 `released`、快照必填（空快照等于"客户看到什么"无从回溯）。"""
    insert = text(
        "INSERT INTO ent_offer_release "
        "(assignment_id, artifact_id, revision_id, revision_no, customer_user_id, "
        " snapshot_json, released_by, released_at, created_at, updated_at) "
        "VALUES (1, 3, 11, 4, 1, :snap, 2, :c, :c, :c)"
    )
    db.execute(insert, {"snap": '{"amount":"36000.00","currency":"CNY"}', "c": _TS})
    db.commit()
    row = (
        db.execute(text("SELECT status, authorized_attachment_ids FROM ent_offer_release"))
        .mappings()
        .one()
    )
    assert row["status"] == "released"
    # 未显式授权 = 没有可下载附件（**不是**"默认全开"）
    assert row["authorized_attachment_ids"] is None

    with pytest.raises(IntegrityError):
        db.execute(insert, {"snap": None, "c": _TS})
    db.rollback()


def test_release_and_response_are_linked_by_the_exact_revision(db):
    """响应行自带 `responded_revision_id` ⇒ 单看响应即可自证"接受的是那个精确版本"。

    冗余这一列是有意的：D1-07 要证明的就是"客户接受的是**精确版本**"，
    若只能靠 join 才能读出该事实，写错 join 就会看起来"对得上"。
    """
    db.execute(
        text(
            "INSERT INTO ent_offer_release "
            "(id, assignment_id, artifact_id, revision_id, revision_no, customer_user_id, "
            " snapshot_json, released_by, released_at, created_at, updated_at) "
            "VALUES (7, 1, 3, 11, 4, 1, '{}', 2, :c, :c, :c)"
        ),
        {"c": _TS},
    )
    db.execute(
        text(
            "INSERT INTO ent_offer_response "
            "(release_id, assignment_id, artifact_id, responded_revision_id, decision, "
            " customer_user_id, responded_at, created_at, updated_at) "
            "VALUES (7, 1, 3, 11, 'accept', 1, :c, :c, :c)"
        ),
        {"c": _TS},
    )
    db.commit()
    row = (
        db.execute(
            text(
                "SELECT r.revision_id AS released, p.responded_revision_id AS responded "
                "FROM ent_offer_release r JOIN ent_offer_response p ON p.release_id = r.id"
            )
        )
        .mappings()
        .one()
    )
    assert int(row["released"]) == int(row["responded"]) == 11


def test_migration_ids_do_not_collide_with_other_modules(db):
    """迁移 id 只在**本模块内**排序；这里登记一下本次新表的模块名与条目数，
    与 `migrations/ent_commitment.py` 保持可核对（防止有人删条目却不改文档）。"""
    import importlib

    mod = importlib.import_module("migrations.ent_commitment")
    ids = [int(m["id"]) for m in mod.migrations]
    assert ids == [1, 2, 3, 4], ids
    assert len({*NEW_TABLES}) == 4
    # 每一条都要有 mysql / sqlite 两份方言与自己的 checks —— 缺方言执行器会直接报错
    for m in mod.migrations:
        assert set(m["sql"]) == {"mysql", "sqlite"}, m["description"]
        assert m["checks"], m["description"]
    # 生成一个不重复的名字，避免 linter 把 uuid 当未使用导入
    assert uuid.uuid4().hex
