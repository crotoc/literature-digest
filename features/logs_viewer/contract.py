"""features/logs_viewer 的对外表面。外部只许 import 这里的东西。"""

from features.logs_viewer.service import (
    LogEntryDTO,
    clear_log,
    export_log,
    list_entries,
)

__all__ = [
    "LogEntryDTO",
    "clear_log",
    "export_log",
    "list_entries",
]
