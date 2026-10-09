"""domain/notes 的 ORM 模型：每篇文献最多一条自由文本笔记。

只有本模块的 service.py 能 import 这里的东西。跨 domain 的引用（`library_id`、
`work_id`）刻意不建数据库外键，约定见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节。
"""

from datetime import UTC, datetime

from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class WorkNote(Base):
    __tablename__ = "work_notes"
    __table_args__ = (UniqueConstraint("work_id", name="uq_work_note_work_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    work_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用（domain.works），刻意不建 FK
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
