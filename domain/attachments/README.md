# domain/attachments

一篇文献挂的文件（PDF/补充材料/图片等）的元数据 + blob 引用计数。单表
`attachments`。实际字节存在 `caps/blobstore`（按 sha256 内容寻址）。

## 对外承诺

```python
from domain.attachments import (
    ROLES, DEFAULT_ROLE, AttachmentDTO, AttachmentNotFound,
    create_attachment, get_attachment, set_main_attachment,
    list_attachments_for_work, count_references, delete_attachment,
)

attachment = create_attachment(
    db, library_id=lib.id, work_id=work.id,
    filename="paper.pdf", digest=sha256_hex, size=204800, role="main",
)

list_attachments_for_work(db, work.id)   # main 排最前，其余按时间
set_main_attachment(db, other_attachment.id)   # 顶替当前 main

count_references(db, digest)             # 这个 digest 现在被几条附件引用
orphaned = delete_attachment(db, attachment.id)
if orphaned:
    blob_store.delete(digest)            # 本模块不替你做这一步，见设计要点
```

异常：`AttachmentNotFound`（404）。

## 设计要点

### 为什么本模块不直接调 `caps/blobstore` 删字节

`caps/blobstore/README.md` 明确写着「`delete()` 不查引用计数」——这是故意
的分工：blobstore 没有表，根本没法知道"还有没有人引用这个 digest"；
`domain/attachments` 有表，天然知道。但即便知道，本模块也**不负责实际调用**
`store.delete(digest)`：`BlobStore` 需要装配层注入的 `Backend`，是一个运行
时对象，而不是本模块该持有或构造的东西。所以分工是：`delete_attachment`
删完元数据行之后，查一遍这个 digest 是不是归零了，把这个布尔事实
（"是否变成孤儿"）返回给调用方（`features/uploading`），由它决定要不要真
的去调已经持有的 `BlobStore` 实例删字节。本模块因此完全不 import
`caps.blobstore`，`digest` 在这里只是一个裸字符串。

### 为什么"同一个 digest 可能被多条附件记录引用"是正常情况，不是 bug

`caps/blobstore` 按内容寻址、同内容只存一份——两个用户上传同一份 PDF
（哪怕是给两篇不同的文献，比如一篇论文和它引用的另一篇论文恰好都被收藏
了同一份预印本 PDF）会落到同一个 `digest` 上。`count_references` 统计的是
**全局**引用数，不分文献，这样"是否安全删除底层字节"这个问题才问得对——
只要还有任何一条附件记录（不管挂在哪篇文献上）指着这个 digest，就不能删。

### 为什么"main"是每篇文献唯一的，用自动降级而不是报错实现

`create_attachment(role="main")` 或 `set_main_attachment` 设置新 main 时，
会自动把这篇文献原来的 main（如果有）悄悄降级成 `other`，而不是报
"已经有一个 main 了"的冲突错误。这是因为"换一个新版本 PDF 当默认阅读目标"
是一个常见且无害的操作（旧单体页面梳理①⑥"设为 Main PDF"），用户的意图很
明确——没有必要先报错再要求调用方自己把旧的降级，一步做完更符合直觉，也
不存在"不小心覆盖了什么重要东西"的风险（旧的 main 文件还在，只是不再是
默认打开的那个）。

### 为什么没有单独的"某个 work 当前的 main"查询函数

`list_attachments_for_work` 已经把 main 排在最前面，调用方取 `[0]`（如果
列表非空）就是当前 main，没必要再维护一个专门的 `get_main_attachment`——
那只是对同一份数据的另一种取法，两个函数会有重复的排序逻辑要保持一致。

## 依赖方向

`infra/db`（`Base`、`new_memory_session`）+ `infra/errors`（`NotFound`）。
**不依赖任何其它 domain / adapters / features**——不 import `domain.works`，
也不 import `caps.blobstore`（见上文设计要点）。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 实际的字节上传/下载/物理删除 | `caps/blobstore` + `adapters/storage/local_fs`，由 `features/uploading` 编排 |
| 文件类型判定、大小上限、魔数校验 | `caps/fileprobe` |
| 文件命名规则（main_pdf_template/attachment_template）、重名策略（ask/overwrite/rename） | `caps/template` + `features/uploading` |
| 整目录批量上传的编排（含进度上报） | `features/uploading` + `domain/jobs`；本模块只是把 `rel_path` 原样存下来 |
| 在线查看（内嵌 PDF 阅读器） | `features/pdf_reading` |
| 全文下载（E5）落下来的附件 | 复用本模块的 `create_attachment`，但下载流程本身归 `features/fulltext` |
| 验证 `work_id` 对应的文献确实存在 | 调用方职责（本模块不 import `domain.works`） |
