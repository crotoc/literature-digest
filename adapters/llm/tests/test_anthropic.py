import json

import httpx
import pytest
from pydantic import BaseModel

from adapters.llm.anthropic import AnthropicError, check, complete_structured

_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731


class _Tag(BaseModel):
    name: str
    confidence: float


def _tool_use_transport(status_code: int, *, tool_input: dict | None = None, raw_body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if raw_body is not None:
            return httpx.Response(status_code, json=raw_body)
        payload = {
            "content": [
                {"type": "tool_use", "name": "emit_result", "input": tool_input},
            ],
        }
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


def test_complete_structured_parses_tool_use_input():
    transport = _tool_use_transport(200, tool_input={"name": "neuro-oncology", "confidence": 0.92})

    result = complete_structured(
        response_model=_Tag,
        messages=[{"role": "user", "content": "给这篇文献打一个标签"}],
        model="claude-haiku-4-5-20251001",
        api_key="ak-test",
        transport=transport,
        resolve=_FAKE_RESOLVE,
    )

    assert result == _Tag(name="neuro-oncology", confidence=0.92)


def test_complete_structured_forces_tool_choice_and_sends_schema():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        payload = {
            "content": [
                {"type": "tool_use", "name": "emit_result", "input": {"name": "x", "confidence": 1.0}}
            ]
        }
        return httpx.Response(200, json=payload)

    complete_structured(
        response_model=_Tag,
        messages=[{"role": "user", "content": "hi"}],
        model="claude-haiku-4-5-20251001",
        api_key="ak-test",
        system="你是一个标签助手。",
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )

    body = captured["body"]
    assert body["tool_choice"] == {"type": "tool", "name": "emit_result"}
    assert body["tools"][0]["name"] == "emit_result"
    assert "confidence" in body["tools"][0]["input_schema"]["properties"]
    assert body["system"] == "你是一个标签助手。"
    assert captured["headers"]["x-api-key"] == "ak-test"
    assert captured["headers"]["anthropic-version"]


def test_complete_structured_raises_on_non_2xx_status():
    transport = _tool_use_transport(401, raw_body={"error": "invalid api key"})

    with pytest.raises(AnthropicError):
        complete_structured(
            response_model=_Tag,
            messages=[{"role": "user", "content": "hi"}],
            model="claude-haiku-4-5-20251001",
            api_key="ak-bad",
            transport=transport,
            resolve=_FAKE_RESOLVE,
        )


def test_complete_structured_raises_when_no_matching_tool_use_block():
    transport = _tool_use_transport(200, raw_body={"content": [{"type": "text", "text": "oops"}]})

    with pytest.raises(AnthropicError):
        complete_structured(
            response_model=_Tag,
            messages=[{"role": "user", "content": "hi"}],
            model="claude-haiku-4-5-20251001",
            api_key="ak-test",
            transport=transport,
            resolve=_FAKE_RESOLVE,
        )


def test_complete_structured_raises_when_tool_input_fails_validation():
    transport = _tool_use_transport(200, tool_input={"name": "x"})  # missing confidence

    with pytest.raises(AnthropicError):
        complete_structured(
            response_model=_Tag,
            messages=[{"role": "user", "content": "hi"}],
            model="claude-haiku-4-5-20251001",
            api_key="ak-test",
            transport=transport,
            resolve=_FAKE_RESOLVE,
        )


def test_check_fails_without_api_key():
    result = check({})
    assert result.ok is False
    assert "api_key" in result.message


def test_check_ok_when_probe_round_trip_succeeds():
    transport = _tool_use_transport(200, tool_input={"ok": True})

    result = check({"api_key": "ak-test"}, transport=transport, resolve=_FAKE_RESOLVE)

    assert result.ok is True


def test_check_fails_when_probe_round_trip_errors():
    transport = _tool_use_transport(500, raw_body={"error": "boom"})

    result = check({"api_key": "ak-test"}, transport=transport, resolve=_FAKE_RESOLVE)

    assert result.ok is False
