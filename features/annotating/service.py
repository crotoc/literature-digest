"""features/annotating：单条文献的元数据编辑 + 笔记。

对外表面见 contract.py。
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from domain.notes import WorkNoteDTO, get_note, set_note
from domain.works import (
    DuplicateCandidateDTO,
    WorkDTO,
    find_candidates_by_title_year_key,
    get_work,
    record_duplicate_candidate,
    update_work,
)

_UNSET = object()


@dataclass(frozen=True)
class UpdateMetadataResult:
    work: WorkDTO
    new_duplicate_candidates: tuple[DuplicateCandidateDTO, ...]


def update_metadata(
    db: Session,
    *,
    library_id: int,
    work_id: int,
    item_type: Any = _UNSET,
    title: Any = _UNSET,
    authors: Any = _UNSET,
    year: Any = _UNSET,
    month: Any = _UNSET,
    day: Any = _UNSET,
    container_title: Any = _UNSET,
    volume: Any = _UNSET,
    issue: Any = _UNSET,
    pages: Any = _UNSET,
    publisher: Any = _UNSET,
    abstract: Any = _UNSET,
    language: Any = _UNSET,
    note: Any = _UNSET,
) -> UpdateMetadataResult:
    """编辑元数据；标题或年份变了就重新扫一遍疑似重复候选池。

    `domain.works.update_work` 自己只负责重算 `title_year_key`，**不会**
    自动重跑疑似重复检查——它自己的 docstring 写明了理由：一次库范围扫描
    代价不小，默认在每次编辑都触发容易在用户还没保存完就弹出一堆候选。
    这正是本 feature 存在的理由：由它决定"什么时候该重新扫"并落
    `duplicate_candidate` 行，`domain/works` 本身保持机械、不认识"什么时候
    该扫"这个策略。

    这里的 `note` 参数是 `works.note`——文献表自己的一个短文本字段（比如
    从 RIS 的 N1 标签带进来的"导入备注"），和下面 `get_work_note` /
    `set_work_note` 操作的 `domain/notes`（用户手写的长文本笔记，
    `features/organizing` 合并时拼接的那个）是两个完全不同的东西，不要混。
    """
    before = get_work(db, work_id)

    candidates = {
        "item_type": item_type,
        "title": title,
        "authors": authors,
        "year": year,
        "month": month,
        "day": day,
        "container_title": container_title,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "publisher": publisher,
        "abstract": abstract,
        "language": language,
        "note": note,
    }
    kwargs = {name: value for name, value in candidates.items() if value is not _UNSET}

    updated = update_work(db, work_id, **kwargs)

    title_changed = "title" in kwargs and kwargs["title"] != before.title
    year_changed = "year" in kwargs and kwargs["year"] != before.year

    new_candidates: list[DuplicateCandidateDTO] = []
    if title_changed or year_changed:
        hits = find_candidates_by_title_year_key(
            db,
            library_id=library_id,
            title_year_key=updated.title_year_key,
            exclude_work_id=work_id,
        )
        for hit in hits:
            new_candidates.append(
                record_duplicate_candidate(
                    db,
                    library_id=library_id,
                    work_id=work_id,
                    candidate_work_id=hit.id,
                    reason="title_year_key",
                )
            )

    return UpdateMetadataResult(work=updated, new_duplicate_candidates=tuple(new_candidates))


def get_work_note(db: Session, work_id: int) -> WorkNoteDTO | None:
    """`domain/notes` 的用户长文本笔记——不是 `works.note` 那个短字段。"""
    return get_note(db, work_id)


def set_work_note(db: Session, *, library_id: int, work_id: int, content: str | None) -> WorkNoteDTO | None:
    """`content=None` 删除这条笔记（`domain.notes.set_note` 自己的语义）。"""
    return set_note(db, library_id=library_id, work_id=work_id, content=content)
