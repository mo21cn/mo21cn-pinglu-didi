"""结算版本与收付依据（S7-3 / §10.1 第 11 步 / 合同 S4 段第 11 条；HO 0918-2 裁定 Q5）。

本文件验的是**五件在数据上能分开的事**，不是"端点存在"：

1. **版本是快照**：生成版本时冻结"此刻计入合计"的费用行（草稿行不进），
   且**参数带当时的 `revision` 与计入金额** —— 费用行后来被改也不影响这一版当时算什么。
2. ⭐ **客户确认挂在"一个具体版本的行"上** ⇒ **旧确认替不了新版本过关**这一条
   是**结构**保证的，不是靠一条 if 判断维持的：`test_old_acceptance_cannot_carry_a_new_version`
   直接断言"v1 上仍留着 accepted，而 v2 上什么都没有，且财务状态不是 settled"。
3. ⭐ **"确认后修改相关费用必须形成新版本"是机制、不是口号**：
   `test_stale_version_is_flagged_after_charges_change` —— 改完费用后，哪怕没人动过 v1，
   派生也必须给出 `settlement_stale`。没有这一格，过期的 v1 会**看起来没问题**，
   结案会拿一份过期口径过关。
4. **对客投影是白名单**：`internal_total` / `payable` 行 / `direction` / `revision`
   一个都不出；断言的是"客户看到的东西里没有这些"，而不是"内部投影里删了几个键"。
5. **收付是独立事实**（Q5 第 4 条）：`mode` 恒 `labeled_sample`（不是入参）、
   `ref` 必填、只能挂在**已确认**的版本上、累计不得超过该方向合计。

`financial_status` 的四条判据（§5.3.2）逐条各钉一次，另加"四条全过 ⇒ settled"
（只钉单边的话，"恒 open"或"恒 settled"都能通过其中一条）。

负例覆盖：非货主确认 ⇒ **403**（看得见但这不是你能做的动作）｜局外人 ⇒ 404｜
重复确认 ⇒ 409｜未确认的版本上记收付 ⇒ 409｜批准非适用版本 ⇒ 409｜
跨币种 ⇒ 400｜没有计入行 ⇒ 400｜未知方向 / 缺 ref / 超收付 ⇒ 400。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_TS = "2026-09-18 00:00:00"
MGR_PERMS = (
    '["entrust:view","entrust:task:dispatch","entrust:settlement:create","entrust:quote:create"]'
)
VIEWER_PERMS = '["entrust:view"]'


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_charges.env` 同源）。"""
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
    """组织 + 经理 + 只读成员 + 货主授权 + 已受理委托 + 局外人。"""
    manager = _login(env.client, "mgr")
    viewer = _login(env.client, "viewer")
    owner = _login(env.client, "shipper")
    outsider = _login(env.client, "other")
    res = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
        {"n": f"org-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    org = int(res.lastrowid or 0)
    for uid, role in ((manager["user_id"], "manager"), (viewer["user_id"], "member")):
        db.execute(
            text(
                "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
                "VALUES (:o, :u, :r, 'active', :c)"
            ),
            {"o": org, "u": uid, "r": role, "c": _TS},
        )
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": owner["user_id"], "p": MGR_PERMS, "c": _TS},
    )
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, '钢材', '800.000', '吨', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "g": org, "t": "S7-3 结算用例", "c": _TS},
    )
    aid = int(res.lastrowid or 0)
    db.commit()
    return {
        "manager": manager,
        "viewer": viewer,
        "owner": owner,
        "outsider": outsider,
        "org_id": org,
        "aid": aid,
    }


def _charge(env, seeded, *, direction="payable", amount="12000", kind="freight", confirm=True):
    """经**服务层**造一条费用行（夹具；权限判定在 API 层，这里要看的是结算行为）。"""
    from app.modules.entrust import charges as charge_svc

    db = env.make_session()
    try:
        row = charge_svc.record_charge(
            db,
            assignment_id=seeded["aid"],
            direction=direction,
            charge_kind=kind,
            amount=amount,
            currency="CNY",
            basis="合同附件三·费率表",
            actor_id=seeded["manager"]["user_id"],
        )
        if confirm:
            row = charge_svc.confirm_charge(db, charge_id=row["charge_id"])
        return row
    finally:
        db.close()


