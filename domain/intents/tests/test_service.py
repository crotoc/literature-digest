import pytest

from domain.intents import (
    DiscoveryRunNotFound,
    IntentNotFound,
    create_candidate,
    create_discovery_run,
    create_intent,
    create_intent_version,
    delete_intent,
    finish_discovery_run,
    get_discovery_run,
    get_intent,
    get_latest_ready_version,
    is_seen,
    list_candidates,
    list_discovery_runs,
    list_intent_versions,
    list_intents,
    mark_seen,
    update_intent,
)

ACCOUNT = 1
OTHER_ACCOUNT = 2


def _make_intent(db, **kwargs):
    kwargs.setdefault("account_id", ACCOUNT)
    kwargs.setdefault("name", "肠道菌群与阿尔茨海默病")
    kwargs.setdefault("question_text", "肠道菌群组成变化是否影响阿尔茨海默病进展")
    return create_intent(db, **kwargs)


# ── create_intent / get_intent ────────────────────────────────────────────


def test_create_intent_defaults(db):
    intent = _make_intent(db)
    assert intent.enabled is True
    assert intent.candidate_limit == 40
    assert intent.lookback_days == 7
    assert intent.query_override is None


def test_create_intent_empty_name_raises(db):
    with pytest.raises(ValueError):
        _make_intent(db, name="   ")


def test_create_intent_empty_question_raises(db):
    with pytest.raises(ValueError):
        _make_intent(db, question_text="")


def test_create_intent_invalid_candidate_limit_raises(db):
    with pytest.raises(ValueError):
        _make_intent(db, candidate_limit=0)


def test_create_intent_invalid_lookback_days_raises(db):
    with pytest.raises(ValueError):
        _make_intent(db, lookback_days=-1)


def test_create_intent_strips_query_override(db):
    intent = _make_intent(db, query_override="  gut microbiome AND alzheimer  ")
    assert intent.query_override == "gut microbiome AND alzheimer"


def test_get_intent_missing_raises(db):
    with pytest.raises(IntentNotFound):
        get_intent(db, 999)


# ── update_intent ──────────────────────────────────────────────────────────


def test_update_intent_partial_update(db):
    intent = _make_intent(db)
    updated = update_intent(db, intent.id, enabled=False, candidate_limit=20)
    assert updated.enabled is False
    assert updated.candidate_limit == 20
    assert updated.name == intent.name  # 未传的字段不变


def test_update_intent_empty_name_raises(db):
    intent = _make_intent(db)
    with pytest.raises(ValueError):
        update_intent(db, intent.id, name="")


def test_update_intent_can_clear_query_override(db):
    intent = _make_intent(db, query_override="old query")
    updated = update_intent(db, intent.id, query_override=None)
    assert updated.query_override is None


# ── delete_intent（级联） ───────────────────────────────────────────────────


def test_delete_intent_cascades_everything(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={"crossref": "q"}, source="ai")
    run = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    create_candidate(db, discovery_run_id=run.id, intent_id=intent.id, source="crossref", source_id="10.1/x")
    mark_seen(db, intent_id=intent.id, source="crossref", source_id="10.1/x")

    delete_intent(db, intent.id)

    with pytest.raises(IntentNotFound):
        get_intent(db, intent.id)
    assert list_intent_versions(db, intent.id) == []
    assert list_discovery_runs(db, intent_id=intent.id) == []
    assert list_candidates(db, intent_id=intent.id) == []
    assert is_seen(db, intent_id=intent.id, source="crossref", source_id="10.1/x") is False


# ── list_intents ────────────────────────────────────────────────────────────


def test_list_intents_scoped_to_account(db):
    _make_intent(db, account_id=ACCOUNT, name="A")
    _make_intent(db, account_id=OTHER_ACCOUNT, name="B")
    names = [i.name for i in list_intents(db, account_id=ACCOUNT)]
    assert names == ["A"]


# ── intent_versions ─────────────────────────────────────────────────────────


def test_create_intent_version_ai(db):
    intent = _make_intent(db)
    version = create_intent_version(
        db,
        intent_id=intent.id,
        query_json={"crossref": "q1", "pubmed": "q2"},
        source="ai",
        model="gpt-4o-mini",
    )
    assert version.status == "ready"
    assert version.query_json == {"crossref": "q1", "pubmed": "q2"}
    assert version.model == "gpt-4o-mini"


def test_create_intent_version_invalid_source_raises(db):
    intent = _make_intent(db)
    with pytest.raises(ValueError):
        create_intent_version(db, intent_id=intent.id, query_json={}, source="bogus")


def test_create_intent_version_failed_status(db):
    intent = _make_intent(db)
    version = create_intent_version(
        db, intent_id=intent.id, query_json={}, source="ai", status="failed", error="LLM 超时"
    )
    assert version.status == "failed"
    assert version.error == "LLM 超时"


def test_get_latest_ready_version_skips_failed(db):
    intent = _make_intent(db)
    create_intent_version(db, intent_id=intent.id, query_json={"a": 1}, source="ai", status="ready")
    create_intent_version(db, intent_id=intent.id, query_json={}, source="ai", status="failed", error="x")
    latest = get_latest_ready_version(db, intent.id)
    assert latest is not None
    assert latest.query_json == {"a": 1}


