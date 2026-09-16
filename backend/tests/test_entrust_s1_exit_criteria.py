"""S1 出口判据的**可执行断言**（DEMO-1 合同原文四条）。

合同原文（`DEMO-1-plan.md` §4 S1 出口判据）：

    A fresh UI-created assignment survives reload, appears in the correct queue,
    can be claimed once, and remains inaccessible to unrelated organization B.

本文件把这一句拆成四条断言，并补上**并发负例**（S1 工作项 4 明确要求的"证据"）。
为什么必须单独成文件：这四条此前只存在于文档里（`DEMO-1-plan.md` §4 "出口判据一条都还没验证"），
而"文档里写了"与"可执行断言跑过了"是两件事 —— 前者会在任何一次重构后静默失真。

三层证据的分工（**互不替代**）
--------------------------------
1. `test_exit_a_*` / `test_exit_b_*` / `test_exit_c_*` / `test_exit_d_*`
   —— **API 层端到端**，走的就是 UI 会走的两个写调用
   （`POST /assignments` 建草稿 → `POST /assignments/{id}/submit` 提交，
   与 `miniapp/utils/entrust.js` 的 `createAssignment` / `submitAssignment` 逐字对应）。
2. `test_claim_race_*`
   —— **数据库层竞争**。API 层的"双认领"（`test_api_full_intake_flow_and_double_claim`）
   是**串行**的：第二个请求到来时状态已是 claimed，被服务层的**前置状态检查**挡掉。
   那证明不了并发安全 —— 真实并发下两个 worker 会**同时**通过前置检查，此时唯一的
   最后防线是那条条件 UPDATE 的 `WHERE status = 'submitted'`。
   本组用例用**两个各自独立连接的 Session 交错读写**来复现这一时点
   （DR-0002：MySQL 用例断言终局必须换新会话读；本机无 MySQL 时用两 Session 交替
   SELECT→UPDATE，看第二次 UPDATE 的 `rowcount` 是否为 0）。
3. `test_claim_sql_is_still_conditional`
   —— **防漂移**。第 2 组复刻了服务层那条 SQL；若服务层日后把 `AND status = ...`
   去掉，复刻的 SQL 就成了没人维护的假证据。故用源码断言钉住它。

⚠️ 一处如实记录的**接口面偏离**（不是本文件要修的）
---------------------------------------------------
`test_exit_d_*` 会钉住两个**不同**的状态码，它们来自两条不同的守卫路径：

* `GET /assignments/{id}` 对非参与方返回 **404**（"不存在"，不泄漏存在性，PRD 9）；
* `POST /assignments/{id}/claim` 对非成员返回 **403**（`claim_assignment` 先
  `_get_or_404` 确认存在、再判组织成员，无权即 `AccessDeniedError` → 403）。

⇒ 拿一个委托 ID 去 `claim`，**403 与 404 可区分**，于是能反推该 ID 是否存在。
这与"非参与方 404 不泄漏存在性"的原则不一致，属**待评审项**：改它要动接口语义，
需要决策记录，**不在本切片单方面修改**。本文件把现状钉成可执行事实，避免它被
静默当作"已经一致"。
"""

from __future__ import annotations

