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
from sqlalchemy.pool import StaticPool

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


def _new_memory_engine():
    # StaticPool：sqlite:///:memory: 默认按线程分配各自独立的连接
    # (SingletonThreadPool)，而 app 层的 HTTP 测试（TestClient）会把同步
    # endpoint 丢进另一个线程池线程去跑（FastAPI 走 anyio.to_thread），
    # 跟建表时所在的线程不是同一个——不用 StaticPool 强制全程只有一个
    # 连接的话，请求线程会连到一个空的、没有任何表的全新内存库上，报
    # "no such table"。这个坑只在 app 层通过真实 HTTP 请求触发时才会
    # 暴露，domain/features 的单测直接用同一个 Session、同一个线程，
    # 刚好把这坑盖住了。
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def new_memory_session() -> Session:
    """给 domain 模块单测用：每次调用一个全新的、与 `ENGINE` 完全独立的内存
    SQLite。不走 `.env` 的 `DATABASE_URL`，不会碰开发库，调用之间也互不可见。

    这是 lint 规则 6 唯一的例外口子——规则本身禁止的是**业务代码**散落着造
    engine（前两轮"两套互不相通的 DB"的根因），不是禁止测试用独立的内存库；
    把这个口子开在本文件而不是各 `domain/<x>/tests/conftest.py` 里自己建，
    是为了让这条例外仍然只有一处。
    """
    return sessionmaker(bind=_new_memory_engine(), autoflush=False, expire_on_commit=False)()


def new_memory_session_factory() -> sessionmaker:
    """给 `app/` 层 HTTP 测试用：返回绑定在一个独立内存 SQLite 上的
    `sessionmaker` 本身，而不是 `new_memory_session()` 那样的单个 Session。

    `app.shell.deps.db` 每个请求各自开关一个 Session；一次 HTTP 测试内
    往往要发多个请求（比如先注册再登录），这些请求必须落在**同一个**内存
    数据库上，但各自仍然是独立的 Session——所以需要的是 sessionmaker，
    调用方（`app.shell.testing.test_client`）拿它去覆盖 `db` 依赖，每次
    请求现造一个 Session，整个测试期间共用同一个底层 engine。

    和 `new_memory_session()` 共用同一个 `_new_memory_engine()`，没有在
    本文件之外新开一个 create_engine 调用点。
    """
    return sessionmaker(bind=_new_memory_engine(), autoflush=False, expire_on_commit=False)