def _open_case(env, seeded, *, status="open"):
    """直接插一条案件（夹具）：用来钉派生第 ③ 条判据（未关闭的案件）。"""
    db = env.make_session()
    try:
        res = db.execute(
            text(
                "INSERT INTO ent_exception "
                "(org_id, assignment_id, kind, title, severity, impact_kind, status, "
                " raised_by_user_id, source, raised_at, created_at, updated_at) "
                "VALUES (:o, :a, 'exception', '卸货短量待核', 'medium', 'informational', :st, "
                " :actor, 'manual', :c, :c, :c)"
            ),
            {
                "o": seeded["org_id"],
                "a": seeded["aid"],
                "st": status,
                "actor": seeded["manager"]["user_id"],
                "c": _TS,
            },
        )
        db.commit()
        return int(res.lastrowid or 0)
    finally:
        db.close()


def _post(env, user, path: str, body: dict | None = None):
    return env.client.post(path, json=body or {}, headers=_headers(user, f"k-{uuid.uuid4().hex}"))


def _create(env, user, aid: int) -> dict:
    resp = _post(env, user, f"/api/v1/entrust/assignments/{aid}/settlements")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _approve(env, user, sid: int) -> dict:
    resp = _post(env, user, f"/api/v1/entrust/settlements/{sid}/approve")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _confirm(env, user, sid: int, decision="accepted"):
    return _post(
        env, user, f"/api/v1/entrust/settlements/{sid}/customer-confirm", {"decision": decision}
    )


def _pay(env, user, sid: int, *, direction="payable", amount="12000", ref="水单-SAMPLE-001"):
    return _post(
        env,
        user,
        f"/api/v1/entrust/settlements/{sid}/payments",
        {"direction": direction, "amount": amount, "ref": ref},
    )


def _financial(env, user, aid: int) -> dict:
    resp = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/financial-status", headers=_headers(user)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _blocker_codes(payload: dict) -> list[str]:
    return [b["code"] for b in payload["blockers"]]


# ─────────────────────────────────────────── 1. 版本是快照


def test_version_snapshots_counted_charges_only(env):
    """草稿行**不进**快照；已确认的应收与应付分别进 `customer_total` / `internal_total`。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000", kind="freight")
    _charge(env, seeded, direction="payable", amount="62000", kind="supplier")
    _charge(env, seeded, direction="receivable", amount="9999", kind="waiting_time", confirm=False)

    version = _create(env, seeded["manager"], seeded["aid"])
    assert version["version_no"] == 1
    assert version["status"] == "draft"
    assert len(version["lines"]) == 2, f"草稿行不该进快照：{version['lines']}"
    assert version["customer_total"] == "100000.0000"
    assert version["internal_total"] == "62000.0000"
    # 快照自足：每条带 charge_id 与**当时**的 revision（`confirm_charge` 会把 revision 推到 2）
    assert all(line["charge_id"] and line["revision"] >= 1 for line in version["lines"])
    assert {line["direction"] for line in version["lines"]} == {"receivable", "payable"}


def test_version_is_immutable_after_charges_change(env):
    """再生成一次得到 v2，**v1 一个字不改**（旧确认因此保留）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    v1 = _create(env, seeded["manager"], seeded["aid"])

    _charge(env, seeded, direction="receivable", amount="5000", kind="waiting_time")
    v2 = _create(env, seeded["manager"], seeded["aid"])
    assert (v1["version_no"], v2["version_no"]) == (1, 2)

    items = env.client.get(
        f"/api/v1/entrust/assignments/{seeded['aid']}/settlements",
        headers=_headers(seeded["manager"]),
    ).json()
    assert items["total"] == 2
    assert items["applicable_settlement_id"] == v2["settlement_id"], "适用版本＝最大版本号"
    old = next(i for i in items["items"] if i["version_no"] == 1)
    assert old["customer_total"] == "100000.0000", "旧版本的快照被改写了"
    assert old["line_count"] == 1


