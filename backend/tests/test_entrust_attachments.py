"""成果类型注册表与附件测试（ENT-009 / S1 第 5 条）。

六组断言：
1. **类型注册表**：取值域固定、未知类型拒绝、字段契约可查；
2. **缺项与未知字段**：缺项由注册表派生（不靠调用方自报）、未知字段记录但不阻断、
   且天然进不了客户投影；
3. **字段变化清单**：added / removed / changed 三态；
4. **附件落盘**：上限、类型白名单、空文件、文件名安全化、存储位置在静态路径之外；
5. **下载授权**（AC-10）：参与方可下载、非参与方 404（不泄漏存在性）、
   缺权限 403、私有草稿只有归属方可见；
6. **绑定与横切**：跨委托绑定 409、重复绑定幂等、开关 404、缺幂等键 400。
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

from app.modules.entrust import assignments as assign_svc  # noqa: E402
from app.modules.entrust import attachments as att  # noqa: E402
from app.modules.entrust import registry as reg  # noqa: E402

_TS = "2026-09-13 00:00:00"
ALL_PERMS = '["entrust:view","entrust:quote:create","entrust:quote:publish"]'
READ_ONLY_PERMS = '["entrust:view"]'


# ─────────────────────────────────────────── fixtures / 播种


@pytest.fixture()
def session(tmp_path, monkeypatch):
    """独立 SQLite 内存库会话，附件目录指向 tmp_path。"""
    from app.core.config import get_settings
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    apply_pending(engine)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_STORAGE_DIR", str(tmp_path / "att"))
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """TestClient + 播种会话；ENTRUST_ENABLED=True，附件目录指向 tmp_path。"""
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
    monkeypatch.setattr(get_settings(), "ATTACHMENT_STORAGE_DIR", str(tmp_path / "att"))

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
    """组织 + 经理成员 + 货主授权，返回 (manager, owner, org_id, entrustment_id)。"""
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=owner["user_id"], permissions=permissions)
    return manager, owner, org, eid


def _upload(
    env, user, *, key=None, filename="报价.txt", data=b"rate 38", ctype="text/plain", **form
):
    return env.client.post(
        "/api/v1/entrust/attachments",
        files={"file": (filename, data, ctype)},
        data={k: str(v) for k, v in form.items()},
        headers=_headers(user, key or uuid.uuid4().hex),
    )


# ─────────────────────────────────────────── 1. 类型注册表


def test_registry_covers_previously_used_types():
    """历史代码用过的类型必须仍在取值域内 —— 否则等于静默改契约。"""
    for code in ("quote_parsed", "customer_quote", "contract_review"):
        assert code in reg.ARTIFACT_TYPES
    assert set(reg.ARTIFACT_TYPES) >= reg.CUSTOMER_VISIBLE_TYPES


def test_unknown_artifact_type_rejected_at_service_layer(session):
    from app.modules.entrust import artifacts as art

    with pytest.raises(art.ArtifactPayloadError) as exc:
        art.create_artifact(
            session,
            entrustment_id=1,
            artifact_type="not_a_type",
            payload={},
            created_by=1,
        )
    assert "未知成果类型" in str(exc.value)


def test_artifact_types_endpoint(env):
    manager, owner, org, eid = _seed(env, env.make_session())
    resp = env.client.get("/api/v1/entrust/artifact-types", headers=_headers(manager))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    codes = {item["code"] for item in body["items"]}
    assert "quote_parsed" in codes
    assert body["customer_visible_types"] == ["contract_review", "customer_quote"]
    assert "document" in body["evidence_kinds"]
    # 内部字段必须显式声明出来，前端与投影才有依据
    compare = next(i for i in body["items"] if i["code"] == "supplier_compare")
    assert "candidates" in compare["internal_fields"]


# ─────────────────────────────────────────── 2. 缺项与未知字段


def test_missing_fields_derived_from_registry(session):
    from app.modules.entrust import artifacts as art

    a = art.create_artifact(
        session,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"carrier": "船东A"},
        created_by=1,
    )
    assert a["missing_fields"] == ["rate"]
    assert a["unknown_fields"] == []

    b = art.create_artifact(
        session,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"carrier": "船东A", "rate": "38"},
        created_by=1,
    )
    assert b["missing_fields"] == []


def test_unknown_fields_recorded_but_do_not_block(session):
    """未声明字段不阻断写入（Agent 产出可能多带键），但会被记下来且不进客户投影。"""
    from app.modules.entrust import artifacts as art

    a = art.create_artifact(
        session,
        entrustment_id=1,
        artifact_type="quote_parsed",
        payload={"carrier": "船东A", "rate": "38", "vendor_note": "口头承诺"},
        created_by=1,
    )
    assert a["unknown_fields"] == ["vendor_note"]
    assert reg.project_for_customer("quote_parsed", a["current_revision"]["payload"]) == {}


# ─────────────────────────────────────────── 3. 字段变化清单


def test_diff_payloads_three_kinds():
    changes = reg.diff_payloads(
        "customer_quote",
        {"amount": "80000", "currency": "CNY", "includes": ["运输"]},
        {"amount": "85000", "currency": "CNY", "excludes": ["装卸费"]},
    )
    by_field = {c["field"]: c for c in changes}
    assert by_field["amount"]["kind"] == "changed"
    assert by_field["includes"]["kind"] == "removed"
    assert by_field["excludes"]["kind"] == "added"


def test_changes_endpoint_returns_field_level_diff(env):
    from app.modules.entrust import artifacts as art

    manager, owner, org, eid = _seed(env, env.make_session())
    db = env.make_session()
    a = art.create_artifact(
        db,
        entrustment_id=eid,
        artifact_type="customer_quote",
        payload={"amount": "80000", "currency": "CNY", "includes": ["运输"]},
        created_by=int(manager["user_id"]),
    )
    art.append_revision(
        db,
        artifact_id=a["artifact_id"],
        payload={"amount": "85000", "currency": "CNY", "includes": ["运输"]},
        actor_id=int(manager["user_id"]),
        source=art.SOURCE_MANUAL,
    )
    resp = env.client.get(
        f"/api/v1/entrust/artifacts/{a['artifact_id']}/changes",
        params={"from_revision": 1, "to_revision": 2},
        headers=_headers(manager),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(c["field"], c["kind"]) for c in body["changes"]] == [("amount", "changed")]


# ─────────────────────────────────────────── 4. 附件落盘


def test_store_upload_records_metadata_and_storage_outside_static(session, tmp_path):
    rec = att.store_bytes(
        session,
        uploader_user_id=10,
        owner_user_id=11,
        org_id=1,
        filename="船东报价.txt",
        content_type="text/plain",
        data=b"rate 38",
        entrustment_id=5,
    )
    assert rec["size_bytes"] == 7
    assert rec["filename"] == "船东报价.txt"
    assert rec["extract_status"] == att.EXTRACT_NOT_REQUESTED
    assert rec["extracted_chars"] is None  # 未提取就是未知，不留 0

    path = att.resolve_path(rec)
    assert path.is_file()
    # 存储位置必须在静态目录之外：仓库里没有任何 static 目录指向它
    assert str(tmp_path) in str(path)
    assert "static" not in str(path).replace(str(tmp_path), "")


def test_upload_rejects_oversize(session, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_MAX_BYTES", 16)
    with pytest.raises(att.AttachmentValidationError) as exc:
        att.store_bytes(
            session,
            uploader_user_id=10,
            owner_user_id=11,
            org_id=1,
            filename="big.txt",
            content_type="text/plain",
            data=b"x" * 17,
        )
    assert "超过上限" in str(exc.value)
    # 超限时不留任何文件
    assert not att.storage_root().exists() or not any(att.storage_root().rglob("*.*"))


def test_upload_rejects_disallowed_type(session):
    with pytest.raises(att.AttachmentValidationError) as exc:
        att.store_bytes(
            session,
            uploader_user_id=10,
            owner_user_id=11,
            org_id=1,
            filename="evil.exe",
            content_type="application/x-msdownload",
            data=b"MZ",
        )
    assert "不支持的文件类型" in str(exc.value)


def test_upload_rejects_empty_content(session):
    with pytest.raises(att.AttachmentValidationError):
        att.store_bytes(
            session,
            uploader_user_id=10,
            owner_user_id=11,
            org_id=1,
            filename="empty.txt",
            content_type="text/plain",
            data=b"",
        )


def test_filename_sanitization_neutralizes_traversal():
    assert att.sanitize_filename("../../etc/passwd") == "passwd"
    assert att.sanitize_filename(r"..\\..\\win\\system32\\cmd.exe") == "cmd.exe"
    assert att.sanitize_filename("/abs/path/报价.pdf") == "报价.pdf"
    assert att.sanitize_filename("bad\x00\x07name.txt") == "badname.txt"
    assert att.sanitize_filename("") == "upload"
    assert att.sanitize_filename("CON.txt") == "file_CON.txt"


def test_set_extract_status_validates_domain(session):
    rec = att.store_bytes(
        session,
        uploader_user_id=10,
        owner_user_id=11,
        org_id=1,
        filename="a.txt",
        content_type="text/plain",
        data=b"hi",
        entrustment_id=5,
    )
    updated = att.set_extract_status(
        session, attachment_id=rec["attachment_id"], status=att.EXTRACT_DONE, extracted_chars=2
    )
    assert updated["extract_status"] == att.EXTRACT_DONE
    assert updated["extracted_chars"] == 2
    with pytest.raises(att.AttachmentValidationError):
        att.set_extract_status(session, attachment_id=rec["attachment_id"], status="bogus")


def test_resolve_path_rejects_escaping_storage_key(session):
    rec = att.store_bytes(
        session,
        uploader_user_id=10,
        owner_user_id=11,
        org_id=1,
        filename="a.txt",
        content_type="text/plain",
        data=b"hi",
        entrustment_id=5,
    )
    rec["storage_key"] = "../../../../etc/hosts"
    with pytest.raises(att.AttachmentStorageError):
        att.resolve_path(rec)


# ─────────────────────────────────────────── 5. 上传与下载授权（AC-10）


def test_upload_requires_idempotency_key(env):
    manager, owner, org, eid = _seed(env, env.make_session())
    resp = env.client.post(
        "/api/v1/entrust/attachments",
        files={"file": ("a.txt", b"hi", "text/plain")},
        data={"entrustment_id": str(eid)},
        headers=_headers(manager),
    )
    assert resp.status_code == 400


def test_upload_requires_exactly_one_owner(env):
    manager, owner, org, eid = _seed(env, env.make_session())
    resp = _upload(env, manager, entrustment_id=eid, assignment_id=1)
    assert resp.status_code == 400
    assert "只能指定一个归属" in resp.json()["detail"]

    resp2 = env.client.post(
        "/api/v1/entrust/attachments",
        files={"file": ("a.txt", b"hi", "text/plain")},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp2.status_code == 400


def test_upload_and_download_roundtrip_for_delegated_manager(env):
    manager, owner, org, eid = _seed(env, env.make_session())
    up = _upload(env, manager, entrustment_id=eid, data=b"rate 38\tCNY")
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["size_bytes"] == len(b"rate 38\tCNY")
    # 内部键不外泄：响应里没有 storage_key / sha256
    assert "storage_key" not in body and "sha256" not in body

    down = env.client.get(
        f"/api/v1/entrust/attachments/{body['attachment_id']}/download",
        headers=_headers(manager),
    )
    assert down.status_code == 200
    assert down.content == b"rate 38\tCNY"
    assert down.headers["content-type"].startswith("text/plain")


def test_owner_can_view_and_download_but_is_not_org_member(env):
    manager, owner, org, eid = _seed(env, env.make_session())
    up = _upload(env, manager, entrustment_id=eid)
    aid = up.json()["attachment_id"]

    # 货主是数据归属方：读得到
    meta = env.client.get(f"/api/v1/entrust/attachments/{aid}", headers=_headers(owner))
    assert meta.status_code == 200
    down = env.client.get(f"/api/v1/entrust/attachments/{aid}/download", headers=_headers(owner))
    assert down.status_code == 200

    # 但货主不是组织成员 → 上传被 404（不泄漏授权存在性）
    denied = _upload(env, owner, entrustment_id=eid)
    assert denied.status_code == 404


def test_outsider_gets_404_not_403(env):
    manager, owner, org, eid = _seed(env, env.make_session())
    outsider = _login(env.client, "outsider")
    up = _upload(env, manager, entrustment_id=eid)
    aid = up.json()["attachment_id"]

    for url in (
        f"/api/v1/entrust/attachments/{aid}",
        f"/api/v1/entrust/attachments/{aid}/download",
        f"/api/v1/entrust/entrustments/{eid}/attachments",
    ):
        resp = env.client.get(url, headers=_headers(outsider))
        assert resp.status_code == 404, (url, resp.status_code)


def test_member_of_other_org_gets_404(env):
    manager, owner, org, eid = _seed(env, env.make_session())
    db = env.make_session()
    stranger = _login(env.client, "othermgr")
    other_org = _org(db, name="另一个组织")
    _member(db, other_org, user_id=stranger["user_id"])
    _entrust(db, other_org, owner_id=owner["user_id"])

    up = _upload(env, manager, entrustment_id=eid)
    aid = up.json()["attachment_id"]
    resp = env.client.get(f"/api/v1/entrust/attachments/{aid}/download", headers=_headers(stranger))
    assert resp.status_code == 404


def test_member_without_write_permission_gets_403(env):
    manager, owner, org, eid = _seed(env, env.make_session(), permissions=READ_ONLY_PERMS)
    resp = _upload(env, manager, entrustment_id=eid)
    assert resp.status_code == 403


def test_private_draft_attachment_only_visible_to_owner(env):
    db = env.make_session()
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    a = assign_svc.create_assignment(db, owner_user_id=int(owner["user_id"]), title="私有草稿")

    up = _upload(env, owner, assignment_id=a["assignment_id"])
    assert up.status_code == 200, up.text
    aid = up.json()["attachment_id"]

    own = env.client.get(f"/api/v1/entrust/attachments/{aid}", headers=_headers(owner))
    assert own.status_code == 200
    stranger = _login(env.client, "stranger")
    other = env.client.get(f"/api/v1/entrust/attachments/{aid}", headers=_headers(stranger))
    assert other.status_code == 404


# ─────────────────────────────────────────── 6. 绑定与横切


def test_bind_attachment_to_artifact_and_list(env):
    from app.modules.entrust import artifacts as art

    manager, owner, org, eid = _seed(env, env.make_session())
    db = env.make_session()
    a = art.create_artifact(
        db,
        entrustment_id=eid,
        artifact_type="quote_parsed",
        payload={"carrier": "船东A", "rate": "38"},
        created_by=int(manager["user_id"]),
    )
    aid = _upload(env, manager, entrustment_id=eid).json()["attachment_id"]

    first = env.client.post(
        f"/api/v1/entrust/artifacts/{a['artifact_id']}/attachments",
        data={"attachment_id": str(aid)},
        headers=_headers(manager, "bind-1"),
    )
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True

    again = env.client.post(
        f"/api/v1/entrust/artifacts/{a['artifact_id']}/attachments",
        data={"attachment_id": str(aid)},
        headers=_headers(manager, "bind-2"),
    )
    assert again.status_code == 200
    assert again.json()["created"] is False  # 唯一键兜住重复绑定

    listing = env.client.get(
        f"/api/v1/entrust/artifacts/{a['artifact_id']}/attachments", headers=_headers(manager)
    )
    assert listing.status_code == 200
    assert [i["attachment_id"] for i in listing.json()["items"]] == [aid]


def test_bind_across_entrustments_rejected(env):
    from app.modules.entrust import artifacts as art

    manager, owner, org, eid = _seed(env, env.make_session())
    db = env.make_session()
    other_eid = _entrust(db, org, owner_id=int(owner["user_id"]))
    a = art.create_artifact(
        db,
        entrustment_id=eid,
        artifact_type="quote_parsed",
        payload={"carrier": "船东A", "rate": "38"},
        created_by=int(manager["user_id"]),
    )
    aid = _upload(env, manager, entrustment_id=other_eid).json()["attachment_id"]

    resp = env.client.post(
        f"/api/v1/entrust/artifacts/{a['artifact_id']}/attachments",
        data={"attachment_id": str(aid)},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 409


def test_attachment_endpoints_disabled_return_404(client, shipper):
    """AC-22：开关关闭时整组端点 404（隐藏入口）。"""
    for url in (
        "/api/v1/entrust/attachments/1",
        "/api/v1/entrust/attachments/1/download",
        "/api/v1/entrust/entrustments/1/attachments",
        "/api/v1/entrust/artifact-types",
    ):
        assert client.get(url, headers=shipper["_headers"]).status_code == 404
