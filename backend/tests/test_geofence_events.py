"""
Unit and integration tests for Geofence Events Ingestion, Alert Dispatching,
and Live Occupancy API (Stage 3 of Build #2).

Covers:
1. Event Ingestion & Querying (POST /api/zones/events, GET /api/zones/events)
2. Live Occupancy Calculation (GET /api/zones/occupancy)
3. Alert Dispatch Integration (dispatch_alert called on alert_triggered)
4. Backend Privacy Mode Gate (strips identity and anonymizes label)
5. Vision-to-Backend Emission helper
"""

from datetime import datetime, timedelta, timezone
import os
import sys
from pathlib import Path
import uuid
import pytest
from fastapi.testclient import TestClient

# Ensure repo root is on sys.path for importing vision modules
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.auth import API_KEY_ENV_VAR, API_KEY_HEADER
from app.database import engine, init_db
from app.geofence_models import GeofenceEvent, GeofenceZone
from app.main import app

init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _create_zone(client, name="Lab Zone", zone_type="restricted", alert_on_enter=True, alert_on_exit=False, office_id="hq", camera_id="CAM_01"):
    payload = {
        "name": f"{name} {uuid.uuid4().hex[:6]}",
        "office_id": office_id,
        "camera_id": camera_id,
        "zone_type": zone_type,
        "polygon": [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]],
        "alert_on_enter": alert_on_enter,
        "alert_on_exit": alert_on_exit,
    }
    resp = client.post("/api/zones", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Event Ingestion & Querying
# ---------------------------------------------------------------------------


def test_ingest_single_geofence_event(client):
    zone = _create_zone(client)
    event_payload = {
        "zone_id": zone["id"],
        "transition_type": "ZONE_ENTER",
        "track_id": 4,
        "camera_id": "CAM_01",
        "person_label": "Alice",
        "identity_name": "Alice",
        "identity_id": "user-alice-123",
        "dwell_seconds": 0.0,
        "alert_triggered": True,
    }

    resp = client.post("/api/zones/events", json=event_payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["id"] is not None
    assert data["zone_id"] == zone["id"]
    assert data["zone_name"] == zone["name"]
    assert data["zone_type"] == zone["zone_type"]
    assert data["transition_type"] == "ZONE_ENTER"
    assert data["track_id"] == 4
    assert data["person_label"] == "Alice"
    assert data["identity_name"] == "Alice"
    assert data["alert_triggered"] is True

    # Query events
    list_resp = client.get(f"/api/zones/events?zone_id={zone['id']}")
    assert list_resp.status_code == 200
    events = list_resp.json()
    assert len(events) >= 1
    assert events[0]["id"] == data["id"]


def test_ingest_batch_geofence_events(client):
    zone = _create_zone(client)
    events_payload = [
        {
            "zone_id": zone["id"],
            "transition_type": "ZONE_ENTER",
            "track_id": 10,
            "person_label": "Person 10",
        },
        {
            "zone_id": zone["id"],
            "transition_type": "DWELL_EXCEEDED",
            "track_id": 10,
            "dwell_seconds": 65.0,
            "person_label": "Person 10",
        },
        {
            "zone_id": zone["id"],
            "transition_type": "ZONE_EXIT",
            "track_id": 10,
            "dwell_seconds": 72.0,
            "person_label": "Person 10",
        },
    ]

    resp = client.post("/api/zones/events", json=events_payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 3
    assert [e["transition_type"] for e in data] == [
        "ZONE_ENTER",
        "DWELL_EXCEEDED",
        "ZONE_EXIT",
    ]


def test_query_events_with_filters(client):
    office = f"off-{uuid.uuid4().hex[:6]}"
    zone = _create_zone(client, office_id=office, camera_id="CAM_A")

    # Ingest 1 alert event and 1 non-alert event
    client.post(
        "/api/zones/events",
        json={
            "zone_id": zone["id"],
            "transition_type": "ZONE_ENTER",
            "track_id": 1,
            "alert_triggered": True,
            "office_id": office,
        },
    )
    client.post(
        "/api/zones/events",
        json={
            "zone_id": zone["id"],
            "transition_type": "ZONE_EXIT",
            "track_id": 1,
            "alert_triggered": False,
            "office_id": office,
        },
    )

    # Filter by alert_triggered=true
    resp_alerts = client.get(f"/api/zones/events?office_id={office}&alert_triggered=true")
    assert resp_alerts.status_code == 200
    alerts = resp_alerts.json()
    assert len(alerts) == 1
    assert alerts[0]["transition_type"] == "ZONE_ENTER"

    # Filter by transition_type=ZONE_EXIT
    resp_exit = client.get(f"/api/zones/events?office_id={office}&transition_type=ZONE_EXIT")
    assert resp_exit.status_code == 200
    exits = resp_exit.json()
    assert len(exits) == 1
    assert exits[0]["alert_triggered"] is False


# ---------------------------------------------------------------------------
# 2. Live Zone Occupancy Calculation
# ---------------------------------------------------------------------------


def test_live_zone_occupancy_lifecycle(client):
    office = f"occ-{uuid.uuid4().hex[:6]}"
    zone = _create_zone(client, name="Secure Room", office_id=office, camera_id="CAM_OCC")
    zone_id = zone["id"]

    # Initial occupancy: 0 occupants
    resp0 = client.get(f"/api/zones/occupancy?office_id={office}")
    assert resp0.status_code == 200
    occ0 = {z["zone_id"]: z for z in resp0.json()}
    assert zone_id in occ0
    assert occ0[zone_id]["current_count"] == 0
    assert len(occ0[zone_id]["occupants"]) == 0

    # Person 1 enters
    client.post(
        "/api/zones/events",
        json={
            "zone_id": zone_id,
            "transition_type": "ZONE_ENTER",
            "track_id": 1,
            "person_label": "Person 1",
            "office_id": office,
        },
    )

    # Occupancy should be 1
    resp1 = client.get(f"/api/zones/occupancy?office_id={office}")
    occ1 = {z["zone_id"]: z for z in resp1.json()}[zone_id]
    assert occ1["current_count"] == 1
    assert len(occ1["occupants"]) == 1
    assert occ1["occupants"][0]["track_id"] == 1

    # Person 2 enters
    client.post(
        "/api/zones/events",
        json={
            "zone_id": zone_id,
            "transition_type": "ZONE_ENTER",
            "track_id": 2,
            "person_label": "Person 2",
            "office_id": office,
        },
    )

    # Occupancy should be 2
    resp2 = client.get(f"/api/zones/occupancy?office_id={office}")
    occ2 = {z["zone_id"]: z for z in resp2.json()}[zone_id]
    assert occ2["current_count"] == 2
    occupant_tracks = {o["track_id"] for o in occ2["occupants"]}
    assert occupant_tracks == {1, 2}

    # Person 1 exits
    client.post(
        "/api/zones/events",
        json={
            "zone_id": zone_id,
            "transition_type": "ZONE_EXIT",
            "track_id": 1,
            "person_label": "Person 1",
            "office_id": office,
        },
    )

    # Occupancy should now be 1 (only Person 2 remains)
    resp3 = client.get(f"/api/zones/occupancy?office_id={office}")
    occ3 = {z["zone_id"]: z for z in resp3.json()}[zone_id]
    assert occ3["current_count"] == 1
    assert occ3["occupants"][0]["track_id"] == 2

    # Person 2 exits
    client.post(
        "/api/zones/events",
        json={
            "zone_id": zone_id,
            "transition_type": "ZONE_EXIT",
            "track_id": 2,
            "person_label": "Person 2",
            "office_id": office,
        },
    )

    # Occupancy returns to 0
    resp4 = client.get(f"/api/zones/occupancy?office_id={office}")
    occ4 = {z["zone_id"]: z for z in resp4.json()}[zone_id]
    assert occ4["current_count"] == 0
    assert len(occ4["occupants"]) == 0


# ---------------------------------------------------------------------------
# 3. Alert Dispatch Integration
# ---------------------------------------------------------------------------


def test_alert_dispatch_on_geofence_events(monkeypatch, client):
    dispatched_alerts = []

    def fake_dispatch(kind, payload=None):
        dispatched_alerts.append((kind, payload or {}))

    monkeypatch.setattr("app.geofence.dispatch_alert", fake_dispatch)

    zone = _create_zone(client, name="Restricted Armory", alert_on_enter=True)

    # 1. Event with alert_triggered=True -> fires dispatch_alert
    resp = client.post(
        "/api/zones/events",
        json={
            "zone_id": zone["id"],
            "transition_type": "ZONE_ENTER",
            "track_id": 5,
            "person_label": "Person 5",
            "alert_triggered": True,
        },
    )
    assert resp.status_code == 201
    assert len(dispatched_alerts) == 1
    kind, payload = dispatched_alerts[0]
    assert "ZONE_ENTER" in kind
    assert payload["zone_id"] == zone["id"]
    assert payload["track_id"] == 5

    # 2. Event with alert_triggered=False -> does NOT fire dispatch_alert
    dispatched_alerts.clear()
    resp_quiet = client.post(
        "/api/zones/events",
        json={
            "zone_id": zone["id"],
            "transition_type": "ZONE_EXIT",
            "track_id": 5,
            "person_label": "Person 5",
            "alert_triggered": False,
        },
    )
    assert resp_quiet.status_code == 201
    assert len(dispatched_alerts) == 0


# ---------------------------------------------------------------------------
# 4. Backend Privacy Mode Gate
# ---------------------------------------------------------------------------


def test_backend_privacy_mode_enforcement(monkeypatch, client):
    # Enable privacy mode on backend via env var
    monkeypatch.setenv("WAMSS_PRIVACY_MODE", "1")

    zone = _create_zone(client, name="Private Zone")

    # Ingest event containing named identity
    resp = client.post(
        "/api/zones/events",
        json={
            "zone_id": zone["id"],
            "transition_type": "ZONE_ENTER",
            "track_id": 42,
            "person_label": "Bob Smith",
            "identity_name": "Bob Smith",
            "identity_id": "bob-secret-uuid",
        },
    )
    assert resp.status_code == 201
    event = resp.json()

    # In Privacy Mode: identity_id and identity_name MUST be stripped to None
    # and person_label MUST be coerced to "Person 42"
    assert event["identity_name"] is None
    assert event["identity_id"] is None
    assert event["person_label"] == "Person 42"

    # Confirm directly in the database
    with engine.begin() as conn:
        from sqlalchemy import text
        row = conn.execute(
            text(f"SELECT person_label, identity_name, identity_id FROM geofence_events WHERE id = {event['id']}")
        ).mappings().first()
        assert row["person_label"] == "Person 42"
        assert row["identity_name"] is None
        assert row["identity_id"] is None


# ---------------------------------------------------------------------------
# 5. Vision-to-Backend Emission Helper
# ---------------------------------------------------------------------------


def test_vision_post_geofence_events_helper():
    from vision.geofence_evaluator import GeofenceEvent
    from vision.multicamera import _post_geofence_events

    posted = []

    class FakeResponse:
        status_code = 201
        def raise_for_status(self):
            pass

    def fake_post(url, json=None, headers=None, timeout=None):
        posted.append((url, json, headers))
        return FakeResponse()

    gf_event = GeofenceEvent(
        event_type="ZONE_ENTER",
        zone_id=1,
        zone_name="Test Zone",
        zone_type="restricted",
        track_id="9",
        person_label="Person 9",
        identity_name=None,
        identity_id=None,
        camera_id="CAM_01",
        timestamp=100.0,
        dwell_seconds=0.0,
        alert_triggered=True,
    )

    ok = _post_geofence_events(
        [gf_event],
        backend_events_url="http://localhost:8000/api/zones/events",
        headers={"X-API-Key": "secret"},
        http_post=fake_post,
    )
    assert ok is True
    assert len(posted) == 1
    url, body, headers = posted[0]
    assert url == "http://localhost:8000/api/zones/events"
    assert body[0]["zone_id"] == 1
    assert body[0]["transition_type"] == "ZONE_ENTER"
    assert body[0]["track_id"] == 9
    assert headers["X-API-Key"] == "secret"
