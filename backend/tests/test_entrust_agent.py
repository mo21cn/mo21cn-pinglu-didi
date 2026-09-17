"""会话、Agent 作业与类型化信封测试（ENT-011 / S2 第 5~8 条）。

七组断言：
1. **信封校验（AC-08）**：非对象、字段类型错、未知成果类型 → 作业失败；
   缺必填字段与未知字段被**记录**；编造来源被标记；`requires_review` 被强制改真；
2. **提案 ≠ 动作（AC-09）**：作业成功**不产生任何成果行**，信封只是提案载体；
3. **客户投影**：内部成本类提案在服务端就被剔除（不是让前端折叠）；
4. **专业槽位**：五个专业全部可列出，只有两个开放；未开放专业建会话/提作业被拒；
5. **会话**：消息序号单调、归档后拒绝新消息、绑定不可变、无上下文会话被拒；
6. **作业状态机**：提交与执行分离、重复推进 409、取消、租约过期可回收、
   有界重试、尝试日志逐条留痕；
7. **权限与幂等（AC-10 / ENT-002）**：非参与方 404、只读成员 403、缺幂等键 400、
   同键重放、同键异体 409、开关关闭 404；
8. **报价单附件引用链（S2 第三片 / BP-02 attachment selection）**：上传 → 提取 →
   引用 → Agent 读到的**就是附件文本**；并钉住两个"看起来都对"的断点：
   未提取的附件不可被当作文本来源；带 `quote_text` 时附件被**静默忽略**（优先序）。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.agent.llm import LLMError  # noqa: E402
from app.modules.entrust import agentjobs as jobs  # noqa: E402
from app.modules.entrust import envelope as env_mod  # noqa: E402
from app.modules.entrust import sessions as sess  # noqa: E402

_TS = "2026-09-13 00:00:00"
ALL_PERMS = (
    '["entrust:view","entrust:quote:create","entrust:quote:publish",'
    '"entrust:agent:job","entrust:task:dispatch"]'
)
READ_ONLY_PERMS = '["entrust:view"]'


# ─────────────────────────────────────────── fixtures / 播种


@pytest.fixture()
def session():
    """独立 SQLite 内存库会话（服务层直测）。"""
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    """TestClient + 播种会话；ENTRUST_ENABLED=True，模型走确定性 fixture。"""
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
    # 与 conftest 的 force_wechat_mock 同理：本机 .env.local 里一旦有真实 Key，
    # 用例就会真的发网络请求 —— 不可复现，且把"真实凭据"变成测试前提。
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _org(db, name="测试组织", status="active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": f"{name}-{uuid.uuid4().hex[:6]}", "s": status, "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _member(db, org_id: int, user_id: int, role: str = "manager") -> None:
    db.execute(
        text(
            "INSERT INTO ent_org_member (org_id, user_id, member_role, status, created_at) "
            "VALUES (:o, :u, :r, 'active', :c)"
        ),
        {"o": org_id, "u": user_id, "r": role, "c": _TS},
    )
    db.commit()


def _entrust(db, org_id: int, owner_id: int, permissions: str = ALL_PERMS) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_entrustment (org_id, entrust_user_id, permissions, status,"
            " created_at) VALUES (:o, :u, :p, 'active', :c)"
        ),
        {"o": org_id, "u": owner_id, "p": permissions, "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _assignment(
    db, *, owner_id: int, org_id: int, cargo_summary: str | None = "钢材 500 吨", quantity=None
) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_assignment "
            "(owner_user_id, org_id, title, cargo_summary, quantity, quantity_unit, status, "
            " revision, created_at, updated_at) "
            "VALUES (:o, :g, :t, :cs, :q, :u, 'claimed', 1, :c, :c)"
        ),
        {
            "o": owner_id,
            "g": org_id,
            "t": "南宁→贵港 钢材运输",
            "cs": cargo_summary,
            "q": quantity,
            "u": "吨" if quantity is not None else None,
            "c": _TS,
        },
    )
    db.commit()
    return int(result.lastrowid or 0)


def _task(db, *, assignment_id: int, status="pending", title="收集单证", created_by=1) -> int:
    result = db.execute(
        text(
            "INSERT INTO ent_workflow_task "
            "(assignment_id, task_type, title, status, created_by, created_at, updated_at) "
            "VALUES (:a, 'collect_documents', :t, :s, :by, :c, :c)"
        ),
        {"a": assignment_id, "t": title, "s": status, "by": created_by, "c": _TS},
    )
    db.commit()
    return int(result.lastrowid or 0)


def _run(coro):
    """在本进程内跑一个协程（用例里没有事件循环，asyncio.run 最直接）。"""
    return asyncio.run(coro)


def _login(client, prefix: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"code": f"{prefix}-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(data: dict, key: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _seed(env, db, *, permissions: str = ALL_PERMS):
    """组织 + 经理成员 + 货主授权 + 一张已受理委托单。"""
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=owner["user_id"], permissions=permissions)
    aid = _assignment(db, owner_id=owner["user_id"], org_id=org)
    return manager, owner, org, eid, aid


def _make_session(env, manager, *, eid: int, aid: int | None = None, specialty="agent_01"):
    return env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": specialty,
            "title": "报价解析",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )


# ─────────────────────────────────────────── 1. 信封校验（AC-08）


def test_envelope_rejects_non_object():
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.parse_envelope(["not", "an", "object"])
    assert err.value.kind == "schema"


def test_envelope_rejects_missing_required_structure():
    """缺 `summary` / `base_revision` 这类结构字段 → 结构校验失败。"""
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.parse_envelope({"base_revision": 1})
    assert err.value.kind == "schema"


def test_envelope_rejects_wrong_container_type():
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.validate_envelope(
            {"base_revision": 1, "summary": "x", "artifact_proposals": "not-a-list"}
        )
    assert err.value.kind == "schema"


def test_envelope_rejects_unknown_artifact_type():
    """未知成果类型是**取值域**问题 → 直接失败（与"多写一个字段"不同）。"""
    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        env_mod.validate_envelope(
            {
                "base_revision": 1,
                "summary": "x",
                "artifact_proposals": [{"artifact_type": "settlement_final", "payload": {}}],
            }
        )
    assert err.value.kind == "unknown_artifact_type"


def test_envelope_records_missing_and_unknown_fields_without_failing():
    result = env_mod.validate_envelope(
        {
            "base_revision": 3,
            "summary": "解析完成",
            "artifact_proposals": [
                {"artifact_type": "quote_parsed", "payload": {"carrier": "某物流", "extra": 1}}
            ],
        }
    )
    assert result.proposal_missing_fields["quote_parsed"] == ["rate"]
    assert result.proposal_unknown_fields["quote_parsed"] == ["extra"]
    assert result.envelope.base_revision == 3


def test_envelope_marks_fabricated_sources():
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [
                {"kind": "attachment", "ref": "999"},
                {"kind": "assignment", "ref": "1"},
            ],
        },
        known_source_refs=frozenset({("assignment", "1")}),
    )
    assert result.unverified_sources == [
        {"kind": "attachment", "ref": "999", "where": "source_refs"}
    ]


# ── 来源核对的六类情形（HO 0917-3 裁定二：回归必须覆盖这些） ──────────────
#
# 背景：2026-09-17 的 live 运行暴露出**两个**缺口 ——
#   ① 校验只看顶层 `source_refs`，`findings[].source_refs` 里的引用**完全没被核对**；
#   ② 模型没有可原样复制的来源目录，于是照着文件名写了描述串。
# 下面六条把"什么叫合法引用"钉死，其中第 6 条是本轮新覆盖的通道。


def test_source_ref_verification_accepts_exact_pairs_everywhere():
    """① 合法引用（顶层 + findings 内）⇒ 一条都不该被标记。"""
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [{"kind": "attachment_text", "ref": "7"}],
            "findings": [
                {
                    "code": "carrier_unparsed",
                    "message": "m",
                    "source_refs": [{"kind": "attachment", "ref": "7"}],
                }
            ],
        },
        known_source_refs=frozenset({("attachment", "7"), ("attachment_text", "7")}),
    )
    assert result.unverified_sources == []


def test_source_ref_rejects_descriptive_string():
    """② 描述串（本次 live 的真实形态）⇒ 标记 —— 目录里只有纯 id。"""
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [
                {
                    "kind": "attachment",
                    "ref": "#1 DEMO1-SYNTHETIC-sample-quotation.txt（text/plain）",
                }
            ],
        },
        known_source_refs=frozenset({("attachment", "1"), ("attachment_text", "1")}),
    )
    assert len(result.unverified_sources) == 1
    assert result.unverified_sources[0]["ref"].startswith("#1 ")


def test_source_ref_cannot_swap_kind():
    """③ 错误 kind ⇒ 标记。`attachment` 只说"附件存在"，`attachment_text` 才表示
    "文本被读进来了" —— 两者不是同一个事实，不能互相顶替。"""
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [{"kind": "attachment", "ref": "7"}],
        },
        # 附件存在，但**没有** attachment_text（即：没提取出文本）
        known_source_refs=frozenset({("attachment", "7")}),
    )
    assert result.unverified_sources == []

    swapped = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [{"kind": "attachment_text", "ref": "7"}],
        },
        known_source_refs=frozenset({("attachment", "7")}),
    )
    assert swapped.unverified_sources == [
        {"kind": "attachment_text", "ref": "7", "where": "source_refs"}
    ]


def test_source_ref_rejects_out_of_scope_id():
    """④ 越权 ID：另一条委托的附件编号不在本次目录里 ⇒ 标记。"""
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [{"kind": "attachment", "ref": "42"}],
        },
        known_source_refs=frozenset({("attachment", "7")}),
    )
    assert result.unverified_sources == [
        {"kind": "attachment", "ref": "42", "where": "source_refs"}
    ]


def test_source_ref_rejects_unextracted_attachment_text():
    """⑤ 未提取的附件**不可**被当作文本来源引用。

    目录本身已经这么构造（`build_source_catalog` 只在 `extract_status='done'`
    时才加 `attachment_text`），本用例钉住的是"引用它就会被标记"这一后果。
    """
    from app.modules.entrust.agents.runner import build_source_catalog

    catalog = build_source_catalog(
        {
            "assignment": {"assignment_id": 1},
            "attachments": [
                {"attachment_id": 7, "filename": "q.txt", "extract_status": "not_requested"}
            ],
        },
        {"attachment_id": 7},
    )
    assert ("attachment", "7") in catalog
    assert ("attachment_text", "7") not in catalog

    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [{"kind": "attachment_text", "ref": "7"}],
        },
        known_source_refs=catalog,
    )
    assert result.unverified_sources == [
        {"kind": "attachment_text", "ref": "7", "where": "source_refs"}
    ]


def test_source_ref_verification_covers_nested_findings():
    """⑥ **嵌套**错误引用必须被抓到 —— 这是本轮的缺口所在。

    校验原先只遍历顶层 `source_refs`：模型在 `findings[].source_refs` 里编来源
    完全不会被发现，而 findings 恰恰是它解释"我为什么这么判"的地方。
    """
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "source_refs": [{"kind": "assignment", "ref": "1"}],
            "findings": [
                {
                    "code": "validity_missing",
                    "message": "m",
                    "source_refs": [
                        {"kind": "attachment", "ref": "999"},
                        {"kind": "assignment", "ref": "1"},
                    ],
                },
                {
                    "code": "carrier_unparsed",
                    "message": "m",
                    "source_refs": [{"kind": "attachment_text", "ref": "abc"}],
                },
            ],
        },
        known_source_refs=frozenset({("assignment", "1")}),
    )
    assert result.unverified_sources == [
        {"kind": "attachment", "ref": "999", "where": "findings[0].source_refs"},
        {"kind": "attachment_text", "ref": "abc", "where": "findings[1].source_refs"},
    ]


def test_quote_prompt_lists_copyable_source_catalog():
    """提示词必须给出**可原样复制**的来源目录，且与校验用的是同一份集合。

    只写"只能引用存在的来源"没有可操作性：模型不知道 ref 长什么样，
    于是写一个看起来合理的描述串（实测形态）。
    """
    from app.modules.entrust.agents.runner import SPECIALTY_MODULES, build_source_catalog

    context = {
        "assignment": {"assignment_id": 1, "revision": 2, "cargo_summary": "钢材"},
        "attachments": [
            {
                "attachment_id": 7,
                "filename": "q.txt",
                "content_type": "text/plain",
                "extract_status": "done",
                "text_excerpt": "报价方：某某航运\n单价：45.00 元/吨",
            }
        ],
    }
    catalog = build_source_catalog(context, {"attachment_id": 7})
    prompt = SPECIALTY_MODULES["agent_02"].build_user_prompt(context, {"attachment_id": 7}, catalog)
    assert "【可引用的来源目录" in prompt
    for kind, ref in sorted(catalog):
        assert f"- kind={kind} ref={ref}" in prompt
    # 文本来自附件提取 ⇒ 提示词必须点名 `attachment_text`，否则模型很可能只写 `attachment`
    assert "kind=attachment_text ref=7" in prompt
    assert "必须" in prompt and "attachment_text" in prompt


def test_quote_parsing_separates_rate_unit_from_quantity():
    """`rate` 的分母是**计价单位**，不是数量单位。

    旧写法把 `元/吨` 的"吨"记成 `quantity_unit`：两者恰好同名时看不出问题，
    换成"每柜 3000 元"就会凭空造出一个不存在的数量事实（HO 0917-3 裁定二）。
    """
    from app.modules.entrust.agents import ag02

    parsed = ag02._parse_quote_text("报价方：某某航运\n单价：3000.00 元/柜\n有效期至：2026-12-31")
    assert parsed["rate"] == "3000.00"
    assert parsed["rate_unit"] == "柜"
    # 只写了每柜价、没写数量 ⇒ 不得出现任何数量字段
    assert "quantity" not in parsed
    assert "quantity_unit" not in parsed

    full = ag02._parse_quote_text(
        "报价方：某某航运\n单价：45.00 元/吨\n数量：1200 吨\n币种：人民币\n"
    )
    assert full["rate_unit"] == "吨"
    assert full["quantity"] == "1200.00"
    assert full["quantity_unit"] == "吨"
    assert full["currency"] == "CNY"


@pytest.mark.parametrize(
    ("filename", "quantity"),
    [
        ("DEMO1-canonical-sample-quotation.txt", "800.00"),
        ("DEMO1-SYNTHETIC-sample-quotation.txt", "1200.00"),
    ],
)
def test_quote_parsing_on_the_real_fixtures_does_not_slice_sentences(
    filename: str, quantity: str
) -> None:
    """真夹具上的抽取边界：**不许把句子片段当成公司名、把并列短语当成航线**。

    这是 HO 0917-3 待裁决清单第 3 行「fixture 解析误判」的回归钉子。

    为什么必须**读真夹具文件**、而不是复用上面的 `_SAMPLE_QUOTE`：那份删减副本
    只有 5 行，**不含夹具首部的数据标签句**（`…也不是真实航运报价`）与
    「运输方案（公路 — 内河 — 公路）」那一行 —— 而缺陷恰好长在这两处。
    实测：旧规则在删减副本上一直绿，在真夹具上给出
    `carrier="也不是真实航运"`、`route="公路→内河"`。

    修的是**规则边界**，**不改夹具文本**：夹具是对外承诺的一部分（合同 §3.1 /
    `DEMO-1-fixture-manifest.md`），靠改夹具让用例变绿等于把缺陷藏起来。
    """
    import pathlib

    from app.modules.entrust.agents import ag02

    fixture = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fixtures" / filename
    payload = ag02._parse_quote_text(fixture.read_text(encoding="utf-8"))

    assert payload["carrier"] == "西江航运有限公司", payload
    assert payload["route"] == "南宁→贵港", payload
    assert payload["rate"] == "45.00", payload
    assert payload["rate_unit"] == "吨", payload
    assert payload["valid_until"] == "2026-12-31", payload
    assert payload["quantity"] == quantity, payload


def test_quote_parsed_contract_now_covers_currency_and_unit():
    """`currency` / `rate_unit` / `includes` / `excludes` 必须在字段契约内。

    它们此前不在契约里 ⇒ 真实模型给出的这四个字段被登记成**未知字段**，
    而它们恰好是 BP-02 要展示的业务内容，不能长期靠"任意 JSON 字段"承载。
    """
    from app.modules.entrust import registry as reg

    spec = reg.get_spec("quote_parsed")
    for field in ("currency", "rate_unit", "includes", "excludes"):
        assert field in spec.optional_fields, field
    assert (
        reg.validate_payload(
            "quote_parsed",
            {
                "carrier": "某某航运",
                "rate": "45.00",
                "currency": "CNY",
                "rate_unit": "吨",
                "includes": ["装船", "卸船"],
                "excludes": ["港建费"],
            },
        )
        == []
    )
    assert (
        reg.unknown_fields(
            "quote_parsed",
            {
                "carrier": "x",
                "rate": "1.00",
                "currency": "CNY",
                "rate_unit": "吨",
                "includes": [],
                "excludes": [],
            },
        )
        == []
    )


def test_envelope_forces_human_review():
    """模型不能自己宣布"不用复核"。"""
    result = env_mod.validate_envelope(
        {"base_revision": 1, "summary": "x", "requires_review": False}
    )
    assert result.envelope.requires_review is True
    assert result.forced_review is True


def test_customer_projection_drops_internal_fields():
    """客户投影在**服务端**剔除内部字段，且非客户可见类型整体剔除。"""
    result = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "artifact_proposals": [
                {
                    "artifact_type": "supplier_compare",
                    "payload": {"candidates": [{"carrier": "A", "rate": "10.00"}]},
                },
                {
                    "artifact_type": "customer_quote",
                    "payload": {"amount": "100.00", "currency": "CNY", "includes": ["运费"]},
                },
            ],
        }
    )
    projected = env_mod.project_envelope_for_operator(result, customer_visible_only=True)
    assert [p["artifact_type"] for p in projected["artifact_proposals"]] == ["customer_quote"]
    assert projected["artifact_proposals"][0]["payload"] == {
        "amount": "100.00",
        "currency": "CNY",
        "includes": ["运费"],
    }


# ─────────────────────────────────────────── 2. 专业槽位


def test_specialties_list_marks_unopened():
    items = {item["code"]: item for item in env_mod.list_specialties()}
    assert items["agent_01"]["open"] is True
    assert items["agent_02"]["open"] is True
    assert items["agent_03"]["open"] is False
    assert items["agent_05"]["open"] is False


@pytest.mark.parametrize("code", ["agent_03", "agent_04", "agent_05", "agent_99"])
def test_unopened_specialty_rejected(code):
    with pytest.raises(env_mod.SpecialtyNotOpenError):
        env_mod.assert_specialty_open(code)


# ─────────────────────────────────────────── 3. 会话（服务层）


def test_session_messages_seq_is_monotonic(session):
    org = _org(session)
    _member(session, org, user_id=1)
    eid = _entrust(session, org, owner_id=2)
    row = sess.create_session(
        session,
        entrustment_id=eid,
        assignment_id=None,
        owner_user_id=2,
        org_id=org,
        created_by=1,
        specialty="agent_01",
        title="受理问答",
    )
    seqs = [
        sess.append_message(
            session,
            session_id=row["session_id"],
            role=sess.ROLE_USER,
            content=f"第{i}条",
            source=sess.SOURCE_MANUAL,
            created_by=1,
        )["seq"]
        for i in range(1, 4)
    ]
    assert seqs == [1, 2, 3]
    assert len(sess.list_messages(session, row["session_id"])) == 3


def test_archived_session_rejects_new_message(session):
    org = _org(session)
    eid = _entrust(session, org, owner_id=2)
    row = sess.create_session(
        session,
        entrustment_id=eid,
        assignment_id=None,
        owner_user_id=2,
        org_id=org,
        created_by=1,
        specialty=None,
        title="通用会话",
    )
    sess.archive_session(session, session_id=row["session_id"], actor_id=1)
    with pytest.raises(sess.SessionArchivedError):
        sess.append_message(
            session,
            session_id=row["session_id"],
            role=sess.ROLE_USER,
            content="还能说吗",
            source=sess.SOURCE_MANUAL,
            created_by=1,
        )


def test_session_requires_binding_and_rejects_bad_role(session):
    with pytest.raises(sess.SessionBindingError):
        sess.create_session(
            session,
            entrustment_id=None,
            assignment_id=None,
            owner_user_id=1,
            org_id=None,
            created_by=1,
            specialty=None,
            title="无上下文",
        )
    org = _org(session)
    eid = _entrust(session, org, owner_id=2)
    row = sess.create_session(
        session,
        entrustment_id=eid,
        assignment_id=None,
        owner_user_id=2,
        org_id=org,
        created_by=1,
        specialty=None,
        title="通用会话",
    )
    with pytest.raises(sess.SessionError):
        sess.append_message(
            session,
            session_id=row["session_id"],
            role="root",
            content="未知角色",
            source=sess.SOURCE_MANUAL,
            created_by=1,
        )
    with pytest.raises(sess.SessionError):
        sess.append_message(
            session,
            session_id=row["session_id"],
            role=sess.ROLE_USER,
            content="未知来源",
            source="telepathy",
            created_by=1,
        )


def test_session_specialty_must_be_open(session):
    org = _org(session)
    eid = _entrust(session, org, owner_id=2)
    with pytest.raises(env_mod.SpecialtyNotOpenError):
        sess.create_session(
            session,
            entrustment_id=eid,
            assignment_id=None,
            owner_user_id=2,
            org_id=org,
            created_by=1,
            specialty="agent_03",
            title="合同助手",
        )


# ─────────────────────────────────────────── 4. 作业状态机（服务层）


def _seed_service(session):
    org = _org(session)
    _member(session, org, user_id=1)
    eid = _entrust(session, org, owner_id=2)
    aid = _assignment(session, owner_id=2, org_id=org, cargo_summary="钢材 500 吨")
    return org, eid, aid


def test_claim_next_consumes_attempt_and_execute_succeeds(session):
    _org_, eid, aid = _seed_service(session)
    _task(session, assignment_id=aid, status="waiting")
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    assert job["status"] == jobs.STATUS_QUEUED
    assert job["attempt_count"] == 0

    claimed = jobs.claim_next(session, worker_id="w1")
    assert claimed is not None
    # 尝试次数在**领取时**消耗：崩溃循环也会被 max_attempts 兜住
    assert claimed["attempt_count"] == 1
    assert claimed["status"] == jobs.STATUS_RUNNING

    scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
    result = _run(jobs.execute_claimed_job(session, job_id=claimed["job_id"], scope=scope))
    assert result["status"] == jobs.STATUS_SUCCEEDED
    assert result["envelope"] is not None
    assert result["envelope"]["requires_review"] is True
    # 受理摘要里的"500 吨"被保守抽取 → 必填字段齐备（缺项派生，不落库）
    assert result["envelope"]["missing_fields"] == []
    # 有 waiting 任务 → 风险提示（缺件会影响后续排期）
    assert any(f["code"] == "tasks_waiting" for f in result["envelope"]["findings"])
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["mocked"] is True


def test_successful_job_creates_no_artifact(session):
    """AC-09：作业成功只产出提案，**不产生任何成果行**。"""
    _org_, eid, aid = _seed_service(session)
    jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    _run(jobs.tick(session, worker_id="w1"))
    count = session.execute(text("SELECT COUNT(*) FROM ent_artifact")).scalar_one()
    assert int(count) == 0


def test_ag02_produces_proposals_but_scope_blocks_cross_specialty(session):
    """AG-02 能产出报价提案；AG-01 产出同类提案会被范围挡下（fail-closed）。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_02",
        base_revision=1,
        job_input={"quote_text": "承运人：桂航物流 38.5 元/吨 有效期至 2026-10-01"},
        created_by=1,
    )
    scope = jobs.scope_for_job(session, job, operator_user_id=1)
    assert scope.allows_artifact_type("quote_parsed") is True

    ag01_scope = sess.build_agent_scope(
        {
            "session_id": None,
            "agent_specialty": "agent_01",
            "owner_user_id": 2,
            "entrustment_id": eid,
            "assignment_id": aid,
            "org_id": scope.org_id,
        },
        operator_user_id=1,
    )
    validation = env_mod.validate_envelope(
        {
            "base_revision": 1,
            "summary": "x",
            "artifact_proposals": [
                {"artifact_type": "quote_parsed", "payload": {"carrier": "A", "rate": "1.00"}}
            ],
        }
    )
    from app.modules.entrust.agents.runner import enforce_scope

    with pytest.raises(env_mod.EnvelopeValidationError) as err:
        enforce_scope(validation, ag01_scope)
    assert err.value.kind == "out_of_scope"


