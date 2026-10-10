"""domain/reports 的业务逻辑：报告 + 报告条目（标题筛选 + AI 摘要分析两阶段
结果）+ 投递记录的 CRUD。

本模块不认识 `domain.intents`/`domain.connections`/`adapters.llm`/
`adapters.delivery` 的任何东西——`intent_id`/`discovery_run_id`/
`connection_id` 全是裸 `int`，`payload_json`/`raw_json`/`tags_json` 的内部
结构完全由 `features/reports`/`features/delivery` 自己解释，这里只负责
存取。状态设计延续 `domain/jobs` 的 status + reason 两个独立字段的约定
（见 domain/jobs/README.md）：报告和条目的每个阶段都有一个本模块定义的、
有限的粗粒度 `*_status`，以及一个调用方自定义、本模块只存不解释的
`*_reason`/`error` 字符串，二者不混用同一个字段。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定同 `domain/accounts/service.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.reports.models import Delivery, Report, ReportItem
from infra.errors import NotFound

_UNSET = object()

REPORT_STATUSES = frozenset({"success", "failed"})
PHASE_STATUSES = frozenset({"not_started", "success", "partial", "failed"})
ITEM_TITLE_SCREEN_STATUSES = frozenset({"not_started", "keep", "exclude", "deferred", "failed"})
ITEM_ANALYSIS_STATUSES = frozenset({"not_started", "success", "failed"})
DELIVERY_STATUSES = frozenset({"pending", "success", "failed"})


# ── 异常 ─────────────────────────────────────────────────────────────────


class ReportNotFound(NotFound):
    code = "report_not_found"


class ReportItemNotFound(NotFound):
    code = "report_item_not_found"


class DeliveryNotFound(NotFound):
    code = "delivery_not_found"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReportDTO:
    id: int
    account_id: int
    intent_id: int
    discovery_run_id: int
    status: str
    title_screen_status: str
    analysis_status: str
    candidate_count: int
    title_kept_count: int
    abstract_candidate_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReportItemDTO:
    id: int
    report_id: int
    source: str
    source_id: str
    title: str | None
    url: str | None
    payload: dict | None
    title_screen_status: str
    title_screen_reason: str | None
    title_relevance_score: float | None
    analysis_status: str
    analysis_reason: str | None
    relevance_score: float | None
    novelty_score: float | None
    evidence_strength_score: float | None
    human_relevance_score: float | None
    recommend: bool | None
    priority: str | None
    tags: list | None
    raw: dict | None
    model: str | None
    prompt_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DeliveryDTO:
    id: int
    report_id: int
    connection_id: int
    status: str
    message_ids: list | None
    error: str | None
    created_at: datetime
    updated_at: datetime


def _report_dto(row: Report) -> ReportDTO:
    return ReportDTO(
        id=row.id,
        account_id=row.account_id,
        intent_id=row.intent_id,
        discovery_run_id=row.discovery_run_id,
        status=row.status,
        title_screen_status=row.title_screen_status,
        analysis_status=row.analysis_status,
        candidate_count=row.candidate_count,
        title_kept_count=row.title_kept_count,
        abstract_candidate_count=row.abstract_candidate_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item_dto(row: ReportItem) -> ReportItemDTO:
    return ReportItemDTO(
        id=row.id,
        report_id=row.report_id,
        source=row.source,
        source_id=row.source_id,
        title=row.title,
        url=row.url,
        payload=row.payload_json,
        title_screen_status=row.title_screen_status,
        title_screen_reason=row.title_screen_reason,
        title_relevance_score=row.title_relevance_score,
        analysis_status=row.analysis_status,
        analysis_reason=row.analysis_reason,
        relevance_score=row.relevance_score,
        novelty_score=row.novelty_score,
        evidence_strength_score=row.evidence_strength_score,
        human_relevance_score=row.human_relevance_score,
        recommend=row.recommend,
        priority=row.priority,
        tags=row.tags_json,
        raw=row.raw_json,
        model=row.model,
        prompt_id=row.prompt_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _delivery_dto(row: Delivery) -> DeliveryDTO:
    return DeliveryDTO(
        id=row.id,
        report_id=row.report_id,
        connection_id=row.connection_id,
        status=row.status,
        message_ids=row.message_ids_json,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_report_row(db: Session, report_id: int) -> Report:
    row = db.get(Report, report_id)
    if row is None:
        raise ReportNotFound(f"报告不存在：{report_id}")
    return row


def _get_item_row(db: Session, item_id: int) -> ReportItem:
    row = db.get(ReportItem, item_id)
    if row is None:
        raise ReportItemNotFound(f"报告条目不存在：{item_id}")
    return row


def _get_delivery_row(db: Session, delivery_id: int) -> Delivery:
    row = db.get(Delivery, delivery_id)
    if row is None:
        raise DeliveryNotFound(f"投递记录不存在：{delivery_id}")
    return row


# ── 报告 ─────────────────────────────────────────────────────────────────


def create_report(
    db: Session,
    *,
    account_id: int,
    intent_id: int,
    discovery_run_id: int,
    status: str,
    candidate_count: int = 0,
) -> ReportDTO:
    if status not in REPORT_STATUSES:
        raise ValueError(f"未知的报告状态：{status!r}，必须是 {sorted(REPORT_STATUSES)} 之一")
    row = Report(
        account_id=account_id,
        intent_id=intent_id,
        discovery_run_id=discovery_run_id,
        status=status,
        candidate_count=candidate_count,
    )
    db.add(row)
    db.flush()
    return _report_dto(row)


def get_report(db: Session, report_id: int) -> ReportDTO:
    return _report_dto(_get_report_row(db, report_id))


def list_reports(
    db: Session,
    *,
    account_id: int,
    intent_id: int | None = _UNSET,
    limit: int = 50,
) -> list[ReportDTO]:
    stmt = select(Report).where(Report.account_id == account_id)
    if intent_id is not _UNSET:
        stmt = stmt.where(Report.intent_id == intent_id)
    stmt = stmt.order_by(Report.created_at.desc()).limit(limit)
    return [_report_dto(row) for row in db.scalars(stmt)]


def update_report(
    db: Session,
    report_id: int,
    *,
    title_screen_status: str = _UNSET,
    analysis_status: str = _UNSET,
    candidate_count: int = _UNSET,
    title_kept_count: int = _UNSET,
    abstract_candidate_count: int = _UNSET,
) -> ReportDTO:
    """整体更新报告的阶段状态/计数——传哪个改哪个，不传的保持原值。阶段
    状态的合法取值集合由本模块定义（`PHASE_STATUSES`），具体什么时候算
    `"partial"`（有条目失败但不整体失败）由调用方（`features/reports`）
    根据条目结果自己判断后传进来，本模块不做这个聚合判断。"""
    row = _get_report_row(db, report_id)
    if title_screen_status is not _UNSET:
        if title_screen_status not in PHASE_STATUSES:
            raise ValueError(f"未知的阶段状态：{title_screen_status!r}")
        row.title_screen_status = title_screen_status
    if analysis_status is not _UNSET:
        if analysis_status not in PHASE_STATUSES:
            raise ValueError(f"未知的阶段状态：{analysis_status!r}")
        row.analysis_status = analysis_status
    if candidate_count is not _UNSET:
        row.candidate_count = candidate_count
    if title_kept_count is not _UNSET:
        row.title_kept_count = title_kept_count
    if abstract_candidate_count is not _UNSET:
        row.abstract_candidate_count = abstract_candidate_count
    db.flush()
    return _report_dto(row)


# ── 报告条目 ─────────────────────────────────────────────────────────────


def create_report_item(
    db: Session,
    *,
    report_id: int,
    source: str,
    source_id: str,
    title: str | None = None,
    url: str | None = None,
    payload: dict | None = None,
) -> ReportItemDTO:
    """新建一条报告条目。同一份报告下 `(source, source_id)` 重复是幂等的，
    直接返回已有那条——理由同 `domain.intents.create_candidate`：调用方
    不需要先查一遍这个条目是不是已经存在。"""
    _get_report_row(db, report_id)
    existing = db.scalar(
        select(ReportItem).where(
            ReportItem.report_id == report_id,
            ReportItem.source == source,
            ReportItem.source_id == source_id,
        )
    )
    if existing is not None:
        return _item_dto(existing)

    row = ReportItem(
        report_id=report_id,
        source=source,
        source_id=source_id,
        title=title,
        url=url,
        payload_json=dict(payload) if payload else None,
    )
    db.add(row)
    db.flush()
    return _item_dto(row)


def get_report_item(db: Session, item_id: int) -> ReportItemDTO:
    return _item_dto(_get_item_row(db, item_id))


def list_report_items(
    db: Session,
    *,
    report_id: int,
    title_screen_status: str | None = _UNSET,
) -> list[ReportItemDTO]:
    stmt = select(ReportItem).where(ReportItem.report_id == report_id)
    if title_screen_status is not _UNSET:
        stmt = stmt.where(ReportItem.title_screen_status == title_screen_status)
    stmt = stmt.order_by(ReportItem.created_at.asc())
    return [_item_dto(row) for row in db.scalars(stmt)]


def update_report_item_metadata(
    db: Session,
    item_id: int,
    *,
    title: str | None = _UNSET,
    url: str | None = _UNSET,
    payload: dict | None = _UNSET,
) -> ReportItemDTO:
    """回填条目的标题/链接/原始数据——用于 AI 分析前先回抓一次来源元数据
    补全这些字段（旧代码的"回抓 PubMed 元数据补 title/url/payload"）。"""
    row = _get_item_row(db, item_id)
    if title is not _UNSET:
        row.title = title
    if url is not _UNSET:
        row.url = url
    if payload is not _UNSET:
        row.payload_json = dict(payload) if payload else None
    db.flush()
    return _item_dto(row)


def update_report_item_title_screen(
    db: Session,
    item_id: int,
    *,
    status: str,
    relevance_score: float | None = None,
    reason: str | None = None,
) -> ReportItemDTO:
    if status not in ITEM_TITLE_SCREEN_STATUSES:
        raise ValueError(
            f"未知的标题筛选状态：{status!r}，必须是 {sorted(ITEM_TITLE_SCREEN_STATUSES)} 之一"
        )
    row = _get_item_row(db, item_id)
    row.title_screen_status = status
    row.title_relevance_score = relevance_score
    row.title_screen_reason = reason
    db.flush()
    return _item_dto(row)


def update_report_item_analysis(
    db: Session,
    item_id: int,
    *,
    status: str,
    reason: str | None = None,
    relevance_score: float | None = None,
    novelty_score: float | None = None,
    evidence_strength_score: float | None = None,
    human_relevance_score: float | None = None,
    recommend: bool | None = None,
    priority: str | None = None,
    tags: list | None = None,
    raw: dict | None = None,
    model: str | None = None,
    prompt_id: str | None = None,
) -> ReportItemDTO:
    """记一条条目的 AI 摘要分析结果。`status="failed"` 时其余评分字段允许
    为空——这一条目本身分析失败，不代表它该被排除（失败隔离：旧代码的
    教训是失败项要保留而不是悄悄消失），由 `reason` 存失败原因。"""
    if status not in ITEM_ANALYSIS_STATUSES:
        raise ValueError(
            f"未知的分析状态：{status!r}，必须是 {sorted(ITEM_ANALYSIS_STATUSES)} 之一"
        )
    row = _get_item_row(db, item_id)
    row.analysis_status = status
    row.analysis_reason = reason
    row.relevance_score = relevance_score
    row.novelty_score = novelty_score
    row.evidence_strength_score = evidence_strength_score
    row.human_relevance_score = human_relevance_score
    row.recommend = recommend
    row.priority = priority
    row.tags_json = list(tags) if tags else None
    row.raw_json = dict(raw) if raw else None
    row.model = model
    row.prompt_id = prompt_id
    db.flush()
    return _item_dto(row)


# ── 投递 ─────────────────────────────────────────────────────────────────


def get_or_create_delivery(db: Session, *, report_id: int, connection_id: int) -> DeliveryDTO:
    """取或建一条投递记录。`(report_id, connection_id)` 唯一——重新投递是
    更新这同一行（见 `update_delivery_status`），不是新建一行，这样"已成功
    的目的地跳过不重发"和"原地编辑已发消息"才有稳定的地方落（`message_ids`
    留在同一行上）。"""
    _get_report_row(db, report_id)
    existing = db.scalar(
        select(Delivery).where(
            Delivery.report_id == report_id, Delivery.connection_id == connection_id
        )
    )
    if existing is not None:
        return _delivery_dto(existing)

    row = Delivery(report_id=report_id, connection_id=connection_id, status="pending")
    db.add(row)
    db.flush()
    return _delivery_dto(row)


def get_delivery(db: Session, delivery_id: int) -> DeliveryDTO:
    return _delivery_dto(_get_delivery_row(db, delivery_id))


def list_deliveries_for_report(db: Session, report_id: int) -> list[DeliveryDTO]:
    stmt = (
        select(Delivery)
        .where(Delivery.report_id == report_id)
        .order_by(Delivery.created_at.asc())
    )
    return [_delivery_dto(row) for row in db.scalars(stmt)]


def list_deliveries(
    db: Session,
    *,
    account_id: int,
    intent_id: int | None = _UNSET,
    limit: int = 20,
    offset: int = 0,
) -> list[DeliveryDTO]:
    """按账号（可选再按研究意图）列出投递记录，按创建时间倒序分页——首页
    「各研究意图最新推送」和投递列表页共用这一个查询。`Delivery` 本身不存
    `account_id`/`intent_id`，这里在同一个 domain 内部 join `Report` 取得，
    不算跨 domain 访问。"""
    stmt = (
        select(Delivery)
        .join(Report, Delivery.report_id == Report.id)
        .where(Report.account_id == account_id)
    )
    if intent_id is not _UNSET:
        stmt = stmt.where(Report.intent_id == intent_id)
    stmt = stmt.order_by(Delivery.created_at.desc()).offset(offset).limit(limit)
    return [_delivery_dto(row) for row in db.scalars(stmt)]


def update_delivery_status(
    db: Session,
    delivery_id: int,
    *,
    status: str,
    message_ids: list | None = _UNSET,
    error: str | None = _UNSET,
) -> DeliveryDTO:
    if status not in DELIVERY_STATUSES:
        raise ValueError(f"未知的投递状态：{status!r}，必须是 {sorted(DELIVERY_STATUSES)} 之一")
    row = _get_delivery_row(db, delivery_id)
    row.status = status
    if message_ids is not _UNSET:
        row.message_ids_json = list(message_ids) if message_ids else None
    if error is not _UNSET:
        row.error = error
    db.flush()
    return _delivery_dto(row)
