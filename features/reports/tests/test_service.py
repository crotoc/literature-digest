import json

import httpx
import pytest

from domain.intents import (
    create_candidate,
    create_discovery_run,
    create_intent,
    create_intent_version,
    finish_discovery_run,
)
from domain.jobs import get_job
from domain.reports import list_report_items
from features.reports import ReportNotReady, analyze_report, materialize_report, screen_report_titles

ACCOUNT = 1
_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731


def _ready_run(db, *, account_id=ACCOUNT, intent_id=None):
    intent = (
        create_intent(db, account_id=account_id, name="AD", question_text="阿尔茨海默病机制")
        if intent_id is None
        else None
    )
    target_intent_id = intent.id if intent else intent_id
    version = create_intent_version(db, intent_id=target_intent_id, query_json={"query": "AD"}, source="ai")
    run = create_discovery_run(db, intent_id=target_intent_id, intent_version_id=version.id, job_id=1)
    finished = finish_discovery_run(db, run.id, status="succeeded", candidate_count=1, new_candidate_count=1)
    return target_intent_id, finished


def _openai_transport(responses):
    queued = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.dumps(queued.pop(0), ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": payload}}]})

    return httpx.MockTransport(handler)


def _materialized_report(db):
    intent_id, run = _ready_run(db)
    create_candidate(
        db,
        discovery_run_id=run.id,
        intent_id=intent_id,
        source="pubmed",
        source_id="1",
        title="Microglia in AD",
        payload={"abstract": "microglia signal"},
    )
    return materialize_report(db, account_id=ACCOUNT, intent_id=intent_id, discovery_run_id=run.id)


def test_materialize_report_creates_a_single_report_and_item(db):
    report = _materialized_report(db)
    repeated = materialize_report(
        db, account_id=ACCOUNT, intent_id=report.intent_id, discovery_run_id=report.discovery_run_id
    )
    assert repeated.id == report.id
    items = list_report_items(db, report_id=report.id)
    assert [(item.source, item.source_id) for item in items] == [("pubmed", "1")]


def test_materialize_rejects_another_accounts_intent(db):
    intent_id, run = _ready_run(db, account_id=2)
    with pytest.raises(Exception, match="无权"):
        materialize_report(db, account_id=ACCOUNT, intent_id=intent_id, discovery_run_id=run.id)


def test_title_screen_keeps_items_and_records_a_job(db):
    report = _materialized_report(db)
    result = screen_report_titles(
        db,
        account_id=ACCOUNT,
        report_id=report.id,
        provider="openai_compat",
        model="test",
        api_key="test-key",
        transport=_openai_transport([{"keep": True, "relevance_score": 0.9, "reason": "相关"}]),
        resolve=_FAKE_RESOLVE,
    )
    assert (result.kept_count, result.failed_count, result.report.title_screen_status) == (1, 0, "success")
    assert get_job(db, result.job.id).status == "succeeded"
    assert list_report_items(db, report_id=report.id)[0].title_screen_status == "keep"


def test_title_screen_isolates_a_provider_failure(db):
    report = _materialized_report(db)
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "down"}))
    result = screen_report_titles(
        db,
        account_id=ACCOUNT,
        report_id=report.id,
        provider="openai_compat",
        model="test",
        api_key="test-key",
        transport=transport,
        resolve=_FAKE_RESOLVE,
    )
    assert (result.failed_count, result.report.title_screen_status) == (1, "partial")
    assert list_report_items(db, report_id=report.id)[0].title_screen_status == "failed"


def test_analyze_requires_completed_title_screen(db):
    report = _materialized_report(db)
    with pytest.raises(ReportNotReady, match="标题筛选"):
        analyze_report(
            db, account_id=ACCOUNT, report_id=report.id, provider="openai_compat", model="test", api_key="key"
        )


def test_analyze_kept_item_and_records_scores(db):
    report = _materialized_report(db)
    screen_report_titles(
        db,
        account_id=ACCOUNT,
        report_id=report.id,
        provider="openai_compat",
        model="test",
        api_key="key",
        transport=_openai_transport([{"keep": True, "relevance_score": 0.9, "reason": "相关"}]),
        resolve=_FAKE_RESOLVE,
    )
    response = {
        "relevance_score": 0.9,
        "novelty_score": 0.4,
        "evidence_strength_score": 0.7,
        "human_relevance_score": 0.8,
        "recommend": True,
        "priority": "high",
        "tags": ["AD"],
        "reason": "值得读",
    }
    result = analyze_report(
        db,
        account_id=ACCOUNT,
        report_id=report.id,
        provider="openai_compat",
        model="test",
        api_key="key",
        transport=_openai_transport([response]),
        resolve=_FAKE_RESOLVE,
    )
    item = list_report_items(db, report_id=report.id)[0]
    assert (result.analyzed_count, result.failed_count, result.report.analysis_status) == (1, 0, "success")
    assert (item.analysis_status, item.recommend, item.tags) == ("success", True, ["AD"])
