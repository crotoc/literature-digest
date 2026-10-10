"""报告漏斗编排；外部只从 contract 导入。"""

from features.reports.contract import (  # noqa: F401
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
