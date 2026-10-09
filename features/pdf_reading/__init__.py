"""features/pdf_reading。详见 contract.py。"""

from features.pdf_reading.contract import (
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
