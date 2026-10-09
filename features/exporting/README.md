# features/exporting

把文库里的条目导出成参考文献文件（纯文本，或连附件一起打包成 ZIP），以及
单条/多条引用的各种衍生文本——citation 相关的"复制"类功能全部归在这里
（计划里"全部归 `exporting.cite()`，不单开 feature"）。本模块不开表。

## 为什么没有依赖 `domain/libraries`

导出是只读操作，而"给定的 `work_ids` 是不是真的属于这个库"这个访问域
校验，在计划里属于 `features/library_browse.resolve_selection` 的职责——
它已经把"全选所有筛选结果"展开成一份校验过的显式 id 列表。本模块收到的
`work_ids` 应该已经是干净的，但仍然用 `domain.works.list_works` 的
`library_id` + `work_ids` 组合过滤查一次（而不是逐个 `get_work`）——这样
任何不属于 `library_id` 的 id 会被静默排除在导出结果之外，不额外报错，
和 `library_browse.resolve_selection` 处理跨库 id 的方式一致，属于同一种
"调用方不应该被信任到底"的防御，不是重新做一次访问控制。

## `_work_to_record`：`WorkDTO` 和 `Record` 几乎同构，但标识符要单独查

`domain.works` 的 `WorkDTO` 字段集和 `caps.bibformats.Record` 几乎一一
对应——不是巧合，`domain.works` 本来就直接复用了 `caps.bibformats` 的
`ITEM_TYPES` 和 `Person`。唯一的结构性差异是标识符：`Record` 把
`doi`/`pmid`/`isbn`/`issn` 当成自己的字段，`WorkDTO` 的标识符活在独立的
`work_identifiers` 表里（一条 work 可以有任意多个，且各自可追溯创建时间），
所以 `_work_to_record` 多查一次 `list_identifiers`。这个转换函数是本模块
几乎所有功能（导出 3 种格式、ZIP 打包、单条引用文本、格式化引用串、
citekey/LaTeX）共用的唯一"领域对象 → 格式层对象"桥梁。

## 为什么 ZIP 导出不是"Zotero RIS 含附件相对路径关联"

计划「③ 导出」表里把"RIS + PDF ZIP"和"Zotero RIS（含附件相对路径）"列成
两行。本模块实现的 `export_with_attachments_zip` 是前者：根目录一份参考
文献文件 + `attachments/` 目录下按 `filename_template` 命名的原始附件
文件，两者在 ZIP 里是平级的，没有互相指认。后者要求在 RIS 记录本身里写
一个指向 ZIP 内相对路径的字段（Zotero 导入 RIS 时认的那种写法），但
`caps/bibformats` 的 `Record` 目前没有对应这个用途的字段约定（`extra`
里塞一个 Zotero 专用键是可以做的，但那是一个需要单独定义、单独测试的
格式细节，不应该在实现这个模块时顺手决定）——按刻度范围表处理，等真的
要做 Zotero 兼容时再定。

## 文件名模板用 `caps/template` 的方括号语法，撞名交给 `caps/archive` 自己判重

`DEFAULT_FILENAME_TEMPLATE = "[firstauthor:1]_[year]_[title:60]"`——
`[firstauthor:1]` 这种写法是 `caps/template.render_bracket` 自带的截断
语法（取前 1 个字符），不是本模块自己写的逻辑。计划把"重名策略
ask/overwrite/rename 的编排"明确划给了 `features/uploading`（那是交互式的、
需要回 UI 问用户），本模块的 ZIP 是一次性生成的产物，不需要那套交互策略。

撞名去重直接交给 `write_zip_to(..., on_duplicate="rename")`，不在本模块
自己重复判重一遍——最初的实现确实自己写了一个基于"渲染后原始名字"的去重
函数，但 `write_zip_to` 内部的判重是在**净化后**（`sanitize_entry_name`
之后）的名字上做的；两个不同的渲染结果完全可能净化后撞到一起（比如分别
含有会被净化掉的不同控制字符），这种情况在净化前判重会漏掉，交给
`write_zip_to` 自己判重才是对的时机——这是本模块唯一一处真实的自查修正：
写完第一版后重新读了 `caps/archive/service.py` 的 `write_zip_to` 实现才
发现这个时机问题，改成直接用它自带的 `on_duplicate="rename"`，同时删掉了
本模块里那个重复、且时机不对的去重函数。

## `cite_keys`/`cite_latex` 为什么按"这一批"去重，而不是全库去重

`caps.citation.generate_bibtex_key` 的去重靠调用方传入的 `existing_keys`
——如果在全库范围去重，意味着每次只生成一个 key 都要把全库现有的
citekey 都查出来传进去，开销和"这次要引用的是哪几条"完全不成比例。
`cite_keys(work_ids=...)` 只在**这一批**给定的 work 之间去重，这和用户
的真实使用场景一致：复制 LaTeX 引用时，用户关心的是"这几篇放在一起时
key 不要撞"，不是"这个 key 有没有在我几年前导入的另一批文献里用过"——
真要做全库唯一的 citekey，得在 `works` 表上加一列持久化，这是比当前范围
大得多的另一个功能，不在这个模块里做。

## 引用样式：账号级设置，三级回退

`resolve_citation_style`/`set_default_citation_style` 包了一层
`domain.settings`（`module="exporting"`, `key="default_citation_style"`），
不自己存状态——计划明确要求设置值走 `settings_site`/`settings_account`
三级回退，不能像旧代码那样塞进业务表的 JSON 列。`cite_formatted` 本身
不自动读这个设置——`style` 仍然是显式参数，调用方（`app/pages`）自己决定
要不要先 `resolve_citation_style` 再把结果传进来；本模块不替调用方做
"该用哪个样式"的隐式决定。

## 依赖方向

依赖 `caps/{bibformats,citation,archive,blobstore,template}` +
`domain/{works,attachments,settings}`。不依赖任何其它 `features/*`
（符合规则 4）。`blob_store` 是调用方注入的 `caps.blobstore.BlobStore`
实例（由 `app/` 在启动时用 `adapters/storage/local_fs.LocalFsBackend`
组装），本模块不自己构造——和 `adapters/storage/local_fs.py` 注释里说的
装配方式一致。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| "Zotero RIS 含附件相对路径关联" | 需要先定义 `Record.extra` 里一个 Zotero 专用字段的写法，目前没有这个约定，见上文专门一节 |
| 复制 DOI / PubMed ID | 这就是读一次 `domain.works.list_identifiers`，没有格式转换也没有 caps 参与，调用方直接调 `domain/works` 的 contract 即可，本模块不需要再包一层 |
| 全文检索范围内的引用导出（比如"导出这次搜索命中的全部结果"） | 筛选编译是 `library_browse` 的职责，本模块只认已经解析好的 `work_ids`，不认筛选条件 |
| 导出任务异步化 / 进度条（大批量导出排队） | v1 范围里导出是同步请求-响应；真要支持数万条规模的异步导出，按"瞬时批量"粒度规则加一个 `domain.jobs` 父行即可，目前没有这个规模的真实需求 |
| 回收站条目可导出 | `export_bibliography`/`export_with_attachments_zip` 用 `domain.works.list_works` 的默认过滤（排除软删），没有传 `include_deleted=True`——导出回收站里的东西目前没有明确的产品需求，真要支持再加一个显式参数 |