def test_bounded_retry_then_failed(session, monkeypatch):
    """外部抖动（超时）按额度重试，用尽即 failed —— 不无限重试。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=3,
        created_by=1,
    )

    async def boom(**kwargs):
        raise LLMError("timeout", "LLM 调用超时（60s）")

    monkeypatch.setattr(jobs, "run_agent", boom)

    for _ in range(3):
        claimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1")
        assert claimed is not None
        scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
        result = _run(jobs.execute_claimed_job(session, job_id=job["job_id"], scope=scope))
        assert result["error_kind"] == "llm_timeout"

    assert result["status"] == jobs.STATUS_FAILED
    assert result["attempt_count"] == 3
    assert len(result["attempts"]) == 3
    assert result["envelope"] is None
    # 额度用尽后再领也领不到
    assert jobs.claim_job(session, job_id=job["job_id"], worker_id="w2") is None


def test_non_retryable_error_fails_immediately(session, monkeypatch):
    """确定性错误（鉴权失败）不重试：重试只会烧预算。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=3,
        created_by=1,
    )

    async def boom(**kwargs):
        raise LLMError("auth", "LLM API Key 无效或无权限")

    monkeypatch.setattr(jobs, "run_agent", boom)

    claimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1")
    scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
    result = _run(jobs.execute_claimed_job(session, job_id=job["job_id"], scope=scope))
    assert result["status"] == jobs.STATUS_FAILED
    assert result["error_kind"] == "llm_auth"
    assert result["attempt_count"] == 1


