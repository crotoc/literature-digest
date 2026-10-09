"""caps/citation 实现：CSL 风格的格式化引用串 + BibTeX citation key 生成 +
LaTeX \\cite{} 命令拼接。依赖 caps/bibformats 的 Record/Person（caps 之间允许
单向依赖，这是架构里明确举的例子）和 caps/slug 的 slugify（生成 BibTeX 安全
的 citekey）。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from caps.bibformats import Person, Record
from caps.slug import slugify


class CitationError(Exception):
    """caps/citation 所有异常的基类。"""


class UnknownStyle(CitationError):
    """`render_citation()` 被传了一个没注册的样式名。"""


class InvalidCitekey(CitationError):
    """`latex_cite()` 收到一个不能安全放进 LaTeX `\\cite{}` 的 citekey
    （空、或包含花括号/反斜杠/逗号/百分号/空白）。"""


# ---------------------------------------------------------------------------
# 姓名格式化（APA 和 Vancouver 各自的写法不同，共享 initials 提取逻辑）
# ---------------------------------------------------------------------------


def _initials(given: str | None, *, with_periods: bool) -> str:
    if not given:
        return ""
    letters = [part[0].upper() for part in given.split() if part]
    if with_periods:
        return "".join(f"{letter}." for letter in letters)
    return "".join(letters)


def _format_author_apa(person: Person) -> str:
    if person.literal is not None:
        return person.literal
    initials = _initials(person.given, with_periods=True)
    if person.family and initials:
        return f"{person.family}, {initials}"
    return person.family or initials


def _format_authors_apa(authors: Sequence[Person]) -> str:
    if not authors:
        return ""
    names = [_format_author_apa(a) for a in authors]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + ", & " + names[-1]


def _format_author_vancouver(person: Person) -> str:
    if person.literal is not None:
        return person.literal
    initials = _initials(person.given, with_periods=False)
    if person.family and initials:
        return f"{person.family} {initials}"
    return person.family or initials


def _format_authors_vancouver(authors: Sequence[Person]) -> str:
    """超过 6 位作者缩写成前 6 位 + "et al"——这是 Vancouver/NLM 风格的标准做法，
    不是本模块自己的裁剪。"""
    if not authors:
        return ""
    names = [_format_author_vancouver(a) for a in authors]
    if len(names) > 6:
        names = names[:6] + ["et al"]
    return ", ".join(names)


# ---------------------------------------------------------------------------
# 两个内置样式
# ---------------------------------------------------------------------------


def render_apa(record: Record) -> str:
    authors = _format_authors_apa(record.authors)
    year = record.year if record.year is not None else "n.d."
    title = (record.title or "").rstrip(".")

    result = f"{authors} ({year}). {title}." if authors else f"({year}). {title}."

    if record.container_title:
        result += f" {record.container_title}"
        if record.volume:
            result += f", {record.volume}"
            if record.issue:
                result += f"({record.issue})"
        if record.pages:
            result += f", {record.pages}"
        result += "."

    if record.doi:
        result += f" https://doi.org/{record.doi}"
    elif record.url:
        result += f" {record.url}"

    return result


def render_vancouver(record: Record) -> str:
    authors = _format_authors_vancouver(record.authors)
    title = (record.title or "").rstrip(".")

    result = f"{authors}. {title}." if authors else f"{title}."

    if record.container_title:
        result += f" {record.container_title}."

    if record.year is not None:
        result += f" {record.year}"
        if record.volume:
            result += f";{record.volume}"
            if record.issue:
                result += f"({record.issue})"
        if record.pages:
            result += f":{record.pages}"
        result += "."

    return result


_STYLES = {"apa": render_apa, "vancouver": render_vancouver}


def list_styles() -> frozenset[str]:
    return frozenset(_STYLES)


def render_citation(record: Record, style: str = "apa") -> str:
    """渲染一条格式化引用串。

    `style` 是 `list_styles()` 里的一个名字（目前 `"apa"` / `"vancouver"`）。
    这不是一个通用 CSL 处理器——不解析 `.csl` 样式文件，样式是硬编码在
    `render_apa`/`render_vancouver` 里的两套固定规则，见 README 的范围说明。
    """
    try:
        renderer = _STYLES[style]
    except KeyError:
        raise UnknownStyle(f"不支持的样式: {style!r}（支持: {sorted(_STYLES)}）") from None
    return renderer(record)


# ---------------------------------------------------------------------------
# BibTeX citation key 生成
# ---------------------------------------------------------------------------

_TITLE_STOPWORDS = frozenset({"a", "an", "the", "of", "on", "in", "and", "for", "to"})


def _first_author_family(record: Record) -> str | None:
    if not record.authors:
        return None
    person = record.authors[0]
    return person.family or person.literal


def _first_title_word(title: str | None) -> str | None:
    if not title:
        return None
    for word in re.findall(r"[^\W\d_]+", title, re.UNICODE):
        if word.casefold() not in _TITLE_STOPWORDS:
            return word
    return None


def _letter_suffix(n: int) -> str:
    """1 → 'a', 2 → 'b', ..., 26 → 'z', 27 → 'aa', 28 → 'ab', ...（和 Excel 列名
    同一个进位算法），用来给撞键的 citekey 加后缀。"""
    letters = []
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters.append(chr(ord("a") + remainder))
    return "".join(reversed(letters))


def generate_bibtex_key(record: Record, *, existing_keys: Iterable[str] = ()) -> str:
    """生成一个 BibTeX citekey：`{第一作者姓}{年份}{标题第一个有意义的词}`，
    全部小写、无分隔符（`caps/slug` 的 `slugify(..., separator="")` 负责规范化）。

    没有作者时用 "anon"；没有年份时直接跳过年份段；没有标题或标题全是停用词
    （"a"/"the"/"of"/...）时跳过标题段——退化到最短也能生成 "anon" 这种兜底。

    和 `existing_keys` 撞了就加字母后缀（`smith2019attention` →
    `smith2019attentiona` → `...b` → ...），不是覆盖、不是报错。
    """
    family = _first_author_family(record) or "anon"
    year_part = str(record.year) if record.year is not None else ""
    word = _first_title_word(record.title) or ""
    base = slugify(f"{family}{year_part}{word}", separator="")

    existing = set(existing_keys)
    if base not in existing:
        return base

    n = 1
    while True:
        candidate = f"{base}{_letter_suffix(n)}"
        if candidate not in existing:
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# LaTeX \cite{} 命令拼接
# ---------------------------------------------------------------------------

_UNSAFE_CITEKEY_CHARS = frozenset("{}\\,% \t\n")


def latex_cite(keys: str | Sequence[str], *, command: str = "cite") -> str:
    """拼一条 LaTeX 引用命令，比如 `latex_cite(["smith2019a", "doe2020b"])`
    → `"\\\\cite{smith2019a,doe2020b}"`。`command` 可以换成 `"citep"`/`"textcite"`
    等 natbib/biblatex 的命令名——这里只管拼字符串，不关心调用方用的是哪个
    引用包。"""
    if isinstance(keys, str):
        keys = [keys]
    keys = list(keys)
    if not keys:
        raise InvalidCitekey("至少需要一个 citekey")
    for key in keys:
        if not key or any(ch in _UNSAFE_CITEKEY_CHARS for ch in key):
            raise InvalidCitekey(f"不能安全放进 \\cite{{}} 的 citekey: {key!r}")
    return f"\\{command}{{{','.join(keys)}}}"
