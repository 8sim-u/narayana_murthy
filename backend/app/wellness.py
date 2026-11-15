"""Employee-owned wellness and self-coaching APIs.

Health data uses a separate, revocable consent record and a per-person token.
Live wearable readings remain in process memory for at most 90 seconds; only
employee-submitted daily summaries and completed-session aggregates are
persisted. A separate employee opt-in and server-configured manager roster may
enable a generic supportive check-in; raw health data never leaves the personal view.
"""

from __future__ import annotations

import logging
import os
import hashlib
import hmac
import json
import re
import threading
import uuid
from functools import wraps
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity_models import ActivityLog
from app.database import get_db
from app.employee_auth import configured_employee_ids, require_employee
from app.wellness_models import (
    BreathingSession,
    DailyOutcome,
    ManagerAlertConsent,
    PredictionFeedback,
    WellnessBreak,
    WellnessConsent,
    WellnessDaily,
    SleepSession,
)

logger = logging.getLogger("wamss.api.wellness")
router = APIRouter(prefix="/wellness", tags=["wellness"])

WELLNESS_CONSENT_VERSION = "1"
MANAGER_ALERT_CONSENT_VERSION = "1"
LIVE_SAMPLE_TTL_SECONDS = 90
MAX_HISTORY_DAYS = 28
ACTIVITY_FORECAST_MIN_DAYS = 4
ACTIVITY_FORECAST_WEEKLY_DROP_PCT = 10.0
ACTIVITY_FORECAST_BASELINE_DROP_PCT = 20.0

# Ephemeral by design: process restart/disconnect means there is no live sample.
_wellness_state_lock = threading.RLock()
_live_lock = threading.Lock()
_live_samples: Dict[str, Dict] = {}
_breathing_lock = threading.Lock()
_breathing_sessions: Dict[str, Dict] = {}
_live_expirer_stop = threading.Event()
_live_expirer_thread: Optional[threading.Thread] = None


def _expire_ephemeral_state() -> None:
    """Purge expired live samples and abandoned breathing sessions from RAM."""
    while not _live_expirer_stop.wait(1.0):
        now = datetime.now(timezone.utc)
        with _live_lock:
            for employee_id, sample in list(_live_samples.items()):
                if (now - sample["received_at"]).total_seconds() >= LIVE_SAMPLE_TTL_SECONDS:
                    _live_samples.pop(employee_id, None)
        with _breathing_lock:
            for session_id, session in list(_breathing_sessions.items()):
                if (now - session["started_at"]).total_seconds() > 1200:
                    _breathing_sessions.pop(session_id, None)


def start_live_sample_expirer() -> None:
    """Start the small process-local TTL cleaner from the application lifespan."""
    global _live_expirer_thread
    if _live_expirer_thread is not None and _live_expirer_thread.is_alive():
        return
    _live_expirer_stop.clear()
    _live_expirer_thread = threading.Thread(
        target=_expire_ephemeral_state, name="wellness-ttl-cleaner", daemon=True,
    )
    _live_expirer_thread.start()


def stop_live_sample_expirer() -> None:
    """Stop the process-local TTL cleaner during application shutdown."""
    global _live_expirer_thread
    _live_expirer_stop.set()
    thread = _live_expirer_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2)
    _live_expirer_thread = None


def _fresh_live_sample(employee_id: str, now: Optional[datetime] = None) -> Optional[Dict]:
    now = now or datetime.now(timezone.utc)
    with _live_lock:
        sample = _live_samples.get(employee_id)
        if sample is None:
            return None
        received_age = (now - sample["received_at"]).total_seconds()
        observed_age = (now - sample["observed_at"]).total_seconds()
        if received_age >= LIVE_SAMPLE_TTL_SECONDS or observed_age > LIVE_SAMPLE_TTL_SECONDS or observed_age < -30:
            _live_samples.pop(employee_id, None)
            return None
        return dict(sample)


