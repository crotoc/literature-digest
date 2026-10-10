import json

import httpx
import pytest

from adapters.delivery.telegram import TelegramError, check, edit_message, send_message

_FAKE_RESOLVE = lambda host: ["149.154.167.220"]  # noqa: E731

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
CHAT_ID = "-100123456789"


def _transport(status_code: int, body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def test_send_message_returns_message_id():
    transport = _transport(200, {"ok": True, "result": {"message_id": 42}})
    message_id = send_message(
        bot_token=BOT_TOKEN, chat_id=CHAT_ID, text="hello", transport=transport, resolve=_FAKE_RESOLVE
    )
    assert message_id == "42"


def test_send_message_sends_correct_payload_and_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    send_message(
        bot_token=BOT_TOKEN, chat_id=CHAT_ID, text="hello world",
        transport=httpx.MockTransport(handler), resolve=_FAKE_RESOLVE,
    )
    assert seen["url"] == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    assert seen["body"]["chat_id"] == CHAT_ID
    assert seen["body"]["text"] == "hello world"
    assert seen["body"]["parse_mode"] == "HTML"


def test_send_message_raises_on_transport_failure():
    transport = _transport(500, {})
    with pytest.raises(TelegramError):
        send_message(
            bot_token=BOT_TOKEN, chat_id=CHAT_ID, text="hello",
            transport=transport, resolve=_FAKE_RESOLVE,
        )


def test_send_message_raises_when_telegram_reports_not_ok():
    transport = _transport(200, {"ok": False, "description": "Bad Request: chat not found"})
    with pytest.raises(TelegramError, match="chat not found"):
        send_message(
            bot_token=BOT_TOKEN, chat_id=CHAT_ID, text="hello",
            transport=transport, resolve=_FAKE_RESOLVE,
        )


def test_edit_message_succeeds_without_raising():
    transport = _transport(200, {"ok": True, "result": {"message_id": 42}})
    edit_message(
        bot_token=BOT_TOKEN, chat_id=CHAT_ID, message_id="42", text="edited",
        transport=transport, resolve=_FAKE_RESOLVE,
    )


def test_edit_message_sends_correct_method_and_message_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    edit_message(
        bot_token=BOT_TOKEN, chat_id=CHAT_ID, message_id="42", text="edited",
        transport=httpx.MockTransport(handler), resolve=_FAKE_RESOLVE,
    )
    assert seen["url"] == f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    assert seen["body"]["message_id"] == 42


def test_edit_message_raises_when_not_modified():
    transport = _transport(200, {"ok": False, "description": "Bad Request: message is not modified"})
    with pytest.raises(TelegramError, match="not modified"):
        edit_message(
            bot_token=BOT_TOKEN, chat_id=CHAT_ID, message_id="42", text="same",
            transport=transport, resolve=_FAKE_RESOLVE,
        )


def test_check_ok_when_send_succeeds():
    transport = _transport(200, {"ok": True, "result": {"message_id": 1}})
    result = check({"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is True


def test_check_fails_when_missing_bot_token():
    result = check({"chat_id": CHAT_ID})
    assert result.ok is False


def test_check_fails_when_missing_chat_id():
    result = check({"bot_token": BOT_TOKEN})
    assert result.ok is False


def test_check_fails_when_config_is_none():
    result = check(None)
    assert result.ok is False


def test_check_fails_when_send_fails():
    transport = _transport(200, {"ok": False, "description": "Forbidden: bot was blocked by the user"})
    result = check({"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}, transport=transport, resolve=_FAKE_RESOLVE)
    assert result.ok is False
    assert "blocked" in result.message
