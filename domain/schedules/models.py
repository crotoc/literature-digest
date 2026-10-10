"""domain/schedules 的 ORM 模型：通用的"这个目标该多久跑一次"追踪表。

和 `domain/jobs` 同一种"刻意无知"的设计：本模块不知道 `target_kind` 的
任何具体含义（比如 `"intent_discovery"`），只提供一张通用的 cron 调度
骨架，具体"这是什么目标、调度到了该干什么"由调用方（各 `features/*`）
注入和解释。

只有本模块的 service.py 能 import 这里的东西——外部统一走 contract.py
拿到的 DTO。`account_id`/`target_id` 跨 domain 引用，刻意不建外键，约定
见 `domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是
数据库外键」一节。
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("target_kind", "target_id", name="uq_schedule_target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK

    target_kind: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK

    cron: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column()

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
