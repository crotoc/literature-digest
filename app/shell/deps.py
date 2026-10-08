"""FastAPI 依赖。

service 层接收 Session、自己不造 session（四件套约定）；
造 session 的唯一职责在这里和 infra.db.session_scope。
"""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from infra.db import SessionFactory


def db() -> Iterator[Session]:
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
