"""Fail-closed per-camera consent and privacy-mode policy.

Consent records are keyed by employee ID. Display names from camera events and
face records must resolve to exactly one employee ID through the configured
attendance identity map before they can authorize recognition or storage.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("wamss.api.camera_consent")

_DEFAULT_MAP = Path(__file__).with_name("attendance_identity_map.json")
_DEFAULT_CONSENTS = Path(__file__).with_name("attendance_camera_consents.json")


class _DuplicateKey(ValueError):
    pass


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey("duplicate JSON key")
        result[key] = value
    return result


def _read_object(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _identity_map_path() -> Path:
    return Path(os.environ.get("WAMSS_ATTENDANCE_IDENTITY_MAP", str(_DEFAULT_MAP)))


def _consent_path() -> Path:
    return Path(os.environ.get(
        "WAMSS_ATTENDANCE_CAMERA_CONSENT_FILE", str(_DEFAULT_CONSENTS)))


def camera_mode(camera_id: str) -> str:
    """Return the configured mode; missing/bad configuration means aggregate-only."""
    if not isinstance(camera_id, str) or not camera_id.strip():
        return "aggregate"
    raw = os.environ.get("WAMSS_CAMERA_PRIVACY_MODES", "{}")
    try:
        modes = json.loads(raw, object_pairs_hook=_object_without_duplicates)
    except (TypeError, ValueError):
        return "aggregate"
    if not isinstance(modes, dict):
        return "aggregate"
    # An explicit wildcard may enable ordinary person-event ingestion on
    # otherwise unlisted cameras, but never grants employee consent: that
    # check still requires the exact camera ID in the employee record. An
    # exact "aggregate" entry always overrides the wildcard.
    mode = modes.get(camera_id.strip(), modes.get("*"))
    return "identity" if mode == "identity" else "aggregate"


def employee_id_for_name(name: str) -> Optional[str]:
    """Resolve a display name to exactly one employee ID; ambiguous is denied."""
    if not isinstance(name, str) or not name.strip() or name.strip().casefold() == "unknown":
        return None
    identities = _read_object(_identity_map_path())
    if identities is None:
        return None
    matches = [
        employee_id.strip()
        for camera_name, employee_id in identities.items()
        if isinstance(camera_name, str) and camera_name.strip().casefold() == name.strip().casefold()
        and isinstance(employee_id, str) and employee_id.strip()
    ]
    return matches[0] if len(matches) == 1 else None


def consented_employee_id_for_name(name: str, camera_id: str) -> Optional[str]:
    """Return the uniquely mapped employee ID only with active camera consent."""
    if camera_mode(camera_id) != "identity":
        return None
    employee_id = employee_id_for_name(name)
    if employee_id is None:
        return None
    records = _read_object(_consent_path())
    if records is None:
        return None
    record = records.get(employee_id)
    if not isinstance(record, dict):
        return None
    cameras = record.get("camera_ids")
    if (record.get("consented") is not True
            or not isinstance(record.get("recorded_at"), str)
            or not record.get("recorded_at").strip()
            or record.get("revoked_at") not in (None, "")
            or not isinstance(cameras, list)
            or any(not isinstance(item, str) or not item.strip() for item in cameras)
            or len({item.strip() for item in cameras}) != len(cameras)):
        return None
    if camera_id.strip() not in {item.strip() for item in cameras}:
        return None
    return employee_id


def has_active_camera_consent(name: str, camera_id: str) -> bool:
    """True only for a unique mapped employee with explicit active camera consent."""
    return consented_employee_id_for_name(name, camera_id) is not None
