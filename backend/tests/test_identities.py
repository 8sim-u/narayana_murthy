"""Tests for the persistent known-persons registry (app/edge.py identities).

Covers: enroll via upload and via snapshot path, DELETE removing the
person from the registry and the gallery, GET /identities reflecting
enrollments and sightings, GET /unknowns returning unknown sightings with
snapshot links, /positions carrying person_name_confidence, the gallery
version marker, and the additive migration for old databases.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app import edge
from app.database import SessionLocal, engine
from app.main import app
from app.models import FaceRecord, PersonIdentity

client = TestClient(app)

_FAKE_JPEG = b"\xff\xd8\xff fake-jpeg-bytes"


def _name(prefix="Ident"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def _cam(prefix="CAM_ID"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _post_event(payload):
    resp = client.post("/event", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _enroll_upload(name):
    resp = client.post(
        "/faces/enroll",
        data={"name": name},
        files={"photo": ("face.jpg", _FAKE_JPEG, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _identities():
    resp = client.get("/identities")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _identity_named(name):
    return [i for i in _identities() if i["name"] == name]


def _write_snapshot(filename):
    edge.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = edge.SNAPSHOTS_DIR / filename
    path.write_bytes(_FAKE_JPEG)
    return path


# ---------------------------------------------------------------- enroll


def test_enroll_via_upload_creates_registry_row():
    """Multipart enroll creates the gallery entry and the registry row."""
    name = _name()
    body = _enroll_upload(name)
    assert body["name"] == name
    assert body["status"] == "unavailable"
    assert isinstance(body.get("gallery_version"), int)
    assert body["gallery_version"] >= 1

    assert any(f["name"] == name for f in client.get("/faces").json())
    assert any(g["name"] == name for g in client.get("/faces/gallery").json())

    rows = _identity_named(name)
    assert len(rows) == 1
    row = rows[0]
    assert row["sightings"] == 0
    assert row["last_seen"] is None
    assert row["cameras"] == []
    assert row["enrolled_at"]


def test_enroll_via_snapshot_path():
    """JSON enroll from a review-queue snapshot works, prefixed or bare."""
    name = _name("Snap")
    filename = f"snap_{uuid.uuid4().hex[:8]}.jpg"
    path = _write_snapshot(filename)
    try:
        resp = client.post(
            "/faces/enroll",
            json={"name": name, "snapshot_path": f"snapshots/{filename}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == name
        assert len(_identity_named(name)) == 1
        assert any(f["name"] == name for f in client.get("/faces").json())

        name2 = _name("Snap")
        resp = client.post(
            "/faces/enroll", json={"name": name2, "snapshot_path": filename}
        )
        assert resp.status_code == 200, resp.text
        assert len(_identity_named(name2)) == 1
    finally:
        path.unlink(missing_ok=True)


def test_enroll_via_snapshot_path_rejects_bad_input():
    """Missing snapshot is 404, missing name or path is 422."""
    name = _name("SnapBad")
    resp = client.post(
        "/faces/enroll",
        json={"name": name, "snapshot_path": "snapshots/no_such_file.jpg"},
    )
    assert resp.status_code == 404

    resp = client.post("/faces/enroll", json={"name": name})
    assert resp.status_code == 422
    resp = client.post(
        "/faces/enroll", json={"snapshot_path": "snapshots/x.jpg"}
    )
    assert resp.status_code == 422
    resp = client.post(
        "/faces/enroll",
        json={"name": name, "snapshot_path": "../secret.jpg"},
    )
    assert resp.status_code == 400


def test_gallery_version_bumps_on_enroll():
    """GET /faces/gallery/version tracks enrollments."""
    before = client.get("/faces/gallery/version").json()
    _enroll_upload(_name("Ver"))
    after = client.get("/faces/gallery/version").json()
    assert after["version"] == before["version"] + 1
    assert after["count"] >= before["count"] + 1


# ---------------------------------------------------------------- delete


def test_delete_removes_registry_and_gallery():
    """DELETE drops the FaceRecord, the registry row, and the listing."""
    name = _name("Del")
    _enroll_upload(name)
    assert len(_identity_named(name)) == 1
    assert any(f["name"] == name for f in client.get("/faces").json())

    version_before = client.get("/faces/gallery/version").json()["version"]
    resp = client.delete(f"/faces/{name}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "deleted"
    assert resp.json()["gallery_version"] == version_before + 1

    assert _identity_named(name) == []
    assert not any(f["name"] == name for f in client.get("/faces").json())
    assert not any(g["name"] == name for g in client.get("/faces/gallery").json())

    db = SessionLocal()
    try:
        assert db.query(FaceRecord).filter(FaceRecord.name == name).first() is None
        assert (
            db.query(PersonIdentity).filter(PersonIdentity.name == name).first()
            is None
        )
    finally:
        db.close()

    assert client.delete(f"/faces/{name}").status_code == 404


# ---------------------------------------------------------------- identities


def test_identities_reflect_enroll_and_sightings():
    """Sightings bump last_seen, sightings, and cameras seen on."""
    name = _name("Seen")
    cam_a = _cam("CAM_SEEN_A")
    cam_b = _cam("CAM_SEEN_B")
    _enroll_upload(name)

    _post_event(
        {
            "camera_id": cam_a,
            "track_id": "t1",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": name,
            "position_x": 0.2,
            "position_y": 0.3,
        }
    )
    _post_event(
        {
            "camera_id": cam_b,
            "track_id": "t2",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": name,
        }
    )
    rows = _identity_named(name)
    assert len(rows) == 1
    row = rows[0]
    assert row["sightings"] == 2
    assert row["last_seen"] is not None
    assert sorted(row["cameras"]) == sorted([cam_a, cam_b])


def test_identities_ordered_most_recently_seen_first():
    """A sighted person sorts ahead of a never-seen enrollee."""
    seen = _name("OrdSeen")
    never = _name("OrdNever")
    _enroll_upload(seen)
    _enroll_upload(never)
    _post_event(
        {
            "camera_id": _cam("CAM_ORD"),
            "track_id": "ord-1",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": seen,
        }
    )
    names = [i["name"] for i in _identities()]
    assert names.index(seen) < names.index(never)


def test_unknown_sighting_creates_no_identity():
    """Events named "unknown" (or with no name) never enter the registry."""
    cam = _cam("CAM_UNK")
    _post_event(
        {
            "camera_id": cam,
            "track_id": "u1",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": "unknown",
        }
    )
    _post_event(
        {
            "camera_id": cam,
            "track_id": "u2",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
        }
    )
    names = {i["name"] for i in _identities()}
    assert "unknown" not in names
    assert "" not in names


def test_sighting_of_vision_only_person_creates_registry_row():
    """A known name with no registry row yet gets one on first sighting."""
    name = _name("VisionOnly")
    assert _identity_named(name) == []
    _post_event(
        {
            "camera_id": _cam("CAM_VIS"),
            "track_id": "v1",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": name,
        }
    )
    rows = _identity_named(name)
    assert len(rows) == 1
    assert rows[0]["sightings"] == 1
    assert rows[0]["last_seen"] is not None


# ---------------------------------------------------------------- unknowns


def test_unknowns_returns_snapshot_links():
    """Unknown sightings with snapshots are listed, most recent first."""
    cam = _cam("CAM_REV")
    files = []
    try:
        for track in ("rev-1", "rev-2"):
            filename = f"rev_{track}_{uuid.uuid4().hex[:6]}.jpg"
            files.append(_write_snapshot(filename))
            _post_event(
                {
                    "camera_id": cam,
                    "track_id": track,
                    "objects": ["person"],
                    "confidence": 0.88,
                    "zone": "UNKNOWN",
                    "person_name": "unknown",
                    "snapshot_path": f"snapshots/{filename}",
                }
            )
        # Unknown with no snapshot: excluded. Known person: excluded.
        _post_event(
            {
                "camera_id": cam,
                "track_id": "rev-nosnap",
                "objects": ["person"],
                "confidence": 0.9,
                "zone": "UNKNOWN",
                "person_name": "unknown",
            }
        )
        known = _name("RevKnown")
        _enroll_upload(known)
        _post_event(
            {
                "camera_id": cam,
                "track_id": "rev-known",
                "objects": ["person"],
                "confidence": 0.9,
                "zone": "UNKNOWN",
                "person_name": known,
                "snapshot_path": f"snapshots/{files[0].name}",
            }
        )

        resp = client.get(f"/unknowns?camera_id={cam}&limit=50")
        assert resp.status_code == 200, resp.text
        entries = resp.json()
        tracks = [e["track_id"] for e in entries]
        assert "rev-1" in tracks and "rev-2" in tracks
        assert "rev-nosnap" not in tracks
        assert "rev-known" not in tracks
        # Most recent first: rev-2 was posted after rev-1.
        assert tracks.index("rev-2") < tracks.index("rev-1")
        for e in entries:
            assert e["snapshot_url"].startswith("/snapshots/")
            assert e["snapshot_path"].startswith("snapshots/")
            assert e["camera_id"] == cam
            assert e["timestamp"]
            assert e["event_id"]
        # The snapshot link actually serves the file.
        first = entries[0]
        shot = client.get(first["snapshot_url"])
        assert shot.status_code == 200
    finally:
        for p in files:
            p.unlink(missing_ok=True)


def test_unknowns_deduplicates_by_track():
    """Repeat sightings of one track collapse to the most recent entry."""
    cam = _cam("CAM_DUP")
    files = []
    try:
        for i in range(2):
            filename = f"dup_{i}_{uuid.uuid4().hex[:6]}.jpg"
            files.append(_write_snapshot(filename))
            _post_event(
                {
                    "camera_id": cam,
                    "track_id": "dup-track",
                    "objects": ["person"],
                    "confidence": 0.9,
                    "zone": "UNKNOWN",
                    "person_name": "unknown",
                    "snapshot_path": f"snapshots/{filename}",
                }
            )
        entries = [
            e
            for e in client.get(f"/unknowns?camera_id={cam}&limit=50").json()
            if e["track_id"] == "dup-track"
        ]
        assert len(entries) == 1
        # Most recent sighting wins, so the second snapshot is linked.
        assert entries[0]["snapshot_path"].endswith(files[1].name)
    finally:
        for p in files:
            p.unlink(missing_ok=True)


def test_unknowns_accepts_missing_person_name():
    """An event with no person_name at all counts as unknown."""
    cam = _cam("CAM_NONAME")
    filename = f"noname_{uuid.uuid4().hex[:6]}.jpg"
    path = _write_snapshot(filename)
    try:
        _post_event(
            {
                "camera_id": cam,
                "track_id": "noname-1",
                "objects": ["person"],
                "confidence": 0.9,
                "zone": "UNKNOWN",
                "snapshot_path": f"snapshots/{filename}",
            }
        )
        entries = client.get(f"/unknowns?camera_id={cam}&limit=50").json()
        assert any(e["track_id"] == "noname-1" for e in entries)
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------- positions


def test_positions_carries_person_name_and_confidence():
    """Live positions include the identification name and confidence."""
    cam = _cam("CAM_POSC")
    name = _name("PosConf")
    _post_event(
        {
            "camera_id": cam,
            "track_id": "pc-1",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": name,
            "person_name_confidence": 0.87,
            "position_x": 0.4,
            "position_y": 0.5,
        }
    )
    mine = [
        p
        for p in client.get("/positions").json()
        if p["camera_id"] == cam and p["track_id"] == "pc-1"
    ]
    assert len(mine) == 1
    assert mine[0]["person_name"] == name
    assert mine[0]["person_name_confidence"] == pytest.approx(0.87)

    # No identification confidence reported: null, not zero.
    _post_event(
        {
            "camera_id": cam,
            "track_id": "pc-2",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": name,
            "position_x": 0.1,
            "position_y": 0.1,
        }
    )
    mine = [
        p
        for p in client.get("/positions").json()
        if p["camera_id"] == cam and p["track_id"] == "pc-2"
    ]
    assert len(mine) == 1
    assert mine[0]["person_name_confidence"] is None

    detail = client.get(f"/events?camera_id={cam}&limit=1").json()[0]
    assert detail["person_name_confidence"] is None


# ---------------------------------------------------------------- migration

def test_old_db_without_registry_still_loads():
    """Simulate a pre-registry database: drop the new table and column,
    run ensure_schema(), and confirm the new endpoints work again."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS person_identities"))
        cols = {c["name"] for c in inspect(conn).get_columns("detection_events")}
        if "person_name_confidence" in cols:
            conn.execute(
                text("ALTER TABLE detection_events DROP COLUMN person_name_confidence")
            )

    cols = {c["name"] for c in inspect(engine).get_columns("detection_events")}
    assert "person_name_confidence" not in cols
    assert "person_identities" not in inspect(engine).get_table_names()

    edge.ensure_schema()

    assert "person_identities" in inspect(engine).get_table_names()
    cols = {c["name"] for c in inspect(engine).get_columns("person_identities")}
    assert {"name", "enrolled_at", "last_seen", "sightings", "cameras"} <= cols
    assert (
        "person_name_confidence"
        in {c["name"] for c in inspect(engine).get_columns("detection_events")}
    )

    # Endpoints work on the migrated database.
    name = _name("Migrated")
    _enroll_upload(name)
    assert len(_identity_named(name)) == 1
    _post_event(
        {
            "camera_id": _cam("CAM_MIG"),
            "track_id": "mig-1",
            "objects": ["person"],
            "confidence": 0.9,
            "zone": "UNKNOWN",
            "person_name": name,
            "person_name_confidence": 0.77,
            "position_x": 0.5,
            "position_y": 0.5,
        }
    )
    assert _identity_named(name)[0]["sightings"] == 1
    assert client.get("/unknowns?limit=5").status_code == 200
