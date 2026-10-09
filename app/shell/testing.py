"""给 `app/pages/*/tests` 用的测试客户端工厂。

和 `infra.db.new_memory_session_factory()` 是同一类例外口子的消费方：
生产路径上 `app.shell.deps.db` 固定绑定 `infra.db.SessionFactory`（唯一
engine，lint 规则 6），但各页面的 HTTP 测试需要一个和开发库完全隔离、
每个测试互不可见的数据库——这里统一提供 `test_client()`，不让每个
`app/pages/<x>/tests/conftest.py` 各自重新发明一遍同样的
`dependency_overrides` 接线。本文件自己不新开 engine/session 工厂，只调
`infra.db.new_memory_session_factory()`——那两处调用点仍然只在
`infra/db.py` 里（满足规则 6；这句话特意不把那两个函数名和左括号连在一起
写，避免 lint 的 grep 把本段说明文字本身误判成一处违规）。
"""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shell.deps import db as db_dependency
from infra.db import new_memory_session_factory


@contextmanager
def test_client(app: FastAPI) -> Iterator[TestClient]:
    """`app` 必须是 `app.main.create_app(create_tables=False)` 的返回值。

    建表动作（`new_memory_session_factory()` 内部）必须晚于 `create_app()`
    里的 `registry.discover()`——规避清单 #2：`discover()` 会 import 所有
    页面模块、从而 import 到所有 domain 的 `models.py`，建表动作排在这
    之前会漏建表。调用方只要保证"先 `create_app()` 再传给这个函数"就自动
    满足这条顺序，不需要自己操心。
    """
    session_factory = new_memory_session_factory()

    def _override_db() -> Iterator:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[db_dependency] = _override_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(db_dependency, None)
