"""
Security / DLP signal ingestion and alerting API.

Endpoints (mounted under /security by the coordinator via include_router):
  POST /security/events  - batch ingest of workstation security events
  GET  /security/alerts  - flagged anomalies over a lookback window

Anomaly detection lives in the pure function flag_anomalies(), which is
unit-testable without a database or FastAPI.

Configuration (env):
  WAMSS_WORK_HOURS         "HH:MM-HH:MM" local work window, default "09:00-18:00"
  WAMSS_SECURITY_TZ        IANA timezone for after-hours checks, default "UTC"
  WAMSS_DLP_WRITE_THRESHOLD_MB  write-burst threshold in MB, default 500

Privacy note: only aggregate signals (device labels, byte counts,
timestamps) are ingested and stored. No file content or keystroke content.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.alerts import dispatch_alert
from app.security_models import SecurityEvent

logger = logging.getLogger("wamss.security")

router = APIRouter(prefix="/security", tags=["security"])

DEFAULT_WORK_HOURS = "09:00-18:00"
DEFAULT_TZ = "UTC"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SecurityEventType(str, Enum):
    USB_INSERT = "usb_insert"
    USB_REMOVE = "usb_remove"
    FILE_WRITE_BURST = "file_write_burst"
    AFTER_HOURS_ACTIVITY = "after_hours_activity"
    # Round 4: USB anomaly detector event types (agent/usb_monitor.py).
    USB_FIRST_SEEN = "usb_first_seen"                    # medium
    USB_UNAUTHORIZED = "usb_unauthorized"                # high
    USB_HID_SPOOF_SUSPECTED = "usb_hid_spoof_suspected"  # high
    USB_OFFHOURS_MOUNT = "usb_offhours_mount"            # medium


class SecurityEventIn(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=64)
    employee_id: str = Field(..., min_length=1, max_length=64)
    office_id: str = Field("hq", max_length=64, description="Office / site the workstation belongs to")
    type: SecurityEventType
    # Detail payload is metadata only: device labels, byte counts, etc.
    # Never file content or keystroke content.
    detail: Optional[Dict[str, Any]] = None
    timestamp: datetime


class SecurityEventsBatch(BaseModel):
    events: List[SecurityEventIn] = Field(..., min_length=1, max_length=1000)


class SecurityIngestResponse(BaseModel):
    received: int


class AlertOut(BaseModel):
    id: Optional[int] = None
    event_type: str
    severity: Literal["high", "medium", "low"]
    reason: str
    machine_id: str
    employee_id: str
    office_id: Optional[str] = None
    timestamp: datetime
    detail: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Pure anomaly-detection rules (unit-testable)
# ---------------------------------------------------------------------------


def _parse_work_hours(work_hours: str) -> tuple[time, time]:
    """Parse "HH:MM-HH:MM" into (start, end). Raises ValueError on bad input."""
    start_s, end_s = work_hours.split("-")
    start = datetime.strptime(start_s.strip(), "%H:%M").time()
    end = datetime.strptime(end_s.strip(), "%H:%M").time()
    return start, end


def _to_local(ts: datetime, tz_name: str) -> datetime:
    """Convert a timestamp to the configured local timezone."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r; falling back to UTC", tz_name)
        tz = timezone.utc
    return ts.astimezone(tz)


