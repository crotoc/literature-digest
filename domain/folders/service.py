"""domain/folders 的业务逻辑：可嵌套文件夹 + work↔folder 多对多关联。

本模块不认识 `domain.works.Work` 这张表（跨 domain，见 models.py 的说明）——
涉及 work 的函数只收发裸 `work_id: int`，从不 import `domain.works`。校验
"这个 work_id 真的存在"是调用方（`features/organizing`）的职责，和
`domain/libraries.add_member(account_id=...)` 信任调用方已经验证过账号存在
是同一个约定。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.folders.models import Folder, WorkFolder
from infra.errors import AppError, NotFound

_UNSET = object()


# ── 异常 ─────────────────────────────────────────────────────────────────


class FolderNotFound(NotFound):
    code = "folder_not_found"


class WorkFolderNotFound(NotFound):
    code = "work_folder_not_found"


class FolderCycle(AppError):
    """把一个文件夹移到它自己的子孙下面——会在树里造出一个环，拒绝。"""

    status_code = 409
    code = "folder_cycle"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FolderDTO:
    id: int
    library_id: int
    parent_folder_id: int | None
    name: str
    created_at: datetime


@dataclass(frozen=True)
class WorkFolderDTO:
    id: int
    library_id: int
    work_id: int
    folder_id: int
    created_at: datetime


def _folder_dto(row: Folder) -> FolderDTO:
    return FolderDTO(
        id=row.id,
        library_id=row.library_id,
        parent_folder_id=row.parent_folder_id,
        name=row.name,
        created_at=row.created_at,
    )


def _work_folder_dto(row: WorkFolder) -> WorkFolderDTO:
    return WorkFolderDTO(
        id=row.id,
        library_id=row.library_id,
        work_id=row.work_id,
        folder_id=row.folder_id,
        created_at=row.created_at,
    )


def _get_folder_row(db: Session, folder_id: int) -> Folder:
    row = db.get(Folder, folder_id)
    if row is None:
        raise FolderNotFound(f"文件夹不存在：{folder_id}")
    return row


def _is_ancestor_or_self(db: Session, candidate_id: int, target_id: int) -> bool:
    """`candidate_id` 是不是 `target_id` 本身，或者是它的某一级祖先——
    `move_folder` 靠这个判断"新父节点在不在要移动的那棵子树里面"（环检测）。
    """
    node_id: int | None = candidate_id
    while node_id is not None:
        if node_id == target_id:
            return True
        node_id = db.scalar(select(Folder.parent_folder_id).where(Folder.id == node_id))
    return False


# ── 文件夹 ───────────────────────────────────────────────────────────────


def create_folder(
    db: Session, *, library_id: int, name: str, parent_folder_id: int | None = None
) -> FolderDTO:
    name = name.strip()
    if not name:
        raise ValueError("文件夹名不能为空")

    if parent_folder_id is not None:
        parent = _get_folder_row(db, parent_folder_id)
        if parent.library_id != library_id:
            raise ValueError("parent_folder_id 属于另一个库")

    row = Folder(library_id=library_id, parent_folder_id=parent_folder_id, name=name)
    db.add(row)
    db.flush()
    return _folder_dto(row)


def get_folder(db: Session, folder_id: int) -> FolderDTO:
    return _folder_dto(_get_folder_row(db, folder_id))


def rename_folder(db: Session, folder_id: int, name: str) -> FolderDTO:
    name = name.strip()
    if not name:
        raise ValueError("文件夹名不能为空")

    row = _get_folder_row(db, folder_id)
    row.name = name
    db.flush()
    return _folder_dto(row)


def move_folder(db: Session, folder_id: int, new_parent_folder_id: int | None) -> FolderDTO:
    """把一个文件夹挪到另一个父节点下（`None` = 挪到根）。拒绝把它挪进自己
    或自己的子孙——那会在树里造出一个环。
    """
    row = _get_folder_row(db, folder_id)

    if new_parent_folder_id is not None:
        if new_parent_folder_id == folder_id or _is_ancestor_or_self(db, new_parent_folder_id, folder_id):
            raise FolderCycle(f"不能把文件夹 {folder_id} 移到它自己的子孙 {new_parent_folder_id} 下面")
        new_parent = _get_folder_row(db, new_parent_folder_id)
        if new_parent.library_id != row.library_id:
            raise ValueError("new_parent_folder_id 属于另一个库")

    row.parent_folder_id = new_parent_folder_id
    db.flush()
    return _folder_dto(row)


def _collect_subtree_ids_top_down(db: Session, folder_id: int) -> list[int]:
    order = []
    queue = [folder_id]
    while queue:
        current = queue.pop(0)
        order.append(current)
        children = db.scalars(select(Folder.id).where(Folder.parent_folder_id == current)).all()
        queue.extend(children)
    return order


def delete_folder(db: Session, folder_id: int) -> None:
    """删除一个文件夹——连同它的全部子文件夹（递归）一起删，但**不删任何
    work**，只清理 work↔folder 的关联行。子文件夹先删、父文件夹后删（自底向
    上），避免自引用 FK 在真实强校验的数据库上报错。
    """
    _get_folder_row(db, folder_id)  # 存在性校验

    subtree_ids = _collect_subtree_ids_top_down(db, folder_id)
    for fid in reversed(subtree_ids):
        for link in db.scalars(select(WorkFolder).where(WorkFolder.folder_id == fid)):
            db.delete(link)
        folder_row = db.get(Folder, fid)
        if folder_row is not None:
            db.delete(folder_row)
    db.flush()


def list_folders(db: Session, *, library_id: int, parent_folder_id: int | None = _UNSET) -> list[FolderDTO]:
    """`parent_folder_id` 不传 → 返回整个库的全部文件夹（扁平列表，调用方
    自己用 `parent_folder_id` 字段拼树）。传了（哪怕是 `None` 代表根层）→
    只返回该层的直接子文件夹。
    """
    stmt = select(Folder).where(Folder.library_id == library_id)
    if parent_folder_id is not _UNSET:
        stmt = stmt.where(Folder.parent_folder_id == parent_folder_id)
    stmt = stmt.order_by(Folder.name)
    return [_folder_dto(row) for row in db.scalars(stmt)]


# ── work ↔ folder 关联 ───────────────────────────────────────────────────


def add_work_to_folder(db: Session, *, library_id: int, work_id: int, folder_id: int) -> WorkFolderDTO:
    """把一篇文献加入一个文件夹。同一对 `(work_id, folder_id)` 重复加是幂
    等的，直接返回已有行，不重复插入、不报错。
    """
    folder = _get_folder_row(db, folder_id)
    if folder.library_id != library_id:
        raise ValueError("folder_id 不属于这个库")

    existing = db.scalar(
        select(WorkFolder).where(WorkFolder.work_id == work_id, WorkFolder.folder_id == folder_id)
    )
    if existing is not None:
        return _work_folder_dto(existing)

    row = WorkFolder(library_id=library_id, work_id=work_id, folder_id=folder_id)
    db.add(row)
    db.flush()
    return _work_folder_dto(row)


def remove_work_from_folder(db: Session, *, work_id: int, folder_id: int) -> None:
    row = db.scalar(
        select(WorkFolder).where(WorkFolder.work_id == work_id, WorkFolder.folder_id == folder_id)
    )
    if row is None:
        raise WorkFolderNotFound(f"文献 {work_id} 不在文件夹 {folder_id} 里")
    db.delete(row)
    db.flush()


def list_folders_for_work(db: Session, work_id: int) -> list[FolderDTO]:
    folder_ids = db.scalars(select(WorkFolder.folder_id).where(WorkFolder.work_id == work_id)).all()
    if not folder_ids:
        return []
    rows = db.scalars(select(Folder).where(Folder.id.in_(folder_ids)).order_by(Folder.name))
    return [_folder_dto(row) for row in rows]


def list_work_ids_in_folder(db: Session, folder_id: int) -> list[int]:
    """只返回裸 `work_id`，不返回 `WorkDTO`——本模块不认识 `domain.works`，
    把 id 展开成完整文献记录是调用方（`library_browse`）的工作。
    """
    rows = db.scalars(
        select(WorkFolder.work_id).where(WorkFolder.folder_id == folder_id).order_by(WorkFolder.created_at)
    )
    return list(rows)
