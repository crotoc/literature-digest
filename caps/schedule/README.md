# caps/schedule

cron 触发器，APScheduler 的无表包装。无表、无网络、无后台线程——这个 cap
只是两个纯函数问题的答案："这个 cron 表达式合法吗"和"给定上一次触发时间，
下一次/该不该触发是什么时候/是不是现在"。

## 为什么这么薄

真正"定期执行"需要一个常驻的轮询循环或进程级调度——那是部署层
（`app/`）的决定，这个 cap 不内置、也不应该内置（和 `domain/jobs`
README 里"不做并发/分布式调度"同一条边界）。`domain/schedules` 存
`(target_kind, target_id, cron, enabled, last_run_at)`，谁要调度什么
（比如 E3 的"每个 intent 定期跑一次 discovery"）由各 `features/*` 决定，
这个 cap 和 `domain/schedules` 都不认识任何具体 `target_kind`。

## 对外承诺

```python
from caps.schedule import InvalidCronExpression, validate_cron, next_fire_time, is_due

validate_cron("0 3 * * 1")          # 合法：什么都不返回
validate_cron("not a cron")         # 抛 InvalidCronExpression

next_fire_time("0 3 * * 1", after=some_datetime)   # -> 下一次触发的朴素 UTC datetime

is_due("0 3 * * 1", last_run_at=None, now=datetime.now(UTC))        # -> True（从没跑过，立刻该跑）
is_due("0 3 * * 1", last_run_at=last_run, now=datetime.now(UTC))    # -> 是否已经错过/到了下一个触发点
```

全程假定 UTC；传入的 `datetime` 不要求带 tzinfo（没有就当 UTC），返回值也
是朴素 UTC，和项目其余部分"朴素 UTC 时间"的约定一致（见
`domain/accounts/models.py` 的 `_utcnow()`）。

## 星期字段编号（实测踩坑，务必注意）

APScheduler 的 `CronTrigger.from_crontab()` 解析出来的星期字段是
**`0=周一 ... 6=周日`**，不是大多数人熟悉的标准 crontab 编号
（`0`/`7`=周日、`1`=周一）。实测验证（2026-10-10 是周六）：

```python
CronTrigger.from_crontab("0 3 * * 0", timezone="UTC")  # 周一 3am（不是周日！）
CronTrigger.from_crontab("0 3 * * 1", timezone="UTC")  # 周二 3am（不是周一！）
```

这个 cap 原样转发 `from_crontab` 的解析结果，不做"翻译成标准 crontab
编号"的转换。任何给用户暴露 cron 表达式输入框的页面（`app/pages/settings`
的 schedule 分区）必须在 UI 文案里显式注明这条编号规则，或者干脆不暴露
星期字段的裸数字输入（比如换成"每天"/"每周一"这种按钮，由页面层拼出
正确数字），否则用户填的"周一"实际落在周二。

## 依赖方向

只依赖 `apscheduler`（第三方库，仅用其 `CronTrigger` 的表达式解析和下一次
触发时间计算，不用它的 `Scheduler`/`Job` 这套常驻调度机制）。不依赖任何
`infra`/`domain`/`features`/`app`。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 常驻调度循环（谁来定期调 `is_due()` 并真正执行任务） | 部署层决策，不是这个 cap 的职责 |
| 非 UTC 时区 | 项目全程朴素 UTC 时间约定，没有多时区需求 |
| 秒级/非标准 cron 扩展（`@daily`/`@hourly` 这类别名） | 标准 5 字段 cron 够用，没必要多支持一套语法 |
