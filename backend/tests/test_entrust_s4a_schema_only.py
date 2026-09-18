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
2. **代码取值域与命令成对**（`completed` 在域里 ⇔ 委托层有 `complete` 端点；
   `_CANCELLABLE` 仍原样 —— Q1 裁定）；
3. 委托层**只**开着 `complete`（`close` / `reopen` 仍未开放）；
   `financial_status` 不进委托投影（它是派生，走自己的端点），
   `completed_at` 进投影且未结案时为 `None`；
4. 迁移模块**排在建表模块之后**（执行器按模块名字典序应用，没有声明式依赖）。

## 本文件**不**做什么

不测结案业务本身（五维度前置、逐条报缺、客户确认、幂等、并发）—— 那些在
`test_entrust_closure.py`。本文件只守**边界**：哪些口子开着、哪些还关着。

⚠️ 本文件最初写于 S4-a（"只落结构、不开能力"）。S4-b 落地时，按本文档原定的处置，
第 2/3 组断言被**替换**成了现在这些（`completed` 进域 ＋ `complete` 端点存在 ＋
`completed_at` 进投影），**不是删掉** —— 它们保护的边界变了，但"边界必须被断言"不变。
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


# ── 2. 代码取值域与命令**成对**演进（S4-b 已落地第五个取值）─────────────────


def test_status_domain_follows_the_completion_command():
    """S4-b 把第五个取值 `completed` 落进代码域 —— 且与 `complete` 命令**同一个提交**。

    S4-a 刻意**没**加它，理由写在当时的边界说明里：`verify_entrust_ui.js` 强制前端
    状态镜像与后端 `STATUS_*` 逐格一致，加了常量界面上就会多出一个「已完成」筛选片
    ——而那时没有任何数据能处于该状态，那是在暗示一条不存在的路径。S4-a 的 §7.1 写的是
    "留到 S4-b 与 `complete` 命令同一个提交里一起改"，本用例就是那句话的可核对形态：
    ⭐ **取值域里有 `completed` ⇔ 委托层有产出它的端点**，两件事一起断言 ——
    这样"加了取值域但没命令"或"有命令但取值域没跟上"都会被拦下。
    """
    domain = {
        name: value
        for name, value in vars(svc).items()
        if name.startswith("STATUS_") and isinstance(value, str)
    }
    assert set(domain.values()) == {
        "draft",
        "submitted",
        "claimed",
        "completed",
        "cancelled",
    }, f"取值域与 S4-b 不符：{sorted(domain.values())}"

    # `active` 集是"非终态"的语义 ⇒ 多一个**终态**不该改变它（S4-a 的断言继续成立）
    assert svc.ACTIVE_STATUSES == (svc.STATUS_DRAFT, svc.STATUS_SUBMITTED, svc.STATUS_CLAIMED), (
        "active 集不得因为新增一个终态而变化"
    )
    assert svc._CANCELLABLE == (svc.STATUS_DRAFT, svc.STATUS_SUBMITTED), (
        "Q1 裁定：DEMO-1 不新增通用取消 ⇒ _CANCELLABLE 保持 (draft, submitted)"
    )

    # ⭐ 取值域与命令成对
    paths = _openapi_entrust_routes()
    assert any(p.endswith("/complete") for p in paths), (
        "代码取值域里有 completed，却没有产出它的端点 —— 那是一个到不了的状态"
    )


# ── 3. 委托层只开着 `complete`；重开仍未开放 ───────────────────────────────


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


def _openapi_entrust_routes_with_methods() -> dict[str, set[str]]:
    """委托支线的 `路径 → 该路径上开放的 HTTP 方法（小写）`。

    为什么需要方法：`closure-readiness` 这种**只读配套**与 `reopen` 这种**命令**
    在路径关键词上长得一样，靠名字区分必然误伤或漏放。
    """
    from app.main import app
    from app.modules.entrust import scope_matrix as sm

    prefix = sm.API_PREFIX
    return {
        path: {m.lower() for m in ops}
        for path, ops in app.openapi()["paths"].items()
        if path.startswith(prefix)
    }


