from urllib.parse import quote

import httpx
import pytest

from caps.secrets import derive_key
from domain.connections import create_connection
from domain.libraries import create_library
from domain.works import add_identifier, create_work, list_provenance
from features.metadata_lookup import (
    MetadataLookupFailed,
    lookup_record,
    lookup_records,
    refresh_work_metadata,
)
from infra.config import settings

ACCOUNT = 1

# 和 `features/metadata_lookup/service.py` 里 `_connections_secret_key()` 用
# 的是同一个推导方式——这是本模块定下的约定：任何要造一个"这个 feature 能解密"
# 的 connection 的测试，都必须用这把派生出来的密钥去加密，不能随手
# `caps.secrets.generate_key()` 另造一把（那是 `domain/connections` 自己
# 单测的做法，它不关心密钥从哪来；这里关心，因为这正是在测"密钥从哪来"这条
# 约定本身）。
CONNECTIONS_KEY = derive_key(
    settings().app_secret_key, salt=settings().app_secret_salt, purpose="connections"
)

CROSSREF_MESSAGE = {
    "DOI": "10.1038/s41586-019-1234-5",
    "type": "journal-article",
    "title": ["A Great Paper"],
    "author": [{"family": "Vaswani", "given": "Ashish"}],
    "container-title": ["Nature"],
    "volume": "573",
    "issue": "7774",
    "pages": "100-110",
    "publisher": "Nature Publishing Group",
    "published-print": {"date-parts": [[2019, 9, 12]]},
    "abstract": "<jats:p>We propose a new architecture.</jats:p>",
}


def _crossref_payload(doi: str | None = None) -> dict:
    message = dict(CROSSREF_MESSAGE)
    if doi is not None:
        message["DOI"] = doi
    return {"status": "ok", "message": message}


def _crossref_transport(status_code: int = 200, doi: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, content=b"error")
        return httpx.Response(200, json=_crossref_payload(doi))

    return httpx.MockTransport(handler)


PMID = "31234567"
PUBMED_SUMMARY = {
    "uid": PMID,
    "title": "A randomized trial of something important.",
    "authors": [{"name": "Smith JA"}],
    "pubdate": "2019 Sep 12",
    "fulljournalname": "The New England Journal of Medicine",
    "source": "N Engl J Med",
    "volume": "381",
    "issue": "11",
    "pages": "1000-1010",
}


def _pubmed_transport(status_code: int = 200, found: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, content=b"error")
        if not found:
            return httpx.Response(200, json={"result": {"uids": []}})
        return httpx.Response(200, json={"result": {"uids": [PMID], PMID: PUBMED_SUMMARY}})

    return httpx.MockTransport(handler)


_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731


# ── lookup_record：crossref ────────────────────────────────────────────────


def test_lookup_record_crossref_success(db):
    record = lookup_record(
        db,
        account_id=ACCOUNT,
        source="crossref",
        identifier="10.1038/s41586-019-1234-5",
        transport=_crossref_transport(),
        resolve=_FAKE_RESOLVE,
    )
    assert record is not None
    assert record.title == "A Great Paper"
    assert record.doi == "10.1038/s41586-019-1234-5"


