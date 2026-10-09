"""domain/notes 的业务逻辑：每篇文献最多一条自由文本笔记。

本模块不认识 `domain.works.Work`（跨 domain，见 models.py 的说明）——涉及
work 的函数只收发裸 `work_id: int`。

没有定义任何"找不到"异常——"这篇文献还没有笔记"是最常见的正常状态（绝大多数
文献永远不会有人写笔记），强迫调用方 try/except 一个几乎总会发生的"错误"
很别扭，所以 `get_note` 直接返回 `None` 表示"还没写"，不是异常路径。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.notes.models import WorkNote


@dataclass(frozen=True)
class WorkNoteDTO:
    id: int
    library_id: int
    work_id: int
    content: str
    created_at: datetime
    updated_at: datetime


def _note_dto(row: WorkNote) -> WorkNoteDTO:
    return WorkNoteDTO(
        id=row.id,
        library_id=row.library_id,
        work_id=row.work_id,
        content=row.content,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def get_note(db: Session, work_id: int) -> WorkNoteDTO | None:
    row = db.scalar(select(WorkNote).where(WorkNote.work_id == work_id))
    return _note_dto(row) if row is not None else None


def set_note(db: Session, *, library_id: int, work_id: int, content: str | None) -> WorkNoteDTO | None:
    """写入/更新一篇文献的笔记。`content` 是 `None` 或空字符串时视为"清空
    笔记"——直接删掉这一行而不是存一条空字符串，避免库里堆满从没真正写过
    内容的空行；此时返回 `None`，和"从来没写过笔记"是同一个外部可观察状态，
    调用方不需要区分"从未写过"和"写了又清空"。
    """
    row = db.scalar(select(WorkNote).where(WorkNote.work_id == work_id))

    if not content:
        if row is not None:
            db.delete(row)
            db.flush()
        return None

    if row is None:
        row = WorkNote(library_id=library_id, work_id=work_id, content=content)
        db.add(row)
    else:
        row.content = content
    db.flush()
    return _note_dto(row)


def list_notes_for_works(db: Session, work_ids: list[int]) -> dict[int, WorkNoteDTO]:
    """批量取一组文献各自的笔记（没有笔记的 work_id 不会出现在返回的字典
    里）。给卡片列表视图用——渲染一屏几十张卡片的笔记预览时避免逐条
    `get_note` 的 N+1 查询。
    """
    if not work_ids:
        return {}
    rows = db.scalars(select(WorkNote).where(WorkNote.work_id.in_(work_ids)))
    return {row.work_id: _note_dto(row) for row in rows}
