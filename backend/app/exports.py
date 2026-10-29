"""
CSV export endpoints for WAMSS dashboard data.

Wiring (applied by the coordinator, NOT done here):
    from app.exports import router as exports_router
    app.include_router(exports_router)

Endpoints:
  GET /exports/events.csv   same filters as GET /events
  GET /exports/activity.csv same filters as GET /activity
  GET /exports/focus.csv    employee_id + date range (extension of GET /focus/daily)

All endpoints stream the CSV with a Content-Disposition attachment filename
so the browser downloads the file. Filter query parameters mirror the
corresponding JSON endpoints; /exports/focus.csv accepts a start_date /
end_date range (default: last 7 days ending today) because a single-day
export would rarely be useful.
"""

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity import strain_level
from app.activity_models import ActivityLog
from app.database import get_db
from app.focus import daily_focus
from app.models import DetectionEvent

logger = logging.getLogger("wamss.api.exports")

router = APIRouter(prefix="/exports", tags=["exports"])

EXPORT_LIMIT = 10000  # safety cap; exports are capped well below this by limit params


# ---------------------------------------------------------------- CSV streaming helper

def _stream_csv(
    header: Sequence[str],
    rows: List,
    row_fn,
    filename: str,
) -> StreamingResponse:
    """
    Build a StreamingResponse that emits the CSV header first and then one
    line per row, reusing a single StringIO buffer. Rows are materialized
    before streaming (simple and safe for the dashboard's export sizes).
    """

    def generate() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        yield buf.getvalue()
        for row in rows:
            buf.seek(0)
            buf.truncate(0)
            writer.writerow(row_fn(row))
            yield buf.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------- /exports/events.csv

def _event_row(event: DetectionEvent) -> List:
    return [
        event.id,
        event.camera_id,
        event.track_id,
        json.dumps(event.objects or []),
        event.confidence,
        event.zone,
        event.decision,
        json.dumps(event.alerts or []),
        event.timestamp.isoformat() if event.timestamp else "",
    ]


@router.get("/events.csv")
def export_events_csv(
    limit: int = Query(1000, ge=1, le=EXPORT_LIMIT, description="Maximum number of events to export"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    camera_id: Optional[str] = Query(None, description="Filter by camera ID"),
    start_time: Optional[datetime] = Query(None, description="Filter events after or at this UTC datetime"),
    end_time: Optional[datetime] = Query(None, description="Filter events before or at this UTC datetime"),
    db: Session = Depends(get_db),
):
    """CSV export of detection events, honoring the same filters as GET /events."""
    try:
        query = db.query(DetectionEvent)
        if camera_id is not None:
            query = query.filter(DetectionEvent.camera_id == camera_id)
        if start_time is not None:
            query = query.filter(DetectionEvent.timestamp >= start_time)
        if end_time is not None:
            query = query.filter(DetectionEvent.timestamp <= end_time)
        events = (
            query.order_by(DetectionEvent.timestamp.desc(), DetectionEvent.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("failed to query events for CSV export: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to export events",
        )
    return _stream_csv(
        ["id", "camera_id", "track_id", "objects", "confidence", "zone", "decision", "alerts", "timestamp"],
        events,
        _event_row,
        "events.csv",
    )


# ---------------------------------------------------------------- /exports/activity.csv

def _activity_row(row: ActivityLog) -> List:
    return [
        row.id,
        row.machine_id,
        row.employee_id,
        row.event_type,
        row.app_name,
        row.window_title,
        row.process_name,
        row.start_time.isoformat() if row.start_time else "",
        row.end_time.isoformat() if row.end_time else "",
        row.keystrokes,
        row.mouse_clicks,
        int(bool(row.is_idle)),
        row.strain_score if row.strain_score is not None else "",
        strain_level(row.strain_score),
        row.created_at.isoformat() if row.created_at else "",
    ]


@router.get("/activity.csv")
def export_activity_csv(
    machine_id: Optional[str] = Query(None, description="Filter by machine ID"),
    employee_id: Optional[str] = Query(None, description="Filter by employee ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type (activity, break_suggested, alert)"),
    start_time: Optional[datetime] = Query(None, description="Filter records at or after this UTC datetime"),
    end_time: Optional[datetime] = Query(None, description="Filter records at or before this UTC datetime"),
    limit: int = Query(1000, ge=1, le=EXPORT_LIMIT, description="Maximum number of records to export"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
):
    """CSV export of activity records, honoring the same filters as GET /activity."""
    try:
        query = db.query(ActivityLog)
        if machine_id is not None:
            query = query.filter(ActivityLog.machine_id == machine_id)
        if employee_id is not None:
            query = query.filter(ActivityLog.employee_id == employee_id)
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
        logger.error("failed to query activity for CSV export: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to export activity records",
        )
    return _stream_csv(
        [
            "id", "machine_id", "employee_id", "event_type", "app_name", "window_title",
            "process_name", "start_time", "end_time", "keystrokes", "mouse_clicks",
            "is_idle", "strain_score", "strain_level", "created_at",
        ],
        rows,
        _activity_row,
        "activity.csv",
    )


# ---------------------------------------------------------------- /exports/focus.csv

def _focus_row(day_result: dict) -> List:
    breakdown = day_result.get("breakdown", {})
    breaks = day_result.get("break_compliance", {})
    return [
        day_result.get("employee_id"),
        day_result.get("date").isoformat() if day_result.get("date") else "",
        day_result.get("focus_score"),
        breakdown.get("focused_blocks"),
        breakdown.get("app_switches"),
        breakdown.get("short_segments"),
        breakdown.get("idle_fraction"),
        breakdown.get("active_minutes"),
        breaks.get("required_breaks"),
        breaks.get("taken_breaks"),
        int(bool(breaks.get("compliant"))),
    ]


@router.get("/focus.csv")
def export_focus_csv(
    employee_id: str = Query(..., description="Employee ID (required)"),
    start_date: Optional[date] = Query(
        None, description="First date of the range (YYYY-MM-DD); defaults to 6 days before end_date"
    ),
    end_date: Optional[date] = Query(
        None, description="Last date of the range (YYYY-MM-DD); defaults to today"
    ),
    db: Session = Depends(get_db),
):
    """
    CSV export of daily focus scores for one employee over a date range.
    Mirrors GET /focus/daily's employee_id filter, extended with a date range
    (default: the last 7 days ending today) so the export contains a full week.
    Scores are computed with the same app.focus.daily_focus logic as /focus/daily.
    """
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=6))
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be at or before end_date",
        )
    if (end - start).days > 366:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date range limited to 366 days",
        )
    try:
        days: List[date] = []
        day = start
        while day <= end:
            days.append(day)
            day += timedelta(days=1)
        results = [daily_focus(db, employee_id, d) for d in days]
    except SQLAlchemyError as exc:
        logger.error("failed to compute focus export for %s: %s", employee_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to export focus scores",
        )
    return _stream_csv(
        [
            "employee_id", "date", "focus_score", "focused_blocks", "app_switches",
            "short_segments", "idle_fraction", "active_minutes",
            "breaks_required", "breaks_taken", "break_compliant",
        ],
        results,
        _focus_row,
        f"focus-{employee_id}.csv",
    )
