# adapters/sources

按标识符（DOI / PMID）查文献元数据的可插拔适配器：`crossref.py`（Crossref
REST API）、`pubmed.py`（NCBI E-utilities `esummary`）。v1 只做这两个。

## 为什么返回 `caps.bibformats.Record`，不是自己发明的 DTO

`Record` 本来就是"RIS/BibTeX/CSL-JSON 三种格式解析出来的记录共用的中间
表示"（见 `caps/bibformats/record.py` 的 docstring）。Crossref/PubMed 返回的
字段（标题、作者、刊物、年卷期页、DOI）和 `Record` 的字段几乎一一对应，
"从外部数据源查到的一条文献记录"在语义上就是同一种东西——没有理由为它另外
发明一个长得几乎一样的类型。调用方（`features/metadata_lookup`）拿到
`Record` 后自己决定怎么往 `domain/works` 的字段上填，两个适配器和
`caps/bibformats` 的三个格式解析器事实上共用同一份"文献记录"概念。

查不到时返回 `None`，不是异常——"这个 DOI/PMID 没查到"是
`features/metadata_lookup` 的正常业务分支（人工补录、用户输入错了标识符都
会走到这里），不该用异常控制流。只有查询本身失败（非 404 的错误状态码、
`caps/httpfetch` 报告的传输层失败）才抛各自的 `CrossrefError`/`PubMedError`——
这两类情况调用方需要能区分："没查到"可以直接告诉用户"没找到"，"查询失败"
应该重试或者报错给用户"稍后再试"。

## 两个适配器彼此独立、互不知道对方存在

按三层判定标准（"可插拔、同类多实现、删一个其余全绿"），`crossref.py` 和
`pubmed.py` 是同一类（`adapters/sources/`）下的两个独立实现，互不 import，
各自定义自己的异常类型（`CrossrefError`/`PubMedError`，不共享基类）——删掉
任意一个，另一个和它自己的测试完全不受影响（`scripts/lint.sh` 规则 7 会
验证这一点）。

## 真正的 HTTP 请求全部走 `caps/httpfetch.fetch()`

重试/429 退避/UA/SSRF 防护/可选限速全部在那一层，这两个适配器只管"怎么把
数据源的 JSON 形状翻译成 `Record`"。`rate_limiter`/`transport`/`resolve`
三个参数原样转发给 `fetch()`：前者供调用方跨多次查询复用限速器（比如
`features/metadata_lookup` 批量查询时用同一个 `RateLimiter` 实例），后两者
是测试钩子（生产代码不传），单测用 `httpx.MockTransport` 模拟响应、用假
`resolve()` 避免真实 DNS 查询。

`mailto`（crossref）/`api_key`（pubmed）这两个可选参数对应各自数据源的
"带上身份信息换更宽松限速"约定，来自调用方从 `domain/connections` 的
`source_credential` 连接读出来的配置——本模块不知道、也不关心这些值从哪来。

## 两个数据源各自的取舍

### crossref.py

- `type` 字段映射表只覆盖 `Record.ITEM_TYPES` 里有的那些 Crossref 类型
  （`journal-article` / `proceedings-article` / `book-chapter` / `monograph`
  / `report` / `dataset` / `posted-content` / `dissertation` 等），查不到
  映射的一律退回 `"journal_article"`（Crossref 索引里占绝大多数）。
- 日期优先取 `published-print`，没有则 `published-online`，再没有则
  `issued`——打印版日期通常更稳定，在线优先出版（epub ahead of print）的
  日期有时会在正式出版后被 Crossref 更新。
- `abstract` 字段常带 JATS 标签（`<jats:p>...</jats:p>`），只做最基础的
  标签剥离（正则去掉 `<...>`），不是完整的 JATS/XML 解析——够用，不严谨。

### pubmed.py

- 用 `esummary.fcgi?retmode=json` 而不是 `efetch.fcgi` 的完整 XML 记录，
  代价是作者名只有 `"Smith JA"` 这种"姓 + 名字缩写"格式，没有逗号分隔、
  没法像 `caps.bibformats.parse_person`（假设"最后一个词是姓"）那样可靠
  拆成 family/given——拆了反而会把姓和缩写拆反。诚实地整段存进
  `Person.literal`，不猜。
- `esummary` 对"查不到"不用 HTTP 404 表示，而是在响应体里把该 uid 排除在
  `result.uids` 之外，或者给它一个带 `error` 字段的条目——两种情况本模块
  都当作"查不到"返回 `None`，判断逻辑必须解析响应体，不能只看状态码。
- `elocationid` 字段可能是 DOI（`"doi: 10.xxx"`）也可能是别的标识符类型
  （比如 `"pii: S0140..."`），只在它以 `"doi"` 开头时才提取。
- `item_type` 固定为 `"journal_article"`——PubMed 索引几乎全是期刊文献，
  v1 不解析 `pubtype` 列表来区分 Review/Case Report 等子类型。

## 依赖方向

依赖 `caps/httpfetch`（发请求）+ `caps/bibformats`（`Record`/`Person`）+
标准库（`json` `re` `urllib.parse`）。不依赖任何 `domain/*`、`features/*`——
符合规则 2。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| `adapters/sources/nih_reporter.py` | 计划里是 E3 阶段才做，不在 v1 范围 |
| PubMed 用 `efetch` 拿完整 XML（含拆分好的作者 given/family、完整摘要） | v1 用 `esummary` 够用；真要换成 `efetch`，这是整个 `pubmed.py` 内部实现的事，不影响 `Record` 这个对外契约 |
| Crossref 完整 JATS abstract 解析（保留格式、处理嵌套标签） | 简单正则剥离够用；真要做，属于可以后续加强的内部实现细节，不影响对外签名 |
| 批量查询（一次查多个 DOI/PMID） | 两边 API 都支持批量接口，但 v1 的 `features/metadata_lookup` 调用量级不需要；要加是新增一个函数，不改现有签名 |
| 对结果做缓存 | 缓存策略属于 `features/metadata_lookup` 的职责，不下沉到这层 |
