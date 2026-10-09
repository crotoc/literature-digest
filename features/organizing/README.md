# features/organizing

对一批文献做批量操作：打标签/去标签（含顺手新建标签）、加入/移出文件夹、
移入回收站、恢复、彻底删除。本模块不开表。

## 对外承诺

```python
from features.organizing import (
    BatchOutcome, BatchResult, WorkNotInTrash,
    bulk_add_tag, bulk_remove_tag, create_tag_and_apply,
    bulk_add_to_folder, bulk_remove_from_folder,
    bulk_soft_delete, bulk_restore, purge_works,
)

result = bulk_add_tag(
    db, account_id=account.id, library_id=lib.id, work_ids=[1, 2, 3], tag_id=tag.id,
)
# result.job.status / result.job.counts / result.outcomes

tag, result = create_tag_and_apply(
    db, account_id=account.id, library_id=lib.id, work_ids=[1, 2, 3], name="  Deep Learning  ",
)

purge_works(db, account_id=account.id, library_id=lib.id, work_ids=[1], blob_store=blob_store)
```

异常：`WorkNotInTrash`（409，`purge_works` 对还没移入回收站的文献抛）。
work_id 跨库误用抛裸 `ValueError`，和 `uploading`/`metadata_lookup`/
`domain.tags`/`domain.folders` 同一个约定。

## 为什么七种操作共用一个 `_run_batch` 辅助函数

七个批量操作（加标签/去标签/加文件夹/去文件夹/软删/恢复/彻底删除）在
"父 job 一行 + counts_json，只有失败项才建子行"这条瞬时批量规则上完全
一样，区别只在每一项具体做什么。这不是跨模块抽象（跨 feature 互相
import 才是规则4 禁止的那种），而是同一个模块内七个兄弟函数的共用骨架，
抽出来避免同一段 job 编排代码写七遍、七份各自可能悄悄跑偏。

## 为什么要在每个批量操作里重新校验 `work_id` 属于这个库

`domain.tags.add_tag_to_work`/`domain.folders.add_work_to_folder` 只校验
`tag_id`/`folder_id` 属于这个库，**不校验 `work_id`**——这两个 domain 模块
刻意不 import `domain.works`，校验 work_id 存在性和归属从来不是它们的
知识（和 `domain.attachments` 同一个理由，见它 README 的"验证 work_id
对应的文献确实存在——调用方职责"）。`domain.works.soft_delete_work`/
`restore_work` 甚至直接认任意存在的 `work_id`，完全不检查 `library_id`。
所以本模块对每一项都要先调 `get_work` 确认存在且属于这个库，这是本模块
作为"认识所有相关 domain"的编排层必须补上的那一步，不是重复劳动。

## 两处"达成目标状态就算成功"的判断（没有照搬域层的异常语义）

`domain.tags.remove_tag_from_work`/`domain.folders.remove_work_from_folder`
对"本来就没这个关联"的情况分别抛 `WorkTagNotFound`/`WorkFolderNotFound`——
这在单条操作里是合理的（调用方明确知道自己要删哪一条）。但批量去标签/
批量移出文件夹的典型场景恰恰是**混合选区**（三态勾选框的"部分"态：
选中的 50 篇里只有 30 篇真的打了这个标签），这时候"没打过的那 20 篇"
不是出错，是从一开始就已经处在目标状态（"没有这个标签"）。所以
`bulk_remove_tag`/`bulk_remove_from_folder` 在各自的 `apply_one` 里
`except WorkTagNotFound/WorkFolderNotFound: pass`，不算进失败计数，也
不建失败子 job。这是故意偏离域层异常语义的一处设计决定，值得在这里写
清楚，不然容易被当成"偷懒吞异常"。

反过来，`domain.works.restore_work`/`soft_delete_work` 本身已经是幂等的
（见各自 docstring），不需要本模块额外吞任何异常。

## `create_tag_and_apply` 里 `caps.slug.normalize` 的分工边界

`domain.tags.create_tag` 的同名判断是**大小写敏感的精确字符串匹配**（它
自己文档写明），故意不做任何归一化——这是域层的决定，不是漏做。但"顺手
新建标签再打上"这个 UI 流程的真实输入是用户现场敲的文本，容易带上全角
字符、首尾空格这类用户自己都看不出来的差异，拿着这样的字符串去精确匹配
只会制造一堆肉眼看着一样、实际是两个标签的脏数据。所以本模块在调
`create_tag` 之前先过一遍 `caps.slug.normalize`（NFKC + 折叠空白 + 去
首尾）——这一步不碰大小写，`create_tag` 的大小写敏感语义原样保留，只是
清理掉用户无意引入的 Unicode/空白噪声。

## `purge_works` 的级联编排

`domain.works.purge_work` 自己的文档写明"不级联 tags/folders/notes/
attachments……跨 domain 的级联顺序由 `features/organizing` 编排，它会
依次调各个 domain 自己的 purge 函数"——本模块的 `_purge_cascade` 就是
那段编排：依次清 `work_tags`/`work_folders`/`work_notes`/`attachments`
（连带回收孤儿 blob，复用和 `features/uploading.remove_attachment` 完全
同构的"先判断是否变孤儿、再决定要不要删 blob"逻辑——两个 feature 禁止
互相 import，这段十行逻辑各自留一份，和 `_naming_values`/
`_fill_missing_fields` 是同一类必要重复），最后才删 `works` 行本身。

### 为什么彻底删除前必须先移入回收站（`WorkNotInTrash`）

彻底删除是真正不可逆的操作（级联删掉 4 张关联表 + 回收 blob）。如果允许
直接对任意文献调用 `purge_works`，"回收站"这个安全网就形同虚设——用户
一次误操作就能跳过所有缓冲直接丢数据。所以 `purge_works` 强制要求
`work.deleted_at is not None`，也就是"必须先软删过"，才允许彻底删除。
这是本模块主动加的一道防护，不是哪个 domain 的约束（`domain.works.
purge_work` 本身对任何 `work_id` 都会执行，不检查 `deleted_at`）。

## 依赖方向

依赖 `caps/slug` + `domain/{tags,folders,notes,attachments,works,jobs}`
+ `infra/errors`。不依赖任何其它 `features/*`（符合规则4）。不依赖
`caps/blobstore` 本身——同 `features/uploading`，`BlobStore` 实例由
调用方注入，本模块只调用它的方法。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| "全选所有筛选结果"展开成 `work_ids` 列表 | 归 `features/library_browse.resolve_selection`（计划"两处已定"第2条），本模块只认裸 id 列表，不认识筛选条件 |
| 批量导出（RIS/BibTeX/ZIP） | 归 `features/exporting`——同样是"瞬时批量"但组合的 caps/domain 完全不同，不值得为了共享 `_run_batch` 这几十行就跨 feature 耦合 |
| AI 自动打标签/AI 重复标签合并 | E2 `tag_grouping`/`ai_enrichment`，v1 范围之外 |
| 文件夹的建/改名/嵌套/删除本身 | 归 `domain.folders` 自己（单条 CRUD，不是批量编排），本模块只消费它已有的函数 |
| 标签池侧栏拖拽排序 | 归 `domain.tags.reorder_tags`，不是批量操作 |
