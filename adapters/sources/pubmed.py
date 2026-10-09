"""PubMed（NCBI E-utilities `esummary`）的查询适配器：给一个 PMID，查回标准化
的 `caps.bibformats.Record`。

用 `esummary.fcgi` 而不是 `efetch.fcgi`，是因为前者直接给 JSON（`retmode=json`），
不需要在这个适配器里再引入一个 XML 解析依赖；代价是 `esummary` 给的字段比
`efetch` 的完整 XML 记录少（比如作者名只有缩写形式、没有完整的结构化摘要文本），
这个取舍记在本模块 README 的裁剪范围里。

和 `adapters/sources/crossref.py` 一样复用 `Record` 当返回类型，理由同那边的
docstring。真正的 HTTP 请求全部走 `caps/httpfetch.fetch()`。
"""

from __future__ import annotations

import json
import re

from caps.bibformats import Person, Record
from caps.httpfetch import HttpFetchError, RateLimiter, default_resolve, fetch

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

_DOI_PREFIX_RE = re.compile(r"^doi:\s*", re.IGNORECASE)


class PubMedError(Exception):
    """esummary 返回了非预期的错误状态码/响应体，或者 `caps/httpfetch` 本身
    传输失败。"""


def lookup_by_pmid(
    pmid: str,
    *,
    api_key: str | None = None,
    rate_limiter: RateLimiter | None = None,
    transport=None,
    resolve=default_resolve,
) -> Record | None:
    """查一个 PMID。查不到时返回 `None`，不是异常——理由和
    `adapters/sources/crossref.lookup_by_doi` 一致。

    `esummary` 对查不到的 uid 不会用 HTTP 404 表示，而是在响应体里把它放进
    `result.uids` 之外、或者给它一个带 `error` 字段的条目，所以"查不到"这个
    判断要解析响应体才能做，不能只看状态码。

    `api_key` 对应 NCBI 的 API key（没有 key 限速更严），来自调用方从
    `domain/connections` 读出来的配置，本模块不知道它从哪来。

    `resolve`/`transport` 原样转发给 `caps/httpfetch.fetch()`，是测试钩子，
    理由同 `adapters/sources/crossref.lookup_by_doi` 的同名参数说明。
    """
    params = {"db": "pubmed", "id": pmid, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    try:
        result = fetch(
            BASE_URL, params=params, rate_limiter=rate_limiter, transport=transport, resolve=resolve
        )
    except HttpFetchError as exc:
        raise PubMedError(f"查询 PubMed 失败：{pmid}") from exc

    if result.status_code >= 400:
        raise PubMedError(f"PubMed 返回 {result.status_code}：{pmid}")

    payload = json.loads(result.content)
    outer_result = payload.get("result")
    if not isinstance(outer_result, dict):
        raise PubMedError(f"PubMed 响应里没有 result 字段：{pmid}")

    uids = outer_result.get("uids") or []
    if pmid not in uids:
        return None

    summary = outer_result.get(pmid)
    if not isinstance(summary, dict) or "error" in summary:
        return None

    return _record_from_summary(summary)


def _record_from_summary(summary: dict) -> Record:
    year, month, day = _parse_pubdate(summary.get("pubdate"))
    doi = _extract_doi(summary.get("elocationid"))

    return Record(
        item_type="journal_article",
        title=summary.get("title") or None,
        authors=tuple(_person_from_author(a) for a in summary.get("authors") or []),
        year=year,
        month=month,
        day=day,
        container_title=summary.get("fulljournalname") or summary.get("source") or None,
        volume=summary.get("volume") or None,
        issue=summary.get("issue") or None,
        pages=summary.get("pages") or None,
        doi=doi,
        pmid=summary.get("uid") or None,
    )


def _person_from_author(author: dict) -> Person:
    # esummary 给的作者名是 "Smith JA" 这种"姓 + 名字缩写"格式，没有逗号分隔，
    # 没法像 caps.bibformats.parse_person 那样可靠拆成 family/given（那个函数
    # 假设"最后一个词是姓"，用在这里会把姓和缩写拆反）。诚实地整段放进
    # literal，不猜——和 Record.Person 文档里"拆不开就用 literal"的约定一致。
    name = author.get("name") or ""
    return Person(literal=name)


def _parse_pubdate(pubdate: str | None) -> tuple[int | None, int | None, int | None]:
    """`pubdate` 形如 `"2019 Jan 15"` / `"2019 Jan"` / `"2019"` / `"2019 Jan-Feb"`
    （季刊/双月刊常见跨月写法，这里只取起始月）。"""
    if not pubdate:
        return None, None, None
    tokens = pubdate.split()
    year = int(tokens[0]) if tokens and tokens[0].isdigit() else None
    month = None
    day = None
    if len(tokens) > 1:
        month_token = tokens[1].split("-")[0].lower()[:3]
        month = _MONTHS.get(month_token)
    if len(tokens) > 2 and tokens[2].isdigit():
        day = int(tokens[2])
    return year, month, day


def _extract_doi(elocationid: str | None) -> str | None:
    """`elocationid` 形如 `"doi: 10.1038/s41586-019-1234-5"`；有些记录是别的
    标识符类型（比如 `"pii: S0140..."`），不是 DOI 的一律忽略。"""
    if not elocationid:
        return None
    if not elocationid.lower().startswith("doi"):
        return None
    return _DOI_PREFIX_RE.sub("", elocationid).strip() or None
