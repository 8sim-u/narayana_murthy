"""
Weekly trend analytics for WAMSS dashboard data.

Wiring (applied by the coordinator, NOT done here):
    from app.analytics import router as analytics_router
    app.include_router(analytics_router)

Endpoints:
  GET /analytics/trends?metric=focus|strain&weeks=N

Math (kept deliberately simple, documented here and in the endpoint docstring):

Week buckets
    Weeks are counted back from today. Week 0 covers the most recent 7 days
    ending today; week 1 covers the 7 days before that, and so on. Each bucket
    is labeled by its first calendar date (week_start). N is capped at 52.

metric=focus
    For each employee and each week, the daily focus score is computed with
    the exact app.focus scorer used by GET /focus/daily, from that employee's
    ActivityLog rows (event_type="activity") for the day. Days with no rows
    carry no data and are excluded (they do not drag the average down).
    The per-employee weekly value is the mean of that employee's daily scores
    with data. The personal rollup for a week is the caller's own weekly
    mean: this endpoint is scoped to the caller's employee token, so only
    the caller's rows are read and no other employee's data contributes.
    The min/max therefore equal the caller's mean, and employee_count is 1
    when the caller has data that week, else 0.

metric=strain
    For each employee and each week, average strain_score over activity rows
    with a non-null strain_score, plus the count of rows in each strain level
    (green < 40, amber 40-69, red >= 70; see app.activity.strain_level). Rows
    with a null strain_score do not count toward the average but are tallied
    as "unknown". The personal rollup is the caller's own weekly average
    and level counts, for the same per-person scoping reason as above.
    Strain is a heuristic workload signal, not a medical measure.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity import strain_level
from app.activity_models import ActivityLog
from app.database import get_db
from app.employee_auth import require_employee
from app.focus import Segment, compute_focus_score

logger = logging.getLogger("wamss.api.analytics")

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_WEEKS = 52


# ---------------------------------------------------------------- schemas

class EmployeeFocusTrend(BaseModel):
    week_start: date
    employee_id: str
    average_focus: Optional[float] = Field(None, description="Mean of daily focus scores with data that week")
    days_with_data: int


class PersonalFocusTrend(BaseModel):
    week_start: date
    average_focus: Optional[float] = Field(None, description="Caller's own weekly mean of daily focus scores")
    min_focus: Optional[float] = None
    max_focus: Optional[float] = None
    employee_count: int


class FocusTrendsOut(BaseModel):
    metric: str = "focus"
    weeks: int
    per_employee: List[EmployeeFocusTrend]
    personal: List[PersonalFocusTrend]


class EmployeeStrainTrend(BaseModel):
    week_start: date
    employee_id: str
    average_strain: Optional[float] = Field(None, description="Mean strain_score over scored rows that week")
    green: int
    amber: int
    red: int
    unknown: int
    records: int


class PersonalStrainTrend(BaseModel):
    week_start: date
    average_strain: Optional[float] = Field(None, description="Caller's own weekly mean strain_score")
    green: int
    amber: int
    red: int
    unknown: int
    employee_count: int


class StrainTrendsOut(BaseModel):
    metric: str = "strain"
    weeks: int
    per_employee: List[EmployeeStrainTrend]
    personal: List[PersonalStrainTrend]


# ---------------------------------------------------------------- helpers

def _as_utc(value: datetime) -> datetime:
    """Normalize stored timestamps; naive database values are interpreted as UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _week_windows(weeks: int, today: date) -> List[Dict[str, date]]:
    """
    Return [ {"week_start": date, "start": datetime, "end": datetime}, ... ]
    for the `weeks` most recent 7-day buckets ending today, oldest first.
    Comparisons are done on day boundaries in UTC.
    """
    windows = []
    for i in reversed(range(weeks)):
        week_end = today - timedelta(days=7 * i)          # inclusive last day
        week_start = week_end - timedelta(days=6)         # inclusive first day
        windows.append(
            {
                "week_start": week_start,
                "start": datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc),
                "end": datetime(week_end.year, week_end.month, week_end.day, tzinfo=timezone.utc)
                + timedelta(days=1),
            }
        )
    return windows


def _rows_in_window(db: Session, start: datetime, end: datetime, employee_id: str) -> List[ActivityLog]:
    """All activity-type rows in [start, end), ordered for segment grouping."""
    return (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == "activity",
            ActivityLog.start_time >= start,
            ActivityLog.start_time < end,
            ActivityLog.employee_id == employee_id,
        )
        .order_by(ActivityLog.employee_id, ActivityLog.start_time)
        .all()
    )


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


