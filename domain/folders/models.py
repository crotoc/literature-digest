"""domain/folders 的 ORM 模型：可嵌套的文件夹 + work↔folder 多对多关联。

只有本模块的 service.py 能 import 这里的东西。跨 domain 的引用（`library_id`、
`WorkFolder.work_id`）刻意不建数据库外键，约定见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节；`Folder.parent_folder_id`（自引用）和 `WorkFolder.folder_id` 都在本模块
内部，是真 FK。
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    parent_folder_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class WorkFolder(Base):
    __tablename__ = "work_folders"
    __table_args__ = (UniqueConstraint("work_id", "folder_id", name="uq_work_folder"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    work_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用（domain.works），刻意不建 FK
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
