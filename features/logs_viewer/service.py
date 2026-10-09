"""features/logs_viewer：日志查看（读 / 清 / 导）。

组合 infra/logging（负责写）+ caps/redact（负责读取时脱敏）。本模块
不开表——操作对象是 infra.config.Settings.log_file 指向的 JSON Lines
文件，每行是 infra.logging.JsonFormatter 写出的一条结构化日志。

读取链路里没有任何环节原样暴露明文：每一行先解析成原始 dict，整体过一遍
caps.redact.redact_mapping 才拆成 LogEntryDTO 返回——`msg` 里的自由文本
和结构化字段（`extra`）用同一份脱敏规则，不需要调用方分别处理两处。

v1 只用 DEFAULT_POLICY（基于键名 + 正则模式识别），**没有接 known_values**
——要把已知密钥原文塞进策略，需要先解密 domain.connections 里的凭据，
那是业务层知识，而本模块刻意不依赖任何 domain（plan 里 logs_viewer 只
组合 infra/logging + caps/redact）。caps/redact 自己的文档说得很清楚：
已知值精确匹配最可靠，正则模式是兜底——本模块只用得上兜底这一层，这是
架构边界带来的权衡，不是没想到。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from caps.redact import DEFAULT_POLICY, RedactionPolicy, redact_mapping

_KNOWN_FIELDS = frozenset({"ts", "level", "logger", "msg", "request_id", "exc"})
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


@dataclass(frozen=True)
class LogEntryDTO:
    ts: str
    level: str
    logger: str
    msg: str
    request_id: str | None
    exc: str | None
    extra: Mapping[str, object] = field(default_factory=dict)


def _level_rank(level: str) -> int:
    return _LEVEL_ORDER.get(level.upper(), 0)


def _parse_line(line: str, *, policy: RedactionPolicy) -> LogEntryDTO | None:
    """容忍坏行：不是合法 JSON、或解析出来不是对象的行直接跳过，不抛异常。

    日志文件可能正被 infra.logging 的 FileHandler 并发追加，读到的最后一行
    有可能是还没写完的半行——这是正常现象，不是需要上报的错误。
    """
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None

    redacted = redact_mapping(raw, policy=policy)
    extra = {key: value for key, value in redacted.items() if key not in _KNOWN_FIELDS}
    return LogEntryDTO(
        ts=str(redacted.get("ts", "")),
        level=str(redacted.get("level", "")),
        logger=str(redacted.get("logger", "")),
        msg=str(redacted.get("msg", "")),
        request_id=redacted.get("request_id"),
        exc=redacted.get("exc"),
        extra=extra,
    )


def _matches(
    entry: LogEntryDTO,
    *,
    min_level: str | None,
    search: str | None,
    request_id: str | None,
    since: str | None,
    until: str | None,
) -> bool:
    if min_level and _level_rank(entry.level) < _level_rank(min_level):
        return False
    if request_id is not None and entry.request_id != request_id:
        return False
    if search is not None and search.lower() not in entry.msg.lower():
        return False
    if since is not None and entry.ts < since:
        return False
    if until is not None and entry.ts > until:
        return False
    return True


def _filtered_entries(
    *,
    log_file: str | Path,
    min_level: str | None,
    search: str | None,
    request_id: str | None,
    since: str | None,
    until: str | None,
    policy: RedactionPolicy,
) -> list[LogEntryDTO]:
    """按文件原本顺序（旧→新）返回，过滤后还没脱敏以外的加工。"""
    path = Path(log_file)
    if not path.exists():
        return []

    entries: list[LogEntryDTO] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            entry = _parse_line(line, policy=policy)
            if entry is None:
                continue
            if _matches(
                entry,
                min_level=min_level,
                search=search,
                request_id=request_id,
                since=since,
                until=until,
            ):
                entries.append(entry)
    return entries


def list_entries(
    *,
    log_file: str | Path,
    min_level: str | None = None,
    search: str | None = None,
    request_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = 200,
    offset: int = 0,
    policy: RedactionPolicy = DEFAULT_POLICY,
) -> list[LogEntryDTO]:
    """给查看页用：最新的排前面（文件里是追加顺序，读完整体倒过来）。

    `log_file` 不存在时当成"还没有任何日志"返回空列表，不是错误——进程
    刚启动、一行日志都还没落盘是正常状态，不该让查看页报错。
    """
    entries = _filtered_entries(
        log_file=log_file,
        min_level=min_level,
        search=search,
        request_id=request_id,
        since=since,
        until=until,
        policy=policy,
    )
    entries.reverse()
    if limit is None:
        return entries[offset:]
    return entries[offset : offset + limit]


def clear_log(*, log_file: str | Path) -> int:
    """清空日志文件，返回清空前的字节数（给调用方显示"刚刚清掉了多少"）。

    `infra.logging` 的 FileHandler 用默认 mode="a"（追加），每次写入都由
    操作系统定位到当前文件末尾——这意味着从外部截断文件是安全的：哪怕
    handler 还持有着那个文件描述符，下一次写入也会从新的（空）文件末尾
    开始，不会出现"写到旧偏移量、文件中间留空洞"的问题。
    """
    path = Path(log_file)
    if not path.exists():
        return 0
    size = path.stat().st_size
    path.write_text("", encoding="utf-8")
    return size


def _entry_to_dict(entry: LogEntryDTO) -> dict[str, object]:
    payload: dict[str, object] = {
        "ts": entry.ts,
        "level": entry.level,
        "logger": entry.logger,
        "msg": entry.msg,
    }
    if entry.request_id is not None:
        payload["request_id"] = entry.request_id
    if entry.exc is not None:
        payload["exc"] = entry.exc
    payload.update(entry.extra)
    return payload


def export_log(
    *,
    log_file: str | Path,
    min_level: str | None = None,
    search: str | None = None,
    request_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    policy: RedactionPolicy = DEFAULT_POLICY,
) -> str:
    """导出成 JSON Lines 文本（和落盘格式一致，调用方可以直接存成 .log 文件）。

    不分页——导出的意图就是要全量；`list_entries` 的分页是给"看"用的，
    两者需求不同不共用一个 limit 语义。导出按文件原本的时间顺序（旧→新），
    不像 list_entries 那样倒序，因为导出文件通常要能从头往下读。
    """
    entries = _filtered_entries(
        log_file=log_file,
        min_level=min_level,
        search=search,
        request_id=request_id,
        since=since,
        until=until,
        policy=policy,
    )
    return "\n".join(json.dumps(_entry_to_dict(e), ensure_ascii=False) for e in entries)
