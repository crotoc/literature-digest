"""按内容识别文件类型，外加 PDF 专项探查。

设计要点：
  - **不看文件名、不信客户端给的 Content-Type**。上传来的那两样都是用户可控的，
    把它们当事实就等于把类型判定交给了上传者。这里只认字节。
  - 判定与策略分开：`sniff` 只回答"这是什么"，大小上限和白名单由调用方
    作为参数传进 `check_upload`（和 `jobs.transition(allowed=...)` 同一个套路——
    策略当数据传，cap 不内置业务规则）。
  - 输入统一归一成**可 seek 的二进制流**，所以 bytes / 路径 / 文件对象三种入口
    行为完全一致；不可 seek 的流直接报错，让调用方自己决定怎么缓冲，
    而不是在这里偷偷读进内存。
"""

import codecs
import io
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

# ── 对外 DTO ───────────────────────────────────────────────────────────────

UNKNOWN_MEDIA_TYPE = "application/octet-stream"

KIND_PDF = "pdf"
KIND_IMAGE = "image"
KIND_TEXT = "text"
KIND_ARCHIVE = "archive"
KIND_DOCUMENT = "document"
KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PdfInfo:
    """PDF 专项探查结果。拿不到的字段给 None，不猜。"""

    version: str
    """头部声明的版本，如 "1.7"。头部没写就是空串。"""

    encrypted: bool
    """文件声明了加密。注意：空口令加密很常见（只限制打印），仍然读得出内容。"""

    page_count: int | None
    """页数；结构坏到读不出来时为 None。"""

    has_text_layer: bool | None
    """前几页是否抽得出非空白文本；读不出来时为 None。False 多半是扫描件。"""

    damaged: bool
    """头部是 PDF，但解析器读不下去。"""


@dataclass(frozen=True)
class FileProbe:
    """按内容得出的识别结果。"""

    media_type: str
    extension: str
    """规范扩展名，带点，如 ".pdf"；认不出来是空串。"""

    kind: str
    """粗分类：pdf / image / text / archive / document / unknown。"""

    size: int
    pdf: PdfInfo | None = None
    """仅当 media_type 是 PDF 且调用方要求了 deep 探查时非 None。"""


class FileProbeError(Exception):
    """本模块所有异常的基类。"""


class FileTooLarge(FileProbeError):
    def __init__(self, size: int, max_bytes: int) -> None:
        super().__init__(f"文件 {size} 字节，超过上限 {max_bytes} 字节")
        self.size = size
        self.max_bytes = max_bytes


class UnsupportedMediaType(FileProbeError):
    def __init__(self, media_type: str, allowed: frozenset[str]) -> None:
        super().__init__(f"类型 {media_type} 不在允许集合 {sorted(allowed)} 内")
        self.media_type = media_type
        self.allowed = allowed


class CorruptFile(FileProbeError):
    def __init__(self, media_type: str, detail: str) -> None:
        super().__init__(f"{media_type} 结构损坏：{detail}")
        self.media_type = media_type
        self.detail = detail


# ── 魔数表 ─────────────────────────────────────────────────────────────────

# (前缀, media_type, 扩展名, kind)。按长度降序匹配，避免短前缀抢掉长前缀。
_MAGIC: tuple[tuple[bytes, str, str, str], ...] = (
    (b"%PDF-", "application/pdf", ".pdf", KIND_PDF),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png", KIND_IMAGE),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg", KIND_IMAGE),
    (b"GIF87a", "image/gif", ".gif", KIND_IMAGE),
    (b"GIF89a", "image/gif", ".gif", KIND_IMAGE),
    (b"II*\x00", "image/tiff", ".tif", KIND_IMAGE),
    (b"MM\x00*", "image/tiff", ".tif", KIND_IMAGE),
    (b"BM", "image/bmp", ".bmp", KIND_IMAGE),
    (b"\x1f\x8b", "application/gzip", ".gz", KIND_ARCHIVE),
    (b"{\\rtf", "application/rtf", ".rtf", KIND_TEXT),
    (b"%!PS", "application/postscript", ".ps", KIND_DOCUMENT),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage", "", KIND_DOCUMENT),
)

_MAGIC_SORTED = tuple(sorted(_MAGIC, key=lambda row: -len(row[0])))
_MAX_MAGIC = max(len(row[0]) for row in _MAGIC)

