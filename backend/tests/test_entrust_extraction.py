"""附件文档提取测试（ENT-013 / S1 第 6 条 / AC-17）。

七组断言：

1. **解码回退**：UTF-8 / UTF-8 BOM / GB18030 逐级回退，每一步都留下 note；
2. **魔数优先于声明**（AC-18）：把 PDF/图片标成 `text/plain` 不会让处理路径改变
   —— 上传者不能靠声明 MIME 决定服务端怎么解释字节；
3. **Office 提取**：docx（`word/document.xml`）与 xlsx（共享字符串表 + 单元格）
   都能用标准库取出文本；
4. **PDF 文本层**：Flate 压缩的内容流能抽出文本；**扫描件**（无文本操作符）
   与**噪声文本层**都判 `needs_transcription`，不返回乱码冒充成功；
5. **能力边界**：老式 OLE2 `.xls`、图片、无法识别类型 → 各自明确结论
   （`unsupported` / `needs_transcription`），不静默给半份文本；
6. **截断显式**：超过上限时 `truncated=True` 且必须写进 note 与数据
   —— 被截断的文本看起来是完整的；
7. **API 与权限**：提取/转录需写权限与幂等键、非参与方 404、重抽覆盖同一份文本、
   状态与文本**不允许矛盾**（结论变成"没有文本"时旧文本必须撤掉）。

测试用的 PDF / docx / xlsx 全部由本文件用标准库现场构造 —— 不往仓库塞二进制样本，
也不引入解析库（那正是本增量要避免的依赖）。
"""

from __future__ import annotations

import os
import uuid
import zlib
from types import SimpleNamespace

os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.modules.entrust import attachments as att  # noqa: E402
from app.modules.entrust import extraction as ex  # noqa: E402

_TS = "2026-09-13 00:00:00"
ALL_PERMS = '["entrust:view","entrust:quote:create","entrust:quote:publish"]'
READ_ONLY_PERMS = '["entrust:view"]'
#: 会话与作业额外要求 `entrust:agent:job`（只读成员能看历史但不能让 Agent 干活）
AGENT_PERMS = '["entrust:view","entrust:quote:create","entrust:quote:publish","entrust:agent:job"]'

QUOTE_TEXT = "承运人：长江物流有限公司\n单价：38.00 元/吨\n有效期至：2026-10-31\n航线：重庆→上海"


# ─────────────────────────────────────────── 样本构造（全部标准库）


def make_pdf(content: bytes, *, compress: bool = True) -> bytes:
    """最小可用 PDF：一个页面 + 一条内容流。"""
    stream = zlib.compress(content) if compress else content
    return b"".join(
        [
            b"%PDF-1.4\n",
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
            b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n",
            b"4 0 obj << /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream),
            stream,
            b"\nendstream endobj\n",
            b"trailer << /Root 1 0 R >>\n%%EOF\n",
        ]
    )


def make_text_pdf(text_body: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text_body}) Tj ET".encode()
    return make_pdf(content)


def make_scanned_pdf() -> bytes:
    """只有图像绘制操作符、没有任何文本操作符 —— 典型扫描件。"""
    return make_pdf(b"q 1 0 0 1 0 0 cm /Im0 Do Q")


def make_garbage_pdf() -> bytes:
    """有文本操作符，但内容是控制字符 —— 解出来是噪声，不能当成功。"""
    return make_pdf(b"BT ( " + b"\x01" * 60 + b" ) Tj ET")


def make_noisy_pdf() -> bytes:
    """有噪声，但也夹着少量可读文本（用于确认不会被误判成 done）。"""
    return make_pdf(b"BT ( " + b"\x02" * 40 + b" ) Tj ET")


def make_zip(entries: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def make_docx(paragraphs: list[str], *, valid_xml: bool = True) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    if not valid_xml:
        document = "<w:document><w:body><w:p><w:t>断裂"  # 故意截断
    return make_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": document.encode(),
        }
    )


