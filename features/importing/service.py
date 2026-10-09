"""features/importing 的业务逻辑：把一段 RIS/BibTeX/CSL-JSON 文本导入到某个文库。

组合 `caps/bibformats`（解析）+ `domain/works`（标识符命中合并 / title_year_key
疑似重复 / 建新条目）+ `domain/libraries`（访问校验）+ `domain/jobs`（长流程批量，
每条记录一个子 job，失败隔离——见计划「两处已定」第 2 条的子 job 粒度规则）。

本模块不开表。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from caps.bibformats import BibFormatsError, Record, parse
from domain.jobs import JobDTO, create_job, transition, update_counts
from domain.libraries import resolve_scope
from domain.works import (
    add_identifier,
    create_work,
    find_by_identifier,
    find_candidates_by_title_year_key,
    record_duplicate_candidate,
    update_work,
)
from infra.errors import AppError

JOB_KIND = "import"
JOB_KIND_ITEM = "import_item"

# importing 是同步瞬时的单条操作（解析+落库都在一次函数调用里完成，没有
# "等待外部资源"的中间态），所以子 job 的转移表只有 queued 直接到终态两条
# 边，不需要一个单独的 running 态——这和 E5 fulltext 的多步状态机不是一回事。
_PARENT_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}
_CHILD_TRANSITIONS = {"queued": {"succeeded", "failed"}}

# 按识别力由强到弱的顺序尝试标识符命中合并；url 故意不在这个列表里——同一篇
# 文章常有多个不同的 url（出版商页/机构代理/预印本镜像），拿 url 做身份判定
# 风险太高，见 README「为什么不用 url 做合并依据」。
IDENTIFIER_SCHEMES_FOR_MATCHING = ("doi", "pmid", "isbn", "issn")

# 合并时只填充 existing 里还没有值的字段——「标识符命中自动合并（不覆盖已有
# 字段）」是计划里明确定的规则，这个元组就是"已有字段"检查会过一遍的字段集合。
_MERGE_FIELDS = (
    "title",
    "authors",
    "year",
    "month",
    "day",
    "container_title",
    "volume",
    "issue",
    "pages",
    "publisher",
    "abstract",
    "language",
    "note",
)

OUTCOME_STATUSES = frozenset({"created", "merged", "flagged_duplicate", "failed"})


class ImportParseFailed(AppError):
    """整段输入文本解析失败——这不是某一条记录的问题，是格式名或整份文本本身
    不对，所以在建任何 job 之前就报错：建了 job 却不知道总共有多少条、没法
    报进度，不如直接拒绝。"""

    status_code = 422
    code = "import_parse_failed"


@dataclass(frozen=True)
class ImportItemOutcome:
    work_id: int | None
    status: str
    reason: str | None = None
    duplicate_candidate_ids: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ImportResult:
    job: JobDTO
    outcomes: tuple[ImportItemOutcome, ...]


def _is_empty(value) -> bool:
    return value is None or value == ()


def _fill_missing_fields(db, *, existing, record: Record):
    """只把 `existing` 里还是空的字段，从 `record` 对应字段补上；`existing`
    已经有值的字段一律不碰——这就是"合并不覆盖已有字段"。"""
    updates = {}
    for name in _MERGE_FIELDS:
        current = getattr(existing, name)
        incoming = getattr(record, name)
        if _is_empty(current) and not _is_empty(incoming):
            updates[name] = incoming
    if not updates:
        return existing
    return update_work(db, existing.id, **updates)


def _attach_identifiers(db, *, library_id: int, work_id: int, record: Record) -> None:
    """把 `record` 身上带的每一种标识符都挂到 `work_id` 上——包括触发合并的
    那一种（`add_identifier` 对同一条记录是幂等的，重复挂不会报错）。这样一次
    导入就能把一条记录身上所有已知的标识符都沉淀到画布上的同一个 work，方便
    以后别的格式/来源用任何一种标识符都能命中它。"""
    for scheme in IDENTIFIER_SCHEMES_FOR_MATCHING:
        value = getattr(record, scheme)
        if value:
            add_identifier(db, library_id=library_id, work_id=work_id, scheme=scheme, value=value)


def _import_one(db, *, library_id: int, record: Record) -> ImportItemOutcome:
    for scheme in IDENTIFIER_SCHEMES_FOR_MATCHING:
        value = getattr(record, scheme)
        if not value:
            continue
        existing = find_by_identifier(db, library_id=library_id, scheme=scheme, value=value)
        if existing is not None:
            merged = _fill_missing_fields(db, existing=existing, record=record)
            # 标识符命中合并时也要把 record 身上其它标识符补全到这同一条
            # work 上——可能出现"这条记录的 pmid 和已有 work 冲突"（两条已有
            # work 各占了一个不同的标识符），这种真实冲突交给外层当作这一条
            # 失败处理，不在这里吞掉。
            _attach_identifiers(db, library_id=library_id, work_id=merged.id, record=record)
            return ImportItemOutcome(work_id=merged.id, status="merged")

    work = create_work(
        db,
        library_id=library_id,
        item_type=record.item_type,
        title=record.title,
        authors=record.authors,
        year=record.year,
        month=record.month,
        day=record.day,
        container_title=record.container_title,
        volume=record.volume,
        issue=record.issue,
        pages=record.pages,
        publisher=record.publisher,
        abstract=record.abstract,
        language=record.language,
        note=record.note,
    )
    _attach_identifiers(db, library_id=library_id, work_id=work.id, record=record)

    if work.title_year_key is not None:
        candidates = find_candidates_by_title_year_key(
            db, library_id=library_id, title_year_key=work.title_year_key, exclude_work_id=work.id
        )
        if candidates:
            dup_ids = tuple(
                record_duplicate_candidate(
                    db,
                    library_id=library_id,
                    work_id=work.id,
                    candidate_work_id=candidate.id,
                    reason="title_year_key_match",
                ).id
                for candidate in candidates
            )
            return ImportItemOutcome(
                work_id=work.id, status="flagged_duplicate", duplicate_candidate_ids=dup_ids
            )

    return ImportItemOutcome(work_id=work.id, status="created")


def import_records(db, *, account_id: int, library_id: int, format: str, raw_text: str) -> ImportResult:
    """解析 `raw_text`，把每条记录分别导入 `library_id`。

    每条记录一个子 job（计划里「长流程批量」的粒度规则——导入和全文下载/AI
    评估同类，每项都值得单独看、单独重试），父 job 用 `counts_json` 汇总
    created/merged/flagged_duplicate/failed 四类计数。单条记录处理失败不影响
    其它记录（失败隔离），父 job 最终状态是 `failed` 当且仅当至少一条子项
    失败。
    """
    resolve_scope(db, account_id=account_id, library_id=library_id)

    try:
        records = parse(format, raw_text)
    except BibFormatsError as exc:
        raise ImportParseFailed(f"解析 {format!r} 文本失败：{exc}") from exc

    counts = {"total": len(records), "created": 0, "merged": 0, "flagged_duplicate": 0, "failed": 0}
    parent = create_job(db, account_id=account_id, kind=JOB_KIND, library_id=library_id, counts=counts)
    transition(db, parent.id, "running", allowed=_PARENT_TRANSITIONS)

    outcomes = []
    for record in records:
        child = create_job(
            db, account_id=account_id, kind=JOB_KIND_ITEM, library_id=library_id, parent_job_id=parent.id
        )
        try:
            outcome = _import_one(db, library_id=library_id, record=record)
        except Exception as exc:  # noqa: BLE001 — 失败隔离：任何一条记录出错都不能打断整批导入
            outcome = ImportItemOutcome(work_id=None, status="failed", reason=str(exc))
            transition(db, child.id, "failed", reason=str(exc), allowed=_CHILD_TRANSITIONS)
        else:
            transition(db, child.id, "succeeded", allowed=_CHILD_TRANSITIONS)
        counts[outcome.status] += 1
        outcomes.append(outcome)
        update_counts(db, parent.id, counts)

    final_status = "succeeded" if counts["failed"] == 0 else "failed"
    final_reason = None if counts["failed"] == 0 else f"{counts['failed']} 条导入失败，详见各子 job"
    parent = transition(db, parent.id, final_status, reason=final_reason, allowed=_PARENT_TRANSITIONS)

    return ImportResult(job=parent, outcomes=tuple(outcomes))
