"""domain/works 的 ORM 模型：文献条目本身 + 四张附属表
（identifiers / relations / provenance / duplicate_candidates）。

只有本模块的 service.py 能 import 这里的东西——外部统一走 contract.py 拿到
的 DTO。跨 domain 的引用（`library_id`）刻意不建数据库外键，约定和理由见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节；同一 domain 内部的引用（`work_id` → `works.id`）仍然是真 FK。
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Work(Base):
    __tablename__ = "works"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK

    item_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    authors_json: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column()
    month: Mapped[int | None] = mapped_column()
    day: Mapped[int | None] = mapped_column()
    container_title: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[str | None] = mapped_column(String(64))
    issue: Mapped[str | None] = mapped_column(String(64))
    pages: Mapped[str | None] = mapped_column(String(64))
    publisher: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)

    title_year_key: Mapped[str | None] = mapped_column(String(300), index=True)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(index=True)


class WorkIdentifier(Base):
    __tablename__ = "work_identifiers"
    __table_args__ = (UniqueConstraint("library_id", "scheme", "value_norm", name="uq_work_identifier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    scheme: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(512))
    value_norm: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class WorkRelation(Base):
    __tablename__ = "work_relations"
    __table_args__ = (
        UniqueConstraint("from_work_id", "to_work_id", "relation_type", name="uq_work_relation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    from_work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    to_work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class WorkProvenance(Base):
    __tablename__ = "work_provenance"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(128))
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        UniqueConstraint("work_id", "candidate_work_id", name="uq_duplicate_candidate_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    candidate_work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    reason: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column()
