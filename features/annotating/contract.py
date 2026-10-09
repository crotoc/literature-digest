"""features/annotating 的对外表面。外部只许 import 这里的东西。"""

from features.annotating.service import (
    UpdateMetadataResult,
    get_work_note,
    set_work_note,
    update_metadata,
)

__all__ = [
    "UpdateMetadataResult",
    "get_work_note",
    "set_work_note",
    "update_metadata",
]
