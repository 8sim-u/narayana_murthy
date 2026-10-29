"""
Daily focus score and streak gamification for WAMSS.

Wiring (applied by the coordinator, NOT done here):
    from app.focus import router as focus_router
    app.include_router(focus_router)

Endpoints:
  GET /focus/daily   (employee_id required, date optional, default today)
  GET /focus/streaks (employee_id required)

Focus score formula (0-100, computed on the fly from ActivityLog rows):

    score = 100
          + FOCUS_BLOCK_REWARD * (number of focused blocks)
          - SWITCH_PENALTY * (switches beyond the free allowance per active hour)
          - FRAGMENT_PENALTY * (excess fragmentation)
          - IDLE_PENALTY_RATIO * (idle fraction of the day)
    clamped to [0, 100]

Where:
- A "segment" is one ActivityLog row (event_type="activity", not idle) for the
  employee on the given date.
- A "focused block" is a completed, uninterrupted active stretch of at least
  FOCUS_BLOCK_MINUTES minutes (default 25). Merging: consecutive active
  segments with gaps of at most MERGE_GAP_MINUTES (default 2) count as one
  uninterrupted stretch. Reward per block is capped by FOCUS_BLOCK_REWARD_CAP
  (default 10 blocks).
- "App switches": changes of app_name between consecutive active segments.
  The first SWITCH_FREE_ALLOWANCE_PER_HOUR (default 8) switches per active
  hour are free; each switch beyond that costs SWITCH_PENALTY (default 1).
- "Fragmentation": segments shorter than FRAGMENT_SHORT_SEGMENT_MINUTES
  (default 3). The first FRAGMENT_FREE_ALLOWANCE (default 6) such short
  segments per day are free; each extra one costs FRAGMENT_PENALTY (default 2).
- "Idle-heavy day": idle rows (is_idle=True) as a fraction of all rows for the
  day; the score loses IDLE_PENALTY_RATIO * fraction (default ratio 30), so a
  fully idle day loses 30 points.
- Rows on a day with no activity rows at all score 0 (no data to reward).

Streaks:
- Focus streak: consecutive calendar days (counting back from today, or from
  yesterday if today has no data yet) with daily focus_score >=
  STREAK_FOCUS_THRESHOLD (default 70).
- Break streak: consecutive days with healthy break compliance, defined as at
  least one idle segment of at least BREAK_MIN_MINUTES (default 5) for every
  90 minutes of active work (i.e. active_minutes / 90 rounded up breaks
  required, counting idle segments of >= 5 minutes).
- Badges are earned from streak milestones:
  "Focused Starter" (focus streak >= 3), "Deep Work Pro" (focus streak >= 7),
  "Zen Master" (focus streak >= 30), "Well Rested" (break streak >= 5),
  "Break Champion" (break streak >= 14).

All numeric constants can be tuned via WAMSS_FOCUS_* environment variables
(see the module constants below).

This is a productivity heuristic, not a medical or psychological measure.
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

try:
    from app.activity_models import ActivityLog
    _ACTIVITY_MODEL_AVAILABLE = True
except Exception:  # pragma: no cover - defensive when sibling file is not present
    ActivityLog = None  # type: ignore[assignment]
    _ACTIVITY_MODEL_AVAILABLE = False

from app.database import get_db

logger = logging.getLogger("wamss.api.focus")

router = APIRouter(prefix="/focus", tags=["focus"])

# ---------------------------------------------------------------- constants (tunable via WAMSS_FOCUS_* env vars)

FOCUS_BLOCK_MINUTES = int(os.environ.get("WAMSS_FOCUS_BLOCK_MINUTES", "25"))
FOCUS_BLOCK_REWARD = int(os.environ.get("WAMSS_FOCUS_BLOCK_REWARD", "2"))
FOCUS_BLOCK_REWARD_CAP = int(os.environ.get("WAMSS_FOCUS_BLOCK_REWARD_CAP", "10"))
MERGE_GAP_MINUTES = int(os.environ.get("WAMSS_FOCUS_MERGE_GAP_MINUTES", "2"))
SWITCH_FREE_ALLOWANCE_PER_HOUR = int(os.environ.get("WAMSS_FOCUS_SWITCH_FREE_PER_HOUR", "8"))
SWITCH_PENALTY = int(os.environ.get("WAMSS_FOCUS_SWITCH_PENALTY", "1"))
FRAGMENT_SHORT_SEGMENT_MINUTES = int(os.environ.get("WAMSS_FOCUS_FRAGMENT_MINUTES", "3"))
FRAGMENT_FREE_ALLOWANCE = int(os.environ.get("WAMSS_FOCUS_FRAGMENT_FREE", "6"))
FRAGMENT_PENALTY = int(os.environ.get("WAMSS_FOCUS_FRAGMENT_PENALTY", "2"))
IDLE_PENALTY_RATIO = int(os.environ.get("WAMSS_FOCUS_IDLE_PENALTY_RATIO", "30"))
STREAK_FOCUS_THRESHOLD = int(os.environ.get("WAMSS_FOCUS_STREAK_THRESHOLD", "70"))
BREAK_MIN_MINUTES = int(os.environ.get("WAMSS_FOCUS_BREAK_MIN_MINUTES", "5"))
BREAK_PER_ACTIVE_MINUTES = int(os.environ.get("WAMSS_FOCUS_BREAK_PER_ACTIVE_MINUTES", "90"))


# ---------------------------------------------------------------- pure scoring logic

class Segment:
    """Minimal activity segment used by the focus scorer (unit-test friendly)."""

    def __init__(self, start_time: datetime, end_time: datetime,
                 app_name: str = "", is_idle: bool = False) -> None:
        self.start_time = start_time
        self.end_time = end_time
        self.app_name = app_name or ""
        self.is_idle = is_idle

    @property
    def duration_minutes(self) -> float:
        return max(0.0, (self.end_time - self.start_time).total_seconds() / 60.0)


def _active_stretches(active: List[Segment]) -> List[List[Segment]]:
    """Merge consecutive active segments into uninterrupted stretches.

    A gap of at most MERGE_GAP_MINUTES between segments keeps them in the same
    stretch.
    """
    stretches: List[List[Segment]] = []
    for seg in sorted(active, key=lambda s: s.start_time):
        if stretches and (seg.start_time - stretches[-1][-1].end_time).total_seconds() / 60.0 <= MERGE_GAP_MINUTES:
            stretches[-1].append(seg)
        else:
            stretches.append([seg])
    return stretches


def compute_focus_score(segments: List[Segment]) -> dict:
    """
    Pure, unit-testable focus scorer. Returns a breakdown dict including the
    final clamped score (0-100).

    segments: activity segments for one employee for one day (any source that
    exposes start_time, end_time, app_name, is_idle attributes works; rows of
    app.activity_models.ActivityLog can be converted with
    segment_from_activity_log()).
    """
    active = [s for s in segments if not s.is_idle]
    idle = [s for s in segments if s.is_idle]

    breakdown = {
        "focused_blocks": 0,
        "focus_reward": 0,
        "app_switches": 0,
        "switch_free_allowance": 0,
        "switch_penalty": 0,
        "short_segments": 0,
        "fragment_penalty": 0,
        "idle_fraction": 0.0,
        "idle_penalty": 0,
        "active_minutes": 0.0,
    }

    if not segments:
        breakdown["score"] = 0
        return breakdown

    # Reward: long uninterrupted active stretches (deep-work blocks)
    stretches = _active_stretches(active)
    focused_blocks = sum(
        1
        for st in stretches
        if sum(s.duration_minutes for s in st) >= FOCUS_BLOCK_MINUTES
    )
    focused_blocks = min(focused_blocks, FOCUS_BLOCK_REWARD_CAP)
    breakdown["focused_blocks"] = focused_blocks
    breakdown["focus_reward"] = focused_blocks * FOCUS_BLOCK_REWARD

    # Penalty: rapid app switching
    ordered = sorted(active, key=lambda s: s.start_time)
    switches = sum(1 for prev, cur in zip(ordered, ordered[1:]) if cur.app_name != prev.app_name)
    active_hours = max(1.0, sum(s.duration_minutes for s in ordered) / 60.0)
    free = int(active_hours * SWITCH_FREE_ALLOWANCE_PER_HOUR)
    excess_switches = max(0, switches - free)
    breakdown["app_switches"] = switches
    breakdown["switch_free_allowance"] = free
    breakdown["switch_penalty"] = excess_switches * SWITCH_PENALTY

    # Penalty: fragmentation (many very short segments)
    short = [s for s in ordered if s.duration_minutes < FRAGMENT_SHORT_SEGMENT_MINUTES]
    excess_short = max(0, len(short) - FRAGMENT_FREE_ALLOWANCE)
    breakdown["short_segments"] = len(short)
    breakdown["fragment_penalty"] = excess_short * FRAGMENT_PENALTY

    # Penalty: idle-heavy days (mild)
    idle_fraction = len(idle) / len(segments) if segments else 0.0
    idle_penalty = int(round(idle_fraction * IDLE_PENALTY_RATIO))
    breakdown["idle_fraction"] = round(idle_fraction, 3)
    breakdown["idle_penalty"] = idle_penalty

    breakdown["active_minutes"] = round(sum(s.duration_minutes for s in ordered), 1)

    raw = (
        100
        + breakdown["focus_reward"]
        - breakdown["switch_penalty"]
        - breakdown["fragment_penalty"]
        - breakdown["idle_penalty"]
    )
    breakdown["score"] = max(0, min(100, int(round(raw))))
    return breakdown


def segment_from_activity_log(row) -> Segment:
    """Convert an ActivityLog row (or compatible object) to a Segment."""
    return Segment(
        start_time=row.start_time,
        end_time=row.end_time,
        app_name=row.app_name,
        is_idle=row.is_idle,
    )


def break_compliance(segments: List[Segment]) -> dict:
    """
    Healthy break compliance for one day: at least one idle segment of at
    least BREAK_MIN_MINUTES for every BREAK_PER_ACTIVE_MINUTES of active work.
    Returns {required_breaks, taken_breaks, compliant}.
    """
    active_minutes = sum(s.duration_minutes for s in segments if not s.is_idle)
    taken = sum(
        1 for s in segments if s.is_idle and s.duration_minutes >= BREAK_MIN_MINUTES
    )
    required = 0 if active_minutes <= 0 else int(-(-active_minutes // BREAK_PER_ACTIVE_MINUTES))
    return {
        "required_breaks": required,
        "taken_breaks": taken,
        "compliant": active_minutes > 0 and taken >= required,
    }


# ---------------------------------------------------------------- schemas

class FocusDailyOut(BaseModel):
    employee_id: str
    date: date
    focus_score: int = Field(..., ge=0, le=100)
    breakdown: dict
    break_compliance: dict


class FocusStreaksOut(BaseModel):
    employee_id: str
    focus_streak_days: int
    break_streak_days: int
    badges: List[str]


# ---------------------------------------------------------------- day-level computation over ActivityLog rows

def _day_bounds(day: date):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _segments_for_day(db: Session, employee_id: str, day: date) -> List[Segment]:
    start, end = _day_bounds(day)
    rows = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.employee_id == employee_id,
            ActivityLog.event_type == "activity",
            ActivityLog.start_time >= start,
            ActivityLog.start_time < end,
        )
        .order_by(ActivityLog.start_time)
        .all()
    )
    return [segment_from_activity_log(r) for r in rows]


def daily_focus(db: Session, employee_id: str, day: date) -> dict:
    segments = _segments_for_day(db, employee_id, day)
    score = compute_focus_score(segments)
    breaks = break_compliance(segments)
    return {
        "employee_id": employee_id,
        "date": day,
        "focus_score": score["score"],
        "breakdown": score,
        "break_compliance": breaks,
    }


def _score_for_day(db: Session, employee_id: str, day: date) -> Optional[int]:
    segments = _segments_for_day(db, employee_id, day)
    if not segments:
        return None
    return compute_focus_score(segments)["score"]


def _break_ok_for_day(db: Session, employee_id: str, day: date) -> Optional[bool]:
    segments = _segments_for_day(db, employee_id, day)
    if not segments:
        return None
    return break_compliance(segments)["compliant"]


def _count_streak(db: Session, employee_id: str, predicate) -> int:
    """
    Count consecutive calendar days backwards from today for which predicate
    returns True. If today has no data yet, the streak starts from yesterday.
    """
    streak = 0
    day = date.today()
    value = predicate(db, employee_id, day)
    if value is None:
        day = day - timedelta(days=1)
        value = predicate(db, employee_id, day)
    while value is True:
        streak += 1
        day = day - timedelta(days=1)
        value = predicate(db, employee_id, day)
        if value is None:
            break
    return streak


def badges_for_streaks(focus_streak_days: int, break_streak_days: int) -> List[str]:
    badges: List[str] = []
    if focus_streak_days >= 3:
        badges.append("Focused Starter")
    if focus_streak_days >= 7:
        badges.append("Deep Work Pro")
    if focus_streak_days >= 30:
        badges.append("Zen Master")
    if break_streak_days >= 5:
        badges.append("Well Rested")
    if break_streak_days >= 14:
        badges.append("Break Champion")
    return badges


# ---------------------------------------------------------------- endpoints

@router.get("/daily", response_model=FocusDailyOut)
def get_daily_focus(
    employee_id: str = Query(..., description="Employee ID (required)"),
    date_: Optional[date] = Query(None, alias="date", description="Date (YYYY-MM-DD); defaults to today"),
    db: Session = Depends(get_db),
):
    """Daily focus score for one employee, computed on the fly from ActivityLog rows."""
    if not _ACTIVITY_MODEL_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Activity module not available; focus scores cannot be computed",
        )
    day = date_ or date.today()
    try:
        return daily_focus(db, employee_id, day)
    except SQLAlchemyError as exc:
        logger.error("failed to compute focus score for %s: %s", employee_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to compute focus score",
        )


@router.get("/streaks", response_model=FocusStreaksOut)
def get_focus_streaks(
    employee_id: str = Query(..., description="Employee ID (required)"),
    db: Session = Depends(get_db),
):
    """Focus streaks and earned badges for one employee."""
    if not _ACTIVITY_MODEL_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Activity module not available; streaks cannot be computed",
        )
    try:
        focus_streak = _count_streak(
            db, employee_id,
            lambda d, e, day: (lambda s: s is not None and s >= STREAK_FOCUS_THRESHOLD)(
                _score_for_day(d, e, day)),
        )
        break_streak = _count_streak(db, employee_id, _break_ok_for_day)
    except SQLAlchemyError as exc:
        logger.error("failed to compute streaks for %s: %s", employee_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to compute streaks",
        )
    return FocusStreaksOut(
        employee_id=employee_id,
        focus_streak_days=focus_streak,
        break_streak_days=break_streak,
        badges=badges_for_streaks(focus_streak, break_streak),
    )
