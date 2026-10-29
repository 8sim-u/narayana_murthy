"""Tests for the CSV export endpoints in app.exports (routers mounted on a
dedicated test FastAPI app, per the agent-4 contract)."""

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.activity_models import ActivityLog
from app.database import SessionLocal, get_db
from app.exports import router as exports_router
from app.models import DetectionEvent

A4 = "a4x"  # unique prefix so these tests do not collide with other suites


def _override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(exports_router)
    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _cleanup(db):
    db.query(ActivityLog).filter(ActivityLog.employee_id.like(f"{A4}%")).delete(
        synchronize_session=False
    )
    db.query(DetectionEvent).filter(DetectionEvent.camera_id.like(f"{A4}%")).delete(
        synchronize_session=False
    )
    db.commit()


def _seed_activity(db, employee_id: str, day: datetime, n: int = 2, strain: int = 30):
    rows = []
    for i in range(n):
        start = day + timedelta(minutes=i * 30)
        rows.append(
            ActivityLog(
                machine_id=f"{A4}-m1",
                employee_id=employee_id,
                office_id="hq",
                event_type="activity",
                app_name="code",
                window_title=f"win {i}",
                process_name="code.exe",
                start_time=start,
                end_time=start + timedelta(minutes=20),
                keystrokes=100 * i,
                mouse_clicks=10 * i,
                is_idle=False,
                strain_score=strain,
            )
        )
    db.add_all(rows)
    db.commit()


def _seed_event(db, camera_id: str, zone: str = "ZONE_A"):
    db.add(
        DetectionEvent(
            camera_id=camera_id,
            track_id="7",
            objects=["person"],
            confidence=0.9,
            zone=zone,
            decision="NO_RULE_TRIGGERED",
            alerts=[],
            timestamp=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _parse_csv(text: str):
    reader = csv.DictReader(io.StringIO(text))
    return reader.fieldnames, list(reader)


# ---------------------------------------------------------------- events.csv

def test_events_csv_shape_and_headers():
    db = SessionLocal()
    _cleanup(db)
    cam = f"{A4}-cam-{uuid.uuid4().hex[:6]}"
    _seed_event(db, cam)
    _seed_event(db, cam, zone="ZONE_B")
    _seed_event(db, f"{A4}-other")
    db.close()

    client = _make_client()
    resp = client.get("/exports/events.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "events.csv" in resp.headers["content-disposition"]

    fieldnames, rows = _parse_csv(resp.text)
    assert fieldnames == [
        "id", "camera_id", "track_id", "objects", "confidence",
        "zone", "decision", "alerts", "timestamp",
    ]
    mine = [r for r in rows if r["camera_id"].startswith(A4)]
    assert len(mine) == 3
    assert {r["camera_id"] for r in mine} == {cam, f"{A4}-other"}


def test_events_csv_filter_respected():
    client = _make_client()
    db = SessionLocal()
    cam_keep = f"{A4}-keep-{uuid.uuid4().hex[:6]}"
    cam_drop = f"{A4}-drop-{uuid.uuid4().hex[:6]}"
    _seed_event(db, cam_keep)
    _seed_event(db, cam_drop)
    db.close()

    resp = client.get("/exports/events.csv", params={"camera_id": cam_keep})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    mine = [r for r in rows if r["camera_id"].startswith(A4)]
    assert mine, "expected at least one exported row"
    assert all(r["camera_id"] == cam_keep for r in mine)


# ---------------------------------------------------------------- activity.csv

def test_activity_csv_shape_and_filter():
    db = SessionLocal()
    _cleanup(db)
    day = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    _seed_activity(db, f"{A4}e1", day, n=2, strain=85)
    _seed_activity(db, f"{A4}e2", day, n=1, strain=20)
    db.close()

    client = _make_client()
    resp = client.get("/exports/activity.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "activity.csv" in resp.headers["content-disposition"]

    fieldnames, rows = _parse_csv(resp.text)
    assert "strain_level" in fieldnames
    assert "employee_id" in fieldnames
    mine = [r for r in rows if r["employee_id"].startswith(A4)]
    assert len(mine) == 3

    resp = client.get("/exports/activity.csv", params={"employee_id": f"{A4}e1"})
    _, rows = _parse_csv(resp.text)
    mine = [r for r in rows if r["employee_id"].startswith(A4)]
    assert len(mine) == 2
    assert all(r["employee_id"] == f"{A4}e1" for r in mine)
    assert all(r["strain_level"] == "red" for r in mine)  # seeded strain 85


# ---------------------------------------------------------------- focus.csv

def test_focus_csv_scores_seeded_day():
    db = SessionLocal()
    _cleanup(db)
    today = datetime.now(timezone.utc)
    day_start = today.replace(hour=9, minute=0, second=0, microsecond=0)
    # Four 60-minute focused coding blocks -> high focus score (>= 95).
    rows = []
    for i in range(4):
        start = day_start + timedelta(minutes=i * 90)
        rows.append(
            ActivityLog(
                machine_id=f"{A4}-m1",
                employee_id=f"{A4}e3",
                office_id="hq",
                event_type="activity",
                app_name="code",
                window_title="editor",
                process_name="code.exe",
                start_time=start,
                end_time=start + timedelta(minutes=60),
                keystrokes=100,
                mouse_clicks=10,
                is_idle=False,
                strain_score=30,
            )
        )
    db.add_all(rows)
    db.commit()
    db.close()

    client = _make_client()
    today_str = today.date().isoformat()
    resp = client.get(
        "/exports/focus.csv",
        params={"employee_id": f"{A4}e3", "start_date": today_str, "end_date": today_str},
    )
    assert resp.status_code == 200
    assert f"focus-{A4}e3.csv" in resp.headers["content-disposition"]

    fieldnames, rows = _parse_csv(resp.text)
    assert fieldnames[0] == "employee_id"
    assert "focus_score" in fieldnames
    assert len(rows) == 1
    assert rows[0]["employee_id"] == f"{A4}e3"
    assert int(rows[0]["focus_score"]) >= 95
    assert int(rows[0]["focused_blocks"]) == 4


def test_focus_csv_rejects_reversed_range():
    client = _make_client()
    resp = client.get(
        "/exports/focus.csv",
        params={
            "employee_id": f"{A4}e3",
            "start_date": "2026-10-30",
            "end_date": "2026-10-20",
        },
    )
    assert resp.status_code == 422
