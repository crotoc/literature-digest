"""内存后端——**只供测试**。

它不是生产后端：没有持久化、没有并发保护、全部内容常驻内存。
生产后端在 `adapters/storage/`（本地盘、对象存储……）。

放在这里而不是各自的 tests/ 里，是为了让所有用到 blobstore 的模块
（attachments / exporting / uploading / fulltext）共用同一个测试替身，
不必各写一个、各写错一次。
"""

import io
import uuid


class _CapturingWriter(io.BytesIO):
    """close() 时把内容交回 backend，而不是丢掉。"""

    def __init__(self, backend: "MemoryBackend", pending: str) -> None:
        super().__init__()
        self._backend = backend
        self._pending = pending

    def close(self) -> None:
        if not self.closed:
            self._backend._pending[self._pending] = self.getvalue()
        super().close()


class MemoryBackend:
    """`Backend` 协议的内存实现。"""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self._pending: dict[str, bytes] = {}
        self.finalize_calls = 0
        self.write_calls = 0

    def open_write(self) -> tuple[io.BytesIO, str]:
        self.write_calls += 1
        pending = uuid.uuid4().hex
        self._pending[pending] = b""
        return _CapturingWriter(self, pending), pending

    def finalize(self, pending: str, key: str) -> bool:
        self.finalize_calls += 1
        payload = self._pending.pop(pending, b"")
        if key in self.blobs:
            return False
        self.blobs[key] = payload
        return True

    def abort(self, pending: str) -> None:
        self._pending.pop(pending, None)

    def open_read(self, key: str) -> io.BytesIO:
        if key not in self.blobs:
            raise KeyError(key)
        return io.BytesIO(self.blobs[key])

    def exists(self, key: str) -> bool:
        return key in self.blobs

    def size(self, key: str) -> int | None:
        payload = self.blobs.get(key)
        return None if payload is None else len(payload)

    def delete(self, key: str) -> bool:
        return self.blobs.pop(key, None) is not None

    # 测试辅助，不属于 Backend 协议
    @property
    def pending_count(self) -> int:
        return len(self._pending)

