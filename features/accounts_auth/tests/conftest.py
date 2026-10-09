import pytest

from domain.accounts import models as _accounts_models  # noqa: F401 — 注册进 Base.metadata
from domain.libraries import models as _libraries_models  # noqa: F401
from infra.db import new_memory_session


@pytest.fixture()
def db():
    session = new_memory_session()
    try:
        yield session
    finally:
        session.close()
