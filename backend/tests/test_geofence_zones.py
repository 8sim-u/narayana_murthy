"""
Tests for GeofenceZone model, schemas, and CRUD endpoints (/api/zones).

Stage 1 of Build #2 (Real Geofence Zones):
- Creating valid zones with coordinate polygons
- Polygon validation rules (minimum 3 vertices, normalized in [0.0, 1.0])
- Retrieval (list with filters, detail by ID)
- Partial updates via PATCH
- Deletion and 404 behavior
- Office and camera scoping/filtering
- API key authentication enforcement
"""

import os
import uuid
import pytest
from fastapi.testclient import TestClient

from app.auth import API_KEY_ENV_VAR, API_KEY_HEADER
from app.database import engine, init_db
from app.geofence_models import GeofenceZone
from app.main import app

# Ensure tables exist for tests
init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _sample_polygon():
    """Return a valid triangular polygon in normalized coordinates."""
    return [
        [0.1, 0.1],
        [0.5, 0.1],
        [0.3, 0.8],
    ]


# ---------------------------------------------------------------------------
# 1. Zone Creation & Retrieval (CRUD)
# ---------------------------------------------------------------------------


def test_create_valid_geofence_zone(client):
    payload = {
        "name": f"Server Room {uuid.uuid4().hex[:6]}",
        "office_id": "hq",
        "camera_id": "CAM_01",
        "zone_type": "restricted",
        "polygon": _sample_polygon(),
        "color": "#ef4444",
        "alert_on_enter": True,
        "alert_on_exit": False,
        "dwell_threshold_seconds": 300,
        "is_active": True,
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["id"] is not None
    assert data["name"] == payload["name"]
    assert data["office_id"] == "hq"
    assert data["camera_id"] == "CAM_01"
    assert data["zone_type"] == "restricted"
    assert data["polygon"] == payload["polygon"]
    assert data["color"] == "#ef4444"
    assert data["alert_on_enter"] is True
    assert data["alert_on_exit"] is False
    assert data["dwell_threshold_seconds"] == 300
    assert data["is_active"] is True
    assert "created_at" in data
    assert "updated_at" in data

    # Retrieve by ID
    get_resp = client.get(f"/api/zones/{data['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == data["id"]
    assert get_resp.json()["name"] == payload["name"]


def test_create_zone_default_color_and_fields(client):
    """When color is omitted, default color for zone_type is assigned."""
    payload = {
        "name": "Desk Area 1",
        "zone_type": "workstation",
        "polygon": _sample_polygon(),
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["office_id"] == "hq"
    assert data["camera_id"] is None
    assert data["zone_type"] == "workstation"
    assert data["color"] == "#3b82f6"  # default for workstation
    assert data["alert_on_enter"] is True
    assert data["alert_on_exit"] is False
    assert data["dwell_threshold_seconds"] is None
    assert data["is_active"] is True


# ---------------------------------------------------------------------------
# 2. Polygon and Field Validation Rejections (422)
# ---------------------------------------------------------------------------


def test_create_zone_rejects_fewer_than_three_vertices(client):
    payload = {
        "name": "Invalid Line Zone",
        "polygon": [[0.1, 0.1], [0.5, 0.5]],  # only 2 points
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 422


def test_create_zone_rejects_out_of_bounds_coordinates(client):
    # Coordinate > 1.0
    payload = {
        "name": "Out of Bounds Zone",
        "polygon": [[0.1, 0.1], [1.2, 0.5], [0.5, 0.8]],
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 422

    # Coordinate < 0.0
    payload_neg = {
        "name": "Negative Coord Zone",
        "polygon": [[-0.1, 0.1], [0.5, 0.5], [0.5, 0.8]],
    }
    response_neg = client.post("/api/zones", json=payload_neg)
    assert response_neg.status_code == 422


def test_create_zone_rejects_malformed_polygon_points(client):
    payload = {
        "name": "Malformed Point Zone",
        "polygon": [[0.1], [0.5, 0.5], [0.5, 0.8]],  # first point has only 1 coordinate
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 422


def test_create_zone_rejects_empty_name(client):
    payload = {
        "name": "   ",
        "polygon": _sample_polygon(),
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 422


def test_create_zone_rejects_invalid_zone_type(client):
    payload = {
        "name": "Bad Zone Type",
        "zone_type": "danger_zone",  # Not in allowed enum
        "polygon": _sample_polygon(),
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 422


def test_create_zone_rejects_negative_dwell_threshold(client):
    payload = {
        "name": "Negative Dwell",
        "dwell_threshold_seconds": -10,
        "polygon": _sample_polygon(),
    }
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 3. Partial Updates via PATCH
# ---------------------------------------------------------------------------


def test_patch_zone_fields_and_polygon(client):
    # Create initial zone
    created = client.post(
        "/api/zones",
        json={"name": "Initial Name", "polygon": _sample_polygon(), "is_active": True},
    ).json()
    zone_id = created["id"]

    # Partial update: name, is_active, and polygon
    new_poly = [[0.2, 0.2], [0.6, 0.2], [0.4, 0.9], [0.1, 0.5]]
    patch_resp = client.patch(
        f"/api/zones/{zone_id}",
        json={
            "name": "Updated Name",
            "is_active": False,
            "polygon": new_poly,
            "dwell_threshold_seconds": 120,
        },
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["id"] == zone_id
    assert updated["name"] == "Updated Name"
    assert updated["is_active"] is False
    assert updated["polygon"] == new_poly
    assert updated["dwell_threshold_seconds"] == 120

    # Ensure updated_at is tracked
    assert updated["updated_at"] is not None


def test_patch_zone_rejects_invalid_polygon(client):
    created = client.post(
        "/api/zones",
        json={"name": "Zone for Patch Test", "polygon": _sample_polygon()},
    ).json()
    zone_id = created["id"]

    # Try invalid polygon
    bad_poly = [[0.1, 0.1], [1.5, 0.5], [0.5, 0.5]]
    patch_resp = client.patch(f"/api/zones/{zone_id}", json={"polygon": bad_poly})
    assert patch_resp.status_code == 422


def test_patch_nonexistent_zone_returns_404(client):
    patch_resp = client.patch("/api/zones/999999", json={"name": "Ghost"})
    assert patch_resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Deletion & 404 Behavior
# ---------------------------------------------------------------------------


def test_delete_zone_and_subsequent_fetch(client):
    created = client.post(
        "/api/zones",
        json={"name": "To Delete", "polygon": _sample_polygon()},
    ).json()
    zone_id = created["id"]

    del_resp = client.delete(f"/api/zones/{zone_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["id"] == zone_id
    assert del_resp.json()["status"] == "deleted"

    # Subsequent fetch returns 404
    fetch_resp = client.get(f"/api/zones/{zone_id}")
    assert fetch_resp.status_code == 404

    # Second delete returns 404
    del_again = client.delete(f"/api/zones/{zone_id}")
    assert del_again.status_code == 404


# ---------------------------------------------------------------------------
# 5. Listing & Filtering (office_id, camera_id, is_active, zone_type)
# ---------------------------------------------------------------------------


def test_list_zones_with_filters(client):
    office_tag = f"off-{uuid.uuid4().hex[:6]}"
    # Zone 1: office_tag, CAM_10, restricted, active
    z1 = client.post(
        "/api/zones",
        json={
            "name": "Z1",
            "office_id": office_tag,
            "camera_id": "CAM_10",
            "zone_type": "restricted",
            "polygon": _sample_polygon(),
            "is_active": True,
        },
    ).json()

    # Zone 2: office_tag, CAM_20, workstation, active
    z2 = client.post(
        "/api/zones",
        json={
            "name": "Z2",
            "office_id": office_tag,
            "camera_id": "CAM_20",
            "zone_type": "workstation",
            "polygon": _sample_polygon(),
            "is_active": True,
        },
    ).json()

    # Zone 3: office_tag, CAM_10, entry_exit, inactive
    z3 = client.post(
        "/api/zones",
        json={
            "name": "Z3",
            "office_id": office_tag,
            "camera_id": "CAM_10",
            "zone_type": "entry_exit",
            "polygon": _sample_polygon(),
            "is_active": False,
        },
    ).json()

    # Filter by office_id
    all_office = client.get(f"/api/zones?office_id={office_tag}").json()
    ids_office = [z["id"] for z in all_office]
    assert z1["id"] in ids_office
    assert z2["id"] in ids_office
    assert z3["id"] in ids_office

    # Filter by office_id and camera_id
    cam10_zones = client.get(f"/api/zones?office_id={office_tag}&camera_id=CAM_10").json()
    cam10_ids = [z["id"] for z in cam10_zones]
    assert z1["id"] in cam10_ids
    assert z3["id"] in cam10_ids
    assert z2["id"] not in cam10_ids

    # Filter by is_active=true
    active_zones = client.get(f"/api/zones?office_id={office_tag}&is_active=true").json()
    active_ids = [z["id"] for z in active_zones]
    assert z1["id"] in active_ids
    assert z2["id"] in active_ids
    assert z3["id"] not in active_ids

    # Filter by zone_type=workstation
    ws_zones = client.get(f"/api/zones?office_id={office_tag}&zone_type=workstation").json()
    assert len(ws_zones) == 1
    assert ws_zones[0]["id"] == z2["id"]


# ---------------------------------------------------------------------------
# 6. API Key Authentication Enforcement
# ---------------------------------------------------------------------------


def test_geofence_zones_api_key_auth(monkeypatch, client):
    test_key = "test-secret-zone-key"
    monkeypatch.setenv(API_KEY_ENV_VAR, test_key)

    from app.auth import APIKeyAuthMiddleware
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.add_middleware(APIKeyAuthMiddleware, api_key=test_key)
    from app.geofence import router as zones_router
    test_app.include_router(zones_router)

    auth_client = TestClient(test_app)

    # 1. Missing key -> 401
    resp_unauth = auth_client.get("/api/zones")
    assert resp_unauth.status_code == 401
    assert "API key" in resp_unauth.json()["detail"]

    # 2. Wrong key -> 401
    resp_bad = auth_client.get("/api/zones", headers={API_KEY_HEADER: "wrong-key"})
    assert resp_bad.status_code == 401

    # 3. Valid key -> 200
    resp_ok = auth_client.get("/api/zones", headers={API_KEY_HEADER: test_key})
    assert resp_ok.status_code == 200
