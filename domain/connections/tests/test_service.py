import pytest

from caps.secrets import DecryptFailed, generate_key
from domain.connections.service import (
    ConnectionNotFound,
    clear_secret,
    create_connection,
    delete_connection,
    get_connection,
    get_decrypted_secret,
    list_connections,
    record_check_result,
    rotate_secret,
    set_default_connection,
    update_connection,
)

ACCOUNT = 1
OTHER_ACCOUNT = 2
KEY = generate_key()
OTHER_KEY = generate_key()


def _make_connection(db, *, kind="source_credential", name="Crossref", **kwargs):
    return create_connection(db, account_id=ACCOUNT, kind=kind, name=name, **kwargs)


# ── create_connection / get_connection ────────────────────────────────────


def test_create_connection_rejects_bad_kind(db):
    with pytest.raises(ValueError):
        _make_connection(db, kind="not_a_real_kind")


def test_create_connection_rejects_empty_name(db):
    with pytest.raises(ValueError):
        _make_connection(db, name="   ")


def test_create_connection_without_secret_has_no_secret(db):
    connection = _make_connection(db)
    assert connection.has_secret is False


def test_create_connection_with_secret_requires_key(db):
    with pytest.raises(ValueError):
        _make_connection(db, secret_plain="sk-abc123")


def test_create_connection_with_secret_and_key_ok(db):
    connection = _make_connection(db, secret_plain="sk-abc123", secret_key=KEY)
    assert connection.has_secret is True


def test_create_connection_stores_config(db):
    connection = _make_connection(db, config={"base_url": "https://api.crossref.org"})
    assert connection.config == {"base_url": "https://api.crossref.org"}


def test_create_connection_default_config_is_empty_dict(db):
    connection = _make_connection(db)
    assert connection.config == {}


def test_create_default_connection_demotes_previous_default(db):
    first = _make_connection(db, kind="ai_profile", name="GPT", is_default=True)
    second = _make_connection(db, kind="ai_profile", name="Claude", is_default=True)

    assert get_connection(db, first.id).is_default is False
    assert get_connection(db, second.id).is_default is True


def test_create_default_connection_does_not_affect_other_kind(db):
    ai = _make_connection(db, kind="ai_profile", name="GPT", is_default=True)
    _make_connection(db, kind="telegram_destination", name="Lab channel", is_default=True)

    assert get_connection(db, ai.id).is_default is True


def test_create_default_connection_does_not_affect_other_account(db):
    mine = create_connection(db, account_id=ACCOUNT, kind="ai_profile", name="Mine", is_default=True)
    create_connection(db, account_id=OTHER_ACCOUNT, kind="ai_profile", name="Theirs", is_default=True)

    assert get_connection(db, mine.id).is_default is True


def test_get_connection_missing_raises(db):
    with pytest.raises(ConnectionNotFound):
        get_connection(db, 999)


# ── list_connections ──────────────────────────────────────────────────────


def test_list_connections_scoped_to_account(db):
    _make_connection(db)
    create_connection(db, account_id=OTHER_ACCOUNT, kind="source_credential", name="Not mine")

    connections = list_connections(db, account_id=ACCOUNT)
    assert len(connections) == 1
    assert connections[0].name == "Crossref"


def test_list_connections_filtered_by_kind(db):
    _make_connection(db, kind="ai_profile", name="GPT")
    _make_connection(db, kind="source_credential", name="Crossref")

    ai_only = list_connections(db, account_id=ACCOUNT, kind="ai_profile")
    assert [c.name for c in ai_only] == ["GPT"]


def test_list_connections_without_kind_filter_returns_all(db):
    _make_connection(db, kind="ai_profile", name="GPT")
    _make_connection(db, kind="source_credential", name="Crossref")

    assert len(list_connections(db, account_id=ACCOUNT)) == 2


def test_list_connections_enabled_only(db):
    _make_connection(db, name="Enabled One", enabled=True)
    _make_connection(db, name="Disabled One", enabled=False)

    enabled = list_connections(db, account_id=ACCOUNT, enabled_only=True)
    assert [c.name for c in enabled] == ["Enabled One"]


# ── update_connection ────────────────────────────────────────────────────


