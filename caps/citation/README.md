# caps/citation

格式化引用串渲染 + BibTeX citation key 生成 + LaTeX `\cite{}` 命令拼接。
无表、无网络，单测不碰磁盘。依赖 `caps/bibformats` 的 `Record`/`Person`
（caps 之间允许单向依赖，这正是架构文档举的例子）和 `caps/slug` 的
`slugify`（生成 BibTeX 安全的 citekey）。

## 对外承诺

```python
from caps.citation import render_citation, generate_bibtex_key, latex_cite, list_styles

render_citation(record, "apa")         # "Smith, J., & Doe, J. (2019). Title. Nature, 567(7748), 100-110. https://doi.org/..."
render_citation(record, "vancouver")   # "Smith J, Doe J. Title. Nature. 2019;567(7748):100-110."
list_styles()                          # frozenset({"apa", "vancouver"})

key = generate_bibtex_key(record, existing_keys=already_used_keys)  # "smith2019title"
latex_cite(key)                        # "\\cite{smith2019title}"
latex_cite([key1, key2], command="citep")  # "\\citep{key1,key2}"
```

异常：`UnknownStyle`（`render_citation` 传了没注册的样式名）/ `InvalidCitekey`
（`latex_cite` 收到空的或包含 `{}\,% ` 等字符的 citekey），共同基类
`CitationError`。

## ⚠️ "CSL 样式渲染"在这里不是一个 CSL 处理器

这是最容易被误解的一点，必须写清楚：`render_citation` **不解析 `.csl` 样式
文件**，不是 citeproc 那种通用引用处理器。`"apa"`/`"vancouver"` 是两套硬编码
在 `render_apa`/`render_vancouver` 里的固定格式化规则，碰巧用了 CSL 生态里
大家认得的名字。要支持新样式就是写一个新的 `render_xxx` 函数、注册进
`_STYLES` 字典——和真正读 CSL XML 描述文件、按它的继承规则动态生成格式是
完全不同量级的工作，v1 不做。选这两个样式是因为这是一个医学/生物文献场景
下的文献管理工具，APA 和 Vancouver/NLM 是最常被要求的两种。

## BibTeX citekey 生成规则

`{第一作者姓}{年份}{标题第一个有意义的词}`，全部小写无分隔符：
`"Smith, John" + 2019 + "A Great Paper"` → `"smith2019great"`（"A" 是停用词
被跳过）。规则细节：

- 没有作者 → 用 `"anon"`；机构作者（`Person.literal`）直接用机构名
- 没有年份 → 跳过年份段（`"smithgreat"` 不是 `"smithNonegreat"`）
- 标题为空或全是停用词（`a`/`an`/`the`/`of`/`on`/`in`/`and`/`for`/`to`）
  → 跳过标题段
- 规范化交给 `caps/slug` 的 `slugify(..., separator="")`——这保证了 Unicode
  姓名（中文姓氏等）被保留而不是丢字符，和 `caps/slug` 用在文件命名上是
  同一套规则，不用在这里重新发明一套

### 撞 key 怎么办

`existing_keys` 撞上了就加字母后缀（`smith2019great` → `...a` → `...b` →
...→`...z`→`...aa`→...，和 Excel 列名同一个进位算法），不是覆盖、不是报错、
不会漏判——这是**调用方**（`features/exporting`、`features/importing`）的
责任去传一份当前已用的 key 集合，这个 cap 本身不记得之前生成过什么。

## 作者格式化是两套独立规则，不是共享一个"姓名格式化器"参数化出来的

APA 用逗号分隔的带点 initials（`"Smith, J."`），多作者用 Oxford 逗号 + `&`
（`"A, X., B, Y., & C, Z."`）；Vancouver 用姓+无点 initials 中间空格
（`"Smith J"`），超过 6 位作者截断成前 6 位 + `"et al"`（这是 Vancouver/NLM
风格本身的标准规则，不是本模块的裁剪）。两套格式在逗号/空格/点号上的差异
细到没法用一个参数化函数覆盖两种风格而不把代码写得比两个独立函数更难读，
所以就是两个独立函数，只共享 `_initials()` 这一个真正通用的子逻辑。

## 姓名拆分准确性依赖上游

这里不重新判断"这段文字到底是姓还是名"——`Person.family`/`given`/`literal`
是 `caps/bibformats` 解析阶段已经做出的判断（它自己也是启发式，见那个 cap
的 README）。这里只管"有了 family/given，怎么按 APA 或 Vancouver 的规则
拼出来"，不管"这个 family 到底对不对"。

## 依赖方向

`caps/bibformats`（`Record`/`Person`）+ `caps/slug`（`slugify`）+ 标准库
（`re`）。**不依赖任何 infra / domain / adapters / features。**

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 真正的 CSL XML 样式处理器（任意 `.csl` 文件、继承规则、locale） | v1 不做，见上 |
| 更多样式（Chicago / MLA / IEEE / 国标 GB/T 7714 等） | 按需加 `render_xxx` 函数，架构上不难，v1 先两个够用的 |
| APA 超过 20 位作者的截断规则（列前 19 位 + "..." + 最后一位） | v1 不做，超长作者列表会被完整列出 |
| 标题大小写转换（APA 要求 sentence case） | 不碰调用方传进来的大小写，原样输出 |
| 期刊名缩写（Vancouver 规范要求缩写刊名，如 "N Engl J Med"） | 没有缩写词典，用 `container_title` 原样 |
| citekey 生成时检查是否和数据库里已有记录重复（语义重复，不是 key 撞车） | `domain/works` 的查重逻辑，这里只管字符串层面的 key 唯一性 |
| LaTeX 特殊字符转义（标题里的 `&`/`%`/`_` 等） | 不在这个 cap 的范围——这里只处理 citekey 本身的安全性，不处理引用串正文进 LaTeX 文档时的转义 |
