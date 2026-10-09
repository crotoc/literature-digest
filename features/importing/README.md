# features/importing

把一段 RIS/BibTeX/CSL-JSON 文本导入到某个文库：解析 → 按标识符命中合并 /
按 `title_year_key` 命中标记疑似重复 / 新建 → 每条记录一个子 job。本模块
不开表，组合 `caps/bibformats` + `domain/{works,libraries,jobs}`。

## 导入决策：合并 / 新建+标记重复 / 新建

每条解析出来的 `Record` 按下面的顺序决策，三者互斥：

1. **标识符命中合并**：按 `doi` → `pmid` → `isbn` → `issn` 的顺序（见
   `IDENTIFIER_SCHEMES_FOR_MATCHING`，顺序即识别力强弱——doi 几乎不会错配，
   isbn/issn 覆盖面更窄但同样可靠），只要有一种在库里已经命中一个未删的
   work，就合并进那条已有记录，不新建。
2. **无标识符命中，但 `title_year_key` 命中**：新建一条 work，同时给命中的
   每一条已有记录各记一条 `duplicate_candidate`（`reason="title_year_key_match"`），
   留给 `dedupe_review` 人工确认或驳回——`title_year_key` 只是粗筛（标题+
   年份规范化后相同），不能自动判定为真重复，更不能自动合并（两篇完全不
   同的文章完全可能标题年份一样）。
3. **都没命中**：正常新建。

这三条分支的判定逐字落在计划的「标识符命中自动合并（不覆盖已有字段）；
`title_year_key` 命中则新建 + 写 `duplicate_candidates` 人工确认」——本模块
不是重新设计这个规则，只是把它接到 `caps/bibformats` 解析出来的 `Record`
上。

## 合并为什么"不覆盖已有字段"，以及为什么不是逐字段比较谁更新

合并命中时，`_fill_missing_fields` 只把 `existing` 里当前是 `None`/空元组
的字段，从新导入的 `Record` 补上；`existing` 已经有值的字段一律不碰，不比较
"哪个更新/更可信"。理由：合并这一刻完全不知道 `existing` 上的值是人工精心
编辑过的，还是上一次导入留下的粗糙版本——贸然用新数据覆盖，等于让"最后一次
导入"默认赢，这正是旧实现会丢用户手改数据的那类坑。想要"哪个数据源更可信"
这种判断，属于 `annotating`（人工编辑）或未来 `metadata_lookup` 回抓时的
覆盖策略，不属于导入这一步。

## 为什么标识符合并顺序里没有 `url`

`doi`/`pmid`/`isbn`/`issn` 都是"一个值几乎唯一对应一条文献"的强标识符。
`url` 不是——同一篇文章常见好几个不同的 url（出版商页面、机构代理改写后的
url、预印本服务器镜像），拿 `url` 做身份判定的误配风险和它们完全不是一个
量级，所以 `IDENTIFIER_SCHEMES_FOR_MATCHING` 故意不含它。`Record.url` 本身
在导入阶段被忽略（不落进 `work_identifiers`，也不参与匹配）——它属于
`fulltext`（E5）用来找全文候选的信息，不是文献身份的一部分。

## 为什么标识符冲突(`IdentifierConflict`)按"这一条记录失败"处理，而不是吞掉或终止整批

同一条记录可能带着两个分别属于**不同**已有 work 的标识符（比如 DOI 命中
work A 而它的 PMID 恰好已经挂在 work B 上）——这说明库里的数据本身存在
矛盾（这两个标识符不该分别属于两条不同的记录），不是导入逻辑能自动决定
"该信谁"的情况，必须留给人去看。所以 `_import_one` 不捕获
`IdentifierConflict`/`IdentifierBelongsToDeletedWork`，让它们从这条记录的
处理过程中原样抛出，交给 `import_records` 的外层循环当作这一条的失败——
失败隔离（见下一节）保证它不影响批次里其它记录。

