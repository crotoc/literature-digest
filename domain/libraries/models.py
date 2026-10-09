"""domain/libraries 的 ORM 模型：库、库成员。

只有本模块的 service.py 能 import 这里的东西——外部统一走 contract.py 拿到的
DTO。

`LibraryMember.account_id` **刻意不是** `ForeignKey("accounts.id")`——跨
domain 的引用只在本 domain 内部用真正的 FK（`library_id` 指回本模块自己的
`libraries` 表），指向别的 domain 的列用裸 `int`，不建 DB 级约束。理由：
DB 级跨 domain FK 会要求任何建这张表的地方都必须先有 `accounts` 表存在，
这会悄悄把"domain 之间只许经由 contract 互相访问"的 Python 级规则在 DB
schema 层面又绕回去——而且会让 `domain/libraries/tests` 没法在不 import
`domain.accounts.models` 的情况下独立建表、独立跑绿。账号确实存在这件事由
调用方保证（`create_library(owner_account_id=...)` 只会被已经拿到一个真实
`AccountDTO.id` 的调用方调用）。
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring（带时区的 datetime 在 SQLite 上写进去再读出来会丢时区）。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class LibraryMember(Base):
    __tablename__ = "library_members"
    __table_args__ = (UniqueConstraint("library_id", "account_id", name="uq_library_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"), index=True)
    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK，见上
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
