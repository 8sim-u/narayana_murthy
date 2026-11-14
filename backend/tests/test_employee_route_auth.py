"""Route-level employee identity binding regressions for personal read APIs."""
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import activity_models  # noqa: F401
from app import security_models  # noqa: F401
from app.database import get_db
from app.exports import router as exports_router
from app.focus import router as focus_router
from app.hours import router as hours_router
from app.models import Base
from app.policy import router as policy_router
from app.security import router as security_router

TOKEN_A = "employee-token-a"
EMPLOYEE_A = "auth-test-employee-a"
EMPLOYEE_B = "auth-test-employee-b"

PROTECTED_ROUTES = [
    ("/focus/daily", {"employee_id": EMPLOYEE_A}),
    ("/focus/streaks", {"employee_id": EMPLOYEE_A}),
    ("/hours/baseline", {"employee_id": EMPLOYEE_A}),
    ("/hours/anomaly", {"employee_id": EMPLOYEE_A}),
    ("/policy/violations", {"employee_id": EMPLOYEE_A, "date": "2026-10-30"}),
    ("/security/alerts", {"employee_id": EMPLOYEE_A}),
    ("/exports/activity.csv", {"employee_id": EMPLOYEE_A}),
    ("/exports/focus.csv", {"employee_id": EMPLOYEE_A}),
]


@pytest.fixture
def auth_client(monkeypatch):
    token_hashes = {
        EMPLOYEE_A: hashlib.sha256(TOKEN_A.encode()).hexdigest(),
        EMPLOYEE_B: hashlib.sha256(b"employee-token-b").hexdigest(),
    }
    monkeypatch.setenv("WAMSS_EMPLOYEE_TOKEN_HASHES", json.dumps(token_hashes))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app = FastAPI()
    for router in (focus_router, hours_router, policy_router, security_router, exports_router):
        app.include_router(router)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    engine.dispose()


@pytest.mark.parametrize("path,params", PROTECTED_ROUTES)
def test_protected_read_requires_employee_token_and_only_allows_self(auth_client, path, params):
    missing = auth_client.get(path, params=params)
    assert missing.status_code == 401

    own = auth_client.get(path, params=params, headers={"X-Employee-Token": TOKEN_A})
    assert own.status_code == 200

    cross_params = dict(params)
    cross_params["employee_id"] = EMPLOYEE_B
    cross = auth_client.get(path, params=cross_params, headers={"X-Employee-Token": TOKEN_A})
    assert cross.status_code == 403