def test_invalid_output_does_not_become_fact(session, monkeypatch):
    """AC-08：信封校验失败 → 作业失败且**没有信封**（谈不上成为事实）。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_02",
        base_revision=1,
        created_by=1,
    )

    async def bad(**kwargs):
        return SimpleNamespace(
            raw={"base_revision": 1, "summary": "x", "artifact_proposals": "oops"},
            raw_text="{}",
            latency_ms=5,
            mocked=True,
        )

    monkeypatch.setattr(jobs, "run_agent", bad)

    claimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1")
    scope = jobs.scope_for_job(session, claimed, operator_user_id=1)
    result = _run(jobs.execute_claimed_job(session, job_id=job["job_id"], scope=scope))
    assert result["status"] == jobs.STATUS_FAILED
    assert result["error_kind"] == "schema"
    assert result["envelope"] is None
    assert session.execute(text("SELECT COUNT(*) FROM ent_artifact")).scalar_one() == 0


def test_lease_expiry_allows_reclaim(session):
    """worker 崩溃后租约过期，作业可被重新领取（重启不丢任务）。"""
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=5,
        created_by=1,
    )
    now = jobs.utcnow_naive()
    first = jobs.claim_job(session, job_id=job["job_id"], worker_id="w1", now=now)
    assert first is not None
    # 租约期内别人抢不到
    assert jobs.claim_job(session, job_id=job["job_id"], worker_id="w2", now=now) is None
    later = now + timedelta(seconds=jobs.DEFAULT_LEASE_SECONDS + 5)
    reclaimed = jobs.claim_job(session, job_id=job["job_id"], worker_id="w2", now=later)
    assert reclaimed is not None
    assert reclaimed["attempt_count"] == 2
    assert reclaimed["lease_owner"] == "w2"


def test_expired_lease_with_attempts_exhausted_is_failed(session):
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=1,
        created_by=1,
    )
    now = jobs.utcnow_naive()
    jobs.claim_job(session, job_id=job["job_id"], worker_id="w1", now=now)
    reaped = jobs.reap_expired(session, now=now + timedelta(seconds=jobs.DEFAULT_LEASE_SECONDS + 5))
    assert reaped == 1
    after = jobs.get_job(session, job["job_id"])
    assert after["status"] == jobs.STATUS_FAILED
    assert after["error_kind"] == "lease_expired"


def test_cancel_and_retry_transitions(session):
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    cancelled = jobs.cancel_job(session, job_id=job["job_id"], actor_id=1)
    assert cancelled["status"] == jobs.STATUS_CANCELLED
    # 幂等：再取消一次不报错
    assert jobs.cancel_job(session, job_id=job["job_id"], actor_id=1)["status"] == (
        jobs.STATUS_CANCELLED
    )
    with pytest.raises(jobs.AgentJobStateError):
        jobs.retry_job(session, job_id=job["job_id"], actor_id=1)

    other = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        created_by=1,
    )
    jobs.cancel_job(session, job_id=other["job_id"], actor_id=1)
    assert jobs.claim_job(session, job_id=other["job_id"], worker_id="w") is None


def test_retry_resets_attempts(session):
    _org_, eid, aid = _seed_service(session)
    job = jobs.submit_job(
        session,
        session_id=None,
        entrustment_id=eid,
        assignment_id=aid,
        specialty="agent_01",
        base_revision=1,
        max_attempts=1,
        created_by=1,
    )
    now = jobs.utcnow_naive()
    jobs.claim_job(session, job_id=job["job_id"], worker_id="w1", now=now)
    jobs.reap_expired(session, now=now + timedelta(seconds=jobs.DEFAULT_LEASE_SECONDS + 5))
    assert jobs.get_job(session, job["job_id"])["status"] == jobs.STATUS_FAILED

    retried = jobs.retry_job(session, job_id=job["job_id"], actor_id=1)
    assert retried["status"] == jobs.STATUS_QUEUED
    assert retried["attempt_count"] == 0
    assert retried["error_kind"] is None


# ─────────────────────────────────────────── 5. API 层：权限 / 幂等 / 开关


def test_create_session_and_job_end_to_end(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    _task(db, assignment_id=aid, status="waiting")

    resp = _make_session(env, manager, eid=eid, aid=aid)
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]
    assert resp.json()["agent_specialty_label"] == "委托助理"

    msg = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/messages",
        json={"content": "帮我看看还缺什么"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert msg.status_code == 200, msg.text
    assert msg.json()["role"] == "user"
    assert msg.json()["source"] == "manual"
    assert msg.json()["seq"] == 1

    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={"base_revision": 1},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 200, job.text
    jid = job.json()["job_id"]
    assert job.json()["status"] == "queued"
    assert job.json()["requires_review"] is True

    run = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["job"]["status"] == "succeeded"
    assert body["job"]["envelope"]["summary"]
    assert body["attempts"][0]["mocked"] is True
    assert "raw_output" not in body["attempts"][0]

    # 重复推进：状态机结构性拦下（不消耗尝试次数）
    again = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert again.status_code == 409

    detail = env.client.get(f"/api/v1/entrust/agent/jobs/{jid}", headers=_headers(manager))
    assert detail.status_code == 200
    assert detail.json()["job"]["attempt_count"] == 1

    # ── S2 首片：页面实际调用的两个查询 ──────────────────────────────
    # 会话页从工作台进来时只有委托单号，`load()` 靠这两个端点恢复现场。
    # 服务层那条测试证明的是过滤逻辑；这里证明的是**参数真的透传到了 HTTP 层**
    # ——只测服务层会漏掉"查询参数忘了声明"这种错。
    found = env.client.get(
        f"/api/v1/entrust/sessions?view=mine&assignment_id={aid}", headers=_headers(manager)
    )
    assert found.status_code == 200, found.text
    assert found.json()["total"] == 1
    assert found.json()["items"][0]["session_id"] == sid

    # 反事实：换一个没有会话的委托单号必须为空，而不是"忽略参数返回全部"
    none_ = env.client.get(
        "/api/v1/entrust/sessions?view=mine&assignment_id=999999", headers=_headers(manager)
    )
    assert none_.status_code == 200
    assert none_.json()["total"] == 0

    # "离开后重新进入仍可恢复"：按委托单号能取回本单作业
    jl = env.client.get(
        f"/api/v1/entrust/agent/jobs?assignment_id={aid}", headers=_headers(manager)
    )
    assert jl.status_code == 200, jl.text
    assert jl.json()["total"] == 1
    assert jl.json()["items"][0]["job_id"] == jid


def test_outsider_cannot_see_or_touch_session(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]
    outsider = _login(env.client, "stranger")

    # 非参与方：404（不区分"不存在"与"无权"）
    assert (
        env.client.get(f"/api/v1/entrust/sessions/{sid}", headers=_headers(outsider)).status_code
        == 404
    )
    assert (
        env.client.post(
            f"/api/v1/entrust/sessions/{sid}/jobs",
            json={},
            headers=_headers(outsider, uuid.uuid4().hex),
        ).status_code
        == 404
    )


def test_readable_but_write_requires_agent_job_permission(env):
    """可见性由授权链给（组织成员即可读）；**写**还要求授权里含 `entrust:agent:job`。

    这是 ENT-003 叠加层的既有语义：按货主作用域的权限判定只看**该货主的生效授权**，
    所以把授权收窄成只读后，连 manager 角色也做不了 Agent 作业 —— 想要能干活，
    货主必须显式授予这个动作。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db, permissions=READ_ONLY_PERMS)
    # 会话由服务层直接建（本用例验证的是读写权限差异，不是建会话本身）
    row = sess.create_session(
        db,
        entrustment_id=eid,
        assignment_id=aid,
        owner_user_id=owner["user_id"],
        org_id=org,
        created_by=manager["user_id"],
        specialty="agent_01",
        title="只读场景",
    )
    sid = row["session_id"]

    assert (
        env.client.get(f"/api/v1/entrust/sessions/{sid}", headers=_headers(manager)).status_code
        == 200
    )
    assert (
        env.client.post(
            f"/api/v1/entrust/sessions/{sid}/jobs",
            json={},
            headers=_headers(manager, uuid.uuid4().hex),
        ).status_code
        == 403
    )
    assert (
        env.client.post(
            f"/api/v1/entrust/sessions/{sid}/archive",
            headers=_headers(manager, uuid.uuid4().hex),
        ).status_code
        == 403
    )


