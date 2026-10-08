"""caps/httpfetch 单测。不碰真实网络：SSRF 检查用 IP 字面量（getaddrinfo 对数字 IP
不发网络查询）或注入 resolve()，HTTP 层用 httpx.MockTransport，等待用假 sleep()。"""

import httpx
import pytest

from caps.httpfetch import (
    FetchResult,
    HttpStatusError,
    NetworkError,
    RateLimiter,
    TooManyRedirects,
    UnsafeURL,
    check_url_safety,
    default_resolve,
    fetch,
    is_safe_ip,
)
from caps.httpfetch.service import _backoff_delay, _merge_headers, _retry_after_seconds


def _no_sleep():
    calls = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)

    sleep.calls = calls
    return sleep


class TestIsSafeIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "127.0.0.1",
            "169.254.169.254",  # 云厂商元数据端点，落在 link-local 段
            "224.0.0.1",
            "0.0.0.0",
            "::1",
            "fe80::1",
            "fc00::1",
        ],
    )
    def test_unsafe(self, ip):
        assert is_safe_ip(ip) is False

    @pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
    def test_safe(self, ip):
        assert is_safe_ip(ip) is True


class TestCheckUrlSafety:
    def test_rejects_non_http_scheme(self):
        with pytest.raises(UnsafeURL):
            check_url_safety("file:///etc/passwd")

    def test_rejects_ftp(self):
        with pytest.raises(UnsafeURL):
            check_url_safety("ftp://example.com/file")

    def test_rejects_no_hostname(self):
        with pytest.raises(UnsafeURL):
            check_url_safety("http:///path")

    def test_rejects_private_ip_literal(self):
        with pytest.raises(UnsafeURL):
            check_url_safety("http://127.0.0.1/")

    def test_rejects_metadata_endpoint(self):
        with pytest.raises(UnsafeURL):
            check_url_safety("http://169.254.169.254/latest/meta-data/")

    def test_accepts_public_ip_literal(self):
        check_url_safety("http://93.184.216.34/")  # 不抛就是过了

    def test_custom_resolve_used(self):
        with pytest.raises(UnsafeURL):
            check_url_safety("http://internal.example.test/", resolve=lambda host: ["10.0.0.5"])

    def test_custom_resolve_safe(self):
        check_url_safety("http://public.example.test/", resolve=lambda host: ["8.8.8.8"])

    def test_resolve_returning_mixed_ips_rejects_if_any_unsafe(self):
        with pytest.raises(UnsafeURL):
            check_url_safety(
                "http://mixed.example.test/", resolve=lambda host: ["8.8.8.8", "10.0.0.1"]
            )

    def test_resolve_empty_raises_network_error(self):
        with pytest.raises(NetworkError):
            check_url_safety("http://nowhere.example.test/", resolve=lambda host: [])


class TestDefaultResolve:
    def test_numeric_ip_resolves_without_network(self):
        assert default_resolve("127.0.0.1") == ["127.0.0.1"]

    def test_dns_failure_wrapped_as_network_error(self, monkeypatch):
        import socket

        def fake_getaddrinfo(*args, **kwargs):
            raise OSError("simulated DNS failure")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(NetworkError):
            default_resolve("example.test")


class TestMergeHeaders:
    def test_adds_default_user_agent(self):
        headers = _merge_headers(None, None)
        assert headers["User-Agent"]

    def test_custom_user_agent(self):
        headers = _merge_headers(None, "my-bot/1.0")
        assert headers["User-Agent"] == "my-bot/1.0"

    def test_explicit_header_not_overridden(self):
        headers = _merge_headers({"User-Agent": "caller-set/2.0"}, "default-bot")
        assert headers["User-Agent"] == "caller-set/2.0"

    def test_explicit_header_case_insensitive(self):
        headers = _merge_headers({"user-agent": "lowercase/1.0"}, "default-bot")
        assert headers["user-agent"] == "lowercase/1.0"
        assert "User-Agent" not in headers

    def test_other_headers_preserved(self):
        headers = _merge_headers({"Accept": "application/json"}, None)
        assert headers["Accept"] == "application/json"
        assert headers["User-Agent"]


class TestRetryAfterSeconds:
    def test_present_numeric(self):
        response = httpx.Response(429, headers={"Retry-After": "7"})
        assert _retry_after_seconds(response) == 7.0

    def test_absent(self):
        response = httpx.Response(429)
        assert _retry_after_seconds(response) is None

    def test_http_date_unsupported_returns_none(self):
        response = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert _retry_after_seconds(response) is None

    def test_negative_clamped_to_zero(self):
        response = httpx.Response(429, headers={"Retry-After": "-5"})
        assert _retry_after_seconds(response) == 0.0


