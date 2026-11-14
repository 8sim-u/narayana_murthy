"""Tests for the WAMSS edge-computing expansion (app/edge.py).

Covers: POST /event with the new edge fields, backward compatibility of the
old payload, the live /positions registry (including 10s expiry),
/heatmap aggregation, /occupancy bucketing, face enrollment/listing, and
/snapshots file serving.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import edge
from app.database import SessionLocal
from app.main import app
from app.models import DetectionEvent
from camera_test_utils import grant_camera_consent

client = TestClient(app)


def _cam(prefix="CAM_EDGE"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _post_event(payload):
    name = payload.get("person_name")
    if isinstance(name, str) and name.strip().casefold() != "unknown":
        grant_camera_consent(name, payload["camera_id"])
    resp = client.post("/event", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------- /event


def test_event_post_with_all_edge_fields():
    """The full vision contract payload is accepted and persisted."""
    cam = _cam()
    payload = {
        "camera_id": cam,
        "track_id": "Person 1",
        "objects": ["person"],
        "confidence": 0.93,
        "zone": "UNKNOWN",
        "person_name": "Agastya Pandey",
        "position_x": 0.42,
        "position_y": 0.67,
        "snapshot_path": "snapshots/Person_1_20261102_120501.jpg",
        "dwell_seconds": 45.2,
    }
    data = _post_event(payload)
    assert data["received"] is True
    assert data["camera_id"] == cam

    detail = client.get(f"/events?camera_id={cam}&limit=1").json()[0]
    assert detail["person_name"] == "Agastya Pandey"
    assert detail["position_x"] == pytest.approx(0.42)
    assert detail["position_y"] == pytest.approx(0.67)
    assert detail["snapshot_path"] == "snapshots/Person_1_20261102_120501.jpg"
    assert detail["dwell_seconds"] == pytest.approx(45.2)


def test_event_post_without_edge_fields_still_works():
    """Old payloads without the new fields stay backward compatible."""
    cam = _cam()
    data = _post_event(
        {
            "camera_id": cam,
            "track_id": "legacy-1",
            "objects": ["person"],
            "confidence": 0.8,
            "zone": "UNKNOWN",
        }
    )
    assert data["received"] is True

    detail = client.get(f"/events?camera_id={cam}&limit=1").json()[0]
    assert detail["person_name"] is None
    assert detail["position_x"] is None
    assert detail["position_y"] is None
    assert detail["snapshot_path"] is None
    assert detail["dwell_seconds"] is None


# ---------------------------------------------------------------- /positions


def test_positions_registry_updated_on_event():
    """POST /event with coordinates updates the live position registry."""
    cam = _cam()
    _post_event(
        {
            "camera_id": cam,
            "track_id": "Person 7",
            "objects": ["person"],
            "confidence": 0.95,
            "zone": "UNKNOWN",
            "person_name": "Agastya Pandey",
            "position_x": 0.3,
            "position_y": 0.4,
        }
    )
    resp = client.get("/positions")
    assert resp.status_code == 200
    mine = [p for p in resp.json() if p["camera_id"] == cam and p["track_id"] == "Person 7"]
    assert len(mine) == 1
    entry = mine[0]
    assert entry["person_name"] == "Agastya Pandey"
    assert entry["x"] == pytest.approx(0.3)
    assert entry["y"] == pytest.approx(0.4)
    assert "last_seen" in entry


def test_positions_excludes_events_without_coordinates():
    """Events without position fields must not appear in /positions."""
    cam = _cam()
    _post_event(
        {
            "camera_id": cam,
            "track_id": "no-coords",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
        }
    )
    mine = [
        p for p in client.get("/positions").json()
        if p["camera_id"] == cam and p["track_id"] == "no-coords"
    ]
    assert mine == []


def test_positions_expire_after_ttl():
    """Registry entries older than 10s are dropped from /positions."""
    cam = _cam()
    _post_event(
        {
            "camera_id": cam,
            "track_id": "stale-track",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "position_x": 0.1,
            "position_y": 0.2,
        }
    )
    # Sanity: entry is live right after posting.
    assert any(
        p["track_id"] == "stale-track" for p in client.get("/positions").json()
    )

    # Age the entry past the 10s TTL, then re-query.
    key = (cam, "stale-track")
    edge._position_registry[key]["last_seen_monotonic"] = time.monotonic() - 11
    mine = [
        p for p in client.get("/positions").json()
        if p["camera_id"] == cam and p["track_id"] == "stale-track"
    ]
    assert mine == []


# ---------------------------------------------------------------- /heatmap


def test_heatmap_returns_points():
    """Stored positions aggregate into heatmap points."""
    cam = _cam()
    for i in range(3):
        _post_event(
            {
                "camera_id": cam,
                "track_id": f"hm-{i}",
                "objects": ["person"],
                "confidence": 0.9,
                "zone": "UNKNOWN",
                "position_x": 0.1 * (i + 1),
                "position_y": 0.2 * (i + 1),
            }
        )
    resp = client.get(f"/heatmap?camera_id={cam}&hours=24")
    assert resp.status_code == 200
    points = resp.json()
    assert len(points) >= 3
    xs = sorted(p["x"] for p in points)
    assert xs[:3] == pytest.approx([0.1, 0.2, 0.3])
    assert all(p["weight"] == 1 for p in points)


def test_heatmap_camera_filter_and_omitted():
    """camera_id filter narrows results; omitting it returns all cameras."""
    cam_a = _cam("CAM_HMA")
    cam_b = _cam("CAM_HMB")
    _post_event(
        {"camera_id": cam_a, "track_id": "a1", "objects": ["person"],
         "confidence": 0.9, "zone": "UNKNOWN", "position_x": 0.5, "position_y": 0.5}
    )
    _post_event(
        {"camera_id": cam_b, "track_id": "b1", "objects": ["person"],
         "confidence": 0.9, "zone": "UNKNOWN", "position_x": 0.6, "position_y": 0.6}
    )
    filtered = client.get(f"/heatmap?camera_id={cam_a}&hours=24").json()
    assert all("x" in p and "y" in p for p in filtered)
    assert len(filtered) >= 1
    unfiltered = client.get("/heatmap?hours=24").json()
    assert len(unfiltered) >= len(filtered)


def test_heatmap_ignores_events_without_positions():
    """Events without coordinates must not contribute heatmap points."""
    cam = _cam("CAM_HM_NONE")
    _post_event(
        {"camera_id": cam, "track_id": "np", "objects": ["person"],
         "confidence": 0.9, "zone": "UNKNOWN"}
    )
    points = client.get(f"/heatmap?camera_id={cam}&hours=24").json()
    assert points == []


# ---------------------------------------------------------------- /occupancy


def _bucket_start(ts: datetime, minutes: int) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    secs = minutes * 60
    start = (int(ts.timestamp()) // secs) * secs
    return datetime.fromtimestamp(start, tz=timezone.utc).isoformat()


def _insert_event(cam, track, ts):
    db = SessionLocal()
    try:
        db.add(
            DetectionEvent(
                camera_id=cam,
                track_id=track,
                objects=["person"],
                confidence=0.9,
                zone="UNKNOWN",
                decision="NO_RULE_TRIGGERED",
                alerts=[],
                timestamp=ts,
            )
        )
        db.commit()
    finally:
        db.close()


def test_occupancy_distinct_track_ids_per_bucket():
    """Distinct track_ids are counted once per bucket per camera."""
    cam = _cam("CAM_OCC")
    now = datetime.now(timezone.utc)
    base = now - timedelta(minutes=now.minute % 5, seconds=now.second, microseconds=now.microsecond)
    # Two events from the same track in one bucket, one from another track.
    _insert_event(cam, "P1", base + timedelta(minutes=1))
    _insert_event(cam, "P1", base + timedelta(minutes=2))
    _insert_event(cam, "P2", base + timedelta(minutes=3))

    resp = client.get(f"/occupancy?hours=1&bucket=5m&camera_id={cam}")
    assert resp.status_code == 200
    buckets = resp.json()
    expected_t = _bucket_start(base, 5)
    match = [b for b in buckets if b["t"] == expected_t]
    assert len(match) == 1
    assert match[0]["count"] == 2  # P1 and P2, P1 counted once
    assert match[0]["camera_id"] == cam


def test_occupancy_bucket_validation():
    """Unsupported bucket sizes are rejected with 422."""
    resp = client.get("/occupancy?hours=1&bucket=2h")
    assert resp.status_code == 422


def test_occupancy_total_when_camera_omitted():
    """Without camera_id the count is the total across cameras."""
    cam1 = _cam("CAM_OCCT1")
    cam2 = _cam("CAM_OCCT2")
    now = datetime.now(timezone.utc)
    base = now - timedelta(minutes=now.minute % 5, seconds=now.second, microseconds=now.microsecond)
    _insert_event(cam1, "PA", base + timedelta(minutes=1))
    _insert_event(cam2, "PB", base + timedelta(minutes=1))

    resp = client.get("/occupancy?hours=1&bucket=5m")
    assert resp.status_code == 200
    expected_t = _bucket_start(base, 5)
    # Find buckets whose t matches; total rows have camera_id null.
    totals = [b for b in resp.json() if b["t"] == expected_t and b["camera_id"] is None]
    assert len(totals) == 1
    assert totals[0]["count"] >= 2


# ---------------------------------------------------------------- faces


def test_face_enroll_list_and_gallery():
    """Enrollment is consent-scoped; unmatchable photos are not in any gallery."""
    name = f"Test Person {uuid.uuid4().hex[:6]}"
    camera_id = _cam("CAM_ENROLL")
    grant_camera_consent(name, camera_id)
    resp = client.post(
        "/faces/enroll",
        data={"name": name, "camera_id": camera_id},
        files={"photo": ("face.jpg", b"\xff\xd8\xff fake-jpeg-bytes", "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == name
    # face_recognition is not installed in this environment, so enrollment
    # stores the photo with the unavailable flag instead of a 500.
    assert body["status"] == "unavailable"

    faces = client.get("/faces").json()
    assert any(f["name"] == name and "enrolled_at" in f for f in faces)

    gallery = client.get("/faces/gallery", params={"camera_id": camera_id}).json()
    match = [g for g in gallery if g["name"] == name]
    assert match == []


def test_face_enroll_rejects_empty_photo():
    """An empty upload is rejected with 400, never a 500."""
    name = f"Nobody {uuid.uuid4().hex[:6]}"
    camera_id = _cam("CAM_ENROLL")
    grant_camera_consent(name, camera_id)
    resp = client.post(
        "/faces/enroll",
        data={"name": name, "camera_id": camera_id},
        files={"photo": ("empty.jpg", b"", "image/jpeg")},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------- snapshots


def test_snapshot_serve_and_traversal_guard():
    """Stored snapshots are served; traversal attempts are rejected."""
    (edge.SNAPSHOTS_DIR).mkdir(parents=True, exist_ok=True)
    filename = f"test_{uuid.uuid4().hex[:8]}.jpg"
    (edge.SNAPSHOTS_DIR / filename).write_bytes(b"\xff\xd8\xff fake")

    try:
        resp = client.get(f"/snapshots/{filename}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

        assert client.get("/snapshots/missing.jpg").status_code == 404
        assert client.get("/snapshots/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    finally:
        (edge.SNAPSHOTS_DIR / filename).unlink(missing_ok=True)


def test_positions_route_coexists_with_events_detail():
    """Sanity: /events/{id} routing is unaffected by the new routes."""
    resp = client.get("/events/99999999")
    assert resp.status_code == 404
