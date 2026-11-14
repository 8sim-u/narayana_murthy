"""Privacy regressions for employee-only geofence transition history."""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.database import SessionLocal
from app.edge import process_geofence_position
from app.models import GeofenceEvent, GeofenceZone


def _employee_headers(monkeypatch, employee_tokens):
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        json.dumps({
            employee_id: hashlib.sha256(token.encode("utf-8")).hexdigest()
            for employee_id, token in employee_tokens.items()
        }),
    )
    return {
        employee_id: {"X-Employee-Token": token}
        for employee_id, token in employee_tokens.items()
    }


def test_geofence_history_requires_employee_token_and_hides_other_and_legacy_rows(
    client, monkeypatch,
):
    suffix = uuid4().hex
    employee_a = f"geofence-owner-a-{suffix}"
    employee_b = f"geofence-owner-b-{suffix}"
    headers = _employee_headers(
        monkeypatch,
        {employee_a: f"token-a-{suffix}", employee_b: f"token-b-{suffix}"},
    )
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    row_ids = []
    try:
        rows = [
            GeofenceEvent(
                camera_id=f"CAM_A_{suffix}", zone_name="A-zone",
                transition="entry", person_name="Employee A",
                employee_id=employee_a, timestamp=now,
            ),
            GeofenceEvent(
                camera_id=f"CAM_B_{suffix}", zone_name="B-zone",
                transition="exit", person_name="Employee B",
                employee_id=employee_b, timestamp=now,
            ),
            GeofenceEvent(
                camera_id=f"CAM_LEGACY_{suffix}", zone_name="Legacy-zone",
                transition="entry", person_name="Legacy Person",
                employee_id=None, timestamp=now,
            ),
        ]
        db.add_all(rows)
        db.commit()
        row_ids = [row.id for row in rows]

        assert client.get("/geofence-events").status_code == 401
        deployment_key_only = client.get(
            "/geofence-events", headers={"X-API-Key": "deployment-key-only"}
        )
        assert deployment_key_only.status_code == 401

        response = client.get("/geofence-events", headers=headers[employee_a])
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["vary"] == "X-Employee-Token"
        result = response.json()
        assert len(result) == 1
        assert result[0]["zone_name"] == "A-zone"
        assert result[0]["person_name"] == "Employee A"
        assert set(result[0]) == {"zone_name", "transition", "person_name", "timestamp"}
        assert "Employee B" not in response.text
        assert "Legacy Person" not in response.text
        assert "track_id" not in response.text
        assert "position_x" not in response.text
    finally:
        if row_ids:
            db.query(GeofenceEvent).filter(GeofenceEvent.id.in_(row_ids)).delete(
                synchronize_session=False
            )
            db.commit()
        db.close()


def test_geofence_transition_keeps_owner_and_global_alert_omits_identity(monkeypatch):
    from app import edge

    suffix = uuid4().hex
    camera_id = f"CAM_GEOFENCE_{suffix}"
    track_id = f"private-track-{suffix}"
    employee_id = f"stable-employee-{suffix}"
    db = SessionLocal()
    captured_alerts = []
    monkeypatch.setattr(
        edge,
        "dispatch_alert",
        lambda kind, payload: captured_alerts.append((kind, dict(payload))),
    )
    try:
        db.add(GeofenceZone(
            camera_id=camera_id,
            name="Private zone",
            polygon=[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
        ))
        db.commit()

        transitions = process_geofence_position(
            db,
            camera_id=camera_id,
            track_id=track_id,
            person_name="Employee A",
            employee_id=employee_id,
            x=0.5,
            y=0.5,
        )
        row = db.query(GeofenceEvent).filter(
            GeofenceEvent.employee_id == employee_id
        ).one()
        assert len(transitions) == 1
        assert transitions[0]["zone_name"] == "Private zone"
        assert transitions[0]["transition"] == "entry"
        assert row.person_name == "Employee A"
        assert row.employee_id == employee_id
        assert not {"track_id", "position_x", "position_y"}.intersection(
            GeofenceEvent.__table__.columns.keys()
        )

        assert len(captured_alerts) == 1
        _, payload = captured_alerts[0]
        assert set(payload) == {"camera_id", "zone_name", "transition", "timestamp"}
        assert "Employee A" not in str(payload)
        assert employee_id not in str(payload)
    finally:
        db.query(GeofenceEvent).filter(
            GeofenceEvent.employee_id == employee_id
        ).delete(synchronize_session=False)
        db.query(GeofenceZone).filter(GeofenceZone.camera_id == camera_id).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()
