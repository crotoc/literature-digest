import pytest

from domain.connections import models  # noqa: F401 — import 时把表注册进 Base.metadata
from infra.db import new_memory_session


@pytest.fixture()
def db():
    session = new_memory_session()
    try:
        yield session
    finally:
        session.close()
