"""RIS 格式的解析与序列化。

RIS 是按行的 tag-value 格式：每条记录以 `TY  - <type>` 开头、`ER  - ` 结尾，
中间每行 `XX  - value`（两字母 tag）。本实现刻意不追求覆盖 RIS 规范里所有
几十个 tag，只取 `Record` 建模的那些字段，其余原样塞进 `extra`，保证至少
"解析再序列化回 RIS"不丢数据。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from caps.bibformats.record import (
    FORMAT_TYPE_KEYS,
    ParseError,
    Record,
    format_person,
    join_pages,
    parse_person,
    split_pages,
)

_TAG_RE = re.compile(r"^([A-Z][A-Z0-9])\s{0,2}-\s?(.*)$")

_RIS_TYPE_TO_ITEM_TYPE = {
    "JOUR": "journal_article",
    "BOOK": "book",
    "CHAP": "book_chapter",
    "CONF": "conference_paper",
    "CPAPER": "conference_paper",
    "THES": "thesis",
    "RPRT": "report",
    "PAT": "patent",
    "DATA": "dataset",
    "ELEC": "webpage",
    "WEB": "webpage",
    "UNPB": "preprint",
}
_ITEM_TYPE_TO_RIS_TYPE = {
    "journal_article": "JOUR",
    "preprint": "UNPB",
    "book": "BOOK",
    "book_chapter": "CHAP",
    "conference_paper": "CONF",
    "thesis": "THES",
    "report": "RPRT",
    "patent": "PAT",
    "dataset": "DATA",
    "webpage": "ELEC",
}

# 这些 tag 由本模块显式处理；其余全部落进 extra，key 用原始 tag 名。
_HANDLED_TAGS = frozenset(
    {"TY", "ER", "AU", "A1", "TI", "T1", "T2", "JO", "JF", "BT", "PY", "Y1",
     "VL", "IS", "SP", "EP", "PB", "DO", "SN", "UR", "AB", "N2", "LA", "N1"}
)


def parse_ris(text: str) -> list[Record]:
    if not text.strip():
        return []

    records: list[Record] = []
    fields: dict[str, list[str]] = {}
    last_tag: str | None = None
    in_record = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue

        match = _TAG_RE.match(line)
        if match is None:
            # 没有 tag 前缀的行：当成上一个 tag 的续行（部分导出器会这样折行长摘要）
            if last_tag is None:
                raise ParseError(f"RIS 第一行就不是合法的 tag 行: {line!r}")
            fields[last_tag][-1] += " " + line.strip()
            continue

        tag, value = match.group(1), match.group(2).strip()

        if tag == "TY":
            fields = {"TY": [value]}
            in_record = True
            last_tag = "TY"
            continue

        if not in_record:
            raise ParseError(f"在 TY 之前出现了 tag {tag!r}，不是一条合法记录的开头")

        if tag == "ER":
            records.append(_record_from_fields(fields))
            fields = {}
            in_record = False
            last_tag = None
            continue

        fields.setdefault(tag, []).append(value)
        last_tag = tag

    if in_record:
        raise ParseError("文件结束但最后一条记录没有 ER 结尾")

    return records


def _record_from_fields(fields: dict[str, list[str]]) -> Record:
    ris_type = fields.get("TY", [""])[0]
    item_type = _RIS_TYPE_TO_ITEM_TYPE.get(ris_type, "journal_article")

    authors = tuple(parse_person(v) for v in (fields.get("AU") or fields.get("A1") or []))

    title = _first(fields, "TI") or _first(fields, "T1")
    container_title = _first(fields, "BT") or _first(fields, "T2") or _first(fields, "JO") or _first(
        fields, "JF"
    )

    year, month, day = _parse_ris_date(_first(fields, "PY") or _first(fields, "Y1"))

    sp = _first(fields, "SP")
    ep = _first(fields, "EP")
    pages = join_pages(sp, ep)

    sn = _first(fields, "SN")
    isbn = sn if item_type in ("book", "book_chapter") else None
    issn = sn if item_type not in ("book", "book_chapter") else None

    urls = fields.get("UR") or []
    url = urls[0] if urls else None

    abstract = _first(fields, "AB") or _first(fields, "N2")

    extra: dict[str, object] = {"ris_type": ris_type}
    if len(urls) > 1:
        extra["extra_urls"] = urls[1:]
    for tag, values in fields.items():
        if tag in _HANDLED_TAGS:
            continue
        extra[tag] = values if len(values) > 1 else values[0]

    return Record(
        item_type=item_type,
        title=title,
        authors=authors,
        year=year,
        month=month,
        day=day,
        container_title=container_title,
        volume=_first(fields, "VL"),
        issue=_first(fields, "IS"),
        pages=pages,
        publisher=_first(fields, "PB"),
        doi=_first(fields, "DO"),
        isbn=isbn,
        issn=issn,
        url=url,
        abstract=abstract,
        language=_first(fields, "LA"),
        note=_first(fields, "N1"),
        extra=extra,
    )


def _first(fields: dict[str, list[str]], tag: str) -> str | None:
    values = fields.get(tag)
    return values[0] if values else None


def _parse_ris_date(raw: str | None) -> tuple[int | None, int | None, int | None]:
    if not raw:
        return None, None, None
    parts = raw.split("/")
    numbers: list[int | None] = []
    for part in parts[:3]:
        part = part.strip()
        numbers.append(int(part) if part.isdigit() else None)
    while len(numbers) < 3:
        numbers.append(None)
    return numbers[0], numbers[1], numbers[2]


def serialize_ris(records: Sequence[Record]) -> str:
    lines: list[str] = []
    for record in records:
        lines.extend(_serialize_one(record))
    return "\n".join(lines) + ("\n" if lines else "")


_MISSING = object()


def _serialize_one(record: Record) -> list[str]:
    extra = dict(record.extra)
    ris_type = extra.pop("ris_type", _MISSING)
    if ris_type is _MISSING:
        ris_type = _ITEM_TYPE_TO_RIS_TYPE.get(record.item_type, "JOUR")
    for key in FORMAT_TYPE_KEYS:
        extra.pop(key, None)  # 别家格式的记号，不是真实的 RIS tag

    lines = [f"TY  - {ris_type}"]

    for author in record.authors:
        lines.append(f"AU  - {format_person(author)}")

    if record.title:
        lines.append(f"TI  - {record.title}")
    if record.container_title:
        tag = "BT" if record.item_type in ("book", "book_chapter") else "T2"
        lines.append(f"{tag}  - {record.container_title}")

    if record.year is not None:
        date = f"{record.year}/{record.month or ''}/{record.day or ''}/"
        lines.append(f"PY  - {date}")

    if record.volume:
        lines.append(f"VL  - {record.volume}")
    if record.issue:
        lines.append(f"IS  - {record.issue}")

    sp, ep = split_pages(record.pages)
    if sp:
        lines.append(f"SP  - {sp}")
    if ep:
        lines.append(f"EP  - {ep}")

    if record.publisher:
        lines.append(f"PB  - {record.publisher}")
    if record.doi:
        lines.append(f"DO  - {record.doi}")

    sn = record.isbn if record.item_type in ("book", "book_chapter") else record.issn
    if sn:
        lines.append(f"SN  - {sn}")

    extra_urls = extra.pop("extra_urls", [])
    if record.url:
        lines.append(f"UR  - {record.url}")
    for extra_url in extra_urls:
        lines.append(f"UR  - {extra_url}")

    if record.abstract:
        lines.append(f"AB  - {record.abstract}")
    if record.language:
        lines.append(f"LA  - {record.language}")
    if record.note:
        lines.append(f"N1  - {record.note}")

    for tag, value in extra.items():
        values = value if isinstance(value, list) else [value]
        for v in values:
            lines.append(f"{tag}  - {v}")

    lines.append("ER  - ")
    return lines