# ─────────────────────────────────────────── 2. 确认链


def test_customer_confirm_requires_internal_approval(env):
    """`draft` 上的客户确认没有意义 —— 我们还没确认这是我们的口径。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded)
    version = _create(env, seeded["manager"], seeded["aid"])
    assert _confirm(env, seeded["owner"], version["settlement_id"]).status_code == 409


def test_only_the_customer_can_confirm(env):
    """⛔ 经理人不得代客户确认：组织成员（**看得见**这个版本）来做 ⇒ 403。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded)
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])

    denied = _confirm(env, seeded["manager"], version["settlement_id"])
    assert denied.status_code == 403, denied.text
    assert "客户" in denied.text
    # 同一个版本，经理**读得到**内部详情 —— 403 与 404 的区别是刻意的
    assert (
        env.client.get(
            f"/api/v1/entrust/settlements/{version['settlement_id']}",
            headers=_headers(seeded["manager"]),
        ).status_code
        == 200
    )


def test_customer_decision_is_recorded_once(env):
    """一版只确认一次：要改口径请出**新版本**（旧确认保留）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded)
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])

    first = _confirm(env, seeded["owner"], version["settlement_id"])
    assert first.status_code == 200, first.text
    assert first.json()["customer_decision"] == "accepted"
    assert first.json()["customer_confirmed_by"] == seeded["owner"]["user_id"]

    again = _confirm(env, seeded["owner"], version["settlement_id"], decision="rejected")
    assert again.status_code == 409, again.text


def test_only_applicable_version_can_be_approved(env):
    """确认一个已被取代的版本，会让「适用版本批准了吗」有两个答案 ⇒ 409。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded)
    v1 = _create(env, seeded["manager"], seeded["aid"])
    _charge(env, seeded, direction="receivable", amount="7000", kind="waiting_time")
    _create(env, seeded["manager"], seeded["aid"])

    stale_approve = _post(
        env, seeded["manager"], f"/api/v1/entrust/settlements/{v1['settlement_id']}/approve"
    )
    assert stale_approve.status_code == 409, stale_approve.text


# ─────────────────────────────────────────── 3. 旧确认替不了新版本（Q5 的核心）


