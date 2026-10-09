"""`caps/blobstore.Backend` 协议的本地文件系统实现。

`caps/blobstore` 不许 import 任何 `adapters/*`（caps 禁止向上依赖），所以
`BlobStore` 只接受一个满足 `Backend` 协议（鸭子类型，不是继承）的对象，由
装配层（`app/`）在启动时注入：`BlobStore(LocalFsBackend(root=...))`。

落盘是"暂存 → 原子提交"两段式，原因是摘要要等整个流读完才能算出来，写的
时候还不知道最终该落在哪个 key 下：`open_write()` 先在 `<root>/.tmp/` 下开
一个随机命名的临时文件，调用方把字节写进去、算完摘要后调 `finalize()`，
这里用 `os.replace()`（同一文件系统内是原子操作）把临时文件挪到最终 key
对应的路径——这保证了"写了一半的文件"不会出现在正式 key 对应的路径上。

本模块信任调用方（`caps/blobstore`）传入的 `key` 已经过摘要格式校验——
`key_for()` 只会产出 `sha256/<2 位>/<2 位>/<64 位十六进制>` 这种形状的字符串，
路径穿越的防线在 `caps/blobstore.validate_digest()` 那一侧，不在这里重复做。
这和 `domain/folders` 信任调用方已经验证过 `work_id` 存在、不在自己这边
重新校验是同一种"信任调用方已经做过它该做的校验"的分层约定。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import BinaryIO


class LocalFsBackend:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._tmp_dir = self._root / ".tmp"
        self._root.mkdir(parents=True, exist_ok=True)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    def open_write(self) -> tuple[BinaryIO, str]:
        pending = uuid.uuid4().hex
        handle = open(self._tmp_dir / pending, "wb")  # noqa: SIM115 — 跨方法持有，不能用 with
        return handle, pending

    def finalize(self, pending: str, key: str) -> bool:
        src = self._tmp_dir / pending
        dest = self._root / key
        if dest.exists():
            # 同内容已经落过位——这是 BlobStore 去重语义依赖的那一步：调用方
            # 按同一个摘要算出同一个 key，先来的那次已经把字节放好了。
            src.unlink(missing_ok=True)
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
        return True

    def abort(self, pending: str) -> None:
        (self._tmp_dir / pending).unlink(missing_ok=True)

    def open_read(self, key: str) -> BinaryIO:
        path = self._root / key
        try:
            return open(path, "rb")  # noqa: SIM115 — 返回给调用方持有，不能用 with
        except FileNotFoundError:
            raise KeyError(key) from None

    def exists(self, key: str) -> bool:
        return (self._root / key).is_file()

    def size(self, key: str) -> int | None:
        try:
            return (self._root / key).stat().st_size
        except FileNotFoundError:
            return None

    def delete(self, key: str) -> bool:
        try:
            (self._root / key).unlink()
            return True
        except FileNotFoundError:
            return False
