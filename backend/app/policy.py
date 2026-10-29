"""
WAMSS working-time policy engine (self-contained addition).

Wiring (applied by the coordinator, NOT done here):
    from app.policy import router as policy_router
    app.include_router(policy_router)

Policy file: env WAMSS_POLICY_FILE (default: backend/policy.example.yaml).
Violations are computed on the fly from ActivityLog rows; no new table.

Endpoints:
  GET /policy/rules                        active policy rules + privacy metadata
  GET /policy/violations?employee_id=&date= violations for one employee on one date

Pure logic lives in evaluate_policies(activity_segments, rules) so it can be
unit-tested without a database. A segment is a dict:
    {"start": datetime, "end": datetime, "is_idle": bool}
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity_models import ActivityLog
from app.database import get_db

logger = logging.getLogger("wamss.api.policy")

router = APIRouter(prefix="/policy", tags=["policy"])

DEFAULT_POLICY_FILE = Path(__file__).resolve().parent.parent / "policy.example.yaml"
POLICY_ENV_VAR = "WAMSS_POLICY_FILE"
RETENTION_ENV_VAR = "WAMSS_RETENTION_DAYS"
EXCLUDED_APPS_ENV_VAR = "WAMSS_EXCLUDED_APPS"
EXCLUDED_TITLES_ENV_VAR = "WAMSS_EXCLUDED_TITLES"


def _policy_file() -> Path:
    return Path(os.environ.get(POLICY_ENV_VAR, str(DEFAULT_POLICY_FILE)))


def default_rules() -> Dict[str, Any]:
    """Built-in defaults used when no policy file is present."""
    return {
        "timezone": "UTC",
        "work_hours": {"no_work_before": "06:00", "no_work_after": "21:00"},
        "lunch_break": {"window_start": "12:30", "window_end": "13:30", "min_break_minutes": 30},
        "continuous_work": {"max_continuous_minutes": 120, "break_reset_gap_minutes": 15},
        "daily": {"max_active_hours": 10},
    }


_policy_cache: Dict[str, Any] = {"mtime": None, "rules": None}


def load_rules() -> Dict[str, Any]:
    """Load (and cache) the policy YAML; falls back to built-in defaults."""
    try:
        import yaml  # local import so the module imports without the dep
    except ImportError as exc:
        raise RuntimeError("pyyaml is required to load the policy file") from exc

    path = _policy_file()
    if not path.exists():
        logger.warning("policy file %s not found; using built-in defaults", path)
        return default_rules()

    mtime = path.stat().st_mtime
    if _policy_cache["rules"] is not None and _policy_cache["mtime"] == mtime:
        return _policy_cache["rules"]

    with path.open("r", encoding="utf-8") as fh:
        rules = yaml.safe_load(fh) or {}
    _policy_cache.update(mtime=mtime, rules=rules)
    logger.info("loaded policy rules from %s", path)
    return rules


# ------------------------------------------------------------------ helpers

def _parse_clock(value: Any) -> Optional[time]:
    if value is None:
        return None
    try:
        hour, minute = str(value).split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def _to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _overlap_minutes(start: datetime, end: datetime, win_start: datetime, win_end: datetime) -> float:
    latest_start = max(start, win_start)
    earliest_end = min(end, win_end)
    if earliest_end <= latest_start:
        return 0.0
    return (earliest_end - latest_start).total_seconds() / 60.0


def _active_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active = [
        s for s in segments
        if not s.get("is_idle") and s.get("start") is not None and s.get("end") is not None
        and s["end"] > s["start"]
    ]
    active.sort(key=lambda s: s["start"])
    return active


# ------------------------------------------------------------------ evaluation

def evaluate_policies(
    activity_segments: List[Dict[str, Any]],
    rules: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Pure function: evaluate working-time rules against activity segments.

    Returns a list of {"policy": name, "detail": message, "severity": "red"|"amber"}.
    Empty segments (a day off, for example) produce no violations.
    """
    violations: List[Dict[str, str]] = []
    active = _active_segments(activity_segments)
    if not active:
        return violations

    tz = ZoneInfo(rules.get("timezone", "UTC"))
    local = [( _to_local(s["start"], tz), _to_local(s["end"], tz)) for s in active]

    # ---- 1. work after hours / before hours ---------------------------------
    work = rules.get("work_hours") or {}
    no_work_before = _parse_clock(work.get("no_work_before"))
    no_work_after = _parse_clock(work.get("no_work_after"))

    def _minutes_outside_window() -> Dict[str, float]:
        totals = {"before": 0.0, "after": 0.0}
        for ls, le in local:
            day = ls.date()
            if no_work_before is not None:
                before_end = datetime.combine(day, no_work_before, tzinfo=tz)
                totals["before"] += _overlap_minutes(ls, le, ls, before_end)
            if no_work_after is not None:
                after_start = datetime.combine(day, no_work_after, tzinfo=tz)
                totals["after"] += _overlap_minutes(ls, le, after_start, le)
        return totals

    outside = _minutes_outside_window()
    if outside["after"] > 0:
        violations.append({
            "policy": "no_work_after_hours",
            "detail": f"{int(round(outside['after']))} min of activity after "
                      f"{no_work_after.strftime('%H:%M') if no_work_after else '?'}",
            "severity": "red",
        })
    if outside["before"] > 0:
        violations.append({
            "policy": "no_work_before_hours",
            "detail": f"{int(round(outside['before']))} min of activity before "
                      f"{no_work_before.strftime('%H:%M') if no_work_before else '?'}",
            "severity": "red",
        })

    # ---- 2. mandatory lunch break --------------------------------------------
    lunch = rules.get("lunch_break") or {}
    win_start = _parse_clock(lunch.get("window_start"))
    win_end = _parse_clock(lunch.get("window_end"))
    min_break = lunch.get("min_break_minutes", 30)
    if win_start is not None and win_end is not None:
        day = local[0][0].date()
        ws = datetime.combine(day, win_start, tzinfo=tz)
        we = datetime.combine(day, win_end, tzinfo=tz)
        window_len = (we - ws).total_seconds() / 60.0
        active_in_window = sum(_overlap_minutes(ls, le, ws, we) for ls, le in local)
        idle_in_window = max(0.0, window_len - active_in_window)
        if idle_in_window < min_break:
            violations.append({
                "policy": "missed_lunch_break",
                "detail": f"only {int(round(idle_in_window))} min of break inside "
                          f"{win_start.strftime('%H:%M')}-{win_end.strftime('%H:%M')} "
                          f"(required {min_break} min)",
                "severity": "amber",
            })

    # ---- 3. max continuous work ----------------------------------------------
    cont = rules.get("continuous_work") or {}
    max_cont = cont.get("max_continuous_minutes", 120)
    reset_gap = cont.get("break_reset_gap_minutes", 15)

    longest_run = 0.0
    run_start: Optional[datetime] = None
    run_end: Optional[datetime] = None
    current = 0.0
    prev_end: Optional[datetime] = None
    for ls, le in local:
        dur = (le - ls).total_seconds() / 60.0
        if prev_end is None:
            run_start, current = ls, dur
        else:
            gap = (ls - prev_end).total_seconds() / 60.0
            if gap >= reset_gap:
                longest_run = max(longest_run, current)
                run_start, current = ls, dur
            else:
                current += gap + dur
        run_end = le
        prev_end = le
    longest_run = max(longest_run, current)

    if longest_run > max_cont:
        violations.append({
            "policy": "max_continuous_work",
            "detail": f"continuous active block of {int(round(longest_run))} min "
                      f"exceeds the {max_cont} min limit (no qualifying break)",
            "severity": "red",
        })

    # ---- 4. max daily active hours --------------------------------------------
    daily = rules.get("daily") or {}
    max_hours = daily.get("max_active_hours")
    total_active = sum((le - ls).total_seconds() / 60.0 for ls, le in local)
    if max_hours is not None and total_active > max_hours * 60:
        violations.append({
            "policy": "max_daily_active_hours",
            "detail": f"{total_active / 60:.1f} h of activity exceeds the "
                      f"{max_hours} h daily limit",
            "severity": "red",
        })

    return violations


