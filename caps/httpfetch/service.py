"""caps/httpfetch 实现：超时/重试/429 退避/UA/速率限制/可选代理/SSRF 防护。"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

DEFAULT_USER_AGENT = "literature-digest-bot/1.0"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_BACKOFF_MAX = 30.0
DEFAULT_MAX_REDIRECTS = 5
ALLOWED_SCHEMES = frozenset({"http", "https"})


class HttpFetchError(Exception):
    """caps/httpfetch 所有异常的基类。"""


class UnsafeURL(HttpFetchError):
    """URL 被 SSRF 防护拒绝：协议不是 http/https，或解析出的 IP 落在私网/内部网段。"""


class NetworkError(HttpFetchError):
    """传输层失败：DNS 解析失败、连接被拒、超时、URL 格式错误等，重试耗尽后仍失败。"""


class TooManyRedirects(HttpFetchError):
    """重定向次数超过 max_redirects。"""


class HttpStatusError(HttpFetchError):
    """调用方要求 raise_for_status=True 时，最终响应状态码 >= 400。"""

    def __init__(self, message: str, result: FetchResult) -> None:
        super().__init__(message)
        self.result = result
        self.status_code = result.status_code


@dataclass(frozen=True)
class FetchResult:
    """一次 fetch() 调用的最终结果。只在成功拿到一个 HTTP 响应时返回；传输层失败走异常，
    不含响应体——HTTP 层的错误状态码（4xx/5xx）默认也算"拿到了响应"，不是异常。"""

    status_code: int
    headers: dict[str, str]
    content: bytes
    url: str
    attempts: int


def default_resolve(host: str) -> list[str]:
    """用系统 DNS 把主机名解析成 IP 字符串列表。host 本身就是数字 IP 时不会触发真实网络查询，
    这也是测试里绕开真实 DNS 的方式：用 IP 字面量当 host。

    `getaddrinfo` 对同一个地址族会按 socktype（TCP/UDP/raw）各返回一条记录，
    同一个 IP 因此重复出现多次——这里按序去重，调用方看到的是"解析到几个不同地址"，
    不是"系统内部枚举了几种 socket 类型"。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise NetworkError(f"无法解析主机名 {host!r}: {exc}") from exc
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(info[4][0], None)
    return list(seen)


def is_safe_ip(ip_str: str) -> bool:
    """判断一个 IP 是否允许访问：拒绝私网/环回/链路本地（含 169.254.169.254 云厂商元数据
    端点,它落在 169.254.0.0/16 link-local 段里，天然被挡）/多播/保留/未指定地址。"""
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_url_safety(
    url: str,
    *,
    resolve: Callable[[str], list[str]] = default_resolve,
) -> None:
    """检查一个 URL 是否可以安全访问：协议必须是 http/https，且解析出的所有 IP 都不落在
    私网/内部网段。

    这是单次检查，不防 DNS rebinding（检查通过后、真正建立连接前主机换了 IP 指向内网）——
    httpx 不提供"按已校验的 IP 连接"的钩子，这个残余风险在当前威胁模型（防的是"被引导访问
    内部服务"，不是对抗持续攻击者）下可接受，在 README 里写明。
    """
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"不支持的协议: {parts.scheme!r}（只允许 http/https）")
    if not parts.hostname:
        raise UnsafeURL(f"URL 没有主机名: {url!r}")

    ips = resolve(parts.hostname)
    if not ips:
        raise NetworkError(f"主机名 {parts.hostname!r} 没有解析出任何地址")
    for ip_str in ips:
        if not is_safe_ip(ip_str):
            raise UnsafeURL(
                f"目标地址 {ip_str}（来自 {parts.hostname!r}）落在私网/内部网段，拒绝访问"
            )


