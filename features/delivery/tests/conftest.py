import pytest

from domain.connections import models as connection_models  # noqa: F401
from domain.reports import models as report_models  # noqa: F401
from infra.db import new_memory_session


@pytest.fixture()
def db():
    session = new_memory_session()
    try:
        yield session
    finally:
        session.close()