# ------------------------------------------------------------------ metadata

def privacy_metadata() -> Dict[str, Any]:
    """Privacy controls summary shown by the dashboard, driven by env vars."""
    excluded_apps = [
        name.strip() for name in os.environ.get(EXCLUDED_APPS_ENV_VAR, "").split(",")
        if name.strip()
    ]
    try:
        retention_days = int(os.environ.get(RETENTION_ENV_VAR, "90"))
    except ValueError:
        retention_days = 90
    return {
        "excluded_apps": excluded_apps,
        "excluded_titles_regex": os.environ.get(EXCLUDED_TITLES_ENV_VAR, "") or None,
        "retention_days": retention_days,
    }


# ------------------------------------------------------------------ endpoints

@router.get("/rules")
def get_rules():
    """Return the loaded policy rules plus privacy metadata for the dashboard."""
    return {
        "policy_file": str(_policy_file()),
        "rules": load_rules(),
        **privacy_metadata(),
    }


@router.get("/violations")
def get_violations(
    employee_id: str = Query(..., description="Employee ID to evaluate"),
    date: date = Query(..., description="Calendar date (YYYY-MM-DD) in the policy timezone"),
    db: Session = Depends(get_db),
):
    """
    Evaluate the policy rules against this employee's activity on the given
    date. Violations are computed on the fly; nothing is persisted.
    """
    rules = load_rules()
    tz = ZoneInfo(rules.get("timezone", "UTC"))
    day_start = datetime(date.year, date.month, date.day, tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    try:
        rows = (
            db.query(ActivityLog)
            .filter(
                ActivityLog.employee_id == employee_id,
                ActivityLog.event_type == "activity",
                ActivityLog.start_time >= day_start.astimezone(timezone.utc),
                ActivityLog.start_time < day_end.astimezone(timezone.utc),
            )
            .order_by(ActivityLog.start_time.asc())
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("failed to query activity for policy check: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to evaluate policies",
        )

    segments = [
        {"start": r.start_time, "end": r.end_time, "is_idle": r.is_idle} for r in rows
    ]
    violations = evaluate_policies(segments, rules)
    return {
        "employee_id": employee_id,
        "date": date.isoformat(),
        "violations": violations,
        "segment_count": len(segments),
    }
