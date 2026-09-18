"""费用行（S7-1 / §10.1 第 10 步 / 合同 S4 段第 9–10 条）。

本文件验的是**四件在数据上能分开的事**，不是"端点存在"：

1. **草稿不进合计**：登记出来是 `draft`，要显式确认才算数 —— 否则"记了一笔"
   就等于"这笔已入账"，而两者在业务上差一次确认。
2. ⭐ **争议行不进合计**（合同 S4 段第 10 条原文 `Keep disputed charges out of
   confirmed totals`）：确认 → 提争议之后，同一笔费用的合计必须**回落**。
3. ⭐ **`resolved` 一词决定不了是否计入**（HO 0918-2 裁定 Q2=B）：处置必须显式给出
   `counts_in_total`；`True` 时按 `resolution_amount` 计入、`False` 时不计入。
   ⇒ 两个方向各钉一条用例，否则"恒计入"或"恒不计入"都能满足其中一条。
4. **合计按「币种 × 收付方向」分开**（裁定 Q1=C）：跨币种**不相加**、应收与应付
   **不相加**。

负例覆盖：无 `entrust:settlement:create` ⇒ 403；**货主本人读 ⇒ 404**（费用行带对手方与
计费依据，是内部成本口径，与运力那一组同型 —— 没有货主旁路）；对 `draft` 提争议 ⇒ 409；
`revision` 过期 ⇒ 409；缺 `basis` / 数量与单位不成对 / `counts_in_total=True` 却没给
`final_amount` ⇒ 400；同键重放 ⇒ 幂等（不产生第二行）。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_TS = "2026-09-18 00:00:00"
#: 经理侧权限：结案/费用用本仓已有的 `entrust:settlement:create`（不新造权限码）。
MGR_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch","entrust:settlement:create"]'
)
#: 只有查看权限的成员 —— 用来钉"写要权限"这条负例。
VIEWER_PERMS = '["entrust:view"]'


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


def _seed(env, db, *, viewer_perms: str = VIEWER_PERMS):
    """组织 + 经理成员 + 一个只读成员 + 货主授权 + 已受理委托 + 一个局外人。"""
    manager = _login(env.client, "mgr")
    viewer = _login(env.client, "viewer")
    owner = _login(env.client, "shipper")
    outsider = _login(env.client, "other")
    res = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n,'active',:c)"),
        {"n": f"org-{uuid.uuid4().hex[:6]}", "c": _TS},
    )
    org = int(res.lastrowid or 0)
    for uid, role, _role_perms in (
        (manager["user_id"], "manager", MGR_PERMS),
        (viewer["user_id"], "member", viewer_perms),
    ):
        db.execute(
            text(
                "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
                "VALUES (:o, :u, :r, 'active', :c)"
            ),
            {"o": org, "u": uid, "r": role, "c": _TS},
        )
    res = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": owner["user_id"], "p": MGR_PERMS, "c": _TS},
    )
    eid = int(res.lastrowid or 0)
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, '钢材', '800.000', '吨', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "g": org, "t": "S7-1 费用行用例", "c": _TS},
    )
    aid = int(res.lastrowid or 0)
    db.commit()
    return manager, viewer, owner, outsider, org, eid, aid


def _record(env, user, *, aid: int, amount="12000", direction="payable", kind="freight", **over):
    body = {
        "direction": direction,
        "charge_kind": kind,
        "amount": amount,
        "basis": "S7-1 用例：按合同单价与结算口径",
        "currency": "CNY",
    }
    body.update(over)
    return env.client.post(
        f"/api/v1/entrust/assignments/{aid}/charges",
        json=body,
        headers=_headers(user, uuid.uuid4().hex),
    )


def _read(env, user, *, aid: int):
    return env.client.get(f"/api/v1/entrust/assignments/{aid}/charges", headers=_headers(user))


def _post(env, user, path: str, body: dict):
    return env.client.post(
        f"/api/v1/entrust{path}", json=body, headers=_headers(user, uuid.uuid4().hex)
    )


def _gate(env, user, *, aid: int) -> tuple:
    """返回 (分组合计 dict, counted_lines, excluded_lines)。"""
    body = _read(env, user, aid=aid).json()
    groups = {f"{g['currency']}/{g['direction']}": g for g in body["groups"]}
    return groups, body["counted_lines"], body["excluded_lines"]


# ───────────────────────── 1. 草稿不进合计 → 确认才进


def test_draft_is_excluded_until_confirmed(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)

    created = _record(env, manager, aid=aid, amount="12000")
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["status"] == "draft"
    assert row["amount"] == "12000.0000"
    assert row["currency"] == "CNY"
    assert row["revision"] == 1
    #: 未处置的行 `counts_in_total` 是 **None**（未知保持未知），不是 False
    assert row["counts_in_total"] is None

    groups, counted, excluded = _gate(env, manager, aid=aid)
    assert counted == 0 and excluded == 1
    #: 分组**仍然出现**（"这单有一条 CNY 应付、但还没确认"本身是可复核的信息），
    #: 只是计入为 0 —— 把整组藏起来会让"这单少了这一笔"没有出处。
    assert groups["CNY/payable"]["total"] == "0.0000"
    assert groups["CNY/payable"]["counted_lines"] == 0
    assert groups["CNY/payable"]["excluded_lines"] == 1

    ok = _post(env, manager, f"/charges/{row['charge_id']}/confirm", {"expected_revision": 1})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "confirmed"
    assert ok.json()["revision"] == 2

    groups, counted, excluded = _gate(env, manager, aid=aid)
    assert counted == 1 and excluded == 0
    assert groups["CNY/payable"]["total"] == "12000.0000"
    assert groups["CNY/payable"]["counted_lines"] == 1


# ───────────────────────── 2. 争议行不进合计（合同 §10 原文）


def test_disputed_charge_is_kept_out_of_totals(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    cid = _record(env, manager, aid=aid, amount="5000", direction="receivable").json()["charge_id"]
    _post(env, manager, f"/charges/{cid}/confirm", {"expected_revision": 1})
    _before, counted_before, _exc = _gate(env, manager, aid=aid)
    assert counted_before == 1

    dis = _post(env, manager, f"/charges/{cid}/dispute", {"reason": "客户说这笔不该收"})
    assert dis.status_code == 200, dis.text
    assert dis.json()["status"] == "disputed"
    assert dis.json()["disputed_reason"] == "客户说这笔不该收"

    groups, counted, excluded = _gate(env, manager, aid=aid)
    assert counted == 0 and excluded == 1
    assert groups["CNY/receivable"]["total"] == "0.0000", (
        "争议行必须**退出**合计 —— 否则合计把未定的钱算成已定"
    )
    assert groups["CNY/receivable"]["excluded_lines"] == 1


# ───────────────────────── 3. `resolved` 决定不了"是否计入"（裁定 Q2=B）


def test_resolved_counts_only_when_explicitly_flagged(env):
    """同一处置结果（认可）在两个 `counts_in_total` 值下必须给出**不同**的合计。"""
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    cid = _record(env, manager, aid=aid, amount="8000").json()["charge_id"]
    _post(env, manager, f"/charges/{cid}/confirm", {"expected_revision": 1})
    _post(env, manager, f"/charges/{cid}/dispute", {"reason": "口径待确认"})

    # ① 认可但**不计入**（例：本期挂账）
    out = _post(
        env,
        manager,
        f"/charges/{cid}/resolve",
        {"outcome": "accepted", "method": "双方口头确认，本期不入账", "counts_in_total": False},
    )
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "resolved"
    assert out.json()["counts_in_total"] is False
    _groups, counted, excluded = _gate(env, manager, aid=aid)
    assert counted == 0 and excluded == 1, "counts_in_total=False ⇒ 不进合计"


def test_resolved_adjusted_amount_is_counted(env):
    """调减 ⇒ 按 **`resolution_amount`** 计入（不是原 `amount`）。"""
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    cid = _record(env, manager, aid=aid, amount="8000").json()["charge_id"]
    _post(env, manager, f"/charges/{cid}/confirm", {"expected_revision": 1})
    _post(env, manager, f"/charges/{cid}/dispute", {"reason": "多算了 2000"})

    out = _post(
        env,
        manager,
        f"/charges/{cid}/resolve",
        {
            "outcome": "adjusted",
            "method": "按实际等候 4 小时重算",
            "counts_in_total": True,
            "final_amount": "6000",
            "evidence_ref": "attachment:12",
        },
    )
    assert out.status_code == 200, out.text
    row = out.json()
    assert row["resolution_outcome"] == "adjusted"
    assert row["resolution_amount"] == "6000.0000"
    assert row["resolution_evidence_ref"] == "attachment:12"
    assert row["resolved_by"] is not None
    assert row["resolved_at"] is not None

    groups, counted, _excluded = _gate(env, manager, aid=aid)
    assert counted == 1
    assert groups["CNY/payable"]["total"] == "6000.0000", "计入的必须是**处置后的**金额"


def test_rejected_charge_never_counts(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    cid = _record(env, manager, aid=aid, amount="3000").json()["charge_id"]
    _post(env, manager, f"/charges/{cid}/confirm", {"expected_revision": 1})
    _post(env, manager, f"/charges/{cid}/dispute", {"reason": "这笔不是本单的"})

    out = _post(
        env,
        manager,
        f"/charges/{cid}/resolve",
        {"outcome": "rejected", "method": "核对后确认属他单", "counts_in_total": False},
    )
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "rejected"
    _groups, counted, excluded = _gate(env, manager, aid=aid)
    assert counted == 0 and excluded == 1


# ───────────────────────── 4. 合计按「币种 × 方向」分开（裁定 Q1=C）


def test_totals_are_grouped_by_currency_and_direction(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    payloads = [
        ("receivable", "12000", "CNY"),
        ("payable", "7000", "CNY"),
        ("payable", "3000", "CNY"),
        ("payable", "1000", "USD"),
    ]
    for direction, amount, currency in payloads:
        row = _record(env, manager, aid=aid, amount=amount, direction=direction, currency=currency)
        assert row.status_code == 200, row.text
        _post(
            env,
            manager,
            f"/charges/{row.json()['charge_id']}/confirm",
            {"expected_revision": 1},
        )

    groups, counted, _excluded = _gate(env, manager, aid=aid)
    assert counted == 4
    assert set(groups) == {"CNY/receivable", "CNY/payable", "USD/payable"}
    assert groups["CNY/receivable"]["total"] == "12000.0000"
    assert groups["CNY/payable"]["total"] == "10000.0000"
    #: ⛔ 跨币种**不相加**（裁定 Q1=C）：USD 单独一组
    assert groups["USD/payable"]["total"] == "1000.0000"


def test_waiting_time_is_an_ordinary_charge_line(env):
    """裁定 Q3=A：`waiting_time` 用**普通费用行**，不建实体、不建计费引擎。"""
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    row = _record(
        env,
        manager,
        aid=aid,
        kind="waiting_time",
        amount="450",
        quantity="3",
        unit="小时",
        counterparty="贵港航运",
    )
    assert row.status_code == 200, row.text
    got = row.json()
    assert got["charge_kind"] == "waiting_time"
    assert got["quantity"] == "3.000"
    assert got["unit"] == "小时"


def test_unregistered_charge_kind_is_kept_as_is(env):
    """`charge_kind` 是自由字符串：未登记的取值**原样保留**，不兜底成别的类别。"""
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    row = _record(env, manager, aid=aid, kind="demurrage_extra")
    assert row.status_code == 200, row.text
    assert row.json()["charge_kind"] == "demurrage_extra"


# ───────────────────────── 5. 负例


def test_owner_cannot_read_charges(env):
    """费用行带对手方与计费依据 = 内部成本口径 ⇒ **货主本人也 404**（无货主旁路）。"""
    db = env.make_session()
    manager, _viewer, owner, _outsider, _org, _eid, aid = _seed(env, db)
    _record(env, manager, aid=aid)
    assert _read(env, owner, aid=aid).status_code == 404


def test_outsider_cannot_read_charges(env):
    db = env.make_session()
    manager, _viewer, _owner, outsider, _org, _eid, aid = _seed(env, db)
    _record(env, manager, aid=aid)
    assert _read(env, outsider, aid=aid).status_code == 404


def test_write_requires_settlement_permission(env):
    """权限**按 (组织, 货主) 对解析**（DR-0008），不是按组织、也不是按角色。

    同组织里另一位货主的授权若不含 `entrust:settlement:create`，
    成员**看得见**那张委托（组织级读判据放行）却**写不了** ⇒ **403**（不是 404：
    他看得见，只是无权）—— 这正是"两个维度分开判"的可观测后果。
    """
    db = env.make_session()
    manager, viewer, _owner, _outsider, org, _eid, aid = _seed(env, db)
    _record(env, manager, aid=aid)
    assert _read(env, viewer, aid=aid).status_code == 200

    owner2 = _login(env.client, "shipper2")
    db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": owner2["user_id"], "p": VIEWER_PERMS, "c": _TS},
    )
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, '钢材', '100.000', '吨', 'claimed', 1, :c, :c)"
        ),
        {"o": owner2["user_id"], "g": org, "t": "S7-1 只读授权用例", "c": _TS},
    )
    aid2 = int(res.lastrowid or 0)
    db.commit()

    assert _read(env, viewer, aid=aid2).status_code == 200
    denied = _record(env, viewer, aid=aid2)
    assert denied.status_code == 403, denied.text


def test_dispute_requires_confirmed_status(env):
    """对 `draft` 提争议 ⇒ 409（草稿本就不计入，"对草稿提争议"没有业务含义）。"""
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    cid = _record(env, manager, aid=aid).json()["charge_id"]
    resp = _post(env, manager, f"/charges/{cid}/dispute", {"reason": "还没确认就提异议"})
    assert resp.status_code == 409, resp.text


def test_stale_revision_conflicts(env):
    """`revision` 过期 ⇒ 409（不许后者静默覆盖前者）。"""
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    cid = _record(env, manager, aid=aid).json()["charge_id"]
    first = _post(env, manager, f"/charges/{cid}/confirm", {"expected_revision": 1})
    assert first.status_code == 200
    stale = _post(env, manager, f"/charges/{cid}/confirm", {"expected_revision": 1})
    assert stale.status_code == 409, stale.text


def test_basis_is_required(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    resp = _record(env, manager, aid=aid, basis="   ")
    assert resp.status_code == 422 or resp.status_code == 400, resp.text


def test_quantity_and_unit_must_come_in_pairs(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    resp = _record(env, manager, aid=aid, quantity="3")
    assert resp.status_code == 400, resp.text


def test_counts_in_total_true_requires_final_amount(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    cid = _record(env, manager, aid=aid).json()["charge_id"]
    _post(env, manager, f"/charges/{cid}/confirm", {"expected_revision": 1})
    _post(env, manager, f"/charges/{cid}/dispute", {"reason": "待核"})
    resp = _post(
        env,
        manager,
        f"/charges/{cid}/resolve",
        {"outcome": "accepted", "method": "口头确认", "counts_in_total": True},
    )
    assert resp.status_code == 400, resp.text


def test_unknown_direction_is_rejected(env):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    resp = _record(env, manager, aid=aid, direction="transfer")
    assert resp.status_code == 400, resp.text


def test_idempotent_replay_creates_one_row(env):
    """同键同体重放 ⇒ 返回首次响应，**不产生第二行**。"""
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    key = uuid.uuid4().hex
    body = {
        "direction": "payable",
        "charge_kind": "freight",
        "amount": "12000",
        "basis": "重放用例",
        "currency": "CNY",
    }
    url = f"/api/v1/entrust/assignments/{aid}/charges"
    first = env.client.post(url, json=body, headers=_headers(manager, key))
    second = env.client.post(url, json=body, headers=_headers(manager, key))
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["charge_id"] == second.json()["charge_id"]
    rows = _read(env, manager, aid=aid).json()["items"]
    assert len(rows) == 1, f"同键重放不该产生第二行，实际 {len(rows)} 行"


def test_empty_assignment_returns_empty_list_not_404(env):
    db = env.make_session()
    _manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    manager = _manager
    resp = _read(env, manager, aid=aid)
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []
    assert resp.json()["groups"] == []


def test_charges_are_404_when_entrust_disabled(env, monkeypatch):
    db = env.make_session()
    manager, _viewer, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    assert _read(env, manager, aid=aid).status_code == 404
    assert _record(env, manager, aid=aid).status_code == 404
