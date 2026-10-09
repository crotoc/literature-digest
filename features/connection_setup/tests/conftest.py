import pytest

from domain.connections import models as _connections_models  # noqa: F401
from infra.db import new_memory_session

ACCOUNT = 1


@pytest.fixture()
def db():
    session = new_memory_session()
    try:
        yield session
    finally:
        session.close()
