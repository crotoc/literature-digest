import json

import httpx
import pytest

from caps.bibformats import Person
from domain.connections import ConnectionNotFound
from domain.jobs import list_child_jobs
from domain.tags import list_tags_for_work
from domain.works import create_work, list_provenance, update_work
from features.ai_enrichment import (
    AiEnrichmentError,
    assess_work,
    bulk_assess_works,
    check_ai_profile,
    create_ai_profile,
)

ACCOUNT = 1
_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731


def _openai_transport(payload: dict):
    content = json.dumps(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _error_transport(status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "nope"})

    return httpx.MockTransport(handler)


def _profile(db):
    return create_ai_profile(
        db, account_id=ACCOUNT, provider="openai_compat", name="p", model="gpt-4o-mini", api_key="sk-test"
    )


def test_assess_work_applies_tags_and_records_provenance_and_usage(db, library_id):
    work = create_work(db, library_id=library_id, title="Deep learning for neuro-oncology")
    update_work(db, work.id, authors=[Person(given="A", family="Smith")], year=2024)
    profile = _profile(db)

    transport = _openai_transport(
        {
            "summary": "一篇关于神经肿瘤深度学习的综述。",
            "relevance": 0.8,
            "novelty": 0.5,
            "tags": ["neuro-oncology", "deep-learning"],
        }
    )

    result = assess_work(
        db, account_id=ACCOUNT, library_id=library_id, work_id=work.id, connection_id=profile.id,
        period="2026-10", transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert result.summary == "一篇关于神经肿瘤深度学习的综述。"
    assert result.tags == ("neuro-oncology", "deep-learning")

    tag_names = {t.name for t in list_tags_for_work(db, work.id)}
    assert tag_names == {"neuro-oncology", "deep-learning"}

    provenance = list_provenance(db, work.id)
    assert len(provenance) == 1
    assert provenance[0].source == "ai_enrichment"
    assert provenance[0].payload["summary"] == "一篇关于神经肿瘤深度学习的综述。"

    from domain.usage import get_usage

    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_enrichment") == 1


def test_assess_work_rejects_work_from_another_library(db, library_id):
    from domain.libraries import create_library

    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    other_work = create_work(db, library_id=other_library_id, title="别的库里的文献")
    profile = _profile(db)

    with pytest.raises(ValueError):
        assess_work(
            db, account_id=ACCOUNT, library_id=library_id, work_id=other_work.id, connection_id=profile.id,
        )


def test_assess_work_raises_ai_enrichment_error_on_llm_failure(db, library_id):
    work = create_work(db, library_id=library_id, title="A paper")
    profile = _profile(db)
    transport = _error_transport(401)

    with pytest.raises(AiEnrichmentError):
        assess_work(
            db, account_id=ACCOUNT, library_id=library_id, work_id=work.id, connection_id=profile.id,
            transport=transport, resolve=_FAKE_RESOLVE,
        )


def test_bulk_assess_works_creates_one_child_job_per_work_even_on_success(db, library_id):
    w1 = create_work(db, library_id=library_id, title="A").id
    w2 = create_work(db, library_id=library_id, title="B").id
    profile = _profile(db)
    transport = _openai_transport({"summary": "x", "relevance": 0.5, "novelty": 0.5, "tags": ["t1"]})

    result = bulk_assess_works(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[w1, w2], connection_id=profile.id,
        period="2026-10", transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert result.job.status == "succeeded"
    assert result.job.counts == {"total": 2, "done": 2, "failed": 0}
    children = list_child_jobs(db, result.job.id)
    assert len(children) == 2
    assert {c.status for c in children} == {"succeeded"}


def test_bulk_assess_works_isolates_per_item_failure(db, library_id):
    from domain.libraries import create_library

    good = create_work(db, library_id=library_id, title="good").id
    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    bad = create_work(db, library_id=other_library_id, title="bad - 不属于这个库").id
    profile = _profile(db)
    transport = _openai_transport({"summary": "x", "relevance": 0.5, "novelty": 0.5, "tags": ["t1"]})

    result = bulk_assess_works(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[good, bad], connection_id=profile.id,
        period="2026-10", transport=transport, resolve=_FAKE_RESOLVE,
    )

    assert result.job.status == "failed"
    assert result.job.counts == {"total": 2, "done": 1, "failed": 1}
    statuses = {o.work_id: o.status for o in result.outcomes}
    assert statuses == {good: "done", bad: "failed"}
    children = list_child_jobs(db, result.job.id)
    assert len(children) == 2
    failed_children = [c for c in children if c.status == "failed"]
    assert len(failed_children) == 1
    assert "不属于这个库" in failed_children[0].reason


def test_bulk_assess_works_rejects_missing_profile_before_creating_any_job(db, library_id):
    from domain.jobs import list_jobs

    work_id = create_work(db, library_id=library_id, title="A").id

    with pytest.raises(ConnectionNotFound):
        bulk_assess_works(
            db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], connection_id=999999,
        )

    assert list_jobs(db, account_id=ACCOUNT) == []


def test_check_ai_profile_reports_failure_from_adapter(db):
    profile = _profile(db)
    transport = _error_transport(401)

    checked = check_ai_profile(db, profile.id, transport=transport, resolve=_FAKE_RESOLVE)

    assert checked.last_check_ok is False
