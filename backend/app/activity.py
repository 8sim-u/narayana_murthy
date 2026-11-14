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

import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity_models import ActivityLog
from app.audit import AuditChainError, append_activity_records, lock_activity_chain_head
from app.alerts import dispatch_alert
from app.database import engine, get_db
from app.employee_auth import (
    ensure_employee_match,
    require_employee,
    require_employee_or_service,
)

logger = logging.getLogger("wamss.api.activity")

router = APIRouter(prefix="/activity", tags=["activity"])

STRAIN_ALERT_THRESHOLD = int(os.environ.get("WAMSS_STRAIN_ALERT_THRESHOLD", "70"))
STRAIN_ALERT_BATCHES = int(os.environ.get("WAMSS_STRAIN_ALERT_BATCHES", "3"))
APP_CATEGORY_PATH = Path(os.environ.get(
    "WAMSS_APP_CATEGORY_FILE", Path(__file__).with_name("app_categories.json")
))
MAX_APP_USAGE_ROWS = 50000


def ensure_activity_schema() -> None:
    """Add the nullable idempotency column and unique index on old databases."""
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if "activity_logs" not in inspector.get_table_names():
                return
            columns = {column["name"] for column in inspector.get_columns("activity_logs")}
            if "record_id" not in columns:
                conn.execute(text("ALTER TABLE activity_logs ADD COLUMN record_id VARCHAR(64)"))
            index_names = {index["name"] for index in inspect(conn).get_indexes("activity_logs")}
            if "ix_activity_logs_record_id_unique" not in index_names:
                conn.execute(text(
                    "CREATE UNIQUE INDEX ix_activity_logs_record_id_unique "
                    "ON activity_logs (record_id)"
                ))
    except Exception as exc:
        # Do not make service startup depend on the additive migration; API
        # writes report a normal database error if an older schema remains.
        logger.warning("activity schema migration skipped: %s", type(exc).__name__)


def _normalize_app_name(value: str) -> str:
    return os.path.basename((value or "").replace("\\", "/")).strip().casefold()


