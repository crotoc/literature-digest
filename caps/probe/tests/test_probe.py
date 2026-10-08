"""caps/probe 单测。不建库、不联网——这个 cap 本身没有任何 IO。"""

import pytest

from caps.probe import ProbeResult, fail, ok, run_check


class TestProbeResultConstruction:
    def test_ok_sets_ok_true(self):
        result = ok("连接成功")
        assert result.ok is True
        assert result.message == "连接成功"
        assert result.detail == {}

    def test_fail_sets_ok_false(self):
        result = fail("连接失败")
        assert result.ok is False
        assert result.message == "连接失败"

    def test_ok_with_detail_kwargs(self):
        result = ok("成功", status_code=200, latency_ms=42)
        assert result.detail == {"status_code": 200, "latency_ms": 42}

    def test_fail_with_detail_kwargs(self):
        result = fail("超时", timeout_seconds=10)
        assert result.detail == {"timeout_seconds": 10}

    def test_is_frozen(self):
        result = ok("成功")
        with pytest.raises(AttributeError):
            result.ok = False

    def test_direct_construction_also_works(self):
        result = ProbeResult(ok=True, message="手动构造", detail={"a": 1})
        assert result.ok is True
        assert result.detail == {"a": 1}

    def test_detail_defaults_to_empty_dict_not_shared(self):
        a = ProbeResult(ok=True, message="a")
        b = ProbeResult(ok=True, message="b")
        assert a.detail == {} and b.detail == {}
        assert a.detail is not b.detail


class TestRunCheckHappyPath:
    def test_passes_through_ok_result(self):
        def check(config):
            return ok("一切正常", host=config["host"])

        result = run_check(check, {"host": "example.com"})
        assert result.ok is True
        assert result.detail == {"host": "example.com"}

    def test_passes_through_fail_result(self):
        def check(config):
            return fail("凭据过期")

        result = run_check(check, {})
        assert result.ok is False
        assert result.message == "凭据过期"

    def test_config_is_passed_through_unchanged(self):
        seen = {}

        def check(config):
            seen["config"] = config
            return ok("ok")

        sentinel = object()
        run_check(check, sentinel)
        assert seen["config"] is sentinel


class TestRunCheckProtocolEnforcement:
    def test_exception_wrapped_as_failure_not_raised(self):
        def check(config):
            raise ConnectionError("无法连接到主机")

        result = run_check(check, {})
        assert result.ok is False
        assert "无法连接到主机" in result.message
        assert result.detail["exception_type"] == "ConnectionError"

    def test_exception_with_empty_message_falls_back_to_class_name(self):
        def check(config):
            raise RuntimeError()

        result = run_check(check, {})
        assert result.ok is False
        assert result.message == "RuntimeError"

    def test_non_proberesult_return_value_wrapped_as_failure(self):
        def check(config):
            return True  # 手滑返回了 bool，不是 ProbeResult

        result = run_check(check, {})
        assert result.ok is False
        assert "bool" in result.message
        assert result.detail["actual_type"] == "bool"

    def test_none_return_value_wrapped_as_failure(self):
        def check(config):
            return None

        result = run_check(check, {})
        assert result.ok is False
        assert result.detail["actual_type"] == "NoneType"

    def test_dict_return_value_wrapped_as_failure(self):
        def check(config):
            return {"ok": True, "message": "看起来像结果但不是"}

        result = run_check(check, {})
        assert result.ok is False
        assert result.detail["actual_type"] == "dict"

    def test_unexpected_exception_types_all_caught(self):
        for exc_type in (ValueError, TimeoutError, OSError, KeyError):

            def check(config, _exc=exc_type):
                raise _exc("boom")

            result = run_check(check, {})
            assert result.ok is False
            assert result.detail["exception_type"] == exc_type.__name__
