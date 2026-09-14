"""异常与变更案件 **API** 用例（ENT-030 切片三之二）。

分组：

1. **登记与写权限**：管理动作权限（403）、非组织成员（404）、委托未受理（409）、
   `org_id` 由服务端派生（不一致 403）、C1/C2 在端点层同样生效（400）；
2. **幂等与横切**：缺键 400、同键同体**只落一行**并与首次响应一致、同键异体 409、
   未登录 401、开关关闭 404（AC-22）；
3. **读**：列表过滤与 `total`、跨委托 404、详情带完整事件链、货主可读不可写；
4. **受影响项**：登记推进案件版本、过期版本 409、跨委托 403、重复登记 409、
   移除最后一条会让阻断变成空转 → 400、关闭后不得改动 → 409；
5. **决定 / 关闭 / 重开**：非法转移 409、`approved` 必须给依据、依据跨委托 403、
   **决定路径改不了 `impact_kind`**、同一个 `rejected` 在两种 kind 下相反、
   关闭缺处置/证据 422、`rejected` 的异常不得以 `resolved` 关闭、
   两轮关闭历史按 `seq` 完整可判定。

本文件**只断言端点口径**（状态码、幂等重放、可见性、投影形状与字段取舍）；
业务不变量在 `test_entrust_exceptions_service.py`，纯规则在 `test_entrust_exceptions.py`。
三处不重复抄同一条断言 —— 抄一遍就等于多一处会漂移的实现。
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import artifacts as art_svc  # noqa: E402
from app.modules.entrust import assignments as assign_svc  # noqa: E402
from app.modules.entrust import exceptions as exc  # noqa: E402
from app.modules.entrust import tasks as task_svc  # noqa: E402

_RAISE_URL = "/api/v1/entrust/assignments/{aid}/exceptions"
_LIST_URL = "/api/v1/entrust/exceptions"
_DETAIL_URL = "/api/v1/entrust/exceptions/{cid}"
_LINKS_URL = "/api/v1/entrust/exceptions/{cid}/links"
_LINK_URL = "/api/v1/entrust/exceptions/{cid}/links/{lid}"
_DECISION_URL = "/api/v1/entrust/exceptions/{cid}/decision"
_CLOSE_URL = "/api/v1/entrust/exceptions/{cid}/close"
_REOPEN_URL = "/api/v1/entrust/exceptions/{cid}/reopen"

OWNER = 900  # 货主（只作为 id 出现，不登录）
ALL_PERMS = (
    '["entrust:view","entrust:assignment:claim","entrust:quote:create","entrust:task:dispatch"]'
)
#: 能受理、能读，但**没有**管理动作权限（`entrust:task:dispatch`）。
VIEW_CLAIM_PERMS = '["entrust:view","entrust:assignment:claim"]'


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 造数会话工厂，且 ENTRUST_ENABLED=True。"""
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


# ─────────────────────────────────────────── 播种助手


def _org(db, name: str = "测试组织") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, 'active', :c)"),
        {"n": name, "c": "2026-09-14 00:00:00"},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": "2026-09-14 00:00:00"},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "p": permissions, "c": "2026-09-14 00:00:00"},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _login(client: TestClient, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _seed(
    env, *, permissions: str = ALL_PERMS, owner_id: int = OWNER, claimed: bool = True
) -> SimpleNamespace:
    """组织 + 经理成员 + 货主授权 + 一张委托单（默认已受理）。"""
    db = env.make_session()
    manager = _login(env.client, "mgr")
    org = _org(db)
    _member(db, org, user_id=int(manager["user_id"]), role="manager")
    entrustment_id = _entrust(db, org, owner_id=owner_id, permissions=permissions)
    draft = assign_svc.create_assignment(db, owner_user_id=owner_id, title="钢材运输")
    submitted = assign_svc.submit_assignment(
        db,
        assignment_id=draft["assignment_id"],
        actor_id=owner_id,
        org_id=org,
        expected_revision=draft["revision"],
    )
    assignment = submitted
    if claimed:
        assignment = assign_svc.claim_assignment(
            db, assignment_id=submitted["assignment_id"], actor_id=int(manager["user_id"])
        )
    return SimpleNamespace(
        db=db,
        manager=manager,
        org=org,
        entrustment_id=entrustment_id,
        assignment_id=int(assignment["assignment_id"]),
    )