class TestBackoffDelay:
    def test_grows_exponentially(self):
        assert _backoff_delay(0, base=1.0, maximum=100.0) == 1.0
        assert _backoff_delay(1, base=1.0, maximum=100.0) == 2.0
        assert _backoff_delay(2, base=1.0, maximum=100.0) == 4.0

    def test_capped_at_maximum(self):
        assert _backoff_delay(10, base=1.0, maximum=5.0) == 5.0


class TestRateLimiter:
    def test_first_call_never_waits(self):
        limiter = RateLimiter(min_interval_seconds=10.0)
        sleep = _no_sleep()
        limiter.wait(sleep=sleep, now=lambda: 100.0)
        assert sleep.calls == []

    def test_second_call_within_window_waits_remaining(self):
        limiter = RateLimiter(min_interval_seconds=10.0)
        sleep = _no_sleep()
        clock = [100.0]
        limiter.wait(sleep=sleep, now=lambda: clock[0])
        clock[0] = 103.0
        limiter.wait(sleep=sleep, now=lambda: clock[0])
        assert sleep.calls == [7.0]

    def test_call_after_window_does_not_wait(self):
        limiter = RateLimiter(min_interval_seconds=1.0)
        sleep = _no_sleep()
        clock = [100.0]
        limiter.wait(sleep=sleep, now=lambda: clock[0])
        clock[0] = 200.0
        limiter.wait(sleep=sleep, now=lambda: clock[0])
        assert sleep.calls == []


