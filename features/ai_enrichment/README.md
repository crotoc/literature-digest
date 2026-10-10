# features/ai_enrichment

LLM 对单篇/批量文献的打标签 + 点评，以及它自己的 `ai_profile` 连接管理。

## `ai_profile` 的 CRUD 为什么长在这里

`domain/connections` 四种 kind（`ai_profile` / `telegram_destination` /
`source_credential` / `download_proxy`）共用一张表，但`features/
connection_setup` 自己的 README 明确写了"怎么管理某一种 kind 是业务知
识"——v1 只实现了 `source_credential`，其余三种"留给 E2/E4/E5 阶段各自
的 feature 自己开一份同构但独立的管理模块"。本模块就是 `ai_profile` 那
份：`create_ai_profile`/`list_ai_profiles`/`update_ai_profile`/
`rotate_ai_profile_key`/`delete_ai_profile`/`set_default_ai_profile`/
`check_ai_profile`，和 `connection_setup` 管理 `source_credential` 的那
一组函数结构对称。`_secret_key()`/`derive_key(..., purpose="connections")`
这个常量/约定是刻意重复的一份（features 之间禁止互相 import）。

`config` 字段形状：`{"provider": "openai_compat"|"anthropic", "model": str, "base_url"?: str}`，
`api_key` 永远加密存（和 `source_credential` 的 crossref/pubmed 两条路径
不同，`ai_profile` 不存在"这种 provider 不需要凭据"的情况）。

## 对外暴露什么（见 `contract.py`）

- `assess_work(db, *, account_id, library_id, work_id, connection_id, period=None, transport=None, resolve=None) -> AssessmentResult`
  —— 单篇点评。
- `bulk_assess_works(db, *, account_id, library_id, work_ids, connection_id, ...) -> BatchAssessResult`
  —— 批量点评，"长流程批量"粒度（见下）。
- `AssessmentResult(work_id, summary, relevance, novelty, tags, model, provenance_id)`。
- `AiEnrichmentError`（`AppError` 子类，502）：LLM 调用失败。
- `JOB_KIND_BULK_ASSESS` / `JOB_KIND_ASSESS_ITEM`。
- `KIND = "ai_profile"`，`PROVIDERS = {"openai_compat", "anthropic"}`。

## AI 点评结果怎么落库

`domain/works` 没有、也不该新增 AI 专用字段。点评结果（summary/
relevance/novelty + 原始 payload）走已有的 `record_provenance(work_id,
source="ai_enrichment", ...)` 追加一条历史记录——`WorkProvenance` 本身
"只追加不去重"（见它自己的 docstring），每次重新点评是一条新记录，不是
覆盖"当前状态"；想看"最新一次"就取 `list_provenance()` 里第一条
`source == "ai_enrichment"` 的。

AI 建议的标签是真标签，走 `domain/tags.create_tag`（同名幂等）+
`add_tag_to_work`——这样"AI 重新打标签"产生的标签和手动打的标签完全一
样，侧栏筛选、三态聚合都直接能用，不需要任何特殊通道。

## `caps/template` 和 `caps/redact` 用在哪

- `caps/template.render_jinja()` 渲染发给 LLM 的 prompt——和 `caps/
  template` 自己 README 里"Jinja（给 prompt 用）"直接对应，不是手工拼
  f-string。
- `caps/redact` 用两处：
  1. 发出去之前：`redact_text(prompt, policy=with_known_values([api_key]))`
     ——纯防御性的一步，正常情况下 `api_key` 不会出现在文献标题/摘要里，
     但万一真出现了（比如用户粘贴笔记时手滑），不该让它原样发给第三方
     LLM 服务。
  2. 存库之前：`redact_mapping()` 过一遍要写进 `payload_json` 的结构化
     结果——这条记录以后会在页面上展示，和日志是同一类"会被人看到的
     输出"。

## job 粒度：为什么批量点评和 `features/organizing` 不一样

单篇点评 `assess_work()` 不建 job——一次同步调用，失败直接抛异常，没有
"部分成功"的概念。

批量点评 `bulk_assess_works()` 走计划「两处已定」里**"长流程批量"**这
一类：**每项一个子 job**，不是像 `features/organizing` 的"瞬时批量"那样
只有失败才建子行。原因：AI 调用是真正的网络 I/O，慢且可能单项超时，调
用方需要能单独看每一项的状态——这和"打标签/去文件夹"这种纯本地数据库
操作不是同一类。

连接解析（provider/model/api_key）在批量开始前**只做一次**，不是每项重
新解析一遍——一个连接配置错误不应该制造出一整批内容相同的失败子 job，
而是在建任何 job 之前就直接拒绝（和 `features/organizing.bulk_add_tag`
"标签不属于这个库时在建 job 前就拒绝"是同一个处理原则）。

## 依赖方向

`adapters/llm`（`openai_compat`/`anthropic`）+ `caps/{template,redact,secrets,httpfetch,probe}`
+ `domain/{works,tags,connections,jobs,usage}`。本模块不开表。

provider 的分发（`openai_compat` ↔ `anthropic`）是本模块自己维护的一张
小表，和 `features/tag_grouping` 各自维护一份是同一个理由——`adapters/
llm` 的两个模块之间没有共享 `contract.py`。

## 刻意裁剪的范围

| 不做 | 为什么 |
|---|---|
| AI 结果的"当前状态"覆盖式存储 | `WorkProvenance` 只追加，和它自己的设计一致；要看最新一次就取第一条 |
| 用量超限自动拒绝 | `domain/usage` 本身"只记账，不判断配额"——配额上限比较是调用方（页面层）的职责 |
| 并发批量点评 | v1 顺序执行；旧代码报告分析那条路径用了 `ThreadPoolExecutor`，这里先不引入并发复杂度 |
| 自定义 prompt 模板 | 固定一套中文模板；真要可配置是以后的事 |
| LLM 调用重试/修正 | 和 `adapters/llm` 自己的裁剪一致——校验失败算一次失败 |

## 测试

`pytest features/ai_enrichment -q`，内存 SQLite + `httpx.MockTransport`，
不依赖真实网络或真实 API key。
