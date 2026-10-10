"""Telegram Bot API 投递适配器：发消息 / 原地编辑已发的消息 / 测连接。

真正的 HTTP 请求全部走 `caps/httpfetch.fetch()`。这里只提供两个原语——
"发一条给定文本的消息"和"编辑一条已发的消息"；一条报告该怎么切成多段
消息、哪些目的地该重发哪些该跳过，是调用方（`features/delivery`）的
编排知识，不下沉到这一层。

和 `adapters/sources/pubmed.py` 把 `api_key` 放进 URL query 参数一样，
这里把 `bot_token` 放进 URL 路径（Telegram Bot API 的官方约定本身就是
`https://api.telegram.org/bot<token>/<method>`）——`caps/httpfetch` 本身
不记录任何日志（没有 logging 调用），所以这里不会把 token 意外写进日志。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from caps.httpfetch import HttpFetchError, RateLimiter, default_resolve, fetch
from caps.probe import ProbeResult, fail, ok

_API_BASE = "https://api.telegram.org"

# "测连接"用的探测消息——发给用户自己配置的 chat_id，只是为了验证
# bot_token + chat_id 这对组合真的能发消息，不关心内容本身。
_PROBE_MESSAGE = "literature-digest：连接测试"


class TelegramError(Exception):
    """调用 Telegram Bot API 失败（传输层失败，或响应体里 `ok` 字段是
    `false`——Telegram 用 200 状态码 + 响应体字段表达业务失败，不是
    HTTP 状态码）。"""


def send_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    rate_limiter: RateLimiter | None = None,
    transport=None,
    resolve=default_resolve,
) -> str:
    """发一条消息，返回 Telegram 那条消息自己的 `message_id`（转成字符串
    返回，和 `chat_id` 一样原样存起来——后续 `edit_message` 定位这条消息
    要用它）。`parse_mode="HTML"` 是调用方渲染报告正文时假定的格式，这里
    固定写死，不暴露成参数（这个适配器只服务一种消息格式，不是通用的
    Telegram 客户端）。
    """
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    result = _call(
        bot_token, "sendMessage", payload, rate_limiter=rate_limiter, transport=transport, resolve=resolve
    )
    return str(result["result"]["message_id"])


def edit_message(
    *,
    bot_token: str,
    chat_id: str,
    message_id: str,
    text: str,
    rate_limiter: RateLimiter | None = None,
    transport=None,
    resolve=default_resolve,
) -> None:
    """原地编辑一条已发的消息（报告重新分析后，用这个把旧消息刷新成新内容，
    不重发一条新的）。Telegram 对"新文本和旧文本完全一样"会返回一个特定的
    业务失败（`message is not modified`），这里原样让 `TelegramError` 冒
    出去——调用方要能分辨"没有变化"和"真的编辑失败"，这一层不替它判断、
    也不吞掉。
    """
    payload = {
        "chat_id": chat_id, "message_id": int(message_id), "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    _call(
        bot_token, "editMessageText", payload,
        rate_limiter=rate_limiter, transport=transport, resolve=resolve,
    )


def _call(
    bot_token: str,
    method: str,
    payload: dict,
    *,
    rate_limiter: RateLimiter | None,
    transport,
    resolve,
) -> dict:
    url = f"{_API_BASE}/bot{bot_token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    try:
        result = fetch(
            url,
            method="POST",
            headers={"Content-Type": "application/json"},
            body=body,
            rate_limiter=rate_limiter,
            transport=transport,
            resolve=resolve,
        )
    except HttpFetchError as exc:
        raise TelegramError(f"请求 Telegram {method} 失败：{exc}") from exc

    try:
        decoded = json.loads(result.content)
    except json.JSONDecodeError as exc:
        raise TelegramError(f"Telegram {method} 响应不是合法 JSON：{exc}") from exc

    if not decoded.get("ok"):
        raise TelegramError(f"Telegram {method} 返回失败：{decoded.get('description')}")
    return decoded


def check(
    config: Mapping[str, Any] | None,
    *,
    transport=None,
    resolve=default_resolve,
) -> ProbeResult:
    """满足 `caps.probe.CheckFn` 协议：发一条真实的测试消息，验证
    `bot_token` + `chat_id` 这对组合有效。`config` 需要这两个字段都配
    齐——缺字段本身就是一种确定的测试结果（"配置不完整"），这里直接返回
    失败结果而不是抛异常，理由同 `adapters.sources.pubmed.check` 对缺
    `api_key` 时的处理。
    """
    bot_token = (config or {}).get("bot_token")
    chat_id = (config or {}).get("chat_id")
    if not bot_token or not chat_id:
        return fail("缺少 bot_token 或 chat_id")
    try:
        send_message(
            bot_token=bot_token, chat_id=chat_id, text=_PROBE_MESSAGE,
            transport=transport, resolve=resolve,
        )
    except TelegramError as exc:
        return fail(f"发送测试消息失败：{exc}")
    return ok("Telegram 连接正常，已发送一条测试消息")