def test_owner_cannot_open_manager_session(env):
    """货主不是组织成员：建会话 404（授权链判定先于权限）。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "agent_specialty": "agent_01", "title": "x"},
        headers=_headers(owner, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


def test_session_assignment_must_match_entrustment(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    other_owner = _login(env.client, "shipper2")
    stray = _assignment(db, owner_id=other_owner["user_id"], org_id=org)
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "assignment_id": stray, "title": "x"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 400


def test_unopened_specialty_endpoint_400(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    resp = _make_session(env, manager, eid=eid, aid=aid, specialty="agent_03")
    assert resp.status_code == 400
    assert "未开放" in resp.json()["detail"]


def test_specialties_endpoint_lists_five(env):
    db = env.make_session()
    manager, _, _, _, _ = _seed(env, db)
    resp = env.client.get("/api/v1/entrust/agent/specialties", headers=_headers(manager))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 5
    assert sum(1 for i in items if i["open"]) == 2


def test_idempotency_required_and_replay(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)

    missing = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "agent_specialty": "agent_01", "title": "x"},
        headers=_headers(manager),
    )
    assert missing.status_code == 400

    key = uuid.uuid4().hex
    first = _make_session(env, manager, eid=eid, aid=aid)
    assert first.status_code == 200
    replay = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": "agent_01",
            "title": "报价解析",
        },
        headers=_headers(manager, key),
    )
    assert replay.status_code == 200
    again = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": "agent_01",
            "title": "报价解析",
        },
        headers=_headers(manager, key),
    )
    assert again.status_code == 200
    assert again.json()["session_id"] == replay.json()["session_id"]

    conflict = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={
            "entrustment_id": eid,
            "assignment_id": aid,
            "agent_specialty": "agent_01",
            "title": "换个标题",
        },
        headers=_headers(manager, key),
    )
    assert conflict.status_code == 409


def test_disabled_feature_hides_endpoints(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]

    from app.core.config import get_settings

    original = get_settings().ENTRUST_ENABLED
    get_settings().ENTRUST_ENABLED = False
    try:
        assert (
            env.client.get(f"/api/v1/entrust/sessions/{sid}", headers=_headers(manager)).status_code
            == 404
        )
        assert (
            env.client.get(
                "/api/v1/entrust/agent/specialties", headers=_headers(manager)
            ).status_code
            == 404
        )
    finally:
        get_settings().ENTRUST_ENABLED = original


def test_archived_session_blocks_new_job(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]
    arch = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/archive",
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert arch.status_code == 200, arch.text
    assert arch.json()["status"] == "archived"
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 409


def test_agent02_end_to_end_reports_cost_constraints(env):
    """AG-02：解析报价 → 提案含 quote_parsed，且缺有效期被提示为风险。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    sid = _make_session(env, manager, eid=eid, aid=aid, specialty="agent_02").json()["session_id"]
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={
            "base_revision": 1,
            "input": {
                "quote_text": "承运人：桂航物流 38.5 元/吨",
                "candidates": [
                    {"carrier": "桂航物流", "rate": "38.50"},
                    {"carrier": "西江航运", "rate": "36.00"},
                ],
            },
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 200, job.text
    jid = job.json()["job_id"]
    run = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert run.status_code == 200, run.text
    envelope = run.json()["job"]["envelope"]
    types = [p["artifact_type"] for p in envelope["artifact_proposals"]]
    assert "quote_parsed" in types
    assert "supplier_compare" in types
    assert any(f["code"] == "validity_missing" for f in envelope["findings"])
    compare = next(
        p for p in envelope["artifact_proposals"] if p["artifact_type"] == "supplier_compare"
    )
    assert compare["payload"]["selected_candidate"] == "西江航运"


def test_job_requires_specialty(env):
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    resp = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "assignment_id": aid, "title": "通用壳"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]
    assert resp.json()["agent_specialty"] is None
    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 400


