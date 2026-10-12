"""features/diagnostics 的公开接口。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class DiagnosticTraceDTO:
    id: int
    work_id: int
    source_url: str
    final_status: str
    created_at: datetime
    steps_summary: str  # 脱敏后的步骤摘要


class TraceNotFound(Exception):
    pass
