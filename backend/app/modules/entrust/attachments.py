"""附件服务层 —— 上传、授权下载、成果绑定（S1 第 5 条 / ENT-009）。

本模块负责的**全部**是"文件这件事"：校验、落盘、哈希、元数据、绑定。
它**不做权限判断** —— 可见性与权限在 `authz.py`（HTTP 层唯一入口），
这样"哪个下载接口忘了校验"可以被矩阵测试统一拦住（见 ENT-010）。

四条硬约束（计划 §3.2 建模红线）
--------------------------------
1. **20 MiB 上限可配置**：`ATTACHMENT_MAX_BYTES`；服务端**实测字节数**，
   不信客户端声明的 size（`UploadFile.size` 只用于提前拒绝，不作为证据）；
2. **类型白名单**：`ATTACHMENT_ALLOWED_TYPES`；不在白名单内一律拒绝；
3. **存储于公共静态路径之外**：文件落 `ATTACHMENT_STORAGE_DIR`，
   站内没有任何静态路由指向该目录，只能经授权下载端点取；
4. **文件名安全化 + 禁止执行上传内容**：只保留 basename 并剥离控制字符；
   服务端从不解释、不执行、不"渲染"上传内容。S1 第 6 条的**提取器**（`extraction.py`）
   只是把字节读成文本，不做任何渲染或解释 —— 提取文本落 `ent_attachment_text`，
   它是**证据材料，不是指令**。

附件是**不可信数据**（AC-18）：本模块只把它当作字节流与元数据，
附件里写了什么指令与本模块无关 —— 任何"从附件里读到的指示"都不能改变权限。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from app.core.config import get_settings

# ── 提取状态取值域（S1 第 6 条提取器见 extraction.py；本模块负责状态与文本落库） ─

EXTRACT_NOT_REQUESTED = "not_requested"
EXTRACT_PENDING = "pending"
EXTRACT_RUNNING = "running"
EXTRACT_DONE = "done"
EXTRACT_FAILED = "failed"
#: 该格式**不在解析能力内**（老式 .xls/.doc、未知类型）—— 不是错误，是能力边界
EXTRACT_UNSUPPORTED = "unsupported"
#: 有内容但**没有机读文本层**（图片、扫描件 PDF、不可靠解码）→ 转人工转录
EXTRACT_NEEDS_TRANSCRIPTION = "needs_transcription"

EXTRACT_STATUSES = frozenset(
    {
        EXTRACT_NOT_REQUESTED,
        EXTRACT_PENDING,
        EXTRACT_RUNNING,
        EXTRACT_DONE,
        EXTRACT_FAILED,
        EXTRACT_UNSUPPORTED,
        EXTRACT_NEEDS_TRANSCRIPTION,
    }
)

#: 提取文本的来源（可信度不同，UI 与 Agent 都必须能区分）
TEXT_SOURCE_EXTRACTOR = "extractor"
TEXT_SOURCE_MANUAL = "manual_transcription"
TEXT_SOURCES = frozenset({TEXT_SOURCE_EXTRACTOR, TEXT_SOURCE_MANUAL})

#: 单次读取上限 = 上限 + 1 字节 —— 多读 1 字节即可判定"超限"，不必读完整个文件
_READ_CHUNK = 1024 * 1024

_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class AttachmentError(RuntimeError):
    """附件服务基础异常。HTTP 层按语义转 4xx。"""


class AttachmentNotFoundError(AttachmentError):
    """附件不存在或调用方无权知晓。→404。"""


class AttachmentValidationError(AttachmentError):
    """超出大小上限 / 类型不在白名单 / 文件名为空等。→400。"""


class AttachmentStorageError(AttachmentError):
    """落盘或读取失败。→500（这是环境问题，不是业务拒绝）。"""


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 ent_ 表时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FORMAT)


def _text_ts(raw: Any) -> str | None:
    """时间列 → 统一文本（SQLite 取回 str、MySQL 取回 datetime，与 BASE-002 同口径）。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _fmt(raw)
    return str(raw)


