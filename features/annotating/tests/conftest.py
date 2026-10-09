import pytest

from domain.libraries import create_library  # noqa: F401
from domain.libraries import models as _libraries_models  # noqa: F401
from domain.notes import models as _notes_models  # noqa: F401
from domain.works import create_work  # noqa: F401
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


@pytest.fixture()
def work_id(db, library_id):
    return create_work(db, library_id=library_id, title="原标题", year=2020).id
