"""features/extension_dist 单测：打包、DTO、错误处理。"""

import pytest

from features.extension_dist.contract import ExtensionPackageDTO, ExtensionPackageError


def test_extension_package_dto():
    """扩展包 DTO 结构。"""
    dto = ExtensionPackageDTO(
        filename="literature-digest-ext.zip",
        content=b"PK\x03\x04...",  # ZIP magic number
    )
    assert dto.filename == "literature-digest-ext.zip"
    assert dto.content.startswith(b"PK")
    assert dto.content_type == "application/zip"


def test_import_service():
    """服务导入。"""
    from features.extension_dist.service import package_extension
    assert callable(package_extension)