def test_old_acceptance_cannot_carry_a_new_version(env):
    """⭐ v1 上仍留着 accepted，而 v2 上什么都没有 ⇒ 财务状态**不能**是 settled。

    这一条不是"再判一次状态"，而是断言**结构上的位置**：
    确认挂在 v1 那一行上，所以 v2 天然是"未确认"的。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    _charge(env, seeded, direction="payable", amount="62000")
    v1 = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], v1["settlement_id"])
    _confirm(env, seeded["owner"], v1["settlement_id"])
    _pay(env, seeded["manager"], v1["settlement_id"], direction="receivable", amount="100000")
    _pay(env, seeded["manager"], v1["settlement_id"], direction="payable", amount="62000")
    assert _financial(env, seeded["manager"], seeded["aid"])["financial_status"] == "settled"

    # 费用事实变了 ⇒ 必须出新版本
    _charge(env, seeded, direction="receivable", amount="3000", kind="waiting_time")
    v2 = _create(env, seeded["manager"], seeded["aid"])

    v1_after = env.client.get(
        f"/api/v1/entrust/settlements/{v1['settlement_id']}", headers=_headers(seeded["manager"])
    ).json()
    assert v1_after["customer_decision"] == "accepted", "旧确认必须**保留**（历史不删除）"
    assert v1_after["customer_confirmed_at"] is not None
    assert v2["customer_confirmed_at"] is None, "新版本上不该凭空有确认"
    assert v2["customer_decision"] is None

    # v2 是 `draft` ⇒ 第一件要做的事是内部确认（而不是客户确认）
    draft_state = _financial(env, seeded["manager"], seeded["aid"])
    assert draft_state["financial_status"] == "open"
    assert "settlement_not_approved" in _blocker_codes(draft_state), draft_state["blockers"]

    # 内部确认之后，阻塞点变成"**客户还没确认这一版**"—— 这就是旧确认替不了新版本的落点
    _approve(env, seeded["manager"], v2["settlement_id"])
    state = _financial(env, seeded["manager"], seeded["aid"])
    assert state["financial_status"] == "open"
    assert "customer_not_confirmed" in _blocker_codes(state), state["blockers"]
    assert state["applicable_settlement"]["customer_decision"] is None, (
        "适用版本上不该有 v1 的客户决定"
    )


def test_stale_version_is_flagged_after_charges_change(env):
    """⭐ 适用版本与当前费用事实不一致 ⇒ `settlement_stale`。

    没有这一格，"确认后改费用必须出新版本"就只是一句口号：
    改完费用后 v1 的快照**看起来仍然没问题**，结案会拿一份过期口径过关。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    v1 = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], v1["settlement_id"])
    _confirm(env, seeded["owner"], v1["settlement_id"])

    # ⚠️ 先让"其它"阻塞项消失，好让这条判据单独可见
    assert "settlement_stale" not in _blocker_codes(
        _financial(env, seeded["manager"], seeded["aid"])
    )

    # 同一条费用行被**调减**（走 dispute → resolve）：id 没变、钱变了
    from app.modules.entrust import charges as charge_svc

    db = env.make_session()
    try:
        rows = charge_svc.list_charges(db, assignment_id=seeded["aid"])
        cid = rows[0]["charge_id"]
        charge_svc.dispute_charge(db, charge_id=cid, reason="客户认为吨位口径不对")
        charge_svc.resolve_charge(
            db,
            charge_id=cid,
            outcome="adjusted",
            method="按复查后的吨位重算",
            counts_in_total=True,
            final_amount="88000",
            actor_id=seeded["manager"]["user_id"],
        )
    finally:
        db.close()

    state = _financial(env, seeded["manager"], seeded["aid"])
    assert "settlement_stale" in _blocker_codes(state), (
        f"费用金额被改了，适用版本却没被标为过期：{state['blockers']}"
    )
    assert "settlement_stale" in str(state["blockers"])

    # 出新版本并重新取得客户确认 ⇒ 过期信号消失
    v2 = _create(env, seeded["manager"], seeded["aid"])
    assert v2["customer_total"] == "88000.0000"
    _approve(env, seeded["manager"], v2["settlement_id"])
    _confirm(env, seeded["owner"], v2["settlement_id"])
    after = _financial(env, seeded["manager"], seeded["aid"])
    assert "settlement_stale" not in _blocker_codes(after), after["blockers"]


def test_rejected_decision_also_blocks_settlement(env):
    """客户**不接受**也是一个已记录的决定，但它不构成"已批准"。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])
    _confirm(env, seeded["owner"], version["settlement_id"], decision="rejected")

    state = _financial(env, seeded["manager"], seeded["aid"])
    assert state["financial_status"] == "open"
    assert "customer_not_confirmed" in _blocker_codes(state)


# ─────────────────────────────────────────── 4. 对客投影是白名单


def test_customer_view_hides_internal_cost(env):
    """⭐ 客户只看对客费用：`payable` 行与 `internal_total` 一个都不出。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000", kind="freight")
    _charge(env, seeded, direction="payable", amount="62000", kind="supplier")
    version = _create(env, seeded["manager"], seeded["aid"])

    view = env.client.get(
        f"/api/v1/entrust/settlements/{version['settlement_id']}/customer-view",
        headers=_headers(seeded["owner"]),
    )
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["total"] == "100000.0000", "对客口径＝应收合计"
    assert body["line_count"] == 1
    assert len(body["lines"]) == 1
    assert body["lines"][0]["charge_kind"] == "freight"
    assert "internal_total" not in body, "内部成本漏给客户了"
    assert "62000" not in view.text, f"内部金额出现在客户面上：{view.text}"
    for line in body["lines"]:
        assert "direction" not in line and "revision" not in line, (
            "客户面按**白名单**裁剪，不是删几个键 —— 这两个键本就不该出现"
        )


