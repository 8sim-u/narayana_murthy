"""Regression coverage for employee-scoped presence, attendance, and analytics routes."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite:///./test_wamss.db"

from app.activity_models import ActivityLog
from app.analytics import router as analytics_router
from app.attendance import router as attendance_router
from app.database import SessionLocal, get_db
from app.models import DetectionEvent
from app.presence import router as presence_router

PREFIX = "employee_scope_regression_"


def _db_override():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _client():
    app = FastAPI()
    app.include_router(presence_router)
    app.include_router(attendance_router)
    app.include_router(analytics_router)
    app.dependency_overrides[get_db] = _db_override
    return TestClient(app)


def _seed(monkeypatch):
    db = SessionLocal()
    db.query(ActivityLog).filter(ActivityLog.employee_id.like(f"{PREFIX}%")).delete(synchronize_session=False)
    # Use a distinct zone/machine to avoid reliance on unrelated fixture data.
    now = datetime.now(timezone.utc)
    for suffix in ("self", "other"):
        employee_id = PREFIX + suffix
        db.add(ActivityLog(
            machine_id="EMPLOYEE_SCOPE_MACHINE", employee_id=employee_id,
            office_id="employee_scope_office", event_type="activity",
            app_name="editor", window_title="test", process_name="test",
            start_time=now - timedelta(minutes=2), end_time=now - timedelta(minutes=1),
            keystrokes=1, mouse_clicks=1, is_idle=False,
        ))
    db.commit()
    db.close()
    monkeypatch.setenv(
        "WAMSS_ZONE_MAP",
        json.dumps({"EMPLOYEE_SCOPE_ZONE": "EMPLOYEE_SCOPE_MACHINE"}),
    )
    return PREFIX + "self", PREFIX + "other"


def _auth(monkeypatch, employee_id, token):
    hashes = {employee_id: hashlib.sha256(token.encode()).hexdigest()}
    monkeypatch.setenv("WAMSS_EMPLOYEE_TOKEN_HASHES", json.dumps(hashes))
    return {"X-Employee-Token": token}


def test_employee_routes_deny_missing_token(monkeypatch):
    self_id, _ = _seed(monkeypatch)
    monkeypatch.setenv("WAMSS_EMPLOYEE_TOKEN_HASHES", json.dumps({self_id: hashlib.sha256(b"valid").hexdigest()}))
    client = _client()
    for path in ("/presence", "/attendance/daily", "/analytics/trends"):
        assert client.get(path).status_code in (401, 403)


def test_presence_and_analytics_never_return_other_employee(monkeypatch):
    self_id, other_id = _seed(monkeypatch)
    client = _client()
    headers = _auth(monkeypatch, self_id, "self-token")

    presence = client.get("/presence", headers=headers)
    assert presence.status_code == 200
    states = presence.json()
    assert states
    assert {state["employee_id"] for state in states} == {self_id}

    trends = client.get("/analytics/trends", params={"metric": "focus", "weeks": 1}, headers=headers)
    assert trends.status_code == 200
    data = trends.json()
    assert data["per_employee"]
    assert {row["employee_id"] for row in data["per_employee"]} == {self_id}
    # Personal rollup values, if present, are necessarily calculated from this same scoped row set.
    assert all(row["employee_count"] <= 1 for row in data["personal"])
    # The personal rollup mirrors the caller's own weekly mean, never a blend
    # with another employee's data from the same week.
    for personal_row in data["personal"]:
        mine = [
            row for row in data["per_employee"]
            if row["week_start"] == personal_row["week_start"]
        ]
        assert personal_row["average_focus"] == mine[0]["average_focus"]
    assert other_id not in str(data)


def test_attendance_disabled_still_requires_auth_and_is_empty(monkeypatch):
    self_id, _ = _seed(monkeypatch)
    from app import attendance

    monkeypatch.setattr(attendance, "ATTENDANCE_ENABLED", False)
    client = _client()
    assert client.get("/attendance/daily").status_code in (401, 403)
    response = client.get("/attendance/daily", headers=_auth(monkeypatch, self_id, "self-token"))
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["items"] == []


def test_enabled_attendance_returns_only_authenticated_employee(monkeypatch):
    self_id, other_id = _seed(monkeypatch)
    from app import attendance

    monkeypatch.setattr(attendance, "ATTENDANCE_ENABLED", True)
    client = _client()
    response = client.get("/attendance/daily", headers=_auth(monkeypatch, self_id, "self-token"))
    assert response.status_code == 200
    data = response.json()
    assert {item["employee_id"] for item in data["items"]} == {self_id}
    assert other_id not in str(data)
