"""Opt-in daily attendance rollups from enrolled camera sightings and activity records.

Camera-derived presence is estimated only from tracked dwell intervals reported by
vision nodes; gaps are not extrapolated. Workstation time includes non-idle activity
segments. Both sources are clipped to UTC calendar days and merged to avoid double
counting overlapping intervals. Attendance is disabled unless explicitly enabled.
"""

import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity_models import ActivityLog
from app.camera_consent import has_active_camera_consent
from app.database import get_db
from app.employee_auth import require_employee
from app.models import DetectionEvent, FaceRecord

logger = logging.getLogger("wamss.api.attendance")
router = APIRouter(prefix="/attendance", tags=["attendance"])

ATTENDANCE_ENABLED = os.environ.get("WAMSS_ATTENDANCE_ENABLED", "0") == "1"
CAMERA_DWELL_CAP_SEC = max(1, int(os.environ.get("WAMSS_ATTENDANCE_DWELL_CAP_SEC", "300")))
MAX_DAILY_SOURCE_ROWS = 50000
try:
    MIN_IDENTITY_CONFIDENCE = float(os.environ.get("WAMSS_ATTENDANCE_MIN_CONFIDENCE", "0.5"))
except ValueError:
    MIN_IDENTITY_CONFIDENCE = 0.5
MIN_IDENTITY_CONFIDENCE = min(1.0, max(0.0, MIN_IDENTITY_CONFIDENCE))


class AttendanceItem(BaseModel):
    employee_id: str
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    present_minutes: float = Field(description="Union of camera dwell and active computer intervals")
    camera_minutes: float
    computer_minutes: float
    sources: List[str] = Field(description="camera and/or computer")


class DailyAttendanceOut(BaseModel):
    day: date = Field(description="UTC calendar date")
    enabled: bool
    items: List[AttendanceItem] = Field(default_factory=list)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _merge_intervals(intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted((start, end) for start, end in intervals if end > start)
    merged: List[Tuple[datetime, datetime]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _minutes(intervals: List[Tuple[datetime, datetime]]) -> float:
    return round(sum((end - start).total_seconds() for start, end in _merge_intervals(intervals)) / 60.0, 2)


@router.get("/daily", response_model=DailyAttendanceOut)
def daily_attendance(
    day: Optional[date] = Query(None, description="UTC calendar day; defaults to today"),
    db: Session = Depends(get_db),
    authenticated_employee_id: str = Depends(require_employee),
):
    """Return opt-in attendance totals with data-source labels for each employee."""
    target_day = day or datetime.now(timezone.utc).date()
    if not ATTENDANCE_ENABLED:
        return DailyAttendanceOut(day=target_day, enabled=False, items=[])

    day_start = datetime.combine(target_day, time.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    try:
        enrolled_names = {
            row.name.strip().casefold()
            for row in db.query(FaceRecord.name).all()
            if row.name and row.name.strip()
        }
        camera_rows = (
            db.query(DetectionEvent)
            .filter(
                DetectionEvent.employee_id == authenticated_employee_id,
                DetectionEvent.timestamp >= day_start,
                DetectionEvent.timestamp < day_end,
                DetectionEvent.person_name.is_not(None),
            )
            .order_by(DetectionEvent.timestamp.asc(), DetectionEvent.id.asc())
            .limit(MAX_DAILY_SOURCE_ROWS + 1)
            .all()
        )
        computer_rows = (
            db.query(ActivityLog)
            .filter(
                ActivityLog.employee_id == authenticated_employee_id,
                ActivityLog.event_type == "activity",
                ActivityLog.is_idle.is_(False),
                ActivityLog.start_time < day_end,
                ActivityLog.end_time > day_start,
            )
            .order_by(ActivityLog.start_time.asc(), ActivityLog.id.asc())
            .limit(MAX_DAILY_SOURCE_ROWS + 1)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("daily attendance query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query attendance",
        )
    if len(camera_rows) > MAX_DAILY_SOURCE_ROWS or len(computer_rows) > MAX_DAILY_SOURCE_ROWS:
        raise HTTPException(status_code=413, detail="Too many source records for one-day attendance aggregation")

    totals: Dict[str, Dict[str, object]] = {}

    def bucket(employee_id: str) -> Dict[str, object]:
        return totals.setdefault(employee_id, {
            "seen": [], "camera_intervals": [], "computer_intervals": [], "sources": set(),
        })

    for row in camera_rows:
        name = (row.person_name or "").strip()
        name_key = name.casefold()
        # Only enrolled, confidently identified people are included. Unknown,
        # anonymous, and unregistered camera detections never become attendance.
        employee_id = (row.employee_id or "").strip()
        if (not employee_id or employee_id != authenticated_employee_id
                or not name or name_key == "unknown" or name_key not in enrolled_names
                or not has_active_camera_consent(name, row.camera_id)
                or row.person_name_confidence is None
                or row.person_name_confidence < MIN_IDENTITY_CONFIDENCE):
            continue
        point = _as_utc(row.timestamp)
        entry = bucket(employee_id)
        entry["seen"].append(point)
        entry["sources"].add("camera")
        dwell = max(0.0, min(float(row.dwell_seconds or 0.0), CAMERA_DWELL_CAP_SEC))
        interval_end = min(point + timedelta(seconds=dwell), day_end)
        interval_start = max(point, day_start)
        if interval_end > interval_start:
            entry["camera_intervals"].append((interval_start, interval_end))

    for row in computer_rows:
        employee_id = (row.employee_id or "").strip()
        if not employee_id:
            continue
        start = max(_as_utc(row.start_time), day_start)
        end = min(_as_utc(row.end_time), day_end)
        if end <= start:
            continue
        entry = bucket(employee_id)
        entry["seen"].extend((start, end))
        entry["sources"].add("computer")
        entry["computer_intervals"].append((start, end))

    items = []
    for employee_id, data in sorted(totals.items()):
        camera_intervals = data["camera_intervals"]
        computer_intervals = data["computer_intervals"]
        all_intervals = camera_intervals + computer_intervals
        seen = data["seen"]
        items.append(AttendanceItem(
            employee_id=employee_id,
            first_seen=min(seen) if seen else None,
            last_seen=max(seen) if seen else None,
            present_minutes=_minutes(all_intervals),
            camera_minutes=_minutes(camera_intervals),
            computer_minutes=_minutes(computer_intervals),
            sources=sorted(data["sources"]),
        ))
    return DailyAttendanceOut(
        day=target_day,
        enabled=True,
        items=[item for item in items if item.employee_id == authenticated_employee_id],
    )
