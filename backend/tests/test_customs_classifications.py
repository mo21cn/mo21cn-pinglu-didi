"""关务 D2 归类测试 —— 主打"模型不可用时怎么办"。

这一组断言的对象不是"归类准不准"（那本来就该由人复核），而是**降级契约**能不能守住：

1. 模型给了建议 ⇒ 只进候选表，`hs_code` 仍为空（模型永不落结论）；
2. 模型调用失败 ⇒ `status=manual_required` + 白名单化的 `degraded`，
   ⛔ 不写候选、不写结论、**不返回 5xx**；
3. "调用成功但没给出候选" 与 "调用失败" 分成两个 kind，但都给同一条人工出口；
4. 人工通路在降级状态下照样可用（AC-08 同族），且人工候选**不覆盖**模型候选；
5. `degraded.reason` 不回显原始异常串 —— 原串里常带 base_url 与凭证片段；
6. 已确认后不可再确认（候选表只追加，更正＝新建）。

每一条都能失败：把 `_clean_candidates` 的截断去掉、把降级改成 raise、把 reason 改成
`str(exc)`，各自都会让对应的用例红。
"""

from __future__ import annotations

import asyncio
import os
import uuid

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.customs import classifications as cls  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"


@pytest.fixture()
def session():
    """独立 SQLite 内存库会话；`cus_` 系列表由迁移创建。"""
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


@pytest.fixture()
def env(monkeypatch):
    """TestClient + 会话工厂（API 层用）。"""
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
    from types import SimpleNamespace

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _propose(session, *, product_name="钢结构支架", user_id=1, **over):
    return asyncio.run(
        cls.propose(
            session,
            user_id=user_id,
            org_ids=frozenset(),
            product_name=product_name,
            **over,
        )
    )


# ─────────────────────────────────────────────── 模型给建议时


def test_propose_returns_model_candidates_but_no_conclusion(session):
    """模型建议只落候选表：`hs_code` 必须仍为空（结论只能由人给）。"""
    out = _propose(session)
    assert out["status"] == "suggested"
    assert out["hs_code"] is None, "模型直接落了结论 —— 归类责任被转移给模型"
    assert out["decided_by_user_id"] is None and out["decided_at"] is None
    assert out["degraded"] is None
    assert out["candidates"], "mock 模式下「钢结构」应给出候选"
    top = out["candidates"][0]
    assert top["hs_code"] == "7308900000"
    assert top["source"] == "model"
    assert top["confidence"] is not None
    assert out["model"]["mocked"] is True


def test_candidates_are_cleaned_and_capped(session, monkeypatch):
    """格式不对的编码必须被丢弃，且最多 5 条 —— 宁可少一条，不展示可疑编码。"""

    class _Fake:
        content = {
            "candidates": [
                {"hs_code": "7308900000", "label": "A", "confidence": 0.5},
                {"hs_code": "7308900000", "label": "重复", "confidence": 0.9},
                {"hs_code": "12345", "label": "太短", "confidence": 0.9},
                {"hs_code": "abcdefgh", "label": "非数字", "confidence": 0.9},
                {"hs_code": "3926909090", "label": "B", "confidence": None},
                {"hs_code": "7308900001", "label": "C", "confidence": 0.8},
                {"hs_code": "7308900002", "label": "D", "confidence": 0.7},
                {"hs_code": "7308900003", "label": "E", "confidence": 0.6},
            ]
        }
        raw = "{}"
        latency_ms = 1
        mocked = True
        reasoning = ""

    async def fake_chat_json(**_kw):
        return _Fake()

    monkeypatch.setattr(cls, "chat_json", fake_chat_json)
    out = _propose(session)
    codes = [c["hs_code"] for c in out["candidates"]]
    assert "12345" not in codes and "abcdefgh" not in codes
    assert len(codes) == len(set(codes)), "重复编码没去重"
    assert len(codes) <= cls.MAX_CANDIDATES
    assert codes[-1] == "3926909090", "置信度缺失的候选应排在最后"


# ─────────────────────────────────────────────── 模型不可用时