# ---------------------------------------------------------------- endpoint

@router.get("/trends")
def get_trends(
    metric: str = Query("focus", description="Trend metric: 'focus' or 'strain'"),
    weeks: int = Query(4, ge=1, le=MAX_WEEKS, description="Number of trailing 7-day weeks"),
    db: Session = Depends(get_db),
    employee_id: str = Depends(require_employee),
):
    """
    Weekly aggregates for the caller only, plus a per-person rollup.

    - metric=focus: per-employee weekly mean of daily focus scores (days with
      data only), and the caller's own weekly mean as the personal rollup.
    - metric=strain: per-employee weekly mean strain_score plus green/amber/
      red/unknown level counts, and the caller's own values as the personal
      rollup. The rollup is named personal because the endpoint reads only
      the caller's rows; it never aggregates other employees.
    """
    if metric not in ("focus", "strain"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="metric must be 'focus' or 'strain'",
        )
    today = datetime.now(timezone.utc).date()
    windows = _week_windows(weeks, today)
    earliest = windows[0]["start"]
    latest = windows[-1]["end"]

    try:
        rows = _rows_in_window(db, earliest, latest, employee_id)
    except SQLAlchemyError as exc:
        logger.error("failed to query activity for trends: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to compute trends",
        )

    per_employee: List[BaseModel] = []
    personal: List[BaseModel] = []

    utc_rows = [
        (row, _as_utc(row.start_time), _as_utc(row.end_time))
        for row in rows
    ]

    for window in windows:
        week_start: date = window["week_start"]
        by_employee: Dict[str, List[Tuple[ActivityLog, datetime, datetime]]] = {}
        for row, start_utc, end_utc in utc_rows:
            if window["start"] <= start_utc < window["end"]:
                by_employee.setdefault(row.employee_id, []).append((row, start_utc, end_utc))

        if metric == "focus":
            weekly_means: List[float] = []
            for employee_id, erows in sorted(by_employee.items()):
                by_day: Dict[date, List[Segment]] = {}
                for r, start_utc, end_utc in erows:
                    day = start_utc.date()
                    by_day.setdefault(day, []).append(
                        Segment(start_utc, end_utc, r.app_name, r.is_idle)
                    )
                daily_scores = [
                    compute_focus_score(segs)["score"]
                    for segs in by_day.values()
                    if segs  # only days with data
                ]
                avg = _mean([float(s) for s in daily_scores])
                if avg is not None:
                    weekly_means.append(avg)
                per_employee.append(
                    EmployeeFocusTrend(
                        week_start=week_start,
                        employee_id=employee_id,
                        average_focus=avg,
                        days_with_data=len(daily_scores),
                    )
                )
            personal.append(
                PersonalFocusTrend(
                    week_start=week_start,
                    average_focus=_mean(weekly_means),
                    min_focus=min(weekly_means) if weekly_means else None,
                    max_focus=max(weekly_means) if weekly_means else None,
                    employee_count=len(weekly_means),
                )
            )
        else:  # metric == "strain"
            weekly_means = []
            totals = {"green": 0, "amber": 0, "red": 0, "unknown": 0}
            for employee_id, erows in sorted(by_employee.items()):
                employee_rows = [r for r, _, _ in erows]
                scored = [r.strain_score for r in employee_rows if r.strain_score is not None]
                levels = {"green": 0, "amber": 0, "red": 0, "unknown": 0}
                for r in employee_rows:
                    levels[strain_level(r.strain_score)] += 1
                for key in totals:
                    totals[key] += levels[key]
                avg = _mean([float(s) for s in scored])
                if avg is not None:
                    weekly_means.append(avg)
                per_employee.append(
                    EmployeeStrainTrend(
                        week_start=week_start,
                        employee_id=employee_id,
                        average_strain=avg,
                        green=levels["green"],
                        amber=levels["amber"],
                        red=levels["red"],
                        unknown=levels["unknown"],
                        records=len(employee_rows),
                    )
                )
            personal.append(
                PersonalStrainTrend(
                    week_start=week_start,
                    average_strain=_mean(weekly_means),
                    green=totals["green"],
                    amber=totals["amber"],
                    red=totals["red"],
                    unknown=totals["unknown"],
                    employee_count=len(weekly_means),
                )
            )

    if metric == "focus":
        return FocusTrendsOut(metric="focus", weeks=weeks, per_employee=per_employee, personal=personal)
    return StrainTrendsOut(metric="strain", weeks=weeks, per_employee=per_employee, personal=personal)
