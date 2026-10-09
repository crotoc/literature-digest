"""domain/attachments 的 ORM 模型：一篇文献挂的文件（PDF/补充材料等）。

字节本身存在 `caps/blobstore`（按 sha256 内容寻址，同内容只存一份）；本模块
只存"谁引用了哪个 digest"的元数据，引用计数靠查本表算出来——`digest` 不是
blobstore 的外键，因为 blobstore 没有表（无表的东西没法建 FK，也不该有）。

只有本模块的 service.py 能 import 这里的东西。跨 domain 的引用（`library_id`、
`work_id`）刻意不建数据库外键，约定见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节。
"""

from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    work_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用（domain.works），刻意不建 FK

    role: Mapped[str] = mapped_column(String(16), default="other")
    filename: Mapped[str] = mapped_column(String(512))
    rel_path: Mapped[str | None] = mapped_column(String(1024))  # 整目录上传时保留的相对路径

    digest: Mapped[str] = mapped_column(String(64), index=True)  # caps/blobstore 的 sha256 key，不是 FK
    size: Mapped[int] = mapped_column()
    content_type: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