def test_manager_can_preview_the_customer_view(env):
    """经理也放行这条通道，好**预览客户看到的东西**（投影里没有内部字段）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    _charge(env, seeded, direction="payable", amount="62000")
    version = _create(env, seeded["manager"], seeded["aid"])

    assert (
        env.client.get(
            f"/api/v1/entrust/settlements/{version['settlement_id']}/customer-view",
            headers=_headers(seeded["manager"]),
        ).status_code
        == 200
    )


def test_internal_channels_reject_the_owner(env):
    """内部通道**货主本人也 404**（版本带 internal_total）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded)
    version = _create(env, seeded["manager"], seeded["aid"])
    for path in (
        f"/api/v1/entrust/assignments/{seeded['aid']}/settlements",
        f"/api/v1/entrust/settlements/{version['settlement_id']}",
        f"/api/v1/entrust/assignments/{seeded['aid']}/financial-status",
    ):
        resp = env.client.get(path, headers=_headers(seeded["owner"]))
        assert resp.status_code == 404, f"{path} 对货主应当 404，实际 {resp.status_code}"


def test_outsider_gets_404_on_settlements(env):
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded)
    version = _create(env, seeded["manager"], seeded["aid"])
    for path in (
        f"/api/v1/entrust/assignments/{seeded['aid']}/settlements",
        f"/api/v1/entrust/settlements/{version['settlement_id']}",
        f"/api/v1/entrust/settlements/{version['settlement_id']}/customer-view",
    ):
        assert env.client.get(path, headers=_headers(seeded["outsider"])).status_code == 404


# ─────────────────────────────────────────── 5. 收付依据


def test_payment_mode_is_labeled_sample_and_not_an_input(env):
    """⛔ `mode` 不是入参：能传 `live` 就等于让系统自称"资金已真实到账"。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="payable", amount="62000")
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])

    # 先试"让系统自称真实到账"：入参里没有这个字段，服务端一律合成样本
    forged = _post(
        env,
        seeded["manager"],
        f"/api/v1/entrust/settlements/{version['settlement_id']}/payments",
        {"direction": "payable", "amount": "1", "ref": "x", "mode": "live"},
    )
    assert forged.status_code == 200, forged.text
    assert forged.json()["mode"] == "labeled_sample", "mode 被调用方指定了"

    rest = _pay(
        env,
        seeded["manager"],
        version["settlement_id"],
        amount="61999",
        ref="水单-SAMPLE-002",
    )
    assert rest.status_code == 200, rest.text
    assert rest.json()["mode"] == "labeled_sample"
    assert rest.json()["recorded_by"] == seeded["manager"]["user_id"]
    assert rest.json()["occurred_at"] is None, "没给业务发生时间就该缺着，不用记录时间顶上"


def test_payment_requires_approved_version_and_ref(env):
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="payable", amount="62000")
    version = _create(env, seeded["manager"], seeded["aid"])

    draft = _pay(env, seeded["manager"], version["settlement_id"], amount="100")
    assert draft.status_code == 409, draft.text

    _approve(env, seeded["manager"], version["settlement_id"])
    no_ref = _post(
        env,
        seeded["manager"],
        f"/api/v1/entrust/settlements/{version['settlement_id']}/payments",
        {"direction": "payable", "amount": "100", "ref": "  "},
    )
    assert no_ref.status_code in (400, 422), no_ref.text


def test_payment_cannot_exceed_the_direction_total(env):
    """超出会让余额变负，而负数余额没有业务含义。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="payable", amount="62000")
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])

    assert _pay(env, seeded["manager"], version["settlement_id"], amount="60000").status_code == 200
    over = _pay(
        env, seeded["manager"], version["settlement_id"], amount="5000", ref="水单-SAMPLE-002"
    )
    assert over.status_code == 400, over.text


