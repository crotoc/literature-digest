"""features/reports 的对外表面。"""

from features.reports.service import (
    JOB_KIND_ANALYZE,
    JOB_KIND_TITLE_SCREEN,
    PROVIDERS,
    AnalysisResult,
    ReportNotReady,
    TitleScreenResult,
    analyze_report,
    materialize_report,
    screen_report_titles,
)

__all__ = [
    "AnalysisResult",
    "JOB_KIND_ANALYZE",
    "JOB_KIND_TITLE_SCREEN",
    "PROVIDERS",
    "ReportNotReady",
    "TitleScreenResult",
    "analyze_report",
    "materialize_report",
    "screen_report_titles",
]