## 为什么用"每条记录一个子 job"，而不是旧「两处已定」里瞬时批量的"只在失败时建子行"

计划明确把"导入"归进「长流程批量」一类（和全文下载、AI 评估同类），不是
打标签/删除那种「瞬时批量」——即使 v1 的导入处理本身是同步完成的（没有
`fulltext` 那种等待外部资源的异步中间态），"每条记录值得单独看、单独追溯"
这一点和处理是否同步无关：导入几百条文献后，用户需要能查"这一条到底是
新建的、合并的，还是被标记疑似重复的"，而不是只看到一个汇总数字。所以
每条记录仍然各建一个 `import_item` 子 job，`_CHILD_TRANSITIONS` 只有
`queued → {succeeded, failed}` 两条边（没有 `running` 中间态）——这是导入
和 `fulltext` 多步状态机的真实区别，不是偷工减料。

父 job 的 `counts_json` 实时累计 `created`/`merged`/`flagged_duplicate`/
`failed` 四类，`total` 在解析完成、建父 job 的那一刻就确定（不会在导入
过程中变化）。父 job 最终状态：只要有任意一条子项失败就是 `failed`，否则
`succeeded`——这是"失败隔离"的落地：一条记录失败不影响其它记录被正确
导入，但调用方能从父 job 状态一眼看出"这批导入是不是完全干净"。

## 为什么整段解析失败要在建任何 job 之前就报错

`caps.bibformats.parse()` 是对**整段文本**一次性解析，不支持"这一条解析
失败、其它条照常"的部分解析——如果格式名不对或者文本整体不合语法，根本
不知道"总共应该有多少条"，这时候建一个父 job 毫无意义（`total` 填什么都是
编的）。所以 `ImportParseFailed` 在 `create_job` 之前就抛出，调用方看到的
是一次干净的失败，不会在 `jobs` 表里留下一个永远是"0 条全部完成"的空
父 job。

## 为什么要先校验 `resolve_scope`

导入是写操作（建 work、建 job），`domain.libraries.resolve_scope` 是整个
多库隔离模型的唯一入口——不校验的话，一个账号理论上可以往任意 `library_id`
导入数据。校验放在解析之前还是之后无所谓（两者都不花钱），放在最前面是
为了"没权限"和"格式不对"两种失败互不干扰地报各自的错。

## 依赖方向

依赖 `caps/bibformats` + `domain/{works,libraries,jobs}`。不依赖任何其它
`features/*`（符合规则 4）。只通过各自 contract 拿 DTO/函数，不碰任何
`models.py`。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 导入前预览（先解析展示、用户确认后才真正落库） | 计划的最小范围没有要求这一步；真要做的话是在本模块加一个只解析不落库的函数，不改 `import_records` 的签名 |
| 批量大小限制 / 分批建子 job | 「两处已定」明确"不设 5000 这类架构硬上限"，规模约束归 `domain/usage` 的配额管，不是本模块的架构限制 |
| 导入来源记录（`domain.works.record_provenance`，"这条数据是从哪个导入批次来的"） | `record_provenance` 的 `source`/`source_id` 更适合描述"从 Crossref/PubMed 回抓"这种持续性来源，一次性文件导入是否值得单独记一条溯源日志，等 `metadata_lookup` 落地后再一起决定命名约定，避免现在先定、以后发现两边语义对不上 |
| 文件格式自动探测（用户上传文件不知道是哪种格式时自动猜） | `format` 由调用方（上传表单的下拉框）明确指定；自动探测是 `app/pages` 的 UX 细节，不是本模块的职责 |
| 标识符冲突以外更复杂的"智能合并"（比如允许用户事后选择"这次导入的数据更新，请覆盖"） | 覆盖已有字段的决策权应该在人工审核环节（`dedupe_review`/`annotating`），本模块只做"安全的自动合并"这一半 |