_ZIP_MAGIC = b"PK\x03\x04"

# zip 家族内部靠成员名区分。(标志成员, media_type, 扩展名, kind)
_OOXML = "application/vnd.openxmlformats-officedocument"

_ZIP_MEMBERS: tuple[tuple[str, str, str, str], ...] = (
    ("word/document.xml", f"{_OOXML}.wordprocessingml.document", ".docx", KIND_DOCUMENT),
    ("xl/workbook.xml", f"{_OOXML}.spreadsheetml.sheet", ".xlsx", KIND_DOCUMENT),
    ("ppt/presentation.xml", f"{_OOXML}.presentationml.presentation", ".pptx", KIND_DOCUMENT),
)

_TEXT_SAMPLE_BYTES = 4096
_PDF_TEXT_PAGES = 3


# ── 输入归一 ───────────────────────────────────────────────────────────────

def _as_stream(source: bytes | bytearray | str | os.PathLike | BinaryIO) -> tuple[BinaryIO, bool]:
    """把三种入口归一成可 seek 的二进制流。

    Returns:
        (流, 是否由本函数打开——需要调用方关闭)
    """
    if isinstance(source, bytes | bytearray):
        return io.BytesIO(bytes(source)), True
    if isinstance(source, str | os.PathLike):
        return Path(source).open("rb"), True
    if not (hasattr(source, "read") and hasattr(source, "seek")):
        raise TypeError("source 必须是 bytes、路径，或可 seek 的二进制流")
    if not source.seekable():
        raise ValueError("流不可 seek；请先缓冲到 bytes 或落盘后再传进来")
    return source, False


def _stream_size(stream: BinaryIO) -> int:
    here = stream.tell()
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    stream.seek(here)
    return size


# ── 识别 ───────────────────────────────────────────────────────────────────

def _looks_like_text(head: bytes) -> bool:
    """UTF-8/UTF-16 可解码且不含 NUL，就算文本。"""
    if not head:
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in head:
        return False
    # 用增量解码器而不是 head.decode()：采样几乎必然切在多字节序列中间，
    # 增量解码器会把不完整的尾部**缓冲**起来而不报错，正好区分
    # "尾部被采样截断"（仍是文本）和"字节真的非法"（不是文本）。
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True


def _classify_zip(stream: BinaryIO) -> tuple[str, str, str]:
    """区分 zip 家族。认不出具体格式就当普通 zip。"""
    here = stream.tell()
    try:
        # epub 规范要求第一个成员是未压缩的 mimetype，固定落在偏移 30
        stream.seek(0)
        prefix = stream.read(58)
        if prefix[30:58] == b"mimetypeapplication/epub+zip":
            return "application/epub+zip", ".epub", KIND_DOCUMENT

        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            names = set(archive.namelist())
        for member, media_type, extension, kind in _ZIP_MEMBERS:
            if member in names:
                return media_type, extension, kind
    except (zipfile.BadZipFile, OSError):
        pass
    finally:
        stream.seek(here)
    return "application/zip", ".zip", KIND_ARCHIVE


def sniff(
    source: bytes | bytearray | str | os.PathLike | BinaryIO,
    *,
    deep: bool = False,
) -> FileProbe:
    """按内容识别文件类型。

    Args:
        source: bytes、文件路径，或可 seek 的二进制流。**文件名不参与判定。**
        deep: PDF 额外做一次结构探查（页数 / 加密 / 文本层）。默认关，因为
            它要真解析一遍文件，比看魔数贵得多。

    Returns:
        FileProbe。认不出来时 media_type 是 `application/octet-stream`、
        extension 空串、kind `unknown`——不猜，也不回退到扩展名。
    """
    stream, owned = _as_stream(source)
    try:
        size = _stream_size(stream)
        stream.seek(0)
        head = stream.read(max(_MAX_MAGIC, _TEXT_SAMPLE_BYTES, 58))

        media_type, extension, kind = UNKNOWN_MEDIA_TYPE, "", KIND_UNKNOWN
        for prefix, candidate_type, candidate_ext, candidate_kind in _MAGIC_SORTED:
            if head.startswith(prefix):
                media_type, extension, kind = candidate_type, candidate_ext, candidate_kind
                break
        else:
            if head.startswith(_ZIP_MAGIC):
                media_type, extension, kind = _classify_zip(stream)
            elif _looks_like_text(head):
                media_type, extension, kind = "text/plain", ".txt", KIND_TEXT

        pdf: PdfInfo | None = None
        if deep and kind == KIND_PDF:
            stream.seek(0)
            pdf = _probe_pdf_stream(stream)

        return FileProbe(
            media_type=media_type,
            extension=extension,
            kind=kind,
            size=size,
            pdf=pdf,
        )
    finally:
        if owned:
            stream.close()


