"""
Camera + computer presence correlation (self-contained addition).

Wiring (applied by the coordinator, NOT done here):
    from app.presence import router as presence_router
    app.include_router(presence_router)

Endpoint:
  GET /presence   per-workstation presence states derived by correlating
                  vision detections (DetectionEvent) with workstation
                  activity logs (ActivityLog)

Config (environment):
  WAMSS_ZONE_MAP           JSON object mapping camera zone -> machine id,
                           e.g. '{"RESTRICTED":"MACHINE_01"}'.
                           Default: {"RESTRICTED": "MACHINE_01"}
  WAMSS_PRESENCE_WINDOW_SEC  Lookback window in seconds. Default 300.

The ActivityLog model (backend/app/activity_models.py, written by a sibling
agent) is imported defensively: if the file is not present yet, this module
still imports and GET /presence returns a clear 503 at request time.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DetectionEvent

logger = logging.getLogger("wamss.api.presence")

try:
    from app.activity_models import ActivityLog
except ImportError:  # sibling agent's file may not exist yet
    ActivityLog = None  # type: ignore[assignment]

router = APIRouter(prefix="/presence", tags=["presence"])

DEFAULT_ZONE_MAP: Dict[str, str] = {"RESTRICTED": "MACHINE_01"}
DEFAULT_WINDOW_SEC = 300

# Derived presence states returned by correlate().
PRESENT_AND_ACTIVE = "PRESENT_AND_ACTIVE"
PRESENT_BUT_IDLE = "PRESENT_BUT_IDLE"
ACTIVE_BUT_ABSENT = "ACTIVE_BUT_ABSENT"
ABSENT = "ABSENT"


# ---------------------------------------------------------------- response schema

class PresenceStateOut(BaseModel):
    machine_id: str
    zone: str
    state: str = Field(..., description="One of PRESENT_AND_ACTIVE, PRESENT_BUT_IDLE, ACTIVE_BUT_ABSENT, ABSENT")
    anomaly: bool = Field(..., description="True when the workstation is active but nobody is seen there")
    person_present: bool
    active: bool
    last_vision_seen: Optional[datetime]
    last_activity_seen: Optional[datetime]
    employee_id: Optional[str]
    window_sec: int


# ---------------------------------------------------------------- helpers

def _field(record: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a dict-like or an attribute-style record."""
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _as_aware(ts: Optional[datetime]) -> Optional[datetime]:
    """Normalize a timestamp to UTC-aware for safe comparison."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


# ---------------------------------------------------------------- correlation

def correlate(
    vision_events: Sequence[Any],
    activity_logs: Sequence[Any],
    zone_map: Dict[str, str],
    window_sec: int,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Correlate vision detections with workstation activity into per-workstation
    presence states. Pure function: no database access, safe to unit test.

    vision_events: records with .zone and .timestamp (datetime)
    activity_logs: records with .machine_id, .employee_id, .event_type,
                   .start_time, .end_time, .is_idle
    zone_map:      {camera zone: machine id}
    window_sec:    lookback window in seconds
    now:           reference time (defaults to current UTC); inject for tests

    Returns one dict per machine in zone_map.values(), sorted by machine_id:

      PRESENT_AND_ACTIVE: person seen in the zone AND workstation active
      PRESENT_BUT_IDLE:   person seen in the zone but workstation not active
      ACTIVE_BUT_ABSENT:  workstation active but nobody seen in the zone
                          (anomaly=True; suggests remote access or a spoofed
                          session while the workstation is unattended)
      ABSENT:             nobody seen and workstation not active
    """
    ref = _as_aware(now) or datetime.now(timezone.utc)
    cutoff = ref - timedelta(seconds=window_sec)

    # machine_id -> first zone that maps to it (in case of duplicates)
    machine_zone: Dict[str, str] = {}
    for zone, machine_id in zone_map.items():
        machine_zone.setdefault(machine_id, zone)

    # Index recent vision events by zone.
    vision_by_zone: Dict[str, List[datetime]] = {}
    for event in vision_events:
        ts = _as_aware(_field(event, "timestamp"))
        if ts is None or ts < cutoff:
            continue
        zone = _field(event, "zone")
        if zone is not None:
            vision_by_zone.setdefault(zone, []).append(ts)

    # Index recent "activity" type logs by machine. Non-activity event types
    # (alerts, break reminders) do not count as user activity.
    activity_by_machine: Dict[str, List[Any]] = {}
    for log in activity_logs:
        end = _as_aware(_field(log, "end_time"))
        if end is None or end < cutoff:
            continue
        if _field(log, "event_type", "activity") != "activity":
            continue
        machine_id = _field(log, "machine_id")
        if machine_id is not None:
            activity_by_machine.setdefault(machine_id, []).append(log)

    results: List[Dict[str, Any]] = []
    for machine_id in sorted(machine_zone):
        zone = machine_zone[machine_id]
        vision_seen = vision_by_zone.get(zone, [])
        person_present = len(vision_seen) > 0
        logs = activity_by_machine.get(machine_id, [])
        active = any(not _field(log, "is_idle", False) for log in logs)

        if person_present and active:
            state, anomaly = PRESENT_AND_ACTIVE, False
        elif person_present:
            state, anomaly = PRESENT_BUT_IDLE, False
        elif active:
            state, anomaly = ACTIVE_BUT_ABSENT, True
        else:
            state, anomaly = ABSENT, False

        # Most recent employee id from the machine's activity logs, if any.
        employee_id = None
        if logs:
            latest = max(logs, key=lambda log: _as_aware(_field(log, "end_time")) or cutoff)
            employee_id = _field(latest, "employee_id")

        results.append(
            {
                "machine_id": machine_id,
                "zone": zone,
                "state": state,
                "anomaly": anomaly,
                "person_present": person_present,
                "active": active,
                "last_vision_seen": max(vision_seen) if vision_seen else None,
                "last_activity_seen": max(
                    (_as_aware(_field(log, "end_time")) for log in logs), default=None
                ),
                "employee_id": employee_id,
                "window_sec": window_sec,
            }
        )
    return results


