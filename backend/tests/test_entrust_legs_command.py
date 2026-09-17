"""航段命令（建段 / 改段 / 版本历史）—— HO 2026-09-17 三条裁定的落地验。

裁定与判据的一一对应（每条至少一个用例，且**负例都要有**）
----------------------------------------------------------
① **谁能建段＝任意验收者/测试者** ⇒ 判据复用计划读通道那一份
   （`assert_can_view_assignment`：货主本人 或 所属组织成员）。
   用例：**货主 200** + **组织经理 200**（两个演示身份都能建）＋ **局外人 404**
   （⛔ 不是 403 —— 不泄漏"这张单存在"）。
② **不强制 公–水–公** ⇒ 用例直接把"两段都是 road"和"一个没登记过的 mode
   （`air`）"建进去，断言 **200** 且 `mode_label` **等于**原值
   （未知保持未知，不兜底成"公路"）。
③ **改段保留版本** ⇒ 用例建 1 版、改 1 版，然后**回读历史**断言
   *旧版的值仍在*（快照，不是"指向当前行"）——这是"留版本"唯一有意义的判据。

另外三类判据（都是本仓库反复踩过的）
------------------------------------
* **作用域自洽**：路径上的 `assignment_id` 必须**就是**该航段的所属委托，
  否则 404（A 单的编号不能改到 B 单的段上）；
* **冲突交给 DB 唯一键**：同 `seq` 建段 ⇒ 409（不是 400），且提示要**点出"改段"**
  —— "建段撞号"最常见的来意其实是"我想改那一段"；
* **幂等**：同键重放不产生第 2 段；缺键 ⇒ 400；开关关闭 ⇒ 404。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_TS = "2026-09-17 00:00:00"
ALL_PERMS = '["entrust:view","entrust:quote:create","entrust:task:dispatch"]'


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_assignment_plan.env` 同源）。"""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app
    from app.models import Base
    from app.modules.auth.router import get_db
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    settings = get_settings()
    monkeypatch.setattr(settings, "ENTRUST_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield type("Env", (), {"client": tc, "make_session": factory})()
    app.dependency_overrides.clear()
    engine.dispose()


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _seed(env, db):
    """组织 + 经理成员 + 货主授权 + 已受理委托单 + 一个局外人 + **第二张单**（作用域用）。"""
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    outsider = _login(env.client, "other")
    res = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
        {"n": f"org-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    org = int(res.lastrowid or 0)
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, 'manager', 'active', :c)"
        ),
        {"o": org, "u": manager["user_id"], "c": _TS},
    )
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": owner["user_id"], "p": ALL_PERMS, "c": _TS},
    )
    aids = []
    for title in ("南宁→贵港 钢材运输", "另一张单（作用域用）"):
        res = db.execute(
            text(
                "INSERT INTO ent_assignment "
                "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
                " revision, created_at, updated_at) "
                "VALUES (:o, :g, :t, '钢材', '800.000', '吨', 'claimed', 1, :c, :c)"
            ),
            {"o": owner["user_id"], "g": org, "t": title, "c": _TS},
        )
        aids.append(int(res.lastrowid or 0))
    db.commit()
    return manager, owner, outsider, org, aids[0], aids[1]


def _key() -> str:
    return uuid.uuid4().hex


def _create(client, hdr, aid, *, seq, mode, frm, to, note=None):
    body: dict = {"seq": seq, "mode": mode, "from_name": frm, "to_name": to}
    if note is not None:
        body["change_note"] = note
    return client.post(f"/api/v1/entrust/assignments/{aid}/legs", json=body, headers=hdr)


def _revisions(client, hdr, aid, leg_id):
    return client.get(f"/api/v1/entrust/assignments/{aid}/legs/{leg_id}/revisions", headers=hdr)


# ── ① 谁能建段：参与方（两个演示身份都能建；局外人 404）──────────────────────