def test_unknown_direction_is_rejected(env):
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="payable", amount="62000")
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])
    bad = _pay(env, seeded["manager"], version["settlement_id"], direction="refund")
    assert bad.status_code == 400, bad.text


# ─────────────────────────────────────────── 6. financial_status 四条判据


def test_not_started_only_when_there_is_no_financial_fact(env):
    """`not_started` **只能**表示"确实还没有任何费用/结算/收付事实"。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    state = _financial(env, seeded["manager"], seeded["aid"])
    assert state["financial_status"] == "not_started"
    assert state["blockers"] == []


def test_draft_charge_blocks_settlement(env):
    """判据①：有草稿行 ⇒ 合计此刻不是最终口径。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000", confirm=False)
    state = _financial(env, seeded["manager"], seeded["aid"])
    assert state["financial_status"] == "open"
    assert "unsettled_charges" in _blocker_codes(state)


def test_open_case_blocks_settlement(env):
    """判据③：有未关闭的案件 ⇒ 未结（关闭后该条消失）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])
    _confirm(env, seeded["owner"], version["settlement_id"])
    _pay(env, seeded["manager"], version["settlement_id"], direction="receivable", amount="100000")
    assert _financial(env, seeded["manager"], seeded["aid"])["financial_status"] == "settled"

    case_id = _open_case(env, seeded)
    blocked = _financial(env, seeded["manager"], seeded["aid"])
    assert blocked["financial_status"] == "open"
    open_blocker = next(b for b in blocked["blockers"] if b["code"] == "open_cases")
    assert case_id in open_blocker["detail"]["case_ids"]

    db = env.make_session()
    try:
        db.execute(text("UPDATE ent_exception SET status = 'closed' WHERE id = :i"), {"i": case_id})
        db.commit()
    finally:
        db.close()
    assert _financial(env, seeded["manager"], seeded["aid"])["financial_status"] == "settled"


def test_unsettled_balance_blocks_settlement(env):
    """判据④：余额未结清 ⇒ 未结；余额为零**不等于**已结清（判据是全四条都不成立）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])
    _confirm(env, seeded["owner"], version["settlement_id"])

    partial = _financial(env, seeded["manager"], seeded["aid"])
    assert partial["financial_status"] == "open"
    balance = next(b for b in partial["balances"] if b["direction"] == "receivable")
    assert balance["outstanding"] == "100000.0000"

    _pay(env, seeded["manager"], version["settlement_id"], direction="receivable", amount="100000")
    done = _financial(env, seeded["manager"], seeded["aid"])
    assert done["financial_status"] == "settled", done["blockers"]
    assert done["blockers"] == []


