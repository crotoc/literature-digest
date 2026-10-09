import httpx
import pytest

from adapters.sources.pubmed import PubMedError, check, lookup_by_pmid

_FAKE_RESOLVE = lambda host: ["130.14.29.110"]  # noqa: E731

PMID = "31234567"

PUBMED_SUMMARY = {
    "uid": PMID,
    "title": "A randomized trial of something important.",
    "authors": [{"name": "Smith JA"}, {"name": "Doe RK"}],
    "pubdate": "2019 Sep 12",
    "fulljournalname": "The New England Journal of Medicine",
    "source": "N Engl J Med",
    "volume": "381",
    "issue": "11",
    "pages": "1000-1010",
    "elocationid": "doi: 10.1056/NEJMoa1234567",
}


def _transport(status_code: int, body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def _result_payload(summary: dict | None = None) -> dict:
    if summary is None:
        summary = PUBMED_SUMMARY
    return {"result": {"uids": [PMID], PMID: summary}}


def test_lookup_by_pmid_parses_full_record():
    transport = _transport(200, _result_payload())
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)

    assert record is not None
    assert record.item_type == "journal_article"
    assert record.title == "A randomized trial of something important."
    assert [a.literal for a in record.authors] == ["Smith JA", "Doe RK"]
    assert record.year == 2019
    assert record.month == 9
    assert record.day == 12
    assert record.container_title == "The New England Journal of Medicine"
    assert record.volume == "381"
    assert record.issue == "11"
    assert record.pages == "1000-1010"
    assert record.doi == "10.1056/NEJMoa1234567"
    assert record.pmid == PMID


def test_lookup_by_pmid_returns_none_when_uid_not_in_result():
    transport = _transport(200, {"result": {"uids": []}})
    record = lookup_by_pmid("99999999", transport=transport, resolve=_FAKE_RESOLVE)
    assert record is None


def test_lookup_by_pmid_returns_none_when_summary_has_error():
    payload = {"result": {"uids": [PMID], PMID: {"uid": PMID, "error": "cannot get document summary"}}}
    transport = _transport(200, payload)
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)
    assert record is None


def test_lookup_by_pmid_raises_on_server_error():
    transport = _transport(500, {})
    with pytest.raises(PubMedError):
        lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)


def test_lookup_by_pmid_missing_result_field_raises():
    transport = _transport(200, {"not_result": {}})
    with pytest.raises(PubMedError):
        lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)


def test_lookup_by_pmid_falls_back_to_source_when_no_fulljournalname():
    summary = {k: v for k, v in PUBMED_SUMMARY.items() if k != "fulljournalname"}
    transport = _transport(200, _result_payload(summary))
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)
    assert record.container_title == "N Engl J Med"


def test_lookup_by_pmid_non_doi_elocationid_ignored():
    summary = {**PUBMED_SUMMARY, "elocationid": "pii: S0140673619301543"}
    transport = _transport(200, _result_payload(summary))
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)
    assert record.doi is None


def test_lookup_by_pmid_pubdate_year_only():
    summary = {**PUBMED_SUMMARY, "pubdate": "2019"}
    transport = _transport(200, _result_payload(summary))
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)
    assert record.year == 2019
    assert record.month is None
    assert record.day is None


def test_lookup_by_pmid_pubdate_cross_month_range_takes_first_month():
    summary = {**PUBMED_SUMMARY, "pubdate": "2019 Jan-Feb"}
    transport = _transport(200, _result_payload(summary))
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)
    assert record.month == 1


def test_lookup_by_pmid_missing_pubdate():
    summary = {k: v for k, v in PUBMED_SUMMARY.items() if k != "pubdate"}
    transport = _transport(200, _result_payload(summary))
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)
    assert record.year is None


def test_lookup_by_pmid_sends_api_key_param():
    seen_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json=_result_payload())

    lookup_by_pmid(
        PMID, api_key="secret-key", transport=httpx.MockTransport(handler), resolve=_FAKE_RESOLVE
    )
    assert seen_params.get("api_key") == "secret-key"


def test_lookup_by_pmid_no_authors():
    summary = {k: v for k, v in PUBMED_SUMMARY.items() if k != "authors"}
    transport = _transport(200, _result_payload(summary))
    record = lookup_by_pmid(PMID, transport=transport, resolve=_FAKE_RESOLVE)
    assert record.authors == ()


_PROBE_PMID = "30049270"


def _probe_result_payload() -> dict:
    summary = {**PUBMED_SUMMARY, "uid": _PROBE_PMID}
    return {"result": {"uids": [_PROBE_PMID], _PROBE_PMID: summary}}


def test_check_ok_without_api_key():
    transport = _transport(200, _probe_result_payload())
    result = check(None, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is True


def test_check_ok_passes_api_key_through():
    seen_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json=_probe_result_payload())

    result = check(
        {"api_key": "secret-key"},
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert result.ok is True
    assert seen_params.get("api_key") == "secret-key"


def test_check_fails_when_probe_pmid_not_found():
    transport = _transport(200, {"result": {"uids": []}})
    result = check(None, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is False


def test_check_fails_on_server_error():
    transport = _transport(500, {})
    result = check(None, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is False
