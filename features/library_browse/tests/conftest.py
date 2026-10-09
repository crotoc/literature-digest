import pytest

from domain.attachments import models as _attachments_models  # noqa: F401 — 注册进 Base.metadata
from domain.folders import models as _folders_models  # noqa: F401
from domain.notes import models as _notes_models  # noqa: F401
from domain.tags import models as _tags_models  # noqa: F401
from domain.works import models as _works_models  # noqa: F401
from infra.db import new_memory_session


@pytest.fixture()
def db():
    session = new_memory_session()
    try:
        yield session
    finally:
        session.close()
