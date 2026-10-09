"""features/pdf_reading 的对外表面。外部只许 import 这里的东西。"""

from features.pdf_reading.service import (
    VIEWABLE_CONTENT_TYPES,
    NotViewable,
    get_main_attachment_for_view,
    open_for_view,
)

__all__ = [
    "VIEWABLE_CONTENT_TYPES",
    "NotViewable",
    "get_main_attachment_for_view",
    "open_for_view",
]
