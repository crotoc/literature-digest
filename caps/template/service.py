"""两种模板渲染：方括号占位（给文件命名）和 Jinja（给 prompt）。

合成一个 cap 是因为两者是同一件事——"按模板生成文本"（caps 复核时把原 `naming`
的渲染部分和原 `prompt` 并了进来）。但两者**刻意用两套完全不同的引擎**：

| 用途 | 引擎 | 为什么不能换成另一个 |
|---|---|---|
- 文件命名 `[FIRSTAUTHOR:1]_[year]_[Title]`：自己的方括号解析。
  模板由用户在设置页里填，给用户一个图灵完备的引擎去生成**文件名**，风险和收益完全不成比例
- LLM prompt：Jinja（**沙箱**）。prompt 真的需要条件和循环（"有摘要就带上摘要"）

方括号渲染器只做字典查表，没有任何执行语义，所以用户填什么都不可能出事。
Jinja 那半用 `SandboxedEnvironment` + `StrictUndefined` + 无 loader（`{% include %}`
直接失败）+ 输出长度上限。

## 和 caps/slug 的分工

本模块**不保证输出是合法文件名**。管道是：`render_bracket()` 产出原始文本
→ `caps/slug.slugify()` 规范化。所以缺值留下的空段（`__`）由 slugify 折叠，
不在这里处理——否则两处都要懂分隔符规则。
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from jinja2 import StrictUndefined, UndefinedError
from jinja2 import TemplateError as JinjaTemplateError
from jinja2 import TemplateSyntaxError as JinjaSyntaxError
from jinja2.sandbox import SandboxedEnvironment

ON_MISSING_RAISE = "raise"
ON_MISSING_EMPTY = "empty"
ON_MISSING_KEEP = "keep"
_ON_MISSING_CHOICES = frozenset({ON_MISSING_RAISE, ON_MISSING_EMPTY, ON_MISSING_KEEP})

DEFAULT_LIST_SEPARATOR = "_"
DEFAULT_MAX_OUTPUT_CHARS = 200_000

# `[[` / `]]` 是字面方括号的转义；其余形如 `[name]` 或 `[name:12]` 的是占位。
_PLACEHOLDER = re.compile(r"\[\[|\]\]|\[([A-Za-z][A-Za-z0-9_]*)(?::(\d+))?\]")


# ── 异常 ───────────────────────────────────────────────────────────────────

class TemplateError(Exception):
    """本模块所有异常的基类。"""


class TemplateSyntaxInvalid(TemplateError):
    def __init__(self, detail: str, *, line: int | None = None) -> None:
        where = f"（第 {line} 行）" if line else ""
        super().__init__(f"模板语法错误{where}：{detail}")
        self.detail = detail
        self.line = line


class MissingValue(TemplateError):
    def __init__(self, key: str) -> None:
        super().__init__(f"模板用到了 [{key}]，但没给这个值")
        self.key = key


class UnknownPlaceholder(TemplateError):
    def __init__(self, keys: Sequence[str], allowed: Sequence[str]) -> None:
        super().__init__(f"模板里有不认识的占位：{sorted(keys)}；可用的是 {sorted(allowed)}")
        self.keys = tuple(keys)
        self.allowed = tuple(allowed)


class OutputTooLarge(TemplateError):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"渲染结果 {size} 字符，超过上限 {limit}")
        self.size = size
        self.limit = limit


# ── 方括号占位 ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Placeholder:
    """模板里出现的一个占位。"""

    key: str
    """原样写法，如 `FIRSTAUTHOR`。大小写决定输出的大小写。"""

    limit: int | None
    """`:N` 的 N。"""

    @property
    def normalized_key(self) -> str:
        """查表用的键名（统一小写）。"""
        return self.key.lower()


def _case_transform(written: str) -> str:
    """占位名的写法决定输出的大小写。

    `[year]` → 原样小写、`[Title]` → 词首大写、`[FIRSTAUTHOR]` → 全大写、
    `[firstAuthor]`（混写）→ **不动**，当作显式的"别碰大小写"。
    """
    if written.isupper():
        return "upper"
    if written.islower():
        return "lower"
    if written[0].isupper() and written[1:].islower():
        return "title"
    return "keep"


def _apply_case(value: str, mode: str) -> str:
    if mode == "upper":
        return value.upper()
    if mode == "lower":
        return value.lower()
    if mode == "title":
        return value.title()
    return value


def _stringify(value: object, *, limit: int | None, separator: str) -> str:
    """把一个值变成字符串，并应用 `:N`。

    `:N` 的含义按类型走，各自是该类型最自然的读法：
      - 列表 → 取**前 N 项**（`[AUTHORS:3]` = 前三个作者）
      - 字符串 → 取**前 N 个字符**（`[Title:40]` = 标题截到 40 字）
    """
    if isinstance(value, str):
        return value[:limit] if limit else value
    if isinstance(value, Mapping):
        return str(value)
    if isinstance(value, Iterable) and not isinstance(value, bytes | bytearray):
        items = [str(item) for item in value]
        if limit:
            items = items[:limit]
        return separator.join(items)
    text = str(value)
    return text[:limit] if limit else text


def iter_placeholders(template: str) -> list[Placeholder]:
    """列出模板里用到的占位，按出现顺序，去重保序。

    设置页用它来显示"这个命名模板需要哪些字段"。
    """
    seen: dict[tuple[str, int | None], Placeholder] = {}
    for match in _PLACEHOLDER.finditer(template):
        if match.group(1) is None:
            continue
        limit = int(match.group(2)) if match.group(2) else None
        placeholder = Placeholder(key=match.group(1), limit=limit)
        seen.setdefault((placeholder.key, limit), placeholder)
    return list(seen.values())


def validate_bracket_template(template: str, *, allowed_keys: Iterable[str]) -> None:
    """检查模板里的占位是否都在允许集合内。

    给设置页用：用户填错字段名时**在保存那一刻**就拒绝，而不是等到几天后
    上传附件时才炸。

    Raises:
        UnknownPlaceholder
    """
    allowed = {key.lower() for key in allowed_keys}
    unknown = [p.key for p in iter_placeholders(template) if p.normalized_key not in allowed]
    if unknown:
        raise UnknownPlaceholder(unknown, sorted(allowed))


def render_bracket(
    template: str,
    values: Mapping[str, object],
    *,
    on_missing: str = ON_MISSING_RAISE,
    list_separator: str = DEFAULT_LIST_SEPARATOR,
) -> str:
    """渲染方括号模板。纯查表，**无执行语义**。

    Args:
        template: 如 `"[FIRSTAUTHOR:1]_[year]_[Title:40]"`。
            `[[` 和 `]]` 是字面方括号的转义。
        values: 键名**不区分大小写**（内部统一小写查表）。
        on_missing:
            - `"raise"`（默认）：缺值抛 `MissingValue`。让 bug 露出来。
            - `"empty"`：缺值渲染成空串。空段留下的 `__` 交给
              `caps/slug.slugify` 折叠，不在这里处理。
            - `"keep"`：原样留下 `[key]`，用于预览。
        list_separator: 列表值的连接符。文件名场景下它最终会被 slugify 归一，
            所以这里写什么都行。

    Returns:
        渲染结果。**不保证是合法文件名**——那是 `caps/slug` 的事。

    Raises:
        MissingValue / ValueError
    """
    if on_missing not in _ON_MISSING_CHOICES:
        raise ValueError(f"on_missing 只能是 {sorted(_ON_MISSING_CHOICES)}")

    lookup = {str(key).lower(): value for key, value in values.items()}

    def replace(match: re.Match[str]) -> str:
        whole = match.group(0)
        if whole == "[[":
            return "["
        if whole == "]]":
            return "]"

        written = match.group(1)
        limit = int(match.group(2)) if match.group(2) else None
        value = lookup.get(written.lower())

        if value is None or value == "" or (isinstance(value, Sequence) and len(value) == 0
                                            and not isinstance(value, str)):
            if on_missing == ON_MISSING_RAISE:
                raise MissingValue(written)
            if on_missing == ON_MISSING_KEEP:
                return whole
            return ""

        text = _stringify(value, limit=limit, separator=list_separator)
        return _apply_case(text, _case_transform(written))

    return _PLACEHOLDER.sub(replace, template)


# ── Jinja（给 prompt）─────────────────────────────────────────────────────

def _environment() -> SandboxedEnvironment:
    """沙箱环境。

    四道约束，都是因为模板可能来自数据库（站点级/账号级设置）而不是代码：
      - `SandboxedEnvironment`：挡住 `__class__` / `__globals__` 这类属性穿越
      - `StrictUndefined`：拼错变量名**报错**而不是静默渲染成空串
      - **不给 loader**：`{% include %}` / `{% extends %}` 直接失败，读不到磁盘文件
      - `autoescape=False`：输出是给 LLM 的纯文本，不是 HTML。
        要是哪天把结果塞进页面，转义是**调用方**的事
    """
    return SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        loader=None,
    )


def render_jinja(
    template: str,
    values: Mapping[str, object],
    *,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> str:
    """用沙箱 Jinja 渲染 prompt 模板。

    Args:
        max_output_chars: 输出长度上限。`{% for %}` 跑飞了的话，这是唯一的刹车
            （Jinja 没有超时机制）。

    Raises:
        TemplateSyntaxInvalid: 模板语法不对。
        MissingValue: 用到了没给的变量（`StrictUndefined`）。
        OutputTooLarge: 输出超过上限。
        TemplateError: 其他渲染失败。
    """
    if max_output_chars <= 0:
        raise ValueError("max_output_chars 必须为正")
    try:
        compiled = _environment().from_string(template)
    except JinjaSyntaxError as error:
        raise TemplateSyntaxInvalid(error.message or str(error), line=error.lineno) from error

    try:
        rendered = compiled.render(dict(values))
    except UndefinedError as error:
        raise MissingValue(_undefined_name(str(error))) from error
    except JinjaSyntaxError as error:
        raise TemplateSyntaxInvalid(error.message or str(error), line=error.lineno) from error
    except JinjaTemplateError as error:
        raise TemplateError(str(error)) from error
    except OverflowError as error:
        # 沙箱自己的 safe_range() 保护（挡 {% for i in range(10**9) %} 这类）
        # 抛的是裸 OverflowError，不是 jinja2 的异常，会直接穿透上面那个
        # except 边界——调用方捕 TemplateError 捕不住它。统一收口。
        raise TemplateError(f"超出沙箱资源限制：{error}") from error

    if len(rendered) > max_output_chars:
        raise OutputTooLarge(len(rendered), max_output_chars)
    return rendered


def _undefined_name(message: str) -> str:
    """从 Jinja 的 "'xxx' is undefined" 里把变量名抠出来，抠不到就原样返回消息。"""
    match = re.search(r"'([^']+)' is undefined", message)
    return match.group(1) if match else message


def jinja_variables(template: str) -> list[str]:
    """列出 Jinja 模板里引用的顶层变量名，排序返回。

    给设置页预览"这个 prompt 需要哪些字段"。

    Raises:
        TemplateSyntaxInvalid
    """
    from jinja2 import meta  # noqa: PLC0415 — 只有这里用到

    environment = _environment()
    try:
        parsed = environment.parse(template)
    except JinjaSyntaxError as error:
        raise TemplateSyntaxInvalid(error.message or str(error), line=error.lineno) from error
    return sorted(meta.find_undeclared_variables(parsed))
