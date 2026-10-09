import time

import pytest

from caps.authn import TokenExpired, TokenInvalid
from domain.accounts import (
    EmailTaken,
    InvalidCredentials,
    PasswordResetExpired,
    PasswordResetInvalid,
    PasswordResetUsed,
    SessionRevoked,
    UsernameTaken,
)
from features.accounts_auth.service import (
    EmailInvalid,
    PasswordResetUnavailable,
    PasswordTooShort,
    RegistrationDisabled,
    UsernameTooShort,
    consume_password_reset,
    login,
    logout,
    register,
    request_password_reset,
    resume_session,
)

SECRET = "unit-test-secret-key"
PASSWORD = "correct horse battery"  # 19 字符，满足 >=12


def _register(db, username="alice", email="alice@example.org", password=PASSWORD):
    return register(db, username=username, email=email, password=password, allow_self_signup=True)


# ── register ───────────────────────────────────────────────────────────────


def test_register_creates_account_and_personal_library(db):
    result = _register(db)
    assert result.account.username == "alice"
    assert result.library.name == "alice 的文库"


def test_register_disabled_when_self_signup_off(db):
    with pytest.raises(RegistrationDisabled):
        register(db, username="alice", email="a@b.com", password=PASSWORD, allow_self_signup=False)


def test_register_rejects_short_username(db):
    with pytest.raises(UsernameTooShort):
        register(db, username="ab", email="a@b.com", password=PASSWORD, allow_self_signup=True)


def test_register_rejects_short_password(db):
    with pytest.raises(PasswordTooShort):
        register(db, username="alice", email="a@b.com", password="short1234", allow_self_signup=True)


def test_register_rejects_email_without_at(db):
    with pytest.raises(EmailInvalid):
        register(db, username="alice", email="not-an-email", password=PASSWORD, allow_self_signup=True)


def test_register_duplicate_username_raises(db):
    _register(db, username="alice", email="a1@example.org")
    with pytest.raises(UsernameTaken):
        _register(db, username="alice", email="a2@example.org")


def test_register_duplicate_email_raises(db):
    _register(db, username="alice", email="dup@example.org")
    with pytest.raises(EmailTaken):
        _register(db, username="bob", email="dup@example.org")


# ── login ──────────────────────────────────────────────────────────────────


def test_login_returns_account_session_and_signed_cookie(db):
    _register(db)
    result = login(db, username_or_email="alice", password=PASSWORD, session_secret=SECRET)
    assert result.account.username == "alice"
    assert result.session.account_id == result.account.id
    assert isinstance(result.cookie_token, str) and result.cookie_token


def test_login_wrong_password_raises_invalid_credentials(db):
    _register(db)
    with pytest.raises(InvalidCredentials):
        login(db, username_or_email="alice", password="wrong password here", session_secret=SECRET)


def test_login_unknown_user_raises_same_invalid_credentials(db):
    with pytest.raises(InvalidCredentials):
        login(db, username_or_email="ghost", password=PASSWORD, session_secret=SECRET)


# ── resume_session ───────────────────────────────────────────────────────────


def test_resume_session_recovers_account_from_cookie(db):
    _register(db)
    result = login(db, username_or_email="alice", password=PASSWORD, session_secret=SECRET)
    account = resume_session(
        db, cookie_token=result.cookie_token, session_secret=SECRET, max_age_seconds=3600
    )
    assert account.id == result.account.id


def test_resume_session_rejects_garbage_token(db):
    with pytest.raises(TokenInvalid):
        resume_session(db, cookie_token="garbage", session_secret=SECRET, max_age_seconds=3600)


def test_resume_session_rejects_wrong_secret(db):
    _register(db)
    result = login(db, username_or_email="alice", password=PASSWORD, session_secret=SECRET)
    with pytest.raises(TokenInvalid):
        resume_session(
            db, cookie_token=result.cookie_token, session_secret="a-different-secret", max_age_seconds=3600
        )


def test_resume_session_rejects_expired_token(db):
    _register(db)
    result = login(db, username_or_email="alice", password=PASSWORD, session_secret=SECRET)
    # itsdangerous 的时间戳按整秒截断（`int(time.time())`），签名那一刻的时间戳
    # 可能比真实时间早将近 1 秒——sleep 必须明显超过 max_age + 1 秒才能稳定触发
    # 过期，不然在秒边界附近跑会偶发 flaky。
    time.sleep(2.5)
    with pytest.raises(TokenExpired):
        resume_session(db, cookie_token=result.cookie_token, session_secret=SECRET, max_age_seconds=1)


def test_resume_session_rejects_revoked_session(db):
    _register(db)
    result = login(db, username_or_email="alice", password=PASSWORD, session_secret=SECRET)
    logout(db, session_id=result.session.id)
    with pytest.raises(SessionRevoked):
        resume_session(
            db, cookie_token=result.cookie_token, session_secret=SECRET, max_age_seconds=3600
        )


def test_logout_is_idempotent(db):
    _register(db)
    result = login(db, username_or_email="alice", password=PASSWORD, session_secret=SECRET)
    logout(db, session_id=result.session.id)
    logout(db, session_id=result.session.id)  # 不该报错


# ── password reset ─────────────────────────────────────────────────────────


def test_request_password_reset_unavailable_when_smtp_not_configured(db):
    _register(db)
    with pytest.raises(PasswordResetUnavailable):
        request_password_reset(db, email="alice@example.org", smtp_configured=False)


def test_request_password_reset_unknown_email_returns_none(db):
    result = request_password_reset(db, email="ghost@example.org", smtp_configured=True)
    assert result is None


def test_request_password_reset_known_email_returns_token(db):
    _register(db)
    result = request_password_reset(db, email="alice@example.org", smtp_configured=True)
    assert result is not None
    account, plaintext = result
    assert account.username == "alice"
    assert isinstance(plaintext, str) and plaintext


def test_consume_password_reset_rejects_short_new_password(db):
    _register(db)
    _account, plaintext = request_password_reset(db, email="alice@example.org", smtp_configured=True)
    with pytest.raises(PasswordTooShort):
        consume_password_reset(db, plaintext=plaintext, new_password="short")


def test_consume_password_reset_changes_password_and_login_works(db):
    _register(db)
    _account, plaintext = request_password_reset(db, email="alice@example.org", smtp_configured=True)
    consume_password_reset(db, plaintext=plaintext, new_password="a brand new password")

    with pytest.raises(InvalidCredentials):
        login(db, username_or_email="alice", password=PASSWORD, session_secret=SECRET)

    result = login(
        db, username_or_email="alice", password="a brand new password", session_secret=SECRET
    )
    assert result.account.username == "alice"


def test_consume_password_reset_invalid_token_raises(db):
    with pytest.raises(PasswordResetInvalid):
        consume_password_reset(db, plaintext="pwdrst_doesnotexist", new_password="a brand new password")


def test_consume_password_reset_used_twice_raises(db):
    _register(db)
    _account, plaintext = request_password_reset(db, email="alice@example.org", smtp_configured=True)
    consume_password_reset(db, plaintext=plaintext, new_password="a brand new password")
    with pytest.raises(PasswordResetUsed):
        consume_password_reset(db, plaintext=plaintext, new_password="yet another password")


def test_consume_password_reset_expired_raises(db):
    _register(db)
    _account, plaintext = request_password_reset(
        db, email="alice@example.org", smtp_configured=True, ttl_seconds=1
    )
    time.sleep(1.1)
    with pytest.raises(PasswordResetExpired):
        consume_password_reset(db, plaintext=plaintext, new_password="a brand new password")
