# caps/template

两种模板渲染：方括号占位（给文件命名）和 Jinja（给 prompt）。无表、纯函数，
单测不建库、不联网。

## 为什么合一个 cap，又为什么两套引擎

caps 复核时把原 `naming` 的渲染部分和原 `prompt` 并了进来——两者都是同一件事
"按模板生成文本"。但引擎**刻意不统一**：

| 用途 | 引擎 | 为什么不能换成另一个 |
|---|---|---|
| 文件命名 `[FIRSTAUTHOR:1]_[year]_[Title]` | 自己的方括号解析 | 模板由用户在设置页里填。给用户一个图灵完备的引擎去生成**文件名**，风险和收益完全不成比例 |
| LLM prompt | Jinja（**沙箱**） | prompt 真的需要条件和循环（"有摘要就带上摘要"） |

方括号渲染器只做字典查表，**没有任何执行语义**——`[__class__]` 这种写法甚至
连占位的语法都够不上（占位名必须以字母开头），用户填什么都不可能出事。
Jinja 那半必须拿沙箱 + 严格模式扛着,因为模板可能来自数据库。

## 对外承诺

```python
from caps.template import render_bracket, iter_placeholders, validate_bracket_template
from caps.template import render_jinja, jinja_variables

render_bracket("[FIRSTAUTHOR:1]_[year]_[Title:40]",
                {"firstauthor": "Smith", "year": "2019", "title": "A Paper"})
# "SMITH_2019_A Paper"

iter_placeholders(template)               # 设置页显示"这个模板需要哪些字段"
validate_bracket_template(template, allowed_keys=[...])  # 保存时拒绝拼错的字段名

render_jinja("{% if abstract %}{{ abstract }}{% endif %}", {"abstract": "..."})
jinja_variables(template)                  # 列出模板引用的顶层变量
```

异常：`MissingValue` / `UnknownPlaceholder` / `TemplateSyntaxInvalid` / `OutputTooLarge`，
共同基类 `TemplateError`。

### 方括号的大小写规则：占位的写法决定输出的大小写

| 写法 | 规则 | 示例 |
|---|---|---|
| `[year]` 全小写 | 强制小写 | `smith` |
| `[YEAR]` 全大写 | 强制大写 | `SMITH` |
| `[Title]` 首字母大写 | `.title()` | `A Paper` |
| `[firstAuthor]` 混写 | **不动**，当作显式的"别碰大小写" | `SmITh` 原样 |

### `:N` 的含义按值的类型走，各自是该类型最自然的读法

- 字符串 `[Title:40]` → 取**前 40 个字符**
- 列表 `[AUTHORS:3]` → 取**前 3 项**

### 缺值的三种模式

- `"raise"`（默认）：抛 `MissingValue`，让 bug 露出来
- `"empty"`：渲染成空串，留下的 `__` 交给 `caps/slug.slugify` 折叠——
  **不在这里处理分隔符**，否则两处都要懂同一套规则
- `"keep"`：原样留下 `[key]`，给设置页预览用

空字符串、空列表、`None` 都算"缺值"，不是"给了个空值"。

### Jinja 那半的四道沙箱约束

模板可能来自数据库（站点级/账号级设置），不是代码里的常量，所以这不是性能优化,
是安全边界：

1. `SandboxedEnvironment`：挡住 `__class__.__mro__` 这类属性穿越
2. `StrictUndefined`：拼错变量名**报错**，不是静默渲染成空串
3. **不给 loader**：`{% include %}` / `{% extends %}` 直接失败，读不到磁盘文件
4. `max_output_chars` 上限：Jinja **没有超时机制**，这是跑飞循环的唯一刹车

### ⚠️ 沙箱自己的保护会抛裸 Python 异常，必须在本模块收口

Jinja 沙箱的 `safe_range()`（挡 `{% for i in range(10**9) %}`）抛的是裸
`OverflowError`，**不是** jinja2 的异常类型，会直接穿透 `except JinjaTemplateError`
这道边界——调用方捕 `TemplateError` 捕不住它。这是写单测时跑出来的真 bug，
已在 `render_jinja` 里单独 catch 并收口成 `TemplateError`。

### 和 `caps/slug` 的分工

本模块**不保证输出是合法文件名**。管道是：
`render_bracket()` 产出原始文本 → `caps/slug.slugify()` 规范化。

## 依赖方向

标准库（`re`）+ `jinja2`（项目已有依赖）。
**不依赖任何 infra / domain / adapters / features，也不依赖别的 cap。**

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 把渲染结果变成合法文件名 | `caps/slug` |
| 重名策略 ask / overwrite / rename | `features/uploading`（要回头调模板、要回 UI，是编排） |
| 决定命名模板里能用哪些字段 | 调用方通过 `allowed_keys` 传入 |
| CSL 引用格式渲染 | `caps/citation`（依赖 `bibformats` 的 record，是另一件事） |
| 模板语法高亮 / 编辑器 | 前端的事 |
| 渲染结果的 HTML 转义 | 调用方（`autoescape=False`，输出是纯文本给 LLM，不是 HTML） |
