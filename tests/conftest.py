import os
import tempfile

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-chars-long")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.sqlite3")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import create_app

    with TestClient(create_app(create_tables=True)) as test_client:
        yield test_client
