"""Focused API tests for issues #6-8; intentionally not executed in this task."""

import hashlib
import json
import uuid
from datetime import datetime, time, timedelta, timezone

from app import activity, attendance
from app.database import SessionLocal
from app.models import DetectionEvent, FaceRecord
from app.activity_models import ActivityLog


def _employee_headers(monkeypatch, employee_id: str):
    token = f"test-employee-token:{employee_id}"
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        json.dumps({employee_id: hashlib.sha256(token.encode()).hexdigest()}),
    )
    return {"X-Employee-Token": token}


def _base_record(employee_id: str, machine_id: str, start: datetime, end: datetime, **extra):
    return {
        "machine_id": machine_id,
        "employee_id": employee_id,
        "office_id": "hq",
        "event_type": "activity",
        "app_name": "Excel",
        "window_title": "Workbook",
        "process_name": "excel.exe",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "keystrokes": 0,
        "mouse_clicks": 0,
        "is_idle": False,
        **extra,
    }


def test_activity_ingest_deduplicates_replayed_record_ids(client, monkeypatch):
    now = datetime.now(timezone.utc)
    record = _base_record(
        f"employee-{uuid.uuid4().hex}", f"machine-{uuid.uuid4().hex}",
        now - timedelta(minutes=1), now, record_id=str(uuid.uuid4()),
    )

    headers = _employee_headers(monkeypatch, record["employee_id"])
    first = client.post("/activity", json={"records": [record]}, headers=headers)
    retry = client.post("/activity", json={"records": [record]}, headers=headers)

    assert first.status_code == 201, first.text
    assert first.json()["stored"] == 1
    assert retry.status_code == 201, retry.text
    assert retry.json()["received"] == 1
    assert retry.json()["stored"] == 0
    assert retry.json()["duplicates"] == 1


def test_daily_app_usage_uses_configured_categories_and_neutral_fallback(client, tmp_path, monkeypatch):
    category_file = tmp_path / "app-categories.json"
    category_file.write_text(json.dumps({"EXCEL.EXE": "work"}), encoding="utf-8")
    monkeypatch.setattr(activity, "APP_CATEGORY_PATH", category_file)

    day = datetime.now(timezone.utc).date()
    start = datetime.combine(day, time(10, 0), tzinfo=timezone.utc)
    employee_id = f"employee-{uuid.uuid4().hex}"
    machine_id = f"machine-{uuid.uuid4().hex}"
    rows = [
        _base_record(employee_id, machine_id, start, start + timedelta(minutes=12), record_id=str(uuid.uuid4())),
        _base_record(
            employee_id, machine_id, start + timedelta(minutes=20), start + timedelta(minutes=25),
            record_id=str(uuid.uuid4()), app_name="Unknown editor", process_name="mystery.exe",
        ),
    ]
    created = client.post(
        "/activity", json={"records": rows},
        headers=_employee_headers(monkeypatch, employee_id),
    )
    assert created.status_code == 201, created.text

    response = client.get(
        "/activity/app-usage",
        params={"day": day.isoformat(), "employee_id": employee_id, "machine_id": machine_id},
        headers=_employee_headers(monkeypatch, employee_id),
    )

    assert response.status_code == 200, response.text
    by_app = {item["app_name"].casefold(): item for item in response.json()}
    assert by_app["excel.exe"]["category"] == "work"
    assert by_app["excel.exe"]["minutes"] == 12.0
    assert by_app["mystery.exe"]["category"] == "neutral"
    assert by_app["mystery.exe"]["minutes"] == 5.0


def test_daily_attendance_is_opt_in_and_combines_only_enrolled_camera_and_active_computer(client, tmp_path, monkeypatch):
    monkeypatch.setattr(attendance, "ATTENDANCE_ENABLED", False)
    employee_id = f"employee-{uuid.uuid4().hex}"
    headers = _employee_headers(monkeypatch, employee_id)
    disabled = client.get("/attendance/daily", headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["items"] == []

    machine_id = f"machine-{uuid.uuid4().hex}"
    name = f"Person {uuid.uuid4().hex}"
    day = datetime.now(timezone.utc).date()
    start = datetime.combine(day, time(11, 0), tzinfo=timezone.utc)
    identity_map = tmp_path / "attendance-map.json"
    identity_map.write_text(json.dumps({name: employee_id}), encoding="utf-8")
    monkeypatch.setattr(attendance, "ATTENDANCE_IDENTITY_MAP_PATH", identity_map)
    monkeypatch.setattr(attendance, "ATTENDANCE_ENABLED", True)
    consent_file = tmp_path / "camera-consents.json"
    consent_file.write_text(json.dumps({employee_id: {
        "consented": True,
        "recorded_at": "2026-11-14T10:00:00Z",
        "camera_ids": ["CAM_TEST"],
    }}), encoding="utf-8")
    monkeypatch.setenv("WAMSS_ATTENDANCE_IDENTITY_MAP", str(identity_map))
    monkeypatch.setenv("WAMSS_ATTENDANCE_CAMERA_CONSENT_FILE", str(consent_file))
    monkeypatch.setenv("WAMSS_CAMERA_PRIVACY_MODES", json.dumps({"CAM_TEST": "identity"}))

    db = SessionLocal()
    try:
        db.add(FaceRecord(name=name, embedding=None, photo_path=None, face_available=False, enrolled_at=start))
        db.add(DetectionEvent(
            camera_id="CAM_TEST", track_id="known-track", objects=["person"], confidence=0.99,
            zone="OFFICE", decision="ALLOW", alerts=[], timestamp=start,
            person_name=name, person_name_confidence=0.95, dwell_seconds=60,
        ))
        db.add(DetectionEvent(
            camera_id="CAM_TEST", track_id="unknown-track", objects=["person"], confidence=0.99,
            zone="OFFICE", decision="ALLOW", alerts=[], timestamp=start + timedelta(minutes=2),
            person_name="unknown", person_name_confidence=0.99, dwell_seconds=60,
        ))
        db.add(ActivityLog(
            record_id=str(uuid.uuid4()), machine_id=machine_id, employee_id=employee_id,
            office_id="hq", event_type="activity", app_name="Excel", process_name="excel.exe",
            window_title="", start_time=start, end_time=start + timedelta(minutes=10),
            keystrokes=0, mouse_clicks=0, is_idle=False,
        ))
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/attendance/daily", params={"day": day.isoformat()}, headers=headers
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is True
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["employee_id"] == employee_id
    assert item["sources"] == ["camera", "computer"]
    assert item["camera_minutes"] == 1.0
    assert item["computer_minutes"] == 10.0
    assert item["present_minutes"] == 10.0
