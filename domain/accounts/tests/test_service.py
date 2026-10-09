from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from caps.authn import PasswordParams
from domain.accounts.models import Account, AccountSession, PasswordReset
from domain.accounts.service import (
    AccountNotFound,
    EmailTaken,
    InvalidCredentials,
    PasswordResetExpired,
    PasswordResetInvalid,
    PasswordResetUsed,
    SessionNotFound,
    SessionRevoked,
    TokenNotFound,
    TokenRevoked,
    UsernameTaken,
    consume_password_reset,
    create_account,
    create_api_token,
    create_session,
    get_account,
    get_session,
    list_api_tokens,
    list_sessions,
    request_password_reset,
    revoke_api_token,
    revoke_session,
    verify_api_token,
    verify_password,
)

_WEAK_PARAMS = PasswordParams(time_cost=1, memory_cost=8, parallelism=1)


def _utcnow() -> datetime:
    """和 service.py 存的朴素 UTC 时间保持同一种形状，否则测试里直接写
    带时区的 datetime 去比较/赋值会在跟 DB 读出来的朴素时间比较时报
    aware/naive 混用的 TypeError。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _make_account(db, *, username="alice", email="alice@example.com", password="correct horse"):
    return create_account(db, username=username, email=email, password=password)


# ── create_account / get_account ──────────────────────────────────────────


def test_create_account_returns_dto(db):
    account = _make_account(db)
    assert account.username == "alice"
    assert account.email == "alice@example.com"
    assert isinstance(account.id, int)
    assert isinstance(account.created_at, datetime)


def test_create_account_strips_and_lowercases_email(db):
    account = create_account(db, username="  bob  ", email="  BOB@Example.COM  ", password="x" * 12)
    assert account.username == "bob"
    assert account.email == "bob@example.com"


def test_create_account_duplicate_username_raises(db):
    _make_account(db)
    with pytest.raises(UsernameTaken):
        create_account(db, username="alice", email="other@example.com", password="x" * 12)


def test_create_account_duplicate_email_case_insensitive_raises(db):
    _make_account(db, email="alice@example.com")
    with pytest.raises(EmailTaken):
        create_account(db, username="other", email="ALICE@EXAMPLE.COM", password="x" * 12)


def test_get_account_returns_dto(db):
    created = _make_account(db)
    fetched = get_account(db, created.id)
    assert fetched == created


def test_get_account_missing_raises(db):
    with pytest.raises(AccountNotFound):
        get_account(db, 999)


# ── verify_password ───────────────────────────────────────────────────────


def test_verify_password_by_username_ok(db):
    _make_account(db, password="correct horse")
    account = verify_password(db, username_or_email="alice", password="correct horse")
    assert account.username == "alice"


def test_verify_password_by_email_ok(db):
    _make_account(db, email="alice@example.com", password="correct horse")
    account = verify_password(db, username_or_email="ALICE@example.com", password="correct horse")
    assert account.username == "alice"


def test_verify_password_wrong_password_raises(db):
    _make_account(db, password="correct horse")
    with pytest.raises(InvalidCredentials):
        verify_password(db, username_or_email="alice", password="wrong")


def test_verify_password_unknown_identifier_raises(db):
    with pytest.raises(InvalidCredentials):
        verify_password(db, username_or_email="nobody", password="whatever")


def test_verify_password_upgrades_weak_hash(db):
    account = create_account(db, username="alice", email="a@example.com", password="correct horse",
                              password_params=_WEAK_PARAMS)
    old_hash = db.get(Account, account.id).password_hash

    verify_password(db, username_or_email="alice", password="correct horse")

    new_hash = db.get(Account, account.id).password_hash
    assert new_hash != old_hash


# ── 会话 ─────────────────────────────────────────────────────────────────


def test_create_session_not_revoked(db):
    account = _make_account(db)
    session = create_session(db, account.id)
    assert session.account_id == account.id
    assert session.revoked is False


def test_get_session_touches_last_seen_at(db):
    account = _make_account(db)
    session = create_session(db, account.id)

    row = db.get(AccountSession, session.id)
    stale = _utcnow() - timedelta(hours=1)
    row.last_seen_at = stale
    db.flush()

    refreshed = get_session(db, session.id)
    assert refreshed.last_seen_at > stale


def test_get_session_missing_raises(db):
    with pytest.raises(SessionNotFound):
        get_session(db, 999)


def test_get_session_revoked_raises(db):
    account = _make_account(db)
    session = create_session(db, account.id)
    revoke_session(db, session.id)
    with pytest.raises(SessionRevoked):
        get_session(db, session.id)


def test_revoke_session_idempotent(db):
    account = _make_account(db)
    session = create_session(db, account.id)
    revoke_session(db, session.id)
    revoke_session(db, session.id)  # 不该报错


def test_revoke_session_missing_raises(db):
    with pytest.raises(SessionNotFound):
        revoke_session(db, 999)


def test_list_sessions_ordered_newest_first(db):
    account = _make_account(db)
    first = create_session(db, account.id)
    second = create_session(db, account.id)
    sessions = list_sessions(db, account.id)
    assert [s.id for s in sessions] == [second.id, first.id]


# ── API token ──────────────────────────────────────────────────────────────


def test_create_api_token_returns_dto_and_plaintext(db):
    account = _make_account(db)
    token, plaintext = create_api_token(db, account.id, name="laptop")
    assert token.name == "laptop"
    assert token.revoked is False
    assert plaintext.startswith("ld_")


def test_verify_api_token_ok_updates_last_used_at(db):
    account = _make_account(db)
    token, plaintext = create_api_token(db, account.id)
    assert token.last_used_at is None

    verified = verify_api_token(db, plaintext)
    assert verified.id == token.id
    assert verified.last_used_at is not None


def test_verify_api_token_missing_raises(db):
    with pytest.raises(TokenNotFound):
        verify_api_token(db, "ld_nonexistent-prefix-value")


def test_verify_api_token_revoked_raises(db):
    account = _make_account(db)
    token, plaintext = create_api_token(db, account.id)
    revoke_api_token(db, token.id, account_id=account.id)
    with pytest.raises(TokenRevoked):
        verify_api_token(db, plaintext)


def test_verify_api_token_tampered_suffix_raises(db):
    account = _make_account(db)
    _token, plaintext = create_api_token(db, account.id)
    tampered = plaintext[:-1] + ("x" if plaintext[-1] != "x" else "y")
    with pytest.raises(InvalidCredentials):
        verify_api_token(db, tampered)


def test_revoke_api_token_wrong_account_raises_not_found(db):
    account = _make_account(db)
    other = create_account(db, username="mallory", email="mallory@example.com", password="x" * 12)
    token, _plaintext = create_api_token(db, account.id)
    with pytest.raises(TokenNotFound):
        revoke_api_token(db, token.id, account_id=other.id)


def test_revoke_api_token_idempotent(db):
    account = _make_account(db)
    token, _plaintext = create_api_token(db, account.id)
    revoke_api_token(db, token.id, account_id=account.id)
    revoke_api_token(db, token.id, account_id=account.id)  # 不该报错


def test_list_api_tokens_ordered_newest_first(db):
    account = _make_account(db)
    first, _ = create_api_token(db, account.id, name="first")
    second, _ = create_api_token(db, account.id, name="second")
    tokens = list_api_tokens(db, account.id)
    assert [t.id for t in tokens] == [second.id, first.id]


# ── 密码重置 ───────────────────────────────────────────────────────────────


def test_request_password_reset_returns_plaintext(db):
    _make_account(db, email="alice@example.com")
    result = request_password_reset(db, email="ALICE@example.com")
    assert result is not None
    account, plaintext = result
    assert account.email == "alice@example.com"
    assert plaintext.startswith("pwdrst_")


def test_request_password_reset_unknown_email_returns_none(db):
    assert request_password_reset(db, email="nobody@example.com") is None


def test_request_password_reset_rejects_non_positive_ttl(db):
    _make_account(db)
    with pytest.raises(ValueError):
        request_password_reset(db, email="alice@example.com", ttl_seconds=0)


def test_consume_password_reset_changes_password(db):
    account = _make_account(db, password="old password")
    old_hash = db.get(Account, account.id).password_hash
    _account, plaintext = request_password_reset(db, email="alice@example.com")

    updated = consume_password_reset(db, plaintext=plaintext, new_password="new password")

    assert updated.id == account.id
    new_hash = db.get(Account, account.id).password_hash
    assert new_hash != old_hash
    verify_password(db, username_or_email="alice", password="new password")


def test_consume_password_reset_invalid_token_raises(db):
    _make_account(db)
    with pytest.raises(PasswordResetInvalid):
        consume_password_reset(db, plaintext="pwdrst_doesnotexist", new_password="x" * 12)


def test_consume_password_reset_used_twice_raises(db):
    _make_account(db)
    _account, plaintext = request_password_reset(db, email="alice@example.com")
    consume_password_reset(db, plaintext=plaintext, new_password="x" * 12)
    with pytest.raises(PasswordResetUsed):
        consume_password_reset(db, plaintext=plaintext, new_password="y" * 12)


def test_consume_password_reset_expired_raises(db):
    _make_account(db)
    _account, plaintext = request_password_reset(db, email="alice@example.com")

    row = db.scalar(select(PasswordReset))
    row.expires_at = _utcnow() - timedelta(seconds=1)
    db.flush()

    with pytest.raises(PasswordResetExpired):
        consume_password_reset(db, plaintext=plaintext, new_password="x" * 12)
