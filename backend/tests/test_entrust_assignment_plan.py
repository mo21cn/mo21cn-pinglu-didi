"""S3 运输计划读模型（BP-03 第 1 条；合同 §10.1 第 4 步）。

合同 §10.1 第 4 步原文：`Show the road–water–road plan and required task prerequisites.`

这一片验的**不是"端点存在"**，而是四件在数据上能分开的事：

1. **段是数据、不是文案**：航段按 `seq` 排序返回，段数就是行数 ——
   服务端**不产出**"三段""公路—内河—公路"这类结论性文案。所以用例断言的是
   "3 行、顺序 1/2/3、每行的 mode 与起终点"，不是"返回了那句中文"。
2. ⭐ **这条通道给货主本人放行**（与运力 / 合同两组**相反**）。判据是
   "这条通道上有没有内部信息"，不是"是不是客户"：航段是客户自己交进来的起讫路线、
   任务标题与前置不含内部成本口径。用例把两个方向**各钉一次**（货主 200 / 局外人 404），
   否则下一个加端点的人只能靠猜该往哪边靠。
3. **空计划不是错误**：`legs` 为空回 `[]` + 200。把"这张委托还没结构化计划"报成 4xx，
   会让它与"端点坏了"在客户端长得一样。
4. **未知保持未知**：未登记的 `mode` ⇒ `mode_label` **等于** `mode` 本身（不兜底成"公路"）；
   字段面**逐键锁定**（航段与任务行都 `sorted(keys) ==`）—— 本读模型只回答
   "计划与前置"，多带一个字段就红（照抄 `workbench._load_tasks` 的先例：
   它带 `precondition_task_id`、**不带** `required_evidence`）。

⚠️ 本文件的航段仍**直接 INSERT**（不调建段命令）—— 但理由已经变了：
建段命令**已经落地**（`legs.create_leg` / `POST …/legs`，见 `legs_api.py`；
口径＝HO 2026-09-17 裁定）。之所以不改成"先调命令再读"：本文件验的是**读模型**，
而直接 INSERT 更能说明"投影对**任何来源**的行都给同一份形状"——
包括种子脚本 `seed_entrust_canonical.py` 铺的那些行。
⇒ 读到 `INSERT INTO ent_leg` 时**不要**以为漏了命令；命令的行为由
`test_entrust_legs_command.py` 单独验（含建段/改段/版本历史三类）。
⚠️ 直接 INSERT **不写** `ent_leg_revision`（append-only 版本表）——
这些行模拟的是"命令落地之前就存在的航段"，版本历史只对**经命令产生的**改动成立。
读模型不碰版本表，所以这个差别对上面的断言无影响。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.entrust import plan as plan_svc

_TS = "2026-09-17 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch"]'
)

#: canonical 的三段（`seed_entrust_canonical.py` 的 `LEG_SPECS` 逐字同源）
LEG_SPECS = (
    (1, "road", "厂区", "南宁港"),
    (2, "water", "南宁港", "贵港港"),
    (3, "road", "贵港港", "卸货地"),
)


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 独立内存库（与 `test_entrust_capacity_confirmation.env` 同源）。"""
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


def _seed(env, db, *, entrustment_perms: str = ALL_PERMS):
    """组织 + 经理成员 + 货主授权 + 已受理委托单 + 一个局外人。"""
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
    res = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status, created_at) "
            "VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org, "u": owner["user_id"], "p": entrustment_perms, "c": _TS},
    )
    eid = int(res.lastrowid or 0)
    res = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, '钢材', '800.000', '吨', 'claimed', 1, :c, :c)"
        ),
        {"o": owner["user_id"], "g": org, "t": "南宁→贵港 钢材运输", "c": _TS},
    )
    aid = int(res.lastrowid or 0)
    db.commit()
    return manager, owner, outsider, org, eid, aid


