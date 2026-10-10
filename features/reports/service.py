"""E4 报告漏斗：把一次发现运行物化为报告，再逐条筛标题和分析摘要。

本模块只编排 reports/intents/jobs 和 LLM adapter；不管理 AI profile、不发
消息、也不回写文献库标签。后两者分别属于已有的页面装配/后续 delivery。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from adapters.llm import anthropic, openai_compat
from adapters.llm.anthropic import AnthropicError
from adapters.llm.openai_compat import OpenAiCompatError
from caps.httpfetch import default_resolve
from domain.intents import get_discovery_run, get_intent, list_candidates
from domain.jobs import JobDTO, create_job, transition, update_counts
from domain.reports import (
    ReportDTO,
    create_report_item,
    get_or_create_report,
    get_report,
    list_report_items,
    update_report,
    update_report_item_analysis,
    update_report_item_title_screen,
)
from infra.errors import Conflict, PermissionDenied

PROVIDERS = frozenset({"openai_compat", "anthropic"})
JOB_KIND_TITLE_SCREEN = "report_title_screen"
JOB_KIND_ANALYZE = "report_analyze"
_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}

_PROVIDER_FNS = {
    "openai_compat": openai_compat.complete_structured,
    "anthropic": anthropic.complete_structured,
}
_PROVIDER_ERRORS = (OpenAiCompatError, AnthropicError)


class ReportNotReady(Conflict):
    code = "report_not_ready"


class _TitleScreenModel(BaseModel):
    keep: bool
    relevance_score: float = Field(ge=0, le=1)
    reason: str


class _AnalysisModel(BaseModel):
    relevance_score: float = Field(ge=0, le=1)
    novelty_score: float = Field(ge=0, le=1)
    evidence_strength_score: float = Field(ge=0, le=1)
    human_relevance_score: float = Field(ge=0, le=1)
    recommend: bool
    priority: str
    tags: list[str]
    reason: str


@dataclass(frozen=True)
class TitleScreenResult:
    report: ReportDTO
    job: JobDTO
    kept_count: int
    failed_count: int


@dataclass(frozen=True)
class AnalysisResult:
    report: ReportDTO
    job: JobDTO
    analyzed_count: int
    failed_count: int


def _require_owned_report(db: Session, *, account_id: int, report_id: int) -> ReportDTO:
    report = get_report(db, report_id)
    if report.account_id != account_id:
        raise PermissionDenied("无权操作这份报告")
    if report.status != "success":
        raise ReportNotReady("发现运行失败，不能分析该报告")
    return report


def _complete(
    *,
    provider: str,
    response_model,
    prompt: str,
    model: str,
    api_key: str,
    base_url: str | None,
    transport,
    resolve,
):
    if provider not in PROVIDERS:
        raise ValueError(f"不认识的 provider：{provider!r}")
    kwargs = {
        "response_model": response_model,
        "messages": [{"role": "user", "content": prompt}],
        "model": model,
        "api_key": api_key,
        "transport": transport,
        "resolve": resolve,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return _PROVIDER_FNS[provider](**kwargs)


def materialize_report(db: Session, *, account_id: int, intent_id: int, discovery_run_id: int) -> ReportDTO:
    """把 discovery run 的候选快照幂等地落为一份报告及条目。"""
    intent = get_intent(db, intent_id)
    if intent.account_id != account_id:
        raise PermissionDenied("无权访问这个研究意图")
    run = get_discovery_run(db, discovery_run_id)
    if run.intent_id != intent_id:
        raise ValueError("discovery run 不属于这个研究意图")

    candidates = list_candidates(db, intent_id=intent_id, discovery_run_id=discovery_run_id)
    report = get_or_create_report(
        db,
        account_id=account_id,
        intent_id=intent_id,
        discovery_run_id=discovery_run_id,
        status="success" if run.status == "succeeded" else "failed",
        candidate_count=len(candidates),
    )
    for candidate in candidates:
        create_report_item(
            db,
            report_id=report.id,
            source=candidate.source,
            source_id=candidate.source_id,
            title=candidate.title,
            url=candidate.url,
            payload=candidate.payload,
        )
    return update_report(db, report.id, candidate_count=len(candidates))


def screen_report_titles(
    db: Session,
    *,
    account_id: int,
    report_id: int,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    transport=None,
    resolve=default_resolve,
) -> TitleScreenResult:
    """逐条标题筛选。每条 LLM 失败单独落 `failed`，其他条目继续。"""
    report = _require_owned_report(db, account_id=account_id, report_id=report_id)
    intent = get_intent(db, report.intent_id)
    items = list_report_items(db, report_id=report.id, title_screen_status="not_started")
    job = create_job(db, account_id=account_id, kind=JOB_KIND_TITLE_SCREEN, counts={"total": len(items)})
    job = transition(db, job.id, "running", allowed=_TRANSITIONS)
    kept = failed = 0
    try:
        for item in items:
            prompt = (
                f"研究问题：{intent.question_text}\n标题：{item.title or '（无标题）'}\n"
                "判断是否保留，并给出 0-1 相关性和一句理由。"
            )
            try:
                result = _complete(
                    provider=provider,
                    response_model=_TitleScreenModel,
                    prompt=prompt,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    transport=transport,
                    resolve=resolve,
                )
            except _PROVIDER_ERRORS as exc:
                failed += 1
                update_report_item_title_screen(db, item.id, status="failed", reason=str(exc))
            else:
                status = "keep" if result.keep else "exclude"
                kept += int(result.keep)
                update_report_item_title_screen(
                    db, item.id, status=status, relevance_score=result.relevance_score, reason=result.reason
                )
            update_counts(db, job.id, {"total": len(items), "kept": kept, "failed": failed})
    except Exception as exc:
        job = transition(db, job.id, "failed", reason=str(exc), allowed=_TRANSITIONS)
        raise
    phase = "partial" if failed else "success"
    report = update_report(db, report.id, title_screen_status=phase, title_kept_count=kept)
    job = transition(db, job.id, "succeeded", allowed=_TRANSITIONS)
    return TitleScreenResult(report=report, job=job, kept_count=kept, failed_count=failed)


def analyze_report(
    db: Session,
    *,
    account_id: int,
    report_id: int,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    transport=None,
    resolve=default_resolve,
) -> AnalysisResult:
    """分析标题筛选保留的条目；失败隔离，成功条目照样可进入最终展示。"""
    report = _require_owned_report(db, account_id=account_id, report_id=report_id)
    if report.title_screen_status not in {"success", "partial"}:
        raise ReportNotReady("请先完成标题筛选")
    intent = get_intent(db, report.intent_id)
    items = list_report_items(db, report_id=report.id, title_screen_status="keep")
    pending = [item for item in items if item.analysis_status == "not_started"]
    job = create_job(db, account_id=account_id, kind=JOB_KIND_ANALYZE, counts={"total": len(pending)})
    job = transition(db, job.id, "running", allowed=_TRANSITIONS)
    analyzed = failed = 0
    try:
        for item in pending:
            abstract = (item.payload or {}).get("abstract") or "（来源未提供摘要）"
            prompt = (
                f"研究问题：{intent.question_text}\n标题：{item.title or '（无标题）'}\n摘要：{abstract}\n"
                "请评估相关性、新颖性、证据强度、人工阅读价值，给出推荐、优先级、标签和理由。"
            )
            try:
                result = _complete(
                    provider=provider,
                    response_model=_AnalysisModel,
                    prompt=prompt,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    transport=transport,
                    resolve=resolve,
                )
            except _PROVIDER_ERRORS as exc:
                failed += 1
                update_report_item_analysis(db, item.id, status="failed", reason=str(exc))
            else:
                analyzed += 1
                update_report_item_analysis(
                    db,
                    item.id,
                    status="success",
                    reason=result.reason,
                    relevance_score=result.relevance_score,
                    novelty_score=result.novelty_score,
                    evidence_strength_score=result.evidence_strength_score,
                    human_relevance_score=result.human_relevance_score,
                    recommend=result.recommend,
                    priority=result.priority,
                    tags=result.tags,
                    raw=result.model_dump(),
                    model=model,
                    prompt_id="report-analysis-v1",
                )
            update_counts(db, job.id, {"total": len(pending), "done": analyzed, "failed": failed})
    except Exception as exc:
        job = transition(db, job.id, "failed", reason=str(exc), allowed=_TRANSITIONS)
        raise
    phase = "partial" if failed else "success"
    report = update_report(db, report.id, analysis_status=phase, abstract_candidate_count=len(items))
    job = transition(db, job.id, "succeeded", allowed=_TRANSITIONS)
    return AnalysisResult(report=report, job=job, analyzed_count=analyzed, failed_count=failed)