def flag_anomalies(
    events: Iterable[Mapping[str, Any]],
    *,
    work_hours: str = DEFAULT_WORK_HOURS,
    tz_name: str = DEFAULT_TZ,
    write_threshold_mb: float = 500.0,
) -> List[Dict[str, Any]]:
    """
    Flag anomalous security events. Pure function: no I/O, no global state.

    Input events are mappings with keys: event_type (or "type"), timestamp,
    machine_id, employee_id, office_id (optional), detail (optional),
    id (optional).

    Rules (one alert per event, first matching rule wins):
      * any usb_insert / usb_remove            -> severity "high"
      * usb_first_seen                           -> severity "medium"
      * usb_unauthorized                         -> severity "high"
      * usb_hid_spoof_suspected                  -> severity "high"
      * usb_offhours_mount                       -> severity "medium"
      * file_write_burst at/above threshold    -> severity "medium"
      * after_hours_activity, or any non-USB event whose local timestamp
        falls outside the configured work-hours window -> severity "medium"

    Returns a list of alert dicts with keys: event_type, severity, reason,
    machine_id, employee_id, office_id, timestamp, detail, id (when provided).
    """
    try:
        start_h, end_h = _parse_work_hours(work_hours)
    except (ValueError, AttributeError) as exc:
        logger.warning("Bad WAMSS_WORK_HOURS %r (%s); using default", work_hours, exc)
        start_h, end_h = _parse_work_hours(DEFAULT_WORK_HOURS)

    alerts: List[Dict[str, Any]] = []
    for raw in events:
        event_type = str(raw.get("event_type", raw.get("type", "")))
        ts = raw.get("timestamp")
        machine_id = str(raw.get("machine_id", ""))
        employee_id = str(raw.get("employee_id", ""))
        detail = raw.get("detail") or {}

        def alert(severity: str, reason: str) -> None:
            alerts.append(
                {
                    "event_type": event_type,
                    "severity": severity,
                    "reason": reason,
                    "machine_id": machine_id,
                    "employee_id": employee_id,
                    "office_id": raw.get("office_id"),
                    "timestamp": ts,
                    "detail": detail if isinstance(detail, dict) else {"value": detail},
                    "id": raw.get("id"),
                }
            )

        flagged = False

        if event_type in ("usb_insert", "usb_remove"):
            action = "inserted" if event_type == "usb_insert" else "removed"
            device = detail.get("device", "unknown device") if isinstance(detail, dict) else "unknown device"
            alert("high", f"Removable storage {action}: {device}")
            flagged = True

        # Round 4: USB anomaly detector event types (agent/usb_monitor.py).
        # One alert per event, severities set by the detector.
        if not flagged and event_type == "usb_first_seen":
            label = detail.get("label", "unknown device") if isinstance(detail, dict) else "unknown device"
            alert("medium", f"First-seen USB device for user: {label}")
            flagged = True

        if not flagged and event_type == "usb_unauthorized":
            label = detail.get("label", "unknown device") if isinstance(detail, dict) else "unknown device"
            alert("high", f"USB device not on allowlist: {label}")
            flagged = True

        if not flagged and event_type == "usb_hid_spoof_suspected":
            alert("high", "Possible BadUSB HID spoofing (heuristic); verify the device physically")
            flagged = True

        if not flagged and event_type == "usb_offhours_mount":
            label = detail.get("label", "unknown device") if isinstance(detail, dict) else "unknown device"
            alert("medium", f"Mass-storage mount outside work hours: {label}")
            flagged = True

        if not flagged and event_type == "file_write_burst":
            if isinstance(detail, dict):
                written_mb = detail.get("bytes_written_mb")
                if written_mb is None and "bytes_written" in detail:
                    written_mb = float(detail["bytes_written"]) / (1024 * 1024)
            else:
                written_mb = None
            threshold = detail.get("threshold_mb", write_threshold_mb) if isinstance(detail, dict) else write_threshold_mb
            if written_mb is not None and float(written_mb) >= float(threshold):
                alert(
                    "medium",
                    f"Large file write burst: {float(written_mb):.1f} MB "
                    f"(threshold {float(threshold):.0f} MB). Byte counts only; no file content.",
                )
                flagged = True

        # After-hours detection: explicit type, or timestamp outside work window.
        # USB events already carry a high-severity alert, so they skip this.
        if not flagged:
            flagged_after_hours = event_type == "after_hours_activity"
            if not flagged_after_hours and isinstance(ts, datetime):
                local = _to_local(ts, tz_name)
                t = local.time()
                if start_h <= end_h:
                    outside = t < start_h or t >= end_h
                else:  # overnight window, e.g. 22:00-06:00
                    outside = t < start_h and t >= end_h
                flagged_after_hours = outside
            if flagged_after_hours:
                alert("medium", f"Activity outside work hours ({work_hours} {tz_name})")

    return alerts


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _work_hours() -> str:
    return os.getenv("WAMSS_WORK_HOURS", DEFAULT_WORK_HOURS)


