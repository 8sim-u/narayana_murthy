"""Unit tests for the security/DLP signal module (Agent I).

Covers the pure flag_anomalies() rules with synthetic events and the
/security router endpoints against an isolated SQLite database. The main
application wiring (include_router in main.py) is owned by the coordinator.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base  # noqa: F401 - registers the shared declarative base
from app.security import flag_anomalies, router
from app.security_models import SecurityEvent  # noqa: F401 - registers table

# ---------------------------------------------------------------------------
# flag_anomalies: pure-function unit tests (synthetic events only)
# ---------------------------------------------------------------------------


def _event(event_type, ts, **kw):
    ev = {
        "event_type": event_type,
        "timestamp": ts,
        "machine_id": "WS-1",
        "employee_id": "EMP-1",
        "detail": {},
    }
    ev.update(kw)
    return ev


def test_usb_insert_flagged_high():
    alerts = flag_anomalies(
        [_event("usb_insert", datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
               detail={"device": "USB-8GB"})]
    )
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "high"
    assert "inserted" in alerts[0]["reason"]


def test_usb_remove_flagged_high():
    alerts = flag_anomalies(
        [_event("usb_remove", datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc))]
    )
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "high"


def test_after_hours_activity_flagged():
    # 23:30 UTC is outside 09:00-18:00 UTC.
    alerts = flag_anomalies(
        [_event("after_hours_activity", datetime(2026, 10, 29, 23, 30, tzinfo=timezone.utc))]
    )
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "medium"
    assert "work hours" in alerts[0]["reason"]


def test_timestamp_outside_work_hours_flagged_even_without_explicit_type():
    alerts = flag_anomalies(
        [_event("usb_insert", datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)),
         _event("file_write_burst", datetime(2026, 10, 29, 21, 5, tzinfo=timezone.utc),
                detail={"bytes_written_mb": 10.0, "threshold_mb": 500.0})]
    )
    assert len(alerts) == 2
    reasons = [a["reason"] for a in alerts]
    assert any("Removable storage" in r for r in reasons)
    # The write burst is below threshold, but 21:05 UTC is after hours.
    assert any("work hours" in r for r in reasons)


def test_normal_hours_not_flagged():
    # 12:00 UTC is inside 09:00-18:00 UTC.
    alerts = flag_anomalies(
        [_event("file_write_burst", datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
                detail={"bytes_written_mb": 10.0, "threshold_mb": 500.0})]
    )
    assert alerts == []


def test_write_burst_above_threshold_flagged():
    alerts = flag_anomalies(
        [_event("file_write_burst", datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
                detail={"bytes_written_mb": 640.0, "threshold_mb": 500.0})]
    )
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "medium"
    assert "640.0 MB" in alerts[0]["reason"]


def test_custom_work_hours_and_tz():
    # 10:00 UTC = 15:30 IST, inside a 09:00-18:00 IST window -> not flagged.
    alerts = flag_anomalies(
        [_event("file_write_burst", datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc),
                detail={"bytes_written_mb": 1.0})],
        work_hours="09:00-18:00",
        tz_name="Asia/Kolkata",
    )
    assert alerts == []


# ---------------------------------------------------------------------------
# Router endpoint tests (isolated app + SQLite; main.py wiring is separate)
# ---------------------------------------------------------------------------


@pytest.fixture
def security_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    from app import security

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[security.get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_ingest_and_alerts_roundtrip(security_client):
    now = datetime.now(timezone.utc)
    payload = {
        "events": [
            {
                "machine_id": "WS-9",
                "employee_id": "EMP-9",
                "type": "usb_insert",
                "detail": {"device": "USB-8GB"},
                "timestamp": now.isoformat(),
            },
            {
                "machine_id": "WS-9",
                "employee_id": "EMP-9",
                "type": "file_write_burst",
                "detail": {"bytes_written_mb": 900.0, "threshold_mb": 500.0},
                "timestamp": now.isoformat(),
            },
        ]
    }
    r = security_client.post("/security/events", json=payload)
    assert r.status_code == 201
    assert r.json()["received"] == 2

    r = security_client.get("/security/alerts", params={"machine_id": "WS-9"})
    assert r.status_code == 200
    alerts = r.json()
    assert len(alerts) == 2
    assert {a["event_type"] for a in alerts} == {"usb_insert", "file_write_burst"}
    by_type = {a["event_type"]: a for a in alerts}
    assert by_type["usb_insert"]["severity"] == "high"
    assert by_type["file_write_burst"]["severity"] == "medium"


def test_alerts_filter_by_employee(security_client):
    now = datetime.now(timezone.utc)
    payload = {
        "events": [
            {
                "machine_id": "WS-A",
                "employee_id": "EMP-A",
                "type": "usb_remove",
                "detail": {"device": "SD-64GB"},
                "timestamp": now.isoformat(),
            }
        ]
    }
    security_client.post("/security/events", json=payload)
    r = security_client.get("/security/alerts", params={"employee_id": "NOPE"})
    assert r.status_code == 200
    assert r.json() == []
    r = security_client.get("/security/alerts", params={"employee_id": "EMP-A"})
    assert len(r.json()) == 1


def test_ingest_rejects_unknown_type(security_client):
    payload = {
        "events": [
            {
                "machine_id": "WS-1",
                "employee_id": "EMP-1",
                "type": "screen_capture",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }
    r = security_client.post("/security/events", json=payload)
    assert r.status_code == 422