import inspect
import os
import uuid
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import assignments as svc  # noqa: E402
from app.modules.entrust.access import utcnow_naive  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 会话工厂，且 `ENTRUST_ENABLED=True`。

    与 `test_entrust_assignments.py` 的 `env` 等价（本支线 API 测试都需要为
    组织/成员/授权播种，而 `conftest.client` 不暴露引擎）。
    """
    from app.core.config import get_settings
    from app.main import app
    from app.models import Base
    from app.modules.auth.router import get_db
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", True)

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture()
def file_db(tmp_path):
    """**文件型** SQLite，交出可造多个独立连接的会话工厂。

    为什么不能用内存库 + `StaticPool`：那种配置下所有 Session **共用一个连接**，
    于是"两个事务同时存在"这件事根本无法表达 —— 交错读写会退化成串行，
    测出来的"并发安全"是假的。文件型库让每个 Session 各自持有连接，
    才能复现"两个 worker 都读到了 `submitted`、然后先后去写"这一真实时序。
    """
    from app.models import Base
    from migrate import apply_pending

    path = tmp_path / "s1-exit.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield SimpleNamespace(engine=engine, make_session=factory)
    finally:
        engine.dispose()


# ── helpers ───────────────────────────────────────────────────────────────


def _org(db, name="测试组织", status="active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": name, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager", status: str = "active") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, :s, :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "s": status, "c": utcnow_naive().strftime(_TS)},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str = '["entrust:view"]') -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " valid_from, valid_until, created_at) VALUES (:o, :u, :p, :s, NULL, NULL, :c)"
        ),
        {
            "o": org_id,
            "u": owner_id,
            "p": permissions,
            "s": "active",
            "c": utcnow_naive().strftime(_TS),
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


def _headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['access_token']}"}


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_via_api(client, owner: dict, **body) -> dict:
    """UI 的"建草稿"那一步。`Idempotency-Key` 是写操作的**必填**（缺 → 400）。"""
    payload = {"title": "钢材运输", **body}
    resp = client.post(
        "/api/v1/entrust/assignments",
        json=payload,
        headers={**_headers(owner), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit_via_api(client, owner: dict, created: dict, org_id: int) -> dict:
    """UI 的"提交"那一步：带 `org_id` + `expected_revision`（乐观锁）。"""
    resp = client.post(
        f"/api/v1/entrust/assignments/{created['assignment_id']}/submit",
        json={"org_id": org_id, "expected_revision": created["revision"]},
        headers={**_headers(owner), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _claim_via_api(client, actor: dict, assignment_id: int):
    return client.post(
        f"/api/v1/entrust/assignments/{assignment_id}/claim",
        headers={**_headers(actor), "Idempotency-Key": uuid.uuid4().hex},
    )


def _raw_claim(db, assignment_id: int, actor_id: int) -> int:
    """直接执行服务层 `claim_assignment` 内部的**那条条件 UPDATE**，返回 rowcount。

    为什么要复刻而不复用服务层函数：服务层在 UPDATE 之前有一道**前置状态检查**，
    它会把"并发竞争"提前拦掉，于是测到的其实是那道检查、而非数据库层的互斥。
    要证明"两个 worker 同时通过检查后，仍只有一个能写成"，就必须绕开前置检查、
    只看条件 UPDATE 自己的 `rowcount`。

    复刻的代价是**可能漂移** ⇒ 由 `test_claim_sql_is_still_conditional` 钉住。
    """
    ts = utcnow_naive().strftime(_TS)
    result = db.execute(
        text(
            "UPDATE ent_assignment SET status = :status, claimed_by = :uid, "
            "claimed_at = :ts, revision = revision + 1, updated_at = :ts "
            "WHERE id = :aid AND status = :from_status"
        ),
        {
            "status": svc.STATUS_CLAIMED,
            "uid": actor_id,
            "ts": ts,
            "aid": assignment_id,
            "from_status": svc.STATUS_SUBMITTED,
        },
    )
    db.commit()
    return int(result.rowcount or 0)


def _fresh_read(factory, assignment_id: int) -> dict | None:
    """**换一个全新会话**读终局（DR-0002）。

    同一个会话里读"我刚写的那行"必然读得到（它有自己的身份映射与未提交快照），
    那不构成"重载后仍在"的证据。只有新会话重新发起查询，才是真的从存储读回来。
    """
    other = factory()
    try:
        return svc.get_assignment(other, assignment_id)
    finally:
        other.close()


def _scene(env):
    """一支最小但完整的场景：货主 + 组织 A（两个经理）+ 无关组织 B（一个经理）。"""
    db = env.make_session()
    owner = _login(env.client, "shipper")
    mgr_a1 = _login(env.client, "mgr")
    mgr_a2 = _login(env.client, "mgr")
    mgr_b = _login(env.client, "mgr")

    org_a = _org(db, name="甲组织")
    org_b = _org(db, name="乙组织")
    _member(db, org_a, user_id=mgr_a1["user_id"], role="manager")
    _member(db, org_a, user_id=mgr_a2["user_id"], role="manager")
    _member(db, org_b, user_id=mgr_b["user_id"], role="manager")
    _entrust(db, org_a, owner_id=owner["user_id"])
    return SimpleNamespace(
        db=db, owner=owner, mgr_a1=mgr_a1, mgr_a2=mgr_a2, mgr_b=mgr_b, org_a=org_a, org_b=org_b
    )


# ── ① survives reload ─────────────────────────────────────────────────────


def test_exit_a_ui_created_assignment_survives_reload(env):
    """判据一：`A fresh UI-created assignment survives reload`。

    "UI-created" = 走 UI 的两步写调用（建草稿 → 提交）。"survives reload" =
    **换一个全新会话**重新查询仍然读得到，且关键字段一致 ——
    如果提交只改写了内存对象而没落库，这条会红。
    """
    s = _scene(env)
    created = _create_via_api(env.client, s.owner, cargo_summary="螺纹钢", quantity="800")
    submitted = _submit_via_api(env.client, s.owner, created, s.org_a)
    assert submitted["status"] == "submitted"

    # 换新会话直接读存储
    reloaded = _fresh_read(env.make_session, created["assignment_id"])
    assert reloaded is not None, "提交后的委托在**新会话**里读不到 —— 没有真正落库"
    assert reloaded["status"] == "submitted"
    assert reloaded["org_id"] == s.org_a
    assert reloaded["quantity"] == "800", "数量必须是字符串原样，不得被二次加工"
    assert reloaded["owner_user_id"] == s.owner["user_id"]
    assert reloaded["submitted_at"] is not None

    # 再走一次 HTTP（另一条独立的重载路径）：货主自己看详情
    detail = env.client.get(
        f"/api/v1/entrust/assignments/{created['assignment_id']}", headers=_headers(s.owner)
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "submitted"
    assert detail.json()["org_id"] == s.org_a


# ── ② appears in the correct queue ────────────────────────────────────────


def test_exit_b_appears_in_the_correct_queue_and_only_there(env):
    """判据二：`appears in the correct queue`。

    "正确"必须**双向**钉住：在目标组织的队列里**出现**，且在无关组织的队列里
    **不出现**。只断言前一半的话，一个"谁都能看到所有单"的实现也能通过 ——
    那正是最危险的错误（越权且不报错）。
    """
    s = _scene(env)
    created = _create_via_api(env.client, s.owner)
    _submit_via_api(env.client, s.owner, created, s.org_a)
    aid = created["assignment_id"]

    q_a = env.client.get(
        "/api/v1/entrust/assignments",
        params={"view": "org", "org_id": s.org_a},
        headers=_headers(s.mgr_a1),
    )
    assert q_a.status_code == 200, q_a.text
    ids_a = [item["assignment_id"] for item in q_a.json()["items"]]
    assert aid in ids_a, "提交的委托没有出现在目标组织的受理队列里"

    q_b = env.client.get(
        "/api/v1/entrust/assignments",
        params={"view": "org", "org_id": s.org_b},
        headers=_headers(s.mgr_b),
    )
    assert q_b.status_code == 200, q_b.text
    ids_b = [item["assignment_id"] for item in q_b.json()["items"]]
    assert aid not in ids_b, "委托泄漏到了无关组织 B 的队列"

    # 货主视角：自己的委托必须在（同一张单，三个视角，口径一致）
    q_owner = env.client.get(
        "/api/v1/entrust/assignments",
        params={"view": "owner"},
        headers=_headers(s.owner),
    )
    assert q_owner.status_code == 200, q_owner.text
    assert aid in [item["assignment_id"] for item in q_owner.json()["items"]]


# ── ③ can be claimed once ─────────────────────────────────────────────────


def test_exit_c_can_be_claimed_once(env):
    """判据三（API 层）：`can be claimed once`。

    ⚠️ 这是**串行**证据：第二个认领被服务层的**前置状态检查**挡掉（409）。
    并发下的证据见 `test_claim_race_*` —— 两者不可互相替代。
    """
    s = _scene(env)
    created = _create_via_api(env.client, s.owner)
    _submit_via_api(env.client, s.owner, created, s.org_a)
    aid = created["assignment_id"]

    first = _claim_via_api(env.client, s.mgr_a1, aid)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "claimed"
    assert first.json()["claimed_by"] == s.mgr_a1["user_id"]

    second = _claim_via_api(env.client, s.mgr_a2, aid)
    assert second.status_code == 409, f"第二次认领未被拒绝：{second.status_code} {second.text}"

    still = _fresh_read(env.make_session, aid)
    assert still is not None
    assert still["claimed_by"] == s.mgr_a1["user_id"], "第一次的认领被后来者覆盖了"
    assert still["status"] == "claimed"


def test_claim_race_only_one_writer_wins(file_db):
    """判据三（**数据库层**）：两个 worker 都通过前置检查，仍只有一个写成。

    时序刻意做成真实并发会发生的样子：

        A 读 → 看到 submitted（"我可以认领"）
        B 读 → 也看到 submitted（"我也可以认领"）   ← 前置检查此刻对两边都放行
        A 条件 UPDATE → rowcount=1
        B 条件 UPDATE → **rowcount=0**              ← 唯一的最后防线

    `rowcount=0` 就是"第二次写没有打中任何行"的机械证据。
    若服务层把那条 UPDATE 改成无条件（去掉 `AND status = :from_status`），
    这里会变成 1 —— 本用例随即变红。
    """
    db = file_db
    s1 = db.make_session()
    s2 = db.make_session()
    try:
        org = _org(s1)
        _member(s1, org, user_id=10, role="manager")
        _member(s1, org, user_id=11, role="manager")
        _entrust(s1, org, owner_id=1)
        a = svc.create_assignment(s1, owner_user_id=1, title="并发认领")
        submitted = svc.submit_assignment(
            s1,
            assignment_id=a["assignment_id"],
            actor_id=1,
            org_id=org,
            expected_revision=a["revision"],
        )
        assert submitted["status"] == "submitted"
        aid = submitted["assignment_id"]

        # 两个独立连接各自"看到" submitted —— 这就是并发窗口
        view_a = svc.get_assignment(s1, aid)
        view_b = svc.get_assignment(s2, aid)
        s1.commit()
        s2.commit()
        assert view_a is not None and view_a["status"] == svc.STATUS_SUBMITTED
        assert view_b is not None and view_b["status"] == svc.STATUS_SUBMITTED, (
            "两个 worker 必须都能读到 submitted，否则测的不是并发"
        )

        rc_a = _raw_claim(s1, aid, actor_id=10)
        rc_b = _raw_claim(s2, aid, actor_id=11)
        assert rc_a == 1, "第一个写入必须打中 1 行"
        assert rc_b == 0, "第二个写入打中了行 —— 条件 UPDATE 没有互斥住，并发认领会互相覆盖"

        # 终局：换新会话读（DR-0002）
        final = _fresh_read(db.make_session, aid)
        assert final is not None
        assert final["claimed_by"] == 10, "终局的 claimed_by 必须是先到者"
        assert final["status"] == svc.STATUS_CLAIMED
    finally:
        s1.close()
        s2.close()


def test_claim_race_loser_cannot_overwrite_via_service_layer(file_db):
    """并发失败者**经服务层**再试一次：必须被拒，且不得改写 claimed_by。

    这一条与上一条的差别：上一条测条件 UPDATE 本身，这一条测"整条服务层路径"
    在竞争之后的状态下是否仍然自洽（前置检查 + 条件更新两级都到位）。
    """
    db = file_db
    s1 = db.make_session()
    s2 = db.make_session()
    try:
        org = _org(s1)
        _member(s1, org, user_id=10, role="manager")
        _member(s1, org, user_id=11, role="manager")
        _entrust(s1, org, owner_id=1)
        a = svc.create_assignment(s1, owner_user_id=1, title="并发认领（服务层）")
        submitted = svc.submit_assignment(
            s1,
            assignment_id=a["assignment_id"],
            actor_id=1,
            org_id=org,
            expected_revision=a["revision"],
        )
        aid = submitted["assignment_id"]

        # 两边都先读到 submitted（模拟并发窗口）
        assert svc.get_assignment(s2, aid)["status"] == svc.STATUS_SUBMITTED
        s2.commit()

        won = svc.claim_assignment(s1, assignment_id=aid, actor_id=10)
        assert won["claimed_by"] == 10

        with pytest.raises(svc.AssignmentStateError):
            svc.claim_assignment(s2, assignment_id=aid, actor_id=11)

        final = _fresh_read(db.make_session, aid)
        assert final is not None and final["claimed_by"] == 10
    finally:
        s1.close()
        s2.close()


def test_claim_sql_is_still_conditional():
    """防漂移：服务层那条认领 UPDATE **必须仍带 `AND status = :from_status`**。

    `test_claim_race_*` 复刻了这条 SQL（为了绕开前置检查、测到数据库层互斥）。
    复刻品不会自动跟随服务层演化 ⇒ 用源码断言把两者绑在一起：
    服务层一旦改成无条件 UPDATE，这条立刻红，而不是让并发用例静默变成假证据。
    """
    source = inspect.getsource(svc.claim_assignment)
    assert "AND status = :from_status" in source, (
        "claim_assignment 的 UPDATE 不再是条件更新 —— 并发防覆盖的最后防线消失了"
    )
    assert "rowcount" in source, "认领必须按受影响行数判定成败，而不是假定写成功"


# ── ④ remains inaccessible to unrelated organization B ────────────────────


def test_exit_d_remains_inaccessible_to_unrelated_org_b(env):
    """判据四：`remains inaccessible to unrelated organization B`。

    三条通路逐一钉住（详情 / 队列 / 认领）。注意状态码**不是**同一个：
    * 详情 → 404（不泄漏存在性，PRD 9）；
    * 认领 → 403（`claim_assignment` 先确认存在、再判成员，无权即拒绝）。

    ⚠️ 通路三那条 403 是**修复后才成立**的：2026-09-16 之前它抛未处理异常 ⇒ 500
    （`_run_write` 的 `except` 漏了 `AccessDeniedError`）。也就是说，
    **本合同判据第四条在修复前根本无从判定** —— 它挡住的越权确实被挡住了，
    但返回的是一个无法归因的服务器错误，而不是一条可解释的拒绝。

    这个不一致会在拿 ID 探测时泄漏"该委托存在"，属**待评审的接口面偏离**，
    见本文件头部的说明 —— 本用例把它钉成事实，而不是当成"已经一致"。
    """
    s = _scene(env)
    created = _create_via_api(env.client, s.owner)
    _submit_via_api(env.client, s.owner, created, s.org_a)
    aid = created["assignment_id"]

    # 通路一：详情 → 404
    detail_b = env.client.get(f"/api/v1/entrust/assignments/{aid}", headers=_headers(s.mgr_b))
    assert detail_b.status_code == 404, f"乙组织看到了甲组织的委托：{detail_b.text}"

    # 通路二：队列不返回（乙组织自己的队列里不该有）
    q_b = env.client.get(
        "/api/v1/entrust/assignments",
        params={"view": "org", "org_id": s.org_b},
        headers=_headers(s.mgr_b),
    )
    assert q_b.status_code == 200, q_b.text
    assert aid not in [item["assignment_id"] for item in q_b.json()["items"]]

    # 通路三：认领 → 403（现状；见 docstring 的偏离说明）
    claim_b = _claim_via_api(env.client, s.mgr_b, aid)
    assert claim_b.status_code == 403, f"乙组织能认领甲组织的委托：{claim_b.text}"

    # 终局未被改动
    final = _fresh_read(env.make_session, aid)
    assert final is not None
    assert final["status"] == "submitted"
    assert final["claimed_by"] is None


def test_denied_submit_is_403_not_500(env):
    """无生效授权的提交 → **403**（不是 500）；这是与 `test_exit_d` 同源、走另一分支的回归。

    来源：`submit_assignment` 在"货主对该组织没有生效委托授权"时抛
    `AccessDeniedError`（`assignments.py`）。UI-07 的正常路径**不会**撞到它 ——
    组织清单来自 `GET /my-entrustments`，只列生效授权；但**竞态**会撞：
    页面打开后授权被撤销，用户仍然点了提交。

    修复前（2026-09-16 之前）这条路径抛的是**未处理异常** ⇒ HTTP 500 ——
    因为 `router._run_write` 的 `except` 只列了 `svc.AssignmentError`，
    而 `AccessDeniedError` 与它没有共同基类。前端 `assignmentWriteError`
    只能处理 403/404/409，于是用户看到的是"服务器错误"而不是"你没有授权"，
    且**无法自助**（重试永远失败、原因永远不明）。
    """
    s = _scene(env)
    created = _create_via_api(env.client, s.owner)

    # 对**没有授权**的乙组织提交（货主只授权过甲组织）
    resp = env.client.post(
        f"/api/v1/entrust/assignments/{created['assignment_id']}/submit",
        json={"org_id": s.org_b, "expected_revision": created["revision"]},
        headers={**_headers(s.owner), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 403, (
        f"无授权提交应为 403，实际 {resp.status_code}：{resp.text[:200]}"
    )
    assert resp.json()["detail"], "403 必须带可读原因（前端要把它展示给用户）"

    # 失败的提交不得留下半成品：状态与 org_id 都不许改
    still = _fresh_read(env.make_session, created["assignment_id"])
    assert still is not None
    assert still["status"] == "draft", "被拒的提交改动了状态"
    assert still["org_id"] is None, "被拒的提交写入了组织"