def _add_leg(db, *, aid: int, seq: int, mode: str, from_name: str, to_name: str) -> int:
    """直接 INSERT —— `ent_leg` 没有服务层建段函数（见模块文档）。"""
    res = db.execute(
        text(
            "INSERT INTO ent_leg "
            "(assignment_id, seq, mode, from_name, to_name, created_at, updated_at) "
            "VALUES (:a, :s, :m, :f, :t, :c, :c)"
        ),
        {"a": aid, "s": seq, "m": mode, "f": from_name, "t": to_name, "c": _TS},
    )
    db.commit()
    return int(res.lastrowid or 0)


def _add_legs_canonical(db, *, aid: int) -> None:
    for seq, mode, from_name, to_name in LEG_SPECS:
        _add_leg(db, aid=aid, seq=seq, mode=mode, from_name=from_name, to_name=to_name)


def _add_task(env, user, *, aid: int, task_type: str, title: str, **over):
    body: dict = {"task_type": task_type, "title": title}
    body.update(over)
    return env.client.post(
        f"/api/v1/entrust/assignments/{aid}/tasks",
        json=body,
        headers=_headers(user, uuid.uuid4().hex),
    )


def _read_plan(env, user, *, aid: int):
    return env.client.get(f"/api/v1/entrust/assignments/{aid}/plan", headers=_headers(user))


# ───────────────────────── 1. 正例：段是数据、按 seq 排序


