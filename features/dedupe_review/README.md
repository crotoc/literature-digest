# features/dedupe_review

疑似重复文献的复核：列出候选对、批量忽略、合并（把 `merge_work_id` 的标签/文件夹/笔记/标识符/附件搬到
`keep_work_id` 上，再把 `merge_work_id` 彻底删除）。

## 对外表面

- `list_pending_candidates(db, *, library_id) -> list[CandidatePairDTO]`
- `bulk_dismiss_candidates(db, *, account_id, library_id, candidate_ids) -> BatchResult`
- `merge_works(db, *, library_id, keep_work_id, merge_work_id) -> WorkDTO`

## 依赖方向

`domain/{works, tags, folders, notes, attachments, jobs}`。**没有 `caps/` 依赖**——
下面"附件迁移为什么不需要 blobstore"一节解释了为什么这不是疏漏，是这个模块的设计结果，
正好对上架构计划模块表里 `dedupe_review` 那一行不列 `caps/` 的事实。

## `merge_works` 为什么不收 `candidate_id` 参数

设计 `merge_works(db, *, library_id, keep_work_id, merge_work_id)` 时，最初想法是让调用方
顺手传一个 `candidate_id`，合并后把那一条候选也标记掉。读过 `domain.works.purge_work` 的
docstring 后发现这是多余的：`merge_work_id` 最终会被 `purge_work` 整体清掉，而 `purge_work`
自己负责清理 `duplicate_candidates` 表里**所有**引用这个 work_id 的行（不管是作为
`work_id` 还是 `candidate_work_id`）。所以：

- 这次合并对应的那条候选行，会随 `merge_work_id` 被 purge 自然清掉，不需要 `merge_works`
  自己再调 `resolve_duplicate_candidate`。
- `merge_work_id` 可能同时出现在别的候选对里（比如它和第三篇也被怀疑重复），这些行同样
  失去意义，同样该消失——如果让 `merge_works` 自己去找"哪些候选行该标记掉"，还得重新发明
  一遍 `purge_work` 已经做的事。

因此 `merge_works` 不需要知道候选表的任何细节，`candidate_id` 这个参数也就没有存在的
必要。测试 `test_merge_works_clears_dangling_duplicate_candidates_about_merge_work` 验证了
这个级联效果。

## 为什么没有 `caps/` 依赖：附件迁移永远不会触发 blob 删除

合并时，`merge_work_id` 名下的每个附件要搬到 `keep_work_id` 上。参考
`features/uploading` 已经用过的"先建新行再删旧行"这个顺序：

1. 在 `keep_work_id` 上用相同的 `digest` 建一条新的 attachment 行（role 统一降级为
   `"other"`，不抢占 `keep_work_id` 已有的 `main`）。
2. 再删除 `merge_work_id` 上的旧 attachment 行。

`domain.attachments.delete_attachment` 自带孤儿检查：删除一行之后，如果同一个 `digest`
在库里已经没有任何行引用了，才需要调用方去删对应的 blob。因为第 1 步已经先把新行建好，
第 2 步删除旧行时，这个 `digest` **永远**还有新行在引用——也就是说这条孤儿检查**永远**
返回"没有孤儿"。于是 `merge_works` 自始至终不需要调用任何 blob 删除方法，`caps/blobstore`
这个依赖自然就不存在，不是故意省掉、也不是漏掉。

## 标签/文件夹迁移：天然幂等

`domain.tags.add_tag_to_work` / `domain.folders.add_work_to_folder` 本身对"已经加过"是
幂等的（不报错），所以 `_migrate_tags` / `_migrate_folders` 直接把 `merge_work_id` 的每个
标签/文件夹都往 `keep_work_id` 上加一遍，不需要先判断 `keep_work_id` 是否已经有了——
包括两边有重叠标签的情况，重复加一次不会出错也不会产生重复行。

## 笔记迁移：有冲突时拼接，不是覆盖

- 只有 `merge_work_id` 有笔记、`keep_work_id` 没有 → 直接把内容搬过去。
- 只有 `keep_work_id` 有笔记 → 不动。
- 两边都有 → 拼接成 `f"{keep.content}\n\n---\n\n{merge.content}"`，两边都保留。

"合并"这个动作本身承诺信息不会因为选了哪一条而丢失；笔记是自由文本，没有字段级的
取舍规则可用，只能拼接，由人事后自己整理。

## 标识符迁移：必须先删后加

`(library_id, scheme, value_norm)` 在库内全局唯一。读 `domain.works.add_identifier` 的
实现发现：如果直接在 `keep_work_id` 上 `add_identifier`，而 `merge_work_id` 名下那个
标识符还没删，会因为全局唯一约束直接冲突失败（`IdentifierConflict`，如果
`merge_work_id` 已经被软删则是 `IdentifierBelongsToDeletedWork`——不管哪种都会失败）。
所以 `_migrate_identifiers` 固定顺序：先 `remove_identifier(old_id)`（释放掉这个全局
唯一槛位），再在 `keep_work_id` 上 `add_identifier(...)`。因为全局唯一约束本身保证了
两个不同 work 不可能同时持有同一个标准化值，删除之后逻辑上不可能再冲突。

## 批量忽略：瞬时批量模式

`bulk_dismiss_candidates` 走项目既定的"瞬时批量"模式（父 job 一行 + `counts_json`，
只有失败项才建子行）——和 `features/organizing` 的 `_run_batch` 同一个形状，但因为
features 之间禁止互相 import（lint 规则 4），这里独立重新实现了一份私有的
`_run_batch`，不是重复劳动而是架构要求下必要的重复。

## 刻意裁剪的范围

- **不迁移 `work_provenance`**：合并时 `merge_work_id` 的来源记录随 `purge_work` 一起
  消失。这是内部审计轨迹，用户不直接查看，可以接受随文献消失。
- **不迁移跨 work 的 `work_relations`**：目前 v1 没有任何 feature 会写入
  `relation_type` 的具体值，没有真正的迁移规则可写，等有实际语义后再补。
- **没有"扫描整库找重复"的批量函数**：目前重复候选只由 `features/importing` 在导入时
  按 `title_year_key` 产生，没有 UI 入口需要整库重扫，所以没有实现。