def _serialize_wellness_state(function):
    """Keep consent revocation ordered with health writes in this API process."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _wellness_state_lock:
            return function(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- auth and consent


def _has_wellness_consent(db: Session, employee_id: str) -> bool:
    row = db.query(WellnessConsent).filter_by(employee_id=employee_id).first()
    return bool(row and row.accepted and row.version == WELLNESS_CONSENT_VERSION)


def _require_wellness_consent(db: Session, employee_id: str) -> None:
    if not _has_wellness_consent(db, employee_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Separate wellness consent is required for health data",
        )


def _unique_json_object(pairs):
    """Reject duplicate keys in administrator-provided auth configuration."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _manager_rosters_by_token_hash() -> Dict[str, List[str]]:
    """Load manager-token hashes mapped only to recognized, server-owned rosters."""
    raw = os.environ.get("WAMSS_MANAGER_ROSTER_TOKEN_HASHES", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (TypeError, ValueError):
        logger.error("WAMSS_MANAGER_ROSTER_TOKEN_HASHES must be valid JSON without duplicate keys")
        return {}
    if not isinstance(data, dict) or not data:
        logger.error("WAMSS_MANAGER_ROSTER_TOKEN_HASHES must be a non-empty JSON object")
        return {}

    known_employee_ids = configured_employee_ids()
    if not known_employee_ids:
        logger.error("Manager check-ins require a valid employee token map")
        return {}

    rosters: Dict[str, List[str]] = {}
    for raw_digest, employee_ids in data.items():
        if not isinstance(raw_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", raw_digest):
            logger.error("WAMSS_MANAGER_ROSTER_TOKEN_HASHES contains an invalid token hash")
            return {}
        digest = raw_digest.lower()
        if digest in rosters or not isinstance(employee_ids, list) or not employee_ids or len(employee_ids) > 100:
            logger.error("WAMSS_MANAGER_ROSTER_TOKEN_HASHES contains an invalid roster")
            return {}
        roster: List[str] = []
        for employee_id in employee_ids:
            if not isinstance(employee_id, str) or not employee_id.strip() or len(employee_id.strip()) > 64:
                logger.error("WAMSS_MANAGER_ROSTER_TOKEN_HASHES contains an invalid employee ID")
                return {}
            normalized_id = employee_id.strip()
            if normalized_id in roster or normalized_id not in known_employee_ids:
                logger.error("Manager roster contains duplicate or unconfigured employee IDs")
                return {}
            roster.append(normalized_id)
        rosters[digest] = roster
    return rosters


def require_manager_roster(
    x_manager_token: Optional[str] = Header(None, alias="X-Manager-Token"),
) -> List[str]:
    """Resolve a manager credential to an administrator-configured roster."""
    if not x_manager_token or len(x_manager_token) > 512:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Manager token required")
    rosters = _manager_rosters_by_token_hash()
    if not rosters:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Manager roster access is not configured",
        )
    digest = hashlib.sha256(x_manager_token.encode("utf-8")).hexdigest()
    matches = [roster for expected, roster in rosters.items() if hmac.compare_digest(digest, expected)]
    if len(matches) != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid manager token")
    return matches[0]


def _has_manager_alert_consent(db: Session, employee_id: str) -> bool:
    row = db.query(ManagerAlertConsent).filter_by(employee_id=employee_id).first()
    return bool(
        _has_wellness_consent(db, employee_id)
        and row and row.accepted and row.version == MANAGER_ALERT_CONSENT_VERSION
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "X-Employee-Token"


def _no_store_manager(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "X-Manager-Token"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _timezone():
    try:
        return ZoneInfo(os.environ.get("WAMSS_TIMEZONE", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Invalid WAMSS_TIMEZONE; falling back to UTC")
        return timezone.utc


def _timezone_name() -> str:
    zone = _timezone()
    return getattr(zone, "key", "UTC")


def _today() -> date:
    return datetime.now(_timezone()).date()


def _local_day(value: datetime) -> date:
    return _utc(value).astimezone(_timezone()).date()


def _retention_days() -> int:
    try:
        return max(1, int(os.environ.get("WAMSS_RETENTION_DAYS", "90")))
    except ValueError:
        return 90


# ---------------------------------------------------------------- request models


class ConsentIn(BaseModel):
    accepted: bool


class DailyMetricsIn(BaseModel):
    day: date
    rmssd_ms: Optional[float] = Field(None, ge=1, le=300)
    rmssd_source: Literal["manual", "wearable_short_window", "overnight", "resting"] = "manual"
    resting_hr_bpm: Optional[int] = Field(None, ge=20, le=250)


class SleepSessionIn(BaseModel):
    started_at: datetime
    ended_at: datetime
    source: Literal["manual", "watch"] = "manual"

class LiveSampleIn(BaseModel):
    heart_rate_bpm: int = Field(..., ge=20, le=250)
    rmssd_ms: Optional[float] = Field(None, ge=1, le=300)
    observed_at: datetime


class TaskCountIn(BaseModel):
    day: date
    tasks_completed: int = Field(..., ge=0, le=1000)


class BreathingCompleteIn(BaseModel):
    session_id: str = Field(..., min_length=16, max_length=64)


class BreakEventIn(BaseModel):
    event_type: Literal["suggested", "taken"]


class PredictionFeedbackIn(BaseModel):
    prediction_key: str = Field(..., min_length=1, max_length=128)
    false_positive: bool


# ---------------------------------------------------------------- private aggregation rules


def _daily_activity(
    db: Session, employee_id: str, start_day: date, end_day: date,
    include_strain: bool = True,
) -> Dict[date, Dict[str, float]]:
    """Return only daily active minutes and strain values; never return app/window details."""
    zone = _timezone()
    start = datetime.combine(start_day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    columns = [ActivityLog.start_time, ActivityLog.end_time, ActivityLog.is_idle]
    if include_strain:
        columns.append(ActivityLog.strain_score)
    rows = (
        db.query(*columns)
        .filter(
            ActivityLog.employee_id == employee_id,
            ActivityLog.event_type == "activity",
            ActivityLog.start_time >= start,
            ActivityLog.start_time < end,
        )
        .order_by(ActivityLog.start_time.desc())
        .limit(50000)
        .all()
    )
    result: Dict[date, Dict[str, float]] = {}
    for row in rows:
        day = _local_day(row.start_time)
        item = result.setdefault(day, {"focus_minutes": 0.0, "strain_sum": 0.0, "strain_count": 0.0})
        if not row.is_idle:
            duration = max(0.0, min(1440.0, (_utc(row.end_time) - _utc(row.start_time)).total_seconds() / 60.0))
            item["focus_minutes"] += duration
        if include_strain and row.strain_score is not None:
            item["strain_sum"] += float(row.strain_score)
            item["strain_count"] += 1
    return result


def _trend_from_daily(rows: List[WellnessDaily], today: date) -> Dict:
    """Compare two adjacent 7-day medians, requiring repeated daily readings."""
    recent = [float(r.rmssd_ms) for r in rows if r.rmssd_ms is not None and today - timedelta(days=6) <= r.day <= today]
    previous = [float(r.rmssd_ms) for r in rows if r.rmssd_ms is not None and today - timedelta(days=13) <= r.day <= today - timedelta(days=7)]
    if len(recent) < 4 or len(previous) < 4:
        return {"available": False, "flag": False, "decline_percent": None, "reason": "At least four readings in each of two weeks are needed."}
    baseline = median(previous)
    current = median(recent)
    decline = max(0.0, (baseline - current) * 100.0 / baseline) if baseline > 0 else 0.0
    return {
        "available": True,
        "flag": decline >= 10.0,
        "decline_percent": round(decline, 1),
        "current_week_rmssd_ms": round(current, 1),
        "previous_week_rmssd_ms": round(baseline, 1),
        "reason": "This retrospective threshold compares weekly medians; a flag requires a 10% or larger change and at least four readings in each week. It is not a forecast or a causal explanation.",
    }


def _readiness(row: WellnessDaily, history: List[WellnessDaily], sleep_minutes: Optional[int] = None) -> Optional[int]:
    """Morning self-guidance using recorded sleep and available personal measurements only."""
    weighted = 0.0
    total_weight = 0.0
    if sleep_minutes is not None:
        weighted += 0.5 * min(100.0, sleep_minutes * 100.0 / 480.0)
        total_weight += 0.5
    prior = [float(r.rmssd_ms) for r in history if r.rmssd_ms is not None and r.day < row.day]
    if row.rmssd_ms is not None and len(prior) >= 4:
        baseline = median(prior[-28:])
        if baseline > 0:
            weighted += 0.3 * min(100.0, float(row.rmssd_ms) * 100.0 / baseline)
            total_weight += 0.3
    prior_rest = [float(r.resting_hr_bpm) for r in history if r.resting_hr_bpm is not None and r.day < row.day]
    if row.resting_hr_bpm is not None and len(prior_rest) >= 4:
        baseline = median(prior_rest[-28:])
        score = max(0.0, min(100.0, 100.0 - max(0.0, float(row.resting_hr_bpm) - baseline) * 5.0))
        weighted += 0.2 * score
        total_weight += 0.2
    return int(round(weighted / total_weight)) if total_weight else None


def _activity_window_mean(
    activity: Dict[date, Dict[str, float]], start: date, end: date,
) -> Optional[Tuple[float, int]]:
    """Return active minutes/day and observed days for an inclusive date window."""
    values = [
        record["focus_minutes"]
        for day, record in activity.items()
        if start <= day <= end and record["focus_minutes"] > 0
    ]
    if len(values) < ACTIVITY_FORECAST_MIN_DAYS:
        return None
    return sum(values) / len(values), len(values)


def _activity_dip_forecast(
    activity: Dict[date, Dict[str, float]], today: date,
) -> Optional[Dict]:
    """Flag a transparent personal active-time trend; this is advisory, not validated."""
    current_week_start = today - timedelta(days=today.weekday())
    recent_end = current_week_start - timedelta(days=1)
    recent_start = recent_end - timedelta(days=6)
    previous_end = recent_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    older_end = previous_start - timedelta(days=1)
    older_start = older_end - timedelta(days=6)

    recent = _activity_window_mean(activity, recent_start, recent_end)
    previous = _activity_window_mean(activity, previous_start, previous_end)
    older = _activity_window_mean(activity, older_start, older_end)
    if recent is None or previous is None or older is None:
        return None

    recent_mean, recent_days = recent
    previous_mean, previous_days = previous
    older_mean, older_days = older
    if previous_mean <= 0 or older_mean <= 0:
        return None

    baseline = (previous_mean + older_mean) / 2
    week_over_week_drop = (previous_mean - recent_mean) * 100 / previous_mean
    baseline_drop = (baseline - recent_mean) * 100 / baseline
    if (
        week_over_week_drop < ACTIVITY_FORECAST_WEEKLY_DROP_PCT
        or baseline_drop < ACTIVITY_FORECAST_BASELINE_DROP_PCT
    ):
        return None

    key = f"active-time-dip:v1:{recent_start.isoformat()}"
    reason = (
        f"Simple advisory rule: in the most recent completed Monday-Sunday week, recorded active time averaged {recent_mean:.1f} minutes/day "
        f"from {recent_start.isoformat()} to {recent_end.isoformat()} ({recent_days} active days). "
        f"That is {week_over_week_drop:.1f}% below the previous 7-day average of "
        f"{previous_mean:.1f} minutes/day ({previous_days} active days), and "
        f"{baseline_drop:.1f}% below the mean of the two preceding weekly averages "
        f"({baseline:.1f} minutes/day; {older_days} and {previous_days} active days). "
        f"The rule requires at least {ACTIVITY_FORECAST_MIN_DAYS} active days in each window, "
        f"a {ACTIVITY_FORECAST_WEEKLY_DROP_PCT:.0f}% week-over-week drop, and a "
        f"{ACTIVITY_FORECAST_BASELINE_DROP_PCT:.0f}% drop below that two-week baseline. "
        "This suggests recorded active time may remain lower over the next 7 days; "
        "it is not a validated forecast, performance rating, or explanation of cause."
    )
    return {
        "key": key,
        "kind": "activity_dip_forecast",
        "title": "Possible lower recorded active time next week",
        "reason": reason,
        "next_step": "Check whether the recorded periods match your experience; you can mark this hint as a false positive.",
    }


def _prediction_rows(
    activity: Dict[date, Dict[str, float]], outcomes: Dict[date, int], today: date,
) -> List[Dict]:
    """Build employee-private advisory and retrospective checks from activity and self-reports."""
    previous_start = today - timedelta(days=13)
    previous_end = today - timedelta(days=7)
    current_start = today - timedelta(days=6)
    current_end = today
    predictions: List[Dict] = []

    activity_forecast = _activity_dip_forecast(activity, today)
    if activity_forecast is not None:
        predictions.append(activity_forecast)

    previous_tasks = [outcomes[d] for d in outcomes if previous_start <= d <= previous_end]
    current_tasks = [outcomes[d] for d in outcomes if current_start <= d <= current_end]
    previous_hours = sum(v["focus_minutes"] for d, v in activity.items() if previous_start <= d <= previous_end) / 60.0
    current_hours = sum(v["focus_minutes"] for d, v in activity.items() if current_start <= d <= current_end) / 60.0
    if len(previous_tasks) >= 3 and len(current_tasks) >= 3 and previous_hours > 0 and current_hours >= previous_hours * 0.8:
        before_rate = sum(previous_tasks) / previous_hours
        current_rate = sum(current_tasks) / current_hours
        decline = max(0.0, (before_rate - current_rate) * 100.0 / before_rate) if before_rate > 0 else 0.0
        if decline >= 30.0:
            key = f"task-flow:{previous_start.isoformat()}:{current_end.isoformat()}"
            predictions.append({
                "key": key,
                "kind": "task_flow",
                "title": "Self-reported task-rate change",
                "reason": f"Retrospective rule: your self-reported task count per recorded active hour was {round(decline)}% lower than the previous 7-day window. This comparison requires at least three task reports in each window and recorded active time of at least 80% of the earlier window; it does not establish a cause or measure performance.",
                "next_step": "Review your own task reports if you find this comparison useful.",
            })
    return predictions


def _recent_combined_prompt(db: Session, employee_id: str, daily_rows: List[WellnessDaily], now: datetime) -> Dict:
    """Work strain + personal HRV deviation; only returned by the employee's own endpoint."""
    cutoff = now - timedelta(minutes=90)
    rows = (
        db.query(ActivityLog.strain_score)
        .filter(
            ActivityLog.employee_id == employee_id,
            ActivityLog.event_type == "activity",
            ActivityLog.is_idle.is_(False),
            ActivityLog.start_time >= cutoff,
            ActivityLog.strain_score.isnot(None),
        )
        .order_by(ActivityLog.start_time.desc())
        .limit(3)
        .all()
    )
    scores = [float(r[0]) for r in rows]
    today = _local_day(now)
    current = next((r for r in daily_rows if r.day == today and r.rmssd_ms is not None), None)
    baseline_values = [
        float(r.rmssd_ms) for r in daily_rows
        if r.rmssd_ms is not None and today - timedelta(days=27) <= r.day <= today - timedelta(days=7)
    ]
    if len(scores) < 3 or current is None or len(baseline_values) < 4:
        return {"show_prompt": False, "score": None, "reason": "A prompt needs repeated work-strain and personal HRV data."}
    baseline = median(baseline_values)
    decline = max(0.0, (baseline - float(current.rmssd_ms)) * 100.0 / baseline) if baseline > 0 else 0.0
    hrv_component = min(100.0, decline * 2.0)
    mean_strain = sum(scores) / len(scores)
    combined = round(0.6 * mean_strain + 0.4 * hrv_component)
    sustained = all(score >= 70.0 for score in scores)
    return {
        "show_prompt": sustained and combined >= 70,
        "score": combined,
        "reason": "60% recent work-strain heuristic + 40% personal HRV-deviation heuristic; shown only to you and not a medical measure.",
    }


def _engagement_decline(activity: Dict[date, Dict[str, float]], today: date) -> bool:
    previous_start, previous_end = today - timedelta(days=13), today - timedelta(days=7)
    current_start, current_end = today - timedelta(days=6), today
    old = [v["focus_minutes"] for d, v in activity.items() if previous_start <= d <= previous_end and v["focus_minutes"] > 0]
    recent = [v["focus_minutes"] for d, v in activity.items() if current_start <= d <= current_end and v["focus_minutes"] > 0]
    if len(old) < 4 or len(recent) < 4:
        return False
    before, current = sum(old) / len(old), sum(recent) / len(recent)
    return before > 0 and current <= before * 0.7


# ---------------------------------------------------------------- routes


@router.post("/consent")
@_serialize_wellness_state
def set_wellness_consent(
    payload: ConsentIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Grant or withdraw separate wellness consent; withdrawal deletes health history."""
    now = datetime.now(timezone.utc)
    row = db.query(WellnessConsent).filter_by(employee_id=employee_id).first()
    if row is None:
        row = WellnessConsent(employee_id=employee_id, accepted=payload.accepted,
                              version=WELLNESS_CONSENT_VERSION,
                              accepted_at=now if payload.accepted else None)
        db.add(row)
    else:
        row.accepted = payload.accepted
        row.version = WELLNESS_CONSENT_VERSION
        row.accepted_at = now if payload.accepted else None
        row.updated_at = now
    if not payload.accepted:
        manager_alert = db.query(ManagerAlertConsent).filter_by(employee_id=employee_id).first()
        if manager_alert:
            manager_alert.accepted = False
            manager_alert.accepted_at = None
            manager_alert.version = MANAGER_ALERT_CONSENT_VERSION
            manager_alert.updated_at = now
        db.query(WellnessDaily).filter_by(employee_id=employee_id).delete(synchronize_session=False)
        db.query(SleepSession).filter_by(employee_id=employee_id).delete(synchronize_session=False)
        db.query(BreathingSession).filter_by(employee_id=employee_id).delete(synchronize_session=False)
        db.query(WellnessBreak).filter_by(employee_id=employee_id).delete(synchronize_session=False)
        with _live_lock:
            _live_samples.pop(employee_id, None)
        with _breathing_lock:
            for key in [k for k, v in _breathing_sessions.items() if v["employee_id"] == employee_id]:
                _breathing_sessions.pop(key, None)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("wellness consent update failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not update wellness consent")
    return {"accepted": payload.accepted, "version": WELLNESS_CONSENT_VERSION,
            "health_history_deleted": not payload.accepted}


@router.get("/manager-alert-consent")
def get_manager_alert_consent(
    response: Response,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Return only this employee's separate manager-alert preference state."""
    _no_store(response)
    row = db.query(ManagerAlertConsent).filter_by(employee_id=employee_id).first()
    accepted = bool(
        _has_wellness_consent(db, employee_id)
        and row and row.accepted and row.version == MANAGER_ALERT_CONSENT_VERSION
    )
    return {"accepted": accepted, "version": MANAGER_ALERT_CONSENT_VERSION}


@router.post("/manager-alert-consent")
@_serialize_wellness_state
def set_manager_alert_consent(
    payload: ConsentIn,
    response: Response,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Set the distinct manager-check-in opt-in; enabling requires wellness consent."""
    _no_store(response)
    if payload.accepted:
        _require_wellness_consent(db, employee_id)
    now = datetime.now(timezone.utc)
    row = db.query(ManagerAlertConsent).filter_by(employee_id=employee_id).first()
    if row is None:
        row = ManagerAlertConsent(
            employee_id=employee_id,
            accepted=payload.accepted,
            version=MANAGER_ALERT_CONSENT_VERSION,
            accepted_at=now if payload.accepted else None,
        )
        db.add(row)
    else:
        row.accepted = payload.accepted
        row.version = MANAGER_ALERT_CONSENT_VERSION
        row.accepted_at = now if payload.accepted else None
        row.updated_at = now
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("manager alert consent update failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not update manager alert preference")
    return {"accepted": payload.accepted, "version": MANAGER_ALERT_CONSENT_VERSION}


@router.get("/manager-checkins")
@_serialize_wellness_state
def manager_checkins(
    response: Response,
    employee_ids: List[str] = Depends(require_manager_roster),
    db: Session = Depends(get_db),
):
    """Return a fixed supportive prompt only for opted-in people in the trusted roster."""
    _no_store_manager(response)
    today = _today()
    first_day = today - timedelta(days=13)
    prompts = []
    for employee_id in employee_ids:
        if not _has_manager_alert_consent(db, employee_id):
            continue
        health_rows = (
            db.query(WellnessDaily.day, WellnessDaily.rmssd_ms)
            .filter(
                WellnessDaily.employee_id == employee_id,
                WellnessDaily.day >= first_day,
                WellnessDaily.day <= today,
            )
            .all()
        )
        hrv_trend = _trend_from_daily(health_rows, today)
        if not hrv_trend["available"] or not hrv_trend["flag"]:
            continue
        activity = _daily_activity(db, employee_id, first_day, today, include_strain=False)
        if _engagement_decline(activity, today):
            prompts.append({
                "employee_id": employee_id,
                "message": "A supportive check-in may be helpful.",
            })
    return {"checkins": prompts}


@router.post("/daily")
@_serialize_wellness_state
def import_daily_metrics(
    payload: DailyMetricsIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Import a date-only HRV/resting-HR/sleep summary; no raw overnight stream is stored."""
    _require_wellness_consent(db, employee_id)
    if payload.rmssd_ms is None and payload.resting_hr_bpm is None:
        raise HTTPException(status_code=422, detail="At least one daily metric is required")
    today = _today()
    if payload.day > today or payload.day < today - timedelta(days=_retention_days()):
        raise HTTPException(status_code=422, detail="Date is outside the allowed retention window")
    row = db.query(WellnessDaily).filter_by(employee_id=employee_id, day=payload.day).first()
    if row is None:
        row = WellnessDaily(employee_id=employee_id, day=payload.day)
        db.add(row)
    for field in ("rmssd_ms", "resting_hr_bpm"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    if payload.rmssd_ms is not None:
        row.rmssd_source = payload.rmssd_source
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("daily wellness import failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not save daily wellness summary")
    return {"saved": True, "day": row.day.isoformat()}


@router.post("/sleep-sessions")
@_serialize_wellness_state
def save_sleep_session(payload: SleepSessionIn, employee_id: str = Depends(require_employee), db: Session = Depends(get_db)):
    """Save a voluntarily entered or explicitly imported sleep interval."""
    _require_wellness_consent(db, employee_id)
    start, end = _utc(payload.started_at), _utc(payload.ended_at)
    duration = (end - start).total_seconds()
    now = datetime.now(timezone.utc)
    if duration <= 0 or duration > 86400 or end > now + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="Sleep session must be a positive interval no longer than 24 hours and cannot end in the future")
    local_end = end.astimezone(_timezone()).date()
    if local_end > _today() or local_end < _today() - timedelta(days=_retention_days()):
        raise HTTPException(status_code=422, detail="Session is outside the allowed retention window")
    row = SleepSession(employee_id=employee_id, started_at=start, ended_at=end, source=payload.source)
    db.add(row)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("sleep session save failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not save sleep session")
    return {"saved": True, "source": row.source, "duration_minutes": round(duration / 60), "morning": local_end.isoformat()}

@router.post("/live")
@_serialize_wellness_state
def receive_live_sample(
    payload: LiveSampleIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Keep only the latest opted-in sample in RAM; never spool or persist BPM/RMSSD."""
    _require_wellness_consent(db, employee_id)
    observed = _utc(payload.observed_at)
    now = datetime.now(timezone.utc)
    age = (now - observed).total_seconds()
    if age > 120 or age < -30:
        raise HTTPException(status_code=422, detail="Live sample timestamp is stale or in the future")
    sample = {
        "heart_rate_bpm": payload.heart_rate_bpm,
        "rmssd_ms": payload.rmssd_ms,
        "observed_at": observed,
        "received_at": now,
    }
    with _live_lock:
        _live_samples[employee_id] = sample
    return {"accepted": True, "persisted": False, "expires_after_seconds": LIVE_SAMPLE_TTL_SECONDS}


@router.post("/tasks")
def save_task_count(
    payload: TaskCountIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Voluntary count only; task names, text, and contents are not collected."""
    today = _today()
    if payload.day > today or payload.day < today - timedelta(days=_retention_days()):
        raise HTTPException(status_code=422, detail="Date is outside the allowed retention window")
    row = db.query(DailyOutcome).filter_by(employee_id=employee_id, day=payload.day).first()
    if row is None:
        row = DailyOutcome(employee_id=employee_id, day=payload.day, tasks_completed=payload.tasks_completed)
        db.add(row)
    else:
        row.tasks_completed = payload.tasks_completed
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("daily outcome save failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not save your task count")
    return {"saved": True, "day": row.day.isoformat(), "tasks_completed": row.tasks_completed}


@router.post("/breathing/start")
@_serialize_wellness_state
def start_breathing_session(
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    _require_wellness_consent(db, employee_id)
    now = datetime.now(timezone.utc)
    latest = _fresh_live_sample(employee_id, now)
    before = latest.get("rmssd_ms") if latest is not None else None
    # Expired sessions are durably classified as abandoned; unfinished timing
    # details and sensor values are never exposed in manager views.
    db.query(BreathingSession).filter(
        BreathingSession.employee_id == employee_id,
        BreathingSession.status == "started",
        BreathingSession.started_at < now - timedelta(minutes=20),
    ).update({BreathingSession.status: "abandoned"}, synchronize_session=False)
    session_id = uuid.uuid4().hex
    row = BreathingSession(employee_id=employee_id, day=_local_day(now), duration_seconds=0,
                           status="started", started_at=now, session_key=session_id)
    db.add(row)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("breathing session start failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not start breathing session")
    with _breathing_lock:
        for key in [k for k, v in _breathing_sessions.items()
                    if (now - v["started_at"]).total_seconds() > 1200]:
            _breathing_sessions.pop(key, None)
        _breathing_sessions[session_id] = {
            "employee_id": employee_id,
            "started_at": now,
            "rmssd_before_ms": before,
            "rmssd_before_observed_at": latest.get("observed_at") if latest is not None else None,
        }
    return {"session_id": session_id, "duration_seconds": 60,
            "rmssd_before_available": before is not None}


@router.post("/breathing/complete")
@_serialize_wellness_state
def complete_breathing_session(
    payload: BreathingCompleteIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    _require_wellness_consent(db, employee_id)
    now = datetime.now(timezone.utc)
    with _breathing_lock:
        session = _breathing_sessions.get(payload.session_id)
        if not session or session["employee_id"] != employee_id:
            raise HTTPException(status_code=404, detail="Breathing session not found")
        elapsed = int((now - session["started_at"]).total_seconds())
        if elapsed < 60 or elapsed > 1200:
            raise HTTPException(status_code=422, detail="Session duration is outside the allowed range")
    row = db.query(BreathingSession).filter_by(employee_id=employee_id, session_key=payload.session_id,
                                               status="started").first()
    if row is None:
        raise HTTPException(status_code=404, detail="Breathing session not found")
    latest = _fresh_live_sample(employee_id, now)
    after = latest.get("rmssd_ms") if latest is not None else None
    before = session.get("rmssd_before_ms")
    before_observed = session.get("rmssd_before_observed_at")
    after_observed = latest.get("observed_at") if latest is not None else None
    reason = None
    if before is None:
        reason = "A fresh before-session RMSSD sample was unavailable."
    elif after is None:
        reason = "A fresh after-session RMSSD sample was unavailable."
    elif after_observed is None or after_observed <= session["started_at"] or after_observed == before_observed:
        reason = "No new fresh RMSSD sample was received after this session began."
    row.status = "completed"
    row.day = _local_day(now)
    row.duration_seconds = min(elapsed, 600)
    row.rmssd_before_ms = before
    row.rmssd_after_ms = after
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("breathing session save failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not save completed session")
    with _breathing_lock:
        _breathing_sessions.pop(payload.session_id, None)
    return {
        "completed": True,
        "status": "completed",
        "sensor_readings_available": reason is None,
        "reason": reason,
        "duration_seconds": row.duration_seconds,
        "rmssd_before_ms": row.rmssd_before_ms,
        "rmssd_after_ms": row.rmssd_after_ms,
    }


@router.post("/breathing/cancel")
@_serialize_wellness_state
def cancel_breathing_session(
    payload: BreathingCompleteIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    _require_wellness_consent(db, employee_id)
    row = db.query(BreathingSession).filter_by(employee_id=employee_id, session_key=payload.session_id,
                                               status="started").first()
    if row is None:
        raise HTTPException(status_code=404, detail="Breathing session not found")
    row.status = "cancelled"
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("breathing session cancellation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not cancel breathing session")
    with _breathing_lock:
        _breathing_sessions.pop(payload.session_id, None)
    return {"status": "cancelled", "completed": False}


@router.post("/breaks")
@_serialize_wellness_state
def record_wellness_break(
    payload: BreakEventIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    _require_wellness_consent(db, employee_id)
    row = db.query(WellnessBreak).filter_by(
        employee_id=employee_id, day=_today(), event_type=payload.event_type,
    ).first()
    if row is None:
        db.add(WellnessBreak(employee_id=employee_id, day=_today(), event_type=payload.event_type))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # idempotent if the browser posts twice
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("wellness break log failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Could not update your break summary")
    return {"logged": True, "event_type": payload.event_type, "day": _today().isoformat()}


@router.post("/prediction-feedback")
def record_prediction_feedback(
    payload: PredictionFeedbackIn,
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    today = _today()
    current_week_start = today - timedelta(days=today.weekday())
    activity = _daily_activity(db, employee_id, current_week_start - timedelta(days=21), today)
    outcomes_rows = db.query(DailyOutcome).filter(
        DailyOutcome.employee_id == employee_id,
        DailyOutcome.day >= today - timedelta(days=13),
        DailyOutcome.day <= today,
    ).all()
    predictions = _prediction_rows(activity, {r.day: r.tasks_completed for r in outcomes_rows}, today)
    if payload.prediction_key not in {p["key"] for p in predictions}:
        raise HTTPException(status_code=404, detail="Prediction is no longer current")
    row = db.query(PredictionFeedback).filter_by(
        employee_id=employee_id, prediction_key=payload.prediction_key,
    ).first()
    if row is None:
        row = PredictionFeedback(employee_id=employee_id, prediction_key=payload.prediction_key,
                                 false_positive=payload.false_positive)
        db.add(row)
    else:
        row.false_positive = payload.false_positive
        row.created_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("prediction feedback save failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not save prediction feedback")
    return {"saved": True, "false_positive": row.false_positive}


@router.get("/me")
@_serialize_wellness_state
def get_personal_dashboard(
    response: Response,
    days: int = Query(28, ge=1, le=MAX_HISTORY_DAYS),
    employee_id: str = Depends(require_employee),
    db: Session = Depends(get_db),
):
    """Return aggregate self-view data only for the employee bound to their token."""
    _no_store(response)
    today = _today()
    start_day = today - timedelta(days=days - 1)
    activity = _daily_activity(db, employee_id, start_day, today)
    outcomes_rows = db.query(DailyOutcome).filter(
        DailyOutcome.employee_id == employee_id,
        DailyOutcome.day >= start_day,
        DailyOutcome.day <= today,
    ).all()
    outcomes = {r.day: r.tasks_completed for r in outcomes_rows}
    calendar_days = [start_day + timedelta(days=i) for i in range(days)]
    activity_rows = [
        {"day": d.isoformat(), "focus_minutes": round(activity.get(d, {}).get("focus_minutes", 0.0), 1),
         "tasks_completed": outcomes.get(d)}
        for d in calendar_days
    ]
    predictions = _prediction_rows(activity, outcomes, today)
    prediction_keys = [p["key"] for p in predictions]
    feedback_rows = []
    if prediction_keys:
        feedback_rows = db.query(PredictionFeedback).filter(
            PredictionFeedback.employee_id == employee_id,
            PredictionFeedback.prediction_key.in_(prediction_keys),
        ).all()
    feedback_by_key = {r.prediction_key: r.false_positive for r in feedback_rows}
    for prediction in predictions:
        prediction["false_positive"] = feedback_by_key.get(prediction["key"])
    feedback_count = db.query(PredictionFeedback).filter(
        PredictionFeedback.employee_id == employee_id,
        PredictionFeedback.false_positive.is_(True),
        PredictionFeedback.created_at >= datetime.now(timezone.utc) - timedelta(days=_retention_days()),
    ).count()

    consented = _has_wellness_consent(db, employee_id)
    health_daily: List[Dict] = []
    live = None
    trend = {"available": False, "flag": False, "decline_percent": None,
             "reason": "Wellness consent is off."}
    combined = {"show_prompt": False, "score": None, "reason": "Wellness consent is off."}
    breathing_count = 0
    breaks_today = {"suggested": False, "taken": False}
    if consented:
        db.query(BreathingSession).filter(
            BreathingSession.employee_id == employee_id,
            BreathingSession.status == "started",
            BreathingSession.started_at < datetime.now(timezone.utc) - timedelta(minutes=20),
        ).update({BreathingSession.status: "abandoned"}, synchronize_session=False)
        db.commit()
        all_daily = db.query(WellnessDaily).filter(
            WellnessDaily.employee_id == employee_id,
            WellnessDaily.day >= today - timedelta(days=27),
            WellnessDaily.day <= today,
        ).order_by(WellnessDaily.day.asc()).all()
        visible_daily = [r for r in all_daily if r.day >= start_day]
        sleep_rows = db.query(SleepSession).filter(SleepSession.employee_id == employee_id).order_by(SleepSession.ended_at.asc()).all()
        sleep_by_morning: Dict[date, int] = {}
        visible_sessions = []
        for session in sleep_rows:
            morning = _local_day(session.ended_at)
            if morning < start_day or morning > today:
                continue
            minutes = round((_utc(session.ended_at) - _utc(session.started_at)).total_seconds() / 60)
            sleep_by_morning[morning] = sleep_by_morning.get(morning, 0) + minutes
            visible_sessions.append({"started_at": _utc(session.started_at).isoformat(), "ended_at": _utc(session.ended_at).isoformat(), "duration_minutes": minutes, "source": session.source})
        health_daily = [
            {"day": r.day.isoformat(), "rmssd_ms": r.rmssd_ms,
             "rmssd_source": r.rmssd_source or "manual",
             "resting_hr_bpm": r.resting_hr_bpm, "sleep_minutes": sleep_by_morning.get(r.day),
             "readiness": _readiness(r, all_daily, sleep_by_morning[r.day]) if r.day in sleep_by_morning else None}
            for r in visible_daily
        ]
        existing_health_days = {entry["day"] for entry in health_daily}
        for morning, minutes in sleep_by_morning.items():
            if morning.isoformat() not in existing_health_days:
                placeholder = WellnessDaily(employee_id=employee_id, day=morning)
                health_daily.append({"day": morning.isoformat(), "rmssd_ms": None, "rmssd_source": "manual", "resting_hr_bpm": None, "sleep_minutes": minutes, "readiness": _readiness(placeholder, all_daily, minutes)})
        health_daily.sort(key=lambda entry: entry["day"])
        trend = _trend_from_daily(all_daily, today)
        combined = _recent_combined_prompt(db, employee_id, all_daily, datetime.now(timezone.utc))
        sample = _fresh_live_sample(employee_id)
        if sample is not None:
            live = {"heart_rate_bpm": sample["heart_rate_bpm"],
                    "rmssd_ms": sample.get("rmssd_ms"),
                    "observed_at": sample["observed_at"].isoformat()}
        breathing_count = db.query(BreathingSession).filter(
            BreathingSession.employee_id == employee_id,
            BreathingSession.day >= start_day,
            BreathingSession.day <= today,
            BreathingSession.status == "completed",
        ).count()
        breaks = db.query(WellnessBreak).filter_by(employee_id=employee_id, day=today).all()
        breaks_today = {"suggested": any(r.event_type == "suggested" for r in breaks),
                        "taken": any(r.event_type == "taken" for r in breaks)}

    return {
        "employee_id": employee_id,
        "day_timezone": _timezone_name(),
        "days": days,
        "activity": activity_rows,
        "wellness": {
            "consented": consented,
            "daily": health_daily,
            "sleep_sessions": visible_sessions if consented else [],
            "live": live,
            "hrv_trend": trend if consented else None,
            "break_prompt": combined if consented else None,
            "breathing_sessions": breathing_count,
            "breaks_today": breaks_today if consented else None,
        },
        "predictions": predictions,
        "false_positive_reports": feedback_count,
        "data_retention_days": _retention_days(),
    }
