"""S4-a 的闸门：**列加了，但能力没有被开放**。

为什么需要这组断言
==================
S4-a 是一次"只落结构"的切片（口径见 `docs/entrust/S4-委托结案状态机口径设计.md`）。
这类切片的失效方式很特殊：**它没有下游症状**。

* 列加上了、默认值填上了，一切看起来"做完了"；
* 但如果顺手把 `STATUS_COMPLETED` 也加进代码取值域、或者把 `financial_status`
  塞进响应投影，就凭空出现了一个**没有前置检查、没有客户确认、没有幂等**的完成路径的
  影子 —— 而它在任何现有用例里都不会红。

所以本文件把 HO 指定的边界写成**可执行断言**：

1. 迁移**确实**落地了结构与默认值，且**不推测历史**（无 DML、既有行原样保留）；
2. 代码取值域与可取消集**未变**（`completed` 不在代码域里，`_CANCELLABLE` 原样）；
3. 没有结案端点、`financial_status` 不出现在服务层投影里（派生未接通 ⇒ 不展示）；
4. 迁移模块**排在建表模块之后**（执行器按模块名字典序应用，没有声明式依赖）。

## 本文件**不**做什么

不测结案业务（前置检查、客户确认、幂等、并发）—— 那属于 S4-b，**现在还不存在**。
⚠️ S4-b 落地时，第 2/3 组断言应当被**替换**成对应的正向用例（"少了前置就 409"等），
**不是删掉** —— 那时它们保护的边界变了，但"边界必须被断言"这件事不变。
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.entrust import assignments as svc
from migrate import apply_pending, load_entries, resolve_sql

NEW_MODULE = "ent_assignment_completion"
FORBIDDEN_DML = ("UPDATE ", "INSERT ", "DELETE ", "MERGE ", "REPLACE ", "TRUNCATE ")


def _engine():
    """全新 SQLite 内存库 + 全量迁移（同一连接共享，便于断言）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    apply_pending(engine)
    return engine


def _insert_claimed(engine) -> None:
    """直接落一行"已受理"的委托 —— 模拟迁移前就存在的历史数据。"""
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO `ent_assignment` "
            "(`owner_user_id`, `title`, `status`, `revision`, `created_at`, `updated_at`) "
            "VALUES (1, '历史委托', 'claimed', 1, '2026-09-17 00:00:00', '2026-09-17 00:00:00')"
        )


# ── 1. 结构落地 + 不推测历史 ─────────────────────────────────────────────


def test_completion_columns_land_with_the_documented_defaults():
    """`completed_at` 可空、`financial_status` NOT NULL 且默认 `not_started`。"""
    columns = {c["name"]: c for c in inspect(_engine()).get_columns("ent_assignment")}

    assert "completed_at" in columns, "S4-a 必须新增 completed_at 列"
    assert columns["completed_at"]["nullable"] is True, "完成时间未知就是 NULL，不能 NOT NULL"

    assert "financial_status" in columns, "S4-a 必须新增 financial_status 列"
    assert columns["financial_status"]["nullable"] is False, "财务维度必须有取值，不能留空"


def test_existing_rows_keep_their_status_and_get_no_completion_time():
    """旧数据**不推测完成**：`claimed` 保持 `claimed`，`completed_at` 保持 NULL。

    `financial_status` 落成 `not_started` 只是**列默认值** ——
    它不是"这些委托财务未开始"这个结论（§5.3.2 末条），所以本用例只断言列值，
    并**同时**断言服务层不把它展示出去（见第 3 组）。
    """
    engine = _engine()
    _insert_claimed(engine)
    with engine.begin() as conn:
        rows = list(
            conn.exec_driver_sql(
                "SELECT `status`, `completed_at`, `financial_status` FROM `ent_assignment`"
            )
        )

    assert len(rows) == 1
    status, completed_at, financial_status = rows[0]
    assert status == "claimed", "迁移不得把 claimed 推断成 completed（旧数据不推测完成）"
    assert completed_at is None, "迁移不得给历史行编一个完成时间"
    assert financial_status == "not_started", "finances 维度只落列默认值"


def test_migration_is_append_only_and_writes_no_data():
    """新增迁移只做 DDL：**没有**任何 UPDATE/INSERT/DELETE（否则就是手改状态造数据）。"""
    entries = [e for e in load_entries() if e.module == NEW_MODULE]
    assert [e.migration_id for e in entries] == [1, 2, 3], "id 只增不改，且本模块应为 1/2/3"

    for entry in entries:
        dialects = entry.sql if isinstance(entry.sql, dict) else {}
        for dialect in sorted(dialects):
            upper = resolve_sql(entry, dialect).upper()
            hits = [token.strip() for token in FORBIDDEN_DML if token in upper]
            assert not hits, (
                f"{entry.ref}[{dialect}] 出现了数据写入语句 {hits} —— "
                "S4-a 只落结构；用迁移把状态改成 completed 会产出"
                "一条不证明结案链路可用的假数据（HO 0917 明禁）"
            )


