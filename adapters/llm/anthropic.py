"""Anthropic Messages API 的结构化输出适配器。

结构化输出走 Anthropic 官方推荐的做法：声明一个唯一的工具（tool），
`input_schema` 直接就是 `response_model.model_json_schema()`，再用
`tool_choice` 强制模型必须调用这个工具——这样拿到的 `tool_use.input`
已经是一个按 schema 校验过结构的 dict（Anthropic 服务端保证），不需要像
`adapters/llm/openai_compat.py` 那样自己把 schema 塞进提示词再祈祷模型
照办。两个适配器对外的函数签名刻意保持一致（`complete_structured(...)`
同一组关键字参数），这样 `features/ai_enrichment` 换 provider 时不需要
改调用方式。

真正的 HTTP 请求全部走 `caps/httpfetch.fetch()`：重试/429 退避/UA/SSRF
防护都在那一层。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ValidationError

from caps.httpfetch import HttpFetchError, RateLimiter, default_resolve, fetch
from caps.probe import ProbeResult, fail, ok

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.0
ANTHROPIC_VERSION = "2023-06-01"

_PROBE_MODEL_PLACEHOLDER = "claude-haiku-4-5-20251001"
_TOOL_NAME = "emit_result"


class AnthropicError(Exception):
    """请求本身失败（鉴权/额度/传输层），或者响应里没有预期的 tool_use 块。"""


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
    """发一次 Messages 请求，强制模型调用唯一的工具，把工具输入解析成
    `response_model` 的实例。

    参数含义和 `adapters/llm/openai_compat.complete_structured` 对齐：
    `api_key`/`base_url`/`model` 由调用方显式传入（来自 `domain/connections`
    的 `ai_profile` 连接配置），`rate_limiter`/`transport`/`resolve` 原样
    转发给 `caps/httpfetch.fetch()`，是测试钩子。
    """
    schema = response_model.model_json_schema()
    payload: dict = {
        "model": model,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "tools": [
            {
                "name": _TOOL_NAME,
                "description": f"提交一个符合 {response_model.__name__} 结构的结果。",
                "input_schema": schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }
    if system:
        payload["system"] = system

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
    }

    try:
        result = fetch(
            f"{base_url.rstrip('/')}/v1/messages",
            method="POST",
            headers=headers,
            body=body,
            rate_limiter=rate_limiter,
            transport=transport,
            resolve=resolve,
        )
    except HttpFetchError as exc:
        raise AnthropicError(f"请求 Anthropic 失败：{exc}") from exc

    if result.status_code >= 400:
        raise AnthropicError(f"Anthropic 返回 {result.status_code}：{_safe_text(result.content)}")

    try:
        response_payload = json.loads(result.content)
        blocks = response_payload["content"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AnthropicError(f"响应里没有可识别的 content 字段：{exc}") from exc

    tool_input = _find_tool_use_input(blocks)
    if tool_input is None:
        raise AnthropicError(f"响应里没有名为 {_TOOL_NAME!r} 的 tool_use 块")

    try:
        return response_model.model_validate(tool_input)
    except ValidationError as exc:
        raise AnthropicError(f"工具输入不符合 {response_model.__name__}：{exc}") from exc


def _find_tool_use_input(blocks: Sequence[Mapping]) -> Mapping | None:
    for block in blocks:
        if block.get("type") == "tool_use" and block.get("name") == _TOOL_NAME:
            return block.get("input")
    return None


def _safe_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")[:500]
    except UnicodeDecodeError:
        return repr(content[:200])


def check(
    config: Mapping[str, object] | None, *, transport=None, resolve=default_resolve
) -> ProbeResult:
    """满足 `caps.probe.CheckFn` 协议：拿配置里的 `api_key`/`base_url`/`model`
    发一次最小的结构化请求，验证鉴权和端点是否可用。"""
    cfg = config or {}
    api_key = cfg.get("api_key")
    if not api_key:
        return fail("缺少 api_key")
    model = cfg.get("model") or _PROBE_MODEL_PLACEHOLDER
    base_url = cfg.get("base_url") or DEFAULT_BASE_URL

    try:
        complete_structured(
            response_model=_ProbeEcho,
            messages=[{"role": "user", "content": "回一个空对象即可。"}],
            model=model,
            api_key=api_key,
            base_url=base_url,
            transport=transport,
            resolve=resolve,
        )
    except AnthropicError as exc:
        return fail(f"连接 Anthropic 失败：{exc}")
    return ok(f"Anthropic 连接正常（model={model}）", model=model, base_url=base_url)


class _ProbeEcho(BaseModel):
    ok: bool = True
