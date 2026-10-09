"""domain/works 的业务逻辑：文献条目的 CRUD + 软删回收站、标识符管理、
去重指纹与疑似重复候选、文献间关系、来源溯源。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。

查询默认排除软删项（`deleted_at IS NOT NULL`）——这是「两处已定」里定下的
安全方向：默认看不到已删的，想看回收站必须显式传 `include_deleted=True`。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from caps.bibformats import ITEM_TYPES, Person
from domain.works.fingerprint import compute_title_year_key
from domain.works.models import (
    DuplicateCandidate,
    Work,
    WorkIdentifier,
    WorkProvenance,
    WorkRelation,
    _utcnow,
)
from infra.errors import AppError, NotFound

CANDIDATE_STATUSES = frozenset({"pending", "confirmed", "dismissed"})
_UNSET = object()


def _normalize_identifier_value(scheme: str, value: str) -> str:
    """同一个标识符的不同写法要落到同一个 `value_norm` 才能互相命中。

    只做最常见的归一化：去首尾空白、转小写；DOI 额外剥掉 `https://doi.org/`
    或 `doi:` 前缀；ISBN/ISSN 额外去掉中间的 `-`/空格。没有做更彻底的校验
    （比如真的验证 DOI 的字符集、ISBN 的校验位）——见 README 裁剪范围。
    """
    normalized = value.strip().lower()
    if scheme == "doi":
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break
    elif scheme in ("isbn", "issn"):
        normalized = normalized.replace("-", "").replace(" ", "")
    return normalized


# ── 异常 ─────────────────────────────────────────────────────────────────


class WorkNotFound(NotFound):
    code = "work_not_found"


class IdentifierConflict(AppError):
    """这个标识符已经挂在**另一条、未被软删**的记录上。

    携带 `existing_work_id`，方便调用方（`features/importing`）决定要不要
    直接合并进那条已有记录，而不是重复创建。
    """

    status_code = 409
    code = "identifier_conflict"

    def __init__(self, message: str, *, existing_work_id: int) -> None:
        super().__init__(message)
        self.existing_work_id = existing_work_id


class IdentifierBelongsToDeletedWork(AppError):
    """这个标识符挂在一条**已被软删**的记录上。

    语义是"恢复它"而不是"冲突"（见「两处已定」）——但恢复不恢复是调用方的
    决定，本模块只负责把这个事实亮出来，携带 `deleted_work_id`。
    """

    status_code = 409
    code = "identifier_belongs_to_deleted_work"

    def __init__(self, message: str, *, deleted_work_id: int) -> None:
        super().__init__(message)
        self.deleted_work_id = deleted_work_id


class IdentifierNotFound(NotFound):
    code = "identifier_not_found"


class RelationNotFound(NotFound):
    code = "relation_not_found"


class DuplicateCandidateNotFound(NotFound):
    code = "duplicate_candidate_not_found"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkDTO:
    id: int
    library_id: int
    item_type: str
    title: str | None
    authors: tuple[Person, ...]
    year: int | None
    month: int | None
    day: int | None
    container_title: str | None
    volume: str | None
    issue: str | None
    pages: str | None
    publisher: str | None
    abstract: str | None
    language: str | None
    note: str | None
    title_year_key: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class IdentifierDTO:
    id: int
    work_id: int
    library_id: int
    scheme: str
    value: str
    value_norm: str
    created_at: datetime


@dataclass(frozen=True)
class RelationDTO:
    id: int
    library_id: int
    from_work_id: int
    to_work_id: int
    relation_type: str
    created_at: datetime


@dataclass(frozen=True)
class ProvenanceDTO:
    id: int
    work_id: int
    source: str
    source_id: str | None
    payload: dict | None
    fetched_at: datetime


@dataclass(frozen=True)
class DuplicateCandidateDTO:
    id: int
    library_id: int
    work_id: int
    candidate_work_id: int
    reason: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


def _person_to_dict(person: Person) -> dict:
    return {"family": person.family, "given": person.given, "literal": person.literal}


def _person_from_dict(raw: dict) -> Person:
    return Person(family=raw.get("family"), given=raw.get("given"), literal=raw.get("literal"))


def _work_dto(row: Work) -> WorkDTO:
    return WorkDTO(
        id=row.id,
        library_id=row.library_id,
        item_type=row.item_type,
        title=row.title,
        authors=tuple(_person_from_dict(a) for a in row.authors_json),
        year=row.year,
        month=row.month,
        day=row.day,
        container_title=row.container_title,
        volume=row.volume,
        issue=row.issue,
        pages=row.pages,
        publisher=row.publisher,
        abstract=row.abstract,
        language=row.language,
        note=row.note,
        title_year_key=row.title_year_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _identifier_dto(row: WorkIdentifier) -> IdentifierDTO:
    return IdentifierDTO(
        id=row.id,
        work_id=row.work_id,
        library_id=row.library_id,
        scheme=row.scheme,
        value=row.value,
        value_norm=row.value_norm,
        created_at=row.created_at,
    )


def _relation_dto(row: WorkRelation) -> RelationDTO:
    return RelationDTO(
        id=row.id,
        library_id=row.library_id,
        from_work_id=row.from_work_id,
        to_work_id=row.to_work_id,
        relation_type=row.relation_type,
        created_at=row.created_at,
    )


def _provenance_dto(row: WorkProvenance) -> ProvenanceDTO:
    return ProvenanceDTO(
        id=row.id,
        work_id=row.work_id,
        source=row.source,
        source_id=row.source_id,
        payload=row.payload_json,
        fetched_at=row.fetched_at,
    )


def _duplicate_candidate_dto(row: DuplicateCandidate) -> DuplicateCandidateDTO:
    return DuplicateCandidateDTO(
        id=row.id,
        library_id=row.library_id,
        work_id=row.work_id,
        candidate_work_id=row.candidate_work_id,
        reason=row.reason,
        status=row.status,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


def _compute_title_year_key_or_none(title: str | None, year: int | None) -> str | None:
    try:
        return compute_title_year_key(title, year)
    except ValueError:
        return None


# ── 文献条目 ───────────────────────────────────────────────────────────────


def create_work(
    db: Session,
    *,
    library_id: int,
    item_type: str = "journal_article",
    title: str | None = None,
    authors: Sequence[Person] = (),
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    container_title: str | None = None,
    volume: str | None = None,
    issue: str | None = None,
    pages: str | None = None,
    publisher: str | None = None,
    abstract: str | None = None,
    language: str | None = None,
    note: str | None = None,
) -> WorkDTO:
    if item_type not in ITEM_TYPES:
        raise ValueError(f"item_type 必须是 {sorted(ITEM_TYPES)} 之一，收到 {item_type!r}")

    row = Work(
        library_id=library_id,
        item_type=item_type,
        title=title,
        authors_json=[_person_to_dict(a) for a in authors],
        year=year,
        month=month,
        day=day,
        container_title=container_title,
        volume=volume,
        issue=issue,
        pages=pages,
        publisher=publisher,
        abstract=abstract,
        language=language,
        note=note,
        title_year_key=_compute_title_year_key_or_none(title, year),
    )
    db.add(row)
    db.flush()
    return _work_dto(row)


def _get_work_row(db: Session, work_id: int, *, include_deleted: bool) -> Work | None:
    row = db.get(Work, work_id)
    if row is None:
        return None
    if row.deleted_at is not None and not include_deleted:
        return None
    return row


def get_work(db: Session, work_id: int, *, include_deleted: bool = False) -> WorkDTO:
    row = _get_work_row(db, work_id, include_deleted=include_deleted)
    if row is None:
        raise WorkNotFound(f"文献不存在：{work_id}")
    return _work_dto(row)


def update_work(
    db: Session,
    work_id: int,
    *,
    item_type: str = _UNSET,
    title: str | None = _UNSET,
    authors: Sequence[Person] = _UNSET,
    year: int | None = _UNSET,
    month: int | None = _UNSET,
    day: int | None = _UNSET,
    container_title: str | None = _UNSET,
    volume: str | None = _UNSET,
    issue: str | None = _UNSET,
    pages: str | None = _UNSET,
    publisher: str | None = _UNSET,
    abstract: str | None = _UNSET,
    language: str | None = _UNSET,
    note: str | None = _UNSET,
) -> WorkDTO:
    """只更新显式传入的字段（用 `_UNSET` 哨兵区分"没传"和"传了 None"，和
    `caps/bibformats` 修过的那个 `or`-fallback bug 是同一类问题的提前预防）。

    `title` 或 `year` 变了就重算 `title_year_key`；**不会**自动重跑疑似
    重复检查——那是一次库范围的扫描，默认在每次编辑时都触发代价太大、也
    太容易在用户还没保存完就弹出一堆候选，v1 把"要不要重新扫描"交给调用方
    显式调 `find_candidates_by_title_year_key`（见 README）。
    """
    row = _get_work_row(db, work_id, include_deleted=False)
    if row is None:
        raise WorkNotFound(f"文献不存在：{work_id}")

    if item_type is not _UNSET:
        if item_type not in ITEM_TYPES:
            raise ValueError(f"item_type 必须是 {sorted(ITEM_TYPES)} 之一，收到 {item_type!r}")
        row.item_type = item_type
    if title is not _UNSET:
        row.title = title
    if authors is not _UNSET:
        row.authors_json = [_person_to_dict(a) for a in authors]
    if year is not _UNSET:
        row.year = year
    if month is not _UNSET:
        row.month = month
    if day is not _UNSET:
        row.day = day
    if container_title is not _UNSET:
        row.container_title = container_title
    if volume is not _UNSET:
        row.volume = volume
    if issue is not _UNSET:
        row.issue = issue
    if pages is not _UNSET:
        row.pages = pages
    if publisher is not _UNSET:
        row.publisher = publisher
    if abstract is not _UNSET:
        row.abstract = abstract
    if language is not _UNSET:
        row.language = language
    if note is not _UNSET:
        row.note = note

    if title is not _UNSET or year is not _UNSET:
        row.title_year_key = _compute_title_year_key_or_none(row.title, row.year)

    db.flush()
    return _work_dto(row)


def list_works(
    db: Session,
    *,
    library_id: int,
    include_deleted: bool = False,
    limit: int | None = None,
    offset: int | None = None,
) -> list[WorkDTO]:
    """v1 固定按 `updated_at desc` 排序（见页面梳理⑨「排序控制」）。更丰富的
    排序键白名单（年份/标题/第一作者）是 `features/library_browse` 要加的
    下一层，不在本模块——这里先给一个能跑的默认序，不预先猜全部排序选项。
    """
    stmt = select(Work).where(Work.library_id == library_id)
    if not include_deleted:
        stmt = stmt.where(Work.deleted_at.is_(None))
    stmt = stmt.order_by(Work.updated_at.desc())
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return [_work_dto(row) for row in db.scalars(stmt)]


def soft_delete_work(db: Session, work_id: int) -> None:
    """移入回收站。幂等——已经软删过的再调一次不报错。不动任何关联表
    （tags/folders/notes/attachments 不是本模块的知识，恢复时要原样回来）。
    """
    row = db.get(Work, work_id)
    if row is None:
        raise WorkNotFound(f"文献不存在：{work_id}")
    if row.deleted_at is None:
        row.deleted_at = _utcnow()
        db.flush()


def restore_work(db: Session, work_id: int) -> WorkDTO:
    """从回收站恢复。不需要重新处理标识符唯一性——一条记录被软删期间，它的
    `work_identifiers` 行并不会被删除或释放（`add_identifier` 命中已删记录
    的标识符时会报 `IdentifierBelongsToDeletedWork` 而不是让别的记录抢占），
    所以恢复时这些标识符仍然唯一地属于它，不存在撞车。见 README。
    """
    row = db.get(Work, work_id)
    if row is None:
        raise WorkNotFound(f"文献不存在：{work_id}")
    row.deleted_at = None
    db.flush()
    return _work_dto(row)


def purge_work(db: Session, work_id: int) -> None:
    """彻底删除。只清本模块自己的四张附属表（identifiers/relations 双向/
    provenance/duplicate_candidates 双向）+ works 行本身。**不**级联
    tags/folders/notes/attachments——那些是别的 domain 的表，本模块没有
    权限也没有知识去动它们；跨 domain 的级联顺序由
    `features/organizing`（"两处已定"里"彻底删除"那条）编排，它会依次调
    各个 domain 自己的 purge 函数。
    """
    for row in db.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == work_id)):
        db.delete(row)
    for row in db.scalars(
        select(WorkRelation).where(
            (WorkRelation.from_work_id == work_id) | (WorkRelation.to_work_id == work_id)
        )
    ):
        db.delete(row)
    for row in db.scalars(select(WorkProvenance).where(WorkProvenance.work_id == work_id)):
        db.delete(row)
    for row in db.scalars(
        select(DuplicateCandidate).where(
            (DuplicateCandidate.work_id == work_id) | (DuplicateCandidate.candidate_work_id == work_id)
        )
    ):
        db.delete(row)

    work_row = db.get(Work, work_id)
    if work_row is not None:
        db.delete(work_row)
    db.flush()


# ── 标识符 ─────────────────────────────────────────────────────────────────


def add_identifier(db: Session, *, library_id: int, work_id: int, scheme: str, value: str) -> IdentifierDTO:
    """挂一个标识符（doi/pmid/isbn/issn/url 等，scheme 不在本模块做白名单
    校验——新标识符类型不需要改这里，见 README）。

    同一个 `(library_id, scheme, value_norm)` 已经被**另一条**记录占用时：
    那条记录没被软删 → `IdentifierConflict`；被软删了 → 报
    `IdentifierBelongsToDeletedWork`。已经挂在**同一条**记录上则直接
    幂等返回已有行，不重复插入、不报错。
    """
    value_norm = _normalize_identifier_value(scheme, value)
    existing = db.scalar(
        select(WorkIdentifier).where(
            WorkIdentifier.library_id == library_id,
            WorkIdentifier.scheme == scheme,
            WorkIdentifier.value_norm == value_norm,
        )
    )
    if existing is not None:
        if existing.work_id == work_id:
            return _identifier_dto(existing)
        other_work = db.get(Work, existing.work_id)
        if other_work is not None and other_work.deleted_at is not None:
            raise IdentifierBelongsToDeletedWork(
                f"标识符 {scheme}:{value} 属于已被软删的文献 {existing.work_id}",
                deleted_work_id=existing.work_id,
            )
        raise IdentifierConflict(
            f"标识符 {scheme}:{value} 已经属于文献 {existing.work_id}",
            existing_work_id=existing.work_id,
        )

    row = WorkIdentifier(
        library_id=library_id, work_id=work_id, scheme=scheme, value=value, value_norm=value_norm
    )
    db.add(row)
    db.flush()
    return _identifier_dto(row)


def find_by_identifier(
    db: Session, *, library_id: int, scheme: str, value: str, include_deleted: bool = False
) -> WorkDTO | None:
    value_norm = _normalize_identifier_value(scheme, value)
    identifier = db.scalar(
        select(WorkIdentifier).where(
            WorkIdentifier.library_id == library_id,
            WorkIdentifier.scheme == scheme,
            WorkIdentifier.value_norm == value_norm,
        )
    )
    if identifier is None:
        return None
    row = _get_work_row(db, identifier.work_id, include_deleted=include_deleted)
    return _work_dto(row) if row is not None else None


def list_identifiers(db: Session, work_id: int) -> list[IdentifierDTO]:
    rows = db.scalars(
        select(WorkIdentifier).where(WorkIdentifier.work_id == work_id).order_by(WorkIdentifier.created_at)
    )
    return [_identifier_dto(row) for row in rows]


def remove_identifier(db: Session, identifier_id: int) -> None:
    row = db.get(WorkIdentifier, identifier_id)
    if row is None:
        raise IdentifierNotFound(f"标识符不存在：{identifier_id}")
    db.delete(row)
    db.flush()


# ── 去重指纹 ───────────────────────────────────────────────────────────────


def find_candidates_by_title_year_key(
    db: Session,
    *,
    library_id: int,
    title_year_key: str,
    exclude_work_id: int | None = None,
    include_deleted: bool = False,
) -> list[WorkDTO]:
    """同一个库里 `title_year_key` 相同的其它记录——疑似重复的候选池。

    这是**粗筛**，不是判定：命中只说明"标题+年份规范化后一样"，调用方
    （`features/importing` / `dedupe_review`）decides 要不要落一条
    `duplicate_candidate` 给人工确认，本函数不自动创建候选、不自动合并。
    """
    stmt = select(Work).where(Work.library_id == library_id, Work.title_year_key == title_year_key)
    if exclude_work_id is not None:
        stmt = stmt.where(Work.id != exclude_work_id)
    if not include_deleted:
        stmt = stmt.where(Work.deleted_at.is_(None))
    return [_work_dto(row) for row in db.scalars(stmt)]


def record_duplicate_candidate(
    db: Session, *, library_id: int, work_id: int, candidate_work_id: int, reason: str
) -> DuplicateCandidateDTO:
    """落一条疑似重复候选，等人工在 `dedupe_review` 里确认或驳回。

    同一对 `(work_id, candidate_work_id)` 已经记过（不管什么状态）就幂等
    返回那一行，不重复插入——否则每次重新导入同一批文献都会堆出新的一行。
    """
    existing = db.scalar(
        select(DuplicateCandidate).where(
            DuplicateCandidate.work_id == work_id,
            DuplicateCandidate.candidate_work_id == candidate_work_id,
        )
    )
    if existing is not None:
        return _duplicate_candidate_dto(existing)

    row = DuplicateCandidate(
        library_id=library_id, work_id=work_id, candidate_work_id=candidate_work_id, reason=reason
    )
    db.add(row)
    db.flush()
    return _duplicate_candidate_dto(row)


def list_duplicate_candidates(
    db: Session, *, library_id: int, status: str | None = None
) -> list[DuplicateCandidateDTO]:
    stmt = select(DuplicateCandidate).where(DuplicateCandidate.library_id == library_id)
    if status is not None:
        stmt = stmt.where(DuplicateCandidate.status == status)
    stmt = stmt.order_by(DuplicateCandidate.created_at)
    return [_duplicate_candidate_dto(row) for row in db.scalars(stmt)]


def resolve_duplicate_candidate(db: Session, candidate_id: int, *, status: str) -> DuplicateCandidateDTO:
    if status not in ("confirmed", "dismissed"):
        raise ValueError(f'status 必须是 "confirmed" 或 "dismissed"，收到 {status!r}')
    row = db.get(DuplicateCandidate, candidate_id)
    if row is None:
        raise DuplicateCandidateNotFound(f"候选不存在：{candidate_id}")

    row.status = status
    row.resolved_at = _utcnow()
    db.flush()
    return _duplicate_candidate_dto(row)


# ── 文献间关系 ─────────────────────────────────────────────────────────────


def add_relation(
    db: Session, *, library_id: int, from_work_id: int, to_work_id: int, relation_type: str
) -> RelationDTO:
    """两条文献之间的一条有向边。`relation_type` 的词表本模块不做限定（比如
    "preprint_of" / "confirmed_not_duplicate"）——具体用什么词是调用方
    （`dedupe_review` 等）的业务知识，v1 不在这里预先穷举。
    """
    if from_work_id == to_work_id:
        raise ValueError("from_work_id 和 to_work_id 不能是同一条记录")

    existing = db.scalar(
        select(WorkRelation).where(
            WorkRelation.from_work_id == from_work_id,
            WorkRelation.to_work_id == to_work_id,
            WorkRelation.relation_type == relation_type,
        )
    )
    if existing is not None:
        return _relation_dto(existing)

    row = WorkRelation(
        library_id=library_id, from_work_id=from_work_id, to_work_id=to_work_id, relation_type=relation_type
    )
    db.add(row)
    db.flush()
    return _relation_dto(row)


def list_relations(db: Session, work_id: int) -> list[RelationDTO]:
    """一条记录参与的所有关系，不管它是边的起点还是终点。"""
    rows = db.scalars(
        select(WorkRelation)
        .where((WorkRelation.from_work_id == work_id) | (WorkRelation.to_work_id == work_id))
        .order_by(WorkRelation.created_at)
    )
    return [_relation_dto(row) for row in rows]


def remove_relation(db: Session, relation_id: int) -> None:
    row = db.get(WorkRelation, relation_id)
    if row is None:
        raise RelationNotFound(f"关系不存在：{relation_id}")
    db.delete(row)
    db.flush()


# ── 来源溯源 ───────────────────────────────────────────────────────────────


def record_provenance(
    db: Session, *, work_id: int, source: str, source_id: str | None = None, payload: dict | None = None
) -> ProvenanceDTO:
    """记一条"这条数据是从哪来的"日志行。只追加，不更新、不去重——同一个
    来源多次回抓（比如 `metadata_lookup` 定期刷新）就是多条独立的历史记录，
    这正是溯源表存在的意义（想知道"上次是什么时候、从哪抓的"）。
    """
    row = WorkProvenance(work_id=work_id, source=source, source_id=source_id, payload_json=payload)
    db.add(row)
    db.flush()
    return _provenance_dto(row)


def list_provenance(db: Session, work_id: int) -> list[ProvenanceDTO]:
    rows = db.scalars(
        select(WorkProvenance)
        .where(WorkProvenance.work_id == work_id)
        .order_by(WorkProvenance.fetched_at.desc())
    )
    return [_provenance_dto(row) for row in rows]
