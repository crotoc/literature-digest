import json

import httpx
import pytest

from domain.intents import create_intent, list_intent_versions, update_intent
from features.query_planner import PROVIDERS, UnknownProvider, compile_intent_query

_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731

ACCOUNT = 1


def _openai_transport(query: str, rationale: str = "因为这是核心关键词"):
    content = json.dumps({"query": query, "rationale": rationale})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _anthropic_transport(query: str, rationale: str = "因为这是核心关键词"):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "content": [
                {"type": "tool_use", "name": "emit_result", "input": {"query": query, "rationale": rationale}}
            ]
        }
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _error_transport(status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "nope"})

    return httpx.MockTransport(handler)


def test_compile_via_openai_compat_creates_ready_version(db, intent_id):
    transport = _openai_transport("CRISPR base editing in vivo delivery")

    version = compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert version.status == "ready"
    assert version.source == "ai"
    assert version.model == "gpt-4o-mini"
    assert version.query_json == {
        "query": "CRISPR base editing in vivo delivery",
        "rationale": "因为这是核心关键词",
    }


def test_compile_via_anthropic_creates_ready_version(db, intent_id):
    transport = _anthropic_transport("base editing delivery")

    version = compile_intent_query(
        db, intent_id=intent_id, provider="anthropic", model="claude-haiku-4-5-20251001",
        api_key="sk-ant-test", transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert version.status == "ready"
    assert version.query_json["query"] == "base editing delivery"


def test_compile_strips_whitespace_from_query(db, intent_id):
    transport = _openai_transport("  padded query  ")

    version = compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert version.query_json["query"] == "padded query"


def test_compile_llm_error_creates_failed_version_not_raises(db, intent_id):
    transport = _error_transport(500)

    version = compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert version.status == "failed"
    assert version.source == "ai"
    assert version.error
    assert version.query_json == {}


def test_compile_empty_query_creates_failed_version(db, intent_id):
    transport = _openai_transport("   ")

    version = compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert version.status == "failed"
    assert "空字符串" in version.error


def test_compile_failed_version_does_not_shadow_previous_ready_version(db, intent_id):
    ok_transport = _openai_transport("good query")
    compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=ok_transport, resolve=_FAKE_RESOLVE,
    )

    from domain.intents import get_latest_ready_version

    bad_transport = _error_transport(500)
    compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=bad_transport, resolve=_FAKE_RESOLVE,
    )

    latest_ready = get_latest_ready_version(db, intent_id)
    assert latest_ready is not None
    assert latest_ready.query_json["query"] == "good query"
    assert len(list_intent_versions(db, intent_id)) == 2


def test_compile_unknown_provider_raises_without_touching_db(db, intent_id):
    with pytest.raises(UnknownProvider):
        compile_intent_query(
            db, intent_id=intent_id, provider="not_a_real_provider", model="x", api_key="sk-test",
        )
    assert list_intent_versions(db, intent_id) == []


def test_compile_with_query_override_short_circuits_without_calling_llm(db, intent_id):
    update_intent(db, intent_id, query_override="manual keyword string")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("query_override 存在时不应该发出任何 LLM 请求")

    version = compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=httpx.MockTransport(handler), resolve=_FAKE_RESOLVE,
    )

    assert version.status == "ready"
    assert version.source == "override"
    assert version.model is None
    assert version.query_json == {"query": "manual keyword string"}


def test_compile_with_query_override_ignores_llm_errors_entirely(db, intent_id):
    update_intent(db, intent_id, query_override="manual keyword string")

    version = compile_intent_query(
        db, intent_id=intent_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=_error_transport(500), resolve=_FAKE_RESOLVE,
    )

    assert version.status == "ready"
    assert version.source == "override"


def test_providers_constant_matches_supported_set():
    assert PROVIDERS == {"openai_compat", "anthropic"}


def test_second_intent_is_isolated_from_first(db):
    other_intent = create_intent(db, account_id=ACCOUNT, name="另一个意图", question_text="别的问题")
    transport = _openai_transport("other query")

    compile_intent_query(
        db, intent_id=other_intent.id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert list_intent_versions(db, other_intent.id)[0].query_json["query"] == "other query"
