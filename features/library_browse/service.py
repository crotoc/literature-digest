"""文献库列表的只读模型：筛选编译 / 分页 / 排序 / 卡片附属信息展开 /
`resolve_selection()`。组合 `domain/{works,folders,tags,attachments,notes}`，
不开表。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 `domain/accounts/service.py` 的同一段说明（本模块本身
全是只读查询，这条约定对它主要意味着"不自己开 session"）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from domain.attachments import AttachmentDTO, list_attachments_for_work
from domain.folders import FolderDTO, get_folder, list_folders_for_work, list_work_ids_in_folder
from domain.notes import WorkNoteDTO, list_notes_for_works
from domain.tags import TagDTO, get_tag, list_tags_for_work, list_work_ids_for_tag
from domain.works import WorkDTO, count_works, list_work_ids, list_works

SORT_KEYS = frozenset({"created_at", "updated_at", "year", "title"})
DEFAULT_SORT_BY = "updated_at"
DEFAULT_SORT_DIR = "desc"
DEFAULT_PAGE_SIZE = 50

VIEWS = frozenset({"all", "trash"})
DEFAULT_VIEW = "all"

SELECTION_MODES = frozenset({"explicit", "all_filtered"})


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LibraryCard:
    """一张卡片要展示的全部东西：文献本体 + 四个附属面板的数据。"""

    work: WorkDTO
    tags: tuple[TagDTO, ...]
    folders: tuple[FolderDTO, ...]
    attachments: tuple[AttachmentDTO, ...]
    note: WorkNoteDTO | None


@dataclass(frozen=True)
class LibraryPage:
    items: tuple[LibraryCard, ...]
    total: int
    """满足筛选条件的总数（不是 `len(items)`）——分页控件算总页数要用这个，
    不是这一页实际拿到几条。"""


# ── 筛选编译 ───────────────────────────────────────────────────────────────


def _validate_view(view: str) -> None:
    if view not in VIEWS:
        raise ValueError(f"不认识的 view：{view!r}")


def _validate_sort(sort_by: str, sort_dir: str) -> None:
    if sort_by not in SORT_KEYS:
        raise ValueError(f"不认识的 sort_by：{sort_by!r}")
    if sort_dir not in ("asc", "desc"):
        raise ValueError(f"sort_dir 必须是 asc 或 desc：{sort_dir!r}")


def _compile_filter_work_ids(
    db: Session, *, library_id: int, tag_ids: Sequence[int], folder_id: int | None
) -> list[int] | None:
    """把"按标签 AND 筛选 + 按文件夹筛选"编译成一组候选 `work_id`。

    标签之间是 AND（必须同时打了全部给定标签），和文件夹筛选之间也是
    AND（既在这个文件夹里、又打了这些标签）——用集合交集实现，候选集合都
    很小（一个标签/文件夹下的文献数,不是全库规模）,交集本身的开销可忽略。

    一个筛选条件都没给时返回 `None`（不是空列表）——`None` 传给
    `domain.works.list_works`/`list_work_ids` 的 `work_ids` 参数语义是"不额外
    限制",空列表的语义是"限制成空集合",两者必须分清楚,否则"没有任何筛选"
    会被误当成"筛选出空结果"。

    Raises:
        ValueError: 给定的 `tag_id`/`folder_id` 不属于 `library_id` 这个库——
            和 `domain.tags.add_tag_to_work`/`domain.folders.add_work_to_folder`
            遇到同样情况时的报法一致，不额外发明新的异常类型。
    """
    candidate_sets: list[set[int]] = []
    for tag_id in tag_ids:
        tag = get_tag(db, tag_id)
        if tag.library_id != library_id:
            raise ValueError(f"tag_id 不属于这个库：{tag_id}")
        candidate_sets.append(set(list_work_ids_for_tag(db, tag_id)))

    if folder_id is not None:
        folder = get_folder(db, folder_id)
        if folder.library_id != library_id:
            raise ValueError(f"folder_id 不属于这个库：{folder_id}")
        candidate_sets.append(set(list_work_ids_in_folder(db, folder_id)))

    if not candidate_sets:
        return None

    result = candidate_sets[0]
    for s in candidate_sets[1:]:
        result &= s
    return sorted(result)


# ── 列表 + 分页 + 排序 + 卡片展开 ───────────────────────────────────────────


def list_library_page(
    db: Session,
    *,
    library_id: int,
    view: str = DEFAULT_VIEW,
    tag_ids: Sequence[int] = (),
    folder_id: int | None = None,
    sort_by: str = DEFAULT_SORT_BY,
    sort_dir: str = DEFAULT_SORT_DIR,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> LibraryPage:
    """给文献库页面的一屏卡片列表：按条件筛选 → 排序 → 分页 → 给这一页每张
    卡片把标签/文件夹/附件/笔记一次性展开好,调用方不需要再逐条调
    `domain.tags.list_tags_for_work` 之类的函数。

    `view="trash"` 对应回收站视图（只看已删的，不是"也看已删的"）。

    `tags`/`folders`/`attachments` 三项是逐条 N+1 查询（`domain.notes` 有
    `list_notes_for_works` 批量版本，另外三个 domain 目前没有）——v1 一页
    卡片数量在几十这个量级，这个代价可接受；真要优化见本模块 README 的
    刻意裁剪范围。

    Raises:
        ValueError: `view`/`sort_by`/`sort_dir` 不合法，或 `tag_ids`/`folder_id`
            不属于这个库（见 `_compile_filter_work_ids`）。
    """
    _validate_view(view)
    _validate_sort(sort_by, sort_dir)
    deleted_only = view == "trash"

    work_ids = _compile_filter_work_ids(db, library_id=library_id, tag_ids=tag_ids, folder_id=folder_id)

    total = count_works(db, library_id=library_id, deleted_only=deleted_only, work_ids=work_ids)
    works = list_works(
        db,
        library_id=library_id,
        deleted_only=deleted_only,
        work_ids=work_ids,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )

    page_work_ids = [w.id for w in works]
    notes_by_work = list_notes_for_works(db, page_work_ids)
    cards = tuple(
        LibraryCard(
            work=work,
            tags=tuple(list_tags_for_work(db, work.id)),
            folders=tuple(list_folders_for_work(db, work.id)),
            attachments=tuple(list_attachments_for_work(db, work.id)),
            note=notes_by_work.get(work.id),
        )
        for work in works
    )
    return LibraryPage(items=cards, total=total)


# ── 全选所有筛选结果 ─────────────────────────────────────────────────────────


def resolve_selection(
    db: Session,
    *,
    library_id: int,
    mode: str,
    work_ids: Sequence[int] = (),
    view: str = DEFAULT_VIEW,
    tag_ids: Sequence[int] = (),
    folder_id: int | None = None,
) -> list[int]:
    """把"选择作用域"展开成一份显式 `work_id` 列表——这是「两处已定」第 2
    条的落地：前端只提交 `mode` + 筛选条件（`mode="all_filtered"` 时）或者一组
    手动勾选的 id（`mode="explicit"` 时），id 本身不需要在分页之间被传来传
    去；本模块用**一次查询**把 `all_filtered` 展开成全量 id,调用方
    （`organizing`/`exporting` 等批量操作 feature）从此只认 id 列表,不需要
    认识筛选条件。

    `mode="explicit"`：原样校验给定的 `work_ids` 确实属于 `library_id`（不属于
    的静默丢弃，不报错——这是一条防御性的库边界检查：前端正常情况下不会
    带来别的库的 id，但调用方不应该信任客户端传来的 id 没被篡改过）。

    `mode="all_filtered"`：按 `view`/`tag_ids`/`folder_id` 编译筛选条件后，
    一次查询返回全部命中的 id（不分页——这正是本函数存在的意义：分页是给
    人看的,批量操作要的是全集)。

    Raises:
        ValueError: `mode` 不合法；或 `mode="all_filtered"` 时 `view`/
            `tag_ids`/`folder_id` 不合法（同 `list_library_page`）。
    """
    if mode not in SELECTION_MODES:
        raise ValueError(f"不认识的 mode：{mode!r}")

    if mode == "explicit":
        if not work_ids:
            return []
        return list_work_ids(db, library_id=library_id, include_deleted=True, work_ids=list(work_ids))

    _validate_view(view)
    deleted_only = view == "trash"
    filter_ids = _compile_filter_work_ids(db, library_id=library_id, tag_ids=tag_ids, folder_id=folder_id)
    return list_work_ids(db, library_id=library_id, deleted_only=deleted_only, work_ids=filter_ids)
