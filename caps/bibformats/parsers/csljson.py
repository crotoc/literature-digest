"""CSL-JSON 格式的解析与序列化。CSL-JSON 本身就是 JSON，所以这里没有自己写
tokenizer——真正的工作是 CSL 的字段命名 / 日期结构 / 作者结构 和 Record 之间
的双向映射。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from caps.bibformats.record import FORMAT_TYPE_KEYS, ParseError, Person, Record

_CSL_TYPE_TO_ITEM_TYPE = {
    "article-journal": "journal_article",
    "article-preprint": "preprint",
    "manuscript": "preprint",
    "book": "book",
    "chapter": "book_chapter",
    "paper-conference": "conference_paper",
    "thesis": "thesis",
    "report": "report",
    "patent": "patent",
    "dataset": "dataset",
    "webpage": "webpage",
}
_ITEM_TYPE_TO_CSL_TYPE = {
    "journal_article": "article-journal",
    "preprint": "article-preprint",
    "book": "book",
    "book_chapter": "chapter",
    "conference_paper": "paper-conference",
    "thesis": "thesis",
    "report": "report",
    "patent": "patent",
    "dataset": "dataset",
    "webpage": "webpage",
}

_SIMPLE_CSL_KEYS = {
    "container-title": "container_title",
    "volume": "volume",
    "issue": "issue",
    "page": "pages",
    "publisher": "publisher",
    "DOI": "doi",
    "PMID": "pmid",
    "ISBN": "isbn",
    "ISSN": "issn",
    "URL": "url",
    "abstract": "abstract",
    "language": "language",
    "note": "note",
}
_RECORD_KEY_TO_CSL = {v: k for k, v in _SIMPLE_CSL_KEYS.items()}
_HANDLED_CSL_KEYS = frozenset(_SIMPLE_CSL_KEYS) | {"id", "type", "title", "author", "issued"}
_MISSING = object()


def parse_csljson(text: str) -> list[Record]:
    if not text.strip():
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"不是合法的 JSON: {exc}") from exc

    if isinstance(data, dict):
        items = data.get("items", [data])
    elif isinstance(data, list):
        items = data
    else:
        raise ParseError(f"顶层结构必须是数组或对象，实际是 {type(data).__name__}")

    return [_record_from_item(item) for item in items]


def _record_from_item(item: dict[str, Any]) -> Record:
    csl_type = item.get("type", "")
    item_type = _CSL_TYPE_TO_ITEM_TYPE.get(csl_type, "journal_article")

    authors = tuple(_person_from_csl(a) for a in item.get("author", []))
    year, month, day = _parse_issued(item.get("issued"))

    kwargs: dict[str, Any] = {}
    for csl_key, record_key in _SIMPLE_CSL_KEYS.items():
        if csl_key in item:
            kwargs[record_key] = item[csl_key]

    extra: dict[str, Any] = {"csl_type": csl_type}
    extra.update({k: v for k, v in item.items() if k not in _HANDLED_CSL_KEYS})

    return Record(
        item_type=item_type,
        citekey=str(item["id"]) if "id" in item else None,
        title=item.get("title"),
        authors=authors,
        year=year,
        month=month,
        day=day,
        extra=extra,
        **kwargs,
    )


def _person_from_csl(raw: dict[str, Any]) -> Person:
    if "literal" in raw:
        return Person(literal=raw["literal"])
    return Person(family=raw.get("family"), given=raw.get("given"))


def _parse_issued(issued: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    if not issued:
        return None, None, None
    date_parts = issued.get("date-parts")
    if date_parts and date_parts[0]:
        parts = list(date_parts[0])
        while len(parts) < 3:
            parts.append(None)
        return parts[0], parts[1], parts[2]
    raw = issued.get("raw") or issued.get("literal")
    if raw:
        m = re.search(r"\d{4}", str(raw))
        if m:
            return int(m.group()), None, None
    return None, None, None


def serialize_csljson(records: Sequence[Record]) -> str:
    items = [_item_from_record(r) for r in records]
    return json.dumps(items, indent=2, ensure_ascii=False) + "\n"


def _item_from_record(record: Record) -> dict[str, Any]:
    extra = dict(record.extra)
    csl_type = extra.pop("csl_type", _MISSING)
    if csl_type is _MISSING:
        csl_type = _ITEM_TYPE_TO_CSL_TYPE.get(record.item_type, "article-journal")
    for key in FORMAT_TYPE_KEYS:
        extra.pop(key, None)  # 别家格式的记号，不是真实的 CSL-JSON 字段

    item: dict[str, Any] = {"id": record.citekey or "", "type": csl_type}
    if record.title:
        item["title"] = record.title
    if record.authors:
        item["author"] = [_person_to_csl(a) for a in record.authors]
    if record.year is not None:
        date_parts = [record.year]
        if record.month is not None:
            date_parts.append(record.month)
            if record.day is not None:
                date_parts.append(record.day)
        item["issued"] = {"date-parts": [date_parts]}

    for record_key, csl_key in _RECORD_KEY_TO_CSL.items():
        value = getattr(record, record_key)
        if value is not None:
            item[csl_key] = value

    item.update(extra)
    return item


def _person_to_csl(person: Person) -> dict[str, str]:
    if person.literal is not None and not person.family and not person.given:
        return {"literal": person.literal}
    result = {}
    if person.family:
        result["family"] = person.family
    if person.given:
        result["given"] = person.given
    return result