def test_only_complete_is_open_in_the_assignment_layer():
    """**委托层**的结案类**写端点**只有 `complete`；重开（S4-c）与"关闭"仍未开放。

    S4-a 的原断言是"一个都没有"（正确表现是「这个能力还没有入口」）。S4-b 落地了
    `complete`，于是本用例改成**边界仍然清晰**：`complete` 在，
    `close` / `closure` / `reopen` 不在。⛔ 判据必须限定在 `/assignments/` 前缀上：
    `/tasks/{id}/complete`、`/exceptions/{id}/close` 是**任务层与案件层**的既有能力，
    它们一直都在，且不得被拿来顶替委托结案（口径设计 §5.7："`close_case`（案件结案）与
    委托 `complete` 是两个层级，不得互相替代"）。

    ⭐ S4-b 的读侧配套：`GET …/closure-readiness` 含 `closure` 字样，但它是**只读**的
    （不改变任何状态）。⇒ 判据按 **method** 收口 —— 从"任何端点"收窄到"**写**端点"，
    因为"重开/关闭"必然是命令；同时**反向核对**白名单里那一条**必须真的是 GET**，
    否则这个名单就变成"往只读的名字里塞写命令"的后门（那样判据反而变弱）。
    """
    routes = _openapi_entrust_routes_with_methods()
    assignment = {p: ms for p, ms in routes.items() if "/assignments/" in p}
    assert assignment, "用例前提：委托层本就有端点，否则本断言是空的"

    assert any(p.endswith("/complete") and "post" in ms for p, ms in assignment.items()), (
        "S4-b 要求委托层有 complete 的**写**端点（取值域里的 completed 靠它产出）"
    )

    from app.modules.entrust import scope_matrix as sm

    readonly_companions = {
        f"{sm.API_PREFIX}/assignments/{{assignment_id}}/closure-readiness",
    }
    #: **本切片（S4-c）正式落地**的重开命令。它出现在这张名单里不是"放行"，而是登记：
    #: S4-c 就是"带理由、授权与历史留痕"的那一片。⚠️ 下面逐条核对它必须是 `POST`，
    #: 所以名单**不可能**被用来夹带一个"只读名字"的写命令。
    allowed_writes = {f"{sm.API_PREFIX}/assignments/{{assignment_id}}/reopen"}
    write_routes = {p for p, ms in assignment.items() if ms & {"post", "put", "patch", "delete"}}
    still_closed = sorted(
        p
        for p in write_routes
        if any(k in p.lower() for k in ("close", "closure", "reopen"))
        and not p.endswith("/complete")
        and p not in allowed_writes
    )
    assert not still_closed, (
        f"委托层出现了本切片之外的结案类**写**端点 {still_closed} —— 重开是 S4-c，"
        "且必须带理由、授权与历史留痕；`close` 不在计划内（案件层才有 close_case）"
    )
    for path in allowed_writes:
        assert assignment.get(path, set()) >= {"post"}, (
            f"{path} 必须以 POST 落地（写命令），实际方法：{sorted(assignment.get(path, set()))}"
        )
    for path in readonly_companions:
        assert path in assignment, f"只读配套路径不存在：{path}"
        assert assignment[path] == {"get"}, (
            f"{path} 必须是**只读**端点，实际开放的方法：{sorted(assignment[path])} —— "
            "只读配套一旦能写，这个白名单就成了塞写命令的后门"
        )


def test_financial_status_is_not_projected_but_completed_at_is():
    """投影的分工：`financial_status` **不进**委托投影；`completed_at` 进，且未结案时为 `None`。

    两者的差别不是"哪个字段安全"，而是**语义**：

    * `financial_status` 是**派生**（由费用/结算/收付/处置事实算出来），它有自己那条
      端点（`/assignments/{id}/financial-status`，带 `blockers`）。把它塞进委托投影等于
      给同一个概念开第二处真相；S4-a 更具体的担心是"派生没接通时列默认值会被读成结论"。
    * `completed_at` 是**事实**：它由 `complete` 命令写入，未结案时就是 `NULL`
      —— 那是"还没有"，不是"某个占位时间"。合同 §6.4 还要求接口能把**运营完成**与
      **财务结案**分开说，所以这个字段必须在。
    """
    engine = _engine()
    _insert_claimed(engine)
    with Session(engine) as session:
        row = svc.get_assignment(session, 1)

    assert row is not None, "用例前提：刚插入的那一行应当可读"
    assert "financial_status" not in row, (
        "委托投影里出现了 financial_status —— 它是派生，走自己的端点；"
        "塞进来就是给同一个概念开第二处真相"
    )
    assert "completed_at" in row, "completed_at 是运营完成的**事实**，界面要靠它区分两个维度"
    assert row["completed_at"] is None, "未结案的委托必须是 None（未知保持未知）—— ⛔ 不编占位时间"


def test_rerun_of_migrations_is_a_no_op():
    """可重复执行：迁移执行器按 `(module, migration_id)` 去重，复跑不得再执行。"""
    engine = _engine()
    assert apply_pending(engine) == [], "复跑不应有新的待执行条目"
