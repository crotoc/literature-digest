"""domain/traces 的公开接口：DTO + 签名 + 异常。"""

from dataclasses import dataclass
from datetime import datetime


class TraceNotFound(Exception):
    pass


@dataclass
class TraceDTO:
    id: int
    work_id: int
    source_url: str
    final_status: str
    final_reason: str | None
    created_at: datetime
    completed_at: datetime | None
    steps: list["TraceStepDTO"]


@dataclass
class TraceStepDTO:
    id: int
    step_type: str
    action: str | None
    observation: dict
    error_message: str | None
    recovery_suggestion: str | None
    created_at: datetime
