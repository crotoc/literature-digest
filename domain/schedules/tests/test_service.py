from datetime import datetime

import pytest

from caps.schedule import InvalidCronExpression
from domain.schedules import (
    ScheduleNotFound,
    delete_schedule,
    get_schedule,
    list_due_schedules,
    list_schedules,
    mark_run,
    upsert_schedule,
)

ACCOUNT = 1
OTHER_ACCOUNT = 2

# 0=周一（APScheduler from_crontab 的编号，见 caps/schedule/README.md）
WEEKLY_MONDAY = "0 3 * * 0"
DAILY = "0 3 * * *"


def test_upsert_creates_new_schedule(db):
    schedule = upsert_schedule(
        db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=WEEKLY_MONDAY
    )
    assert schedule.account_id == ACCOUNT
    assert schedule.target_kind == "intent_discovery"
    assert schedule.target_id == 7
    assert schedule.cron == WEEKLY_MONDAY
    assert schedule.enabled is True
    assert schedule.last_run_at is None


def test_upsert_is_idempotent_on_target(db):
    first = upsert_schedule(
        db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=WEEKLY_MONDAY
    )
    second = upsert_schedule(
        db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY, enabled=False
    )
    assert second.id == first.id
    assert second.cron == DAILY
    assert second.enabled is False
    assert len(list_schedules(db, account_id=ACCOUNT)) == 1


def test_different_target_id_creates_separate_row(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=8, cron=DAILY)
    assert len(list_schedules(db, account_id=ACCOUNT)) == 2


def test_different_target_kind_same_id_creates_separate_row(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    upsert_schedule(db, account_id=ACCOUNT, target_kind="other_kind", target_id=7, cron=DAILY)
    assert len(list_schedules(db, account_id=ACCOUNT)) == 2


def test_upsert_invalid_cron_raises(db):
    with pytest.raises(InvalidCronExpression):
        upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron="garbage")


def test_get_schedule_missing_returns_none(db):
    assert get_schedule(db, target_kind="intent_discovery", target_id=999) is None


def test_get_schedule_found(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    found = get_schedule(db, target_kind="intent_discovery", target_id=7)
    assert found is not None
    assert found.target_id == 7


def test_delete_schedule_removes_row(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    delete_schedule(db, target_kind="intent_discovery", target_id=7)
    assert get_schedule(db, target_kind="intent_discovery", target_id=7) is None


def test_delete_schedule_missing_is_noop(db):
    delete_schedule(db, target_kind="intent_discovery", target_id=999)  # 不抛


def test_mark_run_missing_raises(db):
    with pytest.raises(ScheduleNotFound):
        mark_run(db, target_kind="intent_discovery", target_id=999)


def test_mark_run_sets_last_run_at(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    when = datetime(2026, 10, 10, 3, 0, 0)
    updated = mark_run(db, target_kind="intent_discovery", target_id=7, when=when)
    assert updated.last_run_at == when


def test_mark_run_defaults_to_now(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    updated = mark_run(db, target_kind="intent_discovery", target_id=7)
    assert updated.last_run_at is not None


def test_list_schedules_scoped_to_account(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    upsert_schedule(db, account_id=OTHER_ACCOUNT, target_kind="intent_discovery", target_id=8, cron=DAILY)
    assert [s.target_id for s in list_schedules(db, account_id=ACCOUNT)] == [7]


def test_list_due_schedules_never_run_is_due(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    due = list_due_schedules(db, as_of=datetime(2026, 10, 10, 12, 0, 0))
    assert [s.target_id for s in due] == [7]


def test_list_due_schedules_excludes_disabled(db):
    upsert_schedule(
        db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY, enabled=False
    )
    due = list_due_schedules(db, as_of=datetime(2026, 10, 10, 12, 0, 0))
    assert due == []


def test_list_due_schedules_excludes_not_yet_due(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    mark_run(db, target_kind="intent_discovery", target_id=7, when=datetime(2026, 10, 10, 3, 0, 0))
    due = list_due_schedules(db, as_of=datetime(2026, 10, 10, 12, 0, 0))  # 还没到第二天 3am
    assert due == []


def test_list_due_schedules_includes_past_due(db):
    upsert_schedule(db, account_id=ACCOUNT, target_kind="intent_discovery", target_id=7, cron=DAILY)
    mark_run(db, target_kind="intent_discovery", target_id=7, when=datetime(2026, 10, 10, 3, 0, 0))
    due = list_due_schedules(db, as_of=datetime(2026, 10, 12, 0, 0, 0))
    assert [s.target_id for s in due] == [7]
