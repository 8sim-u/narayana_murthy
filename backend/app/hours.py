"""
Per-user work-hours baselining (CERT-style) for WAMSS.

Wiring (applied by the coordinator, NOT done here):
    from app.hours import router as hours_router
    app.include_router(hours_router)

Endpoints:
  GET /hours/baseline?employee_id=X[&days=N]
  GET /hours/anomaly?employee_id=X

Math (kept deliberately simple and documented here):

Baseline window
    The last N days (default 30, configurable 7-90), built from ActivityLog
    rows with event_type="activity" and is_idle=False. Each row contributes
    its active minutes to 15-minute bins (96 bins per day) in the configured
    timezone (WAMSS_HOURS_TZ, default "UTC"). Rows spanning bin boundaries
    split their minutes across the touched bins.

Normal window
    For each day with activity, the active span is (first active bin,
    last active bin + 1). normal_start is the 5th percentile of daily first
    bins and normal_end the 95th percentile of daily last bins, formatted as
    "HH:MM". Days with no activity are excluded from the percentile
    computation but do not shift the result.

Anomaly scoring
    Baseline stats come from the 30 days BEFORE the most recent 24h; the
    recent window is the last 24h.
      time_anomaly_score = z-score of recent active minutes vs the baseline
        mean/std of daily active minutes (0.0 when std is 0 or undefined).
      after_hours_activity = True when any recent active minute falls
        outside the baseline normal window (falls back to WAMSS_WORK_HOURS
        when the user has no baseline days).
      burstiness = coefficient of variation (std/mean) of per-bin active
        minutes across the 96 bins of the last 24h. A uniform day scores
        near 0; activity concentrated in a few bins scores high.

Privacy note: only aggregate activity metadata (app names, counts,
timestamps) is used. No file content or keystroke content.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity_models import ActivityLog
from app.database import get_db

logger = logging.getLogger("wamss.api.hours")

router = APIRouter(prefix="/hours", tags=["hours"])

BIN_MINUTES = 15
BINS_PER_DAY = 24 * 60 // BIN_MINUTES  # 96
DEFAULT_BASELINE_DAYS = 30
DEFAULT_TZ = "UTC"
DEFAULT_WORK_HOURS = "09:00-18:00"


# ---------------------------------------------------------------- schemas


class BaselineOut(BaseModel):
    employee_id: str
    normal_start: Optional[str] = Field(None, description='"HH:MM" 5th percentile of daily first-active bin')
    normal_end: Optional[str] = Field(None, description='"HH:MM" 95th percentile of daily last-active bin')
    bins: List[int] = Field(..., description="96 counts: days in window with activity in each 15-min bin")
    days_used: int = Field(..., description="Days with activity in the baseline window")
    mean_daily_active_minutes: float
    std_daily_active_minutes: float


class AnomalyOut(BaseModel):
    employee_id: str
    time_anomaly_score: float = Field(..., description="z-score of last-24h active minutes vs baseline")
    after_hours_activity: bool
    burstiness: float = Field(..., description="CV of per-bin active minutes over the last 24h")
    recent_active_minutes: float
    normal_start: Optional[str]
    normal_end: Optional[str]


# ---------------------------------------------------------------- config


def _tz_name() -> str:
    return os.getenv("WAMSS_HOURS_TZ", DEFAULT_TZ)


def _work_hours_fallback() -> str:
    return os.getenv("WAMSS_WORK_HOURS", DEFAULT_WORK_HOURS)


def _to_local(ts: datetime, tz_name: str) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r; falling back to UTC", tz_name)
        tz = timezone.utc
    return ts.astimezone(tz)


# ---------------------------------------------------------------- pure math


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile; None for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    frac = rank - low
    return float(ordered[low] + frac * (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]))


def _bin_index(moment: datetime) -> int:
    return moment.hour * (60 // BIN_MINUTES) + moment.minute // BIN_MINUTES


def _bin_to_hhmm(bin_index: int) -> str:
    total_minutes = min(max(int(round(bin_index)), 0), BINS_PER_DAY) * BIN_MINUTES
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def daily_active_spans(rows: Sequence, tz_name: str = DEFAULT_TZ) -> List[Dict]:
    """
    Group active rows into per-day 15-minute bin sets.

    Each input row needs start_time, end_time (datetimes), event_type and
    is_idle attributes. Returns one dict per local date with activity:
    {"date": date, "bin_minutes": [float x96], "start_bin": int,
     "end_bin": int, "active_minutes": float}.
    """
    per_day: Dict[date, List[float]] = {}
    for row in rows:
        if getattr(row, "event_type", "activity") != "activity":
            continue
        if getattr(row, "is_idle", False):
            continue
        start = getattr(row, "start_time")
        end = getattr(row, "end_time")
        if start is None or end is None or end <= start:
            continue
        local_start = _to_local(start, tz_name)
        local_end = _to_local(end, tz_name)
        cursor = local_start
        while cursor < local_end:
            bin_start_minute = (cursor.hour * 60 + cursor.minute) // BIN_MINUTES * BIN_MINUTES
            bin_start = cursor.replace(hour=bin_start_minute // 60, minute=bin_start_minute % 60,
                                       second=0, microsecond=0)
            bin_end = bin_start + timedelta(minutes=BIN_MINUTES)
            overlap = (min(local_end, bin_end) - max(local_start, bin_start)).total_seconds() / 60.0
            if overlap > 0:
                day = cursor.date()
                bins = per_day.setdefault(day, [0.0] * BINS_PER_DAY)
                idx = _bin_index(cursor)
                bins[idx] += overlap
            cursor = bin_end
    spans = []
    for day in sorted(per_day):
        bin_minutes = per_day[day]
        active = [i for i, m in enumerate(bin_minutes) if m > 0]
        spans.append(
            {
                "date": day,
                "bin_minutes": bin_minutes,
                "start_bin": min(active),
                "end_bin": max(active) + 1,  # exclusive, so 18:00 formats correctly
                "active_minutes": sum(bin_minutes),
            }
        )
    return spans


def build_bin_histogram(spans: Sequence[Dict]) -> List[int]:
    """96 counts: number of days with any activity in each 15-minute bin."""
    histogram = [0] * BINS_PER_DAY
    for span in spans:
        for i, minutes in enumerate(span["bin_minutes"]):
            if minutes > 0:
                histogram[i] += 1
    return histogram


def normal_window(spans: Sequence[Dict]) -> Tuple[Optional[str], Optional[str]]:
    """
    (normal_start, normal_end) as "HH:MM": 5th percentile of daily first
    active bins and 95th percentile of daily last active bins.
    (None, None) when there are no days with activity.
    """
    if not spans:
        return None, None
    start_p5 = _percentile([s["start_bin"] for s in spans], 5)
    end_p95 = _percentile([s["end_bin"] for s in spans], 95)
    return _bin_to_hhmm(start_p5), _bin_to_hhmm(end_p95)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: Sequence[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def compute_baseline(rows: Sequence, tz_name: str = DEFAULT_TZ) -> Dict:
    """
    Build the baseline summary dict consumed by GET /hours/baseline.
    Keys: normal_start, normal_end, bins, days_used,
    mean_daily_active_minutes, std_daily_active_minutes.
    """
    spans = daily_active_spans(rows, tz_name)
    normal_start, normal_end = normal_window(spans)
    minutes = [s["active_minutes"] for s in spans]
    mean = _mean(minutes)
    return {
        "normal_start": normal_start,
        "normal_end": normal_end,
        "bins": build_bin_histogram(spans),
        "days_used": len(spans),
        "mean_daily_active_minutes": mean,
        "std_daily_active_minutes": _std(minutes, mean),
    }


def time_anomaly_score(recent_minutes: float, mean: float, std: float) -> float:
    """z-score of recent active minutes vs the baseline norm."""
    if std <= 0:
        return 0.0
    return (recent_minutes - mean) / std


def _parse_work_hours(work_hours: str) -> Tuple[int, int]:
    """Parse "HH:MM-HH:MM" into (start_bin, end_bin) as bin indices."""
    start_s, end_s = work_hours.split("-")
    start = datetime.strptime(start_s.strip(), "%H:%M")
    end = datetime.strptime(end_s.strip(), "%H:%M")
    return (
        start.hour * (60 // BIN_MINUTES) + start.minute // BIN_MINUTES,
        end.hour * (60 // BIN_MINUTES) + end.minute // BIN_MINUTES,
    )


def after_hours_flag(
    recent_bin_minutes: Sequence[float],
    normal_start: Optional[str],
    normal_end: Optional[str],
    work_hours_fallback: str = DEFAULT_WORK_HOURS,
) -> bool:
    """
    True when any recent active minute falls outside the normal window.
    When the user has no baseline window, the configured work-hours range
    is used instead. Overnight windows (end before start) are supported.
    """
    if normal_start and normal_end:
        start_bin = int(normal_start.split(":")[0]) * 4 + int(normal_start.split(":")[1]) // BIN_MINUTES
        end_bin = int(normal_end.split(":")[0]) * 4 + int(normal_end.split(":")[1]) // BIN_MINUTES
    else:
        try:
            start_bin, end_bin = _parse_work_hours(work_hours_fallback)
        except (ValueError, AttributeError):
            start_bin, end_bin = _parse_work_hours(DEFAULT_WORK_HOURS)
    for i, minutes in enumerate(recent_bin_minutes):
        if minutes <= 0:
            continue
        if start_bin <= end_bin:
            outside = i < start_bin or i >= end_bin
        else:  # overnight window
            outside = i < start_bin and i >= end_bin
        if outside:
            return True
    return False


def burstiness(recent_bin_minutes: Sequence[float]) -> float:
    """
    Coefficient of variation (std/mean) of per-bin active minutes over the
    96 bins of the last 24h. 0.0 when there is no activity.
    """
    mean = _mean(recent_bin_minutes)
    if mean <= 0:
        return 0.0
    return _std(list(recent_bin_minutes), mean) / mean


def recent_window_bin_minutes(rows: Sequence, tz_name: str = DEFAULT_TZ) -> List[float]:
    """Aggregate active minutes per 15-minute bin for the given rows."""
    totals = [0.0] * BINS_PER_DAY
    for span in daily_active_spans(rows, tz_name):
        for i, minutes in enumerate(span["bin_minutes"]):
            totals[i] += minutes
    return totals


# ---------------------------------------------------------------- DB helpers


def _activity_rows(db: Session, employee_id: str, start: datetime, end: datetime) -> List[ActivityLog]:
    return (
        db.query(ActivityLog)
        .filter(ActivityLog.employee_id == employee_id)
        .filter(ActivityLog.event_type == "activity")
        .filter(ActivityLog.is_idle.is_(False))
        .filter(ActivityLog.start_time >= start)
        .filter(ActivityLog.start_time < end)
        .order_by(ActivityLog.start_time.asc())
        .all()
    )


# ---------------------------------------------------------------- endpoints


@router.get("/baseline", response_model=BaselineOut)
def get_baseline(
    employee_id: str = Query(..., min_length=1, max_length=64, description="Employee ID"),
    days: int = Query(DEFAULT_BASELINE_DAYS, ge=7, le=90, description="Rolling baseline window in days"),
    db: Session = Depends(get_db),
):
    """
    30-day (configurable) rolling baseline of active 15-minute bins for one
    employee. normal_start/normal_end are the 5th/95th percentile of the
    daily active span. bins holds 96 per-bin day counts.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    try:
        rows = _activity_rows(db, employee_id, cutoff, now)
    except SQLAlchemyError as exc:
        logger.error("Failed to query activity rows for baseline: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to build hours baseline",
        )
    summary = compute_baseline(rows, _tz_name())
    return BaselineOut(employee_id=employee_id, **summary)


