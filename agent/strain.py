"""
Strain (workload tension) scoring for the WAMSS workstation agent.

Heuristic workload signal computed from the activity logs the agent already
collects. This is a simple, documented, tunable formula. It is NOT a medical
measure of stress, fatigue, or any health condition.

Signals (all configurable):
  1. Continuous active work minutes without a break (default threshold 90).
  2. High input intensity bursts: keystrokes + mouse clicks per minute above
     a set rate (default 120/min). Counts only, never key content.
  3. Excessive app/window switching rate (default > 10 switches per 5 min).
  4. Work outside configured work hours (default 09:00-18:00 local time).

Score is 0-100. Level: green (< 40), amber (40-69), red (>= 70).

Env vars (see agent/.env.example):
  WAMSS_STRAIN_ACTIVE_MIN_THRESHOLD   default 90
  WAMSS_STRAIN_INTENSITY_PER_MIN      default 120
  WAMSS_STRAIN_SWITCHES_PER_5MIN      default 10
  WAMSS_WORK_HOURS                    default "09:00-18:00" (local time)
  WAMSS_BREAK_THRESHOLD               default 70  (score that triggers a break reminder)
  WAMSS_BREAK_SNOOZE_MIN              default 15  (suppress repeat reminders)
  WAMSS_BREAK_REMINDER_COOLDOWN_MIN   default 60  (minimum gap between reminders)
  WAMSS_BREAK_RESET_IDLE_MIN          default 5   (idle minutes that reset the
                                                  continuous-active clock)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime


# Component weights in the final score. They sum to 1.0.
W_ACTIVE = 0.30
W_INTENSITY = 0.25
W_SWITCHING = 0.25
W_HOURS = 0.20

LEVEL_GREEN_MAX = 40
LEVEL_AMBER_MAX = 70


def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass
class StrainConfig:
    active_min_threshold: int = 90
    intensity_per_min: int = 120
    switches_per_5min: int = 10
    work_hours: str = "09:00-18:00"
    break_threshold: int = 70
    break_snooze_min: int = 15
    break_reminder_cooldown_min: int = 60
    break_reset_idle_min: int = 5

    @classmethod
    def from_env(cls) -> "StrainConfig":
        return cls(
            active_min_threshold=_getenv_int("WAMSS_STRAIN_ACTIVE_MIN_THRESHOLD", 90),
            intensity_per_min=_getenv_int("WAMSS_STRAIN_INTENSITY_PER_MIN", 120),
            switches_per_5min=_getenv_int("WAMSS_STRAIN_SWITCHES_PER_5MIN", 10),
            work_hours=os.environ.get("WAMSS_WORK_HOURS", "09:00-18:00"),
            break_threshold=_getenv_int("WAMSS_BREAK_THRESHOLD", 70),
            break_snooze_min=_getenv_int("WAMSS_BREAK_SNOOZE_MIN", 15),
            break_reminder_cooldown_min=_getenv_int("WAMSS_BREAK_REMINDER_COOLDOWN_MIN", 60),
            break_reset_idle_min=_getenv_int("WAMSS_BREAK_RESET_IDLE_MIN", 5),
        )

    def outside_work_hours(self, now: datetime | None = None) -> bool:
        """True if local time is outside the configured work-hours window."""
        now = now or datetime.now()
        try:
            start_s, end_s = self.work_hours.split("-", 1)
            sh, sm = (int(x) for x in start_s.split(":"))
            eh, em = (int(x) for x in end_s.split(":"))
        except (ValueError, AttributeError):
            return False
        mins = now.hour * 60 + now.minute
        start = sh * 60 + sm
        end = eh * 60 + em
        if start <= end:
            return not (start <= mins < end)
        return not (mins >= start or mins < end)  # overnight window


@dataclass
class StrainResult:
    score: int  # 0-100
    level: str  # "green" | "amber" | "red"
    components: dict
    outside_work_hours: bool


def score_level(score: int) -> str:
    if score < LEVEL_GREEN_MAX:
        return "green"
    if score < LEVEL_AMBER_MAX:
        return "amber"
    return "red"


def compute_strain_score(
    active_minutes: float,
    input_events_per_min: float,
    switches_per_5min: int,
    outside_work_hours: bool,
    cfg: StrainConfig | None = None,
) -> StrainResult:
    """
    Pure function: workload tension heuristic, 0-100.

    Each signal is normalized to 0.0-1.0 against its configured threshold,
    then combined with fixed documented weights:
        score = 100 * (0.30*active + 0.25*intensity + 0.25*switching + 0.20*hours)

    No OS interaction, no I/O. Safe to unit test.
    """
    cfg = cfg or StrainConfig()
    c_active = min(max(active_minutes, 0.0) / max(cfg.active_min_threshold, 1), 1.0)
    c_intensity = min(max(input_events_per_min, 0.0) / max(cfg.intensity_per_min, 1), 1.0)
    c_switching = min(max(switches_per_5min, 0) / max(cfg.switches_per_5min, 1), 1.0)
    c_hours = 1.0 if outside_work_hours else 0.0

    score = int(round(100.0 * (
        W_ACTIVE * c_active
        + W_INTENSITY * c_intensity
        + W_SWITCHING * c_switching
        + W_HOURS * c_hours
    )))
    score = max(0, min(100, score))
    return StrainResult(
        score=score,
        level=score_level(score),
        components={
            "continuous_active": round(c_active, 3),
            "input_intensity": round(c_intensity, 3),
            "window_switching": round(c_switching, 3),
            "outside_work_hours": c_hours,
        },
        outside_work_hours=outside_work_hours,
    )
