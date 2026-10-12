"""domain/traces 的 ORM 模型：记录全文下载的诊断轨迹。"""

from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)
    library_id: Mapped[int] = mapped_column(index=True)
    work_id: Mapped[int] = mapped_column(index=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)

    source_url: Mapped[str] = mapped_column(String(2048))
    final_status: Mapped[str] = mapped_column(String(32))
    final_reason: Mapped[str | None] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column()

    # 级联删除 trace 时自动删 trace_steps
    trace_steps: Mapped[list["TraceStep"]] = relationship(
        "TraceStep", 
        back_populates="trace",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class TraceStep(Base):
    __tablename__ = "trace_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[int] = mapped_column(ForeignKey("traces.id"), index=True)

    step_type: Mapped[str] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(256))
    observation: Mapped[dict] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    recovery_suggestion: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    trace: Mapped[Trace] = relationship("Trace", back_populates="trace_steps")
