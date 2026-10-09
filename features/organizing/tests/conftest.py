import pytest

from caps.blobstore import BlobStore
from caps.blobstore.testing import MemoryBackend
from domain.attachments import models as _attachments_models  # noqa: F401 — 注册进 Base.metadata
from domain.folders import models as _folders_models  # noqa: F401
from domain.jobs import models as _jobs_models  # noqa: F401
from domain.libraries import create_library  # noqa: F401
from domain.libraries import models as _libraries_models  # noqa: F401
from domain.notes import models as _notes_models  # noqa: F401
from domain.tags import models as _tags_models  # noqa: F401
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
    return create_work(db, library_id=library_id, title="一篇测试文献").id


@pytest.fixture()
def blob_store():
    return BlobStore(MemoryBackend())