def test_plan_returns_legs_in_seq_order(env):
    """三段按 `seq` 顺序返回；段数就是行数（服务端不产出"三段"这类文案）。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    # 故意**乱序**插入：判据是"按 seq 读"，不是"按写入顺序读"
    _add_leg(db, aid=aid, seq=3, mode="road", from_name="贵港港", to_name="卸货地")
    _add_leg(db, aid=aid, seq=1, mode="road", from_name="厂区", to_name="南宁港")
    _add_leg(db, aid=aid, seq=2, mode="water", from_name="南宁港", to_name="贵港港")

    resp = _read_plan(env, manager, aid=aid)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["assignment_id"] == aid
    assert [leg["seq"] for leg in body["legs"]] == [1, 2, 3]
    assert [(leg["mode"], leg["from_name"], leg["to_name"]) for leg in body["legs"]] == [
        ("road", "厂区", "南宁港"),
        ("water", "南宁港", "贵港港"),
        ("road", "贵港港", "卸货地"),
    ]
    # 标签是**展示**层的事，但必须与 `plan.MODE_LABELS` 同一份表
    assert [leg["mode_label"] for leg in body["legs"]] == ["公路", "内河", "公路"]
    # 响应里**没有**"三段"这种结论性字段 —— 段数是行的属性
    assert "leg_count" not in body
    assert "leg_shape" not in body


def test_unregistered_mode_label_falls_back_to_raw_value(env):
    """未登记的 `mode` ⇒ `mode_label` **等于** `mode` 本身（不兜底成"公路"）。"""
    db = env.make_session()
    manager, *_rest, aid = (*_seed(env, db),)
    _add_leg(db, aid=aid, seq=1, mode="air", from_name="A", to_name="B")

    body = _read_plan(env, manager, aid=aid).json()
    assert body["legs"][0]["mode"] == "air"
    assert body["legs"][0]["mode_label"] == "air"
    # `mode_label` 只由标签表决定，不因未知而消失
    assert plan_svc.mode_label("air") == "air"


# ───────────────────────── 2. ⭐ 取向：这条通道给货主放行


def test_plan_is_visible_to_owner_self(env):
    """⭐ 与运力 / 合同两组**相反**：货主本人**看得见**自己这单的计划。

    理由不是"客户应该看得见"，而是**这条通道上没有内部信息** ——
    航段是客户自己交进来的起讫路线，任务标题与前置里没有承运人 / 单价 / 缺口。
    运力那组带内部成本口径，所以那组一律不给货主放行。判据是数据，不是身份。
    """
    db = env.make_session()
    manager, owner, _outsider, _org, _eid, aid = _seed(env, db)
    _add_legs_canonical(db, aid=aid)

    for who, label in ((manager, "经理（组织成员）"), (owner, "货主本人")):
        resp = _read_plan(env, who, aid=aid)
        assert resp.status_code == 200, f"{label} 应当可见：{resp.text}"
        assert len(resp.json()["legs"]) == 3


def test_plan_non_party_gets_404(env):
    """非参与方 ⇒ 404（不区分"不存在"与"无权知晓"）。"""
    db = env.make_session()
    _manager, _owner, outsider, _org, _eid, aid = _seed(env, db)
    _add_legs_canonical(db, aid=aid)

    resp = _read_plan(env, outsider, aid=aid)
    assert resp.status_code == 404, resp.text


def test_plan_missing_assignment_is_404(env):
    db = env.make_session()
    manager, *_rest = (*_seed(env, db),)
    resp = _read_plan(env, manager, aid=999999)
    assert resp.status_code == 404, resp.text


def test_org_member_role_always_has_entrust_view(env):
    """「组织成员但没有 `entrust:view`」这条分支**当前构造不出来** —— 把它记下来。

    `assert_can_view_assignment` 里有一条"是成员但没权限 ⇒ 404"的分支，但权限来自两处：
    `ORG_ROLE_PERMISSIONS[member_role]`（四个角色**都**含 `PERM_VIEW`）与委托授权
    （**只做加法**，没有"收回"这条路）⇒ 组织成员恒有 `entrust:view`，那条分支今天不可达。

    ⚠️ 本用例**不假装**覆盖了它，而是把"它为什么不可达"变成一条**会自己失效**的断言：
    哪天有人加了一个不含 `entrust:view` 的角色，这条就会红，提醒他那个分支变成可达了、
    必须补负例。"一道从不失败的检查等于没有检查"的反面 ——
    **一条假设会失效的断言，比一句注释可靠**。
    """
    from app.modules.entrust.access import ORG_ROLE_PERMISSIONS, PERM_VIEW

    missing = sorted(role for role, perms in ORG_ROLE_PERMISSIONS.items() if PERM_VIEW not in perms)
    assert not missing, (
        f"有角色不含 {PERM_VIEW}：{missing} —— "
        "`assert_can_view_assignment` 的「成员但无权限 ⇒ 404」分支变成可达，必须补负例"
    )


# ───────────────────────── 3. 空计划不是错误


def test_empty_plan_is_ok_not_an_error(env):
    """没有结构化计划的委托 ⇒ `legs=[]` + 200。

    把"还没计划"报成 404 会让它与"端点坏了"长得一样；而"空"是本支线的一种
    正常状态（`ent_leg` 只被最小化落地，历史委托没有被回溯填充）。
    """
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)

    resp = _read_plan(env, manager, aid=aid)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["legs"] == []
    assert body["task_prerequisites"] == []


# ───────────────────────── 4. 必需任务与其固定前置


def test_task_prerequisites_are_projected(env):
    """必需任务带 `precondition_task_id`，前置链可读；字段面**逐键锁定**。"""
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)

    first = _add_task(env, manager, aid=aid, task_type="quote", title="取两家报价")
    assert first.status_code == 200, first.text
    first_id = first.json()["task_id"]

    second = _add_task(
        env,
        manager,
        aid=aid,
        task_type="purchase",
        title="采购确认",
        precondition_task_id=first_id,
    )
    assert second.status_code == 200, second.text
    second_id = second.json()["task_id"]

    body = _read_plan(env, manager, aid=aid).json()
    rows = {row["task_id"]: row for row in body["task_prerequisites"]}
    assert set(rows) == {first_id, second_id}
    # 没有前置的那条 ⇒ None（不是 0，也不是缺字段）
    assert rows[first_id]["precondition_task_id"] is None
    # 有前置的那条 ⇒ 指回第一条（这就是"必需任务前置"的判据）
    assert rows[second_id]["precondition_task_id"] == first_id
    assert rows[second_id]["task_type"] == "purchase"
    assert rows[second_id]["status"] == "pending"
    assert rows[second_id]["title"] == "采购确认"
    # ⭐ 字段面**精确**锁定：多带一个字段就红。
    #    判据是 `sorted(keys) ==`（不是"包含"）：本读模型只回答"计划与前置"，
    #    每多带一个字段，界面之外就多一个"会与任务页各自演化"的落点
    #    —— 字段面照抄 `workbench._load_tasks` 的先例（它同样不带 `required_evidence`）。
    assert sorted(rows[second_id].keys()) == [
        "precondition_task_id",
        "status",
        "task_id",
        "task_type",
        "title",
    ]


def test_leg_row_shape_is_minimal_and_carries_label(env):
    """航段行只带"给界面看的"那几个字段，且**展示标签由服务端给**。

    标签（`mode_label`）落在服务端而不是界面，是为了让 `road/water/rail` 这一张表
    全仓只有一份 —— 前端再写一份的结果是"改一处、另一处静默留下旧口径"
    （`contracts._MODE_LABELS` 曾经就是这样，已合并进 `plan.MODE_LABELS`）。
    字段面同样**精确**锁定：多带 `assignment_id` / `created_at` 这类行内自明的事实
    会让读模型变成第二份表结构。
    """
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    _add_leg(db, aid=aid, seq=1, mode="road", from_name="厂区", to_name="南宁港")

    leg = _read_plan(env, manager, aid=aid).json()["legs"][0]
    assert sorted(leg.keys()) == [
        "from_name",
        "leg_id",
        "mode",
        "mode_label",
        "seq",
        "to_name",
    ]
    # 原始取值与标签并存：标签表会演进，原始值不会
    assert leg["mode"] == "road"
    assert leg["mode_label"] == plan_svc.MODE_LABELS["road"]


# ───────────────────────── 5. 开关关闭 ⇒ 整组 404


def test_plan_is_404_when_entrust_disabled(env, monkeypatch):
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    _add_legs_canonical(db, aid=aid)

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    resp = _read_plan(env, manager, aid=aid)
    assert resp.status_code == 404, resp.text


# ───────────────────────── 6. 截断可观测（开放项 O-9，2026-09-18 裁定）


def test_plan_marks_truncation_when_tasks_exceed_limit(env, monkeypatch):
    """任务数超过读模型上限 ⇒ `truncated=True`，且**总数照给**。

    这就是 O-9 的判据：截断必须是一条**响应里的事实**。此前
    `list_task_prerequisites` 把 `list_tasks` 回的总数丢掉了，于是"任务被截"
    在响应里没有任何痕迹 —— 下游只能看到"前置解析不出来"，像是界面缺陷。

    上限被 monkeypatch 成 3（而不是造 101 条任务）：判据是
    「读到 3 条 / 总数 4 / 标记为真」**三个数之间的关系**，
    把上限调小只让这个关系更容易看清，不改变它。
    """
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    monkeypatch.setattr(plan_svc, "TASK_LIMIT", 3)

    for i in range(4):
        resp = _add_task(env, manager, aid=aid, task_type="quote", title=f"任务 {i}")
        assert resp.status_code == 200, resp.text

    body = _read_plan(env, manager, aid=aid).json()
    assert body["task_prerequisites_total"] == 4
    assert len(body["task_prerequisites"]) == 3
    assert body["task_prerequisites_truncated"] is True


def test_plan_not_marked_truncated_at_exactly_the_limit(env, monkeypatch):
    """恰好等于上限 ⇒ **不**报截断。

    边界用例：没有它，"把 `truncated` 恒写成 True"也能满足上一条用例。
    """
    db = env.make_session()
    manager, _owner, _outsider, _org, _eid, aid = _seed(env, db)
    monkeypatch.setattr(plan_svc, "TASK_LIMIT", 3)

    for i in range(3):
        resp = _add_task(env, manager, aid=aid, task_type="quote", title=f"任务 {i}")
        assert resp.status_code == 200, resp.text

    body = _read_plan(env, manager, aid=aid).json()
    assert body["task_prerequisites_total"] == 3
    assert len(body["task_prerequisites"]) == 3
    assert body["task_prerequisites_truncated"] is False


def test_plan_task_limit_matches_documented_value():
    """真实上限被钉在测试里：要改它，就必须显式改这一行。

    这个上限不是"性能护栏"，而是"什么时候必须把截断告诉下游"的**业务口径**；
    顺手调大它（例如改成 1000）等于把同一类缺陷的触发门槛推远而不留痕迹。
    """
    assert plan_svc.TASK_LIMIT == 100
