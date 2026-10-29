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

import importlib
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("wamss.agent.strain")


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


# ---------------------------------------------------------------------------
# Round 4: ML stress features, session fatigue, and HR fusion (all optional).
#
# Per scoring cycle, enrich_strain_score() integrates in this order:
#   (a) behavioral features via agent.ml_stress (Agent A, built in parallel)
#       from the agent's in-memory anonymized timing buffers;
#   (b) FatigueTracker check at session end;
#   (c) fusion with the latest wearable HR sample (duck-typed stream;
#       wearable_ble is never hard-imported here, so this module works
#       with or without it);
#   (d) heuristic fallback: when ml_stress is missing or any optional step
#       fails, the original heuristic score is returned unchanged.
#
# Everything in this section is additive. compute_strain_score() and
# StrainConfig above are untouched, and enrich_strain_score() with default
# arguments reproduces the heuristic score exactly when no HR stream is
# passed. Enable the full pipeline in the agent with WAMSS_STRAIN_PIPELINE=1
# (see the wiring proposal in /tmp/round4/activity_agent_wiring.patch).
#
# Privacy: timing buffers keep only inter-event gaps (seconds) in process
# memory. No key identities, no key content, no coordinates, no window
# text. Buffers are never persisted, never spooled, never logged.
# ---------------------------------------------------------------------------

# Guarded import of Agent A's module (built in parallel; may be absent).
# Both the flat layout ("ml_stress", as used by activity_agent.py's
# `from strain import ...` style) and the package layout ("agent.ml_stress",
# per the round-4 contract) are tried. Resolution is lazy per call so tests
# can inject or remove a fake module via sys.modules.
_ML_STRESS_MODULE_NAMES = ("ml_stress", "agent.ml_stress")


def _load_ml_stress():
    """Return the ml_stress module, or None when unavailable."""
    for name in _ML_STRESS_MODULE_NAMES:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


def ml_stress_available() -> bool:
    """True when agent.ml_stress (Agent A) can be imported."""
    return _load_ml_stress() is not None


# Guarded imports of the sibling round-4 modules (mine, always present in
# this tree, but defensive so strain.py never breaks their absence).
try:
    from fatigue import FatigueTracker
except ImportError:  # pragma: no cover - defensive
    FatigueTracker = None  # type: ignore

try:
    from fusion import fuse
except ImportError:  # pragma: no cover - defensive
    fuse = None  # type: ignore


class AnonymizedTimingBuffer:
    """
    In-memory ring buffer of anonymized inter-event intervals (seconds).

    The agent's InputCounter keeps counts only; this buffer adds the timing
    gaps between consecutive input events that keystroke/mouse dynamics
    features need. Only the gap durations are stored: never which key,
    never key content, never mouse coordinates. Process memory only, never
    persisted or spooled. Bounded by capacity (default 600 intervals).
    """

    def __init__(self, capacity: int = 600) -> None:
        self._intervals: Deque[float] = deque(maxlen=max(1, int(capacity)))
        self._last_ts: Optional[float] = None

    def record_event(self, ts: Optional[float] = None) -> None:
        """Record one anonymized input event; stores only the gap to the
        previous event."""
        now = ts if ts is not None else time.time()
        if self._last_ts is not None:
            gap = now - self._last_ts
            if gap >= 0:
                self._intervals.append(gap)
        self._last_ts = now

    def record_interval(self, seconds: float) -> None:
        """Record a precomputed gap directly (tests, synthetic input)."""
        if seconds >= 0:
            self._intervals.append(float(seconds))

    def snapshot(self) -> List[float]:
        """Copy of buffered intervals, oldest first. Does not clear."""
        return list(self._intervals)

    def drain(self) -> List[float]:
        """Copy of buffered intervals, oldest first, then clears the buffer."""
        items = list(self._intervals)
        self._intervals.clear()
        return items

    def __len__(self) -> int:
        return len(self._intervals)


