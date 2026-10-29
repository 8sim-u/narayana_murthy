"""
Behavioral stress score + wearable heart-rate fusion for WAMSS.

Two literature-standard fusion patterns are implemented:

* Early (feature-level) fusion: when the heart-rate sample is present and
  fresh, a combined feature vector is built and scored jointly. The joint
  score adds a small cross-modal interaction term, because simultaneous
  high behavioral load AND elevated heart rate is a stronger signal than
  either alone. Literature pattern: early fusion wins when modalities are
  complete.
* Late (decision-level) fusion: weighted average of the behavioral score
  and the heart-rate-derived score. Robust to modality dropout.

Missing-modality masking: when the HR sample is None or stale (older than
120 s), the behavioral score is returned unchanged with
``meta = {"hr_missing": True, "mode": "behavioral_only"}``.

Heart rate is a heuristic workload signal here. It is NOT a medical
measure of stress, fatigue, or heart health.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger("wamss.agent.fusion")

# A heart-rate sample older than this is treated as missing.
HR_STALE_AFTER_S = 120.0

# Interaction bonus scale for early fusion (max +10 points when both
# modalities are saturated).
EARLY_INTERACTION_MAX = 10.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def hr_score_from_bpm(bpm: float, resting_bpm: float) -> float:
    """
    Heart-rate-derived stress score, 0-100.

    Rises as bpm exceeds personal resting + 20: 0 at or below that line,
    100 at 40 bpm above it, linear in between.
    """
    deviation = max(0.0, float(bpm) - (float(resting_bpm) + 20.0))
    return 100.0 * _clamp01(deviation / 40.0)


def hr_is_fresh(hr_sample: Optional[Dict], now: Optional[float] = None,
                max_age_s: float = HR_STALE_AFTER_S) -> bool:
    """
    True when hr_sample carries a usable, non-stale reading.

    Expected shape: {"bpm": <number>, "ts": <epoch seconds, optional>,
    "resting_bpm": <number, optional>}. A missing "ts" is treated as fresh
    (documented choice: locally attached streams always stamp readings, and
    a missing stamp must not silently discard a live sensor).
    """
    if not isinstance(hr_sample, dict):
        return False
    bpm = hr_sample.get("bpm")
    if not isinstance(bpm, (int, float)) or isinstance(bpm, bool):
        return False
    if not 0 < bpm < 300:
        return False
    ts = hr_sample.get("ts")
    if ts is None:
        return True
    try:
        age = (now if now is not None else time.time()) - float(ts)
    except (TypeError, ValueError):
        return False
    return age <= max_age_s


def _resting_bpm(hr_sample: Dict, resting_bpm: float) -> float:
    sample_resting = hr_sample.get("resting_bpm")
    if isinstance(sample_resting, (int, float)) and not isinstance(sample_resting, bool):
        if 0 < sample_resting < 300:
            return float(sample_resting)
    return float(resting_bpm)


def fuse(
    behavioral_score: float,
    behavioral_features: Dict,
    hr_sample: Optional[Dict],
    weights: Tuple[float, float] = (0.7, 0.3),
    mode: str = "auto",
    resting_bpm: float = 70.0,
    now: Optional[float] = None,
) -> Tuple[float, Dict]:
    """
    Fuse a behavioral stress score (0-100) with a wearable HR sample.

    Args:
        behavioral_score: 0-100 score from keystroke/mouse behavior.
        behavioral_features: feature dict behind that score (carried into
            meta for transparency; the behavioral block enters the fused
            vector as the normalized score, see layout below).
        hr_sample: {"bpm":..., "ts":..., "resting_bpm":...} or None.
        weights: (behavioral_weight, hr_weight); normalized to sum to 1.
        mode: "auto" (early fusion when HR is fresh, else behavioral-only),
            "early" (feature-level), or "late" (decision-level weighted
            average). "early"/"late" with missing or stale HR also fall back
            to behavioral-only.
        resting_bpm: default personal resting HR when the sample does not
            carry its own.

    Early-fusion combined feature vector layout (documented):
        v[0] = behavioral_score / 100.0          (behavioral block)
        v[1] = bpm / 200.0                       (hr block: absolute level)
        v[2] = min(1, max(0, bpm-(resting+20))/40)  (hr block: resting deviation)
        early_score = 100*(w_b*v[0] + w_hr*v[2]) + 10*v[0]*v[2]
    The interaction term (max +10) is the feature-level part: it can only
    be computed from the joint vector, not from separate decisions.

    Returns (score_0_100, meta_dict).
    """
    try:
        behavioral_score = max(0.0, min(100.0, float(behavioral_score)))
    except (TypeError, ValueError):
        behavioral_score = 0.0

    w_b, w_hr = weights
    total = w_b + w_hr
    if total <= 0:
        raise ValueError("fusion weights must sum to a positive value")
    w_b, w_hr = w_b / total, w_hr / total

    if mode not in ("auto", "early", "late"):
        raise ValueError(f"unknown fusion mode: {mode!r}")

    fresh = hr_is_fresh(hr_sample, now=now)
    if not fresh:
        return behavioral_score, {"hr_missing": True, "mode": "behavioral_only"}

    assert isinstance(hr_sample, dict)
    bpm = float(hr_sample["bpm"])
    resting = _resting_bpm(hr_sample, resting_bpm)
    hr_score = hr_score_from_bpm(bpm, resting)

    base_meta = {
        "hr_missing": False,
        "hr_bpm": bpm,
        "resting_bpm": resting,
        "hr_score": round(hr_score, 2),
        "weights": (round(w_b, 3), round(w_hr, 3)),
        "behavioral_score": round(behavioral_score, 2),
        "behavioral_feature_keys": sorted(str(k) for k in (behavioral_features or {}).keys()),
    }

    use_early = mode in ("auto", "early")
    if use_early:
        v0 = behavioral_score / 100.0
        v1 = _clamp01(bpm / 200.0)
        v2 = _clamp01(max(0.0, bpm - (resting + 20.0)) / 40.0)
        interaction = EARLY_INTERACTION_MAX * v0 * v2
        score = 100.0 * (w_b * v0 + w_hr * v2) + interaction
        score = max(0.0, min(100.0, score))
        meta = {
            **base_meta,
            "mode": "early_fusion",
            "feature_vector": [round(v0, 4), round(v1, 4), round(v2, 4)],
            "feature_layout": [
                "v[0]=behavioral_score/100 (behavioral block)",
                "v[1]=bpm/200 (hr block: absolute level)",
                "v[2]=min(1,max(0,bpm-(resting+20))/40) (hr block: resting deviation)",
            ],
            "interaction_bonus": round(interaction, 2),
        }
        return score, meta

    # Late (decision-level) fusion: weighted average, no interaction term.
    score = w_b * behavioral_score + w_hr * hr_score
    score = max(0.0, min(100.0, score))
    return score, {**base_meta, "mode": "late_fusion"}
