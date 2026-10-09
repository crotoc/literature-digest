from domain.usage.service import get_usage, increment_usage, list_usage, reset_usage

ACCOUNT = 1
OTHER_ACCOUNT = 2


# ── get_usage / increment_usage ───────────────────────────────────────────


def test_get_usage_zero_when_never_recorded(db):
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens") == 0


def test_increment_usage_creates_row_with_initial_amount(db):
    counter = increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    assert counter.count == 500
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens") == 500


def test_increment_usage_accumulates(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=300)
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens") == 800


def test_increment_usage_default_amount_is_one(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="fulltext_downloads")
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="fulltext_downloads")
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="fulltext_downloads") == 2


def test_increment_usage_negative_amount_corrects(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=-200)
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens") == 300


def test_usage_scoped_per_account(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    assert get_usage(db, account_id=OTHER_ACCOUNT, period="2026-10", kind="ai_tokens") == 0


def test_usage_scoped_per_period(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-09", kind="ai_tokens", amount=500)
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens") == 0


def test_usage_scoped_per_kind(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="fulltext_downloads") == 0


def test_increment_usage_accepts_weekly_period_format(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-W41", kind="telegram_messages", amount=3)
    assert get_usage(db, account_id=ACCOUNT, period="2026-W41", kind="telegram_messages") == 3


# ── list_usage ────────────────────────────────────────────────────────────


def test_list_usage_scoped_to_account(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    increment_usage(db, account_id=OTHER_ACCOUNT, period="2026-10", kind="ai_tokens", amount=99)

    rows = list_usage(db, account_id=ACCOUNT)
    assert len(rows) == 1
    assert rows[0].count == 500


def test_list_usage_filtered_by_period(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-09", kind="ai_tokens", amount=100)
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=200)

    rows = list_usage(db, account_id=ACCOUNT, period="2026-10")
    assert [r.period for r in rows] == ["2026-10"]


def test_list_usage_filtered_by_kind(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=100)
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="fulltext_downloads", amount=5)

    rows = list_usage(db, account_id=ACCOUNT, kind="ai_tokens")
    assert [r.kind for r in rows] == ["ai_tokens"]


def test_list_usage_without_filters_returns_all(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-09", kind="ai_tokens", amount=100)
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="fulltext_downloads", amount=5)

    assert len(list_usage(db, account_id=ACCOUNT)) == 2


# ── reset_usage ───────────────────────────────────────────────────────────


def test_reset_usage_clears_count(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    reset_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens")
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens") == 0


def test_reset_usage_is_idempotent_when_absent(db):
    reset_usage(db, account_id=ACCOUNT, period="2026-10", kind="never_used")  # 不应报错


def test_reset_usage_then_increment_starts_fresh(db):
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=500)
    reset_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens")
    increment_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens", amount=10)
    assert get_usage(db, account_id=ACCOUNT, period="2026-10", kind="ai_tokens") == 10