def extract_ml_features(
    key_intervals: Sequence[float] = (),
    mouse_intervals: Sequence[float] = (),
    key_pairs: Optional[Sequence[Tuple[float, float]]] = None,
    mouse_events: Optional[Sequence[Dict]] = None,
) -> Optional[Dict]:
    """
    Best-effort behavioral feature extraction via agent.ml_stress.

    The real ml_stress contract (Agent A) takes anonymized
    (press_ts, release_ts) pairs for keystrokes and
    {"ts","x","y","kind"} dicts for mouse events. When the caller passes
    key_pairs / mouse_events they are used directly. Otherwise they are
    synthesized from the anonymized interval buffers with documented,
    privacy-safe assumptions:

    * key pairs: press times are reconstructed cumulatively from the
      press-gap intervals; release = press + ASSUMED_DWELL_S (0.10 s).
      Dwell features therefore become constant and wash out to z = 0 in
      the rolling baseline, while flight, digraph, pause-structure, and
      velocity features stay informative.
    * mouse events: each click interval becomes a click_down/click_up pair
      with x = y = 0.0. The agent never captures coordinates (privacy),
      so path-geometry features neutralize via z-scoring while click
      count and inter-click timing stay informative.

    Returns the merged feature dict, or None when ml_stress is unavailable
    or extraction raises: the caller then falls back to the heuristic with
    no crash. Only finite numeric values are kept.
    """
    mod = _load_ml_stress()
    if mod is None:
        return None
    try:
        pairs = list(key_pairs) if key_pairs is not None else _pairs_from_intervals(key_intervals)
        events = list(mouse_events) if mouse_events is not None else _mouse_events_from_intervals(mouse_intervals)
        key_feats = mod.extract_keystroke_features(pairs) or {}
        mouse_feats = mod.extract_mouse_features(events) or {}
        merged: Dict = {}
        for source in (key_feats, mouse_feats):
            if isinstance(source, dict):
                for k, v in source.items():
                    if (isinstance(v, (int, float)) and not isinstance(v, bool)
                            and math.isfinite(v)):
                        merged[k] = float(v)
        return merged or None
    except Exception as exc:  # never let ML break the scoring cycle
        logger.debug("ml_stress feature extraction failed, using heuristic: %s", exc)
        return None


# Documented synthesis assumptions (see extract_ml_features).
ASSUMED_DWELL_S = 0.10        # key hold time when only press gaps are known
ASSUMED_CLICK_DWELL_S = 0.05  # click_down -> click_up gap, same case


def _pairs_from_intervals(
    intervals: Sequence[float], dwell: float = ASSUMED_DWELL_S
) -> List[Tuple[float, float]]:
    """Rebuild (press_ts, release_ts) pairs from press-gap intervals."""
    pairs: List[Tuple[float, float]] = []
    t = 0.0
    for i, gap in enumerate(intervals):
        try:
            g = float(gap)
        except (TypeError, ValueError):
            continue
        if g < 0 or not math.isfinite(g):
            continue
        if i > 0:
            t += g
        pairs.append((t, t + dwell))
    return pairs


def _mouse_events_from_intervals(
    intervals: Sequence[float], click_dwell: float = ASSUMED_CLICK_DWELL_S
) -> List[Dict]:
    """Rebuild click_down/click_up event dicts from click-gap intervals.

    x and y are 0.0: the agent never captures coordinates (privacy rule),
    so only count and timing features carry signal here.
    """
    events: List[Dict] = []
    t = 0.0
    for i, gap in enumerate(intervals):
        try:
            g = float(gap)
        except (TypeError, ValueError):
            continue
        if g < 0 or not math.isfinite(g):
            continue
        if i > 0:
            t += g
        events.append({"ts": t, "x": 0.0, "y": 0.0, "kind": "click_down"})
        events.append({"ts": t + click_dwell, "x": 0.0, "y": 0.0, "kind": "click_up"})
    return events


