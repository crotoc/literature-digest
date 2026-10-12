"""domain/traces 的服务层：CRUD 和诊断轨迹记录。"""

from sqlalchemy.orm import Session

from caps.redact import redact_mapping, redact_text
from domain.traces.contract import TraceDTO, TraceNotFound, TraceStepDTO
from domain.traces.models import Trace, TraceStep


def create_trace(
    db: Session, account_id: int, library_id: int, work_id: int, job_id: int, source_url: str
) -> TraceDTO:
    """创建一条新的下载诊断轨迹记录。"""
    trace = Trace(
        account_id=account_id,
        library_id=library_id,
        work_id=work_id,
        job_id=job_id,
        source_url=source_url,
        final_status="pending",
    )
    db.add(trace)
    db.flush()
    return _model_to_dto(trace)


def add_step(
    db: Session,
    trace_id: int,
    step_type: str,
    action: str | None = None,
    observation: dict | None = None,
    error_message: str | None = None,
    recovery_suggestion: str | None = None,
) -> None:
    """给轨迹添加一步记录。敏感信息自动脱敏。"""
    obs = observation or {}
    obs = redact_mapping(obs)
    error = redact_text(error_message) if error_message else None
    sugg = recovery_suggestion  # 建议文本不包含敏感信息

    step = TraceStep(
        trace_id=trace_id,
        step_type=step_type,
        action=action,
        observation=obs,
        error_message=error,
        recovery_suggestion=sugg,
    )
    db.add(step)
    db.flush()


def finalize_trace(db: Session, trace_id: int, final_status: str, final_reason: str | None = None) -> None:
    """完成轨迹记录，设置最终状态。"""
    trace = db.query(Trace).filter_by(id=trace_id).one()
    trace.final_status = final_status
    trace.final_reason = final_reason
    trace.completed_at = __import__("datetime").datetime.now(__import__("datetime").UTC).replace(tzinfo=None)
    db.flush()


def get_trace(db: Session, trace_id: int) -> TraceDTO:
    """读取一条轨迹及其所有步骤。"""
    trace = db.query(Trace).filter_by(id=trace_id).one_or_none()
    if not trace:
        raise TraceNotFound(f"Trace {trace_id} not found")
    return _model_to_dto(trace)


def list_traces_for_work(db: Session, work_id: int, limit: int = 100) -> list[TraceDTO]:
    """查看某篇文献的所有下载历史。"""
    traces = db.query(Trace).filter_by(work_id=work_id).order_by(Trace.created_at.desc()).limit(limit).all()
    return [_model_to_dto(t) for t in traces]


def _model_to_dto(trace: Trace) -> TraceDTO:
    """转换 ORM 模型到 DTO，确保步骤按时间排序。"""
    steps = sorted(trace.trace_steps, key=lambda s: s.created_at) if hasattr(trace, "trace_steps") else []
    return TraceDTO(
        id=trace.id,
        work_id=trace.work_id,
        source_url=trace.source_url,
        final_status=trace.final_status,
        final_reason=trace.final_reason,
        created_at=trace.created_at,
        completed_at=trace.completed_at,
        steps=[TraceStepDTO(
            id=s.id,
            step_type=s.step_type,
            action=s.action,
            observation=s.observation,
            error_message=s.error_message,
            recovery_suggestion=s.recovery_suggestion,
            created_at=s.created_at,
        ) for s in steps],
    )
