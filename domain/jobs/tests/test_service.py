import pytest

from domain.jobs.service import (
    DEFAULT_STATUS,
    TERMINAL_STATUSES,
    InvalidTransition,
    JobNotFound,
    create_job,
    get_job,
    list_child_jobs,
    list_jobs,
    retry_job,
    transition,
    update_counts,
    update_cursor,
)

ACCOUNT = 1
OTHER_ACCOUNT = 2

# 一个最小的转移表，模拟某个 feature 在自己 contract.py 里会声明的东西
SIMPLE_TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
}


def _make_job(db, *, kind="import_ris", **kwargs):
    return create_job(db, account_id=ACCOUNT, kind=kind, **kwargs)


# ── create_job / get_job ──────────────────────────────────────────────────


def test_create_job_defaults(db):
    job = _make_job(db)
    assert job.status == DEFAULT_STATUS
    assert job.reason is None
    assert job.counts == {}
    assert job.cursor is None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.parent_job_id is None
    assert job.library_id is None


def test_create_job_with_library_and_parent(db):
    parent = _make_job(db, kind="batch_tag")
    child = _make_job(db, kind="batch_tag", library_id=7, parent_job_id=parent.id)
    assert child.library_id == 7
    assert child.parent_job_id == parent.id


def test_create_job_with_initial_counts(db):
    job = _make_job(db, counts={"total": 10})
    assert job.counts == {"total": 10}


def test_get_job_missing_raises(db):
    with pytest.raises(JobNotFound):
        get_job(db, 999)


# ── list_jobs ──────────────────────────────────────────────────────────


def test_list_jobs_scoped_to_account(db):
    _make_job(db)
    create_job(db, account_id=OTHER_ACCOUNT, kind="import_ris")

    jobs = list_jobs(db, account_id=ACCOUNT)
    assert len(jobs) == 1


def test_list_jobs_filtered_by_kind(db):
    _make_job(db, kind="import_ris")
    _make_job(db, kind="export_bibtex")

    jobs = list_jobs(db, account_id=ACCOUNT, kind="import_ris")
    assert [j.kind for j in jobs] == ["import_ris"]


def test_list_jobs_without_kind_filter_returns_all(db):
    _make_job(db, kind="import_ris")
    _make_job(db, kind="export_bibtex")

    assert len(list_jobs(db, account_id=ACCOUNT)) == 2


def test_list_jobs_filtered_by_status(db):
    a = _make_job(db)
    _make_job(db)
    transition(db, a.id, "running", allowed=SIMPLE_TRANSITIONS)

    running = list_jobs(db, account_id=ACCOUNT, status="running")
    assert [j.id for j in running] == [a.id]


def test_list_jobs_filtered_by_parent_job_id(db):
    parent = _make_job(db, kind="batch_tag")
    child1 = _make_job(db, kind="batch_tag", parent_job_id=parent.id)
    _make_job(db, kind="batch_tag")  # 没有父 job，不应该混进来

    jobs = list_jobs(db, account_id=ACCOUNT, parent_job_id=parent.id)
    assert [j.id for j in jobs] == [child1.id]


def test_list_jobs_orders_by_created_at_desc(db):
    first = _make_job(db)
    second = _make_job(db)

    jobs = list_jobs(db, account_id=ACCOUNT)
    assert [j.id for j in jobs] == [second.id, first.id]


# ── list_child_jobs ──────────────────────────────────────────────────────


def test_list_child_jobs_orders_ascending(db):
    parent = _make_job(db, kind="fulltext_download")
    child1 = _make_job(db, kind="fulltext_download", parent_job_id=parent.id)
    child2 = _make_job(db, kind="fulltext_download", parent_job_id=parent.id)

    children = list_child_jobs(db, parent.id)
    assert [c.id for c in children] == [child1.id, child2.id]


def test_list_child_jobs_empty_for_leaf_job(db):
    leaf = _make_job(db)
    assert list_child_jobs(db, leaf.id) == []


# ── transition ────────────────────────────────────────────────────────────


