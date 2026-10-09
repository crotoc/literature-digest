# domain/usage

账号级配额计数器，单表 `(account_id, period, kind) -> count`。

## 为什么没有 library_id——不按库乘倍

计划里明确写了这条设计约束："按账号+周期+kind 汇总，不按库乘倍"。一个账号
开了 5 个库不代表它的 AI 调用额度、全文下载额度变成 5 倍——配额是对账号本身
（通常对应一个订阅/套餐）的限制，和这个账号把工作分散在几个库里管理是两件
无关的事。所以 `UsageCounter` 表里压根没有 `library_id` 这一列：调用方
（比如 `features/fulltext` 在某个库里触发了一次下载）只需要传 `account_id`，
不需要、也不能传 library 相关的任何东西进来按库区分计数。

## 本模块只负责记账，不负责判断是否超限

`increment_usage` 只是"把计数加上这么多"，`get_usage` 只是"读出当前计数是
多少"。本模块完全不知道任何一个 `kind` 的配额上限是多少——"这个账号这个月
AI 调用是不是已经超过它套餐允许的次数"这个判断，是调用方自己读出
`get_usage()` 的结果、再和它从 `domain/settings`（或别的配置来源）读出的
上限比较之后做的。把限额判断逻辑放进这里会让 `domain/usage` 知道它不该知道
的业务规则（不同套餐允许多少次？超限之后降级还是拒绝？），这些决定因
`kind` 而异、因套餐而异，属于 `features/*` 的业务逻辑，不属于一张记账表。

## period 是任意字符串，格式由调用方约定

`period` 没有被解析成日期，也没有校验格式——它可以是 `"2026-10"`（按月）、
`"2026-W41"`（按周）、甚至 `"2026-10-08"`（按天），只要调用方自己内部一致。
本模块不负责"周期到了自动清零翻页"这种生命周期管理，调用方想开始新周期的
计数，自然会用新的 `period` 字符串去 `increment_usage`，旧周期的行还在，
可以随时回查历史用量。

## API

```python
increment_usage(db, *, account_id, period, kind, amount=1) -> UsageCounterDTO  # upsert，累加
get_usage(db, *, account_id, period, kind) -> int                             # 没记录过就是 0
list_usage(db, *, account_id, period=_UNSET, kind=_UNSET) -> list[UsageCounterDTO]
reset_usage(db, *, account_id, period, kind) -> None                           # 幂等删除
```

`get_usage` 对"没有记录"返回 `0` 而不是抛异常——"这个账号这个月还没用过这个
kind"是最常见的正常状态，不是错误，这和 `domain/notes.get_note` 对"还没写过
笔记"的处理是同一种设计倾向（两处都在各自 README 里做了相同的说明）。

`increment_usage` 的 `amount` 允许负数，用于处理计费纠错这类"发现上次多记
了，扣回去"的场景——本模块不限制只能加不能减，纠错是否合理由调用方判断。

## 依赖方向

只依赖 `infra.db`（`Session` 类型）。不依赖任何其他 `domain/*`、`caps/*`、
`adapters/*`、`features/*`。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 配额上限校验/超限拒绝 | 属于调用方业务逻辑（因 kind、因套餐而异），本模块只记账不裁决 |
| 周期自动翻页/清零 | `period` 是调用方自定义字符串，生命周期管理交给调用方 |
| 按库拆分计数 | 计划明确要求"不按库乘倍"，账号级聚合是设计目标，不是遗漏 |
| 用量历史趋势/图表聚合查询 | 没有明确需求；`list_usage` 配合调用方自己按需要的周期多次查询已经够用 |
