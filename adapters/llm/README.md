# adapters/llm

结构化输出的 LLM 适配器：`openai_compat.py`（OpenAI Chat Completions API
及其广泛兼容的第三方实现）、`anthropic.py`（Anthropic Messages API）。
v1 只做这两个（E2 阶段首个落地的 adapter 类）。

## 为什么是"结构化输出"而不是"聊天"

`features/tag_grouping`/`features/ai_enrichment`/E3 的 `query_planner`/
E4 的 `reports` 这些上层功能要的从来不是"模型说了什么话"，是"给我一个
符合这个 pydantic 模型形状的结果"（一组标签提案、一次元数据评分、一份
筛选结论）。所以两个适配器只导出一个 `complete_structured(...)`，不是
更通用的"发消息、拿纯文本"——那种更底层的接口此刻没有调用方需要，加出来
也只是摆在那没人用。

两个适配器对外签名刻意保持一致：

```python
complete_structured(
    *,
    response_model: type[T],       # 一个 pydantic BaseModel 子类
    messages: Sequence[Mapping[str, str]],   # [{"role": "user"/"assistant", "content": "..."}]
    model: str,
    api_key: str,
    system: str | None = None,
    base_url: str = <各自默认值>,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    rate_limiter: RateLimiter | None = None,
    transport=None,    # 测试钩子
    resolve=default_resolve,   # 测试钩子
) -> T
```

`features/ai_enrichment` 按 `domain/connections` 里 `ai_profile` 连接的
`config["provider"]` 字段选用哪个适配器模块，调用方式不用跟着变——这正是
"可插拔、同类多实现、删一个其余全绿"（lint 规则 7）的落地方式。

## 两边"结构化输出"的做法不同，是故意的

- **`openai_compat.py` 用 JSON mode**（`response_format: {"type":
  "json_object"}`）+ 把 schema 写进 system 提示词让模型自己照着输出。
  不用更新的 `response_format: {"type": "json_schema", ...}` 严格模式——
  那个只有官方 OpenAI 近期模型支持，很多第三方"OpenAI 兼容"端点
  （Together / Groq / 本地 vLLM、Ollama 的兼容接口）没实现，选它会让
  "compat"这个名字名不副实。JSON mode 是老得多、兼容面广得多的能力。
- **`anthropic.py` 用强制工具调用**：声明一个唯一的工具，`input_schema`
  直接就是 `response_model.model_json_schema()`，`tool_choice` 强制模型
  必须调用它——这是 Anthropic 官方文档推荐的结构化输出做法，拿到的
  `tool_use.input` 已经是服务端按 schema 校验过的 dict，比 JSON mode
  更可靠，没理由在这边也退化成"塞进提示词"的做法。

两边都在拿到响应后再过一次 `response_model.model_validate(...)`/
`model_validate_json(...)`——哪怕 Anthropic 服务端已经校验过一次，这层
校验是"把裸 dict 变成调用方要的 pydantic 实例"，不是重复的防御性编程。

## 校验失败算错误，不在这层重试/修复

两个适配器的 `complete_structured` 在模型输出解析/校验失败时都直接抛
`OpenAiCompatError`/`AnthropicError`，不会自己重新提示模型"你刚才输出错了
重新来"——那是编排知识（算不算值得重试、重试几次、要不要换更强的模型），
属于 `features/ai_enrichment`，不下沉到这一层。两边异常类型各自独立，不
共享基类（和 `adapters/sources` 的 `CrossrefError`/`PubMedError`同一个
理由）。

## 真正的 HTTP 请求全部走 `caps/httpfetch.fetch()`

重试/429 退避/UA/SSRF 防护/可选限速全部在那一层。`rate_limiter`/
`transport`/`resolve` 原样转发；后两者是测试钩子，单测用
`httpx.MockTransport` 模拟响应，不碰真实网络和真实 API key。

`api_key`/`base_url`/`model` 全部由调用方显式传入——来自
`domain/connections` 里 `ai_profile` 这种 kind 的连接配置，本模块不知道、
也不关心它们从哪来（和 `adapters/sources` 的 `mailto`/`api_key` 是同一个
理由）。

## `check()` 探测

两边都导出满足 `caps.probe.CheckFn` 协议的 `check(config)`：用一个只有
一个字段的占位 pydantic 模型发一次最小的结构化请求，只验证"鉴权和端点
能不能走通"，不关心模型"回答"了什么内容。

## 依赖方向

依赖 `caps/httpfetch`（发请求）+ `caps/probe`（`check()` 协议）+
`pydantic`（结构化输出的 schema/校验）+ 标准库 `json`。不依赖任何
`domain/*`、`features/*`——符合规则 2。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 流式输出（SSE） | v1 的调用方（打标签/评分/分类）都是"等整个结果"场景，不需要边生成边展示 |
| 多轮工具调用 / agent 循环 | 这里只是"一次结构化请求"，多步编排属于上层 feature |
| 对"校验失败"自动重试/修复提示词 | 编排知识，留给调用方决定要不要做、做几次 |
| 多模态输入（图片/PDF 直接喂给模型） | v1 没有调用方需要；真要加，是新增参数，不改现有签名 |
| 本地/离线模型（llama.cpp 等非 HTTP 接口） | 不是"OpenAI 兼容"也不是 Anthropic，v1 不覆盖；真要支持是第三个独立的 adapter 文件，不改这两个 |
| token 用量计费/配额扣减 | 属于 `domain/usage`，不下沉到这层——`complete_structured` 不返回也不关心 usage 字段 |
