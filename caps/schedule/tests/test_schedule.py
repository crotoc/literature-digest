"""caps/schedule 单测。不建库、不联网——这个 cap 本身没有任何 IO。

星期字段用 0（不是直觉上的 1）表示周一——APScheduler 的 `from_crontab`
星期编号是 0=周一...6=周日，不是标准 crontab 的 0=周日，详见
`caps/schedule/README.md`「星期字段编号」一节；这里按实测结果写断言，
不按直觉猜。
"""

from datetime import datetime

import pytest

from caps.schedule import InvalidCronExpression, is_due, next_fire_time, validate_cron


class TestValidateCron:
    def test_valid_expression_does_not_raise(self):
        validate_cron("0 3 * * 0")

    def test_wrong_field_count_raises(self):
        with pytest.raises(InvalidCronExpression):
            validate_cron("0 3 * *")

    def test_out_of_range_value_raises(self):
        with pytest.raises(InvalidCronExpression):
            validate_cron("99 99 * * *")

    def test_garbage_raises(self):
        with pytest.raises(InvalidCronExpression):
            validate_cron("not a cron")


class TestNextFireTime:
    def test_weekly_monday_3am(self):
        # 2026-10-10 是周六；下一个周一 3am 是 2026-10-12。
        after = datetime(2026, 10, 10, 12, 0, 0)
        result = next_fire_time("0 3 * * 0", after=after)
        assert result == datetime(2026, 10, 12, 3, 0, 0)

    def test_strictly_after_not_equal(self):
        # 正好落在触发点上时，下一次是严格之后的那一次，不是自己。
        at_fire_time = datetime(2026, 10, 12, 3, 0, 0)
        result = next_fire_time("0 3 * * 0", after=at_fire_time)
        assert result == datetime(2026, 10, 19, 3, 0, 0)

    def test_naive_datetime_in_naive_datetime_out(self):
        after = datetime(2026, 10, 10, 12, 0, 0)
        result = next_fire_time("0 3 * * 0", after=after)
        assert result.tzinfo is None

    def test_daily_fires_next_day(self):
        after = datetime(2026, 10, 10, 12, 0, 0)
        result = next_fire_time("0 3 * * *", after=after)
        assert result == datetime(2026, 10, 11, 3, 0, 0)

    def test_invalid_expression_raises(self):
        with pytest.raises(InvalidCronExpression):
            next_fire_time("garbage", after=datetime(2026, 10, 10))


class TestIsDue:
    def test_never_run_is_always_due(self):
        assert is_due("0 3 * * 0", last_run_at=None, now=datetime(2026, 10, 10)) is True

    def test_not_yet_due(self):
        last_run = datetime(2026, 10, 12, 3, 0, 0)
        now = datetime(2026, 10, 15, 0, 0, 0)  # 还没到下周一（10-19）
        assert is_due("0 3 * * 0", last_run_at=last_run, now=now) is False

    def test_due_when_next_fire_time_has_passed(self):
        last_run = datetime(2026, 10, 12, 3, 0, 0)
        now = datetime(2026, 10, 20, 0, 0, 0)  # 已经过了下周一（10-19）
        assert is_due("0 3 * * 0", last_run_at=last_run, now=now) is True

    def test_due_exactly_at_next_fire_time(self):
        last_run = datetime(2026, 10, 12, 3, 0, 0)
        now = datetime(2026, 10, 19, 3, 0, 0)
        assert is_due("0 3 * * 0", last_run_at=last_run, now=now) is True
