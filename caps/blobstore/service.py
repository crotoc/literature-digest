"""sha256 内容寻址存储。

职责边界（这一条是本模块存在的理由，别越过去）：
  - **本模块管"内容 ↔ 地址"**：算摘要、定 key 布局、保证同内容只存一份、
    保证 key 合法。
  - **不管"谁引用了"**。`delete()` 就是删，不查引用计数——"这个 blob 还有没有
    附件指着它"是 `domain/attachments` 的知识（它有表），不是这一层能知道的。
  - **不管字节落在哪**。真正的读写交给一个 `Backend`（协议见下），
    生产实现在 `adapters/storage/`。依赖方向决定了只能这样：caps 不许 import
    adapters，所以后端只能以协议的形式声明在这里、由装配层注入进来。

key 布局：`sha256/ab/cd/<64 位全摘要>`。
  - 两级扇出是为了别让单个目录装几百万项。
  - 前缀带算法名，将来换算法是**新增**一个前缀，老 blob 不用动。
"""

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

ALGORITHM = "sha256"
CHUNK_SIZE = 1 << 20

_DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")


# ── 对外 DTO ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Blob:
    """一份已存内容的身份。"""

    digest: str
    size: int

    @property
    def key(self) -> str:
        return key_for(self.digest)


@dataclass(frozen=True)
class PutResult:
    """一次 put 的结果。"""

    blob: Blob
    deduplicated: bool
    """内容此前已存在，这次没有真的写入。"""

    @property
    def digest(self) -> str:
        return self.blob.digest

    @property
    def size(self) -> int:
        return self.blob.size


class BlobStoreError(Exception):
    """本模块所有异常的基类。"""


class InvalidDigest(BlobStoreError):
    def __init__(self, value: str) -> None:
        super().__init__(f"不是合法的 {ALGORITHM} 摘要（要 64 位小写十六进制）：{value!r}")
        self.value = value


class BlobNotFound(BlobStoreError):
    def __init__(self, digest: str) -> None:
        super().__init__(f"没有这份内容：{digest}")
        self.digest = digest


class DigestMismatch(BlobStoreError):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"内容摘要不符：声明 {expected}，实际 {actual}")
        self.expected = expected
        self.actual = actual


# ── 后端协议 ───────────────────────────────────────────────────────────────

@runtime_checkable
class Backend(Protocol):
    """字节落地的后端。只做键值读写，**不认识摘要、不认识内容寻址**。

    实现放 `adapters/storage/`（本地盘、对象存储……）。
    `caps/blobstore/testing.py` 里有一个内存实现，**只供测试**。

    `open_write` / `finalize` / `abort` 这套暂存-提交是必须的：摘要只有把流读完
    才知道，所以写入时还不知道该用哪个 key，只能先写暂存位置再落位。
    """

    def open_write(self) -> tuple[BinaryIO, str]:
        """开一个暂存写入位置。返回 (可写二进制流, 暂存标识)。"""
        ...

    def finalize(self, pending: str, key: str) -> bool:
        """把暂存内容落到 key。

        Returns:
            True 表示真的写入了；False 表示 key 已存在、暂存内容已被丢弃。

        实现必须是**原子**的（别让别人看到半个文件）。
        """
        ...

    def abort(self, pending: str) -> None:
        """丢弃暂存内容。必须幂等。"""
        ...

    def open_read(self, key: str) -> BinaryIO:
        """按 key 打开读流。不存在时抛 `KeyError`。"""
        ...

    def exists(self, key: str) -> bool: ...

    def size(self, key: str) -> int | None:
        """字节数；不存在时 None。"""
        ...

    def delete(self, key: str) -> bool:
        """删除。Returns: True 删掉了，False 本来就不存在。"""
        ...


# ── key / 摘要 ─────────────────────────────────────────────────────────────

