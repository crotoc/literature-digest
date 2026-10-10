import pytest

from domain.connections import models as _connections_models  # noqa: F401
from domain.intents import create_intent, create_intent_version, models  # noqa: F401
from domain.jobs import models as _jobs_models  # noqa: F401
from domain.libraries import models as _libraries_models  # noqa: F401
from domain.schedules import models as _schedules_models  # noqa: F401
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
def intent_id(db):
    intent = create_intent(
        db, account_id=ACCOUNT, name="测试意图", question_text="CRISPR base editing 体内递送的最新进展"
    )
    create_intent_version(
        db, intent_id=intent.id, query_json={"query": "CRISPR base editing"}, source="ai", model="gpt-4o-mini"
    )
    return intent.id