# ── PDF 专项 ───────────────────────────────────────────────────────────────

def _pdf_version(head: bytes) -> str:
    if not head.startswith(b"%PDF-"):
        return ""
    raw = head[5:13].split(b"\n")[0].split(b"\r")[0].strip()
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return ""


def _probe_pdf_stream(stream: BinaryIO) -> PdfInfo:
    stream.seek(0)
    version = _pdf_version(stream.read(16))
    stream.seek(0)

    try:
        reader = PdfReader(stream)
    except (PyPdfError, OSError, ValueError):
        return PdfInfo(
            version=version, encrypted=False, page_count=None, has_text_layer=None, damaged=True
        )

    encrypted = bool(reader.is_encrypted)
    if encrypted:
        # 空口令加密很常见（只为限制打印），试一下就能继续读
        try:
            reader.decrypt("")
        except (PyPdfError, NotImplementedError, ValueError):
            return PdfInfo(
                version=version,
                encrypted=True,
                page_count=None,
                has_text_layer=None,
                damaged=False,
            )

    try:
        pages = reader.pages
        page_count = len(pages)
    except (PyPdfError, OSError, ValueError, KeyError):
        return PdfInfo(
            version=version,
            encrypted=encrypted,
            page_count=None,
            has_text_layer=None,
            damaged=True,
        )

    has_text: bool | None = False
    try:
        for page in pages[:_PDF_TEXT_PAGES]:
            if (page.extract_text() or "").strip():
                has_text = True
                break
    except (PyPdfError, OSError, ValueError, KeyError):
        has_text = None

    return PdfInfo(
        version=version,
        encrypted=encrypted,
        page_count=page_count,
        has_text_layer=has_text,
        damaged=False,
    )


def probe_pdf(source: bytes | bytearray | str | os.PathLike | BinaryIO) -> PdfInfo | None:
    """只对 PDF 做结构探查。

    Returns:
        PdfInfo；**内容不是 PDF 时返回 None**（而不是抛异常——"这不是 PDF"
        是个正常答案，调用方多半先 sniff 过了）。
    """
    stream, owned = _as_stream(source)
    try:
        stream.seek(0)
        if not stream.read(5).startswith(b"%PDF-"):
            return None
        return _probe_pdf_stream(stream)
    finally:
        if owned:
            stream.close()


# ── 上传闸门 ───────────────────────────────────────────────────────────────

def check_upload(
    source: bytes | bytearray | str | os.PathLike | BinaryIO,
    *,
    max_bytes: int,
    allowed_media_types: frozenset[str] | set[str] | None = None,
    deep: bool = False,
    reject_damaged: bool = False,
) -> FileProbe:
    """按调用方给的策略校验一个待上传文件。

    策略全部作为参数传进来，本模块不内置任何业务规则。

    Args:
        max_bytes: 大小上限（含）。
        allowed_media_types: 允许的 media_type 集合；None 表示不限。
        deep: 是否顺带做 PDF 结构探查。
        reject_damaged: PDF 结构损坏是否算失败（需要 deep=True 才有判据）。

    Returns:
        通过校验的 FileProbe。

    Raises:
        FileTooLarge / UnsupportedMediaType / CorruptFile
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须为正")

    probe = sniff(source, deep=deep or reject_damaged)
    if probe.size > max_bytes:
        raise FileTooLarge(probe.size, max_bytes)
    if allowed_media_types is not None and probe.media_type not in allowed_media_types:
        raise UnsupportedMediaType(probe.media_type, frozenset(allowed_media_types))
    if reject_damaged and probe.pdf is not None and probe.pdf.damaged:
        raise CorruptFile(probe.media_type, "PDF 结构无法解析")
    return probe
