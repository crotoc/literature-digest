# caps/blobstore

sha256 内容寻址存储。无表、无业务规则，单测不碰文件系统。

## 对外承诺

```python
from caps.blobstore import BlobStore, key_for, digest_bytes

store = BlobStore(backend)          # backend 由装配层注入，见下

r = store.put(data)                 # PutResult(digest, size, deduplicated)
r = store.put(path, expected_digest=claimed)   # 不符抛 DigestMismatch，且不留痕

store.open(digest)                  # -> BinaryIO；没有抛 BlobNotFound
store.exists(digest)                # -> bool
store.stat(digest)                  # -> Blob(digest, size) | None
store.delete(digest)                # -> bool（见下面的警告）
store.verify(digest)                # 重读一遍核对摘要，给巡检任务用

key_for(digest)                     # "sha256/ab/cd/<64 位全摘要>"
```

异常：`InvalidDigest` / `BlobNotFound` / `DigestMismatch`，共同基类 `BlobStoreError`。

### 三条值得知道的性质

1. **同内容只存一份**。重复 `put` 返回 `deduplicated=True` 且不会写第二遍。
   这是 `domain/attachments` 去重所依赖的基础性质。
2. **摘要必须先过校验才拼进 key**，合法摘要只有 64 位十六进制。
   这同时是**路径穿越的防线**——摘要可能来自数据库或客户端，
   不校验就拼路径的话 `../..` 能把后端带出存储根目录。单测里有一组攻击样本盯着。
3. **不要求流可 seek**。`put` 只往前读一遍。（`caps/fileprobe` 相反，它要 seek——
   因为要回头看 zip 中央目录。两者刻意不同，各自 README 都写明了。）

### ⚠️ `delete()` 不查引用计数

这是刻意的：「还有没有附件指着这个 blob」需要查表，而这一层没有表也不该有。
调用方（`domain/attachments`）必须自己先确认没人引用，再调 `delete`。
README 和 docstring 和单测 `test_delete_ignores_references` 三处都写着这句，
是因为这正是容易出事的地方。

## 后端协议

真正的字节读写交给注入进来的 `Backend`。这不是过度设计，而是**依赖方向逼出来的**：
`caps/` 不许 import `adapters/`，所以后端只能以协议形式声明在这里，由装配层注入。

```python
class Backend(Protocol):
    def open_write(self) -> tuple[BinaryIO, str]: ...   # 返回 (可写流, 暂存标识)
    def finalize(self, pending: str, key: str) -> bool: ...  # 原子落位；key 已存在返回 False
    def abort(self, pending: str) -> None: ...          # 幂等
    def open_read(self, key: str) -> BinaryIO: ...      # 不存在抛 KeyError
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int | None: ...
    def delete(self, key: str) -> bool: ...
```

**为什么要"暂存-提交"两段**：摘要只有把流读完才知道，所以开始写的时候还不知道
该用哪个 key，只能先写暂存位置、算完摘要再落位。

- 生产实现：`adapters/storage/local_fs.py`（本地盘），接口留着将来接对象存储。
- 测试替身：`caps/blobstore/testing.py` 的 `MemoryBackend`。**不是生产后端**
  （无持久化、无并发保护、全常驻内存）。放在这里而不是各自 `tests/` 里，是为了让
  attachments / exporting / uploading / fulltext 共用同一个替身，不必各写一个、各写错一次。

## key 布局

```
sha256/ab/cd/abcdef...（64 位全摘要）
```

- 两级扇出：别让单个目录装几百万项。
- 前缀带算法名：将来换算法是**新增**一个前缀，老 blob 一个都不用动。

## 依赖方向

只用标准库（`hashlib` `io` `os` `re` `pathlib` `uuid`）。
**不依赖任何 infra / domain / adapters / features。**

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 引用计数 / 垃圾回收 | `domain/attachments`（有表才知道谁引用了） |
| 文件类型判定、大小上限、魔数校验 | `caps/fileprobe` |
| 文件命名、目录结构（给人看的那种） | `caps/template` + `features/uploading`；本模块的 key 是给机器看的 |
| 加密静态存储 | 需要时在 backend 实现里做，协议不变 |
| 分片上传 / 断点续传 | v1 不做；真要做是 backend 的事 |
| 读时自动校验摘要 | 要再读一遍，多数调用只是想把字节发出去。显式用 `verify()` |