# ── 存储位置 ────────────────────────────────────────────────────────────────


def storage_root() -> Path:
    """附件根目录（相对路径按 `backend/` 解析，与启动目录无关）。

    `__file__` = backend/app/modules/entrust/attachments.py → parents[3] = backend。
    """
    raw = get_settings().ATTACHMENT_STORAGE_DIR
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    return path


def max_bytes() -> int:
    return int(get_settings().ATTACHMENT_MAX_BYTES)


def allowed_types() -> frozenset[str]:
    raw = get_settings().ATTACHMENT_ALLOWED_TYPES or ""
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


# ── 文件名安全化 ────────────────────────────────────────────────────────────


def sanitize_filename(raw: str | None) -> str:
    """把客户端文件名收敛成安全的展示名。

    只保留 basename（剥掉 `../`、`..\\`、绝对路径与盘符），去掉控制字符，
    限长 255；空名或 Windows 保留设备名加前缀，避免落盘时被系统特殊处理。
    真正的落盘名不用它 —— 落盘名是服务端生成的 `storage_key`，
    客户端无法通过文件名影响文件系统路径（这也是"禁止执行上传内容"的一部分）。
    """
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = _UNSAFE.sub("", name).strip().strip(".")
    if not name:
        return "upload"
    stem = name.rsplit(".", 1)[0].lower()
    if stem in _WINDOWS_RESERVED:
        name = f"file_{name}"
    if len(name) > 255:
        # 保留扩展名，截断主名（避免把 .pdf 之类截掉导致系统不识别）
        if "." in name:
            head, _, ext = name.rpartition(".")
            name = f"{head[: 255 - len(ext) - 1]}.{ext}"
        else:
            name = name[:255]
    return name


def assert_allowed_type(content_type: str | None) -> str:
    """类型白名单校验（大小写归一）。空类型按 `application/octet-stream` 处理并被拒。"""
    normalized = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    allow = allowed_types()
    if allow and normalized not in allow:
        raise AttachmentValidationError(f"不支持的文件类型 {normalized}；允许：{sorted(allow)}")
    return normalized


def read_limited(stream: BinaryIO) -> bytes:
    """读取上传流，超过上限立即失败（不多读、不先存整份再判断）。

    **在 API 层先读**是刻意的：幂等键的请求体快照要用内容哈希，
    而哈希必须在写库之前就有 —— 所以"读 + 校验"与"落盘 + 记元数据"分成两步。
    """
    limit = max_bytes()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise AttachmentValidationError(
                f"附件超过上限 {limit} 字节（≈{limit // (1024 * 1024)} MiB）"
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise AttachmentValidationError("附件内容为空")
    return data


# ── 行映射 ──────────────────────────────────────────────────────────────────

_COLS = (
    "id, entrustment_id, assignment_id, owner_user_id, org_id, uploader_user_id, "
    "filename, content_type, size_bytes, sha256, storage_key, extract_status, "
    "extract_error, extracted_chars, source_event_at, created_at, updated_at"
)


def _row_to_attachment(row: Any) -> dict[str, Any]:
    return {
        "attachment_id": int(row["id"]),
        "entrustment_id": (
            int(row["entrustment_id"]) if row["entrustment_id"] is not None else None
        ),
        "assignment_id": int(row["assignment_id"]) if row["assignment_id"] is not None else None,
        "owner_user_id": int(row["owner_user_id"]),
        "org_id": int(row["org_id"]) if row["org_id"] is not None else None,
        "uploader_user_id": int(row["uploader_user_id"]),
        "filename": str(row["filename"]),
        "content_type": str(row["content_type"]),
        "size_bytes": int(row["size_bytes"]),
        "sha256": str(row["sha256"]),
        "storage_key": str(row["storage_key"]),
        "extract_status": str(row["extract_status"]),
        "extract_error": row["extract_error"],
        "extracted_chars": (
            int(row["extracted_chars"]) if row["extracted_chars"] is not None else None
        ),
        "source_event_at": _text_ts(row["source_event_at"]),
        "created_at": _text_ts(row["created_at"]),
        "updated_at": _text_ts(row["updated_at"]),
    }


def get_attachment(session: Session, attachment_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {_COLS} FROM ent_attachment WHERE id = :aid"),
            {"aid": attachment_id},
        )
        .mappings()
        .first()
    )
    return _row_to_attachment(row) if row is not None else None