# ---------------------------------------------------------------- config

def _load_zone_map() -> Dict[str, str]:
    raw = os.environ.get("WAMSS_ZONE_MAP", "").strip()
    if not raw:
        return dict(DEFAULT_ZONE_MAP)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed:
            return {str(k): str(v) for k, v in parsed.items()}
        logger.warning("WAMSS_ZONE_MAP is not a non-empty JSON object; using defaults")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("WAMSS_ZONE_MAP is not valid JSON (%s); using defaults", exc)
    return dict(DEFAULT_ZONE_MAP)


def _load_window_sec() -> int:
    try:
        return max(1, int(os.environ.get("WAMSS_PRESENCE_WINDOW_SEC", DEFAULT_WINDOW_SEC)))
    except (TypeError, ValueError):
        logger.warning("WAMSS_PRESENCE_WINDOW_SEC is not a valid integer; using default %d", DEFAULT_WINDOW_SEC)
        return DEFAULT_WINDOW_SEC


# ---------------------------------------------------------------- endpoint

@router.get("", response_model=List[PresenceStateOut])
def get_presence(
    office_id: Optional[str] = Query(None, description="Filter workstation activity to this office ID"),
    db: Session = Depends(get_db),
):
    """
    Per-workstation presence states: correlates recent person detections in
    each mapped camera zone with recent workstation activity on the machine.

    When office_id is given, only that office's workstation activity logs are
    used (camera/zone correlation itself is zone-map based and office-agnostic).
    """
    if ActivityLog is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ActivityLog model unavailable: backend/app/activity_models.py is missing",
        )

    zone_map = _load_zone_map()
    window_sec = _load_window_sec()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_sec)

    try:
        vision_events = (
            db.query(DetectionEvent)
            .filter(DetectionEvent.timestamp >= cutoff)
            .order_by(DetectionEvent.timestamp.desc())
            .limit(5000)
            .all()
        )
        activity_query = db.query(ActivityLog).filter(ActivityLog.end_time >= cutoff)
        if office_id is not None:
            activity_query = activity_query.filter(ActivityLog.office_id == office_id)
        activity_logs = (
            activity_query
            .order_by(ActivityLog.end_time.desc())
            .limit(10000)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("failed to query presence inputs: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to compute presence",
        )

    return correlate(vision_events, activity_logs, zone_map, window_sec)
