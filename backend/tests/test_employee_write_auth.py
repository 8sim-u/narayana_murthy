"""Write-side employee authorization regressions; intentionally not run here."""
import hashlib
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import activity_models  # noqa: F401 - registers ActivityLog
from app import security_models  # noqa: F401 - registers SecurityEvent
from app.activity import router as activity_router
from app.activity_models import ActivityAuditHead
from app.audit import AUDIT_HEAD_ID, GENESIS_HASH
from app.auth import APIKeyAuthMiddleware
from app.database import get_db
from app.models import Base
from app.security import router as security_router

API_KEY = "write-auth-deployment-key"
EMPLOYEE_A = "write-auth-employee-a"
EMPLOYEE_B = "write-auth-employee-b"
TOKEN_A = "write-auth-token-a"
TOKEN_B = "write-auth-token-b"


@pytest.fixture
def write_client(monkeypatch):
    monkeypatch.setenv("WAMSS_API_KEY", API_KEY)
    monkeypatch.setenv("WAMSS_EMPLOYEE_INGESTION_SERVICE_ACCESS", "0")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # Activity writes append to the hash chain, which needs its singleton
    # head row. The main app seeds it at startup via ensure_audit_schema;
    # this standalone fixture seeds the same genesis head directly.
    seed = testing_session()
    try:
        seed.add(ActivityAuditHead(
            id=AUDIT_HEAD_ID,
            last_event_id=None,
            last_hash=GENESIS_HASH,
            pruned_through_event_id=None,
            pruned_through_hash=GENESIS_HASH,
        ))
        seed.commit()
    finally:
        seed.close()
    app = FastAPI()
    app.include_router(activity_router)
    app.include_router(security_router)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.add_middleware(APIKeyAuthMiddleware, api_key=API_KEY)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def _configure_tokens(monkeypatch):
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        json.dumps({
            EMPLOYEE_A: hashlib.sha256(TOKEN_A.encode()).hexdigest(),
            EMPLOYEE_B: hashlib.sha256(TOKEN_B.encode()).hexdigest(),
        }),
    )


def _headers(token=None, api_key=API_KEY):
    headers = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    if token is not None:
        headers["X-Employee-Token"] = token
    return headers


def _activity_record(employee_id):
    now = datetime.now(timezone.utc)
    return {
        "machine_id": "write-auth-machine",
        "employee_id": employee_id,
        "event_type": "activity",
        "app_name": "editor",
        "window_title": "work window",
        "process_name": "editor",
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "keystrokes": 0,
        "mouse_clicks": 0,
        "is_idle": False,
        "strain_score": 20,
    }


def _security_event(employee_id):
    return {
        "machine_id": "write-auth-machine",
        "employee_id": employee_id,
        "type": "usb_insert",
        "detail": {"device": "synthetic-test-device"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def test_activity_and_security_writes_require_matching_employee_token(
    write_client, monkeypatch
):
    """Employee writes bind to the token owner; the deployment key is not
    also required for employee-token writes (documented POST /activity
    exemption in app.auth: wearable clients must not hold the deployment
    key). A deployment key alone, with no employee token, is rejected."""
    _configure_tokens(monkeypatch)
    record_b = _activity_record(EMPLOYEE_B)
    event_b = _security_event(EMPLOYEE_B)

    missing_employee = write_client.post(
        "/activity", json={"records": [_activity_record(EMPLOYEE_A)]},
        headers=_headers(),
    )
    assert missing_employee.status_code == 401
    key_only = write_client.post(
        "/activity", json={"records": [_activity_record(EMPLOYEE_A)]},
        headers=_headers(api_key=API_KEY),
    )
    assert key_only.status_code == 401

    token_only = write_client.post(
        "/activity", json={"records": [_activity_record(EMPLOYEE_A)]},
        headers=_headers(TOKEN_A),
    )
    assert token_only.status_code == 201, token_only.text

    spoofed_activity = write_client.post(
        "/activity", json={"records": [record_b]}, headers=_headers(TOKEN_A)
    )
    spoofed_security = write_client.post(
        "/security/events", json={"events": [event_b]}, headers=_headers(TOKEN_A)
    )
    assert spoofed_activity.status_code == 403
    assert spoofed_security.status_code == 403

    own_activity = write_client.post(
        "/activity", json={"records": [_activity_record(EMPLOYEE_A)]},
        headers=_headers(TOKEN_A),
    )
    own_security = write_client.post(
        "/security/events", json={"events": [_security_event(EMPLOYEE_A)]},
        headers=_headers(TOKEN_A),
    )
    assert own_activity.status_code == 201, own_activity.text
    assert own_security.status_code == 201, own_security.text

    rows_b = write_client.get("/activity", headers=_headers(TOKEN_B))
    alerts_b = write_client.get("/security/alerts", headers=_headers(TOKEN_B))
    assert rows_b.status_code == 200
    assert rows_b.json() == []
    assert alerts_b.status_code == 200
    assert alerts_b.json() == []


def test_service_ingestion_is_opt_in_and_requires_a_validated_api_key(
    write_client, monkeypatch
):
    _configure_tokens(monkeypatch)
    activity_batch = {
        "records": [_activity_record(EMPLOYEE_A), _activity_record(EMPLOYEE_B)]
    }
    security_batch = {
        "events": [_security_event(EMPLOYEE_A), _security_event(EMPLOYEE_B)]
    }

    disabled = write_client.post(
        "/activity", json=activity_batch, headers=_headers()
    )
    assert disabled.status_code == 401

    monkeypatch.setenv("WAMSS_EMPLOYEE_INGESTION_SERVICE_ACCESS", "1")
    missing_key = write_client.post(
        "/activity", json=activity_batch, headers={}
    )
    bad_key = write_client.post(
        "/activity", json=activity_batch, headers=_headers(api_key="wrong-key")
    )
    assert missing_key.status_code == 401
    assert bad_key.status_code == 401

    service_activity = write_client.post(
        "/activity", json=activity_batch, headers=_headers()
    )
    service_security = write_client.post(
        "/security/events", json=security_batch, headers=_headers()
    )
    assert service_activity.status_code == 201, service_activity.text
    assert service_security.status_code == 201, service_security.text

    # Service mode is not a fallback for a caller that did supply a bad token.
    invalid_token = write_client.post(
        "/activity", json={"records": [_activity_record(EMPLOYEE_B)]},
        headers=_headers("not-a-configured-token"),
    )
    assert invalid_token.status_code == 401

    own_rows = write_client.get("/activity", headers=_headers(TOKEN_A))
    own_alerts = write_client.get("/security/alerts", headers=_headers(TOKEN_A))
    assert own_rows.status_code == 200
    assert {row["employee_id"] for row in own_rows.json()} == {EMPLOYEE_A}
    assert own_alerts.status_code == 200
    assert {item["employee_id"] for item in own_alerts.json()} == {EMPLOYEE_A}