def test_quota_error_is_not_retryable():
    """`llm_quota`（余额/配额耗尽）**不可重试** —— 重试不会让账户有钱。

    与 `llm_timeout` / `llm_rate_limit` 对照：那些是外部抖动，重试有意义。
    """
    assert jobs.is_retryable("llm_quota") is False
    assert jobs.is_retryable("llm_bad_request") is False
    assert jobs.is_retryable("llm_timeout") is True
    assert jobs.is_retryable("llm_rate_limit") is True
    # 未分类的一律不重试（宁可停下来人工看，也不要盲目重试掩盖问题）
    assert jobs.is_retryable(None) is False
    assert jobs.is_retryable("llm_unknown") is False


def test_list_sessions_filters_by_assignment(session):
    """S2 首片：`GET /sessions` 必须能按委托单号过滤。

    会话页从工作台进来时手里**只有委托单号**，没有委托授权号。没有这个过滤，
    页面就得把整页会话拉下来在前端筛 —— 既多传数据，又会把"该组织别的单子的会话"
    一并暴露给前端。

    反事实那一条是必要的：只断言"过滤后是 1 条"证明不了过滤生效 ——
    万一建会话那一步本身只建了一条，这个断言照样过。
    """
    sess.create_session(
        session,
        entrustment_id=1,
        assignment_id=101,
        owner_user_id=7,
        org_id=3,
        created_by=7,
        specialty=None,
        title="委托 101 的会话",
    )
    sess.create_session(
        session,
        entrustment_id=1,
        assignment_id=102,
        owner_user_id=7,
        org_id=3,
        created_by=7,
        specialty=None,
        title="委托 102 的会话",
    )

    total_all, _ = sess.list_sessions(session, created_by=7)
    assert total_all == 2, "反事实：不过滤时应有 2 条（否则下面那条断言没有意义）"

    total, items = sess.list_sessions(session, created_by=7, assignment_id=102)
    assert total == 1
    assert items[0]["title"] == "委托 102 的会话"
    assert int(items[0]["assignment_id"]) == 102

    # 查一个没有会话的委托单 ⇒ 空，而不是"忽略过滤条件返回全部"
    total_none, items_none = sess.list_sessions(session, created_by=7, assignment_id=999)
    assert total_none == 0
    assert items_none == []


