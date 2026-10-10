# domain/intents

研究意图（"我想持续关注哪类文献"）+ 它的查询编译历史 + 每轮自动发现的
执行记录 + 发现到的候选文献 + 跨轮去重标记。五张表：`intents` /
`intent_versions` / `discovery_runs` / `candidates` / `intent_seen`。

## 账号级，不是库级

`intents.account_id` 直接对应账号，不带 `library_id`——这是跟
`domain/connections`（账号级共享设置）而不是跟 `domain/tags`/`domain/
folders`（库级）同一类归属，和 `app/pages/settings` 的分区一样"全账号级
共享"。研究意图发现到的候选文献要不要真正收进某个库，是 `features/
reports`（E4）展示结果后用户的显式选择，不是 intent 定义时就要绑定的
属性。

## 为什么查询要版本化（`intent_versions`）

`features/query_planner` 用 LLM 把 `intents.question_text`（一句自然语言
研究问题）编译成每个数据源各自的查询语法（`query_json`，比如
`{"crossref": "...", "pubmed": "..."}`）。重新编译（intent 改了措辞、
或者用户手动点"重新生成查询"）不覆盖旧版本——`intent_versions` 按时间
追加，`get_latest_ready_version()` 只取最新那条 `status="ready"` 的。这
样能看到"这次发现用的到底是哪个版本的查询"，编译失败（LLM 调用出错）也
留得下痕迹（`status="failed"` + `error`），不会覆盖掉上一个还能用的版本。

`intents.query_override` 是用户手写的原始查询字符串，设置了就跳过 LLM
编译，直接拿它当查询——`features/query_planner` 自己决定什么时候读这个
字段、什么时候调 LLM，这个模块只负责存。

## 为什么发现运行记录（`discovery_runs`）单独一张表

一次"发现"是对多个数据源（Crossref/PubMed/…）各查一遍、算出"这次新发现
了几条、一共匹配了几条"的一次性操作，不是逐条文献的多步状态机（不像
`domain/jobs` README 里"长流程批量"类的全文下载/AI 评估那样每项要能单独
重试）——所以 `discovery_runs` 只有粗粒度的 `status`
(`running`/`succeeded`/`failed`) + 两个汇总计数，没有子状态机。仍然绑一个
`domain.jobs` 的 `job_id`（裸 int，跨 domain 引用，不建 FK）是为了让它
出现在账号统一的"后台任务"列表里，具体怎么用这个 job_id 由
`features/discovery` 决定。

## `candidates` 只存"新发现的"，不存重复命中的

`intent_seen` 记录"这个 intent 见过这条来源记录了"（`(intent_id, source,
source_id)` 唯一）。`features/discovery` 每次查完数据源，先用
`is_seen()` 过滤掉已经见过的，只给没见过的调 `create_candidate()` +
`mark_seen()`——这样 `candidates` 表里永远是"净新增"的候选，不会在每轮
发现里把同一篇文献重复插入一遍。`discovery_runs.candidate_count`（本轮
数据源一共返回了多少条，含重复）和 `new_candidate_count`（刨掉已见过的，
真正新增了多少条）分别记录，两个数字的差值就是"本轮被去重掉的数量"。

## 依赖方向

依赖 `infra.db`（`Session`/`Base`）+ `infra.errors`（`NotFound`）。不依赖
任何其他 `domain/*`、不依赖 `adapters/*`/`features/*`/`caps/*`——本模块
不知道 LLM 怎么调、数据源怎么查、`job_id` 对应的 job 是什么状态，这些全部
是调用方（`features/query_planner`/`features/discovery`）的知识。

## API

```python
# 研究意图
create_intent(db, *, account_id, name, question_text, candidate_limit=40, lookback_days=7, query_override=None) -> IntentDTO
get_intent(db, intent_id) -> IntentDTO                              # 不存在抛 IntentNotFound
update_intent(db, intent_id, **fields) -> IntentDTO                 # 部分更新，未传的字段不变
delete_intent(db, intent_id) -> None                                 # 级联删版本/运行记录/候选/去重标记
list_intents(db, *, account_id) -> list[IntentDTO]

# 查询编译版本
create_intent_version(db, *, intent_id, query_json, source, model=None, status="ready", error=None) -> IntentVersionDTO
get_latest_ready_version(db, intent_id) -> IntentVersionDTO | None   # 没有 ready 版本时返回 None
list_intent_versions(db, intent_id) -> list[IntentVersionDTO]       # 按时间倒序，含失败的版本

# 发现运行记录
create_discovery_run(db, *, intent_id, intent_version_id, job_id) -> DiscoveryRunDTO   # status="running"
finish_discovery_run(db, run_id, *, status, candidate_count, new_candidate_count, error=None) -> DiscoveryRunDTO
get_discovery_run(db, run_id) -> DiscoveryRunDTO                     # 不存在抛 DiscoveryRunNotFound
list_discovery_runs(db, *, intent_id, limit=20) -> list[DiscoveryRunDTO]   # 按开始时间倒序

# 候选文献 + 跨轮去重
is_seen(db, *, intent_id, source, source_id) -> bool
mark_seen(db, *, intent_id, source, source_id) -> None               # 幂等
create_candidate(db, *, discovery_run_id, intent_id, source, source_id, title=None, url=None, payload=None) -> CandidateDTO  # 幂等（同一候选重复创建返回已有的）
list_candidates(db, *, intent_id, discovery_run_id=None, limit=100) -> list[CandidateDTO]   # 按创建时间倒序
```

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 候选文献与 `domain/works` 的去重比对（标题/标识符撞库） | 那是"要不要真的收进某个库"这一步的知识，属于 `features/reports`（E4）或后续的导入流程，不是发现阶段的职责——发现阶段只负责"这条来源记录我之前见过没有" |
| `query_json` 的结构校验 | 结构因数据源而异，交给 `features/query_planner`/`features/discovery` 自行约定和解释 |
| 一个 intent 挂多个账号/共享给其他账号 | v1 没有这种协作场景 |
| 发现运行的子级状态机（比如"正在查 crossref"/"正在查 pubmed"） | 规模小（固定两三个数据源），不需要 `domain/jobs` 那种"长流程批量每项一行"的粒度，`discovery_runs` 的粗粒度 status 够用 |