def test_model_failure_degrades_instead_of_raising(session, monkeypatch):
    """调用失败 ⇒ manual_required + 白名单 reason，且**不写任何候选**。"""
    secret = "http://internal-llm.local/v1 key=sk-live-abc123"

    async def boom(**_kw):
        raise cls.LLMError("quota", f"LLM 402 insufficient_balance {secret}")

    monkeypatch.setattr(cls, "chat_json", boom)
    out = _propose(session)

    assert out["status"] == "manual_required"
    assert out["degraded"]["kind"] == "quota"
    assert out["degraded"]["reason"] == cls.DEGRADED_REASON["quota"]
    assert out["candidates"] == []
    assert out["hs_code"] is None
    # 人工出口必须同时打开 —— 否则降级等于死路
    assert out["manual_entry"]["available"] is True
    # ⛔ 原始异常串一个字都不许透出去
    for leak in ("internal-llm.local", "sk-live-abc123", "insufficient_balance"):
        assert leak not in out["degraded"]["reason"]


def test_unknown_error_kind_is_collapsed(session, monkeypatch):
    """白名单外的 kind 收敛到 unknown，而不是原样透传。"""

    async def boom(**_kw):
        raise cls.LLMError("weird_upstream_thing", "whatever")

    monkeypatch.setattr(cls, "chat_json", boom)
    out = _propose(session)
    assert out["degraded"]["kind"] == "unknown"
    assert out["degraded"]["reason"] == cls.DEGRADED_REASON["unknown"]


def test_empty_candidates_count_as_degraded(session, monkeypatch):
    """调用成功但 0 条候选，是另一种降级（empty_result），不能伪装成"没有匹配"。"""

    class _Empty:
        content = {"candidates": []}
        raw = "{}"
        latency_ms = 1
        mocked = True
        reasoning = ""

    async def fake(**_kw):
        return _Empty()

    monkeypatch.setattr(cls, "chat_json", fake)
    out = _propose(session, product_name="某种说不清的东西")
    assert out["status"] == "manual_required"
    assert out["degraded"]["kind"] == "empty_result"
    assert out["manual_entry"]["available"] is True


# ─────────────────────────────────────────────── 人工通路


