"""Per-person scoping tests for the wellness endpoints.

GET /wellness/me must return only the data of the employee bound to the
X-Employee-Token, and GET /wellness/manager-checkins must return only the
fixed vague prompt (employee ID plus one generic message) for opted-in
people on the manager roster, never any health value.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite:///./test_wamss.db"

from app.activity_models import ActivityLog
from app.database import SessionLocal, get_db
from app.wellness import router as wellness_router
from app.wellness_models import ManagerAlertConsent, WellnessConsent, WellnessDaily

PREFIX = "wellness_scope_"
VAGUE_MESSAGE = "A supportive check-in may be helpful."


def _db_override():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _client():
    app = FastAPI()
    app.include_router(wellness_router)
    app.dependency_overrides[get_db] = _db_override
    return TestClient(app)


def _clean(db):
    for model in (WellnessDaily, ManagerAlertConsent, WellnessConsent):
        db.query(model).filter(model.employee_id.like(f"{PREFIX}%")).delete(
            synchronize_session=False
        )
    db.query(ActivityLog).filter(
        ActivityLog.employee_id.like(f"{PREFIX}%")
    ).delete(synchronize_session=False)
    db.commit()


def _auth(monkeypatch, *employee_ids):
    hashes = {}
    headers = {}
    for employee_id in employee_ids:
        token = f"wellness-scope-token:{employee_id}"
        hashes[employee_id] = hashlib.sha256(token.encode()).hexdigest()
        headers[employee_id] = {"X-Employee-Token": token}
    monkeypatch.setenv("WAMSS_EMPLOYEE_TOKEN_HASHES", json.dumps(hashes))
    return headers


def _manager_auth(monkeypatch, roster, token="wellness-scope-manager-token"):
    digest = hashlib.sha256(token.encode()).hexdigest()
    monkeypatch.setenv(
        "WAMSS_MANAGER_ROSTER_TOKEN_HASHES", json.dumps({digest: list(roster)})
    )
    return {"X-Manager-Token": token}


def _add_consent(db, employee_id, alert=False):
    db.add(WellnessConsent(
        employee_id=employee_id, accepted=True, version="1",
        accepted_at=datetime.now(timezone.utc),
    ))
    if alert:
        db.add(ManagerAlertConsent(
            employee_id=employee_id, accepted=True, version="1",
            accepted_at=datetime.now(timezone.utc),
        ))


def _add_daily(db, employee_id, day, rmssd_ms, resting_hr_bpm=None):
    db.add(WellnessDaily(
        employee_id=employee_id, day=day, rmssd_ms=rmssd_ms,
        rmssd_source="manual", resting_hr_bpm=resting_hr_bpm,
    ))


def _add_activity(db, employee_id, day, minutes):
    start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
    db.add(ActivityLog(
        machine_id="WELLNESS_SCOPE_MACHINE", employee_id=employee_id,
        office_id="wellness_scope_office", event_type="activity",
        app_name="editor", window_title="test", process_name="test",
        start_time=start, end_time=start + timedelta(minutes=minutes),
        keystrokes=1, mouse_clicks=1, is_idle=False,
    ))


def _seed_decline(db, employee_id, today):
    """HRV decline plus engagement decline, the two manager-checkin triggers."""
    for offset in (6, 5, 4, 3):
        _add_daily(db, employee_id, today - timedelta(days=offset), 40.0)
    for offset in (13, 12, 11, 10):
        _add_daily(db, employee_id, today - timedelta(days=offset), 60.0)
    for offset in (13, 12, 11, 10):
        _add_activity(db, employee_id, today - timedelta(days=offset), 120)
    for offset in (6, 5, 4, 3):
        _add_activity(db, employee_id, today - timedelta(days=offset), 60)


def test_wellness_me_requires_employee_token(monkeypatch):
    self_id = PREFIX + "self"
    db = SessionLocal()
    _clean(db)
    db.close()
    _auth(monkeypatch, self_id)
    client = _client()
    assert client.get("/wellness/me").status_code == 401
    assert client.get("/wellness/me", headers={"X-Employee-Token": "wrong"}).status_code == 401


def test_wellness_me_returns_only_token_owner_data(monkeypatch):
    self_id, other_id = PREFIX + "self", PREFIX + "other"
    today = datetime.now(timezone.utc).date()
    db = SessionLocal()
    _clean(db)
    _add_consent(db, self_id)
    _add_consent(db, other_id)
    _add_daily(db, self_id, today - timedelta(days=1), 60.0, resting_hr_bpm=62)
    _add_daily(db, self_id, today - timedelta(days=2), 58.0)
    _add_daily(db, other_id, today - timedelta(days=1), 90.0, resting_hr_bpm=48)
    _add_activity(db, self_id, today, 60)
    _add_activity(db, other_id, today, 120)
    db.commit()
    db.close()

    headers = _auth(monkeypatch, self_id, other_id)
    client = _client()
    response = client.get("/wellness/me", params={"days": 7}, headers=headers[self_id])
    assert response.status_code == 200
    data = response.json()
    assert data["employee_id"] == self_id
    assert data["wellness"]["consented"] is True
    daily = data["wellness"]["daily"]
    assert {row["rmssd_ms"] for row in daily} == {60.0, 58.0}
    assert {row["resting_hr_bpm"] for row in daily} == {62, None}
    today_row = next(
        row for row in data["activity"] if row["day"] == today.isoformat()
    )
    assert today_row["focus_minutes"] == 60.0
    assert other_id not in response.text
    assert "90.0" not in response.text

    other_response = client.get(
        "/wellness/me", params={"days": 7}, headers=headers[other_id]
    )
    assert other_response.status_code == 200
    assert other_response.json()["employee_id"] == other_id
    assert {row["rmssd_ms"] for row in other_response.json()["wellness"]["daily"]} == {90.0}
    assert self_id not in other_response.text


def test_wellness_me_without_consent_hides_health(monkeypatch):
    self_id = PREFIX + "noconsent"
    db = SessionLocal()
    _clean(db)
    db.close()
    headers = _auth(monkeypatch, self_id)
    client = _client()
    response = client.get("/wellness/me", params={"days": 7}, headers=headers[self_id])
    assert response.status_code == 200
    data = response.json()
    assert data["employee_id"] == self_id
    assert data["wellness"]["consented"] is False
    assert data["wellness"]["daily"] == []
    assert data["wellness"]["hrv_trend"] is None
    assert data["wellness"]["live"] is None


def test_wellness_consent_withdrawal_deletes_health_history(monkeypatch):
    self_id = PREFIX + "withdraw"
    today = datetime.now(timezone.utc).date()
    db = SessionLocal()
    _clean(db)
    _add_consent(db, self_id)
    _add_daily(db, self_id, today - timedelta(days=1), 60.0)
    db.commit()
    db.close()

    headers = _auth(monkeypatch, self_id)
    client = _client()
    before = client.get("/wellness/me", params={"days": 7}, headers=headers[self_id])
    assert before.json()["wellness"]["daily"]

    response = client.post(
        "/wellness/consent", json={"accepted": False}, headers=headers[self_id]
    )
    assert response.status_code == 200
    assert response.json()["health_history_deleted"] is True

    after = client.get("/wellness/me", params={"days": 7}, headers=headers[self_id])
    assert after.json()["wellness"]["consented"] is False
    assert after.json()["wellness"]["daily"] == []
    db = SessionLocal()
    remaining = db.query(WellnessDaily).filter_by(employee_id=self_id).count()
    db.close()
    assert remaining == 0


def test_manager_checkins_returns_only_vague_hint_for_opted_in(monkeypatch):
    self_id, other_id = PREFIX + "optin", PREFIX + "nooptin"
    today = datetime.now(timezone.utc).date()
    db = SessionLocal()
    _clean(db)
    _add_consent(db, self_id, alert=True)
    _add_consent(db, other_id, alert=False)
    _seed_decline(db, self_id, today)
    _seed_decline(db, other_id, today)
    db.commit()
    db.close()

    _auth(monkeypatch, self_id, other_id)
    headers = _manager_auth(monkeypatch, [self_id, other_id])
    client = _client()
    response = client.get("/wellness/manager-checkins", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["checkins"] == [
        {"employee_id": self_id, "message": VAGUE_MESSAGE}
    ]
    assert set(data["checkins"][0].keys()) == {"employee_id", "message"}
    assert other_id not in response.text
    for leaked in ("40.0", "60.0", "rmssd", "decline", "hrv", "strain"):
        assert leaked not in response.text.lower()


def test_manager_checkins_requires_roster_token(monkeypatch):
    self_id = PREFIX + "optin"
    db = SessionLocal()
    _clean(db)
    db.close()
    _auth(monkeypatch, self_id)
    headers = _manager_auth(monkeypatch, [self_id])
    client = _client()
    assert client.get("/wellness/manager-checkins").status_code == 401
    assert client.get(
        "/wellness/manager-checkins", headers={"X-Manager-Token": "wrong"}
    ).status_code == 401
    assert client.get("/wellness/manager-checkins", headers=headers).status_code == 200


def test_manager_checkins_excludes_opted_in_employee_without_flag(monkeypatch):
    self_id = PREFIX + "flat"
    today = datetime.now(timezone.utc).date()
    db = SessionLocal()
    _clean(db)
    _add_consent(db, self_id, alert=True)
    for offset in (6, 5, 4, 3, 13, 12, 11, 10):
        _add_daily(db, self_id, today - timedelta(days=offset), 55.0)
    db.commit()
    db.close()

    _auth(monkeypatch, self_id)
    headers = _manager_auth(monkeypatch, [self_id])
    client = _client()
    response = client.get("/wellness/manager-checkins", headers=headers)
    assert response.status_code == 200
    assert response.json()["checkins"] == []
