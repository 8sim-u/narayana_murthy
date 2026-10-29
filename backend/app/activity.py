"""
Workstation activity ingest and query API (self-contained addition).

Wiring (applied by the coordinator, NOT done here):
    from app.activity import router as activity_router
    app.include_router(activity_router)

Endpoints:
  POST /activity               batch ingest of activity records
  GET  /activity               query with filters
  GET  /activity/strain-summary latest strain level per employee/machine

Strain alert rule: when a machine/employee sends N consecutive batches whose
peak strain_score is at or above WAMSS_STRAIN_ALERT_THRESHOLD (default 70),
an alert record (event_type="alert", window_title="HIGH_STRAIN_DETECTED") is
written to the same table. The consecutive-batch counter is process-local and
resets whenever a batch arrives under the threshold. This is a heuristic
workload signal, not a medical measure.
"""

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity_models import ActivityLog
from app.alerts import dispatch_alert
from app.database import get_db

logger = logging.getLogger("wamss.api.activity")

router = APIRouter(prefix="/activity", tags=["activity"])

STRAIN_ALERT_THRESHOLD = int(os.environ.get("WAMSS_STRAIN_ALERT_THRESHOLD", "70"))
STRAIN_ALERT_BATCHES = int(os.environ.get("WAMSS_STRAIN_ALERT_BATCHES", "3"))


# ---------------------------------------------------------------- schemas

class ActivityRecordIn(BaseModel):
    record_id: Optional[str] = Field(None, description="Client-side idempotency id")
    machine_id: str = Field(..., max_length=64)
    employee_id: str = Field(..., max_length=64)
    office_id: str = Field("hq", max_length=64, description="Office / site the workstation belongs to")
    event_type: str = Field("activity", max_length=32)
    app_name: str = Field("", max_length=256)
    window_title: str = Field("", max_length=512)
    process_name: str = Field("", max_length=256)
    start_time: datetime
    end_time: datetime
    keystrokes: int = Field(0, ge=0)
    mouse_clicks: int = Field(0, ge=0)
    is_idle: bool = False
    strain_score: Optional[int] = Field(None, ge=0, le=100)
    ml_stress_score: Optional[float] = Field(
        None, ge=0, le=100,
        description="ML behavioral stress score 0-100 (round 4, WAMSS_STRAIN_PIPELINE=1)",
    )


class ActivityBatchIn(BaseModel):
    records: List[ActivityRecordIn] = Field(..., min_length=1, max_length=1000)


class ActivityRecordOut(BaseModel):
    id: int
    machine_id: str
    employee_id: str
    office_id: str = "hq"
    event_type: str
    app_name: str
    window_title: str
    process_name: str
    start_time: datetime
    end_time: datetime
    keystrokes: int
    mouse_clicks: int
    is_idle: bool
    strain_score: Optional[int]
    ml_stress_score: Optional[float] = None
    strain_level: Optional[str] = None  # computed server-side from strain_score
    created_at: datetime

    class Config:
        from_attributes = True


class ActivityIngestResponse(BaseModel):
    received: int
    alerts: List[str] = []


class StrainSummaryItem(BaseModel):
    machine_id: str
    employee_id: str
    strain_score: Optional[int]
    ml_stress_score: Optional[float] = None
    strain_level: str
    last_seen: Optional[datetime]
    is_idle: bool


def strain_level(score: Optional[int]) -> str:
    if score is None:
        return "unknown"
    if score < 40:
        return "green"
    if score < 70:
        return "amber"
    return "red"


# ---------------------------------------------------------------- strain alert rule state (process-local)

# (machine_id, employee_id) -> consecutive over-threshold batch count
_strain_streaks: Dict[Tuple[str, str], int] = {}


def _check_strain_alert(db: Session, batch: List[ActivityRecordIn]) -> List[str]:
    """
    Rule: strain_score >= threshold for N consecutive batches for the same
    machine/employee raises HIGH_STRAIN_DETECTED.
    """
    alerts: List[str] = []
    peaks: Dict[Tuple[str, str], int] = {}
    offices: Dict[Tuple[str, str], str] = {}
    for rec in batch:
        if rec.strain_score is None or rec.event_type != "activity":
            continue
        key = (rec.machine_id, rec.employee_id)
        peaks[key] = max(peaks.get(key, 0), rec.strain_score)
        offices.setdefault(key, rec.office_id)

    for key, peak in peaks.items():
        if peak >= STRAIN_ALERT_THRESHOLD:
            streak = _strain_streaks.get(key, 0) + 1
            _strain_streaks[key] = streak
            if streak >= STRAIN_ALERT_BATCHES:
                machine_id, employee_id = key
                alert = ActivityLog(
                    machine_id=machine_id,
                    employee_id=employee_id,
                    office_id=offices.get(key, "hq"),
                    event_type="alert",
                    app_name="wamss-agent",
                    window_title=f"HIGH_STRAIN_DETECTED score={peak} streak={streak}",
                    process_name="wamss-strain-rule",
                    start_time=datetime.now().astimezone(),
                    end_time=datetime.now().astimezone(),
                    keystrokes=0,
                    mouse_clicks=0,
                    is_idle=False,
                    strain_score=peak,
                )
                db.add(alert)
                alerts.append("HIGH_STRAIN_DETECTED")
                dispatch_alert("HIGH_STRAIN_DETECTED", {
                    "machine_id": machine_id,
                    "employee_id": employee_id,
                    "strain_score": peak,
                    "streak": streak,
                    "severity": "high",
                })
                _strain_streaks[key] = 0  # reset so we do not spam one alert per batch
                logger.warning("HIGH_STRAIN_DETECTED for %s/%s (peak=%d, streak=%d)",
                               machine_id, employee_id, peak, streak)
        else:
            _strain_streaks[key] = 0
    return alerts