@dataclass
class RateLimiter:
    """"两次调用之间至少间隔 N 秒"的限速器。调用方自己创建并持有一个实例，在多次 fetch()
    之间复用（比如每个数据源一个模块级单例）——fetch() 本身不维护任何跨调用的全局状态，
    这个类就是状态该放的地方。"""

    min_interval_seconds: float
    _last_call: float | None = field(default=None, init=False, repr=False)

    def wait(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        current = now()
        if self._last_call is not None:
            remaining = self.min_interval_seconds - (current - self._last_call)
            if remaining > 0:
                sleep(remaining)
                current = now()
        self._last_call = current


def _merge_headers(headers: Mapping[str, str] | None, user_agent: str | None) -> dict[str, str]:
    merged: dict[str, str] = dict(headers) if headers else {}
    if not any(key.lower() == "user-agent" for key in merged):
        merged["User-Agent"] = user_agent or DEFAULT_USER_AGENT
    return merged


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # HTTP-date 格式不支持，退回指数退避


def _backoff_delay(attempt: int, *, base: float, maximum: float) -> float:
    return min(maximum, base * (2**attempt))


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_statuses: frozenset[int] = DEFAULT_RETRY_STATUSES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_max: float = DEFAULT_BACKOFF_MAX,
    user_agent: str | None = None,
    proxy: str | None = None,
    follow_redirects: bool = True,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    raise_for_status: bool = False,
    rate_limiter: RateLimiter | None = None,
    resolve: Callable[[str], list[str]] = default_resolve,
    sleep: Callable[[float], None] = time.sleep,
    transport: httpx.BaseTransport | None = None,
) -> FetchResult:
    """发一个 HTTP 请求，内置超时/重试/429 退避/UA/可选限速/可选代理/SSRF 防护。

    行为边界（刻意设计，不是遗漏）：
    - 状态码 >= 400 默认**不**抛异常，照常返回 FetchResult，让调用方自己判断
      （`raise_for_status=True` 时才在重试耗尽后抛 HttpStatusError）——这一层不替上层
      决定"4xx/5xx 算不算错误"，比如 404 对 metadata_lookup 可能是正常的"没找到"。
    - 只有传输层失败（DNS/连接/超时/SSRF 拦截/重定向过多）会抛异常。
    - 重定向手动跟随（不借助 httpx 的 follow_redirects），因为每一跳都要重新过 SSRF
      检查——出版商 / ezproxy 改写的跳转可能指向内网，交给 httpx 自动跟随就跳过了这一步。
    - `resolve`/`sleep`/`transport` 是测试钩子，生产代码不传，用默认值即可。

    参数:
        url: 请求目标。
        method: HTTP 方法。
        headers: 额外请求头；若未显式提供 User-Agent，会补一个默认值。
        params: 查询参数。
        body: 请求体（原始字节）。
        timeout: 单次请求超时（秒），同时应用到 connect/read/write/pool 四项。
        max_retries: 每一跳失败后的最大重试次数（不含首次尝试）。
        retry_statuses: 触发重试的状态码集合。
        backoff_base/backoff_max: 指数退避的基数和上限（秒）；429 若带 Retry-After 则优先用它。
        user_agent: 默认 User-Agent，被 headers 里显式指定的值覆盖。
        proxy: 可选代理 URL（如 "http://127.0.0.1:8080"）。
        follow_redirects: 是否跟随 3xx 重定向。
        max_redirects: 最多跟随的重定向跳数。
        raise_for_status: True 时最终状态码 >=400 抛 HttpStatusError。
        rate_limiter: 可选的调用方持有的限速器，每一跳发请求前等待。
        resolve/sleep/transport: 测试钩子，见上。

    返回:
        FetchResult。

    异常:
        UnsafeURL: 协议或目标 IP 被 SSRF 防护拒绝（初始 URL 或某一跳重定向目标）。
        NetworkError: DNS/连接/超时/URL 格式错误等传输层问题，重试耗尽后仍失败。
        TooManyRedirects: 重定向跳数超过 max_redirects。
        HttpStatusError: raise_for_status=True 且最终状态码 >=400。
        ValueError: max_retries 或 max_redirects 为负数。
    """
    if max_retries < 0:
        raise ValueError("max_retries 不能为负数")
    if max_redirects < 0:
        raise ValueError("max_redirects 不能为负数")

    request_headers = _merge_headers(headers, user_agent)
    client_timeout = httpx.Timeout(timeout)

    attempts = 0
    last_network_error: Exception | None = None
    response: httpx.Response | None = None

    with httpx.Client(
        timeout=client_timeout,
        proxy=proxy,
        transport=transport,
        follow_redirects=False,
    ) as client:
        current_url = url
        redirect_count = 0

        while True:
            check_url_safety(current_url, resolve=resolve)

            if rate_limiter is not None:
                rate_limiter.wait(sleep=sleep)

            response = None
            attempt_in_hop = 0
            while True:
                attempts += 1
                try:
                    response = client.request(
                        method,
                        current_url,
                        headers=request_headers,
                        params=params,
                        content=body,
                    )
                except (httpx.HTTPError, httpx.InvalidURL) as exc:
                    last_network_error = exc
                    response = None

                if response is not None and response.status_code not in retry_statuses:
                    break
                if attempt_in_hop >= max_retries:
                    break

                delay = None
                if response is not None and response.status_code == 429:
                    delay = _retry_after_seconds(response)
                if delay is None:
                    delay = _backoff_delay(attempt_in_hop, base=backoff_base, maximum=backoff_max)

                sleep(delay)
                attempt_in_hop += 1

            if response is None:
                raise NetworkError(
                    f"请求 {current_url!r} 失败: {last_network_error}"
                ) from last_network_error

            if follow_redirects and response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    break  # 没有 Location 的"重定向"当成最终响应返回
                redirect_count += 1
                if redirect_count > max_redirects:
                    raise TooManyRedirects(f"重定向次数超过 {max_redirects}：停在 {current_url!r}")
                current_url = urljoin(current_url, location)
                continue

            break

    assert response is not None  # 上面任一分支都已排除 None 的情况
    result = FetchResult(
        status_code=response.status_code,
        headers={k.lower(): v for k, v in response.headers.items()},
        content=response.content,
        url=str(response.url),
        attempts=attempts,
    )

    if raise_for_status and result.status_code >= 400:
        raise HttpStatusError(f"{method} {result.url} 返回 {result.status_code}", result)

    return result
