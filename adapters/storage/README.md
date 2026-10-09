# adapters/storage

`caps/blobstore.Backend` 协议的本地文件系统实现——v1 唯一的落盘后端，接口
留着将来接对象存储（S3 等）。

## 为什么这是 adapter 不是 caps

`caps/blobstore` 本身不许 import `adapters/*`（caps 禁止向上依赖），所以
真正的字节读写必须以协议（`Backend` Protocol）的形式声明在 `caps/blobstore`
里，由实现这个协议的具体后端类通过装配层（`app/`）注入：

```python
from caps.blobstore import BlobStore
from adapters.storage.local_fs import LocalFsBackend

store = BlobStore(LocalFsBackend(root="/var/lib/literature-digest/blobs"))
```

`LocalFsBackend` 可插拔——按三层判定标准（"可插拔、同类多实现、删一个其余
全绿"），这正是 `adapters/` 该待的地方，不是 `caps/`。

## 暂存-提交两段式落盘

`open_write()` 在 `<root>/.tmp/` 下开一个随机命名（`uuid4().hex`）的临时
文件，此时还不知道最终的 key——摘要要等整个流读完才算得出来。调用方
（`caps/blobstore.BlobStore.put()`）把字节写进这个临时文件、算完摘要后调
`finalize(pending, key)`，这里用 `os.replace()`（同一文件系统内是原子操作）
把临时文件挪到最终 key 对应的路径。这保证了"写了一半的文件"永远不会出现在
正式 key 对应的路径上——半成品只存在于 `.tmp/` 里，而 `.tmp/` 里的东西从不
被 `open_read`/`exists`/`size` 看到。

`finalize` 如果发现目标 key 已经存在（同内容之前已经落过位），直接丢弃
临时文件并返回 `False`——这是 `BlobStore` 去重语义依赖的那一步，本模块
配合实现，不自己判断"要不要去重"（那是 `caps/blobstore` 的职责，这里只是
老实报告"这个 key 原来就有没有"）。

## 信任边界：不重复校验 key 的合法性

本模块收到的 `key` 参数永远来自 `caps/blobstore.key_for()`，后者已经在
构造 key 之前调用 `validate_digest()` 校验过摘要是合法的 64 位十六进制——
这同时是路径穿越的防线（非法摘要拼不出 `../..` 这类路径片段）。
`LocalFsBackend` 信任这个前提，不在自己这边重复校验——这和 `domain/folders`
信任调用方已经验证过 `work_id` 存在、不在自己这边重新查表是同一种"信任
调用方已经做过它该做的校验"的分层约定。

## API（即 `Backend` 协议的全部六个方法）

```python
open_write() -> (BinaryIO, pending: str)
finalize(pending: str, key: str) -> bool       # True=真的写入了，False=已存在/去重
abort(pending: str) -> None                    # 幂等
open_read(key: str) -> BinaryIO                # 不存在抛 KeyError
exists(key: str) -> bool
size(key: str) -> int | None
delete(key: str) -> bool                       # True=删掉了，False=本来就不存在
```

## 依赖方向

只用标准库（`os` `uuid` `pathlib`）。不依赖任何 `domain/*`、`features/*`——
符合规则 2（`adapters/` 禁止碰 domain/features）。测试里额外依赖
`caps/blobstore`（跨 caps→adapters 方向在测试里验证协议兼容性是允许的，
生产代码本身不依赖）。

## 本次实现中发现并修正的 CI 脚本 bug

验证本模块时，`scripts/lint.sh` 规则 7（可插拔性检查：删掉任一 adapter/
feature，其余 pytest 必须全绿）出现误报：`features/<x>/` 整个目录被删除时
天然带走它自己的 `tests/`，符合规则原意；但 `adapters/<类>/<x>.py` 只是单个
实现文件，它的测试和同类的其它实现共享 `adapters/<类>/tests/` 目录——规则
7 原来的实现只挪走实现文件本身，于是 `adapters/storage/local_fs.py` 被挪走
后，`adapters/storage/tests/test_local_fs.py` 因为 `ImportError` 直接炸掉，
被误判成"删掉 local_fs.py 导致别处出问题"，而实际上炸的只是它自己名下的
测试，不是"别处"。

修正：规则 7 现在按文件名约定（`adapters/<类>/<x>.py` ↔
`adapters/<类>/tests/test_<x>.py`）把实现文件和它自己的测试一起挪开再跑
`pytest`，不计入"其余"。已用本模块实测验证：修正前 `rule 7` 报
`FAIL`，修正后 `rule 7` 报 `ok`。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 对象存储后端（S3 等） | v1 不需要；协议已经留好了口子，加一个新 `adapters/storage/s3.py` 实现同一个 `Backend` 协议即可，不用动 `caps/blobstore` |
| 并发写同一 pending 的保护 | `pending` 是 uuid4，不会被两个写者撞到同一个临时文件名 |
| 磁盘满/IO 错误的专门处理 | 直接让底层 `OSError` 往上抛，调用方（`caps/blobstore`）目前不需要区分 |