def test_migration_module_applies_after_the_module_that_creates_the_table():
    """执行器按**模块名字典序**应用、没有声明式依赖 ⇒ 顺序只能靠文件名保证。

    排错会让迁移在空库上先于建表执行，症状是 `no such table: ent_assignment`。
    """
    order = [e.module for e in load_entries()]
    assert NEW_MODULE in order, f"{NEW_MODULE} 未被执行器装载"
    assert order.index(NEW_MODULE) > order.index("ent_assignment"), (
        f"{NEW_MODULE} 必须排在 ent_assignment 之后（它对后者的表做 ALTER）"
    )


# ── 2. 代码取值域与可取消集未变（能力未开放） ─────────────────────────────


def test_status_domain_and_cancellable_set_are_unchanged():
    """S4-a **不**把 `completed` 加进代码取值域，`_CANCELLABLE` 也一字未改。

    这看起来"少做了一步"，其实是 HO 指定的边界：`verify_entrust_ui.js` 会强制
    前端状态镜像与后端 `STATUS_*` **逐格一致**，一旦这里加了常量，界面上就必须出现
    一个「已完成」筛选片 —— 而此刻没有任何数据能处于该状态，那是在暗示一条不存在的路径。
    代码取值域与前端镜像留到 S4-b 与 `complete` 命令**同一个提交**里一起改。
    """
    domain = {
        name: value
        for name, value in vars(svc).items()
        if name.startswith("STATUS_") and isinstance(value, str)
    }
    assert set(domain.values()) == {"draft", "submitted", "claimed", "cancelled"}, (
        f"代码取值域被改动了：{sorted(domain.values())}；S4-a 只落结构，不动取值域"
    )

    assert svc.ACTIVE_STATUSES == (svc.STATUS_DRAFT, svc.STATUS_SUBMITTED, svc.STATUS_CLAIMED), (
        "active 集不得因为新增列而变化"
    )
    assert svc._CANCELLABLE == (svc.STATUS_DRAFT, svc.STATUS_SUBMITTED), (
        "Q1 裁定：DEMO-1 不新增通用取消 ⇒ _CANCELLABLE 保持 (draft, submitted)"
    )


# ── 3. 没有结案入口、财务维度不出现在投影里 ───────────────────────────────


def _openapi_entrust_routes() -> set[str]:
    """委托支线的全部路径（小写）。

    走 `app.openapi()` 而不是遍历 `app.routes`：本项目的 FastAPI 用 `_IncludedRouter`
    **惰性挂载**，直接遍历 `app.routes` 只能拿到占位对象、拿不到子路由
    （与 `test_entrust_scope_matrix.py` 同一理由）。
    """
    from app.main import app
    from app.modules.entrust import scope_matrix as sm

    prefix = sm.API_PREFIX
    return {p for p in app.openapi()["paths"] if p.startswith(prefix)}


def test_no_closure_or_completion_endpoint_exists_yet():
    """**委托层**不得有结案/重开端点：正确表现是「这个能力还没有入口」。

    ⚠️ 判据必须限定在 `/assignments/` 前缀上：`/tasks/{id}/complete`、
    `/exceptions/{id}/close` 是**任务层与案件层**的既有能力，它们一直都在，
    且**不得**被拿来顶替委托结案（口径设计 §5.7："`close_case`（案件结案）与
    委托 `complete` 是**两个层级**，不得互相替代"）。把三层混在一个正则里查，
    要么永远红、要么把"委托层没做"这件事悄悄放过。
    """
    paths = _openapi_entrust_routes()
    assignment_paths = sorted(p for p in paths if "/assignments/" in p)
    assert assignment_paths, "用例前提：委托层本就有端点，否则本断言是空的"

    suspicious = sorted(
        p
        for p in assignment_paths
        if any(k in p.lower() for k in ("complete", "closure", "close", "reopen"))
    )
    assert not suspicious, (
        f"委托层出现了结案类端点 {suspicious} —— S4-b 才允许有，且必须同时带五类前置、"
        "客户确认、幂等与并发保护；S4-a 只落结构"
    )


def test_financial_status_and_completed_at_are_not_projected():
    """新列**不得**进入服务层投影（派生未接通 ⇒ 不展示，避免默认值被读成结论）。"""
    engine = _engine()
    _insert_claimed(engine)
    with Session(engine) as session:
        row = svc.get_assignment(session, 1)

    assert row is not None, "用例前提：刚插入的那一行应当可读"
    for leaked in ("financial_status", "completed_at"):
        assert leaked not in row, (
            f"投影里出现了 {leaked} —— 它的值来自「列默认值 / 未接通的派生」，"
            "被展示出去等于把「还不知道」渲染成一个结论（规范 3.3 未知保持未知）"
        )


def test_rerun_of_migrations_is_a_no_op():
    """可重复执行：迁移执行器按 `(module, migration_id)` 去重，复跑不得再执行。"""
    engine = _engine()
    assert apply_pending(engine) == [], "复跑不应有新的待执行条目"
