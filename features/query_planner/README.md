# features/query_planner

把一条研究意图（一句人话）编译成一条结构化检索式。

对应 `refs/v2-module-architecture.md` 的 E3 阶段：`intents` 设置分区的
"编译查询"动作，产出喂给 `features/discovery` 的检索式。

## 对外表面

- `compile_intent_query(db, *, intent_id, provider, model, api_key, base_url=None, transport=None, resolve=None) -> IntentVersionDTO`
- `UnknownProvider`（`ValueError` 子类，`provider` 不认识时抛）
- `PROVIDERS`（`frozenset({"openai_compat", "anthropic"})`）

## `query_json` 的结构是本模块的约定，不是 `domain/intents` 的

`domain.intents.IntentVersion.query_json` 本身只是一个不透明的 JSON 列
——`domain/intents` 自己的 docstring 写明了"内部结构完全由
`features/query_planner`/`features/discovery` 自己解释"。本模块定下的
形状：

```json
{"query": "<单条检索式字符串>", "rationale": "<编译依据，纯展示，override 版本没有这个字段>"}
```

只有一条 `query`，不是"每个来源各自编译一条"——`adapters.sources.
crossref.search`/`adapters.sources.pubmed.search` 的 `query`/`term`
都是自由文本关键词参数，没必要为两个来源分别编译两条几乎一样的检索式。
`features/discovery` 拿到这条 `query` 原样喂给两个 `search()`。

## `query_override` 短路，不调 LLM

`intent.query_override` 非空时，`compile_intent_query` 直接落一条
`source="override"` 的版本（`query_json` 只有 `query`，没有 `rationale`），
完全不碰 LLM——用户已经明确给了检索式。调用方不需要自己先判断
`query_override` 再决定走哪条路径，两种情况都调同一个入口函数。

## 编译失败落一条 `status="failed"` 的版本，不抛异常

这是和 `features/ai_enrichment`/`features/tag_grouping` 刻意不同的地方：
那两个模块的 LLM 调用失败是直接抛 `AppError`，因为点评/分组提案本身
不需要留痕迹。但查询编译的失败**应该**留痕——`get_latest_ready_version()`
永远只看最新一条 `status="ready"` 的版本，所以一次编译失败不会让
"上一个能用的检索式"丢掉，同时设置页能展示"上次编译失败，原因是…"。

真正的配置错误（`provider` 不认识）仍然直接抛 `UnknownProvider`，不落
库——那不是"这次编译失败"，是调用方传错了参数。

## 依赖方向

`adapters/llm`（结构化输出）+ `caps/template`（渲染 prompt）+
`domain/intents`（存取 `IntentVersion`）。不依赖 `domain/connections`——
和 `features/tag_grouping` 的 `propose_ai_groups` 同一个理由：
`provider`/`model`/`api_key`/`base_url` 由调用方（`app/pages/settings`，
复用 `features/ai_enrichment.resolve_ai_profile()`）解析好再传入，本模块
不认识"连接"这个概念。不依赖 `features/discovery`、`features/
ai_enrichment`、`features/tag_grouping`（lint 规则 4）。

## 刻意裁剪的范围

- **不做"多条候选检索式让用户选"**：一次编译只产出一条 `query`，编译
  不满意就重新编译（新建一条版本，旧版本仍留在历史里可以回看）。
- **不做检索式语法校验**（比如"这是不是合法的 PubMed 布尔表达式"）：
  `adapters/sources` 的两个 `search()` 都只是把 `query`/`term` 原样拼进
  请求参数，语法错不错由检索接口自己返回错误，不在编译这一步校验。
- **不管 `lookback_days`/日期范围**：那是 `intent` 本身的字段，`features/
  discovery` 在真正发起检索时直接从 `intent` 读，不经过这里。
