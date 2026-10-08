"""Alembic 环境。URL 和 metadata 都从 infra 取，不在 alembic.ini 里重复配置。"""

from alembic import context

from app.shell import registry  # noqa: F401 — 先把所有 models import 进来
from infra.db import ENGINE, Base

registry.discover()  # 确保每个 domain 的 models 都注册到 Base.metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(ENGINE.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with ENGINE.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
