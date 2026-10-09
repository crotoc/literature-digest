"""BibTeX 格式的解析与序列化。

只实现"解析/写出一个条目的字段"这个子集：不支持 `@string` 宏展开、`@preamble`、
`@comment`、字符串拼接运算符 `#`、交叉引用（`crossref` 字段）。这些都是 BibTeX
完整语法里真实存在但 v1 用不到的部分——遇到 `@string`/`@preamble`/`@comment`
条目会被跳过而不是报错，因为它们在很多真实 .bib 文件里出现，跳过比直接拒绝
整个文件更有用。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from caps.bibformats.record import (
    FORMAT_TYPE_KEYS,
    ParseError,
    Record,
    format_person,
    parse_person,
)

_ENTRY_START_RE = re.compile(r"@(\w+)\s*\{", re.IGNORECASE)

_BIBTEX_TYPE_TO_ITEM_TYPE = {
    "article": "journal_article",
    "book": "book",
    "inbook": "book_chapter",
    "incollection": "book_chapter",
    "inproceedings": "conference_paper",
    "conference": "conference_paper",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "techreport": "report",
    "unpublished": "preprint",
    "patent": "patent",
    "dataset": "dataset",
    "misc": "webpage",
}
_ITEM_TYPE_TO_BIBTEX_TYPE = {
    "journal_article": "article",
    "book": "book",
    "book_chapter": "incollection",
    "conference_paper": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "preprint": "unpublished",
    "patent": "patent",
    "dataset": "misc",
    "webpage": "misc",
}
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_CONSUMED_FIELDS = frozenset(
    {
        "author", "title", "year", "month", "pages", "publisher", "school", "institution",
        "journal", "booktitle", "volume", "number", "doi", "pmid", "isbn", "issn", "url",
        "abstract", "language", "note",
    }
)


def parse_bibtex(text: str) -> list[Record]:
    if not text.strip():
        return []

    records: list[Record] = []
    pos = 0
    length = len(text)

    while True:
        match = _ENTRY_START_RE.search(text, pos)
        if match is None:
            break
        entry_type = match.group(1).lower()
        body_start = match.end()

        depth = 1
        i = body_start
        while i < length and depth > 0:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        if depth != 0:
            raise ParseError(f"@{entry_type}{{...}} 条目括号不匹配，从偏移 {match.start()} 开始")

        body = text[body_start : i - 1]
        pos = i

        if entry_type in ("string", "preamble", "comment"):
            continue

        records.append(_record_from_entry(entry_type, body))

    return records


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """按 `sep` 切分，但不切开 `{}` 嵌套或 `"..."` 引号内的部分。"""
    parts: list[str] = []
    depth = 0
    in_quotes = False
    current: list[str] = []
    for ch in s:
        if ch == '"' and depth == 0:
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == sep and depth == 0 and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current or parts:
        parts.append("".join(current))
    return parts


def _unwrap_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == "{" and raw[-1] == "}":
        return raw[1:-1].strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].strip()
    return raw


def _record_from_entry(entry_type: str, body: str) -> Record:
    parts = _split_top_level(body)
    citekey = parts[0].strip() if parts else None
    citekey = citekey or None

    raw_fields: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        raw_fields[key.strip().lower()] = _unwrap_value(value)

    item_type = _BIBTEX_TYPE_TO_ITEM_TYPE.get(entry_type, "journal_article")

    authors = ()
    if "author" in raw_fields:
        names = raw_fields["author"].split(" and ")
        authors = tuple(parse_person(n) for n in names if n.strip())

    title = raw_fields.get("title")
    year = int(raw_fields["year"]) if raw_fields.get("year", "").strip().isdigit() else None
    month = _parse_month(raw_fields.get("month"))
    pages = raw_fields["pages"].replace("--", "-") if "pages" in raw_fields else None

    publisher = raw_fields.get("publisher")
    if publisher is None:
        publisher = raw_fields.get("school") or raw_fields.get("institution")

    extra = {"bibtex_type": entry_type}
    extra.update({k: v for k, v in raw_fields.items() if k not in _CONSUMED_FIELDS})

    container_title = raw_fields.get("journal") or raw_fields.get("booktitle")

    return Record(
        item_type=item_type,
        citekey=citekey,
        title=title,
        authors=authors,
        year=year,
        month=month,
        container_title=container_title,
        volume=raw_fields.get("volume"),
        issue=raw_fields.get("number"),
        pages=pages,
        publisher=publisher,
        doi=raw_fields.get("doi"),
        pmid=raw_fields.get("pmid"),
        isbn=raw_fields.get("isbn"),
        issn=raw_fields.get("issn"),
        url=raw_fields.get("url"),
        abstract=raw_fields.get("abstract"),
        language=raw_fields.get("language"),
        note=raw_fields.get("note"),
        extra=extra,
    )


def _parse_month(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw in _MONTHS:
        return _MONTHS[raw]
    if raw[:3] in _MONTHS:
        return _MONTHS[raw[:3]]
    try:
        return int(raw)
    except ValueError:
        return None


def serialize_bibtex(records: Sequence[Record]) -> str:
    return "\n".join(_serialize_one(r) for r in records)


def _serialize_one(record: Record) -> str:
    extra = dict(record.extra)
    entry_type = extra.pop("bibtex_type", None) or _ITEM_TYPE_TO_BIBTEX_TYPE.get(
        record.item_type, "article"
    )
    for key in FORMAT_TYPE_KEYS:
        extra.pop(key, None)  # 别家格式的记号，不是真实的 BibTeX 字段
    citekey = record.citekey or "unknown"

    fields: list[tuple[str, str]] = []
    if record.authors:
        fields.append(("author", " and ".join(format_person(a) for a in record.authors)))
    if record.title:
        fields.append(("title", record.title))

    is_conference_or_chapter = record.item_type in ("conference_paper", "book_chapter")
    if record.container_title:
        key = "booktitle" if is_conference_or_chapter else "journal"
        fields.append((key, record.container_title))

    if record.year is not None:
        fields.append(("year", str(record.year)))
    if record.month is not None:
        fields.append(("month", str(record.month)))
    if record.volume:
        fields.append(("volume", record.volume))
    if record.issue:
        fields.append(("number", record.issue))
    if record.pages:
        pages = record.pages
        if "-" in pages and "--" not in pages:
            pages = pages.replace("-", "--")
        fields.append(("pages", pages))
    if record.publisher:
        fields.append(("publisher", record.publisher))
    if record.doi:
        fields.append(("doi", record.doi))
    if record.pmid:
        fields.append(("pmid", record.pmid))
    if record.isbn:
        fields.append(("isbn", record.isbn))
    if record.issn:
        fields.append(("issn", record.issn))
    if record.url:
        fields.append(("url", record.url))
    if record.abstract:
        fields.append(("abstract", record.abstract))
    if record.language:
        fields.append(("language", record.language))
    if record.note:
        fields.append(("note", record.note))

    for key, value in extra.items():
        fields.append((key, str(value)))

    body = ",\n".join(f"  {key} = {{{value}}}" for key, value in fields)
    return f"@{entry_type}{{{citekey},\n{body}\n}}\n"
