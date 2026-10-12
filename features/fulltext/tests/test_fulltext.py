"""features/fulltext 烟雾测试：导入、contract、基本 CRUD。"""

import pytest

from features.fulltext.contract import FulltextJobStatus, FULLTEXT_TRANSITIONS


def test_status_enum():
    """状态枚举定义齐全。"""
    assert FulltextJobStatus.QUEUED
    assert FulltextJobStatus.SUCCESS
    assert FulltextJobStatus.FAILED


def test_transitions_table():
    """状态转移表覆盖主路径。"""
    assert "queued" in FULLTEXT_TRANSITIONS
    assert "resolving" in FULLTEXT_TRANSITIONS
    assert "fetching" in FULLTEXT_TRANSITIONS
    # success/failed 是 terminal，不需要转移


def test_import_service():
    """服务模块可导入。"""
    from features.fulltext.service import initiate_fulltext_download, record_observation, record_upload
    assert callable(initiate_fulltext_download)
