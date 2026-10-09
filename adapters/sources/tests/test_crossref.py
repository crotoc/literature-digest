import httpx
import pytest

from adapters.sources.crossref import CrossrefError, check, lookup_by_doi
from caps.bibformats import Person

# api.crossref.org 真实指向公网 IP，但单测不该依赖真实 DNS 查询——用一个固定
# IP 字面量当 resolve() 的返回值，和 caps/httpfetch 自己的测试用同一招。
_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731


def _transport(status_code: int, body: dict | bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, dict):
            return httpx.Response(status_code, json=body)
        return httpx.Response(status_code, content=body)

    return httpx.MockTransport(handler)


CROSSREF_MESSAGE = {
    "DOI": "10.1038/s41586-019-1234-5",
    "type": "journal-article",
    "title": ["Attention Is All You Need Revisited"],
    "author": [
        {"family": "Vaswani", "given": "Ashish"},
        {"family": "Shazeer", "given": "Noam"},
    ],
    "container-title": ["Nature"],
    "volume": "573",
    "issue": "7774",
    "page": "100-110",
    "publisher": "Nature Publishing Group",
    "ISSN": ["0028-0836", "1476-4687"],
    "URL": "https://doi.org/10.1038/s41586-019-1234-5",
    "published-print": {"date-parts": [[2019, 9, 12]]},
    "abstract": "<jats:p>We propose a new architecture.</jats:p>",
}


def test_lookup_by_doi_parses_full_record():
    transport = _transport(200, {"status": "ok", "message": CROSSREF_MESSAGE})
    record = lookup_by_doi(
        "10.1038/s41586-019-1234-5", transport=transport, resolve=_FAKE_RESOLVE
    )

    assert record is not None
    assert record.item_type == "journal_article"
    assert record.title == "Attention Is All You Need Revisited"
    assert record.authors == (
        Person(family="Vaswani", given="Ashish"),
        Person(family="Shazeer", given="Noam"),
    )
    assert record.year == 2019
    assert record.month == 9
    assert record.day == 12
    assert record.container_title == "Nature"
    assert record.volume == "573"
    assert record.issue == "7774"
    assert record.pages == "100-110"
    assert record.publisher == "Nature Publishing Group"
    assert record.doi == "10.1038/s41586-019-1234-5"
    assert record.issn == "0028-0836"
    assert record.abstract == "We propose a new architecture."


def test_lookup_by_doi_returns_none_on_404():
    transport = _transport(404, b"not found")
    record = lookup_by_doi("10.9999/does-not-exist", transport=transport, resolve=_FAKE_RESOLVE)
    assert record is None


def test_lookup_by_doi_raises_on_server_error():
    transport = _transport(500, b"internal error")
    with pytest.raises(CrossrefError):
        lookup_by_doi("10.1038/whatever", transport=transport, resolve=_FAKE_RESOLVE)


def test_lookup_by_doi_maps_proceedings_type():
    message = {**CROSSREF_MESSAGE, "type": "proceedings-article"}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/conf", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.item_type == "conference_paper"


def test_lookup_by_doi_unknown_type_defaults_to_journal_article():
    message = {**CROSSREF_MESSAGE, "type": "some-new-crossref-type"}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/x", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.item_type == "journal_article"


def test_lookup_by_doi_institutional_author_without_family_given():
    message = {**CROSSREF_MESSAGE, "author": [{"name": "The Collaboration Group"}]}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/collab", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.authors[0].literal == "The Collaboration Group"
    assert record.authors[0].family is None


def test_lookup_by_doi_falls_back_to_published_online_date():
    message = dict(CROSSREF_MESSAGE)
    del message["published-print"]
    message["published-online"] = {"date-parts": [[2020, 1]]}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/online", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.year == 2020
    assert record.month == 1
    assert record.day is None


def test_lookup_by_doi_no_date_fields_at_all():
    message = {k: v for k, v in CROSSREF_MESSAGE.items() if "published" not in k and k != "issued"}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/nodate", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.year is None
    assert record.month is None
    assert record.day is None


def test_lookup_by_doi_missing_abstract():
    message = {k: v for k, v in CROSSREF_MESSAGE.items() if k != "abstract"}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/noabstract", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.abstract is None


def test_lookup_by_doi_sends_mailto_param():
    seen_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json={"status": "ok", "message": CROSSREF_MESSAGE})

    lookup_by_doi(
        "10.1038/x",
        mailto="researcher@example.org",
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert seen_params.get("mailto") == "researcher@example.org"


def test_lookup_by_doi_missing_message_field_raises():
    transport = _transport(200, {"status": "ok"})
    with pytest.raises(CrossrefError):
        lookup_by_doi("10.1038/x", transport=transport, resolve=_FAKE_RESOLVE)


def test_lookup_by_doi_url_encodes_doi():
    seen_paths = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # `.path` 是 httpx 解码后给人看的属性，验证"有没有被编码"要看
        # `.raw_path`（原始字节，保留 %2F，不会被解码回 "/"）。
        seen_paths["raw_path"] = bytes(request.url.raw_path)
        return httpx.Response(200, json={"status": "ok", "message": CROSSREF_MESSAGE})

    # DOI 本身含 "/"，必须被编码成 %2F，不能被当成多段路径
    lookup_by_doi(
        "10.1038/s41586-019-1234-5",
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert b"%2F" in seen_paths["raw_path"]


def test_lookup_by_doi_no_title():
    message = {k: v for k, v in CROSSREF_MESSAGE.items() if k != "title"}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/notitle", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.title is None


def test_lookup_by_doi_no_authors():
    message = {k: v for k, v in CROSSREF_MESSAGE.items() if k != "author"}
    transport = _transport(200, {"status": "ok", "message": message})
    record = lookup_by_doi("10.1038/noauthor", transport=transport, resolve=_FAKE_RESOLVE)
    assert record.authors == ()


def test_check_ok_without_mailto():
    transport = _transport(200, {"status": "ok", "message": CROSSREF_MESSAGE})
    result = check(None, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is True


def test_check_ok_passes_mailto_through():
    seen_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json={"status": "ok", "message": CROSSREF_MESSAGE})

    result = check(
        {"mailto": "researcher@example.org"},
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert result.ok is True
    assert seen_params.get("mailto") == "researcher@example.org"


def test_check_fails_when_probe_doi_not_found():
    transport = _transport(404, b"not found")
    result = check(None, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is False


def test_check_fails_on_server_error():
    transport = _transport(500, b"internal error")
    result = check(None, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is False