def test_settled_requires_both_directions(env):
    """应收与应付**不相加**：只收客户的钱、没付供应商，仍是未结。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded, direction="receivable", amount="100000")
    _charge(env, seeded, direction="payable", amount="62000")
    version = _create(env, seeded["manager"], seeded["aid"])
    _approve(env, seeded["manager"], version["settlement_id"])
    _confirm(env, seeded["owner"], version["settlement_id"])
    _pay(env, seeded["manager"], version["settlement_id"], direction="receivable", amount="100000")

    state = _financial(env, seeded["manager"], seeded["aid"])
    assert state["financial_status"] == "open"
    payable = next(b for b in state["balances"] if b["direction"] == "payable")
    assert payable["outstanding"] == "62000.0000"


# ─────────────────────────────────────────── 7. 生成版本的负例


def test_no_counted_charges_means_no_version(env):
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    empty = _post(
        env, seeded["manager"], f"/api/v1/entrust/assignments/{seeded['aid']}/settlements"
    )
    assert empty.status_code == 400, empty.text

    _charge(env, seeded, direction="receivable", amount="1000", confirm=False)
    draft_only = _post(
        env, seeded["manager"], f"/api/v1/entrust/assignments/{seeded['aid']}/settlements"
    )
    assert draft_only.status_code == 400, "只有草稿行时也不该出结算版本"


def test_multi_currency_is_rejected(env):
    """一个版本只有一个币种；计入行跨币种 ⇒ 400（跨币种相加是明确不做的）。"""
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    from app.modules.entrust import charges as charge_svc

    db = env.make_session()
    try:
        for currency in ("CNY", "USD"):
            row = charge_svc.record_charge(
                db,
                assignment_id=seeded["aid"],
                direction="receivable",
                charge_kind="freight",
                amount="1000",
                currency=currency,
                basis="费率表",
                actor_id=seeded["manager"]["user_id"],
            )
            charge_svc.confirm_charge(db, charge_id=row["charge_id"])
    finally:
        db.close()

    resp = _post(env, seeded["manager"], f"/api/v1/entrust/assignments/{seeded['aid']}/settlements")
    assert resp.status_code == 400, resp.text
    assert "币种" in resp.text


def test_write_requires_settlement_permission(env):
    """只读成员能**读**版本链（组织成员），但**生成/确认**版本 ⇒ 403。

    ⚠️ 这条负例必须落在**另一个货主**的委托上：权限按 **(组织, 货主) 对**解析
    （DR-0008），同一货主的授权里已经带了 `settlement:create`，
    再给同一对插一条只读授权是**并集**、收不回权限 —— 那样构造出的 403 是假的。
    """
    db = env.make_session()
    try:
        seeded = _seed(env, db)
    finally:
        db.close()
    _charge(env, seeded)
    _create(env, seeded["manager"], seeded["aid"])
    # 本组织的委托：成员读得到版本链
    assert (
        env.client.get(
            f"/api/v1/entrust/assignments/{seeded['aid']}/settlements",
            headers=_headers(seeded["viewer"]),
        ).status_code
        == 200
    )

    # 另一货主：授权只给 `entrust:view` ⇒ 读得到（组织成员），写出不去
    owner2, aid2 = _view_only_assignment(env, seeded)
    assert _charge(env, {"aid": aid2, **seeded}) is not None
    denied = _post(env, seeded["viewer"], f"/api/v1/entrust/assignments/{aid2}/settlements")
    assert denied.status_code == 403, denied.text
    assert (
        env.client.get(
            f"/api/v1/entrust/assignments/{aid2}/settlements",
            headers=_headers(seeded["viewer"]),
        ).status_code
        == 200
    )
    assert owner2["user_id"] != seeded["owner"]["user_id"]


def _view_only_assignment(env, seeded, *, perms: str = VIEWER_PERMS) -> tuple[dict, int]:
    """另开一个货主 ＋ 一张已受理委托，其授权**不含** `settlement:create`。"""
    from app.modules.entrust import assignments as assign_svc

    db = env.make_session()
    try:
        owner2 = _login(env.client, "shipper2")
        db.execute(
            text(
                "INSERT INTO ent_entrustment "
                "(org_id, entrust_user_id, permissions, status, created_at) "
                "VALUES (:o, :u, :p, 'active', :c)"
            ),
            {"o": seeded["org_id"], "u": owner2["user_id"], "p": perms, "c": _TS},
        )
        db.commit()
        draft = assign_svc.create_assignment(
            db, owner_user_id=owner2["user_id"], title="另一货主的委托"
        )
        submitted = assign_svc.submit_assignment(
            db,
            assignment_id=draft["assignment_id"],
            actor_id=owner2["user_id"],
            org_id=seeded["org_id"],
            expected_revision=draft["revision"],
        )
        claimed = assign_svc.claim_assignment(
            db, assignment_id=submitted["assignment_id"], actor_id=seeded["manager"]["user_id"]
        )
        return owner2, int(claimed["assignment_id"])
    finally:
        db.close()
