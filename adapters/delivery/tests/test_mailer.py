import smtplib

import pytest

from adapters.delivery.mailer import MailerError, check, send_message


class _FakeSmtpClient:
    """`smtplib.SMTP`/`smtplib.SMTP_SSL` 的替身：记录被调用过哪些方法 +
    最后一次 `send_message` 收到的消息对象，不发出任何真实网络流量。
    可以配置在某一步抛出异常，模拟认证失败/连接失败。
    """

    calls: list[str] = []
    sent_message = None
    raise_at: str | None = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        type(self).calls.append(f"connect:{host}:{port}")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        type(self).calls.append("starttls")
        if self.raise_at == "starttls":
            raise smtplib.SMTPException("starttls failed")

    def login(self, username, password):
        type(self).calls.append(f"login:{username}")
        if self.raise_at == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, message):
        type(self).calls.append("send_message")
        type(self).sent_message = message
        if self.raise_at == "send":
            raise smtplib.SMTPException("send failed")


def _fresh_fake_client(raise_at: str | None = None):
    _FakeSmtpClient.calls = []
    _FakeSmtpClient.sent_message = None
    _FakeSmtpClient.raise_at = raise_at
    return _FakeSmtpClient


def test_send_message_calls_starttls_login_and_send_in_order():
    fake = _fresh_fake_client()

    send_message(
        host="smtp.example.org", port=587, username="bot", password="secret",
        from_address="bot@example.org", to_address="me@example.org",
        subject="subject-line", body="body-text", use_tls=True, smtp_class=fake,
    )

    assert fake.calls == ["connect:smtp.example.org:587", "starttls", "login:bot", "send_message"]
    assert fake.sent_message["Subject"] == "subject-line"
    assert fake.sent_message["To"] == "me@example.org"
    assert fake.sent_message.get_content().strip() == "body-text"


def test_send_message_skips_starttls_when_use_tls_false():
    fake = _fresh_fake_client()

    send_message(
        host="smtp.example.org", port=465, username="bot", password="secret",
        from_address="bot@example.org", to_address="me@example.org",
        subject="s", body="b", use_tls=False, smtp_class=fake,
    )

    assert "starttls" not in fake.calls
    assert fake.calls == ["connect:smtp.example.org:465", "login:bot", "send_message"]


def test_send_message_skips_login_when_username_blank():
    fake = _fresh_fake_client()

    send_message(
        host="smtp.example.org", port=25, username="", password="",
        from_address="bot@example.org", to_address="me@example.org",
        subject="s", body="b", use_tls=False, smtp_class=fake,
    )

    assert "login:" not in "".join(fake.calls)
    assert fake.calls == ["connect:smtp.example.org:25", "send_message"]


def test_send_message_raises_mailer_error_on_login_failure():
    fake = _fresh_fake_client(raise_at="login")

    with pytest.raises(MailerError):
        send_message(
            host="smtp.example.org", port=587, username="bot", password="wrong",
            from_address="bot@example.org", to_address="me@example.org",
            subject="s", body="b", use_tls=True, smtp_class=fake,
        )


def test_send_message_raises_mailer_error_on_send_failure():
    fake = _fresh_fake_client(raise_at="send")

    with pytest.raises(MailerError):
        send_message(
            host="smtp.example.org", port=587, username="bot", password="secret",
            from_address="bot@example.org", to_address="me@example.org",
            subject="s", body="b", use_tls=True, smtp_class=fake,
        )


def test_check_ok_when_send_succeeds():
    fake = _fresh_fake_client()

    result = check(
        {
            "host": "smtp.example.org", "port": 587, "username": "bot", "password": "secret",
            "from_address": "bot@example.org", "to_address": "me@example.org",
        },
        smtp_class=fake,
    )
    assert result.ok is True


def test_check_fails_when_missing_fields():
    result = check({"host": "smtp.example.org"})
    assert result.ok is False
    assert "缺少配置" in result.message


def test_check_fails_when_config_is_none():
    result = check(None)
    assert result.ok is False


def test_check_fails_when_send_raises():
    fake = _fresh_fake_client(raise_at="login")

    result = check(
        {
            "host": "smtp.example.org", "port": 587, "username": "bot", "password": "wrong",
            "from_address": "bot@example.org", "to_address": "me@example.org",
        },
        smtp_class=fake,
    )
    assert result.ok is False
