"""features/diagnostics 单测：只读、脱敏、摘要。"""

import pytest

from features.diagnostics.contract import DiagnosticTraceDTO


def test_diagnostic_trace_dto():
    """诊断 DTO 结构。"""
    from datetime import datetime
    dto = DiagnosticTraceDTO(
        id=1,
        work_id=100,
        source_url="https://example.com/paper",
        final_status="success",
        created_at=datetime.now(),
        steps_summary="resolver:generic_html → fetcher:direct → validate → success",
    )
    assert dto.work_id == 100
    assert "resolver" in dto.steps_summary


def test_import_service():
    """服务导入。"""
    from features.diagnostics.service import get_trace_for_display, list_traces_for_work_display
    assert callable(get_trace_for_display)
    assert callable(list_traces_for_work_display)