def test_owner_can_create_leg(env):
    """⭐ 裁定①前半：**货主本人**能建段（这条通道有货主旁路，与运力那组相反）。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    r = _create(
        env.client, _headers(owner, _key()), aid, seq=1, mode="road", frm="厂区", to="南宁港"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seq"] == 1
    assert body["mode"] == "road"
    assert body["mode_label"] == "公路"
    assert body["from_name"] == "厂区"
    assert body["to_name"] == "南宁港"
    # 建段即写第 1 版历史 —— "留版本"从第一行就成立，不是从第一次改才开始。
    assert body["revision_no"] == 1


def test_org_manager_can_create_leg(env):
    """⭐ 裁定①后半：**组织经理**也能建段（"任意验收者/测试者"两个演示身份都通）。"""
    with env.make_session() as db:
        mgr, _owner, _out, _org, aid, _aid2 = _seed(env, db)
    r = _create(env.client, _headers(mgr, _key()), aid, seq=1, mode="water", frm="A", to="B")
    assert r.status_code == 200, r.text
    assert r.json()["mode_label"] == "内河"


def test_non_party_gets_404_not_403(env):
    """⛔ 裁定①的边界：**不是**"任何登录用户"。非参与方 404（不泄漏存在性）。"""
    with env.make_session() as db:
        _mgr, _owner, outsider, _org, aid, _aid2 = _seed(env, db)
    r = _create(env.client, _headers(outsider, _key()), aid, seq=1, mode="road", frm="A", to="B")
    assert r.status_code == 404, r.text
    # 且**一条都没写进去**：404 必须是"什么都没发生"，不是"写完了才说没权限"。
    with env.make_session() as db:
        assert db.execute(text("SELECT COUNT(*) FROM ent_leg")).first()[0] == 0
        assert db.execute(text("SELECT COUNT(*) FROM ent_leg_revision")).first()[0] == 0


# ── ② 不强制 公–水–公 ───────────────────────────────────────────────────────


def test_two_road_legs_are_accepted(env):
    """⭐ 裁定②：**不强制 公–水–公**。两段都是 road 必须能存进来。

    反例意义：若写入口把它焊成"必须三段且 road/water/road"，这条会红 ——
    而 §10.1 第 4 步要的是"能展示方案"，不是"只能有一种方案"。
    """
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    assert (
        _create(
            env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="road", frm="A", to="B"
        ).status_code
        == 200
    )
    assert (
        _create(
            env.client, {**h, "Idempotency-Key": _key()}, aid, seq=2, mode="road", frm="B", to="C"
        ).status_code
        == 200
    )


def test_unregistered_mode_is_accepted_and_label_equals_raw(env):
    """⭐ 裁定②：`mode` **不是枚举**。未登记的取值（`air`）能建，且标签**等于原值**。

    "未知保持未知"是本支线反复在防的那类静默降级：兜底成"公路"会让界面显示一个
    **系统从没被告诉过的事实**。
    """
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    r = _create(env.client, _headers(owner, _key()), aid, seq=1, mode="air", frm="X", to="Y")
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "air"
    assert r.json()["mode_label"] == "air", "未登记的 mode 必须原样回显，不得兜底成'公路'"


def test_blank_mode_is_400(env):
    """结构完整性**仍要**：空白 mode 不是"某种未知方式"，是没写。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    r = _create(env.client, _headers(owner, _key()), aid, seq=1, mode="   ", frm="A", to="B")
    assert r.status_code == 400, r.text


def test_blank_from_name_is_422_or_400(env):
    """起终点非空：pydantic 的 `min_length=1` 拦不住 `" "` ⇒ 服务层去空白后再拦。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    r = _create(env.client, _headers(owner, _key()), aid, seq=1, mode="road", frm=" ", to="B")
    assert r.status_code == 400, r.text


def test_seq_zero_is_rejected(env):
    """`seq` 自 1 起是**读侧判据**（按 seq 排序即方案顺序）⇒ 0 与负数没有语义。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    r = _create(env.client, _headers(owner, _key()), aid, seq=0, mode="road", frm="A", to="B")
    assert r.status_code in (400, 422), r.text


# ── ③ 改段留版本 ───────────────────────────────────────────────────────────