def load_app_categories() -> Dict[str, str]:
    """Read configurable process-name categories; unknown/malformed entries stay neutral."""
    try:
        raw = json.loads(APP_CATEGORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("app category map unavailable; defaulting all apps to neutral")
        return {}
    if not isinstance(raw, dict):
        logger.warning("app category map must be a JSON object")
        return {}
    valid = {"work", "break", "neutral"}
    return {
        _normalize_app_name(str(name)): category.strip().casefold()
        for name, category in raw.items()
        if isinstance(name, str)
        and name.strip()
        and isinstance(category, str)
        and category.strip().casefold() in valid
    }


# ---------------------------------------------------------------- schemas

class ActivityRecordIn(BaseModel):
    record_id: Optional[str] = Field(None, max_length=64, description="Client-side idempotency id")
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
    record_id: Optional[str] = None
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
    stored: int
    duplicates: int = 0
    alerts: List[str] = Field(default_factory=list)


class AppUsageItem(BaseModel):
    employee_id: str
    machine_id: str
    app_name: str
    category: str = Field(description="work, break, or neutral; unknown apps default to neutral")
    minutes: float


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
def ingest_activity(
    batch: ActivityBatchIn,
    db: Session = Depends(get_db),
    authenticated_employee_id: Optional[str] = Depends(require_employee_or_service),
):
    """Ingest employee-bound records or explicitly authorized service batches."""
    if authenticated_employee_id is not None:
        for record in batch.records:
            ensure_employee_match(record.employee_id, authenticated_employee_id)
    record_ids = list({record.record_id for record in batch.records if record.record_id})
    try:
        # Serialize the idempotency check with every activity append. Checking
        # before this lock lets concurrent requests both accept the same key.
        chain_head = lock_activity_chain_head(db)
        existing_ids = set()
        if record_ids:
            existing_ids = {
                value for (value,) in db.query(ActivityLog.record_id)
                .filter(ActivityLog.record_id.in_(record_ids)).all()
            }
        accepted = []
        seen_in_batch = set()
        for record in batch.records:
            if record.record_id and (record.record_id in existing_ids or record.record_id in seen_in_batch):
                continue
            accepted.append(record)
            if record.record_id:
                seen_in_batch.add(record.record_id)
        rows = [
            ActivityLog(
                record_id=r.record_id,
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
            for r in accepted
        ]
        db.add_all(rows)
        alerts = _check_strain_alert(db, accepted) if accepted else []
        if rows:
            pending_activity = [row for row in db.new if isinstance(row, ActivityLog)]
            append_activity_records(db, pending_activity, chain_head)
        db.commit()
    except (SQLAlchemyError, AuditChainError) as exc:
        db.rollback()
        logger.error("failed to persist activity batch: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to store activity records",
        )
    return ActivityIngestResponse(
        received=len(batch.records), stored=len(rows),
        duplicates=len(batch.records) - len(rows), alerts=alerts,
    )


@router.get("/app-usage", response_model=List[AppUsageItem])
def daily_app_usage(
    day: Optional[date] = Query(None, description="UTC calendar day; defaults to today"),
    employee_id: Optional[str] = Query(None),
    machine_id: Optional[str] = Query(None),
    office_id: Optional[str] = Query(None),
    authenticated_employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Daily non-idle app minutes, categorized using a configurable JSON map."""
    ensure_employee_match(employee_id, authenticated_employee_id)
    target_day = day or datetime.now(timezone.utc).date()
    start = datetime.combine(target_day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    try:
        query = db.query(ActivityLog).filter(
            ActivityLog.event_type == "activity",
            ActivityLog.is_idle.is_(False),
            ActivityLog.start_time < end,
            ActivityLog.end_time > start,
        )
        query = query.filter(ActivityLog.employee_id == authenticated_employee_id)
        if machine_id is not None:
            query = query.filter(ActivityLog.machine_id == machine_id)
        if office_id is not None:
            query = query.filter(ActivityLog.office_id == office_id)
        records = (
            query.order_by(ActivityLog.start_time.asc(), ActivityLog.id.asc())
            .limit(MAX_APP_USAGE_ROWS + 1)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("app usage query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query app usage",
        )
    if len(records) > MAX_APP_USAGE_ROWS:
        raise HTTPException(status_code=413, detail="Too many activity records for one-day aggregation")

    categories = load_app_categories()
    totals: Dict[Tuple[str, str, str, str], float] = {}
    display_names: Dict[Tuple[str, str, str, str], str] = {}
    for record in records:
        begin = record.start_time
        finish = record.end_time
        if begin.tzinfo is None:
            begin = begin.replace(tzinfo=timezone.utc)
        if finish.tzinfo is None:
            finish = finish.replace(tzinfo=timezone.utc)
        clipped_start = max(begin, start)
        clipped_end = min(finish, end)
        seconds = max(0.0, (clipped_end - clipped_start).total_seconds())
        if not seconds:
            continue
        app_name = (record.process_name or record.app_name or "(unknown)").strip()[:256] or "(unknown)"
        app_key = _normalize_app_name(app_name) or "(unknown)"
        category = categories.get(app_key, "neutral")
        key = (record.employee_id, record.machine_id, app_key, category)
        totals[key] = totals.get(key, 0.0) + seconds / 60.0
        display_names.setdefault(key, app_name)

    return [
        AppUsageItem(
            employee_id=employee,
            machine_id=machine,
            app_name=display_names[(employee, machine, app, category)],
            category=category,
            minutes=round(minutes, 2),
        )
        for (employee, machine, app, category), minutes in sorted(
            totals.items(), key=lambda item: (-item[1], item[0])
        )
    ]


@router.get("/strain-summary", response_model=List[StrainSummaryItem])
def strain_summary(
    authenticated_employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
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
            .filter(ActivityLog.employee_id == authenticated_employee_id)
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
    authenticated_employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Query activity records in reverse chronological order with filters."""
    ensure_employee_match(employee_id, authenticated_employee_id)
    try:
        query = db.query(ActivityLog).filter(
            ActivityLog.employee_id == authenticated_employee_id
        )
        if machine_id is not None:
            query = query.filter(ActivityLog.machine_id == machine_id)
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
