# caps/bibformats

RIS / BibTeX / CSL-JSON 三种文献格式的解析与序列化。无表、无网络，单测不碰磁盘。
这是 v1 范围里最大的一个 cap，内部按计划拆成 `record.py`（三个格式共用的记录模型）
+ `parsers/{ris,bibtex,csljson}.py`（各格式实现），`service.py` 只做聚合和按格式名分发。

## 为什么三个格式缝在一个 cap 里，不是三个 cap

三者共用同一套记录语义（标题/作者/年份/期刊/DOI/...），耦合是真实的——拆开
会导致 `features/importing`、`features/exporting` 各自维护一份"这三种格式
怎么互相转"的知识。格式种类只会新增（比如以后加 EndNote XML）不会被替换掉，
"可插拔、删一个其余全绿"的 adapter 判据不适用，所以保持一个 cap、内部分子模块。

## 对外承诺

```python
from caps.bibformats import parse, serialize, Record, Person, ITEM_TYPES

records = parse("ris", ris_text)           # -> list[Record]
records = parse("bibtex", bibtex_text)
records = parse("csljson", csljson_text)

text = serialize("ris", records)            # -> str
text = serialize("bibtex", records)
text = serialize("csljson", records)

# 也可以直接用某个格式的函数，不走分发：
from caps.bibformats import parse_ris, serialize_bibtex
```

`Record`：`item_type`（`ITEM_TYPES` 枚举之一）+ `citekey` + `title` + `authors`
（`Person` 元组）+ `year`/`month`/`day` + `container_title`（期刊名/会议名/
书名）+ `volume`/`issue`/`pages` + `publisher` + `doi`/`pmid`/`isbn`/`issn`/`url`
+ `abstract`/`language`/`note` + `extra`（没建模的格式特有字段，按原字段名存）。

异常：`ParseError`（语法错误）/ `UnsupportedFormat`（`parse()`/`serialize()`
传了不认识的格式名），共同基类 `BibFormatsError`。

## 字段建模原则：只建模三者共有且语义明确的部分

`Record` 的字段是三种格式的交集，不是并集——格式特有、或者语义拿不准的字段
（RIS 的 `KW`、BibTeX 的 `address`/`edition`、CSL-JSON 的 `event`）一律落进
`extra`，按原始字段名做 key。这样"解析再序列化回同一种格式"至少不会静默
丢数据，即使这个 cap 没有为某个字段专门建模。

## 三个格式自己的"原始类型值"记号，和为什么不能跨格式泄漏

三个解析器都会把原始的类型标记存进 `extra`：RIS 存 `ris_type`（比如 `CPAPER`
而不是规范化成 `CONF`）、BibTeX 存 `bibtex_type`（比如 `conference` 而不是
`inproceedings`）、CSL-JSON 存 `csl_type`。序列化回**同一种**格式时优先用这个
原始值，保证 "RIS → Record → RIS" 这种同格式往返不会把 `CPAPER` 改写成 `CONF`。

但这带来一个真实的 bug（demo 脚本跑出来的）：一条从 RIS 解析出来的记录，
它的 `extra` 里天然带着 `ris_type` 这个键——序列化成 BibTeX 时如果把 `extra`
剩下的内容照搬成字段，就会在 `.bib` 文件里写出一个不存在的
`ris_type = {JOUR}` 字段（RIS → BibTeX → RIS 这条路径上同理会把
`bibtex_type` 泄漏进最终的 RIS 输出）。修复：三个序列化器在把 `extra` 剩余
内容当成"未知字段"吐出来之前，都会先排除 `record.FORMAT_TYPE_KEYS`
（`{ris_type, bibtex_type, csl_type}`）这三个键，不管当前是不是正在处理
对应的那个格式。

## 姓名拆分是启发式，不保证对

`parse_person`（RIS 的单个 `AU` 值、BibTeX 用 `" and "` 切开后的每一段）：
含逗号按 `"Family, Given"` 拆；不含逗号但有空格，**最后一个词当 family，
前面全部当 given**——这对 "John van der Berg" 这种多词 given name 会拆错
（会把 "van der Berg" 当成姓）。这是英文姓名启发式的已知局限，三种格式的
原始数据本身往往也拆不干净，v1 不做姓名库或规则引擎去猜，整个 given 部分
原样保留，至少不丢字符。`{World Health Organization}` 这种被 `{}` 包住的
整体当机构作者，不拆。

## ISSN/ISBN 消歧

RIS 用同一个 `SN` tag 装 ISSN 或 ISBN（规范本身没区分）。启发式：
`item_type` 是 `book`/`book_chapter` 时当 ISBN，否则当 ISSN。序列化反过来：
按 `item_type` 决定从 `isbn` 还是 `issn` 取值填回 `SN`。BibTeX/CSL-JSON
两者有独立的 `isbn`/`issn` 字段，不存在这个问题。

## BibTeX 解析范围：只覆盖"写一个条目的字段"

不支持 `@string` 宏展开、`@preamble`、字符串拼接运算符 `#`、交叉引用
（`crossref` 字段）。遇到 `@string`/`@preamble`/`@comment` 条目直接跳过
（不报错）——这些在真实 `.bib` 文件里很常见，跳过比拒绝整个文件更有用。
嵌套大括号（`{A Study of {AI} Models}`，常见的"保护大写字母"写法）不做
特殊展开，内层大括号原样保留在字段值里。

## CSL-JSON 解析的宽容之处

标准 CSL-JSON 顶层是一个数组，但也接受单个对象（自动包成单元素列表）和
`{"items": [...]}` 这种 Zotero 常见的包裹形式。`issued` 日期优先用
`date-parts`，没有时退回 `raw`/`literal` 字段里找一个四位数年份。

## 依赖方向

标准库（`re` `json` `dataclasses` `typing`）。**不依赖任何 infra / domain /
adapters / features。** `caps/citation`（CSL 样式渲染 + BibTeX key 生成）
会依赖这个 cap 的 `Record`/`Person`——这是架构里明确允许的"caps 之间单向
依赖"的例子，不违反 `caps/` 只能依赖 `infra` 的规则（那条规则管的是
caps 不能往上依赖 domain/features，caps 之间本身可以依赖）。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 生成新的 citekey/BibTeX key | `caps/citation`（解析已有的 citekey 这里做，生成新的不是这里的知识） |
| CSL 样式渲染（变成"Smith J, 2019. Title. Nature."这种格式化引用串） | `caps/citation` |
| 跨格式完全无损往返（RIS → BibTeX → CSL-JSON → RIS 字节级一致） | v1 不保证；只保证同格式往返不丢`Record`已建模的字段 |
| MEDLINE/PubMed 原生格式、EndNote XML、Zotero RDF | v1 不需要；格式只会新增，加法不难 |
| BibTeX `@string` 宏、`crossref` 字段展开 | 真实 .bib 文件较少依赖这些，用不到 |
| 姓名拆分的语言学规则/姓名库 | 启发式够用，见上 |
| RIS 多个 `UR` 的语义区分（哪个是全文链接哪个是摘要页） | 第一个进 `url`，其余进 `extra["extra_urls"]`，谁也不知道哪个更重要 |
