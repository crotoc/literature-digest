"""caps/archive 的对外表面。外部只许 import 这里的东西。"""

from caps.archive.service import (
    CHUNK_SIZE,
    DEFAULT_COMPRESS_LEVEL,
    DEFAULT_COMPRESSION,
    ArchiveError,
    DuplicateEntryName,
    UnsafeEntryName,
    ZipEntry,
    entry,
    iter_zip_entries,
    sanitize_entry_name,
    write_zip_to,
)

__all__ = [
    "CHUNK_SIZE",
    "DEFAULT_COMPRESS_LEVEL",
    "DEFAULT_COMPRESSION",
    "ArchiveError",
    "DuplicateEntryName",
    "UnsafeEntryName",
    "ZipEntry",
    "entry",
    "iter_zip_entries",
    "sanitize_entry_name",
    "write_zip_to",
]