@router.get("/anomaly", response_model=AnomalyOut)
def get_anomaly(
    employee_id: str = Query(..., min_length=1, max_length=64, description="Employee ID"),
    db: Session = Depends(get_db),
):
    """
    CERT-style anomaly check for one employee: z-score of the last 24h of
    activity against the personal 30-day norm (excluding the last 24h), an
    after-hours flag, and a burstiness metric. Always returns 200, even
    with no data (scores are 0.0 and the after-hours flag falls back to the
    configured work-hours window).
    """
    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(hours=24)
    baseline_start = now - timedelta(days=DEFAULT_BASELINE_DAYS)
    try:
        baseline_rows = _activity_rows(db, employee_id, baseline_start, recent_start)
        recent_rows = _activity_rows(db, employee_id, recent_start, now)
    except SQLAlchemyError as exc:
        logger.error("Failed to query activity rows for anomaly: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to score hours anomaly",
        )
    baseline = compute_baseline(baseline_rows, _tz_name())
    recent_bins = recent_window_bin_minutes(recent_rows, _tz_name())
    recent_minutes = sum(recent_bins)
    return AnomalyOut(
        employee_id=employee_id,
        time_anomaly_score=time_anomaly_score(
            recent_minutes,
            baseline["mean_daily_active_minutes"],
            baseline["std_daily_active_minutes"],
        ),
        after_hours_activity=after_hours_flag(
            recent_bins,
            baseline["normal_start"],
            baseline["normal_end"],
            _work_hours_fallback(),
        ),
        burstiness=burstiness(recent_bins),
        recent_active_minutes=recent_minutes,
        normal_start=baseline["normal_start"],
        normal_end=baseline["normal_end"],
    )
