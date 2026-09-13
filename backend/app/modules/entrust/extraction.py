"""附件文本提取器 —— S1 第 6 条（ENT-013 / AC-17）。

零第三方依赖
------------
项目依赖里**没有任何 PDF / Office 解析库**，本模块也不新增 —— 全部用标准库：

* `text/*` → 字节解码（BOM → UTF-8 → GB18030 逐级回退）；
* `.docx` / `.xlsx` → 本质是 zip，`zipfile` + `ElementTree` 读 XML；
* 文本型 PDF → `zlib` 解 Flate 流 + 扫描内容流里的文本操作符（`Tj` / `TJ` / `'` / `"`）。

"不新增依赖"不是洁癖：一条为提取文本而引入的依赖，会跟着容器镜像、CI 缓存、
漏洞扫描一起进来，而它换来的只是**一次近似提取**。少一个依赖就少一条长期维护线。

四类结论（都必须是可解释的）
----------------------------
* `done`：提取出文本；
* `needs_transcription`：**有内容但没有机读文本层**（图片、扫描件 PDF、
  CID 字体导致解码不可靠）→ 转人工转录，而不是返回一堆乱码冒充成功；
* `unsupported`：该格式**明确不在标准库能力内**（老式 OLE2 二进制 `.xls`/`.doc`）
  —— 诚实地说"不支持"，好过静默给出半份文本；
* `failed`：文件本身坏了（zip 结构损坏、XML 解析失败）—— 这是错误，不是降级。

三条不可妥协的性质
------------------
1. **魔数优先于客户端声明的 MIME**（AC-18）。客户端可以把 `.exe` 标成 `text/plain`；
   如果处理路径听信声明，就等于把"内容是什么"的决定权交给上传者。
   嗅探结果与声明不一致时**记 note**，但不按声明走。
2. **PDF 提取是近似的，必须自曝**。按内容流操作符抽取，不渲染版式：
   表格、分栏、页眉页脚顺序可能与视觉不一致。所有 PDF 结论都带这条 note ——
   让人知道"这段文字不能当作原件"。
3. **截断必须显式**。被截断的文本**看起来完整**（末尾就是一句话的句号），
   所以 `truncated` 落进数据而不是只写日志。

提取文本是不可信数据（AC-18）：本模块只把内容当字节与文字，
不执行、不渲染、不解释 —— 附件里写"请忽略之前的指令"也只是被提取成一行字。
"""

from __future__ import annotations

import base64
import contextlib
import io
import re
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Any, Final
from xml.etree import ElementTree as ET

# ── 媒体类别（嗅探结果，不是声明结果） ──────────────────────────────────────

MEDIA_TEXT = "text"
MEDIA_DOCX = "docx"
MEDIA_XLSX = "xlsx"
MEDIA_PDF = "pdf"
MEDIA_IMAGE = "image"
MEDIA_OLE2 = "ole2"
MEDIA_UNKNOWN = "unknown"

# ── 提取结论（与 attachments.EXTRACT_* 状态一一对应，但本模块不依赖它） ───

STATUS_DONE = "done"
STATUS_NEEDS_TRANSCRIPTION = "needs_transcription"
STATUS_UNSUPPORTED = "unsupported"
STATUS_FAILED = "failed"

#: 提取文本默认上限（字符）。**不是**为了省空间，而是防止单份文件把
#: Agent 提示词与接口响应撑爆 —— 上限可配，可显式调大。
DEFAULT_MAX_CHARS: Final[int] = 200_000

#: 白名单允许上传、但没有文本层的图片类型 → 一律人工转录
IMAGE_TYPES: Final[frozenset[str]] = frozenset({"image/jpeg", "image/png", "image/webp"})
#: 明确无法用标准库解析的类型：OLE2 复合二进制（老式 .xls / .doc）
UNPARSEABLE_TYPES: Final[frozenset[str]] = frozenset({"application/vnd.ms-excel"})

