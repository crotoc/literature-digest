"""domain/jobs 的 ORM 模型：所有后台/批量操作的通用追踪表。

只有本模块的 service.py 能 import 这里的东西——外部统一走 contract.py 拿到
的 DTO。`account_id` / `library_id` 跨 domain 引用，刻意不建外键，约定见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节；`parent_job_id` 是同 domain 内部引用（父子 job 关系），仍然是真 FK。
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)

    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    library_id: Mapped[int | None] = mapped_column(index=True)  # 同上，且本来就可选

    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    reason: Mapped[str | None] = mapped_column(String(64))

    counts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    cursor: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