def test_manual_path_works_while_degraded(session, monkeypatch):
    """降级状态下人工照样能定稿，且人工候选与模型候选分源并存。"""

    async def boom(**_kw):
        raise cls.LLMError("timeout", "timeout")

    monkeypatch.setattr(cls, "chat_json", boom)
    degraded = _propose(session)
    assert degraded["status"] == "manual_required"

    out = cls.submit_manual(
        session,
        user_id=1,
        org_ids=frozenset(),
        classification_id=degraded["id"],
        hs_code="7308900000",
        label="钢铁结构体",
        rationale="按材质与用途归入 73 章",
    )
    assert out["status"] == "confirmed"
    assert out["hs_code"] == "7308900000"
    assert out["decided_by_user_id"] == 1
    assert out["decided_at"]
    assert out["manual_entry"]["available"] is False
    manual = [c for c in out["candidates"] if c["source"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["confidence"] is None, "⛔ 不给人工结果编一个置信度分数"


def test_manual_does_not_overwrite_model_candidates(session):
    """人工结论追加新行，模型那行必须原样保留（否则说不清是谁定的）。"""
    out = _propose(session)
    model_codes = [c["hs_code"] for c in out["candidates"] if c["source"] == "model"]
    assert model_codes

    after = cls.submit_manual(
        session,
        user_id=1,
        org_ids=frozenset(),
        classification_id=out["id"],
        hs_code="3926909090",
        rationale="人工改判",
    )
    after_model = [c["hs_code"] for c in after["candidates"] if c["source"] == "model"]
    assert after_model == model_codes
    assert [c["hs_code"] for c in after["candidates"] if c["source"] == "manual"] == ["3926909090"]


def test_manual_by_teammate_records_the_real_decider(session):
    """Q-10 留痕：同组织他人可以定稿，但「谁定的」必须记**真正按下去的人**。

    本条要拦的反例很具体：把 `decided_by_user_id` 写成记录的 `owner_user_id`。
    那样写看起来"更自然"（记录是谁的，结论就算谁定的），实际是把一次**代定稿**
    记成了货主自己定的 —— 事后追责时留痕直接指向错误的人，而这在界面上完全看不出来。

    正控在同一用例里：所有者自己定稿时，两列必须都等于所有者（否则"都记同一个人"
    也能让前一半通过，判据就变成不可失败的）。
    """
    org = 7
    by_owner = asyncio.run(
        cls.propose(
            session,
            user_id=1,
            org_ids=frozenset({org}),
            product_name="钢结构支架",
            org_id=org,
        )
    )
    got_owner = cls.submit_manual(
        session,
        user_id=1,
        org_ids=frozenset({org}),
        classification_id=by_owner["id"],
        hs_code="7308900000",
    )
    assert got_owner["decided_by_user_id"] == 1, "正控：所有者自己定稿时定稿人必须是他本人"

    by_teammate = asyncio.run(
        cls.propose(
            session,
            user_id=1,
            org_ids=frozenset({org}),
            product_name="钢结构支架",
            org_id=org,
        )
    )
    got = cls.submit_manual(
        session,
        user_id=2,  # 同组织的另一个人
        org_ids=frozenset({org}),
        classification_id=by_teammate["id"],
        hs_code="3926909090",
        rationale="同事代定稿，依据见备注",
    )
    assert got["decided_by_user_id"] == 2, "定稿人必须是按下去的那个人，不是记录所有者"
    assert got["decided_at"], "留时：定稿时间由服务端盖章，不能为空"
    owner_row = (
        session.execute(
            text("SELECT owner_user_id FROM cus_classification WHERE id = :i"),
            {"i": by_teammate["id"]},
        )
        .mappings()
        .one()
    )
    assert int(owner_row["owner_user_id"]) == 1, "代定稿不改所有者"

    manual_rows = [c for c in got["candidates"] if c["source"] == "manual"]
    assert [c["hs_code"] for c in manual_rows] == ["3926909090"]
    assert manual_rows[0]["actor_user_id"] == 2, "候选那一行也要记真正填写的人"


def test_confirmed_cannot_be_reconfirmed(session):
    out = _propose(session)
    cls.submit_manual(
        session,
        user_id=1,
        org_ids=frozenset(),
        classification_id=out["id"],
        hs_code="7308900000",
    )
    with pytest.raises(cls.ClassificationStateError):
        cls.submit_manual(
            session,
            user_id=1,
            org_ids=frozenset(),
            classification_id=out["id"],
            hs_code="3926909090",
        )


def test_manual_rejects_bad_hs_code(session):
    out = _propose(session)
    for bad in ("123", "73089000000", "73089abcde", ""):
        with pytest.raises(cls.ClassificationStateError):
            cls.submit_manual(
                session,
                user_id=1,
                org_ids=frozenset(),
                classification_id=out["id"],
                hs_code=bad,
            )


def test_manual_schema_has_no_decider_fields():
    """入参只有「码 + 名称 + 依据」三样：定稿人与定稿时间**不是**入参。

    同一条约束在 API 层还有端到端用例（`test_api_manual_cannot_spoof_decider_or_time`），
    但那条要真库、本机跑不了（无本地 MySQL）。这条把它压成**本机能跑**的断言：
    谁哪天顺手加一个可选的 `decided_by_user_id`，这里立刻红。
    """
    from app.modules.customs import schemas as cus_schemas

    assert set(cus_schemas.ClassificationManualCreate.model_fields) == {
        "hs_code",
        "label",
        "rationale",
    }


# ─────────────────────────────────────────────── 可见性 / API


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_api_routes_registered(env):
    paths = set(env.client.get("/openapi.json").json()["paths"])
    assert "/api/v1/customs/classifications" in paths
    assert "/api/v1/customs/classifications/{classification_id}" in paths
    assert "/api/v1/customs/classifications/{classification_id}/manual" in paths


def test_api_degraded_is_201_not_5xx(env, monkeypatch):
    """降级走 201：前端拿到的是"需要人工"，不是一个只能显示"服务不可用"的错误页。"""

    async def boom(**_kw):
        raise cls.LLMError("network", "network")

    monkeypatch.setattr(cls, "chat_json", boom)
    headers = _login(env.client, "cus-cls")
    resp = env.client.post(
        "/api/v1/customs/classifications",
        json={"product_name": "钢结构支架", "product_desc": "热镀锌，建筑用"},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "manual_required"
    assert body["degraded"]["kind"] == "network"
    assert body["manual_entry"]["available"] is True


def test_api_manual_is_idempotent(env):
    headers = _login(env.client, "cus-cls-idem")
    created = env.client.post(
        "/api/v1/customs/classifications",
        json={"product_name": "塑料托盘"},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    key = uuid.uuid4().hex
    payload = {"hs_code": "3926909090", "label": "其他塑料制品"}
    first = env.client.post(
        f"/api/v1/customs/classifications/{cid}/manual",
        json=payload,
        headers={**headers, "Idempotency-Key": key},
    )
    second = env.client.post(
        f"/api/v1/customs/classifications/{cid}/manual",
        json=payload,
        headers={**headers, "Idempotency-Key": key},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200
    # 同键重放：人工候选不会变成两条
    assert len([c for c in second.json()["candidates"] if c["source"] == "manual"]) == 1


def test_api_manual_cannot_spoof_decider_or_time(env):
    """Q-10 留痕：定稿人与定稿时间**由服务端盖章**，请求体说了不算。

    这条要拦的是"代填"最隐蔽的一种形态：不是别人来按，而是在请求里带一个
    `decided_by_user_id` / `decided_at`，让留痕指向别人或别的时间。
    schema 不声明这两个字段 ⇒ 夹带进来只会被忽略，不会写进库。
    """
    headers = _login(env.client, "cus-cls-spoof")
    created = env.client.post(
        "/api/v1/customs/classifications",
        json={"product_name": "塑料托盘"},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    resp = env.client.post(
        f"/api/v1/customs/classifications/{cid}/manual",
        json={
            "hs_code": "3926909090",
            "label": "其他塑料制品",
            "decided_by_user_id": 999,
            "decided_at": "2000-01-01 00:00:00",
        },
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decided_by_user_id"] != 999, "客户端指定的定稿人必须被忽略"
    assert body["decided_by_user_id"] is not None, "定稿人必须是服务端从登录态取的那个人"
    assert not str(body["decided_at"]).startswith("2000"), "客户端指定的定稿时间必须被忽略"
    assert body["hs_code"] == "3926909090"


def test_api_manual_requires_idempotency_key(env):
    headers = _login(env.client, "cus-cls-nokey")
    created = env.client.post(
        "/api/v1/customs/classifications",
        json={"product_name": "塑料托盘"},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    cid = created.json()["id"]
    resp = env.client.post(
        f"/api/v1/customs/classifications/{cid}/manual",
        json={"hs_code": "3926909090"},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "Idempotency-Key" in resp.json()["detail"]


def test_api_visibility_is_404_not_403(env):
    """别人的归类记录：404（不泄漏存在性），不是 403（等于承认它存在）。"""
    owner = _login(env.client, "cus-cls-a")
    other = _login(env.client, "cus-cls-b")
    created = env.client.post(
        "/api/v1/customs/classifications",
        json={"product_name": "钢结构支架"},
        headers={**owner, "Idempotency-Key": uuid.uuid4().hex},
    )
    cid = created.json()["id"]
    assert (
        env.client.get(f"/api/v1/customs/classifications/{cid}", headers=other).status_code == 404
    )
    assert (
        env.client.get(f"/api/v1/customs/classifications/{cid}", headers=owner).status_code == 200
    )


def test_api_list_only_returns_own_rows(env):
    a = _login(env.client, "cus-cls-la")
    b = _login(env.client, "cus-cls-lb")
    env.client.post(
        "/api/v1/customs/classifications",
        json={"product_name": "钢结构支架"},
        headers={**a, "Idempotency-Key": uuid.uuid4().hex},
    )
    mine = env.client.get("/api/v1/customs/classifications", headers=b).json()
    assert mine["total"] == 0


def test_degraded_record_is_persisted_for_audit(session, monkeypatch):
    """降级原因必须落库：事后要能回答"当时模型为什么没给建议"。"""

    async def boom(**_kw):
        raise cls.LLMError("rate_limit", "429")

    monkeypatch.setattr(cls, "chat_json", boom)
    out = _propose(session)
    row = (
        session.execute(
            text(
                "SELECT status, degraded_kind, degraded_reason FROM cus_classification WHERE id = :i"
            ),
            {"i": out["id"]},
        )
        .mappings()
        .one()
    )
    assert row["status"] == "manual_required"
    assert row["degraded_kind"] == "rate_limit"
    assert row["degraded_reason"] == cls.DEGRADED_REASON["rate_limit"]