def make_xlsx(rows: list[list[str]]) -> bytes:
    shared: list[str] = []
    for row in rows:
        for cell in row:
            if cell not in shared:
                shared.append(cell)
    sst = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in shared)
        + "</sst>"
    )
    sheet_rows = []
    for index, row in enumerate(rows, start=1):
        cells = "".join(f'<c r="A{index}" t="s"><v>{shared.index(value)}</v></c>' for value in row)
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    return make_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/sharedStrings.xml": sst.encode(),
            "xl/worksheets/sheet1.xml": sheet.encode(),
        }
    )


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
OLE2_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64


# ─────────────────────────────────────────── fixtures / 播种


@pytest.fixture()
def session(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.models import Base
    from migrate import apply_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    """TestClient + 播种会话；ENTRUST_ENABLED / LLM_MOCK 都打开，附件目录指向 tmp_path。"""
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
    monkeypatch.setattr(settings, "ATTACHMENT_STORAGE_DIR", str(tmp_path / "att"))
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    with TestClient(app) as tc:
        yield SimpleNamespace(client=tc, make_session=factory)
    app.dependency_overrides.clear()
    engine.dispose()


def _org(db, status="active") -> int:
    result = db.execute(
        text("INSERT INTO ent_organization (name, status, created_at) VALUES (:n, :s, :c)"),
        {"n": f"org-{uuid.uuid4().hex[:6]}", "s": status, "c": _TS},
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
    head = {"Authorization": f"Bearer {data['access_token']}"}
    if key:
        head["Idempotency-Key"] = key
    return head


def _seed(env, db, *, permissions: str = ALL_PERMS):
    manager = _login(env.client, "mgr")
    owner = _login(env.client, "shipper")
    org = _org(db)
    _member(db, org, user_id=manager["user_id"])
    eid = _entrust(db, org, owner_id=owner["user_id"], permissions=permissions)
    return manager, owner, org, eid


def _upload(env, user, *, data: bytes, filename: str, ctype: str, entrustment_id: int, key=None):
    return env.client.post(
        "/api/v1/entrust/attachments",
        files={"file": (filename, data, ctype)},
        data={"entrustment_id": str(entrustment_id)},
        headers=_headers(user, key or uuid.uuid4().hex),
    )


def _extract(env, user, attachment_id: int, *, key=None):
    return env.client.post(
        f"/api/v1/entrust/attachments/{attachment_id}/extract",
        headers=_headers(user, key or uuid.uuid4().hex),
    )


def _get_text(env, user, attachment_id: int):
    return env.client.get(
        f"/api/v1/entrust/attachments/{attachment_id}/text", headers=_headers(user)
    )


# ─────────────────────────────────────────── 1. 解码回退


def test_text_decoding_utf8_bom_and_gbk():
    utf8, notes = ex.decode_text_bytes("货量：1200 吨".encode())
    assert utf8 == "货量：1200 吨"
    assert notes == []

    with_bom, notes_bom = ex.decode_text_bytes("\ufeffhello".encode("utf-8"))
    assert with_bom == "hello"
    assert any("BOM" in note for note in notes_bom)

    gbk, notes_gbk = ex.decode_text_bytes("承运人：长江物流".encode("gbk"))
    assert gbk == "承运人：长江物流"
    assert any("GB18030" in note for note in notes_gbk)


def test_csv_and_markdown_treated_as_text():
    for name, ctype in (("q.csv", "text/csv"), ("q.md", "text/markdown")):
        outcome = ex.extract(b"carrier,rate\nA,38\n", content_type=ctype, filename=name)
        assert outcome.status == ex.STATUS_DONE
        assert outcome.media_kind == ex.MEDIA_TEXT
        assert "carrier" in (outcome.text or "")


# ─────────────────────────────────────────── 2. 魔数优先于声明（AC-18）


def test_magic_bytes_win_over_declared_mime():
    """把 PDF 标成 text/plain：**不能**按文本处理 —— 声明不等于事实。"""
    outcome = ex.extract(make_text_pdf(QUOTE_TEXT), content_type="text/plain")
    assert outcome.media_kind == ex.MEDIA_PDF
    assert outcome.status == ex.STATUS_DONE
    assert any("声明类型" in note for note in outcome.notes)


def test_image_declared_as_text_is_still_treated_as_image():
    outcome = ex.extract(PNG_BYTES, content_type="text/plain", filename="x.txt")
    assert outcome.media_kind == ex.MEDIA_IMAGE
    assert outcome.status == ex.STATUS_NEEDS_TRANSCRIPTION


# ─────────────────────────────────────────── 3. Office 提取


def test_docx_extraction_keeps_paragraphs():
    outcome = ex.extract(
        make_docx(["承运人：长江物流有限公司", "单价：38.00 元/吨"]),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="quote.docx",
    )
    assert outcome.status == ex.STATUS_DONE
    assert outcome.media_kind == ex.MEDIA_DOCX
    assert "长江物流有限公司" in (outcome.text or "")
    assert "38.00" in (outcome.text or "")
    # 段落之间必须有换行：粘成一坨会让下游的正则抽取串行
    assert "\n" in (outcome.text or "")


def test_xlsx_extraction_reads_shared_strings():
    outcome = ex.extract(
        make_xlsx([["承运人", "单价"], ["长江物流", "38.00"]]),
        content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        filename="quotes.xlsx",
    )
    assert outcome.status == ex.STATUS_DONE
    assert outcome.media_kind == ex.MEDIA_XLSX
    assert "承运人" in (outcome.text or "")
    assert "38.00" in (outcome.text or "")


def test_corrupt_docx_is_failed_not_done():
    outcome = ex.extract(
        make_docx([], valid_xml=False),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="broken.docx",
    )
    assert outcome.status == ex.STATUS_FAILED
    assert "解析失败" in outcome.detail


# ─────────────────────────────────────────── 4. PDF


def test_text_layer_pdf_extracted():
    outcome = ex.extract(make_text_pdf(QUOTE_TEXT), content_type="application/pdf")
    assert outcome.status == ex.STATUS_DONE
    assert "长江物流有限公司" in (outcome.text or "")
    assert "38.00" in (outcome.text or "")
    assert "有效期至" in (outcome.text or "")
    # PDF 提取是近似的，必须自曝
    assert any("PDF 文本层提取" in note for note in outcome.notes)


def test_pdf_with_hex_strings_extracted():
    body = "承运人：长江物流".encode()
    hexed = b"BT <" + body.hex().encode() + b"> Tj ET"
    outcome = ex.extract(make_pdf(hexed), content_type="application/pdf")
    assert outcome.status == ex.STATUS_DONE
    assert "长江物流" in (outcome.text or "")


def test_scanned_pdf_needs_transcription():
    outcome = ex.extract(make_scanned_pdf(), content_type="application/pdf")
    assert outcome.status == ex.STATUS_NEEDS_TRANSCRIPTION
    assert outcome.text is None
    assert "文本层" in outcome.detail


def test_noisy_pdf_text_layer_needs_transcription():
    """解出来是噪声时判 needs_transcription —— 不把乱码当成功结果返回。"""
    outcome = ex.extract(make_garbage_pdf(), content_type="application/pdf")
    assert outcome.status == ex.STATUS_NEEDS_TRANSCRIPTION
    assert outcome.text is None


def test_noisy_pdf_with_incidental_text_still_not_done():
    outcome = ex.extract(make_noisy_pdf(), content_type="application/pdf")
    assert outcome.status == ex.STATUS_NEEDS_TRANSCRIPTION


# ─────────────────────────────────────────── 5. 能力边界


def test_legacy_xls_unsupported():
    outcome = ex.extract(OLE2_BYTES, content_type="application/vnd.ms-excel", filename="a.xls")
    assert outcome.status == ex.STATUS_UNSUPPORTED
    assert outcome.media_kind == ex.MEDIA_OLE2
    assert "另存为" in outcome.detail


def test_image_needs_transcription():
    for payload, ctype, name in (
        (PNG_BYTES, "image/png", "a.png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 32, "image/jpeg", "a.jpg"),
        (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16, "image/webp", "a.webp"),
    ):
        outcome = ex.extract(payload, content_type=ctype, filename=name)
        assert outcome.status == ex.STATUS_NEEDS_TRANSCRIPTION, name
        assert "人工转录" in outcome.detail


def test_unknown_type_unsupported():
    outcome = ex.extract(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 32, content_type="")
    assert outcome.status == ex.STATUS_UNSUPPORTED


def test_binary_masquerading_as_text_is_unsupported():
    """二进制标成 text/plain：转人工转录也没意义 → unsupported（不是 needs_transcription）。"""
    outcome = ex.extract(
        b"\x00\x01\x02\x03\x04\x05\x06\x07" * 32, content_type="text/plain", filename="x.txt"
    )
    assert outcome.status == ex.STATUS_UNSUPPORTED


# ─────────────────────────────────────────── 6. 截断显式


def test_truncation_is_explicit():
    body = "甲" * 500
    outcome = ex.extract(body.encode(), content_type="text/plain", max_chars=100)
    assert outcome.status == ex.STATUS_DONE
    assert outcome.truncated is True
    assert outcome.chars == 100
    assert any("截断" in note for note in outcome.notes)


def test_no_truncation_when_under_limit():
    outcome = ex.extract(b"short", content_type="text/plain", max_chars=100)
    assert outcome.truncated is False
    assert not any("截断" in note for note in outcome.notes)


# ─────────────────────────────────────────── 7. API 与权限


def test_extract_endpoint_persists_text_and_exposes_it(env):
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    uploaded = _upload(
        env,
        manager,
        data=QUOTE_TEXT.encode(),
        filename="报价.txt",
        ctype="text/plain",
        entrustment_id=eid,
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment_id = uploaded.json()["attachment_id"]

    result = _extract(env, manager, attachment_id)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["extract_status"] == att.EXTRACT_DONE
    assert body["truncated"] is False
    assert body["extracted_chars"] == len(QUOTE_TEXT)
    assert "长江物流" in (body["text_preview"] or "")
    assert body["media_kind"] == ex.MEDIA_TEXT

    fetched = _get_text(env, manager, attachment_id)
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert payload["has_text"] is True
    assert payload["source"] == att.TEXT_SOURCE_EXTRACTOR
    assert payload["text"] == QUOTE_TEXT
    assert payload["transcribed_by"] is None


def test_extract_requires_idempotency_key(env):
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env, manager, data=b"x", filename="a.txt", ctype="text/plain", entrustment_id=eid
    ).json()["attachment_id"]
    resp = env.client.post(
        f"/api/v1/entrust/attachments/{attachment_id}/extract",
        headers=_headers(manager),
    )
    assert resp.status_code == 400


def test_extract_is_idempotent_under_same_key(env):
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env,
        manager,
        data=QUOTE_TEXT.encode(),
        filename="报价.txt",
        ctype="text/plain",
        entrustment_id=eid,
    ).json()["attachment_id"]

    key = uuid.uuid4().hex
    first = _extract(env, manager, attachment_id, key=key)
    second = _extract(env, manager, attachment_id, key=key)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["extracted_chars"] == second.json()["extracted_chars"]


def test_reextract_overwrites_single_text_row(env):
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env,
        manager,
        data=QUOTE_TEXT.encode(),
        filename="报价.txt",
        ctype="text/plain",
        entrustment_id=eid,
    ).json()["attachment_id"]

    _extract(env, manager, attachment_id)
    _extract(env, manager, attachment_id)
    rows = (
        env.make_session()
        .execute(
            text("SELECT COUNT(*) FROM ent_attachment_text WHERE attachment_id = :a"),
            {"a": attachment_id},
        )
        .scalar()
    )
    assert int(rows or 0) == 1


def test_non_participant_cannot_extract_or_read_text(env):
    """非参与方一律 404（不泄漏存在性），读文本也不例外。"""
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    outsider = _login(env.client, "stranger")
    attachment_id = _upload(
        env, manager, data=b"secret", filename="a.txt", ctype="text/plain", entrustment_id=eid
    ).json()["attachment_id"]

    assert _get_text(env, outsider, attachment_id).status_code == 404
    assert _extract(env, outsider, attachment_id).status_code == 404


def test_read_only_permission_can_read_text_but_not_extract(env):
    """授权收窄成只读后：能读**已有**的提取文本，但不能触发新的提取（写操作）。"""
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    uploaded = _upload(
        env,
        manager,
        data=b"rate 38",
        filename="报价.txt",
        ctype="text/plain",
        entrustment_id=eid,
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment_id = uploaded.json()["attachment_id"]
    assert _extract(env, manager, attachment_id).status_code == 200

    # 收窄授权（模拟货主把"创作"权限收回，只留查看）
    db.execute(
        text("UPDATE ent_entrustment SET permissions = :p WHERE id = :e"),
        {"p": READ_ONLY_PERMS, "e": eid},
    )
    db.commit()

    readable = _get_text(env, manager, attachment_id)
    assert readable.status_code == 200
    assert readable.json()["has_text"] is True
    assert _extract(env, manager, attachment_id).status_code == 403


def test_text_endpoint_reports_status_without_404(env):
    """没有文本时不返回 404：`extract_status` 本身决定下一步动作。"""
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env,
        manager,
        data=PNG_BYTES,
        filename="scan.png",
        ctype="image/png",
        entrustment_id=eid,
    ).json()["attachment_id"]

    resp = _get_text(env, manager, attachment_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["extract_status"] == att.EXTRACT_NOT_REQUESTED
    assert resp.json()["has_text"] is False


def test_missing_attachment_returns_404(env):
    db = env.make_session()
    manager, _, _, _ = _seed(env, db)
    assert _get_text(env, manager, 999999).status_code == 404
    assert _extract(env, manager, 999999).status_code == 404


def test_entrust_disabled_hides_endpoints(env, monkeypatch):
    from app.core.config import get_settings

    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env, manager, data=b"x", filename="a.txt", ctype="text/plain", entrustment_id=eid
    ).json()["attachment_id"]

    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    assert _extract(env, manager, attachment_id).status_code == 404
    assert _get_text(env, manager, attachment_id).status_code == 404


# ─────────────────────────────────────────── 8. 人工转录（降级路径）


def test_manual_transcription_completes_scanned_attachment(env):
    """扫描件/图片的降级链路：needs_transcription → 人工转录 → done，来源可区分。"""
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env,
        manager,
        data=PNG_BYTES,
        filename="扫描件.png",
        ctype="image/png",
        entrustment_id=eid,
    ).json()["attachment_id"]

    extracted = _extract(env, manager, attachment_id)
    assert extracted.json()["extract_status"] == att.EXTRACT_NEEDS_TRANSCRIPTION
    assert extracted.json()["extract_error"]

    resp = env.client.post(
        f"/api/v1/entrust/attachments/{attachment_id}/transcription",
        json={"text": QUOTE_TEXT},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["extract_status"] == att.EXTRACT_DONE
    assert body["has_text"] is True
    assert body["source"] == att.TEXT_SOURCE_MANUAL
    assert body["transcribed_by"] == manager["user_id"]
    assert body["extract_error"] is None


def test_transcription_rejects_blank_and_requires_key(env):
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env, manager, data=PNG_BYTES, filename="a.png", ctype="image/png", entrustment_id=eid
    ).json()["attachment_id"]

    blank = env.client.post(
        f"/api/v1/entrust/attachments/{attachment_id}/transcription",
        json={"text": "   "},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert blank.status_code == 400

    no_key = env.client.post(
        f"/api/v1/entrust/attachments/{attachment_id}/transcription",
        json={"text": "有内容"},
        headers=_headers(manager),
    )
    assert no_key.status_code == 400


def test_transcription_rejects_oversized_text(env):
    from app.core.config import get_settings

    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env, manager, data=PNG_BYTES, filename="a.png", ctype="image/png", entrustment_id=eid
    ).json()["attachment_id"]

    limit = int(get_settings().EXTRACTION_MAX_TEXT_CHARS)
    resp = env.client.post(
        f"/api/v1/entrust/attachments/{attachment_id}/transcription",
        json={"text": "甲" * (limit + 1)},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert resp.status_code in (400, 422)


def test_non_participant_cannot_transcribe(env):
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    outsider = _login(env.client, "stranger")
    attachment_id = _upload(
        env, manager, data=PNG_BYTES, filename="a.png", ctype="image/png", entrustment_id=eid
    ).json()["attachment_id"]

    resp = env.client.post(
        f"/api/v1/entrust/attachments/{attachment_id}/transcription",
        json={"text": "偷录"},
        headers=_headers(outsider, uuid.uuid4().hex),
    )
    assert resp.status_code == 404


# ─────────────────────────────────────────── 9. 状态与文本不得矛盾


def test_stale_text_dropped_when_conclusion_becomes_no_text(env, monkeypatch):
    """重抽后结论变成"没有文本"时，旧文本必须一起撤掉。

    否则会出现"状态说 needs_transcription、/text 却能读出内容"的矛盾 ——
    UI 与 Agent 都会按其中一个行动。删的是**派生数据**（文件本身不动）。
    """
    db = env.make_session()
    manager, _, _, eid = _seed(env, db)
    attachment_id = _upload(
        env,
        manager,
        data=QUOTE_TEXT.encode(),
        filename="报价.txt",
        ctype="text/plain",
        entrustment_id=eid,
    ).json()["attachment_id"]

    assert _extract(env, manager, attachment_id).json()["extract_status"] == att.EXTRACT_DONE
    assert _get_text(env, manager, attachment_id).json()["has_text"] is True

    # 模拟"提取规则变了/重新判定为不可机读"
    def fake_extract(*args, **kwargs):
        return ex.ExtractOutcome(
            status=ex.STATUS_NEEDS_TRANSCRIPTION,
            media_kind=ex.MEDIA_PDF,
            text=None,
            chars=None,
            detail="PDF 没有可机读的文本层",
        )

    monkeypatch.setattr(ex, "extract", fake_extract)
    again = _extract(env, manager, attachment_id)
    assert again.json()["extract_status"] == att.EXTRACT_NEEDS_TRANSCRIPTION

    latest = _get_text(env, manager, attachment_id)
    assert latest.json()["has_text"] is False
    assert latest.json()["text"] is None


# ─────────────────────────────────────────── 10. Agent 闭环（AC-17）


def test_agent_context_includes_attachment_text(session):
    """`collect_context` 必须把提取文本带给 Agent，否则提取了也白提取。"""
    from app.modules.entrust import agentjobs

    row = att.store_bytes(
        session,
        uploader_user_id=1,
        owner_user_id=1,
        org_id=1,
        filename="报价.txt",
        content_type="text/plain",
        data=QUOTE_TEXT.encode(),
        entrustment_id=77,
    )
    attachment_id = int(row["attachment_id"])
    att.upsert_text(
        session,
        attachment_id=attachment_id,
        content=QUOTE_TEXT,
        source=att.TEXT_SOURCE_EXTRACTOR,
    )
    att.set_extract_status(session, attachment_id=attachment_id, status=att.EXTRACT_DONE)

    context = agentjobs.collect_context(session, {"entrustment_id": 77, "assignment_id": None})
    item = next(i for i in context["attachments"] if i["attachment_id"] == attachment_id)
    assert "长江物流" in (item["text_excerpt"] or "")
    assert item["text_source"] == att.TEXT_SOURCE_EXTRACTOR


def test_agent_uses_attachment_text_and_cites_it_as_source():
    """AG-02 在操作者没粘贴文本时，用附件提取文本解析，并引用 `attachment_text` 来源。

    这条断言是 AC-17 的闭环：上传报价 PDF → 提取 → Agent 直接解析，
    且来源引用能对上服务端枚举的目录（否则会被判成"编造来源"）。
    """
    from app.modules.entrust.agents import ag02, runner

    context = {
        "assignment": {"assignment_id": 5, "revision": 2, "title": "煤炭运输"},
        "tasks": [],
        "artifacts": [],
        "attachments": [
            {
                "attachment_id": 9,
                "filename": "报价.pdf",
                "content_type": "application/pdf",
                "extract_status": "done",
                "text_excerpt": QUOTE_TEXT,
                "text_truncated": False,
                "text_source": "extractor",
            }
        ],
    }
    job_input = {"attachment_id": 9}

    raw = ag02.mock_content(context, job_input)
    kinds = {ref["kind"] for ref in raw["source_refs"]}
    assert "attachment_text" in kinds
    assert "operator_input" not in kinds

    proposals = raw["artifact_proposals"]
    assert any(p["artifact_type"] == "quote_parsed" for p in proposals)
    parsed = next(p for p in proposals if p["artifact_type"] == "quote_parsed")
    assert parsed["payload"]["rate"] == "38.00"

    catalog = runner.build_source_catalog(context, job_input)
    assert ("attachment_text", "9") in catalog

    from app.modules.entrust.envelope import validate_envelope

    validation = validate_envelope(raw, known_source_refs=catalog)
    assert validation.unverified_sources == []


def test_agent_does_not_cite_attachment_text_when_extraction_missing():
    """附件没提取出文本时不能引用 `attachment_text` —— 引用了就是编造来源。"""
    from app.modules.entrust.agents import ag02, runner

    context = {
        "assignment": {"assignment_id": 5, "revision": 2, "title": "煤炭运输"},
        "tasks": [],
        "artifacts": [],
        "attachments": [
            {
                "attachment_id": 9,
                "filename": "扫描件.pdf",
                "content_type": "application/pdf",
                "extract_status": "needs_transcription",
                "text_excerpt": None,
                "text_truncated": False,
                "text_source": None,
            }
        ],
    }
    job_input = {"attachment_id": 9}
    raw = ag02.mock_content(context, job_input)
    assert "attachment_text" not in {ref["kind"] for ref in raw["source_refs"]}

    catalog = runner.build_source_catalog(context, job_input)
    assert ("attachment_text", "9") not in catalog
    assert ("attachment", "9") in catalog


def test_operator_input_still_wins_over_attachment():
    """操作者当场粘贴的文本优先于附件提取文本（现场输入最新）。"""
    from app.modules.entrust.agents import ag02

    context = {
        "assignment": {"assignment_id": 5, "revision": 1, "title": "煤炭运输"},
        "tasks": [],
        "artifacts": [],
        "attachments": [
            {
                "attachment_id": 9,
                "filename": "旧报价.pdf",
                "content_type": "application/pdf",
                "extract_status": "done",
                "text_excerpt": "承运人：旧船东 单价：99.00 元/吨",
                "text_truncated": False,
                "text_source": "extractor",
            }
        ],
    }
    raw = ag02.mock_content(context, {"attachment_id": 9, "quote_text": QUOTE_TEXT})
    kinds = {ref["kind"] for ref in raw["source_refs"]}
    assert "operator_input" in kinds
    assert "attachment_text" not in kinds
    parsed = next(p for p in raw["artifact_proposals"] if p["artifact_type"] == "quote_parsed")
    assert parsed["payload"]["rate"] == "38.00"


def test_end_to_end_job_reads_uploaded_pdf(env):
    """端到端：上传 PDF → 提取 → 建会话/提作业/执行 → 信封里有 quote_parsed。"""
    db = env.make_session()
    manager, _, _, eid = _seed(env, db, permissions=AGENT_PERMS)
    attachment_id = _upload(
        env,
        manager,
        data=make_text_pdf(QUOTE_TEXT),
        filename="报价.pdf",
        ctype="application/pdf",
        entrustment_id=eid,
    ).json()["attachment_id"]
    assert _extract(env, manager, attachment_id).json()["extract_status"] == att.EXTRACT_DONE

    created = env.client.post(
        f"/api/v1/entrust/entrustments/{eid}/sessions",
        json={"entrustment_id": eid, "agent_specialty": "agent_02", "title": "报价解析"},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    submitted = env.client.post(
        f"/api/v1/entrust/sessions/{session_id}/jobs",
        json={"input": {"attachment_id": attachment_id}},
        headers=_headers(manager, uuid.uuid4().hex),
    )
    assert submitted.status_code == 200, submitted.text
    job_id = submitted.json()["job_id"]

    ran = env.client.post(f"/api/v1/entrust/agent/jobs/{job_id}/run", headers=_headers(manager))
    assert ran.status_code == 200, ran.text
    job = ran.json()["job"]
    assert job["status"] == "succeeded", job.get("error_message")
    envelope = job["envelope"]
    assert envelope is not None
    assert envelope["requires_review"] is True
    parsed = next(p for p in envelope["artifact_proposals"] if p["artifact_type"] == "quote_parsed")
    # 数字来自 PDF 文本层（这次操作者**没有**粘贴任何文本）
    assert parsed["payload"]["rate"] == "38.00"
