"""全项目唯一的 engine / Session 工厂。

lint 规则 6：除本文件外禁止出现 create_engine( / sessionmaker(。
前两轮出现过"两套互不相通的 DB"，根因就是多处各自建 engine。

init_db() 必须在**所有** domain 的 models 被 import 之后调用，否则
新进程首次请求会报 no such table（规避清单 #2）。调用点在 app/main.py。
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from infra.config import settings


class Base(DeclarativeBase):
    """所有 domain 的 models 都挂这一个 Base。"""


def _make_engine():
    url = settings().database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


ENGINE = _make_engine()
SessionFactory = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表。仅 dev 用；生产走 alembic。"""
    Base.metadata.create_all(ENGINE)


@contextmanager
def session_scope() -> Iterator[Session]:
    """给后台任务用。请求路径上用 app.shell.deps.db 依赖。"""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def new_memory_session() -> Session:
    """给 domain 模块单测用：每次调用一个全新的、与 `ENGINE` 完全独立的内存
    SQLite。不走 `.env` 的 `DATABASE_URL`，不会碰开发库，调用之间也互不可见。

    这是 lint 规则 6 唯一的例外口子——规则本身禁止的是**业务代码**散落着造
    engine（前两轮"两套互不相通的 DB"的根因），不是禁止测试用独立的内存库；
    把这个口子开在本文件而不是各 `domain/<x>/tests/conftest.py` 里自己建，
    是为了让这条例外仍然只有一处。
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