# ─────────────────────────── S2 首片：会话上下文（该用哪条委托授权）


def test_session_context_resolves_the_only_matching_entrustment(env):
    """经理要建会话，就必须有一个**合法**拿到授权 id 的地方。

    经理不在 `/my-entrustments` 里（那是"货主自己授权出去"的视角），
    `/my-orgs` 又只回成员身份 —— 缺了这个端点，界面只能猜一个 id 试到不报错为止。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)

    resp = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assignment_id"] == aid
    assert body["org_id"] == org
    assert body["entrustment_id"] == eid
    assert body["note"] == ""


def test_session_context_id_is_actually_accepted_by_create(env):
    """交叉断言：上下文给出的 id 必须**真的能用**。

    只断言"字段非空"不够 —— 一个查错的 id 同样非空，而界面会在建会话时撞
    400/403，表现为"能打开页面但建不了会话"。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    ctx = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert ctx["entrustment_id"] == eid, "上下文给的必须就是那条授权"
    made = _make_session(env, manager, eid=ctx["entrustment_id"], aid=aid)
    assert made.status_code == 200, made.text
    assert int(made.json()["assignment_id"]) == aid
    assert int(made.json()["entrustment_id"]) == eid


def test_session_context_agrees_with_assignment_detail_on_org(env):
    """`org_id` 必须与委托详情同一事实 —— 两处不一致时界面无从判断该信谁。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    detail = env.client.get(f"/api/v1/entrust/assignments/{aid}", headers=_headers(manager))
    assert detail.status_code == 200, detail.text
    ctx = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert ctx["org_id"] == detail.json().get("org_id")
    assert ctx["org_id"] == org


def test_session_context_does_not_guess_when_assignment_has_no_org(env):
    """反事实 ①：委托没选服务经营主体 ⇒ 不许猜一条授权回来。

    ⚠️ 这条必须用**货主本人**调用：`org_id` 一旦为 NULL，组织侧的可见性就没了
    （经理是靠组织成员身份才看得到这单），用经理会得到 404 而不是本用例要验的分支。
    反过来这也顺带证明了查找与调用者身份无关 —— 货主不是任何组织的成员，
    却仍能问出"这单所属的(货主,组织)"这件事。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    db.execute(text("UPDATE ent_assignment SET org_id = NULL WHERE id = :i"), {"i": aid})
    db.commit()

    resp = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(owner)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["org_id"] is None
    assert body["entrustment_id"] is None
    assert "未指定服务经营主体" in body["note"]


