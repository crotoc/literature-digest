import json
import logging

from features.logs_viewer import clear_log, export_log, list_entries
from infra.logging import configure as configure_logging


def _write_lines(path, *lines: dict) -> None:
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n")


# ── list_entries：基本行为 ───────────────────────────────────────────────


def test_list_entries_returns_empty_for_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.log"

    assert list_entries(log_file=missing) == []


def test_list_entries_returns_newest_first(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {"ts": "2026-10-09T10:00:00+0000", "level": "INFO", "logger": "x", "msg": "first"},
        {"ts": "2026-10-09T10:00:01+0000", "level": "INFO", "logger": "x", "msg": "second"},
        {"ts": "2026-10-09T10:00:02+0000", "level": "INFO", "logger": "x", "msg": "third"},
    )

    entries = list_entries(log_file=log_file)

    assert [e.msg for e in entries] == ["third", "second", "first"]


def test_list_entries_skips_malformed_lines(tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text(
        '{"ts": "t", "level": "INFO", "logger": "x", "msg": "good"}\n'
        "not even json\n"
        '"just a json string, not an object"\n'
        '{"ts": "t2", "level": "INFO", "logger": "x", "msg": "also good"}\n'
        "\n"  # 空行
    )

    entries = list_entries(log_file=log_file)

    assert [e.msg for e in entries] == ["also good", "good"]


def test_list_entries_pagination(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        *[
            {"ts": f"2026-10-09T10:00:{i:02d}+0000", "level": "INFO", "logger": "x", "msg": f"m{i}"}
            for i in range(5)
        ],
    )

    page = list_entries(log_file=log_file, limit=2, offset=1)

    # 倒序后是 m4,m3,m2,m1,m0；offset=1,limit=2 取 m3,m2
    assert [e.msg for e in page] == ["m3", "m2"]


# ── list_entries：筛选 ──────────────────────────────────────────────────


def test_list_entries_filters_by_min_level(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {"ts": "t1", "level": "DEBUG", "logger": "x", "msg": "debug line"},
        {"ts": "t2", "level": "WARNING", "logger": "x", "msg": "warn line"},
        {"ts": "t3", "level": "ERROR", "logger": "x", "msg": "error line"},
    )

    entries = list_entries(log_file=log_file, min_level="WARNING")

    assert {e.level for e in entries} == {"WARNING", "ERROR"}


def test_list_entries_filters_by_search_case_insensitive(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {"ts": "t1", "level": "INFO", "logger": "x", "msg": "Connection Failed"},
        {"ts": "t2", "level": "INFO", "logger": "x", "msg": "all good"},
    )

    entries = list_entries(log_file=log_file, search="failed")

    assert [e.msg for e in entries] == ["Connection Failed"]


def test_list_entries_filters_by_request_id(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {"ts": "t1", "level": "INFO", "logger": "x", "msg": "a", "request_id": "rid-1"},
        {"ts": "t2", "level": "INFO", "logger": "x", "msg": "b", "request_id": "rid-2"},
    )

    entries = list_entries(log_file=log_file, request_id="rid-2")

    assert [e.msg for e in entries] == ["b"]


def test_list_entries_filters_by_since_until(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {"ts": "2026-10-09T10:00:00+0000", "level": "INFO", "logger": "x", "msg": "early"},
        {"ts": "2026-10-09T10:05:00+0000", "level": "INFO", "logger": "x", "msg": "middle"},
        {"ts": "2026-10-09T10:10:00+0000", "level": "INFO", "logger": "x", "msg": "late"},
    )

    entries = list_entries(
        log_file=log_file,
        since="2026-10-09T10:02:00+0000",
        until="2026-10-09T10:08:00+0000",
    )

    assert [e.msg for e in entries] == ["middle"]


# ── 脱敏 ────────────────────────────────────────────────────────────────


def test_list_entries_redacts_msg_text(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {
            "ts": "t1",
            "level": "ERROR",
            "logger": "x",
            "msg": "调用失败，Authorization: Bearer sk-abcdef1234567890",
        },
    )

    [entry] = list_entries(log_file=log_file)

    assert "sk-abcdef1234567890" not in entry.msg
    assert "<redacted>" in entry.msg


def test_list_entries_redacts_sensitive_extra_field(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {
            "ts": "t1",
            "level": "INFO",
            "logger": "x",
            "msg": "连接测试",
            "api_key": "my-real-secret-key",
        },
    )

    [entry] = list_entries(log_file=log_file)

    assert entry.extra["api_key"] == "<redacted>"


def test_list_entries_redacts_query_string_in_url_extra_field(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {
            "ts": "t1",
            "level": "INFO",
            "logger": "x",
            "msg": "回报观察",
            "url": "https://example.org/reset?token=abc123",
        },
    )

    [entry] = list_entries(log_file=log_file)

    assert "abc123" not in entry.extra["url"]
    assert entry.extra["url"].startswith("https://example.org/reset")


# ── clear_log ────────────────────────────────────────────────────────────


def test_clear_log_truncates_and_returns_previous_size(tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text("some content here\n")
    previous_size = log_file.stat().st_size

    cleared = clear_log(log_file=log_file)

    assert cleared == previous_size
    assert log_file.read_text() == ""


def test_clear_log_on_missing_file_returns_zero(tmp_path):
    missing = tmp_path / "does-not-exist.log"

    assert clear_log(log_file=missing) == 0


# ── export_log ───────────────────────────────────────────────────────────


def test_export_log_returns_jsonl_in_chronological_order(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {"ts": "t1", "level": "INFO", "logger": "x", "msg": "first", "request_id": "r1"},
        {"ts": "t2", "level": "INFO", "logger": "x", "msg": "second"},
    )

    exported = export_log(log_file=log_file)
    lines = [json.loads(line) for line in exported.splitlines()]

    assert [line["msg"] for line in lines] == ["first", "second"]
    assert lines[0]["request_id"] == "r1"
    assert "request_id" not in lines[1]


def test_export_log_applies_same_filters_as_list_entries(tmp_path):
    log_file = tmp_path / "app.log"
    _write_lines(
        log_file,
        {"ts": "t1", "level": "DEBUG", "logger": "x", "msg": "debug"},
        {"ts": "t2", "level": "ERROR", "logger": "x", "msg": "boom"},
    )

    exported = export_log(log_file=log_file, min_level="ERROR")
    lines = [json.loads(line) for line in exported.splitlines()]

    assert [line["msg"] for line in lines] == ["boom"]


# ── 和 infra.logging 的真实集成 ─────────────────────────────────────────


def test_list_entries_reads_back_what_infra_logging_actually_writes(tmp_path):
    """用真正的 JsonFormatter 写一行，再用 list_entries 读回来——确保两边
    对日志行的格式理解完全一致，不是靠手写 fixture 自说自话。
    """
    log_file = tmp_path / "app.log"
    configure_logging("INFO", log_file)
    try:
        logger = logging.getLogger("logs_viewer.integration_test")
        logger.warning(
            "连接测试失败", extra={"extra_fields": {"connection_id": 42, "password": "sh0uldnotleak"}}
        )
    finally:
        logging.getLogger().handlers[:] = []

    [entry] = list_entries(log_file=log_file)

    assert entry.level == "WARNING"
    assert entry.logger == "logs_viewer.integration_test"
    assert entry.msg == "连接测试失败"
    assert entry.extra["connection_id"] == 42
    assert entry.extra["password"] == "<redacted>"
