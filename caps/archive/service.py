"""流式 ZIP 写出。

两个场景共用：`exporting`（RIS/BibTeX + PDF 打成一个 ZIP）和 `extension_dist`
（动态打包扩展成 Chrome 可加载的 ZIP）——两者都要"把若干条目写进一个 ZIP，
边写边流出去，不在内存里攒出整份结果"，所以下沉成一个薄 cap。

## "流式"具体指什么

- 写入方：调用方给一个**可迭代的条目序列**，本模块边读边写，不要求调用方先把
  所有内容读进内存再传进来——附件 ZIP 可能几百 MB。
- 输出方：`write_zip_to` 写到调用方给的任意可写流（文件、`BytesIO`、HTTP 响应体），
  本模块不关心那是什么、最终去哪。

## 路径安全是这里的真实风险点

ZIP 里的条目名**完全由调用方决定**（文件名来自文献标题、作者名……），
如果不做任何约束，`../../etc/passwd` 这种名字会在调用方后续解压时造成路径穿越。
本模块的职责边界很窄——**只保证写进去的条目名干净**，不负责解压那一侧的防护
（那是消费这个 ZIP 的人的事，可能是另一个系统）。但"写的时候就把毒丢掉"
好过"指望以后所有读的人都记得防"。
"""

import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO

DEFAULT_COMPRESSION = zipfile.ZIP_DEFLATED
DEFAULT_COMPRESS_LEVEL = 6
CHUNK_SIZE = 1 << 20

_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f<>:"|?*\\]')


# ── 异常 ───────────────────────────────────────────────────────────────────

class ArchiveError(Exception):
    """本模块所有异常的基类。"""


class UnsafeEntryName(ArchiveError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"条目名不安全：{name!r}（{reason}）")
        self.name = name
        self.reason = reason


class DuplicateEntryName(ArchiveError):
    def __init__(self, name: str) -> None:
        super().__init__(f"条目名重复：{name!r}")
        self.name = name


# ── 条目 ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ZipEntry:
    """一个待写入的条目。

    `content` 是 bytes、可读二进制流，或产出 bytes 块的可迭代对象——
    三种形态覆盖"已经在内存里""从 blobstore 打开的流""从别处边读边转发"。
    """

    name: str
    content: bytes | BinaryIO | Iterable[bytes]
    mtime: datetime | None = None
    """条目的修改时间；None 用写入时刻。"""


def entry(
    name: str,
    content: bytes | BinaryIO | Iterable[bytes],
    *,
    mtime: datetime | None = None,
) -> ZipEntry:
    """构造一个 `ZipEntry` 的便捷写法。"""
    return ZipEntry(name=name, content=content, mtime=mtime)


# ── 条目名校验 ─────────────────────────────────────────────────────────────

def sanitize_entry_name(name: str) -> str:
    """把条目名规范成安全的 ZIP 内部路径。

    规则：
      - 统一用 `/` 分隔（ZIP 规范本身要求，Windows 的 `\\` 必须转）
      - 去掉开头的 `/`（ZIP 里没有绝对路径这个概念，留着只会让某些解压工具困惑）
      - 拒绝 `..` 路径段——**这是路径穿越的防线**，调用方传什么名字都不该
        让解压时跳出目标目录
      - 拒绝控制字符和 Windows 保留字符（`<>:"|?*`）

    Raises:
        UnsafeEntryName
    """
    if not isinstance(name, str) or not name:
        raise UnsafeEntryName(repr(name), "空名字")

    normalized = name.replace("\\", "/")
    segments = [seg for seg in normalized.split("/") if seg != ""]
    if not segments:
        raise UnsafeEntryName(name, "规范化后为空")

    for segment in segments:
        if segment == "..":
            raise UnsafeEntryName(name, "包含 .. 路径段")
        if _UNSAFE_NAME_CHARS.search(segment):
            raise UnsafeEntryName(name, "包含控制字符或 Windows 保留字符")
        if segment.strip() != segment:
            raise UnsafeEntryName(name, "路径段首尾有空白")

    return "/".join(segments)


# ── 写出 ───────────────────────────────────────────────────────────────────

def _iter_bytes(content: bytes | bytearray | BinaryIO | Iterable[bytes]) -> Iterator[bytes]:
    if isinstance(content, bytes | bytearray):
        yield bytes(content)
        return
    if hasattr(content, "read"):
        while chunk := content.read(CHUNK_SIZE):
            yield chunk
        return
    if isinstance(content, Iterable):
        yield from content
        return
    raise TypeError(f"不支持的内容类型：{type(content).__name__}")


def write_zip_to(
    destination: BinaryIO,
    entries: Iterable[ZipEntry],
    *,
    compression: int = DEFAULT_COMPRESSION,
    compresslevel: int | None = DEFAULT_COMPRESS_LEVEL,
    on_duplicate: str = "raise",
) -> int:
    """把一批条目流式写进 ZIP，写到 `destination`。

    Args:
        destination: 任意可写二进制流（文件、`BytesIO`、HTTP 响应体）。
            本模块不关心它最终去哪，也不会 close 它。
        entries: 条目序列，**可以是生成器**——调用方不需要先凑出完整列表。
        on_duplicate:
            - `"raise"`（默认）：撞名抛 `DuplicateEntryName`
            - `"rename"`：自动加 `(2)` `(3)`… 后缀

    Returns:
        实际写入的条目数。

    Raises:
        UnsafeEntryName / DuplicateEntryName
    """
    if on_duplicate not in {"raise", "rename"}:
        raise ValueError('on_duplicate 只能是 "raise" 或 "rename"')

    used_names: set[str] = set()
    written = 0
    with zipfile.ZipFile(destination, mode="w", compression=compression) as archive:
        for item in entries:
            safe_name = sanitize_entry_name(item.name)
            if safe_name in used_names:
                if on_duplicate == "raise":
                    raise DuplicateEntryName(item.name)
                safe_name = _dedupe(safe_name, used_names)
            used_names.add(safe_name)

            when = item.mtime or datetime.now(UTC)
            info = zipfile.ZipInfo(safe_name, date_time=when.timetuple()[:6])
            info.compress_type = compression

            with archive.open(info, mode="w") as handle:
                for chunk in _iter_bytes(item.content):
                    handle.write(chunk)
            written += 1
    return written


def _dedupe(name: str, used: set[str]) -> str:
    if "." in name.rsplit("/", 1)[-1]:
        head, _, ext = name.rpartition(".")
        template = f"{head} ({{}}).{ext}"
    else:
        template = f"{name} ({{}})"
    counter = 2
    candidate = template.format(counter)
    while candidate in used:
        counter += 1
        candidate = template.format(counter)
    return candidate


def iter_zip_entries(source: bytes | BinaryIO) -> Iterator[tuple[str, int]]:
    """列出一个 ZIP 里的条目名与大小，不解压内容。

    给 `extension_dist` 或测试核对"打出来的包里有什么"用。
    """
    stream = io.BytesIO(source) if isinstance(source, bytes | bytearray) else source
    with zipfile.ZipFile(stream) as archive:
        for info in archive.infolist():
            if not info.is_dir():
                yield info.filename, info.file_size
