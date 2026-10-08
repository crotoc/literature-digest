"""caps/fileprobe 的对外表面。外部只许 import 这里的东西。"""

from caps.fileprobe.service import (
    CorruptFile,
    FileProbe,
    FileProbeError,
    FileTooLarge,
    PdfInfo,
    UnsupportedMediaType,
    check_upload,
    probe_pdf,
    sniff,
)

__all__ = [
    "CorruptFile",
    "FileProbe",
    "FileProbeError",
    "FileTooLarge",
    "PdfInfo",
    "UnsupportedMediaType",
    "check_upload",
    "probe_pdf",
    "sniff",
]