def test_lookup_record_crossref_uses_mailto_from_connection(db):
    create_connection(
        db,
        account_id=ACCOUNT,
        kind="source_credential",
        name="Crossref",
        config={"source": "crossref", "mailto": "researcher@example.org"},
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_crossref_payload())

    lookup_record(
        db,
        account_id=ACCOUNT,
        source="crossref",
        identifier="10.1038/s41586-019-1234-5",
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert seen.get("mailto") == "researcher@example.org"


def test_lookup_record_prefers_default_connection(db):
    create_connection(
        db, account_id=ACCOUNT, kind="source_credential", name="Crossref A",
        config={"source": "crossref", "mailto": "a@example.org"},
    )
    create_connection(
        db, account_id=ACCOUNT, kind="source_credential", name="Crossref B",
        config={"source": "crossref", "mailto": "b@example.org"}, is_default=True,
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_crossref_payload())

    lookup_record(
        db,
        account_id=ACCOUNT,
        source="crossref",
        identifier="10.1038/x",
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert seen.get("mailto") == "b@example.org"


def test_lookup_record_not_found_returns_none(db):
    record = lookup_record(
        db,
        account_id=ACCOUNT,
        source="crossref",
        identifier="10.9999/missing",
        transport=_crossref_transport(status_code=404),
        resolve=_FAKE_RESOLVE,
    )
    assert record is None


def test_lookup_record_wraps_adapter_error(db):
    with pytest.raises(MetadataLookupFailed):
        lookup_record(
            db,
            account_id=ACCOUNT,
            source="crossref",
            identifier="10.1038/whatever",
            transport=_crossref_transport(status_code=500),
            resolve=_FAKE_RESOLVE,
        )


def test_lookup_record_unknown_source_raises(db):
    with pytest.raises(ValueError):
        lookup_record(db, account_id=ACCOUNT, source="not-a-source", identifier="x")


# ── lookup_record：pubmed（api_key 来自加密的 connection）───────────────────


def test_lookup_record_pubmed_success_without_connection(db):
    record = lookup_record(
        db,
        account_id=ACCOUNT,
        source="pubmed",
        identifier=PMID,
        transport=_pubmed_transport(),
        resolve=_FAKE_RESOLVE,
    )
    assert record is not None
    assert record.pmid == PMID
    assert record.container_title == "The New England Journal of Medicine"


def test_lookup_record_pubmed_uses_decrypted_api_key(db):
    create_connection(
        db,
        account_id=ACCOUNT,
        kind="source_credential",
        name="PubMed",
        config={"source": "pubmed"},
        secret_plain="ncbi-real-api-key",
        secret_key=CONNECTIONS_KEY,
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"result": {"uids": [PMID], PMID: PUBMED_SUMMARY}})

    lookup_record(
        db,
        account_id=ACCOUNT,
        source="pubmed",
        identifier=PMID,
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert seen.get("api_key") == "ncbi-real-api-key"


def test_lookup_record_pubmed_not_found_returns_none(db):
    record = lookup_record(
        db,
        account_id=ACCOUNT,
        source="pubmed",
        identifier="99999999",
        transport=_pubmed_transport(found=False),
        resolve=_FAKE_RESOLVE,
    )
    assert record is None


# ── lookup_records：批量，失败隔离 ──────────────────────────────────────────


def test_lookup_records_isolates_per_item_failures(db):
    good_doi = "10.1038/good"
    bad_doi = "10.1038/bad"
    good_path = quote(good_doi, safe="").encode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        if good_path in bytes(request.url.raw_path):
            return httpx.Response(200, json=_crossref_payload(good_doi))
        return httpx.Response(500, content=b"boom")

    outcomes = lookup_records(
        db,
        account_id=ACCOUNT,
        source="crossref",
        identifiers=[good_doi, bad_doi],
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )
    assert outcomes[good_doi].error is None
    assert outcomes[good_doi].record.doi == good_doi
    assert outcomes[bad_doi].record is None
    assert outcomes[bad_doi].error is not None


# ── refresh_work_metadata ───────────────────────────────────────────────────


def test_refresh_work_metadata_fills_missing_fields_and_records_provenance(db, library_id):
    work = create_work(db, library_id=library_id, title=None)

    updated = refresh_work_metadata(
        db,
        account_id=ACCOUNT,
        library_id=library_id,
        work_id=work.id,
        source="crossref",
        identifier="10.1038/s41586-019-1234-5",
        transport=_crossref_transport(),
        resolve=_FAKE_RESOLVE,
    )

    assert updated.title == "A Great Paper"
    assert updated.container_title == "Nature"

    provenance = list_provenance(db, work.id)
    assert len(provenance) == 1
    assert provenance[0].source == "crossref"
    assert provenance[0].source_id == "10.1038/s41586-019-1234-5"
    assert provenance[0].payload["title"] == "A Great Paper"


def test_refresh_work_metadata_does_not_overwrite_existing_fields(db, library_id):
    work = create_work(db, library_id=library_id, title="我自己的标题")

    updated = refresh_work_metadata(
        db,
        account_id=ACCOUNT,
        library_id=library_id,
        work_id=work.id,
        source="crossref",
        identifier="10.1038/s41586-019-1234-5",
        transport=_crossref_transport(),
        resolve=_FAKE_RESOLVE,
    )
    assert updated.title == "我自己的标题"
    assert updated.container_title == "Nature"


def test_refresh_work_metadata_falls_back_to_existing_identifier(db, library_id):
    work = create_work(db, library_id=library_id)
    add_identifier(
        db, library_id=library_id, work_id=work.id, scheme="doi", value="10.1038/s41586-019-1234-5"
    )

    updated = refresh_work_metadata(
        db,
        account_id=ACCOUNT,
        library_id=library_id,
        work_id=work.id,
        source="crossref",
        transport=_crossref_transport(),
        resolve=_FAKE_RESOLVE,
    )
    assert updated.title == "A Great Paper"


def test_refresh_work_metadata_raises_without_identifier(db, library_id):
    work = create_work(db, library_id=library_id)
    with pytest.raises(ValueError):
        refresh_work_metadata(
            db, account_id=ACCOUNT, library_id=library_id, work_id=work.id, source="crossref"
        )


def test_refresh_work_metadata_rejects_cross_library_work(db, library_id):
    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    work = create_work(db, library_id=other_library_id)
    with pytest.raises(ValueError):
        refresh_work_metadata(
            db,
            account_id=ACCOUNT,
            library_id=library_id,
            work_id=work.id,
            source="crossref",
            identifier="10.1038/x",
        )


def test_refresh_work_metadata_not_found_leaves_work_unchanged(db, library_id):
    work = create_work(db, library_id=library_id, title=None)

    updated = refresh_work_metadata(
        db,
        account_id=ACCOUNT,
        library_id=library_id,
        work_id=work.id,
        source="crossref",
        identifier="10.9999/missing",
        transport=_crossref_transport(status_code=404),
        resolve=_FAKE_RESOLVE,
    )
    assert updated.title is None
    assert list_provenance(db, work.id) == []
