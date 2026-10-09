# features/library_browse

文献库列表的只读模型：筛选编译 / 分页 / 排序 / 卡片附属信息展开 /
`resolve_selection()`。组合 `domain/{works,folders,tags,attachments,notes}`，
本模块不开表。

## 为什么 `list_library_page` 和 `resolve_selection` 共用同一套筛选编译逻辑

两者的"筛选条件要不要生效"是同一件事——`list_library_page` 把筛选结果
分页展示给人看，`resolve_selection(mode="all_filtered")` 把同一组筛选结果
不分页地展开成全量 id 给批量操作用。两个函数都调用私有的
`_compile_filter_work_ids()`，保证"卡片列表显示的是什么"和"点‘全选所有筛选
结果’选中的是什么"永远是同一个筛选条件算出来的同一个候选集——不会出现
"看到的 20 条和全选出来的 1000 条其实不是同一套筛选逻辑"的不一致。

## 为什么要先往 `domain/works` 加 `list_work_ids`/`count_works`/`work_ids` 过滤/`sort_by`/`deleted_only`

这是在实现本模块时发现的真实缺口：`domain.works.list_works` 原来只接受
`library_id`/`include_deleted`/`limit`/`offset`，固定 `updated_at desc`——
没有办法让"按标签筛选"和"分页"在同一次查询里完成。如果不加这些参数，
`list_library_page`/`resolve_selection` 就只能先把整个库的 `list_works()`
结果拉到 Python 里再做交集/排序/截断——这正是「两处已定」第 2 条明确否决
的那种"先全量再本地筛"的反模式（会让"全选所有筛选结果"退化成要么拉全表、
要么深分页 OFFSET 越往后越慢）。详细设计记在 `domain/works/README.md`
「`list_works` 的 `sort_by`/`work_ids`/`list_work_ids`/`count_works`」一节，
这里不重复。

## 筛选编译：标签 AND、文件夹、两者组合

`tag_ids` 多个标签之间是 AND（必须同时打了全部给定标签才算命中），
`tag_ids` 和 `folder_id` 之间也是 AND——用 Python 里的集合交集实现，不是
SQL 层的 JOIN：每个候选集合（某个标签下的 `work_id` 集合、某个文件夹下的
`work_id` 集合）本身都很小（个位数到几百，不是全库规模），交集的开销可以
忽略；真正昂贵、需要留给数据库做的"按 `library_id` + 排序 + 分页"仍然是
`domain.works` 里的一次 SQL 查询。一个筛选条件都没给时，内部用 `None`（不是
空列表）表示"不额外限制候选集"——`domain.works` 的 `work_ids` 参数对
`None` 和 `[]` 的语义区分同样重要，见上一节链接的 README。

给的 `tag_id`/`folder_id` 不属于 `library_id` 这个库时报 `ValueError`——
和 `domain.tags.add_tag_to_work`/`domain.folders.add_work_to_folder` 遇到
同一种情况时的报法保持一致，不额外发明新的异常类型。

## `resolve_selection` 的两种 mode，和"id 不过浏览器"

计划里「两处已定」第 2 条的落地：前端只提交 `mode` + 筛选条件
（`mode="all_filtered"`）或者一组手动勾选的 id（`mode="explicit"`，比如用户
在当前这一屏逐个勾选了几张卡片，这些 id 本来就是从浏览器交互里产生的，
没法靠筛选条件反推出来）；`work_id` 的全集从不需要在分页之间被传来传去。
`all_filtered` 一次查询展开成全量 id——这是本函数存在的根本原因，分页是给
人看的，批量操作要的是全集。

`explicit` 模式对给定的 `work_ids` 做一次库边界校验（不属于 `library_id`
的静默丢弃，不报错）——这是防御性的：前端正常情况下不会带来别的库的 id，
但调用方（最终是批量操作的 `features/organizing` 等）不应该信任客户端传来
的 id 没被篡改过，静默丢弃比报错更安全（报错反而会告诉攻击者"这个 id 确实
存在，只是不属于你"）。

## 依赖方向

依赖 `domain/{works,folders,tags,attachments,notes}`。不依赖任何其它
`features/*`（符合规则 4）。只通过各 domain 的 contract 拿 DTO，不碰任何
`models.py`。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| `tags`/`folders`/`attachments` 的批量展开（卡片列表逐条 N+1 查询） | `domain.notes` 已经有 `list_notes_for_works` 这个批量版本，另外三个 domain 目前没有；v1 一页卡片数量在几十这个量级，N+1 的代价可接受。真要优化：给 `domain.tags`/`domain.folders`/`domain.attachments` 各加一个 `list_*_for_works(work_ids)` 批量版本（照搬 `domain.notes` 的先例），不改本模块的对外签名 |
| 按第一作者排序 | `domain.works.list_works` 还没有这个排序键（见该模块 README），本模块的 `SORT_KEYS` 跟着同步只有四个 |
| "未归档"（不在任何文件夹里）视图 | 需要给 `domain.folders` 加一个"列出不在任何文件夹里的 work_id"的反向查询（不是简单的"某个文件夹下有哪些"），目前没有实现；v1 先把"按某个具体文件夹筛选"做对 |
| 全文检索（标题/摘要关键词搜索） | 计划里没有把它列进本模块的最小范围；要做的话是在筛选编译里再加一路条件，不影响现有签名 |
| 按年份范围 / `item_type` 筛选 | 同上，`domain.works` 目前也没有暴露这类过滤，等有明确消费方再加 |
| "三层计数显示"（筛选总数/已加载/已选）的具体页面渲染 | 计划把这类"页面设计要素"明确标注成"不进最小模块，视图设计时再定"；本模块只负责把`total` 算对，页面怎么摆是 `app/pages/library` 的事 |
| `resolve_selection` 之外对批量操作本身的编排（打标签/移文件夹/删除/导出） | `features/organizing`/`exporting` 等各自的职责，本模块只负责把"选择范围"解析成 id |
