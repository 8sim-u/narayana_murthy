"""Regression coverage for camera-scoped identity consent.

These tests are intentionally added without execution in this task.
"""

import json
import uuid

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DetectionEvent, FaceRecord


def _ids():
    suffix = uuid.uuid4().hex[:10]
    return f"Employee {suffix}", f"employee-{suffix}", f"CAM_{suffix}"


@pytest.fixture
def policy_files(tmp_path, monkeypatch):
    identity_map = tmp_path / "identity-map.json"
    consents = tmp_path / "camera-consents.json"
    modes = tmp_path / "camera-modes.json"
    identity_map.write_text("{}", encoding="utf-8")
    consents.write_text("{}", encoding="utf-8")
    modes.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("WAMSS_ATTENDANCE_IDENTITY_MAP", str(identity_map))
    monkeypatch.setenv("WAMSS_ATTENDANCE_CAMERA_CONSENT_FILE", str(consents))
    monkeypatch.setenv("WAMSS_CAMERA_PRIVACY_MODES", "{}")

    def configure(name, employee_id, camera_id, *, consented=True, cameras=None):
        identities = json.loads(identity_map.read_text(encoding="utf-8"))
        identities[name] = employee_id
        identity_map.write_text(json.dumps(identities), encoding="utf-8")
        mode_data = json.loads(modes.read_text(encoding="utf-8"))
        mode_data[camera_id] = "identity"
        modes.write_text(json.dumps(mode_data), encoding="utf-8")
        monkeypatch.setenv("WAMSS_CAMERA_PRIVACY_MODES", json.dumps(mode_data))
        records = json.loads(consents.read_text(encoding="utf-8"))
        records[employee_id] = {
            "consented": consented,
            "recorded_at": "2026-11-14T10:00:00Z",
            "camera_ids": list(cameras if cameras is not None else [camera_id]),
        }
        consents.write_text(json.dumps(records), encoding="utf-8")
        return identity_map, consents

    return configure, identity_map, consents, modes


def _post(client, camera_id, name, *, track="consent-track", **extra):
    return client.post("/event", json={
        "camera_id": camera_id,
        "track_id": track,
        "objects": ["person"],
        "confidence": 0.9,
        "zone": "UNKNOWN",
        "person_name": name,
        "person_name_confidence": 0.91,
        "snapshot_path": "snapshots/should-not-be-stored.jpg",
        **extra,
    })


def test_named_event_without_active_camera_consent_is_downgraded(client, policy_files):
    configure, _identity_map, consents, _modes = policy_files
    name, employee_id, camera_id = _ids()
    configure(name, employee_id, camera_id, consented=False)

    response = _post(client, camera_id, name)

    assert response.status_code == 201, response.text
    events = client.get("/events", params={"camera_id": camera_id}).json()
    assert len(events) == 1
    assert events[0]["person_name"] is None
    assert events[0]["person_name_confidence"] is None
    assert events[0]["snapshot_path"] is None
    assert name not in response.text


def test_camera_consent_does_not_authorize_another_camera(client, policy_files):
    configure, _identity_map, _consents, _modes = policy_files
    name, employee_id, camera_a = _ids()
    camera_b = camera_a + "_OTHER"
    configure(name, employee_id, camera_a, consented=True, cameras=[camera_a])
    # The second camera is explicitly in identity mode but is not in the consent scope.
    mode_data = {camera_a: "identity", camera_b: "identity"}
    import os
    os.environ["WAMSS_CAMERA_PRIVACY_MODES"] = json.dumps(mode_data)

    response = _post(client, camera_b, name)

    assert response.status_code == 201, response.text
    event = client.get("/events", params={"camera_id": camera_b}).json()[0]
    assert event["person_name"] is None
    assert event["person_name_confidence"] is None


def test_ambiguous_name_mapping_fails_closed(client, policy_files):
    _configure, identity_map, consents, modes = policy_files
    name, employee_id, camera_id = _ids()
    identity_map.write_text(json.dumps({name: employee_id, name.lower(): "other-id"}), encoding="utf-8")
    consents.write_text(json.dumps({employee_id: {
        "consented": True, "recorded_at": "2026-11-14T10:00:00Z",
        "camera_ids": [camera_id],
    }}), encoding="utf-8")
    modes.write_text(json.dumps({camera_id: "identity"}), encoding="utf-8")
    import os
    os.environ["WAMSS_CAMERA_PRIVACY_MODES"] = json.dumps({camera_id: "identity"})

    response = _post(client, camera_id, name)

    assert response.status_code == 201, response.text
    event = client.get("/events", params={"camera_id": camera_id}).json()[0]
    assert event["person_name"] is None
    assert event["person_name_confidence"] is None