class TestFetchHappyPath:
    def _transport(self, body=b"hello", status=200, headers=None):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, content=body, headers=headers or {})

        return httpx.MockTransport(handler)

    def test_returns_fetch_result(self):
        result = fetch("http://93.184.216.34/", transport=self._transport())
        assert isinstance(result, FetchResult)
        assert result.status_code == 200
        assert result.content == b"hello"
        assert result.attempts == 1

    def test_headers_lowercased(self):
        result = fetch(
            "http://93.184.216.34/",
            transport=self._transport(headers={"X-Custom": "Value"}),
        )
        assert result.headers["x-custom"] == "Value"

    def test_sends_default_user_agent(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent")
            return httpx.Response(200)

        fetch("http://93.184.216.34/", transport=httpx.MockTransport(handler))
        assert seen["ua"]

    def test_custom_user_agent_sent(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent")
            return httpx.Response(200)

        fetch(
            "http://93.184.216.34/",
            user_agent="literature-digest-test/9.9",
            transport=httpx.MockTransport(handler),
        )
        assert seen["ua"] == "literature-digest-test/9.9"

    def test_post_with_body(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.read()
            return httpx.Response(201)

        result = fetch(
            "http://93.184.216.34/submit",
            method="POST",
            body=b'{"a":1}',
            transport=httpx.MockTransport(handler),
        )
        assert result.status_code == 201
        assert seen["body"] == b'{"a":1}'

    def test_error_status_returned_not_raised_by_default(self):
        result = fetch("http://93.184.216.34/missing", transport=self._transport(status=404))
        assert result.status_code == 404

    def test_raise_for_status_true_raises_on_4xx(self):
        with pytest.raises(HttpStatusError) as excinfo:
            fetch(
                "http://93.184.216.34/missing",
                transport=self._transport(status=404),
                raise_for_status=True,
            )
        assert excinfo.value.status_code == 404

    def test_raise_for_status_true_passes_on_2xx(self):
        result = fetch(
            "http://93.184.216.34/",
            transport=self._transport(status=200),
            raise_for_status=True,
        )
        assert result.status_code == 200


class TestFetchSsrf:
    def test_rejects_unsafe_initial_url(self):
        with pytest.raises(UnsafeURL):
            fetch("http://127.0.0.1/admin", transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    def test_rejects_unsafe_redirect_target(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "safe-start" in str(request.url):
                return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
            return httpx.Response(200)

        with pytest.raises(UnsafeURL):
            fetch(
                "http://93.184.216.34/safe-start",
                transport=httpx.MockTransport(handler),
                sleep=_no_sleep(),
            )


class TestFetchRedirects:
    def test_follows_redirect_and_returns_final(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == "http://93.184.216.34/start":
                return httpx.Response(302, headers={"Location": "http://93.184.216.34/final"})
            return httpx.Response(200, content=b"landed")

        result = fetch("http://93.184.216.34/start", transport=httpx.MockTransport(handler))
        assert result.status_code == 200
        assert result.content == b"landed"
        assert result.url == "http://93.184.216.34/final"

    def test_follow_redirects_false_returns_redirect_itself(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "http://93.184.216.34/final"})

        result = fetch(
            "http://93.184.216.34/start",
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        )
        assert result.status_code == 302
        assert result.headers["location"] == "http://93.184.216.34/final"

    def test_redirect_without_location_returned_as_is(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302)

        result = fetch("http://93.184.216.34/start", transport=httpx.MockTransport(handler))
        assert result.status_code == 302

    def test_too_many_redirects(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            return httpx.Response(302, headers={"Location": f"http://93.184.216.34/hop{counter['n']}"})

        with pytest.raises(TooManyRedirects):
            fetch(
                "http://93.184.216.34/start",
                transport=httpx.MockTransport(handler),
                max_redirects=3,
                sleep=_no_sleep(),
            )

    def test_relative_redirect_resolved_against_current_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == "http://93.184.216.34/a/start":
                return httpx.Response(302, headers={"Location": "final"})
            return httpx.Response(200, content=b"ok")

        result = fetch("http://93.184.216.34/a/start", transport=httpx.MockTransport(handler))
        assert result.url == "http://93.184.216.34/a/final"


class TestFetchRetry:
    def test_retries_on_retryable_status_then_succeeds(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            if counter["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, content=b"ok")

        sleep = _no_sleep()
        result = fetch(
            "http://93.184.216.34/",
            transport=httpx.MockTransport(handler),
            max_retries=5,
            sleep=sleep,
        )
        assert result.status_code == 200
        assert result.attempts == 3
        assert len(sleep.calls) == 2

    def test_exhausts_retries_returns_last_error_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        result = fetch(
            "http://93.184.216.34/",
            transport=httpx.MockTransport(handler),
            max_retries=2,
            sleep=_no_sleep(),
        )
        assert result.status_code == 500
        assert result.attempts == 3  # 首次 + 2 次重试

    def test_exhausts_retries_raises_when_raise_for_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with pytest.raises(HttpStatusError):
            fetch(
                "http://93.184.216.34/",
                transport=httpx.MockTransport(handler),
                max_retries=1,
                sleep=_no_sleep(),
                raise_for_status=True,
            )

    def test_non_retryable_status_not_retried(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            return httpx.Response(404)

        result = fetch(
            "http://93.184.216.34/",
            transport=httpx.MockTransport(handler),
            max_retries=5,
            sleep=_no_sleep(),
        )
        assert result.status_code == 404
        assert counter["n"] == 1

    def test_429_uses_retry_after_header_over_backoff(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            if counter["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "42"})
            return httpx.Response(200)

        sleep = _no_sleep()
        fetch(
            "http://93.184.216.34/",
            transport=httpx.MockTransport(handler),
            max_retries=1,
            backoff_base=1.0,
            sleep=sleep,
        )
        assert sleep.calls == [42.0]

    def test_429_without_retry_after_uses_backoff(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            if counter["n"] == 1:
                return httpx.Response(429)
            return httpx.Response(200)

        sleep = _no_sleep()
        fetch(
            "http://93.184.216.34/",
            transport=httpx.MockTransport(handler),
            max_retries=1,
            backoff_base=2.0,
            sleep=sleep,
        )
        assert sleep.calls == [2.0]

    def test_network_error_retried_then_raises(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            raise httpx.ConnectError("boom")

        with pytest.raises(NetworkError):
            fetch(
                "http://93.184.216.34/",
                transport=httpx.MockTransport(handler),
                max_retries=2,
                sleep=_no_sleep(),
            )
        assert counter["n"] == 3

    def test_network_error_retried_then_succeeds(self):
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            if counter["n"] < 2:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, content=b"ok")

        result = fetch(
            "http://93.184.216.34/",
            transport=httpx.MockTransport(handler),
            max_retries=2,
            sleep=_no_sleep(),
        )
        assert result.status_code == 200

    def test_timeout_wrapped_as_network_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(NetworkError):
            fetch(
                "http://93.184.216.34/",
                transport=httpx.MockTransport(handler),
                max_retries=0,
                sleep=_no_sleep(),
            )


class TestFetchRateLimiterIntegration:
    def test_rate_limiter_invoked_before_request(self):
        waited = []

        class FakeLimiter(RateLimiter):
            def wait(self, *, sleep, now=None):
                waited.append(True)

        result = fetch(
            "http://93.184.216.34/",
            transport=httpx.MockTransport(lambda r: httpx.Response(200)),
            rate_limiter=FakeLimiter(min_interval_seconds=0.0),
        )
        assert result.status_code == 200
        assert waited == [True]


class TestFetchValidation:
    def test_negative_max_retries_rejected(self):
        with pytest.raises(ValueError):
            fetch("http://93.184.216.34/", max_retries=-1)

    def test_negative_max_redirects_rejected(self):
        with pytest.raises(ValueError):
            fetch("http://93.184.216.34/", max_redirects=-1)


class TestExceptionHierarchy:
    def test_all_inherit_from_base(self):
        from caps.httpfetch import HttpFetchError

        assert issubclass(UnsafeURL, HttpFetchError)
        assert issubclass(NetworkError, HttpFetchError)
        assert issubclass(TooManyRedirects, HttpFetchError)
        assert issubclass(HttpStatusError, HttpFetchError)
