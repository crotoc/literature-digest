"""domain/tags 的 ORM 模型：库内扁平标签池 + work↔tag 多对多关联。

只有本模块的 service.py 能 import 这里的东西。跨 domain 的引用（`library_id`、
`WorkTag.work_id`）刻意不建数据库外键，约定见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节；`WorkTag.tag_id` 在本模块内部，是真 FK。
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("library_id", "name", name="uq_tag_library_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class WorkTag(Base):
    __tablename__ = "work_tags"
    __table_args__ = (UniqueConstraint("work_id", "tag_id", name="uq_work_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    work_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用（domain.works），刻意不建 FK
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