def test_update_keeps_old_revision_as_snapshot(env):
    """⭐⭐ 裁定③的核心判据：**旧版的值仍在历史里**，且是快照、不是"指向当前行"。

    这一条同时排掉两种假实现：
    * 若改段是 `UPDATE` 掉旧行（不留历史）⇒ 历史只剩 1 版，红；
    * 若历史行"回连" `ent_leg` 读当前值 ⇒ 两版的值会**相同**（都变成新值），红。
    """
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    created = _create(
        env.client,
        {**h, "Idempotency-Key": _key()},
        aid,
        seq=1,
        mode="road",
        frm="厂区",
        to="南宁港",
        note="初次落方案",
    )
    assert created.status_code == 200, created.text
    leg_id = created.json()["leg_id"]

    patched = env.client.patch(
        f"/api/v1/entrust/assignments/{aid}/legs/{leg_id}",
        json={"to_name": "贵港港", "change_note": "改到达港"},
        headers={**h, "Idempotency-Key": _key()},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["to_name"] == "贵港港"
    assert patched.json()["revision_no"] == 2, "改一次应当是第 2 版（建段算第 1 版）"

    hist = _revisions(env.client, h, aid, leg_id)
    assert hist.status_code == 200, hist.text
    rows = hist.json()
    assert [r["revision_no"] for r in rows] == [1, 2], "版本按 revision_no 升序"
    assert [r["change_kind"] for r in rows] == ["created", "updated"]
    # ⭐ 快照：第 1 版记的仍是**当时**的"南宁港"，不被后来那次改动改写。
    assert rows[0]["to_name"] == "南宁港"
    assert rows[0]["change_note"] == "初次落方案"
    assert rows[1]["to_name"] == "贵港港"
    assert rows[1]["change_note"] == "改到达港"
    # 历史行也带标签（未登记 mode 同样原样回显）—— 读侧与写响应同一份口径。
    assert rows[0]["mode_label"] == "公路"


def test_update_seq_change_is_recorded_in_history(env):
    """改 `seq` 也要留痕：历史里能看到"这一版当时排在第几位"。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    leg_id = _create(
        env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="road", frm="A", to="B"
    ).json()["leg_id"]
    assert (
        env.client.patch(
            f"/api/v1/entrust/assignments/{aid}/legs/{leg_id}",
            json={"seq": 3},
            headers={**h, "Idempotency-Key": _key()},
        ).status_code
        == 200
    )
    rows = _revisions(env.client, h, aid, leg_id).json()
    assert [r["seq"] for r in rows] == [1, 3]


def test_update_without_fields_is_400(env):
    """空 PATCH 是**请求组错了**，不是一次改动 —— 静默成功会让调用方以为改过了。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    leg_id = _create(
        env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="road", frm="A", to="B"
    ).json()["leg_id"]
    r = env.client.patch(
        f"/api/v1/entrust/assignments/{aid}/legs/{leg_id}",
        json={},
        headers={**h, "Idempotency-Key": _key()},
    )
    assert r.status_code == 400, r.text


def test_update_with_same_values_is_400_and_adds_no_revision(env):
    """值没变 ⇒ 400，且**历史里不许**多出一版（版本历史是给人读"改过什么"的）。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    leg_id = _create(
        env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="road", frm="A", to="B"
    ).json()["leg_id"]
    r = env.client.patch(
        f"/api/v1/entrust/assignments/{aid}/legs/{leg_id}",
        json={"to_name": "B"},
        headers={**h, "Idempotency-Key": _key()},
    )
    assert r.status_code == 400, r.text
    assert len(_revisions(env.client, h, aid, leg_id).json()) == 1


def test_update_seq_conflict_is_409(env):
    """改 `seq` 撞到**别的段** ⇒ 409（由 DB 唯一键判，不"先查后写"）。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    first = _create(
        env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="road", frm="A", to="B"
    ).json()["leg_id"]
    assert (
        _create(
            env.client, {**h, "Idempotency-Key": _key()}, aid, seq=2, mode="water", frm="B", to="C"
        ).status_code
        == 200
    )
    r = env.client.patch(
        f"/api/v1/entrust/assignments/{aid}/legs/{first}",
        json={"seq": 2},
        headers={**h, "Idempotency-Key": _key()},
    )
    assert r.status_code == 409, r.text


# ── 冲突 / 作用域 / 幂等 / 开关 ────────────────────────────────────────────


def test_duplicate_seq_create_is_409_and_points_at_update(env):
    """同 `seq` 建段 ⇒ 409，且提示要**点出"改段"**（最常见的来意其实是"我想改那段"）。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    assert (
        _create(
            env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="road", frm="A", to="B"
        ).status_code
        == 200
    )
    r = _create(
        env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="water", frm="C", to="D"
    )
    assert r.status_code == 409, r.text
    assert "改段" in r.text, "提示要引导到改段命令，否则调用方只会反复重试建段"


def test_leg_of_another_assignment_is_404(env):
    """⭐ 作用域自洽：A 单的路径不能改到 B 单的段上（路径上的单号必须参与判定）。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, aid2 = _seed(env, db)
    h = _headers(owner)
    leg_id = _create(
        env.client, {**h, "Idempotency-Key": _key()}, aid, seq=1, mode="road", frm="A", to="B"
    ).json()["leg_id"]
    # 用**另一张单**的编号去改它 / 读它的历史 ⇒ 都必须是 404
    assert (
        env.client.patch(
            f"/api/v1/entrust/assignments/{aid2}/legs/{leg_id}",
            json={"to_name": "C"},
            headers={**h, "Idempotency-Key": _key()},
        ).status_code
        == 404
    )
    assert _revisions(env.client, h, aid2, leg_id).status_code == 404
    # 且**什么都没变**（404 不能是"改完了才说对象不属于这张单"）
    rows = _revisions(env.client, h, aid, leg_id).json()
    assert len(rows) == 1 and rows[0]["to_name"] == "B"


def test_replaying_same_key_creates_only_one_leg(env):
    """幂等：同键重放**不产生第 2 段**（否则"重试一次"就多出一段路）。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    key = _key()
    h = _headers(owner, key)
    first = _create(env.client, h, aid, seq=1, mode="road", frm="A", to="B")
    second = _create(env.client, h, aid, seq=1, mode="road", frm="A", to="B")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json(), "同键重放必须回放同一份成功响应"
    with env.make_session() as db:
        assert db.execute(text("SELECT COUNT(*) FROM ent_leg")).first()[0] == 1
        assert db.execute(text("SELECT COUNT(*) FROM ent_leg_revision")).first()[0] == 1


def test_missing_idempotency_key_is_400(env):
    """写端点必须带幂等键（缺 ⇒ 400，与全支线一致）。"""
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    r = _create(env.client, _headers(owner), aid, seq=1, mode="road", frm="A", to="B")
    assert r.status_code == 400, r.text


def test_new_legs_endpoints_404_when_entrust_disabled(env, monkeypatch):
    """支线开关关闭 ⇒ 三条新端点一律 404（与全支线一致，不是 403/500）。"""
    from app.core.config import get_settings

    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    h = _headers(owner, _key())
    assert _create(env.client, h, aid, seq=1, mode="road", frm="A", to="B").status_code == 404
    assert (
        env.client.get(
            f"/api/v1/entrust/assignments/{aid}/legs/1/revisions", headers=_headers(owner)
        ).status_code
        == 404
    )


def test_plan_read_model_sees_the_new_leg(env):
    """建段之后，**读模型立刻看得到**（写与读不是两套数据）。

    这条把"命令落地了但读侧看不到"这种最没意义的成果挡掉。
    """
    with env.make_session() as db:
        _mgr, owner, _out, _org, aid, _aid2 = _seed(env, db)
    h = _headers(owner)
    assert (
        _create(
            env.client,
            {**h, "Idempotency-Key": _key()},
            aid,
            seq=2,
            mode="water",
            frm="南宁港",
            to="贵港港",
        ).status_code
        == 200
    )
    plan = env.client.get(f"/api/v1/entrust/assignments/{aid}/plan", headers=h)
    assert plan.status_code == 200, plan.text
    legs = plan.json()["legs"]
    assert len(legs) == 1
    assert legs[0]["seq"] == 2 and legs[0]["mode_label"] == "内河"