def list_attachments(
    session: Session,
    *,
    entrustment_id: int | None = None,
    artifact_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[int, list[dict[str, Any]]]:
    """列附件：按委托授权或按成果（两者都给则取交集）。调用方已完成可见性校验。"""
    where: list[str] = []
    params: dict[str, Any] = {}
    join = ""
    if entrustment_id is not None:
        where.append("a.entrustment_id = :eid")
        params["eid"] = entrustment_id
    if artifact_id is not None:
        join = "JOIN ent_artifact_attachment l ON l.attachment_id = a.id "
        where.append("l.artifact_id = :art")
        params["art"] = artifact_id
    clause = " AND ".join(where) if where else "1 = 1"

    total = int(
        session.execute(
            text(f"SELECT COUNT(*) FROM ent_attachment a {join}WHERE {clause}"), params
        ).scalar()
        or 0
    )
    rows = (
        session.execute(
            text(
                f"SELECT {', '.join('a.' + c.strip() for c in _COLS.split(','))} "
                f"FROM ent_attachment a {join}WHERE {clause} "
                "ORDER BY a.id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": size, "offset": (page - 1) * size},
        )
        .mappings()
        .all()
    )
    return total, [_row_to_attachment(row) for row in rows]


def list_for_artifact(session: Session, artifact_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                f"SELECT {', '.join('a.' + c.strip() for c in _COLS.split(','))} "
                "FROM ent_attachment a "
                "JOIN ent_artifact_attachment l ON l.attachment_id = a.id "
                "WHERE l.artifact_id = :art ORDER BY a.id ASC"
            ),
            {"art": artifact_id},
        )
        .mappings()
        .all()
    )
    return [_row_to_attachment(row) for row in rows]


# ── 写入 ────────────────────────────────────────────────────────────────────