def test_get_latest_ready_version_none_when_no_ready(db):
    intent = _make_intent(db)
    create_intent_version(db, intent_id=intent.id, query_json={}, source="ai", status="failed", error="x")
    assert get_latest_ready_version(db, intent.id) is None


def test_get_latest_ready_version_picks_most_recent(db):
    intent = _make_intent(db)
    create_intent_version(db, intent_id=intent.id, query_json={"v": 1}, source="ai")
    second = create_intent_version(db, intent_id=intent.id, query_json={"v": 2}, source="override")
    latest = get_latest_ready_version(db, intent.id)
    assert latest.id == second.id
    assert latest.query_json == {"v": 2}


def test_list_intent_versions_desc_order(db):
    intent = _make_intent(db)
    first = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    second = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    versions = list_intent_versions(db, intent.id)
    assert [v.id for v in versions] == [second.id, first.id]


# ── discovery_runs ──────────────────────────────────────────────────────────


def test_create_discovery_run_starts_running(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    run = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=42)
    assert run.status == "running"
    assert run.job_id == 42
    assert run.finished_at is None


def test_finish_discovery_run_succeeded(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    run = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    finished = finish_discovery_run(db, run.id, status="succeeded", candidate_count=10, new_candidate_count=3)
    assert finished.status == "succeeded"
    assert finished.candidate_count == 10
    assert finished.new_candidate_count == 3
    assert finished.finished_at is not None


def test_finish_discovery_run_invalid_status_raises(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    run = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    with pytest.raises(ValueError):
        finish_discovery_run(db, run.id, status="running", candidate_count=0, new_candidate_count=0)


def test_get_discovery_run_missing_raises(db):
    with pytest.raises(DiscoveryRunNotFound):
        get_discovery_run(db, 999)


def test_list_discovery_runs_desc_order(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    first = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    second = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=2)
    runs = list_discovery_runs(db, intent_id=intent.id)
    assert [r.id for r in runs] == [second.id, first.id]


def test_list_discovery_runs_respects_limit(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    for job_id in range(5):
        create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=job_id)
    assert len(list_discovery_runs(db, intent_id=intent.id, limit=2)) == 2


# ── candidates + intent_seen ─────────────────────────────────────────────────


def test_is_seen_false_initially(db):
    intent = _make_intent(db)
    assert is_seen(db, intent_id=intent.id, source="crossref", source_id="10.1/x") is False


def test_mark_seen_then_is_seen_true(db):
    intent = _make_intent(db)
    mark_seen(db, intent_id=intent.id, source="crossref", source_id="10.1/x")
    assert is_seen(db, intent_id=intent.id, source="crossref", source_id="10.1/x") is True


def test_mark_seen_idempotent(db):
    intent = _make_intent(db)
    mark_seen(db, intent_id=intent.id, source="crossref", source_id="10.1/x")
    mark_seen(db, intent_id=intent.id, source="crossref", source_id="10.1/x")  # 不抛


def test_seen_is_scoped_per_intent(db):
    intent_a = _make_intent(db, name="A")
    intent_b = _make_intent(db, name="B")
    mark_seen(db, intent_id=intent_a.id, source="crossref", source_id="10.1/x")
    assert is_seen(db, intent_id=intent_b.id, source="crossref", source_id="10.1/x") is False


def test_create_candidate_basic(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    run = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    candidate = create_candidate(
        db,
        discovery_run_id=run.id,
        intent_id=intent.id,
        source="crossref",
        source_id="10.1/x",
        title="一篇文章",
        url="https://doi.org/10.1/x",
        payload={"year": 2026},
    )
    assert candidate.title == "一篇文章"
    assert candidate.payload == {"year": 2026}


def test_create_candidate_is_idempotent(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    run1 = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    run2 = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=2)
    first = create_candidate(
        db, discovery_run_id=run1.id, intent_id=intent.id, source="crossref", source_id="x"
    )
    second = create_candidate(
        db, discovery_run_id=run2.id, intent_id=intent.id, source="crossref", source_id="x"
    )
    assert first.id == second.id
    assert len(list_candidates(db, intent_id=intent.id)) == 1


def test_list_candidates_filter_by_run(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    run1 = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    run2 = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=2)
    create_candidate(db, discovery_run_id=run1.id, intent_id=intent.id, source="crossref", source_id="a")
    create_candidate(db, discovery_run_id=run2.id, intent_id=intent.id, source="crossref", source_id="b")
    only_run1 = list_candidates(db, intent_id=intent.id, discovery_run_id=run1.id)
    assert [c.source_id for c in only_run1] == ["a"]


def test_list_candidates_desc_order(db):
    intent = _make_intent(db)
    version = create_intent_version(db, intent_id=intent.id, query_json={}, source="ai")
    run = create_discovery_run(db, intent_id=intent.id, intent_version_id=version.id, job_id=1)
    first = create_candidate(
        db, discovery_run_id=run.id, intent_id=intent.id, source="crossref", source_id="a"
    )
    second = create_candidate(
        db, discovery_run_id=run.id, intent_id=intent.id, source="crossref", source_id="b"
    )
    candidates = list_candidates(db, intent_id=intent.id)
    assert [c.id for c in candidates] == [second.id, first.id]