def _tz_name() -> str:
    return os.getenv("WAMSS_SECURITY_TZ", DEFAULT_TZ)


def _write_threshold_mb() -> float:
    try:
        return float(os.getenv("WAMSS_DLP_WRITE_THRESHOLD_MB", "500"))
    except ValueError:
        return 500.0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/events", response_model=SecurityIngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_security_events(batch: SecurityEventsBatch, db: Session = Depends(get_db)):
    """Batch ingest of workstation security/DLP signal events."""
    rows = [
        SecurityEvent(
            machine_id=ev.machine_id,
            employee_id=ev.employee_id,
            office_id=ev.office_id,
            event_type=ev.type.value,
            detail=ev.detail,
            timestamp=ev.timestamp,
        )
        for ev in batch.events
    ]
    try:
        db.add_all(rows)
        db.commit()
        flagged = flag_anomalies(
            [
                {
                    "event_type": ev.type.value,
                    "machine_id": ev.machine_id,
                    "employee_id": ev.employee_id,
                    "timestamp": ev.timestamp,
                    "detail": ev.detail,
                }
                for ev in batch.events
            ],
            work_hours=_work_hours(),
            tz_name=_tz_name(),
            write_threshold_mb=_write_threshold_mb(),
        )
        for anomaly in flagged:
            dispatch_alert(anomaly["event_type"], {
                "machine_id": anomaly["machine_id"],
                "employee_id": anomaly["employee_id"],
                "severity": anomaly["severity"],
                "reason": anomaly["reason"],
                "timestamp": anomaly["timestamp"],
                "detail": anomaly["detail"],
            })
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Failed to persist security events: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to store security events",
        )
    return SecurityIngestResponse(received=len(rows))


@router.get("/alerts", response_model=List[AlertOut])
def get_security_alerts(
    machine_id: Optional[str] = Query(None, description="Filter by machine ID"),
    employee_id: Optional[str] = Query(None, description="Filter by employee ID"),
    office_id: Optional[str] = Query(None, description="Filter by office ID (e.g. hq, branch-north)"),
    hours: int = Query(24, ge=1, le=720, description="Lookback window in hours"),
    db: Session = Depends(get_db),
):
    """
    Return flagged security anomalies for events in the last `hours` hours,
    newest first. Filters are optional.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        query = db.query(SecurityEvent).filter(SecurityEvent.timestamp >= cutoff)
        if machine_id:
            query = query.filter(SecurityEvent.machine_id == machine_id)
        if employee_id:
            query = query.filter(SecurityEvent.employee_id == employee_id)
        if office_id:
            query = query.filter(SecurityEvent.office_id == office_id)
        events = (
            query.order_by(SecurityEvent.timestamp.desc(), SecurityEvent.id.desc()).limit(1000).all()
        )
    except SQLAlchemyError as exc:
        logger.error("Failed to query security events: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query security events",
        )

    raw_events = [
        {
            "id": ev.id,
            "event_type": ev.event_type,
            "machine_id": ev.machine_id,
            "employee_id": ev.employee_id,
            "office_id": ev.office_id,
            "timestamp": ev.timestamp,
            "detail": ev.detail,
        }
        for ev in events
    ]
    alerts = flag_anomalies(
        raw_events,
        work_hours=_work_hours(),
        tz_name=_tz_name(),
        write_threshold_mb=_write_threshold_mb(),
    )
    return alerts