# ---------------------------------------------------------------- endpoints

@router.post("", response_model=ActivityIngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_activity(batch: ActivityBatchIn, db: Session = Depends(get_db)):
    """Batch ingest of workstation activity records from the agent."""
    rows = [
        ActivityLog(
            machine_id=r.machine_id,
            employee_id=r.employee_id,
            office_id=r.office_id,
            event_type=r.event_type,
            app_name=r.app_name,
            window_title=r.window_title,
            process_name=r.process_name,
            start_time=r.start_time,
            end_time=r.end_time,
            keystrokes=r.keystrokes,
            mouse_clicks=r.mouse_clicks,
            is_idle=r.is_idle,
            strain_score=r.strain_score,
            ml_stress_score=r.ml_stress_score,
        )
        for r in batch.records
    ]
    try:
        db.add_all(rows)
        alerts = _check_strain_alert(db, batch.records)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("failed to persist activity batch: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to store activity records",
        )
    return ActivityIngestResponse(received=len(rows), alerts=alerts)


@router.get("/strain-summary", response_model=List[StrainSummaryItem])
def strain_summary(db: Session = Depends(get_db)):
    """
    Latest strain level per employee/machine, for the dashboard indicator.
    Level: green (<40), amber (40-69), red (>=70).
    """
    try:
        latest = (
            db.query(
                ActivityLog.machine_id,
                ActivityLog.employee_id,
                func.max(ActivityLog.created_at).label("last_seen"),
            )
            .group_by(ActivityLog.machine_id, ActivityLog.employee_id)
            .subquery()
        )
        rows = (
            db.query(ActivityLog)
            .join(
                latest,
                (ActivityLog.machine_id == latest.c.machine_id)
                & (ActivityLog.employee_id == latest.c.employee_id)
                & (ActivityLog.created_at == latest.c.last_seen),
            )
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("failed to query strain summary: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query activity",
        )
    return [
        StrainSummaryItem(
            machine_id=r.machine_id,
            employee_id=r.employee_id,
            strain_score=r.strain_score,
            ml_stress_score=r.ml_stress_score,
            strain_level=strain_level(r.strain_score),
            last_seen=r.created_at,
            is_idle=r.is_idle,
        )
        for r in rows
    ]


@router.get("", response_model=List[ActivityRecordOut])
def list_activity(
    machine_id: Optional[str] = Query(None, description="Filter by machine ID"),
    employee_id: Optional[str] = Query(None, description="Filter by employee ID"),
    office_id: Optional[str] = Query(None, description="Filter by office ID (e.g. hq, branch-north)"),
    event_type: Optional[str] = Query(None, description="Filter by event type (activity, break_suggested, fatigue_suspected, alert)"),
    start_time: Optional[datetime] = Query(None, description="Filter records at or after this UTC datetime"),
    end_time: Optional[datetime] = Query(None, description="Filter records at or before this UTC datetime"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
):
    """Query activity records in reverse chronological order with filters."""
    try:
        query = db.query(ActivityLog)
        if machine_id is not None:
            query = query.filter(ActivityLog.machine_id == machine_id)
        if employee_id is not None:
            query = query.filter(ActivityLog.employee_id == employee_id)
        if office_id is not None:
            query = query.filter(ActivityLog.office_id == office_id)
        if event_type is not None:
            query = query.filter(ActivityLog.event_type == event_type)
        if start_time is not None:
            query = query.filter(ActivityLog.start_time >= start_time)
        if end_time is not None:
            query = query.filter(ActivityLog.end_time <= end_time)
        rows = (
            query.order_by(ActivityLog.start_time.desc(), ActivityLog.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("failed to query activity: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query activity records",
        )
    out = []
    for r in rows:
        item = ActivityRecordOut.model_validate(r)
        item.strain_level = strain_level(r.strain_score)
        out.append(item)
    return out
