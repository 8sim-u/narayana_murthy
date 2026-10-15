import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app

client = TestClient(app)


def test_valid_ordinary_detection_event():
    """Test standard detection event outside restricted zones."""
    cam_id = f"CAM_{uuid.uuid4().hex[:8]}"
    payload = {
        "camera_id": cam_id,
        "track_id": "12",
        "objects": ["person"],
        "confidence": 0.91,
        "zone": "ZONE_OPEN_OFFICE",
    }
    response = client.post("/event", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["received"] is True
    assert data["camera_id"] == cam_id
    assert data["alerts"] == []
    assert data["decision"] == "NO_RULE_TRIGGERED"


def test_restricted_zone_event_and_alert():
    """Test restricted zone triggers RESTRICTED_ZONE_DETECTED and REVIEW_REQUIRED."""
    cam_id = f"CAM_{uuid.uuid4().hex[:8]}"
    payload = {
        "camera_id": cam_id,
        "track_id": "34",
        "objects": ["person"],
        "confidence": 0.95,
        "zone": "RESTRICTED",
    }
    response = client.post("/event", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["received"] is True
    assert data["camera_id"] == cam_id
    assert data["alerts"] == ["RESTRICTED_ZONE_DETECTED"]
    assert data["decision"] == "REVIEW_REQUIRED"


def test_unknown_zone_event():
    """Test event marked UNKNOWN is not assumed to be restricted."""
    cam_id = f"CAM_{uuid.uuid4().hex[:8]}"
    payload = {
        "camera_id": cam_id,
        "track_id": "56",
        "objects": ["person"],
        "confidence": 0.82,
        "zone": "UNKNOWN",
    }
    response = client.post("/event", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["received"] is True
    assert data["alerts"] == []
    assert data["decision"] == "NO_RULE_TRIGGERED"


def test_invalid_confidence_values():
    """Test validation errors for confidence values out of bounds (0-1)."""
    # Negative confidence
    resp_negative = client.post(
        "/event",
        json={
            "camera_id": "CAM_01",
            "track_id": "1",
            "objects": ["person"],
            "confidence": -0.05,
            "zone": "UNKNOWN",
        },
    )
    assert resp_negative.status_code == 422

    # Confidence exceeding 1.0
    resp_excess = client.post(
        "/event",
        json={
            "camera_id": "CAM_01",
            "track_id": "1",
            "objects": ["person"],
            "confidence": 1.05,
            "zone": "UNKNOWN",
        },
    )
    assert resp_excess.status_code == 422


def test_missing_required_fields():
    """Test validation errors when required payload fields are omitted."""
    # Missing camera_id
    r1 = client.post(
        "/event",
        json={
            "track_id": "1",
            "objects": ["person"],
            "confidence": 0.8,
            "zone": "UNKNOWN",
        },
    )
    assert r1.status_code == 422

    # Missing track_id
    r2 = client.post(
        "/event",
        json={
            "camera_id": "CAM_01",
            "objects": ["person"],
            "confidence": 0.8,
            "zone": "UNKNOWN",
        },
    )
    assert r2.status_code == 422

    # Missing confidence
    r3 = client.post(
        "/event",
        json={
            "camera_id": "CAM_01",
            "track_id": "1",
            "objects": ["person"],
            "zone": "UNKNOWN",
        },
    )
    assert r3.status_code == 422


def test_event_persistence_and_retrieval():
    """Test that posted events are saved in the database and can be fetched by ID."""
    cam_id = f"CAM_PERSIST_{uuid.uuid4().hex[:8]}"
    payload = {
        "camera_id": cam_id,
        "track_id": "TRACK_99",
        "objects": ["person"],
        "confidence": 0.93,
        "zone": "RESTRICTED",
    }
    post_resp = client.post("/event", json=payload)
    assert post_resp.status_code == 201

    # Fetch from /events list to obtain ID
    list_resp = client.get(f"/events?camera_id={cam_id}&limit=1")
    assert list_resp.status_code == 200
    events = list_resp.json()
    assert len(events) == 1
    event_id = events[0]["id"]

    # Fetch single event
    detail_resp = client.get(f"/events/{event_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == event_id
    assert detail["camera_id"] == cam_id
    assert detail["track_id"] == "TRACK_99"
    assert detail["objects"] == ["person"]
    assert detail["confidence"] == pytest.approx(0.93)
    assert detail["zone"] == "RESTRICTED"
    assert detail["decision"] == "REVIEW_REQUIRED"
    assert detail["alerts"] == ["RESTRICTED_ZONE_DETECTED"]
    assert "timestamp" in detail


def test_pagination_and_camera_filtering():
    """Test pagination limit/offset and filtering by camera_id."""
    cam_filter = f"CAM_FILTER_{uuid.uuid4().hex[:8]}"
    for i in range(5):
        client.post(
            "/event",
            json={
                "camera_id": cam_filter,
                "track_id": f"T_{i}",
                "objects": ["person"],
                "confidence": 0.85,
                "zone": "ZONE_A",
            },
        )

    # Filter by camera
    filtered_resp = client.get(f"/events?camera_id={cam_filter}")
    assert filtered_resp.status_code == 200
    events = filtered_resp.json()
    assert len(events) == 5
    assert all(e["camera_id"] == cam_filter for e in events)

    # Pagination: limit=2
    page1 = client.get(f"/events?camera_id={cam_filter}&limit=2&offset=0")
    assert page1.status_code == 200
    assert len(page1.json()) == 2

    # Pagination: limit=2, offset=2
    page2 = client.get(f"/events?camera_id={cam_filter}&limit=2&offset=2")
    assert page2.status_code == 200
    assert len(page2.json()) == 2

    # Ensure page1 and page2 don't overlap
    page1_ids = {e["id"] for e in page1.json()}
    page2_ids = {e["id"] for e in page2.json()}
    assert page1_ids.isdisjoint(page2_ids)


def test_not_found_behavior_for_event_ids():
    """Test 404 response when querying a non-existent event ID."""
    resp = client.get("/events/99999999")
    assert resp.status_code == 404
    data = resp.json()
    assert "not found" in data["detail"].lower()


def test_database_error_on_event_creation():
    """Test that database failures return HTTP 503 without leaking credentials or internal errors."""
    from sqlalchemy.exc import OperationalError

    class FailingSession:
        def add(self, obj):
            pass

        def commit(self):
            raise OperationalError("SELECT 1", {}, Exception("Connection failed"))

        def rollback(self):
            pass

        def close(self):
            pass

    def override_db():
        yield FailingSession()

    app.dependency_overrides[get_db] = override_db
    try:
        resp = client.post(
            "/event",
            json={
                "camera_id": "CAM_ERR",
                "track_id": "1",
                "objects": ["person"],
                "confidence": 0.9,
                "zone": "RESTRICTED",
            },
        )
        assert resp.status_code == 503
        data = resp.json()
        assert "database unavailable" in data["detail"].lower()
        # Verify no sensitive stack trace or connection string leaked
        assert "postgresql" not in resp.text.lower()
        assert "password" not in resp.text.lower()
    finally:
        app.dependency_overrides.clear()