def test_withdrawal_stops_future_named_storage_without_rewriting_history(client, policy_files):
    configure, _identity_map, consents, _modes = policy_files
    name, employee_id, camera_id = _ids()
    configure(name, employee_id, camera_id, consented=True)

    first = _post(client, camera_id, name, track="before-withdrawal")
    assert first.status_code == 201, first.text
    records = json.loads(consents.read_text(encoding="utf-8"))
    records[employee_id]["consented"] = False
    records[employee_id]["revoked_at"] = "2026-11-14T10:01:00Z"
    consents.write_text(json.dumps(records), encoding="utf-8")

    second = _post(client, camera_id, name, track="after-withdrawal")

    assert second.status_code == 201, second.text
    events = client.get("/events", params={"camera_id": camera_id}).json()
    by_track = {event["track_id"]: event for event in events}
    assert by_track["before-withdrawal"]["person_name"] == name
    assert by_track["after-withdrawal"]["person_name"] is None
    assert by_track["after-withdrawal"]["person_name_confidence"] is None


def test_withdrawal_scrubs_live_position_name(client, policy_files):
    configure, _identity_map, consents, _modes = policy_files
    name, employee_id, camera_id = _ids()
    configure(name, employee_id, camera_id, consented=True)
    response = _post(
        client, camera_id, name, track="live-position",
        position_x=0.3, position_y=0.4,
    )
    assert response.status_code == 201
    positions = client.get("/positions").json()
    before = next(item for item in positions
                  if item["camera_id"] == camera_id)
    assert before["person_name"] == name

    records = json.loads(consents.read_text(encoding="utf-8"))
    records[employee_id]["consented"] = False
    records[employee_id]["revoked_at"] = "2026-11-14T10:01:00Z"
    consents.write_text(json.dumps(records), encoding="utf-8")

    positions = client.get("/positions").json()
    after = next(item for item in positions
                 if item["camera_id"] == camera_id)
    assert after["person_name"] is None
    assert after["person_name_confidence"] is None


def test_aggregate_only_camera_rejects_named_event(client, policy_files):
    _configure, identity_map, consents, modes = policy_files
    name, employee_id, camera_id = _ids()
    identity_map.write_text(json.dumps({name: employee_id}), encoding="utf-8")
    consents.write_text(json.dumps({employee_id: {
        "consented": True, "recorded_at": "2026-11-14T10:00:00Z",
        "camera_ids": [camera_id],
    }}), encoding="utf-8")
    modes.write_text(json.dumps({camera_id: "aggregate"}), encoding="utf-8")
    import os
    os.environ["WAMSS_CAMERA_PRIVACY_MODES"] = json.dumps({camera_id: "aggregate"})

    response = _post(client, camera_id, name)

    assert response.status_code == 403
    assert name not in response.text
    assert client.get("/events", params={"camera_id": camera_id}).json() == []


def test_enrollment_and_gallery_require_active_camera_consent(client, policy_files, tmp_path, monkeypatch):
    configure, _identity_map, _consents, _modes = policy_files
    from app import edge

    name, employee_id, camera_id = _ids()
    configure(name, employee_id, camera_id, consented=False)
    monkeypatch.setattr(edge, "FACES_DIR", tmp_path / "faces")

    refused = client.post(
        "/faces/enroll",
        data={"name": name, "camera_id": camera_id},
        files={"photo": ("face.jpg", b"fake-image", "image/jpeg")},
    )
    assert refused.status_code == 403
    assert not list((tmp_path / "faces").glob("*")) if (tmp_path / "faces").exists() else True

    # Even an existing enrollment is not distributed to a camera without consent.
    db: Session = SessionLocal()
    try:
        db.add(FaceRecord(name=name, embedding=[0.1, 0.2], photo_path=None,
                          face_available=True))
        db.commit()
    finally:
        db.close()
    gallery = client.get("/faces/gallery", params={"camera_id": camera_id})
    assert gallery.status_code == 200
    assert all(item["name"] != name for item in gallery.json())
