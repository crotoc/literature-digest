import httpx
import pytest

from domain.intents import (
    create_intent,
    create_intent_version,
    is_seen,
    list_candidates,
)
from domain.jobs import get_job
from domain.libraries import create_library
from domain.schedules import get_schedule, upsert_schedule
from domain.works import add_identifier, create_work
from features.discovery import IntentDisabled, NoReadyQuery, run_discovery, run_due_intents
from features.discovery.service import _already_in_library

_FAKE_RESOLVE = lambda host: ["93.184.216.34"]  # noqa: E731

ACCOUNT = 1


def _crossref_message(doi: str, title: str = "A paper") -> dict:
    return {
        "DOI": doi,
        "type": "journal-article",
        "title": [title],
        "author": [{"family": "Doe", "given": "Jane"}],
        "container-title": ["Nature"],
        "published-print": {"date-parts": [[2026, 1, 1]]},
        "URL": f"https://doi.org/{doi}",
    }


def _pubmed_summary(pmid: str, title: str = "A pubmed paper") -> dict:
    return {
        "uid": pmid,
        "title": title,
        "authors": [{"name": "Smith JA"}],
        "pubdate": "2026 Jan 1",
        "fulljournalname": "The Lancet",
    }


def _combined_transport(
    crossref_items: list[dict] | None = None,
    pubmed_idlist: list[str] | None = None,
    pubmed_summaries: dict[str, dict] | None = None,
    crossref_status: int = 200,
    pubmed_esearch_status: int = 200,
):
    crossref_items = crossref_items or []
    pubmed_idlist = pubmed_idlist or []
    pubmed_summaries = pubmed_summaries or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.crossref.org" in url:
            if crossref_status >= 400:
                return httpx.Response(crossref_status, json={"error": "nope"})
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message": {"items": crossref_items, "total-results": len(crossref_items)},
                },
            )
        if "esearch.fcgi" in url:
            if pubmed_esearch_status >= 400:
                return httpx.Response(pubmed_esearch_status, json={"error": "nope"})
            return httpx.Response(200, json={"esearchresult": {"idlist": pubmed_idlist}})
        if "esummary.fcgi" in url:
            payload = {"uids": list(pubmed_summaries.keys()), **pubmed_summaries}
            return httpx.Response(200, json={"result": payload})
        raise AssertionError(f"unexpected url: {url}")

    return httpx.MockTransport(handler)


def test_run_discovery_creates_new_candidates_from_both_sources(db, intent_id):
    transport = _combined_transport(
        crossref_items=[_crossref_message("10.1/aaa")],
        pubmed_idlist=["111"],
        pubmed_summaries={"111": _pubmed_summary("111")},
    )

    run = run_discovery(
        db, account_id=ACCOUNT, intent_id=intent_id, transport=transport, resolve=_FAKE_RESOLVE
    )

    assert run.status == "succeeded"
    assert run.candidate_count == 2
    assert run.new_candidate_count == 2

    candidates = list_candidates(db, intent_id=intent_id)
    sources = {(c.source, c.source_id) for c in candidates}
    assert sources == {("crossref", "10.1/aaa"), ("pubmed", "111")}


def test_run_discovery_second_round_skips_already_seen_results(db, intent_id):
    transport = _combined_transport(crossref_items=[_crossref_message("10.1/aaa")])

    run_discovery(db, account_id=ACCOUNT, intent_id=intent_id, transport=transport, resolve=_FAKE_RESOLVE)
    second = run_discovery(
        db, account_id=ACCOUNT, intent_id=intent_id, transport=transport, resolve=_FAKE_RESOLVE
    )

    assert second.status == "succeeded"
    assert second.candidate_count == 1
    assert second.new_candidate_count == 0
    assert len(list_candidates(db, intent_id=intent_id)) == 1


def test_run_discovery_skips_candidates_already_in_a_library(db, intent_id):
    library = create_library(db, owner_account_id=ACCOUNT, name="我的库")
    work = create_work(db, library_id=library.id, title="已经收藏过的论文")
    add_identifier(db, library_id=library.id, work_id=work.id, scheme="doi", value="10.1/already-have")

    transport = _combined_transport(
        crossref_items=[_crossref_message("10.1/already-have")],
        pubmed_idlist=["222"],
        pubmed_summaries={"222": _pubmed_summary("222")},
    )

    run = run_discovery(
        db, account_id=ACCOUNT, intent_id=intent_id, transport=transport, resolve=_FAKE_RESOLVE
    )

    assert run.candidate_count == 2
    assert run.new_candidate_count == 1
    candidates = list_candidates(db, intent_id=intent_id)
    assert [(c.source, c.source_id) for c in candidates] == [("pubmed", "222")]
    # 已经在库里的那条也应该被标记成"见过"，下一轮不会再查一遍 domain.works
    assert is_seen(db, intent_id=intent_id, source="crossref", source_id="10.1/already-have")


