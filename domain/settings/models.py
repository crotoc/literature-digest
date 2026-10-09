"""domain/settings 的 ORM 模型：站点级 + 账号级两张设置表，行式存储。

只有本模块的 service.py 能 import 这里的东西。`AccountSetting.account_id`
跨 domain 引用，刻意不建外键，约定见 `domain/libraries/README.md`
「`LibraryMember.account_id` 刻意不是数据库外键」一节。

两张表都是 `(module, key) -> value_json` 的行式存储，而不是把设置值塞进
某个业务表的 JSON 列（旧单体把 `library_name`/`main_pdf_template` 等塞进
`workspace.fulltext_proxy_json`，是规避清单第 7 条明确要避开的坑）。
`module` 是声明这个设置项的 feature 名字（比如 `"exporting"`），`key` 是
该 feature 内部的设置名（比如 `"default_csl_style"`）——两者组合才唯一。
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class SiteSetting(Base):
    __tablename__ = "settings_site"
    __table_args__ = (UniqueConstraint("module", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(128))
    value_json: Mapped[object] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)


class AccountSetting(Base):
    __tablename__ = "settings_account"
    __table_args__ = (UniqueConstraint("account_id", "module", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    module: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(128))
    value_json: Mapped[object] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