def store_upload(
    session: Session,
    *,
    uploader_user_id: int,
    owner_user_id: int,
    org_id: int | None,
    filename: str | None,
    content_type: str | None,
    stream: BinaryIO,
    entrustment_id: int | None = None,
    assignment_id: int | None = None,
    source_event_at: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """校验 → 落盘 → 写元数据（便捷入口，内部就是 `read_limited` + `store_bytes`）。"""
    data = read_limited(stream)
    return store_bytes(
        session,
        uploader_user_id=uploader_user_id,
        owner_user_id=owner_user_id,
        org_id=org_id,
        filename=filename,
        content_type=content_type,
        data=data,
        entrustment_id=entrustment_id,
        assignment_id=assignment_id,
        source_event_at=source_event_at,
        now=now,
    )


def store_bytes(
    session: Session,
    *,
    uploader_user_id: int,
    owner_user_id: int,
    org_id: int | None,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    entrustment_id: int | None = None,
    assignment_id: int | None = None,
    source_event_at: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """校验 → 落盘 → 写元数据。任何一步失败都不得留下半份文件。

    `owner_user_id`（数据归属货主）由调用方解析后传入：
    挂在委托授权下时 = 授权的 `entrust_user_id`；私有草稿时 = 委托单的货主。
    服务层不猜这个值 —— 它是授权作用域的锚，猜错就等于越权。
    """
    if len(data) > max_bytes():
        raise AttachmentValidationError(
            f"附件超过上限 {max_bytes()} 字节（≈{max_bytes() // (1024 * 1024)} MiB）"
        )
    if not data:
        raise AttachmentValidationError("附件内容为空")
    safe_name = sanitize_filename(filename)
    normalized_type = assert_allowed_type(content_type)
    digest = hashlib.sha256(data).hexdigest()

    current = now or utcnow_naive()
    scope = (
        f"e{entrustment_id}"
        if entrustment_id is not None
        else (f"a{assignment_id}" if assignment_id is not None else f"u{uploader_user_id}")
    )
    suffix = Path(safe_name).suffix.lower()[:16]
    storage_key = f"{scope}/{current.strftime('%Y%m')}/{uuid.uuid4().hex}{suffix}"
    target = storage_root() / storage_key
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:  # 磁盘/权限问题：明确报错，不静默降级成"上传成功"
        raise AttachmentStorageError(f"附件落盘失败：{exc}") from exc

    ts = _fmt(current)
    try:
        result = cast(
            CursorResult[Any],
            session.execute(
                text(
                    "INSERT INTO ent_attachment "
                    "(entrustment_id, assignment_id, owner_user_id, org_id, uploader_user_id, "
                    " filename, content_type, size_bytes, sha256, storage_key, extract_status, "
                    " extract_error, extracted_chars, source_event_at, created_at, updated_at) "
                    "VALUES (:eid, :aid, :owner, :org, :uploader, :name, :ctype, :size, :sha, "
                    " :key, :status, NULL, NULL, :source_at, :ts, :ts)"
                ),
                {
                    "eid": entrustment_id,
                    "aid": assignment_id,
                    "owner": owner_user_id,
                    "org": org_id,
                    "uploader": uploader_user_id,
                    "name": safe_name,
                    "ctype": normalized_type,
                    "size": len(data),
                    "sha": digest,
                    "key": storage_key,
                    "status": EXTRACT_NOT_REQUESTED,
                    "source_at": _text_ts(source_event_at),
                    "ts": ts,
                },
            ),
        )
        session.commit()
    except Exception:
        # 元数据写失败 → 把已落盘的文件删掉，避免出现"有文件无记录"的孤儿
        target.unlink(missing_ok=True)
        raise

    created = get_attachment(session, int(result.lastrowid or 0))
    assert created is not None
    return created


def bind_to_artifact(
    session: Session,
    *,
    artifact_id: int,
    attachment_id: int,
    actor_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把附件挂到成果上。重复绑定是**幂等**的（唯一键 + 冲突即视为已绑定）。"""
    current = now or utcnow_naive()
    exists = int(
        session.execute(
            text(
                "SELECT COUNT(*) FROM ent_artifact_attachment "
                "WHERE artifact_id = :art AND attachment_id = :att"
            ),
            {"art": artifact_id, "att": attachment_id},
        ).scalar()
        or 0
    )
    if exists:
        return {"artifact_id": artifact_id, "attachment_id": attachment_id, "created": False}
    session.execute(
        text(
            "INSERT INTO ent_artifact_attachment "
            "(artifact_id, attachment_id, created_by, created_at) "
            "VALUES (:art, :att, :by, :ts)"
        ),
        {"art": artifact_id, "att": attachment_id, "by": actor_id, "ts": _fmt(current)},
    )
    session.commit()
    return {"artifact_id": artifact_id, "attachment_id": attachment_id, "created": True}


def set_extract_status(
    session: Session,
    *,
    attachment_id: int,
    status: str,
    error: str | None = None,
    extracted_chars: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """推进提取状态（S1 第 6 条的提取作业调用此入口；本增量不实现提取器）。"""
    if status not in EXTRACT_STATUSES:
        raise AttachmentValidationError(
            f"未知提取状态 {status!r}；取值域：{sorted(EXTRACT_STATUSES)}"
        )
    current = now or utcnow_naive()
    result = cast(
        CursorResult[Any],
        session.execute(
            text(
                "UPDATE ent_attachment SET extract_status = :status, extract_error = :err, "
                "extracted_chars = :chars, updated_at = :ts WHERE id = :aid"
            ),
            {
                "status": status,
                "err": error,
                "chars": extracted_chars,
                "ts": _fmt(current),
                "aid": attachment_id,
            },
        ),
    )
    if int(result.rowcount or 0) == 0:
        raise AttachmentNotFoundError(f"附件 {attachment_id} 不存在")
    session.commit()
    updated = get_attachment(session, attachment_id)
    assert updated is not None
    return updated


# ── 提取文本（S1 第 6 条 / ENT-013） ────────────────────────────────────────
# 文本单独一张表 `ent_attachment_text`（1:1）：体积与附件元数据差三个数量级，
# 且它可被重抽/人工转录覆盖 —— 混在附件表里会让"列附件"这种高频读拖着正文。

_TEXT_COLS = (
    "attachment_id, content, char_count, truncated, source, sha256, "
    "created_by, created_at, updated_at"
)


def _row_to_text(row: Any) -> dict[str, Any]:
    return {
        "attachment_id": int(row["attachment_id"]),
        "content": str(row["content"]),
        "char_count": int(row["char_count"]),
        "truncated": bool(row["truncated"]),
        "source": str(row["source"]),
        "sha256": str(row["sha256"]),
        "created_by": int(row["created_by"]) if row["created_by"] is not None else None,
        "created_at": _text_ts(row["created_at"]),
        "updated_at": _text_ts(row["updated_at"]),
    }


def get_text(session: Session, attachment_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {_TEXT_COLS} FROM ent_attachment_text WHERE attachment_id = :aid"),
            {"aid": attachment_id},
        )
        .mappings()
        .first()
    )
    return _row_to_text(row) if row is not None else None


def get_texts(
    session: Session, attachment_ids: list[int], *, limit_chars: int
) -> dict[int, dict[str, Any]]:
    """批量取文本（供 Agent 上下文用），每条按 `limit_chars` 截断。

    一次查完再在内存里截断，而不是逐条查：作业要读的附件数量不多，
    但 N 次往返会在每次作业上叠加延迟。
    """
    if not attachment_ids:
        return {}
    placeholders = ", ".join(f":a{i}" for i in range(len(attachment_ids)))
    params = {f"a{i}": aid for i, aid in enumerate(attachment_ids)}
    rows = (
        session.execute(
            text(
                f"SELECT {_TEXT_COLS} FROM ent_attachment_text "
                f"WHERE attachment_id IN ({placeholders}) AND source IN ('extractor', 'manual_transcription')"
            ),
            params,
        )
        .mappings()
        .all()
    )
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = _row_to_text(row)
        if limit_chars > 0 and len(item["content"]) > limit_chars:
            item["content"] = item["content"][:limit_chars]
            item["truncated"] = True
        result[int(item["attachment_id"])] = item
    return result


def hash_text(content: str) -> str:
    """提取文本的内容哈希。

    单独暴露出来，是因为**幂等键的请求体快照**也要用它：如果 API 层自己写一份
    `sha256(content.encode("utf-8"))`，将来这里改成"先归一空白再哈希"时，
    幂等判断与落库哈希就会用两套口径 —— 同一次转录会被判成两次不同内容。
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def drop_text(session: Session, attachment_id: int) -> int:
    """删除附件的提取文本（派生数据：随时可由原文件重抽或由人重建）。

    用途：重抽后结论变成"没有文本"（扫描件/不支持/失败）时，旧文本必须一起撤掉，
    否则会出现"状态说没有文本、接口却能读出文本"的矛盾。
    **原始证据（附件文件本身）永远不动** —— 这里删的只是派生结果。
    """
    result = cast(
        CursorResult[Any],
        session.execute(
            text("DELETE FROM ent_attachment_text WHERE attachment_id = :aid"),
            {"aid": attachment_id},
        ),
    )
    session.commit()
    return int(result.rowcount or 0)


def upsert_text(
    session: Session,
    *,
    attachment_id: int,
    content: str,
    source: str,
    truncated: bool = False,
    created_by: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """写入/覆盖提取文本（**重抽就是覆盖同一行**，不留多份互相矛盾的历史）。

    `source` 区分机读提取与人工转录 —— 两者的可信度不同，覆盖时必须改来源，
    否则会出现"人工转录的内容被标成机器抽取"这种来源错标。
    """
    if source not in TEXT_SOURCES:
        raise AttachmentValidationError(f"未知文本来源 {source!r}；取值域：{sorted(TEXT_SOURCES)}")
    if not content.strip():
        raise AttachmentValidationError("提取文本为空，不能入库")

    current = now or utcnow_naive()
    ts = _fmt(current)
    digest = hash_text(content)
    existing = get_text(session, attachment_id)
    if existing is None:
        session.execute(
            text(
                "INSERT INTO ent_attachment_text "
                "(attachment_id, content, char_count, truncated, source, sha256, "
                " created_by, created_at, updated_at) "
                "VALUES (:aid, :content, :chars, :trunc, :source, :sha, :by, :ts, :ts)"
            ),
            {
                "aid": attachment_id,
                "content": content,
                "chars": len(content),
                "trunc": 1 if truncated else 0,
                "source": source,
                "sha": digest,
                "by": created_by,
                "ts": ts,
            },
        )
    else:
        session.execute(
            text(
                "UPDATE ent_attachment_text SET content = :content, char_count = :chars, "
                "truncated = :trunc, source = :source, sha256 = :sha, created_by = :by, "
                "updated_at = :ts WHERE attachment_id = :aid"
            ),
            {
                "aid": attachment_id,
                "content": content,
                "chars": len(content),
                "trunc": 1 if truncated else 0,
                "source": source,
                "sha": digest,
                "by": created_by,
                "ts": ts,
            },
        )
    session.commit()
    saved = get_text(session, attachment_id)
    assert saved is not None
    return saved


def resolve_path(attachment: dict[str, Any]) -> Path:
    """附件在磁盘上的绝对路径；文件缺失即报存储错误（不返回空路径）。"""
    root = storage_root()
    candidate = (root / str(attachment["storage_key"])).resolve()
    # 防越界：storage_key 由服务端生成，但读取侧仍做一次 containment 断言
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise AttachmentStorageError(f"附件 {attachment['attachment_id']} 的存储键越界")
    if not candidate.is_file():
        raise AttachmentStorageError(
            f"附件 {attachment['attachment_id']} 的文件已丢失（storage_key={attachment['storage_key']}）"
        )
    return candidate


__all__ = [
    "EXTRACT_DONE",
    "EXTRACT_FAILED",
    "EXTRACT_NEEDS_TRANSCRIPTION",
    "EXTRACT_NOT_REQUESTED",
    "EXTRACT_PENDING",
    "EXTRACT_RUNNING",
    "EXTRACT_STATUSES",
    "EXTRACT_UNSUPPORTED",
    "TEXT_SOURCE_EXTRACTOR",
    "TEXT_SOURCE_MANUAL",
    "TEXT_SOURCES",
    "AttachmentError",
    "AttachmentNotFoundError",
    "AttachmentStorageError",
    "AttachmentValidationError",
    "allowed_types",
    "assert_allowed_type",
    "bind_to_artifact",
    "drop_text",
    "get_attachment",
    "get_text",
    "get_texts",
    "hash_text",
    "list_attachments",
    "list_for_artifact",
    "max_bytes",
    "read_limited",
    "resolve_path",
    "sanitize_filename",
    "set_extract_status",
    "storage_root",
    "store_bytes",
    "store_upload",
    "upsert_text",
    "utcnow_naive",
]