def test_update_connection_only_touches_passed_fields(db):
    connection = _make_connection(db, config={"a": 1})
    updated = update_connection(db, connection.id, name="New Name")
    assert updated.name == "New Name"
    assert updated.config == {"a": 1}


def test_update_connection_rejects_empty_name(db):
    connection = _make_connection(db)
    with pytest.raises(ValueError):
        update_connection(db, connection.id, name="   ")


def test_update_connection_can_disable(db):
    connection = _make_connection(db, enabled=True)
    updated = update_connection(db, connection.id, enabled=False)
    assert updated.enabled is False


def test_update_connection_missing_raises(db):
    with pytest.raises(ConnectionNotFound):
        update_connection(db, 999, name="X")


# ── set_default_connection ───────────────────────────────────────────────


def test_set_default_connection_promotes_and_demotes(db):
    old_default = _make_connection(db, kind="ai_profile", name="GPT", is_default=True)
    other = _make_connection(db, kind="ai_profile", name="Claude")

    promoted = set_default_connection(db, other.id)
    assert promoted.is_default is True
    assert get_connection(db, old_default.id).is_default is False


def test_set_default_connection_already_default_is_a_noop(db):
    connection = _make_connection(db, is_default=True)
    result = set_default_connection(db, connection.id)
    assert result.is_default is True


def test_set_default_connection_missing_raises(db):
    with pytest.raises(ConnectionNotFound):
        set_default_connection(db, 999)


# ── delete_connection ────────────────────────────────────────────────────


def test_delete_connection_ok(db):
    connection = _make_connection(db)
    delete_connection(db, connection.id)
    with pytest.raises(ConnectionNotFound):
        get_connection(db, connection.id)


def test_delete_connection_missing_raises(db):
    with pytest.raises(ConnectionNotFound):
        delete_connection(db, 999)


# ── 凭据加解密 ─────────────────────────────────────────────────────────────


def test_get_decrypted_secret_round_trips(db):
    connection = _make_connection(db, secret_plain="sk-abc123", secret_key=KEY)
    assert get_decrypted_secret(db, connection.id, secret_key=KEY) == "sk-abc123"


def test_get_decrypted_secret_none_when_not_stored(db):
    connection = _make_connection(db)
    assert get_decrypted_secret(db, connection.id, secret_key=KEY) is None


def test_get_decrypted_secret_wrong_key_raises(db):
    connection = _make_connection(db, secret_plain="sk-abc123", secret_key=KEY)
    with pytest.raises(DecryptFailed):
        get_decrypted_secret(db, connection.id, secret_key=OTHER_KEY)


def test_rotate_secret_replaces_ciphertext(db):
    connection = _make_connection(db, secret_plain="sk-old", secret_key=KEY)
    rotated = rotate_secret(db, connection.id, secret_plain="sk-new", secret_key=KEY)
    assert rotated.has_secret is True
    assert get_decrypted_secret(db, connection.id, secret_key=KEY) == "sk-new"


def test_clear_secret_removes_it(db):
    connection = _make_connection(db, secret_plain="sk-abc123", secret_key=KEY)
    cleared = clear_secret(db, connection.id)
    assert cleared.has_secret is False
    assert get_decrypted_secret(db, connection.id, secret_key=KEY) is None


# ── record_check_result ──────────────────────────────────────────────────


def test_record_check_result_ok(db):
    connection = _make_connection(db)
    result = record_check_result(db, connection.id, ok=True, message="连接正常")
    assert result.last_check_ok is True
    assert result.last_check_message == "连接正常"
    assert result.last_checked_at is not None


def test_record_check_result_failure(db):
    connection = _make_connection(db)
    result = record_check_result(db, connection.id, ok=False, message="连不上 crossref")
    assert result.last_check_ok is False
    assert result.last_check_message == "连不上 crossref"


def test_record_check_result_before_any_check_is_none(db):
    connection = _make_connection(db)
    assert connection.last_check_ok is None
    assert connection.last_checked_at is None


def test_record_check_result_missing_raises(db):
    with pytest.raises(ConnectionNotFound):
        record_check_result(db, 999, ok=True, message="x")