def _task(s, *, assignment_id: int | None = None, title: str = "收集单证") -> int:
    created = task_svc.create_task(
        s.db,
        assignment_id=assignment_id if assignment_id is not None else s.assignment_id,
        actor_id=int(s.manager["user_id"]),
        task_type=task_svc.TASK_TYPE_COLLECT,
        title=title,
    )
    return int(created["task_id"])


def _artifact(s, *, assignment_id: int | None = None) -> tuple[int, int]:
    """返回 (artifact_id, revision_id)；两者都属本委托。"""
    created = art_svc.create_artifact(
        s.db,
        entrustment_id=s.entrustment_id,
        artifact_type="quote_parsed",
        payload={"freight": 12000, "currency": "CNY"},
        created_by=int(s.manager["user_id"]),
        assignment_id=assignment_id if assignment_id is not None else s.assignment_id,
    )
    return int(created["artifact_id"]), int(created["current_revision_id"])


def _scalar(db, stmt: str, **params) -> int:
    """读一条标量。**先 rollback**：造数会话与请求会话共用同一个连接，
    不结束上一次读事务就可能拿到过期快照，让断言随机地看错。
    """
    db.rollback()
    return int(db.execute(text(stmt), params).scalar() or 0)


def _count(db, table: str) -> int:
    return _scalar(db, f"SELECT COUNT(*) FROM {table}")


# ─────────────────────────────────────────── 请求助手


def _raise_req(env, s, *, key: str | None = None, **overrides):
    body: dict = {
        "kind": exc.KIND_EXCEPTION,
        "title": "船期延误",
        "severity": "medium",
        "impact_kind": exc.IMPACT_INFORMATIONAL,
    }
    body.update(overrides)
    return env.client.post(
        _RAISE_URL.format(aid=s.assignment_id),
        json=body,
        headers=_headers(s.manager, key or uuid.uuid4().hex),
    )


def _blocking_case(env, s, *, kind: str = exc.KIND_EXCEPTION) -> tuple[dict, int]:
    """经 HTTP 登记一个 `execution-blocking` 案件（C2 要求带一条受影响任务）。"""
    task_id = _task(s)
    resp = _raise_req(
        env,
        s,
        kind=kind,
        severity="high",
        impact_kind=exc.IMPACT_EXECUTION_BLOCKING,
        links=[{"target_kind": exc.TARGET_TASK, "target_id": task_id}],
    )
    assert resp.status_code == 200, resp.text
    return resp.json(), task_id


def _decide(env, s, case: dict, *, to_status: str, expected_revision: int | None = None, **extra):
    body: dict = {
        "expected_revision": (
            case["revision_no"] if expected_revision is None else expected_revision
        ),
        "to_status": to_status,
        **extra,
    }
    return env.client.post(
        _DECISION_URL.format(cid=case["case_id"]),
        json=body,
        headers=_headers(s.manager, uuid.uuid4().hex),
    )


def _close(env, s, case: dict, *, disposition: str, expected_revision: int | None = None, **extra):
    body: dict = {
        "expected_revision": (
            case["revision_no"] if expected_revision is None else expected_revision
        ),
        "closure_disposition": disposition,
        "evidence_ref": "evidence://case/1",
        **extra,
    }
    return env.client.post(
        _CLOSE_URL.format(cid=case["case_id"]),
        json=body,
        headers=_headers(s.manager, uuid.uuid4().hex),
    )


# ─────────────────────────────────────────── 1. 登记与写权限


