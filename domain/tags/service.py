"""domain/tags 的业务逻辑：库内扁平标签池 + work↔tag 多对多关联 + 批量操作
用的三态聚合。

本模块不认识 `domain.works.Work`（跨 domain，见 models.py 的说明）——涉及
work 的函数只收发裸 `work_id: int`。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.tags.models import Tag, WorkTag
from infra.errors import AppError, NotFound

TAG_STATES = frozenset({"all", "some", "none"})


# ── 异常 ─────────────────────────────────────────────────────────────────


class TagNotFound(NotFound):
    code = "tag_not_found"


class TagNameTaken(AppError):
    status_code = 409
    code = "tag_name_taken"


class WorkTagNotFound(NotFound):
    code = "work_tag_not_found"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TagDTO:
    id: int
    library_id: int
    name: str
    sort_order: int
    created_at: datetime


@dataclass(frozen=True)
class WorkTagDTO:
    id: int
    library_id: int
    work_id: int
    tag_id: int
    created_at: datetime


@dataclass(frozen=True)
class TagStateDTO:
    tag: TagDTO
    state: str  # "all" | "some" | "none"


def _tag_dto(row: Tag) -> TagDTO:
    return TagDTO(
        id=row.id,
        library_id=row.library_id,
        name=row.name,
        sort_order=row.sort_order,
        created_at=row.created_at,
    )


def _work_tag_dto(row: WorkTag) -> WorkTagDTO:
    return WorkTagDTO(
        id=row.id,
        library_id=row.library_id,
        work_id=row.work_id,
        tag_id=row.tag_id,
        created_at=row.created_at,
    )


def _get_tag_row(db: Session, tag_id: int) -> Tag:
    row = db.get(Tag, tag_id)
    if row is None:
        raise TagNotFound(f"标签不存在：{tag_id}")
    return row


# ── 标签 ─────────────────────────────────────────────────────────────────


def create_tag(db: Session, *, library_id: int, name: str) -> TagDTO:
    """新建一个标签。同名（库内，大小写敏感）重复创建是幂等的，直接返回
    已有那个标签——这是"顺手新建标签再打上"这个 UI 流程需要的行为：用户不需
    要先查一遍标签池是否已经存在同名标签。新标签追加到排序末尾。
    """
    name = name.strip()
    if not name:
        raise ValueError("标签名不能为空")

    existing = db.scalar(select(Tag).where(Tag.library_id == library_id, Tag.name == name))
    if existing is not None:
        return _tag_dto(existing)

    max_sort_order = db.scalar(
        select(Tag.sort_order).where(Tag.library_id == library_id).order_by(Tag.sort_order.desc())
    )
    next_sort_order = 0 if max_sort_order is None else max_sort_order + 1

    row = Tag(library_id=library_id, name=name, sort_order=next_sort_order)
    db.add(row)
    db.flush()
    return _tag_dto(row)


def get_tag(db: Session, tag_id: int) -> TagDTO:
    return _tag_dto(_get_tag_row(db, tag_id))


def rename_tag(db: Session, tag_id: int, name: str) -> TagDTO:
    name = name.strip()
    if not name:
        raise ValueError("标签名不能为空")

    row = _get_tag_row(db, tag_id)
    if name != row.name:
        collision = db.scalar(select(Tag).where(Tag.library_id == row.library_id, Tag.name == name))
        if collision is not None:
            raise TagNameTaken(f"标签名已被占用：{name!r}")
        row.name = name
        db.flush()
    return _tag_dto(row)


def delete_tag(db: Session, tag_id: int) -> None:
    """删除一个标签——连同它在 `work_tags` 里的全部关联一起删，不碰任何
    work 本身。
    """
    row = _get_tag_row(db, tag_id)
    for link in db.scalars(select(WorkTag).where(WorkTag.tag_id == tag_id)):
        db.delete(link)
    db.delete(row)
    db.flush()


def list_tags(db: Session, *, library_id: int) -> list[TagDTO]:
    rows = db.scalars(select(Tag).where(Tag.library_id == library_id).order_by(Tag.sort_order))
    return [_tag_dto(row) for row in rows]


def reorder_tags(db: Session, *, library_id: int, tag_ids_in_order: list[int]) -> list[TagDTO]:
    """侧栏拖拽排序。必须传入该库**全部**标签的 id（顺序即新的排序），不支
    持只重排一个子集——否则没传的那些标签该插在哪里是没有定义的，宁可拒绝
    也不要猜。
    """
    existing_ids = set(db.scalars(select(Tag.id).where(Tag.library_id == library_id)))
    if set(tag_ids_in_order) != existing_ids:
        raise ValueError("tag_ids_in_order 必须正好是该库全部标签的 id，不能多也不能少")

    for index, tag_id in enumerate(tag_ids_in_order):
        row = db.get(Tag, tag_id)
        row.sort_order = index
    db.flush()
    return list_tags(db, library_id=library_id)


# ── work ↔ tag 关联 ──────────────────────────────────────────────────────


def add_tag_to_work(db: Session, *, library_id: int, work_id: int, tag_id: int) -> WorkTagDTO:
    """给一篇文献打标签。同一对 `(work_id, tag_id)` 重复打是幂等的。"""
    tag = _get_tag_row(db, tag_id)
    if tag.library_id != library_id:
        raise ValueError("tag_id 不属于这个库")

    existing = db.scalar(select(WorkTag).where(WorkTag.work_id == work_id, WorkTag.tag_id == tag_id))
    if existing is not None:
        return _work_tag_dto(existing)

    row = WorkTag(library_id=library_id, work_id=work_id, tag_id=tag_id)
    db.add(row)
    db.flush()
    return _work_tag_dto(row)


def remove_tag_from_work(db: Session, *, work_id: int, tag_id: int) -> None:
    row = db.scalar(select(WorkTag).where(WorkTag.work_id == work_id, WorkTag.tag_id == tag_id))
    if row is None:
        raise WorkTagNotFound(f"文献 {work_id} 没有打标签 {tag_id}")
    db.delete(row)
    db.flush()


def list_tags_for_work(db: Session, work_id: int) -> list[TagDTO]:
    tag_ids = db.scalars(select(WorkTag.tag_id).where(WorkTag.work_id == work_id)).all()
    if not tag_ids:
        return []
    rows = db.scalars(select(Tag).where(Tag.id.in_(tag_ids)).order_by(Tag.sort_order))
    return [_tag_dto(row) for row in rows]


def list_work_ids_for_tag(db: Session, tag_id: int) -> list[int]:
    """只返回裸 `work_id`——同 `domain/folders.list_work_ids_in_folder`，
    本模块不认识 `domain.works`，展开成完整文献记录是调用方的工作。
    """
    rows = db.scalars(select(WorkTag.work_id).where(WorkTag.tag_id == tag_id).order_by(WorkTag.created_at))
    return list(rows)


# ── 三态聚合（批量打标签 UI 用） ───────────────────────────────────────────


def aggregate_states(db: Session, *, library_id: int, work_ids: list[int]) -> list[TagStateDTO]:
    """给一组被选中的文献，算出库里**每一个**标签相对这组文献的三态：

    - "all"：这组文献全部都打了这个标签
    - "some"：只有部分打了（UI 画成"部分勾选"的中间态方框）
    - "none"：一个都没打

    这正是侧栏/卡片批量打标签对话框要的那个"不确定态复选框"的数据来源。
    `work_ids` 为空时所有标签都是 "none"（没有文献可比较，谈不上"全部"）。
    """
    tags = list_tags(db, library_id=library_id)
    if not work_ids:
        return [TagStateDTO(tag=tag, state="none") for tag in tags]

    work_id_set = set(work_ids)
    total = len(work_id_set)

    counts: dict[int, int] = {}
    rows = db.scalars(select(WorkTag.tag_id).where(WorkTag.work_id.in_(work_id_set)))
    for tag_id in rows:
        counts[tag_id] = counts.get(tag_id, 0) + 1

    states = []
    for tag in tags:
        count = counts.get(tag.id, 0)
        if count == 0:
            state = "none"
        elif count == total:
            state = "all"
        else:
            state = "some"
        states.append(TagStateDTO(tag=tag, state=state))
    return states
