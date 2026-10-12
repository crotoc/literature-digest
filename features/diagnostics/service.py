"""features/diagnostics 服务层：只读轨迹查询与展示。"""

from sqlalchemy.orm import Session

from domain.traces.service import get_trace, list_traces_for_work
from domain.traces.contract import TraceDTO
from caps.redact import redact_text
from features.diagnostics.contract import DiagnosticTraceDTO, TraceNotFound


def get_trace_for_display(db: Session, trace_id: int) -> DiagnosticTraceDTO:
    """
    读取一条轨迹，脱敏展示（纵深防御）。
    """
    try:
        trace_dto = get_trace(db, trace_id)
    except Exception as e:
        raise TraceNotFound(f"Trace {trace_id} not found") from e

    # 再过一遍脱敏（纵深防御——虽然存库时已脱敏）
    steps_summary = _summarize_steps(trace_dto.steps)

    return DiagnosticTraceDTO(
        id=trace_dto.id,
        work_id=trace_dto.work_id,
        source_url=redact_text(trace_dto.source_url),
        final_status=trace_dto.final_status,
        created_at=trace_dto.created_at,
        steps_summary=steps_summary,
    )


def list_traces_for_work_display(db: Session, work_id: int, limit: int = 100) -> list[DiagnosticTraceDTO]:
    """
    列举某篇文献的所有下载历史（脱敏展示）。
    """
    traces = list_traces_for_work(db, work_id, limit=limit)
    return [
        DiagnosticTraceDTO(
            id=t.id,
            work_id=t.work_id,
            source_url=redact_text(t.source_url),
            final_status=t.final_status,
            created_at=t.created_at,
            steps_summary=_summarize_steps(t.steps),
        )
        for t in traces
    ]


def _summarize_steps(steps: list) -> str:
    """
    把轨迹步骤压成一行摘要（用于列表视图）。
    
    格式：resolver:generic_html → fetcher:direct(failed) → resolver:nature → ... → success
    """
    if not steps:
        return "(no steps)"

    summary_parts = []
    for step in steps:
        if step.step_type == "resolver" and step.action:
            summary_parts.append(f"resolver:{step.action}")
        elif step.step_type == "fetcher" and step.action:
            reason = f"({step.error_message.split(':')[0]})" if step.error_message else ""
            summary_parts.append(f"fetcher:{step.action}{reason}")
        elif step.step_type == "observation":
            summary_parts.append("observe")
        elif step.step_type == "validation":
            summary_parts.append("validate")

    return " → ".join(summary_parts) if summary_parts else "(unknown)"
