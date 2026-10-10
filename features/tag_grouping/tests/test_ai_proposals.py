import json

import httpx
import pytest

from domain.tags import create_tag
from features.tag_grouping import TagGroupingError, propose_ai_duplicates, propose_ai_groups

_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731


def _openai_transport(clusters: list[dict]):
    content = json.dumps({"clusters": clusters})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _anthropic_transport(clusters: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "content": [
                {"type": "tool_use", "name": "emit_result", "input": {"clusters": clusters}}
            ]
        }
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _error_transport(status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "nope"})

    return httpx.MockTransport(handler)


def test_propose_ai_groups_returns_valid_clusters_via_openai_compat(db, library_id):
    a = create_tag(db, library_id=library_id, name="neuro")
    b = create_tag(db, library_id=library_id, name="neuroscience")
    create_tag(db, library_id=library_id, name="cooking")

    transport = _openai_transport([{"name": "神经科学", "tag_ids": [a.id, b.id]}])

    proposals = propose_ai_groups(
        db, library_id=library_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert len(proposals) == 1
    assert proposals[0].name == "神经科学"
    assert set(proposals[0].tag_ids) == {a.id, b.id}


def test_propose_ai_duplicates_returns_valid_clusters_via_anthropic(db, library_id):
    a = create_tag(db, library_id=library_id, name="ML")
    b = create_tag(db, library_id=library_id, name="ml")

    transport = _anthropic_transport([{"name": "ML", "tag_ids": [a.id, b.id]}])

    proposals = propose_ai_duplicates(
        db, library_id=library_id, provider="anthropic", model="claude-haiku-4-5-20251001",
        api_key="sk-ant-test", transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert len(proposals) == 1
    assert set(proposals[0].tag_ids) == {a.id, b.id}


def test_propose_ai_groups_drops_clusters_with_unknown_tag_ids(db, library_id):
    a = create_tag(db, library_id=library_id, name="neuro")

    transport = _openai_transport([{"name": "幻觉分组", "tag_ids": [a.id, 999999]}])

    proposals = propose_ai_groups(
        db, library_id=library_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert proposals == []


def test_propose_ai_groups_drops_single_tag_clusters(db, library_id):
    a = create_tag(db, library_id=library_id, name="neuro")

    transport = _openai_transport([{"name": "单个标签凑不成组", "tag_ids": [a.id]}])

    proposals = propose_ai_groups(
        db, library_id=library_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
        transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert proposals == []


def test_propose_ai_groups_returns_empty_when_fewer_than_two_tags_exist(db, library_id):
    create_tag(db, library_id=library_id, name="only-one")

    proposals = propose_ai_groups(
        db, library_id=library_id, provider="openai_compat", model="gpt-4o-mini", api_key="sk-test",
    )

    assert proposals == []


def test_propose_ai_groups_raises_tag_grouping_error_on_llm_failure(db, library_id):
    create_tag(db, library_id=library_id, name="a")
    create_tag(db, library_id=library_id, name="b")

    transport = _error_transport(401)

    with pytest.raises(TagGroupingError):
        propose_ai_groups(
            db, library_id=library_id, provider="openai_compat", model="gpt-4o-mini", api_key="bad-key",
            transport=transport, resolve=_FAKE_RESOLVE,
        )


def test_propose_ai_groups_rejects_unsupported_provider(db, library_id):
    create_tag(db, library_id=library_id, name="a")
    create_tag(db, library_id=library_id, name="b")

    with pytest.raises(TagGroupingError):
        propose_ai_groups(db, library_id=library_id, provider="deepseek", model="x", api_key="x")
