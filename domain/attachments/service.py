"""domain/attachments 的业务逻辑：一篇文献挂的文件 + blob 引用计数。

本模块不认识 `domain.works.Work`（跨 domain，见 models.py 的说明），也不认识
`caps.blobstore`——字节的实际读写、`put`/`delete` 全部由调用方
（`features/uploading`）持有的 `BlobStore` 实例完成；本模块只回答"这个
digest 现在还有没有别的附件引用着"这一个问题，`delete_attachment` 把这个
事实（是否变成孤儿）返回给调用方，由它决定是否真的去调 blobstore 删字节——
这正是 `caps/blobstore/README.md`「`delete()` 不查引用计数」那条警告里说的
"调用方必须自己先确认没人引用"，这里的调用方就是本模块的使用者。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.attachments.models import Attachment
from infra.errors import NotFound

ROLES = frozenset({"main", "other"})
DEFAULT_ROLE = "other"


class AttachmentNotFound(NotFound):
    code = "attachment_not_found"


@dataclass(frozen=True)
class AttachmentDTO:
    id: int
    library_id: int
    work_id: int
    role: str
    filename: str
    rel_path: str | None
    digest: str
    size: int
    content_type: str | None
    created_at: datetime


def _attachment_dto(row: Attachment) -> AttachmentDTO:
    return AttachmentDTO(
        id=row.id,
        library_id=row.library_id,
        work_id=row.work_id,
        role=row.role,
        filename=row.filename,
        rel_path=row.rel_path,
        digest=row.digest,
        size=row.size,
        content_type=row.content_type,
        created_at=row.created_at,
    )


def _get_attachment_row(db: Session, attachment_id: int) -> Attachment:
    row = db.get(Attachment, attachment_id)
    if row is None:
        raise AttachmentNotFound(f"附件不存在：{attachment_id}")
    return row


def _demote_existing_main(db: Session, work_id: int) -> None:
    current_main = db.scalar(
        select(Attachment).where(Attachment.work_id == work_id, Attachment.role == "main")
    )
    if current_main is not None:
        current_main.role = "other"


def create_attachment(
    db: Session,
    *,
    library_id: int,
    work_id: int,
    filename: str,
    digest: str,
    size: int,
    role: str = DEFAULT_ROLE,
    rel_path: str | None = None,
    content_type: str | None = None,
) -> AttachmentDTO:
    """挂一个文件。`role="main"` 时自动把这篇文献原来的 main（如果有）降级
    成 `other`——一篇文献任何时候只有一个 main（供内嵌阅读器默认打开）。
    """
    if role not in ROLES:
        raise ValueError(f"role 必须是 {sorted(ROLES)} 之一，收到 {role!r}")

    if role == "main":
        _demote_existing_main(db, work_id)

    row = Attachment(
        library_id=library_id,
        work_id=work_id,
        role=role,
        filename=filename,
        rel_path=rel_path,
        digest=digest,
        size=size,
        content_type=content_type,
    )
    db.add(row)
    db.flush()
    return _attachment_dto(row)


def get_attachment(db: Session, attachment_id: int) -> AttachmentDTO:
    return _attachment_dto(_get_attachment_row(db, attachment_id))


def set_main_attachment(db: Session, attachment_id: int) -> AttachmentDTO:
    """把一个已存在的附件设为 main，同一篇文献原来的 main（如果不是它自己）
    被降级成 `other`。
    """
    row = _get_attachment_row(db, attachment_id)
    if row.role != "main":
        _demote_existing_main(db, row.work_id)
        row.role = "main"
        db.flush()
    return _attachment_dto(row)


def list_attachments_for_work(db: Session, work_id: int) -> list[AttachmentDTO]:
    """main 排在最前面，其余按挂上的时间顺序——内嵌阅读器/卡片面板默认展示
    第一项就是 main。
    """
    rows = db.scalars(
        select(Attachment)
        .where(Attachment.work_id == work_id)
        .order_by((Attachment.role == "main").desc(), Attachment.created_at)
    )
    return [_attachment_dto(row) for row in rows]


def count_references(db: Session, digest: str) -> int:
    """这个 digest 现在被多少条附件记录引用着——`caps/blobstore` 的内容去重
    意味着同一个 digest 可能同时被好几篇文献、好几个附件引用，删除任何一个
    之前都要先问这个问题。
    """
    return db.scalar(select(func.count()).select_from(Attachment).where(Attachment.digest == digest)) or 0


def delete_attachment(db: Session, attachment_id: int) -> bool:
    """删除一条附件记录。返回这个 digest 删完之后是不是变成了孤儿（没有任
    何附件记录还引用着它）——调用方只有在这里返回 `True` 时才应该去调
    `BlobStore.delete(digest)`，本模块不替调用方做这个决定，也不自己去调
    blobstore（本模块不认识 blobstore，见模块顶部说明）。
    """
    row = _get_attachment_row(db, attachment_id)
    digest = row.digest
    db.delete(row)
    db.flush()
    return count_references(db, digest) == 0