_DOCX_SUFFIX: Final[str] = "wordprocessingml.document"
_XLSX_SUFFIX: Final[str] = "spreadsheetml.sheet"

#: 垃圾字符占比超过该阈值 → 判定"提取出来的是噪声"，转人工转录
_GARBAGE_LIMIT: Final[float] = 0.3

_TEXT_ENCODINGS: Final[tuple[str, ...]] = ("utf-8-sig", "utf-8", "gb18030")

_PDF_STREAM_RE: Final[re.Pattern[bytes]] = re.compile(rb"stream\r?\n")
_PDF_OP_RE: Final[re.Pattern[bytes]] = re.compile(rb"[A-Za-z*'\"]{1,3}")
_PDF_TEXT_OPS: Final[frozenset[bytes]] = frozenset({b"Tj", b"TJ", b"'", b'"'})
_PDF_NEWLINE_OPS: Final[frozenset[bytes]] = frozenset({b"Td", b"TD", b"T*"})
_PDF_ESCAPE_MAP: Final[dict[int, int]] = {
    0x6E: 0x0A,  # \n
    0x72: 0x0D,  # \r
    0x74: 0x09,  # \t
    0x62: 0x08,  # \b
    0x66: 0x0C,  # \f
}

#: 这些类别是"控制/格式/未分配/私用/代理"字符 —— 出现在正文里即为噪声
_BAD_CATEGORIES: Final[frozenset[str]] = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})


@dataclass(slots=True)
class ExtractOutcome:
    """一次提取的完整结论（**可解释**：每个结论都带 detail 与 notes）。"""

    status: str
    media_kind: str
    text: str | None
    chars: int | None
    detail: str
    notes: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "media_kind": self.media_kind,
            "text": self.text,
            "chars": self.chars,
            "detail": self.detail,
            "notes": list(self.notes),
            "truncated": self.truncated,
        }


# ── 嗅探 ────────────────────────────────────────────────────────────────────