def test_transition_queued_to_running_sets_started_at(db):
    job = _make_job(db)
    updated = transition(db, job.id, "running", allowed=SIMPLE_TRANSITIONS)
    assert updated.status == "running"
    assert updated.started_at is not None
    assert updated.finished_at is None


def test_transition_to_terminal_sets_finished_at(db):
    job = _make_job(db)
    transition(db, job.id, "running", allowed=SIMPLE_TRANSITIONS)
    done = transition(db, job.id, "succeeded", allowed=SIMPLE_TRANSITIONS)
    assert done.status in TERMINAL_STATUSES
    assert done.finished_at is not None


def test_transition_stores_reason(db):
    job = _make_job(db)
    transition(db, job.id, "running", allowed=SIMPLE_TRANSITIONS)
    failed = transition(
        db, job.id, "failed", reason="network_timeout", allowed=SIMPLE_TRANSITIONS
    )
    assert failed.reason == "network_timeout"


def test_transition_rejects_disallowed_move(db):
    job = _make_job(db)
    with pytest.raises(InvalidTransition) as exc_info:
        transition(db, job.id, "succeeded", allowed=SIMPLE_TRANSITIONS)
    assert exc_info.value.from_status == "queued"
    assert exc_info.value.to_status == "succeeded"


def test_transition_rejects_move_out_of_terminal_state(db):
    job = _make_job(db)
    transition(db, job.id, "running", allowed=SIMPLE_TRANSITIONS)
    transition(db, job.id, "succeeded", allowed=SIMPLE_TRANSITIONS)
    with pytest.raises(InvalidTransition):
        transition(db, job.id, "running", allowed=SIMPLE_TRANSITIONS)


def test_transition_rejects_unknown_status(db):
    job = _make_job(db)
    with pytest.raises(ValueError):
        transition(db, job.id, "not_a_real_status", allowed=SIMPLE_TRANSITIONS)


def test_transition_missing_job_raises(db):
    with pytest.raises(JobNotFound):
        transition(db, 999, "running", allowed=SIMPLE_TRANSITIONS)


# ── update_counts / update_cursor ────────────────────────────────────────


def test_update_counts_replaces_wholesale(db):
    job = _make_job(db, counts={"total": 10})
    updated = update_counts(db, job.id, {"total": 10, "done": 3})
    assert updated.counts == {"total": 10, "done": 3}


def test_update_counts_missing_job_raises(db):
    with pytest.raises(JobNotFound):
        update_counts(db, 999, {"total": 1})


def test_update_cursor_sets_value(db):
    job = _make_job(db)
    updated = update_cursor(db, job.id, "page_3")
    assert updated.cursor == "page_3"


def test_update_cursor_clears_with_none(db):
    job = _make_job(db)
    update_cursor(db, job.id, "page_3")
    cleared = update_cursor(db, job.id, None)
    assert cleared.cursor is None


# ── retry_job ─────────────────────────────────────────────────────────────


def test_retry_job_creates_new_queued_job(db):
    original = _make_job(db, kind="import_ris", library_id=5)
    transition(db, original.id, "running", allowed=SIMPLE_TRANSITIONS)
    transition(db, original.id, "failed", reason="boom", allowed=SIMPLE_TRANSITIONS)

    retried = retry_job(db, original.id)
    assert retried.id != original.id
    assert retried.status == DEFAULT_STATUS
    assert retried.kind == "import_ris"
    assert retried.library_id == 5
    assert retried.counts == {}


def test_retry_job_does_not_touch_original(db):
    original = _make_job(db)
    transition(db, original.id, "running", allowed=SIMPLE_TRANSITIONS)
    transition(db, original.id, "failed", reason="boom", allowed=SIMPLE_TRANSITIONS)

    retry_job(db, original.id)

    unchanged = get_job(db, original.id)
    assert unchanged.status == "failed"
    assert unchanged.reason == "boom"


def test_retry_job_preserves_parent_job_id(db):
    parent = _make_job(db, kind="batch_tag")
    child = _make_job(db, kind="batch_tag", parent_job_id=parent.id)

    retried = retry_job(db, child.id)
    assert retried.parent_job_id == parent.id


def test_retry_job_missing_raises(db):
    with pytest.raises(JobNotFound):
        retry_job(db, 999)
