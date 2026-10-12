"""features/extension_dist 的公开接口。"""

from dataclasses import dataclass


@dataclass
class ExtensionPackageDTO:
    filename: str  # e.g. "literature-digest-ext.zip"
    content: bytes
    content_type: str = "application/zip"


class ExtensionPackageError(Exception):
    pass
