# features/annotating

单条文献的元数据编辑 + 笔记。

## 对外表面

- `update_metadata(db, *, library_id, work_id, **fields) -> UpdateMetadataResult`
- `get_work_note(db, work_id) -> WorkNoteDTO | None`
- `set_work_note(db, *, library_id, work_id, content) -> WorkNoteDTO | None`

## 依赖方向

`domain/{works, notes}`。没有 `caps/` 依赖——标题/年份的规范化已经在
`domain.works.update_work` 内部完成（它自己调 `caps/slug` 算
`title_year_key`），本模块不需要重复接触 `caps/slug`。

## 为什么标题/年份变了要重新扫一遍重复候选

`domain.works.update_work` 自己的 docstring 写得很直白：它只负责在标题或
年份变化时重算 `title_year_key`，**不会**自动重跑一次库范围的疑似重复
扫描——那是故意的，因为默认在每次编辑都触发一次库扫描代价不小，也容易在
用户还没保存完一次完整编辑时就弹出一堆候选打断体验。

这正是 `features/annotating` 存在的理由：`domain/works` 保持机械（只管
自己的表），"什么时候该重新扫"这个策略交给调用方决定。`update_metadata`
的做法是：编辑前先读一次旧值，编辑后比较标题/年份是否真的变了（而不是
"传了这个参数"就触发——`abstract` 等字段传了但标题年份没变就不扫），变了
才调 `find_candidates_by_title_year_key` 粗筛，命中的每一条都落一条
`duplicate_candidate` 交给 `features/dedupe_review` 走人工确认流程。

## 两个"笔记"不是一回事

这个模块同时摸到两个名字都叫"笔记"但完全不同的东西：

- `update_metadata` 的 `note` 参数 —— 是 `works` 表自己的一个字段（比如
  从 RIS 的 `N1` 标签导入时带进来的短文本备注），和业务逻辑无关，纯粹是
  文献记录本身的一个属性。
- `get_work_note` / `set_work_note` —— 操作的是 `domain/notes` 表，用户
  在文献库页面手写的长文本笔记。`features/organizing` 合并文献时拼接的
  也是这一个。

两者没有任何同步关系，改一个不会影响另一个（见
`test_update_metadata_note_field_is_the_works_table_note_not_domain_notes`）。
测试里刻意把两者放在一起验证，就是为了在设计阶段把这个容易搞混的点钉死。

## 没有 `domain/jobs` 依赖

和 `organizing` / `dedupe_review` 不同，这里操作的永远是单条文献、单次
同步调用，不存在"批量 + 失败隔离"的需求，所以没有引入 `jobs`。

## 刻意裁剪的范围

- **不负责"改标题后自动合并"**：扫描只负责产生候选，是否合并、怎么合并
  是 `features/dedupe_review.merge_works` 的事，`annotating` 不越界去调
  它（也不允许——features 之间禁止互相 import）。
- **AI 结果逐字段编辑后保存**（计划里 E2 阶段的功能）不在 v1 范围内，归
  `ai_enrichment`。