def test_session_context_reports_missing_entrustment_without_guessing(env):
    """反事实 ①b：组织与货主之间没有生效授权 ⇒ 说清楚，而不是随便给一条。

    取值用 `suspended`：`ent_entrustment.status` 的列注释是 `active/suspended`
    （不是 `revoked` —— 那个常量属别的表，别按"常量存在"就假定它属于本列）。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    db.execute(text("UPDATE ent_entrustment SET status = 'suspended' WHERE id = :i"), {"i": eid})
    db.commit()

    body = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert body["entrustment_id"] is None
    assert "没有生效中的委托授权" in body["note"]


def test_session_context_does_not_guess_between_multiple_entrustments(env):
    """反事实 ②：同一(货主, 组织)下有两条生效授权 ⇒ 一条都不自动选中。

    猜错的后果是把会话挂到**另一条**授权上（数据边界随之改变），而界面上看不出来。
    ⚠️ 前提是库里真允许两条：`ent_entrustment` **没有** (org_id, entrust_user_id)
    唯一约束（只有 `ent_org_member` 有），所以这条分支是可达的、不是纯粹的防御代码。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    second = _entrust(db, org, owner_id=owner["user_id"])
    assert second != eid, "夹具必须真的造出第二条，否则这条用例没有意义"

    body = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(manager)
    ).json()
    assert body["entrustment_id"] is None
    assert "多条" in body["note"]


