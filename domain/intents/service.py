"""domain/intents 的业务逻辑：研究意图的 CRUD + 查询编译版本 + 发现运行
记录 + 候选文献 + 跨轮去重标记。

本模块不认识 `domain.works`/`domain.jobs`/`adapters.sources`/
`adapters.llm` 的任何东西——`job_id` 只是裸 `int`，`source`/`source_id`
只是调用方约定的字符串，`query_json` 的内部结构（哪些 key、每个 source
的查询语法）完全由 `features/query_planner`/`features/discovery` 自己
解释，这里只负责存取。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定同 `domain/accounts/service.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.intents.models import Candidate, DiscoveryRun, Intent, IntentSeen, IntentVersion
from infra.errors import NotFound

_UNSET = object()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ── 异常 ─────────────────────────────────────────────────────────────────


class IntentNotFound(NotFound):
    code = "intent_not_found"


class IntentVersionNotFound(NotFound):
    code = "intent_version_not_found"


class DiscoveryRunNotFound(NotFound):
    code = "discovery_run_not_found"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntentDTO:
    id: int
    account_id: int
    name: str
    question_text: str
    enabled: bool
    candidate_limit: int
    lookback_days: int
    query_override: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class IntentVersionDTO:
    id: int
    intent_id: int
    query_json: dict
    source: str
    model: str | None
    status: str
    error: str | None
    created_at: datetime


@dataclass(frozen=True)
class DiscoveryRunDTO:
    id: int
    intent_id: int
    intent_version_id: int
    job_id: int
    status: str
    candidate_count: int
    new_candidate_count: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class CandidateDTO:
    id: int
    discovery_run_id: int
    intent_id: int
    source: str
    source_id: str
    title: str | None
    url: str | None
    payload: dict | None
    created_at: datetime


def _intent_dto(row: Intent) -> IntentDTO:
    return IntentDTO(
        id=row.id,
        account_id=row.account_id,
        name=row.name,
        question_text=row.question_text,
        enabled=row.enabled,
        candidate_limit=row.candidate_limit,
        lookback_days=row.lookback_days,
        query_override=row.query_override,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_dto(row: IntentVersion) -> IntentVersionDTO:
    return IntentVersionDTO(
        id=row.id,
        intent_id=row.intent_id,
        query_json=row.query_json,
        source=row.source,
        model=row.model,
        status=row.status,
        error=row.error,
        created_at=row.created_at,
    )


def _run_dto(row: DiscoveryRun) -> DiscoveryRunDTO:
    return DiscoveryRunDTO(
        id=row.id,
        intent_id=row.intent_id,
        intent_version_id=row.intent_version_id,
        job_id=row.job_id,
        status=row.status,
        candidate_count=row.candidate_count,
        new_candidate_count=row.new_candidate_count,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _candidate_dto(row: Candidate) -> CandidateDTO:
    return CandidateDTO(
        id=row.id,
        discovery_run_id=row.discovery_run_id,
        intent_id=row.intent_id,
        source=row.source,
        source_id=row.source_id,
        title=row.title,
        url=row.url,
        payload=row.payload_json,
        created_at=row.created_at,
    )


def _get_intent_row(db: Session, intent_id: int) -> Intent:
    row = db.get(Intent, intent_id)
    if row is None:
        raise IntentNotFound(f"研究意图不存在：{intent_id}")
    return row


def _get_version_row(db: Session, version_id: int) -> IntentVersion:
    row = db.get(IntentVersion, version_id)
    if row is None:
        raise IntentVersionNotFound(f"查询版本不存在：{version_id}")
    return row


def _get_run_row(db: Session, run_id: int) -> DiscoveryRun:
    row = db.get(DiscoveryRun, run_id)
    if row is None:
        raise DiscoveryRunNotFound(f"发现运行记录不存在：{run_id}")
    return row


# ── 研究意图 ─────────────────────────────────────────────────────────────


def create_intent(
    db: Session,
    *,
    account_id: int,
    name: str,
    question_text: str,
    candidate_limit: int = 40,
    lookback_days: int = 7,
    query_override: str | None = None,
) -> IntentDTO:
    name = name.strip()
    question_text = question_text.strip()
    if not name:
        raise ValueError("研究意图名称不能为空")
    if not question_text:
        raise ValueError("研究问题描述不能为空")
    if candidate_limit <= 0:
        raise ValueError("candidate_limit 必须大于 0")
    if lookback_days <= 0:
        raise ValueError("lookback_days 必须大于 0")

    row = Intent(
        account_id=account_id,
        name=name,
        question_text=question_text,
        candidate_limit=candidate_limit,
        lookback_days=lookback_days,
        query_override=query_override.strip() if query_override else None,
    )
    db.add(row)
    db.flush()
    return _intent_dto(row)


def get_intent(db: Session, intent_id: int) -> IntentDTO:
    return _intent_dto(_get_intent_row(db, intent_id))


def update_intent(
    db: Session,
    intent_id: int,
    *,
    name: str = _UNSET,
    question_text: str = _UNSET,
    enabled: bool = _UNSET,
    candidate_limit: int = _UNSET,
    lookback_days: int = _UNSET,
    query_override: str | None = _UNSET,
) -> IntentDTO:
    row = _get_intent_row(db, intent_id)
    if name is not _UNSET:
        name = name.strip()
        if not name:
            raise ValueError("研究意图名称不能为空")
        row.name = name
    if question_text is not _UNSET:
        question_text = question_text.strip()
        if not question_text:
            raise ValueError("研究问题描述不能为空")
        row.question_text = question_text
    if enabled is not _UNSET:
        row.enabled = enabled
    if candidate_limit is not _UNSET:
        if candidate_limit <= 0:
            raise ValueError("candidate_limit 必须大于 0")
        row.candidate_limit = candidate_limit
    if lookback_days is not _UNSET:
        if lookback_days <= 0:
            raise ValueError("lookback_days 必须大于 0")
        row.lookback_days = lookback_days
    if query_override is not _UNSET:
        row.query_override = query_override.strip() if query_override else None
    db.flush()
    return _intent_dto(row)


def delete_intent(db: Session, intent_id: int) -> None:
    """删除一个研究意图，级联删掉它名下的全部版本/发现运行/候选/去重标记
    ——这些东西离开 intent 本身都没有意义，不像 `domain/works` 的软删那种
    "要保留历史"的场景。"""
    row = _get_intent_row(db, intent_id)
    run_ids = list(db.scalars(select(DiscoveryRun.id).where(DiscoveryRun.intent_id == intent_id)))
    for candidate in db.scalars(select(Candidate).where(Candidate.intent_id == intent_id)):
        db.delete(candidate)
    for seen in db.scalars(select(IntentSeen).where(IntentSeen.intent_id == intent_id)):
        db.delete(seen)
    if run_ids:
        for run in db.scalars(select(DiscoveryRun).where(DiscoveryRun.id.in_(run_ids))):
            db.delete(run)
    for version in db.scalars(select(IntentVersion).where(IntentVersion.intent_id == intent_id)):
        db.delete(version)
    db.delete(row)
    db.flush()


def list_intents(db: Session, *, account_id: int) -> list[IntentDTO]:
    rows = db.scalars(select(Intent).where(Intent.account_id == account_id).order_by(Intent.id))
    return [_intent_dto(row) for row in rows]


# ── 查询编译版本 ─────────────────────────────────────────────────────────


def create_intent_version(
    db: Session,
    *,
    intent_id: int,
    query_json: dict,
    source: str,
    model: str | None = None,
    status: str = "ready",
    error: str | None = None,
) -> IntentVersionDTO:
    if source not in ("ai", "override"):
        raise ValueError(f"未知的版本来源：{source!r}，必须是 'ai' 或 'override'")
    if status not in ("ready", "failed"):
        raise ValueError(f"未知的版本状态：{status!r}，必须是 'ready' 或 'failed'")

    row = IntentVersion(
        intent_id=intent_id,
        query_json=dict(query_json),
        source=source,
        model=model,
        status=status,
        error=error,
    )
    db.add(row)
    db.flush()
    return _version_dto(row)


def get_latest_ready_version(db: Session, intent_id: int) -> IntentVersionDTO | None:
    row = db.scalar(
        select(IntentVersion)
        .where(IntentVersion.intent_id == intent_id, IntentVersion.status == "ready")
        .order_by(IntentVersion.created_at.desc())
        .limit(1)
    )
    return _version_dto(row) if row is not None else None


def list_intent_versions(db: Session, intent_id: int) -> list[IntentVersionDTO]:
    rows = db.scalars(
        select(IntentVersion).where(IntentVersion.intent_id == intent_id).order_by(
            IntentVersion.created_at.desc()
        )
    )
    return [_version_dto(row) for row in rows]


# ── 发现运行记录 ─────────────────────────────────────────────────────────


def create_discovery_run(
    db: Session, *, intent_id: int, intent_version_id: int, job_id: int
) -> DiscoveryRunDTO:
    row = DiscoveryRun(
        intent_id=intent_id,
        intent_version_id=intent_version_id,
        job_id=job_id,
        status="running",
    )
    db.add(row)
    db.flush()
    return _run_dto(row)


def finish_discovery_run(
    db: Session,
    run_id: int,
    *,
    status: str,
    candidate_count: int,
    new_candidate_count: int,
    error: str | None = None,
) -> DiscoveryRunDTO:
    if status not in ("succeeded", "failed"):
        raise ValueError(f"未知的结束状态：{status!r}，必须是 'succeeded' 或 'failed'")
    row = _get_run_row(db, run_id)
    row.status = status
    row.candidate_count = candidate_count
    row.new_candidate_count = new_candidate_count
    row.error = error
    row.finished_at = _utcnow()
    db.flush()
    return _run_dto(row)


def get_discovery_run(db: Session, run_id: int) -> DiscoveryRunDTO:
    return _run_dto(_get_run_row(db, run_id))


def list_discovery_runs(db: Session, *, intent_id: int, limit: int = 20) -> list[DiscoveryRunDTO]:
    rows = db.scalars(
        select(DiscoveryRun)
        .where(DiscoveryRun.intent_id == intent_id)
        .order_by(DiscoveryRun.started_at.desc())
        .limit(limit)
    )
    return [_run_dto(row) for row in rows]


# ── 候选文献 + 跨轮去重 ────────────────────────────────────────────────────


def is_seen(db: Session, *, intent_id: int, source: str, source_id: str) -> bool:
    row = db.scalar(
        select(IntentSeen.id).where(
            IntentSeen.intent_id == intent_id,
            IntentSeen.source == source,
            IntentSeen.source_id == source_id,
        )
    )
    return row is not None


def mark_seen(db: Session, *, intent_id: int, source: str, source_id: str) -> None:
    """记"这个 intent 见过这条来源记录了"，用于下一轮发现跳过重复。重复
    标记是幂等的——已经标记过就什么都不做。"""
    if is_seen(db, intent_id=intent_id, source=source, source_id=source_id):
        return
    row = IntentSeen(intent_id=intent_id, source=source, source_id=source_id)
    db.add(row)
    db.flush()


def create_candidate(
    db: Session,
    *,
    discovery_run_id: int,
    intent_id: int,
    source: str,
    source_id: str,
    title: str | None = None,
    url: str | None = None,
    payload: dict | None = None,
) -> CandidateDTO:
    """新建一条候选文献记录。同一个 intent 下 `(source, source_id)` 重复
    是幂等的，直接返回已有那条——理由同 `domain.tags.create_tag` 的同名
    说明：调用方（`features/discovery`）不需要先查一遍这个候选是不是已经
    存在。"""
    existing = db.scalar(
        select(Candidate).where(
            Candidate.intent_id == intent_id,
            Candidate.source == source,
            Candidate.source_id == source_id,
        )
    )
    if existing is not None:
        return _candidate_dto(existing)

    row = Candidate(
        discovery_run_id=discovery_run_id,
        intent_id=intent_id,
        source=source,
        source_id=source_id,
        title=title,
        url=url,
        payload_json=dict(payload) if payload else None,
    )
    db.add(row)
    db.flush()
    return _candidate_dto(row)


def list_candidates(
    db: Session, *, intent_id: int, discovery_run_id: int | None = _UNSET, limit: int = 100
) -> list[CandidateDTO]:
    stmt = select(Candidate).where(Candidate.intent_id == intent_id)
    if discovery_run_id is not _UNSET:
        stmt = stmt.where(Candidate.discovery_run_id == discovery_run_id)
    stmt = stmt.order_by(Candidate.created_at.desc()).limit(limit)
    return [_candidate_dto(row) for row in db.scalars(stmt)]
