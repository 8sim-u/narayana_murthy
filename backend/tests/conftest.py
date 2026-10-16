import os
import pytest
from fastapi.testclient import TestClient

# Ensure DATABASE_URL is available for test execution if not set in environment or .env.
# Tests default to a throwaway SQLite database so the suite runs without PostgreSQL.
# NEVER hardcode real credentials here - this file is committed to git.
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite:///./test_wamss.db"

from app.database import init_db
from app.main import app

# Ensure tables exist
init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
