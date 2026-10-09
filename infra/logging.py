"""结构化日志 + request_id。

request_id 用 contextvar 传递，这样 service 层不需要把它当参数一路传下去。
"""

import json
import logging
import sys
from contextvars import ContextVar
from pathlib import Path

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def current_request_id() -> str | None:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := current_request_id():
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure(level: str = "INFO", log_file: str | Path | None = None) -> None:
    """stdout handler 总是有（本地跑/容器日志采集都认它）；log_file
    给了就额外加一个文件 handler——这是 features/logs_viewer 读/清/导的
    唯一数据源，所以两个 handler 用同一个 JsonFormatter，保证落盘格式和
    屏幕上看到的一致。父目录不存在就先建好，避免 FileHandler 直接报错。
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))

    formatter = JsonFormatter()
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level.upper())