def _suffix_of(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def sniff_media(data: bytes, content_type: str | None, filename: str | None = None) -> str:
    """判定文件**实际**是什么（魔数优先，声明只作兜底）。

    只看前若干字节，不解析结构：嗅探必须是廉价的，否则"大文件上传"会在
    这一步付出代价。
    """
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return MEDIA_PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff"):
        return MEDIA_IMAGE
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return MEDIA_IMAGE
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return MEDIA_OLE2
    if head.startswith(b"PK\x03\x04"):
        return _sniff_ooxml(data)

    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in IMAGE_TYPES:
        return MEDIA_IMAGE
    if declared in UNPARSEABLE_TYPES:
        return MEDIA_OLE2
    if declared.startswith("text/"):
        return MEDIA_TEXT
    if declared.endswith(_DOCX_SUFFIX):
        return MEDIA_DOCX
    if declared.endswith(_XLSX_SUFFIX):
        return MEDIA_XLSX
    if declared == "application/pdf":
        return MEDIA_PDF

    suffix = _suffix_of(filename)
    if suffix in {"txt", "csv", "md", "markdown", "log", "tsv"}:
        return MEDIA_TEXT
    if suffix == "pdf":
        return MEDIA_PDF
    if suffix == "docx":
        return MEDIA_DOCX
    if suffix == "xlsx":
        return MEDIA_XLSX
    return MEDIA_UNKNOWN


def _sniff_ooxml(data: bytes) -> str:
    """zip 容器里看条目名区分 docx / xlsx；不是有效 zip 则归为 unknown。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return MEDIA_UNKNOWN
    if any(name.startswith("word/") for name in names):
        return MEDIA_DOCX
    if any(name.startswith("xl/") for name in names):
        return MEDIA_XLSX
    return MEDIA_UNKNOWN


def _declared_mismatch(media: str, content_type: str | None) -> list[str]:
    """声明类型与嗅探结果不一致时给出提示（不改变处理路径，只记 note）。"""
    declared = (content_type or "").split(";")[0].strip().lower()
    if not declared:
        return []
    expected = {
        MEDIA_PDF: {"application/pdf"},
        MEDIA_DOCX: {f"application/{_DOCX_SUFFIX}"},
        MEDIA_XLSX: {f"application/{_XLSX_SUFFIX}"},
        MEDIA_OLE2: set(UNPARSEABLE_TYPES),
        MEDIA_IMAGE: set(IMAGE_TYPES),
    }.get(media)
    if media == MEDIA_TEXT:
        if not declared.startswith("text/"):
            return [f"内容是文本，但声明类型为 {declared}；已按实际内容处理"]
        return []
    if expected is not None and declared not in expected:
        return [f"声明类型 {declared} 与实际内容不符；已按实际内容处理"]
    return []


# ── 文本解码 ────────────────────────────────────────────────────────────────


def decode_text_bytes(data: bytes) -> tuple[str, list[str]]:
    """BOM → UTF-8 → GB18030 逐级回退解码（每一步都记下来）。"""
    notes: list[str] = []
    for encoding in _TEXT_ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-8-sig":
            if data[:3] == b"\xef\xbb\xbf":
                notes.append("检测到 UTF-8 BOM，已剥离")
        elif encoding == "gb18030":
            notes.append("按 GB18030 解码（原文可能是 GBK/GB2312）")
        return text, notes
    return data.decode("latin-1"), ["非 UTF-8/GB18030 编码，按 latin-1 兜底解码，请人工核对"]


def _garbage_ratio(text: str) -> float:
    """噪声占比：控制/未分配字符的比例（用于识别"解出来的是乱码"）。"""
    meaningful = 0
    bad = 0
    for char in text:
        if char in "\n\r\t":
            continue
        meaningful += 1
        if unicodedata.category(char) in _BAD_CATEGORIES or char == "\ufffd":
            bad += 1
    if meaningful == 0:
        return 1.0
    return bad / meaningful


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── DOCX / XLSX（都是 zip + XML） ───────────────────────────────────────────

_DOCX_PART = "word/document.xml"


def _local_name(tag: str) -> str:
    return tag.rpartition("}")[2]


def _walk_docx(element: ET.Element, out: list[str]) -> None:
    """按**文档顺序**收集文本：`w:t` 是文字，`w:tab`/`w:br` 是分隔，`w:p` 是段落。"""
    name = _local_name(element.tag)
    if name == "t":
        out.append(element.text or "")
        return
    if name == "tab":
        out.append("\t")
        return
    if name in {"br", "cr"}:
        out.append("\n")
        return
    for child in element:
        _walk_docx(child, out)
    if name == "p":
        out.append("\n")


def extract_docx(data: bytes) -> str:
    """抽取 `word/document.xml` 的正文文本（不含批注、修订、文本框）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if _DOCX_PART not in archive.namelist():
            raise ValueError(f"docx 缺少 {_DOCX_PART}")
        xml = archive.read(_DOCX_PART)
    root = ET.fromstring(xml)
    out: list[str] = []
    _walk_docx(root, out)
    return "".join(out)


def extract_xlsx(data: bytes) -> str:
    """抽取单元格文本：共享字符串表 + 各工作表（含内联字符串与数值）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root:
                if _local_name(item.tag) != "si":
                    continue
                parts: list[str] = []
                for node in item.iter():
                    if _local_name(node.tag) == "t":
                        parts.append(node.text or "")
                shared.append("".join(parts))

        sheet_names = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        blocks: list[str] = []
        for sheet in sheet_names:
            root = ET.fromstring(archive.read(sheet))
            rows: list[str] = []
            for row in root.iter():
                if _local_name(row.tag) != "row":
                    continue
                cells: list[str] = []
                for cell in row:
                    if _local_name(cell.tag) != "c":
                        continue
                    cells.append(_xlsx_cell_text(cell, shared))
                line = "\t".join(cells).rstrip("\t")
                if line.strip():
                    rows.append(line)
            label = sheet.rsplit("/", 1)[-1].removesuffix(".xml")
            blocks.append(f"# {label}\n" + "\n".join(rows))
    return "\n\n".join(blocks)


def _xlsx_cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.get("t")
    value = ""
    for node in cell:
        name = _local_name(node.tag)
        if name == "v":
            value = node.text or ""
            break
        if name == "is":
            parts: list[str] = []
            for inner in node.iter():
                if _local_name(inner.tag) == "t":
                    parts.append(inner.text or "")
            value = "".join(parts)
            break
    if cell_type == "s" and value:
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return value
    return value


# ── PDF（zlib 解流 + 内容流文本操作符） ─────────────────────────────────────


def _inflate(raw: bytes) -> bytes:
    """尝试 zlib/gzip/裸 deflate；全部失败则原样返回（流可能未压缩）。"""
    for wbits in (15, 47, -15):
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            continue
    return raw


def _decode_stream(raw: bytes) -> bytes:
    payload = raw.strip()
    if payload.endswith(b"~>"):
        with contextlib.suppress(ValueError):
            payload = base64.a85decode(payload, adobe=True)
    return _inflate(payload)


def _pdf_bytes_to_text(raw: bytes) -> str:
    """PDF 字符串 → Python 字符串（UTF-16BE/LE 带 BOM 时按其编码）。"""
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw[2:].decode("utf-16-be")
        except UnicodeDecodeError:
            return ""
    if raw.startswith(b"\xff\xfe"):
        try:
            return raw[2:].decode("utf-16-le")
        except UnicodeDecodeError:
            return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _read_pdf_literal(data: bytes, start: int) -> tuple[str, int]:
    """读 `(...)` 字面量，处理 `\\n`、八进制转义、嵌套括号与续行。"""
    depth = 1
    index = start + 1
    total = len(data)
    buffer = bytearray()
    while index < total:
        byte = data[index]
        if byte == 0x5C:  # 反斜杠
            if index + 1 >= total:
                break
            nxt = data[index + 1]
            if nxt in _PDF_ESCAPE_MAP:
                buffer.append(_PDF_ESCAPE_MAP[nxt])
                index += 2
                continue
            if 0x30 <= nxt <= 0x37:  # 八进制，最多 3 位
                digits = b""
                cursor = index + 1
                while cursor < total and len(digits) < 3 and 0x30 <= data[cursor] <= 0x37:
                    digits += data[cursor : cursor + 1]
                    cursor += 1
                buffer.append(int(digits, 8) & 0xFF)
                index = cursor
                continue
            if nxt in (0x28, 0x29, 0x5C):
                buffer.append(nxt)
                index += 2
                continue
            if nxt in (0x0A, 0x0D):  # 续行：反斜杠 + 换行 = 什么也不输出
                index += 2
                continue
            buffer.append(nxt)
            index += 2
            continue
        if byte == 0x28:
            depth += 1
            buffer.append(byte)
            index += 1
            continue
        if byte == 0x29:
            depth -= 1
            if depth == 0:
                return _pdf_bytes_to_text(bytes(buffer)), index + 1
            buffer.append(byte)
            index += 1
            continue
        buffer.append(byte)
        index += 1
    return _pdf_bytes_to_text(bytes(buffer)), total


def _read_pdf_hex(data: bytes, start: int) -> tuple[str, int]:
    """读 `<48656C6C6F>` 十六进制字符串。"""
    end = data.find(b">", start + 1)
    if end < 0:
        return "", len(data)
    digits = re.sub(rb"[^0-9A-Fa-f]", b"", data[start + 1 : end])
    if len(digits) % 2:
        digits += b"0"
    try:
        return _pdf_bytes_to_text(bytes.fromhex(digits.decode("ascii"))), end + 1
    except ValueError:
        return "", end + 1


def _scan_content_stream(content: bytes) -> tuple[str, int]:
    """扫描内容流：遇到显示操作符才把累积的字符串吐出来。

    这样 `[(甲) -50 (乙)] TJ` 会拼成"甲乙"，而 `(甲) Td (乙) Tj` 之间会插入换行。
    返回 (文本, 字符串个数) —— 字符串个数用来区分"扫描件"与"真的没有文字"。
    """
    out: list[str] = []
    pending: list[str] = []
    literals = 0
    index = 0
    total = len(content)
    while index < total:
        byte = content[index : index + 1]
        if byte == b"(":
            piece, index = _read_pdf_literal(content, index)
            pending.append(piece)
            literals += 1
            continue
        if byte == b"<" and content[index : index + 2] != b"<<":
            piece, index = _read_pdf_hex(content, index)
            pending.append(piece)
            literals += 1
            continue
        match = _PDF_OP_RE.match(content, index)
        if match:
            operator = match.group(0)
            if operator in _PDF_TEXT_OPS:
                out.append("".join(pending))
                pending.clear()
            elif operator in _PDF_NEWLINE_OPS and out and not out[-1].endswith("\n"):
                out.append("\n")
            index = match.end()
            continue
        index += 1
    if pending:
        out.append("".join(pending))
    return "".join(out), literals


def extract_pdf(data: bytes) -> tuple[str, int]:
    """抽取文本层：逐条解压 `stream ... endstream` 后扫描文本操作符。

    返回 (文本, 字符串个数)。字符串个数为 0 时，调用方按"扫描件"处理。
    """
    chunks: list[str] = []
    literals = 0
    for match in _PDF_STREAM_RE.finditer(data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        piece, count = _scan_content_stream(_decode_stream(data[start:end]))
        literals += count
        if piece.strip():
            chunks.append(piece)
    return "\n".join(chunks), literals


# ── 收口：统一结论 ──────────────────────────────────────────────────────────

_PDF_NOTE: Final[str] = (
    "PDF 文本层提取：按内容流的文本操作符抽取，未渲染版式；"
    "表格/分栏顺序可能与原件不一致，引用前请人工核对。"
)


def _finalize(
    *,
    media: str,
    text: str,
    limit: int,
    detail: str,
    notes: list[str],
    garbage_status: str = STATUS_NEEDS_TRANSCRIPTION,
) -> ExtractOutcome:
    """归一 → 垃圾检测 → 截断 → 出结论。四条判定集中在这里，避免各处漂移。

    `garbage_status` 区分"解出来是噪声"的两种处置：内容确实是扫描件/图片 →
    请人转录（`needs_transcription`）；内容本身就不是文本（二进制冒充文本）→
    直接判 `unsupported` —— 让用户去转录一份二进制文件是白费力气。
    """
    normalized = _normalize(text)
    if not normalized:
        return ExtractOutcome(
            status=STATUS_NEEDS_TRANSCRIPTION,
            media_kind=media,
            text=None,
            chars=None,
            detail=detail,
            notes=notes,
            truncated=False,
        )
    if _garbage_ratio(normalized) > _GARBAGE_LIMIT:
        return ExtractOutcome(
            status=garbage_status,
            media_kind=media,
            text=None,
            chars=None,
            detail=f"{detail}（解出的内容以噪声字符为主，判定为不可机读）",
            notes=[*notes, "提取结果含大量不可打印字符，未保存以防被误用"],
            truncated=False,
        )
    truncated = len(normalized) > limit
    if truncated:
        normalized = normalized[:limit]
        notes = [*notes, f"文本超过上限 {limit} 字符，已截断（truncated=true）"]
    return ExtractOutcome(
        status=STATUS_DONE,
        media_kind=media,
        text=normalized,
        chars=len(normalized),
        detail=detail,
        notes=notes,
        truncated=truncated,
    )


def extract(
    data: bytes,
    *,
    content_type: str | None,
    filename: str | None = None,
    max_chars: int | None = None,
) -> ExtractOutcome:
    """提取入口：嗅探 → 分派 → 出结论。**任何异常都不外抛**（转 failed）。"""
    limit = DEFAULT_MAX_CHARS if max_chars is None else max_chars
    media = sniff_media(data, content_type, filename)
    notes = _declared_mismatch(media, content_type)

    if media == MEDIA_IMAGE:
        return ExtractOutcome(
            status=STATUS_NEEDS_TRANSCRIPTION,
            media_kind=media,
            text=None,
            chars=None,
            detail="图片没有文本层，本服务不做 OCR；请人工转录后提交",
            notes=notes,
        )
    if media == MEDIA_OLE2:
        return ExtractOutcome(
            status=STATUS_UNSUPPORTED,
            media_kind=media,
            text=None,
            chars=None,
            detail="老式 OLE2 二进制格式（.xls/.doc）不在标准库解析能力内，请另存为 .xlsx/.docx 或人工转录",
            notes=notes,
        )
    if media == MEDIA_UNKNOWN:
        return ExtractOutcome(
            status=STATUS_UNSUPPORTED,
            media_kind=media,
            text=None,
            chars=None,
            detail="无法识别的文件类型，未做提取",
            notes=notes,
        )

    try:
        if media == MEDIA_TEXT:
            text, decode_notes = decode_text_bytes(data)
            # 声明为文本但解出的是噪声 → 这是二进制冒充文本，转人工转录没有意义
            return _finalize(
                media=media,
                text=text,
                limit=limit,
                detail="按文本解码",
                notes=[*notes, *decode_notes],
                garbage_status=STATUS_UNSUPPORTED,
            )

        if media == MEDIA_DOCX:
            return _finalize(
                media=media,
                text=extract_docx(data),
                limit=limit,
                detail="抽取 word/document.xml 正文",
                notes=notes,
            )

        if media == MEDIA_XLSX:
            return _finalize(
                media=media,
                text=extract_xlsx(data),
                limit=limit,
                detail="抽取共享字符串表与各工作表单元格",
                notes=[*notes, "单元格格式与公式未保留，数值按原样文本输出"],
            )

        text, literals = extract_pdf(data)
        if literals == 0 or not text.strip():
            return ExtractOutcome(
                status=STATUS_NEEDS_TRANSCRIPTION,
                media_kind=media,
                text=None,
                chars=None,
                detail="PDF 没有可机读的文本层（疑似扫描件或纯图片页），请人工转录",
                notes=[*notes, _PDF_NOTE],
            )
        return _finalize(
            media=media,
            text=text,
            limit=limit,
            detail="抽取 PDF 文本层",
            notes=[*notes, _PDF_NOTE],
        )
    except (zipfile.BadZipFile, ET.ParseError, ValueError, OSError, MemoryError) as exc:
        return ExtractOutcome(
            status=STATUS_FAILED,
            media_kind=media,
            text=None,
            chars=None,
            detail=f"文件解析失败：{type(exc).__name__}: {exc}",
            notes=notes,
        )


#: 允许人工转录的结论状态 —— 只有这两类才需要人补文本
TRANSCRIBABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_NEEDS_TRANSCRIPTION, STATUS_UNSUPPORTED, STATUS_FAILED}
)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "IMAGE_TYPES",
    "MEDIA_DOCX",
    "MEDIA_IMAGE",
    "MEDIA_OLE2",
    "MEDIA_PDF",
    "MEDIA_TEXT",
    "MEDIA_UNKNOWN",
    "MEDIA_XLSX",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_NEEDS_TRANSCRIPTION",
    "STATUS_UNSUPPORTED",
    "TRANSCRIBABLE_STATUSES",
    "UNPARSEABLE_TYPES",
    "ExtractOutcome",
    "decode_text_bytes",
    "extract",
    "extract_docx",
    "extract_pdf",
    "extract_xlsx",
    "sniff_media",
]