# Rolling baselines, one per employee (the real RollingBaseline docstring
# asks the integrator to keep one instance per user). Reset via
# _reset_round4_state() (tests).
_ML_BASELINES: Dict[str, object] = {}


def _reset_round4_state() -> None:
    """Clear round-4 module caches (rolling baselines, fatigue trackers)."""
    _ML_BASELINES.clear()
    _FATIGUE_TRACKERS.clear()


def ml_behavioral_score(
    features: Optional[Dict], employee_id: Optional[str] = None
) -> Optional[float]:
    """
    Score 0-100 from an ml_stress feature dict.

    Folds the features into the employee's RollingBaseline, z-scores them,
    and calls unsupervised_strain_score(z) per the real ml_stress contract
    (single argument, returns 0-100).

    Returns None when ml_stress is missing, features are empty, the
    baseline is still cold (fewer than min_windows windows: z-scores would
    all be zero and an uninformative 0 must not override the heuristic),
    or scoring raises. Any failure falls back to the heuristic, no crash.
    """
    mod = _load_ml_stress()
    if mod is None or not features:
        return None
    try:
        key = employee_id or "default"
        baseline = _ML_BASELINES.get(key)
        if baseline is None:
            baseline = mod.RollingBaseline()
            _ML_BASELINES[key] = baseline
        update = getattr(baseline, "update", None)
        if callable(update):
            update(features)
        # Cold-start guard: neutral z-scores until the baseline learned.
        min_windows = getattr(baseline, "min_windows", 0) or 0
        window_count = getattr(baseline, "window_count", min_windows)
        if window_count < min_windows:
            return None
        zscore = getattr(baseline, "zscore", None)
        z = zscore(features) if callable(zscore) else features
        try:
            raw = mod.unsupervised_strain_score(z)
        except TypeError:
            # Forward-compatible fallback for a two-arg variant.
            raw = mod.unsupervised_strain_score(features, baseline)
        score = float(raw)
    except Exception as exc:  # never let ML break the scoring cycle
        logger.debug("ml_stress scoring failed, using heuristic: %s", exc)
        return None
    if not math.isfinite(score):
        return None
    return max(0.0, min(100.0, score))


def _latest_hr_sample(hr_stream) -> Optional[Dict]:
    """
    Pull the latest HR sample from a wearable stream without importing
    wearable_ble (duck-typed, so the module stays optional).

    Prefers the public latest_sample() accessor ({"bpm": int,
    "ts": epoch}); falls back to the latest_bpm attribute with ts=None,
    which fusion treats as fresh per its documented choice.
    """
    if hr_stream is None:
        return None
    try:
        # A stopped/disabled stream is not a live modality.
        if getattr(hr_stream, "available", True) is False:
            return None
        accessor = getattr(hr_stream, "latest_sample", None)
        if callable(accessor):
            sample = accessor()
            if isinstance(sample, dict) and sample.get("bpm"):
                return sample
        bpm = getattr(hr_stream, "latest_bpm", None)
        if isinstance(bpm, (int, float)) and not isinstance(bpm, bool) and bpm > 0:
            return {"bpm": bpm, "ts": None}
    except Exception as exc:
        logger.debug("HR sample read failed, continuing without HR: %s", exc)
    return None


# Per-employee fatigue trackers, created on demand at session end.
_FATIGUE_TRACKERS: Dict[str, "FatigueTracker"] = {}


def get_fatigue_tracker(employee_id: str) -> Optional["FatigueTracker"]:
    """Return (creating if needed) the FatigueTracker for an employee."""
    if FatigueTracker is None or not employee_id:
        return None
    tracker = _FATIGUE_TRACKERS.get(employee_id)
    if tracker is None:
        tracker = FatigueTracker(employee_id=employee_id)
        _FATIGUE_TRACKERS[employee_id] = tracker
    return tracker


