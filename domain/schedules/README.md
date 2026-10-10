# domain/schedules

通用的"这个目标该多久跑一次"追踪表，和 `domain/jobs` 同一种"刻意无知"的
设计：本模块不知道 `target_kind` 的任何具体业务含义，只提供一张
`(target_kind, target_id)` 唯一的 cron 调度骨架，"该不该跑"的数学交给
`caps/schedule`。

## 为什么一个目标只有一条调度

`UniqueConstraint(target_kind, target_id)`——`upsert_schedule()` 对同一个
目标重复调用是"编辑"，不会堆出第二条记录。v1 唯一的消费方是
`features/discovery`（`target_kind="intent_discovery"`，`target_id` 是
`intents.id`），一个 intent 只需要一条"多久重新发现一次"的调度，没有"一个
目标挂多条调度"的真实需求；真有这种需求时再放开约束，不要提前设计。

## `list_due_schedules()` 为什么在 Python 里过滤

`caps.schedule.is_due()` 要解析 cron 表达式才能判断，这段逻辑没法翻译成
SQL WHERE 条件。所以这个函数先用 `enabled=True` 在数据库层缩小范围，再在
Python 里逐条调 `is_due()`。v1 规模下调度条数（每个账号几个 intent）不会
多到这样做有性能问题，真的变成问题时再考虑把 cron 下一次触发时间物化成
一个可查询的列。

## 谁来真正触发

这个模块只回答"现在该跑的有哪些"，不负责真正执行、不负责常驻轮询——和
`caps/schedule` 本身"不内置调度循环"同一条边界的延伸。实际的"定期调一次
`list_due_schedules()` 然后对每条该跑的调用对应 feature 的入口函数"，是
装配层（`app/`）的职责（E3 阶段落地为一个简单的后台轮询任务）。

## 依赖方向

依赖 `infra.db`（`Session`/`Base`）+ `infra.errors`（`NotFound`）+
`caps.schedule`（cron 合法性校验 + 该不该跑的判断）。不依赖任何其他
`domain/*`、不依赖 `adapters/*`/`features/*`。

## API

```python
upsert_schedule(db, *, account_id, target_kind, target_id, cron, enabled=True) -> ScheduleDTO
get_schedule(db, *, target_kind, target_id) -> ScheduleDTO | None
delete_schedule(db, *, target_kind, target_id) -> None          # 目标没配过调度时是空操作
mark_run(db, *, target_kind, target_id, when=None) -> ScheduleDTO  # 不存在抛 ScheduleNotFound
list_schedules(db, *, account_id) -> list[ScheduleDTO]
list_due_schedules(db, *, as_of=None) -> list[ScheduleDTO]      # 跨全部账号
```

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 常驻调度循环/任务队列 | 和 `caps/schedule`、`domain/jobs` 同一条边界，属于部署层决策 |
| 一个目标挂多条调度 | v1 没有真实需求，`UniqueConstraint` 先约束住 |
| 把 cron 下一次触发时间物化成列 | v1 规模下 Python 过滤够快，不提前做这个优化 |