def test_raise_returns_internal_projection_with_affected(env):
    s = _seed(env)
    task_id = _task(s)

    resp = _raise_req(
        env,
        s,
        cause="船东临时改配载",
        owner_user_id=int(s.manager["user_id"]),
        proposed_action="改配下一班",
        links=[{"target_kind": exc.TARGET_TASK, "target_id": task_id}],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["case_id"] > 0
    assert body["assignment_id"] == s.assignment_id
    assert body["org_id"] == s.org  # 服务端派生，不是客户端给的
    assert body["status"] == exc.STATUS_OPEN
    assert body["blocking"] is False  # informational
    assert body["revision_no"] == 1
    assert [item["target_id"] for item in body["affected"]] == [task_id]
    # 内部投影必须有决策/处置三块（对客投影才没有）
    assert set(body["decision"]) == {"note", "by", "at", "basis_revision_id"}
    assert body["closure"]["disposition"] is None


def test_raise_without_dispatch_permission_403(env):
    """是组织成员、也能受理，但没有管理动作权限 → 403。"""
    s = _seed(env, permissions=VIEW_CLAIM_PERMS)

    resp = _raise_req(env, s)
    assert resp.status_code == 403


def test_raise_by_non_member_404(env):
    """非该组织成员 → 404，不泄漏委托与案件的存在性。"""
    s = _seed(env)
    outsider = _login(env.client, "mgr")

    resp = env.client.post(
        _RAISE_URL.format(aid=s.assignment_id),
        json={
            "kind": exc.KIND_EXCEPTION,
            "title": "船期延误",
            "severity": "medium",
            "impact_kind": exc.IMPACT_INFORMATIONAL,
        },
        headers=_headers(outsider, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


def test_raise_on_unclaimed_assignment_409(env):
    """委托尚未受理 → 409（受理前没有责任主体）。"""
    s = _seed(env, claimed=False)

    resp = _raise_req(env, s)
    assert resp.status_code == 409
    assert _count(s.db, "ent_exception") == 0


def test_raise_org_id_mismatch_403(env):
    """`org_id` 是冗余声明：带上就必须与委托一致，不能由客户端**决定**作用域。"""
    s = _seed(env)

    resp = _raise_req(env, s, org_id=s.org + 999)
    assert resp.status_code == 403


def test_raise_blocking_without_link_400(env):
    """C2：`execution-blocking` 没有受影响项就只是空转阻断 → 400。"""
    s = _seed(env)

    resp = _raise_req(env, s, severity="high", impact_kind=exc.IMPACT_EXECUTION_BLOCKING)
    assert resp.status_code == 400
    assert _count(s.db, "ent_exception") == 0


def test_raise_critical_without_blocking_400(env):
    """C1：`critical` 必须搭配 `execution-blocking`（单向，反向不要求）。"""
    s = _seed(env)

    resp = _raise_req(env, s, severity="critical", impact_kind=exc.IMPACT_INFORMATIONAL)
    assert resp.status_code == 400


def test_raise_unknown_kind_400(env):
    s = _seed(env)

    resp = _raise_req(env, s, kind="incident")
    assert resp.status_code == 400


# ─────────────────────────────────────────── 2. 幂等与横切


def test_raise_missing_idempotency_key_400(env):
    s = _seed(env)

    resp = env.client.post(
        _RAISE_URL.format(aid=s.assignment_id),
        json={
            "kind": exc.KIND_EXCEPTION,
            "title": "船期延误",
            "severity": "medium",
            "impact_kind": exc.IMPACT_INFORMATIONAL,
        },
        headers=_headers(s.manager),
    )
    assert resp.status_code == 400


def test_raise_idempotent_replay_writes_one_row(env):
    """同键同体重放：第二次原样返回首次结果，且**只落一行**。"""
    s = _seed(env)

    first = _raise_req(env, s, key="k-case-1")
    second = _raise_req(env, s, key="k-case-1")

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["case_id"] == first.json()["case_id"]
    assert _count(s.db, "ent_exception") == 1


def test_raise_same_key_different_body_409(env):
    s = _seed(env)

    assert _raise_req(env, s, key="k-case-2").status_code == 200
    conflict = _raise_req(env, s, key="k-case-2", title="另一件事")

    assert conflict.status_code == 409
    assert _count(s.db, "ent_exception") == 1


def test_unauthenticated_401(env):
    resp = env.client.get(_LIST_URL, params={"assignment_id": 1})
    assert resp.status_code == 401


def test_disabled_returns_404(client, shipper):
    """AC-22：开关关闭 → 整组端点 404（开关不是访问控制）。"""
    resp = client.get(_LIST_URL, params={"assignment_id": 1}, headers=shipper["_headers"])
    assert resp.status_code == 404


# ─────────────────────────────────────────── 3. 读


def test_list_filters_kind_and_status(env):
    s = _seed(env)
    _raise_req(env, s, title="甲")
    _raise_req(env, s, title="乙")
    _raise_req(env, s, kind=exc.KIND_CHANGE_REQUEST, title="改配载", severity="low")

    all_cases = env.client.get(
        _LIST_URL, params={"assignment_id": s.assignment_id}, headers=_headers(s.manager)
    )
    assert all_cases.status_code == 200, all_cases.text
    assert all_cases.json()["total"] == 3

    changes = env.client.get(
        _LIST_URL,
        params={"assignment_id": s.assignment_id, "kind": exc.KIND_CHANGE_REQUEST},
        headers=_headers(s.manager),
    )
    assert changes.json()["total"] == 1
    assert changes.json()["items"][0]["kind"] == exc.KIND_CHANGE_REQUEST

    closed = env.client.get(
        _LIST_URL,
        params={"assignment_id": s.assignment_id, "status": exc.STATUS_CLOSED},
        headers=_headers(s.manager),
    )
    assert closed.json()["total"] == 0


def test_list_of_foreign_assignment_404(env):
    """列表按委托取作用域：别人的委托 → 404。"""
    mine = _seed(env)
    other = _seed(env)

    resp = env.client.get(
        _LIST_URL, params={"assignment_id": other.assignment_id}, headers=_headers(mine.manager)
    )
    assert resp.status_code == 404


def test_list_of_unknown_assignment_404(env):
    s = _seed(env)

    resp = env.client.get(
        _LIST_URL, params={"assignment_id": s.assignment_id + 9999}, headers=_headers(s.manager)
    )
    assert resp.status_code == 404


def test_detail_carries_full_event_chain(env):
    s = _seed(env)
    case, _ = _blocking_case(env, s)

    decided = _decide(env, s, case, to_status=exc.STATUS_REJECTED, decision_note="方案不可行")
    assert decided.status_code == 200, decided.text

    resp = env.client.get(_DETAIL_URL.format(cid=case["case_id"]), headers=_headers(s.manager))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [e["seq"] for e in body["events"]] == [1, 2, 3]
    assert [e["event_kind"] for e in body["events"]] == [
        exc.EVENT_CREATED,
        exc.EVENT_LINK_ADDED,
        exc.EVENT_DECIDED,
    ]
    assert body["events"][-1]["from_status"] == exc.STATUS_OPEN
    assert body["events"][-1]["to_status"] == exc.STATUS_REJECTED
    assert body["case"]["status"] == exc.STATUS_REJECTED


def test_detail_of_unknown_case_404(env):
    s = _seed(env)

    resp = env.client.get(_DETAIL_URL.format(cid=99999), headers=_headers(s.manager))
    assert resp.status_code == 404


def test_owner_can_read_detail_but_not_write(env):
    """货主本人：可读案件详情；写命令 404（非组织成员，不能借读权限写）。"""
    owner = _login(env.client, "shipper")
    s = _seed(env, owner_id=int(owner["user_id"]))
    case = _raise_req(env, s).json()

    read = env.client.get(_DETAIL_URL.format(cid=case["case_id"]), headers=_headers(owner))
    assert read.status_code == 200, read.text

    write = env.client.post(
        _DECISION_URL.format(cid=case["case_id"]),
        json={"expected_revision": case["revision_no"], "to_status": exc.STATUS_REJECTED},
        headers=_headers(owner, uuid.uuid4().hex),
    )
    assert write.status_code == 404


def test_stranger_read_404(env):
    s = _seed(env)
    stranger = _login(env.client, "shipper")
    case = _raise_req(env, s).json()

    detail = env.client.get(_DETAIL_URL.format(cid=case["case_id"]), headers=_headers(stranger))
    assert detail.status_code == 404

    listing = env.client.get(
        _LIST_URL, params={"assignment_id": s.assignment_id}, headers=_headers(stranger)
    )
    assert listing.status_code == 404


# ─────────────────────────────────────────── 4. 受影响项


def test_add_link_advances_revision_and_is_listed(env):
    s = _seed(env)
    case = _raise_req(env, s).json()
    task_id = _task(s)

    resp = env.client.post(
        _LINKS_URL.format(cid=case["case_id"]),
        json={
            "expected_revision": case["revision_no"],
            "target_kind": exc.TARGET_TASK,
            "target_id": task_id,
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revision_no"] == case["revision_no"] + 1
    assert [item["target_id"] for item in body["affected"]] == [task_id]


def test_add_link_stale_revision_409(env):
    s = _seed(env)
    case = _raise_req(env, s).json()
    first = _task(s, title="甲")
    second = _task(s, title="乙")

    ok = env.client.post(
        _LINKS_URL.format(cid=case["case_id"]),
        json={
            "expected_revision": case["revision_no"],
            "target_kind": exc.TARGET_TASK,
            "target_id": first,
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert ok.status_code == 200

    stale = env.client.post(
        _LINKS_URL.format(cid=case["case_id"]),
        json={
            "expected_revision": case["revision_no"],  # 已被上一步推进
            "target_kind": exc.TARGET_TASK,
            "target_id": second,
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert stale.status_code == 409
    assert _count(s.db, "ent_exception_link") == 1


def test_add_link_cross_assignment_403(env):
    """受影响项必须与案件同属一张委托：甲单的异常不能卡住乙单的任务。"""
    s = _seed(env)
    other = _seed(env)
    case = _raise_req(env, s).json()
    foreign_task = _task(other)

    resp = env.client.post(
        _LINKS_URL.format(cid=case["case_id"]),
        json={
            "expected_revision": case["revision_no"],
            "target_kind": exc.TARGET_TASK,
            "target_id": foreign_task,
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 403
    assert _count(s.db, "ent_exception_link") == 0


def test_add_link_unknown_target_404(env):
    s = _seed(env)
    case = _raise_req(env, s).json()

    resp = env.client.post(
        _LINKS_URL.format(cid=case["case_id"]),
        json={
            "expected_revision": case["revision_no"],
            "target_kind": exc.TARGET_TASK,
            "target_id": 99999,
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


def test_add_link_duplicate_409(env):
    s = _seed(env)
    case, task_id = _blocking_case(env, s)  # 已带一条 link

    resp = env.client.post(
        _LINKS_URL.format(cid=case["case_id"]),
        json={
            "expected_revision": case["revision_no"],
            "target_kind": exc.TARGET_TASK,
            "target_id": task_id,
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 409
    assert _count(s.db, "ent_exception_link") == 1


def test_remove_link_rejected_when_it_would_break_c2(env):
    """移除最后一条会让阻断变成空转 → 400（要这么做必须先显式改 `impact_kind`）。"""
    s = _seed(env)
    case, _ = _blocking_case(env, s)
    link_id = case["affected"][0]["link_id"]

    resp = env.client.request(
        "DELETE",
        _LINK_URL.format(cid=case["case_id"], lid=link_id),
        params={"expected_revision": case["revision_no"]},
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 400
    assert _count(s.db, "ent_exception_link") == 1


def test_remove_link_unknown_404(env):
    s = _seed(env)
    case = _raise_req(env, s).json()

    resp = env.client.request(
        "DELETE",
        _LINK_URL.format(cid=case["case_id"], lid=99999),
        params={"expected_revision": case["revision_no"]},
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


def test_link_change_on_closed_case_409(env):
    """关闭后不得改动受影响项 —— 要继续处理必须先 reopen（带原因、留审计）。"""
    s = _seed(env)
    case = _raise_req(env, s).json()
    closed = _close(env, s, case, disposition=exc.DISPOSITION_DUPLICATE, decision_note="重复登记")
    assert closed.status_code == 200, closed.text

    resp = env.client.post(
        _LINKS_URL.format(cid=case["case_id"]),
        json={
            "expected_revision": closed.json()["revision_no"],
            "target_kind": exc.TARGET_TASK,
            "target_id": _task(s),
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 409


# ─────────────────────────────────────────── 5. 决定 / 关闭 / 重开


def test_decide_illegal_transition_409(env):
    s = _seed(env)
    case = _raise_req(env, s).json()

    resp = _decide(env, s, case, to_status=exc.STATUS_APPLIED)
    assert resp.status_code == 409
    assert resp.json()["detail"]


def test_decide_cannot_close_409(env):
    """关闭必须走 close（要给出处置与证据），不能借 decide 关闭。"""
    s = _seed(env)
    case = _raise_req(env, s).json()

    resp = _decide(env, s, case, to_status=exc.STATUS_CLOSED)
    assert resp.status_code == 409


def test_decide_approved_requires_basis_revision_400(env):
    s = _seed(env)
    case = _raise_req(env, s).json()

    resp = _decide(env, s, case, to_status=exc.STATUS_APPROVED, decision_note="同意执行")
    assert resp.status_code == 400


def test_decide_basis_from_other_assignment_403(env):
    """决定依据必须指向**本委托**的成果版本（证据同样校验归属）。"""
    s = _seed(env)
    other = _seed(env)
    case = _raise_req(env, s).json()
    _, foreign_revision = _artifact(other)

    resp = _decide(
        env,
        s,
        case,
        to_status=exc.STATUS_APPROVED,
        decision_note="按报价执行",
        basis_revision_id=foreign_revision,
    )
    assert resp.status_code == 403


def test_decide_approved_with_own_basis(env):
    s = _seed(env)
    case = _raise_req(env, s).json()
    _, revision_id = _artifact(s)

    resp = _decide(
        env,
        s,
        case,
        to_status=exc.STATUS_APPROVED,
        decision_note="按报价执行",
        basis_revision_id=revision_id,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"]["basis_revision_id"] == revision_id


def test_decide_stale_revision_409(env):
    s = _seed(env)
    case = _raise_req(env, s).json()

    resp = _decide(
        env, s, case, to_status=exc.STATUS_REJECTED, expected_revision=case["revision_no"] + 5
    )
    assert resp.status_code == 409


def test_decision_cannot_change_impact_kind_by_body_field(env):
    """**C3 的可测形式**：决定路径上多带一个 `impact_kind` 改不了阻断。

    分两半断言：① 契约里没有这两个字段（结构）；② 真发一个也只是被忽略，
    库里 `impact_kind` 不变、`blocking` 仍为真（行为）。
    """
    s = _seed(env)
    case, _ = _blocking_case(env, s)

    schema = env.client.get("/openapi.json").json()["components"]["schemas"]
    decision_props = schema["ExceptionCaseDecisionIn"]["properties"]
    assert "severity" not in decision_props
    assert "impact_kind" not in decision_props
    # 登记路径必须有它们（否则案件根本没法如实描述影响）
    create_props = schema["ExceptionCaseCreate"]["properties"]
    assert {"severity", "impact_kind"} <= set(create_props)

    resp = _decide(
        env,
        s,
        case,
        to_status=exc.STATUS_REJECTED,
        decision_note="驳回处置方案",
        impact_kind=exc.IMPACT_INFORMATIONAL,  # 不该被接受
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["impact_kind"] == exc.IMPACT_EXECUTION_BLOCKING
    assert resp.json()["blocking"] is True
    assert (
        _scalar(
            s.db,
            "SELECT COUNT(*) FROM ent_exception WHERE id = :cid"
            " AND impact_kind = 'execution-blocking'",
            cid=case["case_id"],
        )
        == 1
    )


def test_rejected_exception_still_blocks(env):
    """`exception` 的 `rejected` = 处置方案被驳回，真实异常还在 ⇒ 继续阻断。"""
    s = _seed(env)
    case, _ = _blocking_case(env, s)

    resp = _decide(env, s, case, to_status=exc.STATUS_REJECTED, decision_note="方案不可行")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == exc.STATUS_REJECTED
    assert resp.json()["blocking"] is True


def test_rejected_change_request_stops_blocking(env):
    """`change_request` 的 `rejected` = 变更被否 ⇒ 该变更不会发生 ⇒ 不再阻断。"""
    s = _seed(env)
    case, _ = _blocking_case(env, s, kind=exc.KIND_CHANGE_REQUEST)

    resp = _decide(env, s, case, to_status=exc.STATUS_REJECTED, decision_note="本轮不改")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == exc.STATUS_REJECTED
    assert resp.json()["blocking"] is False


def test_close_requires_disposition_and_evidence_422(env):
    """没有一键关闭：缺处置、缺证据都是入参非法（422），不会在服务层被补默认值。"""
    s = _seed(env)
    case = _raise_req(env, s).json()
    url = _CLOSE_URL.format(cid=case["case_id"])

    no_disposition = env.client.post(
        url,
        json={"expected_revision": case["revision_no"]},
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert no_disposition.status_code == 422

    no_evidence = env.client.post(
        url,
        json={
            "expected_revision": case["revision_no"],
            "closure_disposition": exc.DISPOSITION_DUPLICATE,
            "decision_note": "重复登记",
        },
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert no_evidence.status_code == 422


def test_close_rejected_exception_cannot_use_resolved(env):
    """HO 第 3 条：**不允许通过驳回处置方案解除真实异常**。"""
    s = _seed(env)
    case, _ = _blocking_case(env, s)
    rejected = _decide(env, s, case, to_status=exc.STATUS_REJECTED, decision_note="方案不可行")
    assert rejected.status_code == 200

    resp = _close(
        env,
        s,
        rejected.json(),
        disposition=exc.DISPOSITION_RESOLVED,
        resolution_note="已处理",
    )
    assert resp.status_code == 400
    assert (
        _scalar(
            s.db,
            "SELECT COUNT(*) FROM ent_exception WHERE id = :cid AND status = 'rejected'",
            cid=case["case_id"],
        )
        == 1
    )


def test_close_rejected_exception_with_disqualifying_disposition(env):
    """同一案件可以合法地以 `superseded` 关闭 —— 被堵的是「宣称已解决」，不是关闭本身。"""
    s = _seed(env)
    case, _ = _blocking_case(env, s)
    rejected = _decide(env, s, case, to_status=exc.STATUS_REJECTED, decision_note="方案不可行")

    resp = _close(
        env,
        s,
        rejected.json(),
        disposition=exc.DISPOSITION_SUPERSEDED,
        decision_note="已被另一单取代",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["closure"]["disposition"] == exc.DISPOSITION_SUPERSEDED
    # 关闭后异常不再阻断（这不是「驳回解除阻断」，而是案件确实终结了）
    assert resp.json()["blocking"] is False


def test_reopen_requires_reason_422(env):
    s = _seed(env)
    case = _raise_req(env, s).json()
    closed = _close(env, s, case, disposition=exc.DISPOSITION_DUPLICATE, decision_note="重复")

    resp = env.client.post(
        _REOPEN_URL.format(cid=case["case_id"]),
        json={"expected_revision": closed.json()["revision_no"], "reason": ""},
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 422


def test_close_then_reopen_keeps_two_rounds(env):
    """两轮「关闭 → 重开 → 再关闭」的历史按 `seq` 完整可判定，旧记录不被改写。"""
    s = _seed(env)
    case = _raise_req(env, s).json()
    cid = case["case_id"]

    first = _close(env, s, case, disposition=exc.DISPOSITION_DUPLICATE, decision_note="重复登记")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == exc.STATUS_CLOSED

    reopened = env.client.post(
        _REOPEN_URL.format(cid=cid),
        json={"expected_revision": first.json()["revision_no"], "reason": "同一问题再次出现"},
        headers=_headers(s.manager, uuid.uuid4().hex),
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == exc.STATUS_OPEN
    # 重开清空上一轮的处置，否则历史看起来像被改写
    assert reopened.json()["closure"] == {"disposition": None, "by": None, "at": None}

    second = _close(
        env, s, reopened.json(), disposition=exc.DISPOSITION_CANCELLED, decision_note="不再需要"
    )
    assert second.status_code == 200, second.text

    detail = env.client.get(_DETAIL_URL.format(cid=cid), headers=_headers(s.manager)).json()
    assert [e["seq"] for e in detail["events"]] == [1, 2, 3, 4]
    assert [e["event_kind"] for e in detail["events"]] == [
        exc.EVENT_CREATED,
        exc.EVENT_CLOSED,
        exc.EVENT_REOPENED,
        exc.EVENT_CLOSED,
    ]
    # 第一轮关闭的证据引用仍在（append-only，从未被覆盖）
    assert detail["events"][1]["evidence_ref"] == "evidence://case/1"
    assert detail["events"][3]["evidence_ref"] == "evidence://case/1"
    assert detail["events"][2]["note"] == "同一问题再次出现"
    assert detail["case"]["closure"]["disposition"] == exc.DISPOSITION_CANCELLED
