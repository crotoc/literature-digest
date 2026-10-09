# features/uploading

接收上传的字节：校验（`caps/fileprobe`）+ 内容寻址存储（`caps/blobstore`）+
挂到一篇文献名下（`domain/attachments`），外加重名策略（ask/overwrite/rename）
的编排和"整目录批量上传"的瞬时批量 job 追踪（`domain/jobs`）。本模块不开表。

## 对外承诺

```python
from features.uploading import (
    ON_CONFLICT_POLICIES, DEFAULT_ON_CONFLICT, DEFAULT_MAX_BYTES,
    UploadInput, UploadOutcome, BatchUploadResult,
    UploadRejected, FilenameConflict,
    upload_file, upload_batch, download_attachment, remove_attachment,
    resolve_naming_template, set_naming_template,
)

attachment = upload_file(
    db, library_id=lib.id, work_id=work.id, filename="paper.pdf",
    content=file_bytes_or_stream, blob_store=blob_store, on_conflict="rename",
)

result = upload_batch(
    db, account_id=account.id, library_id=lib.id, work_id=work.id,
    files=[UploadInput(filename="a.pdf", content=..., rel_path="subdir/a.pdf")],
    blob_store=blob_store,
)
```

异常：`UploadRejected`（422，统一包装 `fileprobe` 的三种拒绝原因）、
`FilenameConflict`（409，仅 `on_conflict="ask"` 时抛）。跨库误用
（`work.library_id != library_id`）抛裸 `ValueError`，和
`domain.tags`/`domain.folders`/`library_browse`/`metadata_lookup` 同一个
约定。

## 为什么依赖 `domain/works`，尽管计划表里只写了 `domain/{attachments,jobs}`

`domain/attachments/README.md` 的刻意裁剪范围里写着"验证 `work_id` 对应的
文献确实存在——调用方职责（本模块不 import `domain.works`）"，本模块就是
那个调用方，所以补上这一步校验。这是防御性的边界校验，不是访问控制——
访问控制仍然是调用方 `app/pages` 在路由层做的事，和 `features/exporting`/
`features/metadata_lookup` 同一个套路。重命名模板还需要用到文献的
标题/年份/第一作者，这进一步要求本模块认识 `WorkDTO`。

## `_naming_values` 和 `features/exporting` 里同名函数重复

和 `metadata_lookup`/`importing` 的 `_fill_missing_fields` 是同一类重复：
两个 feature 禁止互相 import（规则 4），"从 `WorkDTO` 提取
firstauthor/year/title 三个命名用字段"这段逻辑在两个模块里各留一份——
它不是哪个 domain 的不变量，只是"按模板命名文件"这个编排动作里的一步，
不值得为它单独开一个 caps/domain 模块。

## 两个真实想清楚才落地的编排细节

### 1. `on_conflict="overwrite"` 时必须先建新行再删旧行

内容寻址下有一个容易踩的顺序坑：如果新旧文件内容恰好完全相同（同一个
digest），先删旧附件行再建新附件行的话，`count_references` 在两行之间的
瞬间会看到这个 digest 的引用数为零（旧行已删、新行还没建），被误判为孤儿
而删掉共享的 blob——但新行马上就要引用它。所以 `upload_file` 的顺序固定是
`blob_store.put()` → `create_attachment()`（新行落地）→ 只有这之后才
`delete_attachment()`（旧行）→ 只有返回值显示"确实变孤儿了"才
`blob_store.delete()`。`test_upload_file_on_conflict_overwrite_keeps_blob_if_identical_content`
专门测的就是这条顺序。

### 2. `caps/fileprobe` 和 `caps/blobstore` 对同一个 stream 的要求不一样，衔接处要手动桥

`check_upload` 要求可寻址（seekable）的流，且用后不保证把位置重置回 0；
`blob_store.put` 不要求可寻址，但要从真正的开头完整读一遍内容。两个 cap
各自的文档都如实写了这条差异，但谁都没有替对方兜底——因为兜底需要同时
知道"用的是同一个 stream 对象"这个事实，这是只有组合它们的 feature 层才
知道的信息。所以 `upload_file` 在 `check_upload` 和 `blob_store.put` 之间
插了一次 `_reset(content)`（仅当 `content` 有 `.seek` 时才重置，纯 `bytes`
不需要）。

## "整目录批量上传"是瞬时批量，不是长流程批量

按计划里"两处已定"的子 job 粒度规则：

- **长流程批量**（全文下载/AI 评估/导入）每项都有自己的多步状态机、
  自己的失败原因、可能停下来等人——每项值得单独留一行。
- **瞬时批量**（打标签/去标签/加移文件夹/删除/导出）父 job 一行 +
  `counts_json`，只有失败项才建子行，成功不留痕。

批量上传离哪一类更近？`upload_batch` 对每个文件只做"校验 → 存 → 建附件
行"三步，没有多阶段状态机，也不会停下来等人介入。和"导入"不一样——导入
对每一项有 created/merged/flagged_duplicate/failed 四种业务含义不同的
结果，值得分别留痕；批量上传的成功结果只有一种："这个文件成了一条附件"，
没有值得单独记录的业务分支，只有失败原因（哪个文件为什么被拒）值得单独
看。所以归为**瞬时批量**：`upload_batch` 建一个父 job（`counts_json`
记 total/created/failed），只有失败的文件才建子 job 记原因。

## 账号级命名模板设置

`resolve_naming_template`/`set_naming_template` 走 `domain/settings` 的
`(module="uploading", key="attachment_filename_template")`，不是业务表
字段——和规避清单第 7 条一致（旧代码把这类设置塞进 `workspace.*_json` 列）。
模板语法由 `set_naming_template` 在写入前用
`caps.template.validate_bracket_template(allowed_keys=("firstauthor","year","title"))`
校验，拒绝未知占位符而不是等渲染时静默吞掉。

## 依赖方向

依赖 `caps/{fileprobe,template}` + `domain/{attachments,jobs,settings,works}`
+ `infra/errors`。不依赖任何其它 `features/*`（符合规则 4）。不依赖
`caps/blobstore` 本身——`BlobStore` 实例由调用方（装配层）持有并注入，本
模块只调用它的方法，不构造它、不认识它的 `Backend` 实现。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| `rel_path` 路径穿越防护 | `caps/fileprobe/README.md` 明确把这条归给本模块（"目录上传时才有 `rel_path` 这个概念"），但 v1 只是把 `rel_path` 原样存进 `domain.attachments`，不做展示层以外的文件系统操作，暂无实际穿越风险面；真要把 `rel_path` 落到物理目录结构时需要补这一步 |
| 断点续传/分片上传 | v1 没有这个真实需求，附件以完整字节一次性 `put` |
| 病毒扫描 | `caps/fileprobe/README.md` 同样明确裁掉，不在范围内 |
| 上传配额/限流 | 归 `domain/usage`（E5 及之后），本模块不感知账号配额 |
| `on_conflict="ask"` 的"等用户选"交互本身 | 本模块只负责在冲突时抛 `FilenameConflict`，由调用方（页面层）捕获后向用户提供选择，再带着用户的决定重新调用 |
