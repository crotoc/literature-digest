"""domain/accounts 的 ORM 模型：账号、会话、密码重置令牌、API token。

只有本模块的 service.py 能 import 这里的东西——外部统一走 contract.py 拿到的
DTO，不碰这些 SQLAlchemy 对象本身：它们绑定着某个 Session，离开那个 Session
的作用域后再访问字段会是 DetachedInstanceError，这种耦合不该泄露到别的模块。
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——这些列是普通 DateTime，不是
    DateTime(timezone=True)，带时区信息的 datetime 在 SQLite 上写进去再读出来
    会丢时区（存成不带 offset 的字符串），和内存里刚写入的带时区对象一比较就是
    aware/naive 混用的 TypeError。统一存朴素 UTC，全模块只用这一个时间源。
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class AccountSession(Base):
    __tablename__ = "account_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column()


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    lookup_prefix: Mapped[str] = mapped_column(String(12), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column()


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(100))
    lookup_prefix: Mapped[str] = mapped_column(String(12), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
