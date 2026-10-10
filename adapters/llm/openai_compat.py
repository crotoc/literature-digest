"""OpenAI Chat Completions API（及其广泛兼容的第三方实现：Azure OpenAI、
Together、Groq、本地 vLLM/Ollama 的 OpenAI 兼容端点……）的结构化输出适配器。

结构化输出走最老、兼容面最广的 **JSON mode**（`response_format:
{"type": "json_object"}`），不是更新的 `json_schema` 严格模式——后者只有
官方 OpenAI 近期模型支持，很多"OpenAI 兼容"第三方端点压根没实现，选它会让
"openai_compat" 这个名字名不副实。做法是把 `response_model` 的 JSON Schema
写进 system 提示词里让模型自己照着输出，拿到的 JSON 字符串再用
`response_model.model_validate_json()` 校验/反序列化——校验失败算一次
失败，不在这层做"重新提示模型修正"这种重试（那是 `features/ai_enrichment`
编排的知识，不是适配器的）。

真正的 HTTP 请求全部走 `caps/httpfetch.fetch()`：重试/429 退避/UA/SSRF
防护都在那一层，这里只管"怎么把结构化输出的意图翻译成 Chat Completions
请求，怎么把响应翻译回 pydantic 实例"。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ValidationError

from caps.httpfetch import HttpFetchError, RateLimiter, default_resolve, fetch
from caps.probe import ProbeResult, fail, ok

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.0

# "测连接"用：只问一句能不能联通、鉴权对不对，不关心回答内容本身。
_PROBE_MODEL_PLACEHOLDER = "gpt-4o-mini"

class OpenAiCompatError(Exception):
    """请求本身失败（鉴权/额度/传输层），或者模型没有照着 schema 输出可解析的 JSON。"""


def complete_structured[T: BaseModel](
    *,
    response_model: type[T],
    messages: Sequence[Mapping[str, str]],
    model: str,
    api_key: str,
    system: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    rate_limiter: RateLimiter | None = None,
    transport=None,
    resolve=default_resolve,
) -> T:
    """发一次 Chat Completions 请求，把回复解析成 `response_model` 的实例。

    `messages` 只认 `role`（`"user"`/`"assistant"`）和 `content` 两个键，
    不需要调用方自己拼 system 消息——`system`（连同 schema 说明）由本函数
    统一拼成第一条 system 角色消息插进去，调用方不用关心"JSON mode 不接受
    额外 schema 参数、只能靠提示词传达"这个实现细节。

    `api_key`/`base_url`/`model` 全部由调用方显式传入，本模块不读任何
    环境变量或配置文件——这三样来自 `domain/connections` 里 `ai_profile`
    这种 kind 的连接配置，模块本身不知道、也不关心它们从哪来（和
    `adapters/sources/crossref.py` 的 `mailto` 是同一个理由）。

    `rate_limiter`/`transport`/`resolve` 原样转发给 `caps/httpfetch.fetch()`，
    是测试钩子；生产代码不传 `transport`/`resolve`。
    """
    schema = response_model.model_json_schema()
    system_text = _build_system_prompt(system, schema)

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_text}, *messages],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        result = fetch(
            f"{base_url.rstrip('/')}/chat/completions",
            method="POST",
            headers=headers,
            body=body,
            rate_limiter=rate_limiter,
            transport=transport,
            resolve=resolve,
        )
    except HttpFetchError as exc:
        raise OpenAiCompatError(f"请求 OpenAI 兼容端点失败：{exc}") from exc

    if result.status_code >= 400:
        raise OpenAiCompatError(
            f"OpenAI 兼容端点返回 {result.status_code}：{_safe_text(result.content)}"
        )

    try:
        response_payload = json.loads(result.content)
        content = response_payload["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise OpenAiCompatError(f"响应里没有可识别的 choices[0].message.content：{exc}") from exc

    try:
        return response_model.model_validate_json(content)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise OpenAiCompatError(f"模型输出不是合法的 {response_model.__name__} JSON：{exc}") from exc


def _build_system_prompt(system: str | None, schema: Mapping) -> str:
    schema_text = json.dumps(schema, ensure_ascii=False)
    instruction = (
        "你必须只输出一个符合下面这段 JSON Schema 的 JSON 对象，不要输出任何"
        f"schema 之外的文字或 markdown 代码块标记。JSON Schema：{schema_text}"
    )
    if system:
        return f"{system}\n\n{instruction}"
    return instruction


def _safe_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")[:500]
    except UnicodeDecodeError:
        return repr(content[:200])


def check(
    config: Mapping[str, object] | None, *, transport=None, resolve=default_resolve
) -> ProbeResult:
    """满足 `caps.probe.CheckFn` 协议：拿配置里的 `api_key`/`base_url`/`model`
    发一次最小的结构化请求，验证鉴权和端点是否可用。

    用一个只有一个字段的占位 pydantic 模型做 schema，不强求模型真的
    "回答"什么——这次探测只关心请求能不能走通、鉴权对不对。
    """
    cfg = config or {}
    api_key = cfg.get("api_key")
    if not api_key:
        return fail("缺少 api_key")
    model = cfg.get("model") or _PROBE_MODEL_PLACEHOLDER
    base_url = cfg.get("base_url") or DEFAULT_BASE_URL

    try:
        _ProbeEcho.model_validate(
            complete_structured(
                response_model=_ProbeEcho,
                messages=[{"role": "user", "content": "回一个空对象即可。"}],
                model=model,
                api_key=api_key,
                base_url=base_url,
                transport=transport,
                resolve=resolve,
            ).model_dump()
        )
    except OpenAiCompatError as exc:
        return fail(f"连接 OpenAI 兼容端点失败：{exc}")
    return ok(f"OpenAI 兼容端点连接正常（model={model}）", model=model, base_url=base_url)


class _ProbeEcho(BaseModel):
    ok: bool = True