def validate_digest(digest: str) -> str:
    """校验并归一摘要。

    这一条同时是**路径穿越的防线**：摘要可能来自数据库或客户端，
    不校验就直接拼进路径的话，`../..` 这种值能把后端带出存储根目录。
    合法摘要只有 64 位十六进制，拼不出任何路径片段。
    """
    if not isinstance(digest, str):
        raise InvalidDigest(repr(digest))
    normalized = digest.strip().lower()
    if not _DIGEST_RE.match(normalized):
        raise InvalidDigest(digest)
    return normalized


def key_for(digest: str) -> str:
    """摘要 → 后端 key。"""
    valid = validate_digest(digest)
    return f"{ALGORITHM}/{valid[:2]}/{valid[2:4]}/{valid}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_source(source: bytes | bytearray | str | os.PathLike | BinaryIO) -> Iterator[bytes]:
    """把三种入口归一成字节块迭代器。

    和 `caps/fileprobe` 不同，这里**不要求流可 seek**——只需要往前读一遍。
    """
    if isinstance(source, bytes | bytearray):
        data = bytes(source)
        for offset in range(0, len(data), CHUNK_SIZE):
            yield data[offset : offset + CHUNK_SIZE]
        return
    if isinstance(source, str | os.PathLike):
        with Path(source).open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                yield chunk
        return
    if not hasattr(source, "read"):
        raise TypeError("source 必须是 bytes、路径，或可读的二进制流")
    while chunk := source.read(CHUNK_SIZE):
        yield chunk


# ── 存储 ───────────────────────────────────────────────────────────────────

class BlobStore:
    """内容寻址存储。所有真实 IO 走注入进来的 backend。"""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    def put(
        self,
        source: bytes | bytearray | str | os.PathLike | BinaryIO,
        *,
        expected_digest: str | None = None,
    ) -> PutResult:
        """存一份内容。同内容重复 put 不会写第二遍。

        Args:
            source: bytes、路径，或可读的二进制流（**不要求可 seek**）。
            expected_digest: 调用方声明的摘要。给了就校验，不符抛 `DigestMismatch`
                并且不留下任何东西。扩展上传的那条路径要用它——那边的字节
                过了一趟别人的手。

        Returns:
            PutResult，`deduplicated` 说明这次是不是白跑。
        """
        if expected_digest is not None:
            expected_digest = validate_digest(expected_digest)

        hasher = hashlib.sha256()
        size = 0
        stream, pending = self._backend.open_write()
        try:
            with stream:
                for chunk in _iter_source(source):
                    hasher.update(chunk)
                    size += len(chunk)
                    stream.write(chunk)
            digest = hasher.hexdigest()
            if expected_digest is not None and digest != expected_digest:
                raise DigestMismatch(expected_digest, digest)
            written = self._backend.finalize(pending, key_for(digest))
        except BaseException:
            self._backend.abort(pending)
            raise
        return PutResult(blob=Blob(digest=digest, size=size), deduplicated=not written)

    def open(self, digest: str) -> BinaryIO:
        """按摘要打开读流。**不顺手校验内容**——那要再读一遍，调用方多数时候
        只是想把字节发出去。要校验用 `verify()`。

        Raises:
            BlobNotFound / InvalidDigest
        """
        key = key_for(digest)
        try:
            return self._backend.open_read(key)
        except KeyError as error:
            raise BlobNotFound(validate_digest(digest)) from error

    def exists(self, digest: str) -> bool:
        return self._backend.exists(key_for(digest))

    def stat(self, digest: str) -> Blob | None:
        size = self._backend.size(key_for(digest))
        if size is None:
            return None
        return Blob(digest=validate_digest(digest), size=size)

    def delete(self, digest: str) -> bool:
        """删掉一份内容。

        **不查引用计数。** 调用方必须先确认没有别的记录指着它——
        那个判断需要表，不在这一层。

        Returns:
            True 删掉了，False 本来就不存在。
        """
        return self._backend.delete(key_for(digest))

    def verify(self, digest: str) -> bool:
        """重新读一遍，确认内容摘要仍然对得上（给巡检任务用）。

        Raises:
            BlobNotFound / InvalidDigest
        """
        expected = validate_digest(digest)
        hasher = hashlib.sha256()
        with self.open(expected) as stream:
            while chunk := stream.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest() == expected
