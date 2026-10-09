# domain/jobs

所有后台/批量操作的通用追踪表。本模块刻意对"这是什么任务"一无所知——它不知道
`import_ris` 和 `fulltext_download` 的区别，只提供一张通用的状态机骨架，具体
语义全部由调用方（各 `features/*`）注入。

## 状态机设计：status + reason 两个独立字段

旧代码（`fulltext/states.py`）把状态字段和错误消息混在一起——比如
`"waiting_browser"` 这个值既是数据库里的状态、又被直接当错误文案显示给用户。
这是错的：状态应该是一个有限、粗粒度、本模块定义的集合，错误/卡点原因应该是
一个细粒度、调用方自定义、本模块只存不解释的字符串。所以拆成两个字段：

- `status`：`STATUSES = {"queued", "running", "succeeded", "failed", "cancelled"}`，
  `TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}`。本模块定义这个
  集合，所有 kind 共用。
- `reason`：任意字符串，调用方自己定义意义（比如 `"no_candidate"` /
  `"network_timeout"` / `"awaiting_resume"`），`domain/jobs` 只负责存下来，
  从不对它做任何判断或分支。

## 转移表由调用方传入，不是本模块内置的

`transition(db, job_id, to_status, *, reason=None, allowed)` 的 `allowed`
参数是调用方自己声明的转移表，形如：

```python
ALLOWED = {"queued": {"running", "cancelled"}, "running": {"succeeded", "failed"}}
transition(db, job.id, "running", allowed=ALLOWED)
```

每个 `features/<x>` 在自己的 `contract.py` 里声明"我这种 job 允许从哪个状态
走到哪个状态"，调用 `jobs.transition()` 时把这张表作为数据传进来。这样
`domain/jobs` 永远不需要 import 任何 feature、不需要认识任何具体 kind 的转移
规则——否则这个模块迟早会变成一个按 kind 分支的大 switch。不在 `allowed` 里
声明的转移（包括任何企图从终态走出去的转移）一律抛 `InvalidTransition`。

进入 `"running"` 时若 `started_at` 还是空的会被自动填上；进入任意终态时
`finished_at` 自动填上。这两个时间戳字段本模块自己管理，调用方不能直接改。

## 重试 = 新建一个 job，不倒退旧的

`retry_job(db, job_id)` 不会修改原 job 的任何字段——原 job 原样留在它最终
落到的终态上，作为"这一次尝试"的历史记录。重试的效果是创建一个全新的
`queued` job（继承同样的 `kind` / `account_id` / `library_id` / `parent_job_id`，
`counts` 清空），从头开始跑一次完整的状态机。想看"这个东西到底重试了几次"，
查 `parent_job_id` 相同、`kind` 相同的一串 job 即可，不需要在单个 job 行上
维护一个 `retry_count` 字段。

## 子 job 粒度：两类规则

批量操作分两种，子 job 该不该开一行的规则不同（否则 5 万条批量会往 `jobs`
表插 5 万行）：

| 类型 | 例子 | 子 job 策略 |
|---|---|---|
| **瞬时批量** | 打标签 / 去标签 / 加移文件夹 / 删除 / 导出 | 父 job 一行 + `counts_json`（`update_counts`）；**只有失败的那些才建子行**，成功的不留痕 |
| **长流程批量** | 全文下载 / AI 评估 / 导入 | **每一项都是一个子 job**——每项有自己的多步状态机、自己的 `reason`、可能卡在某个中间状态等人处理，必须能单独看、单独重试 |

两类都天然满足失败隔离（某个子项/某次失败不影响其余），区别只在于"要不要为
每一项都付一行数据库记录的代价"。`list_child_jobs(db, parent_job_id)` 按创建
顺序（非 `list_jobs` 默认的"最新在前"）返回，因为子 job 通常代表一串待处理
项，调用方关心的是处理顺序。

## API

```python
create_job(db, *, account_id, kind, library_id=None, parent_job_id=None, counts=None) -> JobDTO
get_job(db, job_id) -> JobDTO                                            # 不存在抛 JobNotFound
list_jobs(db, *, account_id, kind=_UNSET, status=_UNSET, parent_job_id=_UNSET) -> list[JobDTO]
list_child_jobs(db, parent_job_id) -> list[JobDTO]                        # 按创建顺序
transition(db, job_id, to_status, *, reason=None, allowed) -> JobDTO
update_counts(db, job_id, counts: dict) -> JobDTO                        # 整体替换，不合并
update_cursor(db, job_id, cursor: str | None) -> JobDTO
retry_job(db, job_id) -> JobDTO                                          # 新建一个 queued job，不改旧的
```

`update_counts` 是整体替换而不是合并——调用方每次上报一份完整的计数快照
（比如 `{"total": 100, "done": 80, "skipped": 5, "failed": 15}`），不支持
"只更新其中一个字段"，避免调用方需要先读再改造成的竞态。

## 依赖方向

只依赖 `infra.db`（`Session` 类型）+ `infra.errors`（`NotFound`/`Conflict` 基类）。
不依赖任何其他 `domain/*`，不依赖任何 `caps/*`、`adapters/*`、`features/*`——
这是本模块"不认识任何具体业务"这条设计原则在依赖图上的体现。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 内置任何具体 kind 的转移规则 | 会让本模块认识它不该认识的业务语义；转移表改由调用方声明并传入 |
| 并发/分布式调度（谁去抢一个 queued job 执行） | v1 同步执行，不需要；真正的任务队列/worker 池属于以后的部署层决策，不是这张表的职责 |
| 单个 job 上维护 `retry_count` | 查 `parent_job_id` + `kind` 相同的一串历史 job 就是重试次数，不需要额外字段 |
| `counts_json` 的字段结构校验 | 结构因 kind 而异，交给调用方自行约定和解释 |
