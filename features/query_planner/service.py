"""features/query_planner：把一条研究意图（`intents.question_text`，一句
人话）编译成一条结构化检索式，落一条新的 `domain.intents.IntentVersion`。

`features/discovery`（E3 的另一个 feature）拿到这个版本里的 `query_json
["query"]`，原样喂给 `adapters/sources/*` 的 `search()`——本模块只管
"编译"这一步，不碰任何来源的 HTTP 请求，也不认识 `domain.works`/
`domain.jobs`。

**`query_json` 的内部结构是本模块自己的约定**（`domain/intents` 的
docstring 说得很清楚，它自己不解释这个字段）：

```
{"query": "<单条检索式字符串>", "rationale": "<编译依据，给用户看，纯展示>"}
```

只有一条 `query` 字符串，不是"每个来源各自一条"——`adapters.sources.
crossref.search`/`adapters.sources.pubmed.search` 的 `query`/`term`
参数都是自由文本关键词，没有必要为两个来源分别编译两条几乎一样的检索式；
`rationale` 只在 `source="ai"` 时存在，让用户在设置页能看懂"AI 为什么
编译出这条检索式"，不参与任何检索逻辑。

**`query_override` 的短路**：`intent.query_override` 非空时，
`compile_intent_query` 直接落一条 `source="override"` 的版本，`query_json`
里只有 `query`、没有 `rationale`，完全不调用 LLM——用户已经明确给了检索式，
没理由还让 AI 猜一遍再被覆盖掉（这也是为什么调用方不需要先检查
`query_override`再决定是否调这个函数：两条路径都走同一个入口）。

**编译失败不抛异常，落一条 `status="failed"` 的版本**：和
`features/ai_enrichment`/`features/tag_grouping` 遇到 LLM 调用失败就直接
抛 `AppError` 不一样——`domain.intents.get_latest_ready_version()` 永远
只看 `status="ready"` 的最新一条，所以"这次编译失败了"本身就该是一条可
追溯的历史记录（给设置页展示"上次编译失败，原因是…"），而不是让异常把
请求打断、什么都不落库。调用方（`app/pages/settings`）自己决定要不要把
`status == "failed"` 的返回值转成页面上的错误提示。

真正的"调用方传的 provider 不认识"（配置错误，不是"这次编译失败"）仍然
直接抛 `UnknownProvider`，不落库——和 `ai_enrichment`/`tag_grouping`
处理未知 provider 的方式一致。
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from adapters.llm import anthropic, openai_compat
from adapters.llm.anthropic import AnthropicError
from adapters.llm.openai_compat import OpenAiCompatError
from caps.template import render_jinja
from domain.intents import IntentVersionDTO, create_intent_version, get_intent

PROVIDERS = frozenset({"openai_compat", "anthropic"})

_PROVIDER_FNS = {
    "openai_compat": openai_compat.complete_structured,
    "anthropic": anthropic.complete_structured,
}
_PROVIDER_ERRORS = (OpenAiCompatError, AnthropicError)

_SYSTEM_PROMPT = (
    "你是一个帮研究者把模糊的关注点编译成文献检索式的助手。"
    "给出的 query 要能直接喂给 Crossref/PubMed 这类学术检索接口——"
    "优先用英文关键词（学术检索命中率更高），可以是几个核心短语用空格或"
    "逻辑连接词组合，不要输出完整的自然语言句子，不要加任何解释性文字。"
)
_PROMPT_TEMPLATE = "研究关注点：{{ question }}"


class _QueryPlanModel(BaseModel):
    query: str
    rationale: str


class UnknownProvider(ValueError):
    """`provider` 不在 `PROVIDERS` 里。"""


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise UnknownProvider(f"不认识的 provider：{provider!r}（支持：{sorted(PROVIDERS)}）")


def compile_intent_query(
    db: Session,
    *,
    intent_id: int,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    transport=None,
    resolve=None,
) -> IntentVersionDTO:
    """编译一次：读 `intent.question_text`（或短路读 `query_override`），
    落一条新的 `IntentVersion`。`provider`/`model`/`api_key`/`base_url`
    由调用方解析好再传入（和 `features/tag_grouping` 的
    `propose_ai_groups` 同一个理由：本模块不认识 `domain.connections`）。
    """
    _validate_provider(provider)
    intent = get_intent(db, intent_id)

    if intent.query_override:
        return create_intent_version(
            db,
            intent_id=intent_id,
            query_json={"query": intent.query_override},
            source="override",
        )

    prompt = render_jinja(_PROMPT_TEMPLATE, {"question": intent.question_text})
    kwargs: dict[str, object] = {
        "response_model": _QueryPlanModel,
        "messages": [{"role": "user", "content": prompt}],
        "model": model,
        "api_key": api_key,
        "system": _SYSTEM_PROMPT,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if transport is not None:
        kwargs["transport"] = transport
    if resolve is not None:
        kwargs["resolve"] = resolve

    try:
        result = _PROVIDER_FNS[provider](**kwargs)
    except _PROVIDER_ERRORS as exc:
        return create_intent_version(
            db, intent_id=intent_id, query_json={}, source="ai", model=model,
            status="failed", error=str(exc),
        )

    query = result.query.strip()
    if not query:
        return create_intent_version(
            db, intent_id=intent_id, query_json={}, source="ai", model=model,
            status="failed", error="LLM 编译出的检索式是空字符串",
        )

    return create_intent_version(
        db, intent_id=intent_id, query_json={"query": query, "rationale": result.rationale},
        source="ai", model=model, status="ready",
    )
