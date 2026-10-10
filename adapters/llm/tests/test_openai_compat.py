import json

import httpx
import pytest
from pydantic import BaseModel

from adapters.llm.openai_compat import OpenAiCompatError, check, complete_structured

# api.openai.com 真实指向公网 IP，但单测不该依赖真实 DNS 查询——和
# adapters/sources 的测试用同一招，固定 IP 字面量当 resolve() 的返回值。
_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731


class _Tag(BaseModel):
    name: str
    confidence: float


def _transport(status_code: int, *, content_string: str | None = None, raw_body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if raw_body is not None:
            return httpx.Response(status_code, json=raw_body)
        payload = {
            "choices": [{"message": {"content": content_string}}],
        }
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


def test_complete_structured_parses_json_mode_response():
    content = json.dumps({"name": "neuro-oncology", "confidence": 0.92})
    transport = _transport(200, content_string=content)

    result = complete_structured(
        response_model=_Tag,
        messages=[{"role": "user", "content": "给这篇文献打一个标签"}],
        model="gpt-4o-mini",
        api_key="sk-test",
        transport=transport,
        resolve=_FAKE_RESOLVE,
    )

    assert result == _Tag(name="neuro-oncology", confidence=0.92)


def test_complete_structured_sends_json_object_response_format_and_schema_in_system():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        content = json.dumps({"name": "x", "confidence": 1.0})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    complete_structured(
        response_model=_Tag,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o-mini",
        api_key="sk-test",
        system="你是一个标签助手。",
        transport=httpx.MockTransport(handler),
        resolve=_FAKE_RESOLVE,
    )

    body = captured["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "你是一个标签助手。" in body["messages"][0]["content"]
    assert "confidence" in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "hi"}


def test_complete_structured_raises_on_non_2xx_status():
    transport = _transport(401, raw_body={"error": "invalid api key"})

    with pytest.raises(OpenAiCompatError):
        complete_structured(
            response_model=_Tag,
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
            api_key="sk-bad",
            transport=transport,
            resolve=_FAKE_RESOLVE,
        )


def test_complete_structured_raises_when_content_is_not_valid_json_for_model():
    transport = _transport(200, content_string="not json at all")

    with pytest.raises(OpenAiCompatError):
        complete_structured(
            response_model=_Tag,
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
            api_key="sk-test",
            transport=transport,
            resolve=_FAKE_RESOLVE,
        )


def test_complete_structured_raises_when_choices_missing():
    transport = _transport(200, raw_body={"unexpected": "shape"})

    with pytest.raises(OpenAiCompatError):
        complete_structured(
            response_model=_Tag,
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
            api_key="sk-test",
            transport=transport,
            resolve=_FAKE_RESOLVE,
        )


def test_check_fails_without_api_key():
    result = check({})
    assert result.ok is False
    assert "api_key" in result.message


def test_check_ok_when_probe_round_trip_succeeds():
    content = json.dumps({"ok": True})
    transport = _transport(200, content_string=content)

    result = check({"api_key": "sk-test"}, transport=transport, resolve=_FAKE_RESOLVE)

    assert result.ok is True


def test_check_fails_when_probe_round_trip_errors():
    transport = _transport(500, raw_body={"error": "boom"})

    result = check({"api_key": "sk-test"}, transport=transport, resolve=_FAKE_RESOLVE)

    assert result.ok is False
