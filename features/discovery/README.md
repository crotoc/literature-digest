# features/discovery

拿 `features/query_planner` 编译好的检索式，去 Crossref/PubMed 跑一轮
搜索，把"没见过的"结果落成 `domain.intents.Candidate`。

对应 `refs/v2-module-architecture.md` 的 E3 阶段：首页"立即运行"按钮 +
`intents` 设置分区的调度触发，最终都落到本模块的 `run_discovery`。

## 对外表面

- `run_discovery(db, *, account_id, intent_id, today=None, transport=None, resolve=default_resolve) -> DiscoveryRunDTO`
- `run_due_intents(db, *, as_of=None, transport=None, resolve=default_resolve) -> list[DiscoveryRunDTO]`
- `IntentDisabled` / `NoReadyQuery`（都是 `ValueError` 子类，前置校验失败时抛）
- `SOURCES`（`("crossref", "pubmed")`）
- `JOB_KIND`（`"discovery_run"`）

## 两层去重，缺一不可

1. **`domain.intents.is_seen()`**——"这个 intent 这一轮之前见过这条
   `(source, source_id)` 吗"，跨轮增量的核心机制：没有它，同一个检索式
   每次重新跑都会把上一轮已经播报过的旧文献再播报一遍。
2. **"已经在库里了吗"**（本模块自己加的一层）——用 `domain.works.
   find_by_identifier` 按 DOI/PMID 查这个账号能看到的**所有**库
   （`domain.libraries.list_libraries_for_account`），命中就直接
   `mark_seen` 跳过、不建候选。`domain/intents` 自己不会做这一层检查
   （它刻意不认识 `domain.works`），但研究雷达不该把用户已经手动收进库
   里的文献当"新发现"再播报一遍，所以放在这里。两层共用同一张
   `intent_seen` 标记表，命中任一层都会写标记，下一轮两层都不用再查。

## 失败是否抛异常，分两种情况

- **前置校验失败**（`intent.enabled == False`，或者还没有一条
  `status == "ready"` 的检索式版本）：直接抛 `IntentDisabled`/
  `NoReadyQuery`，不建 `domain.jobs.Job`、不建 `DiscoveryRun`——这时候
  还没发生任何检索动作，不是"这次运行失败了"，是"根本没法开始这次运行"，
  不值得留一条历史记录。
- **检索请求本身失败**（`CrossrefError`/`PubMedError`）：这时候 job 和
  `DiscoveryRun` 都已经建好了，不会再抛异常——落一条 `status="failed"`
  的 `DiscoveryRun`（`error` 字段记原因）直接返回。这是一次可能被 cron
  触发的后台动作，没有人在同步等着异常被 catch，失败本身需要留痕让用户
  在设置页能看到"上次跑失败了，原因是…"。

**不做"按来源的部分失败"处理**：Crossref/PubMed 任一个报错，整次运行
直接记为失败，不保留另一个来源已经查到的那部分结果——`DiscoveryRun`
本来就只有粗粒度的 `running`/`succeeded`/`failed`，没有"部分成功"这个
状态，硬做只会让"这次到底算成功还是失败"变得含糊。

## `run_due_intents`：谁认领 `domain.schedules` 的哪个 `target_kind`

`domain.schedules.list_due_schedules()` 是跨全部账号、不筛 `target_kind`
的通用查询（它自己的 README 写明"谁来认领哪个 target_kind 是调用方的
事"）。本模块就是"认领 `target_kind == 'intent'`"的那个调用方——E4 的
`reports`/`delivery` 如果也要接调度，会是另一个独立的调用方筛另一个
`target_kind`，彼此不需要知道对方的存在，也不需要改 `domain/schedules`
一行代码。

单个到期意图的前置校验失败（比如调度还留着、但意图后来被停用了）不会
打断整批扫描——跳过它，照样标记 `last_run_at`（不标记的话下次轮询会
重新认领同一个坏掉的调度），继续跑其余到期的意图。

**真正的轮询循环不在这里**：谁来定期调 `run_due_intents()`（一个后台
线程？`app/main.py` 启动时注册的定时任务？管理员手动点的一个按钮？）
是部署/`app/` 层的事——和 `caps/schedule`、`domain/schedules` 自己刻意
不做轮询循环是同一个理由。

## `candidate_limit` 怎么生效

`intent.candidate_limit` 是这一轮**最多新建多少条候选**。两个来源的
`search()` 调用本身按 `rows`/`retmax=candidate_limit` 请求（不会请求
远超需要的数量），遍历结果时按来源顺序（先 Crossref 再 PubMed）数，
新候选数够了就不再看剩下的结果——"看了但因为去重跳过"的不会让这一轮
停不下来（它们不计入新候选数，但也不会被无限遍历，因为请求本身已经
按 `candidate_limit` 限了量）。

## 依赖方向

`adapters/sources/{crossref,pubmed}` + `domain/{intents,works,libraries,
connections,jobs,schedules}`。不依赖 `features/query_planner`——本模块
只读 `IntentVersion.query_json["query"]` 这一个字符串字段，不关心它是
AI 编译出来的还是用户手动指定的（`source == "ai"` 还是 `"override"`），
两个 feature 之间不需要互相认识（lint 规则 4）。

## 刻意裁剪的范围

- **不做"多个来源各自不同检索式"**：两个来源共用 `query_planner` 编译
  出的同一条 `query`，理由见 `features/query_planner/README.md`。
- **不做实时进度上报**：`run_discovery` 是一次同步调用，没有子 job——
  一轮发现只有两次 HTTP 请求（各来源一次），不是"长流程批量"，不需要
  `domain/jobs` 的子 job 粒度。
- **不管 Candidate 之后怎么被人审阅/投递**：那是 E4 `features/reports`
  的职责，本模块只负责"发现并记录"。
