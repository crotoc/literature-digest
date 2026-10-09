import pytest

from domain.jobs import models as _jobs_models  # noqa: F401 — 注册进 Base.metadata
from domain.libraries import create_library  # noqa: F401
from domain.libraries import models as _libraries_models  # noqa: F401
from domain.works import models as _works_models  # noqa: F401
from infra.db import new_memory_session

ACCOUNT = 1


@pytest.fixture()
def db():
    session = new_memory_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def library_id(db):
    return create_library(db, owner_account_id=ACCOUNT, name="测试库").id
