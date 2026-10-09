import httpx
import pytest

from domain.connections import ConnectionNotFound, create_connection, get_connection
from features.connection_setup import (
    UnknownSource,
    WrongKind,
    check_connection,
    create_source_credential,
    delete_source_credential,
    list_source_credentials,
    rotate_api_key,
    set_default_source_credential,
    update_source_credential,
)

ACCOUNT = 1

_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731

_CROSSREF_OK_MESSAGE = {
    "DOI": "10.1037/0003-066x.59.1.29",
    "type": "journal-article",
    "title": ["A classic paper"],
}

_PUBMED_PROBE_PMID = "30049270"


def _crossref_transport(status_code: int = 200, seen: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.update(dict(request.url.params))
        if status_code == 404:
            return httpx.Response(404, content=b"not found")
        return httpx.Response(status_code, json={"status": "ok", "message": _CROSSREF_OK_MESSAGE})

    return httpx.MockTransport(handler)


def _pubmed_transport(status_code: int = 200, seen: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.update(dict(request.url.params))
        if status_code != 200:
            return httpx.Response(status_code, json={})
        summary = {"uid": _PUBMED_PROBE_PMID, "title": "probe"}
        payload = {"result": {"uids": [_PUBMED_PROBE_PMID], _PUBMED_PROBE_PMID: summary}}
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


# ── create_source_credential ──────────────────────────────────────────────


def test_create_crossref_credential_stores_mailto_in_plain_config(db):
    connection = create_source_credential(
        db, account_id=ACCOUNT, source="crossref", name="我的 Crossref", mailto="me@example.org"
    )

    assert connection.kind == "source_credential"
    assert connection.config == {"source": "crossref", "mailto": "me@example.org"}
    assert connection.has_secret is False


def test_create_pubmed_credential_encrypts_api_key(db):
    connection = create_source_credential(
        db, account_id=ACCOUNT, source="pubmed", name="我的 PubMed", api_key="secret-123"
    )

    assert connection.config == {"source": "pubmed"}
    assert connection.has_secret is True


def test_create_crossref_credential_rejects_api_key(db):
    with pytest.raises(ValueError):
        create_source_credential(
            db, account_id=ACCOUNT, source="crossref", name="x", api_key="should-not-be-allowed"
        )


def test_create_pubmed_credential_rejects_mailto(db):
    with pytest.raises(ValueError):
        create_source_credential(db, account_id=ACCOUNT, source="pubmed", name="x", mailto="a@b.com")


def test_create_source_credential_rejects_unknown_source(db):
    with pytest.raises(UnknownSource):
        create_source_credential(db, account_id=ACCOUNT, source="arxiv", name="x")


# ── list_source_credentials ─────────────────────────────────────────────


def test_list_source_credentials_excludes_other_kinds(db):
    create_source_credential(db, account_id=ACCOUNT, source="crossref", name="Crossref")
    create_connection(db, account_id=ACCOUNT, kind="ai_profile", name="不归这个模块管")

    found = list_source_credentials(db, account_id=ACCOUNT)
    assert [c.name for c in found] == ["Crossref"]


# ── update_source_credential ────────────────────────────────────────────


def test_update_source_credential_changes_mailto_for_crossref(db):
    connection = create_source_credential(db, account_id=ACCOUNT, source="crossref", name="x")

    updated = update_source_credential(db, connection.id, mailto="new@example.org")

    assert updated.config["mailto"] == "new@example.org"


def test_update_source_credential_rejects_mailto_for_pubmed(db):
    connection = create_source_credential(db, account_id=ACCOUNT, source="pubmed", name="x")

    with pytest.raises(ValueError):
        update_source_credential(db, connection.id, mailto="a@b.com")


def test_update_source_credential_rejects_wrong_kind(db):
    other = create_connection(db, account_id=ACCOUNT, kind="ai_profile", name="x")

    with pytest.raises(WrongKind):
        update_source_credential(db, other.id, name="new name")


# ── rotate_api_key ───────────────────────────────────────────────────────


def test_rotate_api_key_on_pubmed_connection(db):
    connection = create_source_credential(db, account_id=ACCOUNT, source="pubmed", name="x")
    assert connection.has_secret is False

    updated = rotate_api_key(db, connection.id, api_key="brand-new-key")
    assert updated.has_secret is True


def test_rotate_api_key_rejects_crossref_connection(db):
    connection = create_source_credential(db, account_id=ACCOUNT, source="crossref", name="x")

    with pytest.raises(ValueError):
        rotate_api_key(db, connection.id, api_key="nope")


def test_rotate_api_key_rejects_wrong_kind(db):
    other = create_connection(db, account_id=ACCOUNT, kind="ai_profile", name="x")

    with pytest.raises(WrongKind):
        rotate_api_key(db, other.id, api_key="nope")


# ── delete / set_default ────────────────────────────────────────────────


def test_delete_source_credential(db):
    connection = create_source_credential(db, account_id=ACCOUNT, source="crossref", name="x")

    delete_source_credential(db, connection.id)

    with pytest.raises(ConnectionNotFound):
        get_connection(db, connection.id)


def test_delete_source_credential_rejects_wrong_kind(db):
    other = create_connection(db, account_id=ACCOUNT, kind="ai_profile", name="x")

    with pytest.raises(WrongKind):
        delete_source_credential(db, other.id)


def test_set_default_source_credential_demotes_previous_default(db):
    first = create_source_credential(
        db, account_id=ACCOUNT, source="crossref", name="第一个", is_default=True
    )
    second = create_source_credential(db, account_id=ACCOUNT, source="crossref", name="第二个")

    set_default_source_credential(db, second.id)

    assert get_connection(db, first.id).is_default is False
    assert get_connection(db, second.id).is_default is True


# ── check_connection ──────────────────────────────────────────────────────


def test_check_connection_crossref_success_updates_last_check(db):
    connection = create_source_credential(
        db, account_id=ACCOUNT, source="crossref", name="x", mailto="me@example.org"
    )

    updated = check_connection(db, connection.id, transport=_crossref_transport(), resolve=_FAKE_RESOLVE)

    assert updated.last_check_ok is True
    assert updated.last_checked_at is not None


def test_check_connection_crossref_failure_records_message(db):
    connection = create_source_credential(db, account_id=ACCOUNT, source="crossref", name="x")

    updated = check_connection(
        db, connection.id, transport=_crossref_transport(status_code=404), resolve=_FAKE_RESOLVE
    )

    assert updated.last_check_ok is False
    assert updated.last_check_message


def test_check_connection_pubmed_passes_decrypted_api_key_to_adapter(db):
    connection = create_source_credential(
        db, account_id=ACCOUNT, source="pubmed", name="x", api_key="my-real-key"
    )
    seen: dict = {}

    updated = check_connection(
        db, connection.id, transport=_pubmed_transport(seen=seen), resolve=_FAKE_RESOLVE
    )

    assert updated.last_check_ok is True
    assert seen.get("api_key") == "my-real-key"


def test_check_connection_pubmed_without_api_key_still_checkable(db):
    connection = create_source_credential(db, account_id=ACCOUNT, source="pubmed", name="x")

    updated = check_connection(db, connection.id, transport=_pubmed_transport(), resolve=_FAKE_RESOLVE)

    assert updated.last_check_ok is True


def test_check_connection_rejects_wrong_kind(db):
    other = create_connection(db, account_id=ACCOUNT, kind="ai_profile", name="x")

    with pytest.raises(WrongKind):
        check_connection(db, other.id)
