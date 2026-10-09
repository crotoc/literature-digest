"""domain/usage 的 ORM 模型：账号级配额计数器，单表。

只有本模块的 service.py 能 import 这里的东西。`account_id` 跨 domain 引用，
刻意不建外键，约定见 `domain/libraries/README.md`「`LibraryMember.account_id`
刻意不是数据库外键」一节。

**没有 `library_id`**——配额按账号聚合，不按库乘倍（一个账号开 5 个库不代表
AI 调用额度变成 5 倍）。这是计划里明确写的设计："usage_counters（账号级配额
计数，按账号+周期+kind 汇总，不按库乘倍）"。
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("account_id", "period", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK

    period: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    count: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
