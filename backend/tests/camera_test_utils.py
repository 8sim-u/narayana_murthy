"""Temporary policy-file helpers for backend tests; never use in deployment."""

import json
import os
import tempfile
import uuid
from pathlib import Path

_POLICY_DIR = Path(tempfile.mkdtemp(prefix="wamss-camera-consent-tests-"))
_IDENTITY_MAP = _POLICY_DIR / "identity-map.json"
_CONSENTS = _POLICY_DIR / "camera-consents.json"
_IDENTITY_MAP.write_text("{}", encoding="utf-8")
_CONSENTS.write_text("{}", encoding="utf-8")
os.environ["WAMSS_ATTENDANCE_IDENTITY_MAP"] = str(_IDENTITY_MAP)
os.environ["WAMSS_ATTENDANCE_CAMERA_CONSENT_FILE"] = str(_CONSENTS)


def grant_camera_consent(name, camera_id):
    """Create a test-only mapping and affirmative consent for exactly one camera."""
    identity_map = json.loads(_IDENTITY_MAP.read_text(encoding="utf-8"))
    employee_id = identity_map.get(name)
    if not employee_id:
        employee_id = f"test-employee-{uuid.uuid4().hex}"
        identity_map[name] = employee_id
        _IDENTITY_MAP.write_text(json.dumps(identity_map), encoding="utf-8")

    consents = json.loads(_CONSENTS.read_text(encoding="utf-8"))
    record = consents.get(employee_id, {})
    cameras = set(record.get("camera_ids", []))
    cameras.add(camera_id)
    consents[employee_id] = {
        "consented": True,
        "recorded_at": "2026-11-14T10:00:00Z",
        "camera_ids": sorted(cameras),
    }
    _CONSENTS.write_text(json.dumps(consents), encoding="utf-8")
    return employee_id
