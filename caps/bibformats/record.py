"""caps/bibformats 的共享记录模型。三种格式（RIS/BibTeX/CSL-JSON）解析出来的数据
都落到同一个 Record 上，序列化时再从同一个 Record 出发——这是三个格式解析器能
共用、而不是各写一套数据结构的前提。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# 三个格式解析器各自往 Record.extra 里存一个"原始类型值"（ris_type/bibtex_type/
# csl_type），用来让"解析再序列化回同一种格式"保留原文类型写法（比如 RIS 的
# CPAPER 不会被规范化成 CONF）。但这意味着一条从 RIS 解析出来的记录，它的 extra
# 里天然带着 "ris_type" 这个键——序列化成 BibTeX 时如果把 extra 剩下的内容照搬成
# 字段，就会在 .bib 文件里写出一个不存在的 "ris_type = {...}" 字段。三个序列化器
# 在把 extra 剩余内容当成"未知字段"吐出来之前，都要先排除这三个键。
FORMAT_TYPE_KEYS = frozenset({"ris_type", "bibtex_type", "csl_type"})

ITEM_TYPES = frozenset(
    {
        "journal_article",
        "preprint",
        "book",
        "book_chapter",
        "conference_paper",
        "thesis",
        "report",
        "patent",
        "dataset",
        "webpage",
    }
)


class BibFormatsError(Exception):
    """caps/bibformats 所有异常的基类。"""


class ParseError(BibFormatsError):
    """输入文本不符合该格式的语法（括号不匹配、JSON 格式错误等）。"""


class UnsupportedFormat(BibFormatsError):
    """`parse()`/`serialize()` 被传入一个不在 `SUPPORTED_FORMATS` 里的格式名。"""


@dataclass(frozen=True)
class Person:
    """一个作者/编者。`literal` 用于机构作者或解析器没法可靠拆分姓名时的兜底——
    三者不是互斥的互斥量，但正常情况下调用方应该只看 `literal is not None` 还是
    `family`/`given` 这一组。"""

    family: str | None = None
    given: str | None = None
    literal: str | None = None


@dataclass(frozen=True)
class Record:
    """一条文献记录，三种格式的解析器和序列化器共用的中间表示。

    字段选取原则：只建模三种格式**共有且语义明确**的部分；某个格式特有、或者
    这里拿不准语义的字段，落进 `extra`（按原始格式的字段名做 key），解析器之间
    不互通，但至少不会在"解析再序列化回同一种格式"时静默丢数据。
    """

    item_type: str = "journal_article"
    citekey: str | None = None
    title: str | None = None
    authors: tuple[Person, ...] = ()
    year: int | None = None
    month: int | None = None
    day: int | None = None
    container_title: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    doi: str | None = None
    pmid: str | None = None
    isbn: str | None = None
    issn: str | None = None
    url: str | None = None
    abstract: str | None = None
    language: str | None = None
    note: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


def parse_person(raw: str) -> Person:
    """把一段原始姓名文本拆成 Person。约定（RIS 和 BibTeX 的单个作者字段共用）：
    - 整体被 `{}` 包住 → 机构作者，整段进 `literal`，不拆
    - 含逗号 → "Family, Given"
    - 不含逗号但有空格 → 最后一个词当 family，前面全部当 given（常见英文名启发式，
      对 "John van der Berg" 这类多词 given name 不保证正确，这是启发式的已知局限）
    - 单个词、没有逗号没有空格 → 当成 literal（没法判断是姓是名）
    """
    raw = raw.strip()
    if raw.startswith("{") and raw.endswith("}") and len(raw) >= 2:
        return Person(literal=raw[1:-1].strip())
    if "," in raw:
        family, given = raw.split(",", 1)
        family = family.strip() or None
        given = given.strip() or None
        return Person(family=family, given=given)
    parts = raw.split()
    if len(parts) >= 2:
        return Person(given=" ".join(parts[:-1]), family=parts[-1])
    if parts:
        return Person(literal=parts[0])
    return Person(literal="")


def format_person(person: Person) -> str:
    """`parse_person` 的逆操作：统一格式化成 "Family, Given"（RIS/BibTeX 都认这个
    写法）。`literal` 优先于拆分字段——如果两者都设了（不应该发生，但防御一下），
    `literal` 更可信，因为它通常意味着"这是我们选择不拆的机构名"。"""
    if person.literal is not None:
        return person.literal
    if person.family and person.given:
        return f"{person.family}, {person.given}"
    return person.family or person.given or ""


def split_pages(pages: str | None) -> tuple[str | None, str | None]:
    """"100-110" → ("100", "110")；"100" → ("100", None)；None → (None, None)。
    给 RIS 的 SP/EP 两个独立字段用——CSL-JSON 和 BibTeX 都用一个 pages 字符串，
    只有 RIS 把起止页拆成两个 tag。"""
    if not pages:
        return None, None
    if "-" in pages:
        start, end = pages.split("-", 1)
        return start.strip() or None, end.strip() or None
    return pages.strip() or None, None


def join_pages(start: str | None, end: str | None) -> str | None:
    """`split_pages` 的逆操作。"""
    if start and end:
        return f"{start}-{end}"
    return start or end or None
