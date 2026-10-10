import pytest

from domain.intents import models as intent_models  # noqa: F401
from domain.jobs import models as job_models  # noqa: F401
from domain.reports import models as report_models  # noqa: F401
from infra.db import new_memory_session


@pytest.fixture()
def db():
    session = new_memory_session()
    try:
        yield session
    finally:
        session.close()