@dataclass
class EnrichedStrain:
    """Result of one scoring cycle through the round-4 pipeline."""
    score: int                      # final fused score, 0-100
    level: str                      # "green" | "amber" | "red"
    heuristic: StrainResult         # original heuristic result, untouched
    ml_score: Optional[float]       # ml behavioral score, or None
    ml_features: Optional[Dict]     # merged ml feature dict, or None
    fatigue_event: Optional[Dict]   # fatigue_suspected event, or None
    fusion_meta: Dict               # from fusion.fuse()
    components: Dict = field(default_factory=dict)


def enrich_strain_score(
    heuristic: StrainResult,
    *,
    key_intervals: Sequence[float] = (),
    mouse_intervals: Sequence[float] = (),
    key_pairs: Optional[Sequence[Tuple[float, float]]] = None,
    mouse_events: Optional[Sequence[Dict]] = None,
    fatigue_tracker: Optional["FatigueTracker"] = None,
    employee_id: Optional[str] = None,
    session_ended: bool = False,
    hr_stream=None,
    resting_bpm: float = 90.0,
    fusion_mode: str = "auto",
) -> EnrichedStrain:
    """
    Run one scoring cycle through the round-4 pipeline, in order:

    (a) extract behavioral features from the anonymized timing buffers via
        agent.ml_stress (falls back to None when unavailable);
    (b) at session end, run the employee's FatigueTracker on the session
        features (ml features when present, else the heuristic components
        as a plain numeric dict, which the tracker accepts);
    (c) fuse the behavioral score (ml score when available, else the
        heuristic score) with the latest wearable HR sample;
    (d) heuristic fallback is automatic: with no ml_stress, a cold ML
        baseline, or no HR stream, this returns the heuristic score
        unchanged.

    The returned EnrichedStrain always carries the original heuristic
    result, so break-reminder thresholds, break_suggested records, and the
    backend HIGH_STRAIN_DETECTED rule (which keys on activity-record
    strain_score streaks) keep working on the same score semantics.
    """
    # (a) ML behavioral features from the anonymized buffers.
    ml_features = extract_ml_features(
        key_intervals, mouse_intervals,
        key_pairs=key_pairs, mouse_events=mouse_events,
    )
    ml_score = ml_behavioral_score(ml_features, employee_id=employee_id)

    behavioral = ml_score if ml_score is not None else float(heuristic.score)

    # (b) Session fatigue check at session end.
    fatigue_event: Optional[Dict] = None
    if session_ended and FatigueTracker is not None:
        tracker = fatigue_tracker
        if tracker is None and employee_id:
            tracker = get_fatigue_tracker(employee_id)
        if tracker is not None:
            session_features = ml_features or {
                k: float(v) for k, v in heuristic.components.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
            try:
                fatigue_event = tracker.add_session(session_features)
            except Exception as exc:
                logger.debug("fatigue check failed, continuing: %s", exc)
                fatigue_event = None

    # (c) Fuse with the latest wearable HR sample when one exists.
    hr_sample = _latest_hr_sample(hr_stream)
    if fuse is not None:
        fused_score, fusion_meta = fuse(
            behavioral, ml_features or {}, hr_sample,
            mode=fusion_mode, resting_bpm=resting_bpm,
        )
    else:  # pragma: no cover - defensive; fusion.py ships with this file
        fused_score, fusion_meta = behavioral, {"hr_missing": True, "mode": "behavioral_only"}

    final_score = int(round(max(0.0, min(100.0, fused_score))))
    components = dict(heuristic.components)
    components.update({
        "ml_score": round(ml_score, 2) if ml_score is not None else None,
        "fusion_mode": fusion_meta.get("mode"),
        "fatigue_event": fatigue_event["type"] if fatigue_event else None,
    })
    return EnrichedStrain(
        score=final_score,
        level=score_level(final_score),
        heuristic=heuristic,
        ml_score=ml_score,
        ml_features=ml_features,
        fatigue_event=fatigue_event,
        fusion_meta=fusion_meta,
        components=components,
    )