def test_already_in_library_checks_all_libraries_account_can_see(db, intent_id):
    library = create_library(db, owner_account_id=ACCOUNT, name="第二个库")
    work = create_work(db, library_id=library.id, title="x")
    add_identifier(db, library_id=library.id, work_id=work.id, scheme="pmid", value="333")

    assert _already_in_library(db, account_id=ACCOUNT, source="pubmed", source_id="333") is True
    assert _already_in_library(db, account_id=ACCOUNT, source="pubmed", source_id="999") is False


def test_run_discovery_respects_candidate_limit(db):
    intent = create_intent(
        db, account_id=ACCOUNT, name="限量意图", question_text="问题", candidate_limit=1
    )
    create_intent_version(db, intent_id=intent.id, query_json={"query": "x"}, source="ai", model="m")

    transport = _combined_transport(
        crossref_items=[_crossref_message("10.1/one"), _crossref_message("10.1/two")],
    )

    run = run_discovery(
        db, account_id=ACCOUNT, intent_id=intent.id, transport=transport, resolve=_FAKE_RESOLVE
    )

    assert run.new_candidate_count == 1
    assert run.candidate_count == 2


def test_run_discovery_records_failed_run_on_source_error(db, intent_id):
    transport = _combined_transport(crossref_status=500)

    run = run_discovery(
        db, account_id=ACCOUNT, intent_id=intent_id, transport=transport, resolve=_FAKE_RESOLVE
    )

    assert run.status == "failed"
    assert run.error
    assert run.candidate_count == 0
    assert run.new_candidate_count == 0
    assert get_job(db, run.job_id).status == "failed"


def test_run_discovery_raises_intent_disabled(db, intent_id):
    from domain.intents import update_intent

    update_intent(db, intent_id, enabled=False)

    with pytest.raises(IntentDisabled):
        run_discovery(db, account_id=ACCOUNT, intent_id=intent_id)


def test_run_discovery_raises_no_ready_query_without_any_version(db):
    intent = create_intent(db, account_id=ACCOUNT, name="没编译过", question_text="问题")

    with pytest.raises(NoReadyQuery):
        run_discovery(db, account_id=ACCOUNT, intent_id=intent.id)


def test_run_discovery_raises_no_ready_query_when_latest_version_failed(db):
    intent = create_intent(db, account_id=ACCOUNT, name="只有失败版本", question_text="问题")
    create_intent_version(
        db, intent_id=intent.id, query_json={}, source="ai", model="m", status="failed", error="挂了"
    )

    with pytest.raises(NoReadyQuery):
        run_discovery(db, account_id=ACCOUNT, intent_id=intent.id)


def test_run_discovery_does_not_persist_a_run_on_pre_flight_failure(db, intent_id):
    from domain.intents import list_discovery_runs, update_intent

    update_intent(db, intent_id, enabled=False)
    with pytest.raises(IntentDisabled):
        run_discovery(db, account_id=ACCOUNT, intent_id=intent_id)

    assert list_discovery_runs(db, intent_id=intent_id) == []


def test_run_due_intents_runs_due_schedule_and_marks_last_run(db, intent_id):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent", target_id=intent_id, cron="* * * * *")
    transport = _combined_transport(crossref_items=[_crossref_message("10.1/due")])

    runs = run_due_intents(db, transport=transport, resolve=_FAKE_RESOLVE)

    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    schedule = get_schedule(db, target_kind="intent", target_id=intent_id)
    assert schedule.last_run_at is not None


def test_run_due_intents_skips_disabled_intent_but_still_marks_run(db, intent_id):
    from domain.intents import update_intent

    update_intent(db, intent_id, enabled=False)
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent", target_id=intent_id, cron="* * * * *")

    runs = run_due_intents(db)

    assert runs == []
    schedule = get_schedule(db, target_kind="intent", target_id=intent_id)
    assert schedule.last_run_at is not None


def test_run_due_intents_ignores_schedules_with_a_different_target_kind(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="report", target_id=999, cron="* * * * *")

    runs = run_due_intents(db)

    assert runs == []


def test_run_due_intents_returns_empty_when_not_yet_due(db, intent_id):
    from datetime import datetime

    from domain.schedules import mark_run

    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent", target_id=intent_id, cron="0 0 1 1 *")
    mark_run(db, target_kind="intent", target_id=intent_id, when=datetime(2026, 6, 15))

    runs = run_due_intents(db, as_of=datetime(2026, 6, 16))

    assert runs == []
