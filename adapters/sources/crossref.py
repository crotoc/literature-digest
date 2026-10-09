"""Crossref REST API（`https://api.crossref.org`）的查询适配器：给一个 DOI，
查回标准化的 `caps.bibformats.Record`。

复用 `Record` 而不是自己发明一个 DTO，是因为它本来就是"三种引用格式共用的
中间表示"（见 `caps/bibformats/record.py` 的 docstring）——Crossref 返回的
元数据字段（title/author/container-title/volume/...）和 `Record` 的字段
几乎一一对应，没有理由为"查到的一条文献记录"单独发明一个长得几乎一样的类型。
调用方（`features/metadata_lookup`）拿到 `Record` 之后自己决定怎么往
`domain/works` 的字段上填。

真正的 HTTP 请求全部走 `caps/httpfetch.fetch()`——重试/429 退避/UA/SSRF
防护都在那一层，这里只管"怎么把 Crossref 的 JSON 形状翻译成 Record"。
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from caps.bibformats import Person, Record
from caps.httpfetch import HttpFetchError, RateLimiter, default_resolve, fetch

BASE_URL = "https://api.crossref.org/works"

# Crossref 的 `type` 字段取值比 Record.item_type 细得多（还有 journal-issue /
# standard 等本模块不关心的类型），只映射 ITEM_TYPES 里有的那些；查不到映射的
# 一律退回 "journal_article"——这是 Crossref 索引里占绝大多数的类型，比抛异常
# 或强行发明一个新 item_type 更合理。
_TYPE_MAP = {
    "journal-article": "journal_article",
    "proceedings-article": "conference_paper",
    "book-chapter": "book_chapter",
    "monograph": "book",
    "book": "book",
    "edited-book": "book",
    "report": "report",
    "report-series": "report",
    "dataset": "dataset",
    "posted-content": "preprint",
    "dissertation": "thesis",
}

_JATS_TAG_RE = re.compile(r"<[^>]+>")


class CrossrefError(Exception):
    """Crossref 返回了非 404 的错误状态码，或者 `caps/httpfetch` 本身传输失败。"""


def lookup_by_doi(
    doi: str,
    *,
    mailto: str | None = None,
    rate_limiter: RateLimiter | None = None,
    transport=None,
    resolve=default_resolve,
) -> Record | None:
    """查一个 DOI。查不到（Crossref 返回 404）时返回 `None`，不是异常——
    "这个 DOI 没查到"是 `features/metadata_lookup` 的正常业务分支，不该用
    异常控制流（和 `caps/httpfetch` 本身"4xx/5xx 默认不是异常"的设计是
    同一个理由）。其余非 2xx/404 状态码，或者 `caps/httpfetch` 报告的传输层
    失败（DNS/超时/SSRF 拦截），都包成 `CrossrefError` 往上抛——那些不是
    "没查到"，是"这次查询本身失败了"，调用方应该能区分。

    `mailto` 对应 Crossref 的 polite pool 约定：带上联系邮箱能换来更稳定的
    限速配额。这个值来自调用方从 `domain/connections` 的 `source_credential`
    连接读出来的 `config["mailto"]`，本模块不知道、也不关心它从哪来。

    `resolve`/`transport` 原样转发给 `caps/httpfetch.fetch()`，是测试钩子
    （见那边 README 的同名参数说明）——生产代码不传，单测用它们避免真的发起
    DNS 查询和网络请求。
    """
    url = f"{BASE_URL}/{quote(doi, safe='')}"
    params = {"mailto": mailto} if mailto else None
    try:
        result = fetch(
            url, params=params, rate_limiter=rate_limiter, transport=transport, resolve=resolve
        )
    except HttpFetchError as exc:
        raise CrossrefError(f"查询 Crossref 失败：{doi}") from exc

    if result.status_code == 404:
        return None
    if result.status_code >= 400:
        raise CrossrefError(f"Crossref 返回 {result.status_code}：{doi}")

    payload = json.loads(result.content)
    message = payload.get("message")
    if not isinstance(message, dict):
        raise CrossrefError(f"Crossref 响应里没有 message 字段：{doi}")
    return _record_from_message(message)


def _record_from_message(message: dict) -> Record:
    titles = message.get("title") or []
    container_titles = message.get("container-title") or []
    issns = message.get("ISSN") or []
    year, month, day = _extract_date_parts(message)

    return Record(
        item_type=_TYPE_MAP.get(message.get("type", ""), "journal_article"),
        title=titles[0] if titles else None,
        authors=tuple(_person_from_author(a) for a in message.get("author") or []),
        year=year,
        month=month,
        day=day,
        container_title=container_titles[0] if container_titles else None,
        volume=message.get("volume"),
        issue=message.get("issue"),
        pages=message.get("page"),
        publisher=message.get("publisher"),
        doi=message.get("DOI"),
        issn=issns[0] if issns else None,
        url=message.get("URL"),
        abstract=_strip_jats(message.get("abstract")),
    )


def _person_from_author(author: dict) -> Person:
    family = author.get("family")
    given = author.get("given")
    if family or given:
        return Person(family=family, given=given)
    # 机构作者（比如临床试验协作组）通常只有 "name" 字段，没有 given/family 拆分。
    name = author.get("name")
    return Person(literal=name or "")


def _extract_date_parts(message: dict) -> tuple[int | None, int | None, int | None]:
    for key in ("published-print", "published-online", "issued"):
        date_field = message.get(key)
        if not isinstance(date_field, dict):
            continue
        date_parts_list = date_field.get("date-parts")
        if not date_parts_list or not date_parts_list[0]:
            continue
        parts = date_parts_list[0]
        year = parts[0] if len(parts) > 0 else None
        month = parts[1] if len(parts) > 1 else None
        day = parts[2] if len(parts) > 2 else None
        return year, month, day
    return None, None, None


def _strip_jats(abstract: str | None) -> str | None:
    """Crossref 的 abstract 字段常带 JATS 标签（`<jats:p>...</jats:p>`），
    这里只做最基础的标签剥离，不做完整 XML 解析——够用，不是严谨的 JATS
    解析器，见本模块 README 的裁剪范围说明。"""
    if abstract is None:
        return None
    return _JATS_TAG_RE.sub("", abstract).strip() or None
