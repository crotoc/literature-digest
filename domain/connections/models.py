"""domain/connections 的 ORM 模型：统一存四类对外连接
（ai_profile / telegram_destination / source_credential / download_proxy）。

旧单体给这四类各写了一套几乎一样的 CRUD + 各自的"测试连接"（见规避清单 #6）。
合并成一张按 `kind` 区分的表，`enabled` + `is_default` 两个字段解决
"AI 只能有一个活跃配置、Telegram 可以同时推送到多个目的地"这种不同 kind
语义不一样的问题——`is_default` 在同一个 `(account_id, kind)` 下唯一的
那条不变量由 service.py 维护（见该文件 `_demote_existing_default` 的说明，
和 `domain/attachments` 的"每篇文献只有一个 main"是同一种处理方式，不是数据
库级约束）。

只有本模块的 service.py 能 import 这里的东西。跨 domain 的引用（`account_id`）
刻意不建数据库外键，约定见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节。
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK

    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)  # caps/secrets 产出的密文，本模块不存明文

    last_checked_at: Mapped[datetime | None] = mapped_column()
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_check_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
