"""caps/schedule 实现：cron 表达式校验 + "下一次触发时间"计算，APScheduler
的无表包装。

这个 cap 不跑任何常驻调度循环——它只回答两个纯函数问题："这个 cron 表达式
合法吗"和"给定上一次触发时间，下一次是什么时候"。谁来定期调用 `is_due()`
并真正执行任务（一个后台轮询循环，还是部署层的系统 cron），是装配层
（`app/`）的决定，不是这个 cap 的职责——和 `domain/jobs` README 里"不做
并发/分布式调度"同一条边界。

全程假定 UTC，和项目其余部分"朴素（无 tzinfo）UTC 时间"的约定一致（见
`domain/accounts/models.py` 的 `_utcnow()`）：传入的 `datetime` 不要求带
tzinfo（没有就当作 UTC），返回的 `datetime` 也是朴素 UTC，不往上暴露
APScheduler 内部用的带时区对象。
"""

from __future__ import annotations

from datetime import UTC, datetime

from apscheduler.triggers.cron import CronTrigger


class InvalidCronExpression(ValueError):
    """`expr` 不是一个合法的 5 字段 cron 表达式。"""


# 踩坑记录：APScheduler 的 `CronTrigger.from_crontab()` 星期字段用的是
# 0=周一...6=周日，不是标准 crontab 的 0=周日。实测验证见
# `caps/schedule/README.md`「星期字段编号」一节；这里只是提醒——这个 cap
# 原样转发 `from_crontab` 的解析结果，不打算在这一层做"翻译成标准 crontab
# 编号"的额外转换（没有证据表明这个项目的调用方需要标准 crontab 兼容性，
# 徒增一层容易出错的映射）。


def _trigger(expr: str) -> CronTrigger:
    try:
        return CronTrigger.from_crontab(expr, timezone="UTC")
    except ValueError as exc:
        raise InvalidCronExpression(f"不合法的 cron 表达式 {expr!r}：{exc}") from exc


def _to_aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def validate_cron(expr: str) -> None:
    """校验一个 cron 表达式。不合法抛 `InvalidCronExpression`，合法什么都不
    返回——调用方（`domain/schedules`）只需要知道"抛了就是不合法"。
    """
    _trigger(expr)


def next_fire_time(expr: str, *, after: datetime) -> datetime:
    """`expr` 描述的 cron 表达式，在严格晚于 `after` 的时间点里最早的一次
    触发时间。`expr` 不合法时抛 `InvalidCronExpression`。
    """
    trigger = _trigger(expr)
    aware_after = _to_aware(after)
    next_time = trigger.get_next_fire_time(aware_after, aware_after)
    return next_time.astimezone(UTC).replace(tzinfo=None)


def is_due(expr: str, *, last_run_at: datetime | None, now: datetime) -> bool:
    """这个 schedule 现在该不该跑。

    `last_run_at` 是 `None`（从没跑过）视为立刻该跑一次——这是常见的"注册
    就先跑一次，之后再按 cron 周期走"的启动语义，不强迫用户等到第一个 cron
    触发点。否则，只要 `next_fire_time(expr, after=last_run_at)` 落在
    `now` 之前或恰好等于 `now`，就说明已经错过（或正好到）至少一个该触发
    的时间点，视为该跑。
    """
    if last_run_at is None:
        return True
    return next_fire_time(expr, after=last_run_at) <= now
