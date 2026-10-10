import pytest

from domain.intents import create_intent, models  # noqa: F401 — import 时把表注册进 Base.metadata
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
    return intent.id
