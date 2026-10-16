import os
import pytest
from fastapi.testclient import TestClient

# Ensure DATABASE_URL is available for test execution if not set in environment or .env
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://surveillance_app:tmkbfjgagastya@localhost:5432/surveillance_dev"
    )

from app.database import init_db
from app.main import app

# Ensure tables exist
init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