def test_session_context_is_invisible_to_outsiders(env):
    """反事实 ③：非参与方 404 —— 与委托详情同一条可见性判据，不是新判据。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    outsider = _login(env.client, "stranger")
    resp = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/session-context", headers=_headers(outsider)
    )
    assert resp.status_code == 404
    assert "拒绝" in resp.text or "不存在" in resp.text


# ─────────────────────────── BP-02：作业投影带「模式」与「可采纳提案」


def test_job_projection_carries_attempt_mode_and_stays_unknown(env):
    """作业投影必须带「最近一次尝试是不是 fixture」，且**未知保持未知**。

    为什么这是必须的：界面要标明"这是桩输出、不是真实模型结果" —— 合同 §3.2
    把 deterministic fixture 与 live invocation 列为**必须区分**的两种事实。
    而 `mocked` 只存在于尝试行上：投影不带它，页面就**无从判断**，
    只能把桩显示成真实结果（这是"数据没下发"，不是"界面没做"）。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    _task(db, assignment_id=aid, status="waiting")
    sid = _make_session(env, manager, eid=eid, aid=aid).json()["session_id"]

    job = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={"base_revision": 1, "input": {"quote_text": "贵港到梧州，水泥 3000 吨"}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert job.status_code == 200, job.text
    jid = job.json()["job_id"]

    # ① 还没跑过 ⇒ None。**不是 False**："没跑过"与"跑过且不是桩"是两句不同的话。
    listing = env.client.get(
        f"/api/v1/entrust/agent/jobs?assignment_id={aid}", headers=_headers(manager)
    )
    assert listing.status_code == 200, listing.text
    row = [x for x in listing.json()["items"] if x["job_id"] == jid]
    assert row, "刚提交的作业必须出现在列表里（否则下面的断言是空转）"
    assert row[0]["mocked"] is None, "未尝试的作业不得被写成 False"

    # ② 跑完 ⇒ True（`LLM_MOCK=true` ⇒ 规则模板输出）
    run = env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    assert run.status_code == 200, run.text
    assert run.json()["job"]["mocked"] is True

    # ③ 列表与详情必须**同口径** —— 两条路径给出互相矛盾的模式结论是最坏情况
    after = env.client.get(
        f"/api/v1/entrust/agent/jobs?assignment_id={aid}", headers=_headers(manager)
    ).json()
    row2 = [x for x in after["items"] if x["job_id"] == jid][0]
    assert row2["mocked"] is True
    detail = env.client.get(f"/api/v1/entrust/agent/jobs/{jid}", headers=_headers(manager)).json()
    assert detail["job"]["mocked"] is True
    assert detail["attempts"][-1]["mocked"] is True, "投影与尝试行必须一致"


def test_adopt_makes_the_proposal_a_real_artifact(env):
    """BP-02 出口证据：「提案 → 人工采纳 → 同一成果」。

    合同 §10.1 第 3 步要求"更正一个字段并**从工作台打开同一份成果**"，
    所以采纳必须真的落成成果（可被单委托成果清单读到），而不是只回一个 200。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    _task(db, assignment_id=aid, status="waiting")
    # ⚠️ 必须是 **AG-02**：`artifact_proposals` 是报价专业产出的信封结构。
    # `_make_session` 默认 AG-01（那个专业产出的是另一套信封），拿它跑会得到
    # 一个**空提案列表** —— 断言会以"夹具没产出对象"的形式失败，而真因是专业选错。
    sid = _make_session(env, manager, eid=eid, aid=aid, specialty="agent_02").json()["session_id"]

    jid = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={"base_revision": 1, "input": {"quote_text": "贵港到梧州，水泥 3000 吨，每吨 45 元"}},
        headers=_headers(manager, uuid.uuid4().hex),
    ).json()["job_id"]
    env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))

    detail = env.client.get(f"/api/v1/entrust/agent/jobs/{jid}", headers=_headers(manager)).json()
    proposals = (detail["job"].get("envelope") or {}).get("artifact_proposals") or []
    assert proposals, "夹具必须真产出提案（否则本用例没有对象）"

    before = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/artifacts", headers=_headers(manager)
    ).json()
    n_before = len(before["items"])

    p0 = proposals[0]
    adopted = env.client.post(
        f"/api/v1/entrust/agent/jobs/{jid}/adopt",
        json={
            "artifact_type": p0["artifact_type"],
            "payload": p0.get("payload") or {},
            "note": "人工确认后采纳",
        },
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert adopted.status_code == 200, adopted.text
    new_id = adopted.json()["artifact_id"]

    # 真事实：本单成果清单里多出**这一份**（不是"接口返回了 200"）
    after = env.client.get(
        f"/api/v1/entrust/assignments/{aid}/artifacts", headers=_headers(manager)
    ).json()
    ids = [int(x["artifact_id"]) for x in after["items"]]
    assert len(ids) == n_before + 1, f"成果数应 +1：{n_before} → {len(ids)}"
    assert new_id in ids, "采纳产生的成果必须出现在单委托成果清单里（工作台读的同一份）"

    # 幂等：同一幂等键重复采纳不得再产生一份
    key = uuid.uuid4().hex
    first = env.client.post(
        f"/api/v1/entrust/agent/jobs/{jid}/adopt",
        json={"artifact_type": p0["artifact_type"], "payload": p0.get("payload") or {}},
        headers=_headers(manager, key),
    )
    again = env.client.post(
        f"/api/v1/entrust/agent/jobs/{jid}/adopt",
        json={"artifact_type": p0["artifact_type"], "payload": p0.get("payload") or {}},
        headers=_headers(manager, key),
    )
    assert first.status_code == 200 and again.status_code == 200
    assert again.json()["artifact_id"] == first.json()["artifact_id"], "同键重放必须返回同一份"

    # 张冠李戴：该作业没提出的类型必须被拒（400），不是静默成功
    bad = env.client.post(
        f"/api/v1/entrust/agent/jobs/{jid}/adopt",
        json={"artifact_type": "settlement_draft", "payload": {}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert bad.status_code == 400, bad.text


# ─────────────────────────────────────────── 8. 报价单附件引用链（S2 第三片）


#: 合成样报价单（与 `backend/scripts/fixtures/DEMO1-SYNTHETIC-sample-quotation.txt` 同形）。
#: 数值刻意与"操作者粘贴的那一份"不同 —— 否则分不清 Agent 读的是哪一份，
#: 而"读错来源"恰恰是这条链最可能的失败形态。
_SAMPLE_QUOTE = (
    "数据标签：合成（Synthetic）\n"
    "报价方：西江航运有限公司\n"
    "单价：45.00 元/吨\n"
    "有效期至：2026-12-31\n"
    "航线：南宁 → 贵港\n"
)


def _upload_quote(env, manager, eid: int, text: str = _SAMPLE_QUOTE):
    return env.client.post(
        "/api/v1/entrust/attachments",
        files={
            "file": (
                "DEMO1-SYNTHETIC-sample-quotation.txt",
                text.encode("utf-8"),
                "text/plain",
            )
        },
        data={"entrustment_id": str(eid)},
        headers=_headers(manager, uuid.uuid4().hex),
    )


def _submit_and_run(env, manager, sid: int, job_input: dict):
    """提交并推进一次作业，返回作业详情（含 envelope）。"""
    jid = env.client.post(
        f"/api/v1/entrust/sessions/{sid}/jobs",
        json={"base_revision": 1, "input": job_input},
        headers=_headers(manager, uuid.uuid4().hex),
    ).json()["job_id"]
    env.client.post(f"/api/v1/entrust/agent/jobs/{jid}/run", headers=_headers(manager))
    return env.client.get(f"/api/v1/entrust/agent/jobs/{jid}", headers=_headers(manager)).json()[
        "job"
    ]


def _ref_kinds(job: dict) -> set[str]:
    return {str(r.get("kind")) for r in ((job.get("envelope") or {}).get("source_refs") or [])}


def test_quotation_attachment_chain_agent_reads_the_uploaded_text(env):
    """演示第 2 步的整条链：**上传 → 提取 → 引用 → AG-02 读到的就是附件文本**。

    判定不看 HTTP 码，看信封里的 `payload` 与 `source_refs`：
    只断言"作业成功"是不够的 —— 不带输入时它也会**成功地**返回
    `quote_unparsed`，那种"空成功"看起来一切正常。
    """
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    _task(db, assignment_id=aid, status="waiting")
    sid = _make_session(env, manager, eid=eid, aid=aid, specialty="agent_02").json()["session_id"]

    up = _upload_quote(env, manager, eid)
    assert up.status_code == 200, up.text
    att_id = int(up.json()["attachment_id"])
    # 刚上传就是"未提取"：此刻它对 Agent 只是一个文件名
    assert up.json()["extract_status"] == "not_requested"

    # ---- 反事实 A：还没提取就引用 ⇒ Agent 读不到内容 ----
    job_a = _submit_and_run(env, manager, sid, {"attachment_id": att_id})
    assert "attachment_text" not in _ref_kinds(job_a), (
        "未提取的附件不能被当作文本来源引用 —— 否则「编造来源」这条检查就失去意义"
    )
    props_a = (job_a.get("envelope") or {}).get("artifact_proposals") or []
    assert not [p for p in props_a if p["artifact_type"] == "quote_parsed"], (
        "没有文本就不该产出报价解析稿（产出了说明读到了不该读的东西）"
    )

    # ---- 提取 ----
    ex = env.client.post(
        f"/api/v1/entrust/attachments/{att_id}/extract",
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert ex.status_code == 200, ex.text
    assert ex.json()["extract_status"] == "done", ex.text

    # ---- 正路：只带 attachment_id ----
    job = _submit_and_run(env, manager, sid, {"attachment_id": att_id})
    envelope = job.get("envelope") or {}
    proposals = envelope.get("artifact_proposals") or []
    parsed = [p for p in proposals if p["artifact_type"] == "quote_parsed"]
    assert parsed, f"夹具必须真产出报价解析提案：{proposals}"
    payload = parsed[0]["payload"]
    assert payload["rate"] == "45.00", payload
    assert str(payload["carrier"]).startswith("西江航运"), payload
    assert payload["valid_until"] == "2026-12-31", payload
    assert payload["route"] == "南宁→贵港", payload
    kinds = _ref_kinds(job)
    assert "attachment_text" in kinds, f"引用附件的作业必须把 attachment_text 记为来源：{kinds}"
    assert "operator_input" not in kinds, "本次没有操作者粘贴文本，不该出现 operator_input"

    # ---- 反事实 B：同时带 quote_text ⇒ 附件被**静默忽略**（优先序）----
    # 这一条是界面的依据：会话页"引用附件"那条路径**绝不能**带 `quote_text`，
    # 否则页面上一切正常，而 Agent 读的根本不是那份附件。
    job_b = _submit_and_run(
        env,
        manager,
        sid,
        {"attachment_id": att_id, "quote_text": "报价方：另一家物流公司\n单价：999.00 元/吨\n"},
    )
    env_b = job_b.get("envelope") or {}
    parsed_b = [
        p for p in (env_b.get("artifact_proposals") or []) if p["artifact_type"] == "quote_parsed"
    ]
    assert parsed_b, "操作者文本在场时必须仍能解析"
    assert parsed_b[0]["payload"]["rate"] == "999.00", (
        "操作者当面粘贴的文本优先于附件 —— 这是刻意固定的优先序，不是巧合"
    )
    kinds_b = _ref_kinds(job_b)
    assert "operator_input" in kinds_b and "attachment_text" not in kinds_b, (
        f"引用必须是 operator_input，不能把附件也算成来源：{kinds_b}"
    )


def test_attachment_upload_is_scoped_to_the_entrustment(env):
    """反事实：非参与方不能往这条委托授权上挂附件（否则等于往别人的会话里塞输入）。"""
    db = env.make_session()
    manager, owner, org, eid, aid = _seed(env, db)
    outsider = _login(env.client, "outsider")
    resp = _upload_quote(env, outsider, eid)
    assert resp.status_code == 404, resp.text
